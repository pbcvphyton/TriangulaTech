"""Testes do e-mail diário (qijournal/render/email.py)."""

from __future__ import annotations

import copy
import re

import pytest

from qijournal import text
from qijournal.render.email import render_email, story_url
from tests.fixtures.render.helpers import LEAD_ID, MALICIOUS_ID, load_edition, parse, pbcv_config, default_config

BASE = "https://pbcvphyton.github.io/TriangulaTech/"
PAGE = BASE + "edicoes/2026-09-29.html"  # links das matérias: cópia arquivada da edição


@pytest.fixture(scope="module")
def config():
    return default_config()


@pytest.fixture(scope="module")
def edition():
    return load_edition()


@pytest.fixture(scope="module")
def rendered(edition, config):
    return render_email(edition, config)


def linked_story_ids(html: str) -> list[str]:
    """Ids das matérias com título linkado (a manchete aparece em <h1>, as demais em <h2>)."""
    return re.findall(r'<h[12][^>]*><a href="' + re.escape(PAGE) + r'#s-([^"]+)"', html)


# ── assunto ──────────────────────────────────────────────────────────────────


def expected_subject(edition, brand: str = "Triangula") -> str:
    lead = text.truncate(edition.story(edition.lead).headline, 70)
    return f"{brand} · 29/09/2026: {lead}"


def test_default_subject(rendered, edition):
    """O assunto traz o gancho do dia (a manchete, até 70 caracteres)."""
    subject, _, _ = rendered
    assert subject == expected_subject(edition)
    assert subject.startswith("Triangula · 29/09/2026: Arrecadação federal bate recorde")
    assert len(subject) <= len("Triangula · 29/09/2026: ") + 71


def test_subject_without_lead_and_with_line_breaks(edition):
    cfg = default_config()
    ed = copy.deepcopy(edition)
    ed.story(ed.lead).headline = "Linha um\nlinha dois"
    assert render_email(ed, cfg)[0] == "Triangula · 29/09/2026: Linha um linha dois"
    ed.lead = "nao-existe"
    assert render_email(ed, cfg)[0] == "Triangula · 29/09/2026: Terça-feira, 29 de setembro de 2026"


def test_custom_subject_template_with_short_date(edition):
    cfg = default_config()
    cfg.email.subject_template = "[{brand}] Edição de {date}"
    assert render_email(edition, cfg)[0] == "[Triangula] Edição de 29/09/2026"


@pytest.mark.parametrize("template", ["{brand} {desconhecido}", "{0}", "{brand"])
def test_invalid_subject_template_falls_back(edition, template):
    cfg = default_config()
    cfg.email.subject_template = template
    assert render_email(edition, cfg)[0] == expected_subject(edition)


def test_subject_has_no_line_breaks(edition):
    cfg = default_config()
    cfg.email.subject_template = "{brand}\r\nBcc: alguem@example.com\n{date_label}"
    subject = render_email(edition, cfg)[0]
    assert "\n" not in subject and "\r" not in subject


# ── HTML (o modelo do e-mail do TSA Tech) ──────────────────────────────────


def test_html_is_email_safe(rendered):
    _, html, _ = rendered
    assert len(html.encode("utf-8")) < 20 * 1024  # enxuto: bem abaixo dos 40 KB da rotina do Gmail
    lowered = html.lower()
    assert "<script" not in lowered and "<svg" not in lowered
    assert "fonts.googleapis" not in lowered and "@import" not in lowered
    assert "**" not in html
    assert 'max-width:600px' in html and '<table role="presentation" width="600" align="center"' in html  # Outlook
    assert '<meta name="color-scheme" content="light only">' in html  # como o TSA Tech: sempre claro
    structure = parse(html)
    assert structure.errors == [] and structure.stack == []


def test_html_uses_inline_styles_for_layout(rendered):
    _, html, _ = rendered
    # O <style> é só melhoria progressiva (celular): todo bloco de conteúdo tem estilo inline.
    for tag, attrs in parse(html).tags:
        if tag in ("td", "p", "h1", "h2", "a"):
            assert attrs.get("style"), (tag, attrs)


def test_preheader_is_hidden_and_plain(rendered):
    _, html, _ = rendered
    pre = re.search(r'<div style="display:none;[^"]*">(.*?)&#847;', html, re.S).group(1)
    assert pre.startswith("A cinco dias do primeiro turno, o mercado embute prêmio eleitoral")
    assert "*" not in pre and "<" not in pre


def test_header_band_tagline_and_date(rendered):
    _, html, _ = rendered
    # "Abrir no navegador" (a cópia arquivada) e a faixa com o logo sobre o marinho do topo do site
    assert f'<a href="{PAGE}" style="color:#5f6b7e;text-decoration:underline;">Abrir no navegador</a>' in html
    assert (
        '<td align="center" bgcolor="#273351" style="padding:0 0 20px;background:#273351;border-bottom:3px solid #1B2745;">'
        in html
    )
    assert (
        f'<a href="{BASE}" style="text-decoration:none;"><img src="{BASE}assets/triangula-cabecalho-email.jpg"'
        ' width="600" height="132" alt="Triangula"' in html
    )
    assert "letter-spacing:3px;text-transform:uppercase;color:#D4D6DC;\">Seu terminal financeiro diário</p>" in html
    assert ">Terça-feira, 29 de setembro de 2026</p>" in html
    # nada dos blocos antigos: cotações, clima, editorial, "Em 1 minuto", racional
    for antigo in ("R$ 5,22", "São Paulo</b>", "Em 1 minuto", "Como foi compilada", "font-style:italic"):
        assert antigo not in html, antigo


