"""E-mail diário: assunto, HTML (tabelas + CSS inline) e versão em texto puro.

No modelo do e-mail do TSA Tech: a faixa do cabeçalho (o logo sobre a arte do topo
do site), o slogan e a data; "Hoje em N notícias", com a manchete em destaque e as
demais em linhas de duas e três colunas (número, editoria, título e veículos); "Ler a
edição completa" e o total de notícias do dia; um rodapé curto. O HTML segue as
restrições de clientes de e-mail (Gmail, Outlook, Apple Mail): sem JavaScript, sem
SVG, sem web fonts, 600 px de largura, modo claro, e fica bem abaixo do limite que a
rotina do Gmail aceita (:data:`MAX_EMAIL_BYTES`).
"""

from __future__ import annotations

import logging
import re
import textwrap
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from .. import text
from ..config import Config
from ..models import Edition
from . import filters
from .web import EditionView, StoryView, build_view

log = logging.getLogger(__name__)

DEFAULT_SUBJECT = "{brand} · {date}: {lead}"
SUBJECT_LEAD_MAX = 70
PREHEADER_MAX = 150
TEXT_WIDTH = 72
# A rotina do Claude copia o HTML inteiro no parâmetro htmlBody do Gmail: o
# e-mail precisa ser enxuto. Acima disso, matérias saem do e-mail (a edição
# completa tem todas) até caber.
MAX_EMAIL_BYTES = 40 * 1024
_BETWEEN_TAGS = re.compile(r">\s*\n\s*<")  # só quebras de linha do template (nunca o espaço entre palavras)


@dataclass
class EmailItem:
    """Uma notícia do e-mail: posição (1 = manchete), matéria e link na edição."""

    n: int
    story: StoryView
    href: str

    @property
    def outlets(self) -> int:
        return len(self.story.sources)


def story_url(page_url: str, story_id: str) -> str:
    """Link para a matéria na página da edição (o modal abre pela âncora ``#s-<id>``).

    ``render_email`` usa a cópia arquivada (``edicoes/AAAA-MM-DD.html``): a capa
    muda no dia seguinte, e o link de um e-mail antigo continuaria abrindo a
    matéria certa.
    """
    return f"{page_url}#s-{quote(story_id, safe='-_.~')}"


def _subject(edition: Edition, config: Config) -> str:
    try:
        short_date = text.pt_short_date(date.fromisoformat(edition.date))
    except ValueError:
        short_date = edition.date
    lead = ""
    try:
        lead = text.truncate(" ".join(edition.story(edition.lead).headline.split()), SUBJECT_LEAD_MAX)
    except KeyError:
        pass
    values = {
        "brand": config.brand.name,
        "date_label": edition.date_label,
        "date": short_date,
        "lead": lead or edition.date_label,
    }
    try:
        subject = config.email.subject_template.format(**values)
    except (KeyError, IndexError, ValueError) as exc:
        log.warning("subject_template inválido (%s); usando o padrão", exc)
        subject = DEFAULT_SUBJECT.format(**values)
    # Assunto é cabeçalho de e-mail: nunca pode conter quebras de linha.
    return " ".join(subject.split())


def _select(view: EditionView, limit: int) -> list[StoryView]:
    """Até ``limit`` matérias além da manchete, na prioridade da primeira página
    (chamadas, destaques, demais). Na 1ª passada, uma por editoria que ainda não
    apareceu (a da manchete conta: variedade); na 2ª, completa pela mesma prioridade."""
    lead_id = view.lead.id if view.lead else None
    limit = max(limit, 0)
    priority: list[StoryView] = []
    vistos: set[str] = set()
    for story in [*view.secondary, *view.highlights, *view.stories]:
        if story.id != lead_id and story.id not in vistos:
            vistos.add(story.id)
            priority.append(story)
    escolhidas: list[StoryView] = []
    secoes: set[str] = {view.lead.section_title} if view.lead else set()
    for story in priority:
        if len(escolhidas) >= limit:
            break
        if story.section_title not in secoes:
            secoes.add(story.section_title)
            escolhidas.append(story)
    for story in priority:
        if len(escolhidas) >= limit:
            break
        if story not in escolhidas:
            escolhidas.append(story)
    ordem = {story.id: i for i, story in enumerate(priority)}
    return sorted(escolhidas, key=lambda story: ordem[story.id])


def _rows(items: list[EmailItem]) -> list[list[EmailItem]]:
    """As notícias depois da manchete: uma linha de duas colunas e as demais de três."""
    if not items:
        return []
    rows = [items[:2]]
    rows += [items[i : i + 3] for i in range(2, len(items), 3)]
    return rows


