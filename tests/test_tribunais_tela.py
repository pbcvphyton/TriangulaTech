"""A aba Tribunais na tela: a página (``render/tribunais.py`` + ``tribunais*.j2``) e o botão no
cabeçalho das outras páginas.

Tudo contra a aba de mentira de ``tests/fixtures/tribunais/fabrica.py`` (nomes e números inventados,
réguas de verdade de ``config/tribunais.yaml``), sem rede.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from qijournal.config import Config, load_config
from qijournal.render import tribunais as pagina
from qijournal.render.web import render_archive_index, render_edition_page
from qijournal.tribunais import modelo
from tests.fixtures.pipeline.factory import make_edition
from tests.fixtures.tribunais import fabrica

MODELOS = Path(pagina.__file__).resolve().parent / "templates"
VISTAS = ("resumo", "ministros", "assuntos", "partes", "fila", "metodo")


def _config() -> Config:
    config = load_config(env={})
    config.congresso.publicado = False
    config.tribunais.publicado = False
    return config


def _site(raiz: Path, **kw) -> tuple[Path, Path | None]:
    (raiz / "data").mkdir(parents=True, exist_ok=True)
    fabrica.gravar(raiz / "data", **kw)
    return raiz, pagina.gravar_pagina(raiz, _config())


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    raiz, caminho = _site(tmp_path_factory.mktemp("tribunais"))
    assert caminho == raiz / "edicoes" / "tribunais.html"
    return raiz, caminho.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def html(site: tuple[Path, str]) -> str:
    return site[1]


def _manifesto(html: str) -> dict:
    return json.loads(re.search(r'<script type="application/json" id="tbManifesto">(.*?)</script>', html, re.DOTALL).group(1))


def _paineis(raiz: Path) -> dict[str, dict]:
    return {t: json.loads((raiz / "data" / "tribunais" / f"{t}.json").read_text(encoding="utf-8")) for t in ("stf", "stj")}


# ── a página ─────────────────────────────────────────────────────────────────


def test_a_pagina_tem_a_barra_do_tribunal_e_as_seis_abas(html: str):
    barra = re.search(r'<div class="cg-barra" id="tbBarra">.*?</nav>', html, re.DOTALL).group(0)
    assert re.findall(r'data-trib="(\w+)"', barra) == ["stf", "stj"]  # STF | STJ, nessa ordem
    assert 'aria-pressed="true" title="Supremo Tribunal Federal">STF <span>10</span>' in barra  # o padrão: o primeiro com o acervo do dia
    nomes = re.findall(r'data-vista="(\w+)">([^<]+)</a>', barra)
    assert nomes == [("resumo", "Resumo"), ("ministros", "Ministros"), ("assuntos", "Assuntos"), ("partes", "Partes"), ("fila", "A fila"), ("metodo", "Como ler")]
    for vista in VISTAS:
        assert f'data-vista="{vista}" id="tb-v-{vista}"' in html
    # sem script, os dois tribunais saem em sequência em cada aba (o seletor de tribunal só aparece com script)
    for trib in ("stf", "stj"):
        for vista in ("resumo", "ministros", "assuntos", "partes", "fila"):
            assert f'data-tribunal="{trib}" id="tb-{vista}-{trib}"' in html
    assert 'id="tbTribs" role="group" aria-label="Tribunal" hidden' in html


def test_o_manifesto_leva_o_que_o_script_le(html: str):
    m = _manifesto(html)
    assert m["v"] == 1 and m["padrao"] == "stf" and m["tribs"] == ["stf", "stj"] and m["comDados"] == ["stf", "stj"]
    assert m["base"] == "../data/tribunais/ministros/" and m["dados"] == "../data/tribunais/"
    assert list(m["areas"]) == list(modelo.carregar_regras().ids_areas)
    assert m["t"]["stj"]["pares"] == "a Turma" and m["t"]["stf"]["pares"] == "o tribunal"
    assert m["classes"]["stj"]["REsp"] == "Recurso Especial"
    assert set(m["gloss"]) >= {"mesa", "valor", "idade", "sem_decisao_stj", "sem_decisao_stf"}
    assert "<" not in re.search(r'id="tbManifesto">(.*?)</script>', html, re.DOTALL).group(1)  # escapado


def test_o_script_nunca_monta_html_a_partir_de_texto(html: str):
    for nome in ("tribunais.js.j2", "tribunais.html.j2"):
        texto = (MODELOS / nome).read_text(encoding="utf-8")
        for proibido in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            assert proibido not in texto, (nome, proibido)
    script = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)[-1]
    assert "innerHTML" not in script and "textContent" in script


@pytest.mark.skipif(shutil.which("node") is None, reason="sem node para conferir a sintaxe do script")
def test_o_script_da_pagina_tem_sintaxe_valida(html: str, tmp_path: Path):
    script = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)[-1]
    arquivo = tmp_path / "tribunais.js"
    arquivo.write_text(script, encoding="utf-8")
    saida = subprocess.run(["node", "--check", str(arquivo)], capture_output=True, text=True, timeout=30, check=False)
    assert saida.returncode == 0, saida.stderr


def test_a_pagina_cabe_no_teto(site: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _raiz, html = site
    assert len(html.encode("utf-8")) <= 1_500_000 == pagina.TETO_BYTES
    # acima do teto, a página não é gravada (fica a anterior)
    raiz = tmp_path / "site"
    (raiz / "edicoes").mkdir(parents=True)
    (raiz / "edicoes" / "tribunais.html").write_text("<html>anterior</html>", encoding="utf-8")
    monkeypatch.setattr(pagina, "TETO_BYTES", 10_000)
    with pytest.raises(pagina.PaginaGrandeDemais):  # OP-10: a execução fica vermelha e nada é publicado
        _site(raiz)
    assert (raiz / "edicoes" / "tribunais.html").read_text(encoding="utf-8") == "<html>anterior</html>"


def test_sem_dados_ou_de_outra_versao_nao_ha_pagina(tmp_path: Path):
    assert pagina.carregar(tmp_path / "data") is None
    assert pagina.gravar_pagina(tmp_path, _config()) is None and not (tmp_path / "edicoes" / "tribunais.html").exists()
    raiz, _ = _site(tmp_path / "v2")
    meta = raiz / "data" / "tribunais" / "meta.json"
    meta.write_text(meta.read_text(encoding="utf-8").replace('"v":1', '"v":2', 1), encoding="utf-8")
    assert pagina.carregar(raiz / "data") is None


def test_a_marca_que_nao_usa_a_aba_nao_grava(tmp_path: Path):
    (tmp_path / "data").mkdir()
    fabrica.gravar(tmp_path / "data")
    config = load_config(env={"QIJ_NO_TRIBUNAIS": "1"})
    assert not config.tribunais_ativo
    assert pagina.gravar_pagina(tmp_path, config) is None


# ── a matriz ministro × área ────────────────────────────────────────────────


def test_a_matriz_e_uma_tabela_acessivel_agrupada(site: tuple[Path, str]):
    raiz, html = site
    tabela = re.search(r'<table class="tb-mx" id="tb-mx-stj"[^>]*>.*?</table>', html, re.DOTALL).group(0)
    assert "<caption>" in tabela and 'scope="col"' in tabela and 'scope="row"' in tabela and 'scope="rowgroup"' in tabela
    # agrupada por Seção no STJ, com a linha "média da Seção" em cada grupo de Turma (e sem média na Presidência)
    assert re.findall(r'<tbody data-grupo="(\w*)">', tabela) == ["s1", "s2", "s3", "pr", "fora"]
    assert tabela.count("média da Seção") == 3
    # ▲/▽ só onde o JSON marca: tantas marcas quantas células marcadas nas colunas à mostra
    painel = _paineis(raiz)["stj"]
    colunas = re.findall(r'<th scope="col" class="tb-a-(\w+)" data-a=', tabela)
    marcas = sum(1 for m in painel["ministros"] for a in colunas if m["areas"][a]["marca"] == 1)
    assert marcas and tabela.count(" tb-up") == marcas
    assert tabela.count(" tb-dn") == sum(1 for m in painel["ministros"] for a in colunas if m["areas"][a]["marca"] == -1)
    assert tabela.count('<span class="sr-only"> (bem acima da Turma)</span>') == marcas  # o glifo tem texto
    # a área sem processo relevante em nenhuma mesa fica fora das colunas, escrita na nota
    assert "constitucional" not in colunas and "Áreas com menos de 0,5% em todas as mesas ficam fora das colunas" in html
    # STF: a média do tribunal sem a Presidência é a linha de comparação, no alto
    stf = re.search(r'<table class="tb-mx" id="tb-mx-stf"[^>]*>.*?</table>', html, re.DOTALL).group(0)
    assert stf.index("média do tribunal, sem a Presidência") < stf.index('data-grupo="t1"')


def test_cada_coluna_da_matriz_cabe_o_nome_da_area(html: str):
    """O cabeçalho quebra entre palavras, nunca no meio: cada coluna tem largura para a palavra mais
    longa do nome curto, e a tabela declara a soma (a moldura rola quando não cabe; com um grupo à
    vista, o script refaz a soma com as colunas que ficaram)."""
    for trib in ("stj", "stf"):
        tabela = re.search(rf'<table class="tb-mx" id="tb-mx-{trib}" style="--tb-cols-w:(\d+)px">.*?</table>', html, re.DOTALL)
        assert tabela, trib
        cols = re.findall(r'<th scope="col" class="tb-a-\w+" data-a="\w+" data-w="(\d+)" style="width:(\d+)px" title="[^"]*"><a [^>]*>([^<]+)</a>', tabela.group(0))
        assert len(cols) >= 8
        for w, largura, nome in cols:
            assert w == largura and int(w) >= max(pagina.COLUNA_PX_MINIMA, 6 * max(len(p) for p in nome.split())), (trib, nome, w)
        assert int(tabela.group(1)) == sum(int(w) + 2 for w, _, _ in cols)
    assert "setProperty('--tb-cols-w'" in html


def test_a_celula_usa_a_rampa_do_config(site: tuple[Path, str]):
    raiz, html = site
    painel = _paineis(raiz)["stj"]
    m = painel["ministros"][0]
    rampa = modelo.carregar_regras().limiares["rampa"]
    tabela = re.search(r'<table class="tb-mx" id="tb-mx-stj"[^>]*>.*?</table>', html, re.DOTALL).group(0)
    conferidas = 0
    for a, c in m["areas"].items():
        if not c["n"] or f'<th scope="col" class="tb-a-{a}" data-a="{a}"' not in tabela:
            continue
        nivel = sum(1 for corte in rampa if c["p"] >= corte)
        assert re.search(rf'<td class="cg-j{nivel}[^"]*" data-a="{a}" title="{re.escape(m["nome"])}:', tabela), (a, c)
        conferidas += 1
    assert conferidas >= 5
    assert "menos de 2%" in html and "25% ou mais" in html  # a legenda sai das faixas


# ── a mesa, a folha, o valor ─────────────────────────────────────────────────


def test_uma_mesa_compacta_por_ministro_sem_script(site: tuple[Path, str]):
    raiz, html = site
    paineis = _paineis(raiz)
    ids = [m["id"] for p in paineis.values() for m in p["ministros"]]
    assert re.findall(r'<details class="tb-mesa" id="tb-mesa-([\w-]+)" data-min=', html) == [m["id"] for t in ("stf", "stj") for m in paineis[t]["ministros"]]
    assert len(ids) == 43
    # o nome na matriz leva à mesa (âncora sem script, data-min com script)
    m = paineis["stj"]["ministros"][4]
    assert f'<a href="#tb-mesa-{m["id"]}" data-min="{m["id"]}"' in html
    # a lista da mesa e as setas só existem com script
    assert 'class="tb-me-l" aria-label="Ministros do STJ" data-com-script hidden' in html
    assert 'class="tb-cd-nv" data-com-script hidden' in html
    # o dossiê é lido sob demanda; o cartão traz o lugar onde ele entra
    assert html.count('class="tb-dz" data-dossie=') == 43
    for mid in ids:
        assert (raiz / "data" / "tribunais" / "ministros" / f"{mid}.json").is_file()


def test_a_folha_so_existe_para_os_processos_listados(site: tuple[Path, str]):
    raiz, html = site
    paineis = _paineis(raiz)
    listados = {f["id"] for p in paineis.values() for m in p["ministros"] for f in m["mais_antigos"]}
    listados |= {f["id"] for p in paineis.values() for f in p["fila"]["mais_antigos"]}
    listados |= {v["processo"]["id"] for p in paineis.values() for v in p["fila"]["vistas"]}
    com_folha = set(re.findall(r'data-pr="([\w-]+)"', html))
    assert com_folha and com_folha <= listados
    # sem script, o número leva à página oficial do tribunal (para o leitor; nada aqui a coleta)
    f = paineis["stj"]["ministros"][0]["mais_antigos"][0]
    assert f'<a href="{f["link"].replace("&", "&amp;")}" data-pr="{f["id"]}"' in html
    assert "processo.stj.jus.br/processo/pesquisa/" in html


def test_valor_da_causa_sem_numero(html: str):
    assert "Valor da causa: as fontes abertas não publicam." in html
    assert "não publicado" in html and "R$" not in html
    assert "Com dinheiro público num dos polos" in html and "Execução fiscal" in html  # o que se mede
    assert "ente público num dos polos" not in html and "Ente público num polo" not in html  # N2: a medida não é "ente"


def test_partes_sem_pessoa_fisica_com_nome(html: str):
    assert "Pessoas físicas:" in html and "somadas, sem nome" in html
    assert "Responde: pessoa física" in html or "Recorre: pessoa física" in html  # o tipo, nunca o nome
    assert "recorre" in html and "responde" in html and 'class="tb-bf"' in html  # a borboleta


def test_o_nome_do_litigante_vem_do_mapa_de_nomes(html: str):
    """Grupo citado numa lista e fora do topo do tribunal: o nome sai de ``partes.nomes`` do painel
    (``{grupo: {nome, tipo}}``), nunca o id por extenso; o dossiê traz o seu mapa e o script o soma."""
    assert "pessoa jurídica (Cooperativa K)" in html  # fora do catálogo, a categoria não é conhecida: não se diz "empresa"
    assert "Nome Cooperativa" not in html and "nome-cooperativa-k" in _manifesto(html)["partes"]
    assert "guardarNomes(x)" in html


# ── o "Como ler" e as réguas ─────────────────────────────────────────────────


def test_o_modelo_nao_escreve_area_nem_limiar(html: str):
    regras = modelo.carregar_regras()
    modelos = "".join((MODELOS / n).read_text(encoding="utf-8") for n in ("tribunais.html.j2", "tribunais.js.j2", "tribunais.css.j2", "tribunais_areas.css.j2"))
    for a in regras.areas:
        if a.id in ("outros",):
            continue
        assert a.nome not in modelos and f'"{a.nome_curto}"' not in modelos, a.id
        assert a.nome in html  # mas a página escreve (vem do meta.json)
    for limiar in ("1,25", "0,80", "99%", "365 dias", "180 dias", "0,5%"):
        assert limiar not in modelos, limiar


def test_o_como_ler_sai_das_reguas_do_meta(tmp_path: Path):
    raiz = tmp_path
    (raiz / "data").mkdir()
    pasta = fabrica.gravar(raiz / "data")
    meta = json.loads((pasta / "meta.json").read_text(encoding="utf-8"))
    meta["reguas"]["limiares"]["comparacao"]["razao"] = [0.7, 1.4]
    meta["reguas"]["limiares"]["rampa"] = [0.03, 0.06, 0.12, 0.3]
    meta["reguas"]["limiares"]["nome_parte_tribunal"] = 25
    for a in meta["reguas"]["areas"]:
        if a["id"] == "penal":
            a["nome"] = "Penal (régua mudada)"
    (pasta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    html = pagina.gravar_pagina(raiz, _config()).read_text(encoding="utf-8")
    assert "1,4 vez ou mais (▲) ou 0,7 ou menos (▽)" in html and "1,4 vez ou mais, ou 0,7" in html
    assert "menos de 3%" in html and "30% ou mais" in html
    assert "com 25 processos ou mais no tribunal" in html
    assert "Penal (régua mudada)" in html


def test_o_como_ler_tem_as_tabelas_e_os_limites(html: str):
    metodo = re.search(r'data-vista="metodo" id="tb-v-metodo".*?</section>', html, re.DOTALL).group(0)
    for titulo in ("De onde vem", "Uma área por processo", "Só com os pares, e só com teste", "Na mesa", "Quanto vale", "Quem se repete", "O que isto não diz"):
        assert f"<dt>{titulo}</dt>" in metodo, titulo
    assert "O que entra em cada área" in metodo and "De onde vem cada número" in metodo
    assert "Família (5626)" in metodo  # o código e o nome da TPU
    assert "Fonte: STJ, Portal de Dados Abertos (CC-BY)" in metodo
    assert "casaram com a TPU até o fim do caminho" in metodo  # a cobertura do STF


# ── os estados ───────────────────────────────────────────────────────────────


def test_fonte_fora_do_ar_e_tribunal_sem_dados(tmp_path: Path):
    _, caminho = _site(tmp_path, estado="fora_do_ar", tribunais=("stj",))
    html = caminho.read_text(encoding="utf-8")
    # o acervo do STJ falhou hoje: fica o de 20/11, com o selo e a forma (■) escritos
    assert '<p class="tb-av tb-av-erro"><span class="az-selo">Dado de 20/11</span>' in html
    assert "■</i>" in html and "falhou hoje; dados de 20/11" in html
    # o STF sem dados: o botão fica (com —) e a aba diz por quê; o padrão passa a ser o STJ
    assert 'data-trib="stf" aria-pressed="false" title="Supremo Tribunal Federal">STF <span>—</span>' in html
    assert "Sem dados do STF nesta página. A coleta do acervo do STF não deu certo nenhuma vez" in html
    m = _manifesto(html)
    assert m["padrao"] == "stj" and m["comDados"] == ["stj"] and m["tribs"] == ["stf", "stj"]
    # parcial e desligada: forma e palavra, nunca só cor
    assert "◐</i>" in html and "○</i>" in html and ": desligada" in html


# ── as cores das áreas ───────────────────────────────────────────────────────


def _variaveis(css: str, escuro: bool) -> dict[str, str]:
    seletor = r':root\[data-theme="dark"\]\{([^}]*)\}' if escuro else r":root\{([^}]*)\}"
    return {nome: cor.lower() for bloco in re.findall(seletor, css) for nome, cor in re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{3,6})\b", bloco)}


def _luminancia(cor: str) -> float:
    cor = cor.lstrip("#")
    if len(cor) == 3:
        cor = "".join(c * 2 for c in cor)
    canais = [int(cor[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canais]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contraste(a: str, b: str) -> float:
    la, lb = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("escuro", [False, True])
def test_cada_area_tem_cor_com_contraste_no_claro_e_no_escuro(html: str, escuro: bool):
    """Uma cor por área do config, com 3:1 ou mais contra a página, o fundo suave e o papel da folha,
    e o texto sobre ela (a cor "on" da área) com 4,5:1 ou mais, nos dois modos."""
    css = re.search(r"<style>(.*?)</style>", html, re.DOTALL).group(1)
    cores = _variaveis(css, escuro)
    fundos = {"página": cores["--paper"], "fundo suave": cores["--soft"], "papel da folha": cores["--cg-fo-papel"]}
    regras = modelo.carregar_regras()
    com_cor = [a for a in regras.areas if a.cor]
    assert len(com_cor) == 15 and [a.id for a in regras.areas if not a.cor] == ["sem_assunto"]  # sem assunto é hachura
    for a in com_cor:
        cor = cores[f"--tb-a-{a.id}"]
        assert cor == a.cor[1 if escuro else 0].lower()
        for nome, fundo in fundos.items():
            assert _contraste(cor, fundo) >= 3, (a.id, cor, nome, fundo, round(_contraste(cor, fundo), 2))
        assert _contraste(a.texto, cor) >= 4.5, (a.id, a.texto, cor, round(_contraste(a.texto, cor), 2))
        assert f".tb-a-{a.id}{{--tb-a:var(--tb-a-{a.id});--cg-t:var(--tb-a-{a.id});--tb-on:{a.texto}}}" in css
    assert ".tb-a-sem_assunto{--tb-a:repeating-linear-gradient(" in css


def test_a_cor_que_vem_do_meta_so_passa_se_for_cor(tmp_path: Path):
    (tmp_path / "data").mkdir()
    pasta = fabrica.gravar(tmp_path / "data")
    meta = json.loads((pasta / "meta.json").read_text(encoding="utf-8"))
    meta["reguas"]["areas"][0]["cor"] = ["red;}body{display:none", "#123456"]
    meta["reguas"]["areas"][1]["texto"] = "#fff;background:url(x)"
    (pasta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    html = pagina.gravar_pagina(tmp_path, _config()).read_text(encoding="utf-8")
    assert "display:none" not in html.split("</style>")[0].split("tb-a-constitucional", 1)[1][:200]
    assert "url(x)" not in html


# ── o cabeçalho ──────────────────────────────────────────────────────────────


def test_o_botao_tribunais_na_propria_aba(html: str):
    ferramentas = re.search(r'<div class="mast-tools">.*?</div>\s*<div class="mast-l">', html, re.DOTALL).group(0)
    assert '<nav class="mt-g mt-abas" aria-label="Abas especiais">' in ferramentas
    assert '<a class="tool tool-tb" href="./tribunais.html" title="Tribunais: que ministro do STF e do STJ está com o quê" aria-label="Tribunais" aria-current="page">' in ferramentas
    assert '<div class="mt-g mt-fer">' in ferramentas and 'title="Edições anteriores"' in ferramentas and 'href="./pesquisa.html"' in ferramentas
    # a ordem do HTML é a da tela: as abas, depois as ferramentas, depois o Escuro
    assert ferramentas.index("mt-abas") < ferramentas.index("mt-fer") < ferramentas.index('id="dmBtn"')
    # o ícone: a balança, um path só
    assert 'M12 4v16M8 20h8M5 7h14' in ferramentas


def test_o_botao_cruzado_do_congresso_acende_com_a_aba_dele(tmp_path: Path):
    raiz, caminho = _site(tmp_path)
    assert "tool-cg" not in caminho.read_text(encoding="utf-8")  # sem edicoes/congresso.html
    (raiz / "edicoes" / "congresso.html").write_text("<html></html>", encoding="utf-8")
    html = pagina.gravar_pagina(raiz, _config()).read_text(encoding="utf-8")
    assert '<a class="tool tool-cg" href="./congresso.html"' in html


def test_o_botao_nas_outras_paginas_so_com_a_aba_publicada(tmp_path: Path):
    config = _config()
    edition = make_edition()
    assert not pagina.marcar_publicada(config, tmp_path)
    capa = render_edition_page(edition, config, home_href="./", archive_href="edicoes/")
    assert "tool-tb" not in capa and "tribunais.html" not in capa
    _site(tmp_path)
    assert pagina.publicada(tmp_path) and pagina.marcar_publicada(config, tmp_path)
    capa = render_edition_page(edition, config, home_href="./", archive_href="edicoes/")
    cabecalho = re.search(r'<div class="mast-tools">.*?<div class="mast-l">', capa, re.DOTALL).group(0)
    assert '<a class="tool tool-tb" href="edicoes/tribunais.html"' in cabecalho and 'aria-current="page"' not in cabecalho
    arquivada = render_edition_page(edition, config, home_href="../", archive_href="./", is_archive=True)
    assert '<a class="tool tool-tb" href="./tribunais.html"' in arquivada
    indice = render_archive_index([{"date": "2026-09-29", "href": "2026-09-29.html"}], config, home_href="../")
    cabecalho = re.search(r'<div class="mast-tools">.*?<div class="mast-l">', indice, re.DOTALL).group(0)
    assert '<a class="tool tool-tb" href="tribunais.html"' in cabecalho and 'title="Edições anteriores"' not in cabecalho
    # desligada pela variável: o botão some mesmo com a aba gravada
    desligado = load_config(env={"QIJ_NO_TRIBUNAIS": "1"})
    assert not pagina.marcar_publicada(desligado, tmp_path)
    assert "tool-tb" not in render_edition_page(edition, desligado, home_href="./", archive_href="edicoes/")


def test_o_css_do_cabecalho_em_dois_grupos(html: str):
    css = re.search(r"<style>(.*?)</style>", html, re.DOTALL).group(1)
    assert ".mt-g{display:flex;gap:6px}" in css
    assert ".mast-tools{display:grid;grid-template-columns:minmax(0,1fr) auto;justify-items:end;align-items:center}" in css
    assert "@media (min-width:761px) and (max-width:1023.98px),(min-width:1600px)" in css
    assert ".mt-abas{flex-basis:100%;justify-content:center}" in css and ".mast{padding-top:86px}" in css
    # as regras antigas de esconder rótulo pelo endereço saíram
    assert 'a[title="Edições anteriores"] span' not in css and 'a[href$="pesquisa.html"] span' not in css


def test_a_pagina_aguenta_o_que_a_fonte_nao_trouxe(tmp_path: Path):
    """O STF sem atas, pautas, decisões e temas; um ministro sem acervo; idade sem p90: a página sai,
    e diz o que falta em vez de inventar."""
    (tmp_path / "data").mkdir()
    pasta = fabrica.gravar(tmp_path / "data")
    stf = json.loads((pasta / "stf.json").read_text(encoding="utf-8"))
    for chave in ("entradas", "pautas", "decisoes", "temas"):
        stf[chave] = None
    del stf["fila"]["vistas"]
    m = stf["ministros"][0]
    m.update(processos=0, na_mesa=0, areas={}, subareas=[], partes=[], mais_antigos=[], entradas=None, pauta=None, decisoes=None, temas=None)
    m["no_tribunal"] = {"mediana": None, "p25": None, "p75": None}
    m["sem_decisao"] = {}
    (pasta / "stf.json").write_text(json.dumps(stf, ensure_ascii=False), encoding="utf-8")
    (pasta / "achados.json").write_text('{"v":1,"hoje":"2026-11-23","achados":[]}', encoding="utf-8")
    html = pagina.gravar_pagina(tmp_path, _config()).read_text(encoding="utf-8")
    bloco = re.search(r'id="tb-fila-stf".*?</section>', html, re.S).group(0)
    assert "As atas, as pautas e as decisões deste tribunal não vieram nesta atualização." in bloco
    assert "Nenhum processo público na mesa nesta lista" in html
    # sem achado, a manchete sai dos números do dia, escrita por regra
    assert re.search(r'<h4 class="az-manchete">No STF, [\d.]+ processos estão na mesa dos ministros; [\d.]+ conclusos ao relator há mais de um ano</h4>', html)


def test_a_janela_da_serie_e_dita_como_e():
    """A série das atas e das íntegras cresce no cache: enquanto não cobre o ano, o cartão diz de
    quando a quando ela vai, não "em 12 meses" (integração, 23/11/2026)."""
    assert pagina._janela(["2025-10-16", "2026-10-15"]) == ("em 12 meses", False)
    assert pagina._janela(["2026-08-01", "2026-10-15"]) == ("entre 01/08 e 15/10", False)
    assert pagina._janela(["2026-10-24", "2026-11-22"]) == ("entre 24/10 e 22/11", True)
    assert pagina._janela(None) == ("em 12 meses", False)


# ── a revisão adversarial (23/11/2026): frases, números e ética ─────────────


def _conteudo_da_aba(html: str) -> str:
    """Só o conteúdo das vistas da aba (sem o cabeçalho do site, cujo ícone do Congresso diz "o que deixa parado")."""
    return " ".join(re.findall(r'<section class="[^"]*\b(?:tb|cg)-vista\b.*?</section>', html, re.DOTALL)) or html[html.find('id="tb-v-resumo"'):]


def _texto(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_nada_de_parado_por_escolha_do_gabinete(html: str):
    """PRIV-3: a fonte não diz por que um processo espera; nenhuma frase opõe "suspenso" a "parado por escolha"."""
    aba = _conteudo_da_aba(html)
    assert "escolha do gabinete" not in aba
    assert not re.search(r"\bparad[oa]s?\b", _texto(aba))
    # J7: os motivos como a fonte os dá, sem "por sem motivo"
    assert "sem motivo identificado pela fonte" in html and "por sem motivo" not in html


def test_liminar_do_stj_nao_e_pendente(site: tuple[Path, str]):
    """J1: no STJ, ``pedidoDeLiminar`` diz só que o processo chegou com pedido de liminar: nunca
    "liminar pendente" num item do STJ; no STF a coluna é de liminar pendente."""
    raiz, html = site
    paineis = _paineis(raiz)
    com = [f for f in paineis["stj"]["fila"]["mais_antigos"] if (f.get("flags") or {}).get("liminar")]
    assert com, "a fábrica precisa de um item do STJ com flags.liminar"
    item = pagina._item_fila(com[0], "stj", pagina._reguas(json.loads((raiz / "data" / "tribunais" / "meta.json").read_text()), _config()), {}, {}, True)
    assert "liminar" not in item["texto"]
    stf = next(f for f in paineis["stf"]["fila"]["mais_antigos"] + [x for m in paineis["stf"]["ministros"] for x in m["mais_antigos"]]
               if (f.get("flags") or {}).get("liminar"))
    item = pagina._item_fila(stf, "stf", pagina._reguas(json.loads((raiz / "data" / "tribunais" / "meta.json").read_text()), _config()), {}, {}, True)
    assert "liminar pendente" in item["texto"]
    bloco = re.search(r'id="tb-fila-stj".*?</section>', html, re.DOTALL).group(0)
    assert "liminar pendente" not in bloco
    assert "Chegaram com pedido de liminar" in html and "Com liminar ou tutela pendente" in html
    assert "nomeFlag = { liminar: t === 'stj' ? 'chegou ao STJ com pedido de liminar'" in html  # a folha


def test_sem_decisao_terminativa_e_o_marco_da_conta():
    """J2 e J12: o STJ só registra a decisão terminativa; a data dita é a da conta (a mais recente
    entre a distribuição e a última decisão terminativa)."""
    assert pagina._marco_stj({"distribuicao": "2016-09-19", "ultima_decisao": "2016-06-10"}) == (
        "redistribuído em 09/2016; última decisão terminativa em 06/2016")
    assert pagina._marco_stj({"distribuicao": "2020-01-10", "ultima_decisao": "2021-03-02"}) == "última decisão terminativa em 03/2021"
    assert pagina._marco_stj({"distribuicao": "2020-01-10", "ultima_decisao": None}) == "distribuído em 01/2020"
    assert pagina._marco_stj({}) == ""
    assert "terminativa" in pagina.GLOSSARIO["sem_decisao_stj"] and "liminares, despachos" in pagina.GLOSSARIO["sem_decisao_stj"]


def test_sem_decisao_registrada_sempre_terminativa(html: str):
    texto = _texto(html)
    assert "sem decisão terminativa registrada" in texto
    assert not re.search(r"sem decisão registrada|sem decisão desde|Sem decisão há \+", texto)


def test_stf_mostra_o_ano_de_chegada_nunca_zero_dias(html: str):
    """N3 e A3: a planilha do STF só dá o ano de autuação: nenhum "0 dias desde a chegada"."""
    bloco = re.search(r'id="tb-resumo-stf".*?</section>', html, re.DOTALL)
    texto = _texto(html)
    assert "desde a chegada ao STF" not in texto
    assert "Chegada (ano mediano)" in texto and "a planilha do STF só traz o ano da autuação" in texto
    assert bloco is not None
    assert "72% autuados em 2026" in texto


def test_o_relogio_recomecou_para_quem_chegou_ha_pouco(site: tuple[Path, str]):
    """N5 e J4: quem chegou à Turma (ou herdou) há menos de um ano não tem "0 há mais de um ano":
    o cartão e a fila dizem que o relógio recomeçou, com a data."""
    raiz, html = site
    conv = next(m for m in _paineis(raiz)["stj"]["ministros"] if m.get("relogio"))
    cartao = re.search(rf'id="tb-cd-t-{conv["id"]}".*?</article>', html, re.DOTALL).group(0)
    assert "o relógio recomeçou na chegada à Turma, em 17/11/2026" in _texto(cartao)
    assert "redistribuição e na atribuição" in _texto(cartao)
    assert "Redistribuição e atribuição também zeram a conta" in pagina.GLOSSARIO["sem_decisao_stj"]


def test_quem_se_repete_diz_a_base_e_o_interessado(site: tuple[Path, str]):
    """N1 e N6: a porcentagem de cada litigante é sobre a base de quem se repete (sem segredo e sem
    precatório); o total inclui o interessado (o MP no habeas corpus), dito à parte."""
    raiz, html = site
    meta = json.loads((raiz / "data" / "tribunais" / "meta.json").read_text(encoding="utf-8"))
    base = meta["cobertura"]["stj"]["partes"]["base"]
    bloco = _texto(re.search(r'id="tb-partes-stj".*?</section>', html, re.DOTALL).group(0))
    assert f"os {pagina._num(base)} processos desta conta" in bloco
    assert "os em segredo de justiça e os precatórios ficam fora dela" in bloco
    assert "interessado em" in bloco
    metodo = _texto(re.search(r'data-vista="metodo" id="tb-v-metodo".*?</section>', html, re.DOTALL).group(0))
    assert "quem recorre, quem responde ou interessado" in metodo
    assert "contam no acervo, nas áreas e no dinheiro público, mas ficam fora desta conta" in metodo
    assert "Processo em segredo de justiça conta nos números" not in metodo


def test_polos_nas_acoes_originarias(site: tuple[Path, str]):
    """J8: "recorre"/"responde" só no recurso; na ação originária (ADI, HC, AR...), polo ativo e passivo."""
    raiz, html = site
    r = pagina._reguas(json.loads((raiz / "data" / "tribunais" / "meta.json").read_text()), _config())
    folha = {"classe": "ADI", "partes": [{"polo": "ativo", "tipo": "pj", "grupo": None}, {"polo": "passivo", "tipo": "ente", "grupo": None}]}
    assert pagina._partes_folha(folha, {}, r, "stf") == "Polo ativo: pessoa jurídica · Polo passivo: ente público"
    folha["classe"] = "REsp"
    assert pagina._partes_folha(folha, {}, r, "stj") == "Recorre: pessoa jurídica · Responde: ente público"
    assert "No recurso, “recorre” é quem perdeu embaixo" in html and "quase tudo é recurso" not in html
    assert "do acervo são recursos" in html


def test_entrada_do_stf_e_redistribuicao_nao_heranca(html: str):
    """J10: a ata do STF só tem distribuído e redistribuído: nada de "herança" nem "sorteio" no STF."""
    stf = _texto(re.search(r'id="tb-fila-stf".*?</section>', html, re.DOTALL).group(0))
    entrou = re.search(r"O que entrou (.*?)STF · atas", stf).group(1)
    assert "redistribuídos (impedimento, prevenção ou cadeira vaga; a ata não diz)" in entrou
    assert "herança" not in entrou and "sorteio" not in entrou
    stj = _texto(re.search(r'id="tb-fila-stj".*?</section>', html, re.DOTALL).group(0))
    assert "distribuídos (sorteio ou prevenção)" in stj


def test_temas_somam_e_quem_saiu_sem_imprecisao(html: str):
    """J5 e PRIV-7: o total dos temas só soma repetitivo e IAC; o resto vem à parte. Ninguém "deixou
    o tribunal" e "Relator não informado" não é nome."""
    texto = _texto(html)
    assert "130 temas (repetitivos e IACs) sem julgamento sob relatoria (repetitivo 120, IAC 10)." in texto
    assert "Além disso, 19 controvérsias e 10 temas de relevância." in texto
    assert "Ainda em nome de quem não está mais na composição: Relator Que Saiu, aposentado (2), Relatora Em Disponibilidade, em disponibilidade (1)." in texto
    assert "1 tema sem relator informado." in texto
    assert "deixou o tribunal" not in texto and "Relator não informado" not in texto


