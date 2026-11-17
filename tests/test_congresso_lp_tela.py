"""A tela da visão por parlamentar (``render/congresso_lp.py`` e os modelos ``congresso_lp*.j2``):
o interruptor "Ver por" na barra, "Os projetos" e "Quem propõe".

No molde de ``test_congresso_cota_tela.py``: a página inteira é gerada com o Congresso de
mentira (``tests/fixtures/congresso``), sem rede, duas vezes. **Sem leituras** na guarda, a
atualização não grava o bloco ``lp`` e a página tem de sair sem nada da visão nova. **Com seis
leituras** da Câmara (as mesmas de ``test_congresso_pais.py``: o PL 10 confirmado no topo da
régua, o PL 14 a confirmar, e quatro abaixo), a barra ganha o interruptor, as duas vistas saem
com o essencial no HTML, e as abas que já existiam ficam iguais, byte a byte.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from qijournal.config import Config, load_config
from qijournal.congresso import dados, leitura, pais, regras
from qijournal.render import congresso as pagina
from qijournal.render import congresso_lp
from tests.fixtures.congresso import fabrica

ROOT = Path(__file__).resolve().parents[1]
MODELOS = Path(pagina.__file__).parent / "templates"
MODELO = (MODELOS / "congresso_lp.html.j2").read_text(encoding="utf-8")
BARRA = (MODELOS / "congresso_lp_barra.html.j2").read_text(encoding="utf-8")
SCRIPT = (MODELOS / "congresso_lp.js.j2").read_text(encoding="utf-8")
ESTILO = (MODELOS / "congresso_lp.css.j2").read_text(encoding="utf-8")
ANTIGAS = ("posicoes", "partidos", "espera", "fila", "votacoes", "pessoas", "frentes", "cota", "escolha", "metodo")


@pytest.fixture(scope="module")
def regua() -> regras.ProjetoDePais:
    r = regras.carregar(ROOT).projeto_de_pais
    assert r is not None
    return r


def _gerar(data_dir: Path) -> str:
    config: Config = load_config(env={})
    html = pagina.render_congresso_page(config, data_dir, home_href="../", archive_href="./")
    assert html is not None
    return html


def _atualizar(raiz: Path, regua: regras.ProjetoDePais | None, *, tudo_confirmado: bool = False) -> Path:
    data_dir = raiz / "data"
    (data_dir / "busca").mkdir(parents=True)
    (data_dir / "busca" / "2026-11-12.json").write_text(json.dumps(fabrica.busca_do_dia()), encoding="utf-8")
    r = fabrica.regras_de_teste()
    if regua is not None:
        r.projeto_de_pais = regua
        guarda = leitura.Guarda(regua)
        for chave, codigos, campos in (
            ("1010", "fiscal geral ajuste corta neutro neutro", {"r": 2, "porque": "Corrige a tabela do imposto."}),
            ("1011", "justica grupo pena prisional neutro neutro", {}),
            ("1012", "infra local servico investe neutro neutro", {}),
            ("1013", "institucional geral sistema neutro neutro neutro", {}),
            ("1014", "outro local simbolico neutro neutro neutro", {}),
            ("1015", "produtividade setor sistema neutro neutro neutro", {"r": 2} if tudo_confirmado else {}),
        ):
            guarda.registrar("cd", 2026, chave, leitura.Leitura(codigos=tuple(codigos.split()), **campos))
        guarda.gravar(dados.pasta_de(data_dir) / "leitura")
    resultado = dados.atualizar(raiz, data_dir, raiz / "cache", dados.Opcoes(hoje=fabrica.HOJE, projetos_desde=2026), fetch=fabrica.FetchFalso(), regras=r)
    assert resultado.avisos == []
    return data_dir


@pytest.fixture(scope="module")
def pasta_sem(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _atualizar(tmp_path_factory.mktemp("lp-sem"), None)


@pytest.fixture(scope="module")
def pasta_com(tmp_path_factory: pytest.TempPathFactory, regua: regras.ProjetoDePais) -> Path:
    return _atualizar(tmp_path_factory.mktemp("lp-com"), regua)


@pytest.fixture(scope="module")
def confirmado(tmp_path_factory: pytest.TempPathFactory, regua: regras.ProjetoDePais) -> str:
    """A página quando a segunda leitura já confirmou tudo o que estava no topo da régua."""
    return _gerar(_atualizar(tmp_path_factory.mktemp("lp-tudo"), regua, tudo_confirmado=True))


@pytest.fixture(scope="module")
def sem(pasta_sem: Path) -> str:
    """A página sem leituras: a de sempre."""
    return _gerar(pasta_sem)


@pytest.fixture(scope="module")
def com(pasta_com: Path) -> str:
    """A página com o bloco ``lp`` da Câmara (o Senado fica sem leitura)."""
    return _gerar(pasta_com)


def _texto(trecho: str) -> str:
    """O que se lê num trecho de HTML (sem marcação, espaços juntos)."""
    limpo = re.sub(r"<[^>]+>", " ", trecho)
    for de, para in (("&amp;", "&"), ("&#34;", '"'), ("&#39;", "'"), ("&lt;", "<"), ("&gt;", ">")):
        limpo = limpo.replace(de, para)
    return " ".join(limpo.split())


def _barra(html: str) -> str:
    return html.split('<div class="cg-barra" id="cgBarra">', 1)[1].split("</nav>", 1)[0]


def _vista(html: str, nome: str) -> str:
    """Uma seção da página (de ``data-vista="nome"`` até a seção seguinte)."""
    return html.split(f'data-vista="{nome}" aria-labelledby', 1)[1].split("<section class=", 1)[0].split('<footer class="ft">', 1)[0].rstrip()


def _bloco(html: str, ident: str) -> str:
    """O bloco de uma Casa numa vista nova (``cg-lp-proj-cd``, ``cg-lp-aut-cd``)."""
    return html.split(f'id="{ident}"', 1)[1].split('<div class="cg-c" data-casa=', 1)[0].split("</section>", 1)[0]


def _manifesto(html: str) -> dict:
    return json.loads(html.split('<script type="application/json" id="cgManifesto">', 1)[1].split("</script>", 1)[0])


# Uma página de mentira para rodar no Node o trecho do interruptor "Ver por" (de ``var lenteEl`` até antes de "O que as
# vistas novas têm em comum", em ``congresso_lp.js.j2``): as duas listas de abas ("Como ler" está nas duas), a seção do
# Como ler com o bloco da leitura dos projetos, o armazenamento da aba do navegador e o histórico. ``verVista``, o ouvinte
# das abas e ``doEndereco`` fazem o que os de ``congresso.js.j2`` fazem no que importa aqui.
LENTE_DE_MENTIRA = r"""
function aba(vista, href) {
  return { getAttribute: function (k) { return k === 'data-vista' ? vista : k === 'href' ? href : null; },
    closest: function (sel) { return sel === 'a[data-vista]' || (sel === 'a[href="#cg-lp-metodo"]' && href === '#cg-lp-metodo') ? this : null; } };
}
function ul(lente, abas) { return { getAttribute: function () { return lente; }, querySelectorAll: function () { return abas; } }; }
function botao(l) {
  var at = { 'data-lente': l };
  return { getAttribute: function (k) { return k in at ? at[k] : null; }, setAttribute: function (k, v) { at[k] = v; }, closest: function () { return this; } };
}
var ABAS = {
  partido: [aba('resumo', '#cg-leitura'), aba('escolha', '#cg-escolha'), aba('metodo', '#cg-metodo')],
  pessoa: [aba('projetos', '#cg-projetos'), aba('autores', '#cg-autores'), aba('metodo', '#cg-lp-metodo')]
};
var BOTOES = [botao('partido'), botao('pessoa')], daLente = [], daNav = [], prazos = [], historico = [], rootAttrs = {};
var DENTRO = {}, blocoDaLeituraDeMentira = { rolou: 0, scrollIntoView: function () { this.rolou += 1; }, contains: function (el) { return el === this || el === DENTRO; } };
var IDS = { cgLente: { hidden: true, parentNode: { offsetHeight: LINHA }, querySelectorAll: function () { return BOTOES; }, addEventListener: function (tipo, fn) { daLente.push(fn); } },
  'cg-lp-metodo': blocoDaLeituraDeMentira, 'cg-lp-diaria': DENTRO, 'cg-metodo': {}, 'cg-leitura': {} };
