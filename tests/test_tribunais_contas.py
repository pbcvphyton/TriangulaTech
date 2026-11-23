"""Contas da aba Tribunais: o painel e os dossiês no formato do contrato, o teste com os pares,
segredo e pessoa física fora das listas, determinismo, e o fluxo (atas, pautas, decisões, temas)
com as amostras reais."""

from __future__ import annotations

import json
import random
from datetime import date
from typing import Any

import pytest

from qijournal.tribunais import contas, partes, stf, stj
from qijournal.tribunais.areas import Classificador
from qijournal.tribunais.modelo import carregar_regras
from qijournal.tribunais.tpu import TPU
from tests.fixtures.tribunais import PASTA as FX
from tests.fixtures.tribunais import contas as fx_contas

REGRAS = carregar_regras()
HOJE = "2026-11-23"
DATA = "2026-11-20"
T4 = ["stj-joao-otavio-de-noronha", "stj-raul-araujo", "stj-maria-isabel-gallotti", "stj-antonio-carlos-ferreira"]
AREAS = ("constitucional", "tributario", "administrativo", "saude", "trabalho", "previdenciario", "ambiental", "eleitoral",
         "civil", "familia", "empresarial", "processual", "consumidor", "penal", "outros", "sem_assunto")


@pytest.fixture(scope="module")
def k() -> Classificador:
    return Classificador(REGRAS, TPU.carregar())


