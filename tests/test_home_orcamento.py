"""Orçamento da home: o que é contável não pode voltar a crescer.

A queixa que originou a home em três andares foi "um milhão de coisas jogadas na tela".
Este arquivo trava o que dá para contar sem navegador e sem rede: itens por bloco, palavras
visíveis com tudo fechado (fora das janelas das matérias e das gavetas), controles e links
à vista, peso do arquivo, termos de bastidor e movimento automático. A altura em pixels é
medida à mão, no navegador (os números de cada medição ficam no README).

Fixtures: três edições reais reduzidas, em ``tests/fixtures/home/edicao-AAAA-MM-DD.json``,
feitas por ``tests/fixtures/home/reduzir.py`` a partir de ``data/2026-11-16.json`` (edição
automática, sem IA: sem cobertura comparada), ``data/2026-11-22.json`` (8 assuntos com os
dois lados, mais que o teto) e ``data/2026-11-23.json`` (a edição usada nas medições da
home). O redutor guarda só o que a home mostra (a regra de corte está no docstring dele);
as três somam menos de 300 KB.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from qijournal.models import Edition
from qijournal.render.termometro import VIES_FORA_MAX
from qijournal.render.web import (
    RECENTES_NO_CELULAR,
    TETO_CHAMADAS,
    TETO_DIVERGENCIAS,
    TETO_RECENTES,
    build_view,
    render_edition_page,
)
from tests.fixtures.render.helpers import VOID_TAGS, default_config, parse, pbcv_config

PASTA = Path(__file__).parent / "fixtures" / "home"
DIAS = ("2026-11-16", "2026-11-22", "2026-11-23")
AUTOMATICA = "2026-11-16"

# Metas da home (decisões da reforma), com tudo fechado.
PALAVRAS_MAX = 1000
CONTROLES_MAX = 12
LINKS_MAX = 70
PESO_MAX = 450 * 1024  # index.html de uma edição real
PESO_FORA_DAS_JANELAS_MAX = 190 * 1024  # estilos, script e a capa: a parte que não depende das matérias
TITULOS_DE_BLOCO_MAX = 3  # "Em 1 minuto", a agenda da semana (quando entrar) e "Como a mídia cobriu"
FIXTURES_MAX = 300 * 1024

# Termos de bastidor que não podem aparecer na home (ficam na gaveta "Como a edição foi feita").
BASTIDOR = (
    r"blocos? por editoria",
    r"\bbloco\b",
    r"parte \d+ de \d+",
    r"chamadas? à IA",
    r"\btokens?\b",
    r"análises? de cobertura",
    r"\bassuntos? agrupados?\b",
)


def _edicao(dia: str) -> Edition:
    return Edition.from_dict(json.loads((PASTA / f"edicao-{dia}.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def config():
    return default_config()


@pytest.fixture(scope="module", params=DIAS)
def caso(request, config):
    """(dia, edição, home, cópia arquivada) de cada edição real reduzida."""
    edicao = _edicao(request.param)
    home = render_edition_page(edicao, config, home_href="./", archive_href="edicoes/")
    copia = render_edition_page(edicao, config, home_href="../", archive_href="./", is_archive=True)
    return request.param, edicao, home, copia


class Visivel(HTMLParser):
    """O que o leitor tem diante de si com tudo fechado: o texto, os links e os controles
    fora dos scripts e estilos, das janelas das matérias (``.modals``), do conteúdo das
    gavetas fechadas (``<details>`` sem ``open``; o ``<summary>`` conta), do que só o leitor
    de tela lê (``.sr-only``) e do que está oculto (atributo ``hidden``).

    Conta o que existe no código, sem saber a largura da tela: o que o CSS esconde só no
    celular (a linha fina das chamadas) ou só no computador entra na conta, então o número
    é um teto do que qualquer tela mostra."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pilha: list[tuple[str, bool]] = []  # (tag, o trecho está oculto?)
        self.texto: list[str] = []
        self.links = 0
        self.controles = 0

    def _oculto(self) -> bool:
        return bool(self.pilha) and self.pilha[-1][1]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        dentro_de_gaveta_fechada = bool(self.pilha) and self.pilha[-1][0] == "details:fechada"
        oculto = self._oculto()
        if tag in ("script", "style", "svg", "template") or "hidden" in a or "sr-only" in classes or "modals" in classes:
            oculto = True
        if dentro_de_gaveta_fechada:
            oculto = tag != "summary"
        if not oculto:
            if tag == "a" and a.get("href") is not None:
                self.links += 1
            if tag in ("button", "input", "select", "summary") or a.get("role") == "tab":
                self.controles += 1
        if tag in VOID_TAGS:
            return
        marca = "details:fechada" if tag == "details" and "open" not in a and not oculto else tag
        self.pilha.append((marca, oculto))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.pilha.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag not in VOID_TAGS and self.pilha:
            self.pilha.pop()

    def handle_data(self, data: str) -> None:
        if not self._oculto() and not (self.pilha and self.pilha[-1][0] == "details:fechada"):
            self.texto.append(data)

    @property
    def palavras(self) -> list[str]:
        return " ".join(self.texto).split()


