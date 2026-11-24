"""A leitura do dia da aba Tribunais: tipos, peso, frase com número e janela, prova e alvo; um achado
por ministro; comparação só do que passou no teste e de ministro comparável."""

from __future__ import annotations

from qijournal.tribunais import achados, contas
from qijournal.tribunais.modelo import carregar_regras
from tests.test_tribunais_contas import DATA, HOJE, T4, turma_sintetica

REGRAS = carregar_regras()


def _ministro(mid: str, **extra: object) -> dict:
    m = REGRAS.composicao[mid[:3]].ministro(mid)
    assert m is not None
    base = {"id": mid, "nome": m.nome, "genero": m.genero, "grupo": REGRAS.composicao[mid[:3]].grupo_de(m), "turma": m.turma,
            "convocado": None, "comparavel": True, "processos": 9809}
    base.update(extra)
    return base


def _painel(trib: str, ministros: list[dict]) -> dict:
    return {"trib": trib, "data_dados": DATA if trib == "stj" else "2026-11-22", "ministros": ministros, "partes": {"nomes": {}}}


def _sinais(**extra: object) -> dict:
    base = {"marcas": [], "mesa": [], "heranca": {}, "entradas_dia": {}, "total_dia": {}, "vistas": {}, "temas_recentes": [],
            "temas_quentes": [], "litigantes_semana": [], "hoje": HOJE}
    base.update(extra)
    return base


NORONHA = {"ministro": "stj-joao-otavio-de-noronha", "area": "saude", "k": 851, "n": 9809, "p": 0.0868, "pares": 0.0567,
           "pares_k": 1500, "pares_n": 26450, "razao": 1.53, "p_valor": 1e-33, "acaso": True, "efeito": True, "marca": 1}


def test_formatacao_pt_br() -> None:
    assert achados.numero(1014) == "1.014" and achados.numero(352239) == "352.239"
    assert achados.pct(0.0868) == "8,7%" and achados.pct(0.3384, 0) == "34%"
    assert achados.vezes(1.53) == "1,5 vez" and achados.vezes(2.38) == "2,4 vezes"
    assert achados.data_br("2026-11-19") == "19/11/2026" and achados.data_br("2026-11-19", ano=False) == "19/11"
    assert achados.peso(REGRAS, "heranca", 1014) == 15.03 and achados.peso(REGRAS, "fora_do_padrao", 851) == 8.79


def test_fora_do_padrao_frase_e_prova() -> None:
    painel = _painel("stj", [_ministro(m) for m in T4])
    out = achados.montar(REGRAS, {"stj": painel}, {"stj": _sinais(marcas=[NORONHA])}, hoje=HOJE)
    [a] = out["achados"]
    assert a["titulo"] == "Saúde é 8,7% do acervo de João Otávio de Noronha, 1,5 vez a parte dos colegas da Quarta Turma"
    assert a["texto"].startswith("851 de 9.809 processos em 20/11/2026; nos outros três gabinetes da Quarta Turma, 5,7%.")
    assert "binomial exata, 99%, Holm" in a["texto"]
    assert (a["tipo"], a["selo"], a["peso"], a["trib"]) == ("fora_do_padrao", "ÁREA", 8.79, "stj")
    assert a["alvo"] == "#v=ministros&t=stj&min=stj-joao-otavio-de-noronha&area=saude"
    assert a["prova"] == {"fonte": "stj_acervo", "data": DATA, "url": "https://dadosabertos.web.stj.jus.br/dataset/acervo-em-tramitacao"}
    assert out["v"] == 1 and out["hoje"] == HOJE


def test_so_o_que_passou_no_teste_e_de_quem_e_comparavel() -> None:
    sem_piso = {**NORONHA, "efeito": False, "marca": 0}
    painel = _painel("stj", [_ministro(m) for m in T4])
    assert achados.montar(REGRAS, {"stj": painel}, {"stj": _sinais(marcas=[sem_piso])}, hoje=HOJE)["achados"] == []
    nao_comparavel = _painel("stj", [_ministro(T4[0], comparavel=False)])
    assert achados.montar(REGRAS, {"stj": nao_comparavel}, {"stj": _sinais(marcas=[NORONHA])}, hoje=HOJE)["achados"] == []


