"""Base comum dos formatos do Instagram e o Terminal da manhã (dias sem IA).

Nada aqui chama IA: textos, legendas e textos alternativos saem do JSON da
edição por template, para não criar um novo ponto em que fatos possam ser
inventados. Cada formato (:mod:`.formatos`) devolve uma :class:`Peca` (slides +
legenda); a escolha do dia e a renderização ficam em :mod:`.gerar`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

from markupsafe import Markup

from qijournal import text as qtext
from qijournal.models import Edition, Quote, Story

from . import estatistica

DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
DIAS_CURTOS = ["SEG", "TER", "QUA", "QUI", "SEX", "SÁB", "DOM"]
MESES_CURTOS = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]

HASHTAG_SECAO = {
    "brasil": "#economia",
    "mercados": "#mercadofinanceiro",
    "juridico": "#direito",
    "politica": "#politica",
    "mundo": "#geopolitica",
    "tecnologia": "#tecnologia",
    "esporte": "#esporte",
    "natureza": "#meioambiente",
    "variedades": "#cultura",
}
CAPTION_LIMIT = 2200
ALT_LIMIT = 1000

# Período eleitoral: pesquisa de intenção de voto não sai (Res. TSE 23.600/2019
# exige dados de registro que a edição não tem) e política não vira vitrine.
_PESQUISA = re.compile(
    r"\b(pesquisas?|datafolha|quaest|atlasintel|atlas|ipec|ipespe|paran[aá] pesquisas|poderdata|futura|nexus|"
    r"real time big data|inten[cç][aã]o de voto|inten[cç][oõ]es de voto|empate t[eé]cnico|lideran[cç]a num[eé]rica|"
    r"pontos percentuais|margem de erro)\b",
    re.I,
)


@dataclass
class Slide:
    template: str  # nome do template (sem extensão) em qijournal/instagram/templates
    ctx: dict[str, Any]
    alt: str = ""  # texto alternativo (só imagem e item de carrossel)


@dataclass
class Peca:
    """Uma publicação pronta para renderizar: um carrossel, uma imagem ou uma sequência de stories."""

    slot: str  # "manha", "stories", "numero", "noite", "lancamento"
    kind: str  # "carousel" | "image" | "story"
    title: str
    slides: list[Slide]
    caption: str = ""
    story_ids: list[str] = field(default_factory=list)  # matérias usadas (para não repetir no dia)
    serie: str = ""  # série numerada (Post.serie): "em1min", "numero", "raiox" (Placar do dia também), "mercado"
    numero_serie: int = 0  # preenchido pelo gerar com Fila.numero_para
    chave: str = ""  # do Número do dia (Numero.chave)
    entidades: list[str] = field(default_factory=list)  # do Raio-X (selecao.entidades)
    formula: str = ""  # molde do gancho (selecao.Gancho.formula)


# ── Utilidades de texto e números ─────────────────────────────────────────


def plain(value: str | None) -> str:
    return qtext.strip_markdown(value or "").strip()


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in value if not unicodedata.combining(c)).lower()


def _words(value: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", _norm(plain(value)))}


def pct(value: float | None) -> str:
    """Variação com sinal e vírgula: ``0.1051`` → ``"+0,11%"``; ``-0.171`` → ``"−0,17%"``."""
    if value is None:
        return ""
    rounded = round(value, 2)
    sign = "+" if rounded > 0 else "−" if rounded < 0 else ""
    return f"{sign}{abs(rounded):.2f}%".replace(".", ",")


def trend(value: float | None) -> str:
    if value is None:
        return ""
    rounded = round(value, 2)
    return "up" if rounded > 0 else "down" if rounded < 0 else "flat"


def short_date(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}"


def kicker_date(day: date) -> str:
    """``TER 20 OUT``."""
    return f"{DIAS_CURTOS[day.weekday()]} {day.day:02d} {MESES_CURTOS[day.month - 1]}"


def truncate_sentence(value: str, limit: int) -> str:
    """Corta em fim de frase (ou de palavra) sem passar de ``limit`` caracteres."""
    value = value.strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    if end >= limit * 0.5:
        return cut[: end + 1].strip()
    return cut.rsplit(" ", 1)[0].rstrip(",;:—-") + "…"


def num(value: int | None) -> str:
    return qtext.format_number_pt(int(value or 0), 0)


_DQUOTES = re.compile(r'"([^"\n]+)"')
_SQUOTES = re.compile(r"(^|[\s(\[{—–*-])'([^'\n]+)'(?=[\s.,;:!?)\]}—–*-]|$)")
_EMPHASIS = re.compile(r"\*\*(.+?)\*\*")
EMPHASIS_MAX_CHARS = 40
EMPHASIS_MAX_WORDS = 5
_NBSP_AFTER = re.compile(r"(?<![\w])(R\$|US\$|€|nº|n\.º) ")
_NBSP_NUMBER = re.compile(r"(?<=\d) (?=(?:mil|milhões|milhão|bilhões|bilhão|trilhões|trilhão|bi|mi|pts|pontos|minutos?|horas?|dias?|anos?|meses|mês|veículos|notícias|matérias|%)\b)")


def smart_quotes(value: str) -> str:
    """Aspas retas → tipográficas (“…” e ‘…’); apóstrofos ficam como estão."""
    value = _DQUOTES.sub("“\\1”", value)
    return _SQUOTES.sub("\\1‘\\2’", value)


def tidy(value: str) -> str:
    """Texto de slide: aspas tipográficas, ênfase (**…**) só em trechos curtos e espaço
    inseparável entre número e unidade ("1 minuto", "R$ 42,5", "827 milhões")."""
    value = smart_quotes(value)

    def emphasis(m: re.Match) -> str:
        inner = m.group(1)
        short = len(inner) <= EMPHASIS_MAX_CHARS and len(inner.split()) <= EMPHASIS_MAX_WORDS
        return m.group(0) if short else inner

    value = _EMPHASIS.sub(emphasis, value)
    value = _NBSP_AFTER.sub(lambda m: m.group(1) + "\u00a0", value)
    return _NBSP_NUMBER.sub("\u00a0", value)


def polish(ctx: Any) -> Any:
    """Aplica :func:`tidy` a todos os textos do contexto de um slide (recursivo); ``Markup``
    (o SVG dos gráficos) passa sem mexer."""
    if isinstance(ctx, Markup):
        return ctx
    if isinstance(ctx, str):
        return tidy(ctx)
    if isinstance(ctx, dict):
        return {k: (v if k in ("color", "id", "trend", "stance") else polish(v)) for k, v in ctx.items()}
    if isinstance(ctx, list):
        return [polish(v) for v in ctx]
    return ctx


# ── Leitura da edição ─────────────────────────────────────────────────────


def edition_day(edition: Edition) -> date:
    return date.fromisoformat(edition.date)


def section_info(edition: Edition, section_id: str) -> dict[str, str]:
    for s in edition.sections:
        if s.id == section_id:
            return {"id": s.id, "title": s.title, "color": s.color}
    return {"id": section_id, "title": section_id.title(), "color": "#394A7A"}


def source_names(story: Story) -> list[str]:
    seen: list[str] = []
    for src in story.sources:
        if src.name and src.name not in seen:
            seen.append(src.name)
    return seen


def sources_line(names: list[str], shown: int = 3) -> str:
    if not names:
        return ""
    head = ", ".join(names[:shown])
    return f"{head} +{len(names) - shown}" if len(names) > shown else head


def match_story(edition: Edition, item: str) -> Story | None:
    """Matéria do item do 'Em 1 minuto' (o briefing não traz a seção): mais palavras em comum."""
    words = _words(item)
    best, best_score = None, 0
    for story in edition.stories:
        score = len(words & _words(f"{story.headline} {story.dek}"))
        if score > best_score:
            best, best_score = story, score
    return best if best_score >= 3 else None


def quote_items(edition: Edition, ids: Iterable[str]) -> list[dict[str, Any]]:
    """Cotações para os slides: valor, variação com sinal e seta e de quando é a leitura ("fech. 19/10"
    só no fechamento de pregão, "leitura das 06h00", "set/26"); leitura velha sai sem variação."""
    by_id = {q.id: q for q in edition.quotes}
    day = edition_day(edition)
    items = []
    for qid in ids:
        q: Quote | None = by_id.get(qid)
        if q is None or not q.display:
            continue
        ref, change = "", q.change_pct
        if q.kind in ("fx", "index", "commodity", "crypto"):
            ponto = estatistica.ponto_da_cotacao(q)
            ref = estatistica.selo_leitura(q.id, ponto, day) if ponto is not None else ""
            if ponto is not None and not ref:  # leitura do começo do dia sem variação: não é dado
                change = None
        elif q.kind == "inflation" and q.as_of:
            year, _, month = q.as_of.partition("-")
            if month.isdigit():
                ref = f"{MESES_CURTOS[int(month) - 1].lower()}/{year[2:]}"
        items.append(
            {
                "id": q.id,
                "label": q.label,
                "value": q.display,
                "change": pct(change),
                "trend": trend(change),
                "ref": ref,
            }
        )
    return items


def quote_sentence(items: list[dict[str, Any]], ids: Iterable[str]) -> str:
    by_id = {i["id"]: i for i in items}
    parts = []
    for qid in ids:
        i = by_id.get(qid)
        if not i:
            continue
        extra = ", ".join(x for x in (i["change"], i["ref"]) if x)
        parts.append(f"{i['label']} {i['value']}" + (f" ({extra})" if extra else ""))
    return " · ".join(parts)


# ── Período eleitoral ─────────────────────────────────────────────────────


def is_poll(value: str) -> bool:
    return bool(_PESQUISA.search(plain(value)))


def is_electoral(story: Story | None, value: str = "", nomes: Iterable[str] = estatistica.NOMES_ELEICAO) -> bool:
    """:func:`.estatistica.is_electoral` sobre ``value`` e a matéria inteira (com a seção dela)."""
    text = " ".join(t for t in (plain(value), estatistica.story_text(story) if story is not None else "") if t)
    return estatistica.is_electoral(text, story.section if story is not None else None, nomes)


def is_political(story: Story | None, value: str = "", nomes: Iterable[str] = estatistica.NOMES_ELEICAO) -> bool:
    """Política, pesquisa ou texto eleitoral: no período eleitoral, fica fora das capas e do Raio-X."""
    return (story is not None and story.section == "politica") or is_poll(value) or is_electoral(story, value, nomes)


# ── Legendas ──────────────────────────────────────────────────────────────


def hashtags(fmt_tag: str, sections: Iterable[str], *, election: bool) -> str:
    tags = ["#triangula", fmt_tag]
    for sid in sections:
        tag = HASHTAG_SECAO.get(sid)
        if not tag or tag in tags or (election and sid == "politica"):
            continue
        tags.append(tag)
        if len(tags) == 4:
            break
    return " ".join(tags)


def finish_caption(parts: list[str]) -> str:
    caption = smart_quotes("\n\n".join(p.strip() for p in parts if p and p.strip()))
    if len(caption) > CAPTION_LIMIT:
        caption = caption[: CAPTION_LIMIT - 1].rsplit("\n", 1)[0]
    return caption


def alt(value: str) -> str:
    value = re.sub(r"\s+", " ", plain(value)).strip()
    return truncate_sentence(value, ALT_LIMIT)


# ── Terminal da manhã ─────────────────────────────────────────────────────


TERMINAL_MERCADO = ["usd", "ibov", "brent", "btc"]  # as linhas "▸" da legenda (os juros vão numa linha só)
TERMINAL_CTA = "Salve para comparar com a próxima leitura."
TERMINAL_VAR_MIN = 1.0  # % : abaixo disso, nenhuma variação vira título


def _achado_terminal(
    quotes: list[dict[str, Any]],
    edition: Edition,
    evitar: Iterable[str] = (),
    history: Sequence[Edition] = (),
    *,
    election: bool = False,
    nomes: Iterable[str] = estatistica.NOMES_ELEICAO,
) -> tuple[str, str, str]:
    """O título do Terminal, o trecho em laranja e o ativo do achado (a linha marcada na tabela).

    Achado = a maior variação entre as leituras que valem, fora dos ativos em ``evitar``, com pelo
    menos 1% e, se o ativo tem série para a escala robusta, 2 vezes a variação típica dele (o yuan
    +0,62% de 26/10 não é achado; "Brent +1,95% na leitura das 06h45" pode ser). Leitura velha
    (:func:`.estatistica.leitura_velha`) não vale e, no período eleitoral, nem o ativo de matéria
    eleitoral do dia (:func:`.estatistica.mercado_eleitoral`). Sem achado, o título é neutro:
    "13 indicadores antes da abertura" (ou "em dia sem pregão"), com o número em laranja."""
    day = edition_day(edition)
    evitar = set(evitar)
    antes = [e for e in history if e.date < edition.date]
    anterior = max(antes, key=lambda e: e.date, default=None)
    by_id = {q.id: q for q in edition.quotes}
    candidatos = []
    for item in quotes:
        q = by_id.get(item["id"])
        ponto = estatistica.ponto_da_cotacao(q) if q is not None and q.change_pct is not None else None
        if ponto is None or not item["change"] or not item["ref"] or item["id"] in evitar:
            continue
        if abs(q.change_pct) < TERMINAL_VAR_MIN or estatistica.leitura_velha(q.id, ponto, day, anterior):
            continue
        if election and estatistica.mercado_eleitoral(edition, q.id, nomes):
            continue
        serie = [x for x in estatistica.serie(antes, q.id) if x.as_of < ponto.as_of]
        escala = estatistica.escala_robusta(serie)
        if escala and abs(q.change_pct) / escala < estatistica.Z_MEDIO:
            continue
        candidatos.append((abs(q.change_pct), item))
    if not candidatos:
        pregao = day.weekday() < 5 and day not in estatistica.FERIADOS["ibov"]
        n = f"{len(quotes)} indicadores"
        return f"{n} {'antes da abertura' if pregao else 'em dia sem pregão'}", n, ""
    _, q = max(candidatos, key=lambda c: c[0])
    onde = q["ref"].replace("fech.", "no fechamento de", 1) if q["ref"].startswith("fech.") else f"na {q['ref']}"
    return f"{q['label']} {q['change']} {onde}", q["change"], q["id"]


def terminal(
    edition: Edition,
    brand: dict[str, Any],
    *,
    evitar: Iterable[str] = (),
    history: Sequence[Edition] = (),
    election: bool = False,
    nomes: Iterable[str] = estatistica.NOMES_ELEICAO,
) -> Peca | None:
    """Imagem única com os indicadores (07:00 dos dias sem IA, ou quando o Em 1 minuto não sai).

    ``evitar``: ativos que já são o assunto de outro post do dia (o do Número, o do título do
    Mercado em linha) e por isso não viram o título daqui. ``history`` (com ou sem a edição do dia):
    a escala de cada ativo e a edição anterior, para o título só ir para uma variação fora do comum
    (:func:`_achado_terminal`). ``election``/``nomes``: o filtro eleitoral do título.
    """
    from .formatos.mercado import faixa_juros  # o mercado importa daqui

    quotes = quote_items(edition, [q.id for q in edition.quotes])
    if len(quotes) < 4:
        return None
    day = edition_day(edition)
    titulo, destaque, achado = _achado_terminal(quotes, edition, evitar, history, election=election, nomes=nomes)
    for q in quotes:
        q["achado"] = q["id"] == achado
    juros = faixa_juros(edition)
    linhas = [
        f"▸ {q['label']}: {q['value']}" + (f" ({', '.join(x for x in (q['change'], q['ref']) if x)})" if q["change"] or q["ref"] else "") + "."
        for q in quotes if q["id"] in TERMINAL_MERCADO
    ]
    if juros:
        linhas.append(f"▸ {juros[0].upper()}{juros[1:]}.")
    caption = finish_caption(
        [
            f"Terminal da manhã · {short_date(day)}: {titulo}",
            "\n".join(
                linhas
                + [
                    "Como medimos: a última leitura de cada indicador (Yahoo Finance e Banco Central), em horário de "
                    "Brasília; fech. = fechamento do pregão.",
                    TERMINAL_CTA,
                    "#triangula #dados #mercadofinanceiro #economia",
                ]
            ),
        ]
    )
    return Peca(
        slot="manha",
        kind="image",
        title="Terminal da manhã",
        slides=[
            Slide(
                "terminal",
                {
                    "selo": f"TERMINAL DA MANHÃ · {kicker_date(day)}",
                    "titulo": titulo,
                    "destaque": destaque,
                    "quotes": quotes,
                    "cta": TERMINAL_CTA,
                    "nota": "fonte: Yahoo Finance e BCB",
                    "section": "mercados",
                },
                alt=f"Terminal da manhã, {short_date(day)}: {titulo}. " + quote_sentence(quotes, [q["id"] for q in quotes]) + ".",
            )
        ],
        caption=caption,
    )
