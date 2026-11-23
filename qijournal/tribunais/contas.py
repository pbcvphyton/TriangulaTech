"""As contas da aba Tribunais: dos processos (``stj.py``/``stf.py``) ao painel de cada tribunal
(``stj.json``/``stf.json``) e aos dossiês dos ministros (``ministros/<id>.json``), no formato do
contrato (§5 e §6).

Uma passada só pelo acervo (:class:`Contas`, ``somar`` processo a processo): o acervo do STJ tem
352 mil processos e, em lista, custa 1,25 GB. Cada processo entra uma vez em todas as contagens;
das listas (os mais antigos na mesa, a pauta, as vistas) ficam só os candidatos.

O que se mede (analista jurídico §1–§8):

- **o acervo de cada relator**, com os ministros da composição em linhas próprias e o resto
  (Presidência, Vice, "Não informado", resíduos de quem saiu) à parte, fora das médias;
- **na mesa** (o processo está no gabinete do relator) e **há quanto tempo**: no STJ, "sem decisão
  registrada há N dias" (da última distribuição ou decisão, a mais recente); no STF, "concluso ao
  relator há N dias"; sobrestado fica fora da conta do tempo;
- **a composição por área e subárea**, comparada com os pares (STJ: a Turma sem o ministro; STF:
  o tribunal sem ele e sem a Presidência) só com teste: binomial exata bicaudal, Holm a 99% sobre
  todas as comparações do tribunal, e piso de efeito (razão ≥ 1,25 ou ≤ 0,80, e 2 pontos ou
  mais). Quem herdou acervo, chegou à Turma há menos de 6 meses ou tem menos de 1.000 processos
  aparece sem marca, com o selo;
- **quem se repete**: os litigantes (PJ e ente, pelo grupo de ``partes.py``) em processos
  distintos, por polo (recorre, responde ou interessado), no tribunal e por ministro, sobre a base
  de ``cobertura.partes.base``: sem os processos em segredo (que contam no acervo, nas áreas e no
  dinheiro público, mas não entram nesta conta nem em lista nenhuma) e sem os precatórios (Prc,
  RPV), em que o requerente é o credor; a parte de cada um (``p``) é sobre essa base;
- **o que se mede no lugar do valor**: dinheiro público num polo (União, Fazenda Nacional, INSS,
  Estado ou DF, Município; o Ministério Público, a Defensoria e as autarquias não entram), a
  liminar (STJ: o processo chegou com pedido de liminar; STF: liminar ou tutela pendente),
  recortes econômicos;
- **o fluxo**: o que entrou (atas: sorteio × herança × registro), o que vai a julgamento (pautas),
  o que foi decidido (STJ: íntegras, com o atraso; STF: a pesquisa de jurisprudência) e os temas.

:func:`montar` devolve um :class:`Resultado` com o painel, os dossiês, a cobertura e os
**sinais** (as comparações com p-valor, os dias de herança, as vistas, as entradas recentes) que
``achados.py`` lê. Saída determinística: mesmas entradas, mesmos bytes.
"""

from __future__ import annotations

import heapq
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median, median_low
from typing import Any
from zoneinfo import ZoneInfo

from ..congresso.pais_estat import binomial_bicaudal, holm, percentil
from . import VERSAO
from .achados import temas_recentes
from .modelo import Composicao, Ministro, Regras, normalizar
from .partes import (
    Catalogo,
    CoberturaSTF,
    catalogo_padrao,
    dinheiro_publico,
    litigantes,
    nome_proprio,
    tem_pessoa_fisica,
)

NOMES = {"stj": ("Superior Tribunal de Justiça", "STJ"), "stf": ("Supremo Tribunal Federal", "STF")}
ONDES = ("gabinete", "outro_gabinete", "cartorio", "mp", "presidencia", "stf", "outro")
FAIXAS = ("ate_6m", "6_12m", "1_2a", "2_3a", "3_5a", "mais_5a")
TEXTO_VALOR = "Nenhuma fonte aberta do STF nem do STJ publica o valor da causa. A página não estima."
FONTES = {
    "stj": {"acervo": "stj_acervo", "atas": "stj_atas", "pautas": "stj_pautas", "decisoes": "stj_integras", "temas": "stj_precedentes"},
    "stf": {"acervo": "stf_acervo", "atas": "stf_atas", "pautas": "stf_pauta", "decisoes": "stf_jurisprudencia", "temas": "stf_rg"},
}
PARTES_FOLHA = 8  # partes numa mini-folha (só polo, tipo e grupo)


# ── números ──────────────────────────────────────────────────────────────────────────────────


def fracao(n: float, d: float) -> float | None:
    """Parte de um todo com 4 casas (contrato §0); ``None`` sem denominador."""
    return round(n / d, 4) if d else None


def razao(p: float | None, q: float | None) -> float | None:
    return round(p / q, 2) if p is not None and q else None


def idade(valores: Sequence[int], *, limites: Sequence[int] = (180, 365), completa: bool = True) -> dict[str, Any]:
    """``modelo.Idade`` de uma lista de dias: mediana, quartis, p90, máximo e quantos passam dos
    limites (``mesa_dias``). ``completa=False`` (STF, idade pelo ano de autuação): só mediana e
    quartis; o resto ``None``."""
    if not valores:
        return {"mediana": None, "p25": None, "p75": None, "p90": None, "max": None,
                "mais_180": 0 if completa else None, "mais_365": 0 if completa else None}
    ordenados = sorted(valores)
    saida = {"mediana": round(percentil(ordenados, 50)), "p25": round(percentil(ordenados, 25)),
             "p75": round(percentil(ordenados, 75))}
    if completa:
        saida.update({"p90": round(percentil(ordenados, 90)), "max": ordenados[-1],
                      "mais_180": sum(1 for v in ordenados if v > limites[0]),
                      "mais_365": sum(1 for v in ordenados if v > limites[1])})
    else:
        saida.update({"p90": None, "max": None, "mais_180": None, "mais_365": None})
    return saida


def faixas(valores: Iterable[int], meses: Sequence[int]) -> list[list[Any]]:
    """``[["ate_6m", n], ["6_12m", n], …]`` pelas fronteiras de ``idade_faixas_meses``."""
    limites = [m * 30.44 for m in meses]
    contagem = [0] * (len(limites) + 1)
    for v in valores:
        i = 0
        while i < len(limites) and v >= limites[i]:
            i += 1
        contagem[i] += 1
    rotulos = FAIXAS if len(contagem) == len(FAIXAS) else tuple(f"f{i}" for i in range(len(contagem)))
    return [[r, n] for r, n in zip(rotulos, contagem, strict=True)]


def _ordenado(contagem: Mapping[Any, int], limite: int | None = None) -> list[list[Any]]:
    itens = sorted(((k, v) for k, v in contagem.items() if v), key=lambda kv: (-kv[1], str(kv[0])))
    return [[k, v] for k, v in (itens[:limite] if limite is not None else itens)]


def _data(valor: date | str | None) -> date | None:
    if valor is None or isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


# ── a mini-folha ─────────────────────────────────────────────────────────────────────────────


def link_oficial(p: Mapping[str, Any], regras: Regras) -> str | None:
    """O endereço do processo no tribunal, para o leitor (nada aqui o coleta)."""
    modelo = ((regras.fontes.get("links_oficiais") or {}).get(p.get("trib") or "")) or None
    if not modelo:
        return None
    if p.get("trib") == "stj":
        return modelo.format(registro=p.get("registro") or "") if p.get("registro") else None
    return modelo.format(classe=p.get("classe") or "", numero=p.get("numero") or "")


def mini_folha(p: Mapping[str, Any], regras: Regras, publicavel: Any = None) -> dict[str, Any]:
    """Um processo numa lista (contrato §7): sem nome de parte (só polo, tipo e o grupo do
    litigante, este só quando o nome é publicável) e nunca de processo em segredo (quem chama
    filtra; aqui é conferido de novo)."""
    if p.get("segredo"):
        raise ValueError("processo em segredo de justiça não vira mini-folha")
    datas = p.get("datas") or {}
    flags = {k: v for k, v in (p.get("flags") or {}).items() if v not in (None, False, "")}
    partes: list[dict[str, Any]] = []
    vistos: set[tuple[Any, ...]] = set()
    for parte in p.get("partes") or ():
        polo, tipo = parte.get("polo"), parte.get("tipo")
        if polo is None or tipo in ("autoridade", "qualificador"):
            continue
        grupo = parte.get("grupo") if tipo in ("pj", "ente") else None
        if grupo is not None and publicavel is not None and not publicavel(grupo):
            grupo = None
        chave = (polo, tipo, grupo)
        if chave in vistos:
            continue
        vistos.add(chave)
        partes.append({"polo": polo, "tipo": tipo, "grupo": grupo})
        if len(partes) >= PARTES_FOLHA:
            break
    assunto = p.get("assunto") or {}
    return {
        "id": p.get("id"),
        "classe": p.get("classe"),
        "numero": p.get("numero"),
        "cnj": p.get("cnj"),
        "registro": p.get("registro"),
        "uf": (p.get("origem") or {}).get("uf"),
        "area": p.get("area"),
        "subarea": p.get("subarea"),
        "assunto": {"codigo": assunto.get("codigo"), "nome": assunto.get("nome"), "caminho": assunto.get("caminho")},
        "relator_id": p.get("relator_id"),
        "local": p.get("local"),
        "na_mesa": bool(p.get("na_mesa")),
        "datas": {k: datas.get(k) for k in ("autuacao", "ano_autuacao", "distribuicao", "ultima_decisao", "andamento")},
        "sem_decisao_dias": p.get("sem_decisao_dias"),
        "concluso_dias": p.get("concluso_dias"),
        "flags": flags,
        "partes": partes,
        "link": link_oficial(p, regras),
    }


class _Antigos:
    """Os N processos mais antigos (dias desc, id asc), guardando só os candidatos."""

    def __init__(self, n: int) -> None:
        self.n = n
        self._heap: list[tuple[int, tuple[int, ...], str, Any]] = []

    def guardar(self, dias: int, ident: str, p: Any) -> None:
        # no heap (mínimo na raiz) o "menor" é o que sai primeiro: menos dias e, no empate, o id maior
        chave = (dias, tuple(-ord(c) for c in ident) + (1,), ident, p)
        if len(self._heap) < self.n:
            heapq.heappush(self._heap, chave)
        elif chave[:2] > self._heap[0][:2]:
            heapq.heapreplace(self._heap, chave)

    def lista(self) -> list[Any]:
        return [x[3] for x in sorted(self._heap, key=lambda x: (-x[0], x[2]))]


# ── uma conta (um ministro ou uma linha fora da grade) ───────────────────────────────────────


