"""A cota com período (qijournal.congresso.cota.analisar, regras.Cota e a parte da cota em
qijournal.render.congresso): a série mensal, os pontos, o parlamentar do meio, o arquivo
mensal (cota-<casa>.json) e o estado padrão que a página mostra.

No molde de ``test_congresso_cota.py``: lançamentos feitos à mão, pequenos o bastante para
refazer a conta na cabeça. A legislatura começa em fev/2023 e "hoje" é 15/10/2023: os
meses fechados vão de fevereiro a setembro (8), e outubro, aberto, fica fora.

    quem      despesa por mês                                   total    meses  média
    Ana       DIVULGAÇÃO 1.000 (fev a jul) + LOCAÇÃO 600 (mar, abr)  7.200   6    1.200
    Bia       DIVULGAÇÃO 2.000 (fev a set); LOCAÇÃO +400 (fev), −400 (mar)  16.000  8  2.000
    Caio      TELEFONIA 3.000 (fev, mar, abr, jun, jul, ago)    18.000   6      3.000
    Davi      DIVULGAÇÃO 10.000 (fev a jul); fora do exercício  60.000   6      (não é ponto)
    Eva       DIVULGAÇÃO 100 (fev a jun): só 5 meses               500   5      (fora do gráfico)
    Fábio     DIVULGAÇÃO 4.000 (fev a jul)                      24.000   6      4.000
"""

from __future__ import annotations

import gzip
import json
import statistics
from pathlib import Path
from typing import Any

import pytest

from qijournal.config import Config, load_config
from qijournal.congresso import cota, dados, regras
from qijournal.render import congresso as pagina
from tests.fixtures.congresso import fabrica

RAIZ = Path(__file__).resolve().parents[1]
INICIO, HOJE = "2023-02-01", "2023-10-15"
MESES = ["2023-02", "2023-03", "2023-04", "2023-05", "2023-06", "2023-07", "2023-08", "2023-09"]
XIS, TELE, LOCA, AGENCIA = "11111111000191", "22222222000191", "33333333000191", "33937681000178"
SIGEPA = "PASSAGEM AÉREA - SIGEPA"

PESSOAS = {
    1: {"k": "cd-1", "nome": "Ana Lima", "partido": "PT", "uf": "SP", "ex": True},
    2: {"k": "cd-2", "nome": "Bia Reis", "partido": "PL", "uf": "RJ", "ex": True},
    3: {"k": "cd-3", "nome": "Caio Luz", "partido": "PP", "uf": "MG", "ex": True},
    4: {"k": "cd-4", "nome": "Davi Rocha", "partido": "MDB", "uf": "BA", "ex": False},  # fora do exercício
    5: {"k": "cd-5", "nome": "Eva Prado", "partido": "NOVO", "uf": "GO", "ex": True},  # só 5 meses de despesa
    6: {"k": "cd-6", "nome": "Fábio Sá", "partido": "PSD", "uf": "SP", "ex": True},
}

# As escolhas de apresentação: dois rótulos (a TELEFONIA fica sem, de propósito) e dois meses recentes.
BLOCO = {
    "meses_recentes": 2,
    "rotulos": {
        "cd": [
            {"oficial": "Divulgação.", "id": "divulgacao", "curto": "Divulgação do mandato"},  # casa sem acento, caixa e ponto
            {"oficial": "LOCAÇÃO", "id": "veiculos", "curto": "Aluguel de veículos"},
        ]
    },
}
ESCOLHAS = regras.de_dict({"cota": BLOCO}).cota
MARCOS = (
    regras.Marco("eleições (1º turno)", "2023-06-04"),
    regras.Marco("recesso de julho", "07-18", "07-31"),  # sem ano: não é data cheia, não vira tique
    regras.Marco("outra eleição", "2023-10-10"),  # cai no mês aberto: fora do eixo
)


def _d(pessoa: int, mes: int, valor: float, cnpj: str = XIS, *, categoria: str = "DIVULGAÇÃO", fornecedor: str = "XIS", ano: int = 2023, glosa: float = 0.0) -> cota.Despesa:
    return cota.Despesa(pessoa, ano, mes, categoria, fornecedor, cnpj, str(100 * pessoa + mes), f"{ano}-{mes:02d}-10", valor, glosa, "")


def _lancamentos() -> list[cota.Despesa]:
    ana = [
        *(_d(1, m, 1_000) for m in range(2, 8)),
        _d(1, 3, 600, LOCA, categoria="LOCAÇÃO", fornecedor="LOCA"),
        _d(1, 4, 600, LOCA, categoria="LOCAÇÃO", fornecedor="LOCA"),
        _d(1, 10, 9_999),  # outubro, o mês de hoje: fora de tudo o que é por mês
        _d(1, 1, 5_000),  # janeiro, antes da legislatura
        _d(1, 5, 300, AGENCIA, categoria=SIGEPA, fornecedor="LATAM"),  # passagem da agência: fora das contas
        _d(1, 7, -100, AGENCIA, categoria=SIGEPA, fornecedor="LATAM"),  # cancelamento: abate o total, não marca o mês
    ]
    bia = [
        *(_d(2, m, 2_000) for m in range(2, 10)),
        _d(2, 2, 400, LOCA, categoria="LOCAÇÃO", fornecedor="LOCA"),
        _d(2, 3, -400, LOCA, categoria="LOCAÇÃO", fornecedor="LOCA"),  # o estorno do mês seguinte
    ]
    caio = [_d(3, m, 3_000, TELE, categoria="TELEFONIA", fornecedor="TELE") for m in (2, 3, 4, 6, 7, 8)]  # maio zerado
    davi = [_d(4, m, 10_000) for m in range(2, 8)]
    eva = [_d(5, m, 100) for m in range(2, 7)]
    fabio = [_d(6, m, 4_000, glosa=50 if m == 2 else 0.0) for m in range(2, 8)]
    estranho = [_d(9, m, 1_000_000) for m in range(2, 8)]  # não está em PESSOAS: não conta em nada
    return ana + bia + caio + davi + eva + fabio + estranho


def _analisar(escolhas: regras.Cota | None = ESCOLHAS, casa: str = "cd") -> cota.Analise:
    return cota.analisar(_lancamentos(), casa=casa, inicio=INICIO, hoje=HOJE, pessoas=PESSOAS, escolhas=escolhas, marcos=MARCOS)


@pytest.fixture(scope="module")
def analise() -> cota.Analise:
    return _analisar()


def _celulas(registro: list[Any], n_despesas: int, n_meses: int) -> list[list[int]]:
    """Desfaz o formato compacto de uma pessoa do arquivo mensal: despesa × mês, em centavos
    (o que o script da página faz ao carregar)."""
    cubo = [[0] * n_meses for _ in range(n_despesas)]
    for serie in registro[5]:
        despesa, primeiro, *valores = serie
        for i, centavos in enumerate(valores):
            assert cubo[despesa][primeiro + i] == 0  # nenhuma célula aparece duas vezes
            cubo[despesa][primeiro + i] = centavos
    return cubo


def _textos(no: Any) -> list[str]:
    """Todos os textos de uma estrutura (para procurar o que não pode aparecer)."""
    if isinstance(no, str):
        return [no]
    if isinstance(no, dict):
        return [t for v in no.values() for t in _textos(v)]
    if isinstance(no, (list, tuple)):
        return [t for v in no for t in _textos(v)]
    return []


# ── o painel ─────────────────────────────────────────────────────────────────


