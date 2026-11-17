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

import pytest

from qijournal.congresso import dados, pais, regras
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
    "divergentes": [{"k": "cd-1", "nome": "Fulano de Tal", "pt": "PP", "uf": "SP", "div": 27, "div_cont": 4, "vn": 58}, {"k": "cd-2", "nome": "Beltrana", "pt": "PP", "uf": "RJ", "div": 1, "div_cont": 0, "vn": 30}],
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
    meta.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return _gerar(copia)


def _metodo(html: str) -> str:
    return html.split('id="cg-lp-metodo"', 1)[1].split("</section>", 1)[0]


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
    barra = {ident: trecho for ident, trecho in re.findall(r'data-x="([a-z]+)" aria-pressed="false">(.*?)</button>', bloco)}
    # a barra, o intervalo e o traço do acaso, em largura e posição; a palavra da zona
    assert '<i style="width:75.1%"></i><u style="left:70.2%;width:9.4%"></u><s style="left:2.2%"></s>' in barra["saldo"]
    assert "<b>75%</b> diz quase tudo" in barra["saldo"] and "régua</em>" in barra["saldo"] and "510 deputados em 20 partidos · intervalo de 95%: de 70% a 80% · por acaso daria até 2%" in barra["saldo"]
    assert "<b>70%</b> diz parte" in barra["gov"] and "<b>12%</b> diz pouco: olhe a pessoa" in barra["fid"] and "régua</em>" not in barra["gov"]
    # o que encosta no acaso sai em cinza, com as palavras; o que não tem gente bastante não tem número
    assert 'class="cg-lp-xb cg-lp-xb-cz" data-x="topo"' in bloco and "<b>0%</b> não se distingue do acaso" in barra["topo"]
    assert 'class="cg-lp-xb cg-lp-xb-cz" data-x="sc"' in bloco and "não dá para responder por pessoa: só 39 podem ser medidos (a régua pede ao menos 50)" in barra["sc"] and "<b>" not in barra["sc"].split("cg-lp-xv", 1)[1]
    assert "não dá para responder por pessoa: ninguém pode ser medido (a régua pede ao menos 50)" in barra["vf"]
    assert 'class="cg-lp-xb" data-x="pres"' in bloco and "<b>7%</b> diz pouco: olhe a pessoa" in barra["pres"]
    assert "70% ou mais: o partido diz quase tudo; de 30% a 70%: diz parte; abaixo de 30%: diz pouco." in texto
    # o que anda junto: a pergunta, a barra a partir do zero e a resposta em frase; só afirma o que passa do acaso
    pares = bloco.split('<ul class="cg-lp-pares">', 1)[1].split("</ul>", 1)[0].split("<li")[1:]
    assert len(pares) == 4
    assert 'class="cg-lp-pb-p" style="width:12.5%"' in pares[0] and "<b>0,25</b> Quem propõe mais do lado do caixa dá mais voto fiscal: correlação fraca (0,25; n = 226)." in pares[0] and "dentro do mesmo partido: 0,11 (fraca)" in pares[0]
    assert 'class="cg-lp-pb-n" style="width:11.5%"' in pares[1] and "<b>−0,23</b> Quem vota mais à esquerda tem parte maior na faixa" in pares[1]
    assert pares[2].startswith(' class="cg-lp-par-cz">') and "Não se distingue do acaso (0,06; n = 513)." in pares[2]
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


def test_as_medidas_da_tela_sao_as_das_contas(R: congresso_lp.Rotulos):
    """O script tira o valor de cada pessoa das colunas que :func:`medidas` aponta: toda medida
    das contas tem de estar lá, com nome, unidade e coluna."""
    from qijournal.congresso import pais_cruzar

    M = congresso_lp_voto.medidas(R)
    assert {ident for ident, _g, _v in pais_cruzar.MEDIDAS} <= set(M) and set(congresso_lp_voto.DO_VOTO) <= set(M)
    for ident, m in M.items():
        assert m["nome"] and m["curto"] and m["un"] in ("%", "saldo", "n", "peso", "reais") and len(m["col"]) in (1, 3), ident
    linha = {"va": 6, "vn": 16, "estr": 1, "n": 9, "saldo": -25, "prop": None}
    assert congresso_lp_voto.valor(linha, M["vf"]) == pytest.approx(37.5) and congresso_lp_voto.valor(linha, M["topo"]) is None  # menos de 10 projetos lidos
    assert congresso_lp_voto.valor(linha, M["saldo"]) == -25 and congresso_lp_voto.valor(linha, M["prop"]) == 0 and congresso_lp_voto.valor(linha, M["gov"]) is None


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
    assert "embaralhando os partidos entre as pessoas 1.000 vezes; o intervalo, reamostrando as pessoas 2.000 vezes" in lido
    # o que a régua não mede, os cinco erros de leitura e os limites
    erros = bloco.split('<ol class="cg-lp-met-e">', 1)[1].split("</ol>", 1)[0]
    assert erros.count("<li>") == 5 and "<b>Peso como qualidade.</b>" in erros and "<b>“Voto fiscal” como voto certo ou como independência.</b>" in erros
    assert "o custo em reais, a constitucionalidade, o efeito real" in lido and "ementa vaga fica sem leitura; ementa franca leva as marcas" in lido


def test_o_como_ler_publica_a_cobertura_e_a_auditoria_que_vieram(grande: str, pequena: str):
    lido = _texto(_metodo(grande))
    # a cobertura, de meta.json (o Congresso de mentira tem seis proposições lidas na Câmara)
    cobertura = _metodo(grande).split('class="an-tbl cg-tb cg-lp-met-cb"', 1)[1].split("</table>", 1)[0]
    assert '<tr><th scope="row">Câmara</th><td>6</td><td><b>6</b> <small>100%</small></td><td>0</td><td>1</td><td>5 de 5</td>' in cobertura and cobertura.count("<tr>") == 2
    # quem leu e o estado das leituras
    assert "Primeira leitura. O modelo de IA mais barato leu tudo, às cegas." in lido and "Segunda leitura. Um modelo intermediário releu" in lido and "Terceira leitura. O modelo mais forte desempatou." in lido
    assert "Das 40.106 proposições lidas: 37.960 têm uma leitura só, 811 têm duas leituras na mesma faixa, 1.112 foram decididas pelo terceiro leitor e 223 ficaram instáveis" in lido
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
