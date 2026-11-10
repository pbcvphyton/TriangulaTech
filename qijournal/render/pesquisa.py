"""Pesquisa analítica (``edicoes/pesquisa.html``): busca em todas as edições.

Cada edição ganha um arquivo de busca compacto, ``data/busca/AAAA-MM-DD.json``
(:func:`dados_do_dia`), com tudo o que ela publicou:

- as **matérias** (título, linha fina, texto, "por que importa" e veículos);
- **todas as notícias** do dia (título, veículo, link, horário e assunto);
- a **cobertura comparada** (assunto, conclusão, lados e o foco de cada veículo),
  como na seção da edição: as matérias com análise da cobertura e os demais
  assuntos comparados, cada assunto uma vez;
- as **análises do Termômetro** feitas no dia (T, rótulo, qualidade e resumo).

O ``publish`` grava o arquivo do dia e cria os que faltam a partir de
``data/AAAA-MM-DD.json`` (edições anteriores à pesquisa), assim como refaz os
gravados em outro formato (:data:`VERSAO`); eles expiram com as edições. A
página traz só o índice das edições (:func:`resumo_do_dia`) e os
filtros; o navegador carrega os arquivos dos dias do período pedido, busca neles
(sem acento e sem diferenciar maiúsculas) e mostra os resultados, com links para
a matéria na edição ou para a notícia original, e as contagens por dia, veículo e
editoria.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .. import text
from ..config import Config
from ..models import Edition
from ..termometro.referencia import PARAMETROS
from . import filters, midia, tom
from .midia import DestaquesView
from .termometro import ANALITICO_PAGINA, CLASSE, analises_validas
from .web import DEFAULT_COLORS, brand_view, compared_topics, coverage_view

log = logging.getLogger(__name__)

PAGINA = "pesquisa.html"
PASTA = "busca"  # data/busca/AAAA-MM-DD.json
# Formato do arquivo de busca: um arquivo gravado com outro número é refeito no publish a
# partir de data/AAAA-MM-DD.json (ler_dia o recusa). 2: a cobertura das matérias da edição
# entra em "comparadas" (antes, só a dos demais assuntos).
VERSAO = 2

TIPOS = (
    ("m", "Matérias"),
    ("n", "Todas as notícias"),
    ("c", "Cobertura comparada"),
    ("a", "Termômetro"),
)


def caminho_do_dia(data_dir: Path, data: str) -> Path:
    return Path(data_dir) / PASTA / f"{data}.json"


def serializar(dados: Mapping[str, Any]) -> str:
    """JSON compacto (o arquivo vai inteiro para o navegador)."""
    return json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + "\n"


# ── arquivo de busca de uma edição ───────────────────────────────────────────


class _Tabela:
    """Lista de valores repetidos (veículos, seções), referenciados pelo índice."""

    def __init__(self) -> None:
        self.itens: list[Any] = []
        self._pos: dict[str, int] = {}

    def __call__(self, chave: str, valor: Any = None) -> int:
        if chave not in self._pos:
            self._pos[chave] = len(self.itens)
            self.itens.append(chave if valor is None else valor)
        return self._pos[chave]


def _hora(iso: str | None, tz: str, dia: date) -> int | None:
    """Minutos desde a meia-noite do dia da edição, no fuso do jornal (negativo no
    dia anterior); ``None`` sem data válida. A página soma isso à data da edição."""
    moment = filters.parse_iso(iso)
    if moment is None:
        return None
    local = moment.astimezone(filters._zone(tz))
    return (local.date() - dia).days * 1440 + local.hour * 60 + local.minute


def _url(url: str | None) -> str:
    """Link http(s) validado, sem o ``https://`` (a página o devolve)."""
    seguro = filters.safe_url(url) or ""
    return seguro[8:] if seguro.startswith("https://") else seguro


def _limpo(value: Any) -> str:
    return " ".join(filters.plain(str(value or "")).split())