var VISTA_DO_ID = { 'cg-lp-metodo': 'metodo', 'cg-lp-diaria': 'metodo', 'cg-metodo': 'metodo', 'cg-leitura': 'resumo' };
var d = { getElementById: function (id) { return IDS[id] || null; } };
var w = {
  location: { hash: HASH },
  sessionStorage: { getItem: function (k) { return k in ARMAZEM ? ARMAZEM[k] : null; }, setItem: function (k, v) { ARMAZEM[k] = String(v); } },
  history: { pushState: function (estado, titulo, endereco) { w.location.hash = endereco; historico.push(endereco); } },
  setTimeout: function (fn) { prazos.push(fn); }, addEventListener: function () {}
};
var root = { setAttribute: function (k, v) { rootAttrs[k] = v; }, style: { setProperty: function (k, v) { rootAttrs[k] = v; } } };
var nav = { querySelectorAll: function () { return [ul('partido', ABAS.partido), ul('pessoa', ABAS.pessoa)]; }, addEventListener: function (tipo, fn, captura) { daNav.push([fn, !!captura]); } };
var casas = { hidden: false, offsetHeight: 40 }, abas = ABAS.partido.concat(ABAS.pessoa), vistaPadrao = 'resumo', vistaAtual = '', aoTrocarVista = null, LPM = { abas: ['projetos', 'autores'] };
function lista(x) { return Array.prototype.slice.call(x); }
function verVista(nome) { vistaAtual = nome; if (aoTrocarVista) aoTrocarVista(nome); return true; }
/* O ouvinte das abas de sempre: grava #v=<vista> e troca a vista. */
nav.addEventListener('click', function (event) {
  var a = event.target.closest('a[data-vista]');
  if (!a) return;
  w.history.pushState(null, '', '#v=' + a.getAttribute('data-vista'));
  verVista(a.getAttribute('data-vista'));
});
function doEndereco() {
  var h = w.location.hash, v = /^#v=([a-z]+)/.exec(h);
  if (v) verVista(v[1]); else if (VISTA_DO_ID[h.slice(1)]) verVista(VISTA_DO_ID[h.slice(1)]); else verVista(vistaPadrao);
}
/* O fim de congresso.js.j2: a vista padrão, depois o endereço; e, passado o carregamento, os prazos. */
function carregar() { verVista(vistaPadrao); doEndereco(); prazos.forEach(function (fn) { fn(); }); }
function irAoEndereco(h) { w.location.hash = h; doEndereco(); }  // voltar, avançar ou um link de dentro da página
function clicarNaLente(l) { daLente.forEach(function (fn) { fn({ target: BOTOES[l === 'partido' ? 0 : 1] }); }); }
function clicarNaAba(a) {
  var ev = { target: a, parou: false, preventDefault: function () {}, stopPropagation: function () { this.parou = true; } };
  daNav.forEach(function (o) { if (o[1]) o[0](ev); });
  if (!ev.parou) daNav.forEach(function (o) { if (!o[1]) o[0](ev); });
}
function estado() {
  return { vista: vistaAtual, lente: rootAttrs['data-lente'], hash: w.location.hash, guardadas: [ARMAZEM['qij-vista-partido'] || '', ARMAZEM['qij-vista-pessoa'] || ''],
    pressionado: BOTOES.filter(function (b) { return b.getAttribute('aria-pressed') === 'true'; }).map(function (b) { return b.getAttribute('data-lente'); }).join() };
}
"""


def _lente_no_node(tmp_path: Path, cenario: str, *, guardado: dict[str, str] | None = None, endereco: str = "", linha: int = 40) -> dict:
    """Roda no Node o interruptor "Ver por" sobre a página de mentira. ``guardado``: o que a aba do navegador traz de
    uma visita anterior (``sessionStorage``); ``endereco``: o da carga; ``linha``: a altura da primeira linha da barra
    (a do grupo da Casa tem 40). ``cenario`` é o JavaScript do teste: preenche ``r``, que volta como dicionário."""
    trecho = "  var lenteEl = " + SCRIPT.split("  var lenteEl = ", 1)[1].split("  /* ── O que as vistas novas têm em comum ── */", 1)[0]
    script = tmp_path / "lente.js"
    script.write_text(
        f"var ARMAZEM = {json.dumps(guardado or {})}, HASH = {json.dumps(endereco)}, LINHA = {linha};\n" + LENTE_DE_MENTIRA + trecho + "\nvar r = {};\n" + cenario + "\nconsole.log(JSON.stringify(r));\n",
        encoding="utf-8",
    )
    return json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, check=True, timeout=60).stdout)


# ── sem o bloco "lp", a página é a de sempre ─────────────────────────────────


def test_sem_leituras_a_pagina_sai_sem_nada_da_visao_nova(sem: str, pasta_sem: Path):
    assert "lp" not in json.loads((dados.pasta_de(pasta_sem) / "casa-cd.json").read_text(encoding="utf-8"))
    for trecho in ("cgLente", "cg-lente-b", "data-lente", "cg-barra-1", "cg-vistas-r", "Visão por parlamentar", "Ver por", "cg-lp-", "aoTrocarVista", "lpArquivo", "Perfil pelos projetos", 'data-vista="projetos"', 'data-vista="autores"'):
        assert trecho not in sem, trecho
    assert "lp" not in _manifesto(sem)
    # a barra é a de sempre: a frase de ajuda dentro do grupo da Casa e uma lista só, sem rótulo
    barra = _barra(sem)
    assert '<span class="cg-casas-n">Escolha a Casa e, abaixo, o que quer ver dela.</span>\n    </div>\n    <nav class="cg-vistas" id="cgNav" aria-label="O que ver">\n      <ul>\n' in barra
    assert barra.count("<ul") == 1


def test_sem_a_regua_no_config_o_bloco_dos_dados_nao_vira_tela(pasta_com: Path, monkeypatch: pytest.MonkeyPatch):
    """O bloco está nos dados, mas o config não traz a régua (não há de onde tirar os rótulos): a
    visão não sai, e a página é a de sempre."""
    monkeypatch.setattr(congresso_lp, "_regras", lambda _config: fabrica.regras_de_teste())
    html = _gerar(pasta_com)
    assert "cgLente" not in html and 'data-vista="projetos"' not in html and "lp" not in _manifesto(html)


def test_com_leituras_as_abas_que_ja_existiam_saem_iguais(sem: str, com: str):
    """Só se acrescenta: cada vista antiga tem o mesmo HTML nas duas páginas, e as abas dela
    continuam na mesma ordem, com os mesmos endereços. O "Como ler" é o de sempre com um bloco a
    mais no fim ("A leitura dos projetos"): tirado o bloco, é igual."""
    for nome in ANTIGAS:
        if nome != "metodo":
            assert _vista(com, nome) == _vista(sem, nome), nome
    antes, depois = _vista(sem, "metodo"), _vista(com, "metodo")
    cabeca, bloco = depois.split('    <div class="az-blk cg-lp-met" id="cg-lp-metodo">', 1)
    assert antes.startswith(cabeca) and bloco.endswith(antes[len(cabeca) :]) and "<section" not in bloco[: -len(antes[len(cabeca) :])]
    antes = re.findall(r'<li><a href="(#[^"]+)" data-vista="([a-z]+)">([^<]+)</a></li>', _barra(sem))
    partido = _barra(com).split('<ul data-lente="partido">', 1)[1].split("</ul>", 1)[0]
    assert re.findall(r'<li><a href="(#[^"]+)" data-vista="([a-z]+)">([^<]+)</a></li>', partido) == antes
    assert len(antes) >= 10 and antes[-1] == ("#cg-metodo", "metodo", "Como ler")


# ── a barra: o interruptor "Ver por" ─────────────────────────────────────────


def test_a_barra_ganha_o_interruptor_e_a_lista_da_visao_por_parlamentar(com: str):
    barra = _barra(com)
    # o interruptor fica fora do grupo da Casa (a troca de Casa regrava os botões de lá) e nasce escondido, como ela
    casas = barra.split('id="cgCasas"', 1)[1].split("</div>", 1)[0]
    assert "data-lente" not in casas and casas.count("<button") == 2
    assert '<div class="cg-lente" id="cgLente" role="group" aria-label="Ver por" hidden>' in barra
    assert '<button type="button" class="cg-lente-b" data-lente="partido" aria-pressed="true">Partido <span>como votam</span></button>' in barra
    assert '<button type="button" class="cg-lente-b" data-lente="pessoa" aria-pressed="false">Parlamentar <span>o que propõem</span></button>' in barra
    # a frase de ajuda não muda de lugar: dentro do grupo da Casa, logo depois dos botões; o interruptor vem depois do grupo
    assert casas.rstrip().endswith('<span class="cg-casas-n">Escolha a Casa e, abaixo, o que quer ver dela.</span>') and barra.count("cg-casas-n") == 1 and "cgCasasN" not in com
    assert barra.index('<span class="cg-casas-n">') < barra.index('id="cgLente"') < barra.index("<nav")
    # sem script: as duas listas, cada uma com o rótulo
    assert barra.index('<p class="cg-vistas-r">Visão por partido</p>') < barra.index('<ul data-lente="partido">') < barra.index('<p class="cg-vistas-r">Visão por parlamentar</p>') < barra.index('<ul data-lente="pessoa">')
    pessoa = barra.split('<ul data-lente="pessoa">', 1)[1].split("</ul>", 1)[0]
    assert re.findall(r'<a href="(#[^"]+)" data-vista="([a-z]+)">([^<]+)</a>', pessoa) == [
        ("#cg-projetos", "projetos", "Os projetos"),
        ("#cg-autores", "autores", "Quem propõe"),
        ("#cg-decide", "decide", "Na hora do voto"),
        ("#cg-cruzar", "cruzar", "Pessoa ou partido?"),
        ("#cg-lp-metodo", "metodo", "Como ler"),  # a mesma vista, no bloco da leitura dos projetos
    ]
    # o Resumo ganha um item no fim de "O que tem em cada aba", e o dossiê, um link para o perfil pelos projetos
    assert '<li><a class="hit" href="#cg-projetos">Visão por parlamentar</a><p>' in com and "Perfil pelos projetos (" in com
    # as vistas novas vêm depois de "Escolha o seu candidato" e antes de "Como ler"
    ordem = re.findall(r'class="[^"]*cg-vista[^"]*" data-vista="([a-z]+)"', com)
    assert ordem[-6:] == ["escolha", "projetos", "autores", "decide", "cruzar", "metodo"]


def test_o_grupo_da_casa_e_o_de_sempre_e_quem_se_ajusta_a_largura_e_o_interruptor(sem: str, com: str):
    """O que já existia fica onde estava: o grupo da Casa (os botões e a frase de ajuda) tem o mesmo HTML com e sem a
    visão por parlamentar, e nenhuma regra da visão nova esconde nem move a frase (ela sumia de 721 a 1.099 px e, acima
    disso, ia para depois do interruptor). Quem se ajusta é o interruptor: perde o texto de apoio abaixo de 1.000 px,
    o rótulo entre 721 e 819 px (a faixa em que a frase aparece e a linha é curta) e ocupa uma linha só dele no celular."""
    grupo_com, grupo_sem = (_barra(html).split('<div class="cg-casas" id="cgCasas"', 1)[1].split("</div>", 1)[0] for html in (com, sem))
    assert grupo_com == grupo_sem and "Escolha a Casa e, abaixo, o que quer ver dela." in grupo_sem
    assert "cg-casas-n" not in ESTILO and "cgCasasN" not in SCRIPT and "fraseDaBarra" not in SCRIPT
    antigo = (MODELOS / "congresso.css.j2").read_text(encoding="utf-8")
    assert antigo.count(".cg-casas-n{display:none}") == 1 and "@media (max-width:720px){" in antigo  # a main só a esconde até 720 px
    assert "@media (max-width:999px){.cg-lente .cg-lente-b span{display:none}}" in ESTILO
    assert "@media (min-width:721px) and (max-width:819px){.cg-lente-r{display:none}}" in ESTILO
    assert ".cg-barra-1{display:flex;flex-wrap:wrap;" in ESTILO and "  .cg-lente{flex:1 1 100%;" in ESTILO.split("@media (max-width:560px){", 1)[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="sem Node.js para rodar o JavaScript da página")
def test_a_margem_das_ancoras_cresce_o_que_a_barra_cresce_com_o_interruptor(tmp_path: Path):
    """No celular o interruptor ocupa uma linha a mais, e a barra fixa fica 39 px mais alta: com a margem de rolagem de
    sempre (104 px), o alvo de um endereço antigo (``#cg-rachas-cd``) parava com o título debaixo da barra. O script
    mede quanto a primeira linha cresceu sobre o grupo da Casa e o estilo soma isso à margem; a ficha continua sem
    margem, e a regra dela vem depois (as duas têm o mesmo peso)."""
    antigo = (MODELOS / "congresso.css.j2").read_text(encoding="utf-8")
    assert ".cg [id]{scroll-margin-top:104px}\n.cg-mo [id]{scroll-margin-top:0}" in antigo  # os 104 px de sempre: se mudarem lá, mudam aqui
    assert ".cg [id]{scroll-margin-top:calc(104px + var(--cg-lp-barra,0px))}\n.cg-mo [id]{scroll-margin-top:0}\n" in ESTILO
    medir = "r.extra = rootAttrs['--cg-lp-barra'];"
    assert _lente_no_node(tmp_path, medir, linha=79)["extra"] == "39px"  # o interruptor numa linha só dele: 40 do grupo da Casa + 39
    assert _lente_no_node(tmp_path, medir, linha=40)["extra"] == "0px"  # na linha da Casa: a margem de sempre
    assert _lente_no_node(tmp_path, "casas.hidden = true; lpBarra(); " + medir, linha=39)["extra"] == "39px"  # sem o grupo da Casa (uma Casa só), a linha inteira é a mais
    assert "w.addEventListener('resize', lpBarra);" in SCRIPT and "d.fonts.ready.then(lpBarra)" in SCRIPT


@pytest.mark.skipif(shutil.which("node") is None, reason="sem Node.js para rodar o JavaScript da página")
def test_ver_por_devolve_a_vista_em_que_a_pessoa_estava_e_nao_a_de_uma_visita_anterior(tmp_path: Path):
    """A aba do navegador guarda a última vista de cada visão. A página carregada de novo na mesma aba mostra o Resumo
    (a vista padrão, que não é guardada enquanto a página carrega): ir a "Parlamentar" e voltar a "Partido" tem de
    devolver o Resumo, e não "Escolha o seu candidato", guardada na visita anterior. Vale também para quem troca de
    visão por um link da página. E a lembrança de uma carga para a outra continua: quem recarrega numa vista da visão
    por parlamentar volta, em "Partido", à vista da visão por partido em que estava antes."""
    de_antes = {"qij-vista-partido": "escolha", "qij-lente": "partido"}
    r = _lente_no_node(tmp_path, "carregar(); r.chegada = estado(); clicarNaLente('pessoa'); r.pessoa = estado(); clicarNaLente('partido'); r.partido = estado();", guardado=de_antes)
    assert (r["chegada"]["vista"], r["chegada"]["lente"], r["chegada"]["guardadas"]) == ("resumo", "partido", ["resumo", ""])
    assert (r["pessoa"]["vista"], r["pessoa"]["lente"], r["pessoa"]["hash"]) == ("projetos", "pessoa", "#v=projetos")
    assert (r["partido"]["vista"], r["partido"]["lente"], r["partido"]["hash"], r["partido"]["pressionado"]) == ("resumo", "partido", "#v=resumo", "partido")
    # por um link da página (o item do Resumo que leva a "Os projetos"), sem passar pelo interruptor
    r = _lente_no_node(tmp_path, "carregar(); irAoEndereco('#v=projetos'); r.pessoa = estado(); clicarNaLente('partido'); r.partido = estado();", guardado=de_antes)
    assert r["pessoa"]["lente"] == "pessoa" and (r["partido"]["vista"], r["partido"]["lente"]) == ("resumo", "partido")
    # recarregada em "Os projetos": a vista padrão, que só passou pela tela, não apaga a da visão por partido
    r = _lente_no_node(tmp_path, "carregar(); r.chegada = estado(); clicarNaLente('partido'); r.partido = estado();", guardado=de_antes, endereco="#v=projetos")
    assert (r["chegada"]["vista"], r["chegada"]["lente"], r["chegada"]["guardadas"]) == ("projetos", "pessoa", ["escolha", "projetos"])
    assert (r["partido"]["vista"], r["partido"]["lente"]) == ("escolha", "partido")


@pytest.mark.skipif(shutil.which("node") is None, reason="sem Node.js para rodar o JavaScript da página")
def test_cada_endereco_do_como_ler_diz_de_que_visao_e(tmp_path: Path):
    """"Como ler" é das duas visões. ``#v=metodo`` (e as âncoras antigas da seção) é o da visão por partido, como sempre
    foi; ``#cg-lp-metodo`` (o bloco da leitura dos projetos, ou uma âncora de dentro dele) é o da visão por
    parlamentar, e é o endereço que a aba "Como ler" dela grava. Assim o voltar do navegador para o Como ler da visão
    por partido devolve a barra da visão por partido (ficava na visão por parlamentar, sem as abas de sempre)."""
    r = _lente_no_node(
        tmp_path,
        """
        carregar(); r.chegada = estado();
        clicarNaLente('pessoa'); r.pessoa = estado();
        irAoEndereco('#v=metodo'); r.voltar = estado();                      // o voltar do navegador
        irAoEndereco('#v=projetos'); r.avancar = estado();                   // e o avançar
        clicarNaAba(ABAS.pessoa[2]); r.aba = estado(); r.rolou = blocoDaLeituraDeMentira.rolou;  // "Como ler" da visão por parlamentar
        clicarNaLente('partido'); r.partido = estado();
        clicarNaLente('pessoa'); r.de_volta = estado(); r.rolou_de_novo = blocoDaLeituraDeMentira.rolou;
        clicarNaLente('partido'); clicarNaAba(ABAS.partido[2]); r.aba_antiga = estado();        // "Como ler" de sempre
        irAoEndereco('#cg-lp-diaria'); r.dentro = estado();                  // uma âncora de dentro do bloco da leitura
        irAoEndereco('#cg-metodo'); r.antiga = estado();                     // a âncora antiga da seção
        """,
        endereco="#v=metodo",
    )
    assert (r["chegada"]["vista"], r["chegada"]["lente"]) == ("metodo", "partido")
    assert (r["pessoa"]["vista"], r["pessoa"]["lente"], r["pessoa"]["hash"]) == ("projetos", "pessoa", "#v=projetos")
    assert (r["voltar"]["vista"], r["voltar"]["lente"], r["voltar"]["pressionado"]) == ("metodo", "partido", "partido")
    assert (r["avancar"]["vista"], r["avancar"]["lente"]) == ("projetos", "pessoa")
    assert (r["aba"]["vista"], r["aba"]["lente"], r["aba"]["hash"], r["rolou"]) == ("metodo", "pessoa", "#cg-lp-metodo", 1)
    assert (r["partido"]["vista"], r["partido"]["lente"], r["partido"]["hash"]) == ("metodo", "partido", "#v=metodo")
    assert (r["de_volta"]["vista"], r["de_volta"]["lente"], r["de_volta"]["hash"], r["rolou_de_novo"]) == ("metodo", "pessoa", "#cg-lp-metodo", 2)
    assert (r["aba_antiga"]["vista"], r["aba_antiga"]["lente"], r["aba_antiga"]["hash"]) == ("metodo", "partido", "#v=metodo")
    assert (r["dentro"]["vista"], r["dentro"]["lente"]) == ("metodo", "pessoa") and (r["antiga"]["vista"], r["antiga"]["lente"]) == ("metodo", "partido")
    # colado numa aba nova: cada um abre na sua visão
    assert _lente_no_node(tmp_path, "carregar(); r.e = estado();", endereco="#cg-lp-metodo")["e"]["lente"] == "pessoa"
    assert _lente_no_node(tmp_path, "carregar(); r.e = estado();", endereco="#v=metodo", guardado={"qij-lente": "pessoa", "qij-vista-pessoa": "metodo"})["e"]["lente"] == "partido"


def test_o_como_ler_de_cada_vista_nova_leva_ao_bloco_da_leitura(com: str):
    """O "como ler" ao lado do selo, no cabeçalho de cada uma das quatro vistas, abre o Como ler no
    bloco "A leitura dos projetos", e não no começo do método da visão por partido."""
    assert re.findall(r'<a class="cg-lp-como" href="(#[^"]+)">como ler</a>', com) == ["#cg-lp-metodo"] * 4
    assert com.count('id="cg-lp-metodo"') == 1


def test_o_manifesto_leva_os_rotulos_do_config_e_os_enderecos_dos_arquivos(com: str, regua: regras.ProjetoDePais):
    lp = _manifesto(com)["lp"]
    assert lp["arq"] == {"projetos": "../data/congresso/projetos-", "autores": "../data/congresso/autores-", "decide": "../data/congresso/decide-", "autoria": "../data/congresso/autoria/"}
    assert lp["selo"] == regua.selo and lp["fontes"] == regua.fontes and lp["alertas"] == regua.alertas and lp["objetivo"] == regua.objetivo
    assert [f[:2] for f in lp["faixas"]] == [[f.id, f.rotulo] for f in regua.faixas] and [e[0] for e in lp["eixos"]] == list(regua.alavanca.ids)
    assert lp["rot"]["topo"] == regua.faixas[-1].rotulo and lp["rot"]["piso"] == regua.faixas[0].rotulo and lp["rot"]["eixo"] == regua.alavanca.rotulo
    assert lp["rot"]["baixas"] == f"{regua.faixas[0].rotulo} ou {regua.faixas[1].rotulo}" and lp["rot"]["a_confirmar"] == regua.alertas["a_confirmar"]
    assert lp["abas"] == ["projetos", "autores", "decide", "cruzar"] and set(lp["portas_nome"]) == set(pais.PORTAS)
    assert lp["escala_tempo"][0] == [0, 0] and lp["escala_tempo"][2] == [365, 36] and lp["cortes"]["teto_pontos"] == regua.limiares.teto_pontos
    assert lp["selo_dica"].startswith("Aqui, e só na visão por parlamentar")
    # o script e o estilo novos estão na página
    for trecho in ("aoTrocarVista = function", "function lpArquivo(", "lpVista('projetos')", "lpVista('autores')", ".cg-lente{", "--cg-lp1:"):
        assert trecho in com, trecho


# ── "Os projetos" sem script ─────────────────────────────────────────────────


def test_os_projetos_traz_o_essencial_no_html(com: str, regua: regras.ProjetoDePais):
    topo, meio = regua.faixas[-1].rotulo, regua.faixas[-2].rotulo
    vista = _vista(com, "projetos")
    assert f'<button class="az-selo cg-lp-selo" type="button">{regua.selo}</button>' in vista and regua.objetivo in vista
    camara = _bloco(com, "cg-lp-proj-cd")
    lido = _texto("<" + camara)
    # a Janela: o conjunto, a unidade e a contagem, dizendo o que está confirmado e o que não está
    assert f"que tramitam na Câmara · o topo da régua: peso 60 ou mais em alavanca do núcleo, pela ementa · 2 projetos · 1 {topo} confirmado por dois leitores e 1 a confirmar · 100% dos que tramitam têm leitura" in lido
    # os quatro números, cada um com o que conta
    numeros = re.findall(r'<button type="button" class="cg-pa-m cg-lp-n" data-f="([^"]+)" aria-pressed="false"><span class="cg-pa-m-n"><b>([^<]+)</b> ([^<]+)</span><span class="cg-pa-m-f">([^<]+)</span>', camara)
    assert [n[0] for n in numeros] == ["todos", "dir:poupa", "par", "porta:relator"]
    assert numeros[0][1:] == ("1", f"{topo} confirmado", "espera na Câmara") and "outro 1 a confirmar" in lido and "de 4 projetos que tramitam" in lido
    assert numeros[2][1:] == ("1", f"{topo} confirmado", "parado há mais de um ano") and numeros[3][1:] == ("1", f"{topo} confirmado", "espera um relator")
    # as cabeças das faixas por porta e a tabela gêmea
    assert re.findall(r'<li class="cg-lp-pl" data-porta="([a-z]+)">', camara) == ["mesa", "comissao", "relator", "parecer", "pauta", "recurso"]
    assert "Pronta, sem pauta 1 do topo · 0 confirmados" in lido and "nenhum projeto do topo nesta porta" in lido
    assert "Ver como tabela: porta a porta" in lido and f"{topo.capitalize()} confirmado" in lido and f"conta, por ora, na coluna “{meio}”" in lido
    # o cartão do projeto em foco: os códigos, a conta do peso por extenso, a justificativa, a fonte e a porta
    cartao = _texto("<" + camara.split('<div class="cg-pa-card cg-lp-card">', 1)[1].split('<div class="cg-pa-nav"', 1)[0])
    assert cartao.startswith(f"tramitando PL 10/2026 {topo} peso 60") and f"A leitura {regua.selo} {topo} : {regua.fontes['ia2']}." in cartao
    assert "A conta do peso: 100 × 1,0 (núcleo) × 1,0 (todos) × 0,6 (ajuste de regra) = 60" in cartao and "Corrige a tabela do imposto." in cartao
    assert f"{regua.alavanca.rotulo} {regua.alavanca.codigo('fiscal').rotulo} núcleo" in cartao and f"Marca de contas {regua.marca('contas').de(1).rotulo}" in cartao
    assert "Onde parou" in cartao and "De quem é Ana Lima (PT-SP)" in cartao and "ver o perfil de projetos →" in cartao
    assert 'href="#v=autores&amp;c=cd&amp;quem=cd-1"' in camara
    # o que não dá para concluir, e a linha que leva à próxima vista
    assert "O que não dá para concluir" in lido and "o motivo de não andar, o registro não diz" in lido and "A seguir: quem propõe o que pesa →" in lido
    # o Senado, sem leitura, diz isso
    assert "Sem a leitura dos projetos desta Casa nesta atualização." in _texto("<" + vista.split('<div class="cg-c" data-casa="sf">', 1)[1])


def test_os_projetos_sem_script_nao_mostra_controle_morto(com: str):
    camara = _bloco(com, "cg-lp-proj-cd")
    assert '<div class="cg-lp-fs" role="group" aria-label="Filtros do gráfico" hidden>' in camara
    assert camara.count('<div class="cg-lp-pista" hidden></div>') == 6 and '<div class="cg-pa-nav" hidden>' in camara
    assert '<div class="cg-lp-eixo" aria-hidden="true" hidden>' in camara and '<p class="cg-leg cg-lp-leg" hidden>' in camara
    assert "O gráfico de pontos precisa de script; as listas e a tabela trazem os mesmos números." in camara
    assert "ver no gráfico</button>" not in camara  # o atalho da lista é posto pelo script
    # os pontos não saem no HTML: vêm de projetos-<casa>.json quando a vista abre
    assert not re.search(r'class="cg-lp-p[ "]', camara)


def test_a_tela_diz_qual_e_qual_com_poucos_e_com_todos_confirmados(com: str, confirmado: str, regua: regras.ProjetoDePais):
    """Enquanto a segunda leitura não chega, o projeto do topo aparece como "a confirmar" (com
    o filtro e a legenda dele); confirmado tudo, a tela diz isso e o "a confirmar" some."""
    topo = regua.faixas[-1].rotulo
    antes, depois = _bloco(com, "cg-lp-proj-cd"), _bloco(confirmado, "cg-lp-proj-cd")
    assert 'data-f="conf:0"' in antes and "a confirmar: um leitor só" in antes and "outro 1 a confirmar" in _texto("<" + antes)
    lido = _texto("<" + depois)
    assert f"2 projetos · todos {congresso_lp.plural(topo)} confirmados por dois leitores" in lido and "a confirmar" not in lido.split("O que não dá para concluir", 1)[0]
    assert 'data-f="conf:0"' not in depois and 'data-f="conf:1"' not in depois and "todos confirmados por dois leitores" in depois
    assert f"Pronta, sem pauta 1 {topo} confirmado" in lido and 'data-f="par"' in depois


# ── "Quem propõe" sem script ─────────────────────────────────────────────────


def test_quem_propoe_traz_o_essencial_no_html(com: str, regua: regras.ProjetoDePais):
    piso, topo = regua.faixas[0].rotulo, regua.faixas[-1].rotulo
    camara = _bloco(com, "cg-lp-aut-cd")
    lido = _texto("<" + camara)
    # o que compara pessoas conta só o confirmado (pais.FUNIL), e a tela diz isso
    assert dict(pais.FUNIL)["topo"] == "estr" and _manifesto(com)["lp"]["funil"] == [list(par) for par in pais.FUNIL]
    assert f"Deputados com 10 projetos lidos ou mais · a parte dos projetos de cada um que são {congresso_lp.plural(topo)} confirmados · 0 de 11" in lido
    numeros = re.findall(r'<button type="button" class="cg-pa-m cg-lp-n" data-f="([^"]+)" aria-pressed="false"><span class="cg-pa-m-n"><b>([^<]+)</b> ([^<]+)</span><span class="cg-pa-m-f">([^<]+)</span>', camara)
    assert numeros == [
        ("tem", "1", "deputado", f"tem ao menos um {topo} confirmado"),
        ("acima:topo", "Nenhum", "deputado", f"propõe mais {topo} confirmado do que o acaso explica"),
        ("acima:sb", "Nenhum", "deputado", f"propõe mais projeto da faixa “{piso}” do que o acaso explica"),
    ]
    assert "Ficam fora do gráfico 11 com menos de 10 projetos lidos (9 sem nenhum)." in lido and "de autoria principal (quem assina primeiro)" in lido
    # as três listas levam o nome do que contam; vazias, dizem por quê
    assert f"Quem tem a maior parte de {congresso_lp.plural(topo)} confirmados" in lido and f"Quem tem a maior parte dos projetos na faixa “{piso}”" in lido and "Quem fez virar norma o que pesa" in lido
    assert "melhores" not in lido.lower() and "piores" not in lido.lower()
    assert f"Nenhum deputado com 10 projetos lidos ou mais tem {topo} confirmado." in lido
    assert "O que não dá para concluir" in lido and "Volume não é mérito" in lido
    # sem script: o gráfico, os filtros, a busca e o cartão ficam escondidos, com a frase no lugar
    assert '<div class="cg-pa-fs cg-lp-fs" role="group" aria-label="Medida e filtros do gráfico" hidden>' in camara
    assert '<div class="cg-pa-plot cg-lp-plot" hidden>' in camara and '<div class="cg-pa-cw cg-lp-cw" hidden>' in camara
    assert "O gráfico de pontos precisa de script; as listas abaixo trazem os mesmos números." in camara


# ── os modelos e o script ────────────────────────────────────────────────────


def _nomes_da_regua(regua: regras.ProjetoDePais) -> tuple[set[str], set[str]]:
    """Os nomes que não podem estar escritos na tela: ``(ids, rótulos)`` das faixas, das
    alavancas, das marcas e dos valores delas, e dos códigos de todos os campos. Dos códigos,
    só os rótulos de mais de uma palavra entram como texto (um rótulo de uma palavra só, como
    "Todos", é palavra comum da língua); os ids valem todos, como literal entre aspas."""
    ids = {f.id for f in regua.faixas} | {m.id for m in regua.marcas} | {v.id for m in regua.marcas for v in m.valores} | {c.id for campo in regua.campos for c in campo.codigos}
    rotulos = {f.rotulo for f in regua.faixas} | {c.rotulo for c in regua.alavanca.codigos} | {v.rotulo for m in regua.marcas for v in m.valores if v.id != regras.ND}
    rotulos |= {c.rotulo for campo in regua.campos for c in campo.codigos if " " in c.rotulo}
    return ids - {regras.ND}, rotulos


def test_nenhum_nome_de_categoria_da_regua_esta_escrito_no_modelo_nem_no_script(regua: regras.ProjetoDePais):
    """Faixa, alavanca, marca e código: tudo vem do config (pelo render e pelo manifesto). Se a
    régua mudar um nome, a tela muda sozinha."""
    ids, rotulos = _nomes_da_regua(regua)
    assert {"estruturante", "minimo", "fiscal", "poupa", "contas", "sistema"} <= ids and "médio alcance" in rotulos and "Regra de sistema" in rotulos
    for nome, texto in (("congresso_lp.html.j2", MODELO), ("congresso_lp_barra.html.j2", BARRA), ("congresso_lp.js.j2", SCRIPT), ("congresso_lp.css.j2", ESTILO)):
        for rotulo in sorted(rotulos):
            assert not re.search(r"(?<![\wÀ-ÿ])" + re.escape(rotulo) + r"(?![\wÀ-ÿ])", texto, flags=re.IGNORECASE), f"{nome}: o rótulo “{rotulo}” está escrito"
        for ident in sorted(ids):
            # como literal (no script), como valor de atributo ou de filtro (no modelo) ou como classe (no estilo)
            assert not re.search(r"""['":=]""" + re.escape(ident) + r"""['"]""", texto), f"{nome}: o id “{ident}” está escrito"
            assert not re.search(r"cg-lp-" + re.escape(ident) + r"(?![\w-])", texto), f"{nome}: a classe cg-lp-{ident} leva o nome de uma categoria"


def test_o_script_nao_monta_html_e_e_do_estilo_da_casa():
    assert "innerHTML" not in SCRIPT and "outerHTML" not in SCRIPT and "insertAdjacentHTML" not in SCRIPT and "document.write" not in SCRIPT
    assert "=>" not in SCRIPT and "`" not in SCRIPT and not re.search(r"\b(let|const)\s", SCRIPT)  # o mesmo ES5 dos outros arquivos
    # cada filtro ou foco regrava o endereço sem criar entrada no histórico; só criam a troca de visão, a aba "Como ler" da
    # visão por parlamentar (que grava o endereço dela, #cg-lp-metodo) e os links de vista para vista, com a linha "A seguir"
    assert SCRIPT.count("replaceState(") == 2 and SCRIPT.count("pushState(") == 3
    assert "w.history.pushState(null, '', '#cg-lp-metodo'); lpComoLer();" in SCRIPT and "w.history.pushState(null, '', enderecoDe(destino, l));" in SCRIPT
    # os pontos não levam data-k nem data-m (a página abre o dossiê ou a ficha a qualquer clique nesses atributos)
    for linha in SCRIPT.splitlines():
        if "'cg-lp-p " in linha or "'cg-lp-q'" in linha:
            assert "data-k" not in linha and "data-m" not in linha, linha
    assert "'data-q': q.k" in SCRIPT and "cg-lp-p " not in MODELO  # os pontos nem saem no modelo
    # os arquivos da visão só são pedidos quando uma vista nova abre
    assert SCRIPT.count("buscar(") == 1 and "lpArquivo('projetos', casa" in SCRIPT and "lpArquivo('autores', casa" in SCRIPT and "lpArquivo('autoria', q.k" in SCRIPT


def test_quem_propoe_diz_em_palavras_simples_o_que_e_o_acaso(com: str, pasta_com: Path):
    """O termo fica ("o que o acaso explica"), e vem explicado onde aparece primeiro: uma frase curta na
    abertura da vista e, na dica "i" do gráfico, a explicação inteira (acaso é sorte; a faixa cinza é o que
    a sorte sozinha daria; quem sai dela; e que dentro dela não dá para afirmar)."""
    vista = _vista(com, "autores")
    abertura = _texto(vista.split('<p class="az-ld">', 1)[1].split("</p>", 1)[0])
    assert "Acaso é sorte: a faixa cinza é o que a sorte sozinha daria a um parlamentar médio; dentro dela, não dá para afirmar diferença." in abertura
    assert vista.index("Acaso é sorte") < vista.index("do que o acaso explica")  # antes dos números, que usam o termo
    legenda = vista.split('<figcaption class="az-h cg-lp-h">', 1)[1].split("</figcaption>", 1)[0]
    dica = legenda.split('data-dica="', 1)[1].split('"', 1)[0]
    assert "a faixa cinza é o que o acaso explica" in _texto(legenda)
    comparados = json.loads((dados.pasta_de(pasta_com) / "casa-cd.json").read_text(encoding="utf-8"))["lp"]["autores"]["plotados"]
    quantos = f"que são {comparados} comparados" if comparados > 1 else "todos os comparados"
    assert dica == (
        "Acaso é sorte. Com poucos projetos, um ou dois a mais ou a menos aparecem por sorte, mesmo em quem é igual à média da Casa. "
        "A faixa cinza é o que a sorte sozinha daria a um deputado médio com aquele número de projetos. "
        f"Só sai dela quem tem um resultado que a sorte daria menos de 1 vez em 100, já contando {quantos} de uma vez. "
        "Dentro da faixa não quer dizer “igual à média”: quer dizer que não dá para afirmar."
    )
    assert "378 comparados" in congresso_lp._autores("cd", "deputado", "deputados", {"autores": {"em_exercicio": 513, "plotados": 378}}, congresso_lp.Rotulos.de(pais.manifesto(regras.carregar(ROOT)), regras.carregar(ROOT).projeto_de_pais))["dica_acaso"]


def test_os_links_entre_as_vistas_abrem_a_vista_de_destino_no_comeco(com: str):
    """Os links "→" (``a.cg-lp-ir``: os dos cartões, o da frase de "Pessoa ou partido?", o do cartão de projeto no
    HTML e o "Perfil pelos projetos" do dossiê) e a linha "A seguir" passam pelo mesmo ouvinte: endereço novo no
    histórico, a vista trocada e a página no começo dela. O ouvinte fica no documento, porque o dossiê está fora
    da seção da página."""
    ouvinte = SCRIPT.split("function lpAoComeco()", 1)[1].split("function lpVista(", 1)[0]
    assert "d.addEventListener('click'" in ouvinte and "closest('a[data-lp-ir], a.cg-lp-ir')" in ouvinte and "}, true);" in ouvinte
    assert "w.history.pushState(null, '', destino); doEndereco(); lpAoComeco();" in ouvinte and "w.scrollTo(0, 0)" not in SCRIPT
    assert "if (!a || event.metaKey || event.ctrlKey || event.shiftKey) return;" in ouvinte  # abrir em outra aba continua valendo
    # todo link de vista para vista leva a classe que o ouvinte pega: os que o script monta, o do HTML e o do dossiê
    assert "function lpIr(vista, quem, texto) { return h('a', { cls: 'cg-lp-ir', href: '#v=' + vista" in SCRIPT
    assert re.search(r'<a class="cg-lp-ir" href="#v=autores&amp;c=cd&amp;quem=cd-\d+">ver o perfil de projetos →</a>', com)
    assert "h('a', { cls: 'cg-lp-ir', href: '#v=autores&c=' + f.k.slice(0, 2) + '&quem=' + f.k, text: 'Perfil pelos projetos ('" in com


def test_a_medida_do_endereco_so_vale_se_for_das_contas():
    """``#v=autores&med=constructor`` (ou outro nome herdado de Object) não é medida: a vista cai na
    primeira, como com qualquer valor desconhecido. O teste é pela chave própria de MEDIDAS."""
    assert "function eMedida(med) { return Object.prototype.hasOwnProperty.call(MEDIDAS, med); }" in SCRIPT
    assert "trocarMedida(eMedida(p.med) ? p.med : 'topo')" in SCRIPT and "if (!eMedida(med) || med === F.med) return;" in SCRIPT
    assert not re.search(r"MEDIDAS\[(?:p\.)?med\]\s*(?:\?|&&|\|\|)", SCRIPT) and "!MEDIDAS[" not in SCRIPT


def test_as_frases_do_script_concordam_no_singular_e_o_titulo_da_lista_conta_as_linhas_dela():
    """Com um só, o contador diz "1 realçado", "1 projeto", "1 deputado no gráfico"; quem tem um projeto só não
    lê "100% dos 1 sem leitura"; e "Todos os N projetos" conta as linhas da lista que abre (os que entram nos
    números e os que ficam fora: ementa repetida e de bancada)."""
    assert "temFiltro() ? ' realçados'" not in SCRIPT and SCRIPT.count("(n === 1 ? ' realçado' : ' realçados')") == 4
    assert "(n === 1 ? ' projeto' : ' projetos')" in SCRIPT and "' ' + (n === 1 ? UM : UNS) + ' no gráfico'" in SCRIPT
    assert "q.tot === 1 ? '1 sem leitura' : lpDec(q.sl) + '% dos ' + num(q.tot) + ' sem leitura'" in SCRIPT
    assert "'Todos os ' + num(q.tot)" not in SCRIPT and "var todos = q.tot + (q.rp || 0) + (q.bc || 0)" in SCRIPT
    assert "todos === 1 ? 'O único projeto, com a leitura dele' : 'Todos os ' + num(todos) + ' projetos, com a leitura de cada um'" in SCRIPT
    assert "if (q.tot + (q.rp || 0) + (q.bc || 0)) nos.push(todosOsProjetos(q));" in SCRIPT


def test_o_plural_do_rotulo_nao_inventa_concordancia():
    assert congresso_lp.plural("estruturante") == "estruturantes" and congresso_lp.plural("estrutural") == "“estrutural”"
    assert congresso_lp.plural("médio alcance") == "“médio alcance”" and congresso_lp.plural("maior") == "maiores"


def test_a_rampa_das_faixas_tem_um_matiz_so_e_degraus_visiveis():
    """Os três passos das barras de composição, nos dois temas: do mais claro para o mais forte
    (no escuro, do mais apagado para o mais aceso), com degrau de luminância entre vizinhos."""

    def luminancia(cor: str) -> float:
        canais = [int(cor[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canais)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    temas = re.findall(r"--cg-lp1:(#[0-9A-Fa-f]{6});--cg-lp2:(#[0-9A-Fa-f]{6});--cg-lp3:(#[0-9A-Fa-f]{6})", ESTILO)
    assert len(temas) == 2
    claro, escuro = ([luminancia(c) for c in tema] for tema in temas)
    assert claro[0] > claro[1] > claro[2] and escuro[0] < escuro[1] < escuro[2]
    for tema in (claro, escuro):
        assert all(max(a, b) / min(a, b) > 1.4 for a, b in itertools.pairwise(tema))
