"""O que cada parlamentar propõe e relata, e a fila de cada Casa.

Fontes: na Câmara, os arquivos em lote ``proposicoes`` e ``proposicoesAutores``
(um por ano; trazem o último andamento de cada proposição); no Senado, a API
``/processo`` (por autor e o que tramita) e ``/processo/relatoria``.

A situação oficial de cada matéria é levada a duas réguas comuns às duas Casas:

- o **estado**: tramitando, apensada (anda junto com outra), aprovada na Casa,
  virou norma, ou encerrada (arquivada, retirada, rejeitada, prejudicada);
- para o que tramita, a **fila**: de quem a matéria está esperando — a
  designação de um relator (presidente da comissão), o parecer (relator), a pauta
  (presidente da comissão ou da Casa), um despacho da Mesa, a criação de uma
  comissão ou o prazo de recurso.

"Parada" é a matéria que tramita e não tem andamento há mais de um ano
(``limiares.dias_parado``).
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .. import net, text
from . import fontes
from .fontes import Coletor
from .regras import Limiares
from .temas import TemasCamara

log = logging.getLogger(__name__)

# Tipos de proposição acompanhados: os que podem virar norma.
SIGLAS_CAMARA = ("PL", "PLP", "PEC", "PDL", "MPV", "PRC", "PLV")
SIGLAS_SENADO = ("PL", "PLP", "PEC", "PDL", "PRS", "MPV", "PLS", "PLC", "PDS", "PLN")
NOMES_SIGLA = {
    "PL": "projeto de lei",
    "PLP": "projeto de lei complementar",
    "PEC": "proposta de emenda à Constituição",
    "PDL": "projeto de decreto legislativo",
    "MPV": "medida provisória",
    "PRC": "projeto de resolução",
    "PRS": "projeto de resolução",
    "PLV": "projeto de lei de conversão",
    "PLS": "projeto de lei do Senado",
    "PLC": "projeto de lei da Câmara",
    "PDS": "projeto de decreto legislativo",
    "PLN": "projeto de lei do Congresso",
}

# As que mudam lei ou a Constituição (resoluções internas e decretos legislativos ficam fora dos destaques).
SIGLAS_DE_LEI = frozenset(("PL", "PLP", "PEC", "MPV", "PLV", "PLS", "PLC"))

TRAMITANDO, APENSADA, APROVADA, NORMA, ENCERRADA = "tramitando", "apensada", "aprovada", "norma", "encerrada"
ESTADOS = {
    TRAMITANDO: "tramitando",
    APENSADA: "anda junto com outra",
    APROVADA: "aprovada na Casa",
    NORMA: "virou norma",
    ENCERRADA: "encerrada",
}
F_RELATOR, F_PARECER, F_PAUTA, F_MESA, F_COMISSAO, F_RECURSO, F_OUTRA = (
    "relator",
    "parecer",
    "pauta",
    "mesa",
    "comissao",
    "recurso",
    "outra",
)
FILAS = {
    F_RELATOR: "esperando a designação de um relator",
    F_PARECER: "com o relator, esperando o parecer",
    F_PAUTA: "pronta, esperando entrar na pauta",
    F_MESA: "esperando despacho da Mesa",
    F_COMISSAO: "esperando a criação da comissão",
    F_RECURSO: "no prazo de recurso",
    F_OUTRA: "em outra etapa",
}
QUEM_SEGURA = {
    F_RELATOR: "o presidente da comissão",
    F_PARECER: "o relator",
    F_PAUTA: "quem faz a pauta",
    F_MESA: "o presidente da Casa",
    F_COMISSAO: "o presidente da Casa",
}
EMENTA_MAX = 260
PARALELO = 4


@dataclass
class Proposicao:
    id: int
    casa: str
    sigla: str
    numero: int
    ano: int
    ementa: str = ""
    apresentada: str = ""  # AAAA-MM-DD
    situacao: str = ""  # a descrição oficial
    estado: str = TRAMITANDO
    fila: str = ""  # só para o que tramita
    orgao: str = ""  # sigla de onde está
    movimento: str = ""  # data do último andamento
    relator: int | None = None
    urgencia: bool = False
    autores: list[int] = field(default_factory=list)  # parlamentares proponentes (ids)
    primeiro: int | None = None  # quem assina primeiro (o autor principal)
    autoria: str = ""  # texto da autoria (quando não é de parlamentar da Casa)
    temas: list[str] = field(default_factory=list)
    apelido: str = ""

    @property
    def titulo(self) -> str:
        return f"{self.sigla} {self.numero}/{self.ano}"

    @property
    def url(self) -> str:
        if self.casa == "cd":
            return f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={self.id}"
        return f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{self.id}"

    def dias_sem_andar(self, hoje: str) -> int | None:
        return dias_entre(self.movimento or self.apresentada, hoje)


@dataclass
class Relatoria:
    """Uma matéria em que o parlamentar é o relator e o parecer ainda não saiu."""

    prop: str  # "PL 123/2024"
    prop_id: int | None
    ementa: str
    desde: str  # designação (Senado) ou último andamento (Câmara)
    orgao: str = ""
    autoria: str = ""
    url: str = ""


def dias_entre(inicio: str, fim: str) -> int | None:
    try:
        return (date.fromisoformat(fim[:10]) - date.fromisoformat(inicio[:10])).days
    except (TypeError, ValueError):
        return None


def _curto(valor: Any, limite: int = EMENTA_MAX) -> str:
    return text.truncate(re.sub(r"\s+", " ", str(valor or "").strip()), limite)


# ── situação → estado e fila ─────────────────────────────────────────────────

_ENCERRADA = re.compile(r"\b(arquivad|retirad|prejudic|perdeu a eficacia|rejeitad|devolvid[oa] ao|vetad[oa] totalmente|tramitacao encerrada|tramitacao finalizada|sem eficacia|anulad|revogad|transformad[oa] em nova)")
_NORMA = re.compile(r"\b(transformad[oa] em norma|transformad[oa] n[oa] lei|promulgad|sancionad|convertid[oa] em lei|norma juridica)")
_APROVADA = re.compile(
    r"\b(aguardando apreciacao pelo senado|aguardando envio ao senado|enviad[oa] ao senado|remetid[oa] a camara|enviad[oa] a camara"
    r"|aguardando sancao|enviad[oa] a sancao|aguardando promulgacao|aguardando autografos|aguardando envio ao executivo"
    r"|aguardando retorno|aprovad[oa]$|aprovad[oa] pelo plenario|aguardando remessa|remetid[oa] a sancao|aguardando envio a redacao final"
    r"|aguardando redacao final|enviad[oa] ao congresso|aguardando apreciacao do veto)"
)
_APENSADA = re.compile(r"\b(tramitando em conjunto|apensad|aguardando apensacao|tramitacao conjunta)")
_FILAS = (
    (F_RELATOR, re.compile(r"\baguardando designacao|aguardando devolucao de relator")),
    (F_PARECER, re.compile(r"\baguardando parecer|com a relatoria|aguardando relatorio")),
    (F_RECURSO, re.compile(r"\brecurso\b")),
    (F_COMISSAO, re.compile(r"\b(criacao|constituicao|instalacao) d[ae] comissao")),
    (
        F_PAUTA,
        re.compile(
            r"\bpront[oa] para|aguardando deliberacao|aguardando inclusao|incluid[oa] (?:na|em) (?:pauta|ordem do dia)"
            r"|vista|aguardando votacao|aprovado parecer|turno suplementar"
        ),
    ),
    (F_MESA, re.compile(r"\bdespacho|chancela|encaminhamento|aguardando leitura|decisao da mesa|providencias|analise de inconstitucionalidade")),
)


def classificar(situacao: str | None, *, tramitando: bool | None = None) -> tuple[str, str]:
    """``(estado, fila)`` de uma situação oficial. ``tramitando``: o que o Senado
    informa à parte (a Câmara não informa; vale o texto)."""
    s = text.normalize(situacao or "")
    if _NORMA.search(s):
        return NORMA, ""
    if _APROVADA.search(s):
        return APROVADA, ""
    if _ENCERRADA.search(s) or tramitando is False:
        return ENCERRADA, ""
    if _APENSADA.search(s):
        return APENSADA, ""
    for fila, padrao in _FILAS:
        if padrao.search(s):
            return TRAMITANDO, fila
    return TRAMITANDO, (F_MESA if not s else F_OUTRA)


# ── Câmara ───────────────────────────────────────────────────────────────────


def camara_proposicoes(
    coletor: Coletor,
    anos: Iterable[int],
    *,
    temas: TemasCamara | None = None,
    validade_h: float = 20.0,
    validade_antigos_h: float = 24 * 8,
    ano_corrente: int | None = None,
) -> list[Proposicao]:
    """As proposições que podem virar norma, dos arquivos em lote de ``anos``.

    Os dois anos mais recentes são baixados de novo a cada dia (``validade_h``); os
    mais antigos, uma vez por semana (``validade_antigos_h``): o que está parado há
    anos não muda de um dia para o outro.
    """
    resultado: list[Proposicao] = []
    anos = sorted(set(anos))
    corrente = ano_corrente or (max(anos) if anos else 0)
    for ano in anos:
        validade = validade_h if ano >= corrente - 1 else validade_antigos_h
        try:
            do_ano = _camara_ano(coletor, ano, temas=temas, validade_h=validade)
        except Exception as exc:  # noqa: BLE001 — um ano fora do ar não derruba os outros
            log.warning("Câmara: proposições de %d indisponíveis (%s)", ano, exc)
            continue
        resultado += do_ano
    log.info("Câmara: %d proposições acompanhadas (%s a %s)", len(resultado), anos[0] if anos else "-", anos[-1] if anos else "-")
    return resultado


def _camara_ano(coletor: Coletor, ano: int, *, temas: TemasCamara | None, validade_h: float) -> list[Proposicao]:
    por_id: dict[int, Proposicao] = {}
    for r in fontes.csv_linhas(fontes.camara_arquivo(coletor, "proposicoes", ano, validade_h=validade_h)):
        sigla = (r.get("siglaTipo") or "").strip()
        pid = fontes.inteiro(r.get("id"))
        numero = fontes.inteiro(r.get("numero"))
        if sigla not in SIGLAS_CAMARA or pid is None or not numero:
            continue
        situacao = (r.get("ultimoStatus_descricaoSituacao") or "").strip()
        estado, fila = classificar(situacao)
        regime = text.normalize(r.get("ultimoStatus_regime") or "")
        por_id[pid] = Proposicao(
            id=pid,
            casa="cd",
            sigla=sigla,
            numero=numero,
            ano=fontes.inteiro(r.get("ano")) or ano,
            ementa=_curto(r.get("ementa")),
            apresentada=(r.get("dataApresentacao") or "")[:10],
            situacao=situacao,
            estado=estado,
            fila=fila,
            orgao=(r.get("ultimoStatus_siglaOrgao") or "").strip(),
            movimento=(r.get("ultimoStatus_dataHora") or "")[:10],
            relator=fontes.id_da_uri(r.get("ultimoStatus_uriRelator")),
            urgencia="urgencia" in regime,
            temas=temas(pid, ano) if temas else [],
        )
    for r in fontes.csv_linhas(fontes.camara_arquivo(coletor, "proposicoesAutores", ano, validade_h=validade_h)):
        prop = por_id.get(fontes.inteiro(r.get("idProposicao")) or -1)
        if prop is None:
            continue
        autor = fontes.inteiro(r.get("idDeputadoAutor"))
        proponente = (r.get("proponente") or "").strip() == "1"
        if autor and (r.get("ordemAssinatura") or "").strip() == "1":
            prop.primeiro = autor
        if autor and proponente:
            if autor not in prop.autores:
                prop.autores.append(autor)
        elif not autor and proponente and not prop.autoria:
            prop.autoria = _curto(r.get("nomeAutor"), 80)
    return list(por_id.values())


def camara_presidentes(coletor: Coletor, legislatura: int, hoje: str, *, validade_h: float = 24 * 3) -> dict[str, tuple[int, str]]:
    """``sigla do órgão → (id, nome)`` de quem preside cada comissão hoje (arquivo
    ``orgaosDeputados-L<legislatura>.csv``)."""
    presidentes: dict[str, tuple[int, str]] = {}
    try:
        arquivo = fontes.camara_arquivo(coletor, "orgaosDeputados", f"L{legislatura}", validade_h=validade_h)
    except Exception as exc:  # noqa: BLE001
        log.warning("Câmara: composição das comissões indisponível (%s)", exc)
        return presidentes
    for r in fontes.csv_linhas(arquivo):
        if text.normalize(r.get("cargo") or "") != "presidente":
            continue
        fim = (r.get("dataFim") or "").strip()[:10]
        inicio = (r.get("dataInicio") or "").strip()[:10]
        if (fim and fim < hoje) or (inicio and inicio > hoje):
            continue
        pid = fontes.id_da_uri(r.get("uriDeputado"))
        sigla = (r.get("siglaOrgao") or "").strip()
        if pid and sigla:
            presidentes[sigla] = (pid, (r.get("nomeDeputado") or "").strip())
    return presidentes


# ── Senado ───────────────────────────────────────────────────────────────────

_IDENT = re.compile(r"^([A-Z.]+)\s+(\d+)/(\d{4})")


def _do_senado(item: Mapping[str, Any]) -> Proposicao | None:
    match = _IDENT.match(str(item.get("identificacao") or "").strip())
    pid = fontes.inteiro(item.get("id"))
    if not match or pid is None or match.group(1) not in SIGLAS_SENADO:
        return None
    tramitando = str(item.get("tramitando") or "").strip().lower().startswith("s")
    situacao = str(item.get("situacaoAtual") or "").strip()
    estado, fila = classificar(situacao, tramitando=tramitando)
    if tramitando and estado in (ENCERRADA, APROVADA, NORMA):
        estado, fila = TRAMITANDO, F_OUTRA  # o Senado diz que tramita: vale o que ele diz
    return Proposicao(
        id=fontes.inteiro(item.get("codigoMateria")) or pid,
        casa="sf",
        sigla=match.group(1),
        numero=int(match.group(2)),
        ano=int(match.group(3)),
        ementa=_curto(item.get("ementa")),
        apresentada=str(item.get("dataApresentacao") or "")[:10],
        situacao=situacao.capitalize(),
        estado=estado,
        fila=fila,
        movimento=str(item.get("dataSituacaoAtual") or item.get("dataUltimaAtualizacao") or "")[:10],
        autoria=_curto(item.get("autoria"), 120),
        apelido=_curto(item.get("apelido"), 60),
    )


def senado_tramitando(coletor: Coletor, *, validade_h: float = 20.0) -> list[Proposicao]:
    """Tudo o que tramita no Senado e no Congresso e pode virar norma (``/processo?tramitando=S``)."""
    bruto = fontes.senado_api(coletor, "processo", validade_h=validade_h, tramitando="S")
    props = [p for p in (_do_senado(item) for item in bruto if isinstance(item, dict)) if p is not None]
    log.info("Senado: %d matérias em tramitação", len(props))
    return props


def senado_de_autoria(
    coletor: Coletor, nomes: Mapping[int, str], *, validade_h: float = 20.0
) -> dict[int, list[Proposicao]]:
    """As matérias de autoria de cada senador (``/processo?codigoParlamentarAutor=``).
    ``nomes``: código → nome parlamentar; quem abre o texto da autoria é o autor principal."""

    def do_senador(codigo: int) -> tuple[int, list[Proposicao]]:
        try:
            bruto = fontes.senado_api(coletor, "processo", validade_h=validade_h, codigoParlamentarAutor=codigo)
        except Exception as exc:  # noqa: BLE001 — um senador fora do ar não derruba os outros
            log.warning("Senado: matérias do senador %s indisponíveis (%s)", codigo, exc)
            return codigo, []
        props = []
        for item in bruto if isinstance(bruto, list) else []:
            prop = _do_senado(item) if isinstance(item, dict) else None
            if prop is not None:
                prop.autores = [codigo]
                nome = text.normalize(nomes.get(codigo) or "")
                autoria = re.sub(r"^senadora? ", "", text.normalize(prop.autoria))
                if nome and autoria.startswith(nome):
                    prop.primeiro = codigo
                props.append(prop)
        return codigo, props

    return dict(net.parallel_map(do_senador, sorted(nomes), max_workers=PARALELO))


def senado_relatorias(coletor: Coletor, *, validade_h: float = 20.0) -> dict[int, list[Relatoria]]:
    """As relatorias em aberto de cada senador (``/processo/relatoria?tramitando=S``)."""
    bruto = fontes.senado_api(coletor, "processo/relatoria", validade_h=validade_h, tramitando="S")
    por: dict[int, list[Relatoria]] = defaultdict(list)
    for item in bruto if isinstance(bruto, list) else []:
        codigo = fontes.inteiro(item.get("codigoParlamentar"))
        ident = str(item.get("identificacaoProcesso") or "").strip()
        if codigo is None or item.get("dataDestituicao") or not ident:
            continue
        if ident.split(" ")[0] not in SIGLAS_SENADO:
            continue
        materia = fontes.inteiro(item.get("codigoMateria"))
        por[codigo].append(
            Relatoria(
                prop=ident,
                prop_id=materia,
                ementa=_curto(item.get("ementaProcesso")),
                desde=str(item.get("dataDesignacao") or "")[:10],
                orgao=str(item.get("siglaColegiado") or "").strip(),
                autoria=_curto(item.get("autoriaProcesso"), 100),
                url=f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{materia}" if materia else "",
            )
        )
    return dict(por)


def camara_relatorias(props: Iterable[Proposicao]) -> dict[int, list[Relatoria]]:
    """As matérias que esperam o parecer de cada deputado (o relator do último andamento)."""
    por: dict[int, list[Relatoria]] = defaultdict(list)
    for p in props:
        if p.estado == TRAMITANDO and p.fila == F_PARECER and p.relator:
            por[p.relator].append(
                Relatoria(prop=p.titulo, prop_id=p.id, ementa=p.ementa, desde=p.movimento, orgao=p.orgao, url=p.url)
            )
    return dict(por)


# ── resumos ──────────────────────────────────────────────────────────────────


def _item(p: Proposicao, hoje: str, *, dias: bool = False) -> dict[str, Any]:
    """Uma proposição para as listas. ``dias``: também a contagem de dias sem andar (só
    nos painéis da Casa; nas fichas vai a data, que não muda todo dia)."""
    d: dict[str, Any] = {
        "t": p.titulo,
        "e": p.ementa,
        "u": p.url,
        "d": p.apresentada,
        "s": p.situacao,
        "est": p.estado,
    }
    if p.fila:
        d["f"] = p.fila
    if p.orgao:
        d["o"] = p.orgao
    if p.movimento:
        d["m"] = p.movimento
    if dias:
        parado = p.dias_sem_andar(hoje)
        if parado is not None:
            d["dias"] = parado
    if p.temas:
        d["tm"] = p.temas
    if p.apelido:
        d["ap"] = p.apelido
    if p.urgencia:
        d["urg"] = 1
    return d


def resumo_de_autoria(
    props: Iterable[Proposicao], hoje: str, lim: Limiares, *, pessoa: int | None = None, lista: int = 10
) -> dict[str, Any]:
    """O que alguém propôs: quantas, de que tipo e tema, em que estado, e as listas
    que importam (as paradas há mais tempo, as que andaram por último, as que viraram norma).

    Com ``pessoa``, contam as proposições em que ela assina primeiro (autoria
    principal); as demais entram só no número de coautorias. Uma PEC tem 171
    assinaturas: quem só apoia não é o autor."""
    todas = list(props)
    props = [p for p in todas if pessoa is None or p.primeiro == pessoa]
    coautorias = len(todas) - len(props)
    if not props:
        return {"total": 0, "coautorias": coautorias}
    por_estado = Counter(p.estado for p in props)
    tramitam = [p for p in props if p.estado == TRAMITANDO]
    paradas = sorted(
        (p for p in tramitam if (p.dias_sem_andar(hoje) or 0) > lim.dias_parado),
        key=lambda p: p.movimento or p.apresentada,
    )
    recentes = sorted(tramitam, key=lambda p: p.movimento or p.apresentada, reverse=True)
    vencedoras = sorted((p for p in props if p.estado in (NORMA, APROVADA)), key=lambda p: p.movimento, reverse=True)
    vencedoras.sort(key=lambda p: p.estado != NORMA)  # as que viraram norma primeiro; em cada grupo, da mais nova
    temas = Counter(t for p in props for t in p.temas[:1])
    return {
        "total": len(props),
        "coautorias": coautorias,
        "siglas": dict(Counter(p.sigla for p in props).most_common()),
        "estados": {e: por_estado.get(e, 0) for e in ESTADOS if por_estado.get(e)},
        "filas": dict(Counter(p.fila for p in tramitam if p.fila).most_common()),
        "temas": dict(temas.most_common()),
        "paradas_n": len(paradas),
        "paradas": [_item(p, hoje) for p in paradas[:lista]],
        "andando": [_item(p, hoje) for p in recentes[: max(4, lista // 2)]],
        "vingaram": [_item(p, hoje) for p in vencedoras[:lista]],
        "desde": min((p.apresentada for p in props if p.apresentada), default=""),
    }


def resumo_de_relatorias(relatorias: Iterable[Relatoria], hoje: str, lim: Limiares, *, lista: int = 8) -> dict[str, Any]:
    itens = sorted(relatorias, key=lambda r: r.desde or "9999")
    if not itens:
        return {"total": 0}
    dias = [d for d in (dias_entre(r.desde, hoje) for r in itens) if d is not None]
    antigas = [r for r in itens if (dias_entre(r.desde, hoje) or 0) > lim.dias_relatoria]
    return {
        "total": len(itens),
        "antigas_n": len(antigas),
        "mais_antiga_dias": max(dias) if dias else None,
        "mais_antiga": itens[0].desde,
        "lista": [{"t": r.prop, "e": r.ementa, "u": r.url, "desde": r.desde, "o": r.orgao, "a": r.autoria} for r in itens[:lista]],
    }


def _mediana(valores: list[int]) -> int | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    meio = len(ordenados) // 2
    return ordenados[meio] if len(ordenados) % 2 else (ordenados[meio - 1] + ordenados[meio]) // 2


def fila_da_casa(
    props: Iterable[Proposicao],
    hoje: str,
    lim: Limiares,
    *,
    presidentes: Mapping[str, tuple[int, str]] | None = None,
    nomes: Mapping[int, str] | None = None,
    plenario: tuple[str, ...] = ("PLEN",),
    lista: int = 15,
) -> dict[str, Any]:
    """A fila da Casa: o que tramita, esperando o quê e há quanto tempo; onde está
    represado (por órgão, com quem preside) e com quem (os relatores com mais matérias
    esperando parecer)."""
    props = list(props)
    tramitam = [p for p in props if p.estado == TRAMITANDO]
    total_props = len(tramitam)
    por_fila: dict[str, list[Proposicao]] = defaultdict(list)
    for p in tramitam:
        por_fila[p.fila or F_OUTRA].append(p)

    def bloco(itens: list[Proposicao]) -> dict[str, Any]:
        dias = [d for d in (p.dias_sem_andar(hoje) for p in itens) if d is not None]
        return {
            "n": len(itens),
            "mediana_dias": _mediana(dias),
            "parados": sum(1 for d in dias if d > lim.dias_parado),
            "quatro_anos": sum(1 for d in dias if d > 4 * 365),
        }

    filas = {fila: bloco(itens) for fila, itens in por_fila.items()}
    prontas_plenario = sorted(
        (p for p in por_fila.get(F_PAUTA, []) if p.orgao in plenario and p.sigla in SIGLAS_DE_LEI),
        key=lambda p: p.movimento or p.apresentada,
    )
    orgaos = []
    por_orgao: dict[str, dict[str, list[Proposicao]]] = defaultdict(lambda: defaultdict(list))
    for fila in (F_RELATOR, F_PAUTA, F_PARECER):
        for p in por_fila.get(fila, []):
            if p.orgao and p.orgao not in plenario:
                por_orgao[p.orgao][fila].append(p)
    for sigla, grupos in por_orgao.items():
        sem_relator, pauta = grupos.get(F_RELATOR, []), grupos.get(F_PAUTA, [])
        represadas = sem_relator + pauta
        if not represadas:
            continue
        dias = [d for d in (p.dias_sem_andar(hoje) for p in represadas) if d is not None]
        presidente = (presidentes or {}).get(sigla)
        orgaos.append(
            {
                "orgao": sigla,
                "sem_relator": len(sem_relator),
                "pauta": len(pauta),
                "com_relator": len(grupos.get(F_PARECER, [])),
                "mediana_dias": _mediana(dias),
                "mais_antiga_dias": max(dias) if dias else None,
                "presidente": {"id": presidente[0], "nome": presidente[1]} if presidente else None,
            }
        )
    orgaos.sort(key=lambda o: -(o["sem_relator"] + o["pauta"]))
    relatores: dict[int, list[Proposicao]] = defaultdict(list)
    for p in por_fila.get(F_PARECER, []):
        if p.relator:
            relatores[p.relator].append(p)
    sentados = []
    for pid, itens in relatores.items():
        dias = [d for d in (p.dias_sem_andar(hoje) for p in itens) if d is not None]
        antigas = sum(1 for d in dias if d > lim.dias_relatoria)
        sentados.append(
            {"id": pid, "nome": (nomes or {}).get(pid, ""), "n": len(itens), "antigas": antigas, "mais_antiga_dias": max(dias) if dias else None}
        )
    sentados.sort(key=lambda s: (-s["antigas"], -s["n"]))
    return {
        "tramitando": total_props,
        "apensadas": sum(1 for p in props if p.estado == APENSADA),
        "filas": filas,
        "prontas_plenario_n": len(prontas_plenario),
        "prontas_plenario": [_item(p, hoje, dias=True) for p in prontas_plenario[:lista]],
        "orgaos": orgaos[:lista],
        "sentados": sentados[:lista],
        "temas": dict(Counter(t for p in tramitam for t in p.temas[:1]).most_common()),
    }