def visivel(html: str) -> Visivel:
    assert parse(html).errors == []  # a contagem supõe HTML bem formado
    leitor = Visivel()
    leitor.feed(html[html.index("<body", html.index("</head>")) :])
    leitor.close()
    return leitor


def trecho(html: str, inicio: str, *fins: str) -> str:
    a = html.index(inicio)
    cortes = [html.index(fim, a + len(inicio)) for fim in fins if fim in html[a + len(inicio) :]]
    return html[a : min(cortes)] if cortes else html[a:]


def corpo(html: str) -> str:
    """A página sem os estilos, o script e as janelas das matérias."""
    return html[html.index("<body", html.index("</head>")) : html.index('<div class="modals">')]


# ── as fixtures ──────────────────────────────────────────────────────────────


def test_as_fixtures_sao_as_tres_edicoes_reais_e_pesam_pouco():
    arquivos = sorted(PASTA.glob("edicao-*.json"))
    assert [a.name for a in arquivos] == [f"edicao-{dia}.json" for dia in DIAS]
    assert sum(a.stat().st_size for a in arquivos) < FIXTURES_MAX
    modos = {dia: _edicao(dia).mode for dia in DIAS}
    assert modos[AUTOMATICA] == "heuristic" and [m for d, m in modos.items() if d != AUTOMATICA] == ["ai", "ai"]
    # a automática é o estado sem cobertura comparada; as outras têm mais divergências que o teto
    assert not _edicao(AUTOMATICA).compared and all(not s.coverage for s in _edicao(AUTOMATICA).stories)
    assert len(build_view(_edicao("2026-11-22"), default_config()).compared_map.divided) > TETO_DIVERGENCIAS
    assert len(_edicao("2026-11-23").stories) == 20 and len(_edicao("2026-11-23").wire) > TETO_RECENTES


# ── itens por bloco ──────────────────────────────────────────────────────────


