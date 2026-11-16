"""A tela da aba "Cota parlamentar" com período (os modelos congresso_cota.html.j2,
congresso_cota.css.j2 e congresso_cota.js.j2): o que a página mostra sem script, a unidade e
a janela escritas ao lado de cada número, o limite mensal fora de cena enquanto a tabela não
for conferida, e o script incluído, sem montar HTML.

No molde de ``test_congresso_papel.py``: a página inteira é gerada com os dados fabricados,
sem rede. O painel da cota da Câmara é trocado pelo de ``test_congresso_cota_painel.py``
(lançamentos feitos à mão: fev a set/2023, 8 meses fechados, R$ 125.700, quatro pontos),
pequeno o bastante para conferir cada número na cabeça.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from qijournal.config import Config, load_config
from qijournal.congresso import dados
from qijournal.render import congresso as pagina
from tests.fixtures.congresso import fabrica
from tests.test_congresso_cota_painel import _analisar

MODELOS = Path(pagina.__file__).parent / "templates"
SCRIPT = (MODELOS / "congresso_cota.js.j2").read_text(encoding="utf-8")
NOVOS = ("aberto", "meses", "recentes", "pico", "marcos", "q1", "q3", "n_pontos", "todos", "fora", "meio", "n_concentrados", "tetos", "sigepa_ate")


def _gerar(data_dir: Path) -> str:
    config: Config = load_config(env={})
    html = pagina.render_congresso_page(config, data_dir, home_href="../", archive_href="./")
    assert html is not None
    return html


def _trocar_cota(data_dir: Path, casa: str, painel: dict) -> None:
    arquivo = dados.pasta_de(data_dir) / f"casa-{casa}.json"
    conteudo = json.loads(arquivo.read_text(encoding="utf-8"))
    conteudo["cota"] = painel
    arquivo.write_text(json.dumps(conteudo, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(scope="module")
def pasta(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A pasta de dados fabricada, com a página dos dados fabricados já possível de gerar."""
    raiz = tmp_path_factory.mktemp("cota-tela")
    data_dir = raiz / "data"
    (data_dir / "busca").mkdir(parents=True)
    (data_dir / "busca" / "2026-11-12.json").write_text(json.dumps(fabrica.busca_do_dia()), encoding="utf-8")
    dados.atualizar(raiz, data_dir, raiz / "cache", dados.Opcoes(hoje=fabrica.HOJE, projetos_desde=2026), fetch=fabrica.FetchFalso(), regras=fabrica.regras_de_teste())
    return data_dir


@pytest.fixture(scope="module")
def html_fabrica(pasta: Path) -> str:
    """A página com a cota fabricada do jeito que o gerador a gravou (poucos meses: ninguém no gráfico)."""
    return _gerar(pasta)


@pytest.fixture(scope="module")
def html(pasta: Path, html_fabrica: str) -> str:
    """A mesma página com o painel feito à mão no lugar da cota da Câmara."""
    _trocar_cota(pasta, "cd", {**_analisar().painel, "sem_despesa": []})
    return _gerar(pasta)


def _vista(html: str) -> str:
    """A seção da aba (as duas Casas)."""
    return html.split('data-vista="cota"', 2)[2].split('<section class="az-sec', 1)[0]


def _camara(html: str) -> str:
    return _vista(html).split('id="cg-cota-cd"', 1)[1].split('id="cg-cota-sf"', 1)[0]


def _zona_a(html: str) -> str:
    return _camara(html).split('class="cg-co-a"', 1)[1].split('class="cg-co-z"', 1)[0]


