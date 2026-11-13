"""A ficha de um parlamentar: o que vai em ``data/congresso/fichas/<casa>-<id>.json``
e a linha dele em ``resumo.json``.

A ficha junta o perfil de votos (:mod:`.perfil`), o que ele propõe e relata
(:mod:`.projetos`) e o que o jornal publicou (:mod:`.jornal`), e abre com uma
**frase** montada dos números (sem IA): com quem vota, quanto acompanha o
governo, quanto comparece e o que fez das próprias propostas.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .. import text
from . import base
from .perfil import Eixo, Fracao, Marca, Mudanca, Perfil, Recorte
from .regras import Regras

FOTO_CAMARA = "https://www.camara.leg.br/internet/deputado/bandep/{id}.jpg"
PAGINA_CAMARA = "https://www.camara.leg.br/deputados/{id}"
FOTO_SENADO = "https://www.senado.leg.br/senadores/img/fotos-oficiais/senador{id}.jpg"
PAGINA_SENADO = "https://www25.senado.leg.br/web/senadores/senador/-/perfil/{id}"
MARCA_DESCRICAO = 170  # a descrição da votação e a ementa, curtas, em cada voto listado na ficha
MARCA_EMENTA = 150
VOTO = {
    base.SIM: "Sim",
    base.NAO: "Não",
    base.ABSTENCAO: "Abstenção",
    base.OBSTRUCAO: "Obstrução",
    base.PRESIDIU: "Presidia",
    base.SEM_VOTO: "Presente, sem voto",
    base.SECRETO: "Votou (secreta)",
    base.JUSTIFICADA: "Ausência justificada",
    base.FALTA: "Faltou",
    base.SEM_REGISTRO: "Sem registro",
}


@dataclass
class Cadastro:
    """Quem é a pessoa hoje, pelo registro da Casa."""

    id: int
    casa: str
    nome: str
    partido: str = ""
    uf: str = ""
    exercicio: bool = False  # em exercício hoje
    foto: str = ""
    pagina: str = ""
    condicao: str = ""  # "Titular", "1º Suplente"…
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def chave(self) -> str:
        return f"{self.casa}-{self.id}"


def pct(valor: float | None, casas: int = 0) -> float | int | None:
    if valor is None:
        return None
    return round(valor, casas) if casas else round(valor)


def fracao(f: Fracao) -> dict[str, Any] | None:
    return {"k": f.k, "n": f.n, "pct": pct(f.pct, 1)} if f.n else None


def eixo(e: Eixo) -> dict[str, Any] | None:
    return {"esq": e.esq, "dir": e.dir, "n": e.n, "saldo": pct(e.saldo)} if e.n else None


def recorte(r: Recorte, *, presenca: bool = True) -> dict[str, Any]:
    d: dict[str, Any] = {"sim": r.sim, "nao": r.nao, "abst": r.abstencao, "obst": r.obstrucao}
    if presenca and r.possiveis:
        d["pres"] = {
            "n": r.possiveis,
            "votou": r.votou,
            "sem_voto": r.sem_voto,
            "just": r.justificadas,
            "faltas": r.faltas,
            "pct": pct(r.presenca, 1),
        }
    for nome, valor in (("gov", fracao(r.governo)), ("eixo", eixo(r.eixo)), ("fid", fracao(r.partido))):
        if valor:
            d[nome] = valor
    return d


def marca(m: Marca) -> dict[str, Any]:
    v = m.votacao
    d: dict[str, Any] = {"id": v.id, "d": v.data, "t": v.tipo, "x": text.truncate(v.descricao, MARCA_DESCRICAO), "v": m.voto}
    if v.prop:
        d["p"] = v.prop
    if v.ementa:
        d["e"] = text.truncate(v.ementa, MARCA_EMENTA)
    if v.aprovada is not None:
        d["ap"] = 1 if v.aprovada else 0
    governo = v.ori.get(base.GOVERNO)
    if governo:
        d["g"] = governo
    if m.partido:
        d["pt"] = m.partido
    if m.lado_partido:
        d["pm"] = m.lado_partido
        d["c"] = round(100 * m.coesao)
    if m.com:
        d["com"] = m.com
    return d


def _n(valor: int, singular: str, plural: str) -> str:
    return f"{text.format_number_pt(valor, 0)} {singular if valor == 1 else plural}"


def _p(valor: float) -> str:
    """Percentual por extenso, sem fingir tudo ou nada: 99,6% não vira 100%."""
    if 99.5 <= valor < 100 or 0 < valor < 0.5:
        return f"{text.format_number_pt(valor, 1)}%"
    return f"{round(valor)}%"


def frase(
    casa: str,
    atual: Recorte | None,
    governos: Mapping[str, Recorte],
    governo_atual: str | None,
    autoria: Mapping[str, Any] | None,
    relatorias: Mapping[str, Any] | None,
    regras: Regras,
) -> list[str]:
    """O perfil em até cinco frases curtas, cada uma com o número que a sustenta."""
    lim = regras.limiares
    frases: list[str] = []
    if atual is not None and atual.eixo.n >= lim.minimo_divisivas:
        saldo = atual.eixo.saldo or 0.0
        lado, vezes = ("direita", atual.eixo.dir) if saldo >= 0 else ("esquerda", atual.eixo.esq)
        parte = 100.0 * vezes / atual.eixo.n
        # A frase segue a faixa do campo (as mesmas de config/congresso.yaml), para dizer o
        # mesmo que o rótulo ao lado: "dividido", "mais com a…", "com a…" e, além de ±80,
        # "quase sempre com a…".
        faixa = regras.campo(saldo).id
        if faixa == "centro":
            frases.append(
                f"Divide-se entre os polos: nas {_n(atual.eixo.n, 'votação', 'votações')} de conteúdo em que esquerda e "
                f"direita divergiram, votou com a direita em {_p(100.0 * atual.eixo.dir / atual.eixo.n)}."
            )
        else:
            frases.append(
                f"Vota {'quase sempre ' if abs(saldo) >= 80 else '' if faixa in ('esquerda', 'direita') else 'mais '}com a {lado}: "
                f"ficou desse lado em {_p(parte)} das "
                f"{_n(atual.eixo.n, 'votação', 'votações')} de conteúdo em que os polos divergiram."
            )
    do_governo = governos.get(governo_atual or "")
    if do_governo is not None and do_governo.governo.n >= lim.minimo_divisivas:
        frases.append(
            f"Seguiu a orientação do governo {governo_atual} em {_p(do_governo.governo.pct or 0.0)} das "
            f"{_n(do_governo.governo.n, 'votação', 'votações')} em que ela existiu."
        )
    if atual is not None and atual.possiveis >= lim.minimo_presenca and atual.presenca is not None:
        frases.append(
            f"Votou em {_p(atual.presenca)} das {_n(atual.possiveis, 'votação nominal', 'votações nominais')} em que podia votar."
        )
    if autoria and autoria.get("total"):
        estados = autoria.get("estados") or {}
        partes = []
        if estados.get("norma"):
            partes.append(_n(estados["norma"], "virou norma", "viraram norma"))
        if estados.get("aprovada"):
            partes.append(_n(estados["aprovada"], "passou na Casa", "passaram na Casa"))
        if autoria.get("paradas_n"):
            partes.append(_n(autoria["paradas_n"], "está parada há mais de um ano", "estão paradas há mais de um ano"))
        desde = str(autoria.get("desde") or "")[:4]
        frases.append(
            f"Tem {_n(autoria['total'], 'proposição', 'proposições')} de autoria principal"
            + (f" desde {desde}" if desde else "")
            + (": " + ", ".join(partes) + "." if partes else ".")
        )
    if relatorias and relatorias.get("total"):
        antigas = relatorias.get("antigas_n") or 0
        frases.append(
            f"Tem {_n(relatorias['total'], 'matéria esperando', 'matérias esperando')} o seu parecer"
            + (f", {antigas} há mais de {lim.dias_relatoria // 30} meses." if antigas else ".")
        )
    return frases


def montar(
    cadastro: Cadastro,
    perfil: Perfil | None,
    regras: Regras,
    *,
    legislatura: int,
    governo_atual: str | None,
    autoria: Mapping[str, Any] | None = None,
    relatorias: Mapping[str, Any] | None = None,
    jornal: Mapping[str, Any] | None = None,
    mudanca: Mudanca | None = None,
    media_partido: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A ficha completa de uma pessoa (o dicionário que vira JSON)."""
    lim = regras.limiares
    atual = perfil.legislaturas.get(legislatura) if perfil else None
    governos = perfil.governos if perfil else {}
    ficha: dict[str, Any] = {
        "k": cadastro.chave,
        "casa": cadastro.casa,
        "id": cadastro.id,
        "nome": cadastro.nome,
        "partido": cadastro.partido,
        "uf": cadastro.uf,
        "exercicio": cadastro.exercicio,
        "foto": cadastro.foto,
        "pagina": cadastro.pagina,
    }
    if cadastro.condicao:
        ficha["condicao"] = cadastro.condicao
    if cadastro.extra:
        ficha["extra"] = cadastro.extra
    ficha["frases"] = frase(cadastro.casa, atual, governos, governo_atual, autoria, relatorias, regras)
    if perfil is not None:
        ficha["partidos"] = [list(p) for p in perfil.partidos]
        ficha["ultimo_voto"] = perfil.ultimo_voto
        ficha["leg"] = {str(numero): recorte(r) for numero, r in sorted(perfil.legislaturas.items())}
        ficha["gov"] = {nome: fracao(r.governo) for nome, r in perfil.governos.items() if r.governo.n}
        ficha["sem"] = [
            {"s": s, **recorte(r)} for s, r in sorted(perfil.semestres.items()) if r.possiveis or r.sim + r.nao
        ]
        temas = perfil.temas.get(legislatura, {})
        ficha["temas"] = [
            {"t": tema, "nome": regras.nome_do_tema(tema), **recorte(r, presenca=False)}
            for tema, r in sorted(temas.items(), key=lambda kv: -(kv[1].sim + kv[1].nao))
        ]
        ficha["tipos"] = {
            tipo: recorte(r, presenca=False) for tipo, r in sorted(perfil.tipos.get(legislatura, {}).items())
        }
        if atual is not None and atual.eixo.n >= lim.minimo_divisivas and atual.eixo.saldo is not None:
            campo = regras.campo(atual.eixo.saldo)
            ficha["campo"] = {"id": campo.id, "nome": campo.nome, "saldo": pct(atual.eixo.saldo), "n": atual.eixo.n}
            # Quem trocou de partido na legislatura: o saldo só dos votos dados no partido atual
            # (é esse que se compara com a média da bancada).
            por_partido = perfil.eixos_por_partido.get(legislatura, {})
            if cadastro.partido and set(por_partido) - {cadastro.partido}:
                no_atual = por_partido.get(cadastro.partido) or Eixo()
                ficha["campo"]["no_partido"] = {"saldo": pct(no_atual.saldo), "n": no_atual.n}
        ficha["dissidencias"] = [marca(m) for m in perfil.dissidencias]
        ficha["surpresas"] = [marca(m) for m in perfil.surpresas]
        ficha["recentes"] = [marca(m) for m in perfil.recentes]
    if mudanca is not None:
        ficha["mudanca"] = {
            "semestre": mudanca.semestre,
            "antes": fracao(mudanca.antes),
            "agora": fracao(mudanca.agora),
            "pontos": round(mudanca.pontos),
        }
    if media_partido:
        ficha["partido_media"] = dict(media_partido)
    if autoria:
        ficha["autoria"] = dict(autoria)
    if relatorias:
        # A contagem de dias muda todo dia; a ficha guarda as datas e a página faz a conta.
        ficha["relatorias"] = {k: v for k, v in relatorias.items() if not k.endswith("_dias")}
    if jornal:
        ficha["jornal"] = dict(jornal)
    return ficha