def test_itens_por_bloco_dentro_dos_tetos(caso, config):
    dia, edicao, home, _copia = caso
    view = build_view(edicao, config)
    assert view.manchete is not None and len(view.chamadas) == TETO_CHAMADAS == 5
    assert len(view.midia.divergencias) <= TETO_DIVERGENCIAS == 3 and len(view.midia.recentes) == TETO_RECENTES == 5
    assert view.midia.vies is None or len(view.midia.vies.fora) <= VIES_FORA_MAX == 3

    capa = corpo(home)
    # uma manchete, sem corpo: editoria, linha fina e linha de serviço, nenhum parágrafo da matéria
    assert capa.count('<article class="man ') == 1
    manchete = trecho(capa, '<article class="man ', "</article>")
    assert manchete.count("<p") <= 3 and edicao.story(view.lead.id).body[0][:60] not in capa
    # cinco chamadas, e cada matéria uma única vez no andar das notícias
    andar = trecho(capa, '<div class="capa', '<section class="bl md"', '<details class="gav')
    assert trecho(andar, '<ol class="um-l">', "</ol>").count("<li>") == 5
    materias = re.findall(r'href="#s-([^"]+)"', andar)
    assert len(materias) == len(set(materias)) == 6
    # "Como a mídia cobriu": até 3 divergências e 5 recentes, das quais 3 no celular
    midia = trecho(capa, '<section class="bl md"', "</section>")
    assert midia.count('<li id="dv-') == len(view.midia.divergencias) <= 3
    recentes = trecho(midia, '<ol class="rc">', "</ol>")
    assert recentes.count("<li") == 5 and recentes.count("<li>") == RECENTES_NO_CELULAR == 3
    assert midia.count("<li>") + midia.count("<li ") <= 3 * 4 + 5 + 4  # nenhuma lista além dessas
    # um título por bloco, um bloco recolhido, nada de abas ou filtros à vista
    assert capa.count('class="rot"') <= TITULOS_DE_BLOCO_MAX and capa.count('<h3 class="sub">') <= 2
    gavetas = [a for t, a in parse(home).tags if t == "details" and "gav" in (a.get("class") or "").split()]
    assert [g.get("id") for g in gavetas] == ["editorias"] and "open" not in gavetas[0]
    assert dia and capa.count('<section class="tk"') == 1 and "tk-pause" not in capa


def test_palavras_controles_e_links_visiveis(caso):
    _dia, _edicao_, home, _copia = caso
    tela = visivel(home)
    assert len(tela.palavras) <= PALAVRAS_MAX, len(tela.palavras)
    assert tela.controles <= CONTROLES_MAX, tela.controles
    assert tela.links <= LINKS_MAX, tela.links
    # o que a conta deixa de fora existe de verdade no código (senão a conta não diria nada)
    assert home.count('<div class="mo"') >= 20 and home.count('<article class="cl"') >= 20
    texto = " ".join(tela.palavras)
    assert "Como a mídia cobriu" in texto and "Todas as" in texto and "Por que importa" not in texto


def test_peso_da_home(caso):
    _dia, _edicao_, home, copia = caso
    assert len(home.encode("utf-8")) <= PESO_MAX
    fora_das_janelas = home[: home.index('<div class="modals">')] + home[home.index("</div>\n<script>") :]
    assert len(fora_das_janelas.encode("utf-8")) <= PESO_FORA_DAS_JANELAS_MAX, len(fora_das_janelas.encode("utf-8"))
    # o detalhe pesa na cópia arquivada, não na home
    assert len(copia) >= len(home)
    assert home.count("<script") == 2 and copia.count("<script") == 2


# ── bastidor e movimento ─────────────────────────────────────────────────────


def test_nenhum_termo_de_bastidor_na_home(caso):
    _dia, _edicao_, home, copia = caso
    texto = " ".join(visivel(home).palavras)
    for termo in BASTIDOR:
        assert not re.search(termo, texto, re.I), termo
    # a home não carrega as seções de detalhe nem o que as monta
    capa = corpo(home)
    for marca in ('id="cobertura"', 'id="termometro"', 'id="racional"', 'class="cmp"', 'class="term"', 'class="rat"', "tm-placar", "rat-blocks"):
        assert marca not in capa, marca
    # o que saiu da capa não voltou
    for sumiu in ("Editorial do dia", "Mais para ler", "Radar de", "Racional da edição", "Sem divergência entre", "Termômetro do dia"):
        assert sumiu not in capa, sumiu
    assert "Como a edição foi feita" in copia or 'id="racional"' not in copia


def test_nada_se_move_sozinho_na_pagina_da_edicao(caso):
    _dia, _edicao_, home, _copia = caso
    css = home[home.index("<style>") : home.index("</style>")]
    # a única animação é a da janela da matéria abrindo (o leitor é quem abre)
    assert set(re.findall(r"@keyframes ([\w-]+)", css)) == {"ml-in"}
    assert re.findall(r"animation:([\w-]+)", css) == ["ml-in"]
    script = home[home.rindex("<script>") :]
    assert "setInterval" not in script and "tk-scroll" not in home
    # a rede do cabeçalho fica desenhada, sem se mexer, na página da edição
    assert "var fixa = d.body.classList.contains('ed');" in script and "if (!fixa && !frame &&" in script
    assert 'id="rdprog"' not in home and 'id="btt"' not in home


