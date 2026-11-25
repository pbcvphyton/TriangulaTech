"""Página HTML da edição (autocontida) e índice do arquivo de edições.

O modelo ``Edition`` é convertido primeiro numa *view* (:class:`EditionView`)
com todos os textos já limpos (sem markdown em títulos), URLs validadas e
horários formatados; os templates só apresentam. A mesma view é usada pelo
e-mail (``render/email.py``).

A home tem três andares, cada um com teto (as constantes ``TETO_*`` abaixo): o
que aconteceu (a manchete, sem corpo, e as chamadas de "Em 1 minuto"), o que vem
aí (a agenda da semana, que entra entre os dois) e como a mídia cobriu (a linha
do viés, "Onde os veículos divergiram" e "Mais recentes"). O que é detalhe mora
nas janelas das matérias e nas gavetas da cópia arquivada
(``edicoes/AAAA-MM-DD.html``: ``#cobertura``, ``#termometro`` e ``#racional``),
que só essa cópia renderiza; a home aponta para elas (``EditionView.copia_href``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from markupsafe import Markup

from .. import text
from ..collect.market import format_change
from ..config import Config
from ..agenda.retrato import ler_retrato
from ..models import Coverage, Edition, Quote, Rationale, Story
from . import agenda as agenda_tela
from . import filters
from .termometro import ANALITICO_PAGINA, TermometroDiaView, ViesDoDiaView, termometro_dia_view, vies_do_dia

log = logging.getLogger(__name__)

DEFAULT_COLORS = {
    "primary": "#394A7A",
    "navy": "#1B2745",
    "accent": "#FF5A1F",
    "alert": "#FF5A1F",
    "ticker_up": "#7FC8FF",
    "ticker_down": "#FF5C9A",
}
DEFAULT_ON_MASTHEAD = "#FFFFFF"
# Tetos da home: o gerador corta por eles, a edição nunca passa (tests/test_home_orcamento.py).
TETO_CHAMADAS = 5  # chamadas de "Em 1 minuto": as secundárias e os primeiros destaques
TETO_DIVERGENCIAS = 3  # assuntos em "Onde os veículos divergiram"
TETO_RECENTES = 5  # itens de "Mais recentes"
RECENTES_NO_CELULAR = 3  # quantos deles aparecem no celular (os demais, só em tela larga)
CHAMADAS_SECUNDARIAS = 3  # secundárias que abrem "Em 1 minuto"; os destaques completam o teto
DIVERGENCIA_MIN_VEICULOS = 3  # assunto com os dois lados só entra na home com 3 veículos ou mais
DIVERGE_MIN_LADO_MENOR = 2  # lado menor com 2 veículos ou mais: "veículos divergem"; com 1, "destoou"
TITULO_CURTO = 60  # manchete até este comprimento: letra maior
TITULO_MEDIO = 85  # até este: letra média; acima, a menor (o título nunca é cortado)
DESCRIPTION_MAX = 200
ORPHAN_SECTION = ("outras", "Outras notícias")


@dataclass(frozen=True)
class SourceLink:
    name: str
    url: str | None  # None quando a URL original não é http(s)


@dataclass(frozen=True)
class OutletView:
    name: str
    url: str | None
    framing: str


@dataclass
class CoverageView:
    """Cobertura comparada pronta para exibição: medidor, lado predominante e veículos por lado."""

    topic: str
    conclusion: str
    side_a: str
    side_b: str
    a: list[OutletView]
    b: list[OutletView]
    neutral: list[OutletView]
    pct_a: int  # larguras do medidor (somam 100)
    pct_n: int
    pct_b: int
    lean: str  # "a" | "b" | "equilibrio" | "convergente"
    lean_label: str  # "Pende para: Destaca o alívio · 3 de 5 veículos"
    aria: str  # descrição do medidor para leitores de tela
    section_title: str = ""
    color: str = ""
    url: str | None = None  # artigo principal (assuntos fora da edição)
    time: str = ""

    @property
    def total(self) -> int:
        return len(self.a) + len(self.b) + len(self.neutral)


@dataclass
class StoryView:
    """Matéria pronta para exibição (textos limpos, URLs seguras, horários locais)."""

    id: str
    anchor: str  # id do modal / âncora: "s-<id>"
    section_title: str
    color: str
    headline: str
    dek: str
    body: list[str]  # pode conter **negrito** (renderizar com md_lite)
    why: str
    image: str | None
    sources: list[SourceLink]
    time: str  # "05:07", "ontem, 21:10" ou "27/09, 18:30" (fuso do site, relativo à edição)
    age: str  # "há 3 h" (vazio nas cópias do arquivo, em que ficaria congelado)
    published: str | None
    lang: str | None = None  # idioma do texto quando não é português (atributo lang)
    coverage: CoverageView | None = None
    compiled: str = ""  # "Compilada de 7 notícias de 5 veículos · bloco Economia & Mercados"
    marca: str = ""  # assunto com veículos dos dois lados: "veículos divergem" ou "1 veículo destoou"
    congresso: list[dict[str, Any]] = field(default_factory=list)  # parlamentares citados (fichas curtas)

    @property
    def source_names(self) -> str:
        return " · ".join(s.name for s in self.sources)

    @property
    def dek_in_body(self) -> bool:
        """O 1º parágrafo já começa com a linha fina (a edição automática corta a
        primeira frase longa na linha fina e a mantém inteira no corpo)? O modal
        então mostra só o corpo, sem repetir o texto."""
        dek = text.normalize(self.dek)
        return bool(dek and self.body and text.normalize(filters.plain(self.body[0])).startswith(dek))


@dataclass
class RecenteView:
    """Linha de "Mais recentes": notícia do dia (link externo) ou matéria da edição (âncora)."""

    time: str
    source: str
    title: str
    href: str
    external: bool
    lang: str | None = None
    so_tela_larga: bool = False  # passa de RECENTES_NO_CELULAR: some no celular


@dataclass
class ServicoView:
    """Um trecho da linha de serviço de uma chamada (a linha pequena sob o título).

    ``tipo`` diz ao modelo como desenhar: "veiculos" é texto simples; "idade" e "hora"
    viram ``<time>`` (a idade é recalculada no navegador); "diverge" leva a marca de
    divergência e o link do item em "Onde os veículos divergiram". Qualquer outro tipo
    sai como texto (ou link, com ``href``): é por onde a agenda da semana acrescenta o
    seu trecho ("na agenda: qua 25")."""

    texto: str
    tipo: str = ""
    href: str = ""
    quando: str = ""  # instante ISO da publicação (tipos "idade" e "hora")


@dataclass
class ChamadaView:
    """Uma chamada do andar das notícias: a manchete ou um item de "Em 1 minuto"."""

    story: StoryView
    servico: list[ServicoView] = field(default_factory=list)
    tamanho: str = ""  # só na manchete: classe de letra do título ("curto" | "medio" | "longo")


@dataclass
class LadoView:
    """Uma linha de um assunto em que os veículos divergiram: a conta e o lado em palavras."""

    conta: str  # "3 veículos", "1 veículo destoou", "11 sem lado"
    texto: str = ""  # o lado, como a análise o escreveu (vazio em "sem lado")


@dataclass
class DivergenciaView:
    """Um assunto de "Onde os veículos divergiram": o título e os dois lados em palavras."""

    anchor: str  # id do item na home ("dv-1"), alvo de "veículos divergem" da chamada
    titulo: str
    href: str  # "#s-<id>" (abre a matéria da edição) ou a cobertura completa da cópia arquivada
    lados: list[LadoView]
    marca: str  # "veículos divergem" | "1 veículo destoou" | "1 veículo de cada lado"
    story_id: str = ""


@dataclass
class MidiaView:
    """Bloco "Como a mídia cobriu": a linha do viés e as duas colunas.

    Cada parte some sozinha quando a edição não a tem (edição automática, sem
    cobertura comparada; marca sem termômetro), sem deixar coluna ou título vazio."""

    vies: ViesDoDiaView | None  # só a marca com termômetro, no dia em que ele rodou
    comparados: int  # assuntos com cobertura comparada (0: a coluna das divergências não aparece)
    divergencias: list[DivergenciaView]
    recentes: list[RecenteView]
    ate: str  # hora da edição no título de "Mais recentes": "09h28"

    @property
    def colunas(self) -> int:
        return (1 if self.comparados else 0) + (1 if self.recentes else 0)


@dataclass
class SectionView:
    id: str
    title: str
    color: str
    stories: list[StoryView]


@dataclass
class BlockView:
    """Um bloco da compilação por editoria, no racional da edição."""

    name: str
    articles: int
    stories: int
    compared: int
    ok: bool


@dataclass
class RationaleView:
    """Racional da compilação: resumo, números e blocos por editoria."""

    summary: str  # parágrafo em texto puro
    linha: str  # linha de método do rodapé: "Compilada de 1.146 notícias de 42 veículos"
    articles: int
    outlets: int
    topics: int
    method: str
    blocks: list[BlockView] = field(default_factory=list)


@dataclass
class IndexItemView:
    source: str
    title: str
    url: str | None
    time: str
    lang: str | None = None


@dataclass
class IndexTopicView:
    title: str
    items: list[IndexItemView]
    href: str | None = None  # âncora da matéria da edição
    compared: bool = False


@dataclass
class IndexSectionView:
    id: str
    title: str
    color: str
    topics: list[IndexTopicView]

    @property
    def articles(self) -> int:
        return sum(len(t.items) for t in self.topics)


@dataclass
class QuoteView:
    label: str
    display: str
    change: str | None  # "+0,19%" ou None para indicadores
    css: str  # u / d / f / ""
    note: str  # dica (fonte e data de referência)


@dataclass
class WeatherView:
    city: str
    now_emoji: str
    now_desc: str
    now_temp: str
    today_min: str
    today_max: str
    tomorrow_emoji: str
    tomorrow_desc: str
    tomorrow_min: str
    tomorrow_max: str


@dataclass
class SourceGroup:
    """Situação de um veículo (que pode ter vários feeds) na coleta do dia."""

    name: str
    feeds: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.failed:
            return "ok"
        return "erro" if self.failed >= self.feeds else "parcial"


@dataclass(frozen=True)
class EmailLogo:
    src: str  # URL absoluta (http/https)
    width: int
    height: int


@dataclass
class BrandView:
    name: str
    wordmark: list[tuple[str, str]]  # (texto, classe css)
    tagline: str
    logo_svg: Markup | None  # SVG do repositório (confiável), embutido como está
    favicon_uri: str
    colors: dict[str, str]
    rgb: dict[str, str]
    # Cabeçalho em bloco de cor (None = cabeçalho no papel, com o logo na cor original).
    masthead: str | None = None
    on_masthead: str = DEFAULT_ON_MASTHEAD  # logo e textos sobre o bloco
    # Logo pintado com currentColor: segue a cor do cabeçalho em vez do filtro de inversão do modo escuro.
    logo_mono: bool = False
    logo_svg_dark: Markup | None = None  # versão própria para o modo escuro (ids prefixados)
    logo_height: int | None = None  # altura do logo no cabeçalho (px, desktop)
    logo_height_mobile: int | None = None  # no celular (px)
    email_logo: EmailLogo | None = None
    email_logo_dark: EmailLogo | None = None  # só com email_logo
    email_header: EmailLogo | None = None  # faixa do topo do e-mail (modelo do TSA Tech)
    termometro: bool = False  # marca com Termômetro de Viés (estilos do bloco do dia e da aba Analítico Político)
    congresso: bool = False  # aba Congresso publicada: botão no cabeçalho e fichas nas matérias
    tribunais: bool = False  # aba Tribunais publicada (ligada para a marca e com a página gravada): botão no cabeçalho


@dataclass
class CompareItemView:
    """Um assunto no mapa da cobertura comparada (matéria da edição ou demais assuntos)."""

    c: CoverageView
    section: str  # id da editoria (filtro por categoria)
    divergence: float | None  # 0 a 1: 2 × lado menor ÷ (A + B); None sem dois lados
    story_href: str = ""  # matéria da edição: âncora que abre a matéria
    story_title: str = ""

    @property
    def divergence_pct(self) -> int:
        return round((self.divergence or 0) * 100)


@dataclass
class CompareCategoryView:
    """Uma aba da cobertura comparada: os assuntos de uma editoria."""

    id: str
    title: str
    color: str
    total: int
    divided: int
    items: list[CompareItemView] = field(default_factory=list)  # sem divergência, na editoria
    divergentes: list[CompareItemView] = field(default_factory=list)  # com dois lados, mais disputados primeiro


@dataclass
class ComparedMapView:
    """Cobertura comparada organizada para escolher o que ler: uma aba por editoria
    (na ordem do jornal) e "Todas"; em cada aba, primeiro onde a mídia divergiu (os
    mais disputados antes), depois o que os veículos relataram de forma semelhante."""

    divided: list[CompareItemView]
    categories: list[CompareCategoryView]
    total: int
    stories: int  # matérias da edição no mapa

    @property
    def consensus(self) -> int:
        return self.total - len(self.divided)


@dataclass
class EditionView:
    brand: BrandView
    date_label: str
    time_label: str
    tz_label: str
    mode_label: str
    editorial: str
    briefing: list[str]
    lead: StoryView | None
    secondary: list[StoryView]
    highlights: list[StoryView]
    sections: list[SectionView]
    stories: list[StoryView]
    quotes: list[QuoteView]
    clima: WeatherView | None  # a primeira cidade da configuração, último item da linha de indicadores
    sources_total: int
    sources_ok: int
    source_groups: list[SourceGroup]
    base_url: str
    repo_url: str
    edition_url: str
    description: str
    og_image: str | None
    mode: str = "heuristic"  # "ai" | "heuristic"
    compared: list[CoverageView] = field(default_factory=list)  # demais assuntos com cobertura comparada
    compared_map: ComparedMapView | None = None  # todos os assuntos comparados (matérias + demais), por categoria
    rationale: RationaleView | None = None  # racional da compilação (vazio em edições antigas)
    index: list[IndexSectionView] = field(default_factory=list)  # todas as notícias do dia, por seção
    instagram_url: str = ""  # perfil do Instagram (config instagram.handle), no rodapé
    index_url: str = ""  # página com todas as notícias do dia (edicoes/AAAA-MM-DD-todas.html)
    # Termômetro do dia (marca com termômetro, quando ele rodou na edição): o painel da
    # gaveta da cópia arquivada; o link da aba "Analítico Político" sai em
    # termometro.analitico_href. A home leva só o resumo dele (midia.vies).
    termometro: TermometroDiaView | None = None
    # Andar das notícias: a manchete e as chamadas de "Em 1 minuto" (cada matéria uma vez).
    manchete: ChamadaView | None = None
    chamadas: list[ChamadaView] = field(default_factory=list)
    # "Como a mídia cobriu" (None quando a edição não tem nada para o bloco).
    midia: MidiaView | None = None
    # "Agenda da semana" (None sem retrato na edição, com a agenda desligada ou com erro).
    agenda: agenda_tela.AgendaView | None = None
    # Cópia arquivada (edicoes/AAAA-MM-DD.html): é ela que leva as gavetas de detalhe.
    arquivada: bool = False
    # Endereço da cópia arquivada visto da página ("" na própria cópia): os links de
    # detalhe da home são copia_href + "#cobertura", "#termometro" e "#racional".
    copia_href: str = ""

    @property
    def index_total(self) -> int:
        return sum(section.articles for section in self.index)


# ── marca ────────────────────────────────────────────────────────────────────


def brand_view(config: Config) -> BrandView:
    """Cores validadas, wordmark em partes e favicon como data URI."""
    brand = config.brand
    colors = {k: filters.safe_color(brand.colors.get(k), v) for k, v in DEFAULT_COLORS.items()}
    classes = ("q", "i", "journal")
    wordmark = [(part, classes[min(i, 2)]) for i, part in enumerate(brand.wordmark or []) if part]
    if not wordmark:
        wordmark = [(brand.name, "journal")]
    favicon_svg = brand.favicon_svg or filters.initial_favicon(
        wordmark[0][0].strip() or brand.name, colors["navy"], colors["accent"]
    )
    masthead = filters.safe_color(brand.colors.get("masthead"), "") or None
    on_masthead = filters.safe_color(brand.colors.get("on_masthead"), DEFAULT_ON_MASTHEAD)
    logo_svg = filters.clean_svg(brand.logo_svg)
    logo_dark = filters.clean_svg(filters.prefix_svg_ids(brand.logo_svg_dark, "dk-")) if brand.logo_svg_dark else None
    height = brand.logo_height if brand.logo_height and 16 <= brand.logo_height <= 200 else None
    mobile = brand.logo_height_mobile if brand.logo_height_mobile and 16 <= brand.logo_height_mobile <= 200 else None
    if height and not mobile:
        mobile = round(height * 0.85)
    rgb = {k: filters.rgb_triplet(v) for k, v in colors.items()}
    rgb["on_masthead"] = filters.rgb_triplet(on_masthead)
    return BrandView(
        name=brand.name,
        wordmark=wordmark,
        tagline=brand.tagline,
        logo_svg=logo_svg,
        favicon_uri=filters.svg_data_uri(favicon_svg),
        colors=colors,
        rgb=rgb,
        masthead=masthead,
        on_masthead=on_masthead,
        logo_mono=bool(logo_svg and "currentColor" in logo_svg),
        logo_svg_dark=logo_dark if logo_svg else None,
        logo_height=height,
        logo_height_mobile=mobile if height else None,
        email_logo=(email_logo := _email_logo(brand.email_logo, config.site.base_url)),
        email_logo_dark=_email_logo(brand.email_logo_dark, config.site.base_url) if email_logo else None,
        email_header=_email_logo(brand.email_header, config.site.base_url),
        termometro=config.termometro_ativo,
        congresso=config.congresso_ativo and config.congresso.publicado,
        tribunais=config.tribunais_ativo and config.tribunais.publicado,
    )


def _email_logo(raw: dict[str, Any] | None, base_url: str) -> EmailLogo | None:
    """Logo do e-mail com URL absoluta (``src`` relativo vira ``base_url + src``) e tamanho válido."""
    if not raw:
        return None
    src = filters.safe_href(str(raw.get("src") or "")) or ""  # http(s) absoluto ou caminho relativo
    url = filters.safe_url(src if filters.safe_url(src) else base_url + src.lstrip("/")) if src else None
    try:
        width, height = int(raw.get("width") or 0), int(raw.get("height") or 0)
    except (TypeError, ValueError):
        width = height = 0
    if not url or not (0 < width <= 600 and 0 < height <= 200):
        log.warning("Logo do e-mail ignorado (src, width ou height inválido): %r", raw)
        return None
    return EmailLogo(src=url, width=width, height=height)


# ── cobertura comparada ──────────────────────────────────────────────────────


def _veiculos(count: int) -> str:
    return f"{count} veículo{'s' if count != 1 else ''}"


def _percentages(a: int, n: int, b: int) -> tuple[int, int, int]:
    """Larguras inteiras que somam 100 (maior resto), sem zerar um lado presente."""
    total = a + n + b
    if not total:
        return 0, 100, 0
    raw = [a * 100 / total, n * 100 / total, b * 100 / total]
    widths = [int(x) for x in raw]
    for i in sorted(range(3), key=lambda i: raw[i] - widths[i], reverse=True)[: 100 - sum(widths)]:
        widths[i] += 1
    return widths[0], widths[1], widths[2]


def coverage_view(
    coverage: Coverage | None,
    *,
    section: tuple[str, str] | None = None,
    tz: str | None = None,
    now_iso: str | None = None,
) -> CoverageView | None:
    """Medidor da cobertura: cresce para o lado com mais veículos; sem debate, "convergente"."""
    if coverage is None or not coverage.outlets:
        return None
    debate = bool(filters.plain(coverage.side_a) and filters.plain(coverage.side_b))
    groups: dict[str, list[OutletView]] = {"a": [], "b": [], "neutro": []}
    for outlet in coverage.outlets:
        name = filters.plain(outlet.name)
        if not name:
            continue
        stance = outlet.stance if debate and outlet.stance in groups else "neutro"
        groups[stance].append(OutletView(name=name, url=filters.safe_url(outlet.url), framing=filters.plain(outlet.framing)))
    a, b, neutral = groups["a"], groups["b"], groups["neutro"]
    total = len(a) + len(b) + len(neutral)
    if not total:
        return None
    side_a, side_b = (filters.plain(coverage.side_a), filters.plain(coverage.side_b)) if debate else ("", "")
    if not debate:
        lean, label = "convergente", f"Sem divergência entre {'os ' + _veiculos(total) if total > 1 else 'os veículos'}"
    elif len(a) > len(b):
        lean, label = "a", f"Pende para: {side_a} · {len(a)} de {_veiculos(total)}"
    elif len(b) > len(a):
        lean, label = "b", f"Pende para: {side_b} · {len(b)} de {_veiculos(total)}"
    else:
        lean, label = "equilibrio", f"Equilíbrio: {len(a)} × {len(b)} de {_veiculos(total)}"
    if debate:
        aria = f"{side_a}: {_veiculos(len(a))}; neutros: {len(neutral)}; {side_b}: {_veiculos(len(b))}"
    else:
        aria = f"Todos os {_veiculos(total)} relataram o assunto de forma semelhante"
    pct_a, pct_n, pct_b = _percentages(len(a), len(neutral), len(b))
    title, color = section or ("", "")
    return CoverageView(
        topic=filters.plain(coverage.topic),
        conclusion=filters.plain(coverage.conclusion),
        side_a=side_a,
        side_b=side_b,
        a=a,
        b=b,
        neutral=neutral,
        pct_a=pct_a,
        pct_n=pct_n,
        pct_b=pct_b,
        lean=lean,
        lean_label=label,
        aria=aria,
        section_title=title,
        color=color,
        url=filters.safe_url(coverage.url),
        time=filters.local_time(coverage.published, tz, now_iso) if tz else "",
    )


# ── matérias ─────────────────────────────────────────────────────────────────


def _source_links(story: Story) -> list[SourceLink]:
    links: list[SourceLink] = []
    seen: set[str] = set()
    for ref in story.sources:
        name = filters.plain(ref.name)
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        links.append(SourceLink(name=name, url=filters.safe_url(ref.url)))
    return links


def _compiled_label(story: Story, sources: list[SourceLink]) -> str:
    """"Compilada de 7 notícias de 5 veículos · bloco Economia & Mercados"."""
    count = len(dict.fromkeys(story.article_ids))
    if count <= 1 and not story.block:
        return ""
    label = f"Compilada de {_count(count, 'notícia', 'notícias')}"
    if sources:
        label += f" de {_count(len(sources), 'veículo', 'veículos')}"
    if story.block:
        label += f" · bloco {filters.plain(story.block)}"
    return label


def _count(value: int, singular: str, plural: str) -> str:
    return f"{text.format_number_pt(value, 0)} {singular if value == 1 else plural}"


def _story_view(
    story: Story, section: tuple[str, str], *, tz: str, now_iso: str, archive: bool = False
) -> StoryView:
    title, color = section
    lang = (story.lang or "pt").strip().lower()
    sources = _source_links(story)
    coverage = coverage_view(story.coverage)
    return StoryView(
        id=story.id,
        anchor=f"s-{story.id}",
        section_title=title,
        color=color,
        headline=filters.plain(story.headline),
        dek=filters.plain(story.dek),
        body=[p.strip() for p in story.body if p and filters.plain(p)],
        why=filters.plain(story.why_it_matters),
        image=filters.safe_url(story.image),
        sources=sources,
        time=filters.local_time(story.published, tz, now_iso),
        age="" if archive else filters.rel_age(story.published, now_iso),
        published=story.published,
        lang=None if lang.startswith("pt") else lang,
        coverage=coverage,
        compiled=_compiled_label(story, sources),
        marca=_marca_da_materia(coverage),
    )


def _fichas_do_congresso(edition: Edition) -> dict[str, list[dict[str, Any]]]:
    """As fichas dos parlamentares citados em cada matéria (``Edition.congresso``), prontas
    para o modelo: a legenda, a barra do saldo e uma linha com o que os registros dizem."""
    materias = (edition.congresso or {}).get("materias") or {}
    saida: dict[str, list[dict[str, Any]]] = {}
    for story_id, lista in materias.items():
        views = []
        for p in lista if isinstance(lista, list) else []:
            if not isinstance(p, dict) or not p.get("k") or not p.get("nome"):
                continue
            saldo = p.get("saldo")
            partes = []
            if p.get("preside"):
                partes.append(f"Preside {'a Câmara' if p.get('casa') == 'Câmara' else 'o Senado'} (só vota em caso de empate)")
            if p.get("gov") is not None:
                partes.append(f"votou com o governo em {round(float(p['gov']))}% das vezes")
            if p.get("pres") is not None:
                partes.append(f"participou de {round(float(p['pres']))}% das votações nominais")
            if p.get("par"):
                partes.append(f"tem {p['par']} {'proposta parada' if p['par'] == 1 else 'propostas paradas'} há mais de um ano")
            if p.get("rel"):
                partes.append(f"segura {p['rel']} {'relatoria' if p['rel'] == 1 else 'relatorias'} há mais de seis meses")
            texto = "; ".join(partes)
            view = {
                "k": str(p["k"]),
                "nome": filters.plain(str(p["nome"])),
                "legenda": " · ".join(x for x in ("-".join(y for y in (p.get("partido"), p.get("uf")) if y), p.get("casa")) if x)
                + ("" if p.get("ex", True) else " · fora do exercício"),
                "texto": (texto[:1].upper() + texto[1:] + ".") if texto else "",
                "saldo_txt": "",
                "campo": filters.plain(str(p.get("campo") or "")),
                "lado": "c",
                "left": 50.0,
                "width": 0.0,
            }
            if isinstance(saldo, (int, float)):
                valor = max(-100.0, min(100.0, float(saldo)))
                view["saldo_txt"] = f"{round(valor):+d}".replace("-", "−")
                view["lado"] = "d" if valor > 20 else "e" if valor < -20 else "c"
                view["left"] = round(50 + valor / 2, 1) if valor < 0 else 50.0
                view["width"] = round(abs(valor) / 2, 1)
            views.append(view)
        if views:
            saida[str(story_id)] = views
    return saida


def _section_lookup(edition: Edition, config: Config, default_color: str) -> dict[str, tuple[str, str]]:
    """``id → (título, cor)``: prioriza a edição e completa com a configuração."""
    lookup = {s.id: (s.title, filters.safe_color(s.color, default_color)) for s in config.sections}
    for sec in edition.sections:
        fallback_title = lookup.get(sec.id, (sec.id, default_color))[0]
        lookup[sec.id] = (sec.title or fallback_title, filters.safe_color(sec.color, default_color))
    return lookup


def _published_ts(view: StoryView) -> float:
    moment = filters.parse_iso(view.published)
    return moment.timestamp() if moment else float("-inf")


def _pick(ids: list[str], by_id: dict[str, StoryView], exclude: set[str]) -> list[StoryView]:
    """Resolve ids em views, pulando os inexistentes e os já usados (``exclude`` é atualizado)."""
    out: list[StoryView] = []
    for story_id in ids:
        view = by_id.get(story_id)
        if view and story_id not in exclude:
            out.append(view)
            exclude.add(story_id)
    return out


def _build_sections(
    edition: Edition, by_id: dict[str, StoryView], lookup: dict[str, tuple[str, str]], default_color: str
) -> list[SectionView]:
    """Seções na ordem da edição, sem seções vazias; matérias órfãs vão para "Outras"."""
    placed: set[str] = set()
    sections: list[SectionView] = []
    for sec in edition.sections:
        stories = _pick(sec.story_ids, by_id, placed)
        if stories:
            title, color = lookup.get(sec.id, (sec.title, default_color))
            sections.append(SectionView(id=sec.id, title=title, color=color, stories=stories))
    orphans = [v for sid, v in by_id.items() if sid not in placed]
    if orphans:
        log.warning("%d matéria(s) sem seção na edição; exibidas em %r", len(orphans), ORPHAN_SECTION[1])
        sections.append(
            SectionView(id=ORPHAN_SECTION[0], title=ORPHAN_SECTION[1], color=default_color, stories=orphans)
        )
    return sections


# ── painel (cotações, clima, fontes) ─────────────────────────────────────────


def _quote_view(quote: Quote) -> QuoteView:
    change = format_change(quote.change_pct) if quote.change_pct is not None else None
    note = " · ".join(p for p in (quote.source or "", f"ref. {quote.as_of[:10]}" if quote.as_of else "") if p)
    return QuoteView(
        label=quote.label,
        display=quote.display,
        change=change,
        css=filters.change_class(quote.change_pct),
        note=note,
    )


def _weather_views(edition: Edition, config: Config) -> list[WeatherView]:
    """O clima mostra só as cidades da configuração (``weather`` em ``config/site.yaml``):
    uma cidade que saiu de lá some também das edições refeitas, mesmo que a coleta
    daquele dia a tenha registrado. A home usa a primeira (último item da linha de
    indicadores, só em tela larga)."""
    cidades = {text.normalize(c.name) for c in config.weather}
    views = []
    for w in edition.weather:
        if cidades and text.normalize(w.city) not in cidades:
            continue
        now_temp = filters.temp(w.current_c)
        views.append(
            WeatherView(
                city=w.city,
                now_emoji=w.current_emoji,
                now_desc=w.current_desc,
                now_temp=f"{now_temp}C" if now_temp != "–" else now_temp,
                today_min=filters.temp(w.today_min),
                today_max=filters.temp(w.today_max),
                tomorrow_emoji=w.tomorrow_emoji,
                tomorrow_desc=w.tomorrow_desc,
                tomorrow_min=filters.temp(w.tomorrow_min),
                tomorrow_max=filters.temp(w.tomorrow_max),
            )
        )
    return views


def _source_groups(edition: Edition, config: Config) -> list[SourceGroup]:
    """Agrupa os feeds por veículo e marca os que falharam na coleta."""
    failed_by_url = {f.get("url"): f for f in edition.stats.sources_failed if f.get("url")}
    groups: dict[str, SourceGroup] = {}
    matched: set[str] = set()
    for source in config.sources:
        group = groups.setdefault(source.name, SourceGroup(name=source.name))
        group.feeds += 1
        failure = failed_by_url.get(source.url)
        if failure:
            matched.add(source.url)
            group.failed += 1
            group.errors.append(str(failure.get("error") or "erro"))
    # Falhas de feeds que não estão (mais) na configuração também aparecem.
    for failure in edition.stats.sources_failed:
        if failure.get("url") in matched:
            continue
        name = str(failure.get("source_id") or failure.get("url") or "fonte")
        group = groups.setdefault(name, SourceGroup(name=name))
        group.feeds += 1
        group.failed += 1
        group.errors.append(str(failure.get("error") or "erro"))
    return list(groups.values())


def _recentes(edition: Edition, ordered: list[StoryView], na_capa: set[str], tz: str) -> list[RecenteView]:
    """"Mais recentes": as notícias do dia que não viraram matéria (``edition.wire``, com
    link para a fonte), até ``TETO_RECENTES``; em edições sem esse campo, as matérias
    mais recentes da edição que não estão no andar das notícias (``na_capa``)."""
    items: list[RecenteView] = []
    for entry in edition.wire:
        href = filters.safe_url(entry.get("url"))
        title = filters.plain(str(entry.get("title") or ""))
        if not href or not title:
            continue
        items.append(
            RecenteView(
                time=filters.local_time(entry.get("published"), tz, edition.generated_at),
                source=filters.plain(str(entry.get("source") or "")),
                title=title,
                href=href,
                external=True,
            )
        )
    if not items:
        fora_da_capa = (v for v in ordered if v.published and v.id not in na_capa)
        items = [
            RecenteView(
                time=v.time,
                source=v.sources[0].name if v.sources else v.section_title,
                title=v.headline,
                href=f"#{v.anchor}",
                external=False,
                lang=v.lang,
            )
            for v in sorted(fora_da_capa, key=_published_ts, reverse=True)
        ]
    items = items[:TETO_RECENTES]
    for item in items[RECENTES_NO_CELULAR:]:
        item.so_tela_larga = True
    return items


def _compared_pairs(
    edition: Edition, lookup: dict[str, tuple[str, str]], default_color: str, tz: str
) -> list[tuple[str, CoverageView]]:
    """Demais assuntos com cobertura comparada (``(editoria, view)``), na ordem da edição
    (mais veículos primeiro)."""
    pares = []
    for coverage in edition.compared:
        section = lookup.get(coverage.section or "", ("", default_color))
        view = coverage_view(coverage, section=section, tz=tz, now_iso=edition.generated_at)
        if view is not None and view.topic:
            pares.append((coverage.section or "", view))
    return pares


def _compared(edition: Edition, lookup: dict[str, tuple[str, str]], default_color: str, tz: str) -> list[CoverageView]:
    """Demais assuntos com cobertura comparada, na ordem da edição (mais veículos primeiro)."""
    return [view for _sec, view in _compared_pairs(edition, lookup, default_color, tz)]


def _divergence(c: CoverageView) -> float | None:
    """2 × o lado menor ÷ (A + B), só com veículos dos dois lados (100% = meio a meio)."""
    if not (c.side_a and c.a and c.b):
        return None
    return 2 * min(len(c.a), len(c.b)) / (len(c.a) + len(c.b))


def _compared_map(
    edition: Edition,
    ordered: list[StoryView],
    pares: list[tuple[str, CoverageView]],
    lookup: dict[str, tuple[str, str]],
    config: Config,
    default_color: str,
) -> ComparedMapView | None:
    """Matérias da edição com análise da cobertura + demais assuntos comparados, num
    mapa só: onde a mídia divergiu (mais disputados primeiro) e o resto por editoria."""
    secao_da_materia = {s.id: s.section or "" for s in edition.stories}
    itens: list[CompareItemView] = []
    for view in ordered:
        if view.coverage is None:
            continue
        sec = secao_da_materia.get(view.id, "")
        cov = view.coverage
        if not cov.section_title:
            titulo, cor = lookup.get(sec, (view.section_title, view.color))
            cov = replace(cov, section_title=titulo or view.section_title, color=cor or view.color, time=view.time)
        itens.append(
            CompareItemView(c=cov, section=sec, divergence=_divergence(cov), story_href=f"#{view.anchor}", story_title=view.headline)
        )
    for sec, view in pares:
        itens.append(CompareItemView(c=view, section=sec, divergence=_divergence(view)))
    if not itens:
        return None
    ordem = {s.id: i for i, s in enumerate(config.sections)}
    posicao = {id(item): i for i, item in enumerate(itens)}
    # Mais disputados primeiro: mais veículos no lado menor (um 1 × 1 entre dois veículos
    # não vale um 3 × 4), depois o mais equilibrado e o com mais veículos.
    divididos = sorted(
        (i for i in itens if i.divergence is not None),
        key=lambda i: (-min(len(i.c.a), len(i.c.b)), -(i.divergence or 0), -i.c.total, posicao[id(i)]),
    )
    ordem_dividido = {id(i): k for k, i in enumerate(divididos)}
    categorias: dict[str, CompareCategoryView] = {}
    for item in itens:
        cat = categorias.get(item.section)
        if cat is None:
            titulo, cor = lookup.get(item.section, (item.c.section_title or "Outras", default_color))
            cat = categorias[item.section] = CompareCategoryView(
                id=item.section or "outras", title=titulo or "Outras", color=cor, total=0, divided=0
            )
        cat.total += 1
        if item.divergence is not None:
            cat.divided += 1
            cat.divergentes.append(item)
        else:
            cat.items.append(item)
    for cat in categorias.values():
        cat.divergentes.sort(key=lambda i: ordem_dividido[id(i)])
    lista = sorted(categorias.values(), key=lambda c: (ordem.get(c.id, len(ordem)), c.title))
    return ComparedMapView(
        divided=divididos,
        categories=lista,
        total=len(itens),
        stories=sum(1 for i in itens if i.story_href),
    )


def _marca_da_materia(c: CoverageView | None) -> str:
    """A marca de divergência de uma matéria, ou "" quando o assunto não teve veículos dos
    dois lados (ou teve menos de ``DIVERGENCIA_MIN_VEICULOS`` veículos). É o que sobra, na
    capa e nos cartões, das listas de veículos e das barras, que ficam na janela da matéria."""
    if c is None or _divergence(c) is None or c.total < DIVERGENCIA_MIN_VEICULOS:
        return ""
    return _marca_de_divergencia(c)


def _marca_de_divergencia(c: CoverageView) -> str:
    """Como chamar um assunto com veículos dos dois lados: só é divergência quando o lado
    menor tem ``DIVERGE_MIN_LADO_MENOR`` veículos ou mais; com um só, ele destoou."""
    menor, maior = sorted((len(c.a), len(c.b)))
    if menor >= DIVERGE_MIN_LADO_MENOR:
        return "veículos divergem"
    return "1 veículo destoou" if maior > menor else "1 veículo de cada lado"


def _lados(c: CoverageView) -> list[LadoView]:
    """Os dois lados em palavras, com a conta: o lado com mais veículos primeiro, depois o
    menor ("1 veículo destoou" quando é um só contra vários) e os veículos sem lado."""
    (n_maior, maior), (n_menor, menor) = sorted(((len(c.a), c.side_a), (len(c.b), c.side_b)), key=lambda lado: -lado[0])
    destoou = n_menor < DIVERGE_MIN_LADO_MENOR and n_maior > n_menor
    linhas = [
        LadoView(conta=_veiculos(n_maior), texto=maior.rstrip(" .")),
        LadoView(conta="1 veículo destoou" if destoou else _veiculos(n_menor), texto=menor.rstrip(" .")),
    ]
    if c.neutral:
        linhas.append(LadoView(conta=f"{len(c.neutral)} sem lado"))
    return linhas


def _divergencias(mapa: ComparedMapView | None, copia_href: str) -> list[DivergenciaView]:
    """"Onde os veículos divergiram": até ``TETO_DIVERGENCIAS`` assuntos com veículos dos
    dois lados e ``DIVERGENCIA_MIN_VEICULOS`` veículos ou mais. Primeiro os de lado menor
    mais cheio; no empate, o assunto com mais veículos e, depois, o mais equilibrado.
    O título abre a matéria da edição; assunto que não virou matéria leva à cobertura
    completa da cópia arquivada (nunca ao texto de um dos lados)."""
    if mapa is None:
        return []
    candidatos = [i for i in mapa.divided if i.c.total >= DIVERGENCIA_MIN_VEICULOS]
    posicao = {id(item): k for k, item in enumerate(candidatos)}
    candidatos.sort(key=lambda i: (-min(len(i.c.a), len(i.c.b)), -i.c.total, -(i.divergence or 0), posicao[id(i)]))
    saida = []
    for n, item in enumerate(candidatos[:TETO_DIVERGENCIAS], start=1):
        materia = item.story_href.startswith("#s-")
        saida.append(
            DivergenciaView(
                anchor=f"dv-{n}",
                titulo=item.story_title or item.c.topic,
                href=item.story_href or f"{copia_href}#cobertura",
                lados=_lados(item.c),
                marca=_marca_de_divergencia(item.c),
                story_id=item.story_href[3:] if materia else "",
            )
        )
    return saida


def _tamanho_do_titulo(headline: str) -> str:
    """Classe de letra da manchete pelo comprimento do título (calculada aqui, não no
    navegador): o título nunca é cortado, a letra é que diminui."""
    if len(headline) <= TITULO_CURTO:
        return "curto"
    return "medio" if len(headline) <= TITULO_MEDIO else "longo"


def _servico(
    view: StoryView, *, manchete: bool, divergencias: list[DivergenciaView], copia_href: str
) -> list[ServicoView]:
    """Linha de serviço de uma chamada, na ordem em que aparece.

    Manchete: "N veículos", há quanto tempo (na cópia arquivada, a hora) e a marca de
    divergência. Chamadas de "Em 1 minuto": só a marca de divergência (sem ela, a
    chamada não tem linha de serviço). A marca só existe quando o assunto teve veículos
    dos dois lados (e 3 veículos ou mais) e leva ao item da home em "Onde os veículos
    divergiram"; se o assunto não coube ali, à cobertura completa da cópia arquivada.
    Nenhuma lista de veículos, nenhuma barra.

    Para acrescentar um trecho (a agenda da semana: "na agenda: qua 25"), junte um
    ``ServicoView`` ao fim da lista devolvida, aqui ou em ``build_view``; o modelo
    desenha os trechos na ordem, separados por " · ".
    """
    linha: list[ServicoView] = []
    if manchete:
        if view.sources:
            linha.append(ServicoView(texto=_veiculos(len(view.sources)), tipo="veiculos"))
        if view.age:
            linha.append(ServicoView(texto=view.age, tipo="idade", quando=view.published or ""))
        elif view.time:
            linha.append(ServicoView(texto=view.time, tipo="hora", quando=view.published or ""))
    if view.marca:
        item = next((d for d in divergencias if d.story_id == view.id), None)
        linha.append(
            ServicoView(texto=view.marca, tipo="diverge", href=f"#{item.anchor}" if item else f"{copia_href}#cobertura")
        )
    return linha


def _chamadas(
    lead: StoryView | None, secondary: list[StoryView], highlights: list[StoryView], ordered: list[StoryView]
) -> list[StoryView]:
    """As chamadas de "Em 1 minuto": as secundárias (até ``CHAMADAS_SECUNDARIAS``), os
    primeiros destaques e, se ainda faltar, as próximas matérias na ordem das seções, até
    ``TETO_CHAMADAS``. Nenhuma repete a manchete nem outra chamada: cada matéria aparece
    uma vez no andar das notícias. As demais ficam em "Todas as matérias, por editoria"."""
    if lead is None:
        return []
    usadas = {lead.id}
    saida: list[StoryView] = []
    for story in [*secondary[:CHAMADAS_SECUNDARIAS], *highlights, *ordered]:
        if len(saida) >= TETO_CHAMADAS:
            break
        if story.id not in usadas:
            usadas.add(story.id)
            saida.append(story)
    return saida


def _agenda_da_semana(
    edition: Edition, config: Config, brand: BrandView, materias: dict[str, StoryView], copia_href: str, pagina: bool
) -> tuple[agenda_tela.AgendaView | None, dict[str, str]]:
    """O bloco "Agenda da semana" e o rótulo "na agenda: qua 25" de cada matéria ligada.

    O retrato gravado na edição (``Edition.agenda``) vira aqui objetos validados; o modelo
    só lê esses objetos. O bloco é adicional: com a agenda desligada para a marca, sem
    retrato, com retrato em formato inesperado ou com qualquer erro, devolve vazio e a
    página sai como sairia sem a agenda. ``pagina``: a página completa existe (sem ela, o
    pé não leva o link).
    """
    if not config.agenda_ativo or edition.agenda is None:
        return None, {}
    try:
        retrato = ler_retrato(edition.agenda)
        # A pasta edicoes/ vista desta página: "edicoes/" na home, "" na cópia arquivada.
        pasta = f"{copia_href.rsplit('/', 1)[0]}/" if "/" in copia_href else ""
        bloco = agenda_tela.bloco_da_home(
            retrato, materias=materias.keys(), pasta=pasta, pagina=pagina, congresso=brand.congresso, tribunais=brand.tribunais
        )
        if retrato is None or bloco is None:
            return None, {}
        rotulos = {m.materia: " ".join(m.rotulo.split())[:40] for m in retrato.materias if m.materia in materias and m.rotulo.strip()}
        return bloco, rotulos
    except Exception as exc:  # noqa: BLE001 — a agenda é adicional: nunca derruba a página da edição
        log.warning("Agenda da semana fora da página da edição (%s: %s)", type(exc).__name__, exc)
        return None, {}


_METHOD_TEXT = {
    "blocos": "em blocos por editoria",
    "etapas": "em etapas (agrupamento, pauta, redação e cobertura)",
    "duas chamadas": "em duas chamadas (pauta e redação)",
}


def _rationale_view(edition: Edition) -> RationaleView | None:
    """Racional da compilação em texto (resumo, linha curta e blocos)."""
    rationale: Rationale | None = edition.rationale
    if rationale is None or not rationale.articles:
        return None
    articles = _count(rationale.articles, "notícia", "notícias")
    outlets = _count(rationale.outlets, "veículo", "veículos")
    stories = _count(len(edition.stories), "matéria", "matérias")
    analysed = sum(1 for s in edition.stories if s.coverage) + len(edition.compared)
    blocks = [
        BlockView(name=filters.plain(b.name), articles=b.articles, stories=b.stories, compared=b.compared, ok=b.ok)
        for b in rationale.blocks
    ]
    if rationale.method == "automática" or edition.mode != "ai":
        topics = rationale.groups
        summary = (
            f"Edição automática (sem IA): as {articles} coletadas de {outlets} foram agrupadas por títulos "
            f"parecidos em {_count(topics, 'assunto', 'assuntos')}; as {stories} saíram por relevância, "
            "frescor e número de veículos. Nenhuma notícia foi descartada: todas estão na lista completa do dia."
        )
    else:
        topics = rationale.topics or rationale.groups
        how = _METHOD_TEXT.get(rationale.method, "")
        summary = f"A IA leu as {articles} coletadas de {outlets}"
        if blocks:
            summary += f", compiladas em {_count(len(blocks), 'bloco', 'blocos')} por editoria"
        elif how:
            summary += f", {how}"
        if rationale.topics:
            summary += f"; uniu as notícias repetidas em {_count(rationale.topics, 'assunto', 'assuntos')}"
        if analysed:
            summary += (
                f", interpretou o foco de cada veículo e comparou para que lado cada um seguiu em "
                f"{_count(analysed, 'assunto', 'assuntos')}"
            )
        summary += (
            f" e escolheu as {stories} desta edição. Nenhuma notícia foi descartada: todas estão na lista "
            "completa do dia."
        )
    return RationaleView(
        summary=summary,
        linha=f"Compilada de {articles} de {outlets}",
        articles=rationale.articles,
        outlets=rationale.outlets,
        topics=topics,
        method=rationale.method,
        blocks=blocks,
    )


def _index_view(
    edition: Edition, lookup: dict[str, tuple[str, str]], default_color: str, tz: str, by_id: dict[str, StoryView]
) -> list[IndexSectionView]:
    """Todas as notícias do dia por seção e assunto (links validados, horários locais)."""
    sections: dict[str, IndexSectionView] = {}
    for topic in edition.index:
        items = []
        for item in topic.items:
            title = filters.plain(item.title)
            if not title:
                continue
            lang = (item.lang or "pt").strip().lower()
            items.append(
                IndexItemView(
                    source=filters.plain(item.source),
                    title=title,
                    url=filters.safe_url(item.url),
                    time=filters.local_time(item.published, tz, edition.generated_at),
                    lang=None if lang.startswith("pt") else lang,
                )
            )
        if not items:
            continue
        story = by_id.get(topic.story_id or "")
        title, color = lookup.get(topic.section, (topic.section, default_color))
        section = sections.setdefault(
            topic.section, IndexSectionView(id=topic.section, title=title, color=color, topics=[])
        )
        section.topics.append(
            IndexTopicView(
                title=filters.plain(topic.title) or items[0].title,
                items=items,
                href=f"#{story.anchor}" if story else None,
                compared=topic.compared,
            )
        )
    order = {sid: i for i, sid in enumerate(lookup)}
    return sorted(sections.values(), key=lambda s: order.get(s.id, len(order)))


def _mode_label(edition: Edition) -> str:
    if edition.mode == "ai":
        return f"Edição gerada por IA ({edition.model})" if edition.model else "Edição gerada por IA"
    return "Edição automática (sem IA)"


# ── view completa ────────────────────────────────────────────────────────────


def instagram_url(config: Config) -> str:
    """Link do perfil do Instagram, se o @ estiver configurado (instagram.handle)."""
    handle = re.sub(r"[^A-Za-z0-9._]", "", (config.instagram.handle or "").lstrip("@"))
    return f"https://www.instagram.com/{handle}/" if handle else ""


def build_view(
    edition: Edition, config: Config, *, archive: bool = False, copia_href: str = "", agenda_pagina: bool = True
) -> EditionView:
    """Converte a edição num modelo de apresentação validado e sem marcação.

    ``archive``: cópia do arquivo (``edicoes/AAAA-MM-DD.html``), sem idade
    relativa ("há 3 h") que ficaria congelada; é ela que leva as gavetas de
    detalhe. ``copia_href``: endereço dessa cópia visto da página que está sendo
    montada ("" na própria cópia), para os links de detalhe da home.
    ``agenda_pagina``: a página ``edicoes/agenda.html`` existe (sem ela, o pé do
    bloco "Agenda da semana" não leva o link "Agenda completa").
    """
    brand = brand_view(config)
    tz = config.site.timezone
    default_color = brand.colors["primary"]
    lookup = _section_lookup(edition, config, default_color)

    fichas = _fichas_do_congresso(edition) if brand.congresso else {}
    by_id: dict[str, StoryView] = {}
    for story in edition.stories:
        if story.id in by_id:
            log.warning("Matéria com id duplicado ignorada: %s", story.id)
            continue
        view = _story_view(
            story,
            lookup.get(story.section, (story.section, default_color)),
            tz=tz,
            now_iso=edition.generated_at,
            archive=archive,
        )
        if not view.headline:
            log.warning("Matéria sem título ignorada na renderização: %s", story.id)
            continue
        view.congresso = fichas.get(story.id, [])
        by_id[story.id] = view

    sections = _build_sections(edition, by_id, lookup, default_color)
    ordered = [v for sec in sections for v in sec.stories]

    used: set[str] = set()
    lead = by_id.get(edition.lead)
    if lead is None and ordered:
        log.warning("Manchete %r não encontrada; usando a primeira matéria", edition.lead)
        lead = ordered[0]
    if lead:
        used.add(lead.id)
    secondary = _pick(edition.secondary, by_id, used)
    highlights = _pick(edition.highlights, by_id, used)

    description = filters.plain(edition.editorial) or (lead.dek if lead else "") or brand.tagline
    base_url = config.site.base_url
    pares = _compared_pairs(edition, lookup, default_color, tz)
    mapa = _compared_map(edition, ordered, pares, lookup, config, default_color)
    painel = termometro_dia_view(edition.termometro) if config.termometro_ativo else None
    time_label = filters.local_time(edition.generated_at, tz)

    # Andar das notícias (manchete e "Em 1 minuto") e "Como a mídia cobriu".
    divergencias = _divergencias(mapa, copia_href)
    chamadas = _chamadas(lead, secondary, highlights, ordered)
    na_capa = {s.id for s in ([lead] if lead else []) + chamadas}
    recentes = _recentes(edition, ordered, na_capa, tz)
    vies = vies_do_dia(painel)
    midia = None
    if vies or mapa or recentes:
        midia = MidiaView(
            vies=vies,
            comparados=mapa.total if mapa else 0,
            divergencias=divergencias,
            recentes=recentes,
            ate=time_label.replace(":", "h") if re.fullmatch(r"\d{2}:\d{2}", time_label) else "",
        )

    # Agenda da semana: o bloco e o rótulo "na agenda" das chamadas ligadas a um item pautado.
    agenda, na_agenda = _agenda_da_semana(edition, config, brand, by_id, copia_href, agenda_pagina)

    def chamada(story: StoryView, *, manchete: bool = False) -> ChamadaView:
        servico = _servico(story, manchete=manchete, divergencias=divergencias, copia_href=copia_href)
        if story.id in na_agenda:
            servico.append(ServicoView(texto=na_agenda[story.id], tipo="agenda", href=f"#{agenda_tela.ANCORA}"))
        return ChamadaView(
            story=story,
            servico=servico,
            tamanho=_tamanho_do_titulo(story.headline) if manchete else "",
        )

    clima = _weather_views(edition, config)
    return EditionView(
        brand=brand,
        date_label=edition.date_label,
        time_label=time_label,
        tz_label=filters.tz_label(tz, edition.generated_at),
        mode_label=_mode_label(edition),
        editorial=edition.editorial.strip() if filters.plain(edition.editorial) else "",
        briefing=[item.strip() for item in edition.briefing if filters.plain(item)],
        lead=lead,
        secondary=secondary,
        highlights=highlights,
        sections=sections,
        stories=ordered,
        compared=[view for _sec, view in pares],
        compared_map=mapa,
        quotes=[_quote_view(q) for q in edition.quotes],
        clima=clima[0] if clima else None,
        sources_total=edition.stats.sources_total,
        sources_ok=edition.stats.sources_ok,
        source_groups=_source_groups(edition, config),
        base_url=base_url,
        repo_url=filters.safe_url(config.site.repo_url) or "",
        instagram_url=instagram_url(config),
        edition_url=f"{base_url}edicoes/{edition.date}.html",
        description=text.truncate(description, DESCRIPTION_MAX),
        og_image=lead.image if lead else None,
        mode=edition.mode,
        rationale=_rationale_view(edition),
        index=_index_view(edition, lookup, default_color, tz, by_id),
        index_url=f"{base_url}edicoes/{index_page_name(edition.date)}" if edition.index else "",
        termometro=painel,
        manchete=chamada(lead, manchete=True) if lead else None,
        chamadas=[chamada(s) for s in chamadas],
        midia=midia,
        agenda=agenda,
        arquivada=archive,
        copia_href=copia_href,
    )


# ── páginas ──────────────────────────────────────────────────────────────────


def render_edition_page(
    edition: Edition,
    config: Config,
    *,
    home_href: str,
    archive_href: str,
    is_archive: bool = False,
    agenda_pagina: bool = True,
) -> str:
    """Página única e autocontida da edição (CSS/JS inline).

    A mesma função monta a home (``index.html``) e a cópia arquivada
    (``edicoes/AAAA-MM-DD.html``, com ``is_archive``). A cópia não leva a idade
    relativa, que ficaria congelada, e é a única que renderiza as gavetas de
    detalhe (``#cobertura``, ``#termometro`` e ``#racional``); na home, os links
    de detalhe apontam para a cópia do dia, que fica em ``archive_href``.
    ``agenda_pagina``: ``edicoes/agenda.html`` existe (ver :func:`build_view`).
    """
    archive = filters.safe_href(archive_href) or "edicoes/"
    copia = "" if is_archive else filters.safe_href(f"{archive.rstrip('/')}/{edition.date}.html") or ""
    view = build_view(edition, config, archive=is_archive, copia_href=copia, agenda_pagina=agenda_pagina)
    if view.termometro is not None:  # a aba fica em edicoes/, ao lado das edições anteriores
        view.termometro.analitico_href = filters.safe_href(f"{archive.rstrip('/')}/{ANALITICO_PAGINA}") or ""
    template = filters.environment().get_template("edition.html.j2")
    html = template.render(
        v=view,
        brand=view.brand,
        home_href=filters.safe_href(home_href) or "./",
        archive_href=archive,
        agenda_script=view.agenda is not None,  # page.js.j2: HOJE, AMANHÃ e os dias passados
    )
    log.info(
        "Página da edição %s renderizada: %d matérias, %d seções, %.0f KB",
        edition.date,
        len(view.stories),
        len(view.sections),
        len(html.encode("utf-8")) / 1024,
    )
    return html


def index_page_name(iso_date: str) -> str:
    """Nome da página com todas as notícias do dia: ``2026-09-29-todas.html``."""
    return f"{iso_date}-todas.html"


def render_index_page(edition: Edition, config: Config, *, home_href: str, edition_href: str) -> str:
    """Página com todas as notícias do dia, por editoria e assunto (links das
    matérias apontam para a cópia arquivada da edição)."""
    view = build_view(edition, config, archive=True)
    for section in view.index:
        for topic in section.topics:
            if topic.href:
                topic.href = f"{edition_href}{topic.href}"
    template = filters.environment().get_template("todas.html.j2")
    html = template.render(
        v=view,
        brand=view.brand,
        home_href=filters.safe_href(home_href) or "../",
        edition_href=filters.safe_href(edition_href) or "./",
    )
    log.info(
        "Página com todas as notícias de %s renderizada: %d notícias, %d KB",
        edition.date,
        view.index_total,
        len(html.encode("utf-8")) // 1024,
    )
    return html


def _month_label(iso_date: str) -> str:
    try:
        day = date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return "Outras edições"
    return f"{text.MONTHS_PT[day.month - 1].capitalize()} de {day.year}"


def render_archive_index(entries: list[dict], config: Config, *, home_href: str) -> str:
    """Índice do arquivo: edições agrupadas por mês, da mais recente para a mais antiga."""
    brand = brand_view(config)
    months: list[dict[str, Any]] = []
    for entry in entries:
        href = filters.safe_href(str(entry.get("href") or ""))
        if not href:
            log.warning("Entrada do arquivo sem link válido ignorada: %r", entry.get("date"))
            continue
        label = _month_label(str(entry.get("date") or ""))
        if not months or months[-1]["label"] != label:
            months.append({"label": label, "items": []})
        months[-1]["items"].append(
            {
                "date": str(entry.get("date") or ""),
                "date_label": str(entry.get("date_label") or entry.get("date") or ""),
                "href": href,
                "headline": filters.plain(str(entry.get("lead_headline") or "")),
                "ai": entry.get("mode") == "ai",
            }
        )
    template = filters.environment().get_template("archive.html.j2")
    return template.render(
        brand=brand,
        months=months,
        total=sum(len(m["items"]) for m in months),
        home_href=filters.safe_href(home_href) or "../",
        base_url=config.site.base_url,
        repo_url=filters.safe_url(config.site.repo_url) or "",
    )