def test_relatores_das_turmas_com_convocados(site: tuple[Path, str]):
    """J15: convocado não é ministro: "os N relatores das Turmas (X ministros e Y desembargadores convocados)"."""
    raiz, _html = site
    p = _paineis(raiz)["stj"]
    texto, curto = pagina._relatores_turmas(p)
    grade = [m for m in p["ministros"] if m["grupo"] != "pr"]
    assert texto == f"os {len(grade)} relatores das Turmas ({len(grade) - 1} ministros e 1 desembargador convocado)" and curto == "relatores nas Turmas"
    assert pagina._relatores_turmas(_paineis(raiz)["stf"])[0].endswith("ministros das Turmas")


def test_vista_sem_ministro_nao_diz_em_vista_com(site: tuple[Path, str]):
    """J18: a planilha só marca "com pedido de vista": sem o ministro, nada de "Em vista com —"."""
    _raiz, html = site
    assert "Em vista com —" not in html and "— sessão" not in _texto(html)


def test_contestar_este_numero(tmp_path: Path, html: str):
    """PRIV-6: cada achado, cada mesa e a lista de quem se repete têm o link "Contestar este número"
    (uma issue no repositório, título sem nome de parte nem processo); sem repositório, sem link."""
    links = re.findall(r'<a class="tb-ct-l" href="([^"]+)"', html)
    assert links and all("/issues/new?title=Tribunais%3A%20" in x for x in links)
    titulos = [x.split("title=", 1)[1] for x in links]
    assert any(t.startswith("Tribunais%3A%20stj-") for t in titulos)  # uma mesa
    assert "Tribunais%3A%20quem%20se%20repete%20stj" in titulos
    assert not any(re.search(r"%20(?:REsp|AREsp|HC|ADI)%20", t) for t in titulos)  # nunca um processo
    (tmp_path / "data").mkdir()
    fabrica.gravar(tmp_path / "data")
    config = _config()
    config.site.repo_url = ""
    sem = pagina.gravar_pagina(tmp_path, config).read_text(encoding="utf-8")
    assert 'class="tb-ct-l"' not in sem