# ── estados ──────────────────────────────────────────────────────────────────


def test_copia_arquivada_leva_as_gavetas_do_que_a_edicao_tem(caso, config):
    dia, edicao, home, copia = caso
    view = build_view(edicao, config, archive=True)
    esperadas = ["editorias"]
    esperadas += ["cobertura"] if view.compared_map else []
    esperadas += ["termometro"] if view.termometro else []
    esperadas += ["racional"] if view.rationale else []
    estrutura = parse(copia)
    assert estrutura.errors == [] and estrutura.stack == [] and all(n == 1 for n in estrutura.ids.values())
    gavetas = [a for t, a in estrutura.tags if t == "details" and "gav" in (a.get("class") or "").split()]
    assert [g["id"] for g in gavetas] == esperadas and not any("open" in g for g in gavetas)
    assert "racional" in esperadas and ("cobertura" in esperadas) == (dia != AUTOMATICA)
    # todo link de detalhe da home aponta para uma gaveta que a cópia do dia tem
    for ancora in re.findall(rf'href="edicoes/{dia}\.html#(\w+)"', home):
        assert ancora in esperadas, ancora
    assert re.findall(rf'href="edicoes/{dia}\.html#\w+"', home)
    assert f"{dia}.html#" not in copia.split("</head>")[1]  # na cópia, âncoras da própria página
    for ancora in esperadas[1:]:
        assert f'href="#{ancora}"' in copia


def test_edicao_automatica_mostra_so_mais_recentes(config):
    edicao = _edicao(AUTOMATICA)
    home = render_edition_page(edicao, config, home_href="./", archive_href="edicoes/")
    midia = trecho(home, '<section class="bl md"', "</section>")
    assert '<div class="md-cols uma">' in midia and midia.count('<div class="md-col">') == 1
    assert midia.count('<h3 class="sub">') == 1 and "Mais recentes até 08h07" in midia
    assert "Onde os veículos divergiram" not in midia and "assuntos comparados" not in midia and "#cobertura" not in home
    # nenhum título sem conteúdo logo depois
    assert not re.search(r"</h[234]>\s*(</div>|</section>|</details>)", corpo(home))
    tela = visivel(home)
    assert len(tela.palavras) <= PALAVRAS_MAX and tela.controles <= CONTROLES_MAX
    # 16/11 tem um único texto analisado: a home não afirma o viés "das notícias de hoje"
    assert "md-vies" not in midia and "Viés político" not in home
    # sem termômetro na edição, a linha do viés também some e o bloco fica só com a lista
    edicao.termometro = None
    midia = trecho(render_edition_page(edicao, config, home_href="./", archive_href="edicoes/"), '<section class="bl md"', "</section>")
    assert "md-vies" not in midia and "Mais recentes até 08h07" in midia and midia.count("<h") == 2


def test_marca_sem_termometro_cabe_no_mesmo_orcamento():
    config = pbcv_config()
    edicao = _edicao("2026-11-23")
    home = render_edition_page(edicao, config, home_href="./", archive_href="edicoes/")
    copia = render_edition_page(edicao, config, home_href="../", archive_href="./", is_archive=True)
    assert "Viés político" not in home and "termometro" not in home and "termometro" not in copia
    midia = trecho(home, '<section class="bl md"', "</section>")
    assert midia.count('<h3 class="sub">') == 2 and midia.count('<li id="dv-') == 3
    tela = visivel(home)
    assert len(tela.palavras) <= PALAVRAS_MAX and tela.controles <= CONTROLES_MAX and tela.links <= LINKS_MAX
    gavetas = [a.get("id") for t, a in parse(copia).tags if t == "details" and "gav" in (a.get("class") or "").split()]
    assert gavetas == ["editorias", "cobertura", "racional"]