def dados_do_dia(edition: Edition, config: Config, termometro: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """O arquivo de busca de uma edição (``termometro``: o registro do dia em
    ``data/termometro/``, com as análises feitas nele).

    Formato (compacto: o arquivo vai inteiro para o navegador):

    - ``secoes``: ``[id, título, cor]``; ``veiculos``: nomes. Os demais campos
      apontam para essas listas pelo índice.
    - ``materias``: ``{id, s (seção), t (título), l (linha fina), c (texto),
      p (por que importa), f (veículos), h, m (1 = manchete)}``.
    - ``assuntos``: ``[título, id da matéria ou "", 1 se comparado]``, só dos
      assuntos que dizem algo além do título da notícia;
      ``noticias``: ``[seção, assunto (-1: nenhum), veículo, título, link, h]``.
    - ``comparadas``: ``{s, t (assunto), c (conclusão), a/b (lados), p (rótulo do
      medidor), k (lado), h, u (link), f: [[veículo, lado, foco, link]…], m}``: as
      matérias com análise da cobertura (``m``: id da matéria, só nelas; ``t`` é o
      título dela) e depois os demais assuntos, cada assunto uma vez
      (:func:`qijournal.render.web.compared_topics`).
    - ``analises`` (Termômetro): ``{id, s, f, t, u, e (editoria), d (publicação),
      T, r (rótulo), q (qualidade), x (selo), z (resumo), o (origem)}``.

    ``h``: minutos desde a meia-noite do dia da edição (negativo no dia anterior;
    ``null`` sem horário). Links sem o ``https://``.
    """
    tz = config.site.timezone
    dia = date.fromisoformat(edition.date)
    padrao = filters.safe_color(config.brand.colors.get("primary"), DEFAULT_COLORS["primary"])
    titulos = {s.id: (s.title, filters.safe_color(s.color, padrao)) for s in config.sections}
    for sec in edition.sections:
        anterior = titulos.get(sec.id, (sec.id, padrao))
        titulos[sec.id] = (sec.title or anterior[0], filters.safe_color(sec.color, anterior[1]))
    secoes = _Tabela()
    veiculos = _Tabela()

    def secao(sid: str | None) -> int:
        sid = sid or ""
        titulo, cor = titulos.get(sid, (sid or "Outras", padrao))
        return secoes(sid, [sid, filters.plain(titulo), cor])

    def veiculo(nome: Any) -> int | None:
        nome = _limpo(nome)
        return veiculos(nome) if nome else None

    materias = []
    for story in edition.stories:
        titulo = _limpo(story.headline)
        if not titulo:
            continue
        fontes = [i for i in dict.fromkeys(veiculo(s.name) for s in story.sources) if i is not None]
        item: dict[str, Any] = {
            "id": story.id,
            "s": secao(story.section),
            "t": titulo,
            "l": _limpo(story.dek),
            "c": "\n".join(p for p in (_limpo(par) for par in story.body) if p),
            "p": _limpo(story.why_it_matters),
            "f": fontes,
            "h": _hora(story.published, tz, dia),
        }
        if story.id == edition.lead:
            item["m"] = 1
        materias.append(item)

    assuntos: list[list[Any]] = []
    noticias: list[list[Any]] = []
    for topic in edition.index:
        itens = []
        for news in topic.items:
            titulo = _limpo(news.title)
            url = _url(news.url)
            fonte = veiculo(news.source)
            if titulo and url and fonte is not None:
                itens.append([fonte, titulo, url, _hora(news.published, tz, dia)])
        if not itens:
            continue
        titulo = _limpo(topic.title)
        a = -1
        if len(itens) > 1 or topic.story_id or topic.compared or (titulo and titulo != itens[0][1]):
            a = len(assuntos)
            assuntos.append([titulo or itens[0][1], topic.story_id or "", 1 if topic.compared else 0])
        s = secao(topic.section)
        noticias += [[s, a, *item] for item in itens]

    # Cobertura comparada: a lista da seção da edição e da lente do enquadramento
    # (as matérias com análise da cobertura, depois os demais assuntos; cada assunto uma vez).
    comparadas = []
    for coverage, story in compared_topics(edition):
        view = coverage_view(coverage)
        if view is None or not view.topic:
            continue
        outlets = []
        for lado, lista in (("a", view.a), ("b", view.b), ("neutro", view.neutral)):
            for o in lista:
                fonte = veiculo(o.name)
                if fonte is not None:
                    outlets.append([fonte, lado, o.framing, _url(o.url)])
        if story is not None:  # matéria da edição: a editoria, o título e o horário dela
            sid, titulo = story.section, _limpo(story.headline) or view.topic
            quando = story.published or coverage.published
        else:
            sid, titulo, quando = coverage.section, view.topic, coverage.published
        item = {
            "s": secao(sid),
            "t": titulo,
            "c": view.conclusion,
            "a": view.side_a,
            "b": view.side_b,
            "p": view.lean_label,
            "k": view.lean,
            "h": _hora(quando, tz, dia),
            "u": _url(view.url),
            "f": outlets,
        }
        if story is not None:
            item["m"] = story.id  # o link vai à matéria (#s-<id>), que traz a análise da cobertura
        comparadas.append(item)

    analises = []
    for a in analises_validas(termometro):
        if a.get("versao_calculo") != PARAMETROS["versao"]:
            continue
        qualidade = a.get("qualidade_apuracao") or {}
        analises.append(
            {
                "id": str(a["id"]),
                "s": secao(a.get("secao")),
                "f": veiculo(a.get("veiculo")),
                "t": _limpo(a.get("titulo")),
                "u": _url(a.get("url")),
                "e": _limpo(a.get("editoria")),
                "d": str(a.get("data_publicacao") or "")[:10],
                "T": a.get("T") if a.get("estado") == "ok" else None,
                "r": _limpo(a.get("rotulo")),
                "q": qualidade.get("Q"),
                "x": _limpo(a.get("selo")),
                "z": _limpo(a.get("resumo")),
                "o": _limpo(a.get("origem")) or "materia",
            }
        )

    return {
        "v": VERSAO,
        "data": edition.date,
        "rotulo": edition.date_label,
        "modo": edition.mode,
        "todas": bool(edition.index),
        "secoes": secoes.itens,
        "veiculos": veiculos.itens,
        "materias": materias,
        "assuntos": assuntos,
        "noticias": noticias,
        "comparadas": comparadas,
        "analises": analises,
    }


def ler_dia(caminho: Path) -> dict[str, Any] | None:
    """Arquivo de busca gravado (``None`` se ausente, ilegível ou de outra versão)."""
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(dados, dict) or dados.get("v") != VERSAO:
        return None
    return dados


# ── índice das edições (vai na página) ───────────────────────────────────────


def resumo_do_dia(dados: Mapping[str, Any]) -> dict[str, Any]:
    """O que a página precisa saber de um dia antes de carregar o arquivo dele:
    data, rótulo, contagens por tipo, veículos (com quantas notícias) e seções."""
    noticias = dados.get("noticias") or []
    veiculos = dados.get("veiculos") or []
    por_veiculo: Counter[str] = Counter()
    for item in noticias:
        if 0 <= item[2] < len(veiculos):
            por_veiculo[veiculos[item[2]]] += 1
    for grupo in ("materias", "analises"):
        for item in dados.get(grupo) or []:
            for i in item["f"] if isinstance(item.get("f"), list) else [item.get("f")]:
                if isinstance(i, int) and 0 <= i < len(veiculos):
                    por_veiculo.setdefault(veiculos[i], 0)
    for item in dados.get("comparadas") or []:
        for o in item.get("f") or []:
            if isinstance(o[0], int) and 0 <= o[0] < len(veiculos):
                por_veiculo.setdefault(veiculos[o[0]], 0)
    return {
        "d": dados.get("data"),
        "l": dados.get("rotulo") or "",
        "t": bool(dados.get("todas")),
        "n": [len(dados.get(k) or []) for k in ("materias", "noticias", "comparadas", "analises")],
        "veiculos": dict(por_veiculo),
        "secoes": dados.get("secoes") or [],
    }


@dataclass
class PesquisaView:
    edicoes: int
    periodo: str
    intro: str
    primeira: str  # AAAA-MM-DD
    ultima: str
    secoes: list[tuple[str, str]]  # (id, título), na ordem do jornal
    veiculos: list[str]
    tipos: list[tuple[str, str]]
    manifesto: str  # JSON do índice, seguro dentro de <script>
    termometro: bool = False
    totais: dict[str, int] = field(default_factory=dict)
    atualizado: str = ""
    destaques: DestaquesView | None = None  # nomes em destaque (sugestões de busca)


def _data_label(iso: str) -> str:
    try:
        return text.pt_short_date(date.fromisoformat(iso[:10]))
    except ValueError:
        return iso


def pesquisa_view(
    resumos: Iterable[Mapping[str, Any]],
    config: Config,
    *,
    edition_date: str | None = None,
    destaques: DestaquesView | None = None,
) -> PesquisaView:
    dias = sorted((r for r in resumos if r.get("d")), key=lambda r: str(r["d"]), reverse=True)
    ordem = {s.id: i for i, s in enumerate(config.sections)}
    secoes: dict[str, tuple[str, str]] = {}
    veiculos: Counter[str] = Counter()
    totais = [0, 0, 0, 0]
    for dia in reversed(dias):  # o título mais recente de cada seção vale
        for sid, titulo, cor in dia.get("secoes") or []:
            if sid:  # sem seção (id vazio): fora do filtro, que já tem "Todas" com valor vazio
                secoes[sid] = (titulo, cor)
        for nome, n in (dia.get("veiculos") or {}).items():
            veiculos[nome] += n
        totais = [a + b for a, b in zip(totais, dia.get("n") or [0, 0, 0, 0], strict=False)]
    lista_secoes = sorted(secoes.items(), key=lambda kv: (ordem.get(kv[0], len(ordem)), kv[1][0]))
    termometro = config.termometro_ativo or totais[3] > 0
    tipos = [t for t in TIPOS if t[0] != "a" or termometro]
    manifesto = {
        "v": VERSAO,
        "base": f"../data/{PASTA}/",
        "analitico": ANALITICO_PAGINA if termometro else "",
        "dias": [{"d": r["d"], "l": r.get("l") or "", "t": bool(r.get("t")), "n": r.get("n") or [0, 0, 0, 0]} for r in dias],
        "secoes": [[sid, titulo, cor] for sid, (titulo, cor) in lista_secoes],
        "tipos": [list(t) for t in tipos],
        "classes": CLASSE,  # rótulo do termômetro → cor (t-ef, t-eq…)
        "faixas": list(PARAMETROS["faixas"]),
        # Como a mídia cobre (o analítico da busca): veículos de fora do Brasil e as
        # palavras e nomes que não contam como nome (as mesmas regras de midia.py).
        "exterior": midia.exterior(config),
        "fora": sorted(midia.FORA),
        "calendario": sorted(midia.CALENDARIO),
        "foraNomes": sorted(midia.FORA_NOMES),
        # Tom das manchetes: o dicionário e os limites de qijournal/render/tom.py e midia.py.
        "tom": tom.dicionario(),
        "tomP": {
            "min": midia.MIN_TOM,
            "minV": midia.MIN_TOM_VEICULO,
            "z": midia.Z_VEICULO,
            "zT": midia.Z_TENDENCIA,
            "minSem": midia.MIN_TOM_SEMANA,
            "semV": midia.SEMANA_VEICULO,
            "semD": midia.SEMANA_DEMAIS,
            "padS": midia.PADRAO_SEMANAS,
            "padF": midia.PADRAO_FRACAO,
            "barra": midia.BARRA_TOM,
        },
    }
    if dias:
        primeira, ultima = str(dias[-1]["d"]), str(dias[0]["d"])
        periodo = _data_label(primeira) if primeira == ultima else f"{_data_label(primeira)} a {_data_label(ultima)}"
        partes = [
            f"{text.format_number_pt(totais[1], 0)} notícias coletadas",
            f"{text.format_number_pt(totais[0], 0)} matérias",
            f"{text.format_number_pt(totais[2], 0)} assuntos da cobertura comparada",
        ]
        if termometro:
            partes.append(f"{text.format_number_pt(totais[3], 0)} análises do Termômetro")
        quantas = f"todas as {text.format_number_pt(len(dias), 0)} edições" if len(dias) > 1 else "1 edição"
        intro = (
            f"Busca em {quantas} do arquivo, de {periodo}: "
            + ", ".join(partes[:-1])
            + f" e {partes[-1]}. Cada resultado leva à matéria na edição ou à notícia original."
        )
    else:
        primeira = ultima = periodo = ""
        intro = "Nenhuma edição no arquivo ainda."
    return PesquisaView(
        edicoes=len(dias),
        periodo=periodo,
        intro=intro,
        primeira=primeira,
        ultima=ultima,
        secoes=[(sid, titulo) for sid, (titulo, _cor) in lista_secoes],
        veiculos=sorted(veiculos, key=lambda nome: (text.normalize(nome), nome)),
        tipos=tipos,
        manifesto=json.dumps(manifesto, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c"),
        termometro=termometro,
        totais=dict(zip("mnca", totais, strict=True)),
        atualizado=_data_label(edition_date) if edition_date else "",
        destaques=destaques if destaques is not None and destaques.nomes else None,
    )


def render_pesquisa_page(
    resumos: Iterable[Mapping[str, Any]],
    config: Config,
    *,
    home_href: str,
    archive_href: str,
    edition_date: str | None = None,
    destaques: DestaquesView | None = None,
) -> str:
    """Página ``edicoes/pesquisa.html`` (autocontida, como as demais; os dados de
    cada edição vêm de ``data/busca/``). ``destaques``: nomes sugeridos para buscar."""
    view = pesquisa_view(resumos, config, edition_date=edition_date, destaques=destaques)
    brand = brand_view(config)
    template = filters.environment().get_template("pesquisa.html.j2")
    html = template.render(
        p=view,
        brand=brand,
        home_href=filters.safe_href(home_href) or "../",
        archive_href=filters.safe_href(archive_href) or "./",
        base_url=config.site.base_url,
        repo_url=filters.safe_url(config.site.repo_url) or "",
    )
    log.info("Pesquisa analítica renderizada: %d edição(ões), %d KB", view.edicoes, len(html.encode("utf-8")) // 1024)
    return html