def test_today_in_six_stories_with_the_lead_first(rendered, edition):
    _, html, _ = rendered
    assert ">Hoje em 6 notícias</td>" in html
    ids = linked_story_ids(html)
    assert ids[0] == LEAD_ID and len(ids) == 6 and len(set(ids)) == 6
    lead = edition.story(LEAD_ID)
    assert re.search(r'<h1 class="r1" style="margin:0 0 10px;font:bold 28px/34px Georgia', html)
    assert f'<a href="{PAGE}#s-{LEAD_ID}" style="color:#1a2332;text-decoration:none;">{lead.headline}</a></h1>' in html
    assert '<span style="color:#1a2332;">1</span>&nbsp;·&nbsp;Brasil · Economia</p>' in html
    assert f"{lead.dek}</p>" in html
    # depois da manchete: uma linha de duas colunas e uma de três
    assert html.count('<div class="col" style="display:inline-block;width:49.9%;') == 2
    assert html.count('<div class="col" style="display:inline-block;width:34.1%;') == 2
    assert html.count('<div class="col" style="display:inline-block;width:31.7%;') == 1
    assert re.search(r'>\d+ veículos?</p>', html)


def test_stories_follow_the_front_page_and_vary_the_sections(rendered, edition):
    _, html, _ = rendered
    ids = linked_story_ids(html)[1:]
    assert ids[:3] == list(edition.secondary)  # as chamadas da primeira página primeiro
    section_of = {s.id: s.section for s in edition.stories}
    secoes = [section_of[i] for i in [LEAD_ID, *ids]]
    assert len(set(secoes)) == len(secoes)  # uma notícia por editoria (a da manchete conta)


def test_max_stories_zero_keeps_only_the_lead(edition):
    cfg = default_config()
    cfg.email.max_stories = 0
    _, html, text = render_email(edition, cfg)
    assert linked_story_ids(html) == [LEAD_ID] and ">Hoje em 1 notícia</td>" in html
    assert text.count("#s-") == 1 and "PRINCIPAIS NOTÍCIAS" not in text


def test_more_stories_go_in_rows_of_three(edition):
    cfg = default_config()
    cfg.email.max_stories = 8
    _, html, _ = render_email(edition, cfg)
    assert ">Hoje em 9 notícias</td>" in html  # 1 + 2 + 3 + 3
    assert html.count('width:49.9%;') == 2 and html.count('width:34.1%;') == 2 * 2 and html.count('width:31.7%;') == 2


def test_cta_and_footer(rendered):
    _, html, _ = rendered
    assert f'<a href="{BASE}" style="color:#1a2332;text-decoration:underline;">Ler a edição completa &rarr;</a>' in html
    assert (
        "Triangula · Seu terminal financeiro diário · 64 fontes · "
        f'<a href="{BASE}edicoes/" style="color:#5f6b7e;text-decoration:underline;">Edições anteriores</a>' in html
    )


def test_all_news_link_when_the_edition_has_the_full_list(edition):
    from qijournal.models import IndexItem, IndexTopic

    ed = copy.deepcopy(edition)
    ed.index = [IndexTopic(section="brasil", title="T", items=[IndexItem(source="Folha", title=f"N {i}", url=f"https://x.com/{i}") for i in range(3)])]
    _, html, text = render_email(ed, default_config())
    assert f'<a href="{BASE}edicoes/2026-09-29-todas.html" style="color:#5f6b7e;text-decoration:underline;">3 notícias hoje</a>' in html
    assert f"Todas as 3 notícias do dia: {BASE}edicoes/2026-09-29-todas.html" in text


def test_malicious_story_is_escaped_in_email(edition):
    cfg = default_config()
    cfg.email.max_stories = 50
    _, html, text = render_email(edition, cfg)
    assert MALICIOUS_ID in linked_story_ids(html)
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "Teste &lt;script&gt;alert(1)&lt;/script&gt; &amp; &#34;aspas&#34; no título" in html
    assert "javascript:" not in html.lower() and "javascript:" not in text.lower()
    assert len(html.encode("utf-8")) <= 40 * 1024


def test_without_editorial_the_preheader_uses_the_lead_dek(edition, config):
    ed = copy.deepcopy(edition)
    ed.editorial, ed.briefing, ed.quotes, ed.weather = "", [], [], []
    ed.mode, ed.model = "heuristic", None
    _, html, text = render_email(ed, config)
    assert re.search(r'<div style="display:none;[^"]*">Receita somou R\$ 212,4 bilhões', html)
    assert "Edição automática (sem IA)" in text