def _json(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def proc(i: int, relator_id: str | None, area: str = "civil", *, na_mesa: bool = True, dias: int = 100, segredo: bool = False,
         sobrestado: bool = False, partes_: tuple = (), classe: str = "REsp", fora: str | None = None, relator: str | None = None,
         subarea: str | None = None, recortes: tuple = (), liminar: bool = False) -> dict:
    """Um processo sintético do STJ (as partes vão no formato de ``stj.parte_preliminar``)."""
    return {
        "trib": "stj", "id": f"stj-{classe}-{i}", "classe": classe, "numero": str(i), "cnj": None, "registro": f"2026{i:08d}",
        "relator": relator, "relator_id": relator_id, "fora": fora, "orgao": "T4", "local": "GABINETE" if na_mesa else "COORDENADORIA",
        "onde": "gabinete" if na_mesa else "cartorio", "na_mesa": na_mesa, "area": area, "area_por": "principal",
        "subarea": subarea, "recortes": list(recortes), "tambem_toca": [],
        "assunto": {"codigo": 7681, "nome": "Obrigações", "caminho": "DIREITO CIVIL › Obrigações", "profundidade": 2, "texto": None},
        "assuntos": [7681], "segredo": segredo, "partes": [dict(p) for p in partes_],
        "datas": {"autuacao": "2024-01-10", "ano_autuacao": 2024, "distribuicao": "2024-02-01", "ultima_decisao": None,
                  "andamento": None, "localizacao": None},
        "andamento": None, "sem_decisao_dias": dias, "concluso_dias": None, "local_dias": None,
        "flags": {"liminar": liminar, "sobrestado": sobrestado, "motivo_sobrestamento": "RG" if sobrestado else None},
        "origem": {"uf": "SP", "orgao": None},
    }


BB = {"polo": "ativo", "papel": "AGRAVANTE", "nome": "BANCO DO BRASIL SA", "tipo": "pj", "grupo": None, "cnpj_raiz": "00000000"}
INSS = {"polo": "passivo", "papel": "AGRAVADO", "nome": "INSTITUTO NACIONAL DO SEGURO SOCIAL", "tipo": "ente", "grupo": None,
        "cnpj_raiz": "29979036"}
PF = {"polo": "passivo", "papel": "AGRAVADO", "nome": None, "tipo": "pf", "grupo": None, "cnpj_raiz": None}


def turma_sintetica() -> list[dict]:
    """A Quarta Turma com 1.200 processos por gabinete: Noronha com 12% de Saúde (os outros, 5%),
    Fátima Maranhão (recém-chegada) com o mesmo desvio; a Presidência e um processo antigo em segredo."""
    saida: list[dict] = []
    i = 0
    for mid in T4 + ["stj-fatima-maranhao"]:
        saude = 144 if mid in ("stj-joao-otavio-de-noronha", "stj-fatima-maranhao") else 60
        for j in range(1200):
            i += 1
            area = "saude" if j < saude else "civil"
            partes_ = (BB, PF) if j % 3 == 0 else (PF,)
            saida.append(proc(i, mid, area, dias=30 + (j * 7) % 900, partes_=partes_, subarea="civil.contratos" if area == "civil" else None,
                              recortes=("bancario",) if j % 3 == 0 else ()))
    for j in range(40):
        i += 1
        saida.append(proc(i, None, "administrativo", fora="presidencia", relator="Presidente do STJ", na_mesa=False, partes_=(INSS, PF)))
    # o mais antigo de Noronha está em segredo: conta e nunca aparece em lista
    i += 1
    saida.append(proc(i, "stj-joao-otavio-de-noronha", "familia", dias=5000, segredo=True, partes_=(BB,)))
    # precatório: o requerente não entra em "quem se repete"
    for j in range(25):
        i += 1
        saida.append(proc(i, None, "administrativo", classe="Prc", fora="presidencia", relator="Presidente do STJ", na_mesa=False,
                          partes_=({"polo": "ativo", "papel": "REQUERENTE", "nome": "FUNDO DE PRECATORIOS LTDA", "tipo": "pj",
                                    "grupo": None, "cnpj_raiz": "32774233"},)))
    return saida


@pytest.fixture(scope="module")
def resultado() -> contas.Resultado:
    return contas.montar("stj", turma_sintetica(), REGRAS, data_dados=DATA, hoje=HOJE)


# ── números e formatos ───────────────────────────────────────────────────────────────────────


def test_idade_e_faixas() -> None:
    assert contas.idade([]) == {"mediana": None, "p25": None, "p75": None, "p90": None, "max": None, "mais_180": 0, "mais_365": 0}
    i = contas.idade([10, 200, 400, 30])
    assert (i["mediana"], i["max"], i["mais_180"], i["mais_365"]) == (115, 400, 2, 1)
    assert contas.idade([0, 365], completa=False)["p90"] is None
    assert contas.faixas([10, 200, 400, 800, 1200, 2000], [6, 12, 24, 36, 60]) == [
        ["ate_6m", 1], ["6_12m", 1], ["1_2a", 1], ["2_3a", 1], ["3_5a", 1], ["mais_5a", 1]]
    assert contas.fracao(851, 9809) == 0.0868 and contas.fracao(1, 0) is None and contas.razao(0.0868, 0.0566) == 1.53


def test_painel_tem_a_forma_do_contrato(resultado: contas.Resultado) -> None:
    p = resultado.painel
    assert list(p)[:5] == ["v", "trib", "nome", "sigla", "data_dados"]
    for chave in ("universo", "idade", "grupos", "media", "ministros", "fora_da_grade", "areas", "recortes", "partes", "fila",
                  "entradas", "pautas", "decisoes", "temas", "valor"):
        assert chave in p, chave
    assert p["v"] == 1 and p["sigla"] == "STJ" and p["data_dados"] == DATA
    assert set(p["universo"]) == {"processos", "na_grade", "na_mesa", "segredo", "sobrestados", "liminar", "ente_publico",
                                  "sem_decisao_180", "sem_decisao_365", "pessoas_fisicas", "recursos"}
    assert [a["id"] for a in p["areas"]] == list(AREAS)
    assert [m["id"] for m in p["ministros"]] == [m.id for m in REGRAS.composicao["stj"].ministros]
    assert [g["id"] for g in p["grupos"]] == ["s1", "s2", "s3", "pr"]
    assert p["entradas"] is None and p["pautas"] is None and p["decisoes"] is None and p["temas"] is None
    assert p["valor"]["publicado"] is False and "não estima" in p["valor"]["texto"]


def test_resumo_do_ministro(resultado: contas.Resultado) -> None:
    m = next(x for x in resultado.painel["ministros"] if x["id"] == "stj-joao-otavio-de-noronha")
    assert m["processos"] == 1201 and m["segredo"] == 1 and m["grupo"] == "s2" and m["turma"] == "T4"
    assert set(m["areas"]) == set(AREAS)
    assert set(m["areas"]["saude"]) == {"n", "p", "pares", "razao", "marca"}
    assert set(m["onde"]) == {"gabinete", "outro_gabinete", "cartorio", "mp", "presidencia", "stf", "outro"}
    assert m["sem_decisao"]["max"] == 5000  # o processo em segredo conta na idade...
    assert all(f["id"] != "stj-REsp-6041" for f in m["mais_antigos"])  # ...e nunca vai à lista
    assert len(m["mais_antigos"]) == REGRAS.limiares["listas"]["painel_mais_antigos"]
    assert m["partes"] == [["banco-do-brasil", 400]]  # o processo em segredo não entra em "quem se repete"


def test_comparacao_com_a_turma_teste_e_piso(resultado: contas.Resultado) -> None:
    ministros = {m["id"]: m for m in resultado.painel["ministros"]}
    noronha = ministros["stj-joao-otavio-de-noronha"]["areas"]["saude"]
    assert (noronha["n"], noronha["marca"]) == (144, 1)
    assert noronha["pares"] == round(180 / 3600, 4)  # os outros três gabinetes da Quarta Turma (a Fátima é da Sexta)
    assert noronha["razao"] == round((144 / 1201) / (180 / 3600), 2)
    # a recém-chegada tem o mesmo desvio, mas aparece com o selo e sem marca
    fatima = ministros["stj-fatima-maranhao"]
    assert "recem_chegado" in fatima["selos"] and not fatima["comparavel"] and fatima["areas"]["saude"]["marca"] == 0
    # os pares de Raul Araújo incluem o acervo de Noronha: 5% contra 7,3% passa do teste e do piso
    raul = ministros["stj-raul-araujo"]["areas"]["saude"]
    assert raul["pares"] == round(264 / 3601, 4) and raul["marca"] == -1
    # ministro da grade sem processos: poucos processos, sem teste
    assert "poucos_processos" in ministros["stj-nancy-andrighi"]["selos"]
    marcas = [x for x in resultado.sinais["marcas"] if x["marca"]]
    assert {(x["ministro"], x["area"]) for x in marcas} >= {("stj-joao-otavio-de-noronha", "saude")}


def test_piso_de_efeito_segura_a_marca() -> None:
    """Diferença que passa do acaso mas fica no piso (razão 1,2): sem marca."""
    lote = []
    i = 0
    for mid in T4:
        saude = 600 if mid == T4[0] else 500
        for j in range(10000):
            i += 1
            lote.append(proc(i, mid, "saude" if j < saude else "civil", partes_=()))
    r = contas.montar("stj", lote, REGRAS, data_dados=DATA, hoje=HOJE)
    x = next(m for m in r.sinais["marcas"] if m["ministro"] == T4[0] and m["area"] == "saude")
    assert x["acaso"] and not x["efeito"] and x["marca"] == 0


def test_fora_da_grade_e_universo(resultado: contas.Resultado) -> None:
    p = resultado.painel
    pres = next(f for f in p["fora_da_grade"] if f["id"] == "presidencia")
    assert pres["n"] == 65 and pres["na_mesa"] is None and pres["relatores"] == [["Presidente do STJ", 65]]
    assert all(f["n"] for f in p["fora_da_grade"])  # linha vazia sai
    u = p["universo"]
    assert u["processos"] == 6066 and u["na_grade"] == 6001 and u["segredo"] == 1
    assert u["ente_publico"] == 40  # o INSS como quem responde, na Presidência
    grupos = {g["id"]: g for g in p["grupos"]}
    assert grupos["s2"]["processos"] == 4801 and grupos["pr"]["processos"] == 0


def test_quem_se_repete_sem_segredo_e_sem_precatorio(resultado: contas.Resultado) -> None:
    top = {x["id"]: x for x in resultado.painel["partes"]["top"]}
    assert top["banco-do-brasil"]["n"] == 2000 and top["banco-do-brasil"]["recorre"] == 2000  # o processo em segredo fica fora
    assert top["banco-do-brasil"]["mesas"] == 5 and top["banco-do-brasil"]["area"] == "civil"
    assert top["inss"]["responde"] == 40
    assert "cnpj-32774233" not in top  # o fundo de precatórios (Prc) não entra
    assert resultado.painel["partes"]["nomes"]["banco-do-brasil"] == {"nome": "Banco do Brasil", "tipo": "pj"}


def test_nome_de_litigante_so_acima_do_limiar() -> None:
    lote = [proc(i, T4[0], partes_=({"polo": "ativo", "papel": "AGRAVANTE", "nome": "EMPRESA RARA LTDA", "tipo": "pj",
                                     "grupo": None, "cnpj_raiz": "11222333"},)) for i in range(19)]
    r = contas.montar("stj", lote, REGRAS, data_dados=DATA, hoje=HOJE)
    assert r.painel["partes"]["top"] == []
    m = next(x for x in r.painel["ministros"] if x["id"] == T4[0])
    assert m["partes"] == [["cnpj-11222333", 19]]  # no ministro, 5 bastam
    folha = m["mais_antigos"][0]
    assert folha["partes"] == [{"polo": "ativo", "tipo": "pj", "grupo": None}]  # na mini-folha, só com nome publicável no tribunal


def test_sobrestado_fora_da_idade_e_na_fila(resultado: contas.Resultado) -> None:
    lote = [proc(1, T4[0], dias=900, sobrestado=True), proc(2, T4[0], dias=50)]
    r = contas.montar("stj", lote, REGRAS, data_dados=DATA, hoje=HOJE)
    m = next(x for x in r.painel["ministros"] if x["id"] == T4[0])
    assert m["sobrestados"] == 1 and m["sem_decisao"]["max"] == 50 and m["na_mesa"] == 2
    assert r.painel["fila"]["sobrestados"] == {"total": 1, "por_motivo": {"RG": 1}}


def test_dossie_forma_e_privacidade(resultado: contas.Resultado) -> None:
    d = resultado.dossies["stj-joao-otavio-de-noronha"]
    for chave in ("v", "id", "trib", "data_dados", "ministro", "resumo", "areas", "subareas", "recortes", "classes", "onde", "idade",
                  "mais_antigos", "em_pauta", "em_vista", "partes", "entradas", "decisoes", "temas"):
        assert chave in d, chave
    assert "mais_antigos" not in d["resumo"]
    assert d["ministro"]["orgao_nome"] == "Quarta Turma" and d["ministro"]["link_oficial"] is None
    assert [a["id"] for a in d["areas"]] == ["saude", "civil", "familia"]
    assert d["idade"]["sem_decisao"]["faixas"][0][0] == "ate_6m"
    assert len(d["mais_antigos"]) == 15 and all(f["link"].startswith("https://processo.stj.jus.br/") for f in d["mais_antigos"])
    top = d["partes"]["top"][0]
    assert (top["id"], top["nome"], top["n"]) == ("banco-do-brasil", "Banco do Brasil", 400)
    assert "stj-nancy-andrighi" not in resultado.dossies  # sem acervo, pauta, entrada nem tema: sem dossiê
    texto = _json(resultado.painel) + _json(resultado.dossies)
    assert "stj-REsp-6041" not in texto  # o processo em segredo
    assert "FUNDO DE PRECATORIOS" not in texto


def test_saida_deterministica(resultado: contas.Resultado) -> None:
    lote = turma_sintetica()
    random.Random(3).shuffle(lote)
    outro = contas.montar("stj", lote, REGRAS, data_dados=DATA, hoje=HOJE)
    assert _json(outro.painel) == _json(resultado.painel)
    assert _json(outro.dossies) == _json(resultado.dossies)


def test_tamanho_dentro_do_teto(resultado: contas.Resultado) -> None:
    assert len(_json(resultado.painel).encode()) < 300_000
    assert max(len(_json(d).encode()) for d in resultado.dossies.values()) < 300_000


# ── os casos difíceis do analista (registros reais) ──────────────────────────────────────────


def test_casos_dificeis_do_analista(k: Classificador) -> None:
    casos = json.loads((fx_contas.PASTA / "casos_dificeis_stj.json").read_text(encoding="utf-8"))["casos"]
    leitor = stj.LeitorSTJ(REGRAS, k)
    processos = [stj.processo_stj(r, leitor, data_dados=DATA) for r in casos.values()]
    r = contas.montar("stj", processos, REGRAS, data_dados=DATA, hoje=HOJE)
    texto = _json(r.painel) + _json(r.dossies)
    assert "PESSOA FÍSICA" not in texto and "MASCARADO" not in texto  # nome de gente nunca
    em_segredo = {p["id"] for p in processos if p["segredo"]}
    assert em_segredo and not any(f'"{i}"' in texto for i in em_segredo)  # processo em segredo nunca aparece
    assert r.painel["universo"]["segredo"] == len(em_segredo)  # mas conta
    fora = {f["id"]: f["n"] for f in r.painel["fora_da_grade"]}
    assert fora["presidencia"] >= 1 and fora["residuos"] >= 1 and fora["sem_relator"] >= 1
    nomes = r.painel["partes"]["nomes"]
    assert not any(g.startswith("cnpj-27548667") for g in nomes)  # o MEI


# ── o fluxo, com as amostras reais ───────────────────────────────────────────────────────────


def test_entradas_do_stj_sorteio_heranca_registro(k: Classificador) -> None:
    leitor = stj.LeitorSTJ(REGRAS, k)
    itens = stj.ler_ata(FX / "stj/ata20261119_amostra.json", REGRAS, k, leitor=leitor)
    itens += stj.ler_ata(FX / "stj/ata20261121.json", REGRAS, k, leitor=leitor)
    r = contas.montar("stj", [proc(1, "stj-fatima-maranhao")], REGRAS, data_dados=DATA, hoje=HOJE, atas=itens)
    e = r.painel["entradas"]
    assert e["fonte"] == "stj_atas" and e["janela"] == ["2026-11-19", "2026-11-21"] and e["dias_com_ata"] == 2
    assert e["total"] == {"sorteio": 13, "heranca": 6, "registro": 12}
    fatima = next(m for m in r.painel["ministros"] if m["id"] == "stj-fatima-maranhao")
    assert fatima["entradas"]["heranca_12m"] == 6 and "heranca" in fatima["selos"]
    assert r.dossies["stj-fatima-maranhao"]["entradas"]["heranca_dias"] == [["2026-11-19", 4], ["2026-11-21", 2]]
    assert r.sinais["heranca"]["stj-fatima-maranhao"] == [["2026-11-19", 4], ["2026-11-21", 2]]


def test_pautas_decisoes_temas_do_stj(k: Classificador) -> None:
    leitor = stj.LeitorSTJ(REGRAS, k)
    pautas = stj.ler_pautas(FX / "stj/pautas_futuras_20261122_amostra.json.gz", REGRAS, k, leitor=leitor)
    integras = stj.ler_integras(FX / "stj/metadados20261015_amostra.json", REGRAS, k, arquivo="metadados20261015.json", leitor=leitor)
    temas = stj.montar_temas(stj.ler_temas(FX / "stj/temas.csv"), stj.ler_processos_precedentes(FX / "stj/processos.csv"), REGRAS, k,
                             leitor=leitor)
    r = contas.montar("stj", [proc(1, T4[0])], REGRAS, data_dados=DATA, hoje=HOJE,
                      pautas={"data_dados": "2026-11-22", "itens": pautas}, decisoes=integras, temas=temas)
    pa = r.painel["pautas"]
    assert pa["itens"] == len(pautas) and pa["principal"] + pa["interno"] == pa["itens"] and pa["data_dados"] == "2026-11-22"
    assert pa["vistas"] is None
    de = r.painel["decisoes"]
    # a janela é a que os dados cobrem (um arquivo só: o dia dele), nunca 12 meses presumidos
    assert (de["ultimo_arquivo"], de["atraso_dias"], de["janela"]) == ("2026-10-15", 39, ["2026-10-15", "2026-10-15"])
    assert de["documentos"] == 42 and de["monocraticas"] + de["acordaos"] == 42
    te = r.painel["temas"]
    assert te["fonte"] == "stj_precedentes" and te["pendentes"] == te["por_tipo"]["repetitivo"] + te["por_tipo"]["iac"]
    soma = sum(m["temas"] for m in r.painel["ministros"])
    assert soma + sum(x[1] for x in te["relator_saiu"]) + te["sem_relator"] == te["pendentes"]
    assert all(len(x) == 3 and x[0] != "Relator não informado" for x in te["relator_saiu"])  # [nome, n, motivo]


def test_stf_planilha_atas_jurisprudencia_rg_pauta() -> None:
    regras = REGRAS
    acervo = stf.ler_acervo(FX / "stf/acervo_amostra.xlsx", regras)
    ler = lambda nome: (FX / "stf" / nome).read_text(encoding="utf-8")
    ata = stf.ler_ata(ler("ata_2026-11-20_253.html"), regras)
    indice = stf.ler_indice_atas(ler("ata_recuperaata_2026-11-20.xml"))
    atas = {"janela": ["2026-11-20", "2026-11-20"], "dias": [{"data": "2026-11-20", "vazio": False, "presidencia": indice["presidencia"],
                                                              "atas": [ata]}]}
    decisoes = stf.ler_decisoes(json.loads(ler("juris_agregacao_12m.json")), regras, inicio="2025-11-23", fim="2026-11-22")
    rg = {"temas": stf.temas_rg(stf.ler_todos_temas(ler("rg_todostemas_trecho.html")), stf.ler_exportacao(ler("rg_exportar_trecho.xls.html")),
                                regras)}
    pauta = {"janela": ["2026-11-23", "2026-12-07"], "itens": stf.ler_sessao_presencial(json.loads(ler("pauta_sessao-presencial_2026-11-25.json")), regras)}
    r = contas.montar("stf", acervo.processos, regras, data_dados=acervo.data_dados, hoje=HOJE, atas=atas, pautas=pauta,
                      decisoes=decisoes, temas=rg)
    p = r.painel
    assert p["sigla"] == "STF" and [g["id"] for g in p["grupos"]] == ["t1", "t2", "pr"]
    assert p["universo"]["processos"] == len(acervo.processos)
    assert p["decisoes"]["presidencia"] == decisoes["presidencia"] and p["decisoes"]["atraso_dias"] is None
    fachin = next(m for m in p["ministros"] if m["id"] == "stf-edson-fachin")
    assert fachin["grupo"] == "pr" and not fachin["comparavel"] and fachin["areas"]["penal"]["pares"] is None
    assert p["entradas"]["total"]["sorteio"] == ata["total"]["sorteio"]
    assert p["pautas"]["vistas"] is not None and p["temas"]["por_tipo"]["rg"] == p["temas"]["pendentes"]
    # no STF a planilha só dá o ano de autuação: nenhuma idade em dias (seria inventada); vale o ano
    assert p["idade"]["no_tribunal"]["mediana"] is None and p["idade"]["no_tribunal"]["p90"] is None
    au = p["idade"]["autuacao"]
    assert set(au) == {"ano_mediano", "no_ano_dos_dados", "por_ano"} and sum(n for _, n in au["por_ano"]) > 0
    assert all(m["no_tribunal"]["mediana"] is None for m in p["ministros"])
    assert any("autuacao" in m for m in p["ministros"])
    assert "stf" in r.cobertura["partes"]
    texto = _json(p) + _json(r.dossies)
    assert not any(f'"{x["id"]}"' in texto for x in acervo.processos if x["segredo"])


def test_mini_folha_recusa_segredo() -> None:
    with pytest.raises(ValueError):
        contas.mini_folha(proc(1, T4[0], segredo=True), REGRAS)
    f = contas.mini_folha(proc(1, T4[0], liminar=True, partes_=(BB, PF)), REGRAS)
    assert f["flags"] == {"liminar": True} and f["link"].endswith("termo=202600000001")
    assert {x["tipo"] for x in f["partes"]} == {"pj", "pf"}


def test_catalogo_padrao_sem_efeito_colateral() -> None:
    assert partes.catalogo_padrao() is partes.catalogo_padrao()


def test_o_que_o_presidente_assinou_vai_para_a_presidencia():
    """STJ: as íntegras dão o nome de quem assinou, não o cargo. O que Herman Benjamin assinou até
    24/08/2026 (presidente) vai para a Presidência; o que assinou depois, de volta à Turma, para o
    cartão dele (integração, 23/11/2026: 150 mil decisões de 12 meses estavam no cartão)."""
    def doc(dia: str, mid: str | None, fora: str | None = None) -> dict:
        return {"arquivo": f"metadados{dia.replace('-', '')}.json", "data": dia, "registro": f"{dia}{mid}", "id": "x",
                "relator_id": mid, "fora": fora, "documento": "decisao", "interno": False}
    itens = [doc("2026-08-20", "stj-herman-benjamin"), doc("2026-08-21", "stj-herman-benjamin"), doc("2026-09-01", "stj-herman-benjamin"),
             doc("2026-09-01", "stj-luis-felipe-salomao"), doc("2026-08-20", "stj-luis-felipe-salomao"), doc("2026-09-02", None, "presidencia")]
    painel, por = contas._decisoes("stj", {"itens": itens, "ultimo_arquivo": "2026-09-02"}, date(2026, 11, 23), REGRAS)
    assert painel["presidencia"] == 4 and painel["documentos"] == 6
    assert por["stj-herman-benjamin"]["documentos"] == 1  # só o de 01/09, de volta à Primeira Turma
    assert por["stj-luis-felipe-salomao"]["documentos"] == 1  # o de 20/08, antes de presidir