@dataclass
class Conta:
    """O que se soma de um relator (ou de uma linha fora da grade)."""

    processos: int = 0
    na_mesa: int = 0
    segredo: int = 0
    sobrestados: int = 0
    liminar: int = 0
    ente_publico: int = 0
    pessoas_fisicas: int = 0
    base_partes: int = 0  # processos que entram em "quem se repete" (sem segredo, sem Prc/RPV)
    onde: Counter[str] = field(default_factory=Counter)
    areas: Counter[str] = field(default_factory=Counter)
    toca: Counter[str] = field(default_factory=Counter)  # processos da área que tocam outra área
    subareas: Counter[str] = field(default_factory=Counter)
    recortes: Counter[str] = field(default_factory=Counter)
    classes: Counter[str] = field(default_factory=Counter)
    relatores: Counter[str] = field(default_factory=Counter)  # o nome como veio (linhas fora da grade)
    litigantes: Counter[str] = field(default_factory=Counter)
    polos: Counter[tuple[str, str]] = field(default_factory=Counter)  # (grupo, polo) → processos
    sem_decisao: list[int] = field(default_factory=list)  # na mesa, não sobrestado (STJ: sem decisão; STF: concluso)
    no_tribunal: list[int] = field(default_factory=list)
    anos: Counter[int] = field(default_factory=Counter)  # STF: o ano de autuação (a planilha não dá o dia)
    recursos: int = 0
    antigos: _Antigos | None = None
    vistas: list[Any] = field(default_factory=list)


# ── o fluxo: entradas, pautas, decisões, temas ───────────────────────────────────────────────


@dataclass
class Fluxo:
    """As entradas das atas, já sem repetição: por dia e por destino (ministro ou linha fora da
    grade), em sorteio, herança e registro."""

    fonte: str
    por_dia: dict[str, dict[str, Counter[str]]] = field(default_factory=dict)  # data → destino → tipo → n
    por_dia_area: dict[str, Counter[str]] = field(default_factory=dict)  # data → subárea → n (STJ)
    por_dia_parte: dict[str, Counter[str]] = field(default_factory=dict)  # data → grupo → n (STJ)
    total_dia: dict[str, int] = field(default_factory=dict)  # data → processos distribuídos (sorteio + herança)

    @property
    def dias(self) -> list[str]:
        return sorted(self.por_dia)


def fluxo_stj(itens: Iterable[Mapping[str, Any]], *, janela_dias: int = 365) -> Fluxo:
    """As atas do STJ (``stj.ler_ata``, de um ou mais dias) → :class:`Fluxo`. Uma entrada é
    (destino, forma, registro) contada uma vez na janela, no primeiro dia em que aparece: a mesma
    redistribuição pode sair às 8h para a Presidência e às 11h para o ministro (são destinos
    diferentes e contam nos dois)."""
    itens = [i for i in itens if i.get("data")]
    fluxo = Fluxo("stj_atas")
    if not itens:
        return fluxo
    fim = max(str(i["data"]) for i in itens)
    ini = (date.fromisoformat(fim) - timedelta(days=janela_dias - 1)).isoformat()
    vistos: set[tuple[Any, ...]] = set()
    for i in sorted(itens, key=lambda x: (str(x["data"]), str(x.get("instante") or ""), str(x.get("id")))):
        data = str(i["data"])
        if data < ini:
            continue
        dia = fluxo.por_dia.setdefault(data, {})
        if i.get("tipo") not in ("sorteio", "heranca", "registro"):
            continue
        destino = i.get("relator_id") or i.get("fora") or "residuos"
        chave = (destino, i["tipo"], i.get("registro") or i.get("id"))
        if chave in vistos:
            continue
        vistos.add(chave)
        dia.setdefault(destino, Counter())[i["tipo"]] += 1
        if i["tipo"] in ("sorteio", "heranca"):
            fluxo.total_dia[data] = fluxo.total_dia.get(data, 0) + 1
            if i.get("subarea"):
                fluxo.por_dia_area.setdefault(data, Counter())[i["subarea"]] += 1
            if not i.get("segredo"):
                for gid in litigantes(i):
                    fluxo.por_dia_parte.setdefault(data, Counter())[gid] += 1
    return fluxo


def fluxo_stf(atas: Mapping[str, Any] | None) -> Fluxo:
    """As atas do STF (``stf.coletar_atas``) → :class:`Fluxo`, pelo totalizador oficial de cada
    ata (``sorteio`` = distribuído, ``heranca`` = redistribuído) e o registro à Presidência do dia."""
    fluxo = Fluxo("stf_atas")
    for dia in (atas or {}).get("dias") or ():
        data = str(dia.get("data"))
        registro = fluxo.por_dia.setdefault(data, {})
        for ata in dia.get("atas") or ():
            for t in ata.get("totais") or ():
                destino = t.get("ministro_id") or "residuos"
                c = registro.setdefault(destino, Counter())
                c["sorteio"] += int(t.get("sorteio") or 0)
                c["heranca"] += int(t.get("heranca") or 0)
                fluxo.total_dia[data] = fluxo.total_dia.get(data, 0) + int(t.get("sorteio") or 0) + int(t.get("heranca") or 0)
        if dia.get("presidencia"):
            registro.setdefault("presidencia", Counter())["registro"] += int(dia["presidencia"])
    return fluxo


def _entradas(fluxo: Fluxo | None, regras: Regras) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    """O bloco ``entradas`` do painel e, por destino, o do ministro (resumo e dossiê)."""
    if fluxo is None or not fluxo.por_dia:
        return None, {}
    janelas = regras.limiares.get("janelas") or {}
    recentes = int(janelas.get("entradas_recentes_dias", 30))
    longa = int(janelas.get("entradas_dias", 365))
    dias = fluxo.dias
    fim = dias[-1]
    ini = (date.fromisoformat(fim) - timedelta(days=longa - 1)).isoformat()
    ini30 = (date.fromisoformat(fim) - timedelta(days=recentes - 1)).isoformat()
    total: Counter[str] = Counter()
    ult30: Counter[str] = Counter()
    por_mes: dict[str, Counter[str]] = defaultdict(Counter)
    destino: dict[str, dict[str, Any]] = {}
    for data in dias:
        if data < ini:
            continue
        for dest, c in fluxo.por_dia[data].items():
            total.update(c)
            if data >= ini30:
                ult30.update(c)
            por_mes[data[:7]]["sorteio"] += c.get("sorteio", 0)
            por_mes[data[:7]]["heranca"] += c.get("heranca", 0)
            d = destino.setdefault(dest, {"sorteio": 0, "heranca": 0, "registro": 0, "s30": 0, "h30": 0,
                                          "por_mes": defaultdict(Counter), "heranca_dias": []})
            for t in ("sorteio", "heranca", "registro"):
                d[t] += c.get(t, 0)
            if data >= ini30:
                d["s30"] += c.get("sorteio", 0)
                d["h30"] += c.get("heranca", 0)
            d["por_mes"][data[:7]]["sorteio"] += c.get("sorteio", 0)
            d["por_mes"][data[:7]]["heranca"] += c.get("heranca", 0)
            if c.get("heranca"):
                d["heranca_dias"].append([data, c["heranca"]])
    painel = {
        "fonte": fluxo.fonte,
        "janela": [max(ini, dias[0]), fim],
        "dias_com_ata": sum(1 for d in dias if d >= ini and fluxo.por_dia[d]),
        "total": {"sorteio": total["sorteio"], "heranca": total["heranca"], "registro": total["registro"]},
        "ultimos_30d": {"sorteio": ult30["sorteio"], "heranca": ult30["heranca"]},
        "por_mes": [[m, c["sorteio"], c["heranca"]] for m, c in sorted(por_mes.items())],
    }
    por_destino = {}
    for dest, d in destino.items():
        por_destino[dest] = {
            "resumo": {"sorteio_12m": d["sorteio"], "heranca_12m": d["heranca"], "sorteio_30d": d["s30"], "heranca_30d": d["h30"]},
            "dossie": {"janela": painel["janela"], "sorteio": d["sorteio"], "heranca": d["heranca"],
                       "ultimos_30d": {"sorteio": d["s30"], "heranca": d["h30"]},
                       "por_mes": [[m, c["sorteio"], c["heranca"]] for m, c in sorted(d["por_mes"].items())],
                       "heranca_dias": sorted(d["heranca_dias"])},
        }
    return painel, por_destino


def _pautas_stj(dados: Any) -> tuple[str | None, list[Mapping[str, Any]]]:
    if dados is None:
        return None, []
    if isinstance(dados, Mapping):
        return dados.get("data_dados"), list(dados.get("itens") or ())
    itens = list(dados)
    return max((str(i.get("publicada")) for i in itens if i.get("publicada")), default=None), itens


def _pautas(trib: str, dados: Any) -> tuple[dict[str, Any] | None, dict[str, Counter[str]], list[Mapping[str, Any]]]:
    """O bloco ``pautas`` do painel, as contagens por ministro e os itens (para o dossiê)."""
    if dados is None:
        return None, {}, []
    if trib == "stj":
        data_dados, itens = _pautas_stj(dados)
        fonte = "stj_pautas"
    else:
        itens = list((dados or {}).get("itens") or ())
        data_dados = ((dados or {}).get("janela") or [None])[0]
        fonte = "stf_pauta"
    sessoes: Counter[tuple[str, str]] = Counter()
    por_tipo: Counter[str] = Counter()
    por_ministro: dict[str, Counter[str]] = defaultdict(Counter)
    vistas = destaques = 0
    for i in itens:
        data = str(i.get("sessao") if trib == "stj" else i.get("data") or "")
        sessoes[(data, str(i.get("orgao") or ""))] += 1
        if trib == "stj":
            tipo = str(i.get("tipo") or "principal")
            interno = bool(i.get("interno"))
        else:
            tipo = "principal" if "-" not in str(i.get("classe") or "") else str(i["classe"]).split("-", 1)[1]
            interno = tipo != "principal"
            vistas += 1 if i.get("vista") else 0
            destaques += 1 if i.get("destaque") else 0
        if tipo != "principal":
            por_tipo[tipo] += 1
        rid = i.get("relator_id")
        if rid:
            c = por_ministro[rid]
            c["itens"] += 1
            c["interno" if interno else "principal"] += 1
    total = len(itens)
    internos = sum(por_tipo.values()) if trib == "stj" else sum(1 for i in itens if "-" in str(i.get("classe") or ""))
    painel = {
        "fonte": fonte,
        "data_dados": data_dados,
        "sessoes": [[d, o, n] for (d, o), n in sorted(sessoes.items())],
        "itens": total,
        "principal": total - internos,
        "interno": internos,
        "por_tipo": dict(_ordenado(por_tipo)),
        "vistas": vistas if trib == "stf" else None,
        "destaques": destaques if trib == "stf" else None,
    }
    return painel, por_ministro, itens


