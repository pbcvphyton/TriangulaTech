"""Testes da Agenda da semana na tela: o bloco da home e a página ``edicoes/agenda.html``.

Sem rede: os retratos saem das fixtures de coleta (``tests/fixtures/agenda``) e a edição é
a dos testes de renderização (``tests/fixtures/render``). O que se trava aqui: HTML bem
formado e ids únicos, escape do texto das fontes, tetos da home, o pé e o aviso, nenhuma
expressão proibida, marca sem agenda, e que nada da agenda derruba a página da edição nem
o ``publish``.
"""

from __future__ import annotations

import copy
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from qijournal import pipeline
from qijournal.agenda import ligar, montar, regras
from qijournal.agenda.retrato import DiaTela, Retrato
from qijournal.models import Edition
from qijournal.render import agenda as tela
from qijournal.render.web import build_view, render_edition_page
from tests.fixtures import agenda as fx
from tests.fixtures.pipeline.factory import make_config, make_edition
from tests.fixtures.render.helpers import LEAD_ID, default_config, load_edition, parse, pbcv_config

REGRAS = regras.carregar()
TERCA = "2026-11-24"


def _cheio(**kwargs: Any) -> Retrato:
    return montar.montar(fx.camara_encerrada(), fx.tribunais(), TERCA, REGRAS, **kwargs)


def _com_midia() -> Retrato:
    """O retrato cheio com dois itens citados: um por matéria da edição, outro por matéria
    que a edição não tem (o link vai para a Pesquisa)."""
    return _cheio(
        ligacoes={
            "RE 1326541": ligar.Ligacao(veiculos=("Folha", "G1", "Valor"), materias=(LEAD_ID,)),
            "PL 6461/2019": ligar.Ligacao(veiculos=("Folha", "G1"), materias=("materia-que-nao-existe",)),
        }
    )


def _edicao(retrato: Retrato | None | dict[str, Any] | str) -> Edition:
    edicao = load_edition()
    edicao.agenda = retrato.para_dict() if isinstance(retrato, Retrato) else retrato  # type: ignore[assignment]
    return edicao


def _home(edicao: Edition, config=None, **kwargs: Any) -> str:
    return render_edition_page(edicao, config or default_config(), home_href="./", archive_href="edicoes/", **kwargs)


def _copia(edicao: Edition, config=None) -> str:
    return render_edition_page(edicao, config or default_config(), home_href="../", archive_href="./", is_archive=True)


def _pagina(edicao: Edition, config=None) -> str | None:
    return tela.render_agenda_page(edicao, config or default_config(), home_href="../", archive_href="./")


def _bloco(html: str) -> str:
    """Só o bloco da agenda (da abertura ao fechamento da <section>)."""
    inicio = html.index('<section class="bl ag" id="agenda"')
    return html[inicio : html.index("</section>", inicio) + len("</section>")]


