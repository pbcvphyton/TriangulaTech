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
    _, caminho = _site(raiz)
    assert caminho is None and (raiz / "edicoes" / "tribunais.html").read_text(encoding="utf-8") == "<html>anterior</html>"


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
    tabela = re.search(r'<table class="tb-mx" id="tb-mx-stj">.*?</table>', html, re.DOTALL).group(0)
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
    stf = re.search(r'<table class="tb-mx" id="tb-mx-stf">.*?</table>', html, re.DOTALL).group(0)
    assert stf.index("média do tribunal, sem a Presidência") < stf.index('data-grupo="t1"')


def test_a_celula_usa_a_rampa_do_config(site: tuple[Path, str]):
    raiz, html = site
    painel = _paineis(raiz)["stj"]
    m = painel["ministros"][0]
    rampa = modelo.carregar_regras().limiares["rampa"]
    tabela = re.search(r'<table class="tb-mx" id="tb-mx-stj">.*?</table>', html, re.DOTALL).group(0)
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
    assert "Com um ente público num dos polos" in html and "Execução fiscal" in html  # o que se mede


def test_partes_sem_pessoa_fisica_com_nome(html: str):
    assert "Pessoas físicas:" in html and "somadas, sem nome" in html
    assert "Responde: pessoa física" in html or "Recorre: pessoa física" in html  # o tipo, nunca o nome
    assert "recorre" in html and "responde" in html and 'class="tb-bf"' in html  # a borboleta


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