def test_os_meses_somam_o_total_e_o_mes_aberto_fica_fora(analise: cota.Analise):
    painel = analise.painel
    assert (painel["desde"], painel["ate"], painel["aberto"]) == ("2023-02", "2023-09", "2023-10")
    assert painel["total"] == 125_700.0  # Ana 7.200 + Bia 16.000 + Caio 18.000 + Davi 60.000 + Eva 500 + Fábio 24.000
    assert painel["meses"] == [
        {"m": "2023-02", "total": 20_500.0, "n": 6},  # 1.000 + 2.400 + 3.000 + 10.000 + 100 + 4.000
        {"m": "2023-03", "total": 20_300.0, "n": 6},  # Ana 1.600; Bia 2.000 − 400
        {"m": "2023-04", "total": 20_700.0, "n": 6},
        {"m": "2023-05", "total": 17_100.0, "n": 5},  # sem o Caio
        {"m": "2023-06", "total": 20_100.0, "n": 6},
        {"m": "2023-07", "total": 20_000.0, "n": 5},  # sem a Eva
        {"m": "2023-08", "total": 5_000.0, "n": 2},
        {"m": "2023-09", "total": 2_000.0, "n": 1},
    ]
    assert round(sum(m["total"] for m in painel["meses"]), 2) == painel["total"]
    assert "2023-10" not in [m["m"] for m in painel["meses"]]  # o mês de hoje, ainda aberto


def test_os_meses_recentes_o_pico_e_as_datas_do_calendario(analise: cota.Analise):
    painel = analise.painel
    assert painel["recentes"] == 2  # agosto e setembro ainda recebem notas
    assert painel["pico"] == {"m": "2023-04", "total": 20_700.0}  # o maior mês fora os recentes
    # Só a data cheia que cai num mês fechado: o recesso (sem ano) e a data do mês aberto não entram.
    assert painel["marcos"] == [{"m": "2023-06", "d": "2023-06-04", "nome": "eleições (1º turno)"}]
    sem_folga = cota.analisar(_lancamentos(), casa="cd", inicio=INICIO, hoje=HOJE, pessoas=PESSOAS, escolhas=regras.de_dict({"cota": {"meses_recentes": 99}}).cota)
    assert sem_folga.painel["recentes"] == 8 and sem_folga.painel["pico"] is None  # todos os meses ainda recebem notas: nenhum rótulo


def test_os_pontos_sao_quem_esta_em_exercicio_com_seis_meses_ou_mais(analise: cota.Analise):
    painel = analise.painel
    todos = painel["todos"]
    # Da menor média para a maior. Davi (fora do exercício) e Eva (5 meses) não são pontos.
    assert [(p["k"], p["media"], p["total"], p["meses"], p["pos"]) for p in todos] == [
        ("cd-1", 1_200.0, 7_200.0, 6, 4),
        ("cd-2", 2_000.0, 16_000.0, 8, 3),
        ("cd-3", 3_000.0, 18_000.0, 6, 2),
        ("cd-6", 4_000.0, 24_000.0, 6, 1),  # o 1º é quem mais gasta, como no ranking do dossiê
    ]
    assert painel["n_pontos"] == len(todos) == 4
    assert all(p["meses"] >= cota.MINIMO_MESES for p in todos)
    assert painel["fora"] == [{"k": "cd-5", "nome": "Eva Prado", "partido": "NOVO", "uf": "GO", "media": 100.0, "total": 500.0, "meses": 5}]
    # As listas antigas continuam e dizem o mesmo que os pontos.
    assert [p["k"] for p in painel["menos_gastam"]] == [p["k"] for p in todos]
    assert [p["k"] for p in painel["mais_gastam"]] == [p["k"] for p in reversed(todos)]


def test_a_mediana_dos_pontos_e_a_mediana_do_painel(analise: cota.Analise):
    painel = analise.painel
    medias = [p["media"] for p in painel["todos"]]
    assert painel["mediana"] == statistics.median(medias) == 2_500.0
    # Quartis por interpolação na posição (n − 1)·p: 0,75 → 1.200 + 0,75 × 800; 2,25 → 3.000 + 0,25 × 1.000.
    assert (painel["q1"], painel["q3"]) == (1_800.0, 3_250.0)


def test_o_parlamentar_do_meio(analise: cota.Analise):
    meio = analise.painel["meio"]
    # Bia (2.000) e Caio (3.000) estão à mesma distância da mediana (2.500): vale a média menor.
    assert meio["k"] == "cd-2"
    assert meio["cats"] == [["divulgacao", 16_000.0, 100.0]]  # a LOCAÇÃO dela se anulou (+400 − 400)
    assert meio["serie"] == [2_400.0, 1_600.0, 2_000.0, 2_000.0, 2_000.0, 2_000.0, 2_000.0, 2_000.0]
    assert round(sum(meio["serie"]), 2) == 16_000.0
    assert meio["fornecedores"] == [{"nome": "XIS", "cnpj": XIS, "valor": 16_000.0, "pct": 100.0}]
    assert meio["glosa"] == {"valor": 0.0, "n": 0}


def test_cada_ponto_diz_a_maior_despesa_e_as_passagens_da_agencia(analise: cota.Analise):
    ana, bia, caio, _fabio = analise.painel["todos"]
    assert (ana["cat"], ana["pct_cat"]) == ("divulgacao", 83.3)  # 6.000 de 7.200
    assert (caio["cat"], caio["pct_cat"]) == ("telefonia", 100.0)
    assert ana["sigepa"] == {"2023": 200.0} and bia["sigepa"] == {}  # 300 do bilhete − 100 do cancelamento
    assert analise.painel["sigepa_ate"] == "2023-05"  # o último mês com bilhete (o cancelamento de julho não conta)
    senado = _analisar(casa="sf").painel
    assert "sigepa" not in senado["todos"][0] and "sigepa_ate" not in senado  # só a Câmara


def test_rotulos_ids_e_nomes_curtos_das_despesas(analise: cota.Analise):
    painel = analise.painel
    assert [(c["nome"], c["total"], c["pct"], c["id"], c["curto"]) for c in painel["categorias"]] == [
        ("DIVULGAÇÃO", 106_500.0, 84.7, "divulgacao", "Divulgação do mandato"),
        ("TELEFONIA", 18_000.0, 14.3, "telefonia", "Telefonia"),  # sem rótulo no arquivo: o id e o nome saem do oficial
        ("LOCAÇÃO", 1_200.0, 1.0, "veiculos", "Aluguel de veículos"),
    ]
    assert [(f["cnpj"], f["categoria"]) for f in painel["fornecedores"]] == [
        (XIS, "Divulgação do mandato"),
        (TELE, "Telefonia"),
        (LOCA, "Aluguel de veículos"),
    ]
    # Sem o bloco no arquivo de escolhas, tudo sai do nome oficial.
    sem = _analisar(escolhas=None).painel
    assert [(c["id"], c["curto"]) for c in sem["categorias"]] == [("divulgacao", "Divulgação"), ("telefonia", "Telefonia"), ("locacao", "Locação")]
    assert sem["recentes"] == 3  # o padrão


def test_a_contagem_inteira_de_um_fornecedor_so(analise: cota.Analise):
    # Ninguém pagou R$ 50 mil a uma empresa só entre os que estão em exercício: lista vazia, contagem zero.
    assert analise.painel["concentrados"] == [] and analise.painel["n_concentrados"] == 0


def test_o_que_ja_existia_no_painel_nao_muda_com_as_escolhas():
    com, sem = _analisar().painel, _analisar(escolhas=None).painel
    antigos = ("desde", "ate", "sem", "total", "pessoas", "mediana", "mais_gastam", "menos_gastam", "concentrados", "repetidas", "glosas")
    assert {k: com[k] for k in antigos} == {k: sem[k] for k in antigos}
    for lista in ("categorias", "fornecedores"):
        velhos = ("nome", "total", "pct") if lista == "categorias" else ("nome", "cnpj", "total", "gabinetes", "n")
        assert [{k: x[k] for k in velhos} for x in com[lista]] == [{k: x[k] for k in velhos} for x in sem[lista]]
    assert com["mais_gastam"][0] == {"k": "cd-6", "nome": "Fábio Sá", "partido": "PSD", "uf": "SP", "media": 4_000.0, "total": 24_000.0, "meses": 6, "sigepa_2024": 0.0}


