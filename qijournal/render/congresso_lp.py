"""A tela da visão por parlamentar da aba Congresso (os modelos ``congresso_lp.*.j2``).

As contas ficam em :mod:`qijournal.congresso.pais` (o bloco ``lp`` de ``casa-<casa>.json`` e os
arquivos ``projetos-``, ``autores-``, ``decide-`` e ``autoria/``). Aqui só se **apresenta**:
o que o HTML estático de cada vista mostra, com os textos prontos, e o que o script precisa
para rotular (``cgManifesto`` → ``lp``).

- :func:`encaixar` é a única porta de entrada: chamada por ``render/congresso.py`` depois de
  montar a página, põe ``view.lp`` (o que vale para as duas Casas), ``casa.lp`` (por Casa) e
  ``manifesto.lp``. **Sem o bloco ``lp`` nos dados (ou sem a régua no config), não faz nada**: a
  página sai como era, sem o interruptor "Ver por" e sem as vistas novas.
- Nenhum nome de faixa, alavanca, marca ou código é escrito aqui nem nos modelos: os rótulos
  vêm de ``config/congresso.yaml`` (``projeto_de_pais``), por :func:`pais.manifesto`. O que este
  módulo nomeia é o que é do **registro** (as portas, em nome curto) e da própria tela.
- Duas palavras da tela: **confirmado** (a faixa do topo com duas leituras: só ele leva o nome
  da faixa) e **a confirmar** (peso do topo pela ementa, com uma leitura só). Todo número diz
  qual é qual. O gráfico dos projetos mostra os dois, marcados; o que compara pessoas (o funil
  e as listas de "Quem propõe") conta só o confirmado, como as contas (``pais.FUNIL``).

Quem for acrescentar uma vista: ponha a aba em :data:`ABAS`, monte o bloco dela em
:func:`_casa` (``casa.lp["<vista>"]``) e escreva a seção em ``congresso_lp.html.j2``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from .. import text
from ..config import Config
from ..congresso import dados, pais, projetos, relevancia
from ..congresso import regras as regras_mod
from . import congresso as pagina
from . import congresso_lp_metodo as metodo_mod
from . import congresso_lp_voto as voto
from . import filters

log = logging.getLogger(__name__)

# As abas da visão por parlamentar, na ordem: (vista, âncora, nome). "Como ler" fecha a lista.
ABAS = (
    ("projetos", "cg-projetos", "Os projetos"),
    ("autores", "cg-autores", "Quem propõe"),
    *voto.ABAS,  # "Na hora do voto" e "Pessoa ou partido?" (render/congresso_lp_voto.py)
)
# O tempo sem andar no eixo das faixas por porta, em saltos (dias → % da largura); além do
# último salto, o ponto fica preso em TEMPO_TETO.
ESCALA_TEMPO = ((0, 0), (182, 20), (365, 36), (730, 54), (1460, 74), (2920, 96))
TEMPO_TETO = 98
TEMPO_MARCAS = ("0", "6 meses", "1 ano", "2 anos", "4 anos", "8 anos ou mais")
TEMPO_MARCAS_CEL = ("", "", "1 ano", "", "4 anos", "8+")
# As portas do registro, em nome curto (a cabeça de cada faixa) e em frase, no plural e no singular (os números do alto).
PORTAS = {
    projetos.F_MESA: ("Na Mesa", "esperam despacho da Mesa", "espera despacho da Mesa"),
    projetos.F_COMISSAO: ("Esperando comissão", "esperam a criação da comissão", "espera a criação da comissão"),
    projetos.F_RELATOR: ("Esperando relator", "esperam um relator", "espera um relator"),
    projetos.F_PARECER: ("Com o relator", "estão com o relator", "está com o relator"),
    projetos.F_PAUTA: ("Pronta, sem pauta", "estão prontos, sem pauta", "está pronto, sem pauta"),
    projetos.F_RECURSO: ("Prazo de recurso", "estão no prazo de recurso", "está no prazo de recurso"),
    projetos.F_OUTRA: ("Outra etapa", "estão em outra etapa", "está em outra etapa"),
}
TAXA = "Taxa de exceção"  # o nome do índice da régua (a parte dos lidos com a marca da exceção)
NORMAS_MINIMO = 20  # com menos normas do que isto, "o que virou norma" não vira barra
FORMAS = {"cheio": "●", "vazado": "○", "losango": "◆"}  # a forma do ponto, também escrita nas listas

SELO_DICA = (
    "Aqui, e só na visão por parlamentar, a página lê o conteúdo: cada projeto foi classificado por IA, pela ementa, sem ver o autor nem o partido, "
    "com a régua publicada, e o peso sai de uma fórmula com os valores de config/congresso.yaml. É leitura, não registro: os códigos, a conta do "
    "peso e a justificativa de cada projeto estão no cartão dele. Na visão por partido, nenhuma IA entra nas contas."
)


def _num(valor: Any) -> str:
    return pagina._num(valor)


def _dec(valor: Any, casas: int = 1) -> str:
    """Número com vírgula ("1,9"); sem a casa decimal quando ela é zero ("20")."""
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        return "—"
    return text.format_number_pt(valor, 0 if valor == int(valor) else casas)


def _pct(parte: float, todo: float) -> str:
    return pagina._pct(100.0 * parte / todo) if todo else "—"


def _dias(iso: str | None, hoje: str) -> int | None:
    try:
        return (date.fromisoformat(hoje[:10]) - date.fromisoformat(str(iso)[:10])).days
    except (TypeError, ValueError):
        return None


def _nenhum(n: int) -> str:
    """O número de um cartão: zero vai por extenso."""
    return _num(n) if n else "Nenhum"


def plural(rotulo: str) -> str:
    """O plural de um rótulo de uma palavra só ("estruturante" → "estruturantes"); rótulo de
    mais de uma palavra fica como está, entre aspas, para a frase não inventar concordância."""
    rotulo = rotulo.strip()
    if " " in rotulo or not rotulo:
        return f"“{rotulo}”"
    if rotulo[-1] in "aeiouãõé":
        return rotulo + "s"
    if rotulo[-1] in "rz":
        return rotulo + "es"
    return f"“{rotulo}”"


@dataclass
class Rotulos:
    """Os rótulos do config, como a tela os usa (de :func:`pais.manifesto`)."""

    m: dict[str, Any]
    topo: str  # a faixa que só conta confirmada
    topo_pl: str
    baixas: str  # as faixas baixas, juntas ("mínimo ou pontual")
    piso: str  # a primeira faixa
    meio: str  # o que fica entre as baixas e o topo
    nivel_do_topo: str  # o nível de alavanca que o topo exige ("núcleo"), ou vazio
    a_confirmar: str  # como o config escreve o alerta de quem espera a segunda leitura
    eixo_rot: str = ""  # o rótulo do campo que dá o nível (a alavanca: "Em que sistema mexe")

    @classmethod
    def de(cls, m: dict[str, Any], regua: Any) -> Rotulos:
        nomes = {ident: rotulo for ident, rotulo, _de in m["faixas"]}
        grupos = m["grupos"]
        do_meio = [rotulo for ident, rotulo, _de in m["faixas"] if ident not in grupos["topo"] and ident not in grupos["baixas"]]
        topo = " ou ".join(nomes[i] for i in grupos["topo"])
        niveis = {f.so_nivel for f in regua.faixas if f.id in grupos["topo"] and f.so_nivel}
        return cls(
            m=m,
            topo=topo,
            topo_pl=plural(topo),
            baixas=" ou ".join(nomes[i] for i in grupos["baixas"]),
            piso=nomes.get(grupos["piso"], ""),
            meio=" ou ".join(do_meio),
            nivel_do_topo=" ou ".join(regua.rotulo_do_nivel(n) for n in sorted(niveis)),
            a_confirmar=str((m.get("alertas") or {}).get("a_confirmar") or ""),
            eixo_rot=regua.alavanca.rotulo,
        )

    def confirmados(self, n: int) -> str:
        """"1 estruturante confirmado", "225 estruturantes confirmados", "Nenhum estruturante confirmado"."""
        return f"{self.topo} confirmado" if n in (0, 1) else f"{self.topo_pl} confirmados"

    def eixo(self, ident: str) -> str:
        return next((rotulo for i, rotulo, _n in self.m["eixos"] if i == ident), ident)

    def nivel_do_eixo(self, ident: str) -> str:
        nivel = next((n for i, _r, n in self.m["eixos"] if i == ident), "")
        return next((rotulo for i, rotulo in self.m["niveis"] if i == nivel), "")

    def direcao(self, ident: str) -> tuple[str, str]:
        """(rótulo, forma) de um valor da marca do caixa."""
        return next(((rotulo, forma) for i, rotulo, forma, _v in self.m["direcoes"] if i == ident), (ident, "vazado"))

    def codigo(self, campo: str, codigo: str) -> tuple[str, str]:
        """(rótulo do campo, rótulo do código); o "nd" é o texto do config."""
        par = (self.m.get("campos") or {}).get(campo)
        if not par:
            return campo, codigo
        return par[0], (self.m["nd"] if codigo == regras_mod.ND else par[1].get(codigo, codigo))


def _forma(forma: str) -> str:
    return FORMAS.get(forma, FORMAS["vazado"])


def _legenda(partido: str, uf: str) -> str:
    return "-".join(x for x in (partido, uf) if x)


# ── Os projetos ──────────────────────────────────────────────────────────────


def _cartao(linha: Mapping[str, Any], R: Rotulos, hoje: str, repo: str) -> dict[str, Any]:
    """O cartão de um projeto (uma linha de ``projetos-<casa>.json``), com os textos prontos.
    O script monta o mesmo cartão, com as mesmas classes, para os outros projetos."""
    m = R.m
    confirmado = linha.get("fx") in m["grupos"]["topo"]
    rotulo_dir, forma = R.direcao(linha.get("dir") or "")
    dias = _dias(linha.get("ult"), hoje)
    porta = linha.get("pp") or projetos.F_OUTRA
    relator = linha.get("rln") or ""
    com_o_relator = bool(relator) and porta == projetos.F_PARECER
    faixa_conta = next((rotulo for ident, rotulo, _de in m["faixas"] if ident == linha.get("fx")), "")
    return {
        "k": linha.get("k") or "",
        "t": linha.get("t") or "",
        "u": filters.safe_url(linha.get("u")) or "",
        "f": bool(linha.get("f")),
        "peso": linha.get("w"),
        "confirmado": confirmado,
        "estado": R.topo if confirmado else "a confirmar",
        "estado_x": (m["fontes"].get(linha.get("fo") or "", "") if confirmado else f"{R.a_confirmar}; por ora conta como {faixa_conta}"),
        "ementa": linha.get("e") or "",
        "alavanca": (R.eixo_rot, R.eixo(linha.get("ex") or ""), R.nivel_do_eixo(linha.get("ex") or "")),
        "cod": [R.codigo(campo, codigo) for campo, codigo in linha.get("cod") or []],
        "contas": {"forma": forma, "sinal": _forma(forma), "rotulo": rotulo_dir, "marca": next((rot for ident, rot, _v in m["marcas"] if ident == m["papeis"].get("caixa")), "")},
        "porque": linha.get("j") or "",
        "conta": linha.get("conta") or "",
        "fonte": m["fontes"].get(linha.get("fo") or "", ""),
        "confianca": linha.get("cf"),
        "alertas": [m["alertas"].get(a, a) for a in linha.get("al") or []],
        "porta": projetos.FILAS.get(porta, ""),
        "orgao": linha.get("org") or "",
        "sem_andar": pagina._tempo(dias) if dias is not None else "",
        "ultimo": pagina._data(linha.get("ult"), ano=True),
        "relator": {"k": linha.get("rl") or "", "nome": relator} if com_o_relator else None,
        "segura": "" if com_o_relator else projetos.QUEM_SEGURA.get(porta, ""),
        "autor": {"k": linha.get("a") or "", "nome": linha.get("an") or "", "legenda": _legenda(linha.get("ap") or "", linha.get("au") or "")},
        "origem": relevancia.ORIGENS.get(linha.get("og") or "", ""),
        "apresentada": pagina._data(linha.get("d"), ano=True),
        "contestar": (repo.rstrip("/") + "/issues/new?title=" + quote(f"Leitura do {linha.get('t')}")) if repo else "",
    }


def _outros(n: int) -> str:
    return f"{'outro' if n == 1 else 'outros'} {_num(n)} a confirmar"


def _projetos(casa: str, nome_casa: str, lp: Mapping[str, Any], R: Rotulos, arquivo: Any, hoje: str, repo: str) -> dict[str, Any] | None:
    """O que "Os projetos" mostra de uma Casa sem script: a janela, os quatro números, as
    cabeças das faixas por porta com a tabela gêmea, o cartão do projeto em foco, a lista dos
    que mais pesam e não andam, com quem está e o que espera × o que virou norma."""
    e = lp.get("estoque") or {}
    if not e or not e.get("lidos"):
        return None
    m, cortes = R.m, R.m["cortes"]
    estr, topo = int(e.get("estr") or 0), int(e.get("topo") or 0)
    so_ementa = topo - estr
    tem = estr > 0  # com algum confirmado, os números do alto contam os confirmados
    un = R.confirmados(2) if tem else "do topo da régua, a confirmar"

    def cartao(n_estr: int, n_topo: int, frases: tuple[str, str], linhas: list[str], filtro: str, ir: str = "ver no gráfico") -> dict[str, Any]:
        """``frases``: (com vários, com um ou nenhum)."""
        n = n_estr if tem else n_topo
        resto = n_topo - n_estr
        return {
            "n": _nenhum(n),
            "un": (R.confirmados(n) if tem else un),
            "f": frases[1] if n in (0, 1) else frases[0],
            "x": [*linhas, *([_outros(resto)] if tem and resto else [])],
            "filtro": filtro,
            "ir": ir,
        }

    onde = f"{'na' if casa == 'cd' else 'no'} {nome_casa}"
    direcoes = m["direcoes"]
    mais = next((d for d in direcoes if d[3] == 1), None)
    conta_dir = e.get("dir") if tem else e.get("dir_topo")
    conta_dir, conta_topo = conta_dir or {}, e.get("dir_topo") or {}
    numeros = [
        cartao(estr, topo, (f"esperam {onde}", f"espera {onde}"), [f"de {_num(e.get('tramitando'))} projetos que tramitam"], "todos", "ver todos"),
    ]
    if mais:
        # ao lado, só a marca oposta (as demais estão nas fichas do filtro, logo abaixo, com a contagem de cada uma)
        demais = "; ".join(f"{_num(conta_dir.get(d[0]) or 0)}: {d[1]}" for d in direcoes if d[3] == -1)
        numeros.append(cartao(int((e.get("dir") or {}).get(mais[0]) or 0), int(conta_topo.get(mais[0]) or 0), (f"com a marca “{mais[1]}”",) * 2, [demais], f"dir:{mais[0]}"))
    numeros.append(cartao(int(e.get("estr_parados") or 0), int(e.get("topo_parados") or 0), ("parados há mais de um ano", "parado há mais de um ano"), ["sem nenhum andamento oficial"], "par"))
    por_porta = {b["id"]: b for b in e.get("por_porta") or []}
    maior = por_porta.get(e.get("porta_maior") or "")
    if maior:
        numeros.append(cartao(int(maior.get("estr") or 0), int(maior.get("topo") or 0), PORTAS[maior["id"]][1:], ["a porta que mais segura"], f"porta:{maior['id']}"))

    portas = []
    for ident in pais.PORTAS:
        b = por_porta.get(ident)
        if not b or (ident == projetos.F_OUTRA and not b.get("topo")):
            continue
        n_topo, n_estr = int(b.get("topo") or 0), int(b.get("estr") or 0)
        portas.append(
            {
                "id": ident,
                "nome": PORTAS[ident][0],
                "longo": projetos.FILAS[ident],
                "topo": _num(n_topo),
                "estr": _num(n_estr),
                # com tudo confirmado, a cabeça diz só isso; senão, quantos do topo e, deles, quantos confirmados
                "resto": R.confirmados(n_topo) if n_estr == n_topo else f"do topo · {_num(n_estr)} {'confirmado' if n_estr == 1 else 'confirmados'}",
                "vazia": not n_topo,
                "mediana": pagina._tempo(b["mediana_dias"]) if b.get("mediana_dias") is not None else "",
                "total": _num(b.get("n")),
                "lidos": _num(b.get("lidos")),
                "meio": _num(b.get("setorial")),
                "topo_parados": _num(b.get("topo_parados")),
                "estr_parados": _num(b.get("estr_parados")),
            }
        )

    linhas = {}
    if isinstance(arquivo, dict) and isinstance(arquivo.get("cols"), list):
        linhas = {linha[0]: dict(zip(arquivo["cols"], linha, strict=False)) for linha in arquivo.get("linhas") or [] if linha}
    foco = linhas.get(e.get("foco") or "")

    top = []
    for i in e.get("top") or []:
        dias = _dias(i.get("ult"), hoje)
        rotulo_dir, forma = R.direcao(i.get("dir") or "")
        porta = i.get("pp") or projetos.F_OUTRA
        com_o_relator = bool(i.get("rln")) and porta == projetos.F_PARECER
        top.append(
            {
                "k": i.get("k") or "",
                "t": i.get("t") or "",
                "u": filters.safe_url(i.get("u")) or "",
                "f": bool(i.get("f")),
                "e": i.get("e") or "",
                "peso": i.get("w"),
                "a_confirmar": bool(i.get("ac")),
                "sinal": _forma(forma),
                "dir": rotulo_dir,
                "autor": {"k": i.get("a") or "", "nome": i.get("an") or "", "legenda": _legenda(i.get("ap") or "", i.get("au") or "")},
                "porta": PORTAS[porta][0] if porta in PORTAS else "",
                "orgao": i.get("org") or "",
                "relator": {"k": i.get("rl") or "", "nome": i.get("rln")} if com_o_relator else None,
                "segura": "" if com_o_relator else projetos.QUEM_SEGURA.get(porta, ""),
                "sem_andar": pagina._tempo(dias) if dias is not None else "",
                "no_grafico": i.get("k") in linhas,
            }
        )

    def desde(iso: str) -> str:
        dias = _dias(iso, hoje)
        return pagina._tempo(dias) if dias is not None else "—"

    orgaos = [
        {"org": o.get("org") or "", "topo": _num(o.get("topo")), "estr": _num(o.get("estr")), "desde": desde(o.get("mais_antiga")), "presidente": o.get("presidente") or "", "presidente_k": o.get("presidente_k") or ""}
        for o in e.get("orgaos") or []
    ]
    relatores = [
        {"k": r.get("k") or "", "nome": r.get("nome") or "", "legenda": _legenda(r.get("pt") or "", r.get("uf") or ""), "topo": _num(r.get("topo")), "estr": _num(r.get("estr")), "desde": desde(r.get("mais_antiga"))}
        for r in e.get("relatores") or []
        if r.get("nome")
    ]

    plotados, plotaveis = int(e.get("plotados") or 0), int(e.get("plotaveis") or 0)
    nucleo = f" em alavanca do {R.nivel_do_topo}" if R.nivel_do_topo else ""
    janela = [
        (f"Projetos de lei e propostas de emenda à Constituição que tramitam {'na' if casa == 'cd' else 'no'} {nome_casa}", False),
        (f"o topo da régua: peso {cortes['confirma']} ou mais{nucleo}, pela ementa", False),
        (f"{_num(topo)} {'projeto' if topo == 1 else 'projetos'}", True),
        (f"{_num(estr)} {R.confirmados(estr)} por dois leitores e {_num(so_ementa)} a confirmar" if so_ementa else f"{'todos ' if topo != 1 else ''}{R.confirmados(topo)} por dois leitores", False),
    ]
    if plotados < plotaveis:
        janela.append((f"no gráfico, os {_num(plotados)} de maior peso", False))
    janela.append((f"{_pct(e.get('lidos') or 0, e.get('tramitando') or 0)} dos que tramitam têm leitura", False))

    notas = []
    if e.get("topo_apensados"):
        n, c = int(e["topo_apensados"]), int(e.get("estr_apensados") or 0)
        notas.append(
            f"{'Outro' if n == 1 else 'Outros'} {_num(n)} do topo da régua ({_num(c)} {'confirmado' if c == 1 else 'confirmados'}) "
            f"{'anda' if n == 1 else 'andam'} junto com outra matéria: {'segue' if n == 1 else 'seguem'} a porta da principal e não {'entra' if n == 1 else 'entram'} nas faixas."
        )
    if e.get("sem_data"):
        notas.append(f"{_num(e['sem_data'])} não têm data de andamento no registro: ficam fora das faixas.")

    return {
        "janela": janela,
        "numeros": numeros,
        "portas": portas,
        "foco": _cartao(foco, R, hoje, repo) if foco else None,
        "foco_k": e.get("foco") or "",
        "top": top,
        "orgaos": orgaos,
        "relatores": relatores,
        "exn": _espera_x_norma(e.get("espera_x_norma") or {}, R),
        "notas": notas,
        "eixos": [{"id": b["id"], "nome": R.eixo(b["id"]), "topo": _num(b.get("topo")), "estr": _num(b.get("estr"))} for b in e.get("por_eixo") or [] if b.get("topo")],
        "direcoes": [
            {"id": d[0], "nome": d[1], "forma": d[2], "sinal": _forma(d[2]), "n": _num((e.get("dir_topo") or {}).get(d[0]) or 0)}
            for d in direcoes
            if (e.get("dir_topo") or {}).get(d[0])
        ],
        "confirmados": _num(estr),
        "so_ementa": _num(so_ementa),
        "tem_a_confirmar": so_ementa > 0,  # sem nenhum à espera da segunda leitura, o filtro e a legenda de "a confirmar" não saem
        "parados": _num(e.get("topo_parados")),
        "plotados": _num(plotados),
        "tabela": {
            "tramitando": _num(e.get("tramitando")),
            "lidos": _num(e.get("lidos")),
            "meio": _num(e.get("setorial")),
            "topo": _num(topo),
            "estr": _num(estr),
            "topo_parados": _num(e.get("topo_parados")),
            "estr_parados": _num(e.get("estr_parados")),
        },
    }


def _espera_x_norma(x: Mapping[str, Any], R: Rotulos) -> dict[str, Any] | None:
    """As duas barras de "O que espera × o que virou norma": a parte de cada grupo de faixa nos
    projetos lidos (de origem parlamentar). Com poucas normas, a segunda barra não sai."""
    espera, norma = x.get("espera") or {}, x.get("norma") or {}
    if not espera.get("n"):
        return None

    def lado(nome: str, b: Mapping[str, Any]) -> dict[str, Any]:
        n = int(b.get("n") or 0)
        partes = [("baixo", R.baixas), ("setorial", R.meio), ("estr", f"{R.topo} confirmado")]
        ac = int(b.get("topo") or 0) - int(b.get("estr") or 0)
        return {
            "nome": nome,
            "n": _num(n),
            "total": _num(b.get("total")),
            "segs": [{"cls": f"cg-lp-s{i + 1}", "rotulo": rotulo, "n": _num(b.get(chave) or 0), "pct": _pct(b.get(chave) or 0, n), "w": round(100.0 * (b.get(chave) or 0) / n, 2) if n else 0} for i, (chave, rotulo) in enumerate(partes)],
            "a_confirmar": _num(ac) if ac else "",
            "baixo_pct": _pct(b.get("baixo") or 0, n),
        }

    e, n = lado("O que espera", espera), lado("O que virou norma na legislatura", norma)
    poucas = int(norma.get("n") or 0) < NORMAS_MINIMO
    return {"espera": e, "norma": None if poucas else n, "poucas": _num(norma.get("total") or 0) if poucas else "", "baixas": R.baixas}


# ── Quem propõe ──────────────────────────────────────────────────────────────


def _autores(casa: str, um: str, uns: str, lp: Mapping[str, Any], R: Rotulos) -> dict[str, Any] | None:
    """O que "Quem propõe" mostra de uma Casa sem script: a janela, os três números, a frase
    dos que ficam fora do gráfico e as três listas."""
    a = lp.get("autores") or {}
    if not a or not a.get("em_exercicio"):
        return None
    cortes = R.m["cortes"]
    minimo = cortes["min_proj"]
    taxa = a.get("taxa") or {}
    plotados, total = int(a.get("plotados") or 0), int(a.get("em_exercicio") or 0)
    Uns = uns[:1].upper() + uns[1:]

    def quantos(n: int, um_verbo: str, uns_verbo: str) -> tuple[str, str, str]:
        return _nenhum(n), (um if n in (0, 1) else uns), (um_verbo if n in (0, 1) else uns_verbo)

    # O que compara pessoas conta só o confirmado (a régua não deixa o topo pela ementa entrar em ranking).
    com_estr, so_ementa = int(a.get("com_estr") or 0), int(a.get("com_topo") or 0) - int(a.get("com_estr") or 0)
    n1, u1, v1 = quantos(com_estr, "tem", "têm")
    n2, u2, v2 = quantos(int(a.get("acima_topo") or 0), "propõe", "propõem")
    n3, u3, v3 = quantos(int(a.get("acima_sb") or 0), "propõe", "propõem")
    entre = f"entre os {_num(plotados)} com {minimo} projetos lidos ou mais"

    def menos(n: int) -> str:
        return f"{_nenhum(n).lower()} {'propõe' if n in (0, 1) else 'propõem'} menos do que o acaso explica"

    numeros = [
        {
            "n": n1, "un": u1, "f": f"{v1} ao menos um {R.topo} confirmado",
            "x": [f"de {_num(total)} {uns} em exercício", *([f"{'outro' if so_ementa == 1 else 'outros'} {_num(so_ementa)} só com projeto a confirmar"] if so_ementa > 0 else [])],
            "filtro": "tem", "ir": "ver no gráfico",
        },
        {
            "n": n2, "un": u2, "f": f"{v2} mais {R.topo} confirmado do que o acaso explica",
            "x": [entre, menos(int(a.get("abaixo_topo") or 0))],
            "filtro": "acima:topo", "ir": "ver no gráfico",
        },
        {
            "n": n3, "un": u3, "f": f"{v3} mais projeto da faixa “{R.piso}” do que o acaso explica",
            "x": [entre, menos(int(a.get("abaixo_sb") or 0))],
            "filtro": "acima:sb", "ir": "ver no gráfico",
        },
    ]  # fmt: skip

    def lista(itens: list[Mapping[str, Any]], valor: Any, extra: Any) -> list[dict[str, Any]]:
        return [{"k": i["k"], "nome": i["nome"], "legenda": _legenda(i.get("pt") or "", i.get("uf") or ""), "valor": valor(i), "extra": extra(i)} for i in itens if i.get("nome")]

    def a_confirmar(i: Mapping[str, Any]) -> int:
        return int(i.get("topo") or 0) - int(i.get("estr") or 0)

    listas = a.get("listas") or {}
    acaso = {1: "passa do que o acaso explica", 0: "dentro do que o acaso explica", -1: "abaixo do que o acaso explica"}
    blocos = [
        {
            "titulo": f"Quem tem a maior parte de {R.topo_pl} confirmados",
            "sub": f"nos projetos lidos de cada um; primeiro quem passa do acaso; só quem tem {minimo} projetos lidos ou mais",
            "itens": lista(
                listas.get("mais_estr") or [],
                lambda i: f"{_num(i.get('estr'))} de {_num(i.get('n'))} · {_dec(i.get('pct'))}%",
                lambda i: (f"mais {_num(a_confirmar(i))} a confirmar · " if a_confirmar(i) > 0 else "") + acaso.get(i.get("fz"), ""),
            ),
            "vazio": f"Nenhum {um} com {minimo} projetos lidos ou mais tem {R.topo} confirmado.",
        },
        {
            "titulo": f"Quem tem a maior parte dos projetos na faixa “{R.piso}”",
            "sub": "só quem passa do que o acaso explica",
            "itens": lista(
                listas.get("mais_simb") or [],
                lambda i: f"{_num(i.get('sb'))} de {_num(i.get('n'))} · {_dec(i.get('pct'))}%",
                lambda i: f"{_num(i.get('simb'))} na faixa {R.baixas}",
            ),
            "vazio": f"Nenhum {um} passa do que o acaso explica nesta medida.",
        },
        {
            "titulo": "Quem fez virar norma o que pesa",
            "sub": f"projetos de peso {cortes['de_peso']} ou mais, de autoria principal, que viraram norma na legislatura",
            "itens": lista(listas.get("norma_peso") or [], lambda i: _num(i.get("qt")), lambda i: ", ".join(i.get("t") or []) + ("…" if int(i.get("qt") or 0) > len(i.get("t") or []) else "")),
            "vazio": f"Nenhum projeto de peso {cortes['de_peso']} ou mais de {um} em exercício virou norma na legislatura.",
        },
    ]
    poucos, sem = int(a.get("poucos") or 0), int(a.get("sem") or 0)
    fora = a.get("fora") or {}
    em_quantas = round(1 / float(cortes.get("funil_alfa") or 0.01))  # a sorte daria o resultado menos de 1 vez em tantas
    return {
        # "o que o acaso explica", em palavras simples (a dica "i" do gráfico)
        "dica_acaso": (
            f"Acaso é sorte. Com poucos projetos, um ou dois a mais ou a menos aparecem por sorte, mesmo em quem é igual à média da Casa. "
            f"A faixa cinza é o que a sorte sozinha daria a um {um} médio com aquele número de projetos. Só sai dela quem tem um resultado que a sorte daria menos de 1 vez em {_num(em_quantas)}, "
            f"já contando {'que são ' + _num(plotados) + ' comparados' if plotados > 1 else 'todos os comparados'} de uma vez. Dentro da faixa não quer dizer “igual à média”: quer dizer que não dá para afirmar."
        ),
        "janela": [
            (f"{Uns} com {minimo} projetos lidos ou mais", False),
            (f"a parte dos projetos de cada um que são {R.topo_pl} confirmados", False),
            (f"{_num(plotados)} de {_num(total)}", True),
            (f"na Casa: {_dec(taxa.get('topo'))} em cada 100" if taxa.get("topo") is not None else "sem taxa da Casa", False),
        ],
        "numeros": numeros,
        "fora": f"Ficam fora do gráfico {_num(poucos)} com menos de {minimo} projetos lidos ({_num(sem)} sem nenhum)." if poucos else "",
        "universo": (
            f"Entram {_num(a.get('lidos'))} projetos lidos de {_num(a.get('total'))}: PL, PLP e PEC de autoria principal (quem assina primeiro), apresentados na legislatura. "
            f"Ficam fora dos números {_num(fora.get(pais.REAPRESENTADA) or 0)} com ementa igual à de proposição anterior e {_num(fora.get(pais.DE_BANCADA) or 0)} de bancada."
        ),
        "listas": blocos,
        "taxa": {"topo": _dec(taxa.get("topo")), "sb": _dec(taxa.get("sb"))},
        "ninguem": {"topo": not a.get("acima_topo") and not a.get("abaixo_topo"), "sb": not a.get("acima_sb") and not a.get("abaixo_sb")},
    }


# ── tudo junto ───────────────────────────────────────────────────────────────


def _regras(config: Config) -> Any:
    try:
        return regras_mod.carregar(config.root)
    except Exception as erro:  # noqa: BLE001 (o config da régua é acessório para a página: sem ele, a visão não sai)
        log.warning("Visão por parlamentar: não foi possível ler config/congresso.yaml (%s)", erro)
        return None


def _limites_da_diaria(config: Config) -> Any:
    """Os limites da leitura de todo dia (``projeto_de_pais.leitura.diaria`` em
    ``config/congresso.yaml``: o tamanho do lote, as perguntas de controle e o mínimo de
    acertos), para o Como ler dizer como ela é feita. ``None`` se o bloco não puder ser lido:
    a frase sai sem os números."""
    # import tardio: o módulo da leitura diária só entra aqui, para os limites
    from ..congresso import ler

    try:
        bruto = yaml.safe_load((Path(config.root) / "config" / regras_mod.ARQUIVO).read_text(encoding="utf-8")) or {}
        da_leitura = (bruto.get("projeto_de_pais") or {}).get("leitura") or {}
        return ler.limites_de(da_leitura.get("diaria"))
    except Exception as erro:  # noqa: BLE001 (acessório para a página: sem os limites, o texto sai sem os números)
        log.warning("Visão por parlamentar: não foi possível ler os limites da leitura diária (%s)", erro)
        return None


def _casa(casa: Any, lp: Mapping[str, Any], R: Rotulos, pasta: Path, hoje: str, repo: str, desde: int | None, medidas: Mapping[str, Any]) -> dict[str, Any]:
    um, uns = ("deputado", "deputados") if casa.id == "cd" else ("senador", "senadores")
    arquivo = dados.ler_json(pasta / pais.PROJETOS.format(casa=casa.id))
    cobertura = lp.get("cobertura") or {}
    return {
        "um": um,
        "uns": uns,
        "lidos": _num(cobertura.get("lidos")),
        "total": _num(cobertura.get("total")),
        "projetos": _projetos(casa.id, casa.curto, lp, R, arquivo, hoje, repo),
        "autores": _autores(casa.id, um, uns, lp, R),
        "decide": voto.decide(casa.id, casa.curto, um, uns, lp, R, desde),
        "cruzar": voto.cruzar(casa.id, um, uns, lp, R, medidas),
    }


def manifesto(m: dict[str, Any], R: Rotulos, repo: str, medidas: Mapping[str, Any]) -> dict[str, Any]:
    """``cgManifesto.lp``: o de :func:`pais.manifesto` (rótulos, cortes, faixas, alavancas,
    marcas, portas, fontes e selo, tudo do config) e, a mais, o que é só da tela: ``arq`` com o
    endereço de cada arquivo (falta só ``<casa>.json``; ``autoria`` é a pasta), a escala do
    tempo, os nomes curtos das portas, os rótulos compostos (``rot``), as abas que existem e o
    repositório (para "Contestar esta leitura")."""
    base = f"../data/{dados.PASTA}/"
    return {
        **m,
        "arq": {nome: base + (arq.replace("{casa}.json", "") if "{casa}" in arq else arq + "/") for nome, arq in m["arq"].items()},
        "escala_tempo": [list(par) for par in ESCALA_TEMPO],
        "tempo_teto": TEMPO_TETO,
        "tempo_marcas": [list(TEMPO_MARCAS), list(TEMPO_MARCAS_CEL)],
        "portas_nome": {ident: nomes[0] for ident, nomes in PORTAS.items()},
        "rot": {"topo": R.topo, "topo_pl": R.topo_pl, "baixas": R.baixas, "piso": R.piso, "meio": R.meio, "a_confirmar": R.a_confirmar, "nivel": R.nivel_do_topo, "taxa": TAXA, "eixo": R.eixo_rot},
        "abas": [vista for vista, _ancora, _nome in ABAS],
        "origens": dict(relevancia.ORIGENS),
        "repo": repo,
        "selo_dica": SELO_DICA,
        "medidas_tela": medidas,  # o nome, a unidade, a escala e as colunas de cada medida (render/congresso_lp_voto.py)
        "fiscal": voto.fiscal(R),  # como a tela escreve a marca fiscal +1 e −1 de uma matéria votada
        "outros": voto.OUTROS,
    }


def encaixar(view: Any, d: Any, config: Config, data_dir: Path) -> None:
    """Põe a visão por parlamentar na página já montada: ``view.lp``, ``casa.lp`` de cada Casa
    que tem o bloco ``lp`` nos dados e ``lp`` no manifesto. Sem o bloco em nenhuma Casa, ou sem a
    régua no config, nada muda."""
    com = {casa.id: d.casas[casa.id]["lp"] for casa in view.casas if isinstance((d.casas.get(casa.id) or {}).get("lp"), dict)}
    if not com:
        return
    regras = _regras(config)
    m = pais.manifesto(regras) if regras is not None else None
    if not m:
        return
    R = Rotulos.de(m, regras.projeto_de_pais)
    repo = filters.safe_url(config.site.repo_url) or ""
    hoje = str(d.meta.get("hoje") or "")
    pasta = dados.pasta_de(data_dir)
    medidas = voto.medidas(R)
    anos = [int(a) for a in d.meta.get("anos") or [] if str(a).isdigit()]
    for casa in view.casas:
        if casa.id in com:
            casa.lp = _casa(casa, com[casa.id], R, pasta, hoje, repo, min(anos) if anos else None, medidas)
    meta = d.meta.get("leitura_projetos") or {}
    view.lp = {
        "abas": [{"vista": vista, "ancora": ancora, "nome": nome} for vista, ancora, nome in ABAS],
        "selo": m.get("selo") or "Leitura por IA",
        "selo_dica": SELO_DICA,
        "objetivo": m.get("objetivo") or "",
        "rot": {"topo": R.topo, "topo_pl": R.topo_pl, "baixas": R.baixas, "piso": R.piso, "meio": R.meio, "nivel": R.nivel_do_topo, "a_confirmar": R.a_confirmar},
        "cortes": m["cortes"],
        "direcoes": [{"id": i, "nome": rotulo, "forma": forma, "sinal": _forma(forma)} for i, rotulo, forma, _v in m["direcoes"]],
        "marca_do_caixa": next((rot for ident, rot, _v in m["marcas"] if ident == m["papeis"].get("caixa")), ""),
        "fiscal": voto.fiscal(R),
        "medidas": medidas,
        "tempo": [{"x": x, "rot": rot, "cel": cel} for (_dias_, x), rot, cel in zip(ESCALA_TEMPO, TEMPO_MARCAS, TEMPO_MARCAS_CEL, strict=True)],
        "parado_x": next(x for dias, x in ESCALA_TEMPO if dias >= int(m["cortes"].get("dias_parado") or 365)),
        "data": pagina._data(meta.get("data"), ano=True),
        "dicas": {
            "peso": "De 0 a 100: quanto o texto mexe em regra de sistema nas alavancas do objetivo, pela régua. Sai de uma conta com três códigos lidos na ementa. Não diz se o projeto é bom nem se seria aprovado.",
            "topo": (
                f"O topo da régua: peso {m['cortes']['confirma']} ou mais" + (f" em alavanca do {R.nivel_do_topo}" if R.nivel_do_topo else "") + f". Só leva o nome “{R.topo}” o projeto que dois leitores de IA, "
                f"um sem ver o outro, puseram nessa faixa. Com uma leitura só, ele aparece como “a confirmar” e, nas contas, vale como {R.meio}."
            ),
            "caixa": "Esta marca diz o que a ementa enuncia para o caixa público. Não diz que falta fonte, não mede o tamanho do gasto nem o mérito. Anda ao lado do peso: nunca é somada nem multiplicada.",
            "sem_andar": "Tempo desde o último andamento oficial. A data muda também quando a matéria é pautada e não votada.",
        },
    }
    # o bloco "A leitura dos projetos" do Como ler: a régua (do config), a cobertura e a auditoria (de meta.json)
    parametros = next((com[casa.id].get("cruzar", {}).get("parametros") for casa in view.casas if casa.id in com and (com[casa.id].get("cruzar") or {}).get("parametros")), None)
    votadas = {ident: (lp.get("decide") or {}).get("materias") for ident, lp in com.items()}  # quantas matérias "Na hora do voto" conta em cada Casa
    view.lp["metodo"] = metodo_mod.metodo(R, regras.projeto_de_pais, meta, [(casa.id, casa.curto) for casa in view.casas if casa.id in com], min(anos) if anos else None, parametros, medidas, votadas, _limites_da_diaria(config))
    manifesto_da_pagina = json.loads(view.manifesto)
    manifesto_da_pagina["lp"] = manifesto(m, R, repo, medidas)
    view.manifesto = json.dumps(manifesto_da_pagina, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
