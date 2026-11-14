"""A ficha de cada matéria que a aba mostra: o que é, de onde veio, onde está e como o
Plenário votou.

Entram as matérias com votação nominal aberta no Plenário nesta legislatura e as do
painel "o que espera × o que passa" (as prontas para o Plenário e as aprovadas em 12
meses). Cada ficha junta o que os registros oficiais dizem:

- **sobre o quê**: a ementa inteira e, quando a Casa escreveu, a explicação dela
  (a ementa detalhada da Câmara; a explicação da ementa do Senado), as palavras-chave
  do registro e o endereço do texto integral. A página não resume o projeto: mostra o
  que a Casa registrou;
- **de onde veio e onde está**: autoria, origem, data, situação oficial, relator;
- **os sinais de relevância** (a mesma régua de :mod:`.relevancia`), decompostos, e
  **por que andou** (as aprovadas da Câmara);
- **como o Plenário votou**: cada votação nominal, com o que estava em votação (na
  Câmara, a abertura da votação diz quem pediu o destaque e qual trecho), o placar, o
  lado de cada polo com a contagem de cada partido do polo, a orientação do Governo e o
  que o Sim queria dizer.

É daqui que a página tira a resposta para "por que este voto conta como de esquerda?":
não é o assunto que define o lado, é como os partidos de cada polo votaram **naquela**
votação. A ficha mostra a contagem.

O arquivo (``materias-<casa>.json``) tem uma matéria por linha e só é lido quando alguém
abre um detalhe. Guarda datas, não contagens de dias: não muda de um dia para o outro
sem que a matéria mude.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .. import text
from . import VERSAO, base, perfil, projetos, relevancia
from .base import AnoVotos, Votacao
from .projetos import Proposicao
from .regras import Regras
from .relevancia import Aprovada, Regua

log = logging.getLogger(__name__)

ARQUIVO = "materias-{casa}.json"
TERMOS = 12  # palavras-chave do registro mostradas em cada ficha
PARTIDOS = 14  # partidos listados em cada votação de conteúdo (os maiores)
AUTORES_PARTIDOS = 6
CRUZADA = 8  # matérias de um polo em que o outro votou a favor, na lista
_SLUG = re.compile(r"^([A-Z]+) (\d+)/(\d{4})$")

# O que o Sim queria dizer (ver sentido_do_sim).
TEXTO, EMENDA, TRECHO, REQUERIMENTO = "texto", "emenda", "trecho", "requerimento"
_MANTIDO = re.compile(r"\b(mantid[oa]s?|suprimid[oa]s?)\b")
_EMENDA = re.compile(r"\b(emendas?|subemendas?)\b")
_SEPARADO = re.compile(
    r"\b(votacao em separado|dvs|destacad[oa]s?|dispositivo|expressao|expressoes|artigo|art|inciso|paragrafo|alinea|caput|capitulo)\b"
)
# Quem pediu o destaque, na abertura da votação da Câmara: "Votação do DTQ 5: PL: Emenda…",
# "Votação do DTQ 3 (PL, UNIÃO, PP): Destaque da Emenda…", "Votação da EMA 1 (Fulano): …".
_PEDIU = re.compile(r"^Vota[çc][ãa]o d[oa]s? (?:DTQ|DVS|EMA|EMP)\s*\d+\s*(?::\s*([^:()]{2,80}?)\s*:|\(([^)]{2,160})\)\s*:?)", re.IGNORECASE)


def quem_pediu(abertura: str) -> str:
    """A bancada (ou o deputado) que pediu o destaque, quando a abertura da votação diz."""
    achado = _PEDIU.match(abertura or "")
    return re.sub(r"\s+", " ", (achado.group(1) or achado.group(2) or "")).strip() if achado else ""


def chave(casa: str, titulo: str) -> str:
    """``"PL 2630/2020"`` → ``"cd-PL-2630-2020"`` (o endereço da ficha: ``#m=cd-PL-2630-2020``).
    Vazio para o que não é matéria numerada (``"REQ 5"``)."""
    achado = _SLUG.match(titulo or "")
    return f"{casa}-{achado.group(1)}-{achado.group(2)}-{achado.group(3)}" if achado else ""


_TITULO = re.compile(r"^([A-Z]+ \d+/\d{4})\b")


def titulo_da_materia(prop: str) -> str:
    """``"PEC 66/2023 (fase 2)"`` → ``"PEC 66/2023"``: o Senado anota a fase ao lado do número
    (o substitutivo da Câmara, a segunda fase de uma PEC); a matéria é a mesma."""
    achado = _TITULO.match(prop or "")
    return achado.group(1) if achado else (prop or "")


def sentido_do_sim(v: Votacao) -> str:
    """O que o Sim queria dizer, pela regra da Casa e pela descrição oficial:

    - ``texto``: aprovar o texto em votação (texto principal, substitutivo, PEC em cada turno);
    - ``emenda``: aprovar a emenda destacada;
    - ``trecho``: manter no texto o trecho destacado (o Não o retira): é o destaque para
      votação em separado, cujo resultado a Mesa anota como "mantido" ou "suprimido";
    - ``requerimento``: aprovar o requerimento (urgência, retirada de pauta, adiamento).

    Vazio quando a descrição não deixa claro. Vale primeiro o resultado anotado; depois, o
    que a Mesa pôs em votação."""
    if v.tipo == base.MERITO:
        return TEXTO
    if v.tipo in (base.URGENCIA, base.PROCEDIMENTO):
        return REQUERIMENTO
    if v.tipo != base.DESTAQUE:
        return ""
    resultado, abertura = text.normalize(v.descricao), text.normalize(v.abertura)
    for trecho in (resultado, abertura):
        if _MANTIDO.search(trecho):
            return TRECHO
        if _EMENDA.search(trecho):
            return EMENDA
        if _SEPARADO.search(trecho):
            return TRECHO
    return ""


def sim_venceu(v: Votacao) -> bool | None:
    """Se o lado do Sim venceu: o que a fonte diz (aprovada ou rejeitada) ou, no destaque
    para votação em separado, o que a Mesa anotou: "mantido" (o Sim venceu) ou "suprimido"."""
    if v.aprovada is not None:
        return v.aprovada
    resultado = text.normalize(v.descricao)
    if resultado.startswith("mantid"):
        return True
    if resultado.startswith(("suprimid", "rejeitad")):
        return False
    return None


# ── uma votação ──────────────────────────────────────────────────────────────


def votacao(v: Votacao, ano: AnoVotos, k: int, regras: Regras) -> dict[str, Any]:
    """Uma votação para a ficha: o que se votou, o placar, o lado de cada polo com a
    contagem de cada partido do polo, o Governo e, nas de conteúdo, os demais partidos."""
    partidos = [p.partido_em(k) for p in ano.pessoas]
    contagem = perfil.contar(v, partidos)
    leitura = perfil.ler_votacao(v, partidos, regras, contagem)
    conta = v.conta()
    d: dict[str, Any] = {
        "id": v.id,
        "d": v.data,
        "t": v.tipo,
        "x": v.descricao,
        "s": conta.get(base.SIM, 0),
        "n": conta.get(base.NAO, 0) + conta.get(base.OBSTRUCAO, 0),
    }
    if v.hora:
        d["h"] = v.hora
    if v.abertura:
        d["ab"] = v.abertura
        pediu = quem_pediu(v.abertura)
        if pediu:
            d["dq"] = pediu
    turno = base.turno(v.descricao) if v.tipo in perfil.CONTEUDO else None
    if turno:
        d["tu"] = turno
    venceu = sim_venceu(v)
    if venceu is not None:
        d["apv"] = 1 if venceu else 0
    if conta.get(base.ABSTENCAO):
        d["a"] = conta[base.ABSTENCAO]
    sentido = sentido_do_sim(v)
    if sentido:
        d["ss"] = sentido
    if leitura.governo:
        d["g"] = leitura.governo
    if leitura.esquerda:
        d["e"] = leitura.esquerda
    if leitura.direita:
        d["dr"] = leitura.direita
    if leitura.divisiva and v.tipo in perfil.CONTEUDO:
        d["dv"] = 1
    if leitura.racha:
        d["rc"] = dict(leitura.racha)
    polos = regras.polos_em(v.data)
    if polos is not None:
        # [partido, Sim, Não, polo]: os da esquerda e depois os da direita, do maior para o menor
        do_polo = []
        for nome, siglas in (("e", polos.esquerda), ("d", polos.direita)):
            presentes = [(s, contagem[s]) for s in siglas if s in contagem and sum(contagem[s])]
            presentes.sort(key=lambda par: (-sum(par[1]), par[0]))
            do_polo += [[s, sim, nao, nome] for s, (sim, nao) in presentes]
        if do_polo:
            d["pp"] = do_polo
        if v.tipo in perfil.CONTEUDO:
            dos_polos = polos.esquerda | polos.direita
            demais = sorted(
                ((s, lado, parte, n) for s, (lado, parte, n) in leitura.partidos.items() if s not in dos_polos),
                key=lambda t: (-t[3], t[0]),
            )[:PARTIDOS]
            if demais:
                d["pt"] = [[s, lado, round(100 * parte)] for s, lado, parte, _n in demais]
    return d


def do_plenario(anos: Iterable[AnoVotos], regras: Regras, legislatura: int) -> dict[str, list[tuple[AnoVotos, int]]]:
    """Matéria (``"PEC 3/2021"``) → as votações nominais abertas de Plenário dela na
    legislatura, de todos os tipos, em ordem."""
    por: dict[str, list[tuple[AnoVotos, int]]] = defaultdict(list)
    for ano in anos:
        for k, v in enumerate(ano.votacoes):
            leg = regras.legislatura_em(v.data)
            if v.plenario and not v.secreta and v.prop and leg is not None and leg.numero == legislatura:
                por[titulo_da_materia(v.prop)].append((ano, k))
    for lista in por.values():
        lista.sort(key=lambda par: (par[0].votacoes[par[1]].data, par[0].votacoes[par[1]].hora, par[0].votacoes[par[1]].id))
    return dict(por)


# ── uma matéria ──────────────────────────────────────────────────────────────


def da_proposicao(p: Proposicao, nomes: Mapping[int, str], fichas: Mapping[int, str]) -> dict[str, Any]:
    """O que o registro da matéria diz. ``nomes``: id → nome do parlamentar; ``fichas``:
    id → chave do dossiê, para quem tem um."""
    d: dict[str, Any] = {"id": p.id, "e": p.inteira or p.ementa, "u": p.url, "est": p.estado}
    if p.original:
        d["eo"] = p.original
    if p.apelido:
        d["ap"] = p.apelido
    if p.explicacao:
        d["ex"] = p.explicacao
    termos = projetos.termos(p.chaves, TERMOS)
    if termos:
        d["kw"] = termos
    if p.teor:
        d["ut"] = p.teor
    if p.apresentada:
        d["da"] = p.apresentada
    autor = p.primeiro if p.primeiro is not None else (p.autores[0] if p.autores else None)
    if autor is not None and nomes.get(autor):
        outros = len([a for a in p.autores if a != autor])
        d["au"] = nomes[autor] + (f" e mais {outros}" if outros else "")
        if fichas.get(autor):
            d["auk"] = fichas[autor]
    elif p.autoria:
        d["au"] = p.autoria
    if p.origem:
        d["o"] = p.origem
    if p.partidos:
        d["pa"] = p.partidos[:AUTORES_PARTIDOS]
    if p.temas:
        d["tm"] = p.temas
    if p.situacao:
        d["sit"] = p.situacao
    if p.fila:
        d["f"] = p.fila
    if p.orgao:
        d["org"] = p.orgao
    if p.movimento:
        d["mov"] = p.movimento
    if p.urgencia:
        d["urg"] = 1
    if p.relator is not None and nomes.get(p.relator):
        d["rl"] = nomes[p.relator]
        if fichas.get(p.relator):
            d["rlk"] = fichas[p.relator]
    return d


def _nota(nota: relevancia.Nota, quando: str, **extra: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"q": quando, "n": nota.valor, "pt": nota.pontos}
    if nota.demanda is not None:
        d["dem"] = nota.demanda
    if nota.simbolica:
        d["simb"] = 1
    d.update({k: v for k, v in extra.items() if v not in (None, "", {})})
    return d


def montar(
    casa: str,
    anos: Iterable[AnoVotos],
    regras: Regras,
    legislatura: int,
    hoje: str,
    *,
    por_id: Mapping[int, Proposicao],
    esperando: Iterable[Proposicao] = (),
    aprovadas: Iterable[Aprovada] = (),
    regua: Regua | None = None,
    citadas: Mapping[str, int] | None = None,
    nomes: Mapping[int, str] | None = None,
    fichas: Mapping[int, str] | None = None,
    detalhe: Callable[[Iterable[int]], Mapping[int, Proposicao]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """As fichas das matérias de uma Casa e, à parte, a autoria cruzada
    (:func:`autoria_cruzada`), que vai para o painel da Casa.

    ``por_id``: as proposições conhecidas pela chave que a votação guarda (na Câmara, o id
    da proposição; no Senado, o id do processo). ``detalhe``: busca as que faltam (o Senado
    guarda o detalhe de cada processo): ``detalhe(ids) → {id: Proposicao}``.
    """
    nomes, fichas, citadas = nomes or {}, fichas or {}, citadas or {}
    anos = list(anos)
    votadas = do_plenario(anos, regras, legislatura)
    esperando, aprovadas = list(esperando), list(aprovadas)

    # A proposição de cada matéria: a do painel (que tem a nota), a conhecida, a do detalhe.
    props: dict[str, Proposicao] = {}
    for p in [*esperando, *(a.prop for a in aprovadas)]:
        props.setdefault(p.titulo, p)
    faltam: dict[int, str] = {}
    for titulo, lista in votadas.items():
        if titulo in props:
            continue
        ident = anos_votacao(lista).prop_id
        if ident is not None and ident in por_id:
            props[titulo] = por_id[ident]
        elif ident is not None:
            faltam[ident] = titulo
    # No Senado, as listas não trazem a explicação da ementa nem a indexação: o detalhe traz.
    pedir = set(faltam)
    if casa == "sf":
        pedir |= {p.processo for p in props.values() if p.processo and not (p.explicacao or p.chaves)}
    if detalhe is not None and pedir:
        achadas = detalhe(sorted(pedir))
        for ident, titulo in faltam.items():
            if ident in achadas:
                props[titulo] = achadas[ident]
        for p in props.values():
            completa = achadas.get(p.processo or -1)
            if completa is not None and completa is not p:
                p.explicacao = p.explicacao or completa.explicacao
                p.chaves = p.chaves or completa.chaves
                p.teor = p.teor or completa.teor
                p.inteira = p.inteira or completa.inteira
                if not p.partidos:
                    p.partidos = completa.partidos

    materias: dict[str, dict[str, Any]] = {}
    for titulo, p in props.items():
        if not chave(casa, titulo):
            continue
        if not p.apelido:
            p.apelido = regras.apelido_de(titulo)
        materias[titulo] = da_proposicao(p, nomes, fichas)
    for titulo, lista in votadas.items():
        if not chave(casa, titulo):
            continue
        ultima = anos_votacao(lista)
        m = materias.get(titulo)
        if m is None:  # sem registro da proposição: fica o que a votação diz
            m = materias[titulo] = {"e": ultima.ementa, "est": ""}
            apelido = regras.apelido_de(titulo)
            if apelido:
                m["ap"] = apelido
            if casa == "cd" and ultima.prop_id:
                m["id"] = ultima.prop_id
                m["u"] = f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={ultima.prop_id}"
        if not m.get("tm") and ultima.temas:
            m["tm"] = list(ultima.temas)
        m["vot"] = [votacao(ano.votacoes[k], ano, k, regras) for ano, k in lista]

    # Os sinais de relevância: a nota do painel para as que esperam e as que passaram; para
    # as demais votadas, a nota na data da última votação (só os tipos que a régua mede).
    if regua is not None:
        for p in esperando:
            if p.titulo in materias:
                materias[p.titulo]["rel"] = _nota(regua.nota(p, hoje), "espera")
        for a in aprovadas:
            if a.prop.titulo not in materias:
                continue
            dias = projetos.dias_entre(a.prop.apresentada, a.data)
            materias[a.prop.titulo]["rel"] = _nota(regua.nota(a.prop, a.data), "passou", apv=a.data, dias=dias, cam=a.caminho)
        for titulo, lista in votadas.items():
            m, p = materias.get(titulo), props.get(titulo)
            if m is None or p is None or "rel" in m or p.sigla not in relevancia.SIGLAS:
                continue
            m["rel"] = _nota(regua.nota(p, anos_votacao(lista).data), "votada", ref=anos_votacao(lista).data)
    for titulo, m in materias.items():
        if citadas.get(titulo):
            m["jor"] = citadas[titulo]

    polos = regras.polos_em(hoje)
    pesos = {}
    if regua is not None:
        validos = {k: w for k, w in regua.rel.pesos if w > 0 and (k != "demanda" or regua.filhos is not None)}
        total = sum(validos.values()) or 1
        pesos = {k: round(100 * w / total) for k, w in validos.items()}
    ordem = sorted(materias, key=_ordem)
    return {
        "v": VERSAO,
        "casa": casa,
        "legislatura": legislatura,
        "polos": {"esquerda": sorted(polos.esquerda), "direita": sorted(polos.direita)} if polos else {},
        "pesos": pesos,
        "materias": {titulo: materias[titulo] for titulo in ordem},
    }, autoria_cruzada(materias, props, regras, hoje)


def autoria_cruzada(
    materias: Mapping[str, Mapping[str, Any]], props: Mapping[str, Proposicao], regras: Regras, hoje: str, lista: int = CRUZADA
) -> dict[str, Any]:
    """As matérias de autores de um polo e como o **outro** polo votou o texto principal.

    Responde à pergunta "ninguém de um lado vota a proposta do outro?": para cada matéria de
    parlamentares de partidos de um polo (sem coautor do outro) que teve o texto principal
    votado nominalmente no Plenário, o lado do outro polo na última dessas votações: a
    favor, contra, ou sem maioria clara. Devolve as contas e as em que o outro polo votou
    a favor, da mais recente."""
    polos = regras.polos_em(hoje)
    if polos is None:
        return {}
    saida: dict[str, Any] = {}
    for autor, meus, outros, campo in (("esquerda", polos.esquerda, polos.direita, "dr"), ("direita", polos.direita, polos.esquerda, "e")):
        conta = {"n": 0, "favor": 0, "contra": 0, "dividido": 0}
        itens = []
        for titulo, m in materias.items():
            p = props.get(titulo)
            partidos = set(p.partidos) if p is not None else set()
            if not partidos & meus or partidos & outros:
                continue
            principais = [v for v in m.get("vot") or [] if v["t"] == base.MERITO]
            if not principais:
                continue
            v = principais[-1]
            lado = v.get(campo, "")
            conta["n"] += 1
            conta["favor" if lado == base.SIM else "contra" if lado == base.NAO else "dividido"] += 1
            if lado == base.SIM:
                item = {"t": titulo, "e": text.truncate(m.get("e") or "", 200), "d": v["d"], "pa": sorted(partidos & meus)}
                if m.get("ap"):
                    item["ap"] = m["ap"]
                if v.get("apv") is not None:
                    item["apv"] = v["apv"]
                itens.append(item)
        itens.sort(key=lambda i: (i["d"], i["t"]), reverse=True)
        saida[autor] = {**conta, "itens": itens[:lista]}
    return saida


def anos_votacao(lista: list[tuple[AnoVotos, int]]) -> Votacao:
    """A última votação de uma lista ``[(ano, índice)]`` (a mais recente)."""
    ano, k = lista[-1]
    return ano.votacoes[k]


def _ordem(titulo: str) -> tuple[str, int, int]:
    achado = _SLUG.match(titulo)
    return (achado.group(1), int(achado.group(3)), int(achado.group(2))) if achado else (titulo, 0, 0)


def serializar(dados: Mapping[str, Any]) -> str:
    """O arquivo com uma matéria por linha (o git guarda só o que mudou)."""
    cabeca = json.dumps({k: v for k, v in dados.items() if k != "materias"}, ensure_ascii=False, separators=(",", ":"))[:-1]
    linhas = ",\n".join(
        f"{json.dumps(titulo, ensure_ascii=False)}:{json.dumps(m, ensure_ascii=False, separators=(',', ':'))}"
        for titulo, m in dados["materias"].items()
    )
    return f'{cabeca},"materias":{{\n{linhas}\n}}}}\n'
