"""A cota parlamentar (qijournal.congresso.cota): leitura das duas fontes e o painel.

Sem rede: o zip da Câmara é montado em memória e o JSON do Senado segue o formato real
da CEAPS, servidos por um ``fetch`` de mentira. As contas do painel são conferidas num
punhado de lançamentos feitos à mão, pequeno o bastante para refazer na cabeça.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from qijournal.congresso import cota, fontes
from qijournal.net import FetchError, Response

HOJE = "2026-11-14"
URL_CD = cota.CAMARA_COTAS.format(ano=2026)
URL_CD_RESERVA = cota.CAMARA_COTAS_RESERVA.format(ano=2026)
URL_SF = cota.SENADO_CEAPS.format(ano=2026)
CPF_DO_DEPUTADO = "12345678909"  # está no arquivo da Câmara e não pode sair dele


class Rede:
    """``fetch`` de teste: responde pela URL inteira e anota o que foi pedido. O que não
    está no mapa falha como a rede falharia."""

    def __init__(self, mapa: dict[str, bytes]) -> None:
        self.mapa = dict(mapa)
        self.pedidos: list[str] = []

    def __call__(self, url: str, **_kwargs: Any) -> Response:
        self.pedidos.append(url)
        if url not in self.mapa:
            raise FetchError(url, "HTTP 404", status=404)
        return Response(url=url, status=200, content=self.mapa[url], headers={})


def _coletor(tmp_path: Path, mapa: dict[str, bytes]) -> tuple[fontes.Coletor, Rede]:
    rede = Rede(mapa)
    return fontes.Coletor(tmp_path / "cache", fetch=rede, pausa=0.0), rede


# ── Câmara ───────────────────────────────────────────────────────────────────

COLUNAS = (
    "txNomeParlamentar cpf ideCadastro nuCarteiraParlamentar nuLegislatura sgUF sgPartido codLegislatura numSubCota "
    "txtDescricao numEspecificacaoSubCota txtDescricaoEspecificacao txtFornecedor txtCNPJCPF txtNumero indTipoDocumento "
    "datEmissao vlrDocumento vlrGlosa vlrLiquido numMes numAno numParcela txtPassageiro txtTrecho numLote "
    "numRessarcimento datPagamentoRestituicao vlrRestituicao nuDeputadoId ideDocumento urlDocumento"
).split()
PDF = "https://www.camara.leg.br/cota-parlamentar/documentos/publ/1/2026/{}.pdf"


def _linha(**campos: str) -> dict[str, str]:
    base = {c: "" for c in COLUNAS}
    base.update(
        txNomeParlamentar="Ana Lima", cpf=CPF_DO_DEPUTADO, ideCadastro="1", sgUF="SP", sgPartido="PT", numSubCota="3",
        txtDescricao="COMBUSTÍVEIS E LUBRIFICANTES.", txtFornecedor="POSTO AZUL LTDA", txtCNPJCPF="11.111.111/0001-91",
        txtNumero="100", datEmissao="2026-03-10T00:00:00", vlrDocumento="100", vlrGlosa="0", vlrLiquido="100",
        numMes="3", numAno="2026", numParcela="0", ideDocumento="9000", urlDocumento=PDF.format(9000),
    )
    base.update(campos)
    return base


LINHAS_CAMARA = [
    # 0: a máscara errada do CNPJ e o valor com vírgula decimal
    _linha(txtCNPJCPF="085.324.290/0013-1", vlrDocumento="1.234,56", vlrLiquido="1.234,56", ideDocumento="1", urlDocumento=PDF.format(1)),
    # 1: glosa (a Câmara recusou 200 de uma nota de 1.000)
    _linha(vlrDocumento="1000", vlrGlosa="200", vlrLiquido="800", txtNumero="101", ideDocumento="2", urlDocumento=PDF.format(2)),
    # 2: passagem comprada pela agência da Câmara (vem marcada; resumir a deixa fora das contas)
    _linha(numSubCota="998", txtDescricao="PASSAGEM AÉREA - SIGEPA", txtFornecedor="Cia Aérea", vlrDocumento="900", vlrLiquido="900"),
    # fora: liderança, sem deputado
    _linha(ideCadastro="", txNomeParlamentar="LIDERANÇA DO PT", vlrDocumento="50", vlrLiquido="50"),
    # 3: complementação do auxílio-moradia, com o líquido de sinal trocado e sem data
    _linha(numSubCota="40", txtDescricao="COMPLEMENTAÇÃO DO AUXÍLIO-MORADIA", txtFornecedor="COMPLEMENTAÇÃO DO AUXÍLIO-MORADIA",
           txtCNPJCPF="00.530.352/0001-59", txtNumero="", datEmissao="", vlrDocumento="1747", vlrGlosa="", vlrLiquido="-1747",
           vlrRestituicao="0.0", urlDocumento=""),
    # 4: fornecedor pessoa física: o CPF não fica
    _linha(numSubCota="1", txtDescricao="MANUTENÇÃO DE ESCRITÓRIO DE APOIO À ATIVIDADE PARLAMENTAR", txtFornecedor="JOSÉ DA SILVA",
           txtCNPJCPF="346.769.613/91  -", txtNumero="7", vlrDocumento="3000", vlrLiquido="3000", ideDocumento="3", urlDocumento="javascript:alert(1)"),
    # 5 e 6: a mesma nota dividida entre duas subcotas (o vlrDocumento repete a nota inteira)
    _linha(numSubCota="14", txtDescricao="HOSPEDAGEM ,EXCETO DO PARLAMENTAR NO DISTRITO FEDERAL.", txtFornecedor="HOTEL",
           txtCNPJCPF="03.851.093/0001-56", vlrDocumento="295.5", vlrLiquido="283.5", ideDocumento="4", urlDocumento=PDF.format(4)),
    _linha(numSubCota="122", txtDescricao="SERVIÇO DE TÁXI, PEDÁGIO E ESTACIONAMENTO", txtFornecedor="HOTEL",
           txtCNPJCPF="03.851.093/0001-56", vlrDocumento="295.5", vlrLiquido="12", ideDocumento="4", urlDocumento=PDF.format(4)),
    # 7 e 8: a nota reembolsada e, depois, devolvida pelo deputado (a linha da devolução repete a nota)
    _linha(ideCadastro="2", vlrDocumento="620.55", vlrGlosa="10", vlrLiquido="610.55", ideDocumento="5", urlDocumento=PDF.format(5)),
    _linha(ideCadastro="2", vlrDocumento="620.55", vlrGlosa="10", vlrLiquido="0", vlrRestituicao="610.55",
           datPagamentoRestituicao="2026-07-06T00:00:00", ideDocumento="5", urlDocumento=PDF.format(5)),
]


def _zip(linhas: list[dict[str, str]], nome: str = "Ano-2026.csv") -> bytes:
    texto = io.StringIO()
    escritor = csv.DictWriter(texto, fieldnames=COLUNAS, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    escritor.writeheader()
    escritor.writerows(linhas)
    pacote = io.BytesIO()
    with zipfile.ZipFile(pacote, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(nome, texto.getvalue().encode("utf-8-sig"))
    return pacote.getvalue()


@pytest.fixture
def despesas_cd(tmp_path: Path) -> list[cota.Despesa]:
    coletor, _rede = _coletor(tmp_path, {URL_CD: _zip(LINHAS_CAMARA)})
    return cota.camara(coletor, [2026], ano_corrente=2026)


def test_camara_le_o_zip_sem_lideranca_e_marca_a_sigepa(despesas_cd: list[cota.Despesa]):
    assert len(despesas_cd) == 9
    assert [cota.e_sigepa(d) for d in despesas_cd] == [False, False, True, False, False, False, False, False, False]
    assert {d.pessoa for d in despesas_cd} == {1, 2}
    primeira = despesas_cd[0]
    assert (primeira.pessoa, primeira.ano, primeira.mes, primeira.categoria) == (1, 2026, 3, "COMBUSTÍVEIS E LUBRIFICANTES.")
    assert (primeira.fornecedor, primeira.numero, primeira.data, primeira.url) == ("POSTO AZUL LTDA", "100", "2026-03-10", PDF.format(1))


def test_camara_normaliza_cnpj_e_nao_guarda_cpf(despesas_cd: list[cota.Despesa]):
    assert despesas_cd[0].cnpj == "08532429000131"  # a máscara "085.324.290/0013-1" estava errada
    assert despesas_cd[4].cnpj == ""  # CPF de fornecedor pessoa física
    for d in despesas_cd:
        assert not any(CPF_DO_DEPUTADO in str(v) for v in (d.fornecedor, d.cnpj, d.numero, d.url, d.categoria))


def test_camara_valor_liquido_de_glosa_e_devolucao(despesas_cd: list[cota.Despesa]):
    valores = [d.valor for d in despesas_cd]
    assert valores == [1234.56, 800.0, 900.0, 1747.0, 3000.0, 283.5, 12.0, 610.55, -610.55]
    assert [d.glosa for d in despesas_cd] == [0.0, 200.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.0]  # a glosa da nota devolvida conta uma vez
    moradia = despesas_cd[3]
    assert moradia.data == "" and moradia.url == "" and moradia.cnpj == "00530352000159"
    assert despesas_cd[4].url == ""  # só link para a página da Câmara


def test_camara_usa_a_reserva_quando_o_espelho_falha(tmp_path: Path):
    coletor, rede = _coletor(tmp_path, {URL_CD_RESERVA: _zip(LINHAS_CAMARA[:2])})
    assert [d.valor for d in cota.camara(coletor, [2026], ano_corrente=2026)] == [1234.56, 800.0]
    assert rede.pedidos == [URL_CD, URL_CD_RESERVA]


def test_camara_zip_estragado_sai_do_cache_e_vai_para_a_reserva(tmp_path: Path):
    coletor, rede = _coletor(tmp_path, {URL_CD: b"<html>fora do ar</html>", URL_CD_RESERVA: _zip(LINHAS_CAMARA[:1])})
    assert len(cota.camara(coletor, [2026], ano_corrente=2026)) == 1
    assert rede.pedidos == [URL_CD, URL_CD_RESERVA]
    rede.mapa[URL_CD] = _zip(LINHAS_CAMARA[:2])
    assert len(cota.camara(coletor, [2026], ano_corrente=2026)) == 2  # o espelho é baixado de novo


def test_camara_ano_que_falha_levanta_erro_claro(tmp_path: Path):
    coletor, _rede = _coletor(tmp_path, {URL_CD: _zip(LINHAS_CAMARA)})
    with pytest.raises(cota.CotaIndisponivel, match="Câmara: cota parlamentar de 2025 indisponível"):
        cota.camara(coletor, [2025, 2026], ano_corrente=2026)


def test_camara_arquivo_sem_as_colunas_e_erro(tmp_path: Path):
    pacote = io.BytesIO()
    with zipfile.ZipFile(pacote, "w") as z:
        z.writestr("Ano-2026.csv", '"ideCadastro";"numMes"\r\n"1";"3"\r\n'.encode("utf-8-sig"))
    coletor, _rede = _coletor(tmp_path, {URL_CD: pacote.getvalue()})
    with pytest.raises(cota.CotaIndisponivel, match="faltam as colunas"):
        cota.camara(coletor, [2026], ano_corrente=2026)


def test_validade_dos_anos_da_camara():
    def validade(ano: int, mes: int) -> float:
        return cota.validade_camara(ano, ano_corrente=2026, mes=mes, validade_h=20, validade_antigos_h=720)

    assert validade(2026, 11) == 20
    assert validade(2025, 4) == 20  # até abril ainda chegam documentos do ano anterior
    assert validade(2025, 5) == 720
    assert validade(2019, 1) == 720


# ── Senado ───────────────────────────────────────────────────────────────────

MATERIAL = (
    "Aquisição de material de consumo para uso no escritório político, inclusive aquisição ou locação de software, "
    "despesas postais, aquisição de publicações, locação de móveis e de equipamentos. "
)
ALUGUEL = "Aluguel de imóveis para escritório político, compreendendo despesas concernentes a eles."
PASSAGENS = "Passagens aéreas, aquáticas e terrestres nacionais"


def _item(**campos: Any) -> dict[str, Any]:
    base = {
        "id": 2300648, "tipoDocumento": "Nota Fiscal Eletrônica", "ano": 2026, "mes": 9, "codSenador": 22,
        "nomeSenador": "ESPERIDIÃO AMIN", "tipoDespesa": MATERIAL, "cpfCnpj": "08.194.293/0001-05",
        "fornecedor": "BV PAPELARIA E INFORMÁTICA LTDA ME", "documento": "000.001.317", "data": "2026-09-25",
        "detalhamento": None, "valorReembolsado": 626.2,
    }
    base.update(campos)
    return base


ITENS_SENADO = [
    _item(),
    # fora: reembolso de moradia (tipoDespesa vazio)
    _item(id=2300700, tipoDocumento="Recibo", tipoDespesa=None, cpfCnpj="11.222.333/0001-81",
          fornecedor="CHIOVATO PARTICIPAÇÕES LTDA", documento="9", valorReembolsado=3500),
    # CPF mascarado de pessoa física, documento nulo, valor inteiro
    _item(id=2300701, codSenador=5012, tipoDocumento="Recibo", tipoDespesa=ALUGUEL, cpfCnpj="038.***.***-68",
          fornecedor="FULANO DE TAL", documento=None, valorReembolsado=6000),
    # CNPJ que perdeu o zero da frente
    _item(id=2300702, cpfCnpj="8194293000105", data="0202-02-24", documento="1318"),
    # estorno e a linha idêntica (menos o id), que fica: é do registro oficial
    _item(id=2296899, mes=10, tipoDocumento="Passagem / Bilhete / Código Localizador", tipoDespesa=PASSAGENS,
          cpfCnpj="05.120.923/0001-09", fornecedor="Aerotur Serviços", documento="2254444273", data="2026-10-10",
          valorReembolsado=-1088.26),
    _item(id=2296604, mes=10, tipoDocumento="Passagem / Bilhete / Código Localizador", tipoDespesa=PASSAGENS,
          cpfCnpj="05.120.923/0001-09", fornecedor="Aerotur Serviços", documento="2254444273", data="2026-10-10",
          valorReembolsado=-1088.26),
]


def test_senado_le_a_ceaps(tmp_path: Path):
    coletor, rede = _coletor(tmp_path, {URL_SF: json.dumps(ITENS_SENADO).encode()})
    despesas = cota.senado(coletor, [2026], ano_corrente=2026)
    assert rede.pedidos == [URL_SF]
    assert len(despesas) == 5  # sem a moradia
    primeira = despesas[0]
    assert (primeira.pessoa, primeira.ano, primeira.mes) == (22, 2026, 9)
    assert primeira.categoria == MATERIAL.strip()
    assert (primeira.cnpj, primeira.numero, primeira.data, primeira.valor, primeira.glosa, primeira.url) == (
        "08194293000105", "000.001.317", "2026-09-25", 626.2, 0.0, "",
    )
    aluguel = despesas[1]
    assert (aluguel.pessoa, aluguel.cnpj, aluguel.numero, aluguel.valor) == (5012, "", "", 6000.0)
    assert (despesas[2].cnpj, despesas[2].data) == ("08194293000105", "")  # zero da frente recuperado; data impossível fora
    assert [d.valor for d in despesas[3:]] == [-1088.26, -1088.26]


def test_senado_fora_do_formato_ou_fora_do_ar_levanta_erro(tmp_path: Path):
    coletor, _rede = _coletor(tmp_path, {URL_SF: b'{"statusCode": 500, "data": []}'})
    with pytest.raises(cota.CotaIndisponivel, match="Senado: cota parlamentar de 2026 fora do formato"):
        cota.senado(coletor, [2026], ano_corrente=2026)
    with pytest.raises(cota.CotaIndisponivel, match="Senado: cota parlamentar de 2025 indisponível"):
        cota.senado(coletor, [2025], ano_corrente=2026)


# ── o painel ─────────────────────────────────────────────────────────────────

SIGEPA = "PASSAGEM AÉREA - SIGEPA"
X, Y, Z, W, IZA, CELULAR = "11111111000191", "22222222000191", "55555555000191", "33333333000191", "44444444000191", "00000000000001"
NOTA_ANA, NOTA_BIA = PDF.format("ana-234"), PDF.format("bia-234")

PESSOAS = {
    1: {"k": "cd-1", "nome": "Ana Lima", "partido": "PT", "uf": "SP", "ex": True},
    2: {"k": "cd-2", "nome": "Bia Reis", "partido": "PL", "uf": "RJ", "ex": True},
    3: {"k": "cd-3", "nome": "Caio Luz", "partido": "PP", "uf": "MG", "ex": True},  # só 5 meses de despesa
    4: {"k": "cd-4", "nome": "Davi Rocha", "partido": "MDB", "uf": "BA", "ex": False},  # fora do exercício
    5: {"k": "cd-5", "nome": "Eva Prado", "partido": "NOVO", "uf": "GO", "ex": True},
}


def _d(pessoa: int, mes: int, valor: float, cnpj: str, *, numero: str = "1", data: str = "", fornecedor: str = "F",
       categoria: str = "DIVULGAÇÃO", glosa: float = 0.0, url: str = "", ano: int = 2023) -> cota.Despesa:
    return cota.Despesa(pessoa, ano, mes, categoria, fornecedor, cnpj, numero, data, valor, glosa, url)


def _lancamentos() -> list[cota.Despesa]:
    ana = [
        _d(1, 1, 99_999, X, fornecedor="XIS LTDA"),  # antes do início da legislatura
        *(_d(1, m, 10_000, X, numero=str(100 + m), data=f"2023-{m:02d}-10", fornecedor="XIS LTDA") for m in range(2, 8)),
        _d(1, 8, 20_000, Y, numero="77", data="2023-08-15", fornecedor="YPSILON", categoria="LOCAÇÃO", glosa=300),
        _d(1, 9, 4_800, IZA, numero="9/2023", data="2023-09-01", fornecedor="IZA", categoria="ESCRITÓRIO"),
        _d(1, 10, 500, CELULAR, numero="123", data="2023-10-01", fornecedor="CELULAR FUNCIONAL", categoria="TELEFONIA"),
        _d(1, 11, 11_500, W, numero="234", data="2023-11-30", fornecedor="ML LOCAÇÕES", categoria="LOCAÇÃO", url=NOTA_ANA),
        _d(1, 11, 300, W, numero="S/N", data="2023-11-29", fornecedor="ML LOCAÇÕES", categoria="LOCAÇÃO"),
        _d(1, 1, 1_000, X, fornecedor="XIS LTDA", ano=2024),  # o mês de hoje, ainda aberto
        _d(1, 5, 500, "33937681000178", fornecedor="LATAM", categoria=SIGEPA),  # passagem da agência: fora das contas
    ]
    bia = [
        *(_d(2, m, 5_000, Z, numero=str(200 + m), data=f"2023-{m:02d}-05", fornecedor="ZETA") for m in range(2, 11)),
        _d(2, 9, 4_800, IZA, numero="092023", data="2023-09-01", fornecedor="IZA", categoria="ESCRITÓRIO"),
        _d(2, 10, 500, CELULAR, numero="123", data="2023-10-01", fornecedor="CELULAR FUNCIONAL", categoria="TELEFONIA"),
        _d(2, 11, 11_500, W, numero="0234", data="2023-11-30", fornecedor="ML LOCACOES EIRELI", categoria="LOCAÇÃO", url=NOTA_BIA),
        _d(2, 11, 300, W, numero="S/N", data="2023-11-29", fornecedor="ML LOCAÇÕES", categoria="LOCAÇÃO"),
        _d(2, 12, -1_000, Z, numero="999", data="2023-12-01", fornecedor="ZETA"),  # estorno: dezembro não conta como mês
    ]
    caio = [_d(3, m, 100_000, "66666666000191", fornecedor="CONSULTA", categoria="CONSULTORIA") for m in range(2, 7)]
    davi = [_d(4, m, 50_000, X, fornecedor="XIS") for m in range(2, 10)]
    eva = [
        *(_d(5, m, 1_000, "77777777000191", fornecedor="EME", categoria="COMBUSTÍVEIS") for m in range(2, 8)),
        _d(5, 3, 3_000, "33937681000178", fornecedor="LATAM", categoria=SIGEPA, ano=2024),
        _d(5, 3, 700, "33937681000178", fornecedor="LATAM", categoria=SIGEPA, ano=2022),  # antes da legislatura
    ]
    estranho = [  # não está em PESSOAS: não conta em nada
        _d(9, 11, 11_500, W, numero="234", data="2023-11-30", fornecedor="ML LOCAÇÕES"),
        *(_d(9, m, 1_000_000, Y) for m in range(2, 8)),
    ]
    return ana + bia + caio + davi + eva + estranho


@pytest.fixture
def resumo() -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    return cota.resumir(_lancamentos(), casa="cd", inicio="2023-02-01", hoje="2024-01-15", pessoas=PESSOAS)


def test_painel_totais_e_periodo(resumo):
    painel, _por = resumo
    assert painel["desde"] == "2023-02" and painel["ate"] == "2023-12"
    assert painel["sem"] == cota.SEM["cd"] and "SIGEPA" in painel["sem"]
    assert painel["total"] == 1_064_200.0  # Ana 97.100 + Bia 61.100 + Caio 500.000 + Davi 400.000 + Eva 6.000
    assert painel["pessoas"] == 5


def test_painel_medias_com_minimo_de_seis_meses(resumo):
    painel, _por = resumo
    # Ana: 97.100 em 10 meses; Bia: 61.100 em 10 (dezembro só tem estorno); Eva: 6.000 em 6.
    # Caio (5 meses) e Davi (fora do exercício) não entram.
    assert painel["mediana"] == 6_110.0
    assert painel["mais_gastam"] == [
        {"k": "cd-1", "nome": "Ana Lima", "partido": "PT", "uf": "SP", "media": 9_710.0, "total": 97_100.0, "meses": 10, "sigepa_2024": 0.0},
        {"k": "cd-2", "nome": "Bia Reis", "partido": "PL", "uf": "RJ", "media": 6_110.0, "total": 61_100.0, "meses": 10, "sigepa_2024": 0.0},
        {"k": "cd-5", "nome": "Eva Prado", "partido": "NOVO", "uf": "GO", "media": 1_000.0, "total": 6_000.0, "meses": 6, "sigepa_2024": 3_000.0},
    ]
    assert [p["k"] for p in painel["menos_gastam"]] == ["cd-5", "cd-2", "cd-1"]


def test_painel_categorias(resumo):
    painel, _por = resumo
    assert [(c["nome"], c["total"]) for c in painel["categorias"]] == [
        ("DIVULGAÇÃO", 504_000.0),
        ("CONSULTORIA", 500_000.0),
        ("LOCAÇÃO", 43_600.0),
        ("ESCRITÓRIO", 9_600.0),
        ("COMBUSTÍVEIS", 6_000.0),
        ("TELEFONIA", 1_000.0),
    ]
    assert painel["categorias"][0]["pct"] == 47.4  # as passagens SIGEPA não entram


def test_painel_fornecedores_sem_pseudo_cnpj(resumo):
    painel, _por = resumo
    fornecedores = painel["fornecedores"]
    assert CELULAR not in {f["cnpj"] for f in fornecedores}
    assert [f["cnpj"] for f in fornecedores] == ["66666666000191", X, Z, W, Y, IZA, "77777777000191"]
    assert fornecedores[1] == {"nome": "XIS", "cnpj": X, "total": 460_000.0, "gabinetes": 2, "n": 14}  # 8 notas da Davi, 6 da Ana
    assert fornecedores[3] == {"nome": "ML LOCAÇÕES", "cnpj": W, "total": 23_600.0, "gabinetes": 2, "n": 4}  # a grafia mais frequente


def test_painel_concentracao(resumo):
    painel, _por = resumo
    # Caio: 100% a um fornecedor; Ana: 60.000 de 97.100 (61,8%) à XIS. Bia passa de 25%, mas a ZETA
    # soma 44.000 (menos de 50 mil); Davi está fora do exercício.
    assert [(c["k"], c["cnpj"], c["valor"], c["pct"], c["total"]) for c in painel["concentrados"]] == [
        ("cd-3", "66666666000191", 500_000.0, 100.0, 500_000.0),
        ("cd-1", X, 60_000.0, 61.8, 97_100.0),
    ]
    assert painel["concentrados"][1]["fornecedor"] == "XIS"


def test_painel_notas_repetidas_sem_falsos_positivos(resumo):
    painel, _por = resumo
    # Só a nota 234 da ML LOCAÇÕES ("0234" no outro gabinete): os recibos numerados pelo mês
    # ("9/2023", "092023"), o pseudo-CNPJ do celular funcional e o "S/N" não contam.
    assert painel["repetidas"] == [
        {
            "fornecedor": "ML LOCAÇÕES",
            "cnpj": W,
            "numero": "234",
            "data": "2023-11-30",
            "valor": 11_500.0,
            "docs": [
                {"k": "cd-1", "nome": "Ana Lima", "partido": "PT", "uf": "SP", "url": NOTA_ANA},
                {"k": "cd-2", "nome": "Bia Reis", "partido": "PL", "uf": "RJ", "url": NOTA_BIA},
            ],
        }
    ]


def test_painel_glosas(resumo):
    painel, _por = resumo
    assert painel["glosas"] == [{"k": "cd-1", "nome": "Ana Lima", "partido": "PT", "uf": "SP", "valor": 300.0, "n": 1}]


def test_resumo_por_pessoa(resumo):
    _painel, por = resumo
    assert set(por) == {1, 2, 3, 4, 5}  # Davi, fora do exercício, também ganha o resumo
    ana = por[1]
    assert ana["leg"] == {"total": 97_100.0, "meses": 10, "media": 9_710.0}
    assert ana["anos"] == {"2023": 197_099.0, "2024": 1_000.0}  # todos os anos, não só a legislatura; sem SIGEPA
    assert ana["sigepa"] == {"2023": 500.0, "2024": 0.0}  # os anos da legislatura em que o arquivo traz as passagens
    assert por[5]["sigepa"] == {"2023": 0.0, "2024": 3_000.0}
    assert ana["categorias"] == [["DIVULGAÇÃO", 60_000.0], ["LOCAÇÃO", 31_800.0], ["ESCRITÓRIO", 4_800.0], ["TELEFONIA", 500.0]]
    assert ana["fornecedores"] == [
        {"nome": "XIS", "cnpj": X, "valor": 60_000.0, "n": 6},  # a grafia mais frequente na Casa
        {"nome": "YPSILON", "cnpj": Y, "valor": 20_000.0, "n": 1},
        {"nome": "ML LOCAÇÕES", "cnpj": W, "valor": 11_800.0, "n": 2},
        {"nome": "IZA", "cnpj": IZA, "valor": 4_800.0, "n": 1},
    ]
    assert [(m["d"], m["valor"]) for m in ana["maiores"]] == [
        ("2023-08-15", 20_000.0),
        ("2023-11-30", 11_500.0),
        ("2023-02-10", 10_000.0),
        ("2023-03-10", 10_000.0),
        ("2023-04-10", 10_000.0),
        ("2023-05-10", 10_000.0),
    ]
    assert ana["maiores"][1] == {"d": "2023-11-30", "fornecedor": "ML LOCAÇÕES", "categoria": "LOCAÇÃO", "valor": 11_500.0, "url": NOTA_ANA}
    assert ana["glosa"] == {"valor": 300.0, "n": 1}
    assert por[2]["leg"] == {"total": 61_100.0, "meses": 10, "media": 6_110.0}


def test_maiores_documentos_somam_as_linhas_da_mesma_nota():
    nota = PDF.format("dividida")
    devolvida = PDF.format("devolvida")
    despesas = [
        _d(1, 3, 283.5, X, categoria="HOSPEDAGEM", url=nota, data="2023-03-01"),
        _d(1, 3, 12, X, categoria="TÁXI", url=nota, data="2023-03-01"),
        _d(1, 4, 620.55, Y, url=devolvida, data="2023-04-01"),
        _d(1, 4, -620.55, Y, url=devolvida, data="2023-04-01"),
        _d(1, 5, 100, Z, data="2023-05-01"),
    ]
    _painel, por = cota.resumir(despesas, casa="sf", inicio="2023-02-01", hoje=HOJE, pessoas=PESSOAS)
    assert por[1]["maiores"] == [
        {"d": "2023-03-01", "fornecedor": "F", "categoria": "HOSPEDAGEM", "valor": 295.5, "url": nota},
        {"d": "2023-05-01", "fornecedor": "F", "categoria": "DIVULGAÇÃO", "valor": 100.0, "url": ""},
    ]
    assert _painel["sem"] == cota.SEM["sf"]
    assert "sigepa" not in por[1]  # só a Câmara


def test_mes_aberto_fica_fora_das_somas_mas_nas_listas_de_notas():
    def notas(pessoa: int, base: float) -> list[cota.Despesa]:
        return [_d(pessoa, m, base, X, numero=str(10 * pessoa + m), fornecedor="XIS") for m in range(2, 8)]

    nota = dict(numero="234", data="2023-11-10", fornecedor="ML LOCAÇÕES", categoria="LOCAÇÃO")
    despesas = [
        *notas(1, 1_000),
        *notas(2, 2_000),
        _d(1, 11, 50_000, W, url=NOTA_ANA, **nota),  # novembro: o mês de hoje
        _d(2, 11, 50_000, W, url=NOTA_BIA, **nota),
        _d(1, 12, 9_999, Y),  # depois de hoje
    ]
    painel, por = cota.resumir(despesas, casa="cd", inicio="2023-02-01", hoje="2023-11-20", pessoas=PESSOAS)
    assert painel["ate"] == "2023-07"
    assert painel["total"] == 18_000.0
    assert [(p["k"], p["media"], p["meses"]) for p in painel["mais_gastam"]] == [("cd-2", 2_000.0, 6), ("cd-1", 1_000.0, 6)]
    assert W not in {f["cnpj"] for f in painel["fornecedores"]}
    assert por[1]["leg"] == {"total": 6_000.0, "meses": 6, "media": 1_000.0}
    assert por[1]["anos"] == {"2023": 65_999.0}
    assert por[1]["maiores"][0] == {"d": "2023-11-10", "fornecedor": "ML LOCAÇÕES", "categoria": "LOCAÇÃO", "valor": 50_000.0, "url": NOTA_ANA}
    assert [(r["fornecedor"], r["cnpj"], r["valor"], len(r["docs"])) for r in painel["repetidas"]] == [("ML LOCAÇÕES", W, 50_000.0, 2)]


def test_pessoa_fisica_nao_tem_nome_publicado_nem_entra_na_concentracao():
    despesas = [
        *(_d(1, m, 50_000, "", numero=str(m), data=f"2023-{m:02d}-01", fornecedor="JOÃO DA SILVA", categoria="ESCRITÓRIO") for m in range(2, 8)),
        *(_d(1, m, 25_000, Y, numero=str(50 + m), data=f"2023-{m:02d}-02", fornecedor="YPSILON") for m in range(2, 8)),
        *(_d(1, m, 5_000, Z, numero=str(90 + m), fornecedor="ZETA") for m in range(2, 8)),
    ]
    painel, por = cota.resumir(despesas, casa="cd", inicio="2023-02-01", hoje=HOJE, pessoas=PESSOAS)
    assert "JOÃO" not in json.dumps([painel, por], ensure_ascii=False)
    # 300 mil a uma pessoa física (60%) não contam; a maior empresa, YPSILON, levou 150 mil de 480 mil (31,2%).
    assert [(c["fornecedor"], c["cnpj"], c["valor"], c["pct"]) for c in painel["concentrados"]] == [("YPSILON", Y, 150_000.0, 31.2)]
    assert [f["cnpj"] for f in painel["fornecedores"]] == [Y, Z]
    assert por[1]["fornecedores"][0] == {"nome": "pessoa física", "cnpj": "", "valor": 300_000.0, "n": 6}
    assert por[1]["maiores"][0]["fornecedor"] == "pessoa física"


def test_numero_de_nota_valido():
    for numero in ("234", "0234", "169", "2600000000001", "59159"):
        assert cota.numero_valido(numero), numero
    for numero in ("9/2026", "08/26", "102026", "092025", "0", "000", "S/N", "SN", "s/nº", ""):
        assert not cota.numero_valido(numero), numero