def saldo_no_partido(ficha: Mapping[str, Any], minimo: int) -> float | None:
    """O saldo que se compara com a média do partido atual: o da legislatura, ou, para quem
    trocou de partido, só o dos votos dados no partido atual (``None`` se forem poucos)."""
    campo = ficha.get("campo") or {}
    no_partido = campo.get("no_partido")
    if no_partido is None:
        return campo.get("saldo")
    return no_partido.get("saldo") if (no_partido.get("n") or 0) >= minimo else None


def linha(ficha: Mapping[str, Any], legislatura: int) -> dict[str, Any]:
    """A linha da pessoa no índice (``resumo.json``): o que a tabela, o mapa e as
    fichas dentro das matérias precisam, sem abrir a ficha inteira."""
    atual = (ficha.get("leg") or {}).get(str(legislatura)) or {}
    autoria = ficha.get("autoria") or {}
    relatorias = ficha.get("relatorias") or {}
    jornal = ficha.get("jornal") or {}
    campo = ficha.get("campo") or {}
    governo = next(reversed(list((ficha.get("gov") or {}).values())), None) if ficha.get("gov") else None
    d: dict[str, Any] = {
        "k": ficha["k"],
        "nome": ficha["nome"],
        "partido": ficha.get("partido") or "",
        "uf": ficha.get("uf") or "",
        "ex": 1 if ficha.get("exercicio") else 0,
        "foto": ficha.get("foto") or "",
    }
    if campo:
        d["saldo"] = campo["saldo"]
        d["campo"] = campo["id"]
        d["div"] = campo["n"]
    if governo:
        d["gov"] = governo["pct"]
    if atual.get("pres"):
        d["pres"] = atual["pres"]["pct"]
        d["vot"] = atual["pres"]["n"]
    if atual.get("fid"):
        d["fid"] = atual["fid"]["pct"]
    if autoria.get("total"):
        d["prop"] = autoria["total"]
        d["par"] = autoria.get("paradas_n") or 0
        d["norma"] = (autoria.get("estados") or {}).get("norma") or 0
    if relatorias.get("total"):
        d["rel"] = relatorias["total"]
        d["rel_ant"] = relatorias.get("antigas_n") or 0
    if jornal.get("n"):
        d["jor"] = jornal.get("n30") or 0
    if ficha.get("mudanca"):
        d["mud"] = ficha["mudanca"]["pontos"]
    if any(not fim for _inicio, fim in ficha.get("presidiu") or []):
        d["preside"] = 1  # preside a Casa hoje: só vota em caso de empate
    if ficha.get("frases"):
        d["frase"] = ficha["frases"][0]
    return d
