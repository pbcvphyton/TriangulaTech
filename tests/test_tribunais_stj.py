"""As fontes do STJ (qijournal/tribunais/stj.py): a página do conjunto no CKAN, o acervo em
tramitação lido em fluxo, as atas de distribuição, as pautas futuras, os precedentes qualificados e
as íntegras do DJ. Sem rede: o coletor recebe um ``fetch`` falso e um relógio que só anda quando
ele dorme.

Fixtures em ``tests/fixtures/tribunais/stj/`` (todas reais, baixadas em 22–23/11/2026 do Portal de
Dados Abertos do STJ, CC-BY; nomes de pessoa física, MEI e advogados e números de OAB **mascarados**;
o MEI mascarado guarda 11 zeros no fim do nome e um CNPJ de exemplo, para o teste ver o sinal):

- ``acervo_20261120_amostra.json.gz``: 44 registros do acervo de 20/11/2026 (um ou mais por rótulo
  de relator, e os casos de borda: MEI, Banco do Brasil, tribunal impetrado, sobrestados T/RG/ST/C,
  sem número na classe, sem relator, no MPF, remetido ao STF, triagem, gabinete de outro ministro,
  secretaria de Turma), no formato do arquivo (um objeto por linha);
- ``ata20261119_amostra.json`` (20 distribuições: as quatro formas, a herança de Fátima Maranhão, o
  par Presidência → ministro do mesmo registro) e ``ata20261121.json`` (o dia inteiro, 11);
- ``pautas_futuras_20261122_amostra.json.gz``: 16 itens (principal, AgInt, AgRg, EDcl, RCD, ProRQF,
  segredo, Corte Especial);
- ``temas.csv``, ``temas_copia_parada.csv`` (a cópia de 13/11 que a página também publica) e
  ``processos.csv``: 14 precedentes (pendentes, julgados, cancelados, relevância, IAC, PUIL);
- ``metadados20261015_amostra.json``: 42 documentos das íntegras (sem ``descricaoMonocratica``);
- ``pagina_*.html``: recortes das páginas reais dos conjuntos (o esqueleto do CKAN com alguns
  itens, inclusive os republicados e os nomes fora do padrão).
"""

from __future__ import annotations

import gzip
import io
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from qijournal import net
from qijournal.net import FetchError
from qijournal.tribunais import stj
from qijournal.tribunais.areas import Classificador
from qijournal.tribunais.fontes import ColetorTribunais, RecusaDoHost
from qijournal.tribunais.modelo import carregar_regras
from qijournal.tribunais.tpu import TPU
from tests.fixtures import tribunais as fx

PASTA = fx.PASTA / "stj"
PORTAL = "https://dadosabertos.web.stj.jus.br"
ACERVO = PASTA / "acervo_20261120_amostra.json.gz"


@pytest.fixture(scope="module")
def regras():
    return carregar_regras()


@pytest.fixture(scope="module")
def classificador(regras):
    return Classificador(regras, TPU.carregar())


@pytest.fixture(scope="module")
def acervo(regras, classificador):
    resumo = stj.ResumoAcervo()
    processos = list(stj.ler_acervo(ACERVO, regras, classificador, data_dados="2026-11-20", resumo=resumo))
    return {p["registro"]: p for p in processos}, resumo


def _html(nome: str) -> str:
    return (PASTA / nome).read_text(encoding="utf-8")


# ── a página do conjunto ─────────────────────────────────────────────────────────────────────


def test_pagina_do_acervo_da_o_link_do_dia_sem_api():
    recursos = stj.links_da_pagina(fx.ler("ckan_dataset_acervo.html"))
    assert [r.arquivo for r in recursos] == ["dicionario-tramitando.csv", "processos_tramitando_20261120.json.gz"]
    escolhido = stj.escolher_mais_recente(recursos, "acervo")
    assert escolhido.data == "2026-11-20"
    assert escolhido.url == (f"{PORTAL}/dataset/d68560f4-eb0c-4fb2-8395-f8a36f441c10/resource/"
                             "265df1ec-72dd-412a-bdfb-ac5033d620d0/download/processos_tramitando_20261120.json.gz")
    assert all("/api/" not in r.url for r in recursos)


def test_link_relativo_vira_absoluto_e_api_nunca_entra():
    html = ('<a href="/dataset/x1/resource/y1/download/ata20261119.json">a</a>'
            '<a href="https://dadosabertos.web.stj.jus.br/api/dataset/x/resource/y/download/ata20261120.json">b</a>'
            '<a href="/dataset/x1/resource/y1">mais informações</a>')
    recursos = stj.links_da_pagina(html)
    assert [r.url for r in recursos] == [f"{PORTAL}/dataset/x1/resource/y1/download/ata20261119.json"]


