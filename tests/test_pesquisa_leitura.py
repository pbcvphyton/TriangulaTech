"""A Pesquisa analítica de ponta a ponta no Node: o script inteiro da página (busca, fatos da
leitura em ``midia.js.j2``, achados e desenho), rodado sobre arquivos de busca de exemplo com um
DOM mínimo, sem navegador. Confere o que só aparece juntando as camadas: o modo Analítico (a
busca de um nome do placar herda os vereditos de lá, sem diferenciar maiúsculas e acentos), a
contagem de assuntos divididos igual à do dossiê do nome no Analítico e a leitura sem as notícias."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

import pytest

from qijournal.config import load_config
from qijournal.render import midia, pesquisa
from tests.test_midia import SECOES, _dia, _dias

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="sem Node.js para rodar o JavaScript da página")



@pytest.fixture
def config():
    return load_config(env={})


DOM = r"""
function el(id) {
  var e = { id: id, hidden: false, innerHTML: '', textContent: '', value: '', checked: false, open: false, children: [], style: {},
    parentNode: null, offsetHeight: 0, isConnected: true, tagName: 'DIV',
    classList: { _c: {}, toggle: function (c, v) { this._c[c] = v === undefined ? !this._c[c] : !!v; }, add: function (c) { this._c[c] = true; },
      remove: function (c) { delete this._c[c]; }, contains: function (c) { return !!this._c[c]; } },
    addEventListener: function () {}, setAttribute: function () {}, getAttribute: function () { return null; }, hasAttribute: function () { return false; },
    removeAttribute: function () {}, querySelector: function () { return null; }, querySelectorAll: function () { return []; },
    getBoundingClientRect: function () { return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 }; },
    scrollIntoView: function () {}, focus: function () {}, closest: function () { return null; }, contains: function () { return true; }, appendChild: function () {} };
  return e;
}
function select(opcoes) {
  var s = el('select');
  s.options = opcoes.map(function (o) { return { value: o[0], text: o[1] }; });
  s.selectedIndex = 0;
  Object.defineProperty(s, 'value', { get: function () { return this.options[this.selectedIndex].value; } });
  return s;
}
var ELS = {};
['pqStatus', 'pqOut', 'pqAn', 'pqCount', 'pqList', 'pqMore', 'pqCsv', 'pesquisa', 'pqRefR', 'pq-res', 'pqDet', 'pqNav', 'pqLentes', 'pqHist',
 'pqTipos', 'pq-refinar', 'midia', 'pqLimpar', 'pqQ', 'pqDe', 'pqAte', 'pqTit'].forEach(function (id) { ELS[id] = el(id); });
