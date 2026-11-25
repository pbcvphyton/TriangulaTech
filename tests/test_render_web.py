"""Testes da página web da edição e do índice do arquivo (qijournal/render/web.py).

A página da edição sai duas vezes: a home (``index.html``), em três andares com teto, e a
cópia arquivada (``edicoes/AAAA-MM-DD.html``), que leva também as gavetas de detalhe.
"""

from __future__ import annotations

import copy
import html as html_lib
import re

import pytest

from qijournal.models import Section, SourceRef, Story
from qijournal.render.web import (
    RECENTES_NO_CELULAR,
    TETO_CHAMADAS,
    TETO_DIVERGENCIAS,
    TETO_RECENTES,
    TITULO_CURTO,
    TITULO_MEDIO,
    build_view,
    render_archive_index,
    render_edition_page,
)
from tests.fixtures.render.helpers import (
    LEAD_ID,
    MALICIOUS_ID,
    load_edition,
    parse,
    pbcv_config,
    default_config,
    visible_text,
)


@pytest.fixture(scope="module", autouse=True)
def _vies_com_amostra_pequena():
    """As edições inventadas destes testes têm poucas análises: a amostra mínima da linha
    do viés cai para 1 aqui (a regra é conferida em tests/test_termometro.py)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("qijournal.render.termometro.VIES_MIN_NOTICIAS", 1)
        yield


@pytest.fixture(scope="module")
def config():
    return default_config()


@pytest.fixture(scope="module")
def edition():
    return load_edition()


@pytest.fixture(scope="module")
def page(edition, config):
    return render_edition_page(edition, config, home_href="./", archive_href="edicoes/")


@pytest.fixture(scope="module")
def copia(edition, config):
    """A cópia arquivada (edicoes/AAAA-MM-DD.html) da mesma edição."""
    return render_edition_page(edition, config, home_href="../", archive_href="./", is_archive=True)


def render(edition, config=None, **kwargs):
    kwargs.setdefault("home_href", "./")
    kwargs.setdefault("archive_href", "edicoes/")
    return render_edition_page(edition, config or default_config(), **kwargs)


def arquivada(edition, config=None):
    return render_edition_page(edition, config or default_config(), home_href="../", archive_href="./", is_archive=True)


def trecho(page_html: str, inicio: str, *fins: str) -> str:
    """Do marcador ``inicio`` até o primeiro dos marcadores ``fins`` que aparecer depois dele."""
    a = page_html.index(inicio)
    cortes = [page_html.index(fim, a + len(inicio)) for fim in fins if fim in page_html[a + len(inicio) :]]
    return page_html[a : min(cortes)] if cortes else page_html[a:]


def andar_das_noticias(page_html: str) -> str:
    """A manchete e "Em 1 minuto" (o primeiro andar da home)."""
    return trecho(page_html, '<div class="capa', '<section class="bl md"', '<details class="gav', "</main>")


def bloco_da_midia(page_html: str) -> str:
    return trecho(page_html, '<section class="bl md"', "</section>")


def bloco_das_materias(page_html: str) -> str:
    return trecho(page_html, '<details class="gav tabs-wrap all" id="editorias">', "</details>")


# ── documento ────────────────────────────────────────────────────────────────


def test_document_basics(page):
    assert page.startswith("<!DOCTYPE html>")
    assert '<html lang="pt-BR"' in page
    assert "<title>Triangula — Terça-feira, 29 de setembro de 2026</title>" in page
    assert '<meta name="viewport" content="width=device-width,initial-scale=1">' in page
    assert len(page.encode("utf-8")) < 250 * 1024


def test_html_is_well_formed_with_unique_ids(page):
    structure = parse(page)
    assert structure.errors == []
    assert structure.stack == []
    duplicated = {k: n for k, n in structure.ids.items() if n > 1}
    assert duplicated == {}


def test_page_is_self_contained(page):
    structure = parse(page)
    scripts = [attrs for tag, attrs in structure.tags if tag == "script"]
    assert len(scripts) == 2 and all("src" not in attrs for attrs in scripts)
    assert "three" not in page.lower() and "gsap" not in page.lower()
    stylesheets = [attrs["href"] for tag, attrs in structure.tags if tag == "link" and attrs.get("rel") == "stylesheet"]
    assert stylesheets and all(href.startswith("https://fonts.googleapis.com/") for href in stylesheets)
    assert '<canvas id="mast-canvas"' in page


def test_meta_and_open_graph(page, edition):
    lead = edition.story(LEAD_ID)
    assert f'<meta property="og:image" content="{lead.image}">' in page
    assert '<meta property="og:title" content="Triangula — Terça-feira, 29 de setembro de 2026">' in page
    description = re.search(r'<meta name="description" content="([^"]*)"', page).group(1)
    assert description.startswith("A cinco dias do primeiro turno, o mercado embute prêmio eleitoral")
    assert re.search(r'<meta property="og:description" content="A cinco dias', page)
    assert '<link rel="canonical" href="https://pbcvphyton.github.io/TriangulaTech/edicoes/2026-09-29.html">' in page
    assert '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,' in page


def test_description_falls_back_to_lead_dek_without_editorial(edition, config):
    ed = copy.deepcopy(edition)
    ed.editorial = ""
    html = render(ed, config)
    dek = ed.story(LEAD_ID).dek
    assert f'<meta property="og:description" content="{dek}">' in html


# ── segurança e limpeza de conteúdo ──────────────────────────────────────────


def test_malicious_story_is_escaped(page):
    assert "<script>alert" not in page
    assert "Teste &lt;script&gt;alert(1)&lt;/script&gt; &amp; &#34;aspas&#34; no título" in page
    assert "<img src=x" not in page and "&lt;img src=x onerror=alert(2)&gt;" in page
    assert "<iframe" not in page
    assert "&lt;b&gt;crua&lt;/b&gt;" in page
    assert "Fonte &lt;i&gt;Maliciosa&lt;/i&gt;" in page
    assert "javascript:" not in page.lower()


def test_malicious_source_url_is_not_linked_but_valid_one_is(page):
    modal = re.search(rf'<div class="mo" id="s-{MALICIOUS_ID}".*?</article>', page, re.S).group(0)
    assert "<span>Fonte &lt;i&gt;Maliciosa&lt;/i&gt;</span>" in modal
    assert '<a href="https://example.com/materia?a=1&amp;b=2" target="_blank" rel="noopener noreferrer">' in modal
    assert "<figure" not in modal  # imagem javascript: descartada


def test_no_markdown_leaks_anywhere(page):
    assert "**" not in page
    assert "*" not in visible_text(page)


def test_no_empty_headings(page):
    headings = parse(page).headings
    assert len(headings) > 50
    assert all(re.search(r"\w", text) for _, text in headings), [h for h in headings if not re.search(r"\w", h[1])]
    assert not re.search(r"<h[1-6][^>]*>\s*:?\s*</h[1-6]>", page)


def test_bold_is_rendered_as_strong(page):
    assert "<strong>R$ 212,4 bilhões</strong>" in page
    assert "<strong>Supremo Tribunal Federal</strong>" in page
    # o editorial saiu da capa: fica só na descrição da página, sem marcação
    assert "<strong>prêmio eleitoral</strong>" not in page
    assert re.search(r'<meta name="description" content="[^"]*o mercado embute prêmio eleitoral', page)


# ── estrutura da edição ──────────────────────────────────────────────────────


def test_every_story_has_a_modal_anchor_and_a_link(page, edition):
    structure = parse(page)
    for story in edition.stories:
        assert structure.ids[f"s-{story.id}"] == 1, story.id
        assert f"#s-{story.id}" in structure.hrefs, story.id
    modals = [attrs for tag, attrs in structure.tags if tag == "div" and attrs.get("class") == "mo"]
    assert len(modals) == len(edition.stories)
    assert all(m.get("role") == "dialog" and m.get("aria-modal") == "true" for m in modals)


def test_modal_content(page):
    modal = re.search(
        r'<div class="mo" id="s-stf-forma-maioria-para-limitar-cobranca-retroativa-do-difal".*?</article>', page, re.S
    ).group(0)
    assert 'aria-labelledby="t-stf-forma-maioria-para-limitar-cobranca-retroativa-do-difal"' in modal
    assert "<h3>Por que importa</h3>" in modal
    assert "<strong>Supremo Tribunal Federal</strong>" in modal
    assert (
        'href="https://www.jota.info/tributos/stf-difal-modulacao" target="_blank" rel="noopener noreferrer"' in modal
    )
    # publicado no dia anterior ao da edição: a hora ganha "ontem" (09:00 numa edição das 05:07 parecia futuro)
    assert "Publicado: <time" in modal and "ontem, 23:07</time> BRT · " in modal
    assert '<time datetime="2026-09-29T02:07:00+00:00" data-age>há 6 h</time>' in modal
    assert '<button class="mc2" type="button">&larr; Voltar à edição</button>' in modal


def test_modal_abre_na_tela_inteira_em_qualquer_tela(page):
    # sem margem em volta nem largura máxima da caixa (antes: 700 px no computador e no tablet);
    # o texto fica numa coluna de leitura no meio da tela
    assert ".mo{display:none;position:fixed;inset:0;z-index:1000;overflow-y:auto;overscroll-behavior:contain;padding:0;" in page
    assert ".ml{position:relative;width:100%;min-height:100%;padding:56px 40px 64px;" in page
    assert ".ml>:not(.mc){max-width:720px;margin-left:auto;margin-right:auto}" in page
    assert "max-width:700px" not in page


def test_manchete_sem_corpo_com_foto_e_linha_de_servico(page, edition, config):
    lead = edition.story(LEAD_ID)
    manchete = trecho(page, '<article class="man ', "</article>")
    assert f'<h2><a class="hit" href="#s-{LEAD_ID}">{lead.headline}</a></h2>' in manchete
    assert f'<p class="man-dek">{lead.dek}</p>' in manchete
    # a foto da manchete já está na primeira tela: não espera a rolagem para carregar
    assert f'<figure class="man-f" data-img><img src="{lead.image}" alt="" decoding="async" referrerpolicy="no-referrer"></figure>' in manchete
    # nenhum parágrafo do corpo, nenhuma lista de veículos, nenhuma barra: o texto fica na janela
    assert manchete.count("<p") == 3  # editoria, linha fina e linha de serviço
    assert lead.body[0][:40] not in manchete and "Valor Econômico" not in manchete and "cov-bar" not in manchete
    assert '<p class="sv"><span>4 veículos</span><span class="sv-s"> · </span><time datetime="2026-09-29T05:55:00+00:00" data-age>há 2 h</time></p>' in manchete
    view = build_view(edition, config)
    assert [(i.tipo, i.texto) for i in view.manchete.servico] == [("veiculos", "4 veículos"), ("idade", "há 2 h")]


def test_tamanho_do_titulo_da_manchete_vem_do_comprimento(edition, config):
    """O título nunca é cortado: a classe de letra (calculada aqui, não no navegador) diminui."""
    ed = copy.deepcopy(edition)
    lead = ed.story(LEAD_ID)
    for titulo, classe in (("T" * TITULO_CURTO, "curto"), ("T" * TITULO_MEDIO, "medio"), ("T" * (TITULO_MEDIO + 1), "longo")):
        lead.headline = titulo
        assert build_view(ed, config).manchete.tamanho == classe
        html = render(ed, config)
        assert f'<article class="man {classe}"' in html and f">{titulo}</a></h2>" in html
    lead.image = None
    assert '<article class="man longo noimg"' in render(ed, config)


def test_em_1_minuto_tem_as_secundarias_e_os_primeiros_destaques(page, edition, config):
    """Lista numerada única: as 3 secundárias e os 2 primeiros destaques, cada uma com o
    título que abre a janela e a linha fina (que o CSS mostra só em tela larga)."""
    view = build_view(edition, config)
    esperadas = [*edition.secondary, *edition.highlights[:2]]
    assert [c.story.id for c in view.chamadas] == esperadas and len(esperadas) == TETO_CHAMADAS == 5
    lista = trecho(page, '<ol class="um-l">', "</ol>")
    assert re.findall(r'<h3><a class="hit" href="#s-([^"]+)"', lista) == esperadas
    assert lista.count("<li>") == 5 and lista.count('<p class="um-dk">') == 5
    um = trecho(page, '<section class="um"', "</section>")
    assert '<h2 class="rot" id="um-h">Em 1 minuto</h2>' in um
    assert f'<p class="mais"><a href="#editorias">Todas as {len(edition.stories)} matérias</a></p>' in um
    # sem divergência na edição de exemplo: as chamadas não têm linha de serviço
    assert 'class="sv"' not in um
    assert ".um-dk{display:none;" in page and ".um-dk{display:block}" in page  # linha fina só no computador


def test_cada_materia_aparece_uma_unica_vez_no_andar_das_noticias(page, edition, config):
    andar = andar_das_noticias(page)
    links = re.findall(r'href="#s-([^"]+)"', andar)
    assert links == [LEAD_ID, *edition.secondary, *edition.highlights[:2]]
    assert len(links) == len(set(links)) == 1 + TETO_CHAMADAS
    for story_id in links:  # e o título de cada uma existe uma vez só no código do andar
        assert andar.count(edition.story(story_id).headline) == 1
    # nada do que saiu da capa: editorial, resumo em prosa, "Mais para ler", "Destaques", Radar e barras
    for sumiu in ("Editorial do dia", "Mais para ler", "Destaques", "Radar de", "cov-bar", "fp-", 'class="hero', 'class="dst"'):
        assert sumiu not in andar, sumiu
    assert "A cinco dias do primeiro turno" not in visible_text(page).split("</head>")[1]  # o editorial só na descrição
    assert "sec-nav" not in page and 'class="wx"' not in page


def test_em_1_minuto_completa_com_as_proximas_materias_na_ordem_das_secoes(edition, config):
    ed = copy.deepcopy(edition)
    ed.secondary, ed.highlights = ed.secondary[:1], ed.highlights[:1]
    view = build_view(ed, config)
    usadas = [view.lead.id, ed.secondary[0], ed.highlights[0]]
    resto = [s.id for s in view.stories if s.id not in usadas]
    assert [c.story.id for c in view.chamadas] == [ed.secondary[0], ed.highlights[0], *resto[:3]]
    # edição com menos matérias que o teto: mostra as que há, sem título órfão nem link para o resto
    pequena = copy.deepcopy(edition)
    manter = {LEAD_ID, pequena.secondary[0]}
    pequena.stories = [s for s in pequena.stories if s.id in manter]
    html = render(pequena, config)
    assert trecho(html, '<ol class="um-l">', "</ol>").count("<li>") == 1 and 'class="mais"><a href="#editorias"' not in html
    so_manchete = copy.deepcopy(edition)
    so_manchete.stories = [s for s in so_manchete.stories if s.id == LEAD_ID]
    html = render(so_manchete, config)
    assert '<div class="capa solo">' in html and 'id="um-h"' not in html and "A matéria da edição" in html
    assert parse(html).errors == []


def test_bloco_recolhido_das_materias_com_abas_comecando_em_todas(page, edition):
    """O único bloco recolhido da home: fechado, uma linha; dentro, a busca do dia, as abas
    (a primeira é "Todas", aberta) e todos os cartões."""
    gavetas = [attrs for tag, attrs in parse(page).tags if tag == "details"]
    assert [g.get("id") for g in gavetas] == ["editorias", None]  # este e o estado das fontes, no rodapé
    assert not any("open" in g for g in gavetas)
    assert f'<summary><h2 class="gav-t">Todas as {len(edition.stories)} matérias, por editoria</h2></summary>' in page
    bloco = bloco_das_materias(page)
    assert " open" not in bloco.split(">", 1)[0]
    tabs = re.findall(r'<button class="tab-btn"[^>]*data-tab="([^"]+)"[^>]*>([^<]+)<', bloco)
    assert [t[0] for t in tabs] == ["all"] + [s.id for s in edition.sections]
    assert tabs[0][1] == "Todas"
    titles = [html_lib.unescape(t[1]) for t in tabs[1:]]
    assert titles == [s.title for s in edition.sections]  # nomes completos, sem truncar
    selecionadas = re.findall(r'data-tab="([^"]+)"[^>]*aria-selected="true"', bloco)
    assert selecionadas == ["all"]
    assert bloco.count('<div class="tab-pane active"') == len(edition.sections)
    assert bloco.count('<article class="cl"') == len(edition.stories)
    assert '<form class="srch" role="search" action="edicoes/pesquisa.html" method="get">' in bloco
    assert "Radar" not in bloco and "radar" not in bloco  # sem a segunda cópia do Radar
    # fechado, o bloco não deixa nada por dentro; o atalho "/" abre o bloco antes de focar a busca
    assert ".gav:not([open])>:not(summary){display:none}" in page
    assert "var gaveta = input.closest ? input.closest('details') : null;" in page and "if (gaveta) gaveta.open = true;" in page


def test_cartoes_so_com_editoria_titulo_e_linha_fina(page, edition):
    bloco = bloco_das_materias(page)
    pane = trecho(bloco, 'id="pane-brasil"', '<div class="tab-pane')
    cards = re.findall(r'<article class="cl".*?</article>', pane, re.S)
    assert len(cards) == 4
    lead = edition.story(LEAD_ID)
    assert '<p class="cat">Brasil · Economia</p>' in cards[0]
    assert f'<h3><a class="hit" href="#s-{LEAD_ID}">{lead.headline}</a></h3>' in cards[0]
    assert f'<p class="dk">{lead.dek}</p>' in cards[0]
    for card in cards:  # foto, veículos, hora e barra ficam na janela da matéria
        assert "<figure" not in card and "<time" not in card and "Valor Econômico" not in card and "cov" not in card
        assert card.count("<p") == 2


def test_linha_de_indicadores_parada_com_o_clima_no_fim(page):
    linha = trecho(page, '<section class="tk"', "</section>")
    assert 'Dólar <b>R$ 5,22</b> <span class="u">+0,19%</span>' in linha
    assert 'Ibovespa <b>182.991 pts</b> <span class="d">-0,27%</span>' in linha
    assert re.search(r"Selic <b>13,75%</b></li>", linha)  # indicador sem variação
    # cotações paradas: sem botão de pausa, sem animação; arrasta-se para o lado
    assert "tk-pause" not in page and "tk-scroll" not in page and "animation" not in trecho(page, ".tk{", ".kicker{")
    assert '<div class="tk-rail" tabindex="0" role="group"' in linha and ".tk-rail{flex:1 1 auto;min-width:0;overflow-x:auto;" in page
    # o clima é o último item da mesma linha: só a primeira cidade, sem emoji; a faixa própria acabou
    assert linha.index("</ul>") < linha.index('<p class="tk-wx"')
    clima = trecho(linha, '<p class="tk-wx"', "</p>")
    assert "São Paulo <b>19°C</b>" in clima and "19°" in clima and "33°" in clima
    assert 'title="Agora: Nublado. Amanhã: Pancadas de chuva fracas, de 19° a 30°."' in clima
    assert "☁" not in linha and "🌦" not in linha and 'role="img"' not in linha
    # Montevidéu foi coletada nesse dia, mas saiu da configuração
    assert "Montevidéu" not in page and 'class="wx"' not in page
    # no celular o clima não aparece
    assert ".tk-wx{flex:none;display:none;" in page and "@media (min-width:641px){.tk-wx{display:flex}}" in page


def test_o_clima_segue_as_cidades_da_configuracao(edition):
    from qijournal.config import CityConfig

    cfg = default_config()
    cfg.weather = [CityConfig(name="Montevidéu", lat=-34.9, lon=-56.16, timezone="America/Montevideo"), *cfg.weather]
    view = build_view(edition, cfg)
    assert view.clima.city == "São Paulo"  # a primeira das configuradas, na ordem em que a edição as coletou
    cfg.weather = [CityConfig(name="Montevidéu", lat=-34.9, lon=-56.16, timezone="America/Montevideo")]
    html = render(edition, cfg)
    assert "Montevidéu <b>14°C</b>" in trecho(html, '<p class="tk-wx"', "</p>") and "São Paulo" not in trecho(html, '<section class="tk"', "</section>")
    sem = copy.deepcopy(edition)
    sem.weather = []
    assert "tk-wx" not in render(sem, default_config()).split("</style>")[1]


def test_header_dateline_and_tools(page):
    # "·" colado na data e hora/fuso inseparáveis: no celular nenhuma linha começa com "·" nem fica só com "BRT"
    assert 'Terça-feira, 29 de setembro de 2026&nbsp;· <span class="nw">05:07 BRT</span>' in page
    assert '<header class="mast-wrap solid">' in page  # bloco marinho, como o topo do TSA Tech
    assert '<h1 class="logo mono"><a href="./"><svg' in page  # letreiro em currentColor (on_masthead)
    assert '<title id="triangula-logo-title">Triangula</title>' in page
    assert "dk-triangula-logo-title" not in page  # um só logo: o bloco é escuro nos dois modos
    assert 'class="q"' not in page  # wordmark de texto não é usado quando há logo
    assert 'id="dmBtn"' in page and 'id="srchInput"' in page
    # "Buscar" leva à Pesquisa analítica (todas as edições); Enter na caixa da edição também
    assert '<a class="tool" href="edicoes/pesquisa.html" title="Pesquisa analítica: buscar em todas as edições">' in page
    assert '<form class="srch" role="search" action="edicoes/pesquisa.html" method="get">' in page
    assert 'name="q" type="search"' in page and '<button class="srch-all" type="submit"' in page


def test_barra_de_progresso_e_voltar_ao_topo_ficam_so_nas_outras_paginas(page, copia, config):
    """As duas peças moram no modelo comum; a página da edição (home e cópia) esvazia os blocos."""
    for html in (page, copia):
        assert 'id="rdprog"' not in html and 'id="btt"' not in html and '<body class="ed">' in html
    indice = render_archive_index([{"date": "2026-09-29", "href": "2026-09-29.html"}], config, home_href="../")
    assert '<body>\n<a class="skip"' in indice
    assert '<div id="rdprog" aria-hidden="true"></div>' in indice and '<button class="btt" id="btt"' in indice
    assert parse(indice).errors == []


def test_cabecalho_do_celular_em_uma_fileira_so_na_edicao(page):
    """Até 640 px: logo e uma fileira de botões (as abas com o nome escrito; as ferramentas com
    o ícone e o nome para o leitor de tela), e o lema desce para o rodapé."""
    css = page.split("</style>")[0]
    regra = css[css.index("@media (max-width:640px){\n  .ed .mast-tools") :]
    regra = regra[: regra.index("\n}\n")]
    assert ".ed .mast-tools{display:flex;flex-wrap:nowrap;" in regra and ".ed .mast-sub{display:none}" in regra
    assert ".ed .mt-abas .tool svg{display:none}" in regra and ".ed .mt-abas .tool span{display:inline}" in regra
    # todo botão tem nome: escrito, ou no rótulo acessível do ícone
    cabecalho = trecho(page, '<div class="mast-tools">', '<div class="mast-l">')
    botoes = re.findall(r'<(?:a|button) class="tool[^>]*>.*?</(?:a|button)>', cabecalho, re.S)
    assert len(botoes) >= 3 and all(re.search(r"<span[^>]*>\w+</span>", b) for b in botoes)
    rodape = re.search(r'<footer class="ft">.*?</footer>', page, re.S).group(0)
    assert "Seu terminal financeiro diário" in rodape


def test_footer(page):
    footer = re.search(r'<footer class="ft">.*?</footer>', page, re.S).group(0)
    assert "<p>Edição gerada por IA (claude-opus-5-5) · Terça-feira, 29 de setembro de 2026</p>" in footer
    assert '<a href="edicoes/">Edições anteriores</a>' in footer
    assert '<a href="https://github.com/pbcvphyton/TriangulaTech" rel="noopener">Código-fonte</a>' in footer
    assert "<details>" in footer and "<summary>Estado das fontes · 59 de 64 feeds ok</summary>" in footer
    assert "Bloomberg Línea: <em>XML inválido</em>" in footer
    assert 'class="st-parcial"><span class="dot" aria-hidden="true"></span>South China Morning Post' in footer
    assert 'class="st-erro"><span class="dot" aria-hidden="true"></span>Exame' in footer
    assert (
        'class="st-ok"><span class="dot" aria-hidden="true"></span>Valor Econômico <span class="nf">(7 feeds)</span>'
        in footer
    )
    # o lema fecha o rodapé (no celular ele sai do cabeçalho)
    assert footer.rstrip().endswith('<p class="ft-ass"><strong>Triangula</strong> · Seu terminal financeiro diário</p>\n</footer>')
    # edição sem racional gravado (edições antigas): sem a linha de método, e nada de contadores de produção
    assert "Como foi feita" not in footer and "blocos" not in footer and "chamadas" not in footer


def test_rodape_com_a_linha_de_metodo_que_leva_a_copia_arquivada(edition, config):
    from qijournal.models import Rationale

    ed = copy.deepcopy(edition)
    ed.rationale = Rationale(method="blocos", articles=1146, outlets=42, groups=908, topics=825)
    home, copia = render(ed, config), arquivada(ed, config)
    assert '<p>Compilada de 1.146 notícias de 42 veículos. <a href="edicoes/2026-09-29.html#racional">Como foi feita</a></p>' in home
    assert '<p>Compilada de 1.146 notícias de 42 veículos. <a href="#racional">Como foi feita</a></p>' in copia
    # o racional completo (com os termos de bastidor) só na gaveta da cópia arquivada
    assert 'id="racional"' not in home and "Nenhuma notícia foi descartada" not in home and "825 assuntos" not in home
    gaveta = trecho(copia, '<details class="gav" id="racional">', "</details>")
    assert '<summary><h2 class="gav-t" id="rat-h">Como a edição foi feita</h2></summary>' in gaveta
    assert "Nenhuma notícia foi descartada" in gaveta and "825 assuntos" in gaveta


def test_heuristic_mode_label(edition, config):
    ed = copy.deepcopy(edition)
    ed.mode, ed.model = "heuristic", None
    assert "Edição automática (sem IA)" in render(ed, config)


def test_archive_page_links(edition, config):
    html = render(edition, config, home_href="../", archive_href="./")
    assert '<h1 class="logo mono"><a href="../">' in html
    assert '<a href="./">Edições anteriores</a>' in html


def test_unsafe_hrefs_fall_back_to_defaults(edition, config):
    html = render(edition, config, home_href="javascript:alert(1)", archive_href="//evil.example/")
    hrefs = parse(html).hrefs
    assert not [h for h in hrefs if h.startswith(("javascript:", "//"))]
    assert '<h1 class="logo mono"><a href="./">' in html
    assert '<a href="edicoes/">Edições anteriores</a>' in html


# ── seções opcionais e dados incompletos ─────────────────────────────────────


def test_empty_optional_blocks_are_omitted(edition, config):
    ed = copy.deepcopy(edition)
    ed.editorial = "  "
    ed.briefing = ["", "**"]
    ed.quotes, ed.weather = [], []
    html = render(ed, config)
    assert 'class="tk"' not in html and "tk-wx" not in html.split("</style>")[1]
    assert parse(html).errors == []
    # sem cotações mas com clima: a linha de indicadores traz só o clima
    so_clima = copy.deepcopy(edition)
    so_clima.quotes = []
    linha = trecho(render(so_clima, config), '<section class="tk"', "</section>")
    assert "tk-rail" not in linha and "São Paulo <b>19°C</b>" in linha


def test_empty_sections_are_omitted(edition, config):
    ed = copy.deepcopy(edition)
    ed.sections.insert(1, Section(id="vazia", title="Seção Vazia", color="#123456", story_ids=[]))
    ed.sections.append(Section(id="fantasma", title="Só ids inexistentes", color="#123456", story_ids=["nao-existe"]))
    html = render(ed, config)
    assert "Seção Vazia" not in html and 'data-tab="vazia"' not in html
    assert "Só ids inexistentes" not in html


def test_story_without_section_goes_to_outras(edition, config):
    ed = copy.deepcopy(edition)
    ed.sections[0].story_ids.remove("bndes-aprova-r-8-bilhoes-para-transicao-energetica")
    html = render(ed, config)
    assert 'data-tab="outras"' in html
    assert re.search(r'id="pane-outras".*?href="#s-bndes-aprova-r-8-bilhoes-para-transicao-energetica"', html, re.S)


def test_story_with_empty_headline_is_dropped(edition, config):
    ed = copy.deepcopy(edition)
    broken = ed.story("bndes-aprova-r-8-bilhoes-para-transicao-energetica")
    broken.headline = " ** "
    html = render(ed, config)
    assert "s-bndes-aprova-r-8-bilhoes-para-transicao-energetica" not in html
    assert parse(html).errors == []


def test_invalid_lead_falls_back_to_first_story(edition, config):
    ed = copy.deepcopy(edition)
    ed.lead = "nao-existe"
    view = build_view(ed, config)
    assert view.lead is not None and view.lead.id == ed.sections[0].story_ids[0]
    assert view.lead.id not in [s.id for s in view.secondary + view.highlights]


def test_edition_without_stories_still_renders(edition, config):
    ed = copy.deepcopy(edition)
    ed.stories, ed.sections, ed.secondary, ed.highlights = [], [], [], []
    html = render(ed, config)
    assert 'class="capa' not in html and 'id="editorias"' not in html and "tabs-wrap all" not in html
    assert 'class="bl md"' not in html and 'class="rot"' not in html  # nenhum bloco, nenhum título órfão
    assert 'id="srchBtn"' not in html
    assert parse(html).errors == []


def test_invalid_brand_color_is_replaced(edition):
    cfg = default_config()
    cfg.brand.colors["primary"] = "red;}</style><script>alert(1)</script>"
    html = render(edition, cfg)
    assert "<script>alert(1)" not in html
    assert "--qi:#394A7A;" in html


# ── view ─────────────────────────────────────────────────────────────────────


def test_view_cleans_sources_and_orders_the_most_recent(edition, config):
    ed = copy.deepcopy(edition)
    story = ed.story(LEAD_ID)
    story.sources.append(SourceRef(name="valor econômico", url="https://outra.url/"))
    story.sources.append(SourceRef(name="**Negrito**", url="ftp://x"))
    view = build_view(ed, config)
    names = [s.name for s in view.lead.sources]
    assert names == ["Valor Econômico", "Folha de S.Paulo", "Estadão", "Agência Brasil", "Negrito"]
    assert view.lead.sources[-1].url is None
    # edição sem "wire": "Mais recentes" lista as matérias mais recentes da própria edição
    # (âncoras) que não estão no andar das notícias
    recentes = view.midia.recentes
    assert not ed.wire and len(recentes) == TETO_RECENTES
    by_anchor = {f"#{s.anchor}": s for s in view.stories}
    times = [by_anchor[r.href].published for r in recentes]
    assert times == sorted(times, reverse=True)
    assert not any(r.external for r in recentes)
    na_capa = {view.lead.id, *(c.story.id for c in view.chamadas)}
    assert not {by_anchor[r.href].id for r in recentes} & na_capa
    assert [r.so_tela_larga for r in recentes] == [False] * RECENTES_NO_CELULAR + [True] * (TETO_RECENTES - RECENTES_NO_CELULAR)


def test_view_strips_markdown_from_headline_and_dek(edition, config):
    ed = copy.deepcopy(edition)
    ed.stories.append(
        Story(
            id="md",
            section="brasil",
            headline="**Título** com *marcação*",
            dek="# Linha fina",
            body=["Texto **forte**"],
            sources=[],
            article_ids=[],
        )
    )
    ed.sections[0].story_ids.append("md")
    view = build_view(ed, config)
    story = next(s for s in view.stories if s.id == "md")
    assert story.headline == "Título com marcação"
    assert story.dek == "Linha fina"
    assert story.body == ["Texto **forte**"]  # negrito só vira <strong> no template


# ── marca alternativa ────────────────────────────────────────────────────────


def test_pbcv_brand_renders_inline_logo(edition):
    html = render(edition, pbcv_config())
    assert "<title>PBCV Advogados — Terça-feira, 29 de setembro de 2026</title>" in html
    assert '<h1 class="logo"><a href="./"><svg' in html
    assert '<title id="pbcv-logo-title">PBCV Advogados</title>' in html
    assert 'class="q"' not in html  # wordmark de texto não é usado quando há logo
    assert "--qi:#394A7A;" in html and "--qn:#1B2745;" in html
    favicon = re.search(r'<link rel="icon" type="image/svg\+xml" href="([^"]+)"', html).group(1)
    assert favicon.startswith("data:image/svg+xml,%3Csvg")
    assert "Boletim diário · Mercados, Negócios e Direito" in html
    structure = parse(html)
    assert structure.errors == [] and structure.stack == []
    # Sem brand.masthead: cabeçalho no papel e logo invertido por filtro no modo escuro.
    assert '<header class="mast-wrap">' in html and ".mast-wrap.solid{" not in html
    assert '<meta name="theme-color" content="#1B2745">' in html


def test_default_brand_logo_palette_and_favicon(edition):
    html = render(edition, default_config())
    assert '<meta name="theme-color" content="#273351">' in html  # marinho do cabeçalho
    assert '<meta name="generator" content="Triangula · qijournal 2.0">' in html
    assert "--qi:#394A7A;--qn:#1B2745;--qc:#FF5A1F;" in html
    assert '<header class="mast-wrap solid">' in html and ".mast-wrap.solid{--mh:#273351;--on-mh:#FFFFFF;" in html
    # Marca Editorial: letreiro em contornos na cor do bloco (currentColor) e o pingo do i
    # em triângulo laranja; vetor liso (não é pixel art)
    assert 'fill="currentColor"' in html and 'fill="#FF5A1F"' in html
    assert "crispEdges" not in html
    # a altura da marca vale também para o logo monocromático (vem depois da regra padrão de 64 px)
    assert html.index(".mast h1.logo.mono svg{height:64px") < html.index(
        ".mast h1.logo svg,.mast h1.logo.mono svg{height:64px;max-width:100%}"
    )
    assert ".mast h1.logo svg,.mast h1.logo.mono svg{height:46px}" in html
    assert "--ink:#1a2332;--paper:#fff;--bg:#f0f2f5;" in html  # neutros frios do layout original
    favicon = re.search(r'<link rel="icon" type="image/svg\+xml" href="([^"]+)"', html).group(1)
    # ícone: o triângulo laranja sobre o marinho
    assert favicon.startswith("data:image/svg+xml,%3Csvg") and "FF5A1F" in favicon and "1B2745" in favicon
    assert "QI Journal" not in html


def test_solid_masthead_is_still_supported(edition):
    cfg = default_config()
    cfg.brand.colors["masthead"] = "#3322CC"
    cfg.brand.colors["on_masthead"] = "#F5F3ED"
    html = render(edition, cfg)
    assert '<header class="mast-wrap solid">' in html and '<meta name="theme-color" content="#3322CC">' in html
    assert ".mast-wrap.solid{--mh:#3322CC;--on-mh:#F5F3ED;--on-mh-rgb:245,243,237;" in html


def test_text_wordmark_without_logo(edition):
    cfg = default_config()
    cfg.brand.logo_svg = None
    html = render(edition, cfg)
    assert '<h1><a href="./"><span class="journal">Triangula</span></a></h1>' in html


def test_invalid_masthead_color_keeps_paper_header(edition):
    cfg = default_config()
    cfg.brand.colors["masthead"] = "blue;}</style><script>alert(1)</script>"
    html = render(edition, cfg)
    assert "<script>alert(1)" not in html
    assert '<header class="mast-wrap">' in html and ".mast-wrap.solid{" not in html


# ── arquivo ──────────────────────────────────────────────────────────────────


def test_archive_index_groups_by_month(config):
    entries = [
        {
            "date": "2026-09-29",
            "date_label": "Terça-feira, 29 de setembro de 2026",
            "href": "2026-09-29.html",
            "lead_headline": "Arrecadação <b>recorde</b> & **ok**",
            "mode": "ai",
        },
        {
            "date": "2026-09-28",
            "date_label": "Segunda-feira, 28 de setembro de 2026",
            "href": "2026-09-28.html",
            "lead_headline": "Outra manchete",
            "mode": "heuristic",
        },
        {
            "date": "2026-08-31",
            "date_label": "Segunda-feira, 31 de agosto de 2026",
            "href": "2026-08-31.html",
            "lead_headline": "",
            "mode": "ai",
        },
        {"date": "2026-08-30", "date_label": "Domingo", "href": "javascript:alert(1)", "lead_headline": "x"},
    ]
    html = render_archive_index(entries, config, home_href="../")
    months = re.findall(r'<h3 class="arch-m">([^<]+)</h3>', html)
    assert months == ["Setembro de 2026", "Agosto de 2026"]
    assert "Arrecadação &lt;b&gt;recorde&lt;/b&gt; &amp; ok" in html
    assert html.count('<span class="badge"') == 2
    assert '<a href="2026-09-28.html"><time datetime="2026-09-28">' in html
    assert "javascript:" not in html
    assert '<a class="arch-home" href="../">' in html
    assert "3 edições no arquivo" in html
    assert "<title>Triangula — Edições anteriores</title>" in html
    structure = parse(html)
    assert structure.errors == [] and all(re.search(r"\w", t) for _, t in structure.headings)


def test_archive_index_empty(config):
    html = render_archive_index([], config, home_href="../")
    assert "Nenhuma edição arquivada ainda." in html
    assert "0 edições no arquivo" in html


def test_o_corpo_da_manchete_fica_so_na_janela_da_materia(edition, config):
    ed = copy.deepcopy(edition)
    lead = ed.story(LEAD_ID)
    lead.dek = "Linha fina própria."
    lead.body = ["Primeiro parágrafo do corpo.", "Segundo **parágrafo**.", "Terceiro parágrafo."]
    html = render(ed, config)
    assert "Primeiro parágrafo do corpo." not in andar_das_noticias(html)
    janela = _modal(html, LEAD_ID)
    assert "<p>Primeiro parágrafo do corpo.</p>" in janela and "<p>Segundo <strong>parágrafo</strong>.</p>" in janela
    assert html.count("Primeiro parágrafo do corpo.") == 1  # o texto existe uma vez no código


# ── texto da manchete e do modal ─────────────────────────────────────────────


def _modal(page_html: str, story_id: str) -> str:
    start = page_html.index(f'<div class="mo" id="s-{story_id}"')
    return page_html[start : page_html.index("</article>", start)]


def test_modal_hides_dek_already_contained_in_body(edition):
    ed = copy.deepcopy(edition)
    lead = ed.story(ed.lead)
    first = "A arrecadação federal somou R$ 230 bilhões em agosto, alta real de 8% sobre agosto de 2025, com o IR."
    lead.dek = first[:60].rstrip() + "…"  # linha fina cortada da 1ª frase (edição automática)
    lead.body = [first + " Outra frase.", "Segundo parágrafo."]
    other = next(s for s in ed.stories if s.id != ed.lead and s.dek and s.body)
    other.body = ["Parágrafo que não repete a linha fina."]
    page_html = render(ed)
    assert 'class="mdek"' not in _modal(page_html, lead.id)
    assert 'class="mdek"' in _modal(page_html, other.id)
    view = build_view(ed, default_config())
    assert view.lead.dek_in_body and not next(s for s in view.stories if s.id == other.id).dek_in_body


# ── regressões da edição real de 29/09/2026 ─────────────────────────────────


def test_english_story_is_marked_with_lang_en(edition, config):
    ed = copy.deepcopy(edition)
    story = next(s for s in ed.stories if s.id != LEAD_ID)
    story.lang = "en"
    page = render_edition_page(ed, config, home_href="./", archive_href="edicoes/")
    card = re.search(rf'<article class="cl"[^>]*>(?:(?!</article>).)*#s-{re.escape(story.id)}".*?</article>', page, re.S)
    assert card and '<h3 lang="en">' in card.group(0)
    modal = re.search(rf'<div class="mo" id="s-{re.escape(story.id)}".*?</article>', page, re.S).group(0)
    assert '<article class="ml" tabindex="-1" lang="en">' in modal
    # matérias em português não repetem o atributo (a página já é pt-BR)
    assert 'lang="pt' not in re.sub(r'<html lang="pt-BR"|lang="pt-BR">Publicado|<p class="mback" lang="pt-BR"', "", page)


def test_archive_copy_has_no_frozen_relative_age(edition, config):
    archived = render_edition_page(edition, config, home_href="../", archive_href="./", is_archive=True)
    home = render_edition_page(edition, config, home_href="./", archive_href="edicoes/")
    assert "<time datetime=\"2026-09-29T05:55:00+00:00\" data-age>" in home
    assert " data-age>" not in archived
    assert "há 2 h" in home and "há 2 h" not in archived


def test_mais_recentes_usa_as_noticias_do_dia_com_link_para_a_fonte(edition, config):
    ed = copy.deepcopy(edition)
    ed.wire = [
        {"title": "Juros futuros sobem com pesquisas", "url": "https://valor.globo.com/financas/juros.ghtml",
         "source": "Valor Econômico", "published": "2026-09-28T20:10:00+00:00", "section": "mercados"},
        {"title": "Link inválido", "url": "javascript:alert(1)", "source": "X", "published": None, "section": "brasil"},
    ]
    page = render_edition_page(ed, config, home_href="./", archive_href="edicoes/")
    midia = bloco_da_midia(page)
    assert '<h3 class="sub">Mais recentes até 05h07</h3>' in midia  # a hora da edição: a lista não se atualiza
    recentes = trecho(midia, '<ol class="rc">', "</ol>")
    assert recentes.count("<li") == 1 and "javascript:" not in recentes
    assert 'href="https://valor.globo.com/financas/juros.ghtml" target="_blank" rel="noopener noreferrer"' in recentes
    assert "<time>ontem, 17:10</time><span><b>Valor Econômico</b>" in recentes
    # a edição round-trip mantém o campo (e edições antigas, sem ele, continuam válidas)
    from qijournal.models import Edition

    assert Edition.from_dict(ed.to_dict()).wire[0]["title"] == "Juros futuros sobem com pesquisas"
    old = ed.to_dict()
    del old["wire"]
    assert Edition.from_dict(old).wire == []


def test_modal_opened_from_the_email_link_can_go_back_to_the_edition(page):
    assert "w.history.pushState(null, '', '#' + initial.id)" in page  # "voltar" do sistema fecha o modal
    assert "w.addEventListener('load'" in page  # foco no diálogo depois da navegação por âncora
    assert ".mc{position:sticky" in page  # o × acompanha a rolagem
    assert "closest('.mc,.mc2')" in page


def test_tabs_scroll_without_hiding_sections(page, config):
    # as abas não quebram nem somem: rolam para o lado, com as setas quando não cabem
    assert ".tabs-bar{display:flex;overflow-x:auto;" in page and ".tab-btn{flex-shrink:0;" in page
    assert '<button class="tabs-arrow next" type="button" tabindex="-1" aria-hidden="true" hidden>' in page
    assert next(s.title for s in config.sections if s.id == "imobiliario") == "Imobiliário"


def test_unknown_brand_falls_back_to_the_default(caplog):
    # Ex.: variável QIJ_BRAND=qi que sobrou da marca antiga no GitHub.
    from qijournal.config import load_config

    with caplog.at_level("WARNING", logger="qijournal.config"):
        cfg = load_config(env={"QIJ_BRAND": "qi"})
    assert cfg.brand.key == "tech" and cfg.brand.name == "Triangula"
    assert "Marca 'qi' não existe" in caplog.text


# ── cobertura comparada ──────────────────────────────────────────────────────


def _coverage(stances, *, sides=("Destaca o alívio", "Destaca o risco"), **extra):
    from qijournal.models import Coverage, CoverageOutlet

    outlets = [
        CoverageOutlet(name=f"Veículo {i}", stance=st, framing=f"Enfoque {i}", url=f"https://v{i}.example/n")
        for i, st in enumerate(stances)
    ]
    return Coverage(
        topic="Assunto do dia", conclusion="A cobertura pendeu para o alívio.", outlets=outlets,
        side_a=sides[0], side_b=sides[1], **extra,
    )


def test_coverage_view_meter_grows_toward_the_side_with_more_outlets():
    from qijournal.render.web import coverage_view

    view = coverage_view(_coverage(["a", "a", "a", "b", "neutro", "neutro", "a"]))
    assert (view.pct_a, view.pct_n, view.pct_b) == (57, 29, 14) and view.lean == "a"
    assert view.lean_label == "Pende para: Destaca o alívio · 4 de 7 veículos"
    assert view.aria == "Destaca o alívio: 4 veículos; neutros: 2; Destaca o risco: 1 veículo"
    tie = coverage_view(_coverage(["a", "b"]))
    assert tie.lean == "equilibrio" and tie.lean_label == "Equilíbrio: 1 × 1 de 2 veículos"
    flat = coverage_view(_coverage(["a", "b", "neutro"], sides=("", "")))  # sem debate: todos neutros
    assert flat.lean == "convergente" and (flat.pct_a, flat.pct_n, flat.pct_b) == (0, 100, 0)
    assert flat.lean_label == "Sem divergência entre os 3 veículos"
    assert coverage_view(None) is None


def test_coverage_mark_on_the_cover_and_full_analysis_in_the_modal_and_the_archive(edition, config):
    ed = copy.deepcopy(edition)
    ed.story(LEAD_ID).coverage = _coverage(["a", "a", "b"])
    card_story = next(s for s in ed.stories if s.id not in (ed.lead, *ed.secondary, *ed.highlights))
    card_story.coverage = _coverage(["b", "b", "a", "a"])
    ed.compared = [_coverage(["a", "neutro"], section="mercados", url="javascript:alert(1)", published=ed.generated_at)]
    page = render(ed, config)
    # na capa e nos cartões não há barra nem lista de veículos: só a marca de divergência,
    # e só quando o assunto teve veículos dos dois lados
    capa = page[page.index("<main") : page.index("</main>")]
    assert "cov-bar" not in capa and 'class="cov ' not in capa and "Sem divergência" not in capa
    manchete = trecho(page, '<article class="man ', "</article>")
    assert '<a class="sv-dv" href="#dv-2">1 veículo destoou</a>' in manchete  # o item dela em "Onde os veículos divergiram"
    cartoes = bloco_das_materias(page)
    assert cartoes.count('<span class="sv-dv">') == 2
    assert '<p class="sv"><span class="sv-dv">veículos divergem</span></p>' in trecho(cartoes, f'href="#s-{card_story.id}"', "</article>")
    # a análise completa continua na janela da matéria
    modal = re.search(rf'<div class="mo" id="s-{LEAD_ID}".*?</article>\s*</div>', page, re.S).group(0)
    assert '<section class="mcov" aria-label="Análise da cobertura">' in modal
    assert '<details class="cov-who" open>' in modal
    assert "Análise por veículo: o foco de cada um e o lado que seguiu (3)" in modal
    assert '<span class="cov-a" style="width:67%"></span><span class="cov-b" style="width:33%"></span>' in modal
    assert "A cobertura pendeu para o alívio." in modal
    # a home não leva a cobertura completa; a cópia arquivada, sim, numa gaveta fechada
    assert 'id="cobertura"' not in page and 'class="cmp"' not in page and "cmp-pane" not in page.split("</style>")[1].split("<script>")[0]
    copia = arquivada(ed, config)
    section = trecho(copia, '<details class="gav" id="cobertura">', "</main>")
    assert '<summary><h2 class="gav-t" id="cmp-h">Cobertura comparada: os 3 assuntos, veículo a veículo</h2></summary>' in section
    # Uma aba por editoria (as matérias com análise e os demais assuntos) e "Todas"; na aba,
    # os assuntos com dois lados primeiro (com link para a matéria) e depois os semelhantes.
    assert 'role="tablist" aria-label="Cobertura comparada por editoria"' in section
    assert 'data-cmp-tab="mercados"' in section and 'data-cmp-tab="all"' in section
    assert section.count('<li class="cmp-i"') == 2 and f'href="#s-{LEAD_ID}"' in section and "matéria da edição" in section
    assert "divergência 67" in section
    mercados = re.search(r'<div class="tab-pane cmp-pane[^"]*" id="cmp-pane-mercados".*?(?=<div class="tab-pane cmp-pane|</section>)', section, re.S).group(0)
    assert '<span class="cmp-ct">Assunto do dia</span>' in mercados and "Abrir a fonte" not in mercados  # URL insegura: sem link
    assert "Mercados &amp; Finanças" in section and "javascript:" not in copia
    for html in (page, copia):
        estrutura = parse(html)
        assert estrutura.errors == [] and all(n == 1 for n in estrutura.ids.values())


def test_onde_os_veiculos_divergiram_traz_os_dois_lados_em_palavras(edition, config):
    """Até 3 assuntos com os dois lados e 3 veículos ou mais; cada um com o título e a conta
    de cada lado. Só é "divergem" com 2 veículos ou mais no lado menor; com 1, "destoou"."""
    ed = copy.deepcopy(edition)
    ed.story(LEAD_ID).coverage = _coverage(["a", "a", "a", "b"] + ["neutro"] * 11, sides=("Critica a falta de ambição.", "Destaca os limites do consenso"))
    ed.compared = [
        _coverage(["a", "b"], section="mercados"),  # só 2 veículos: fica de fora
        _coverage(["a", "a", "a", "b", "b", "b", "b"], section="politica"),  # 3 × 4: o mais disputado
        _coverage(["a", "b", "neutro"], section="politica"),  # 1 × 1 e um sem lado
        _coverage(["a", "neutro", "neutro"], section="politica"),  # um lado só: não é divergência
        _coverage(["a", "a", "b", "neutro"], section="esporte"),  # passa do teto
    ]
    view = build_view(ed, config, copia_href="edicoes/2026-09-29.html")
    dv = view.midia.divergencias
    assert len(dv) == TETO_DIVERGENCIAS == 3 and view.midia.comparados == 6
    assert [d.marca for d in dv] == ["veículos divergem", "1 veículo destoou", "1 veículo destoou"]
    assert [(lado.conta, lado.texto) for lado in dv[0].lados] == [("4 veículos", "Destaca o risco"), ("3 veículos", "Destaca o alívio")]
    assert [(lado.conta, lado.texto) for lado in dv[1].lados] == [
        ("3 veículos", "Critica a falta de ambição"),
        ("1 veículo destoou", "Destaca os limites do consenso"),
        ("11 sem lado", ""),
    ]
    # o título abre a matéria da edição; assunto que não virou matéria leva à cobertura completa
    assert (dv[1].href, dv[1].story_id) == (f"#s-{LEAD_ID}", LEAD_ID)
    assert dv[0].href == "edicoes/2026-09-29.html#cobertura" and dv[0].story_id == ""
    assert [i.texto for i in view.manchete.servico if i.tipo == "diverge"] == ["1 veículo destoou"]
    assert [i.href for i in view.manchete.servico if i.tipo == "diverge"] == ["#dv-2"]

    home = render(ed, config)
    midia = bloco_da_midia(home)
    assert '<h3 class="sub">Onde os veículos divergiram</h3>' in midia and midia.count('<li id="dv-') == 3
    item = trecho(midia, '<li id="dv-2">', "</ul>")
    assert f'<h4><a href="#s-{LEAD_ID}">' in item
    assert "<li><b>3 veículos:</b> Critica a falta de ambição.</li>" in item
    assert "<li><b>1 veículo destoou:</b> Destaca os limites do consenso.</li>" in item
    assert '<li class="resto">11 sem lado.</li>' in item
    assert "<li><b>4 veículos:</b> Destaca o risco.</li>" in trecho(midia, '<li id="dv-1">', "</ul>")
    assert '<p class="mais"><a href="edicoes/2026-09-29.html#cobertura">Ver os 6 assuntos comparados</a></p>' in midia
    assert "no outro enfoque" not in home and "divergiu" not in midia  # nada de frase sem dizer quem diverge de quem
    # na cópia arquivada os mesmos links são âncoras da própria página
    copia = arquivada(ed, config)
    assert '<p class="mais"><a href="#cobertura">Ver os 6 assuntos comparados</a></p>' in copia
    assert '<h4><a href="#cobertura">Assunto do dia</a></h4>' in bloco_da_midia(copia)

    from qijournal.render.web import _marca_de_divergencia, coverage_view

    assert _marca_de_divergencia(coverage_view(_coverage(["a", "b", "neutro"]))) == "1 veículo de cada lado"
    assert _marca_de_divergencia(coverage_view(_coverage(["a", "a", "b", "b"]))) == "veículos divergem"


def test_sem_divergencia_uma_frase_so(edition, config):
    ed = copy.deepcopy(edition)
    ed.compared = [
        _coverage(["neutro", "neutro"], sides=("", ""), section="esporte"),
        _coverage(["a", "neutro", "neutro"], section="politica"),
        _coverage(["a", "b"], section="mercados"),  # dois lados, mas só 2 veículos
    ]
    midia = bloco_da_midia(render(ed, config))
    assert '<p class="md-nada">Hoje os veículos relataram os 3 assuntos comparados de forma semelhante.</p>' in midia
    assert '<ol class="dv">' not in midia and "Ver os 3 assuntos comparados" in midia
    ed.compared = ed.compared[:1]
    midia = bloco_da_midia(render(ed, config))
    assert "o único assunto comparado de forma semelhante" in midia and ">Ver o assunto comparado</a>" in midia


def test_compared_map_puts_the_most_disputed_first_and_groups_the_rest(edition, config):
    ed = copy.deepcopy(edition)
    ed.compared = [
        _coverage(["a", "b"], section="mercados"),  # 1 × 1: divergência 100, mas só 2 veículos
        _coverage(["a", "a", "a", "b", "b", "b", "b"], section="politica"),  # 3 × 4: mais disputado
        _coverage(["a", "neutro", "neutro"], section="politica"),  # um lado só: sem divergência
        _coverage(["neutro", "neutro"], sides=("", ""), section="esporte"),
    ]
    cm = build_view(ed, config).compared_map
    assert cm is not None and cm.total == 4 and cm.consensus == 2 and cm.stories == 0
    assert [(len(i.c.a), len(i.c.b)) for i in cm.divided] == [(3, 4), (1, 1)]
    assert [i.divergence_pct for i in cm.divided] == [86, 100]
    cats = {c.id: c for c in cm.categories}
    assert cats["politica"].total == 2 and cats["politica"].divided == 1 and len(cats["politica"].items) == 1
    order = [s.id for s in config.sections]
    assert [c.id for c in cm.categories] == sorted(cats, key=order.index)
    assert [i.divergence_pct for i in cats["politica"].divergentes] == [86]
    assert [i.divergence_pct for i in cats["mercados"].divergentes] == [100]
    page = arquivada(ed, config)  # a cobertura completa mora na gaveta da cópia arquivada
    assert "setupCompareTabs" in page and 'id="cmp-pane-politica"' in page and 'id="cmp-tab-politica"' in page
    # a primeira aba (editoria na ordem do jornal) abre selecionada; "Todas" fica por último
    tabs = re.findall(r'data-cmp-tab="([^"]+)" aria-controls="[^"]+" aria-selected="(true|false)"', page)
    assert tabs[0][1] == "true" and [t for t, on in tabs if on == "true"] == [tabs[0][0]] and tabs[-1][0] == "all"
    politica = page[page.index('id="cmp-pane-politica"') :]
    politica = politica[: politica.index('<div class="tab-pane cmp-pane', 1) if '<div class="tab-pane cmp-pane' in politica[1:] else None]
    assert politica.index("Onde a mídia divergiu") < politica.index("Relataram de forma semelhante")
    assert "Nenhum assunto de Esporte teve veículos dos dois lados hoje." in page


def test_no_coverage_no_section(edition, config, copia):
    page = render(edition, config)
    for html in (page, copia):
        assert 'class="cmp"' not in html and 'class="cov ' not in html and 'id="cobertura"' not in html
        # sem cobertura comparada, "Como a mídia cobriu" fica com uma coluna só, sem título órfão
        midia = bloco_da_midia(html)
        assert "Onde os veículos divergiram" not in midia and '<div class="md-cols uma">' in midia
        assert midia.count('<h3 class="sub">') == 1 and "Mais recentes até 05h07" in midia


# ── home e cópia arquivada ───────────────────────────────────────────────────


def _edicao_cheia(edition):
    """A edição de exemplo com tudo o que a home resume: racional, cobertura comparada com
    mais divergências que o teto, notícias além das matérias, índice do dia e termômetro."""
    from qijournal.models import IndexItem, IndexTopic, Rationale
    from qijournal.termometro import agregacao, analise

    ed = copy.deepcopy(edition)
    ed.rationale = Rationale(method="blocos", articles=1146, outlets=42, groups=908, topics=825)
    ed.secondary = [s.id for s in ed.stories[1:7]]  # mais secundárias e destaques que o teto das chamadas
    ed.highlights = [s.id for s in ed.stories[7:19]]
    ed.story(LEAD_ID).coverage = _coverage(["a", "a", "b", "b", "neutro"])
    ed.compared = [_coverage(["a", "a", "b", "b"] + ["neutro"] * i, section="politica") for i in range(8)]
    ed.wire = [
        {"title": f"Notícia {i}", "url": f"https://exemplo.com/n{i}", "source": f"Veículo {i}",
         "published": "2026-09-29T07:00:00+00:00", "section": "brasil"}
        for i in range(15)
    ]
    ed.index = [
        IndexTopic(section="brasil", title="Assunto", items=[IndexItem(source="Folha", title=f"Notícia {i}", url=f"https://exemplo.com/x{i}") for i in range(3)])
    ]

    def analise_(i: str, T: int) -> dict:
        return {
            "id": i, "url": f"https://x.com/{i}", "titulo": f"Texto {i}", "veiculo": "Folha", "editoria": "Política",
            "secao": "politica", "materia": "m", "data_publicacao": ed.date, "analisado_em": ed.date,
            "versao_calculo": "1.1.0", "selo": None, "genero": "noticia",
            "triagem": {"relevancia_politica": "alta", "eixos": ["economico"]}, "estado": "ok", "T": T,
            "rotulo": agregacao.rotulo_T(T), "confianca": "media", "resumo": "Resumo.",
            "qualidade_apuracao": {"Q": 60, "rotulo": "Regular"}, "evidencias": [],
        }

    ed.termometro = analise.registro_do_dia(ed.date, [analise_(f"t{i}", T) for i, T in enumerate([-40, -30, 20, 14, 2, 0, -3, 5])], provedor="falsa")
    return ed


def test_tetos_respeitados_com_a_edicao_cheia(edition, config):
    """Os tetos valem sempre: 1 manchete, 5 chamadas, 3 divergências, 5 recentes (3 no celular)
    e até 3 textos fora da faixa na linha do viés, por mais que a edição traga."""
    from qijournal.render.termometro import VIES_FORA_MAX

    ed = _edicao_cheia(edition)
    view = build_view(ed, config)
    assert len(view.chamadas) == TETO_CHAMADAS and len(view.midia.divergencias) == TETO_DIVERGENCIAS
    assert len(view.midia.recentes) == TETO_RECENTES and len(view.midia.vies.fora) == VIES_FORA_MAX == 3
    for html in (render(ed, config), arquivada(ed, config)):
        andar = andar_das_noticias(html)
        assert andar.count('<article class="man ') == 1 and trecho(andar, '<ol class="um-l">', "</ol>").count("<li>") == 5
        links = re.findall(r'href="#s-([^"]+)"', andar)
        assert len(links) == len(set(links)) == 6  # cada matéria uma vez no andar das notícias
        midia = bloco_da_midia(html)
        assert midia.count('<li id="dv-') == 3
        recentes = trecho(midia, '<ol class="rc">', "</ol>")
        assert recentes.count("<li") == 5 and recentes.count('<li class="larga">') == 2
        assert trecho(midia, '<ul class="md-fora"', "</ul>").count("<li>") == 3 + 1  # 3 textos e "E mais 1"
        assert '<div class="md-cols">' in midia and midia.count('<h3 class="sub">') == 2  # as duas colunas
        assert '2026-09-29-todas.html">Todas as 3 notícias do dia</a></p>' in midia  # um link só para a lista do dia
        assert html.count("2026-09-29-todas.html") == (2 if 'id="racional"' in html else 1)
    assert "@media (max-width:640px){.rc .larga{display:none}}" in html  # no celular, 3 recentes


def test_linha_do_vies_em_palavras_sem_t_e_sem_numero(edition, config):
    ed = _edicao_cheia(edition)
    home = render(ed, config)
    midia = bloco_da_midia(home)
    # o rótulo vem do termômetro, em palavras; o link leva à gaveta da cópia arquivada
    assert '<p class="md-vies">Viés político das notícias de hoje: <b>equilibrado</b>. <a href="edicoes/2026-09-29.html#termometro">Ver a análise</a></p>' in midia
    assert not re.search(r"\bT [−+]?\d", midia) and "T médio" not in midia and "qualidade" not in midia.lower()
    # só cresce quando algum texto sai da faixa de equilíbrio, e lista até 3 (os mais inclinados)
    fora = trecho(midia, '<ul class="md-fora"', "</ul>")
    assert re.findall(r"<li><b>Folha</b>, ([^:]+): <a href=\"https://x.com/(t\d)\"", fora) == [
        ("esquerda moderada", "t0"), ("esquerda moderada", "t1"), ("leve à direita", "t2"),
    ]
    assert "E mais 1 fora da faixa de equilíbrio." in fora
    # dia normal: nenhuma lista, só a linha
    calmo = copy.deepcopy(ed)
    calmo.termometro["analises"] = [a for a in calmo.termometro["analises"] if abs(a["T"]) <= 10]
    midia = bloco_da_midia(render(calmo, config))
    assert "md-fora" not in midia and "Viés político das notícias de hoje: <b>equilibrado</b>." in midia
    # termômetro que não rodou (sem análise): a linha não aparece
    calmo.termometro["analises"] = []
    assert "md-vies" not in bloco_da_midia(render(calmo, config))


def test_a_home_nao_tem_as_secoes_completas_e_a_copia_tem_as_tres_gavetas(edition, config):
    ed = _edicao_cheia(edition)
    home, copia = render(ed, config), arquivada(ed, config)
    corpo_da_home = home.split("</style>")[1].split("<script>")[0]
    for marca in ('id="cobertura"', 'id="termometro"', 'id="racional"', 'class="cmp"', 'class="term"', 'class="rat"',
                  "tm-placar", "cmp-pane", "rat-sum", 'id="tmList"', 'id="term-metodo"'):
        assert marca not in corpo_da_home, marca
    assert [a.get("id") for t, a in parse(home).tags if t == "details" and a.get("class", "").startswith("gav")] == ["editorias"]
    # os links de detalhe da home apontam para a cópia arquivada do dia (relativos à pasta das edições)
    for ancora in ("cobertura", "termometro", "racional"):
        assert f'href="edicoes/2026-09-29.html#{ancora}"' in home
        assert f'href="#{ancora}"' in copia and f"2026-09-29.html#{ancora}" not in copia
    estrutura = parse(copia)
    assert estrutura.errors == [] and estrutura.stack == []
    for ancora in ("editorias", "cobertura", "termometro", "racional"):
        assert estrutura.ids[ancora] == 1, ancora
    gavetas = [a for t, a in estrutura.tags if t == "details" and a.get("class", "").startswith("gav")]
    assert [g["id"] for g in gavetas] == ["editorias", "cobertura", "termometro", "racional"]
    assert not any("open" in g for g in gavetas)  # fechadas; o script abre a que a âncora pede
    # o conteúdo de sempre por dentro
    assert 'class="tm-placar"' in copia and 'id="tmList"' in copia and 'id="term-metodo"' in copia
    assert 'id="cmp-panes"' in copia and '<p class="rat-sum">' in copia
    assert len(copia) > len(home) and all(n == 1 for n in estrutura.ids.values())
    # a home de endereço diferente continua levando à cópia certa
    outra = render(ed, config, home_href="../", archive_href="./")
    assert 'href="./2026-09-29.html#cobertura"' in outra


def test_uma_funcao_so_abre_a_gaveta_da_ancora(copia):
    js = copia.split("<script>")[-1]
    assert js.count("function setupGavetas()") == 1 and "setupGavetas();" in js
    gavetas = js[js.index("function setupGavetas()") : js.index("/* ── Modais das matérias")]
    assert "el.tagName === 'DETAILS' && !el.open" in gavetas and "w.addEventListener('hashchange'" in gavetas
    # ao abrir, as medidas das abas são refeitas (dentro da gaveta fechada, tudo mede zero)
    assert "d.addEventListener('toggle'" in gavetas and "medidores.forEach" in gavetas and "medidores.push(update);" in js
    # e nada mais se move sozinho: sem rolagem das cotações, sem ajuste da manchete, sem Radar
    for sumiu in ("setupTicker", "setupLeadFit", "setupRadarMore", "setupSectionNav", "tk-scroll"):
        assert sumiu not in copia, sumiu


def test_marca_sem_termometro_nao_mostra_a_linha_do_vies(edition):
    ed = _edicao_cheia(edition)
    for html in (render(ed, pbcv_config()), arquivada(ed, pbcv_config())):
        assert "Viés político" not in html and "md-vies" not in html.split("</style>")[1]
        assert "termometro" not in html and "Analítico" not in html
        midia = bloco_da_midia(html)
        assert '<h2 class="rot" id="md-h">Como a mídia cobriu</h2>' in midia
        assert midia.count('<h3 class="sub">') == 2 and midia.count('<li id="dv-') == 3  # as duas colunas continuam
        assert parse(html).errors == []


def test_edicao_automatica_sem_coluna_vazia(edition, config):
    """Edição em modo automático: sem cobertura comparada e sem termômetro. "Como a mídia
    cobriu" fica só com "Mais recentes"; nenhuma coluna vazia, nenhum título sem conteúdo."""
    ed = _edicao_cheia(edition)
    ed.mode, ed.model, ed.compared, ed.termometro = "heuristic", None, [], None
    for story in ed.stories:
        story.coverage = None
    for html in (render(ed, config), arquivada(ed, config)):
        midia = bloco_da_midia(html)
        assert '<div class="md-cols uma">' in midia and midia.count('<div class="md-col">') == 1
        assert "Mais recentes" in midia and "Onde os veículos divergiram" not in midia and "md-vies" not in midia
        assert 'id="cobertura"' not in html and 'id="termometro"' not in html
        # todo título de bloco ou de coluna vem seguido de conteúdo
        assert not re.search(r"</h[23]>\s*(</div>|</section>)", html.split("</style>")[1].split('<div class="modals">')[0])
        assert parse(html).errors == []
    # sem notícias recentes nem matéria fora da capa, o bloco inteiro some
    vazia = copy.deepcopy(ed)
    vazia.wire = []
    vazia.stories = vazia.stories[:3]
    vazia.lead, vazia.secondary, vazia.highlights = vazia.stories[0].id, [s.id for s in vazia.stories[1:]], []
    assert 'class="bl md"' not in render(vazia, config)