def test_atas_um_por_dia_republicada_vale_a_ultima_e_janela_por_data():
    recursos = stj.links_da_pagina(_html("pagina_atas.html"))
    dias = stj.por_dia(recursos, "atas")
    assert list(dias) == sorted(dias)  # na página, 13/11 vem antes de 11/11; a ordem é a da data
    tres = [r for r in recursos if r.arquivo == "ata20230803.json"]
    assert len(tres) == 3 and dias["2023-08-03"] == tres[-1]
    ultimos = stj.escolher_ultimos_dias(recursos, "atas", 5)
    assert [r.data for r in ultimos] == ["2026-11-18", "2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22"]
    ate = stj.escolher_ultimos_dias(recursos, "atas", 3, ate="2026-11-20")
    assert [r.data for r in ate] == ["2026-11-18", "2026-11-19", "2026-11-20"]
    assert stj.escolher_ultimos_dias(recursos, "atas", 0) == []


def test_integras_so_metadados_diarios_e_a_janela_termina_no_ultimo_arquivo():
    recursos = stj.links_da_pagina(_html("pagina_integras.html"))
    dias = stj.por_dia(recursos, "integras")
    assert list(dias) == ["2026-10-09", "2026-10-13", "2026-10-14", "2026-10-15"]  # sem mensais, sem ZIP, sem "___"
    republicadas = [r for r in recursos if r.arquivo == "metadados20261013.json"]
    assert len(republicadas) == 7 and dias["2026-10-13"] == republicadas[-1]
    janela = stj.escolher_ultimos_dias(recursos, "integras", 3)
    assert [r.data for r in janela] == ["2026-10-13", "2026-10-14", "2026-10-15"]
    assert stj.atraso_dias("2026-10-15", date(2026, 11, 22)) == 38
    assert stj.atraso_dias(None, date(2026, 11, 22)) is None


def test_precedentes_trazem_dois_recursos_de_mesmo_nome():
    recursos = stj.links_da_pagina(_html("pagina_precedentes.html"))
    assert len(stj.por_nome(recursos, "temas.csv")) == 2
    assert len(stj.por_nome(recursos, "Processos.CSV")) == 2


def test_data_do_nome():
    assert stj.data_do_nome("processos_tramitando_20261120.json.gz") == "2026-11-20"
    assert stj.data_do_nome("metadados202101.json") is None
    assert stj.data_do_nome("ata20261399.json") is None


# ── leitura em fluxo ─────────────────────────────────────────────────────────────────────────


def test_objetos_json_por_linha_indentado_e_linha_ilegivel():
    por_linha = '[\n{"a":1,"s":"x,y"},\n{"a":2,"l":[1,2]},\n{"a":3}\n]\n'
    assert [o["a"] for o in stj.objetos_json(io.StringIO(por_linha), bloco=7)] == [1, 2, 3]
    indentado = json.dumps([{"a": 1, "b": {"c": [1, 2]}}, {"a": 2}], indent=4)
    assert [o["a"] for o in stj.objetos_json(io.StringIO(indentado), bloco=5)] == [1, 2]
    leitura = stj.Leitura()
    quebrado = '[\n{"a":1},\n{"a":2,"b":tru},\n{"a":3}\n]\n'
    assert [o["a"] for o in stj.objetos_json(io.StringIO(quebrado), leitura)] == [1, 3]
    assert (leitura.objetos, leitura.ilegiveis) == (2, 1)


def test_abrir_texto_pelo_numero_magico():
    texto = '[{"a": "ção"}]'
    assert json.load(stj.abrir_texto(gzip.compress(texto.encode()))) == [{"a": "ção"}]
    assert json.load(stj.abrir_texto(("﻿" + texto).encode("utf-8"))) == [{"a": "ção"}]