def test_dinheiro_publico_e_a_media_por_gabinete(site: tuple[Path, str]):
    """N2 e M2: a medida é "dinheiro público num dos polos"; a coluna Acervo da linha média é a média por gabinete."""
    raiz, html = site
    p = _paineis(raiz)["stj"]
    g = next(x for x in p["grupos"] if x["id"] == "s1")
    n = sum(1 for m in p["ministros"] if m["grupo"] == "s1")
    linha = re.search(r'<tr class="tb-mx-m"><th scope="row">média da Seção</th><td class="tb-n" title="([^"]+)">([^<]+)</td>', html)
    assert linha and linha.group(2) == pagina._num(round(g["processos"] / n))
    assert linha.group(1) == f"média por gabinete; o grupo soma {pagina._num(g['processos'])} processos"
    assert "dinheiro público num dos polos" in html


def test_porcentagem_com_processo_nunca_zero_e_concordancia():
    """B1 e B5: n > 0 nunca sai "0%"; "2,0 anos", nunca "2,0 ano"; "1 processo"."""
    assert pagina._pct(0.0, n=3) == "<0,1%" and pagina._pct(0.0, n=0) == "0%" and pagina._pct(0.0) == "0%"
    assert pagina._anos(729) == ("2,0", "anos") and pagina._anos(400) == ("1,1", "ano")
    assert pagina._plural(1, "processo", "processos") == "1 processo"
    assert pagina._plural(None, "processo", "processos") == "— processos"


