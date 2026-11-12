"""A leitura da busca (``pesquisa_achados.js.j2``): a manchete, a prova, a linha fina e os
achados que a Pesquisa analítica escreve a partir dos fatos de uma busca, rodados no Node.
Os fatos imitam buscas reais do arquivo (Lula, Selic, um termo estrangeiro, uma busca curta);
os testes estatísticos que decidem o que "passa" ficam em ``test_pesquisa_estat.py``."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from typing import Any

import pytest

from qijournal.render import achados, lados
from qijournal.render.filters import environment

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="sem Node.js para rodar o JavaScript da página")

P = {
    "alfaTom": achados.ALFA_TOM,
    "alfaEsp": achados.ALFA_ESPACO,
    "alfaVei": achados.ALFA_VEICULO,
    "alfaLados": lados.ALFA,
    "minPar": lados.MIN_PAR,
    "minDestoa": lados.MIN_DESTOA,
    "achados": achados.ACHADOS,
}

# Palavras que a leitura não usa: superlativos sem teste e o "padrão semanal" que saiu.
PROIBIDAS = ("Mais crítico:", "quem mais dá", "embora devessem", "padrão", "puxado por", "Quem mais fala")


def _leituras(tmp_path, casos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    env = environment()
    js = env.get_template("pesquisa_estat.js.j2").render() + "\n" + env.get_template("pesquisa_achados.js.j2").render()
    script = tmp_path / "achados.js"
    script.write_text(
        "(function () {\n'use strict';\n" + js + f"\nvar ACH = achadosCriar({json.dumps(P)}, estatCriar());\n"
        f"console.log(JSON.stringify({json.dumps(casos)}.map(function (F) {{ return ACH.leitura(F); }})));\n}})();\n",
        encoding="utf-8",
    )
    saida = subprocess.run(["node", str(script)], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(saida.stdout)


def _lula() -> dict[str, Any]:
    """Os fatos da busca "Lula" nas 43 edições (os números da página publicada)."""
    return {
        "q": {"rot": "“Lula”", "excl": [], "umTermo": True, "T": "Lula"},
        "f": {
            "ed": "", "edTit": "", "v": "", "tit": False, "tipos": {"m": True, "n": True, "c": True, "a": True},
            "parcial": False, "tiposTxt": "", "D": 43, "de": "2026-09-29", "ate": "2026-11-10",
        },
        "res": 3485, "tipos": {"m": 142, "n": 3153, "c": 111, "a": 79}, "diasRes": 43,
        "editorias": [{"s": "politica", "t": "Política", "k": 2892}, {"s": "economia", "t": "Brasil · Economia", "k": 180}],
        "B": 49590, "n": 3153, "nBr": 3070, "nveic": 38, "diasCom": 43,
        "jan": {"r0": "2026-11-04", "r1": "2026-11-10", "a0": "2026-10-28", "a1": "2026-11-03"}, "antes": 2530,
        "tend": {"ka": 321, "ba": 8116, "kr": 302, "br": 7667, "v": -0.004, "z": -0.05, "p": 0.96, "passa": False},
        "picos": {"m": 42, "dias": [
            {"d": "2026-10-26", "k": 287, "b": 1108, "p0": 0.059, "R": 4.4, "p": 1e-80, "passa": True},
            {"d": "2026-10-27", "k": 250, "b": 1389, "p0": 0.06, "R": 3.0, "p": 1e-40, "passa": True},
            {"d": "2026-10-01", "k": 40, "b": 1200, "p0": 0.064, "R": 0.5, "p": 1.0, "passa": False},
        ]},
        "ext": {"ke": 83, "be": 13897, "kb": 3070, "bb": 35693, "nE": 13, "z": -20.0, "p": 1e-80, "passa": True},
        "mAt": 24, "mVei": 15,
        "hist": [
            {"titulo": "Lula vence Flávio Bolsonaro em virada histórica e conquista quarto mandato", "nv": 11, "n": 19,
             "d0": "2026-10-26", "d1": "2026-10-27", "mat": True, "comp": True},
            {"titulo": "Lula e Flávio Bolsonaro trocam acusações em debate na Band", "nv": 10, "n": 59,
             "d0": "2026-10-19", "d1": "2026-10-19", "mat": True, "comp": True},
            {"titulo": "Quaest aponta empate técnico", "nv": 10, "n": 34, "d0": "2026-10-14", "d1": "2026-10-15", "mat": True, "comp": True},
        ],
        "pauta": {"grupos": [
            {"f": "Flávio", "formas": ["Flávio", "Flávio Bolsonaro"], "co": 687},
            {"f": "TSE", "formas": ["TSE"], "co": 195},
            {"f": "Datafolha", "formas": ["Datafolha"], "co": 109},
        ], "temas": [{"f": "turno", "n": 350}, {"f": "campanha", "n": 264}, {"f": "debate", "n": 217}]},
        "lados": {"c": 63, "d": 10, "itens": [], "pares": [], "destoa": [], "mP": 0, "mD": 0,
                  "mais": {"titulo": "Campanhas de Lula e Flávio fecham pacto contra excesso de ações no TSE", "a": 4, "b": 3}},
        "texto": {"nA": 71, "nT": 71, "eq": 71, "esq": 10, "dir": 9, "Q": 84, "p": 1.0, "passa": False},
        "az": None,
        "veic": [
            {"o": "Folha de S.Paulo", "idx": 1, "k": 782, "b": 5197, "esp": 676, "i": 1.16, "pAt": 1e-5, "atForte": True,
             "sv": 3, "so": 6, "z": -1.2, "tomForte": False, "tomIsolado": ""},
            {"o": "g1", "idx": 3, "k": 558, "b": 3300, "esp": 500, "i": 1.1, "pAt": 0.02, "atForte": False,
             "sv": 10, "so": 4, "z": 2.99, "tomForte": False, "tomIsolado": "pos"},
            {"o": "Estadão", "idx": 4, "k": 308, "b": 2800, "esp": 260, "i": 1.18, "pAt": 0.01, "atForte": False,
             "sv": -3, "so": 6, "z": -3.49, "tomForte": True, "tomIsolado": "neg"},
            {"o": "Agência Senado", "idx": 0, "k": 9, "b": 387, "esp": 62, "i": 0.145, "pAt": 1e-17, "atForte": True,
             "sv": None, "so": None, "z": None, "tomForte": False, "tomIsolado": ""},
        ],
        "tom": {"aplica": True, "motivo": "", "n": 3070, "comTom": 559, "saldo": 5, "desf": 197, "fav": 362,
                "tend": {"sa": 11, "sr": -4, "na": 312, "nr": 300, "z": -4.36, "p": 1.3e-5, "passa": True}},
    }


def _com(base: dict[str, Any], **mudancas: Any) -> dict[str, Any]:
    F = copy.deepcopy(base)
    for chave, valor in mudancas.items():
        alvo, partes = F, chave.split("__")
        for p in partes[:-1]:
            alvo = alvo[p]
        alvo[partes[-1]] = valor
    return F


def _sem_testes(F: dict[str, Any]) -> dict[str, Any]:
    """Nada do que se testa passa: só sobram histórias, pauta, lados (contagem) e presença."""
    F = copy.deepcopy(F)
    for x in F["picos"]["dias"]:
        x["passa"] = False
    F["tom"]["tend"]["passa"] = False
    F["ext"]["passa"] = False
    for v in F["veic"]:
        v["tomForte"] = v["atForte"] = False
    return F


def test_lula_a_manchete_e_o_tom_e_os_achados_vao_do_maior_peso_ao_menor(tmp_path):
    (L,) = _leituras(tmp_path, [_lula()])
    assert L["chapeu"] == "A leitura · “Lula” · 43 edições, de 29/09 a 10/11/2026"
    assert L["principal"]["titulo"] == "O tom das manchetes sobre Lula piorou: saldo de +11 para −4 em sete edições"
    assert L["principal"]["texto"].endswith("A diferença passa do acaso.") and "(28/10 a 03/11)" in L["principal"]["texto"]
    assert [a["tipo"] for a in L["achados"]] == ["pico", "historia", "veiculos", "atencao", "lados", "exterior"]
    assert [a["peso"] for a in L["achados"]] == sorted((a["peso"] for a in L["achados"]), reverse=True)
    pico, historia, veiculos, atencao = L["achados"][:4]
    # o pico leva a história do dia; a história vira a seguinte (o debate da Band)
    assert pico["titulo"] == "Pico em 26/10: Lula em 26% das manchetes do dia, 4,4 vezes a parte dos outros dias"
    assert "A história do dia: “Lula vence Flávio Bolsonaro" in pico["texto"] and "outro dia também: 27/10" in pico["texto"]
    assert historia["titulo"].startswith("Lula e Flávio Bolsonaro trocam acusações") and historia["href"] == "#pq-h-2"
    # só o veículo que passa contando os 15 comparados; o g1 (só na comparação isolada) fica de fora
    assert veiculos["titulo"] == "Estadão tem manchetes mais críticas a Lula que os demais veículos" and "g1" not in veiculos["texto"]
    assert veiculos["texto"].endswith("Passa do acaso, mesmo contando os 15 veículos comparados.") and veiculos["href"] == "#pq-v-4"
    # a Folha passa do acaso na atenção, mas 1,16 vez o esperado é pouco para virar achado
    assert atencao["titulo"] == "Agência Senado quase não cita Lula: 9 manchetes, onde o que publica faria esperar umas 62"
    assert atencao["href"] == "#pq-b-atencao"  # sem lugar no placar: a prova é a tabela completa da atenção (a página abre o bloco)
    assert L["fina"] == (
        "Lula está em 3.153 manchetes de 38 veículos: 6,4% das 49.590 coletadas em 43 edições. No texto, as 71 notícias "
        "lidas pelo termômetro ficam todas em “Equilibrado”; apuração média 84 de 100."
    )
    assert [(m["nome"], m["selo"], m["forte"], m["classe"]) for m in L["mudancas"]] == [("Espaço", "estável", False, ""), ("Tom", "piorou", True, "neg")]
    assert L["fortes"] == 1 and [x["ordem"] for x in L["lentes"]] == [1, 2, 3]
    assert not L["curta"] and not L["nada"]


def test_selic_sem_tom_o_pico_vira_manchete_com_a_historia_do_dia(tmp_path):
    F = _com(
        _lula(), q={"rot": "“Selic”", "excl": [], "umTermo": True, "T": "Selic"}, res=86, n=55, nBr=55, nveic=13, diasCom=20,
        tipos={"m": 20, "n": 55, "c": 9, "a": 2}, veic=[],
        tom={"aplica": False, "motivo": "só 7 das 55 manchetes brasileiras têm palavra de tom (o mínimo é 20)", "n": 55, "comTom": 7,
             "saldo": None, "desf": 0, "fav": 0, "tend": None},
        tend={"ka": 16, "ba": 8116, "kr": 27, "br": 7667, "v": 0.79, "z": 1.87, "p": 0.06, "passa": False},
        picos={"m": 42, "dias": [{"d": "2026-11-05", "k": 11, "b": 1622, "p0": 0.0009, "R": 7.4, "p": 1e-9, "passa": True}]},
        hist=[{"titulo": "Banco Central mantém Selic em 13,75%", "nv": 3, "n": 6, "d0": "2026-11-05", "d1": "2026-11-05", "mat": True, "comp": False},
              {"titulo": "Copom inicia reunião", "nv": 3, "n": 4, "d0": "2026-11-03", "d1": "2026-11-03", "mat": True, "comp": True}],
        pauta={"grupos": [{"f": "Copom", "formas": ["Copom"], "co": 14}, {"f": "BC", "formas": ["BC"], "co": 5}], "temas": [{"f": "juros", "n": 9}]},
        lados={"c": 3, "d": 1, "itens": [], "pares": [], "destoa": [], "mP": 0, "mD": 0, "mais": {"titulo": "x", "a": 1, "b": 1}},
        texto={"nA": 2, "nT": 2, "eq": 2, "esq": 0, "dir": 0, "Q": 80, "p": 1.0, "passa": False},
        ext={"ke": 0, "be": 13897, "kb": 55, "bb": 35693, "nE": 13, "z": None, "p": 1.0, "passa": False}, mVei=0, mAt=4,
    )
    (L,) = _leituras(tmp_path, [F])
    assert L["principal"]["tipo"] == "pico"
    assert L["principal"]["titulo"] == "Pico em 05/11: Selic em 0,68% das manchetes do dia, 7,4 vezes a parte dos outros dias"
    assert "contra 0,09% nos demais" in L["principal"]["texto"] and "A história do dia: “Banco Central mantém Selic em 13,75%”" in L["principal"]["texto"]
    assert [a["tipo"] for a in L["achados"]] == ["historia", "pauta"]  # o racha de 1 assunto não vira achado
    assert L["achados"][0]["titulo"] == "Copom inicia reunião"  # a do dia foi para a prova do pico
    assert L["achados"][1]["titulo"] == "Selic aparece ao lado de Copom em 14 das 55 manchetes brasileiras"
    assert L["fina"] == "Selic está em 55 manchetes de 13 veículos: 0,11% das 49.590 coletadas em 43 edições. Há também 20 matérias do Triangula sobre o tema."
    assert [(m["nome"], m["selo"], m["forte"]) for m in L["mudancas"]] == [("Espaço", "▲ 79%", False), ("Tom", "não se aplica", False)]
    assert [x["chave"] for x in L["lentes"]] == ["manchetes", "enquadramento"]  # 2 análises: a lente do texto some


def test_nada_passa_do_acaso_e_a_prova_diz_so_o_que_foi_comparado(tmp_path):
    curto = _com(_sem_testes(_lula()), jan=None, tend=None, f__D=5, tom__tend=None, picos=None)
    L, C = _leituras(tmp_path, [_sem_testes(_lula()), curto])
    assert L["nada"] and L["principal"]["tipo"] == "historia"  # a história de maior peso, contagem
    assert L["principal"]["texto"].endswith(
        "Nas manchetes, nada passa do acaso: nem um dia de pico, nem a mudança em sete edições, nem a do tom, "
        "nem o espaço de algum veículo contra o esperado, nem o tom de algum veículo contra os demais."
    )
    assert {a["tipo"] for a in L["achados"]} <= {"historia", "pauta", "lados"}
    assert [(m["selo"], m["forte"]) for m in L["mudancas"]] == [("estável", False), ("estável", False)] and L["fortes"] == 0
    # 5 edições: sem picos nem sete contra sete, a prova não fala do que não foi comparado
    assert C["principal"]["texto"].endswith(
        "Nas manchetes, nada passa do acaso: nem o espaço de algum veículo contra o esperado, nem o tom de algum veículo contra os demais."
    )
    # uma atenção que passa do acaso, mesmo sem virar achado (efeito pequeno), já impede o "nada passa"
    com_folha = _sem_testes(_lula())
    com_folha["veic"][0]["atForte"] = True
    (D,) = _leituras(tmp_path, [com_folha])
    assert not D["nada"] and "nada passa" not in D["principal"]["texto"]
    assert C["mudancas"] is None and C["mud0"] == "Com 5 edições no período, não há sete contra sete para comparar."


def test_ate_dez_resultados_a_pagina_so_descreve(tmp_path):
    F = _com(_lula(), q={"rot": "“Lewandowski”", "excl": [], "umTermo": True, "T": "Lewandowski"}, res=6, diasRes=4,
             editorias=[{"s": "esporte", "t": "Esporte", "k": 5}, {"s": "mundo", "t": "Geopolítica & Mundo", "k": 1}])
    (L,) = _leituras(tmp_path, [F])
    assert L["curta"] and L["espalha"] and L["achados"] == [] and L["lentes"] == []
    assert L["chapeu"] == "A busca · “Lewandowski” · 43 edições, de 29/09 a 10/11/2026"
    assert L["principal"]["titulo"] == "6 resultados para Lewandowski em 4 edições: 5 de Esporte e 1 de Geopolítica & Mundo"
    assert L["fina"].startswith("Com tão poucos resultados, a página não lê espaço, tom nem lados")


def test_termo_estrangeiro_e_termo_sem_manchete(tmp_path):
    fora = _com(_lula(), q={"rot": "“Ukraine”", "excl": [], "umTermo": True, "T": "Ukraine"}, res=241, n=241, nBr=0, nveic=10, veic=[],
                tom={"aplica": False, "motivo": "nenhuma manchete brasileira cita o termo", "n": 0, "comTom": 0, "saldo": None, "desf": 0, "fav": 0, "tend": None})
    sem = _com(_lula(), q={"rot": "“reforma do STF”", "excl": [], "umTermo": True, "T": "reforma do STF"}, res=30, n=0, nBr=0, nveic=0,
               tipos={"m": 20, "n": 0, "c": 10, "a": 0}, veic=[], tom=None, picos=None, tend=None, jan=None, hist=[], pauta=None,
               lados={"c": 10, "d": 1, "itens": [], "pares": [], "destoa": [], "mP": 0, "mD": 0, "mais": None}, texto=None)
    A, B = _leituras(tmp_path, [fora, sem])
    assert A["principal"]["titulo"] == "Ukraine só aparece em veículos do exterior: 241 manchetes de 10 veículos estrangeiros, nenhuma brasileira"
    assert A["fina"] == "" and not {"veiculos", "atencao", "exterior", "tom"} & {a["tipo"] for a in A["achados"]}
    assert B["principal"]["titulo"] == "Reforma do STF não está em nenhuma manchete coletada; aparece em 20 matérias e 10 assuntos da cobertura comparada"
    assert [x["chave"] for x in B["lentes"]] == ["enquadramento"] and B["mudancas"] is None


def test_filtro_de_veiculo_nao_compara_veiculos(tmp_path):
    F = _com(_lula(), f__v="Folha de S.Paulo", n=782, nBr=782, nveic=1, B=5197)
    (L,) = _leituras(tmp_path, [F])
    tipos = {a["tipo"] for a in L["achados"]} | {L["principal"]["tipo"]}
    assert not tipos & {"veiculos", "atencao", "exterior"}
    assert L["chapeu"].endswith("· só Folha de S.Paulo")
    assert L["fina"].startswith("Lula está em 782 das 5.197 manchetes coletadas do veículo em 43 edições (15%).")
    lados_ = next(a for a in L["achados"] if a["tipo"] == "lados")
    assert "par" not in lados_["texto"]  # com o filtro, os pares não são comparados (e a frase não diz que foram)


def test_chegando_do_analitico_valem_os_vereditos_de_la(tmp_path):
    F = _com(_lula(), f__ed="politica", f__edTit="Política", f__D=30, f__de="2026-10-12",
             az={"i": 1, "tom": True, "esp": False, "vei": [], "mTom": 8, "mEsp": 12, "mVei": 79})
    for v in F["veic"]:
        v["tomForte"] = False  # o Analítico não confirma nenhum veículo para Lula
    (L,) = _leituras(tmp_path, [F])
    assert L["chapeu"] == "A leitura · “Lula” · 30 edições, de 12/10 a 10/11/2026 · Política · mesma base do Analítico"
    assert L["principal"]["titulo"] == "O tom das manchetes de Política sobre Lula piorou: saldo de +11 para −4 em sete edições"
    assert L["principal"]["texto"].endswith("Passa do acaso, mesmo contando os 8 nomes comparados no Analítico.")
    assert "veiculos" not in {a["tipo"] for a in L["achados"]}


def test_textos_sem_superlativo_sem_teste(tmp_path):
    casos = [_lula(), _sem_testes(_lula()), _com(_lula(), f__v="g1")]
    for L in _leituras(tmp_path, casos):
        textos = [L["principal"]["titulo"], L["principal"]["texto"], L["fina"]] + [a["titulo"] + " " + a["texto"] for a in L["achados"]]
        for t in textos:
            assert not any(p in t for p in PROIBIDAS), t
            assert "  " not in t and " ." not in t and "undefined" not in t and "NaN" not in t, t


def test_sem_as_noticias_a_leitura_nao_afirma_ausencia(tmp_path):
    F = _com(_lula(), n=0, nBr=0, f__tipos={"m": True, "n": False, "c": False, "a": True}, f__parcial=True,
             f__tiposTxt="só matérias e termômetro", tipos={"m": 142, "n": 0, "c": 0, "a": 79}, res=221, lados=None, veic=[], tom=None)
    (L,) = _leituras(tmp_path, [F])
    assert L["principal"]["titulo"] == "Lula em 142 matérias e 79 análises do Termômetro" and L["semNoticias"]
    assert "não está" not in L["principal"]["titulo"] and "filtrada para matérias e termômetro" in L["principal"]["texto"]


def test_concordancia_vez_e_o_que_volta(tmp_path):
    dois = _com(_lula(), q={"rot": "“Lula” e “Haddad”", "excl": [], "umTermo": False, "T": "Lula e Haddad", "plural": True},
                tom={"aplica": False, "motivo": "a busca tem mais de um termo", "n": 300, "comTom": 0, "saldo": None, "desf": 0, "fav": 0, "tend": None},
                tend={"ka": 0, "ba": 8000, "kr": 40, "br": 7600, "v": None, "z": 6.0, "p": 1e-9, "passa": True}, antes=0)
    volta = _com(dois, antes=12)
    pouco = _com(_lula(), n=18, nBr=18)  # abaixo de 20 manchetes, a atenção não vira achado (a lente não compara)
    A, B, C = _leituras(tmp_path, [dois, volta, pouco])
    assert A["fina"].startswith("Lula e Haddad estão em 3.153 manchetes")
    espaco = next(a for a in A["achados"] + [A["principal"]] if a["tipo"] == "espaco")
    assert espaco["titulo"] == "Lula e Haddad entram nas manchetes: 40 manchetes nas últimas 7 edições, nenhuma nas 7 anteriores"
    assert next(a for a in B["achados"] + [B["principal"]] if a["tipo"] == "espaco")["titulo"].startswith("Lula e Haddad voltam às manchetes")
    assert [m["selo"] for m in A["mudancas"]] == ["entra", "não se aplica"] and A["comparadas"] == 1
    assert "atencao" not in {a["tipo"] for a in C["achados"]}
    pico = next(a for a in _leituras(tmp_path, [_com(_lula(), picos__dias=[
        {"d": "2026-10-26", "k": 30, "b": 1000, "p0": 0.018, "R": 1.7, "p": 1e-9, "passa": True}])])[0]["achados"] if a["tipo"] == "pico")
    assert pico["titulo"].endswith("1,7 vez a parte dos outros dias")