def test_o_bloco_da_agenda_fica_entre_os_dois_andares_e_sem_retrato_nao_gera_saida(page, copia):
    from qijournal.render import filters

    fonte = (filters.TEMPLATES_DIR / "edition.html.j2").read_text(encoding="utf-8")
    marca = "{# AGENDA-DA-SEMANA:"
    assert fonte.count(marca) == 1
    # entre "Em 1 minuto" e "Como a mídia cobriu"
    assert fonte.index('id="um-h"') < fonte.index(marca) < fonte.index('id="ag-h"') < fonte.index('id="md-h"')
    for html in (page, copia):  # a edição destes testes não traz o retrato da agenda
        assert "AGENDA-DA-SEMANA" not in html and 'id="agenda"' not in html
        entre = html[html.index("</section>", html.index('id="um-h"')) : html.index('<section class="bl md"')]
        assert re.sub(r"\s+", "", entre) == "</section></div>"  # nada entre os dois andares
        # nem os estilos nem o script da agenda entram na página que não a mostra
        assert ".ag-cols" not in html and "setupAgenda" not in html and "data-ag-dia" not in html


def test_logo_with_dark_variant_and_height(edition):
    cfg = default_config()
    cfg.brand.logo_svg = '<svg viewBox="0 0 10 2"><title id="t">Claro</title><path fill="#123456" d="M0 0h1"/></svg>'
    cfg.brand.logo_svg_dark = '<svg viewBox="0 0 10 2"><title id="t">Escuro</title><path fill="#FFFFFF" d="M0 0h1"/></svg>'
    cfg.brand.logo_height = 56
    cfg.brand.logo_height_mobile = None  # padrão: 85% no celular
    html = render(edition, cfg)
    assert '<h1 class="logo duo"><a href="./"><span class="lg lg-l"><svg' in html
    assert '<span class="lg lg-d"><svg viewBox="0 0 10 2"><title id="dk-t">Escuro</title>' in html
    assert ".mast h1.logo svg,.mast h1.logo.mono svg{height:56px;max-width:100%}" in html
    assert ".mast h1.logo svg,.mast h1.logo.mono svg{height:48px}" in html  # celular: 85%
    assert parse(html).ids["t"] == 1 and parse(html).ids["dk-t"] == 1