def _site_mexido(raiz: Path, mexer) -> str:
    """A aba de mentira com os painéis mexidos por ``mexer(paineis)`` antes de a página sair."""
    (raiz / "data").mkdir(parents=True, exist_ok=True)
    fabrica.gravar(raiz / "data")
    paineis = _paineis(raiz)
    mexer(paineis)
    for t, painel in paineis.items():
        (raiz / "data" / "tribunais" / f"{t}.json").write_text(json.dumps(painel, ensure_ascii=False), encoding="utf-8")
    return pagina.gravar_pagina(raiz, _config()).read_text(encoding="utf-8")


def _zera_as_fracoes(paineis: dict[str, dict]) -> None:
    """Contagens pequenas num acervo grande: a fração guardada com 4 casas sai 0,0 (o Eleitoral do STJ
    real: 14 processos em 352 mil)."""
    p = paineis["stj"]
    area = min((a for a in p["areas"] if a["n"]), key=lambda a: a["n"])
    area.update(n=14, p=0.0)
    p["recortes"][0].update(n=4, p=0.0)
    p["partes"]["top"][-1]["p"] = 0.0
    grade = [m for m in p["ministros"] if m["grupo"] != "pr"]
    grade[0]["areas"][area["id"]].update(n=2, p=0.0)
    maior = max(grade[1]["areas"], key=lambda a: grade[1]["areas"][a]["n"])
    grade[1]["areas"][maior].update(p=0.0)  # uma barra do cartão
    grade[1]["subareas"][0][2] = 0.0