def _preheader(view: EditionView) -> str:
    source = filters.plain(view.editorial) or (view.lead.dek if view.lead else "") or view.brand.tagline
    return text.truncate(source, PREHEADER_MAX)


# ── texto puro ───────────────────────────────────────────────────────────────


def _wrap(value: str, indent: str = "", first: str | None = None) -> list[str]:
    return textwrap.wrap(
        value,
        width=TEXT_WIDTH,
        initial_indent=indent if first is None else first,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [first or indent]


def _heading(title: str) -> list[str]:
    title = title.upper()
    return ["", title, "-" * len(title)]


def _story_lines(story: StoryView, url: str, bullet: str) -> list[str]:
    indent = " " * len(bullet)
    lines = _wrap(story.headline, indent, first=bullet)
    if story.dek:
        lines += _wrap(story.dek, indent)
    lines.append(f"{indent}{url}")
    return lines


def _render_text(view: EditionView, items: list[EmailItem], footer: dict[str, str]) -> str:
    dateline = view.date_label + (f" · {view.time_label} {view.tz_label}" if view.time_label else "")
    lines = [view.brand.name.upper(), dateline]
    lead = items[0] if items and view.lead and items[0].story.id == view.lead.id else None
    if lead:
        lines += _heading("Manchete") + _wrap(lead.story.headline)
        if lead.story.dek:
            lines += _wrap(lead.story.dek)
        lines.append(f"Ler na edição: {lead.href}")
    others = items[1:] if lead else items
    if others:
        lines += _heading("Principais notícias")
        for item in others:
            lines += _story_lines(item.story, item.href, "• ") + [""]
        lines.pop()
    lines += ["", "=" * TEXT_WIDTH, f"Abrir edição completa: {view.base_url}"]
    if view.index_url:
        lines.append(f"Todas as {filters.num(view.index_total)} notícias do dia: {view.index_url}")
    lines.append(f"Edições anteriores: {footer['archive_url']}")
    if view.instagram_url:
        lines.append(f"Siga no Instagram: {view.instagram_url}")
    lines += _wrap(footer["generated"])
    return "\n".join(lines).strip() + "\n"


# ── API ──────────────────────────────────────────────────────────────────────


def _minify(html: str) -> str:
    """Tira a indentação e as quebras de linha entre tags (o visual não muda)."""
    return _BETWEEN_TAGS.sub("><", html).strip() + "\n"


def render_email(edition: Edition, config: Config) -> tuple[str, str, str]:
    """Gera ``(assunto, html, texto)`` do e-mail da edição.

    O HTML respeita :data:`MAX_EMAIL_BYTES`: se passar, as últimas notícias saem
    (uma a uma) até caber; a versão em texto traz as mesmas notícias.
    """
    view = build_view(edition, config)
    subject = _subject(edition, config)
    time_part = f" em {view.time_label} ({view.tz_label})" if view.time_label else ""
    footer = {
        "archive_url": f"{view.base_url}edicoes/",
        "generated": f"Gerado automaticamente{time_part} · {view.mode_label}",
    }
    template = filters.environment().get_template("email.html.j2")
    limit = max(config.email.max_stories, 0)
    while True:
        stories = ([view.lead] if view.lead else []) + _select(view, limit)
        items = [EmailItem(n=i + 1, story=s, href=story_url(view.edition_url, s.id)) for i, s in enumerate(stories)]
        lead = items[0] if view.lead and items else None
        html = _minify(
            template.render(
                v=view,
                brand=view.brand,
                subject=subject,
                preheader=_preheader(view),
                lead=lead,
                rows=_rows(items[1:] if lead else items),
                total=len(items),
                footer=footer,
            )
        )
        size = len(html.encode("utf-8"))
        shown = len(items) - (1 if lead else 0)
        if size <= MAX_EMAIL_BYTES or shown == 0:
            break
        limit = shown - 1
    if size > MAX_EMAIL_BYTES:
        log.warning(
            "HTML do e-mail com %.0f KB mesmo sem notícias além da manchete (limite: %d KB)",
            size / 1024,
            MAX_EMAIL_BYTES // 1024,
        )
    elif limit < config.email.max_stories:
        log.info("E-mail reduzido a %d notícias para caber em %d KB", len(items), MAX_EMAIL_BYTES // 1024)
    plain_text = _render_text(view, items, footer)
    log.info("E-mail renderizado: %d notícias, %.0f KB", len(items), size / 1024)
    return subject, html, plain_text