def test_heranca_da_semana_com_convocada() -> None:
    fatima = _ministro("stj-fatima-maranhao", convocado={"tribunal": "TJPB", "vaga_de": None, "desde": "2026-11-17"},
                       comparavel=False, processos=4637)
    painel = _painel("stj", [fatima])
    sinais = _sinais(heranca={"stj-fatima-maranhao": [["2026-11-19", 1014], ["2026-11-21", 2]]})
    [a] = achados.montar(REGRAS, {"stj": painel}, {"stj": sinais}, hoje=HOJE)["achados"]
    assert a["titulo"] == ("Fátima Maranhão, convocada do TJPB na Sexta Turma desde 17/11, recebeu 1.014 processos por atribuição "
                           "em 19/11/2026")
    assert "herança de acervo, não distribuição" in a["texto"] and "O acervo dela em 20/11/2026: 4.637 processos." in a["texto"]
    assert (a["peso"], a["selo"], a["prova"]["fonte"], a["prova"]["data"]) == (15.03, "MESA", "stj_atas", "2026-11-19")
    # herança antiga (mais de uma semana) não é achado do dia; abaixo do limiar (300), também não
    velha = _sinais(heranca={"stj-fatima-maranhao": [["2026-11-01", 1014]]})
    assert achados.montar(REGRAS, {"stj": painel}, {"stj": velha}, hoje=HOJE)["achados"] == []
    pouca = _sinais(heranca={"stj-fatima-maranhao": [["2026-11-21", 299]]})
    assert achados.montar(REGRAS, {"stj": painel}, {"stj": pouca}, hoje=HOJE)["achados"] == []


def test_um_achado_por_ministro_e_a_manchete() -> None:
    raul = {"turma": "T4", "ministro": "stj-raul-araujo", "k": 2800, "n": 10289, "p": 0.2721, "pares": 0.1411, "pares_k": 4320,
            "pares_n": 30617, "p_valor": 1e-200, "razao": 1.93, "passa": True}
    noronha_mesa = {**raul, "ministro": "stj-joao-otavio-de-noronha", "k": 30, "passa": True}
    painel = _painel("stj", [_ministro(m) for m in T4])
    out = achados.montar(REGRAS, {"stj": painel}, {"stj": _sinais(marcas=[NORONHA], mesa=[raul, noronha_mesa])}, hoje=HOJE)["achados"]
    assert [a["ministro"] for a in out] == ["stj-raul-araujo", "stj-joao-otavio-de-noronha"]
    assert out[0]["tipo"] == "mesa_antiga" and out[0]["titulo"].startswith("Raul Araújo: 2.800 dos 10.289 processos na mesa")
    assert out[1]["tipo"] == "fora_do_padrao"  # o de Noronha com mais peso (a mesa dele pesa menos)
    assert [a["peso"] for a in out] == sorted((a["peso"] for a in out), reverse=True)


def test_stf_vista_e_entrou() -> None:
    gilmar = _ministro("stf-gilmar-mendes", processos=1914)
    painel = _painel("stf", [gilmar, _ministro("stf-luiz-fux", processos=2058)])
    sinais = _sinais(vistas={"stf-gilmar-mendes": 2}, pauta_janela=["2026-11-23", "2026-12-07"],
                     entradas_dia={"2026-11-19": {"stf-gilmar-mendes": {"sorteio": 34}, "stf-luiz-fux": {"sorteio": 20}}},
                     total_dia={"2026-11-19": 228})
    out = achados.montar(REGRAS, {"stf": painel}, {"stf": sinais}, hoje=HOJE)["achados"]
    # um por ministro: 1 × log10(35) = 1,54 da entrada pesa mais que 2 × log10(3) = 0,95 da vista
    assert [(a["tipo"], a["ministro"], a["peso"]) for a in out] == [("entrou", "stf-gilmar-mendes", 1.54)]
    assert out[0]["titulo"] == "Gilmar Mendes recebeu 34 dos 228 processos distribuídos em 19/11/2026"
    assert out[0]["prova"]["url"] == "https://portal.stf.jus.br/atadistribuicao/"
    so_vista = achados.montar(REGRAS, {"stf": painel}, {"stf": {**sinais, "entradas_dia": {}}}, hoje=HOJE)["achados"]
    assert so_vista[0]["titulo"] == "Gilmar Mendes pediu vista em 2 processos da pauta presencial de 23/11 a 07/12/2026"
    assert (so_vista[0]["selo"], so_vista[0]["alvo"]) == ("VISTA", "#v=fila&t=stf&min=stf-gilmar-mendes")