def test_area_com_processo_nunca_sai_zero_por_cento_na_pagina(tmp_path: Path):
    """B1 na página inteira: o tribunal por área, os recortes, as mesas de cada área, o título da
    área, o cartão do ministro e quem se repete passam a contagem para a porcentagem; com processo,
    nunca "0%" (sai "<0,1%")."""
    html = _site_mexido(tmp_path, _zera_as_fracoes)
    assert '<span class="tb-br-v">14<small>&lt;0,1%</small></span>' in html  # o tribunal por área
    assert '<span class="tb-br-v">4<small>&lt;0,1%</small></span>' in html  # o recorte
    assert not re.search(r">[1-9][\d.]*<small>0%", html)
    texto = _texto(html)
    assert "14 processos, <0,1% do acervo" in texto.replace("&lt;", "<")
    assert not re.search(r"[1-9][\d.]* processos?, 0% do acervo", texto)
    assert not re.search(r"(?<![\d.,])0% · [1-9]", texto)  # a barra do cartão: "0% · 812"
    assert not re.search(r"(?<![\d.,])[1-9][\d.]* · 0%", texto)  # quem se repete: "61 · 0%"
    assert not re.search(r"(?<![\d.,])[1-9][\d.]* 0%", texto)


def test_a_pagina_publicada_nao_tem_zero_por_cento_com_processo():
    """B1 na página real do repositório (quando há): nenhuma contagem com "0%"."""
    real = Path(__file__).resolve().parents[1] / "edicoes" / "tribunais.html"
    if not real.exists():
        pytest.skip("sem a página publicada")
    html = real.read_text(encoding="utf-8")
    assert not re.search(r">[1-9][\d.]*<small>0%", html)
    texto = _texto(html)
    assert not re.search(r"[1-9][\d.]* processos?, 0% do acervo", texto)
    assert not re.search(r"(?<![\d.,])0% · [1-9]", texto) and not re.search(r"(?<![\d.,])[1-9][\d.]* · 0%", texto)