def _decisoes(trib: str, dados: Any, hoje: date, regras: Regras) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    """O bloco ``decisoes`` do painel e o de cada ministro."""
    if dados is None:
        return None, {}
    if trib == "stf":
        janela = list(dados.get("janela") or [None, None])
        painel = {"fonte": "stf_jurisprudencia", "janela": janela, "ultimo_arquivo": None, "atraso_dias": None,
                  "processos": dados.get("processos"), "documentos": dados.get("documentos"),
                  "monocraticas": dados.get("monocraticas"), "acordaos": dados.get("acordaos"),
                  "acordaos_recurso_interno": dados.get("acordaos_recurso_interno"), "presidencia": dados.get("presidencia")}
        por = {mid: {"janela": janela, "ultimo_arquivo": None, "processos": v.get("processos"), "documentos": v.get("documentos"),
                     "monocraticas": v.get("monocraticas"), "acordaos": v.get("acordaos"), "acordaos_recurso_interno": None}
               for mid, v in (dados.get("ministros") or {}).items()}
        return painel, por
    itens = list(dados.get("itens") if isinstance(dados, Mapping) else dados)
    if not itens:
        return None, {}
    from .stj import (
        data_do_nome,  # o nome do arquivo diz o dia (metadadosAAAAMMDD.json)
    )

    ultimo = (dados.get("ultimo_arquivo") if isinstance(dados, Mapping) else None) or max(
        (data_do_nome(str(i.get("arquivo") or "")) or str(i.get("data") or "") for i in itens), default=None)
    dias = int((regras.limiares.get("janelas") or {}).get("decisoes_dias", 365))
    ini = (date.fromisoformat(ultimo) - timedelta(days=dias - 1)).isoformat() if ultimo else None
    # A janela é a que os dados cobrem: a série das íntegras cresce no cache arquivo a arquivo e,
    # enquanto não tem os 12 meses, a página diz de quando a quando ela vai (não "em 12 meses").
    primeiro = min((data_do_nome(str(i.get("arquivo") or "")) or str(i.get("data") or "") for i in itens), default=None)
    if ini and primeiro and primeiro > ini:
        ini = primeiro
    conta: dict[str, dict[str, Any]] = defaultdict(lambda: {"processos": set(), "documentos": 0, "monocraticas": 0, "acordaos": 0,
                                                            "acordaos_recurso_interno": 0})
    total = conta["__total__"]
    presidencia = 0
    composicao = regras.composicao["stj"]
    for i in itens:
        dia = data_do_nome(str(i.get("arquivo") or "")) or str(i.get("data") or "")
        if ini and dia and dia < ini:
            continue
        chave = i.get("registro") or i.get("id")
        # O que quem presidia o tribunal assinou no período é da Presidência, não do cartão dele
        # (as íntegras dão o nome, não o cargo: a mesma regra do "PRESIDENTE" no STF).
        como_presidente = bool(i.get("relator_id")) and composicao.presidente_em(i.get("data") or dia) == i.get("relator_id")
        if i.get("fora") == "presidencia" or como_presidente:
            presidencia += 1
        for alvo in (total, conta[i["relator_id"]] if i.get("relator_id") and not como_presidente else None):
            if alvo is None:
                continue
            alvo["processos"].add(chave)
            alvo["documentos"] += 1
            if i.get("documento") == "acordao":
                alvo["acordaos"] += 1
                alvo["acordaos_recurso_interno"] += 1 if i.get("interno") else 0
            else:
                alvo["monocraticas"] += 1
    janela = [ini, ultimo]
    painel = {"fonte": "stj_integras", "janela": janela, "ultimo_arquivo": ultimo,
              "atraso_dias": (hoje - date.fromisoformat(ultimo)).days if ultimo else None,
              "processos": len(total["processos"]), "documentos": total["documentos"], "monocraticas": total["monocraticas"],
              "acordaos": total["acordaos"], "acordaos_recurso_interno": total["acordaos_recurso_interno"], "presidencia": presidencia}
    por = {mid: {"janela": janela, "ultimo_arquivo": ultimo, "processos": len(v["processos"]), "documentos": v["documentos"],
                 "monocraticas": v["monocraticas"], "acordaos": v["acordaos"], "acordaos_recurso_interno": v["acordaos_recurso_interno"]}
           for mid, v in conta.items() if mid != "__total__"}
    return painel, por


TIPOS_TEMA_PENDENTE = ("repetitivo", "iac")  # o que o analista conta como "tema sob relatoria" no STJ


def _nome_relator(texto: str) -> str:
    """O nome de um relator fora da composição como a página mostra: ``"Fulano de Tal
    (DESEMBARGADOR Convocado do Tjdft)"`` → ``"Fulano de Tal (desembargador convocado do TJDFT)"``."""
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", texto)
    nome, extra = (m.group(1), m.group(2)) if m else (texto, "")
    nome = nome_proprio(nome) or nome
    if not extra:
        return nome
    extra = " ".join(extra.split()).lower()
    extra = re.sub(r"\bconvocad([oa]) (tj|trf|trt)", r"convocad\1 do \2", extra, flags=re.IGNORECASE)
    extra = re.sub(r"\b(tj[a-z]{1,4}|trf|trt)\b", lambda x: x.group(1).upper(), extra)
    return f"{nome} ({extra})"


def _temas(trib: str, dados: Any, regras: Regras) -> tuple[dict[str, Any] | None, dict[str, list[dict[str, Any]]]]:
    """O bloco ``temas`` do painel e a lista de temas pendentes de cada ministro."""
    if dados is None:
        return None, {}
    por: dict[str, list[dict[str, Any]]] = defaultdict(list)
    saiu: Counter[str] = Counter()
    motivos: dict[str, str] = {}
    sem_relator = 0
    por_tipo: Counter[str] = Counter()
    composicao = regras.composicao[trib]

    def quem_saiu(bruto: Any) -> None:
        """O relator que não está mais na composição, pelo nome canônico de ``saiu`` (com o motivo);
        fora da lista, o nome como veio, com o parêntese do convocado arrumado."""
        nonlocal sem_relator
        texto = re.sub(r"^MIN\.?\s*", "", str(bruto or "").strip())
        if not texto or normalizar(texto) in ("NAO INFORMADO", "RELATOR NAO INFORMADO", "-"):
            sem_relator += 1
            return
        achado = composicao.saida(texto)
        if achado is not None:
            nome, motivo = achado
            motivos[nome] = motivo
        else:
            nome = _nome_relator(texto)
        saiu[nome] += 1

    if trib == "stj":
        temas = list(dados)
        for t in temas:
            if not t.get("pendente"):
                continue
            por_tipo[t.get("tipo") or "outro"] += 1
            if t.get("tipo") not in TIPOS_TEMA_PENDENTE:
                continue
            if t.get("relator_id"):
                por[t["relator_id"]].append({"numero": t.get("numero"), "tipo": t.get("tipo"), "titulo": t.get("titulo"),
                                             "situacao": t.get("situacao"), "desde": t.get("desde"), "link": t.get("link")})
            elif (t.get("fora") or "residuos") == "residuos":
                quem_saiu(t.get("relator"))
        pendentes = sum(por_tipo[t] for t in TIPOS_TEMA_PENDENTE)
        painel = {"fonte": "stj_precedentes", "pendentes": pendentes,
                  "por_tipo": {"repetitivo": por_tipo["repetitivo"], "iac": por_tipo["iac"], "rg": 0,
                               **{k: v for k, v in sorted(por_tipo.items()) if k not in ("repetitivo", "iac")}},
                  "relator_saiu": [[n, k, motivos.get(n, "")] for n, k in _ordenado(saiu)], "sem_relator": sem_relator}
    else:
        temas = list((dados or {}).get("temas") or ())
        for t in temas:
            if not t.get("pendente"):
                continue
            por_tipo["rg"] += 1
            if t.get("relator_id"):
                por[t["relator_id"]].append({"numero": t.get("numero"), "tipo": "rg", "titulo": t.get("titulo"),
                                             "situacao": t.get("situacao"), "desde": t.get("desde"), "link": t.get("link")})
            elif (t.get("fora") or "residuos") == "residuos":
                quem_saiu(t.get("relator"))
        painel = {"fonte": "stf_rg", "pendentes": por_tipo["rg"], "por_tipo": {"repetitivo": 0, "iac": 0, "rg": por_tipo["rg"]},
                  "relator_saiu": [[n, k, motivos.get(n, "")] for n, k in _ordenado(saiu)], "sem_relator": sem_relator}
    for lista in por.values():
        lista.sort(key=lambda x: (x["tipo"] or "", x["numero"] if x["numero"] is not None else 10**9))
    return painel, por


# ── o resultado ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Resultado:
    painel: dict[str, Any]
    dossies: dict[str, dict[str, Any]]
    cobertura: dict[str, Any]
    sinais: dict[str, Any]


# ── a passada pelo acervo ────────────────────────────────────────────────────────────────────


