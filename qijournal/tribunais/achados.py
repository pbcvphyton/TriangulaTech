"""A leitura do dia da aba Tribunais (``data/tribunais/achados.json``, contrato §4).

Como o Congresso: cada achado é uma frase com número, unidade e janela (``titulo``), a prova em
uma ou duas frases (``texto``), o endereço da prova na página (``alvo``, o estado do endereço:
``#v=ministros&t=stj&min=<id>&area=<id>``) e a fonte oficial do dia (``prova``). A manchete é o
de maior ``peso`` = ``pesos_achados[tipo] × log10(1 + n)``, com ``n`` o número do achado.

Os tipos (analista jurídico §7.2):

- ``heranca`` — atribuição de ``achado_heranca`` (300) processos ou mais a um relator num dia da
  última semana (atas): herança de acervo, não sorteio;
- ``mesa_antiga`` — em cada Turma (STJ) ou grupo (STF), o ministro com a maior parte da mesa sem
  decisão (STJ) ou conclusa (STF) há mais de um ano, **se** a diferença para o resto da Turma passa
  no teste (binomial exata, Holm a 99%) e no piso de efeito;
- ``fora_do_padrao`` — a marca da matriz ministro × área com mais processos (o maior peso; no
  empate, o maior afastamento), só o que passou no teste e no piso, de ministro comparável;
- ``litigante`` — grupo cuja entrada da semana (atas) passa de 3 vezes a média semanal das 12
  anteriores, com 100 processos ou mais (precisa de 13 semanas de atas);
- ``tema`` — tema repetitivo ou IAC afetado ou julgado na última semana (STJ);
- ``tema_quente`` — subárea cuja entrada de 30 dias passa de 2 vezes a mediana mensal dos 12 meses
  anteriores, com 50 ou mais (precisa de 13 meses de atas);
- ``vista`` (STF) — o ministro com mais processos com pedido de vista na pauta;
- ``entrou`` (STF) — o ministro que mais recebeu no último dia de ata da semana.

Regras: um achado por ministro por dia (o de maior peso); nenhum sobre pessoa física nem sobre
processo em segredo (aqui só há agregados); comparação de pessoas só se passou no teste e o
ministro é comparável; frase sem adjetivo.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from . import VERSAO
from .modelo import Regras

SELOS = {"heranca": "MESA", "entrou": "MESA", "mesa_antiga": "FILA", "fora_do_padrao": "ÁREA", "tema_quente": "ÁREA",
         "litigante": "PARTE", "vista": "VISTA", "tema": "TEMA"}
MAXIMO = 20  # achados no arquivo
_EXTENSO = {1: "um", 2: "dois", 3: "três", 4: "quatro", 5: "cinco", 6: "seis", 7: "sete", 8: "oito", 9: "nove", 10: "dez"}
DIAS_DA_SEMANA = 7  # "da semana": o achado de fluxo vale por 7 dias


# ── formatação (pt-BR, como a página) ────────────────────────────────────────────────────────


def numero(n: float) -> str:
    """``1014`` → ``"1.014"``."""
    return f"{int(n):,}".replace(",", ".")


def pct(p: float | None, casas: int = 1) -> str:
    """``0.0868`` → ``"8,7%"``."""
    if p is None:
        return "—"
    return f"{p * 100:.{casas}f}".replace(".", ",") + "%"


def vezes(r: float) -> str:
    """``1.53`` → ``"1,5 vez"``; ``2.4`` → ``"2,4 vezes"``."""
    texto = f"{r:.1f}".replace(".", ",")
    return f"{texto} {'vez' if r < 2 else 'vezes'}"


def data_br(iso: str | None, *, ano: bool = True) -> str:
    """``"2026-11-19"`` → ``"19/11/2026"`` (ou ``"19/11"``)."""
    if not iso:
        return ""
    a, m, d = iso[:10].split("-")
    return f"{d}/{m}/{a}" if ano else f"{d}/{m}"


def extenso(n: int) -> str:
    return _EXTENSO.get(n, numero(n))


def peso(regras: Regras, tipo: str, n: float) -> float:
    base = float((regras.limiares.get("pesos_achados") or {}).get(tipo, 1))
    return round(base * math.log10(1 + max(0.0, float(n))), 2)


# ── as provas ────────────────────────────────────────────────────────────────────────────────

_FONTES_STF = {"stf_acervo": "acervo", "stf_rg": "rg_temas"}
_URL_STF = {"stf_atas": "https://portal.stf.jus.br/atadistribuicao/", "stf_pauta": "https://portal.stf.jus.br/pauta/",
            "stf_jurisprudencia": "https://jurisprudencia.stf.jus.br/"}
_CONJUNTO_STJ = {"stj_acervo": "acervo", "stj_atas": "atas", "stj_pautas": "pautas", "stj_precedentes": "precedentes",
                 "stj_integras": "integras"}


def url_da_fonte(regras: Regras, fonte: str) -> str | None:
    """A página oficial de uma fonte de ``meta.fontes`` (para o leitor conferir)."""
    cfg = regras.fontes
    if fonte in _CONJUNTO_STJ:
        stj = cfg.get("stj") or {}
        slug = (stj.get("conjuntos") or {}).get(_CONJUNTO_STJ[fonte])
        modelo = stj.get("pagina_conjunto") or "https://dadosabertos.web.stj.jus.br/dataset/{slug}"
        return modelo.format(slug=slug) if slug else None
    if fonte in _FONTES_STF:
        return (cfg.get("stf") or {}).get(_FONTES_STF[fonte])
    return _URL_STF.get(fonte)


def _prova(regras: Regras, fonte: str, data: str | None) -> dict[str, Any]:
    return {"fonte": fonte, "data": data, "url": url_da_fonte(regras, fonte)}


def _achado(regras: Regras, tipo: str, trib: str, n: float, titulo: str, texto: str, alvo: str, ministro: str | None,
            fonte: str, data: str | None) -> dict[str, Any]:
    return {"tipo": tipo, "trib": trib, "peso": peso(regras, tipo, n), "selo": SELOS[tipo], "titulo": titulo, "texto": texto,
            "alvo": alvo, "ministro": ministro, "prova": _prova(regras, fonte, data)}


# ── por tipo ─────────────────────────────────────────────────────────────────────────────────


def _ministros(painel: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {m["id"]: m for m in painel.get("ministros") or ()}


def _turma(regras: Regras, trib: str, m: Mapping[str, Any]) -> str:
    return str(regras.composicao[trib].orgaos.get(m.get("turma") or "", m.get("turma") or "a Turma"))


def _colegas(regras: Regras, trib: str, m: Mapping[str, Any], n_pares: int) -> str:
    """"os colegas da Quarta Turma" (STJ) / "os outros ministros do tribunal, sem a Presidência" (STF)."""
    if trib == "stj":
        return f"nos outros {extenso(n_pares)} gabinetes da {_turma(regras, trib, m)}"
    return f"nos outros {extenso(n_pares)} gabinetes do tribunal (sem a Presidência)"


def _pares_de(regras: Regras, trib: str, painel: Mapping[str, Any], mid: str) -> int:
    ministros = _ministros(painel)
    m = ministros.get(mid) or {}
    grade = [x for x in ministros.values() if x.get("grupo") != "pr" and x["id"] != mid]
    if trib == "stj":
        return sum(1 for x in grade if x.get("turma") == m.get("turma"))
    return len(grade)


def achados_heranca(regras: Regras, trib: str, painel: Mapping[str, Any], sinais: Mapping[str, Any], hoje: date) -> list[dict[str, Any]]:
    minimo = int(regras.limiares.get("achado_heranca", 300))
    ministros = _ministros(painel)
    fonte = "stj_atas" if trib == "stj" else "stf_atas"
    saida = []
    for mid, dias in sorted((sinais.get("heranca") or {}).items()):
        m = ministros.get(mid)
        if m is None:
            continue
        recentes = [(d, n) for d, n in dias if n >= minimo and (hoje - date.fromisoformat(d)).days < DIAS_DA_SEMANA]
        if not recentes:
            continue
        dia, n = max(recentes, key=lambda x: (x[1], x[0]))
        quem = m["nome"]
        conv = m.get("convocado")
        if conv:
            artigo = "convocada" if m.get("genero") == "f" else "convocado"
            desde = f" desde {data_br(conv.get('desde'), ano=False)}" if conv.get("desde") else ""
            quem += f", {artigo} do {conv.get('tribunal')} na {_turma(regras, trib, m)}{desde},"
        ela, dela = ("ela", "dela") if m.get("genero") == "f" else ("ele", "dele")
        titulo = f"{quem} recebeu {numero(n)} processos por atribuição em {data_br(dia)}"
        texto = (f"A atribuição é herança de acervo, não sorteio: aparece à parte e não soma no que {ela} recebeu por "
                 f"distribuição. O acervo {dela} em {data_br(painel.get('data_dados'))}: {numero(m.get('processos') or 0)} processos.")
        saida.append(_achado(regras, "heranca", trib, n, titulo, texto, f"#v=ministros&t={trib}&min={mid}", mid, fonte, dia))
    return saida


def achados_mesa(regras: Regras, trib: str, painel: Mapping[str, Any], sinais: Mapping[str, Any]) -> list[dict[str, Any]]:
    ministros = _ministros(painel)
    data = painel.get("data_dados")
    saida = []
    for v in sinais.get("mesa") or ():
        if not v.get("passa"):
            continue
        m = ministros.get(v["ministro"])
        if m is None or not m.get("comparavel"):
            continue
        n_pares = sum(1 for x in ministros.values() if x.get("grupo") != "pr" and x["id"] != v["ministro"]
                      and x.get("turma") == m.get("turma"))
        if trib == "stj":
            titulo = (f"{m['nome']}: {numero(v['k'])} dos {numero(v['n'])} processos na mesa estão sem decisão registrada há "
                      f"mais de um ano ({pct(v['p'], 0)})")
            nota = ("\"Sem decisão registrada\" conta da última distribuição ou decisão, a mais recente; sobrestados ficam fora. "
                    "Não é a data em que o processo foi concluso.")
            grupo = f"da {_turma(regras, trib, m)}"
        else:
            titulo = (f"{m['nome']}: {numero(v['k'])} dos {numero(v['n'])} processos conclusos na mesa estão conclusos há "
                      f"mais de um ano ({pct(v['p'], 0)})")
            nota = "Conta do andamento \"Conclusos ao(à) Relator(a)\" da planilha do STF; sobrestados ficam fora."
            grupo = f"da {_turma(regras, trib, m)}" if m.get("turma") else "do grupo"
        texto = (f"Em {data_br(data)}. Nos outros {extenso(n_pares)} gabinetes {grupo}, {pct(v['pares'])} "
                 f"({numero(v['pares_k'])} de {numero(v['pares_n'])}). Diferença que passa do acaso (binomial exata, 99%, Holm). {nota}")
        saida.append(_achado(regras, "mesa_antiga", trib, v["k"], titulo, texto, f"#v=ministros&t={trib}&min={v['ministro']}",
                             v["ministro"], f"{trib}_acervo", data))
    return saida


def achados_area(regras: Regras, trib: str, painel: Mapping[str, Any], sinais: Mapping[str, Any]) -> list[dict[str, Any]]:
    ministros = _ministros(painel)
    data = painel.get("data_dados")
    melhor: dict[str, Mapping[str, Any]] = {}
    for x in sinais.get("marcas") or ():
        if not x.get("marca"):
            continue
        m = ministros.get(x["ministro"])
        if m is None or not m.get("comparavel"):
            continue
        atual = melhor.get(x["ministro"])
        # a marca que vira achado é a de maior peso (mais processos); no empate, o maior afastamento
        if atual is None or (x["k"], abs(math.log(x["razao"])), x["area"]) > (atual["k"], abs(math.log(atual["razao"])), atual["area"]):
            melhor[x["ministro"]] = x
    saida = []
    for mid, x in sorted(melhor.items()):
        m = ministros[mid]
        area = regras.area(x["area"]).nome_curto
        n_pares = _pares_de(regras, trib, painel, mid)
        alvo_pares = (f"dos colegas da {_turma(regras, trib, m)}" if trib == "stj"
                      else "dos outros ministros do tribunal (sem a Presidência)")
        titulo = f"{area} é {pct(x['p'])} do acervo de {m['nome']}, {vezes(x['razao'])} a parte {alvo_pares}"
        texto = (f"{numero(x['k'])} de {numero(x['n'])} processos em {data_br(data)}; {_colegas(regras, trib, m, n_pares)}, "
                 f"{pct(x['pares'])}. Diferença que passa do acaso (binomial exata, 99%, Holm).")
        saida.append(_achado(regras, "fora_do_padrao", trib, x["k"], titulo, texto,
                             f"#v=ministros&t={trib}&min={mid}&area={x['area']}", mid, f"{trib}_acervo", data))
    return saida


def achados_vista(regras: Regras, trib: str, painel: Mapping[str, Any], sinais: Mapping[str, Any]) -> list[dict[str, Any]]:
    vistas = sinais.get("vistas") or {}
    if trib != "stf" or not vistas:
        return []
    ministros = _ministros(painel)
    mid, n = min(vistas.items(), key=lambda kv: (-kv[1], kv[0]))
    m = ministros.get(mid)
    if m is None:
        return []
    janela = sinais.get("pauta_janela") or [None, None]
    periodo = f" de {data_br(janela[0], ano=False)} a {data_br(janela[1])}" if janela[0] else ""
    total = sum(vistas.values())
    titulo = f"{m['nome']} pediu vista em {numero(n)} {'processo' if n == 1 else 'processos'} da pauta presencial{periodo}"
    texto = (f"São {numero(total)} processos com vista na pauta, de {len(vistas)} {'ministro' if len(vistas) == 1 else 'ministros'}. "
             "A pauta não traz a data do pedido de vista: o prazo não é contado aqui.")
    return [_achado(regras, "vista", trib, n, titulo, texto, f"#v=fila&t={trib}&min={mid}", mid, "stf_pauta", janela[0])]


def achados_entrou(regras: Regras, trib: str, painel: Mapping[str, Any], sinais: Mapping[str, Any], hoje: date) -> list[dict[str, Any]]:
    if trib != "stf":
        return []
    por_dia = sinais.get("entradas_dia") or {}
    dias = [d for d in sorted(por_dia) if (hoje - date.fromisoformat(d)).days < DIAS_DA_SEMANA and (sinais.get("total_dia") or {}).get(d)]
    if not dias:
        return []
    dia = dias[-1]
    ministros = _ministros(painel)
    contagem = {mid: int(c.get("sorteio", 0)) + int(c.get("heranca", 0)) for mid, c in por_dia[dia].items() if mid in ministros}
    if not contagem:
        return []
    mid, n = min(contagem.items(), key=lambda kv: (-kv[1], kv[0]))
    total = int((sinais.get("total_dia") or {}).get(dia) or 0)
    m = ministros[mid]
    titulo = f"{m['nome']} recebeu {numero(n)} dos {numero(total)} processos distribuídos em {data_br(dia)}"
    texto = ("Conta do totalizador oficial da ata (distribuídos e redistribuídos). A distribuição é por sorteio, entre os "
             "ministros sem a Presidência; prevenção e redistribuição explicam diferenças de um dia.")
    return [_achado(regras, "entrou", trib, n, titulo, texto, f"#v=ministros&t={trib}&min={mid}", mid, "stf_atas", dia)]


def achados_temas(regras: Regras, trib: str, painel: Mapping[str, Any], sinais: Mapping[str, Any]) -> list[dict[str, Any]]:
    ministros = _ministros(painel)
    saida = []
    for t in sinais.get("temas_recentes") or ():
        mid = t.get("relator_id")
        m = ministros.get(mid or "")
        quem = f", sob relatoria de {m['nome']}" if m else ""
        rotulo = {"repetitivo": "Tema repetitivo", "iac": "IAC"}.get(t.get("tipo"), "Tema")
        acao = "afetado" if t.get("evento") == "afetado" else "julgado"
        titulo = f"{rotulo} {numero(t.get('numero') or 0)} {acao} em {data_br(t.get('data'))}{quem}"
        texto = (t.get("titulo") or "").strip()
        saida.append(_achado(regras, "tema", trib, 1, titulo, texto, f"#v=ministros&t={trib}" + (f"&min={mid}" if m else ""),
                             mid if m else None, f"{trib}_precedentes" if trib == "stj" else "stf_rg", t.get("data")))
    return saida


def achados_quentes(regras: Regras, trib: str, painel: Mapping[str, Any], sinais: Mapping[str, Any]) -> list[dict[str, Any]]:
    saida = []
    for t in sinais.get("temas_quentes") or ():
        sub = regras.nome_subarea(t["subarea"])
        area = t["subarea"].split(".", 1)[0]
        titulo = f"{sub}: {numero(t['n'])} processos entraram no {trib.upper()} entre {data_br(t['janela'][0], ano=False)} e {data_br(t['janela'][1])}"
        texto = (f"A mediana dos 12 meses anteriores é de {numero(t['mediana'])} por mês. Conta das atas de distribuição "
                 "(sorteio e herança), pelo assunto principal.")
        saida.append(_achado(regras, "tema_quente", trib, t["n"], titulo, texto, f"#v=assuntos&t={trib}&area={area}", None,
                             f"{trib}_atas", t["janela"][1]))
    for x in sinais.get("litigantes_semana") or ():
        nome = ((painel.get("partes") or {}).get("nomes") or {}).get(x["grupo"], {}).get("nome")
        if not nome:
            continue
        titulo = f"{nome} aparece em {numero(x['n'])} processos que entraram no {trib.upper()} na última semana"
        texto = f"A média das 12 semanas anteriores é de {numero(round(x['media']))} por semana. Conta das atas de distribuição."
        saida.append(_achado(regras, "litigante", trib, x["n"], titulo, texto, f"#v=partes&t={trib}", None, f"{trib}_atas",
                             sinais.get("hoje")))
    return saida


# ── a leitura do dia ─────────────────────────────────────────────────────────────────────────


def montar(regras: Regras, paineis: Mapping[str, Mapping[str, Any]], sinais: Mapping[str, Mapping[str, Any]], *,
           hoje: date | str) -> dict[str, Any]:
    """``achados.json``: os achados dos dois tribunais, um por ministro (o de maior peso),
    ordenados por peso (desempate: tribunal, tipo, alvo); a manchete é o primeiro."""
    dia = hoje if isinstance(hoje, date) else date.fromisoformat(str(hoje)[:10])
    todos: list[dict[str, Any]] = []
    for trib in ("stf", "stj"):
        painel, s = paineis.get(trib), sinais.get(trib)
        if painel is None or s is None:
            continue
        todos += achados_heranca(regras, trib, painel, s, dia)
        todos += achados_mesa(regras, trib, painel, s)
        todos += achados_area(regras, trib, painel, s)
        todos += achados_vista(regras, trib, painel, s)
        todos += achados_entrou(regras, trib, painel, s, dia)
        todos += achados_temas(regras, trib, painel, s)
        todos += achados_quentes(regras, trib, painel, s)
    ordem = sorted(todos, key=lambda a: (-a["peso"], a["trib"], a["tipo"], a["alvo"]))
    vistos: set[str] = set()
    saida = []
    for a in ordem:
        if a["ministro"]:
            if a["ministro"] in vistos:
                continue
            vistos.add(a["ministro"])
        saida.append(a)
    return {"v": VERSAO, "hoje": dia.isoformat(), "achados": saida[:MAXIMO]}


def temas_recentes(temas: Sequence[Mapping[str, Any]] | None, hoje: date | str, dias: int = DIAS_DA_SEMANA) -> list[dict[str, Any]]:
    """Temas do STJ (``stj.montar_temas``, só repetitivo e IAC) afetados ou julgados nos últimos
    ``dias``: ``[{numero, tipo, evento, data, relator_id, titulo}]``."""
    dia = hoje if isinstance(hoje, date) else date.fromisoformat(str(hoje)[:10])
    inicio = (dia - timedelta(days=dias - 1)).isoformat()
    saida = []
    for t in temas or ():
        if t.get("tipo") not in ("repetitivo", "iac"):
            continue
        for evento, campo in (("julgado", "julgado_em"), ("afetado", "desde")):
            d = t.get(campo)
            if d and inicio <= str(d) <= dia.isoformat():
                saida.append({"numero": t.get("numero"), "tipo": t.get("tipo"), "evento": evento, "data": str(d),
                              "relator_id": t.get("relator_id"), "titulo": t.get("titulo")})
                break
    saida.sort(key=lambda x: (x["data"], x["tipo"], x["numero"] or 0))
    return saida


__all__ = ["data_br", "montar", "numero", "pct", "peso", "temas_recentes", "url_da_fonte", "vezes"]