def _uns(paineis: dict[str, dict]) -> None:
    """Contagens iguais a 1 em todo lugar que a revisão achou "1 processos" e afins."""
    for trib in ("stj", "stf"):
        p = paineis[trib]
        grade = [m for m in p["ministros"] if m["grupo"] != "pr" and m.get("entradas")]
        m = grade[0]
        m["entradas"].update(sorteio_12m=1, heranca_12m=1)
        m["pauta"].update(itens=2, principal=1, interno=1)
        m["decisoes"].update(processos=1, monocraticas=1, acordaos=1)
        maiores = sorted((a for a in m["areas"] if m["areas"][a]["n"]), key=lambda a: -m["areas"][a]["n"])[: pagina.AREAS_NO_CARTAO]
        m["processos"] = sum(m["areas"][a]["n"] for a in maiores) + 1  # "outras áreas: 1 processo"
        if isinstance(p.get("pautas"), dict):
            p["pautas"].update(principal=1, interno=1)
        if isinstance(p.get("decisoes"), dict):
            p["decisoes"].update(processos=1, monocraticas=1, acordaos=1)
        e = p.get("entradas")
        if isinstance(e, dict):
            e["total"].update(sorteio=1, heranca=1, registro=1)
            e["dias_com_ata"] = 1


def test_um_nunca_vem_com_substantivo_no_plural(tmp_path: Path):
    """B5 na página inteira: "1 processo", "1 recurso principal", "1 redistribuído", "Entrou 1
    distribuído", "outras áreas: 1 processo"; nunca o número 1 seguido do plural."""
    html = _site_mexido(tmp_path, _uns)
    texto = _texto(re.sub(r"<(style|script)\b.*?</\1>", " ", html, flags=re.DOTALL))
    plurais = re.findall(r"(?<![\d.,])1 (processos|recursos|redistribuídos|distribuídos|decisões|acórdãos|itens|ministros|relatores|dias)\b", texto)
    assert plurais == []
    assert "Entraram 1 " not in texto and "Entrou 1 distribuído (sorteio ou prevenção)" in texto
    assert "1 redistribuído, à parte" in texto and "(1 recurso principal, 1 recurso interno)" in texto
    assert "outras áreas: 1 processo" in texto
    assert "Outro foi registrado à Presidência ou à Vice" in texto