class Contas:
    """Soma os processos de um tribunal, um por vez (:meth:`somar`), e monta o painel e os
    dossiês (:meth:`resultado`)."""

    def __init__(self, trib: str, regras: Regras, *, catalogo: Catalogo | None = None, data_dados: str | None = None,
                 hoje: date | str | None = None, pauta_ids: Iterable[str] = ()) -> None:
        self.trib = trib
        self.regras = regras
        self.catalogo = catalogo or catalogo_padrao()
        self.composicao: Composicao = regras.composicao[trib]
        self.data_dados = data_dados
        self.hoje = _data(hoje) or _data(data_dados) or datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        lim = regras.limiares
        self.mesa_dias = tuple(lim.get("mesa_dias") or (180, 365))
        self.listas = lim.get("listas") or {}
        self.ministros: dict[str, Ministro] = {m.id: m for m in self.composicao.ministros}
        self.grade = {m.id for m in self.composicao.da_grade()}
        self.grupo_de = {m.id: self.composicao.grupo_de(m) for m in self.composicao.ministros}
        self.contas: dict[str, Conta] = {}
        self.fora: dict[str, Conta] = {}
        n_dossie = int(self.listas.get("dossie_mais_antigos", 15))
        for m in self.composicao.ministros:
            self.contas[m.id] = Conta(antigos=_Antigos(n_dossie))
        self.fila = _Antigos(int(self.listas.get("fila_mais_antigos", 30)))
        # o tribunal inteiro
        self.processos = 0
        self.segredo = 0
        self.sobrestados = 0
        self.motivos: Counter[str] = Counter()
        self.liminar = 0
        self.ente_publico = 0
        self.ente_por_grupo: Counter[str] = Counter()
        self.pessoas_fisicas = 0
        self.areas: Counter[str] = Counter()
        self.segredo_area: Counter[str] = Counter()
        self.areas_grade: Counter[str] = Counter()
        self.areas_grupo: Counter[tuple[str, str]] = Counter()
        self.subareas: dict[str, Counter[str]] = defaultdict(Counter)
        self.assuntos: dict[str, Counter[int]] = defaultdict(Counter)
        self.toca: dict[str, Counter[str]] = defaultdict(Counter)
        self.recortes: Counter[str] = Counter()
        self.recortes_grade: Counter[str] = Counter()
        self.lit_n: Counter[str] = Counter()
        self.lit_polo: Counter[tuple[str, str]] = Counter()
        self.lit_mesas: dict[str, set[str]] = defaultdict(set)
        self.lit_area: dict[str, Counter[str]] = defaultdict(Counter)
        self.lit_area_top: dict[str, Counter[str]] = defaultdict(Counter)  # área → grupo → processos
        self.lit_por_processo: list[tuple[str, ...]] = []
        self.base_partes = 0
        self.vistas: list[Any] = []
        self.pauta_ids = set(pauta_ids)
        self.na_pauta: dict[str, Any] = {}
        self.pauta_segredo: set[str] = set()  # na pauta, mas em segredo no acervo: nunca vai a lista
        self.cobertura_stf = CoberturaSTF() if trib == "stf" else None
        self.area_por: Counter[str] = Counter()
        self.recursos = 0  # processos de classe recursal (limiares.classes_recurso): "recorre" e "responde"
        self.classes_recurso = frozenset(str(c) for c in ((lim.get("classes_recurso") or {}).get(trib) or ()))

    # ── somar ────────────────────────────────────────────────────────────────────────────────

    def _conta(self, p: Mapping[str, Any]) -> tuple[Conta, bool]:
        rid = p.get("relator_id")
        if rid and rid in self.contas:
            return self.contas[rid], rid in self.grade
        chave = p.get("fora") or "residuos"
        conta = self.fora.get(chave)
        if conta is None:
            conta = self.fora[chave] = Conta()
        return conta, False

    def _dias_no_tribunal(self, p: Mapping[str, Any]) -> int | None:
        datas = p.get("datas") or {}
        ref = _data(self.data_dados)
        if ref is None:
            return None
        if datas.get("autuacao"):
            d = _data(datas["autuacao"])
            return max(0, (ref - d).days) if d else None
        return None  # só o ano (STF): a idade em dias seria inventada; vale o ano (autuacao)

    def _dias_mesa(self, p: Mapping[str, Any]) -> int | None:
        return p.get("sem_decisao_dias") if self.trib == "stj" else p.get("concluso_dias")

    def somar(self, p: Mapping[str, Any]) -> None:
        """Um processo (já com ``partes.completar``) em todas as contas."""
        conta, na_grade = self._conta(p)
        rid = p.get("relator_id")
        grupo = self.grupo_de.get(rid or "") if na_grade else None
        flags = p.get("flags") or {}
        segredo = bool(p.get("segredo"))
        sobrestado = bool(flags.get("sobrestado"))
        liminar = bool(flags.get("liminar") or flags.get("tutela"))
        area = str(p.get("area") or "sem_assunto")
        self.processos += 1
        self.area_por[str(p.get("area_por") or "")] += 1
        conta.processos += 1
        if p.get("relator") and not rid:
            conta.relatores[str(p.get("relator"))] += 1
        elif not rid:
            conta.relatores[""] += 1
        conta.onde[str(p.get("onde") or "outro")] += 1
        conta.areas[area] += 1
        self.areas[area] += 1
        if na_grade:
            self.areas_grade[area] += 1
            self.areas_grupo[(grupo or "", area)] += 1
        if p.get("subarea"):
            conta.subareas[p["subarea"]] += 1
            self.subareas[area][p["subarea"]] += 1
        codigo = (p.get("assunto") or {}).get("codigo")
        if codigo is not None:
            self.assuntos[area][codigo] += 1
        toca = p.get("tambem_toca") or ()
        if toca:
            conta.toca[area] += 1
            for outra in toca:
                self.toca[area][outra] += 1
        for r in p.get("recortes") or ():
            conta.recortes[r] += 1
            self.recortes[r] += 1
            if na_grade:
                self.recortes_grade[r] += 1
        conta.classes[str(p.get("classe") or "")] += 1
        if segredo:
            conta.segredo += 1
            self.segredo += 1
            self.segredo_area[area] += 1
        if sobrestado:
            conta.sobrestados += 1
            self.sobrestados += 1
            self.motivos[str(flags.get("motivo_sobrestamento") or "nao_informado")] += 1
        if liminar:
            conta.liminar += 1
            self.liminar += 1
        # partes
        if dinheiro_publico(p, self.catalogo):
            conta.ente_publico += 1
            self.ente_publico += 1
            if na_grade:
                self.ente_por_grupo[grupo or ""] += 1
        if tem_pessoa_fisica(p):
            conta.pessoas_fisicas += 1
            self.pessoas_fisicas += 1
        if self.cobertura_stf is not None:
            self.cobertura_stf.somar(p, self.catalogo)
        if not segredo and str(p.get("classe") or "") not in self.catalogo.classes_fora:
            conta.base_partes += 1
            self.base_partes += 1
            lits = litigantes(p)
            if lits:
                self.lit_por_processo.append(tuple(sorted(lits)))
            for gid, polos in lits.items():
                conta.litigantes[gid] += 1
                self.lit_n[gid] += 1
                self.lit_area[gid][area] += 1
                self.lit_area_top[area][gid] += 1
                if na_grade and rid:
                    self.lit_mesas[gid].add(rid)
                for polo in polos:
                    conta.polos[(gid, polo)] += 1
                    self.lit_polo[(gid, polo)] += 1
        # idade
        dias_trib = self._dias_no_tribunal(p)
        if dias_trib is not None:
            conta.no_tribunal.append(dias_trib)
        else:
            ano = (p.get("datas") or {}).get("ano_autuacao")
            if ano:
                conta.anos[int(ano)] += 1
        if str(p.get("classe") or "") in self.classes_recurso:
            conta.recursos += 1
            self.recursos += 1
        if p.get("na_mesa"):
            conta.na_mesa += 1
            dias = self._dias_mesa(p)
            if dias is not None and not sobrestado:
                conta.sem_decisao.append(int(dias))
                if not segredo:
                    if conta.antigos is not None:
                        conta.antigos.guardar(int(dias), str(p.get("id")), p)
                    if na_grade:
                        self.fila.guardar(int(dias), str(p.get("id")), p)
        # listas do STF e da pauta
        if not segredo:
            if flags.get("vista"):
                conta.vistas.append(p)
                self.vistas.append(p)
            chave = p.get("registro") if self.trib == "stj" else p.get("id")
            if chave and chave in self.pauta_ids:
                self.na_pauta[chave] = p
        elif self.pauta_ids:
            chave = p.get("registro") if self.trib == "stj" else p.get("id")
            if chave and chave in self.pauta_ids:
                self.pauta_segredo.add(chave)

    # ── montar ───────────────────────────────────────────────────────────────────────────────

    def publicavel_tribunal(self, gid: str) -> bool:
        return self.lit_n.get(gid, 0) >= int(self.regras.limiares.get("nome_parte_tribunal", 20))

    def _publicavel_folha(self, gid: str) -> bool:
        return gid in self.catalogo.grupos or self.publicavel_tribunal(gid)

    def _pares(self, mid: str) -> list[str]:
        """Os pares da comparação: STJ, a Turma sem ele; STF, o tribunal (a grade) sem ele."""
        m = self.ministros[mid]
        if mid not in self.grade:
            return []
        modo = ((self.regras.limiares.get("comparacao") or {}).get("pares") or {}).get(self.trib, "turma")
        if modo == "turma":
            return [o for o in self.grade if o != mid and self.ministros[o].turma == m.turma]
        return [o for o in self.grade if o != mid]

    def _soma(self, ids: Iterable[str], campo: str) -> Counter[Any]:
        total: Counter[Any] = Counter()
        for i in ids:
            total.update(getattr(self.contas[i], campo))
        return total

    def resultado(self, *, fluxo: Fluxo | None = None, pautas: Any = None, decisoes: Any = None, temas: Any = None) -> Resultado:
        regras, lim, trib = self.regras, self.regras.limiares, self.trib
        ids_areas = regras.ids_areas
        comp = lim.get("comparacao") or {}
        alfa = 1.0 - float(comp.get("confianca", 0.99))
        razao_min, razao_max = (float(x) for x in comp.get("razao", (0.80, 1.25)))
        pontos = float(comp.get("pontos", 2)) / 100.0
        minimo_area = int(comp.get("minimo_area", 30))
        minimo_acervo = int(comp.get("minimo_acervo", 1000))
        meses_nc = int(lim.get("nao_comparavel_meses", 6))
        nome_ministro = int(lim.get("nome_parte_ministro", 5))

        entradas_painel, entradas_por = _entradas(fluxo, regras)
        pautas_painel, pautas_por, pautas_itens = _pautas(trib, pautas)
        decisoes_painel, decisoes_por = _decisoes(trib, decisoes, self.hoje, regras)
        temas_painel, temas_por = _temas(trib, temas, regras)

        # selos e comparabilidade
        selos: dict[str, list[str]] = {}
        comparavel: dict[str, bool] = {}
        relogio: dict[str, str] = {}  # quem teve o relógio da mesa reiniciado há menos de um ano (chegada à Turma, herança)
        ref = _data(self.data_dados) or self.hoje
        reduzida = self._distribuicao_reduzida(entradas_painel, entradas_por)
        for mid, m in self.ministros.items():
            c = self.contas[mid]
            s = []
            if m.convocado:
                s.append("convocado")
            herdado = 0
            if mid in entradas_por:
                corte = (ref - timedelta(days=round(meses_nc * 30.44))).isoformat()
                herdado = sum(n for d, n in entradas_por[mid]["dossie"]["heranca_dias"] if d >= corte)
            if c.processos and herdado >= float(lim.get("heranca_parte", 0.30)) * c.processos:
                s.append("heranca")
            if m.recem_chegado(ref, meses_nc):
                s.append("recem_chegado")
            if (mid in self.grade and entradas_painel is not None and entradas_painel["dias_com_ata"] >= 20
                    and not self._chegou_na_janela(m, ref)
                    and not (entradas_por.get(mid) or {}).get("resumo", {}).get("sorteio_30d")):
                s.append("sem_distribuicao")
            elif mid in reduzida:
                s.append("distribuicao_reduzida")
            if mid in self.grade and c.processos < minimo_acervo:
                s.append("poucos_processos")
            selos[mid] = s
            comparavel[mid] = mid in self.grade and not ({"heranca", "recem_chegado", "poucos_processos"} & set(s))
            # O relógio do "sem decisão" recomeça na redistribuição e na atribuição: quem chegou à Turma
            # (ou herdou acervo) há menos de mesa_dias[1] dias não pode ter nada acima disso por construção.
            marcos = []
            if mid in self.grade and m.turma_desde and (ref - date.fromisoformat(m.turma_desde)).days < self.mesa_dias[1]:
                marcos.append(m.turma_desde)
            if "heranca" in s and mid in entradas_por:
                dias_h = [d for d, n in entradas_por[mid]["dossie"]["heranca_dias"] if (ref - date.fromisoformat(d)).days < self.mesa_dias[1]]
                grandes = [d for d, n in entradas_por[mid]["dossie"]["heranca_dias"] if d in dias_h and n >= 100]
                if grandes or dias_h:
                    marcos.append(min(grandes or dias_h))
            if marcos:
                relogio[mid] = max(marcos)

        # a matriz e o teste
        modo_pares = ((comp.get("pares") or {}).get(trib) or "turma")
        sem_destoante = trib in (comp.get("sem_destoante") or ())

        def testar(destoantes: Mapping[str, set[str]]) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[tuple[str, str], float],
                                                                 dict[tuple[str, str], dict[str, Any]]]:
            """As células da matriz e os testes; ``destoantes``: por área, quem sai dos pares dos outros."""
            celulas: dict[str, dict[str, dict[str, Any]]] = {}
            testes: dict[tuple[str, str], float] = {}
            detalhes: dict[tuple[str, str], dict[str, Any]] = {}
            for mid in self.ministros:
                c = self.contas[mid]
                pares = self._pares(mid)
                pares_areas = self._soma(pares, "areas")
                pares_n = sum(self.contas[o].processos for o in pares)
                linha = {}
                for a in ids_areas:
                    fora = sorted(o for o in destoantes.get(a, ()) if o in pares)
                    pa_k, pa_n = pares_areas.get(a, 0), pares_n
                    if fora:
                        pa_k -= sum(self.contas[o].areas.get(a, 0) for o in fora)
                        pa_n -= sum(self.contas[o].processos for o in fora)
                    k = c.areas.get(a, 0)
                    p = fracao(k, c.processos)
                    q = fracao(pa_k, pa_n) if pa_n else None
                    linha[a] = {"n": k, "p": p if p is not None else 0.0, "pares": q, "razao": razao(p, q), "marca": 0}
                    if comparavel[mid] and pa_n and (k >= minimo_area or pa_k >= minimo_area):
                        p0 = pa_k / pa_n
                        if 0.0 < p0 < 1.0:
                            testes[(mid, a)] = binomial_bicaudal(k, c.processos, p0)
                            detalhes[(mid, a)] = {"k": k, "n": c.processos, "p": k / c.processos, "pares": p0,
                                                  "pares_n": pa_n, "pares_k": pa_k, "fora": fora}
                celulas[mid] = linha
            return celulas, testes, detalhes

        def marcar(celulas: dict[str, dict[str, dict[str, Any]]], testes: Mapping[tuple[str, str], float],
                   detalhes: Mapping[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
            passam = holm(dict(testes), alfa) if testes else set()
            saida = []
            for chave, pv in sorted(testes.items(), key=lambda kv: (kv[1], kv[0])):
                d = detalhes[chave]
                r = d["p"] / d["pares"]
                efeito = (r >= razao_max or r <= razao_min) and abs(d["p"] - d["pares"]) >= pontos
                marca = (1 if r > 1 else -1) if (chave in passam and efeito) else 0
                celulas[chave[0]][chave[1]]["marca"] = marca
                saida.append({"ministro": chave[0], "area": chave[1], "k": d["k"], "n": d["n"], "p": round(d["p"], 4),
                              "pares": round(d["pares"], 4), "pares_k": d["pares_k"], "pares_n": d["pares_n"],
                              "razao": round(r, 2), "p_valor": pv, "acaso": chave in passam, "efeito": efeito, "marca": marca,
                              **({"fora": d["fora"]} if d.get("fora") else {})})
            return saida

        celulas, testes, detalhes = testar({})
        marcas = marcar(celulas, testes, detalhes)
        if sem_destoante and modo_pares != "turma":
            # Pares = o tribunal inteiro: o gabinete bem acima numa área (▲) sai da conta dos outros
            # naquela área (um acervo concentrado por prevenção não marca os demais "abaixo").
            destoantes: dict[str, set[str]] = defaultdict(set)
            for x in marcas:
                if x["marca"] > 0:
                    destoantes[x["area"]].add(x["ministro"])
            if destoantes:
                celulas, testes, detalhes = testar(destoantes)
                marcas = marcar(celulas, testes, detalhes)

        # os nomes publicáveis
        def nome_de(gid: str) -> dict[str, Any]:
            lit = self.catalogo.litigante(gid)
            return {"nome": lit.nome if lit else gid, "tipo": lit.tipo if lit else "pj"}

        nomes_usados: set[str] = set()

        def folha(p: Any) -> dict[str, Any]:
            f = mini_folha(p, regras, self._publicavel_folha)
            nomes_usados.update(x["grupo"] for x in f["partes"] if x["grupo"])
            return f

        # pauta: itens por ministro (para o dossiê)
        em_pauta: dict[str, list[dict[str, Any]]] = defaultdict(list)
        n_pauta = int(self.listas.get("dossie_em_pauta", 20))
        for i in sorted(pautas_itens, key=lambda x: (str(x.get("sessao") or x.get("data") or ""), str(x.get("orgao") or ""), str(x.get("id")))):
            rid = i.get("relator_id")
            if not rid or len(em_pauta[rid]) >= n_pauta or i.get("segredo"):
                continue
            chave = i.get("registro") if trib == "stj" else i.get("id")
            p = self.na_pauta.get(chave)
            if p is None:
                continue
            tipo = (str(i.get("tipo") or "principal") if trib == "stj"
                    else ("principal" if "-" not in str(i.get("classe") or "") else str(i["classe"]).split("-", 1)[1]))
            em_pauta[rid].append({"processo": folha(p), "sessao": i.get("sessao") if trib == "stj" else i.get("data"),
                                  "orgao": i.get("orgao"), "tipo": tipo})

        # ministros: resumo e dossiê
        resumos: list[dict[str, Any]] = []
        dossies: dict[str, dict[str, Any]] = {}
        n_painel = int(self.listas.get("painel_mais_antigos", 3))
        meses = list(lim.get("idade_faixas_meses") or (6, 12, 24, 36, 60))
        for mid, m in self.ministros.items():
            c = self.contas[mid]
            pares = self._pares(mid)
            antigos = c.antigos.lista() if c.antigos else []
            folhas = [folha(p) for p in antigos]
            partes_min = [[g, n] for g, n in _ordenado(c.litigantes) if n >= nome_ministro][: int(self.listas.get("partes_painel_ministro", 3))]
            nomes_usados.update(g for g, _ in partes_min)
            convocado = dict(m.convocado) if m.convocado else None
            if convocado is not None:
                convocado = {"tribunal": convocado.get("tribunal"), "vaga_de": convocado.get("vaga_de"), "desde": m.turma_desde}
            sem_decisao = idade(c.sem_decisao, limites=self.mesa_dias)
            no_tribunal = idade(c.no_tribunal, limites=self.mesa_dias, completa=trib == "stj")
            autuacao = _autuacao(c.anos, ref)
            resumo = {
                "id": mid, "nome": m.nome, "genero": m.genero, "grupo": self.grupo_de[mid], "turma": m.turma, "secao": m.secao,
                "cargos": list(m.cargos), "convocado": convocado, "selos": selos[mid], "comparavel": comparavel[mid],
                "processos": c.processos, "na_mesa": c.na_mesa, "onde": {o: c.onde.get(o, 0) for o in ONDES},
                "segredo": c.segredo, "sobrestados": c.sobrestados, "liminar": c.liminar, "ente_publico": c.ente_publico,
                "sem_decisao": sem_decisao, "no_tribunal": no_tribunal, **({"autuacao": autuacao} if autuacao else {}),
                **({"relogio": relogio[mid]} if mid in relogio else {}), "areas": celulas[mid],
                "subareas": [[s, n, fracao(n, c.processos)] for s, n in _ordenado(c.subareas, int(self.listas.get("subareas_painel", 5)))],
                "partes": partes_min,
                "mais_antigos": folhas[:n_painel],
                "entradas": (entradas_por.get(mid) or {}).get("resumo") if entradas_painel is not None else None,
                "pauta": ({k: pautas_por.get(mid, Counter()).get(k, 0) for k in ("itens", "principal", "interno")}
                          if pautas_painel is not None else None),
                "decisoes": ({k: (decisoes_por.get(mid) or {}).get(k, 0) for k in ("processos", "documentos", "monocraticas", "acordaos")}
                             if decisoes_painel is not None else None),
                "temas": len(temas_por.get(mid, ())) if temas_painel is not None else None,
            }
            if entradas_painel is not None and resumo["entradas"] is None:
                resumo["entradas"] = {"sorteio_12m": 0, "heranca_12m": 0, "sorteio_30d": 0, "heranca_30d": 0}
            resumos.append(resumo)
            tem_algo = c.processos or pautas_por.get(mid) or (entradas_por.get(mid) or {}) or temas_por.get(mid) or decisoes_por.get(mid)
            if not tem_algo:
                continue
            # dossiê
            pares_n = sum(self.contas[o].processos for o in pares)
            pares_sub = self._soma(pares, "subareas")
            pares_rec = self._soma(pares, "recortes")
            pares_lit = self._soma(pares, "litigantes")
            pares_base = sum(self.contas[o].base_partes for o in pares)
            pares_ente = sum(self.contas[o].ente_publico for o in pares)
            top = []
            for gid, n in _ordenado(c.litigantes):
                if n < nome_ministro or len(top) >= int(self.listas.get("partes_ministro", 10)):
                    break
                p_min = fracao(n, c.base_partes)
                q = fracao(pares_lit.get(gid, 0), pares_base) if pares_base else None
                top.append({"id": gid, **nome_de(gid), "n": n, "recorre": c.polos.get((gid, "ativo"), 0),
                            "responde": c.polos.get((gid, "passivo"), 0), "outro": c.polos.get((gid, "outro"), 0),
                            "pares": q, "razao": razao(p_min, q)})
            dossie_folhas = folhas
            vistas = [{"processo": folha(p), "ministro_vista": None, "dias": None}
                      for p in sorted(c.vistas, key=lambda x: str(x.get("id")))[: n_pauta]]
            nomes_dossie = sorted({x["grupo"] for f in dossie_folhas for x in f["partes"] if x["grupo"]}
                                  | {x["grupo"] for e in em_pauta.get(mid, ()) for x in e["processo"]["partes"] if x["grupo"]}
                                  | {x["grupo"] for e in vistas for x in e["processo"]["partes"] if x["grupo"]})
            dossies[mid] = {
                "v": VERSAO, "id": mid, "trib": trib, "data_dados": self.data_dados,
                "ministro": {"id": mid, "nome": m.nome, "nome_completo": m.nome_completo, "genero": m.genero, "turma": m.turma,
                             "secao": m.secao, "orgao_nome": self.composicao.orgaos.get(m.turma or "") if m.turma else None,
                             "corte_especial": m.corte_especial, "cargos": list(m.cargos), "grade": m.grade, "posse": m.posse,
                             "turma_desde": m.turma_desde, "convocado": convocado, "gabinete": m.gabinete,
                             "grupo": self.grupo_de[mid], "link_oficial": None},
                "resumo": {k: v for k, v in resumo.items() if k != "mais_antigos"},
                "areas": [{"id": a, **celulas[mid][a], "tambem_toca": c.toca.get(a, 0)} for a in ids_areas if celulas[mid][a]["n"]],
                "subareas": [{"id": s, "area": s.split(".", 1)[0], "n": n, "p": fracao(n, c.processos),
                              "pares": fracao(pares_sub.get(s, 0), pares_n) if pares_n else None} for s, n in _ordenado(c.subareas)],
                "recortes": [{"id": r.id, "n": c.recortes.get(r.id, 0), "p": fracao(c.recortes.get(r.id, 0), c.processos),
                              "pares": fracao(pares_rec.get(r.id, 0), pares_n) if pares_n else None} for r in regras.recortes],
                "classes": _ordenado(c.classes),
                "onde": {o: c.onde.get(o, 0) for o in ONDES},
                "idade": {
                    "no_tribunal": {"faixas": faixas(c.no_tribunal, meses) if c.no_tribunal else None,
                                    "mediana": no_tribunal["mediana"], "p90": no_tribunal["p90"]},
                    **({"autuacao": autuacao} if autuacao else {}),
                    "sem_decisao": {"faixas": faixas(c.sem_decisao, meses), "mediana": sem_decisao["mediana"], "p90": sem_decisao["p90"],
                                    "mais_180": sem_decisao["mais_180"], "mais_365": sem_decisao["mais_365"]},
                },
                "mais_antigos": dossie_folhas,
                "em_pauta": em_pauta.get(mid, []),
                "em_vista": vistas if trib == "stf" else [],
                "partes": {"top": top, "pessoas_fisicas": c.pessoas_fisicas,
                           "ente_publico": {"n": c.ente_publico, "p": fracao(c.ente_publico, c.processos),
                                            "pares": fracao(pares_ente, pares_n) if pares_n else None},
                           "nomes": {g: nome_de(g) for g in nomes_dossie}},
                "entradas": (entradas_por.get(mid) or {}).get("dossie") if entradas_painel is not None else None,
                "decisoes": decisoes_por.get(mid) if decisoes_painel is not None else None,
                "temas": temas_por.get(mid, []) if temas_painel is not None else None,
            }

        # grupos e média
        grupos = []
        for g in self.composicao.grupos:
            membros = [m.id for m in self.composicao.ministros if self.grupo_de[m.id] == g.id]
            da_grade = [i for i in membros if i in self.grade]
            soma = sum(self.contas[i].processos for i in da_grade)
            areas_g = self._soma(da_grade, "areas")
            grupos.append({"id": g.id, "nome": g.nome, "descricao": g.descricao, "turmas": list(g.turmas), "ministros": membros,
                           "processos": soma, "na_mesa": sum(self.contas[i].na_mesa for i in da_grade),
                           "areas": {a: [areas_g[a], fracao(areas_g[a], soma)] for a in ids_areas if areas_g.get(a)}})
        grade_n = sum(self.contas[i].processos for i in self.grade)
        media = {"processos": grade_n, "areas": {a: [self.areas_grade[a], fracao(self.areas_grade[a], grade_n)]
                                                 for a in ids_areas if self.areas_grade.get(a)}}

        # fora da grade
        fora = []
        for linha in self.composicao.fora_da_grade:
            c = self.fora.get(linha.id)
            if c is None or not c.processos:
                continue
            fora.append({"id": linha.id, "rotulo": linha.rotulo, "nota": linha.nota, "n": c.processos, "na_mesa": None,
                         "areas": {a: c.areas[a] for a in ids_areas if c.areas.get(a)}, "relatores": _ordenado(c.relatores)})

        # áreas e recortes do tribunal
        grupos_grade = [g.id for g in self.composicao.grupos if any(self.grupo_de[i] == g.id for i in self.grade)]
        n_assuntos = int(self.listas.get("assuntos_por_area", 10))
        n_partes_area = int(self.listas.get("partes_por_area", 5))
        areas = []
        for a in ids_areas:
            partes_area = [[g, n] for g, n in _ordenado(self.lit_area_top.get(a, Counter())) if self.publicavel_tribunal(g)][:n_partes_area]
            nomes_usados.update(g for g, _ in partes_area)
            areas.append({"id": a, "n": self.areas.get(a, 0), "p": fracao(self.areas.get(a, 0), self.processos) or 0.0,
                          "na_grade": self.areas_grade.get(a, 0), "segredo": self.segredo_area.get(a, 0),
                          "por_grupo": {g: self.areas_grupo.get((g, a), 0) for g in grupos_grade},
                          "subareas": _ordenado(self.subareas.get(a, Counter())),
                          "assuntos": _ordenado(self.assuntos.get(a, Counter()), n_assuntos),
                          "partes": partes_area,
                          "tambem_toca": _ordenado(self.toca.get(a, Counter()))})
        recortes = [{"id": r.id, "n": self.recortes.get(r.id, 0), "p": fracao(self.recortes.get(r.id, 0), self.processos) or 0.0,
                     "na_grade": self.recortes_grade.get(r.id, 0)} for r in regras.recortes]

        # partes do tribunal
        top_ids = [g for g, n in _ordenado(self.lit_n) if self.publicavel_tribunal(g)]
        top = []
        for gid in top_ids[: int(self.listas.get("partes_tribunal", 30))]:
            nomes_usados.add(gid)
            area_dele = _ordenado(self.lit_area.get(gid, Counter()), 1)
            top.append({"id": gid, **nome_de(gid), "n": self.lit_n[gid], "p": fracao(self.lit_n[gid], self.base_partes),
                        "recorre": self.lit_polo.get((gid, "ativo"), 0), "responde": self.lit_polo.get((gid, "passivo"), 0),
                        "outro": self.lit_polo.get((gid, "outro"), 0), "mesas": len(self.lit_mesas.get(gid, ())),
                        "area": area_dele[0][0] if area_dele else None})
        top20 = set(top_ids[:20])
        concentrados = sum(1 for t in self.lit_por_processo if top20.intersection(t))
        ente_por_grupo = {g: [self.ente_por_grupo.get(g, 0), sum(self.contas[i].processos for i in self.grade if self.grupo_de[i] == g)]
                          for g in grupos_grade}

        # a fila
        fila_folhas = [folha(p) for p in self.fila.lista()]
        por_grupo_fila = {}
        for g in grupos_grade:
            ids = [i for i in self.grade if self.grupo_de[i] == g]
            valores = [v for i in ids for v in self.contas[i].sem_decisao]
            por_grupo_fila[g] = {"na_mesa": sum(self.contas[i].na_mesa for i in ids),
                                 "mais_180": sum(1 for v in valores if v > self.mesa_dias[0]),
                                 "mais_365": sum(1 for v in valores if v > self.mesa_dias[1])}
        vistas_fila = []
        if trib == "stf":
            pauta_vistas = {i.get("id"): i for i in pautas_itens if i.get("vista")}
            for p in sorted(self.vistas, key=lambda x: str(x.get("id")))[: int(self.listas.get("fila_mais_antigos", 30))]:
                item = pauta_vistas.get(p.get("id")) or {}
                vistas_fila.append({"processo": folha(p), "ministro_vista": item.get("vista_id"), "relator_id": p.get("relator_id"),
                                    "sessao": item.get("data")})
            for i in sorted(pauta_vistas.values(), key=lambda x: (str(x.get("data")), str(x.get("id")))):
                if i.get("id") in self.pauta_segredo or any(v["processo"]["id"] == i.get("id") for v in vistas_fila):
                    continue
                vistas_fila.append({"processo": {"id": i.get("id"), "classe": i.get("classe"), "numero": i.get("numero")},
                                    "ministro_vista": i.get("vista_id"), "relator_id": i.get("relator_id"), "sessao": i.get("data")})

        grade_sem = [v for i in self.grade for v in self.contas[i].sem_decisao]
        grade_trib = [v for i in self.grade for v in self.contas[i].no_tribunal]
        grade_anos: Counter[int] = Counter()
        for i in self.grade:
            grade_anos.update(self.contas[i].anos)
        autuacao_grade = _autuacao(grade_anos, ref)
        universo = {
            "processos": self.processos, "na_grade": grade_n,
            "na_mesa": sum(self.contas[i].na_mesa for i in self.grade), "segredo": self.segredo,
            "sobrestados": self.sobrestados, "liminar": self.liminar, "ente_publico": self.ente_publico,
            "sem_decisao_180": sum(1 for v in grade_sem if v > self.mesa_dias[0]),
            "sem_decisao_365": sum(1 for v in grade_sem if v > self.mesa_dias[1]),
            "pessoas_fisicas": self.pessoas_fisicas, "recursos": self.recursos,
        }
        nome, sigla = NOMES[trib]
        painel = {
            "v": VERSAO, "trib": trib, "nome": nome, "sigla": sigla, "data_dados": self.data_dados,
            "universo": universo,
            "idade": {"sem_decisao": idade(grade_sem, limites=self.mesa_dias),
                      "no_tribunal": idade(grade_trib, limites=self.mesa_dias, completa=trib == "stj"),
                      **({"autuacao": autuacao_grade} if autuacao_grade else {})},
            "grupos": grupos, "media": media, "ministros": resumos, "fora_da_grade": fora, "areas": areas, "recortes": recortes,
            "partes": {"top": top, "pessoas_fisicas": self.pessoas_fisicas,
                       "ente_publico": {"n": self.ente_publico, "p": fracao(self.ente_publico, self.processos), "por_grupo": ente_por_grupo},
                       "concentracao_top20": fracao(concentrados, self.base_partes),
                       "nomes": {}},
            "fila": {"mais_antigos": fila_folhas, "por_grupo": por_grupo_fila,
                     "sobrestados": {"total": self.sobrestados, "por_motivo": dict(_ordenado(self.motivos))},
                     "vistas": vistas_fila},
            "entradas": entradas_painel, "pautas": pautas_painel, "decisoes": decisoes_painel, "temas": temas_painel,
            "valor": {"publicado": False, "texto": TEXTO_VALOR,
                      "medidas": {"ente_publico": {"n": self.ente_publico, "p": fracao(self.ente_publico, self.processos)},
                                  "liminar": {"n": self.liminar, "p": fracao(self.liminar, self.processos)},
                                  "recortes": {r: self.recortes.get(r, 0) for r in regras.recortes_economicos}}},
        }
        nomes_usados.discard(None)  # type: ignore[arg-type]
        painel["partes"]["nomes"] = {g: nome_de(g) for g in sorted(nomes_usados)}

        # cobertura e sinais (para meta.json e para achados.py)
        cobertura = {"partes": {"base": self.base_partes, "litigantes": len(self.lit_n),
                                "publicaveis": sum(1 for g in self.lit_n if self.publicavel_tribunal(g)),
                                "processos_com_litigante": len(self.lit_por_processo)}}
        if self.cobertura_stf is not None:
            cobertura["partes"]["stf"] = self.cobertura_stf.para_meta()
        sinais = {
            "trib": trib, "data_dados": self.data_dados, "hoje": self.hoje.isoformat(),
            "testes": len(testes), "marcas": marcas,
            "mesa": self._sinais_mesa(comparavel, alfa, razao_min, razao_max, pontos, selos=selos, relogio=relogio),
            "heranca": {mid: d["dossie"]["heranca_dias"] for mid, d in entradas_por.items() if d["dossie"]["heranca_dias"]},
            "entradas_dia": ({d: {dest: dict(c) for dest, c in fluxo.por_dia[d].items()} for d in fluxo.dias[-7:]}
                             if fluxo is not None else {}),
            "total_dia": {d: fluxo.total_dia.get(d, 0) for d in fluxo.dias[-7:]} if fluxo is not None else {},
            "temas_quentes": _temas_quentes(fluxo, regras),
            "litigantes_semana": _litigantes_semana(fluxo, regras),
            "vistas": dict(Counter(v["ministro_vista"] for v in vistas_fila if v.get("ministro_vista"))),
            "temas_recentes": temas_recentes(temas, self.hoje) if trib == "stj" and temas is not None else [],
            "pauta_janela": ((pautas or {}).get("janela") if trib == "stf" and isinstance(pautas, Mapping) else None),
        }
        return Resultado(painel=painel, dossies=dossies, cobertura=cobertura, sinais=sinais)

    def _chegou_na_janela(self, m: Any, ref: date) -> bool:
        """Chegou à Turma, foi convocado ou tomou posse há menos de ``janelas.entradas_recentes_dias``
        (30) dias da data dos dados: o sorteio de 30 dias dele não é comparável ao dos pares."""
        dias = int(((self.regras.limiares.get("janelas") or {}).get("entradas_recentes_dias")) or 30)
        for marco in (getattr(m, "turma_desde", None), getattr(m, "posse", None)):
            d = _data(marco)
            if d is not None and 0 <= (ref - d).days < dias:
                return True
        return False

    def _distribuicao_reduzida(self, entradas_painel: Mapping[str, Any] | None, entradas_por: Mapping[str, Any]) -> set[str]:
        """Os gabinetes da grade que recebem por distribuição bem menos que os pares (quem acumula o
        TSE, por exemplo): com 20 dias de ata ou mais, sorteio de 30 dias abaixo de
        ``limiares.distribuicao_reduzida`` × a mediana da Turma (STJ) ou do grupo (STF). O zero é
        "sem distribuição" (outro selo)."""
        if entradas_painel is None or entradas_painel.get("dias_com_ata", 0) < 20:
            return set()
        ref = _data(self.data_dados) or self.hoje
        fator = float(self.regras.limiares.get("distribuicao_reduzida", 0.5))
        chave = (lambda m: m.turma) if self.trib == "stj" else (lambda m: self.grupo_de[m.id])
        por_turma: dict[str, list[str]] = defaultdict(list)
        for mid in sorted(self.grade):
            # Quem chegou à Turma (ou tomou posse) dentro da janela de 30 dias não teve os 30 dias de
            # sorteio: nem é marcado, nem entra na mediana dos pares.
            if self._chegou_na_janela(self.ministros[mid], ref):
                continue
            por_turma[str(chave(self.ministros[mid]))].append(mid)
        saida = set()
        for ids in por_turma.values():
            recebido = {mid: int(((entradas_por.get(mid) or {}).get("resumo") or {}).get("sorteio_30d") or 0) for mid in ids}
            if len(ids) < 2:
                continue
            base = median(recebido.values())
            saida |= {mid for mid, n in recebido.items() if 0 < n < fator * base}
        return saida

    def _sinais_mesa(self, comparavel: Mapping[str, bool], alfa: float, razao_min: float, razao_max: float,
                     pontos: float, *, selos: Mapping[str, Sequence[str]] | None = None,
                     relogio: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
        """A "mesa antiga": em cada Turma (STJ) ou grupo (STF), o ministro comparável com a maior
        parte da mesa parada há mais de um ano, testado contra o resto da Turma (binomial exata,
        Holm a 99% entre as Turmas, piso de efeito).

        Ficam fora dos candidatos **e dos pares**: quem não é comparável, quem recebe menos por
        distribuição (selos ``distribuicao_reduzida`` e ``sem_distribuicao``: o acervo envelhece de
        outro jeito) e quem teve o relógio reiniciado há menos de ``mesa_dias[1]`` dias (chegada à
        Turma, herança: não pode ter nada acima do limite por construção, e baixaria a taxa dos
        pares). Sem pelo menos 2 pares, a Turma fica sem candidato."""
        limite = self.mesa_dias[1]
        selos = selos or {}
        relogio = relogio or {}
        chave_grupo = (lambda m: m.turma) if self.trib == "stj" else (lambda m: self.grupo_de[m.id])
        por_turma: dict[str, list[str]] = defaultdict(list)
        for mid in sorted(self.grade):
            por_turma[str(chave_grupo(self.ministros[mid]))].append(mid)
        candidatos = {}
        for turma, ids in sorted(por_turma.items()):
            dados = {}
            for mid in ids:
                valores = self.contas[mid].sem_decisao
                dados[mid] = (sum(1 for v in valores if v > limite), len(valores))
            validos = [mid for mid in ids if comparavel.get(mid) and mid not in relogio
                       and not ({"distribuicao_reduzida", "sem_distribuicao"} & set(selos.get(mid) or ()))]
            elegiveis = [mid for mid in validos if dados[mid][1] >= 100]
            if not elegiveis:
                continue
            mid = max(elegiveis, key=lambda i: (dados[i][0] / dados[i][1], i))
            k, n = dados[mid]
            pares = [o for o in validos if o != mid]
            if len(pares) < 2:
                continue
            pk = sum(dados[o][0] for o in pares)
            pn = sum(dados[o][1] for o in pares)
            if not pn or not (0 < pk / pn < 1):
                continue
            candidatos[(turma, mid)] = {"turma": turma, "ministro": mid, "k": k, "n": n, "p": round(k / n, 4),
                                        "pares": round(pk / pn, 4), "pares_k": pk, "pares_n": pn,
                                        "n_pares": len(pares), "fora": sorted(o for o in ids if o != mid and o not in pares),
                                        "p_valor": binomial_bicaudal(k, n, pk / pn)}
        passam = holm({c: v["p_valor"] for c, v in candidatos.items()}, alfa) if candidatos else set()
        saida = []
        for chave, v in sorted(candidatos.items()):
            r = v["p"] / v["pares"] if v["pares"] else math.inf
            v["razao"] = round(r, 2) if math.isfinite(r) else None
            v["passa"] = chave in passam and r >= razao_max and (v["p"] - v["pares"]) >= pontos
            saida.append(v)
        return saida


def _autuacao(anos: Mapping[int, int], ref: date) -> dict[str, Any] | None:
    """STF: a planilha só dá o ano de autuação. ``{ano_mediano, no_ano_dos_dados, por_ano}``: o
    ano em que metade do acervo já tinha chegado (a mediana dos anos), a parte autuada no ano dos
    dados e a contagem por ano. ``None`` sem nenhum ano (STJ, que tem a data)."""
    total = sum(anos.values())
    if not total:
        return None
    ordenados = sorted(anos.elements())
    return {"ano_mediano": int(median_low(ordenados)), "no_ano_dos_dados": fracao(anos.get(ref.year, 0), total),
            "por_ano": [[a, anos[a]] for a in sorted(anos)]}


def _temas_quentes(fluxo: Fluxo | None, regras: Regras) -> list[dict[str, Any]]:
    """Subárea cuja entrada dos últimos 30 dias passa de ``tema_quente_vezes`` × a mediana mensal
    dos 12 meses anteriores, com ``tema_quente_minimo`` processos ou mais. Só com 13 meses de atas."""
    if fluxo is None or not fluxo.por_dia_area:
        return []
    dias = sorted(fluxo.por_dia_area)
    fim = date.fromisoformat(dias[-1])
    if (fim - date.fromisoformat(dias[0])).days < 30 * 13 - 5:
        return []
    lim = regras.limiares
    vezes, minimo = float(lim.get("tema_quente_vezes", 2)), int(lim.get("tema_quente_minimo", 50))
    janelas = [(fim - timedelta(days=30 * (i + 1) - 1), fim - timedelta(days=30 * i)) for i in range(13)]
    contagem: dict[str, list[int]] = defaultdict(lambda: [0] * 13)
    for d, c in fluxo.por_dia_area.items():
        dd = date.fromisoformat(d)
        for j, (a, b) in enumerate(janelas):
            if a <= dd <= b:
                for sub, n in c.items():
                    contagem[sub][j] += n
                break
    saida = []
    for sub, serie in sorted(contagem.items()):
        base = median(serie[1:])
        if serie[0] >= minimo and base > 0 and serie[0] > vezes * base:
            saida.append({"subarea": sub, "n": serie[0], "mediana": base, "janela": [janelas[0][0].isoformat(), janelas[0][1].isoformat()]})
    return saida


def _litigantes_semana(fluxo: Fluxo | None, regras: Regras) -> list[dict[str, Any]]:
    """Litigante cuja entrada dos últimos 7 dias passa de ``litigante_semana_vezes`` × a média
    semanal das 12 semanas anteriores, com ``litigante_semana_minimo`` processos ou mais. Só com
    13 semanas de atas."""
    if fluxo is None or not fluxo.por_dia_parte:
        return []
    dias = sorted(fluxo.por_dia)
    fim = date.fromisoformat(dias[-1])
    if (fim - date.fromisoformat(dias[0])).days < 7 * 13 - 1:
        return []
    lim = regras.limiares
    vezes, minimo = float(lim.get("litigante_semana_vezes", 3)), int(lim.get("litigante_semana_minimo", 100))
    semana: Counter[str] = Counter()
    antes: Counter[str] = Counter()
    for d, c in fluxo.por_dia_parte.items():
        idade_dias = (fim - date.fromisoformat(d)).days
        if idade_dias < 7:
            semana.update(c)
        elif idade_dias < 7 * 13:
            antes.update(c)
    saida = []
    for gid, n in sorted(semana.items()):
        media_sem = antes.get(gid, 0) / 12
        if n >= minimo and n > vezes * media_sem:
            saida.append({"grupo": gid, "n": n, "media": round(media_sem, 1)})
    return saida


# ── a porta de entrada ───────────────────────────────────────────────────────────────────────


def montar(
    trib: str,
    processos: Iterable[Mapping[str, Any]],
    regras: Regras,
    *,
    data_dados: str | None,
    hoje: date | str | None = None,
    catalogo: Catalogo | None = None,
    atas: Any = None,
    pautas: Any = None,
    decisoes: Any = None,
    temas: Any = None,
    completar_partes: bool = True,
) -> Resultado:
    """Painel, dossiês, cobertura e sinais de um tribunal, numa passada pelos ``processos``.

    STJ: ``atas`` = itens de ``stj.ler_ata`` (de um ou mais dias); ``pautas`` = itens de
    ``stj.ler_pautas`` (ou ``{"data_dados", "itens"}``); ``decisoes`` = itens de
    ``stj.ler_integras`` (ou ``{"itens", "ultimo_arquivo"}``); ``temas`` = ``stj.montar_temas``.
    STF: ``atas`` = ``stf.coletar_atas``; ``pautas`` = ``stf.coletar_pauta``; ``decisoes`` =
    ``stf.ler_decisoes``; ``temas`` = ``stf.coletar_rg``. Fonte que faltou: ``None`` (o bloco vem
    ``null`` no painel). ``completar_partes``: passa cada processo por ``partes.completar`` (desligue
    quem já tipificou)."""
    cat = catalogo or catalogo_padrao()
    janela = int((regras.limiares.get("janelas") or {}).get("entradas_dias", 365))
    if trib == "stj":
        itens_atas = list(atas) if atas is not None else None
        if itens_atas is not None:
            for i in itens_atas:
                cat.completar({"trib": "stj", "partes": i.get("partes") or []})  # type: ignore[typeddict-item]
        fluxo = fluxo_stj(itens_atas, janela_dias=janela) if itens_atas is not None else None
        _, itens_pauta = _pautas_stj(pautas)
        pauta_ids = {str(i.get("registro")) for i in itens_pauta if i.get("registro")}
    else:
        fluxo = fluxo_stf(atas) if atas is not None else None
        pauta_ids = {str(i.get("id")) for i in ((pautas or {}).get("itens") or ()) if i.get("id")}
    contas = Contas(trib, regras, catalogo=cat, data_dados=data_dados, hoje=hoje, pauta_ids=pauta_ids)
    for p in processos:
        if completar_partes:
            if contas.cobertura_stf is not None and not p.get("segredo"):
                contas.cobertura_stf.somar_colados(p.get("partes") or (), cat)
            cat.completar(p)  # type: ignore[arg-type]
        contas.somar(p)
    return contas.resultado(fluxo=fluxo, pautas=pautas, decisoes=decisoes, temas=temas)


# ── a camada do CNJ (DataJud): "concluso há N dias" no STJ ───────────────────────────────────

# O estado de cada processo na mesa diante do que o DataJud trouxe (a letra vai ao cnj.json):
ESTADOS_CNJ = {
    "c": "concluso ao relator (a conclusão veio depois do último ato do magistrado)",
    "n": "sem conclusão em aberto (houve ato do magistrado depois da última conclusão, ou nenhuma conclusão desde a última distribuição ou decisão)",
    "d": "o DataJud está atrás do acervo do STJ para este processo (o último movimento dele é anterior à última distribuição ou decisão)",
    "x": "há movimento fora da Tabela Processual Unificada depois da última conclusão",
    "a": "não está no índice público do DataJud",
    "p": "ainda não consultado (a carga anda em fatias)",
}
COM_DATA = ("c", "n")  # o estado do processo está determinado (entra na cobertura)


def estado_cnj(linha: Mapping[str, Any], registro: Mapping[str, Any] | None, ref: date) -> tuple[str, int | None]:
    """O estado de um processo na mesa (``ESTADOS_CNJ``) e, quando concluso, há quantos dias.

    ``linha``: o processo no retrato do acervo do STJ (``distribuicao``, ``ultima_decisao``);
    ``registro``: o que o DataJud trouxe (``datajud.resumir_documento``); ``ref``: a data do acervo.
    Concluso só quando (1) a última Conclusão (TPU 51) veio depois do último ato do magistrado, (2) não
    há movimento fora da TPU depois dela e (3) ela não é anterior à última distribuição nem à última
    decisão que o acervo do STJ registra (o STJ sabe de algo que o DataJud ainda não tem: não vale).
    Os dias contam da data da conclusão até a data do acervo, como o "sem decisão" do STJ, e nunca
    passam dele (a conclusão vem depois da distribuição ou da decisão)."""
    if registro is None:
        return "p", None
    if registro.get("aus"):
        return "a", None
    marco = max((d for d in (linha.get("distribuicao"), linha.get("ultima_decisao")) if d), default=None)
    ultimo = registro.get("u")
    if not ultimo or (marco and str(ultimo) < str(marco)):
        return "d", None
    if registro.get("x"):
        return "x", None
    conclusao = registro.get("c")
    if registro.get("ab") and conclusao and (not marco or str(conclusao) >= str(marco)):
        d = _data(str(conclusao))
        return "c", (max(0, (ref - d).days) if d else None)
    return "n", None


@dataclass
class _ContaCNJ:
    base: int = 0  # na mesa, público, não suspenso, com a data da última distribuição ou decisão
    segredo: int = 0  # na mesa em segredo de justiça: o índice público não tem
    estados: Counter[str] = field(default_factory=Counter)
    conclusos: list[int] = field(default_factory=list)  # dias dos conclusos
    candidatos: Counter[int] = field(default_factory=Counter)  # limite → sem decisão há mais que ele
    com_data: Counter[int] = field(default_factory=Counter)  # limite → candidatos com o estado determinado
    acima: Counter[int] = field(default_factory=Counter)  # limite → conclusos há mais que ele

    def somar(self, outra: _ContaCNJ) -> None:
        self.base += outra.base
        self.segredo += outra.segredo
        self.estados.update(outra.estados)
        self.conclusos += outra.conclusos
        self.candidatos.update(outra.candidatos)
        self.com_data.update(outra.com_data)
        self.acima.update(outra.acima)

    def para_json(self, limites: Sequence[int], minimo: float) -> dict[str, Any]:
        com_data = sum(self.estados.get(e, 0) for e in COM_DATA)
        cobertura = round(com_data / self.base, 4) if self.base else None
        suficiente = cobertura is not None and cobertura >= minimo
        faixas_saida = []
        for lim in limites:
            cand, det = self.candidatos.get(lim, 0), self.com_data.get(lim, 0)
            cob = round(det / cand, 4) if cand else None
            ok = cand == 0 or (cob is not None and cob >= minimo)
            faixas_saida.append({"dias": lim, "candidatos": cand, "com_data": det, "cobertura": cob, "suficiente": ok,
                                 "conclusos": self.acima.get(lim, 0) if ok else None})
        ordenados = sorted(self.conclusos)
        return {
            "base": self.base, "com_data": com_data, "cobertura": cobertura, "suficiente": suficiente, "segredo": self.segredo,
            "estados": {e: self.estados.get(e, 0) for e in ESTADOS_CNJ},
            "conclusos": self.estados.get("c", 0),
            "mediana": round(percentil(ordenados, 50)) if suficiente and ordenados else None,
            "p90": round(percentil(ordenados, 90)) if suficiente and ordenados else None,
            "faixas": faixas_saida,
        }


def camada_cnj(
    linhas: Iterable[Mapping[str, Any]],
    registros: Mapping[str, Mapping[str, Any]],
    regras: Regras,
    *,
    data_dados: str | None,
    listados: Iterable[str] = (),
) -> dict[str, Any]:
    """A camada do CNJ sobre a mesa do STJ (o ``cnj.json``, só publicado com ``datajud.publicar``).

    ``linhas``: a mesa de hoje (``datajud.ler_retrato``); ``registros``: o diário do DataJud
    (número CNJ → registro); ``listados``: os ids dos processos que a página lista (a fila e os mais
    antigos de cada ministro), que ganham o estado um a um.

    Por ministro e no tribunal (só a grade, como o "sem decisão"): ``base`` (na mesa, público, não
    suspenso), quantos têm o estado determinado (``com_data``: concluso ou sem conclusão em aberto),
    a ``cobertura``, os estados, a mediana dos dias de concluso (só com cobertura suficiente) e as
    **faixas** de ``mesa_dias``: para cada limite, os candidatos (sem decisão registrada há mais que
    ele: só esses podem estar conclusos há mais que ele), quantos deles têm o estado determinado e,
    quando a cobertura passa de ``datajud.movimentos.cobertura_minima``, quantos estão conclusos há
    mais que o limite (senão ``conclusos: null`` e ``suficiente: false``: "cobertura insuficiente")."""
    composicao: Composicao = regras.composicao["stj"]
    limites = tuple(int(x) for x in (regras.limiares.get("mesa_dias") or (180, 365)))
    minimo = float((regras.datajud.get("movimentos") or {}).get("cobertura_minima", 0.80))
    ref = _data(data_dados) or datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    grade = {m.id for m in composicao.da_grade()}
    ministros = {m.id for m in composicao.ministros}  # a grade e os de cargo (Presidência, Vice, Corregedoria)
    contas: dict[str, _ContaCNJ] = defaultdict(_ContaCNJ)
    fora = _ContaCNJ()
    listados = set(listados)
    processos: dict[str, list[Any]] = {}
    for x in linhas:
        rid = str(x.get("relator") or "")
        conta = contas[rid] if rid in ministros else fora
        if x.get("segredo"):
            conta.segredo += 1
            continue
        if x.get("sobrestado") or x.get("sem_decisao_dias") is None:
            continue
        registro = registros.get(str(x.get("cnj") or ""))
        estado, dias = estado_cnj(x, registro, ref)
        conta.base += 1
        conta.estados[estado] += 1
        sem = int(x["sem_decisao_dias"])
        if estado == "c" and dias is not None:
            conta.conclusos.append(dias)
        for lim in limites:
            if sem > lim:
                conta.candidatos[lim] += 1
                if estado in COM_DATA:
                    conta.com_data[lim] += 1
                    if estado == "c" and dias is not None and dias > lim:
                        conta.acima[lim] += 1
        if x.get("id") in listados:
            data = (registro or {}).get("c") if estado == "c" else (registro or {}).get("a") if estado == "n" else None
            processos[str(x["id"])] = [estado, dias, data]
    tribunal = _ContaCNJ()
    for mid in sorted(contas):
        if mid in grade:
            tribunal.somar(contas[mid])
    todos = _ContaCNJ()
    for c in contas.values():
        todos.somar(c)
    todos.somar(fora)
    return {
        "data_dados": data_dados,
        "limites": list(limites), "cobertura_minima": minimo,
        "universo": tribunal.para_json(limites, minimo),
        "mesa_inteira": todos.para_json(limites, minimo),  # com a Presidência, a Vice e os resíduos (a cobertura da carga)
        "ministros": {mid: contas[mid].para_json(limites, minimo) for mid in sorted(contas)},
        "processos": {k: processos[k] for k in sorted(processos)},
    }



__all__ = ["ESTADOS_CNJ", "Conta", "Contas", "Fluxo", "Resultado", "camada_cnj", "estado_cnj", "faixas", "fluxo_stf", "fluxo_stj",
           "fracao", "idade", "link_oficial", "mini_folha", "montar", "razao"]