def test_temas_recentes() -> None:
    temas = [{"tipo": "repetitivo", "numero": 1475, "desde": "2026-11-20", "julgado_em": None, "relator_id": "stj-sergio-kukina",
              "titulo": "Questão de teste."},
             {"tipo": "repetitivo", "numero": 900, "desde": "2020-01-01", "julgado_em": None, "relator_id": None, "titulo": "Antigo."},
             {"tipo": "controversia", "numero": 5, "desde": "2026-11-21", "julgado_em": None, "relator_id": None, "titulo": "Não conta."}]
    recentes = achados.temas_recentes(temas, HOJE)
    assert [(t["numero"], t["evento"]) for t in recentes] == [(1475, "afetado")]
    painel = _painel("stj", [_ministro("stj-sergio-kukina")])
    [a] = achados.montar(REGRAS, {"stj": painel}, {"stj": _sinais(temas_recentes=recentes)}, hoje=HOJE)["achados"]
    assert a["titulo"] == "Tema repetitivo 1.475 afetado em 20/11/2026, sob relatoria de Sérgio Kukina" and a["selo"] == "TEMA"


def test_de_ponta_a_ponta_com_as_contas() -> None:
    r = contas.montar("stj", turma_sintetica(), REGRAS, data_dados=DATA, hoje=HOJE)
    out = achados.montar(REGRAS, {"stj": r.painel}, {"stj": r.sinais}, hoje=HOJE)
    tipos = {(a["tipo"], a["ministro"]) for a in out["achados"]}
    assert ("fora_do_padrao", "stj-joao-otavio-de-noronha") in tipos
    assert all(a["ministro"] != "stj-fatima-maranhao" or a["tipo"] != "fora_do_padrao" for a in out["achados"])  # recém-chegada
    ministros = [a["ministro"] for a in out["achados"] if a["ministro"]]
    assert len(ministros) == len(set(ministros))
    for a in out["achados"]:
        assert set(a) == {"tipo", "trib", "peso", "selo", "titulo", "texto", "alvo", "ministro", "prova"}
        assert a["alvo"].startswith("#v=") and "&t=stj" in a["alvo"]


def test_heranca_soma_os_dias_grandes_da_semana() -> None:
    """N7: a herança de Fátima Maranhão veio em três dias; o título soma os dias grandes (o maior à
    parte) e o texto diz o que veio por distribuição (sorteio ou prevenção), não "por sorteio"."""
    fatima = _ministro("stj-fatima-maranhao", convocado={"tribunal": "TJPB", "vaga_de": None, "desde": "2026-11-17"},
                       comparavel=False, processos=4637, entradas={"sorteio_30d": 214})
    sinais = _sinais(heranca={"stj-fatima-maranhao": [["2026-11-17", 660], ["2026-11-18", 2791], ["2026-11-19", 1014], ["2026-11-21", 2]]})
    [a] = achados.montar(REGRAS, {"stj": _painel("stj", [fatima])}, {"stj": sinais}, hoje=HOJE)["achados"]
    assert a["titulo"].endswith("recebeu 4.465 processos por atribuição entre 17/11 e 19/11/2026 (2.791 só em 18/11)")
    assert "(214 em 30 dias, sorteio ou prevenção)" in a["texto"] and "por sorteio" not in a["texto"]
    assert a["prova"]["data"] == "2026-11-19"


def test_mesa_antiga_diz_os_pares_que_entraram() -> None:
    """N4: "nos outros N gabinetes que entram na comparação", com quem ficou fora dito pelo nome."""
    sinal = {"turma": "T4", "ministro": "stj-raul-araujo", "k": 2800, "n": 10289, "p": 0.2721, "pares": 0.191, "pares_k": 4320,
             "pares_n": 22626, "n_pares": 3, "fora": ["stj-luis-carlos-gambogi"], "p_valor": 1e-40, "passa": True}
    painel = _painel("stj", [_ministro(m) for m in [*T4, "stj-luis-carlos-gambogi"]])
    [a] = achados.montar(REGRAS, {"stj": painel}, {"stj": _sinais(mesa=[sinal])}, hoje=HOJE)["achados"]
    assert "sem decisão terminativa registrada há mais de um ano" in a["titulo"]
    assert "Nos outros três gabinetes da Quarta Turma que entram na comparação, 19,1% (4.320 de 22.626)" in a["texto"]
    assert "Ficam fora da comparação Luís Carlos Gambogi" in a["texto"]