def test_o_que_fica_de_fora_sem_a_camada_do_cnj(html: str):
    """Sem o ``cnj.json``, a data da conclusão no STJ também fica de fora (com a camada, o texto é
    outro: ``test_tribunais_datajud``)."""
    assert ("O valor da causa, a data exata da conclusão no STJ e o andamento processo a processo não estão nas fontes abertas "
            "lidas aqui.") in _texto(html)


def test_assuntos_do_stj_dizem_relatores_das_turmas(site: tuple[Path, str]):
    """J15 na aba Assuntos: no STJ, com desembargador convocado na grade, "com os relatores das
    Turmas" (e "e mais N relatores"); no STF, "com os ministros das Turmas"."""
    _raiz, html = site
    stf = html[html.index('id="tb-assuntos-stf"'):html.index('id="tb-assuntos-stj"')]
    stj = html[html.index('id="tb-assuntos-stj"'):html.index('id="tb-v-partes"')]
    assert "com os relatores das Turmas" in stj and "ministros das Turmas" not in stj
    assert "com os ministros das Turmas" in stf and "relatores das Turmas" not in stf
    assert not re.search(r"e mais \d+ ministros?,", stj)


def test_o_script_tem_as_correcoes_da_tela(html: str):
    """M1, M3, M4, M6, M7, M9, M10, B2: o que a revisão de tela pediu está no script."""
    assert "diasDaJanela(jan) > 30" in html and "diasDe(en.janela)" not in html  # M10
    assert "Não foi possível carregar a folha agora" in html and "nunca para processo em segredo de justiça" not in html  # M1
    assert "fundoInerte(true)" in html and "ev.key === 'Tab' && aberta" in html  # M3
    assert "aria-describedby', 'tbDica'" in html  # M6
    assert "data-tentar" in html and "Tentar de novo" in html  # B2
    assert "mês parcial" in html  # M9
    assert "left:calc(-50% + 6px)" in html  # A2
    assert ".tb-id-x{position:relative;max-width:calc(1100px - 498px)" in html  # A1
    assert ".tb .cg-mapa li:has(.hit:focus-visible)" in html  # M5