def test_acervo_truncado_e_formato_inesperado(regras, classificador):
    corpo = ACERVO.read_bytes()
    with pytest.raises(stj.FormatoInesperado):
        list(stj.ler_acervo(corpo[: len(corpo) // 2], regras, classificador))


# ── o acervo: o registro Processo ───────────────────────────────────────────────────────────


def test_acervo_resumo_bate_com_a_amostra(acervo):
    processos, resumo = acervo
    assert len(processos) == 44 and resumo.processos == resumo.lidos == 44 and resumo.ilegiveis == 0
    assert resumo.data == "2026-11-20" and resumo.problemas() == []
    assert list(resumo.para_meta()["fora_da_grade"]) == ["presidencia", "vice", "sem_relator", "presidentes_orgao",
                                                          "residuos", "sem_relator_arquivo"]
    assert resumo.para_meta() == {
        "processos": 44, "na_grade": 26, "segredo": 5,
        "area_por": {"principal": 40, "classe": 3, "sem_assunto": 1},
        "codigos_fora_da_tpu": 0, "relatores_fora_do_mapa": [],
        "fora_da_grade": {"presidencia": 4, "vice": 2, "sem_relator": 3, "presidentes_orgao": 2, "residuos": 3,
                          "sem_relator_arquivo": 1},  # na ordem do config
    }
    assert resumo.na_mesa == 12 and resumo.sem_numero == 1
    assert dict(resumo.cargos) == {"stj-mauro-campbell-marques": 2, "stj-benedito-goncalves": 1}


def test_acervo_campos_de_um_processo_na_mesa(acervo):
    p = acervo[0]["201601120800"]  # AREsp 912033, Gallotti, Banco do Brasil
    assert (p["trib"], p["id"], p["classe"], p["numero"], p["cnj"]) == ("stj", "stj-AREsp-912033", "AREsp", "912033",
                                                                           "20124790620148260000")
    assert (p["relator"], p["relator_id"], p["fora"], p["orgao"]) == ("Maria Isabel Gallotti", "stj-maria-isabel-gallotti", None, "T4")
    assert (p["local"], p["na_mesa"], p["onde"]) == ("GABINETE DA MINISTRA MARIA ISABEL GALLOTTI", True, "gabinete")
    assert (p["area"], p["area_por"], p["subarea"]) == ("civil", "principal", "civil.contratos")
    assert p["assunto"]["codigo"] == 4805 and p["assunto"]["texto"] is None and p["assuntos"] == [4805]
    assert p["datas"] == {"autuacao": "2016-04-25", "ano_autuacao": 2016, "distribuicao": p["datas"]["distribuicao"],
                          "ultima_decisao": p["datas"]["ultima_decisao"], "andamento": None, "localizacao": None}
    assert p["sem_decisao_dias"] == 3825 and p["concluso_dias"] is None and p["local_dias"] is None
    assert p["flags"] == {"liminar": False, "sobrestado": False, "motivo_sobrestamento": None}
    assert p["origem"]["uf"] == "SP"
    bb = p["partes"][0]
    assert bb == {"polo": "ativo", "papel": "AGRAVANTE", "nome": "BANCO DO BRASIL SA", "tipo": "pj", "grupo": None,
                  "cnpj_raiz": "00000000"}
    assert list(p) == ["trib", "id", "classe", "numero", "cnj", "registro", "relator", "relator_id", "fora", "orgao", "local",
                       "onde", "na_mesa", "area", "area_por", "subarea", "recortes", "tambem_toca", "assunto", "assuntos",
                       "segredo", "partes", "datas", "andamento", "sem_decisao_dias", "concluso_dias", "local_dias",
                       "flags", "origem"]


@pytest.mark.parametrize("registro, relator_id, fora, onde, na_mesa", [
    ("201502450031", "stj-nilsoni-de-freitas", None, "gabinete", True),  # convocada: "GABINETE DA DESEMBARGADORA CONVOCADA DO TJDFT"
    ("201800858020", "stj-luis-carlos-gambogi", None, "outro_gabinete", False),  # relator Gambogi, no gabinete de Gallotti
    ("202001989087", "stj-fatima-maranhao", None, "cartorio", False),  # herança: sendo redistribuído
    ("201801376552", None, "residuos", "cartorio", False),  # quem saiu (Og Fernandes)
    ("202604841819", None, "sem_relator", "presidencia", False),  # "Não Informado", na triagem
    ("200801648910", None, "sem_relator_arquivo", "outro", False),  # sem relator nem local
    ("201500519043", None, "presidencia", "presidencia", False),
    ("200602248835", None, "vice", "stf", False),  # remetido ao STF, sobrestado por RG
    ("201402911392", "stj-regina-helena-costa", None, "mp", False),
    ("201403105015", "stj-sergio-kukina", None, "outro", False),  # secretaria da Primeira Turma
    ("201001327708", None, "presidentes_orgao", "cartorio", False),  # ExeMS do Presidente da Terceira Seção
    ("201300117194", "stj-mauro-campbell-marques", None, "gabinete", True),  # Vice: ministro fora da grade
])
def test_acervo_relator_e_lugar(acervo, registro, relator_id, fora, onde, na_mesa):
    p = acervo[0][registro]
    assert (p["relator_id"], p["fora"], p["onde"], p["na_mesa"]) == (relator_id, fora, onde, na_mesa)


def test_acervo_heranca_zera_o_relogio_e_sobrestados(acervo):
    processos = acervo[0]
    assert processos["202001989087"]["sem_decisao_dias"] == 2  # atribuição de 18/11: a armadilha da herança
    assert processos["202604841819"]["sem_decisao_dias"] is None  # sem distribuição nem decisão
    motivos = sorted(p["flags"]["motivo_sobrestamento"] for p in processos.values() if p["flags"]["sobrestado"])
    assert motivos == ["C", "RG", "RG", "RG", "RG", "RG", "ST", "T", "T", "T"]


def test_acervo_sem_numero_na_classe_usa_o_registro(acervo):
    p = acervo[0]["202604862507"]
    assert (p["id"], p["numero"], p["classe"]) == ("stj-AREsp-r202604862507", "", "AREsp")


def test_acervo_partes_sem_nome_de_pessoa(acervo):
    processos = acervo[0]
    texto = json.dumps(list(processos.values()), ensure_ascii=False)
    assert "PESSOA FÍSICA" not in texto and "ADVOGADO(A)" not in texto and "advogados" not in texto
    for p in processos.values():
        for parte in p["partes"]:
            if parte["tipo"] in ("pf", "desconhecido"):
                assert parte["nome"] is None and parte["cnpj_raiz"] is None
            if parte["tipo"] in ("autoridade", "qualificador"):
                assert parte["polo"] is None
    mei = processos["201903348109"]["partes"]  # o MEI (CNPJ com CPF no nome) é pessoa física
    assert [(x["tipo"], x["nome"]) for x in mei][:2] == [("pf", None), ("pf", None)]
    hc = processos["201700584521"]["partes"]
    assert [x["tipo"] for x in hc if x["papel"] == "PACIENTE"] and all(
        x["tipo"] == "pf" and x["polo"] == "ativo" for x in hc if x["papel"] == "PACIENTE")
    impetrado = next(x for x in hc if x["papel"] == "IMPETRADO")
    assert (impetrado["tipo"], impetrado["nome"]) == ("autoridade", "TRIBUNAL DE JUSTIÇA DO ESTADO DO CEARÁ")
    segredo = processos["201502450573"]
    assert segredo["segredo"] and all(x["tipo"] == "desconhecido" for x in segredo["partes"])


def test_acervo_casos_dificeis_do_analista(regras, classificador):
    """Os 18 casos do analista (fixture da fundação), um registro real por caso."""
    casos = json.loads(fx.ler("stj_acervo_casos.json"))["casos"]
    leitor = stj.LeitorSTJ(regras, classificador)
    p = {nome: stj.processo_stj(r, leitor) for nome, r in casos.items()}
    assert p["codigo_com_prefixo_9_na_lista"]["assuntos"] == [10288]
    assert p["execucao_ms_presidente_secao"]["fora"] == "presidentes_orgao"
    assert p["sobrestado_repercussao_geral"]["flags"]["motivo_sobrestamento"] == "RG"
    assert [x["tipo"] for x in p["qualificador_representado_por"]["partes"]] == ["pf", "qualificador", "pf"]
    requisitante = p["precatorio_stj_requisitante"]["partes"][-1]
    assert (requisitante["papel"], requisitante["tipo"], requisitante["polo"]) == ("REQUISITANTE", "autoridade", None)
    assert p["familia_em_segredo"]["segredo"] and p["familia_em_segredo"]["area"] == "familia"
    assert p["banco_do_brasil_raiz_00000000"]["partes"][0]["cnpj_raiz"] == "00000000"
    assert p["ramo_fora_do_padrao"]["area"] == "sem_assunto"
    assert p["hc_tribunal_como_impetrado"]["area_por"] == "classe"
    assert p["no_mpf_vista"]["onde"] == "mp"
    assert p["presidencia_na_arp"]["onde"] == "presidencia"
    assert (p["convocado_gambogi_vaga_buzzi"]["relator_id"], p["convocado_gambogi_vaga_buzzi"]["relator"]) == (
        "stj-luis-carlos-gambogi", "Luís Carlos Gambogi")
    assert p["ex_ministro_em_redistribuicao_og"]["fora"] == "residuos"
    assert p["heranca_convocada_fatima_maranhao"]["sem_decisao_dias"] == 2
    assert p["relator_nao_informado"]["fora"] == "sem_relator"
    assert p["principal_processual_com_material_na_lista"]["area_por"] == "so_processual"


def test_acervo_data_divergente_vira_problema(regras, classificador):
    resumo = stj.ResumoAcervo()
    for _ in stj.ler_acervo(ACERVO, regras, classificador, data_dados="2026-11-21", resumo=resumo):
        pass
    assert resumo.problemas() == ["O campo Data dos registros (2026-11-20) difere do nome do arquivo (2026-11-21)."]


def test_sem_decisao_e_a_mais_recente_entre_distribuicao_e_decisao(regras, classificador):
    leitor = stj.LeitorSTJ(regras, classificador)
    base = {"Data": "2026-11-20", "siglaClasse": "REsp  ", "numeroNaClasse": 1, "nomeMinistroRelator": "Nancy Andrighi",
            "codigoOrgaoJulgador": "T3 ", "localProcesso": "GABINETE DA MINISTRA NANCY ANDRIGHI"}
    p = stj.processo_stj({**base, "dataUltimaDistribuicao": "2026-01-01", "dataUltimaDecisao": "2026-06-01"}, leitor)
    assert (p["sem_decisao_dias"], p["classe"], p["orgao"], p["na_mesa"]) == (172, "REsp", "T3", True)
    assert stj.processo_stj(base, leitor)["sem_decisao_dias"] is None


# ── partes ───────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("papel, nome, cnpj, esperado", [
    ("AGRAVANTE", "BANCO DO BRASIL SA", "00000000000191", ("ativo", "pj", "BANCO DO BRASIL SA", "00000000")),
    ("AGRAVADO", "FAZENDA NACIONAL", "00394460000141", ("passivo", "ente", "FAZENDA NACIONAL", "00394460")),
    ("RECORRENTE", "FULANO DE TAL 12345678901", "11222333000181", ("ativo", "pf", None, None)),  # MEI
    ("RECORRENTE", "EMPRESA X LTDA", "00000000000000", ("ativo", "pj", "EMPRESA X LTDA", None)),  # CNPJ vazio
    ("INTERESSADO", "FULANO DE TAL", None, ("outro", "pf", None, None)),
    ("PACIENTE", "A L R X", None, ("ativo", "pf", None, None)),
    ("IMPETRADO", "TRIBUNAL DE JUSTIÇA DO ESTADO DO CEARÁ", "09444530000101", (None, "autoridade", "TRIBUNAL DE JUSTIÇA DO ESTADO DO CEARÁ", "09444530")),
    ("IMPETRADO", "FULANO DE TAL", None, (None, "autoridade", None, None)),
    ("RECLAMADO", "TRIBUNAL DE JUSTIÇA DO ESTADO DE SÃO PAULO", None, (None, "autoridade", "TRIBUNAL DE JUSTIÇA DO ESTADO DE SÃO PAULO", None)),
    ("REPRESENTADO POR", "FULANA DE TAL", None, (None, "qualificador", None, None)),
    ("POR INCORPORAÇÃO", "BANCO X S.A.", "00000000000191", (None, "qualificador", "BANCO X S.A.", "00000000")),
    ("AGRAVADO", "Parte em Segredo de Justiça", None, ("passivo", "desconhecido", None, None)),
    ("RECORRIDO", "OS MESMOS", None, ("passivo", "desconhecido", None, None)),
    ("AGRAVADO", "SECRETARIA DE ESTADO DA SAÚDE", None, ("passivo", "ente", "SECRETARIA DE ESTADO DA SAÚDE", None)),
    ("REQUERENTE", "SINDICATO DOS SERVIDORES  X", None, ("ativo", "pj", "SINDICATO DOS SERVIDORES X", None)),
])
def test_parte_preliminar(papel, nome, cnpj, esperado):
    parte = stj.parte_preliminar({"descTipoParte": papel, "nomeParte": nome, "numeroCNPJ": cnpj, "advogados": [{"x": 1}]})
    assert (parte["polo"], parte["tipo"], parte["nome"], parte["cnpj_raiz"]) == esperado
    assert parte["grupo"] is None and set(parte) == {"polo", "papel", "nome", "tipo", "grupo", "cnpj_raiz"}


def test_cnpj_valido():
    assert stj.cnpj_valido("00000000000191") and stj.cnpj_valido("29.979.036/0001-40")
    assert not stj.cnpj_valido("00000000000000") and not stj.cnpj_valido("29979036000141") and not stj.cnpj_valido(None)


def test_tipificar_de_partes_py_substitui_o_preliminar(regras, classificador):
    vistos = []

    def tipificar(bruta, trib):
        vistos.append((trib, sorted(bruta)))
        return {"polo": "ativo", "papel": bruta["descTipoParte"], "nome": None, "tipo": "pj", "grupo": "g", "cnpj_raiz": None}

    p = next(iter(stj.ler_acervo(ACERVO, regras, classificador, tipificar=tipificar)))
    assert {x["grupo"] for x in p["partes"]} == {"g"}
    assert vistos[0] == ("stj", ["descTipoParte", "nomeParte", "numeroCNPJ"])  # advogados nunca vão


# ── atas, pautas, precedentes, íntegras ──────────────────────────────────────────────────────


def test_ata_formas_e_relatores(regras, classificador):
    ata = stj.ler_ata(PASTA / "ata20261119_amostra.json", regras, classificador)
    assert len(ata) == 20
    contagem: dict[tuple[str, str], int] = {}
    for e in ata:
        chave = (e["tipo"], e["relator_id"] or e["fora"])
        contagem[chave] = contagem.get(chave, 0) + 1
    assert contagem == {
        ("heranca", "stj-fatima-maranhao"): 4, ("registro", "presidencia"): 3, ("sorteio", "presidencia"): 2,
        ("sorteio", "stj-nancy-andrighi"): 2, ("sorteio", "stj-rogerio-schietti"): 2, ("registro", "vice"): 2,
        ("sorteio", "stj-luis-carlos-gambogi"): 1, ("sorteio", "stj-nilsoni-de-freitas"): 1,
        ("sorteio", "stj-sergio-kukina"): 1, ("registro", "cogepac"): 1, ("sorteio", "stj-joel-ilan-paciornik"): 1,
    }
    e = ata[0]
    assert (e["data"], e["instante"], e["forma"], e["tipo"], e["id"], e["relator"]) == (
        "2026-11-19", "2026-11-19T08:01:02", "Atribuição", "heranca", "stj-AREsp-2418503", "Fátima Maranhão")
    assert (e["area"], e["assunto"], e["destino"], e["segredo"]) == ("penal", 3614, "CONCLUSÃO AO MINISTRO RELATOR", None)
    par = [x for x in ata if x["registro"] == "202605036336"]
    assert [(x["forma"], x["fora"] or x["relator_id"]) for x in par] == [
        ("Redistribuição", "presidencia"), ("Redistribuição", "stj-joel-ilan-paciornik")]
    assert "PESSOA FÍSICA" not in json.dumps(ata, ensure_ascii=False)
    dia21 = stj.ler_ata(PASTA / "ata20261121.json", regras, classificador)
    assert len(dia21) == 11 and {e["data"] for e in dia21} == {"2026-11-21"}
    assert stj.tipo_da_forma("Redistribuição") == "sorteio" and stj.tipo_da_forma("Outra") == "outro"


def test_pautas(regras, classificador):
    itens = stj.ler_pautas(PASTA / "pautas_futuras_20261122_amostra.json.gz", regras, classificador)
    assert len(itens) == 16
    assert [i["sessao"] for i in itens] == sorted(i["sessao"] for i in itens)
    tipos: dict[tuple[str, bool], int] = {}
    for i in itens:
        tipos[(i["tipo"], i["interno"])] = tipos.get((i["tipo"], i["interno"]), 0) + 1
    assert tipos == {("AgInt", True): 5, ("principal", False): 4, ("AgRg", True): 3, ("EDcl", True): 2,
                     ("RCD", False): 1, ("ProRQF", False): 1}
    assert sum(i["segredo"] for i in itens) == 2
    vice = [i for i in itens if i["fora"] == "vice"]
    assert len(vice) == 3 and {i["orgao"] for i in vice} == {"CE"}
    i = itens[0]
    assert (i["sessao"], i["publicada"], i["orgao"], i["id"], i["tipo"], i["peticao"], i["relator_id"]) == (
        "2026-11-24", "2026-11-03", "T1", "stj-AREsp-3263892", "AgInt", "AGRAVO INTERNO", "stj-paulo-sergio-domingues")


@pytest.mark.parametrize("situacao, julgamento, estado", [
    ("Afetado", None, "pendente"), ("Em Julgamento", None, "pendente"), ("Pendente", None, "pendente"),
    ("Sobrestado", None, "pendente"), ("Admitido", None, "pendente"), ("Relevância reconhecida", None, "pendente"),
    ("Afetado - Possível Revisão de Tese", None, "pendente"), ("Em Afetação", None, "pendente"),
    ("Trânsito em Julgado", "2020-01-01", "julgado"), ("Acórdão Publicado - RE Pendente", "2020-01-01", "julgado"),
    ("Mérito Julgado", None, "julgado"), ("Revisado", None, "julgado"),
    ("Relevância reconhecida com reafirmação de jurisprudência", None, "julgado"),
    ("Cancelado", None, "encerrado"), ("Cancelada", None, "encerrado"), ("Vinculada a Tema", None, "encerrado"),
    ("Sem processo vinculado", None, "encerrado"), ("Prejudicada", None, "encerrado"),
    ("Suspensão indeferida", None, "encerrado"), ("", "2020-01-01", "julgado"), (None, None, "pendente"),
])
def test_estado_do_tema(situacao, julgamento, estado):
    assert stj.estado_do_tema(situacao, julgamento) == estado


def test_precedentes_temas_com_relator_e_situacao(regras, classificador):
    temas = stj.montar_temas(stj.ler_temas(PASTA / "temas.csv"), stj.ler_processos_precedentes(PASTA / "processos.csv"),
                             regras, classificador)
    por = {t["seq"]: t for t in temas}
    assert len(temas) == 15 - 1  # 14 precedentes; o 4781 vem em 3 linhas (uma por tema de RG)
    assert [t["tipo"] for t in temas][:5] == ["repetitivo"] * 5
    r = por[4781]
    assert (r["tipo"], r["estado"], r["rg_stf"], r["fora"], r["desde"]) == ("relevancia", "pendente", [852, 1209, 1450],
                                                                           "cogepac", "2026-10-15")
    t = por[3481]
    assert (t["numero"], t["estado"], t["relator_id"], t["processos"], t["leading"]) == (
        1126, "pendente", "stj-rogerio-schietti", 3, ["stj-REsp-1962736"])
    assert t["link"].endswith("cod_tema_inicial=1126&cod_tema_final=1126")
    beneti = por[555]  # tema afetado em 2009 ainda em nome de quem saiu do tribunal
    assert (beneti["relator"], beneti["relator_id"], beneti["fora"], beneti["estado"]) == ("SIDNEI BENETI", None, "residuos", "pendente")
    assert por[3516]["relator"] is None and por[3516]["tipo"] == "controversia"  # controvérsia: sem relator ainda
    assert por[3582]["estado"] == "encerrado" and por[2637]["tipo"] == "puil"
    assert all(t["titulo"] is None or len(t["titulo"]) <= stj.TITULO_MAX for t in temas)
    assert sum(t["pendente"] for t in temas) == 9


def test_precedentes_data_do_conteudo_separa_a_copia_parada():
    assert stj.data_do_conteudo(stj.ler_temas(PASTA / "temas.csv"), stj.CAMPOS_DATA_TEMAS) == "2026-11-19"
    assert stj.data_do_conteudo(stj.ler_temas(PASTA / "temas_copia_parada.csv"), stj.CAMPOS_DATA_TEMAS) == "2026-10-20"


def test_integras(regras, classificador):
    docs = stj.ler_integras(PASTA / "metadados20261015_amostra.json", regras, classificador, arquivo="2026-10-15")
    assert len(docs) == 42
    assert sum(d["documento"] == "decisao" for d in docs) == 27 and sum(d["documento"] == "acordao" for d in docs) == 15
    assert sum(d["interno"] for d in docs) == 21 and sum(d["recurso"] is None for d in docs) == 8
    d = docs[0]
    assert d == {
        "trib": "stj", "arquivo": "2026-10-15", "data": "2026-10-15", "seq": 404514025, "documento": "decisao",
        "id": "stj-HC-1136410", "classe": "HC", "numero": "1136410", "registro": "202604370086", "recurso": None,
        "interno": False, "teor": "Negando", "desfecho": "negando", "relator": "Joel Ilan Paciornik",
        "relator_id": "stj-joel-ilan-paciornik", "fora": None, "recebimento": "2026-10-07", "distribuicao": "2026-10-08",
        "assuntos": [3372, 5555], "area": "penal", "subarea": "penal.vida",
    }
    assert {d["fora"] for d in docs if d["relator_id"] is None} == {"residuos"}  # Og Fernandes
    assert "stj-luis-felipe-salomao" in {d["relator_id"] for d in docs}
    assert stj.desfecho("Não Conhecendo") == "nao_conhecendo" and stj.desfecho(None) is None


# ── a coleta (ColetorTribunais com fetch falso) ──────────────────────────────────────────────


class Relogio:
    def __init__(self) -> None:
        self.t = time.time()
        self.esperas: list[float] = []

    def __call__(self) -> float:
        return self.t

    def dormir(self, s: float) -> None:
        self.esperas.append(round(s, 2))
        self.t += s


class Fetch:
    """Responde pelo endereço (bytes, exceção ou lista: uma por chamada); o resto: 404."""

    def __init__(self, respostas: dict[str, Any]) -> None:
        self.respostas = {f"{PORTAL}/robots.txt": fx.ler("robots_dadosabertos_stj.txt").encode(), **respostas}
        self.urls: list[str] = []

    def __call__(self, url: str, **kw: Any) -> net.Response:
        self.urls.append(url)
        r = self.respostas.get(url)
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if r is None:
            raise FetchError(url, "HTTP 404", status=404)
        if isinstance(r, Exception):
            raise r
        return net.Response(url=url, status=200, content=r, headers={"content-type": "text/html; charset=utf-8"})


def _coletor(tmp_path: Path, fetch: Fetch, regras, relogio: Relogio | None = None) -> ColetorTribunais:
    relogio = relogio or Relogio()
    return ColetorTribunais(tmp_path, regras=regras, fetch=fetch, relogio=relogio, dormir=relogio.dormir, recusa_espera_s=0)


def _url(recursos: list[stj.Recurso], arquivo: str) -> str:
    return [r.url for r in recursos if r.arquivo == arquivo][-1]


def test_coleta_do_acervo_pela_pagina_com_pausa_e_sem_api(tmp_path, regras, classificador):
    pagina = fx.ler("ckan_dataset_acervo.html")
    url = _url(stj.links_da_pagina(pagina), "processos_tramitando_20261120.json.gz")
    fetch = Fetch({f"{PORTAL}/dataset/acervo-em-tramitacao": pagina.encode(), url: ACERVO.read_bytes()})
    relogio = Relogio()
    c = _coletor(tmp_path, fetch, regras, relogio)
    baixado = stj.baixar_acervo(c, regras)
    assert baixado.data == "2026-11-20" and baixado.caminho.read_bytes() == ACERVO.read_bytes()
    assert fetch.urls == [f"{PORTAL}/robots.txt", f"{PORTAL}/dataset/acervo-em-tramitacao", url]
    assert all("/api/" not in u for u in fetch.urls)
    assert relogio.esperas and min(relogio.esperas) >= 9.9  # Crawl-Delay: 10 entre os pedidos ao portal
    assert sum(1 for _ in stj.ler_acervo(baixado.caminho, regras, classificador)) == 44
    stj.baixar_acervo(c, regras)  # de novo: página e arquivo do cache, nada sai
    assert len(fetch.urls) == 3


def test_pagina_sem_link_e_formato_inesperado(tmp_path, regras):
    fetch = Fetch({f"{PORTAL}/dataset/pautas-futuras": b"<html><body>manuten\xc3\xa7\xc3\xa3o</body></html>"})
    with pytest.raises(stj.FormatoInesperado):
        stj.baixar_pautas(_coletor(tmp_path, fetch, regras), regras)


def test_serie_de_atas_com_maximo_e_retomada(tmp_path, regras):
    pagina = _html("pagina_atas.html")
    recursos = stj.links_da_pagina(pagina)
    corpo = (PASTA / "ata20261121.json").read_bytes()
    respostas = {f"{PORTAL}/dataset/atas-de-distribuicao": pagina.encode()}
    respostas.update({r.url: corpo for r in recursos if r.arquivo.startswith("ata")})
    fetch = Fetch(respostas)
    c = _coletor(tmp_path, fetch, regras)
    serie = stj.baixar_atas(c, regras, dias=5, maximo=2)
    assert [b.data for b in serie.baixados] == ["2026-11-21", "2026-11-22"]  # das mais novas para as mais velhas
    assert [r.data for r in serie.pendentes] == ["2026-11-18", "2026-11-19", "2026-11-20"]
    assert (serie.novos, serie.erro, serie.data_dados, serie.ultimo_publicado) == (2, None, "2026-11-22", "2026-11-22")
    antes = len(fetch.urls)
    serie = stj.baixar_atas(c, regras, dias=5, maximo=2)  # a próxima execução: as duas já baixadas vêm do cache
    assert [b.data for b in serie.baixados] == ["2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22"]
    assert serie.novos == 2 and len(fetch.urls) == antes + 2


def test_serie_parada_pela_recusa_guarda_o_que_baixou(tmp_path, regras):
    pagina = _html("pagina_atas.html")
    recursos = stj.links_da_pagina(pagina)
    corpo = (PASTA / "ata20261121.json").read_bytes()
    recusa = FetchError("", "HTTP 403", status=403)
    respostas = {f"{PORTAL}/dataset/atas-de-distribuicao": pagina.encode()}
    respostas.update({r.url: corpo for r in recursos if r.arquivo.startswith("ata")})
    respostas[_url(recursos, "ata20261120.json")] = recusa  # recusa duas vezes (a nova tentativa também)
    serie = stj.baixar_atas(_coletor(tmp_path, Fetch(respostas), regras), regras, dias=4)
    assert isinstance(serie.erro, RecusaDoHost)
    assert [b.data for b in serie.baixados] == ["2026-11-21", "2026-11-22"]
    assert [r.data for r in serie.pendentes] == ["2026-11-19", "2026-11-20"]


def test_integras_baixa_so_metadados_ate_o_ultimo_publicado(tmp_path, regras, classificador):
    pagina = _html("pagina_integras.html")
    recursos = stj.links_da_pagina(pagina)
    corpo = (PASTA / "metadados20261015_amostra.json").read_bytes()
    respostas = {f"{PORTAL}/dataset/integras-de-decisoes-terminativas-e-acordaos-do-diario-da-justica": pagina.encode()}
    respostas.update({r.url: corpo for r in recursos})
    fetch = Fetch(respostas)
    serie = stj.baixar_integras(_coletor(tmp_path, fetch, regras), regras, dias=3)
    assert [b.recurso.arquivo for b in serie.baixados] == ["metadados20261013.json", "metadados20261014.json",
                                                          "metadados20261015.json"]
    assert serie.baixados[0].recurso.url == _url(recursos, "metadados20261013.json")  # a última republicação
    assert serie.ultimo_publicado == "2026-10-15" and not any(u.endswith(".zip") for u in fetch.urls)
    assert len(stj.ler_integras(serie.baixados[-1].caminho, regras, classificador)) == 42


def test_precedentes_escolhe_o_de_conteudo_mais_recente(tmp_path, regras):
    pagina = _html("pagina_precedentes.html")
    recursos = stj.links_da_pagina(pagina)
    temas = stj.por_nome(recursos, "temas.csv")
    processos = stj.por_nome(recursos, "processos.csv")
    respostas = {
        f"{PORTAL}/dataset/precedentes-qualificados": pagina.encode(),
        temas[0].url: (PASTA / "temas.csv").read_bytes(),  # o recurso antigo, atualizado todo dia
        temas[1].url: (PASTA / "temas_copia_parada.csv").read_bytes(),  # a cópia de 13/11, que vem depois na página
        processos[0].url: (PASTA / "processos.csv").read_bytes(),
        processos[1].url: (PASTA / "processos.csv").read_bytes(),
    }
    escolhido = stj.baixar_precedentes(_coletor(tmp_path, Fetch(respostas), regras), regras)
    assert escolhido.temas.read_bytes() == (PASTA / "temas.csv").read_bytes()
    assert escolhido.data_dados == "2026-11-19" and escolhido.candidatos == {"temas.csv": 2, "processos.csv": 2}
