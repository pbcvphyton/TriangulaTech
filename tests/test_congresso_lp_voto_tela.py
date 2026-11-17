"""A tela da visão por parlamentar, segunda metade (``render/congresso_lp_voto.py``,
``render/congresso_lp_metodo.py`` e os modelos ``congresso_lp_voto.*.j2`` e
``congresso_lp_metodo.html.j2``): "Na hora do voto", "Pessoa ou partido?" e o bloco "A leitura
dos projetos" do Como ler.

Duas páginas, como em ``test_congresso_lp_tela.py`` (de onde vêm as fábricas). A do Congresso de
mentira tem 11 deputados e uma votação: é o estado em que **não dá para responder por pessoa**,
e a tela tem de dizer isso sem inventar número. A outra é a mesma página com os blocos ``decide``
e ``cruzar`` e a auditoria trocados por números do tamanho dos de verdade (só o que a tela lê):
serve para conferir as barras, os pares, o aviso do voto fiscal e a auditoria no Como ler.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from qijournal.congresso import dados, pais, pais_cruzar, regras
from qijournal.render import congresso_lp, congresso_lp_metodo, congresso_lp_voto
from tests.test_congresso_lp_tela import MODELOS, ROOT, _atualizar, _bloco, _gerar, _nomes_da_regua, _texto

VOTO_HTML = (MODELOS / "congresso_lp_voto.html.j2").read_text(encoding="utf-8")
VOTO_JS = (MODELOS / "congresso_lp_voto.js.j2").read_text(encoding="utf-8")
VOTO_CSS = (MODELOS / "congresso_lp_voto.css.j2").read_text(encoding="utf-8")
METODO_HTML = (MODELOS / "congresso_lp_metodo.html.j2").read_text(encoding="utf-8")

# Um "decide" do tamanho do da Câmara: 21 matérias no voto fiscal, 4 de marca +1 e 17 de −1.
DECIDE = {
    "voto_peso": 20, "materias": 379, "sem_texto": 16, "quase_unanimes": 182, "universo": 197, "lidas": 170, "sem_leitura": 27, "n_votacoes": 78, "de_peso": 74, "demais": 96,
    "fiscais": 21, "confirmadas": 170, "favorece": 4, "contraria": 17, "aprovadas_favorece": 3, "aprovadas_contraria": 17, "com_partido_pct": 95.3, "com_partido_n": 17888,
    "quando_pesa": {"pres": [81.7, 79.5], "fid": [90.5, 88.1]},
    "potencia": {"minimo_votos": 10, "minimo_pessoas": 50, "peso": 498, "fiscal": 425, "por_pessoa": {"peso": True, "fiscal": True}},
    "por_partido": [
        {"pt": "PP", "n": 47, "vf": [37, 23.1, 18.0, 36.0], "fp": [45, 95.5, 88.9, 96.9], "pp": [47, 82.1, 71.6, 89.3], "mm": [3, 21]},
        {"pt": "NOVO", "n": 5, "vf": None, "fp": [5, 100.0, 98.3, 100.0], "pp": None, "mm": [6, 14]},  # menos de 5 medidos: sem mediana
        {"pt": "Outros", "n": 13, "vf": [11, 20.0, 14.3, 30.0], "fp": None, "pp": None, "mm": None},
    ],
    "votacoes": [
        {"id": "9-1", "d": "2026-08-12", "k": "cd-PLP-230-2025", "t": "PLP 230/2025", "w": 24, "ex": "fiscal", "dir": "nd", "pes": 1, "mf": -1, "res": "aprovado", "sim": 333, "nao": 91, "contra_partido": 26, "e": "Altera a lei de responsabilidade fiscal.", "f": 0},
        {"id": "9-2", "d": "2025-11-05", "k": "cd-PL-4357-2023", "t": "PL 4357/2023", "w": 16, "ex": "produtividade", "dir": "neutro", "pes": 0, "mf": 0, "res": "rejeitado", "sim": 113, "nao": 287, "contra_partido": 11, "e": "Altera a lei de locações.", "f": 0},
    ],
    "divergentes": [{"k": "cd-1", "nome": "Fulano de Tal", "pt": "PP", "uf": "SP", "div": 27, "div_cont": 4, "vn": 58}, {"k": "cd-2", "nome": "Beltrana", "pt": "PP", "uf": "RJ", "div": 1, "div_cont": 1, "vn": 30}],
}  # fmt: skip

PARAMETROS = {"zonas": [30, 70], "permutacoes": 1000, "reamostras": 2000, "semente": 57, "confianca": 0.95, "pessoas_minimo": 50, "alfa_pares": 0.01}
CRUZAR = {
    "pessoas": 513, "partidos": 20, "parametros": PARAMETROS,
    "medidas": [
        {"id": "saldo", "grupo": "voto", "n": 510, "k": 20, "e2": 75.1, "ic": [70.2, 79.6], "acaso": 2.2, "zona": "quase_tudo", "distingue": True},
        {"id": "gov", "grupo": "voto", "n": 512, "k": 20, "e2": 69.9, "ic": [63.6, 75.9], "acaso": 2.1, "zona": "parte", "distingue": True},
        {"id": "fid", "grupo": "voto", "n": 512, "k": 20, "e2": 12.2, "ic": [8.8, 16.3], "acaso": 2.5, "zona": "pouco", "distingue": True},
        {"id": "vf", "grupo": "voto", "n": 0, "k": 0, "e2": None, "ic": None, "acaso": None, "zona": "sem_como_dizer", "distingue": False, "motivo": "poucas_pessoas"},
        {"id": "topo", "grupo": "projetos", "n": 377, "k": 20, "e2": 0.0, "ic": [0.0, 10.0], "acaso": 4.3, "zona": "pouco", "distingue": False},
        {"id": "sc", "grupo": "projetos", "n": 39, "k": 9, "e2": None, "ic": None, "acaso": None, "zona": "sem_como_dizer", "distingue": False, "motivo": "poucas_pessoas"},
        {"id": "pres", "grupo": "gabinete", "n": 512, "k": 20, "e2": 7.3, "ic": [2.4, 13.6], "acaso": 2.2, "zona": "pouco", "distingue": True},
    ],
    "frase": {"caso": "voto_sim_projetos_nao", "voto": 75.1, "projetos": 0.0, "voto_distingue": True, "projetos_distingue": False, "n": 510, "k": 20},
    "pares": [
        {"a": "sc", "b": "vf", "n": 226, "rho": 0.25, "dentro": 0.11, "p": 0.00013, "passa": True},
        {"a": "saldo", "b": "sb", "n": 378, "rho": -0.23, "dentro": -0.06, "p": 6.3e-06, "passa": True},
        {"a": "pres", "b": "prop", "n": 513, "rho": 0.06, "dentro": 0.05, "p": 0.16, "passa": False},
        {"a": "topo", "b": "vf", "n": 0, "rho": None, "dentro": None, "p": None, "passa": False, "sem": 1},
    ],
    "por_partido": [{"pt": "PP", "n": 47, "med": {"saldo": 41, "gov": 70.3, "vf": 23.1, "fid": 93.1, "prop": 52, "topo": None, "sb": None, "tip": None, "sc": None, "tx": None, "pres": 82.6, "ct": 36604}}],
}  # fmt: skip

AUDITORIA = {
    "data": "2026-11-16",
    "lidas": 40106,
    "leitores": {"primeira": "o modelo de IA mais barato leu tudo, às cegas", "segunda": "um modelo intermediário releu o topo da régua e as matérias votadas", "terceira": "o modelo mais forte desempatou"},
    "controles": {"o_que_e": "Em cada lote iam escondidas 10 proposições já classificadas.", "lotes": 263, "perguntas": 2630, "mesma_faixa": 2361, "mesma_faixa_pct": 89.8, "campos_iguais_pct": 91.0, "lotes_relidos": 5},
    "piloto": {
        "o_que_e": "Concordância entre dois leitores de IA independentes.", "amostra": 334, "composicao": "150 ao acaso; 90 PLP e PEC",
        "por_campo": {"alavanca": {"igual_pct": 91, "kappa": 0.89}, "contas": {"igual_pct": 86, "kappa": 0.77}},
        "faixa": {"igual_pct": 82, "kappa": 0.74},
        "depois_do_ajuste_das_instrucoes": {"amostra": 184, "composicao": "a parte mais difícil", "faixa": {"igual_pct": 84, "kappa": 0.77}},
        "pares_espelho": {"total": 8, "na_mesma_faixa": 7},
    },
    "segunda_leitura": {
        "lidas": 2146, "mesma_faixa": 811, "mesma_faixa_pct": 37.8, "topo": {"n": 1448, "mesma_faixa": 341}, "votadas": {"n": 698, "mesma_faixa": 470},
        "decididas_pelo_terceiro": 1112, "instaveis": 223, "esperam_o_terceiro": 0, "votadas_com_marca_de_contas_em_disputa": 93,
    },
    "estado_das_leituras": {"uma_leitura": 37960, "duas_na_mesma_faixa": 811, "decidida_pelo_terceiro": 1112, "instavel": 223},
    "limites": ["A leitura é da ementa, não do texto integral.", "Na matéria votada, lê-se a ementa do registro."],
}  # fmt: skip


@pytest.fixture(scope="module")
def regua() -> regras.ProjetoDePais:
    r = regras.carregar(ROOT).projeto_de_pais
    assert r is not None
    return r


@pytest.fixture(scope="module")
def R(regua: regras.ProjetoDePais) -> congresso_lp.Rotulos:
    m = pais.manifesto(regras.carregar(ROOT))
    assert m is not None
    return congresso_lp.Rotulos.de(m, regua)


@pytest.fixture(scope="module")
def pasta(tmp_path_factory: pytest.TempPathFactory, regua: regras.ProjetoDePais) -> Path:
    return _atualizar(tmp_path_factory.mktemp("lpv-com"), regua)


@pytest.fixture(scope="module")
def pequena(pasta: Path) -> str:
    """A página do Congresso de mentira: 11 deputados, uma votação, sem auditoria."""
    return _gerar(pasta)


@pytest.fixture(scope="module")
def grande(pasta: Path, tmp_path_factory: pytest.TempPathFactory) -> str:
    """A mesma página com números do tamanho dos de verdade em ``decide``, ``cruzar`` e na auditoria."""
    copia = tmp_path_factory.mktemp("lpv-grande") / "data"
    shutil.copytree(pasta, copia)
    casa = dados.pasta_de(copia) / "casa-cd.json"
    bloco = json.loads(casa.read_text(encoding="utf-8"))
    bloco["lp"]["decide"], bloco["lp"]["cruzar"] = DECIDE, CRUZAR
    casa.write_text(json.dumps(bloco, ensure_ascii=False), encoding="utf-8")
    meta = dados.pasta_de(copia) / "meta.json"
    m = json.loads(meta.read_text(encoding="utf-8"))
    m["leitura_projetos"]["auditoria"] = AUDITORIA
    m["leitura_projetos"]["cobertura"]["cd"]["voto"] = {"total": 380, "lidos": 342}  # um registro a mais do que as 379 matérias de DECIDE
    meta.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return _gerar(copia)


def _metodo(html: str) -> str:
    return html.split('id="cg-lp-metodo"', 1)[1].split("</section>", 1)[0]


# O bastante de página para rodar as faixas por partido (lpFaixas) no Node: elementos de mentira, com
# a largura que o teste der, e os formatadores da página. ``texto(el)`` devolve o que se lê num elemento.
PAGINA_DE_MENTIRA = r"""
function El(tag) {
  var el = this;
  this.tag = tag; this.filhos = []; this.attrs = {}; this.style = {}; this.cls = {}; this.parentNode = null; this.textContent = ''; this.hidden = false;
  this.classList = { add: function (c) { el.cls[c] = 1; }, remove: function (c) { delete el.cls[c]; }, contains: function (c) { return !!el.cls[c]; } };
}
El.prototype.appendChild = function (f) { f.parentNode = this; this.filhos.push(f); return f; };
El.prototype.removeChild = function (f) { this.filhos.splice(this.filhos.indexOf(f), 1); return f; };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.getAttribute = function (k) { return this.attrs[k] == null ? null : this.attrs[k]; };
El.prototype.addEventListener = function () {};
El.prototype.getBoundingClientRect = function () { return { left: 0, top: 0, width: this.tag === 'span' ? this.textContent.length * 6.3 : LARGURA, height: 20 }; };  // letra de largura fixa
function texto(el) { return el.textContent + el.filhos.map(texto).join(''); }
function h(tag, attrs, filhos) {
  var el = new El(tag), k;
  for (k in attrs || {}) {
    if (attrs[k] == null) continue;
    if (k === 'cls') String(attrs[k]).split(' ').forEach(function (c) { el.cls[c] = 1; }); else if (k === 'text') el.textContent = String(attrs[k]); else el.attrs[k] = String(attrs[k]);
  }
  (filhos || []).forEach(function (f) { if (f == null) return; if (typeof f === 'string') { var t = new El('#text'); t.textContent = f; f = t; } el.appendChild(f); });
  return el;
}
function esvaziar(el) { el.filhos = []; }
function num(v) { return String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, '.'); }
function pct(v) { return Math.round(v) + '%'; }
function saldo(v) { return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(Math.round(v)); }
function lpDec(v) { return v == null ? '—' : String(Math.round(v * 10) / 10).replace('.', ','); }
function faixas() { var caixa = new El('div'); return { caixa: caixa, fx: lpFaixas(caixa, { fig: new El('figure'), dica: new El('div'), longe: false, escolher: function () {}, texto: function () { return ''; } }) }; }
"""


def _faixas_no_node(tmp_path: Path, medidas: dict, cenario: str, *, largura: int = 515) -> dict:
    """Roda no Node o componente das faixas por partido (o trecho de ``congresso_lp_voto.js.j2`` que vai
    de ``LPMED`` até antes de "Na hora do voto"), com ``medidas`` no lugar das do manifesto. ``cenario``
    é o JavaScript do teste: preenche o objeto ``r``, que volta como dicionário."""
    componente = VOTO_JS.split("  var LPMED = ", 1)[1].split("  /* ════ Na hora do voto", 1)[0]
    script = tmp_path / "faixas.js"
    script.write_text(
        f"var LARGURA = {largura}, LPM = {json.dumps({'medidas_tela': medidas, 'outros': 'Outros partidos'})}, LPC = {json.dumps({'min_partido': 5, 'min_pessoas': 50, 'longe': 20})};\n"
        + PAGINA_DE_MENTIRA + "  var LPMED = " + componente + "\nvar r = {};\n" + cenario + "\nconsole.log(JSON.stringify(r));\n",
        encoding="utf-8",
    )
    saida = subprocess.run(["node", str(script)], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(saida.stdout)


# ── Na hora do voto ──────────────────────────────────────────────────────────


def test_na_hora_do_voto_traz_o_essencial_sem_script(grande: str):
    bloco = _bloco(grande, "cg-lp-dec-cd")
    lido = _texto(bloco)
    # a janela e a resposta, antes de tudo
    assert "Votações nominais do texto principal no Plenário da Câmara" in lido and "78 votações" in lido and "74 de matéria de peso 20 ou mais e 4 leves, só do voto fiscal" in lido
    assert '<p class="cg-lp-resp"><b>95 de cada 100</b> votos seguiram o partido fechado.' in bloco and "95,3% dos 17.888 votos dados, nas 78 votações que entram" in lido
    assert bloco.index("cg-lp-resp") < bloco.index("cg-lp-us") < bloco.index("cg-lp-qp") < bloco.index("cg-lp-fxs") < bloco.index("cg-lp-tv") < bloco.index("cg-lp-nao")
    # os números do universo, passo a passo, e por que o resto fica de fora
    for trecho in (
        "379 matérias com votação nominal do texto principal no Plenário",
        "16 só tiveram voto de emenda, substitutivo ou redação final: fora",
        "197 ficam depois de tirar as quase unânimes",
        "182 com 90% ou mais do mesmo lado: fora",
        "170 com leitura; 27 sem leitura, fora",
        "74 de matéria de peso 20 ou mais",
        "as outras 96 lidas são de peso menor: servem de comparação",
        "21 entram no voto fiscal 4 de marca +1 e 17 de marca −1",
        "as outras não têm marca legível no caixa, confirmada por dois leitores",
    ):
        assert trecho in lido, trecho
    # quando pesa: as duas barras de cada medida, na mesma escala, com a diferença dita
    assert "nas 74 votações de matéria de peso 20 ou mais, ao lado das 96 de peso menor" in lido
    assert "Presença votou, entre quem podia votar de peso 81,7% demais 79,5% 2,2 pontos a mais quando pesa" in lido
    assert '<b style="width:90.5%"></b>' in bloco and '<b style="width:88.1%"></b>' in bloco
    # a tabela por partido aceita partido sem mediana (menos de 5 pessoas medidas)
    tabela = bloco.split('data-lp-tab="decide"', 1)[1].split("</details>", 1)[0]
    assert "<summary>Ver como tabela: partido a partido</summary>" in tabela
    assert '<tr><th scope="row">PP</th><td>47</td><td><b>23,1%</b> <small>18% a 36% · 37</small></td>' in tabela
    assert '<tr><th scope="row">NOVO</th><td>5</td><td>—</td><td><b>100%</b> <small>98,3% a 100% · 5</small></td><td>—</td><td>6 de 14</td></tr>' in tabela
    assert f'<tr><th scope="row">{congresso_lp_voto.OUTROS}</th><td>13</td>' in tabela and tabela.count("<td>—</td>") >= 5
    # as votações: a matéria, o peso (em negrito o que entra em "quando pesa"), a marca, o resultado, o placar e quantos votaram contra o próprio partido
    votacoes = bloco.split('class="an-tbl cg-tb cg-lp-tv"', 1)[1].split("</table>", 1)[0]
    assert "As 78 votações que entram" in lido and votacoes.count("<tr data-ex=") == 2
    assert re.search(r'<td>12/08/2026</td><th scope="row">PLP 230/2025<small>Altera a lei de responsabilidade fiscal\.</small></th><td><b>24</b></td><td>[^<]+</td><td><i class="cg-lp-g cg-lp-\w+" aria-hidden="true"></i>−1</td><td>aprovado</td><td>333 × 91</td><td>26</td>', votacoes)
    assert '<td>16</td>' in votacoes and '<span class="cg-lp-fora-vf">fora</span></td><td>rejeitado</td><td>113 × 287</td><td>11</td>' in votacoes
    # quem mais divergiu: em contagem, sem ranking de independência
    assert "Quem mais votou contra o próprio partido fechado" in lido and "não é ranking de independência" in lido
    assert "27 vezes em 58 votos" in lido and "em 4 delas, do lado do caixa (voto fiscal)" in lido and "1 vez em 30 votos" in lido
    assert "em 1 delas, do lado do caixa (voto fiscal)" in lido and "1 dela," not in lido  # uma das vezes: "delas"
    assert "O que não dá para concluir" in lido and "não se votou certo nem se é independente" in lido and "A seguir: pessoa ou partido? →" in lido


def test_o_aviso_do_voto_fiscal_vai_colado_ao_grafico_com_os_numeros(grande: str, R: congresso_lp.Rotulos):
    """Quase todas as matérias do voto fiscal são de uma marca só: a tela diz quantas de cada
    marca e com o que a medida se confunde, dentro da figura (não só no Como ler)."""
    bloco = _bloco(grande, "cg-lp-dec-cd")
    figura = bloco.split('<figure class="cg-pa-fig cg-lp-fig">', 1)[1].split("</figure>", 1)[0]
    aviso = figura.split('<p class="cg-lp-av" data-so-med="vf">', 1)[1].split("</p>", 1)[0]
    lido = _texto(aviso)
    assert figura.index("cg-lp-av") < figura.index('class="cg-lp-fxs"')
    assert "Das 21 matérias do voto fiscal, 17 são de marca −1 e só 4, de marca +1." in lido and "<b>17 são de marca −1</b>" in aviso
    assert "conta quase só quem disse Não a matéria de marca −1" in lido and "deputado de oposição que vota Não a quase tudo o que vai a voto pontua alto do mesmo jeito" in lido
    assert "<b>Voto fiscal alto, aqui, se confunde com ser oposição ao governo do dia.</b>" in aviso and "Compare com o governismo de cada um" in lido
    # o outro lado, e o equilíbrio: sem aviso
    F = congresso_lp_voto.fiscal(R)
    espelho = "".join(t for t, _forte in congresso_lp_voto._um_lado_so(10, 9, 1, F, "senador"))
    assert "9 são de marca +1 e só 1, de marca −1" in espelho and "senador da base que vota Sim a quase tudo" in espelho and "apoiar o governo do dia" in espelho
    assert congresso_lp_voto._um_lado_so(21, 10, 11, F, "deputado") == [] and congresso_lp_voto._um_lado_so(0, 0, 0, F, "deputado") == []
    assert "nenhuma, de marca −1" in "".join(t for t, _forte in congresso_lp_voto._um_lado_so(3, 3, 0, F, "deputado"))


def test_onde_poucos_alcancam_o_corte_a_tela_diz_que_nao_da_para_responder_por_pessoa(pequena: str):
    """11 deputados e uma votação: nada de gráfico por pessoa, nada de conclusão; fica o que há
    por partido, e os textos concordam no singular."""
    bloco = _bloco(pequena, "cg-lp-dec-cd")
    lido = _texto(bloco)
    assert 'data-med=""' in bloco  # nenhuma medida tem gente bastante: o script nem monta o gráfico
    assert "1 votação" in lido and "na votação que entra" in lido and "na votação de matéria de peso 20 ou mais, ao lado da de peso menor" in lido and "a outra lida é de peso menor: serve de comparação" in lido
    assert "A votação que entra" in lido and "cg-lp-vmais" not in bloco
    assert "No voto fiscal, nenhum deputado deu 10 votos ou mais (é só 1 votação; a régua pede ao menos 50 pessoas): não dá para responder por pessoa." in lido
    assert "A tabela mostra o que há por partido, sem concluir que “é o partido”." in lido
    assert "Nenhum deputado com 10 votos ou mais votou contra o próprio partido fechado nestas votações." in lido
    # "Pessoa ou partido?": o título diz que não dá, e nenhuma barra tem número
    cabeca = _texto(pequena.split('id="cg-cruzar"', 1)[1].split("</header>", 1)[0])
    assert "Com 10 nomes em 3 partidos, não dá para separar o partido do acaso." in cabeca and "olhe o partido" not in cabeca
    cruzar = _bloco(pequena, "cg-lp-cru-cd")
    assert 'data-x=""' in cruzar and cruzar.count('<i style="width:0%"></i>') == cruzar.count('class="cg-lp-xb cg-lp-xb-cz"') == 12
    assert _texto(cruzar).count("não dá para responder por pessoa") == 12 and "Não dá para responder por pessoa: ninguém tem as duas medidas (a régua pede ao menos 50)." in _texto(cruzar)


def test_sem_script_as_duas_vistas_nao_mostram_controle_morto(grande: str):
    decide, cruzar = _bloco(grande, "cg-lp-dec-cd"), _bloco(grande, "cg-lp-cru-cd")
    # os filtros, o cartão com ‹ ›, "mostrar todas" e a figura dos pontos nascem escondidos: o script é que os mostra
    for marca in ('<div class="cg-pa-fs cg-lp-fs" role="group" aria-label="Medida e busca" hidden>', '<div class="cg-pa-cw cg-lp-cw" hidden>', '<button type="button" class="cg-pa-limpa cg-lp-vmais" hidden>', '<div class="cg-lp-fxs" hidden></div>'):
        assert marca in decide, marca
    assert '<figure class="cg-pa-fig cg-lp-fig cg-lp-xfig2" hidden>' in cruzar and "<input" not in cruzar.split('cg-lp-xfig2" hidden>', 1)[0]
    assert "O gráfico de pontos precisa de script; a tabela abaixo traz os mesmos números, partido a partido." in decide
    # as tabelas gêmeas já vêm abertas
    assert '<details class="cg-rv-tb cg-pa-tb cg-lp-tb" data-lp-tab="decide" open>' in decide and '<details class="cg-rv-tb cg-pa-tb cg-lp-tb cg-lp-xtb" open>' in cruzar
    # os pontos nem saem no modelo, e não levam data-k nem data-m (ponto de pessoa usa data-q)
    assert "cg-lp-pt" not in VOTO_HTML and "'data-q': q.k" in VOTO_JS
    for linha in VOTO_JS.splitlines():
        if "'cg-lp-pt'" in linha:
            assert "data-k" not in linha and "data-m" not in linha, linha


# ── Pessoa ou partido? ───────────────────────────────────────────────────────


def test_pessoa_ou_partido_a_resposta_e_as_barras_saem_dos_numeros(grande: str):
    cabeca = grande.split('id="cg-cruzar"', 1)[1].split("</header>", 1)[0]
    lido = _texto(cabeca)
    # a frase-resposta: no voto, quase tudo; nos projetos, o intervalo encosta no acaso
    assert "No voto, o partido diz quase tudo. No que cada um propõe, não dá para separar o partido do acaso." in lido
    assert "O partido explica 75% da diferença entre deputados no lado em que votam e 0% na parte de" in lido and "(o intervalo vai de 0% a 10% e encosta no que o acaso daria)" in lido
    assert "Para o voto, olhe o partido; para os projetos, olhe a pessoa." in lido  # até o topo do intervalo fica abaixo de "diz pouco"
    assert "se lê pelo partido" not in lido
    bloco = _bloco(grande, "cg-lp-cru-cd")
    texto = _texto(bloco)
    assert 'data-x="saldo"' in bloco.split(">", 1)[0] and "513 deputados em exercício, em 20 partidos de dois ou mais" in texto and "todas as alavancas" in texto
    # três grupos, e dentro de cada um da maior barra para a menor
    assert [g for g in re.findall(r'<p class="cg-lp-xg">([^<]+)</p>', bloco)] == ["Voto", "Projetos", "Gabinete"]
    assert re.findall(r'class="cg-lp-xb[^"]*" data-x="([a-z]+)"', bloco) == ["saldo", "gov", "fid", "vf", "topo", "sc", "pres"]
    barra = {ident: trecho for ident, trecho in re.findall(r'data-x="([a-z]+)" data-n="\d+" aria-pressed="false">(.*?)</button>', bloco)}
    # a barra, o intervalo e o traço do acaso, em largura e posição; a palavra da zona
    assert '<i style="width:75.1%"></i><u style="left:70.2%;width:9.4%"></u><s style="left:2.2%"></s>' in barra["saldo"]
    assert "<b>75%</b> diz quase tudo" in barra["saldo"] and "régua</em>" in barra["saldo"] and "510 deputados em 20 partidos · intervalo de 95%: de 70% a 80% · por acaso daria até 2%" in barra["saldo"]
    # perto de um corte, o número vai com uma casa: 69,9 é "diz parte" e não pode sair "70%" ao lado de "70% ou mais: quase tudo"
    assert "<b>69,9%</b> diz parte" in barra["gov"] and "intervalo de 95%: de 64% a 76% · por acaso daria até 2%" in barra["gov"] and "70%" not in _texto(barra["gov"])
    assert "<b>12%</b> diz pouco: olhe a pessoa" in barra["fid"] and "régua</em>" not in barra["gov"]
    # o que encosta no acaso sai em cinza, com as palavras; o que não tem gente bastante não tem número
    assert 'class="cg-lp-xb cg-lp-xb-cz" data-x="topo"' in bloco and "<b>0%</b> não se distingue do acaso" in barra["topo"]
    assert 'class="cg-lp-xb cg-lp-xb-cz" data-x="sc"' in bloco and "não dá para responder por pessoa: só 39 podem ser medidos (a régua pede ao menos 50)" in barra["sc"] and "<b>" not in barra["sc"].split("cg-lp-xv", 1)[1]
    # cada barra leva quantas pessoas as contas mediram: o painel ao lado decide (e escreve) pelo mesmo número
    assert 'data-x="sc" data-n="39" aria-pressed' in bloco and 'data-x="saldo" data-n="510" aria-pressed' in bloco
    assert "não dá para responder por pessoa: ninguém pode ser medido (a régua pede ao menos 50)" in barra["vf"]
    assert 'class="cg-lp-xb" data-x="pres"' in bloco and "<b>7%</b> diz pouco: olhe a pessoa" in barra["pres"]
    # barra colorida cujo intervalo (2,4) e acaso (2,2) dariam "de 2%" e "até 2%": os dois vão com uma casa, para não parecer que encosta
    assert "512 deputados em 20 partidos · intervalo de 95%: de 2,4% a 13,6% · por acaso daria até 2,2%" in barra["pres"]
    # "por acaso daria até N%" vem explicado em palavras simples: na dica "i" do gráfico das barras e na nota embaixo delas
    dica = bloco.split("Quanto o partido explica <span>", 1)[1].split("</figcaption>", 1)[0].split('data-dica="', 1)[1].split('"', 1)[0]
    assert dica.startswith("Acaso é sorte. Mesmo que o partido não dissesse nada, dividir os deputados em tantos grupos já daria algum número, só por sorte.")
    # o traço é o 95º percentil dos embaralhamentos, não o máximo: a frase não diz "até onde a sorte chega" sem dizer em quantas vezes
    assert pais_cruzar.PERCENTIL_DO_ACASO == 95.0 and "até onde a sorte chega" not in bloco
    assert "O traço “por acaso daria até” mostra até onde a sorte costuma chegar: com os partidos embaralhados entre as pessoas 1.000 vezes, o número fica abaixo dele em 95 de cada 100." in dica
    assert "A barra só conta quando o intervalo dela fica todo acima do traço. Barra em cinza não quer dizer “é a pessoa”: quer dizer que não dá para afirmar." in dica
    assert "sai em cinza: acaso é sorte, e “por acaso daria até” é o que a sorte sozinha daria em 95 de cada 100 vezes, com os partidos embaralhados entre as pessoas." in texto
    assert "70% ou mais: o partido diz quase tudo; de 30% a 70%: diz parte; abaixo de 30%: diz pouco." in texto
    # o que anda junto: a pergunta, a barra a partir do zero e a resposta em frase; só afirma o que passa do acaso
    pares = bloco.split('<ul class="cg-lp-pares">', 1)[1].split("</ul>", 1)[0].split("<li")[1:]
    assert len(pares) == 4
    assert 'class="cg-lp-pb-p" style="width:12.5%"' in pares[0] and "<b>0,25</b> Quem propõe mais do lado do caixa dá mais voto fiscal: correlação fraca (0,25; n = 226)." in pares[0] and "dentro do mesmo partido: 0,11 (fraca)" in pares[0]
    assert 'class="cg-lp-pb-n" style="width:11.5%"' in pares[1] and "<b>−0,23</b> Quem vota mais à esquerda tem parte maior na faixa" in pares[1]
    assert pares[2].startswith(' class="cg-lp-par-cz">') and "Não se distingue do acaso (0,06; n = 513)." in pares[2]
    assert "Quem vota mais apresentou mais projetos na legislatura?" in pares[2]  # a pergunta diz a janela da medida, a da legislatura
    assert pares[3].startswith(' class="cg-lp-par-cz">') and "Não dá para responder por pessoa: ninguém tem as duas medidas" in pares[3] and "cg-lp-pb-" not in pares[3]
    assert "Só vira afirmação o par que passa do acaso a 99%, já contando os 4 pares" in texto and "andar junto não é causa" in texto
    # a tabela das medianas aceita partido sem mediana
    tabela = bloco.split('class="cg-rv-tb cg-pa-tb cg-lp-tb cg-lp-xtb"', 1)[1].split("</details>", 1)[0]
    assert '<tr><th scope="row">PP</th><td>47</td><td>+41</td><td>70%</td><td>23%</td><td>93%</td><td>52</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>83%</td><td>R$ 36.604</td></tr>' in tabela
    assert "O que não dá para concluir" in texto and "Barra em cinza não quer dizer “é a pessoa”" in texto


@pytest.mark.parametrize(
    ("frase", "ic_projetos", "titulo", "olhe"),
    [
        ({"caso": "voto_sim_projetos_nao", "projetos_distingue": True}, [2.0, 12.0], "No voto, o partido diz quase tudo. No que cada um propõe, quase nada.", True),
        ({"caso": "voto_sim_projetos_nao", "projetos_distingue": True}, [2.0, 41.0], "No voto, o partido diz quase tudo. No que cada um propõe, quase nada.", False),
        ({"caso": "voto_sim_projetos_parte", "projetos_distingue": True}, [31.0, 60.0], "No voto, o partido diz quase tudo. No que cada um propõe, só uma parte.", False),
        ({"caso": "os_dois", "projetos_distingue": True}, [71.0, 90.0], "O partido diz quase tudo: no voto e no que cada um propõe.", False),
        ({"caso": "nem_o_voto", "projetos_distingue": False}, [0.0, 10.0], "Nem no voto o partido diz tudo. No que cada um propõe, não dá para separar o partido do acaso.", False),
        ({"caso": "sem_como_dizer", "n": 72, "k": 10}, [0.0, 70.0], "Com 72 nomes em 10 partidos, não dá para separar o partido do acaso.", False),
    ],
)
def test_a_frase_resposta_muda_com_os_numeros(R: congresso_lp.Rotulos, frase: dict, ic_projetos: list[float], titulo: str, olhe: bool):
    """O título de "Pessoa ou partido?" é gerado dos números. O conselho curto ("para o voto,
    olhe o partido; para os projetos, olhe a pessoa") só sai quando eles dizem isso: quase tudo
    no voto e, nos projetos, até o topo do intervalo abaixo do corte de "diz pouco". E "olhe a
    pessoa" não promete que a pessoa se distingue: se ninguém sai da faixa do acaso em "Quem
    propõe", na mesma medida, a linha diz isso com o número."""
    medidas = {"saldo": {"e2": 75.1, "ic": [70.0, 80.0], "distingue": True}, "topo": {"e2": 5.0, "ic": ic_projetos, "distingue": bool(frase.get("projetos_distingue"))}}
    r = congresso_lp_voto.resposta(frase, medidas, [30.0, 70.0], "deputado", "deputados", R)
    assert " ".join(r["titulo"]) == titulo and r["olhe"] is olhe
    linha = "".join(t for t, _forte in r["linha"])
    conselho = "Para o voto, olhe o partido; para os projetos, olhe a pessoa."
    assert (conselho in linha) is olhe and "Quanto maior a barra, mais o partido basta" in linha and "faixa cinza" not in linha
    # com o funil de "Quem propõe": ninguém fora da faixa do acaso, e a linha diz; alguém fora, e não diz
    ninguem = "".join(t for t, _f in congresso_lp_voto.resposta(frase, medidas, [30.0, 70.0], "deputado", "deputados", R, {"plotados": 378, "acima_topo": 0, "abaixo_topo": 0})["linha"])
    alguem = "".join(t for t, _f in congresso_lp_voto.resposta(frase, medidas, [30.0, 70.0], "deputado", "deputados", R, {"plotados": 378, "acima_topo": 2, "abaixo_topo": 0})["linha"])
    assert ("nenhum dos 378 deputados comparados em “Quem propõe” sai da faixa cinza" in ninguem) is olhe and "faixa cinza" not in alguem
    assert "se lê pelo partido" not in linha + ninguem  # a frase antiga afirmava que a proposta "se lê pela pessoa"


def test_o_numero_escrito_concorda_com_a_palavra_e_com_a_cor_perto_de_um_corte(R: congresso_lp.Rotulos):
    """A palavra ("diz parte"), a cor da barra e o número ao lado saem do mesmo lugar. Nas contas, a zona e "distingue"
    são decididos pelo número publicado (uma casa). Na tela, o percentual é inteiro, a não ser que o inteiro caia do
    outro lado de um corte: aí vai com a casa das contas. O mesmo na linha da frase-resposta, cujos números decidem o
    título."""
    zonas = (30.0, 70.0)
    assert pais_cruzar.publicado(0.6996, (0.6, 0.8), 0.02, zonas) == {"e2": 70.0, "ic": [60.0, 80.0], "acaso": 2.0, "zona": "quase_tudo", "distingue": True}
    assert pais_cruzar.publicado(0.6994, (0.6, 0.8), 0.02, zonas)["zona"] == "parte" and pais_cruzar.publicado(0.2996, None, None, zonas)["zona"] == "parte"
    assert pais_cruzar.publicado(0.073, (0.0244, 0.136), 0.0236, zonas)["distingue"] is False  # 2,4 contra 2,4: encosta
    assert pais_cruzar.publicado(0.073, (0.0246, 0.136), 0.0236, zonas)["distingue"] is True  # 2,5 contra 2,4
    assert pais_cruzar.publicado(0.073, None, 0.02, zonas)["distingue"] is False and pais_cruzar.publicado(0.073, (0.03, 0.1), None, zonas)["distingue"] is False
    escritos = {v: congresso_lp_voto._pct_do_corte(v, zonas) for v in (69.9, 69.5, 70.0, 70.4, 29.6, 30.4, 75.1, 0.3, 0.0)}
    assert escritos == {69.9: "69,9%", 69.5: "69,5%", 70.0: "70%", 70.4: "70%", 29.6: "29,6%", 30.4: "30%", 75.1: "75%", 0.3: "0,3%", 0.0: "0%"}
    medidas = {"saldo": {"e2": 69.9, "ic": [63.6, 75.9], "distingue": True}, "topo": {"e2": 5.0, "ic": [0.0, 29.6], "distingue": False}}
    r = congresso_lp_voto.resposta({"caso": "nem_o_voto", "projetos_distingue": False}, medidas, [30.0, 70.0], "deputado", "deputados", R)
    linha = "".join(t for t, _forte in r["linha"])
    assert r["titulo"][0] == "Nem no voto o partido diz tudo." and "O partido explica 69,9% da diferença entre deputados no lado em que votam e 5% na parte de" in linha
    assert "(o intervalo vai de 0% a 29,6% e encosta no que o acaso daria)" in linha


def test_o_par_de_quantos_projetos_diz_a_janela_da_medida(R: congresso_lp.Rotulos):
    """A medida conta os projetos apresentados na legislatura (a coluna ``tot``). Quando o par passa do acaso, a frase
    diz isso, e não "apresenta mais projetos", que se lia como o total do dossiê."""
    lp = {"cruzar": {**CRUZAR, "pares": [{"a": "pres", "b": "prop", "n": 513, "rho": 0.2, "dentro": 0.22, "p": 4e-06, "passa": True}, {"a": "ct", "b": "prop", "n": 512, "rho": -0.21, "dentro": -0.2, "p": 1e-05, "passa": True}]}}
    pares = congresso_lp_voto.cruzar("cd", "deputado", "deputados", lp, R, congresso_lp_voto.medidas(R))["pares"]
    assert (pares[0]["pergunta"], pares[0]["resposta"]) == ("Quem vota mais apresentou mais projetos na legislatura?", "Quem vota mais (presença) apresentou mais projetos na legislatura: correlação fraca (0,20; n = 513).")
    assert pares[1]["pergunta"] == "Quem gasta mais cota apresentou mais projetos na legislatura?" and "apresentou menos projetos na legislatura: correlação fraca (−0,21; n = 512)." in pares[1]["resposta"]


def test_as_medidas_da_tela_sao_as_das_contas(R: congresso_lp.Rotulos, regua: regras.ProjetoDePais):
    """O script tira o valor de cada pessoa das colunas que :func:`medidas` aponta: toda medida
    das contas tem de estar lá, com nome, unidade e coluna, e a coluna é a que as contas usam."""
    from qijournal.congresso import pais_cruzar

    M = congresso_lp_voto.medidas(R)
    assert {ident for ident, _g, _v in pais_cruzar.MEDIDAS} <= set(M) and set(congresso_lp_voto.DO_VOTO) <= set(M)
    for ident, m in M.items():
        assert m["nome"] and m["curto"] and m["un"] in ("%", "saldo", "n", "peso", "reais") and len(m["col"]) in (1, 3), ident
    linha = {"va": 6, "vn": 16, "estr": 1, "n": 9, "saldo": -25, "prop": None}
    assert congresso_lp_voto.valor(linha, M["vf"]) == pytest.approx(37.5) and congresso_lp_voto.valor(linha, M["topo"]) is None  # menos de 10 projetos lidos
    assert congresso_lp_voto.valor(linha, M["saldo"]) == -25 and congresso_lp_voto.valor(linha, M["prop"]) == 0 and congresso_lp_voto.valor(linha, M["gov"]) is None
    # uma pessoa com todas as colunas dá, em cada medida, o mesmo valor na tela e nas contas
    completa = {"saldo": -25, "gov": 61.5, "va": 6, "vn": 16, "fid": 88.0, "tot": 12, "prop": 66, "n": 11, "estr": 1, "sb": 3, "tip": 4.5, "sc": -20, "tx": 9.1, "pres": 77.0, "ct": 31000}
    das_contas = pais_cruzar.valores([completa], regua)
    for ident, _grupo, _valor in pais_cruzar.MEDIDAS:
        assert congresso_lp_voto.valor(completa, M[ident]) == pytest.approx(das_contas[ident][0]), ident
    # "quantos projetos apresentou" é da legislatura (12), não o total do dossiê (66), e o nome diz a janela
    assert congresso_lp_voto.valor(completa, M["prop"]) == 12 and M["prop"]["col"] == ["tot"] and M["prop"]["nome"] == "Quantos projetos apresentou na legislatura"


# ── Como ler: a leitura dos projetos ─────────────────────────────────────────


def test_o_como_ler_ganha_a_leitura_dos_projetos_com_a_regua_do_config(grande: str, regua: regras.ProjetoDePais):
    bloco = _metodo(grande)
    lido = _texto(bloco)
    assert "A leitura dos projetos só na visão por parlamentar (Os projetos, Quem propõe, Na hora do voto, Pessoa ou partido?); na visão por partido, nenhuma IA entra nas contas" in lido
    # a premissa declarada: o objetivo e as alavancas do nível mais alto, do config, como escolha editorial
    nucleo = [c.rotulo for c in regua.alavanca.codigos if c.nivel == regua.niveis[0][0]]
    assert f"um objetivo declarado: {regua.objetivo}." in lido and "são escolha editorial do dono do jornal, escrita em config/congresso.yaml" in lido
    assert all(rotulo in lido.split("A premissa", 1)[1].split("O que é lido", 1)[0] for rotulo in nucleo) and "Quem parte de outro objetivo chega a outra régua." in lido
    # a régua: os seis campos com todos os códigos e o que valem, a conta do peso, as faixas e as três marcas
    assert len(regua.campos) == 6 and bloco.count('<th scope="rowgroup"') == 6
    for campo in regua.campos:
        assert f'<tr><th scope="row">{campo.rotulo}</th>' in bloco, campo.id
        for codigo in campo.codigos:
            assert f"<td>{codigo.rotulo}</td>" in bloco and codigo.definicao in lido, codigo.id
    assert f"Ver a definição de cada um dos {sum(len(c.codigos) for c in regua.campos)} códigos" in lido
    de_peso = regua.campos_de_peso
    assert f"Peso = 100 × o nível de “{regua.alavanca.rotulo}” × “{de_peso[0].rotulo}” × “{de_peso[1].rotulo}”, arredondado" in lido
    assert f"Por exemplo: 100 × 1 ({regua.niveis[0][2]}) × 0,6 ({de_peso[0].codigos[1].rotulo}) × 1 ({de_peso[1].codigos[0].rotulo}) = 60." in lido
    assert f"{regua.niveis[0][2]} × 1; {regua.niveis[1][2]} × 0,6; {regua.niveis[2][2]} × 0,3" in lido
    for faixa, pesos in zip(regua.faixas, ("0 a 9", "10 a 19", "20 a 59", "60 ou mais"), strict=True):
        assert f"<li><b>{faixa.rotulo}</b> <small>{pesos}</small>" in bloco, faixa.id
    assert f"só em alavanca do {regua.niveis[0][2]}" in lido
    for marca in regua.marcas:
        assert f"<li><b>{marca.rotulo}</b>" in bloco and all(v.rotulo in lido for v in marca.valores if v.valor is not None), marca.id
        assert f"dá a marca “{marca.rotulo}”" in lido
    # os termos das quatro vistas
    for termo in ("O que é lido", "O peso (0 a 100)", "As três marcas", "Parado, e em que porta", "O perfil de cada um", "A faixa do acaso", "Quando pesa", "Voto fiscal", "Contra o próprio partido", "Quanto o partido explica", "O que anda junto"):
        assert f"<dt>{termo}</dt>" in bloco, termo
    assert "o traço “por acaso daria até” mostra até onde a sorte costuma chegar. É o valor que 95 de cada 100 embaralhamentos dos partidos entre as pessoas não passam (são 1.000 embaralhamentos); o intervalo sai reamostrando as pessoas 2.000 vezes." in lido
    assert "até onde a sorte chega" not in lido
    # "o acaso", em palavras simples, nos dois termos em que ele entra; a conta continua dita
    faixa = _texto(bloco.split("<dt>A faixa do acaso</dt>", 1)[1].split("</dd>", 1)[0])
    assert faixa.startswith("Acaso é sorte. Com poucos projetos, um ou dois a mais ou a menos aparecem por sorte, mesmo em quem é igual à média da Casa.")
    assert "é o que a sorte sozinha daria a um parlamentar médio com aquele número de projetos" in faixa and "a sorte daria menos de 1 vez em 100, já contando que são muitos comparados de uma vez" in faixa
    assert "Dentro da faixa não quer dizer “igual à média”: quer dizer que não dá para afirmar." in faixa and "binomial exata contra a taxa da Casa, 99% de confiança" in faixa
    # o que a conta supõe, e por que a borda não é para ser lida ao pé da letra (está no comentário das contas, e tem de estar na página)
    assert faixa.endswith("Ela trata cada projeto como um sorteio separado; como os projetos de uma mesma pessoa se parecem, a faixa de verdade é um pouco mais larga que a desenhada: quem está bem na borda não deve ser lido como fora dela.")
    assert "Aqui o acaso também é sorte: mesmo que o partido não dissesse nada, dividir as pessoas em tantos grupos já daria algum número" in lido
    # a janela da medida de quantos projetos: a da legislatura (a do perfil), não o total do dossiê
    assert "“Quantos projetos apresentou na legislatura” conta os PL, PLP e PEC de autoria principal apresentados na legislatura, com ou sem leitura" in lido and "não o total do dossiê" in lido
    # o que a régua não mede, os cinco erros de leitura e os limites
    erros = bloco.split('<ol class="cg-lp-met-e">', 1)[1].split("</ol>", 1)[0]
    assert erros.count("<li>") == 5 and "<b>Peso como qualidade.</b>" in erros and "<b>“Voto fiscal” como voto certo ou como independência.</b>" in erros
    assert "o custo em reais, a constitucionalidade, o efeito real" in lido and "ementa vaga fica sem leitura; ementa franca leva as marcas" in lido


def test_o_como_ler_publica_a_cobertura_e_a_auditoria_que_vieram(grande: str, pequena: str):
    lido = _texto(_metodo(grande))
    # a cobertura, de meta.json (o Congresso de mentira tem seis proposições lidas na Câmara)
    cobertura = _metodo(grande).split('class="an-tbl cg-tb cg-lp-met-cb"', 1)[1].split("</table>", 1)[0]
    assert '<tr><th scope="row">Câmara</th><td>6</td><td><b>6</b> <small>100%</small></td><td>0</td><td>1</td><td>5 de 5</td>' in cobertura and cobertura.count("<tr>") == 2
    # as matérias votadas da cobertura são proposições do registro; "Na hora do voto" conta matérias: quando os números diferem, a nota diz
    assert "<td>342 de 380</td>" in cobertura and "379 matérias" in _texto(_bloco(grande, "cg-lp-dec-cd"))
    assert (
        "Na Câmara, as matérias votadas são 380 proposições do registro para as 379 matérias que “Na hora do voto” conta: há 1 registro a mais, "
        "de matéria que foi a voto sob mais de um número de registro (acontece com a que volta da outra Casa e é votada de novo, com outra ementa). "
        "Aqui ela é lida uma vez por registro; no voto, conta uma vez só, pela última votação."
    ) in lido
    assert "proposições do registro para as" not in _texto(_metodo(pequena))  # sem diferença, nada a explicar
    # quem leu e o estado das leituras
    assert "Primeira leitura. O modelo de IA mais barato leu tudo, às cegas." in lido and "Segunda leitura. Um modelo intermediário releu" in lido and "Terceira leitura. O modelo mais forte desempatou." in lido
    # a frase da auditoria leva a data dela: o que chegou depois é da leitura de todo dia
    assert "Das 40.106 proposições lidas até 16/11/2026: 37.960 têm uma leitura só, 811 têm duas leituras na mesma faixa, 1.112 foram decididas pelo terceiro leitor e 223 ficaram instáveis" in lido
    # as perguntas de controle, o piloto e a segunda leitura, com os números que vieram
    assert "As perguntas de controle" in lido and "Resultado: 2.361 de 2.630 na mesma faixa (89,8%), em 263 lotes." in lido and "5 lotes foram relidos." in lido
    assert "Amostra: 334 ementas (150 ao acaso; 90 PLP e PEC)." in lido and "A faixa do peso 82% 0,74" in lido and "A marca “Contas” 86% 0,77" in lido
    assert "Depois do ajuste das instruções, em 184 ementas (a parte mais difícil): mesma faixa em 84% (kappa 0,77)." in lido and "7 de 8." in lido
    assert "releu 2.146 proposições sem ver a primeira leitura: 1.448 que a primeira pôs no topo da régua e 698 matérias votadas." in lido
    assert "No topo, só 341 de 1.448" in lido and "Em 93 matérias votadas, os leitores divergiram na marca do caixa: elas ficam fora do voto fiscal." in lido and "ainda esperam o terceiro" not in lido
    # a frase que diz o que isto mede, e os limites da auditoria
    assert f"Tudo isto {congresso_lp_metodo.CONSTANCIA}." in lido and congresso_lp_metodo.CONSTANCIA == "mede a constância entre leitores de IA; não foi conferida contra codificação humana"
    assert "<li>A leitura é da ementa, não do texto integral.</li>" in _metodo(grande) and "<li>Na matéria votada, lê-se a ementa do registro.</li>" in _metodo(grande)
    # sem o arquivo da auditoria: a tela diz que ela não veio, não inventa número, e os limites são os da régua
    sem_auditoria = _texto(_metodo(pequena))
    assert "A auditoria da leitura não veio nesta atualização." in sem_auditoria and "kappa" not in sem_auditoria.lower() and "Primeira leitura" not in sem_auditoria
    assert f"Tudo isto {congresso_lp_metodo.CONSTANCIA}." in sem_auditoria and all(limite in sem_auditoria for limite in congresso_lp_metodo.LIMITES)


def test_toda_tabela_do_bloco_da_leitura_rola_dentro_da_caixa_dela(grande: str):
    """Tabela fora da caixa (``an-tbl-wrap``) não rola sozinha: no celular estreito (360 px ou menos), "Os dois
    leitores, lado a lado" fazia a página inteira rolar de lado, e o Como ler abria deslocado."""
    tabelas = re.findall(r'(<div class="an-tbl-wrap">)?<table class="([^"]+)"', _metodo(grande))
    assert len(tabelas) >= 4 and "an-tbl cg-tb cg-lp-met-c" in [classe for _caixa, classe in tabelas]
    assert [classe for caixa, classe in tabelas if not caixa] == []


DIARIA = {
    "data": "2026-11-18", "quando": "2026-11-18T09:10:00+00:00", "rodadas": 1, "primeiras": 20, "segundas": 5, "terceiras": 0, "lotes": 2, "descartados": 0, "falhas": 0, "controles": [17, 20],
    "confirmadas": 5, "decididas": 0, "instaveis": 0, "invalidas": 1, "mesmo_editor": 5, "chamadas": 2, "tokens": {"entrada": 1, "saida": 1}, "pendentes": {"primeiras": 12, "segundas": 3},
    "provedor": "editor_a → editor_b", "modelo": "modelo-a + modelo-b", "motivo": "",
    "periodo": {"dias": 3, "desde": "2026-11-16", "leu_em": "2026-11-17", "rodadas": 3, "primeiras": 143, "segundas": 31, "terceiras": 2, "lotes": 7, "descartados": 1, "falhas": 0, "mesmo_editor": 5, "de_outra_rodada": 12, "controles": [52, 70]},
}  # fmt: skip


def test_o_como_ler_diz_como_e_a_leitura_de_todo_dia_com_e_sem_o_registro(grande: str, pequena: str, pasta: Path, tmp_path: Path):
    """O parágrafo "A leitura de todo dia" sai sempre (quem lê o que chega depois da leitura inicial, os lotes
    com as perguntas de controle e o mínimo de acertos do config, a segunda leitura, o "sem leitura" até lá).
    Com ``leitura_projetos.diaria`` em ``meta.json``, ganha os números do período e quem respondeu."""
    from qijournal.congresso import ler

    limites = ler.Limites()
    assert (limites.lote, limites.controles, limites.controles_minimo) == (30, 10, 7)  # os do config de hoje, que a frase repete
    for html, depois in ((grande, "depois de 16/11/2026"), (pequena, "depois da leitura inicial")):
        bloco = _metodo(html)
        paragrafo = _texto(bloco.split('<p class="cg-lp-met-k" id="cg-lp-diaria">A leitura de todo dia</p>', 1)[1].split("</p>", 1)[0])
        assert paragrafo.startswith(f"As proposições e as matérias votadas que chegam {depois} são lidas todo dia pela cadeia de IAs do jornal")
        assert "Vão em lotes de até 30, e em cada lote vão escondidas 10 perguntas de controle" in paragrafo and "o lote que acerta a faixa em menos de 7 delas é descartado inteiro" in paragrafo
        assert "ganha uma segunda leitura, de outra IA da cadeia quando há. Se as duas caem em faixas diferentes, uma terceira decide, de uma IA que ainda não leu a proposição, quando há." in paragrafo
        assert "Quando só uma IA responde, a segunda ou a terceira é de quem já tinha lido a mesma proposição, e o registro conta quantas foram assim." in paragrafo
        assert "Quando a primeira leitura é de uma rodada anterior, o registro não guarda de que IA ela foi: a segunda evita a IA que costuma fazer as primeiras, sem garantia de que seja outra, e o registro conta essas à parte." in paragrafo
        assert "editor" not in paragrafo  # quem lê é "IA", como no resto do parágrafo
        assert "Até ser lida, a proposição nova aparece como “sem leitura”, fora das contas" in paragrafo and "a de todo dia tem só as perguntas de controle" in paragrafo
        # sem o registro: nenhum número de rodada, nenhum editor
        assert "primeiras leituras" not in paragrafo and "quem respondeu" not in paragrafo and "A última rodada" not in paragrafo
        assert bloco.index('id="cg-lp-diaria"') < bloco.index("O que isto mede")
    # com o registro: os números do período, a última rodada, quem respondeu e a fila
    copia = tmp_path / "data"
    shutil.copytree(pasta, copia)
    meta = dados.pasta_de(copia) / "meta.json"
    m = json.loads(meta.read_text(encoding="utf-8"))
    m["leitura_projetos"]["auditoria"] = AUDITORIA
    meta.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    sem_registro = _gerar(copia)
    m["leitura_projetos"]["diaria"] = DIARIA
    meta.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    com_registro = _gerar(copia)
    paragrafo = _texto(_metodo(com_registro).split('id="cg-lp-diaria">A leitura de todo dia</p>', 1)[1].split("</p>", 1)[0])
    assert (
        "Desde 16/11/2026 (3 dias com rodada): 143 primeiras leituras, 31 segundas e 2 terceiras; "
        "das segundas e terceiras, 5 foram feitas por uma IA que já tinha lido a mesma proposição na rodada; "
        "em 12 das segundas, a primeira leitura era de outra rodada e o registro não diz de que IA era (pode ter sido a mesma); "
        "nos controles, 52 de 70 na faixa certa (74,3%); 1 lote descartado, de 7."
    ) in paragrafo
    assert "A última rodada foi em 18/11/2026 (a última em que algo foi lido, em 17/11/2026). Nela, quem respondeu: editor_a → editor_b (modelo-a + modelo-b)." in paragrafo
    assert "Ficaram na fila: 12 sem leitura e 3 à espera da segunda leitura." in paragrafo
    # o registro só acrescenta ao parágrafo: o resto da página é igual, byte a byte
    de, ate = '<p class="cg-lp-met-k" id="cg-lp-diaria">', '<p class="cg-lp-av"><b class="cg-lp-av-k">O que isto mede</b>'
    assert com_registro.split(de, 1)[0] == sem_registro.split(de, 1)[0] and com_registro.split(ate, 1)[1] == sem_registro.split(ate, 1)[1]
    antes = sem_registro.split(de, 1)[1].split(ate, 1)[0]
    assert com_registro.split(de, 1)[1].split(ate, 1)[0].startswith(antes.rstrip().removesuffix("</p>")) and "<b>Desde 16/11/2026" not in antes


def test_a_leitura_de_todo_dia_so_escreve_o_que_veio(R: congresso_lp.Rotulos, regua: regras.ProjetoDePais):
    """O resumo do registro é só repassado: chave que falta, ou de outro tipo, não vira número inventado."""
    from qijournal.congresso import ler

    limites = ler.Limites()
    vazio = congresso_lp_metodo._diaria(None, limites, "")
    assert vazio["numeros"] == "" and vazio["quem"] == "" and "depois da leitura inicial" in vazio["como"]
    # o registro pela metade (o período sem lotes; controles zerados; nenhum editor respondeu)
    y = congresso_lp_metodo._diaria({"data": "2026-11-18", "provedor": "", "motivo": "sem IA: nada lido", "periodo": {"dias": 1, "desde": "2026-11-18", "primeiras": 0, "segundas": 0, "controles": [0, 0]}, "pendentes": "x"}, limites, "16/11/2026")
    assert y["numeros"] == "Desde 18/11/2026 (1 dia com rodada): 0 primeiras leituras e 0 segundas." and y["quem"] == "A última rodada foi em 18/11/2026. Nela, nenhuma IA respondeu (sem IA: nada lido)."
    assert congresso_lp_metodo._diaria({"periodo": "?", "controles": "?", "data": 5}, limites, "") == {**vazio}
    # uma segunda só, de quem já tinha lido; sem o campo das de outra rodada (registro de antes dele), nada se diz delas; nenhum lote descartado
    y = congresso_lp_metodo._diaria({"periodo": {"primeiras": 1, "segundas": 1, "mesmo_editor": 1, "lotes": 1, "descartados": 0}}, limites, "")
    assert y["numeros"] == "No registro: 1 primeira leitura e 1 segunda; a segunda foi feita por uma IA que já tinha lido a mesma proposição na rodada; nenhum lote descartado, de 1."

    def numeros(**periodo: int) -> str:
        return congresso_lp_metodo._diaria({"periodo": periodo}, limites, "")["numeros"]

    # "mesmo_editor" conta as segundas e as terceiras de quem já tinha lido: a frase diz de quais, e não "delas pelo mesmo que fez a primeira"
    assert numeros(primeiras=9, segundas=4, terceiras=1, mesmo_editor=0, de_outra_rodada=0) == "No registro: 9 primeiras leituras, 4 segundas e 1 terceira; nenhuma das segundas e terceiras foi feita por uma IA que já tinha lido a mesma proposição na rodada."
    assert numeros(primeiras=9, segundas=4, terceiras=0, mesmo_editor=2, de_outra_rodada=1) == (
        "No registro: 9 primeiras leituras e 4 segundas; das segundas, 2 foram feitas por uma IA que já tinha lido a mesma proposição na rodada; "
        "em 1 das segundas, a primeira leitura era de outra rodada e o registro não diz de que IA era (pode ter sido a mesma)."
    )
    assert numeros(primeiras=0, segundas=3, de_outra_rodada=3) == "No registro: 0 primeiras leituras e 3 segundas; em 3 das segundas, a primeira leitura era de outra rodada e o registro não diz de que IA era (pode ter sido a mesma)."
    # sem os dois campos, só as contagens; sem segundas nem terceiras, nada a dizer de quem as fez
    assert numeros(primeiras=9, segundas=4, terceiras=1) == "No registro: 9 primeiras leituras, 4 segundas e 1 terceira."
    assert numeros(primeiras=9, segundas=0, terceiras=0, mesmo_editor=0, de_outra_rodada=0) == "No registro: 9 primeiras leituras e 0 segundas."
    assert "editor" not in numeros(primeiras=9, segundas=4, terceiras=1, mesmo_editor=5, de_outra_rodada=2)
    # com uma segunda só, a frase concorda: "a segunda", não "das segundas, 1" nem "em 1 das segundas"
    assert numeros(primeiras=2, segundas=1, mesmo_editor=0, de_outra_rodada=1) == (
        "No registro: 2 primeiras leituras e 1 segunda; a segunda não foi feita por uma IA que já tinha lido a mesma proposição na rodada; "
        "na segunda, a primeira leitura era de outra rodada e o registro não diz de que IA era (pode ter sido a mesma)."
    )
    # sem os limites (config ilegível), a frase sai sem os números; desligada, diz que está desligada; sem controles, diz que não há
    assert "com perguntas de controle escondidas" in congresso_lp_metodo._diaria(None, None, "")["como"] and "30" not in congresso_lp_metodo._diaria(None, None, "")["como"]
    assert congresso_lp_metodo._diaria(None, ler.Limites(ligada=False), "16/11/2026")["como"].startswith("A leitura de todo dia está desligada no config: a proposição que chega depois de 16/11/2026 fica como “sem leitura”")
    assert "sem perguntas de controle (estão desligadas no config)" in congresso_lp_metodo._diaria(None, ler.Limites(controles=0, controles_minimo=0), "")["como"]
    # o método inteiro, sem nada em meta.json: o parágrafo está lá, sem números
    assert congresso_lp_metodo.metodo(R, regua, {}, [], None, None)["diaria"] == congresso_lp_metodo._diaria(None, None, "")


def test_a_nota_da_cobertura_so_sai_quando_ha_registros_a_mais_do_que_materias():
    cobertura = {"cd": {"total": 32838, "lidos": 31604, "voto": {"total": 381, "lidos": 342}}, "sf": {"total": 6715, "lidos": 6048, "voto": {"total": 288, "lidos": 237}}}
    casas = [("cd", "Câmara"), ("sf", "Senado")]
    camara, senado = congresso_lp_metodo._cobertura(casas, cobertura, {"cd": 379, "sf": 288})
    assert camara["voto"] == "342 de 381" and "são 381 proposições do registro para as 379 matérias" in camara["nota"] and "há 2 registros a mais" in camara["nota"]
    assert senado["voto"] == "237 de 288" and senado["nota"] == ""  # iguais
    # sem o número do voto, com número que não é número, ou com menos registros do que matérias: nenhuma nota inventada
    assert [linha["nota"] for linha in congresso_lp_metodo._cobertura(casas, cobertura)] == ["", ""]
    assert [linha["nota"] for linha in congresso_lp_metodo._cobertura(casas, cobertura, {"cd": "379", "sf": 300})] == ["", ""]


def test_a_auditoria_pela_metade_nao_derruba_a_pagina(R: congresso_lp.Rotulos, regua: regras.ProjetoDePais):
    """A auditoria é escrita por fora: chave que falta, ou de outro tipo, só some da tela."""
    meta = {"cobertura": {"cd": {"total": 0}, "sf": "x"}, "auditoria": {"leitores": "três", "controles": {"perguntas": 0}, "piloto": {"faixa": {"igual_pct": "?"}}, "segunda_leitura": {"lidas": 10, "topo": 3}, "limites": "nenhum"}}
    y = congresso_lp_metodo.metodo(R, regua, meta, [("cd", "Câmara"), ("sf", "Senado")], None, None)
    assert y["cobertura"] == [] and y["auditoria"]["quem"] == [] and y["auditoria"]["estados"] == "" and y["limites"] == list(congresso_lp_metodo.LIMITES)
    titulos = [b["titulo"] for b in y["auditoria"]["blocos"]]
    assert titulos == ["O piloto: dois leitores nas mesmas ementas", "A segunda e a terceira leituras"]
    assert y["auditoria"]["blocos"][0]["tabela"] == [{"nome": "A faixa do peso", "igual": "—", "kappa": "—", "forte": True}]
    assert y["auditoria"]["blocos"][1]["texto"] == "O leitor intermediário releu 10 proposições sem ver a primeira leitura."
    assert congresso_lp_metodo.metodo(R, regua, {}, [], None, None)["auditoria"] is None


# ── o que vale para os arquivos novos ────────────────────────────────────────


def test_nenhum_nome_de_categoria_da_regua_esta_escrito_nos_modelos_novos(regua: regras.ProjetoDePais):
    ids, rotulos = _nomes_da_regua(regua)
    for nome, texto in (("congresso_lp_voto.html.j2", VOTO_HTML), ("congresso_lp_voto.js.j2", VOTO_JS), ("congresso_lp_voto.css.j2", VOTO_CSS), ("congresso_lp_metodo.html.j2", METODO_HTML)):
        for rotulo in sorted(rotulos):
            assert not re.search(r"(?<![\wÀ-ÿ])" + re.escape(rotulo) + r"(?![\wÀ-ÿ])", texto, flags=re.IGNORECASE), f"{nome}: o rótulo “{rotulo}” está escrito"
        for ident in sorted(ids):
            assert not re.search(r"""['":=]""" + re.escape(ident) + r"""['"]""", texto), f"{nome}: o id “{ident}” está escrito"
            assert not re.search(r"cg-lp-" + re.escape(ident) + r"(?![\w-])", texto), f"{nome}: a classe cg-lp-{ident} leva o nome de uma categoria"


@pytest.mark.skipif(shutil.which("node") is None, reason="sem Node.js para rodar o JavaScript da página")
def test_a_medida_que_nao_e_desenhada_nao_herda_a_mediana_da_anterior(tmp_path: Path):
    """Em "Pessoa ou partido?", a medida que não alcança o mínimo de pessoas não ganha pontos. A frase de quem
    está em foco não pode trazer a mediana do partido da medida desenhada antes (era "saldo de contas: −100 ·
    mediana do PP: +59", com a mediana de "De que lado vota"); e o aviso diz a contagem das contas, a da barra."""
    medidas = {"a": {"nome": "A", "un": "n", "min": 0, "col": ["a"]}, "b": {"nome": "B", "un": "saldo", "min": -100, "max": 100, "zero": True, "col": ["b"]}}
    r = _faixas_no_node(
        tmp_path,
        medidas,
        """
        var Q = [], i, f = faixas();
        for (i = 0; i < 60; i++) Q.push({ k: 'sf-' + i, nome: 'Nome ' + i, pt: i < 30 ? 'PP' : 'PT', uf: 'SC', a: 10 + i, b: i < 43 ? i - 100 : null });
        r.a = f.fx.desenhar(Q, 'a', 'senador', 'senadores');
        r.frase_a = lpNaMedida(Q[0], LPMED.a);
        r.pontos_a = f.fx.ordem().length;
        r.b = f.fx.desenhar(Q, 'b', 'senador', 'senadores', 39);
        r.frase_b = lpNaMedida(Q[0], LPMED.b);
        r.pontos_b = f.fx.ordem().length;
        r.aviso_b = texto(f.caixa);
        r.b_sem_as_contas = f.fx.desenhar(Q, 'b', 'senador', 'senadores');
        r.aviso_sem_as_contas = texto(f.caixa);
        """,
    )
    assert r["a"] is True and r["pontos_a"] == 60 and r["frase_a"] == "10 · mediana do PP: 24,5"
    assert r["b"] is False and r["pontos_b"] == 0 and r["frase_b"] == "−100"  # só o número dele: nenhuma mediana de outra medida
    assert r["aviso_b"].startswith("Não dá para responder por pessoa nesta medida: só 39 senadores podem ser medidos (a régua pede ao menos 50).")
    assert r["b_sem_as_contas"] is False and "só 43 senadores podem ser medidos" in r["aviso_sem_as_contas"]  # sem o número das contas, o que a página contou


@pytest.mark.skipif(shutil.which("node") is None, reason="sem Node.js para rodar o JavaScript da página")
@pytest.mark.parametrize(
    ("largura", "projetos", "cota"),
    [
        (515, ["1", "10", "100", "2.000"], ["R$ 0", "R$ 25.000", "R$ 50.000", "R$ 75.000", "R$ 100.000"]),  # tela larga: só "1.000" encostaria em "2.000"
        (342, ["1", "10", "100", "2.000"], ["R$ 0", "R$ 50.000", "R$ 100.000"]),  # celular: os cinco rótulos em reais não cabem
    ],
)
def test_os_rotulos_da_regua_das_faixas_nao_encostam_um_no_outro(tmp_path: Path, largura: int, projetos: list[str], cota: list[str]):
    """A régua das faixas por partido escrevia "1.0002.000" (a potência de 10 colada no teto do eixo logarítmico)
    e, no celular, "R$ 75.000" por cima de "R$ 100.000". O rótulo que encostaria no vizinho não é escrito; a
    linha da marca fica no gráfico, e as escalas que cabem saem inteiras."""
    medidas = {
        "prop": {"nome": "Projetos", "un": "n", "min": 1, "log": True, "col": ["tot"], "padrao": 0},
        "ct": {"nome": "Cota", "un": "reais", "min": 0, "col": ["ct"]},
        "pres": {"nome": "Presença", "un": "%", "min": 0, "max": 100, "col": ["pres"]},
    }
    r = _faixas_no_node(
        tmp_path,
        medidas,
        """
        var Q = [], i;
        for (i = 0; i < 60; i++) Q.push({ k: 'cd-' + i, nome: 'Nome ' + i, pt: i < 30 ? 'PP' : 'PT', uf: 'SP', tot: i ? i : 1208, ct: 1000 + i * 1500, pres: 40 + i });
        function regua(medida) {
          var f = faixas(), rotulos, pista;
          f.fx.desenhar(Q, medida, 'deputado', 'deputados');
          rotulos = f.caixa.filhos[0].filhos[0].filhos;
          pista = f.caixa.filhos[1].filhos[1];
          return { marcas: rotulos.map(texto), escritos: rotulos.filter(function (s) { return !s.hidden; }).map(texto), linhas: pista.filhos.filter(function (x) { return x.cls['cg-lp-fx-g']; }).length };
        }
        r.prop = regua('prop'); r.ct = regua('ct'); r.pres = regua('pres');
        """,
        largura=largura,
    )
    assert r["prop"]["marcas"] == ["1", "10", "100", "1.000", "2.000"] and r["prop"]["escritos"] == projetos and r["prop"]["linhas"] == 5
    assert r["ct"]["marcas"] == ["R$ 0", "R$ 25.000", "R$ 50.000", "R$ 75.000", "R$ 100.000"] and r["ct"]["escritos"] == cota and r["ct"]["linhas"] == 5
    assert r["pres"]["escritos"] == ["0%", "25%", "50%", "75%", "100%"]


@pytest.mark.skipif(shutil.which("node") is None, reason="sem Node.js para rodar o JavaScript da página")
def test_o_rotulo_de_cada_marca_da_regua_diz_onde_ela_esta(tmp_path: Path):
    """Na "Mediana do peso", de 0 a 25, as marcas ficam em 6,25, 12,5 e 18,75 e saíam escritas "6", "13" e "19". E, no
    eixo logarítmico de quantos projetos, quem tem 0 é desenhado na primeira marca (não há zero em escala logarítmica):
    quando há alguém assim, a marca diz "até 1", e não "1"."""
    medidas = {
        "tip": {"nome": "Mediana do peso", "un": "peso", "min": 0, "col": ["tip"]},
        "prop": {"nome": "Projetos", "un": "n", "min": 1, "log": True, "col": ["tot"], "padrao": 0},
        "ct": {"nome": "Cota", "un": "reais", "min": 0, "col": ["ct"]},
    }
    r = _faixas_no_node(
        tmp_path,
        medidas,
        """
        var Q = [], i;
        for (i = 0; i < 60; i++) Q.push({ k: 'cd-' + i, nome: 'Nome ' + i, pt: i < 30 ? 'PP' : 'PT', uf: 'SP', tot: i, tip: i % 24, ct: 1000 + i * 1500 });
        function marcas(medida) { var f = faixas(); f.fx.desenhar(Q, medida, 'deputado', 'deputados'); return f.caixa.filhos[0].filhos[0].filhos.map(texto); }
        r.tip = marcas('tip'); r.com_zero = marcas('prop'); r.ct = marcas('ct');
        Q[0].tot = 1; r.sem_zero = marcas('prop');
        """,
    )
    assert r["tip"] == ["0", "6,25", "12,5", "18,75", "25"]
    assert r["com_zero"] == ["até 1", "10", "100"] and r["sem_zero"] == ["1", "10", "100"]
    assert r["ct"] == ["R$ 0", "R$ 25.000", "R$ 50.000", "R$ 75.000", "R$ 100.000"]  # as marcas inteiras saem como sempre


def test_sem_pontos_na_medida_o_contador_e_a_janela_do_voto_dizem_isso():
    """Em "Na hora do voto", quando a medida não é desenhada, o contador não diz que a pessoa em foco está
    "sem número" (o cartão ao lado mostra o número dela) nem a Janela diz "0 medidos"."""
    assert "else if (!temPontos) posEl.textContent = emFoco.nome + ' · sem pontos nesta medida';" in VOTO_JS
    assert VOTO_JS.index("else if (!temPontos) posEl.textContent") < VOTO_JS.index("' · fora do gráfico (sem número nesta medida)'")
    assert "temPontos ? num(faixas.ordem().length) + ' ' + UNS + ' medidos' : 'sem pontos nesta medida'" in VOTO_JS


def test_as_frases_do_cartao_do_voto_concordam_no_singular():
    """"0 vezes em 1 voto dado" e "em 1 delas" (uma das vezes), no cartão montado pelo script; o contador
    diz "1 deputado no gráfico"."""
    assert "(q.wn === 1 ? ' voto dado' : ' votos dados') + ' nas votações que entram'" in VOTO_JS
    assert "'; em ' + num(q.div_cont) + ' delas, do lado do caixa.'" in VOTO_JS and "' dela'" not in VOTO_JS
    assert "num(n) + ' ' + (n === 1 ? UM : UNS) + ' no gráfico'" in VOTO_JS


def test_o_script_das_duas_vistas_e_do_estilo_da_casa(grande: str):
    assert "innerHTML" not in VOTO_JS and "outerHTML" not in VOTO_JS and "insertAdjacentHTML" not in VOTO_JS and "document.write" not in VOTO_JS
    assert "=>" not in VOTO_JS and "`" not in VOTO_JS and not re.search(r"\b(let|const)\s", VOTO_JS)
    # cada vista regrava o endereço sem criar entrada no histórico, e não pede arquivo por conta própria (só pelo leitor comum, com memória)
    assert VOTO_JS.count("replaceState(") == 2 and "pushState(" not in VOTO_JS and "buscar(" not in VOTO_JS
    assert "lpVista('decide')" in VOTO_JS and "lpVista('cruzar')" in VOTO_JS and "lpArquivo('autores', casa" in VOTO_JS and "lpArquivo('decide', casa" in VOTO_JS
    # falha ao baixar: a frase de erro, e o que é estático fica
    assert VOTO_JS.count("Não foi possível abrir os pontos agora.") == 4 and "Não foi possível abrir os votos agora." in VOTO_JS
    for trecho in ("lpVista('decide')", "lpVista('cruzar')", "function lpFaixas(", ".cg-lp-fx-p{", ".cg-lp-xb{", ".cg-lp-met{"):
        assert trecho in grande, trecho
    # o manifesto leva as medidas (o nome, a unidade e as colunas) e como escrever a marca fiscal
    lp = json.loads(grande.split('<script type="application/json" id="cgManifesto">', 1)[1].split("</script>", 1)[0])["lp"]
    assert set(congresso_lp_voto.DO_VOTO) <= set(lp["medidas_tela"]) and lp["medidas_tela"]["vf"]["col"] == ["va", "vn", lp["cortes"]["min_votos"]]
    assert lp["fiscal"]["mais"]["n"] == "+1" and lp["fiscal"]["menos"]["n"] == "−1" and lp["outros"] == congresso_lp_voto.OUTROS