def test_explicit_mobile_logo_height(edition):
    cfg = default_config()
    cfg.brand.logo_height, cfg.brand.logo_height_mobile = 60, 40
    html = render(edition, cfg)
    assert ".mast h1.logo svg,.mast h1.logo.mono svg{height:60px;max-width:100%}" in html
    assert ".mast h1.logo svg,.mast h1.logo.mono svg{height:40px}" in html
    cfg.brand.logo_height_mobile = 999  # fora da faixa: volta aos 85%
    assert ".mast h1.logo svg,.mast h1.logo.mono svg{height:51px}" in render(edition, cfg)


# ── agenda da semana ─────────────────────────────────────────────────────────


def _retrato_da_agenda(com_midia: bool = False):
    """O retrato cheio das fixtures de coleta (``tests/fixtures/agenda``); com mídia, um item
    ligado a uma matéria desta edição e outro a uma matéria que ela não tem."""
    from qijournal.agenda import ligar, montar, regras
    from tests.fixtures import agenda as fx

    ligacoes = {
        "RE 1326541": ligar.Ligacao(veiculos=("Folha", "G1", "Valor"), materias=(LEAD_ID,)),
        "PL 6461/2019": ligar.Ligacao(veiculos=("Folha", "G1"), materias=("materia-de-outra-edicao",)),
    }
    return montar.montar(fx.camara_encerrada(), fx.tribunais(), "2026-11-24", regras.carregar(), ligacoes=ligacoes if com_midia else {})