def test_pbcv_brand_email_uses_text_wordmark(edition):
    subject, html, _ = render_email(edition, pbcv_config())
    assert subject.startswith("PBCV Advogados · 29/09/2026: ")
    assert "<svg" not in html.lower() and "<img" not in html
    assert '<span style="color:#A3B4E0;">PBCV</span>' in html
    assert "border-top:3px solid #1B2745;" in html  # o cartão com a régua na cor da marca


def test_email_header_falls_back_to_the_logo_and_then_to_text(edition):
    cfg = default_config()
    cfg.brand.email_header = {"src": "javascript:alert(1)", "width": 600, "height": 132}
    _, html, _ = render_email(edition, cfg)
    assert "javascript:" not in html
    assert f'<img src="{BASE}assets/triangula-marca-email.png" width="248" height="64" alt="Triangula"' in html
    cfg.brand.email_logo = {"src": "javascript:alert(1)", "width": 212, "height": 44}
    _, html, _ = render_email(edition, cfg)
    assert "<img" not in html and '<span style="color:#FFFFFF;font-weight:900;">Triangula</span>' in html


def test_solid_masthead_email_uses_blended_tones(edition):
    cfg = default_config()
    cfg.brand.colors["masthead"] = "#3322CC"
    cfg.brand.colors["on_masthead"] = "#F5F3ED"
    _, html, _ = render_email(edition, cfg)
    assert '<td align="center" bgcolor="#3322CC" style="padding:0 0 20px;background:#3322CC;' in html
    assert 'text-transform:uppercase;color:#CEC9E6;">Seu terminal financeiro diário</p>' in html  # 80% sobre o azul
    assert "border-top:3px solid #3322CC;" in html


# ── texto puro ───────────────────────────────────────────────────────────────


def test_text_version_is_plain_and_complete(rendered, edition):
    _, html, text = rendered
    assert "**" not in text
    assert not re.search(r"</?(strong|a|p|b|span|div|br)\b", text)
    assert text.startswith("TRIANGULA\nTerça-feira, 29 de setembro de 2026 · 05:07 BRT\n")
    for heading in ("MANCHETE", "PRINCIPAIS NOTÍCIAS"):
        assert f"\n{heading}\n{'-' * len(heading)}\n" in text
    for antigo in ("MERCADOS\n", "CLIMA\n", "EDITORIAL\n", "EM 1 MINUTO"):
        assert antigo not in text
    assert f"Ler na edição: {PAGE}#s-{LEAD_ID}" in text
    # As mesmas notícias do HTML, com links por extenso.
    for story_id in linked_story_ids(html):
        assert f"{PAGE}#s-{story_id}" in text
    assert f"Abrir edição completa: {BASE}\n" in text
    assert f"Edições anteriores: {BASE}edicoes/\n" in text
    assert text.endswith("(claude-opus-5-5)\n")
    assert "Gerado automaticamente em 05:07 (BRT) · Edição gerada por IA (claude-opus-5-5)" in " ".join(text.split())


def test_text_lines_are_wrapped(rendered):
    _, _, text = rendered
    long_lines = [ln for ln in text.splitlines() if len(ln) > 72 and "https://" not in ln]
    assert long_lines == []


def test_story_url_encodes_unexpected_characters():
    assert story_url(BASE, "copom-mantem-selic") == f"{BASE}#s-copom-mantem-selic"
    assert story_url(BASE, 'a b"<c') == f"{BASE}#s-a%20b%22%3Cc"


# ── orçamento e regressões ──────────────────────────────────────────────────


def test_email_is_slim_and_shows_at_most_three_sources(edition):
    from qijournal.models import SourceRef

    cfg = default_config()
    inflated = copy.deepcopy(edition)
    for story in inflated.stories:
        story.sources = [SourceRef(name=f"Veículo {i}", url=f"https://v{i}.example.com/{story.id}") for i in range(10)]
    _, html, _ = render_email(inflated, cfg)
    assert len(html.encode("utf-8")) <= 40 * 1024
    assert "Veículo 3" not in html and "Veículo 0 · Veículo 1 · Veículo 2 · +7" in html  # manchete
    assert ">10 veículos</p>" in html  # demais notícias: só a contagem
    assert "\n  <" not in html  # sem indentação do template


def test_email_budget_drops_stories_until_it_fits(edition, monkeypatch):
    from qijournal.render import email as email_module

    cfg = default_config()
    tamanho = len(render_email(edition, cfg)[1].encode("utf-8"))
    monkeypatch.setattr(email_module, "MAX_EMAIL_BYTES", tamanho - 1)
    _, html, text = render_email(edition, cfg)
    assert len(html.encode("utf-8")) <= tamanho - 1
    shown = linked_story_ids(html)
    assert shown[0] == LEAD_ID and 1 <= len(shown) - 1 < cfg.email.max_stories
    assert text.count("#s-") == len(shown)  # texto puro com as mesmas notícias


def test_english_story_is_marked_in_the_email(edition, config):
    ed = copy.deepcopy(edition)
    ed.story(LEAD_ID).lang = "en"
    _, html, _ = render_email(ed, config)
    assert re.search(r'<h1 class="r1"[^>]*lang="en"', html)