def _texto(trecho: str) -> str:
    """O que se lê: sem as marcas e com os espaços juntos."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", trecho)).strip()


# ── sem script, a aba inteira no estado padrão ───────────────────────────────


def test_a_frase_da_janela_vem_escrita(html: str):
    a = _zona_a(html)
    frase = a.split('class="cg-co-ve"', 1)[1].split("</p>", 1)[0]
    assert 'role="status"' in frase and 'aria-live="polite"' in frase
    assert _texto("<" + frase) == (
        "Você está vendo: Câmara · fev a set/2023 (8 meses fechados) · todas as despesas · todos os deputados · os 2 últimos meses ainda recebem notas"
    )


def test_os_tres_numeros_com_a_unidade_na_mesma_linha(html: str):
    a = _zona_a(html)
    numeros = a.split('class="cg-pa-ms cg-co-ns"', 1)[1].split("</ul>", 1)[0]
    for trecho in (
        '<span class="cg-pa-m-n"><b>R$ 125,7 mil</b> no período</span>',  # quanto: o total e a janela
        '<span class="cg-pa-m-f">reembolsados em 8 meses fechados</span>',
        '<span class="cg-pa-m-x">R$ 15,7 mil por mês</span>',
        '<span class="cg-pa-m-n"><b>R$ 2.500</b> por mês</span>',  # quem: o do meio, por mês
        '<span class="cg-pa-m-f">gasta o deputado do meio</span>',
        '<span class="cg-pa-m-x">metade fica entre R$ 1.800 e R$ 3.250</span>',
        '<span class="cg-pa-m-n"><b>85%</b> do total</span>',  # com o quê: a maior despesa
        '<span class="cg-pa-m-f">vai para divulgação do mandato</span>',
        '<span class="cg-pa-m-x">R$ 106,5 mil no período</span>',
        'data-n="quanto"', 'data-n="quem"', 'data-n="oque" data-cat="divulgacao"',
    ):  # fmt: skip
        assert trecho in numeros, trecho
    assert numeros.count("<li>") == 3
    # o primeiro número não é botão (não leva a lugar nenhum); os outros dois só ganham ação com o script
    assert '<div class="cg-pa-m cg-co-n" data-n="quanto">' in numeros and numeros.count("<button type=\"button\" class=\"cg-pa-m cg-co-n\"") == 2


def test_sem_script_os_graficos_e_as_tabelas_estao_no_html(html: str):
    a = _zona_a(html)
    # Quando: uma coluna por mês fechado, com a tabela gêmea
    assert re.findall(r'<i data-mes="(\d{4}-\d{2})"', a) == ["2023-02", "2023-03", "2023-04", "2023-05", "2023-06", "2023-07", "2023-08", "2023-09"]
    assert a.count('class="cg-co-r"') >= 2 and ">ainda recebem notas</b>" in a  # os meses recentes, mais claros e rotulados
    assert "Mês a mês, em tabela" in a and "<th scope=\"col\">R$ no mês</th>" in a and "Quantos tiveram despesa" in a
    # Com o quê: uma barra por despesa, com o id que vai no endereço
    assert re.findall(r'class="cg-co-b" data-cat="([^"]+)"', a)[:2] == ["divulgacao", "telefonia"]
    assert "Com o quê <span data-t=\"oque\">parte do total, de fev a set/2023</span>" in a
    # Quem: um ponto por parlamentar em exercício com meses bastantes (data-p: o clique não abre o dossiê)
    pontos = a.split('class="cg-co-pts"', 1)[1].split("</div>", 1)[0]
    assert sorted(re.findall(r'data-p="(cd-\d+)"', pontos)) == ["cd-1", "cd-2", "cd-3", "cd-6"]
    assert "data-k=" not in pontos and pontos.count("cg-co-pf") == 1  # um só em foco: o do meio
    assert "R$ por mês com despesa, de fev a set/2023" in a and "mediana R$ 2.500" in a and "metade dos deputados" in a
    # o cartão do parlamentar do meio: a medida, o total e os meses, juntos
    cartao = a.split('class="cg-pa-card cg-co-card"', 1)[1].split("</article>", 1)[0]
    lido = _texto("<" + cartao)
    assert 'aria-label="Deputado em foco"' in cartao
    assert "Em foco o deputado do meio" in lido and "Bia Reis PL-RJ" in lido
    assert "R$ 2.000 por mês com despesa" in lido and "R$ 16.000 no período, em 8 de 8 meses" in lido
    assert "Com o quê" in lido and "Mês a mês" in lido and "Abrir o dossiê" in lido
    assert 'href="#p=cd-2" data-k="cd-2"' in cartao  # o nome abre o dossiê
    # as pontas e a tabela de todos
    assert "As pontas <span data-t=\"pontas\">R$ por mês com despesa, de fev a set/2023</span>" in a
    assert "Todos os 4, em tabela" in a and '<th scope="col" data-ord="v">R$ por mês com despesa</th>' in a
    assert '<th scope="col" data-ord="m">Meses com despesa</th>' in a and '<th scope="col" data-ord="t">R$ no período</th>' in a


def test_nenhum_valor_de_pessoa_aparece_sozinho(html: str):
    """Nas pontas, cada pessoa leva a medida por mês, o total e "N de M meses"."""
    a = _zona_a(html)
    pontas = a.split('class="cg-co-pls', 1)[1].split('<details class="cg-co-tb"', 1)[0]
    linhas = re.findall(r'<li data-p="(cd-\d+)"[^>]*>(.*?)</li>', pontas, re.S)
    assert [k for k, _ in linhas] == ["cd-6", "cd-3", "cd-2", "cd-1"]  # do que mais gasta ao que menos gasta
    for _k, linha in linhas:
        lido = _texto(linha)
        assert re.search(r"R\$ [\d.]+(,\d\d)? por mês R\$ [\d.]+(,\d\d)? em \d+ de 8 meses", lido), lido
    assert "R$ 4.000 por mês R$ 24.000 em 6 de 8 meses" in _texto(linhas[0][1])
    # a tabela de todos: a medida, os meses e o total em colunas com nome
    corpo = a.split('class="an-tbl cg-tb cg-co-t cg-co-tt"', 1)[1].split("</tbody>", 1)[0].split("<tbody>", 1)[1]
    assert corpo.count("<tr") == 4
    assert "<td>1º</td>" in corpo and "<td>R$ 4.000</td><td>6 de 8</td><td>R$ 24.000</td>" in corpo


def test_sem_script_nenhum_controle_morto_aparece(html: str):
    a = _zona_a(html)
    for trecho in (
        '<form class="cg-co-fil" aria-label="Período e filtros da cota" hidden>',  # os filtros só aparecem com o arquivo mensal
        '<form class="cg-co-ach" role="search" hidden>',  # "Achar um nome"
        '<div class="cg-pa-nav cg-co-nav" hidden>',  # ‹ gasta menos | gasta mais ›
        '<p class="cg-co-falha" role="status" hidden>',  # a frase de falha
        '<p class="cg-co-vz" role="status" hidden>',  # a frase do recorte vazio
    ):
        assert trecho in a, trecho
    assert "Não foi possível carregar os dados para mudar o período e os filtros. A página mostra a legislatura inteira." in a
    # os dois números e as barras só ganham mão e ação com o script carregado
    css = (MODELOS / "congresso_cota.css.j2").read_text(encoding="utf-8")
    assert ".cg-co:not(.cg-co-on) .cg-co-n{cursor:default}" in css and ".cg-co:not(.cg-co-on) .cg-co-n .cg-pa-m-ir{display:none}" in css
    assert ".cg-co-on .cg-co-b{cursor:pointer}" in css


def test_para_conferir_diz_que_e_a_legislatura_inteira(html: str):
    b = _camara(html).split('class="cg-co-z"', 1)[1]
    assert "Para conferir <span>achados da legislatura inteira (fev a set/2023). Não mudam com o período nem com os filtros acima.</span>" in b
    blocos = re.findall(r'<details class="cg-co-zd" open>\s*<summary>(.*?)</summary>', b, re.S)
    assert blocos and all("na legislatura inteira (fev a set/2023)" in _texto(x) for x in blocos)  # cada bloco repete a janela
    assert "Quem mais recebe" in _texto(blocos[0]) and "Principal despesa" in b
    assert "Como esta conta é feita." in b and 'href="#cg-metodo"' in b
    # a frase da janela fica dentro da zona A: ao chegar aqui, ela não vale mais
    assert 'class="cg-co-ve"' not in b


# ── o limite mensal por estado fica de fora ──────────────────────────────────


def test_sem_a_tabela_conferida_nada_fala_em_teto(html: str):
    vista = _vista(html)
    assert "teto" not in vista.lower()  # nem no texto, nem nas dicas, nem nos atributos
    assert "até um limite por mês que muda conforme o estado" in vista  # a linha fina diz a verdade, sem número
    assert "O limite mensal da cota muda por estado e não entra nesta conta." in vista  # a dica do segundo número
    assert "O limite mensal muda por estado e não entra nesta conta." in vista  # "Como esta conta é feita"
    assert 'data-ord="lim"' not in vista and 'class="cg-co-lim"' not in vista and 'class="cg-co-lim-t"' not in vista  # a coluna, o colchete, a frase
    assert "teto" not in SCRIPT.lower()  # o script não escreve nada sobre o limite


def test_o_como_ler_explica_a_aba_sem_citar_valor_de_limite(html: str):
    item = html.split("<dt>A aba da cota: período, medidas e filtros</dt><dd>", 1)[1].split("</dd>", 1)[0]
    for trecho in (
        "o mês em curso fica fora",
        "R$ no período", "R$ por mês", "R$ por mês com despesa",
        "6 meses ou mais, ou em metade do período",
        "“Para conferir” é sempre a legislatura inteira",
        "O limite mensal da cota muda por estado e não entra em nenhuma conta desta aba.",
    ):  # fmt: skip
        assert trecho in item, trecho
    assert "teto" not in item.lower() and not re.search(r"limite[^.]*R\$", item)


# ── o script ─────────────────────────────────────────────────────────────────


def test_o_script_da_cota_esta_na_pagina(html: str):
    for trecho in ("aoAbrirVista.cota = function", "aoEndereco.cota = function", "var cotaDaCasa = {};", ".cg-co-ve{"):
        assert trecho in html, trecho
    manifesto = json.loads(html.split('<script type="application/json" id="cgManifesto">', 1)[1].split("</script>", 1)[0])
    assert manifesto["cotaArq"] == "../data/congresso/cota-"  # o script junta "cd.json" ou "sf.json"
    assert manifesto["cotaRegras"]["minimoMeses"] == 6 and manifesto["cotaRegras"]["faixaPct"] == 10
    # o bloco de cada Casa diz ao script as palavras dela
    assert '<div class="cg-co" data-cota="cd" data-nome="Câmara" data-um="deputado" data-uns="deputados"' in html


def test_o_script_da_cota_nao_monta_html_e_e_do_estilo_da_casa():
    assert "innerHTML" not in SCRIPT and "outerHTML" not in SCRIPT and "insertAdjacentHTML" not in SCRIPT
    assert "=>" not in SCRIPT and "`" not in SCRIPT and not re.search(r"\b(let|const)\s", SCRIPT)  # o mesmo ES5 dos outros arquivos
    # o endereço guarda o estado, sem criar entrada no histórico a cada filtro
    assert "replaceState" in SCRIPT and "pushState" not in SCRIPT
    # as colunas usam data-mes e os pontos, data-p: data-m e data-k abrem fichas no clique geral da página
    modelo = (MODELOS / "congresso_cota.html.j2").read_text(encoding="utf-8")
    assert '<i data-mes="{{ x.m }}"' in modelo and " data-m=" not in modelo
    assert '<i data-p="{{ p.p }}"' in modelo


# ── os casos de borda da página ──────────────────────────────────────────────


def test_sem_ninguem_no_grafico_a_aba_nao_escreve_frase_pela_metade(html_fabrica: str):
    """A cota fabricada tem poucos meses: nenhum deputado chega aos 6 meses com despesa."""
    a = _zona_a(html_fabrica)
    lido = _texto("<" + a)
    assert "Você está vendo: Câmara · jan a jun/2026 (6 meses fechados) · todas as despesas · todos os deputados" in lido
    assert "R$ 90,0 mil no período" in lido and "reembolsados em 6 meses fechados" in lido
    assert "Nenhum deputado em exercício tem despesa em meses bastantes para entrar no gráfico." in lido
    assert "Nenhum deputado em exercício tem despesa em meses bastantes para entrar nas listas." in lido
    assert "metade fica entre e" not in lido and "Todos os 0, do que" not in lido
    assert '<details class="cg-co-tb" data-tab="todos" hidden>' in a  # a tabela vazia não aparece
    assert "teto" not in _vista(html_fabrica).lower()


def test_painel_gravado_antes_da_aba_nova_cai_no_desenho_antigo(pasta: Path, html: str):
    antigo = {k: v for k, v in _analisar().painel.items() if k not in NOVOS} | {"sem_despesa": []}
    _trocar_cota(pasta, "cd", antigo)
    try:
        camara = _camara(_gerar(pasta))
    finally:
        _trocar_cota(pasta, "cd", {**_analisar().painel, "sem_despesa": []})
    assert 'data-cota="cd"' not in camara  # sem a série mensal, não há painel para o script assumir
    for trecho in ("Quem mais gasta", "Quem menos gasta", "Para onde vai", "Quem mais recebe", "pediram <b>R$ 125.700</b> de reembolso da cota"):
        assert trecho in camara, trecho
