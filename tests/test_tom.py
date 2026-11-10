"""Tom das manchetes (``qijournal/render/tom.py``), a mesma regra no navegador
(``tom.js.j2``), o tom por nome e por veículo (``midia.py``) e as páginas."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date, timedelta

import pytest

from qijournal.config import load_config
from qijournal.render import midia, pesquisa, tom
from qijournal.render.analitico import render_analitico_page
from qijournal.render.filters import environment

# ── classificação de uma manchete ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("titulo", "nome", "outros", "esperado"),
    [
        # palavra negativa perto do nome
        ("PF investiga circunstâncias da fuga de Flávio Bolsonaro", "Flávio Bolsonaro", ["PF"], ("neg", True, False)),
        ("Flávio Bolsonaro é investigado em três inquéritos", "Flávio Bolsonaro", [], ("neg", False, False)),
        # ação contra alguém: quem vem antes ataca, quem vem depois é o alvo
        ("Lula critica Flávio no debate", "Lula", ["Flávio"], ("neutro", False, True)),
        ("Lula critica Flávio no debate", "Flávio", ["Lula"], ("neg", True, False)),
        # a palavra vale para o nome mais próximo ("vitória de Lula"), não para todos do título
        ("Flávio não reconhece vitória de Lula", "Lula", ["Flávio"], ("pos", False, False)),
        ("Flávio não reconhece vitória de Lula", "Flávio", ["Lula"], ("neutro", False, False)),
        # empate na distância: vale o nome de depois
        ("Moraes elogia Gonet", "Gonet", ["Moraes"], ("pos", False, False)),
        # expressão de duas palavras e negação
        ("Datafolha: Lula na frente em SP", "Lula", ["Datafolha", "SP"], ("pos", False, False)),
        ("Tarcísio sem apoio para 2030", "Tarcísio", [], ("neutro", False, False)),
        ("Lula perde apoio no Nordeste", "Lula", ["Nordeste"], ("neg", False, False)),
        # positivo e negativo empatados: misto
        ("Após críticas, Tarcísio recebe apoio", "Tarcísio", [], ("misto", False, False)),
        # longe demais (mais de JANELA palavras): não conta
        ("Saiba quem ganha espaço na direita após a eleição de Flávio", "Flávio", [], ("neutro", False, False)),
        # nome ausente
        ("Copom mantém os juros", "Lula", [], ("neutro", False, False)),
    ],
)
def test_classificar(titulo, nome, outros, esperado):
    r = tom.classificar(titulo, nome, outros)
    assert (r.tom, r.alvo, r.ataca) == esperado


def test_gatilhos_saem_como_no_titulo_e_a_busca_casa_pelo_comeco():
    r = tom.classificar("Escândalo derruba aliado do Senado", "Senado", [])
    assert r.tom == "neg" and r.alvo and r.gatilhos == ["Escândalo", "derruba"]
    # "Senado" acha "senador" (a regra da busca), mas outro nome casa só inteiro
    assert tom.ocorrencias(["o", "senador", "vota"], ["senado"], busca=True) == [(1, 1)]
    assert tom.ocorrencias(["o", "senador", "vota"], ["senado"]) == []
    assert tom.ocorrencias(["pt", "e", "ptb"], ["pt"], busca=True) == [(0, 0)]  # até 3 letras: inteira


def test_dicionario_vai_para_a_pagina_sem_perder_nada():
    d = tom.dicionario()
    assert d["janela"] == tom.JANELA and "nao" in d["negadores"]
    assert "volta atras" in d["neg"]["p"] and "escandalo" in d["neg"]["c"] and "critica" in d["contra"]["i"]
    assert set(d) == {"janela", "negadores", "neg", "pos", "contra"}


CASOS_JS = [
    ("PF investiga circunstâncias da fuga de Flávio Bolsonaro do Brasil", "Flávio Bolsonaro", ["PF", "Brasil"]),
    ("Lula critica 'tesouraço' de Flávio Bolsonaro e exibe depoimento", "Lula", ["Flávio Bolsonaro"]),
    ("Lula critica 'tesouraço' de Flávio Bolsonaro e exibe depoimento", "Flávio", ["Lula", "Flávio Bolsonaro"]),
    ("TSE suspende propaganda de Lula que chama chapa de Flávio de 'dupla de escândalos'", "Lula", ["TSE", "Flávio"]),
    ("Aprovação de Trump cai a 31%, menor nível", "Trump", []),
    ("Datafolha: Lula na frente e Flávio volta atrás", "Flávio", ["Datafolha", "Lula"]),
    ("Pró-Lula, ato reúne apoio de artistas", "pro lula", []),
    ("Senadores rejeitam indicação; senador critica governo", "Senado", []),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="sem Node.js para rodar o JavaScript da página")
def test_javascript_da_pagina_classifica_igual_ao_python(tmp_path):
    js = environment().get_template("tom.js.j2").render()
    casos = [{"t": t, "n": n, "o": o} for t, n, o in CASOS_JS]
    script = tmp_path / "tom.js"
    script.write_text(
        js
        + f"\nvar T = tomCriar({json.dumps(tom.dicionario())});\n"
        + f"var C = {json.dumps(casos, ensure_ascii=False)};\n"
        + "console.log(JSON.stringify(C.map(function (c) { var r = T.classificar(c.t, c.n, c.o);"
        + " return [r.tom, r.neg, r.pos, r.alvo, r.ataca, r.gatilhos]; })));\n",
        encoding="utf-8",
    )
    saida = subprocess.run(["node", str(script)], capture_output=True, text=True, check=True, timeout=60)
    esperado = []
    for t, n, o in CASOS_JS:
        r = tom.classificar(t, n, o)
        esperado.append([r.tom, r.neg, r.pos, r.alvo, r.ataca, r.gatilhos])
    assert json.loads(saida.stdout) == esperado


# ── comparação entre veículos ────────────────────────────────────────────────


def test_comparar_usa_a_variancia_das_duas_juntas():
    todo_neutro, misturado = [0] * 30, [-1] * 3 + [0] * 57
    z = midia.comparar(todo_neutro, misturado)
    assert z is not None and abs(z) < midia.Z_VEICULO  # um veículo todo neutro não vira "mais favorável"
    assert midia.comparar([0, 0], [0, 0]) is None  # sem variação
    assert midia.comparar([-1] * 30 + [0] * 10, [0] * 200 + [1] * 20) <= -midia.Z_VEICULO


def _dias(n: int, fim: str = "2026-10-30") -> list[str]:
    ultimo = date.fromisoformat(fim)
    return [(ultimo - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def _dia(dia: str, manchetes: list[tuple[str, str]]) -> dict:
    veiculos: list[str] = []

    def v(nome: str) -> int:
        if nome not in veiculos:
            veiculos.append(nome)
        return veiculos.index(nome)

    noticias = [[0, -1, v(o), t, f"x.com/{dia}/{i}", 0] for i, (o, t) in enumerate(manchetes)]
    return {
        "v": pesquisa.VERSAO, "data": dia, "secoes": [["politica", "Política", "#000"]], "veiculos": veiculos,
        "noticias": noticias, "comparadas": [], "assuntos": [],
    }


def _destaques_com_tom() -> midia.DestaquesView:
    # A Folha põe Fulano em contexto desfavorável todo dia; os demais, em contexto neutro.
    arquivos = []
    for i, d in enumerate(_dias(21)):
        manchetes = [("Folha", f"Escândalo atinge Fulano de Tal {i}")]
        manchetes += [(f"Veículo {k}", f"Fulano de Tal visita a cidade {i}-{k}") for k in range(7)]
        manchetes += [("g1", f"Hoje Beltrano Silva fala {i}")]
        arquivos.append(_dia(d, manchetes))
    view = midia.destaques([midia.corpus_do_dia(a) for a in arquivos], fim=_dias(21)[-1])
    assert view is not None
    return view


def test_tom_do_nome_aponta_o_veiculo_mais_critico_e_o_padrao():
    view = _destaques_com_tom()
    fulano = next(n for n in view.nomes if n.nome == "Fulano de Tal")
    t = fulano.tom
    assert t is not None and t.manchetes == 21 * 8
    assert t.desfavoraveis == "12%" and t.favoraveis == "0,0%" and t.saldo == "−12" and t.classe == "neg"
    folha = next(v for v in t.veiculos if v.nome == "Folha")
    assert folha.destaque == "mais crítico" and folha.classe == "neg" and folha.saldo == "−100" and folha.demais == "0"
    assert folha.padrao == "mais negativo que os demais em 3 de 3 semanas" and folha.presenca == "21 de 21"
    assert all(not v.destaque for v in t.veiculos if v.nome != "Folha")
    assert any(f.startswith("Mais crítico: Folha (saldo −100, contra 0 nos demais veículos)") for f in t.leitura)
    assert t.exemplos and t.exemplos[0].tipo == "neg" and t.exemplos[0].href.startswith("https://x.com/")
    beltrano = next(n for n in view.nomes if n.nome == "Beltrano Silva")
    assert beltrano.tom is not None and beltrano.tom.saldo == "0"  # 21 manchetes neutras


def test_analitico_mostra_o_tom_e_quem_fala_de_quem():
    config = load_config(env={})
    html = render_analitico_page([], config, home_href="../", archive_href="./", destaques=_destaques_com_tom())
    assert 'id="an-quem"' in html and "quem fala de quem, e como" in html
    assert '<span class="qf-tag qf-neg">mais crítico</span>' in html and "mais negativo que os demais em 3 de 3 semanas" in html
    # o placar diz quem destoa, a matriz contorna a célula e as duas levam ao dossiê do nome
    assert '<span class="az-dq neg"><b>Folha</b> mais crítico</span>' in html and 'class="az-c az-n3 az-on"' in html
    assert 'href="#qf-1"' in html and ".tom-bar" in html


def test_pesquisa_recebe_o_dicionario_e_os_limites_do_tom():
    config = load_config(env={})
    html = pesquisa.render_pesquisa_page([], config, home_href="../", archive_href="./")
    manifesto = json.loads(re.search(r'id="pqDados">(.*?)</script>', html, re.S).group(1))
    assert manifesto["tom"] == json.loads(json.dumps(tom.dicionario()))
    assert manifesto["tomP"]["min"] == midia.MIN_TOM and manifesto["tomP"]["z"] == midia.Z_VEICULO
    assert "function tomCriar(L)" in html and "function blocoTom(tm)" in html
