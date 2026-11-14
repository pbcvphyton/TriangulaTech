"""A pauta (qijournal.congresso.pauta): os sinais de cada matéria aprovada, o "enquanto
isso", a direção por tema, as semanas cheias, o calendário e a leitura do bloco ``pauta``
de config/congresso.yaml. Tudo com fichas fabricadas, sem rede."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

import pytest

from qijournal.congresso import pauta, regras
from qijournal.render import congresso as pagina

HOJE = "2026-11-13"
RAIZ = Path(__file__).resolve().parents[1]

BLOCO = {
    "relampago_dias": 30,
    "sinais_vale_olhar": 4,
    "sinais_vale_olhar_senado": 3,
    "surto_vezes": 2,
    "ementa_ate": 400,
    "tema_minimo": 50,
    "calendario": [
        {"nome": "recesso de julho", "de": "07-18", "ate": "07-31"},
        {"nome": "recesso de fim de ano", "de": "12-23", "ate": "02-01"},
        {"nome": "eleições (1º turno)", "de": "2026-10-04"},
    ],
    "proprio_jogo": [
        {"id": "partidos", "nome": "partidos", "padroes": [r"\bpartidos? politicos?\b", r"\blei (?:n[o0]? ?)?9 096\b"]},
        {"id": "casas", "nome": "cargos das Casas", "padroes": [r"\bcarreira legislativa\b"]},
    ],
}


@pytest.fixture
def r() -> regras.Regras:
    return regras.de_dict({"pauta": BLOCO})


def aprovada(
    dias: int = 400,
    nota: int = 45,
    apv: str = "2026-06-16",
    ementa: str = "Altera o Código Penal.",
    temas: tuple[str, ...] = ("seguranca",),
    origem: str = "parlamentar",
    urgencia: str | None = None,
    parecer: bool = False,
    votos: tuple[str, ...] = (),
) -> dict[str, Any]:
    caminho: dict[str, Any] = {}
    if urgencia:
        caminho["urgencia"] = {"d": urgencia, "por": "Ana Lima"}
    if parecer:
        caminho["parecer_plenario"] = {"d": apv, "relator": "Davi Rocha (PX-SP)"}
    rel: dict[str, Any] = {"q": "passou", "n": nota, "apv": apv, "dias": dias}
    if caminho:
        rel["cam"] = caminho
    return {"e": ementa, "o": origem, "tm": list(temas), "da": "2025-01-01", "rel": rel, "vot": [{"t": t, "d": apv} for t in votos]}


def esperando(nota: int, da: str, temas: tuple[str, ...] = ("seguranca",), ementa: str = "Dispõe sobre a segurança nas escolas.") -> dict[str, Any]:
    return {"e": ementa, "tm": list(temas), "da": da, "rel": {"q": "espera", "n": nota}}


# ── o arquivo de escolhas ────────────────────────────────────────────────────


def test_o_bloco_pauta_do_yaml_vira_regras(r: regras.Regras):
    p = r.pauta
    assert (p.relampago_dias, p.sinais_vale_olhar, p.sinais_vale_olhar_senado, p.surto_vezes, p.ementa_ate, p.tema_minimo) == (30, 4, 3, 2.0, 400, 50)
    assert [(g.id, g.nome, len(g.padroes)) for g in p.proprio_jogo] == [("partidos", "partidos", 2), ("casas", "cargos das Casas", 1)]
    assert [(m.nome, m.de, m.ate) for m in p.calendario][2] == ("eleições (1º turno)", "2026-10-04", "")
    # sem o bloco, valem os limiares de fábrica e nenhum padrão: o sinal "próprio jogo" não existe
    vazio = regras.de_dict({}).pauta
    assert (vazio.relampago_dias, vazio.proprio_jogo, vazio.calendario) == (30, (), ())
    with pytest.raises(re.error):  # padrão inválido no arquivo: erro na leitura, não uma regra muda
        regras.de_dict({"pauta": {"proprio_jogo": [{"id": "x", "padroes": ["(aberto"]}]}})


def test_o_arquivo_de_verdade_traz_os_padroes_e_o_calendario():
    p = regras.carregar(RAIZ).pauta
    assert [g.id for g in p.proprio_jogo] == ["eleicoes", "partidos", "fundos", "mandato", "imunidade", "emendas", "cadeiras", "anistia", "casas"]
    assert {m.nome for m in p.calendario} == {"recesso de julho", "recesso de fim de ano", "eleições (1º turno)"}
    assert pauta.proprio_jogo("Altera a Lei nº 9.504, de 30 de setembro de 1997, para dispor sobre a propaganda.", p) == "eleicoes"
    assert pauta.proprio_jogo("Dispõe sobre a carreira legislativa da Câmara dos Deputados.", p) == "casas"
    assert pauta.proprio_jogo("Altera a Lei nº 9.096, de 1995 (Lei dos Partidos Políticos).", p) == "partidos"
    assert pauta.proprio_jogo("Institui o Dia Nacional do Pão de Queijo.", p) == ""
    assert pauta.proprio_jogo("Altera os limites da Floresta Nacional do Jamanxim.", p) == ""


def test_o_padrao_so_vale_no_comeco_da_ementa(r: regras.Regras):
    assert pauta.proprio_jogo("Dispõe sobre os Partidos Políticos e dá outras providências.", r.pauta) == "partidos"
    longe = "Dispõe sobre a merenda escolar. " * 14 + "Revoga artigo da lei dos partidos políticos."
    assert len(longe) > 400 and pauta.proprio_jogo(longe, r.pauta) == ""
    assert pauta.proprio_jogo(None, r.pauta) == ""


# ── os sinais de uma aprovada ────────────────────────────────────────────────


def test_cada_sinal_sai_do_registro(r: regras.Regras):
    def sinais(**campos: Any) -> list[str]:
        return pauta.sinais_da_materia(aprovada(**campos), r)

    assert sinais(votos=("merito",)) == []  # sem atalho, votação nominal do texto, nota média
    assert sinais(votos=("merito",), urgencia="2026-06-01") == ["urgencia"]
    assert sinais(votos=("merito",), urgencia="2026-06-16") == ["urgencia", "no_dia"]  # a urgência e o texto no mesmo dia
    assert sinais(votos=("merito",), parecer=True) == ["plenario"]
    assert sinais(votos=("merito",), dias=30) == ["relampago"]  # até 30 dias, inclusive
    assert sinais(votos=("merito",), dias=31) == []
    assert sinais() == ["sem_nominal"]  # nenhuma votação nominal
    assert sinais(votos=("destaque", "urgencia")) == ["sem_nominal"]  # só destaque e requerimento: o texto passou sem lista de votos
    assert sinais(votos=("merito",), ementa="Altera a Lei nº 9.096, de 1995, para tratar das contas.") == ["proprio"]
    assert sinais(votos=("merito",), nota=29) == ["fraca"]
    assert sinais(votos=("merito",), nota=30) == []
    todos = sinais(dias=0, nota=12, urgencia="2026-06-16", parecer=True, ementa="Reestrutura a carreira legislativa.")
    assert todos == list(pauta.SINAIS)


def test_quem_nao_foi_aprovada_nao_tem_sinal(r: regras.Regras):
    assert pauta.sinais_da_materia(esperando(10, "2020-01-01"), r) == []
    sem_tempo = aprovada()
    del sem_tempo["rel"]["dias"]  # sem a data do protocolo, fica fora da comparação
    assert pauta.sinais_da_materia(sem_tempo, r) == []


def test_no_senado_so_valem_os_sinais_que_o_registro_permite(r: regras.Regras):
    m = aprovada(dias=2, nota=12, urgencia="2026-06-16", parecer=True)
    assert pauta.sinais_da_materia(m, r, "sf") == ["relampago", "sem_nominal", "fraca"]
    assert pauta.sinais_da_casa("sf") == ("relampago", "sem_nominal", "proprio", "fraca")


def test_sem_registro_de_voto_so_vale_onde_os_votos_foram_lidos(r: regras.Regras):
    antiga = aprovada(apv="2023-01-20")
    assert pauta.sinais_da_materia(antiga, r, votos_desde="2023-02-01") == []
    assert pauta.sinais_da_materia(aprovada(apv="2023-02-01"), r, votos_desde="2023-02-01") == ["sem_nominal"]
    # a matéria teve votação nominal, mas ela saiu das contas (o registro não bate com o placar): não dá para dizer
    assert pauta.sinais_da_materia(aprovada(), r, voto_fora=True) == []
    mats = {"PL 9/2026": aprovada(), "PL 8/2026": aprovada()}
    pauta.anotar(mats, r, votos_fora=["PL 9/2026"])
    assert (mats["PL 9/2026"]["rel"]["sin"], mats["PL 8/2026"]["rel"]["sin"]) == ([], ["sem_nominal"])


def test_a_urgencia_sozinha_nao_conta_para_a_lista():
    assert pauta.para_a_lista(["urgencia", "plenario", "sem_nominal"]) == 2
    assert pauta.para_a_lista([]) == 0


# ── enquanto isso ────────────────────────────────────────────────────────────


def test_enquanto_isso_conta_o_que_espera_no_mesmo_tema_com_nota_maior():
    fila = {
        "PL 1/2010": esperando(60, "2010-03-01"),
        "PL 2/2015": esperando(50, "2015-03-01"),
        "PL 3/2001": esperando(20, "2001-03-01"),  # nota menor: não entra
        "PL 4/2005": esperando(90, "2005-03-01", temas=("saude", "seguranca")),  # o 1º tema é outro
    }
    assert pauta.enquanto_isso(aprovada(nota=45), fila) == {"n": 2, "t": "PL 1/2010", "da": "2010-03-01"}
    assert pauta.enquanto_isso(aprovada(nota=50), fila) == {"n": 1, "t": "PL 1/2010", "da": "2010-03-01"}  # nota igual não é maior
    assert pauta.enquanto_isso(aprovada(nota=95), fila) == {"n": 0}
    assert pauta.enquanto_isso(aprovada(temas=()), fila) is None  # sem tema: não mostra


def test_anotar_grava_os_sinais_e_o_enquanto_isso_na_ficha(r: regras.Regras):
    mats = {"PL 9/2026": aprovada(dias=3, nota=12), "PL 1/2010": esperando(60, "2010-03-01"), "PL 7/2026": {"e": "Só votada.", "rel": {"q": "votada", "n": 40}}}
    pauta.anotar(mats, r)
    assert mats["PL 9/2026"]["rel"]["sin"] == ["relampago", "sem_nominal", "fraca"]
    assert mats["PL 9/2026"]["rel"]["enq"] == {"n": 1, "t": "PL 1/2010", "da": "2010-03-01"}
    assert "sin" not in mats["PL 1/2010"]["rel"] and "sin" not in mats["PL 7/2026"]["rel"]


# ── a direção, as semanas e o calendário ─────────────────────────────────────


def test_a_direcao_ordena_pela_diferenca_entre_as_partes():
    aprovadas = [aprovada(temas=("cidades",)), aprovada(temas=("cidades",)), aprovada(temas=("saude",)), aprovada(temas=())]
    fila = [esperando(10, "2020-01-01", temas=("saude",))] * 3 + [esperando(10, "2020-01-01", temas=("cidades",))]
    assert pauta.direcao(aprovadas, fila) == [{"t": "cidades", "a": 2, "e": 1}, {"t": "saude", "a": 1, "e": 3}]
    assert pauta.direcao([], []) == []


def test_a_semana_cheia_tem_o_dobro_da_mediana():
    dias = ["2026-03-02", "2026-03-04", "2026-03-10", "2026-03-11", "2026-03-17", "2026-03-18", "2026-03-19", "2026-03-20", "2026-03-22"]
    aprovadas = [(f"PL {k}/2026", aprovada(apv=d)) for k, d in enumerate(dias, 1)]
    semanas = pauta.semanas(aprovadas, 2)
    assert [(s["d"], s["n"], s.get("s", 0)) for s in semanas] == [("2026-03-02", 2, 0), ("2026-03-09", 2, 0), ("2026-03-16", 5, 1)]  # mediana 2; 5 ≥ 4
    assert semanas[2]["m"][0] == "PL 5/2026" and pauta.semana_de("2026-03-22") == "2026-03-16"  # segunda a domingo
    assert [s.get("s", 0) for s in pauta.semanas(aprovadas, 3)] == [0, 0, 0]  # 5 < 6
    assert pauta.semanas([], 2) == []


def test_o_calendario_so_traz_as_datas_da_janela(r: regras.Regras):
    marcos = pauta.calendario(r.pauta.calendario, "2025-11-13", HOJE)
    assert marcos == [
        {"nome": "recesso de fim de ano", "de": "2025-12-23", "ate": "2026-02-01"},
        {"nome": "recesso de julho", "de": "2026-07-18", "ate": "2026-07-31"},
        {"nome": "eleições (1º turno)", "de": "2026-10-04", "ate": "2026-10-04"},
    ]
    # a janela que começa dentro do recesso de fim de ano ainda o mostra
    assert pauta.calendario(r.pauta.calendario, "2026-01-10", "2026-03-01")[0]["de"] == "2025-12-23"
    assert pauta.calendario((regras.Marco("dia que não existe", "02-30"),), "2026-01-01", "2026-12-31") == []


# ── o painel da Casa ─────────────────────────────────────────────────────────


def _casa(r: regras.Regras) -> dict[str, dict[str, Any]]:
    mats = {
        "PL 1/2026": aprovada(dias=0, nota=12, apv="2026-06-16", urgencia="2026-06-16", parecer=True, ementa="Reestrutura a carreira legislativa.", temas=("politica",)),
        "PL 2/2026": aprovada(dias=1, nota=12, apv="2026-06-17", urgencia="2026-06-17", parecer=True, temas=("seguranca",)),
        "PL 3/2025": aprovada(dias=300, nota=45, apv="2026-03-03", votos=("merito",), origem="executivo", temas=("economia",)),
        "PL 4/2025": aprovada(dias=500, nota=45, apv="2026-03-10", votos=("merito",), urgencia="2026-02-01", temas=()),
        "PL 5/2010": esperando(70, "2010-03-01", temas=("seguranca",)),
        "PL 6/2012": esperando(20, "2012-03-01", temas=("politica",), ementa="Dispõe sobre os partidos políticos."),
    }
    pauta.anotar(mats, r)
    return mats


def test_o_painel_conta_os_sinais_e_monta_a_lista_vale_olhar(r: regras.Regras):
    mats = _casa(r)
    p = pauta.painel(mats, r, HOJE)
    assert (p["n"], p["esperam"], p["desde"], p["altas"], p["mediana_dias"]) == (4, 2, "2025-11-13", 0, 150)
    assert p["conta"] == {"urgencia": 3, "plenario": 2, "relampago": 2, "no_dia": 2, "sem_nominal": 2, "proprio": 1, "fraca": 2}
    assert p["caminho"] == {"os_dois": 2, "nenhum": 1}
    # a lista: 4 sinais ou mais, sem contar a urgência; primeiro a de mais sinais, depois a mais rápida
    assert [(i["t"], i["dias"]) for i in p["vale"]] == [("PL 1/2026", 0), ("PL 2/2026", 1)] and p["vale_n"] == 2
    assert p["vale"][0]["sin"] == ["urgencia", "plenario", "relampago", "no_dia", "sem_nominal", "proprio", "fraca"]
    assert p["vale"][1]["enq"] == {"n": 1, "t": "PL 5/2010", "da": "2010-03-01"} and p["vale"][1]["cam"]["urgencia"]["por"] == "Ana Lima"
    assert p["foco"]["t"] == "PL 1/2026"  # a aprovada com mais sinais abre a aba
    assert p["proprio"] == {"n": 1, "esperam": 1, "mediana_dias": 0, "mediana_demais": 300}
    assert p["origem"] == {"parlamentar": 3, "executivo": 1} and p["sem_tema"] == {"a": 1, "e": 0}
    assert [s["d"] for s in p["semanas"]] == ["2026-03-02", "2026-03-09", "2026-06-15"] and (p["mediana_semana"], p["surto_de"]) == (1, 2)
    assert [s["d"] for s in p["semanas"] if s.get("s")] == ["2026-06-15"]
    assert p["regras"] == {"relampago_dias": 30, "vale_olhar": 4, "surto_vezes": 2.0, "tema_minimo": 50}
    assert pauta.painel({}, r, HOJE) == {}


def test_o_painel_do_senado_nao_fala_do_caminho(r: regras.Regras):
    mats = _casa(r)
    for m in mats.values():
        m["rel"].pop("sin", None)
    pauta.anotar(mats, r, "sf")
    p = pauta.painel(mats, r, HOJE, "sf")
    assert set(p["conta"]) == {"relampago", "sem_nominal", "proprio", "fraca"} and "caminho" not in p
    assert p["regras"]["vale_olhar"] == 3 and [i["t"] for i in p["vale"]] == ["PL 1/2026", "PL 2/2026"]


def test_os_pontos_do_grafico_ganham_o_tema_e_os_sinais(r: regras.Regras):
    mats = _casa(r)
    brutos = [[6000, 70, 0, "PL 5/2010"], [5000, 20, 0, "PL 6/2012"], [1, 12, 1, "PL 2/2026"], [500, 45, 1, "PL 4/2025"]]
    assert pauta.pontos(brutos, mats, r) == [
        [6000, 70, 0, "PL 5/2010", "seguranca", ""],
        [5000, 20, 0, "PL 6/2012", "politica", "proprio fraca"],  # da que espera, só o que dá para dizer antes do voto
        [1, 12, 1, "PL 2/2026", "seguranca", "urgencia plenario relampago no_dia sem_nominal fraca"],
        [500, 45, 1, "PL 4/2025", "", "urgencia"],
    ]
    assert pauta.pontos(brutos, mats, r, "sf")[2][5] == "relampago sem_nominal fraca"


def test_a_linha_de_cada_sinal_usa_os_limiares_do_arquivo(r: regras.Regras):
    outro = dataclasses.replace(r, pauta=dataclasses.replace(r.pauta, relampago_dias=15))
    assert "até 15 dias" in pauta.linha_do_sinal("relampago", outro)
    assert "abaixo de 30" in pauta.linha_do_sinal("fraca", r)
    assert pauta.linha_do_sinal("fraca", r, curta=True) == "nota abaixo de 30"
    assert set(pauta.ROTULOS) == set(pauta.LINHAS) == set(pauta.CURTAS) == set(pauta.SINAIS)


# ── a aba (qijournal.render.congresso) ───────────────────────────────────────

TEMAS = [{"id": "politica", "nome": "Política e Estado", "curto": "Política"}, {"id": "seguranca", "nome": "Segurança", "curto": "Segurança"}, {"id": "economia", "nome": "Economia", "curto": "Economia"}]


def _visao(r: regras.Regras, casa: str = "cd") -> dict[str, Any]:
    mats = _casa(r)
    if casa != "cd":
        for m in mats.values():
            m["rel"].pop("sin", None)
        pauta.anotar(mats, r, casa)
    brutos = [[6000, 70, 0, "PL 5/2010"], [5000, 20, 0, "PL 6/2012"], [0, 12, 1, "PL 1/2026"], [1, 12, 1, "PL 2/2026"], [300, 45, 1, "PL 3/2025"], [500, 45, 1, "PL 4/2025"]]
    rel = {"pontos": pauta.pontos(brutos, mats, r, casa), "fortes_paradas": 1}
    visao = pagina._pauta(pauta.painel(mats, r, HOJE, casa), rel, casa, TEMAS, 60.0, 30.0)
    assert visao is not None
    return visao


def test_a_manchete_muda_a_frase_quando_o_numero_e_zero():
    cheia = pagina._manchete(88, 110, "teve urgência", "tiveram urgência", "", "s:urgencia")
    assert (cheia["num"], cheia["de"], cheia["frase"]) == ("88", "de 110 aprovadas", "tiveram urgência")
    uma = pagina._manchete(1, 110, "teve urgência", "tiveram urgência", "", "s:urgencia")
    assert (uma["num"], uma["frase"]) == ("1", "teve urgência")
    nenhuma = pagina._manchete(0, 110, "teve urgência", "tiveram urgência", "", "s:urgencia")
    assert (nenhuma["num"], nenhuma["de"], nenhuma["frase"]) == ("Nenhuma", "das 110 aprovadas", "teve urgência")


def test_as_manchetes_da_camara_e_as_do_senado(r: regras.Regras):
    cd = _visao(r)
    assert [m["f"] for m in cd["manchetes"]] == ["s:urgencia", "s:plenario", "s:sem_nominal", "f:forte"]
    assert (cd["manchetes"][0]["num"], cd["manchetes"][0]["frase"]) == ("3", "tiveram urgência")
    forte = cd["manchetes"][-1]
    assert (forte["num"], forte["frase"]) == ("Nenhuma", "tinha sinais fortes de relevância") and forte["nota"].startswith("1 que tem espera pronta")
    assert [f["id"] for f in cd["filtros"]] == list(pauta.SINAIS) and not cd["sem_caminho"]
    # no Senado o registro não traz o caminho: as manchetes da urgência e do parecer não aparecem
    sf = _visao(r, "sf")
    assert [m["f"] for m in sf["manchetes"]] == ["s:relampago", "s:sem_nominal", "f:forte"] and sf["sem_caminho"]
    assert [f["id"] for f in sf["filtros"]] == ["relampago", "sem_nominal", "proprio", "fraca"]


def test_o_cartao_em_foco_conta_o_atalho_passo_a_passo(r: regras.Regras):
    visao = _visao(r)
    foco = visao["foco"]
    assert (foco["t"], foco["k"], foco["tempo"]) == ("PL 1/2026", "cd-PL-1-2026", "no mesmo dia")
    assert [(p["o"], p["data"], p["depois"]) for p in foco["passos"]] == [
        ("Protocolo", "01/01/2025", ""),
        ("Urgência aprovada", "16/06/2026", "17 meses depois"),
        ("Parecer em Plenário", "16/06/2026", "no mesmo dia"),
        ("Aprovada pelo Plenário", "16/06/2026", "no mesmo dia"),
    ]
    assert foco["passos"][1]["x"] == "a pedido de Ana Lima" and foco["passos"][2]["x"] == "relator: Davi Rocha (PX-SP)"
    linhas = {s["id"]: s["x"] for s in foco["sinais"]}
    assert list(linhas) == list(pauta.SINAIS)
    assert linhas["relampago"] == "do protocolo ao voto no mesmo dia" and linhas["fraca"] == "nota 12, abaixo de 30"
    assert visao["foco_xy"] == {"x": pagina._gx(0), "y": pagina._gy(12)}
    # a segunda da lista espera atrás de uma matéria de nota maior no mesmo tema
    segunda = pagina._cartao_da_pauta(pauta.painel(_casa(r), r, HOJE)["vale"][1], "cd", pauta.painel(_casa(r), r, HOJE), {"seguranca": "Segurança"}, 30.0)
    assert segunda["enq"] == {"n": 1, "tema": "Segurança", "t": "PL 5/2010", "ha": "16 anos e 11 meses"}


def test_o_relampago_que_veio_de_fora_leva_a_ressalva(r: regras.Regras):
    mats = {"PL 1/2026": aprovada(dias=2, origem="outra_casa", votos=("merito",)), "PL 2/2026": aprovada(dias=2, origem="executivo", votos=("merito",))}
    pauta.anotar(mats, r)
    p = pauta.painel(mats, r, HOJE)
    cartoes = {t: pagina._cartao_da_pauta({**pauta._cartao(t, m)}, "cd", p, {}, 30.0) for t, m in mats.items()}
    assert cartoes["PL 1/2026"]["sinais"][0]["x"] == "do protocolo ao voto em 2 dias (veio do Senado já votada lá)"
    assert cartoes["PL 2/2026"]["sinais"][0]["x"] == "do protocolo ao voto em 2 dias (veio do Executivo)"


def test_a_direcao_so_aparece_quando_o_registro_traz_os_temas(r: regras.Regras):
    visao = _visao(r)
    assert [(d["id"], d["nome"], d["a"], d["e"], d["corte"]) for d in visao["direcao"]] == [
        ("economia", "Economia", "1", "0", False), ("politica", "Política", "1", "1", True), ("seguranca", "Segurança", "1", "1", False),
    ]
    assert visao["origem"] == ["3 de parlamentar", "1 do Executivo"]
    # com menos da metade das matérias com tema, a comparação não aparece
    mats = _casa(r)
    for t in ("PL 1/2026", "PL 2/2026", "PL 3/2025"):
        mats[t]["tm"] = []
    rel = {"pontos": [], "fortes_paradas": 0}
    assert pagina._pauta(pauta.painel(mats, r, HOJE), rel, "cd", TEMAS, 60.0, 30.0)["direcao"] == []


def test_o_quando_vota_marca_a_semana_cheia_o_recesso_e_a_eleicao(r: regras.Regras):
    q = _visao(r)["quando"]
    colunas = {c["d"]: c for c in q["colunas"]}
    assert (q["colunas"][0]["d"], q["colunas"][-1]["d"], len(q["colunas"])) == ("2025-11-10", "2026-11-09", 53)
    assert (colunas["2026-06-15"]["n"], colunas["2026-06-15"]["cheia"], [m["t"] for m in colunas["2026-06-15"]["m"]]) == (2, True, ["PL 1/2026", "PL 2/2026"])
    assert colunas["2026-03-02"]["cheia"] is False and colunas["2026-01-05"]["n"] == 0
    assert colunas["2025-12-22"]["recesso"] and colunas["2025-12-22"]["recesso_ini"] and not colunas["2025-12-29"]["recesso_ini"]
    assert colunas["2026-07-13"]["recesso"] and not colunas["2026-08-03"]["recesso"]
    assert colunas["2026-09-28"]["marco"] == "eleições (1º turno), 04/10"
    assert (q["topo"][0], q["surto_de"], q["mediana"], q["vezes"]) == ({"data": "15/06/2026", "n": 2}, 2, "1", "2")