# ── o arquivo mensal ─────────────────────────────────────────────────────────


def test_o_arquivo_mensal_no_formato_compacto(analise: cota.Analise):
    arquivo = analise.arquivo
    assert {k: v for k, v in arquivo.items() if k != "pessoas"} == {
        "v": cota.VERSAO_ARQUIVO,
        "casa": "cd",
        "meses": MESES,
        "recentes": 2,
        "aberto": "2023-10",
        "sem": cota.SEM["cd"],
        "sigepa_ate": "2023-05",
        "cats": [["divulgacao", "Divulgação do mandato", "DIVULGAÇÃO"], ["telefonia", "Telefonia", "TELEFONIA"], ["veiculos", "Aluguel de veículos", "LOCAÇÃO"]],
        "tetos": None,
    }
    # Em ordem de nome; quem tem lançamento em mês fechado, em exercício ou não (Davi e Eva entram).
    assert [p[:5] for p in arquivo["pessoas"]] == [
        ["cd-1", "Ana Lima", "PT", "SP", 1],
        ["cd-2", "Bia Reis", "PL", "RJ", 1],
        ["cd-3", "Caio Luz", "PP", "MG", 1],
        ["cd-4", "Davi Rocha", "MDB", "BA", 0],
        ["cd-5", "Eva Prado", "NOVO", "GO", 1],
        ["cd-6", "Fábio Sá", "PSD", "SP", 1],
    ]
    ana, bia, caio = arquivo["pessoas"][:3]
    # [índice da despesa, índice do primeiro mês, centavos de cada mês seguido…]
    assert ana[5] == [[0, 0, 100_000, 100_000, 100_000, 100_000, 100_000, 100_000], [2, 1, 60_000, 60_000]]
    assert ana[6] == {"2023": 20_000}  # as passagens da agência por ano, em centavos
    assert bia[5] == [[0, 0, *[200_000] * 8], [2, 0, 40_000, -40_000]] and bia[6] == {}  # o estorno entra negativo
    assert caio[5] == [[1, 0, 300_000, 300_000, 300_000], [1, 4, 300_000, 300_000, 300_000]]  # o zero de maio abre outra série
    assert all(isinstance(v, int) for p in arquivo["pessoas"] for serie in p[5] for v in serie)  # centavos inteiros


def test_o_arquivo_mensal_reconstroi_o_total_e_a_media_de_cada_pessoa(analise: cota.Analise):
    arquivo, painel, por_pessoa = analise.arquivo, analise.painel, analise.por_pessoa
    n_despesas, n_meses = len(arquivo["cats"]), len(arquivo["meses"])
    por_chave = {PESSOAS[pid]["k"]: resumo for pid, resumo in por_pessoa.items()}
    soma_geral = 0
    por_despesa, por_mes = [0] * n_despesas, [0] * n_meses
    for registro in arquivo["pessoas"]:
        cubo = _celulas(registro, n_despesas, n_meses)
        do_mes = [sum(cubo[d][m] for d in range(n_despesas)) for m in range(n_meses)]
        total, meses = sum(do_mes), sum(1 for v in do_mes if v > 0)  # mês só de estorno não conta
        leg = por_chave[registro[0]]["leg"]
        assert (round(total / 100, 2), meses) == (leg["total"], leg["meses"]), registro[0]
        assert round(total / meses / 100, 2) == leg["media"], registro[0]
        soma_geral += total
        for d in range(n_despesas):
            por_despesa[d] += sum(cubo[d])
        for m in range(n_meses):
            por_mes[m] += do_mes[m]
    assert soma_geral == 12_570_000 and soma_geral / 100 == painel["total"]  # em centavos, bate com o total do painel
    assert [v / 100 for v in por_despesa] == [c["total"] for c in painel["categorias"]]
    assert [v / 100 for v in por_mes] == [m["total"] for m in painel["meses"]]
    # Quem é ponto sai do próprio arquivo: em exercício e com 6 meses ou mais de despesa.
    pontos = []
    for registro in arquivo["pessoas"]:
        cubo = _celulas(registro, n_despesas, n_meses)
        meses = sum(1 for m in range(n_meses) if sum(cubo[d][m] for d in range(n_despesas)) > 0)
        if registro[4] and meses >= cota.MINIMO_MESES:
            pontos.append(registro[0])
    assert sorted(pontos) == sorted(p["k"] for p in painel["todos"])


def test_o_mes_aberto_nao_entra_no_arquivo_mensal(analise: cota.Analise):
    arquivo = analise.arquivo
    assert arquivo["aberto"] == "2023-10" and "2023-10" not in arquivo["meses"] and "2023-01" not in arquivo["meses"]
    ana = arquivo["pessoas"][0]
    # Os 9.999 de outubro (aberto) e os 5.000 de janeiro (antes da legislatura) não estão em célula nenhuma.
    assert sum(v for serie in ana[5] for v in serie[2:]) == 720_000
    assert analise.por_pessoa[1]["anos"] == {"2023": 22_199.0}  # no total do ano da ficha, como sempre, eles contam


def test_serializar_poe_uma_pessoa_por_linha_e_e_json(analise: cota.Analise):
    texto = cota.serializar(analise.arquivo)
    assert json.loads(texto) == analise.arquivo
    assert texto.count("\n") == len(analise.arquivo["pessoas"]) + 2 and texto.endswith("]}\n")
    assert '"cd-2","Bia Reis","PL","RJ",1,' in texto  # sem espaços


