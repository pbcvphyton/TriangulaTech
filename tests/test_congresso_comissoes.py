"""Votações nominais de comissão (qijournal.congresso.comissoes).

Sem rede: o ``votacoes-AAAA.csv`` da Câmara e as respostas do Senado saem de um ``fetch``
falso, no formato das fontes reais (CSV com BOM, ``;`` e aspas; JSON do XML convertido,
em que a lista de um item só vem como objeto). As contas são conferidas num Congresso de
mentira montado aqui mesmo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from qijournal.congresso import base, comissoes, fontes, regras
from qijournal.net import FetchError, Response

HOJE = "2026-11-14"


# ── fetch falso ──────────────────────────────────────────────────────────────


def _csv(colunas: list[str], linhas: list[list[Any]]) -> bytes:
    """CSV como os da Câmara: BOM, ``;`` e tudo entre aspas."""

    def celula(valor: Any) -> str:
        return '"' + str("" if valor is None else valor).replace('"', '""') + '"'

    texto = "\n".join(";".join(celula(c) for c in linha) for linha in [colunas, *linhas]) + "\n"
    return b"\xef\xbb\xbf" + texto.encode("utf-8")


class FetchFalso:
    """Responde pelo caminho da URL (com a consulta) e anota o que foi pedido. Uma URL que não
    está no mapa, ou que tem um trecho de ``fora_do_ar``, falha como a rede falharia."""

    def __init__(self, mapa: dict[str, bytes]) -> None:
        self.mapa = mapa
        self.pedidos: list[str] = []
        self.fora_do_ar: set[str] = set()

    def __call__(self, url: str, **_kwargs: Any) -> Response:
        self.pedidos.append(url)
        caminho = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
        if caminho not in self.mapa or any(trecho in url for trecho in self.fora_do_ar):
            raise FetchError(url, "HTTP 404", status=404)
        return Response(url=url, status=200, content=self.mapa[caminho], headers={"content-type": "application/json"})


def _regras() -> regras.Regras:
    return regras.de_dict(
        {
            "legislaturas": [
                {"numero": 56, "inicio": "2019-02-01", "fim": "2023-01-31"},
                {"numero": 57, "inicio": "2023-02-01", "fim": "2027-01-31"},
            ]
        }
    )


# ── a conclusão do parecer ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("registro", "esperado"),
    [
        ("Parecer do Relator, Dep. Caio Luz (PL-SP), pela rejeição.", "pela rejeição"),
        ("Parecer da Relatora, Dep. Bia Reis (PT-RJ), pela aprovação, com substitutivo.", "pela aprovação"),
        # A admissibilidade vem antes; vale o mérito.
        ("Parecer do Relator, Dep. X (PL-MT), pela constitucionalidade, juridicidade,  técnica legislativa e, no mérito, pela rejeição.", "pela rejeição"),
        ("Parecer do Relator, Dep. X, pela inadmissibilidade do art. 4º da Emenda nº 1, pela rejeição das disposições restantes, e, no mérito, pela aprovação da Proposta.", "pela aprovação"),
        # A primeira conclusão é a da matéria; a dos apensados vem depois.
        ("Parecer da Relatora, Dep. Y (PT-RS), pela aprovação deste, e pela rejeição do PL 5245/2020, do PL 102/2021, apensados.", "pela aprovação"),
        ("Parecer do Relator, Dep. Z (REPUBLIC-TO), pelo conhecimento do Recurso nº 21/2024 e, no mérito, por sua improcedência.", "pela improcedência"),
        ("Parecer do Relator, Dep. W (PP-RS), pela constitucionalidade, juridicidade e técnica legislativa deste e dos Projetos apensados.", "pela constitucionalidade"),
        ("Parecer do Relator, Dep. Alfredo Gaspar (UNIÃO-AL), pela sustação do andamento da Ação Penal contida na Petição n.\n12.100.", "pela sustação"),
        ("Parecer do Relator, Dep. V, pela admissibilidade, com emenda.", "pela admissibilidade"),
        ("Parecer do Relator, Dep. V, pelo não provimento do recurso.", "pelo não provimento"),
        ("Pela aprovação, com substitutivo, e pela rejeição das emendas apresentadas ao substitutivo.", "pela aprovação"),
        # Sem conclusão: o parecer vencedor só lido, o que não diz uma das palavras, a apresentação de requerimento.
        ("Lido o Parecer Vencedor pelo Relator, Deputado Cláudio Cajado.", ""),
        ("Parecer do Relator, Dep. D (PSD-SC), pelo atendimento dos requisitos; e no mérito, pela preservação da eficácia da decisão.", ""),
        ('Apresentação do REQ n. 61/2023 (Requerimento de Audiência Pública), pelo Deputado X (PL/PE), que "Requer a aprovação de moção".', ""),
        ("", ""),
    ],
)
def test_conclusao_do_parecer(registro: str, esperado: str):
    assert comissoes.conclusao(registro) == esperado


@pytest.mark.parametrize(
    ("descricao", "orgao", "esperado"),
    [
        ("Aprovado o parecer da Comissão de Constituição e Justiça e de Cidadania à Sustação de Andamento de Ação Penal nº 1, de 2025, pela sustação.", "PLEN", True),
        ("Rejeitado o Parecer da Comissão de Constituição e Justiça e de Cidadania à Representação nº 2, de 2025, que conclui pela procedência.", "PLEN", True),
        # O parecer da Comissão Mista (MP): o voto dela não está nos arquivos da Câmara.
        ("Aprovado, em apreciação preliminar, o Parecer da Comissão Mista, na parte em que manifesta opinião favorável quanto ao atendimento dos pressupostos.", "PLEN", False),
        # Vota-se outra coisa; o parecer só é citado.
        ("Aprovado o Substitutivo do Senado Federal ao PLP nº 108, de 2024, com parecer pela aprovação, excetuadas as modificações indicadas no Parecer da Comissão.", "PLEN", False),
        ("Rejeitado o Recurso nº 2, de 2026, contra parecer terminativo da Comissão de Constituição e Justiça e de Cidadania.", "PLEN", False),
        ("Aprovado o parecer da Comissão de Constituição e Justiça.", "CCJC", False),  # não é Plenário
    ],
)
def test_plenario_que_vota_o_parecer_de_uma_comissao(descricao: str, orgao: str, esperado: bool):
    v = base.Votacao(id="1-1", data="2025-05-07", orgao=orgao, descricao=descricao, tipo=base.MERITO, votos="")
    assert comissoes.vota_parecer_de_comissao(v) is esperado


def test_validade_do_arquivo_da_camara():
    assert comissoes.validade_camara(2026, HOJE) == comissoes.VALIDADE_H
    assert comissoes.validade_camara(2025, HOJE) == comissoes.VALIDADE_ANTIGOS_H


# ── Câmara ───────────────────────────────────────────────────────────────────
#
# Seis deputados: 1 Ana (PT), 2 Bia (PT), 3 Caio (PL), 4 Davi (PL; PP a partir do Plenário de
# 10/12), 5 Eva (PP), 6 Gil (MDB, nunca vota em comissão). Votos na ordem das pessoas:
#
#   100-1  10/03 CCJC  requerimento        SNNN--   PL 1/2025
#   100-2  10/03 CCJC  "Aprovado o Parecer" SSSNA-   parecer pela rejeição (no mérito)
#   100-3  11/03 PLEN  o projeto            SSNNSS   (não vota parecer: não é caso)
#   101-1  02/04 CSPCCO parecer, unânime    SS-S--
#   200-3  10/12 PLEN  "Rejeitado o Parecer da CCJC… que conclui pela procedência"  NSSOAS
#                      (às 00:46: o registro passou da meia-noite)
#   200-1  10/12 CCJC  "Rejeitado o Parecer" SSNNS-   parecer pela improcedência
#   200-2  10/12 CCJC  "Aprovado o Parecer Vencedor" NNSSS-  (o parecer só "lido": sem conclusão)
#   200-4  15/12 CCJC  "Aprovado o Parecer" SSSSS-   depois do Plenário: não conta
#   100-9  03/03/2026 CCJC destaque "Mantido o texto."  SN-   (1 × 1: empate)
#
# REP 2/2025: o Plenário votou o parecer vencedor (200-2). Contra ele, Bia (Não → Sim) e Davi
# (Sim → obstrução, que conta como Não) mudaram; Ana e Caio ficaram; Eva se absteve no
# Plenário e Gil não votou na comissão. Contra o parecer rejeitado (200-1), mudariam Ana e Caio.

PESSOAS = [(1, "Ana Lima", "SP"), (2, "Bia Reis", "RJ"), (3, "Caio Luz", "SP"), (4, "Davi Rocha", "MG"), (5, "Eva Prado", "BA"), (6, "Gil Castro", "GO")]
PARTIDOS = {1: [(0, "PT")], 2: [(0, "PT")], 3: [(0, "PL")], 4: [(0, "PL"), (4, "PP")], 5: [(0, "PP")], 6: [(0, "MDB")]}
PL1, REP = ("PL 1/2025", 100), ("REP 2/2025", 200)
REP_PLENARIO = (
    "Rejeitado o Parecer da Comissão de Constituição e Justiça e de Cidadania à Representação nº 2, de 2025, "
    "que conclui pela procedência da representação e pela perda do mandato. Sim: 3; Não: 2; Abstenção: 1; Total: 6."
)


def _v(vid: str, data: str, hora: str, orgao: str, tipo: str, descricao: str, votos: str, prop: tuple[str, int], aprovada: bool | None) -> base.Votacao:
    return base.Votacao(id=vid, data=data, hora=hora, orgao=orgao, tipo=tipo, descricao=descricao, votos=votos, prop=prop[0], prop_id=prop[1], aprovada=aprovada)


def _camara() -> list[base.AnoVotos]:
    M, P = base.MERITO, base.PROCEDIMENTO
    ano_2025 = base.AnoVotos(
        casa="cd",
        ano=2025,
        pessoas=[base.Pessoa(pid, nome, uf, PARTIDOS[pid]) for pid, nome, uf in PESSOAS],
        votacoes=[
            _v("100-1", "2025-03-10", "10:00", "CCJC", P, "Rejeitado o Requerimento de Retirada de Pauta.", "SNNN--", PL1, False),
            _v("100-2", "2025-03-10", "11:00", "CCJC", M, 'Aprovado o Parecer com o seguinte resultado: 3 votos "Sim", 1 voto "Não", 1 Abstenção.', "SSSNA-", PL1, True),
            _v("100-3", "2025-03-11", "16:00", "PLEN", M, "Aprovado o Projeto de Lei nº 1, de 2025.", "SSNNSS", PL1, True),
            _v("101-1", "2025-04-02", "10:00", "CSPCCO", M, "Aprovado o Parecer.", "SS-S--", ("PL 2/2025", 101), True),
            _v("200-3", "2025-12-10", "00:46", "PLEN", M, REP_PLENARIO, "NSSOAS", REP, False),
            _v("200-1", "2025-12-10", "14:00", "CCJC", M, "Rejeitado o Parecer. Resultado: 3 votos \"Sim\", 2 votos \"Não\".", "SSNNS-", REP, False),
            _v("200-2", "2025-12-10", "15:00", "CCJC", M, "Aprovado o Parecer Vencedor do Dep. Davi Rocha com o seguinte resultado: 3 votos \"Sim\", 2 votos \"Não\".", "NNSSS-", REP, True),
            _v("200-4", "2025-12-15", "10:00", "CCJC", M, "Aprovado o Parecer.", "SSSSS-", REP, True),
        ],
    )
    ano_2026 = base.AnoVotos(
        casa="cd",
        ano=2026,
        pessoas=[base.Pessoa(pid, nome, uf, PARTIDOS[pid][:1]) for pid, nome, uf in PESSOAS[:3]],
        votacoes=[_v("100-9", "2026-03-03", "10:00", "CCJC", base.DESTAQUE, "Mantido o texto.", "SN-", PL1, True)],
    )
    ano_2022 = base.AnoVotos(  # legislatura 56: fica de fora (e o arquivo de 2022 não é pedido)
        casa="cd",
        ano=2022,
        pessoas=[base.Pessoa(1, "Ana Lima", "SP", [(0, "PT")])],
        votacoes=[_v("90-1", "2022-05-05", "10:00", "CCJC", M, "Aprovado o Parecer.", "S", ("PL 9/2022", 90), True)],
    )
    return [ano_2025, ano_2026, ano_2022]


LONGO = (
    "Parecer do Relator, Dep. Caio Luz (PL-SP), pela constitucionalidade, juridicidade e técnica legislativa deste e dos "
    "Projetos de Lei nºs 1.175/2015, 943/2022, 1.375/2023, 4.644/2023, 944/2022 e 434/2024, apensados, e, no mérito, "
    "pela rejeição deste e dos apensados."
)
COLUNAS = [
    "id", "uri", "data", "dataHoraRegistro", "siglaOrgao", "aprovacao", "votosSim", "votosNao", "votosOutros", "descricao",
    "ultimaAberturaVotacao_descricao", "ultimaApresentacaoProposicao_descricao", "ultimaApresentacaoProposicao_idProposicao",
]


def _linha(vid: str, orgao: str, apresentacao: str) -> list[Any]:
    return [vid, f"https://dadosabertos.camara.leg.br/api/v2/votacoes/{vid}", "2025-01-01", "2025-01-01T10:00:00", orgao, "1", 0, 0, 0, "", "", apresentacao, 0]


def _arquivos_da_camara() -> dict[str, bytes]:
    return {
        "/arquivos/votacoes/csv/votacoes-2025.csv": _csv(
            COLUNAS,
            [
                _linha("100-1", "CCJC", LONGO),
                _linha("100-2", "CCJC", LONGO),
                _linha("100-3", "PLEN", "Apresentação do PL n. 1/2025, pela Deputada Ana Lima (PT/SP)."),
                _linha("101-1", "CSPCCO", "Parecer da Relatora, Dep. Bia Reis (PT-RJ), pela aprovação."),
                _linha("200-3", "PLEN", 'Apresentação da REP n. 2/2025 (Representação), pela Mesa Diretora, que: "Representa em desfavor".'),
                _linha("200-1", "CCJC", "Parecer do Relator, Dep. Caio Luz (PL-SP), pela improcedência."),
                _linha("200-2", "CCJC", "Lido o Parecer Vencedor pelo Relator, Deputado Davi Rocha."),
                _linha("200-4", "CCJC", "Parecer do Relator, Dep. Caio Luz (PL-SP), pela\nimprocedência."),
                _linha("999-1", "CFT", "Parecer do Relator, Dep. Fulano, pela aprovação."),  # votação simbólica: não está na base
            ],
        ),
        # O destaque não vota a conclusão do relator: o parecer abaixo não aparece nele.
        "/arquivos/votacoes/csv/votacoes-2026.csv": _csv(COLUNAS, [_linha("100-9", "CCJC", "Parecer do Relator, Dep. Caio Luz (PL-SP), pela aprovação.")]),
    }


def _rodar_camara(tmp_path: Path) -> tuple[FetchFalso, dict[str, Any], dict[int, dict[str, Any]]]:
    fetch = FetchFalso(_arquivos_da_camara())
    coletor = fontes.Coletor(tmp_path / "cache", fetch=fetch, pausa=0.0)
    painel, por_pessoa = comissoes.camara(_camara(), coletor, _regras(), 57, hoje=HOJE)
    return fetch, painel, por_pessoa


def test_camara_painel(tmp_path: Path):
    fetch, painel, _ = _rodar_camara(tmp_path)
    # Sete votações de comissão na legislatura (a de 2022 fica de fora); seis de conteúdo (o
    # requerimento não é); cinco divididas (o 101-1 e o 200-4 foram unânimes).
    assert (painel["votacoes"], painel["conteudo"], painel["divididas"]) == (7, 6, 5)
    assert painel["por_comissao"] == [["CCJC", 6], ["CSPCCO", 1]]
    assert painel["ultimas"] == [
        {"id": "100-9", "d": "2026-03-03", "comissao": "CCJC", "prop": "PL 1/2025", "x": "Mantido o texto.", "parecer": "",
         "sim": 1, "nao": 1, "abst": 0, "aprovada": True},
        # O parecer vencedor só foi "lido" (sem conclusão); o Plenário diz a conclusão do mesmo parecer.
        {"id": "200-2", "d": "2025-12-10", "comissao": "CCJC", "prop": "REP 2/2025",
         "x": 'Aprovado o Parecer Vencedor do Dep. Davi Rocha com o seguinte resultado: 3 votos "Sim", 2 votos "Não".',
         "parecer": "pela procedência", "sim": 3, "nao": 2, "abst": 0, "aprovada": True},
        {"id": "200-1", "d": "2025-12-10", "comissao": "CCJC", "prop": "REP 2/2025",
         "x": 'Rejeitado o Parecer. Resultado: 3 votos "Sim", 2 votos "Não".', "parecer": "pela improcedência",
         "sim": 3, "nao": 2, "abst": 0, "aprovada": False},
        {"id": "100-2", "d": "2025-03-10", "comissao": "CCJC", "prop": "PL 1/2025",
         "x": 'Aprovado o Parecer com o seguinte resultado: 3 votos "Sim", 1 voto "Não", 1 Abstenção.', "parecer": "pela rejeição",
         "sim": 3, "nao": 1, "abst": 1, "aprovada": True},
    ]
    assert [u.rsplit("/", 1)[-1] for u in fetch.pedidos] == ["votacoes-2025.csv", "votacoes-2026.csv"]


def test_camara_mudou_compara_so_o_parecer_aprovado_que_o_plenario_votou(tmp_path: Path):
    _, painel, por_pessoa = _rodar_camara(tmp_path)
    # Um caso só: o Plenário do PL 1/2025 votou o projeto, não um parecer.
    assert painel["mudaram_casos"] == [{"prop": "REP 2/2025", "comissao": "CCJC", "dc": "2025-12-10", "dp": "2025-12-10", "mudaram": 2, "ambas": 4}]
    comum = {"prop": "REP 2/2025", "comissao": "CCJC", "dc": "2025-12-10", "dp": "2025-12-10", "parecer": "pela procedência"}
    assert painel["mudaram"] == [
        {"k": "cd-2", "nome": "Bia Reis", "partido": "PT", "uf": "RJ", **comum, "vc": "N", "vp": "S"},
        # A obstrução conta como Não; o partido é o do dia do Plenário.
        {"k": "cd-4", "nome": "Davi Rocha", "partido": "PP", "uf": "MG", **comum, "vc": "S", "vp": "N"},
    ]
    assert por_pessoa[4]["mudou"] == [painel["mudaram"][1]]
    assert por_pessoa[1]["mudou"] == [] and por_pessoa[3]["mudou"] == []


def test_camara_por_deputado(tmp_path: Path):
    _, _, por_pessoa = _rodar_camara(tmp_path)
    assert sorted(por_pessoa) == [1, 2, 3, 4, 5]  # Gil nunca votou em comissão
    ana = por_pessoa[1]
    assert (ana["n"], ana["divididas"], ana["comissoes"]) == (7, 5, [["CCJC", 6], ["CSPCCO", 1]])
    assert [(v["d"], v["prop"], v["v"], v["parecer"], v["venceu"]) for v in ana["votos"]] == [
        ("2026-03-03", "PL 1/2025", "S", "", None),  # 1 × 1: empate
        ("2025-12-10", "REP 2/2025", "N", "pela procedência", False),  # ficou vencida
        ("2025-12-10", "REP 2/2025", "S", "pela improcedência", True),
        ("2025-03-10", "PL 1/2025", "S", "pela rejeição", True),
    ]
    assert ana["votos"][1] == {
        "d": "2025-12-10", "comissao": "CCJC", "prop": "REP 2/2025",
        "x": 'Aprovado o Parecer Vencedor do Dep. Davi Rocha com o seguinte resultado: 3 votos "Sim", 2 votos "Não".',
        "parecer": "pela procedência", "v": "N", "sim": 3, "nao": 2, "abst": 0, "venceu": False,
    }
    eva = por_pessoa[5]
    assert (eva["n"], eva["divididas"], eva["comissoes"]) == (4, 3, [["CCJC", 4]])
    assert [(v["v"], v["venceu"]) for v in eva["votos"]] == [("S", True), ("S", True), ("A", None)]  # abstenção: sem lado


def test_camara_pareceres_guarda_o_texto_curto(tmp_path: Path):
    fetch = FetchFalso(_arquivos_da_camara())
    coletor = fontes.Coletor(tmp_path / "cache", fetch=fetch, pausa=0.0)
    lidos = comissoes.pareceres(coletor, {2025: ["100-2", "200-2", "200-4", "000-0"]}, hoje=HOJE)
    assert sorted(lidos) == ["100-2", "200-2", "200-4"]
    assert lidos["100-2"].conclusao == "pela rejeição"
    assert len(lidos["100-2"].texto) <= comissoes.PARECER_MAX and lidos["100-2"].texto.endswith("…")
    assert lidos["200-2"] == comissoes.Parecer(texto="Lido o Parecer Vencedor pelo Relator, Deputado Davi Rocha.", conclusao="")
    assert lidos["200-4"].texto == "Parecer do Relator, Dep. Caio Luz (PL-SP), pela improcedência."  # a quebra de linha vira espaço


def test_camara_usa_o_cache_e_falha_com_mensagem_clara(tmp_path: Path):
    fetch, _, _ = _rodar_camara(tmp_path)
    coletor = fontes.Coletor(tmp_path / "cache", fetch=fetch, pausa=0.0)
    comissoes.camara(_camara(), coletor, _regras(), 57, hoje=HOJE)
    assert len(fetch.pedidos) == 2  # dentro da validade, nada é baixado de novo

    sem_2026 = FetchFalso({k: v for k, v in _arquivos_da_camara().items() if "2026" not in k})
    coletor = fontes.Coletor(tmp_path / "outro", fetch=sem_2026, pausa=0.0)
    with pytest.raises(comissoes.ComissoesIndisponiveis, match="votacoes-2026.csv indisponível"):
        comissoes.camara(_camara(), coletor, _regras(), 57, hoje=HOJE)


# ── Senado ───────────────────────────────────────────────────────────────────
#
# Cinco senadores. CCJ: 501 (PL 10/2025, dividida), 502 (requerimento sobre o PL 10/2025,
# dividida), 503 (voto em bloco, sem IdentificacaoMateria), 504 (um voto só, que a fonte
# manda como objeto) e 490 (de janeiro de 2023, legislatura 56). CAE: 601 (PL 20/2026,
# dividida, com o senador 102 repetido na lista de votos), que vem de novo na própria CAE e
# na CAS. As demais comissões vêm vazias, como a CEsp.

SENADORES = {101: ("Rita Alves", "PT"), 102: ("Sérgio Dias", "PL"), 103: ("Túlio Braga", "MDB"), 104: ("Vera Campos", "PSD"), 105: ("Leo Brito", "PODEMOS")}


def _voto(codigo: int, qualidade: str) -> dict[str, Any]:
    nome, partido = SENADORES[codigo]
    return {"CodigoParlamentar": str(codigo), "NomeParlamentar": nome, "SiglaPartidoParlamentar": partido, "SiglaCasaParlamentar": "SF", "QualidadeVoto": qualidade, "VotoPresidente": "false"}


def _votacao(codigo: str, quando: str, sigla: str, descricao: str, votos: list[tuple[int, str]], materia: str | None, *, um_objeto: bool = False) -> dict[str, Any]:
    lista = [_voto(c, q) for c, q in votos]
    contagem = {q: sum(1 for _c, x in votos if x == q) for q in "SNA"}
    item: dict[str, Any] = {
        "CodigoVotacao": codigo, "SiglaCasaColegiado": "SF", "CodigoReuniao": "9" + codigo, "DataHoraInicioReuniao": quando,
        "NumeroReuniaoColegiado": "4", "TipoReuniao": "Ordinária", "CodigoColegiado": "34", "SiglaColegiado": sigla,
        "NomeColegiado": f"Comissão {sigla}", "CodigoParlamentarPresidente": "3830", "NomeParlamentarPresidente": "Fulano",
        "DescricaoVotacao": descricao, "Votos": {"Voto": lista[0] if um_objeto else lista},
        "TotalVotosSim": str(contagem["S"]), "TotalVotosNao": str(contagem["N"]), "TotalVotosAbstencao": str(contagem["A"]),
    }
    if materia:
        item["IdentificacaoMateria"] = materia
        item["DescricaoIdentificacaoMateria"] = f"Projeto {materia}"
    return item


def _resposta(votacoes: list[dict[str, Any]]) -> bytes:
    raiz: dict[str, Any] = {"noNamespaceSchemaLocation": "https://legis.senado.leg.br/dadosabertos/dados/VotacoesComissaov1.xsd", "Metadados": {"Versao": "14/11/2026"}}
    if votacoes:
        raiz["Votacoes"] = {"Votacao": votacoes if len(votacoes) > 1 else votacoes[0]}
    return json.dumps({"VotacoesComissao": raiz}).encode("utf-8")


def _respostas_do_senado() -> dict[str, bytes]:
    ccj = [
        _votacao("490", "2023-01-20T10:00:00", "CCJ", "PL 9/2022 (nos termos do Parecer)", [(101, "S"), (102, "N")], "PL 9/2022"),
        _votacao("501", "2025-03-12T10:15:00", "CCJ", "PL 10/2025 (nos termos do Parecer)", [(101, "S"), (102, "N"), (103, "S"), (104, "A")], "PL 10/2025"),
        _votacao("502", "2025-03-12T10:40:00", "CCJ", "Requerimento de adiamento da votação da matéria", [(101, "N"), (102, "S"), (103, "S")], "PL 10/2025"),
        _votacao("503", "2025-04-02T10:00:00", "CCJ", "Itens 6, 7 e 9 nos termos dos relatórios.", [(101, "S"), (102, "S")], None),
        _votacao("504", "2025-05-07T10:00:00", "CCJ", "Emenda nº 4 ao PLP 3/2025", [(102, "S")], "PLP 3/2025", um_objeto=True),
    ]
    cae_601 = _votacao("601", "2026-06-10T10:00:00", "CAE", "Substitutivo ao PL 20/2026", [(101, "N"), (102, "S"), (102, "S"), (105, "S")], "PL 20/2026")
    cae_601["TotalVotosSim"] = "2"  # a fonte conta o senador repetido uma vez só
    cae_601_de_novo = {**cae_601, "DataHoraInicioReuniao": "2026-06-10T10:25:00"}  # a mesma votação, outra hora da reunião
    por_sigla = {"CCJ": ccj, "CAE": [cae_601, cae_601_de_novo], "CAS": [{**cae_601, "SiglaColegiado": "CAS"}]}
    consulta = "dataInicio=20230201&dataFim=20261114"
    return {f"/dadosabertos/votacaoComissao/comissao/{s}?{consulta}": _resposta(por_sigla.get(s, [])) for s in comissoes.SENADO_COMISSOES}


def _rodar_senado(tmp_path: Path, fetch: FetchFalso | None = None) -> tuple[FetchFalso, dict[str, Any], dict[int, dict[str, Any]]]:
    fetch = fetch or FetchFalso(_respostas_do_senado())
    coletor = fontes.Coletor(tmp_path / "cache", fetch=fetch, pausa=0.0)
    painel, por_pessoa = comissoes.senado(coletor, _regras(), 57, inicio="2023-02-01", hoje=HOJE)
    return fetch, painel, por_pessoa


def test_senado_painel(tmp_path: Path):
    fetch, painel, _ = _rodar_senado(tmp_path)
    # 501, 502, 503, 504 e 601 (uma vez só); de conteúdo, as com matéria que não são
    # requerimento (501, 504, 601); divididas, 501, 502 e 601.
    assert (painel["votacoes"], painel["conteudo"], painel["divididas"]) == (5, 3, 3)
    assert painel["por_comissao"] == [["CCJ", 4], ["CAE", 1]]  # a repetida fica com a primeira comissão da lista
    assert painel["ultimas"] == [
        {"id": "601", "d": "2026-06-10", "comissao": "CAE", "prop": "PL 20/2026", "x": "Substitutivo ao PL 20/2026", "parecer": "",
         "sim": 2, "nao": 1, "abst": 0, "aprovada": None},
        {"id": "501", "d": "2025-03-12", "comissao": "CCJ", "prop": "PL 10/2025", "x": "PL 10/2025 (nos termos do Parecer)", "parecer": "",
         "sim": 2, "nao": 1, "abst": 1, "aprovada": None},
    ]
    assert painel["mudaram"] == [] and painel["mudaram_casos"] == []
    # Uma chamada por comissão permanente, com o período da legislatura.
    assert len(fetch.pedidos) == len(comissoes.SENADO_COMISSOES) == 16
    assert all(u.endswith("?dataInicio=20230201&dataFim=20261114") for u in fetch.pedidos)


def test_senado_por_senador(tmp_path: Path):
    _, _, por_pessoa = _rodar_senado(tmp_path)
    assert sorted(por_pessoa) == [101, 102, 103, 104, 105]
    rita = por_pessoa[101]
    assert (rita["n"], rita["divididas"], rita["comissoes"], rita["mudou"]) == (4, 3, [["CCJ", 3], ["CAE", 1]], [])
    assert rita["votos"] == [
        {"d": "2026-06-10", "comissao": "CAE", "prop": "PL 20/2026", "x": "Substitutivo ao PL 20/2026", "parecer": "",
         "v": "N", "sim": 2, "nao": 1, "abst": 0, "venceu": False},
        {"d": "2025-03-12", "comissao": "CCJ", "prop": "PL 10/2025", "x": "PL 10/2025 (nos termos do Parecer)", "parecer": "",
         "v": "S", "sim": 2, "nao": 1, "abst": 1, "venceu": True},
    ]
    # O 102 vem duas vezes na 601 e conta uma; a 504 (o voto que veio como objeto) conta.
    sergio = por_pessoa[102]
    assert (sergio["n"], sergio["divididas"], sergio["comissoes"]) == (5, 3, [["CCJ", 4], ["CAE", 1]])
    assert [(v["v"], v["venceu"]) for v in sergio["votos"]] == [("S", True), ("N", False)]
    assert [(v["v"], v["venceu"]) for v in por_pessoa[104]["votos"]] == [("A", None)]


def test_senado_usa_o_cache_e_falha_com_mensagem_clara(tmp_path: Path):
    fetch, _, _ = _rodar_senado(tmp_path)
    _rodar_senado(tmp_path, fetch)
    assert len(fetch.pedidos) == 16  # dentro da validade, nada é pedido de novo

    fora = FetchFalso(_respostas_do_senado())
    fora.fora_do_ar.add("comissao/CAS?")
    with pytest.raises(comissoes.ComissoesIndisponiveis, match="CAS indisponíveis"):
        _rodar_senado(tmp_path / "fora", fora)

    estranha = _respostas_do_senado()
    estranha["/dadosabertos/votacaoComissao/comissao/CI?dataInicio=20230201&dataFim=20261114"] = b'{"erro": "manutencao"}'
    with pytest.raises(comissoes.ComissoesIndisponiveis, match="CI fora do formato"):
        _rodar_senado(tmp_path / "estranha", FetchFalso(estranha))