def test_sem_retrato_da_agenda_a_home_nao_muda(edition, page, copia, config):
    # Sem o retrato na edição, a home e a cópia arquivada saem byte a byte como sempre saíram.
    sem = copy.deepcopy(edition)
    sem.agenda = None
    assert render(sem, config) == page and arquivada(sem, config) == copia
    # Com o retrato e a agenda desligada, idem: nem bloco, nem estilos, nem script.
    com = copy.deepcopy(edition)
    com.agenda = _retrato_da_agenda().para_dict()
    desligada = default_config()
    desligada.agenda.enabled = False
    assert render(com, desligada) == page and arquivada(com, desligada) == copia
    # Ligada, o bloco entra entre os dois andares e o resto da página fica como estava.
    home = render(com, config)
    assert home != page and home.count('<section class="bl ag" id="agenda"') == 1
    inicio = home.index('<section class="bl ag" id="agenda"')
    fim = home.index("</section>", inicio) + len("</section>")
    corpo_sem_bloco = home[home.index("<body") : inicio] + home[fim:]
    referencia = page[page.index("<body") :]
    assert re.sub(r"\s+", "", corpo_sem_bloco[: corpo_sem_bloco.index("<script>")]) == re.sub(r"\s+", "", referencia[: referencia.index("<script>")])