def _texto(html: str) -> str:
    """O texto de tela de um trecho: sem marcação, sem o que só o leitor de tela lê."""
    sem_sr = re.sub(r'<span class="sr-only">.*?</span>', "", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", sem_sr).split())


def _conteudo(pagina: str) -> str:
    return pagina[pagina.index("<main") : pagina.index("</main>")]


@pytest.fixture(scope="module")
def home() -> str:
    return _home(_edicao(_com_midia()))


@pytest.fixture(scope="module")
def pagina() -> str:
    html = _pagina(_edicao(_com_midia()))
    assert html is not None
    return html


# ── HTML e ids ───────────────────────────────────────────────────────────────


def test_bloco_bem_formado_com_um_titulo_so_e_ids_unicos(home):
    estrutura = parse(home)
    assert estrutura.errors == []
    assert [i for i, n in estrutura.ids.items() if n > 1] == []
    assert estrutura.ids["agenda"] == 1 and estrutura.ids["ag-h"] == 1
    assert [t for nivel, t in estrutura.headings if t == "Agenda da semana"] == ["Agenda da semana"]
    assert parse(_bloco(home)).errors == []
    # entre "Em 1 minuto" e "Como a mídia cobriu"
    assert home.index('id="um-h"') < home.index('id="agenda"') < home.index('id="md-h"')
    copia = _copia(_edicao(_com_midia()))
    assert parse(copia).errors == [] and [i for i, n in parse(copia).ids.items() if n > 1] == []
    assert 'href="agenda.html"' in _bloco(copia)  # na cópia arquivada, a página completa mora na mesma pasta


def test_pagina_completa_bem_formada_e_ids_unicos(pagina):
    estrutura = parse(pagina)
    assert estrutura.errors == []
    assert [i for i, n in estrutura.ids.items() if n > 1] == []
    for ancora in ("semana", "decidido", "radar", "fora", "como-ler"):
        assert estrutura.ids[ancora] == 1 and f'href="#{ancora}"' in pagina
    assert len(pagina.encode("utf-8")) <= tela.PAGINA_BYTES_MAX
    # sem botão novo no cabeçalho: a página não se anuncia nas ferramentas
    cabecalho = pagina[pagina.index('<div class="mast-tools">') : pagina.index('<div class="mast-l">')]
    assert "agenda" not in cabecalho.lower()


def test_cada_linha_traz_instituicao_e_orgao_por_extenso_e_o_assunto_antes_do_numero(home):
    bloco = _bloco(home)
    texto = _texto(bloco)
    for cabeca in ("Câmara · Plenário", "Senado · Plenário", "STF · Plenário", "Congresso"):
        assert cabeca in texto
    assert not re.search(r"·\s*PL\s*<", bloco)  # nunca "PL" no lugar de "Plenário"
    # o assunto vem antes do identificador, que é o link oficial
    assert bloco.index("Estatuto do Aprendiz") < bloco.index(">PL 6461/2019<")
    assert re.search(r'<a href="https://[^"]+" target="_blank" rel="noopener noreferrer">PL 6461/2019<', bloco)
    assert "Medida provisória perde a validade" in texto and "nada de peso pautado" in texto
    assert "Já decidido esta semana" in texto and "texto principal aprovado; os trechos destacados são votados em separado" in texto
    # nenhuma cor nem ícone por instituição: o bloco não leva estilo embutido nem imagem
    assert "style=" not in bloco and "<svg" not in bloco and "<img" not in bloco


# ── escape ───────────────────────────────────────────────────────────────────


def test_texto_e_links_das_fontes_saem_escapados():
    dados = _cheio().para_dict()
    ataque = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    linha = dados["dias"][0]["linhas"][0]
    linha["cabeca"] = f"Câmara {ataque}"
    linha["itens"][0]["nome"] = f"Assunto {ataque}"
    linha["itens"][0]["link"] = "javascript:alert(1)"
    dados["frases"][0]["texto"] = f"Frase {ataque}"
    dados["frases"][0]["link"] = "data:text/html,<script>alert(1)</script>"
    dados["pagina"]["dias"][0]["linhas"][0]["itens"][0]["nome"] = f"Na página {ataque}"
    dados["pagina"]["dias"][0]["linhas"][0]["itens"][0]["link"] = "javascript:alert(2)"
    dados["pagina"]["limites"] = [f"Limite {ataque}"]
    edicao = _edicao(dados)
    for html in (_home(edicao), _copia(edicao), _pagina(edicao)):
        assert html is not None and parse(html).errors == []
        assert "<script>alert" not in html and "<img src=x" not in html
        assert "javascript:" not in html and "data:text/html" not in html
        assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;" in html


# ── tetos ────────────────────────────────────────────────────────────────────


def _linhas(view: tela.AgendaView) -> list[tela.LinhaView]:
    return [linha for dia in view.principais + view.resto for linha in dia.linhas]


def test_tetos_da_home_com_o_retrato_cheio():
    view = build_view(_edicao(_com_midia()), default_config(), copia_href="edicoes/2026-09-29.html").agenda
    assert view is not None
    linhas = _linhas(view)
    assert 0 < len(linhas) <= tela.TETO_LINHAS
    assert sum(1 for linha in linhas if linha.aberta) <= tela.TETO_ABERTAS_NO_CELULAR
    assert all(len(linha.itens) <= tela.TETO_ITENS_POR_LINHA for linha in linhas)
    assert all(len(linha.itens) <= 1 for dia in view.resto for linha in dia.linhas)  # resto da semana: um item por linha
    assert all(sum(1 for item in linha.itens if not item.so_tela_larga) <= 1 for linha in linhas)  # celular: um item
    assert len(view.frases) <= tela.TETO_FRASES and len(view.decidido) <= tela.TETO_DECIDIDO
    assert all(len(item.nome) <= tela.NOME_MAX for linha in linhas for item in linha.itens)
    assert [d.rotulo for d in view.principais] == ["ter 24", "qua 25"]  # "hoje e amanhã": os dois primeiros dias
    assert view.pagina_href == "edicoes/agenda.html" and view.pagina_rotulo == "Agenda completa"


def test_a_tela_reaplica_os_tetos_mesmo_com_retrato_inchado():
    base = _cheio()
    dia = base.dias[0]
    linha = replace(dia.linhas[0], aberta_no_celular=True, itens=dia.linhas[0].itens * 3)
    dias = tuple(replace(d, linhas=(linha,) * 6, vazio="") for d in base.dias)
    inchado = replace(base, dias=dias, frases=base.frases * 4, decidido=base.decidido * 5, fora_do_teto=3)
    view = tela.bloco_da_home(inchado, materias=(), pasta="edicoes/")
    assert view is not None
    linhas = _linhas(view)
    assert len(linhas) == tela.TETO_LINHAS
    assert sum(1 for l in linhas if l.aberta) == tela.TETO_ABERTAS_NO_CELULAR
    assert all(len(l.itens) <= tela.TETO_ITENS_POR_LINHA for l in linhas)
    assert len(view.frases) == tela.TETO_FRASES and len(view.decidido) == tela.TETO_DECIDIDO
    assert view.pagina_rotulo == "Mais 3 na agenda completa"
    html = _home(_edicao(inchado))
    assert parse(html).errors == [] and _bloco(html).count('<li class="ag-l') <= tela.TETO_LINHAS + tela.TETO_DECIDIDO


# ── pé, aviso e script ───────────────────────────────────────────────────────


def test_pe_do_bloco_e_aviso_da_pagina(home, pagina):
    bloco = _bloco(home)
    pe = bloco[bloco.index('<p class="ag-pe">') :]
    assert "Conferido em 23/11. A pauta pode mudar." in pe
    assert '<a href="edicoes/agenda.html">Agenda completa</a>' in pe
    assert pe.count("<a ") == 1  # as abas só entram no pé quando estão publicadas
    config = default_config()
    config.congresso.publicado = config.tribunais.publicado = True
    pe = _bloco(_home(_edicao(_cheio()), config))
    pe = pe[pe.index('<p class="ag-pe">') :]
    assert '<a href="edicoes/congresso.html">Raio-X do Congresso</a>' in pe
    assert '<a href="edicoes/tribunais.html">Raio-X dos Tribunais</a>' in pe
    # sem a página completa (a renderização falhou e ela nunca existiu), o pé fica sem o link
    sem = _bloco(_home(_edicao(_cheio()), agenda_pagina=False))
    assert "agenda.html" not in sem and "A pauta pode mudar." in sem
    # a página completa: o aviso, a régua, as fontes com a data e os limites
    texto = _texto(_conteudo(pagina))
    assert "Pautado não é votado nem julgado" in texto and "Conferido em 23/11. A pauta pode mudar." in texto
    for trecho in ("Como ler", "A régua", "As fontes e a data dos dados", "O que cada fonte permite dizer", "O que ficou de fora", "No radar"):
        assert trecho in texto, trecho
    assert "dados de 23/11" in texto and "Por que está aqui: Plenário (30)" in texto
    assert "Situação oficial:" in texto and "Relatoria:" in texto
    assert "menção direta encontrada em 3 veículos" in texto and f'href="2026-09-29.html#s-{LEAD_ID}"' in pagina


def test_dias_levam_a_data_para_o_navegador_marcar_hoje_e_amanha(home, pagina):
    bloco = _bloco(home)
    assert re.findall(r'data-ag-dia="([^"]+)"', bloco)[:2] == ["2026-11-24", "2026-11-25"]
    assert "HOJE" not in _texto(bloco) and "AMANHÃ" not in _texto(bloco)  # sem script, só as datas
    for html in (home, pagina):
        assert "function setupAgenda()" in html and "America/Sao_Paulo" in html and html.count("setupAgenda();") == 1
    assert 'data-ag-dia="2026-11-24"' in pagina


def test_midia_so_aparece_com_ligacao_e_aponta_para_materia_real(home):
    bloco = _bloco(home)
    assert f'<a class="ag-md" href="#s-{LEAD_ID}">na mídia: 3 veículos</a>' in bloco
    assert '<a class="ag-md" href="edicoes/pesquisa.html?q=PL%206461/2019">na mídia: 2 veículos</a>' in bloco
    assert "materia-que-nao-existe" not in home
    assert bloco.count("na mídia:") == 2  # item sem ligação fica calado
    ids = parse(home).ids
    for alvo in re.findall(r'href="#([^"]+)"', bloco):
        assert ids[alvo] == 1, alvo
    assert "na mídia" not in _bloco(_home(_edicao(_cheio())))


def test_chamada_ligada_ganha_o_rotulo_na_agenda(home):
    view = build_view(_edicao(_com_midia()), default_config())
    servico = view.manchete.servico
    assert (servico[-1].texto, servico[-1].tipo, servico[-1].href) == ("na agenda: qua 25", "agenda", "#agenda")
    assert all(c.servico == [] or c.servico[-1].tipo != "agenda" for c in view.chamadas)
    assert '<a href="#agenda">na agenda: qua 25</a>' in home and home.count("na agenda:") == 1
    assert "na agenda:" not in _home(_edicao(_cheio()))


# ── expressões proibidas ─────────────────────────────────────────────────────


@pytest.mark.parametrize("retrato", ["cheio", "um_lado", "sem_pauta"])
def test_nenhuma_expressao_proibida_na_tela(retrato):
    escolhido = {
        "cheio": _com_midia,
        "um_lado": lambda: montar.montar(fx.camara_encerrada(), None, TERCA, REGRAS),
        "sem_pauta": lambda: montar.montar(fx.congresso(), fx.tribunais(), "2026-12-05", REGRAS),
    }[retrato]()
    edicao = _edicao(escolhido)
    pagina = _pagina(edicao)
    assert pagina is not None
    for texto in (_texto(_bloco(_home(edicao))), _texto(_conteudo(pagina))):
        baixo = texto.lower()
        for expressao in (*regras.EXPRESSOES_PROIBIDAS, "previsão", "chance", "provável"):
            assert expressao not in baixo, expressao


# ── estados ──────────────────────────────────────────────────────────────────


def test_estado_sem_pauta_valida_e_um_lado_so():
    sem_pauta = _edicao(montar.montar(fx.congresso(), fx.tribunais(), "2026-12-05", REGRAS))
    home = _home(sem_pauta)
    bloco = _bloco(home)
    assert parse(home).errors == [] and "Sem pauta válida desde 23/11." in _texto(bloco)
    assert 'class="ag-dia' not in bloco and '<p class="ag-pe">' in bloco
    pagina = _pagina(sem_pauta)
    assert pagina is not None and parse(pagina).errors == []
    assert "Sem pauta válida desde 23/11." in _texto(_conteudo(pagina)) and "Como ler" in pagina
    um_lado = _edicao(montar.montar(fx.camara_encerrada(), None, TERCA, REGRAS))
    home = _home(um_lado)
    assert parse(home).errors == [] and "STF e STJ: pauta indisponível nesta edição." in _texto(_bloco(home))
    assert parse(_pagina(um_lado) or "<").errors == []


# ── marca sem agenda e defesa ────────────────────────────────────────────────


def test_marca_sem_agenda_sai_sem_nada():
    config = pbcv_config()
    assert not config.agenda_ativo
    com, sem = _edicao(_com_midia()), _edicao(None)
    for render in (_home, _copia):
        html = render(com, config)
        assert html == render(sem, config)
        assert 'id="agenda"' not in html and "na agenda:" not in html and "setupAgenda" not in html and ".ag-cols" not in html
    assert _pagina(com, config) is None


@pytest.mark.parametrize(
    "malformado",
    [
        "lixo",
        ["lista"],
        {},
        {"versao": 99, "dias": []},
        {"versao": 1, "dias": "não é lista"},
        {"versao": 1, "dias": [{"linhas": [{"itens": [{"nota": "alta"}]}]}]},
        {"versao": 1, "pe": {"fontes": [1, 2]}},
        {"versao": True},
    ],
)
def test_retrato_malformado_nao_derruba_a_pagina_da_edicao(malformado):
    referencia = _home(_edicao(None))
    edicao = _edicao(malformado)
    assert _home(edicao) == referencia
    assert _copia(edicao) == _copia(_edicao(None))
    assert _pagina(edicao) is None


def test_retrato_valido_e_vazio_nao_desenha_o_bloco():
    assert _home(_edicao({"versao": 1})) == _home(_edicao(None))
    so_dia_sem_rotulo = replace(_cheio(), dias=(DiaTela(data="não é data"),), frases=(), decidido=())
    assert tela.bloco_da_home(so_dia_sem_rotulo, materias=(), pasta="") is None


def test_erro_ao_montar_o_bloco_nao_derruba_a_pagina_da_edicao(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    def falha(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("defeito forçado no bloco")

    monkeypatch.setattr(tela, "bloco_da_home", falha)
    with caplog.at_level("WARNING"):
        html = _home(_edicao(_com_midia()))
    assert html == _home(_edicao(None))
    assert "Agenda da semana fora da página da edição" in caplog.text


# ── publish ──────────────────────────────────────────────────────────────────


def _publicar(edicao: Edition, out: Path) -> dict[str, Path]:
    return pipeline.publish(
        edicao, make_config(), out_dir=out, subject="Assunto", email_html="<html>e-mail</html>", email_text="e-mail"
    )


def test_excecao_no_renderizador_da_agenda_nao_derruba_o_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    def falha(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("defeito forçado na página da agenda")

    edicao = make_edition()
    edicao.agenda = _cheio().para_dict()
    referencia = _publicar(copy.deepcopy(edicao), tmp_path / "ok")
    assert referencia["agenda"] == tmp_path / "ok" / "edicoes" / "agenda.html" and referencia["agenda"].is_file()

    monkeypatch.setattr(pipeline.agenda_pagina, "render_agenda_page", falha)
    with caplog.at_level("WARNING"):
        paths = _publicar(copy.deepcopy(edicao), tmp_path / "falha")
    assert "a página completa não foi renderizada" in caplog.text
    assert set(paths) == set(referencia) - {"agenda"}
    assert not (tmp_path / "falha" / "edicoes" / "agenda.html").exists()
    home = (tmp_path / "falha" / "index.html").read_text(encoding="utf-8")
    assert parse(home).errors == [] and 'id="agenda"' in home  # a edição sai, com o bloco
    assert "agenda.html" not in home  # e sem link para a página que não existe
    assert 'href="edicoes/agenda.html"' in (tmp_path / "ok" / "index.html").read_text(encoding="utf-8")

    # A página que já existia fica como estava quando a renderização falha, mas a home do dia
    # não aponta para ela: é de outro dia, com outra data de conferência.
    paths = _publicar(copy.deepcopy(edicao), tmp_path / "ok")
    assert "agenda" not in paths
    assert (tmp_path / "ok" / "edicoes" / "agenda.html").read_bytes() == referencia["agenda"].read_bytes()
    assert "agenda.html" not in (tmp_path / "ok" / "index.html").read_text(encoding="utf-8")


def test_erro_ao_desenhar_o_bloco_da_agenda_nao_derruba_a_pagina(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """Um defeito no desenho do bloco (o modelo é estrito) não derruba a home: ela sai sem a
    agenda e sem as marcas "na agenda", como numa edição sem retrato."""
    from qijournal.render import web

    edicao = make_edition()
    edicao.agenda = _cheio().para_dict()
    original = web.build_view

    def view_quebrada(edition: Any, *args: Any, **kwargs: Any) -> Any:
        v = original(edition, *args, **kwargs)
        if v.agenda is not None:
            v.agenda = object()  # o modelo vai pedir campos que este objeto não tem
        return v

    monkeypatch.setattr(web, "build_view", view_quebrada)
    with caplog.at_level("WARNING"):
        home = web.render_edition_page(edicao, make_config(), home_href="./", archive_href="edicoes/")
    assert "o bloco não foi desenhado" in caplog.text
    assert parse(home).errors == [] and 'id="agenda"' not in home and "na agenda:" not in home