# ── a camada do DJe (DJEN) ───────────────────────────────────────────────────


def test_a_camada_do_dje_aparece_com_o_djen_json(tmp_path: Path):
    """Com o ``djen.json`` fresco: o bloco "O que saiu no DJe" do tribunal (com área e mesa), o de
    cada ministro (publicações e os processos da mesa, com link oficial e nunca o nome de parte), a
    fonte na linha das fontes do STJ (não do STF, que não publica no DJEN) e o "Como ler"."""
    (tmp_path / "data").mkdir()
    fabrica.gravar(tmp_path / "data", com_djen="2026-11-20")
    html = pagina.gravar_pagina(tmp_path, _config()).read_text(encoding="utf-8")
    texto = _texto(html)
    fila = _texto(re.search(r'id="tb-fila-stj".*?</section>', html, re.DOTALL).group(0))
    assert "O que saiu no DJe" in fila and "Em 20/11/2026, o último dia coletado: 212 publicações de 201 processos" in fila
    assert "Por área, em processos: Civil 1.500, Penal 1.300" in fila and "812 processos que estavam na mesa do relator" in fila
    assert "CNJ · DJe (DJEN), coletado no Brasil, dados de 20/11/2026" in fila
    stf = _texto(re.search(r'id="tb-fila-stf".*?</section>', html, re.DOTALL).group(0))
    assert "O STF não publicou nada no DJEN em 20 dias úteis" in stf
    cartoes = re.findall(r'<ul class="tb-fx tb-dj">(.*?)</ul>', html, re.DOTALL)
    assert cartoes and all("processos na mesa tiveram publicação" in c for c in cartoes)
    assert "saiu no DJe em 20/11 (decisão)" in texto and "processo.stj.jus.br/processo/pesquisa/" in " ".join(cartoes)
    assert not re.search(r"\bparad[oa]s?\b", texto)
    assert "CNJ · DJe (DJEN)</a>: dados de 20/11" in html  # a linha das fontes
    metodo = _texto(re.search(r'data-vista="metodo" id="tb-v-metodo".*?</section>', html, re.DOTALL).group(0))
    assert "O DJe (DJEN)" in metodo and "recusa quem está fora do Brasil" in metodo and "Processo em segredo conta nos números" in metodo


def test_a_camada_do_dje_velha_vira_nota_e_ausente_some(tmp_path: Path):
    """Último dia a mais de 4 dias (de segunda a sexta): sai só a nota. Sem o arquivo, ou com ele
    quebrado, a página sai igual, sem o bloco (a aba nunca depende do DJEN)."""
    (tmp_path / "a" / "data").mkdir(parents=True)
    fabrica.gravar(tmp_path / "a" / "data", com_djen="2026-11-13")  # sexta 13/11; hoje é segunda 23/11: 6 dias da semana
    html = pagina.gravar_pagina(tmp_path / "a", _config()).read_text(encoding="utf-8")
    assert "DJe (DJEN) sem atualização desde 13/11/2026" in html and "Em 13/11/2026, o último dia coletado" not in html
    (tmp_path / "b" / "data").mkdir(parents=True)
    pasta = fabrica.gravar(tmp_path / "b" / "data")
    sem = pagina.gravar_pagina(tmp_path / "b", _config()).read_text(encoding="utf-8")
    assert "O que saiu no DJe" not in sem and 'class="tb-fx tb-dj"' not in sem
    (pasta / "djen.json").write_text("{quebrado", encoding="utf-8")
    assert pagina.gravar_pagina(tmp_path / "b", _config()).read_text(encoding="utf-8") == sem


def test_notas_da_presidencia_dos_suspensos_e_da_cadeira_vaga(html: str):
    """J13, J14 e J17: a linha da Presidência do STJ diz o que ela guarda; o Como ler diz que os
    suspensos no gabinete contam na mesa, mas não na idade; o acervo de quem saiu do STF é da cadeira vaga."""
    assert "homologação de decisão estrangeira" in html and "cartas rogatórias" in html  # J13
    assert "contam na mesa, mas ficam fora da" in html and "conta da idade" in html  # J14
    assert "cadeira vaga de Luís Roberto Barroso" in html  # J17


def _regra(css: str, seletor: str) -> str:
    m = re.search(re.escape(seletor) + r"\{([^}]*)\}", css)
    assert m, seletor
    return m.group(1)


def test_nome_do_ministro_inteiro_e_foco_visivel_na_matriz(html: str):
    """M8 e B6: o nome do ministro nunca sai com reticências (na matriz, na lista e na miniatura da
    mesa: ele é a resposta a "na mesa de quem"), e o contorno de foco do nome fica dentro da célula fixa."""
    for seletor in (".tb-mx tbody th", ".tb-me-n", ".tb-me-l .tb-me-n"):
        for m in re.finditer(re.escape(seletor) + r"\{([^}]*)\}", html):
            regra = m.group(1)
            assert "ellipsis" not in regra and "line-clamp" not in regra and "nowrap" not in regra, (seletor, regra)
    assert "outline-offset:-2px" in _regra(html, ".tb-mx tbody th a:focus-visible")  # B6