def test_com_retrato_os_links_de_materia_da_agenda_apontam_para_materias_reais(edition, config):
    com = copy.deepcopy(edition)
    com.agenda = _retrato_da_agenda(com_midia=True).para_dict()
    for html, pesquisa in ((render(com, config), "edicoes/pesquisa.html"), (arquivada(com, config), "pesquisa.html")):
        estrutura = parse(html)
        assert estrutura.errors == [] and [i for i, n in estrutura.ids.items() if n > 1] == []
        bloco = trecho(html, '<section class="bl ag" id="agenda"', "</section>")
        # a marca de mídia abre a janela de uma matéria que existe; a que não existe vai para a Pesquisa
        assert f'href="#s-{LEAD_ID}">na mídia: 3 veículos</a>' in bloco
        assert f'href="{pesquisa}?q=PL%206461/2019">na mídia: 2 veículos</a>' in bloco
        assert "materia-de-outra-edicao" not in html
        internos = re.findall(r'href="#([^"]+)"', bloco)
        assert internos and all(estrutura.ids[alvo] == 1 for alvo in internos)
        # a chamada da matéria ligada ganha "na agenda", que leva ao bloco
        capa = trecho(html, '<div class="capa', '<section class="bl ag"')
        assert capa.count('<a href="#agenda">na agenda: qua 25</a>') == 1 and estrutura.ids["agenda"] == 1
    view = build_view(com, config)
    assert view.manchete.story.id == LEAD_ID and view.manchete.servico[-1].texto == "na agenda: qua 25"
    assert all(i.tipo != "agenda" for c in view.chamadas for i in c.servico)