def test_o_arquivo_de_uma_casa_inteira_cabe_no_orcamento():
    """650 pessoas com 6 despesas em todos os 45 meses: 175 mil células, 40% a mais do que a
    Câmara tinha em nov/2026 (123 mil), e com valores que não se repetem (o pior caso para o
    gzip; no registro, aluguéis e assinaturas se repetem). Fica abaixo de 1,8 MB, e de 700 KB
    com gzip."""
    pessoas = {pid: {"k": f"cd-{pid}", "nome": f"Pessoa {pid:03d}", "partido": "PT", "uf": "SP", "ex": True} for pid in range(1, 651)}
    despesas = [
        cota.Despesa(pid, 2023 + (m + 1) // 12, (m + 1) % 12 + 1, f"DESPESA {d}", "F", XIS, "1", "", ((pid * 7919 + m * 104729 + d * 1299709) % 1_500_000) / 100 + 1, 0.0, "")
        for pid in pessoas
        for m in range(45)
        for d in range(6)
    ]
    arquivo = cota.analisar(despesas, casa="cd", inicio=INICIO, hoje="2026-11-15", pessoas=pessoas).arquivo
    assert len(arquivo["meses"]) == 45 and sum(len(serie) - 2 for p in arquivo["pessoas"] for serie in p[5]) == 650 * 45 * 6
    texto = cota.serializar(arquivo).encode("utf-8")
    assert len(texto) < 1_800_000 and len(gzip.compress(texto, 9)) < 700_000


# ── o teto: nulo não gera nada; uma tabela (fictícia, só de teste) funciona ──

TABELA_FICTICIA = {  # valores inventados para o teste: NÃO são os da cota
    "fonte": "tabela fictícia de teste",
    "url": "https://exemplo.invalid/tabela",
    "vigencias": [
        {"desde": "2023-02", "valores": {uf: 2_000 for uf in regras.UFS} | {"SP": 3_000, "RJ": 2_500, "MG": 4_000}},
        {"desde": "2023-06", "valores": {uf: 2_000 for uf in regras.UFS} | {"SP": 3_600, "RJ": 2_500, "MG": 4_000}},  # SP muda em junho
    ],
}


def _com_tabela() -> regras.Cota:
    return regras.de_dict({"cota": BLOCO | {"tetos": {"cd": TABELA_FICTICIA, "sf": None}}}).cota


def test_teto_nulo_nao_gera_nada(analise: cota.Analise):
    painel, arquivo = analise.painel, analise.arquivo
    assert painel["tetos"] is None and arquivo["tetos"] is None
    assert all(p["teto"] is None and p["pct_teto"] is None for p in painel["todos"])
    vista = pagina._cota({**painel, "sem_despesa": []}, "cd")
    assert vista is not None and vista["painel"] is not None
    pn = vista["painel"]
    assert pn["tetos"] is None and pn["quem"]["teto"] is None and pn["numeros"]["quem"]["teto"] == "" and pn["cartao"]["teto"] == ""
    assert pn["tabela"]["teto"] is False and all(x["teto"] == "" and x["pct_teto"] == "" for x in pn["tabela"]["linhas"])
    # Nenhum texto preparado para a tela fala em teto.
    assert not [t for t in _textos(vista) if "teto" in t.lower()]
    assert "O limite mensal muda por estado e não entra nesta conta." in pn["textos"]["como"]


def test_teto_ficticio_injetado_funciona():
    analise = _analisar(escolhas=_com_tabela())
    painel = analise.painel
    assert painel["tetos"] == {
        "fonte": "tabela fictícia de teste",
        "url": "https://exemplo.invalid/tabela",
        "desde": "2023-06",  # a vigência que vale no último mês fechado
        "min": {"uf": "AC", "valor": 2_000.0},  # no empate, a primeira em ordem alfabética
        "max": {"uf": "MG", "valor": 4_000.0},
        "ufs": {uf: 2_000.0 for uf in regras.UFS} | {"SP": 3_600.0, "RJ": 2_500.0, "MG": 4_000.0},
    }
    ana, bia, caio, fabio = painel["todos"]
    # Ana (SP), despesa de fev a jul: 4 meses a 3.000 + 2 meses a 3.600 = 19.200 de limite; gastou 7.200.
    assert (ana["teto"], ana["pct_teto"]) == (3_600.0, 37.5)
    assert (bia["teto"], bia["pct_teto"]) == (2_500.0, 80.0)  # RJ: 8 meses × 2.500 = 20.000; gastou 16.000
    assert (caio["teto"], caio["pct_teto"]) == (4_000.0, 75.0)  # MG: 6 × 4.000 = 24.000; gastou 18.000
    assert (fabio["teto"], fabio["pct_teto"]) == (3_600.0, 125.0)  # SP: 19.200 de limite; gastou 24.000
    assert analise.arquivo["tetos"] == {
        "fonte": "tabela fictícia de teste",
        "url": "https://exemplo.invalid/tabela",
        "vig": [
            ["2023-02", {uf: 200_000 for uf in regras.UFS} | {"SP": 300_000, "RJ": 250_000, "MG": 400_000}],  # centavos por mês
            ["2023-06", {uf: 200_000 for uf in regras.UFS} | {"SP": 360_000, "RJ": 250_000, "MG": 400_000}],
        ],
    }
    # Nada do que já existia muda por haver tabela.
    sem = _analisar().painel
    assert {k: v for k, v in painel.items() if k not in ("tetos", "todos")} == {k: v for k, v in sem.items() if k not in ("tetos", "todos")}
    assert [{k: v for k, v in p.items() if k not in ("teto", "pct_teto")} for p in painel["todos"]] == [
        {k: v for k, v in p.items() if k not in ("teto", "pct_teto")} for p in sem["todos"]
    ]

    pn = pagina._cota({**painel, "sem_despesa": []}, "cd")["painel"]
    assert pn["numeros"]["quem"]["teto"] == "teto: de R$ 2.000 (AC) a R$ 4.000 (MG), conforme o estado"
    assert pn["quem"]["teto"] == {"x1": 3.636, "x2": 7.273, "rot": "teto, conforme o estado: de R$ 2.000 (AC) a R$ 4.000 (MG)"}  # em % do eixo de R$ 55 mil
    assert pn["cartao"]["teto"] == "80% do teto do RJ (R$ 2.500 por mês)"  # o cartão é o da Bia, a do meio
    linhas = {x["k"]: x for x in pn["tabela"]["linhas"]}
    assert pn["tabela"]["teto"] is True and (linhas["cd-6"]["pct_teto"], linhas["cd-6"]["teto"]) == ("125%", "125% do teto de SP")
    assert linhas["cd-2"]["extra"] == "R$ 16.000 em 8 de 8 meses · 80% do teto do RJ"
    assert pn["tetos"]["fonte"] == "tabela fictícia de teste" and "tabela fictícia de teste" in pn["textos"]["como"]
    assert "Quatro medidas" in pn["textos"]["como"] and "% do teto do estado" in pn["textos"]["como"]


def test_tabela_de_teto_pela_metade_fica_desligada_e_vira_aviso():
    sem_sp = {"fonte": "tabela fictícia", "url": "https://exemplo.invalid/t", "vigencias": [{"desde": "2023-02", "valores": {uf: 2_000 for uf in regras.UFS if uf != "SP"}}]}
    escolhas = regras.de_dict({"cota": {"tetos": {"cd": sem_sp}}}).cota
    assert escolhas.tetos == {} and escolhas.teto("cd") is None
    assert len(escolhas.avisos) == 1 and "cota.tetos.cd" in escolhas.avisos[0] and "SP" in escolhas.avisos[0]
    assert _analisar(escolhas=escolhas).painel["tetos"] is None
    sem_fonte = dict(TABELA_FICTICIA, fonte="")
    assert regras.de_dict({"cota": {"tetos": {"cd": sem_fonte}}}).cota.tetos == {}  # sem dizer de onde saiu, não vale
    zerada = dict(TABELA_FICTICIA, vigencias=[{"desde": "2023-02", "valores": {uf: 0 for uf in regras.UFS}}])
    assert regras.de_dict({"cota": {"tetos": {"cd": zerada}}}).cota.tetos == {}  # valor zero não é valor


def test_tabela_que_comeca_no_meio_da_janela_nao_inventa_percentual():
    # A tabela só vale a partir de maio: quem teve despesa antes disso fica sem a parte do limite
    # (dividir só pelos meses com limite daria um percentual maior do que o verdadeiro).
    tardia = dict(TABELA_FICTICIA, vigencias=[{"desde": "2023-05", "valores": {uf: 2_000 for uf in regras.UFS}}])
    painel = _analisar(escolhas=regras.de_dict({"cota": {"tetos": {"cd": tardia}}}).cota).painel
    assert painel["tetos"]["desde"] == "2023-05"
    assert [(p["teto"], p["pct_teto"]) for p in painel["todos"]] == [(2_000.0, None)] * 4
    # E a que só começa depois do último mês fechado ainda não aparece em lugar nenhum do painel.
    futura = dict(TABELA_FICTICIA, vigencias=[{"desde": "2023-10", "valores": {uf: 2_000 for uf in regras.UFS}}])
    painel = _analisar(escolhas=regras.de_dict({"cota": {"tetos": {"cd": futura}}}).cota).painel
    assert painel["tetos"] is None and all(p["teto"] is None and p["pct_teto"] is None for p in painel["todos"])


def test_o_limite_de_cada_mes_segue_a_vigencia():
    tabela = _com_tabela().teto("cd")
    assert tabela is not None
    assert (tabela.em("SP", "2023-05"), tabela.em("SP", "2023-06"), tabela.em("SP", "2026-01")) == (300_000, 360_000, 360_000)
    assert tabela.em("SP", "2023-01") is None and tabela.em("XX", "2023-06") is None  # antes da primeira vigência; UF desconhecida


# ── o arquivo de escolhas ────────────────────────────────────────────────────


def test_sem_o_bloco_da_cota_valem_os_padroes():
    padrao = regras.de_dict({}).cota
    assert (padrao.meses_recentes, padrao.rotulos, padrao.tetos, padrao.avisos) == (3, {}, {}, ())
    assert padrao.rotulo("cd", "HOSPEDAGEM ,EXCETO DO PARLAMENTAR NO DISTRITO FEDERAL.") == (
        "hospedagem-exceto-do-parlamentar-no-distrito-federal",
        "Hospedagem, exceto do parlamentar no distrito federal",
    )
    with pytest.raises(ValueError, match="id repetido"):
        regras.de_dict({"cota": {"rotulos": {"cd": [{"oficial": "A", "id": "x"}, {"oficial": "B", "id": "x"}]}}})


def test_o_arquivo_de_escolhas_da_casa_tem_os_rotulos_das_duas_casas():
    escolhas = regras.carregar(RAIZ).cota
    assert escolhas.meses_recentes == 3 and escolhas.avisos == ()
    assert len(escolhas.rotulos["cd"]) == 21 and len(escolhas.rotulos["sf"]) == 7
    assert escolhas.rotulo("cd", "DIVULGAÇÃO DA ATIVIDADE PARLAMENTAR.") == ("divulgacao", "Divulgação do mandato")
    assert escolhas.rotulo("cd", "HOSPEDAGEM ,EXCETO DO PARLAMENTAR NO DISTRITO FEDERAL.") == ("hospedagem", "Hospedagem")
    assert escolhas.rotulo("sf", "Passagens aéreas, aquáticas e terrestres nacionais") == ("passagens", "Passagens")
    assert escolhas.rotulo("sf", "Divulgação da atividade parlamentar") == ("divulgacao", "Divulgação do mandato")
    for casa in ("cd", "sf"):
        ids = [r.id for r in escolhas.rotulos[casa]]
        assert len(set(ids)) == len(ids)  # o id vai no endereço: não pode repetir


# ── o estado padrão que a página mostra ──────────────────────────────────────


@pytest.fixture(scope="module")
def vista(analise: cota.Analise) -> dict[str, Any]:
    v = pagina._cota({**analise.painel, "sem_despesa": []}, "cd")
    assert v is not None
    return v


def test_a_vista_antiga_continua_inteira(vista: dict[str, Any]):
    # O que o modelo de hoje usa não muda de nome nem de formato.
    assert (vista["desde"], vista["ate"], vista["total"], vista["pessoas"], vista["mediana"], vista["quem"]) == ("fev/2023", "set/2023", "R$ 125.700", "6", "R$ 2.500", "deputados")
    assert vista["mais"][0] == {"k": "cd-6", "nome": "Fábio Sá", "legenda": "PSD-SP", "valor": "R$ 4.000", "extra": "R$ 24.000 em 6 meses"}
    assert vista["categorias"][0] == {"nome": "DIVULGAÇÃO", "valor": "R$ 106.500", "pct": "85%", "w": 100.0}
    assert vista["fornecedores"][0]["categoria"] == "Divulgação do mandato"  # a coluna nova da tabela


def test_a_frase_da_janela_e_os_tres_numeros(vista: dict[str, Any]):
    pn = vista["painel"]
    assert pn["janela"] == {
        "de": "2023-02", "ate": "2023-09", "n": 8, "curta": "fev a set/2023", "texto": "fev a set/2023 (8 meses fechados)",
        "aberto": "out/2023", "recentes": 2, "recentes_texto": "os 2 últimos meses ainda recebem notas",
    }  # fmt: skip
    assert pn["frase"] == ["Câmara", "fev a set/2023 (8 meses fechados)", "todas as despesas", "todos os deputados"]
    numeros = pn["numeros"]
    assert (numeros["quanto"]["n"], numeros["quanto"]["linhas"]) == ("R$ 125,7 mil", ["reembolsados em 8 meses fechados", "R$ 15,7 mil por mês"])  # 125.700 ÷ 8 = 15.712,50
    assert "Entram os 6 deputados que tiveram despesa" in numeros["quanto"]["dica"]
    assert (numeros["quem"]["n"], numeros["quem"]["un"], numeros["quem"]["k"]) == ("R$ 2.500", "por mês", "cd-2")
    assert numeros["quem"]["linhas"] == ["gasta o deputado do meio", "metade fica entre R$ 1.800 e R$ 3.250"]
    assert numeros["com_o_que"] == {"n": "85%", "linhas": ["vai para divulgação do mandato", "R$ 106,5 mil"], "cat": "divulgacao", "curto": "Divulgação do mandato"}


def test_as_colunas_de_quando(vista: dict[str, Any]):
    quando = vista["painel"]["quando"]
    colunas = quando["colunas"]
    assert [c["m"] for c in colunas] == MESES
    assert round(sum(c["v"] for c in colunas), 2) == 125_700.0  # as colunas somam o número 1
    assert (quando["unidade"], quando["divisor"]) == ("R$ mil por mês", 1_000.0)
    assert [(m["rot"], m["y"]) for m in quando["marcas"]] == [("0", 0.0), ("10", 44.73), ("20", 89.46)]  # o topo é o maior mês + 8%: 22.356
    abril = colunas[2]
    assert (abril["rot"], abril["valor"], abril["n"], abril["pct"], abril["h"], abril["y"]) == ("abr/2023", "R$ 20,7 mil", 6, "16,5%", 92.59, 0.0)
    assert abril["dica"] == "abr/2023 · R$ 20,7 mil · 6 deputados com despesa · 16,5% do período"
    assert (abril["x"], abril["w"], abril["cx"]) == (26.75, 9.0, 31.25)  # passo de 12,5%; a coluna ocupa 72% dele
    assert [c["recente"] for c in colunas] == [False] * 6 + [True, True]
    assert [c["ano"] for c in colunas] == ["fev/2023", "", "", "", "", "", "", ""]
    assert quando["pico"] == {"m": "2023-04", "cx": 31.25, "y": 92.59, "texto": "abr/2023: R$ 20,7 mil"}
    assert quando["recentes"] == {"n": 2, "x": 75.0, "w": 25.0, "rot": "ainda recebem notas"}
    assert quando["marcos"] == [{"m": "2023-06", "cx": 56.25, "rot": "eleição", "titulo": "eleições (1º turno), 04/06/2023"}]
    assert quando["tabela"][0] == {"m": "2023-02", "mes": "fev/2023", "valor": "R$ 20.500", "n": "6", "recente": False}
    assert quando["titulo"] == "quanto a Câmara reembolsou em cada mês"


def test_as_barras_de_com_o_que(vista: dict[str, Any]):
    barras = vista["painel"]["com_o_que"]
    assert barras["janela"] == "parte do total, de fev a set/2023"
    assert [(b["id"], b["curto"], b["texto"], b["w"]) for b in barras["linhas"]] == [
        ("divulgacao", "Divulgação do mandato", "85% · R$ 106,5 mil", 84.73),  # a barra cheia é 100% do total
        ("telefonia", "Telefonia", "14% · R$ 18,0 mil", 14.32),
        ("veiculos", "Aluguel de veículos", "1% · R$ 1.200", 0.95),
    ]
    assert barras["outras"] is None  # com sete despesas ou menos, todas à vista
    assert vista["painel"]["textos"]["fora"] == f"Fora desta conta: {cota.SEM['cd']}."


def test_mais_de_sete_despesas_viram_seis_e_outras():
    despesas = [_d(1, m, 1_000 * (9 - d), categoria=f"DESPESA {d}") for m in range(2, 8) for d in range(9)]
    despesas += [_d(1, 2, -50, categoria="DEVOLVIDA")]  # saldo negativo: mais devolução do que despesa
    painel = cota.analisar(despesas, casa="cd", inicio=INICIO, hoje=HOJE, pessoas=PESSOAS).painel
    barras = pagina._cota({**painel, "sem_despesa": []}, "cd")["painel"]["com_o_que"]
    assert [b["oficial"] for b in barras["linhas"]] == [f"DESPESA {d}" for d in range(6)]  # as seis maiores, na ordem da Casa
    outras = barras["outras"]
    # DESPESA 6, 7 e 8 (18.000 + 12.000 + 6.000) − 50 devolvidos = 35.950 de 269.950.
    assert (outras["n"], outras["rot"], outras["texto"]) == (4, "Outras 4 despesas", "13% · R$ 36,0 mil")
    devolvida = outras["linhas"][-1]
    assert (devolvida["neg"], devolvida["w"], devolvida["pct"], devolvida["texto"]) == (True, 0.0, "", "−R$ 50,00 (devoluções)")


def test_os_pontos_de_quem(vista: dict[str, Any]):
    quem = vista["painel"]["quem"]
    assert (quem["n"], quem["faixa"], quem["eixo"], quem["pilha"]) == (4, 500, 55_000, 1)
    # Faixas de R$ 500 num eixo de R$ 55 mil: 1.200 cai na 3ª faixa (1.000 a 1.500), centro em 1.250 → 2,273%.
    assert [(p["p"], p["v"], p["x"], p["n"], p["foco"]) for p in quem["pontos"]] == [
        ("cd-1", 1_200.0, 2.273, 0, False),
        ("cd-2", 2_000.0, 4.091, 0, True),  # o do meio começa em foco
        ("cd-3", 3_000.0, 5.909, 0, False),
        ("cd-6", 4_000.0, 7.727, 0, False),
    ]
    assert [(p["x2"], p["n2"]) for p in quem["pontos"]] == [(2.727, 0), (4.545, 0), (6.364, 0), (8.182, 0)]  # com a faixa dobrada (tela estreita)
    assert quem["mediana"] == {"v": 2_500.0, "x": 4.545, "rot": "mediana R$ 2.500"}
    assert quem["metade"] == {"x1": 3.273, "x2": 5.909, "rot": "metade dos deputados", "de": "R$ 1.800", "a": "R$ 3.250"}
    assert [m["rot"] for m in quem["marcas"]] == ["0", "10", "20", "30", "40", "50 mil"]
    assert quem["fora"] == {
        "n": 1,
        "texto": "1 deputado em exercício fora do gráfico: despesa em menos de 6 meses do período.",
        "nomes": [{"k": "cd-5", "nome": "Eva Prado", "legenda": "NOVO-GO"}],
    }
    assert quem["titulo"] == "cada ponto é um deputado em exercício · R$ por mês com despesa, de fev a set/2023"


def test_os_pontos_se_empilham_na_mesma_faixa_e_o_eixo_estica():
    # Três na faixa de R$ 1.000 a R$ 1.500 e um acima do eixo padrão (R$ 56.200 por mês).
    despesas = [_d(pid, m, valor) for pid, valor in ((1, 1_100), (2, 1_200), (3, 1_300), (6, 56_200)) for m in range(2, 8)]
    painel = cota.analisar(despesas, casa="cd", inicio=INICIO, hoje=HOJE, pessoas=PESSOAS).painel
    quem = pagina._cota({**painel, "sem_despesa": []}, "cd")["painel"]["quem"]
    assert quem["eixo"] == 60_000 and quem["pilha"] == 3 and quem["altura"] == 3 * pagina.COTA_PILHA_PX + 34
    assert [(p["p"], p["n"], p["y"]) for p in quem["pontos"]] == [("cd-1", 0, 0), ("cd-2", 1, 7), ("cd-3", 2, 14), ("cd-6", 0, 0)]
    assert quem["pontos"][0]["x"] == quem["pontos"][2]["x"] == 2.083  # a mesma faixa: centro em 1.250 de 60.000


def test_o_cartao_do_parlamentar_do_meio(vista: dict[str, Any]):
    cartao = vista["painel"]["cartao"]
    assert (cartao["k"], cartao["nome"], cartao["legenda"], cartao["origem"]) == ("cd-2", "Bia Reis", "PL-RJ", "o deputado do meio")
    assert (cartao["pos"], cartao["pos_uf"]) == ("3º de 4", "1º de 1 no RJ")
    # Os três números sempre juntos, cada um com a unidade.
    assert (cartao["valor"], cartao["un"], cartao["resto"]) == ("R$ 2.000", "por mês com despesa", "R$ 16.000 no período, em 8 de 8 meses")
    assert cartao["antes"] == [
        "100% em uma despesa só: divulgação do mandato.",
        "100% para um fornecedor só, na legislatura inteira: XIS.",
    ]
    assert cartao["cats"] == [
        {"id": "divulgacao", "curto": "Divulgação do mandato", "v": 16_000.0, "valor": "R$ 16.000", "pct": "100%", "w": 100.0, "casa": "85%", "casa_w": 84.7}
    ]
    assert cartao["outras"] is None
    serie = cartao["serie"]
    assert [c["h"] for c in serie["colunas"]] == [100.0, 66.7, 83.3, 83.3, 83.3, 83.3, 83.3, 83.3]  # o maior mês (fev, 2.400) é a altura cheia
    assert serie["aria"] == "Despesa mês a mês, de fev/2023 a set/2023; maior em fev/2023, R$ 2.400."
    assert cartao["fornecedores"] == [{"nome": "XIS", "valor": "R$ 16.000", "pct": "100%"}]
    assert cartao["glosa"] == ""  # a Casa não recusou nada dela


def test_antes_de_comparar_fora_da_conta_uma_despesa_e_um_fornecedor():
    """Um deputado só, com despesa em todos os meses: 9.990 em ESCRITÓRIO e 10 em TELEFONIA;
    5.100 para um fornecedor (51%); passagens da agência em março e maio."""
    despesas = [
        *(_d(1, m, 850, XIS, categoria="ESCRITÓRIO", fornecedor="IMOBILIÁRIA") for m in range(2, 8)),  # 5.100
        *(_d(1, m, 815, LOCA, categoria="ESCRITÓRIO", fornecedor="GRÁFICA", glosa=20 if m < 4 else 0.0) for m in range(2, 8)),  # 4.890
        _d(1, 2, 10, TELE, categoria="TELEFONIA", fornecedor="TELE"),
        _d(1, 3, 1_000, AGENCIA, categoria=SIGEPA, fornecedor="LATAM"),
        _d(1, 5, 2_000, AGENCIA, categoria=SIGEPA, fornecedor="LATAM"),
    ]
    painel = cota.analisar(despesas, casa="cd", inicio=INICIO, hoje="2023-08-15", pessoas=PESSOAS).painel
    pn = pagina._cota({**painel, "sem_despesa": []}, "cd")["painel"]
    cartao = pn["cartao"]
    assert cartao["resto"] == "R$ 10.000 no período, em 6 de 6 meses"
    assert cartao["antes"] == [
        "Fora desta conta: R$ 3.000 em passagens compradas pela agência da Câmara em 2023, até maio.",
        "99,9% em uma despesa só: escritório.",
        "51% para um fornecedor só, na legislatura inteira: IMOBILIÁRIA.",
    ]
    assert cartao["glosa"] == "A Casa recusou R$ 40,00 em 2 notas, na legislatura inteira."
    assert pn["numeros"]["quem"]["linhas"] == ["1 deputado"]  # um ponto só: não há "do meio" nem faixa


def test_antes_de_comparar_meses_e_nada_nas_duas_maiores_despesas():
    despesas = [
        # Na Casa: DIVULGAÇÃO 256.000, LOCAÇÃO 56.000, HOSPEDAGEM e TELEFONIA 36.000 cada.
        *(_d(1, m, 2_000, categoria="DIVULGAÇÃO") for m in range(2, 10)),
        *(_d(1, m, 7_000, LOCA, categoria="LOCAÇÃO", fornecedor="LOCA") for m in range(2, 10)),
        *(_d(3, m, 30_000, categoria="DIVULGAÇÃO") for m in range(2, 10)),
        # Bia, a do meio: metade em TELEFONIA, metade em HOSPEDAGEM, para pessoa física, em 6 dos 8 meses.
        *(_d(2, m, 6_000, "", categoria="TELEFONIA", fornecedor="FULANO") for m in range(2, 8)),
        *(_d(2, m, 6_000, "", categoria="HOSPEDAGEM", fornecedor="BELTRANO") for m in range(2, 8)),
    ]
    painel = cota.analisar(despesas, casa="cd", inicio=INICIO, hoje=HOJE, pessoas=PESSOAS).painel
    assert painel["meio"]["k"] == "cd-2"  # 9.000, 12.000 e 30.000 por mês
    cartao = pagina._cota({**painel, "sem_despesa": []}, "cd")["painel"]["cartao"]
    assert cartao["antes"] == [
        "Teve despesa em 6 dos 8 meses do período.",
        "Nada em divulgação nem em locação, as duas maiores despesas da Casa.",
    ]  # pessoa física não entra em "um fornecedor só" (nem o nome dela aparece)
    assert "FULANO" not in json.dumps(painel, ensure_ascii=False)


def test_fora_da_conta_na_lista_e_no_cartao():
    # Os valores do registro de 15/11/2026: Kim Kataguiri e Marcio Alvino.
    kim = {"sigepa": {"2023": 261_646.77, "2024": 134_337.39, "2025": 60_646.63}}
    assert pagina._cota_fora_da_conta(kim, "2025-07", curta=False) == (
        "Fora desta conta: R$ 456.631 em passagens compradas pela agência da Câmara (R$ 261.647 em 2023, R$ 134.337 em 2024, R$ 60.647 em 2025, até julho)."
    )
    assert pagina._cota_fora_da_conta({"sigepa": {"2024": 15_794.19}}, "2025-07", curta=True) == "fora da conta: R$ 15.794 em passagens da agência em 2024"
    assert pagina._cota_fora_da_conta({"sigepa": {"2023": 500.0}}, "2025-07", curta=True) == ""  # na lista, só o último ano inteiro
    assert pagina._cota_fora_da_conta({"sigepa": {}}, "2025-07", curta=False) == "" == pagina._cota_fora_da_conta({}, "", curta=False)


def test_as_pontas_e_a_tabela_dizem_sempre_a_unidade_o_total_e_os_meses(vista: dict[str, Any]):
    pn = vista["painel"]
    pontas, tabela = pn["pontas"], pn["tabela"]
    assert pontas["titulo"] == "R$ por mês com despesa, de fev a set/2023"
    assert pontas["uma"] is True and pontas["menos"] == []  # 20 pontos ou menos: uma lista só, com todos
    assert [(x["pos"], x["k"], x["valor"], x["un"], x["extra"]) for x in pontas["mais"]] == [
        (1, "cd-6", "R$ 4.000", "por mês", "R$ 24.000 em 6 de 8 meses"),
        (2, "cd-3", "R$ 3.000", "por mês", "R$ 18.000 em 6 de 8 meses"),
        (3, "cd-2", "R$ 2.000", "por mês", "R$ 16.000 em 8 de 8 meses"),
        (4, "cd-1", "R$ 1.200", "por mês", "R$ 7.200 em 6 de 8 meses"),
    ]
    assert tabela["titulo"] == "Todos os 4, em tabela" and tabela["linhas"] == pontas["mais"]
    ana = tabela["linhas"][3]
    assert (ana["p"], ana["nome"], ana["legenda"], ana["meses"], ana["total"], ana["maior"], ana["foco"]) == (
        "cd-1", "Ana Lima", "PT-SP", "6 de 8", "R$ 7.200", "Divulgação do mandato · 83%", False,
    )  # fmt: skip
    assert tabela["linhas"][2]["foco"] is True  # a linha do parlamentar do meio
    # Nenhuma linha leva o valor sozinho: há sempre "por mês", o total e "N de M meses".
    assert all(x["un"] == "por mês" and " em " in x["extra"] and " de 8 meses" in x["extra"] for x in tabela["linhas"])


def test_com_mais_de_vinte_pontos_as_pontas_sao_duas_listas_de_dez():
    pessoas = {pid: {"k": f"cd-{pid}", "nome": f"Pessoa {pid:02d}", "partido": "PT", "uf": "SP", "ex": True} for pid in range(1, 26)}
    despesas = [cota.Despesa(pid, 2023, m, "DIVULGAÇÃO", "XIS", XIS, "1", "", 1_000.0 * pid, 0.0, "") for pid in pessoas for m in range(2, 8)]
    painel = cota.analisar(despesas, casa="sf", inicio=INICIO, hoje=HOJE, pessoas=pessoas).painel
    pn = pagina._cota({**painel, "sem_despesa": []}, "sf")["painel"]
    pontas = pn["pontas"]
    assert pontas["uma"] is False
    assert [x["k"] for x in pontas["mais"]] == [f"cd-{pid}" for pid in range(25, 15, -1)]  # do que mais gasta para baixo
    assert [x["k"] for x in pontas["menos"]] == [f"cd-{pid}" for pid in range(1, 11)]  # do que menos gasta para cima
    assert pn["filtros"]["faixa_n"] == 2  # os 10% de 25, arredondado
    assert pn["cartao"]["pos"] == "13º de 25" and pn["cartao"]["pos_uf"] == ""  # no Senado não há posição no estado
    assert (pn["casa"], pn["um"], pn["frase"][-1]) == ("Senado", "senador", "todos os senadores")
    assert pn["quem"]["faixa"] == 1_000  # no Senado, faixas de R$ 1 mil


def test_os_titulos_de_para_conferir_e_as_opcoes_dos_filtros(vista: dict[str, Any]):
    pn = vista["painel"]
    assert pn["conferir"]["janela"] == "legislatura inteira (fev a set/2023)"
    assert pn["conferir"]["frase"] == "achados da legislatura inteira (fev a set/2023). Não mudam com o período nem com os filtros acima."
    assert (pn["conferir"]["fornecedores_n"], pn["conferir"]["concentrados_de"]) == (3, 0)
    filtros = pn["filtros"]
    assert filtros["anos"] == [{"ano": "2023", "de": "2023-02", "ate": "2023-09"}]
    assert filtros["ultimos"] == {"n": 8, "de": "2023-02", "ate": "2023-09", "rot": "Últimos 8 meses"}
    assert filtros["meses"][0] == {"m": "2023-02", "rot": "fev/2023"} and len(filtros["meses"]) == 8
    assert filtros["despesas"] == [
        {"id": "divulgacao", "curto": "Divulgação do mandato"},
        {"id": "telefonia", "curto": "Telefonia"},
        {"id": "veiculos", "curto": "Aluguel de veículos"},
    ]
    assert (filtros["ufs"], filtros["partidos"]) == (["MG", "RJ", "SP"], ["PL", "PP", "PSD", "PT"])  # só de quem é ponto


def test_painel_gravado_antes_da_aba_nova_nao_quebra_a_pagina(analise: cota.Analise):
    novos = ("aberto", "meses", "recentes", "pico", "marcos", "q1", "q3", "n_pontos", "todos", "fora", "meio", "n_concentrados", "tetos", "sigepa_ate")
    antigo = {k: v for k, v in analise.painel.items() if k not in novos} | {"sem_despesa": []}
    vista = pagina._cota(antigo, "cd")
    assert vista is not None and vista["painel"] is None  # o modelo cai no desenho antigo
    assert vista["total"] == "R$ 125.700" and vista["fornecedores"][0]["nome"] == "XIS"


@pytest.mark.parametrize(
    ("valor", "texto"),
    [
        (774_680_105.59, "R$ 774,7 milhões"),
        (1_877_758.12, "R$ 1,9 milhão"),
        (999_960.0, "R$ 1,0 milhão"),  # não vira "1.000,0 mil"
        (84_773.32, "R$ 84,8 mil"),
        (10_000.0, "R$ 10,0 mil"),
        (9_999.4, "R$ 9.999"),  # abaixo de R$ 10 mil, por extenso
        (1.16, "R$ 1,16"),
        (-113_787.24, "−R$ 113,8 mil"),
        (None, "—"),
    ],
)
def test_reais_curto(valor: float | None, texto: str):
    assert pagina.reais_curto(valor) == texto


def test_a_janela_por_escrito():
    assert pagina.janela_da_cota("2023-02", "2026-10", 45, fechados=True) == ("fev/2023 a out/2026", "fev/2023 a out/2026 (45 meses fechados)")
    assert pagina.janela_da_cota("2025-01", "2025-12", 12) == ("jan a dez/2025", "jan a dez/2025 (12 meses)")
    assert pagina.janela_da_cota("2025-12", "2025-12", 1) == ("dez/2025", "dez/2025 (1 mês)")
    assert cota.meses_entre("2023-11", "2024-02") == ["2023-11", "2023-12", "2024-01", "2024-02"] and cota.meses_entre("2024-02", "2023-11") == []


# ── de ponta a ponta, com o Congresso de mentira ─────────────────────────────


@pytest.fixture(scope="module")
def publicado(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dados.Resultado]:
    tmp_path = tmp_path_factory.mktemp("cota")
    data_dir = tmp_path / "data"
    (data_dir / "busca").mkdir(parents=True)
    (data_dir / "busca" / "2026-11-12.json").write_text(json.dumps(fabrica.busca_do_dia()), encoding="utf-8")
    resultado = dados.atualizar(tmp_path, data_dir, tmp_path / "cache", dados.Opcoes(hoje=fabrica.HOJE, projetos_desde=2026), fetch=fabrica.FetchFalso(), regras=fabrica.regras_de_teste())
    return data_dir, resultado


def _ler(data_dir: Path, nome: str) -> dict[str, Any]:
    return json.loads((data_dir / "congresso" / nome).read_text(encoding="utf-8"))


def test_atualizar_grava_o_arquivo_mensal_das_duas_casas(publicado: tuple[Path, dados.Resultado]):
    data_dir, resultado = publicado
    assert resultado.avisos == []
    for casa in ("cd", "sf"):
        arquivo, painel = _ler(data_dir, dados.COTA.format(casa=casa)), _ler(data_dir, f"casa-{casa}.json")["cota"]
        assert (arquivo["v"], arquivo["casa"], arquivo["tetos"]) == (cota.VERSAO_ARQUIVO, casa, None)
        assert arquivo["meses"] == [m["m"] for m in painel["meses"]] and arquivo["aberto"] == fabrica.HOJE[:7]
        assert [c[0] for c in arquivo["cats"]] == [c["id"] for c in painel["categorias"]]
        assert len(arquivo["pessoas"]) == painel["pessoas"]
        soma = 0
        for registro in arquivo["pessoas"]:
            total = sum(v for serie in registro[5] for v in serie[2:])
            ficha = _ler(data_dir, f"fichas/{registro[0]}.json")["cota"]
            assert round(total / 100, 2) == ficha["leg"]["total"], registro[0]  # o arquivo bate com a ficha de cada um
            soma += total
        assert round(soma / 100, 2) == painel["total"]
        assert painel["tetos"] is None and all(p["teto"] is None for p in painel["todos"])
    assert "sigepa_ate" in _ler(data_dir, "cota-cd.json") and "sigepa_ate" not in _ler(data_dir, "cota-sf.json")


def test_o_manifesto_diz_onde_esta_o_arquivo_e_a_pagina_continua_saindo(publicado: tuple[Path, dados.Resultado]):
    data_dir, _resultado = publicado
    config: Config = load_config(env={})
    html = pagina.render_congresso_page(config, data_dir, home_href="../", archive_href="./")
    assert html is not None
    assert '"cotaArq":"../data/congresso/cota-"' in html
    manifesto = json.loads(html.split('id="cgManifesto"', 1)[1].split(">", 1)[1].split("</script>", 1)[0])
    regras_da_aba = manifesto["cotaRegras"]  # as réguas que o script repete
    assert (regras_da_aba["v"], regras_da_aba["minimoMeses"], regras_da_aba["anoSigepa"]) == (cota.VERSAO_ARQUIVO, cota.MINIMO_MESES, cota.ANO_SIGEPA)
    assert (regras_da_aba["faixa"], regras_da_aba["eixo"], regras_da_aba["umaDespesa"], regras_da_aba["umFornecedor"]) == ({"cd": 500, "sf": 1000}, 55_000, 60, 25)
    assert set(regras_da_aba["ufDe"]) == set(regras.UFS)
    for trecho in ('id="cg-cota-cd"', 'id="cg-cota-sf"', "Quanto cada um gasta, com o quê e com quem", "Quem mais recebe"):
        assert trecho in html, trecho
    d = pagina.carregar(data_dir)
    assert d is not None
    vista = pagina.congresso_view(d, config)
    for casa in vista.casas:
        assert casa.cota is not None and casa.cota["painel"] is not None  # o estado padrão da aba nova, pronto para o modelo
        assert casa.cota["painel"]["frase"][0] == casa.curto
        assert not [t for t in _textos(casa.cota) if "teto" in t.lower()]  # sem tabela conferida, nada de teto


def test_tabela_incompleta_no_arquivo_de_escolhas_vira_aviso_e_o_resto_sai(tmp_path: Path):
    data_dir = tmp_path / "data"
    (data_dir / "busca").mkdir(parents=True)
    (data_dir / "busca" / "2026-11-12.json").write_text(json.dumps(fabrica.busca_do_dia()), encoding="utf-8")
    r = fabrica.regras_de_teste()
    r.cota = regras.de_dict({"cota": {"tetos": {"cd": {"fonte": "x", "url": "https://exemplo.invalid/", "vigencias": [{"desde": "2023-02", "valores": {"SP": 1}}]}}}}).cota
    resultado = dados.atualizar(tmp_path, data_dir, tmp_path / "cache", dados.Opcoes(hoje=fabrica.HOJE, projetos_desde=2026), fetch=fabrica.FetchFalso(), regras=r)
    assert len(resultado.avisos) == 1 and "cota.tetos.cd" in resultado.avisos[0]
    assert _ler(data_dir, "meta.json")["avisos"] == resultado.avisos
    assert _ler(data_dir, "casa-cd.json")["cota"]["tetos"] is None and (data_dir / "congresso" / "cota-cd.json").is_file()


def test_sem_o_arquivo_mensal_o_painel_e_o_resto_saem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def falha(_arquivo: Any) -> str:
        raise RuntimeError("disco cheio")

    monkeypatch.setattr(cota, "serializar", falha)
    data_dir = tmp_path / "data"
    (data_dir / "busca").mkdir(parents=True)
    (data_dir / "busca" / "2026-11-12.json").write_text(json.dumps(fabrica.busca_do_dia()), encoding="utf-8")
    resultado = dados.atualizar(tmp_path, data_dir, tmp_path / "cache", dados.Opcoes(hoje=fabrica.HOJE, projetos_desde=2026), fetch=fabrica.FetchFalso(), regras=fabrica.regras_de_teste())
    assert sorted(resultado.avisos) == [
        "Câmara: arquivo mensal da cota não gravado (disco cheio)",
        "Senado: arquivo mensal da cota não gravado (disco cheio)",
    ]
    assert not (data_dir / "congresso" / "cota-cd.json").exists()
    assert _ler(data_dir, "casa-cd.json")["cota"]["total"] > 0 and _ler(data_dir, "fichas/cd-4.json")["cota"]["leg"]["total"] > 0  # o painel e as fichas saem
