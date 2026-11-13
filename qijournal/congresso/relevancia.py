"""O que espera e o que passa: o índice de relevância das matérias.

Duas listas por Casa, lidas com a mesma régua:

- **as que esperam**: prontas para o Plenário e fora da pauta (na Câmara, "Pronta para
  Pauta no Plenário"; no Senado, "Pronto para deliberação do Plenário");
- **as que passaram**: aprovadas pelo Plenário nos últimos 12 meses. Na Câmara, a data
  e o caminho saem do histórico oficial de cada matéria (``/proposicoes/{id}/tramitacoes``):
  quando a urgência foi aprovada e a pedido de quem, se o parecer foi dado direto no
  Plenário (sem passar pelas comissões) e por quem. No Senado, da lista de deliberações
  (``/processo?dataInicioDeliberacao=``).

O **índice** (0 a 100) soma sinais de relevância pública que dá para medir nos registros,
com os pesos de ``config/congresso.yaml`` (``relevancia``):

- **demanda**: quantos projetos parecidos, apresentados até a data de referência,
  foram anexados à matéria (outros parlamentares pedindo o mesmo), em escala
  logarítmica. Só na Câmara: os dados do Senado não dizem o que tramita junto;
- **origem**: já aprovada pela outra Casa, do Executivo, de uma comissão, do Judiciário
  e órgãos de controle, da sociedade, ou de um parlamentar;
- **alcance**: o tipo de norma (PEC, lei complementar, lei, decreto legislativo);
  matéria simbólica (dia nacional, denominação, título) tem teto baixo;
- **apoio**: autores de mais de um partido, e dos dois polos.

O índice não diz se a matéria é boa; diz quantos sinais de relevância ela tem. Cada
nota vai decomposta, para quem lê conferir de onde veio.
"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .. import net, text
from . import fontes, projetos
from .fontes import Coletor
from .projetos import Proposicao
from .regras import Regras, Relevancia

log = logging.getLogger(__name__)

SIGLAS = ("PL", "PLP", "PEC", "PLS", "PLC")  # o que entra nas duas listas (medidas provisórias têm prazo na Constituição)
FILHOS = frozenset(("PL", "PLP", "PEC", "PDL"))  # o que conta como projeto parecido anexado
JANELA_APROVADAS = 365  # dias: as aprovadas pelo Plenário neste período
RAPIDA = 120  # dias do protocolo à aprovação: abaixo disso, passou rápido
LISTA = 12
PONTOS_MAX = 2500  # pontos no gráfico (as prontas mais antigas ficam de fora, se passar disso)
VALIDADE_TRAMITACAO_H = 24 * 30  # o caminho de uma matéria já aprovada não muda
PARALELO = 4
NOMES = {
    "demanda": "projetos parecidos",
    "origem": "origem",
    "alcance": "alcance",
    "apoio": "apoio entre partidos",
}
ORIGENS = {
    "outra_casa": "já aprovada pela outra Casa",
    "popular": "da sociedade",
    "executivo": "do Executivo",
    "comissao": "de comissão",
    "judiciario": "do Judiciário ou órgão de controle",
    "parlamentar": "de parlamentar",
}


@dataclass
class Nota:
    valor: int  # 0 a 100
    pontos: dict[str, int]  # componente → pontos na nota (somam o valor, a menos do teto das simbólicas)
    demanda: int | None  # projetos parecidos anexados na janela (None: a Casa não informa)
    simbolica: bool = False


@dataclass
class Aprovada:
    prop: Proposicao
    data: str  # aprovação no Plenário
    caminho: dict[str, Any] = field(default_factory=dict)  # urgência, parecer em Plenário, relator


# ── a régua ──────────────────────────────────────────────────────────────────


def apensados(props: Iterable[Proposicao]) -> dict[int, list[str]]:
    """Matéria principal → datas de apresentação dos projetos parecidos anexados a ela."""
    por: dict[int, list[str]] = defaultdict(list)
    for p in props:
        if p.principal and p.sigla in FILHOS and p.apresentada:
            por[p.principal].append(p.apresentada)
    for datas in por.values():
        datas.sort()
    return dict(por)


def _simbolica(ementa: str, padroes: list[re.Pattern[str]]) -> bool:
    e = text.normalize(ementa or "")
    return any(p.search(e) for p in padroes)


def _apoio(partidos: list[str], polos: Any) -> float:
    if len(partidos) < 2:
        return 0.0
    if polos is not None and set(partidos) & polos.esquerda and set(partidos) & polos.direita:
        return 1.0
    return 0.5 if len(partidos) == 2 else 0.75


class Regua:
    """Dá a nota de uma matéria numa data de referência (hoje, para as que esperam; a
    aprovação, para as que passaram)."""

    def __init__(self, regras: Regras, filhos: Mapping[int, list[str]] | None) -> None:
        self.regras = regras
        self.rel: Relevancia = regras.relevancia
        self.filhos = filhos  # None: a Casa não informa os apensados
        self.padroes = [re.compile(p) for p in self.rel.simbolicas]

    def nota(self, p: Proposicao, ref: str) -> Nota:
        rel = self.rel
        partes: dict[str, float] = {}
        demanda = None
        if self.filhos is not None:
            inicio = (date.fromisoformat(ref) - timedelta(days=365 * rel.janela_demanda_anos)).isoformat() if rel.janela_demanda_anos else ""
            demanda = sum(1 for d in self.filhos.get(p.id, []) if inicio <= d <= ref)
            partes["demanda"] = min(1.0, math.log1p(demanda) / math.log1p(max(1, rel.demanda_cheia)))
        partes["origem"] = dict(rel.origem).get(p.origem or "parlamentar", 0.0)
        partes["alcance"] = dict(rel.alcance).get(p.sigla, 0.4)
        partes["apoio"] = _apoio(p.partidos, self.regras.polos_em(ref))
        pesos = {k: w for k, w in rel.pesos if k in partes and w > 0}
        total = sum(pesos.values())
        if not total:
            return Nota(0, {}, demanda)
        pontos = {k: round(100 * w * partes[k] / total) for k, w in pesos.items()}
        valor = round(100 * sum(w * partes[k] for k, w in pesos.items()) / total)
        simbolica = _simbolica(p.ementa, self.padroes)
        if simbolica:
            valor = min(valor, round(rel.simbolica_teto))
        return Nota(valor, pontos, demanda, simbolica)


# ── as que passaram ──────────────────────────────────────────────────────────

_REQUERENTES = re.compile(
    r"(?:dos?|das?)\s+(?:Srs?\.?|Sras?\.?|Senhor(?:es)?|Senhoras?|Deputad[oa]s?|L[ií]der(?:es)?(?: d[oa]s?)?)\s*(.+?),?\s+que\s+solicita",
    re.IGNORECASE,
)
_RELATOR = re.compile(r"Relator(?:a)?,?\s+(?:Dep\.|Deputad[oa])\s*([^(]+?\s*\([^)]+\))")


def caminho_camara(tramitacoes: Iterable[Mapping[str, Any]], desde: str) -> tuple[str, dict[str, Any]] | None:
    """Do histórico oficial: a última aprovação no Plenário desde ``desde`` e o caminho
    até ela (a urgência aprovada antes dela, o parecer dado no Plenário e o relator)."""
    passos = sorted(tramitacoes, key=lambda t: (str(t.get("dataHora") or ""), t.get("sequencia") or 0))
    aprovacao = None
    for t in passos:
        data = str(t.get("dataHora") or "")[:10]
        descricao = text.normalize(t.get("descricaoTramitacao") or "")
        if (t.get("siglaOrgao") or "").upper() == "PLEN" and descricao.startswith("aprovacao de proposicao") and data >= desde:
            aprovacao = data
            break  # a primeira aprovação no Plenário dentro da janela (a redação final vem no mesmo dia)
    if aprovacao is None:
        return None
    caminho: dict[str, Any] = {}
    for t in passos:
        data = str(t.get("dataHora") or "")[:10]
        if data > aprovacao:
            break
        descricao = text.normalize(t.get("descricaoTramitacao") or "")
        despacho = re.sub(r"\s+", " ", str(t.get("despacho") or "")).strip()
        orgao = (t.get("siglaOrgao") or "").upper()
        if "aprovacao de urgencia" in descricao and "requerimento" in text.normalize(despacho):
            achado = _REQUERENTES.search(despacho)
            caminho["urgencia"] = {"d": data, "por": text.truncate(achado.group(1).strip(), 120) if achado else ""}
        elif orgao == "PLEN" and "parecer proferido em plenario" in text.normalize(despacho):
            relator = _RELATOR.search(despacho)
            caminho["parecer_plenario"] = {"d": data, "relator": relator.group(1).strip() if relator else ""}
    return aprovacao, caminho


def camara_aprovadas(
    coletor: Coletor, props: Iterable[Proposicao], hoje: str, *, dias: int = JANELA_APROVADAS, validade_h: float = VALIDADE_TRAMITACAO_H
) -> tuple[list[Aprovada], int]:
    """As matérias aprovadas pelo Plenário da Câmara nos últimos ``dias`` (as que o arquivo
    diz aprovadas, sujeitas ao Plenário e com andamento na janela), com o caminho de cada
    uma. Devolve também quantos históricos não puderam ser lidos."""
    desde = (date.fromisoformat(hoje) - timedelta(days=dias)).isoformat()
    candidatas = [
        p
        for p in props
        if p.sigla in SIGLAS
        and p.estado in (projetos.APROVADA, projetos.NORMA)
        and p.apreciacao == "plenario"
        and (p.movimento or "") >= desde
    ]

    def uma(p: Proposicao) -> tuple[Proposicao, tuple[str, dict[str, Any]] | None, bool]:
        try:
            passos = fontes.camara_api(coletor, f"proposicoes/{p.id}/tramitacoes", validade_h=validade_h, paginada=False)
        except Exception as exc:  # noqa: BLE001 — uma matéria fora do ar não derruba as outras
            log.debug("Câmara: histórico de %s indisponível (%s)", p.titulo, exc)
            return p, None, False
        return p, caminho_camara(passos, desde), True

    aprovadas, falhas = [], 0
    for p, achado, ok in net.parallel_map(uma, candidatas, max_workers=PARALELO):
        falhas += not ok
        if achado is not None:
            aprovadas.append(Aprovada(prop=p, data=achado[0], caminho=achado[1]))
    log.info("Câmara: %d matérias aprovadas pelo Plenário desde %s (%d históricos consultados)", len(aprovadas), desde, len(candidatas))
    return aprovadas, falhas


def senado_aprovadas(coletor: Coletor, hoje: str, *, dias: int = JANELA_APROVADAS, validade_h: float = 20.0) -> list[Aprovada]:
    """As matérias aprovadas pelo Plenário do Senado nos últimos ``dias`` (a lista de
    deliberações, um trimestre por consulta)."""
    fim = date.fromisoformat(hoje)
    inicio = fim - timedelta(days=dias)
    vistos: set[int] = set()
    aprovadas = []
    trecho = inicio
    while trecho <= fim:
        ate = min(fim, trecho + timedelta(days=91))
        bruto = fontes.senado_api(
            coletor, "processo", validade_h=validade_h, dataInicioDeliberacao=trecho.isoformat(), dataFimDeliberacao=ate.isoformat()
        )
        for item in bruto if isinstance(bruto, list) else []:
            if not isinstance(item, dict) or str(item.get("siglaTipoDeliberacao") or "") != "APROVADA_NO_PLENARIO":
                continue
            if text.normalize(item.get("objetivo") or "") in ("substitutivo", "emenda"):
                continue
            p = projetos._do_senado({**item, "tramitando": "Não"})
            if p is None or p.sigla not in SIGLAS or p.id in vistos:
                continue
            vistos.add(p.id)
            p.estado = projetos.APROVADA
            aprovadas.append(Aprovada(prop=p, data=str(item.get("dataDeliberacao") or "")[:10]))
        trecho = ate + timedelta(days=1)
    log.info("Senado: %d matérias aprovadas pelo Plenário desde %s", len(aprovadas), inicio.isoformat())
    return aprovadas


# ── o painel ─────────────────────────────────────────────────────────────────


def prontas(props: Iterable[Proposicao]) -> list[Proposicao]:
    """As que esperam: prontas para o Plenário, fora da pauta."""
    return [p for p in props if p.estado == projetos.TRAMITANDO and p.fila == projetos.F_PAUTA and p.orgao == "PLEN" and p.sigla in SIGLAS]


def _item(p: Proposicao, nota: Nota, **extra: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "t": p.titulo,
        "e": p.ementa,
        "u": p.url,
        "da": p.apresentada,
        "o": p.origem or "parlamentar",
        "n": nota.valor,
        "pt": nota.pontos,
    }
    if nota.demanda:
        d["ap"] = nota.demanda
    if nota.simbolica:
        d["simb"] = 1
    if p.urgencia:
        d["urg"] = 1
    if p.temas:
        d["tm"] = p.temas[:2]
    if p.autoria and p.origem != "parlamentar":
        d["autoria"] = p.autoria
    if p.apelido:
        d["apel"] = p.apelido
    d.update(extra)
    return d


def painel(
    esperando: Iterable[Proposicao],
    aprovadas: Iterable[Aprovada],
    regua: Regua,
    hoje: str,
    *,
    lista: int = LISTA,
    citadas: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """As duas listas lidas com a mesma régua, os pontos do gráfico e as contas do achado.
    ``citadas``: quantos textos do jornal citam cada proposição (pelo número ou apelido)."""
    rel = regua.rel
    citadas = citadas or {}

    def jornal(p: Proposicao) -> dict[str, Any]:
        return {"jor": citadas[p.titulo]} if citadas.get(p.titulo) else {}

    esperam = []
    for p in esperando:
        nota = regua.nota(p, hoje)
        idade = projetos.dias_entre(p.apresentada, hoje)
        pronta = projetos.dias_entre(p.movimento or p.apresentada, hoje)
        esperam.append((p, nota, idade, pronta))
    passaram = []
    for a in aprovadas:
        nota = regua.nota(a.prop, a.data)
        dias = projetos.dias_entre(a.prop.apresentada, a.data)
        if dias is None or dias < 0:
            continue
        passaram.append((a, nota, dias))
    fortes_paradas = [e for e in esperam if e[1].valor >= rel.alta and (e[3] or 0) > 365]  # sem andamento há mais de um ano
    fracas_rapidas = [x for x in passaram if x[1].valor < rel.baixa and x[2] < RAPIDA]
    topo = sorted(esperam, key=lambda e: (-e[1].valor, -(e[3] or 0), e[0].titulo))[:lista]
    # As aprovadas que mais pedem explicação: sinais fracos e pressa, da mais rápida; depois, as de nota mais baixa.
    fracas = sorted((x for x in passaram if x[1].valor < rel.baixa), key=lambda x: (x[2], x[1].valor, x[0].prop.titulo))
    resto = sorted((x for x in passaram if x[1].valor >= rel.baixa), key=lambda x: (x[1].valor, x[2], x[0].prop.titulo))
    baixas = (fracas + resto)[:lista]
    pontos = [[e[2], e[1].valor, 0, e[0].titulo] for e in esperam if e[2] is not None]
    pontos.sort(key=lambda q: -q[0])
    pontos = pontos[-PONTOS_MAX:] + [[x[2], x[1].valor, 1, x[0].prop.titulo] for x in passaram]

    def mediana(valores: list[int]) -> int | None:
        valores = sorted(valores)
        return valores[len(valores) // 2] if valores else None

    return {
        "esperam_n": len(esperam),
        "passaram_n": len(passaram),
        "fortes_paradas": len(fortes_paradas),
        "fracas_rapidas": len(fracas_rapidas),
        "rapidas": sum(1 for x in passaram if x[2] < RAPIDA),
        "mediana_esperam": mediana([e[1].valor for e in esperam]),
        "mediana_passaram": mediana([x[1].valor for x in passaram]),
        "sem_demanda": regua.filhos is None,
        "esperam": [_item(p, nota, pronta=p.movimento or p.apresentada, idade=idade, **jornal(p)) for p, nota, idade, _pr in topo],
        "passaram": [
            _item(a.prop, nota, aprovada=a.data, dias=dias, **({"caminho": a.caminho} if a.caminho else {}), **jornal(a.prop))
            for a, nota, dias in baixas
        ],
        "pontos": pontos,
    }