ELS.pqEd = select(SECOES);
ELS.pqV = select([['', 'Todos']].concat(VEICULOS.map(function (v) { return [v, v]; })));
ELS.pqOrd = select([['rec', 'Mais recentes'], ['rel', 'Mais relevantes'], ['ant', 'Mais antigas']]);
var TIPOS = ['m', 'n', 'c', 'a'].map(function (t) { var i = el('tipo'); i.value = t; i.checked = true; return i; });
var FORM = el('pqForm');
FORM.querySelectorAll = function (sel) { return sel.indexOf('tipo') >= 0 ? TIPOS : []; };
FORM.querySelector = function () { return el('sum'); };
ELS.pqForm = FORM;
ELS.pqDados = el('pqDados');
ELS.pqDados.textContent = MANIFESTO;
global.document = {
  getElementById: function (id) { return ELS[id] || null; },
  createElement: function () { return el('novo'); },
  body: { appendChild: function () {} },
  documentElement: { clientWidth: 1440 },
  addEventListener: function () {}
};
global.window = {
  location: { search: BUSCA, hash: '', pathname: '/edicoes/pesquisa.html', href: 'http://x/edicoes/pesquisa.html' + BUSCA },
  history: { replaceState: function () {}, pushState: function () {} },
  matchMedia: function () { return { matches: false, addEventListener: function () {} }; },
  requestAnimationFrame: function (f) { return setTimeout(f, 0); },
  setTimeout: setTimeout, clearTimeout: clearTimeout, pageYOffset: 0, scrollBy: function () {}, addEventListener: function () {},
  fetch: function (url) {
    var dia = /(\d{4}-\d{2}-\d{2})\.json$/.exec(url)[1];
    return Promise.resolve({ ok: !!ARQUIVOS[dia], status: ARQUIVOS[dia] ? 200 : 404, json: function () { return Promise.resolve(ARQUIVOS[dia]); } });
  },
  URLSearchParams: URLSearchParams
};
global.fetch = global.window.fetch;
"""

FIM = r"""
var inicio = Date.now();
(function esperar() {
  var m = ELS.midia.innerHTML, pronto = (/az-manchete/.test(m) && !/pq-sk/.test(m)) || /pq-empty/.test(ELS.pqList.innerHTML);
  if (!pronto && Date.now() - inicio < 30000) return setTimeout(esperar, 20);
  setTimeout(function () {
    console.log(JSON.stringify({ leitura: ELS.midia.innerHTML, lentes: ELS.pqLentes.innerHTML, hist: ELS.pqHist.innerHTML, count: ELS.pqCount.innerHTML, lista: ELS.pqList.innerHTML }));
  }, 50);
})();
"""


def _arquivos() -> tuple[dict[str, Any], list[str]]:
    """14 edições de Política: Fulano de Tal muito citado (com o tom piorando na última semana),
    Beltrano Silva menos; assuntos comparados que citam Fulano no título, só no enfoque de um
    veículo ou sem lados; uma matéria por dia."""
    dias = _dias(14)
    arquivos: dict[str, Any] = {}
    for i, d in enumerate(dias):
        ruim = i >= 7
        manchetes = []
        for j, o in enumerate(["Folha", "g1", "Estadão", "Valor", "CNN"]):
            verbo = "é investigado por escândalo" if ruim and j % 2 == 0 else "anuncia vitória e apoio"
            manchetes.append((o, f"Ministro diz que Fulano de Tal {verbo} no caso {i}-{j}"))
            manchetes.append((o, f"Relator afirma que Beltrano Silva vota hoje o projeto {i}-{j}"))
            manchetes.append((o, f"Câmara aprova texto sobre orçamento do ano {i}-{j}"))
        comparadas = [
            {"s": 0, "t": f"Fulano de Tal e a reforma {i}", "c": "Os veículos divergiram.", "a": "Defende", "b": "Critica", "p": "", "k": "", "h": 0, "u": "",
             "f": [("Folha", "a"), ("g1", "a"), ("Estadão", "b"), ("Valor", "b")]},
            {"s": 0, "t": f"Orçamento em debate {i}", "c": "Todos relataram.", "a": "", "b": "", "p": "", "k": "", "h": 0, "u": "",
             "f": [("Folha", "neutro"), ("g1", "neutro")]},
        ]
        if i % 2:
            comparadas.append({"s": 0, "t": f"Sessão no Senado {i}", "c": "Houve divergência.", "a": "Prazo", "b": "Mérito", "p": "", "k": "", "h": 0, "u": "",
                               "f": [("Folha", "a"), ("CNN", "b")]})
        dado = _dia(d, manchetes, comparadas)
        # o enfoque do primeiro veículo no assunto do Senado cita Fulano: a busca acha o assunto por ele
        for c in dado["comparadas"]:
            if c["t"].startswith("Sessão"):
                c["f"][0][2] = "Destaca o papel de Fulano de Tal"
        dado["materias"] = [{"id": f"m{i}", "s": 0, "t": f"Fulano de Tal no centro da semana {i}", "l": "", "c": "Texto da matéria.", "p": "", "f": [], "h": 0}]
        arquivos[d] = dado
    return arquivos, dias


def _pagina(config, busca: str, arquivos: dict[str, Any]) -> dict[str, str]:
    corpora = [midia.corpus_do_dia(arquivos[d]) for d in sorted(arquivos)]
    destaques = midia.destaques(corpora, fim=max(arquivos))
    resumos = [pesquisa.resumo_do_dia(arquivos[d]) for d in sorted(arquivos)]
    html = pesquisa.render_pesquisa_page(resumos, config, home_href="../", archive_href="./", destaques=destaques)
    manifesto = re.search(r'id="pqDados">(.*?)</script>', html, re.S).group(1)
    script = next(s for s in re.findall(r"<script>(.*?)</script>", html, re.S) if "function leituraMidia(e, ts)" in s)
    veiculos = sorted({v for a in arquivos.values() for v in a["veiculos"]})
    js = (
        f"var SECOES = {json.dumps([['', 'Todas']] + [s[:2] for s in SECOES])};\nvar VEICULOS = {json.dumps(veiculos)};\n"
        f"var MANIFESTO = {json.dumps(manifesto)};\nvar BUSCA = {json.dumps(busca)};\nvar ARQUIVOS = {json.dumps(arquivos)};\n"
        + DOM + "\nvar window = global.window, document = global.document;\n" + script + FIM
    )
    return js, destaques


def _rodar(tmp_path, js: str) -> dict[str, str]:
    arquivo = tmp_path / "pagina.js"
    arquivo.write_text(js, encoding="utf-8")
    saida = subprocess.run(["node", str(arquivo)], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(saida.stdout)


def _texto(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&quot;", '"').replace("&#39;", "'").replace("&amp;", "&")


def test_busca_de_um_nome_do_placar_herda_o_analitico_e_conta_os_mesmos_assuntos(tmp_path, config):
    arquivos, dias = _arquivos()
    js, destaques = _pagina(config, "", arquivos)
    fulano = next(n for n in destaques.nomes if n.nome == "Fulano de Tal")
    # minúsculas e sem acento dão a mesma busca: o modo Analítico vale do mesmo jeito
    busca = f"?q=%22fulano+de+tal%22&de={destaques.inicio}&ed={destaques.secao}"
    js, _ = _pagina(config, busca, arquivos)
    r = _rodar(tmp_path, js)
    leitura, lentes = _texto(r["leitura"]), _texto(r["lentes"])
    assert "mesma base do Analítico" in leitura and "Fulano de Tal" in leitura
    # os assuntos que dividiram os veículos são os do dossiê do nome no Analítico (midia.py)
    m = re.search(r"Dos (\d+) assuntos da cobertura comparada que citam Fulano de Tal, (\d+) tiveram", lentes)
    assert m is not None, lentes[:400]
    assert int(m.group(2)) == fulano.debates
    for texto in (leitura, lentes):
        assert "undefined" not in texto and "NaN" not in texto and "null" not in texto


def test_sem_as_noticias_a_leitura_nao_diz_que_o_termo_nao_esta_nas_manchetes(tmp_path, config):
    arquivos, _ = _arquivos()
    js, _ = _pagina(config, "?q=Fulano&tipo=m", arquivos)
    so_materias = _rodar(tmp_path, js)
    leitura = _texto(so_materias["leitura"])
    assert "Fulano em 14 matérias" in leitura and "filtrada para matérias" in leitura
    assert "não está em nenhuma manchete" not in leitura and 'data-tipo-n="1"' in so_materias["leitura"]  # o botão para incluir as notícias
    js, _ = _pagina(config, "?q=Fulano&tipo=m,c", arquivos)
    com_lados = _rodar(tmp_path, js)  # com a cobertura comparada, a manchete é o racha (a presença não afirma ausência)
    leitura = _texto(com_lados["leitura"])
    assert "enquadram do mesmo jeito" in leitura and "não está em nenhuma manchete" not in leitura


def test_busca_sem_resultado_e_com_html_no_termo(tmp_path, config):
    arquivos, _ = _arquivos()
    js, _ = _pagina(config, "?q=%3Cimg+src%3Dx+onerror%3Dalert(1)%3E", arquivos)
    r = _rodar(tmp_path, js)
    assert "<img" not in r["lista"] and "<img" not in r["count"] and "Nada encontrado" in r["lista"]
    assert "&lt;img" in r["count"]
