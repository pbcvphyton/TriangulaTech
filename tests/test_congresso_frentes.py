"""Frentes parlamentares: leitura, frentes de causa e as contas (qijournal.congresso.frentes).

Sem rede: os dois CSVs da Câmara saem de um ``fetch`` falso, como em
``tests/fixtures/congresso/fabrica.py``, e as contas são conferidas num Congresso de
mentira com sete deputados, montado aqui mesmo para caber numa tabela.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from qijournal.congresso import base, fontes, frentes, regras
from qijournal.net import FetchError, Response

CAMARA = "https://dadosabertos.camara.leg.br"


# ── fetch falso ──────────────────────────────────────────────────────────────


def _csv(colunas: list[str], linhas: list[list[Any]]) -> bytes:
    """CSV como os da Câmara: BOM, ``;`` e tudo entre aspas."""

    def celula(valor: Any) -> str:
        return '"' + str("" if valor is None else valor).replace('"', '""') + '"'

    texto = "\n".join(";".join(celula(c) for c in linha) for linha in [colunas, *linhas]) + "\n"
    return b"\xef\xbb\xbf" + texto.encode("utf-8")


class FetchFalso:
    """Responde pelo caminho da URL e anota o que foi pedido; o resto falha como a rede."""

    def __init__(self, mapa: dict[str, bytes]) -> None:
        self.mapa = mapa
        self.pedidos: list[str] = []

    def __call__(self, url: str, **_kwargs: Any) -> Response:
        self.pedidos.append(url)
        caminho = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
        if caminho not in self.mapa:
            raise FetchError(url, "HTTP 404", status=404)
        return Response(url=url, status=200, content=self.mapa[caminho], headers={"content-type": "text/csv"})


COLUNAS_FRENTES = [
    "id", "uri", "titulo", "dataCriacao", "idLegislatura", "telefone", "email", "keywords", "idSituacao", "situacao",
    "urlWebsite", "urlDocumento", "coordenador_id", "coordenador_uri", "coordenador_nome", "coordenador_siglaPartido",
    "coordenador_uriPartido", "coordenador_siglaUf", "coordenador_idLegislatura", "coordenador_urlFoto",
]
COLUNAS_MEMBROS = [
    "id", "uri", "titulo", "deputado_.id", "deputado_.uri", "deputado_.uriPartido", "deputado_.nome", "deputado_.siglaUf",
    "deputado_.idLegislatura", "deputado_.urlFoto", "deputado_.codTitulo", "deputado_.titulo", "dataInicio", "dataFim",
]
PDF = "https://www.camara.leg.br/internet/deputado/Frente_Parlamentar/{}-integra.pdf"


def _frente(fid: int, titulo: str, criada: str, leg: int, coordenador: int | None) -> list[Any]:
    situacao = "REQ 1/2023\r\n11/04/23 - Publicada"  # o campo vem com quebras de linha
    return [
        fid, f"{CAMARA}/api/v2/frentes/{fid}", titulo, criada, leg, "", "", "", "", situacao, "",
        PDF.format(fid) + '"', coordenador or "", "", "", "", "", "", leg, "",
    ]


def _membro(fid: int, titulo: str, pid: int | None, leg: int, cod: int, nome: str = "") -> list[Any]:
    rotulo = {14: "Coordenador", 1004: "Membro"}[cod]
    return [
        fid, f"{CAMARA}/api/v2/frentes/{fid}", titulo, pid or "", f"{CAMARA}/api/v2/deputados/{pid}" if pid else "", "",
        nome or f"Deputado {pid}", "SP", leg if pid else "", "", cod, rotulo if pid else "Coordernador", "", "",
    ]


def _arquivos() -> dict[str, bytes]:
    amb, cafe = "Frente Parlamentar Mista Ambientalista", "Frente Parlamentar do Café"
    lista = [
        _frente(54406, amb, "2023-04-11", 57, 1),
        _frente(54531, cafe, "2023-05-02T00:00:00", 57, 6),  # ninguém marcado na lista: fica sem coordenador
        _frente(53000, amb, "2019-03-01", 56, 1),  # outra legislatura
        _frente(54999, "Frente  Parlamentar   Nova ", "", 57, None),  # sem membros nem coordenador
    ]
    membros = [
        _membro(54406, amb, 1, 57, 14),
        _membro(54406, amb, 2, 57, 1004),
        _membro(54406, amb, None, 57, 14, nome="Fulano de Tal"),  # "Coordernador" só com o nome
        _membro(54406, amb, 4, 57, 1004),
        _membro(54406, amb, 2, 57, 1004),  # repetido
        _membro(54406, amb, 9, 56, 1004),  # deputado de outra legislatura na frente desta
        _membro(54531, cafe, 5, 57, 1004),
        _membro(54531, cafe, 1, 57, 1004),
        _membro(53000, amb, 3, 56, 14),
        _membro(77777, "Frente que não está no cadastro", 3, 57, 1004),
    ]
    return {
        "/arquivos/frentes/csv/frentes.csv": _csv(COLUNAS_FRENTES, lista),
        "/arquivos/frentesDeputados/csv/frentesDeputados.csv": _csv(COLUNAS_MEMBROS, membros),
    }


# ── leitura ──────────────────────────────────────────────────────────────────


def test_coletar_le_as_frentes_da_legislatura_com_os_membros(tmp_path: Path):
    fetch = FetchFalso(_arquivos())
    coletor = fontes.Coletor(tmp_path / "cache", fetch=fetch, pausa=0.0)
    lidas = frentes.coletar(coletor, 57)
    assert sorted(lidas) == [54406, 54531, 54999]  # a 53000 é da legislatura 56
    amb = lidas[54406]
    assert amb.titulo == "Frente Parlamentar Mista Ambientalista" and amb.legislatura == 57
    assert amb.criada == "2023-04-11"
    assert amb.documento == PDF.format(54406)  # sem a aspa que sobra no fim
    assert amb.coordenador == 1
    # Sem a linha sem id, sem o repetido e sem o deputado marcado com outra legislatura.
    assert amb.membros == frozenset({1, 2, 4})
    # Ninguém marcado como coordenador na lista: a frente fica sem coordenador (o coordenador_id
    # do cadastro não é confiável: na PROSUL real, aponta para um simples membro).
    cafe = lidas[54531]
    assert (cafe.coordenador, cafe.membros, cafe.criada) == (None, frozenset({1, 5}), "2023-05-02")
    nova = lidas[54999]
    assert (nova.titulo, nova.criada, nova.coordenador, nova.membros) == ("Frente Parlamentar Nova", "", None, frozenset())
    assert [u.rsplit("/", 1)[-1] for u in fetch.pedidos] == ["frentes.csv", "frentesDeputados.csv"]
    # Dentro da validade, o cache responde: nada é baixado de novo.
    frentes.coletar(coletor, 57)
    assert len(fetch.pedidos) == 2


# ── frentes de causa ─────────────────────────────────────────────────────────


def _f(fid: int, titulo: str, membros: set[int], coordenador: int | None = None) -> frentes.Frente:
    return frentes.Frente(fid, titulo, 57, "2023-03-01", coordenador, frozenset(membros), "")


def test_de_causa_casa_pelo_comeco_do_titulo_e_fica_com_a_de_mais_membros(caplog: pytest.LogCaptureFixture):
    r = regras.de_dict(
        {
            "frentes": [
                {"titulo": "Frente Parlamentar Mista Ambientalista", "nome": "Ambientalista", "tema": "ambiente"},
                {"titulo": "Frente Parlamentar Católica", "nome": "Católica"},  # sem tema: "todas"
                {"titulo": "Frente Parlamentar da Agropecuária", "nome": "FPA", "tema": "agro"},
                {"titulo": "Frente Parlamentar do Café", "nome": "Café", "tema": "agro"},
            ]
        }
    )
    lidas = {
        f.id: f
        for f in (
            _f(10, "Frente Parlamentar Mista Ambientalista", {1, 2, 3}),
            _f(11, "Frente Parlamentar Mista Ambientalista e do Desenvolvimento Sustentável", {1, 2, 3, 4, 5}),
            _f(12, "FRENTE PARLAMENTAR DA AGROPECUÁRIA - FPA", {1}),
            _f(13, "Frente Parlamentar do Café", {1, 2}),
            _f(14, "Frente Parlamentar do Café", {3, 4}),  # registro repetido, mesmo tamanho: vale o de id menor
            _f(15, "Frente Parlamentar Mista da Agricultura Familiar", {1}),
        )
    }
    with caplog.at_level(logging.INFO, logger="qijournal.congresso.frentes"):
        pares = frentes.de_causa(lidas, r)
    assert [(c.nome, c.tema, f.id) for c, f in pares] == [("Ambientalista", "ambiente", 11), ("FPA", "agro", 12), ("Café", "agro", 13)]
    assert "Católica" in caplog.text  # a que não casou vai para o log


# ── lados ────────────────────────────────────────────────────────────────────


def test_corte_vem_da_faixa_do_centro():
    assert frentes.corte(regras.de_dict({})) == (-20.0, 20.0)
    campos = [
        {"id": "esquerda", "ate": -50},
        {"id": "centro_esquerda", "ate": -30},
        {"id": "centro", "ate": 10},
        {"id": "direita", "ate": 100},
    ]
    assert frentes.corte(regras.de_dict({"campos": campos})) == (-30.0, 10.0)
    sem_centro = [{"id": "esquerda", "ate": 0}, {"id": "direita", "ate": 100}]
    assert frentes.corte(regras.de_dict({"campos": sem_centro})) == frentes.CORTE_PADRAO
    limites = (-20.0, 20.0)
    assert [frentes.lado(s, limites) for s in (-100, -20, -19.9, 0, 20, 20.1, 100)] == ["e", "e", "c", "c", "c", "d", "d"]


# ── as contas ────────────────────────────────────────────────────────────────
#
# Sete deputados (o 6 saiu da Câmara; o 7 não assina nada) e, em 2026, seis votações
# divisivas do tema "ambiente" (a1 a a6) e uma de "economia" (e1). As de baixo não
# contam: requerimento, consenso, secreta, de comissão e uma de 2022 (legislatura 56).
#
#   pessoa:   1 Ana  2 Bia  3 Caio  4 Davi  5 Eva  6 Fábio  7 Gil
#   partido:  PT     PT     PL      PL      PP     PP       MDB
#
#   a1  S S N N - N S      a4  S S N N - - S      e1  S S N N N N S
#   a2  S S N N S N -      a5  S S N N S N S
#   a3  N N S S S S -      a6  S S N O N N S      (O = obstrução, conta como Não)
#
# Ambientalista (tema ambiente, coordenadora Ana): Ana, Bia, Davi, Eva e Fábio.
# Evangélica (todas, coordenador Caio): Ana, Bia, Caio, Davi e Eva.
# Café (não é de causa, coordenadora Eva): Ana e Eva.

PESSOAS = [(1, "Ana Lima", "PT", "SP"), (2, "Bia Reis", "PT", "RJ"), (3, "Caio Luz", "PL", "SP"), (4, "Davi Rocha", "PL", "MG"),
           (5, "Eva Prado", "PP", "BA"), (6, "Fábio Nunes", "PP", "PE"), (7, "Gil Castro", "MDB", "GO")]


def _regras_analise() -> regras.Regras:
    return regras.de_dict(
        {
            "legislaturas": [
                {"numero": 56, "inicio": "2019-02-01", "fim": "2023-01-31"},
                {"numero": 57, "inicio": "2023-02-01", "fim": "2027-01-31"},
            ],
            "polos": [{"inicio": "2019-01-01", "esquerda": ["PT"], "direita": ["PL"]}],
            "limiares": {"coesao_polo": 0.75, "minimo_polo": 2, "minimo_divisivas": 2, "minimo_partido": 2},
            "frentes": [
                {"titulo": "Frente Parlamentar Mista Ambientalista", "nome": "Ambientalista", "tema": "ambiente"},
                {"titulo": "Frente Parlamentar Evangélica", "nome": "Evangélica", "tema": "todas"},
            ],
        }
    )


def _ano(ano: int, votacoes: list[tuple[str, str, list[str], str, dict[str, Any]]]) -> base.AnoVotos:
    """Cada votação: (id, tipo, temas, votos das pessoas 1 a 7, campos extras)."""
    return base.AnoVotos(
        casa="cd",
        ano=ano,
        pessoas=[base.Pessoa(pid, nome, uf, [(0, sigla)]) for pid, nome, sigla, uf in PESSOAS],
        votacoes=[
            base.Votacao(
                **{"orgao": base.PLENARIO, **extra},
                id=vid, data=f"{ano}-03-{k + 1:02d}", descricao="Aprovado o Projeto.", tipo=tipo, votos=votos, temas=temas,
            )
            for k, (vid, tipo, temas, votos, extra) in enumerate(votacoes)
        ],
    )


def _anos() -> list[base.AnoVotos]:
    amb, M, D = ["ambiente"], base.MERITO, base.DESTAQUE
    return [
        _ano(2022, [("x5", M, amb, "SSNNSNS", {})]),
        _ano(
            2026,
            [
                ("a1", M, amb, "SSNN-NS", {}),
                ("a2", D, amb, "SSNNSN-", {}),
                ("a3", M, amb, "NNSSSS-", {}),
                ("x1", base.PROCEDIMENTO, amb, "SSNNSNS", {}),
                ("a4", M, amb, "SSNN--S", {}),
                ("x2", M, amb, "SSSSSSS", {}),
                ("a5", M, ["ambiente", "economia"], "SSNNSNS", {}),
                ("x3", M, amb, "SSNNSNS", {"secreta": True}),
                ("a6", D, amb, "SSNONNS", {}),
                ("x4", M, amb, "SSNNSNS", {"orgao": "CCJC"}),
                ("e1", M, ["economia"], "SSNNNNS", {}),
            ],
        ),
    ]


def _analise() -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    r = _regras_analise()
    lidas = {
        f.id: f
        for f in (
            _f(100, "Frente Parlamentar Mista Ambientalista", {1, 2, 4, 5, 6}, coordenador=1),
            _f(200, "Frente Parlamentar Evangélica do Congresso Nacional", {1, 2, 3, 4, 5}, coordenador=3),
            _f(300, "Frente Parlamentar do Café", {1, 5}, coordenador=5),
        )
    }
    pessoas = {pid: {"k": f"cd-{pid}", "nome": nome, "partido": sigla, "uf": uf, "ex": pid != 6} for pid, nome, sigla, uf in PESSOAS}
    # No tema, só quem tem 5 votações ou mais (a Eva, com 4 em ambiente, fica sem).
    saldos_tema = {1: {"ambiente": (-100.0, 6)}, 2: {"ambiente": (20.0, 6)}, 4: {"ambiente": (100.0, 6)}, 6: {"ambiente": (-60.0, 5)}}
    saldo_geral = {1: (-90.0, 7), 2: (-85.0, 7), 3: (95.0, 7), 4: (100.0, 7), 5: (5.0, 5), 6: (-50.0, 6), 7: (0.0, 7)}
    causas = frentes.de_causa(lidas, r)
    return frentes.analisar(
        lidas, causas, _anos(), r, 57, pessoas=pessoas, saldos_tema=saldos_tema, saldo_geral=saldo_geral
    )


def test_painel_conta_quantas_frentes_cada_um_assina():
    """Em exercício: Ana 3, Bia 2, Caio 1, Davi 2, Eva 3, Gil 0 (o Fábio saiu): mediana 2,
    média 11 ÷ 6 = 1,8, e um que não assina nenhuma."""
    painel, _ = _analise()
    assert (painel["legislatura"], painel["total"], painel["mediana"], painel["media"], painel["nenhuma"]) == (57, 3, 2, 1.8, 1)
    assert [(p["k"], p["n"]) for p in painel["mais_assinam"]] == [("cd-1", 3), ("cd-5", 3), ("cd-2", 2), ("cd-4", 2), ("cd-3", 1)]
    assert painel["mais_assinam"][0] == {"k": "cd-1", "nome": "Ana Lima", "partido": "PT", "uf": "SP", "n": 3}


def test_ambientalista_com_a_coordenadora_no_tema():
    """Seis votações de ambiente contam (a1 a a6), e a Ana votou em todas. Bia: 6 de 6 com
    ela; Davi: 0 de 6; Eva: 2 de 4 (fora da lista "contra", que pede 5); Fábio saiu e não
    entra no painel. Com a coordenadora: (6 + 0 + 2) ÷ (6 + 6 + 4) = 50%."""
    painel, _ = _analise()
    amb = painel["causas"][0]
    assert (amb["id"], amb["nome"], amb["titulo"], amb["tema"]) == (100, "Ambientalista", "Frente Parlamentar Mista Ambientalista", "ambiente")
    assert (amb["membros"], amb["em_exercicio"], amb["votacoes"], amb["com_coord"]) == (5, 4, 6, 50.0)
    assert amb["coord"] == {"k": "cd-1", "nome": "Ana Lima", "partido": "PT", "uf": "SP", "ex": 1, "saldo": -100, "n": 6}
    # Ana −100 (esquerda), Bia +20 (o limite do dividido), Davi +100 (direita), Eva sem saldo no tema.
    assert amb["lados"] == {"e": 1, "c": 1, "d": 1, "sem": 1}
    # PT: 2 dos 2 em exercício; PL: 1 de 2; PP: 1 de 1 (o Fábio saiu).
    assert amb["partidos"] == [["PT", 2, 2], ["PL", 1, 2], ["PP", 1, 1]]
    assert [(c["k"], c["pct"], c["k_n"], c["n"]) for c in amb["contra"]] == [("cd-4", 0.0, 0, 6), ("cd-2", 100.0, 6, 6)]
    assert amb["contra"][0] == {"k": "cd-4", "nome": "Davi Rocha", "partido": "PL", "uf": "MG", "pct": 0.0, "k_n": 0, "n": 6}


def test_frente_de_todas_conta_todas_as_divisivas_e_o_saldo_geral():
    """Evangélica (todas): as sete divisivas, com o Caio. Ana e Bia: 0 de 7 (empate: pelo
    nome); Eva: 3 de 5; Davi: 7 de 7. Com o coordenador: 10 ÷ 26 = 38,5%."""
    painel, _ = _analise()
    ev = painel["causas"][1]
    assert (ev["id"], ev["tema"], ev["membros"], ev["em_exercicio"], ev["votacoes"], ev["com_coord"]) == (200, "todas", 5, 5, 7, 38.5)
    assert ev["coord"] == {"k": "cd-3", "nome": "Caio Luz", "partido": "PL", "uf": "SP", "ex": 1, "saldo": 95, "n": 7}
    assert ev["lados"] == {"e": 2, "c": 1, "d": 2, "sem": 0}
    assert [(c["k"], c["pct"], c["n"]) for c in ev["contra"]] == [("cd-1", 0.0, 7), ("cd-2", 0.0, 7), ("cd-5", 60.0, 5), ("cd-4", 100.0, 7)]
    assert ev["partidos"] == [["PL", 2, 2], ["PT", 2, 2], ["PP", 1, 1]]


def test_por_deputado():
    _, por = _analise()
    assert por[7] == {"n": 0, "de": 3}  # não assina nenhuma
    assert por[1] == {
        "n": 3,
        "de": 3,
        "coordena": [{"id": 100, "titulo": "Frente Parlamentar Mista Ambientalista"}],
        "causas": [
            {"id": 100, "nome": "Ambientalista", "tema": "ambiente", "coord": 1, "com": None, "n": 6},
            {"id": 200, "nome": "Evangélica", "tema": "todas", "coord": 0, "com": 0.0, "n": 7},
        ],
    }
    # A Eva coordena uma frente que não é de causa; na Ambientalista tem só 4 votações (sem porcentagem).
    assert por[5]["coordena"] == [{"id": 300, "titulo": "Frente Parlamentar do Café"}]
    assert por[5]["causas"] == [
        {"id": 100, "nome": "Ambientalista", "tema": "ambiente", "coord": 0, "com": None, "n": 4},
        {"id": 200, "nome": "Evangélica", "tema": "todas", "coord": 0, "com": 60.0, "n": 5},
    ]
    # Quem saiu da Câmara fica fora do painel, mas tem a ficha: 0 de 5 com a coordenadora.
    assert por[6]["causas"] == [{"id": 100, "nome": "Ambientalista", "tema": "ambiente", "coord": 0, "com": 0.0, "n": 5}]
    assert sorted(por) == [1, 2, 3, 4, 5, 6, 7]


def test_a_base_de_comparacao_e_quem_nao_assina():
    """Quem está em exercício e não assina a Ambientalista: Caio (6 votações, nenhuma igual à
    da coordenadora) e Gil (4 votações, as 4 iguais): 4 de 10 = 40%. É o número que diz se a
    assinatura separa alguém de alguém (quem assina: 50%)."""
    painel, _ = _analise()
    amb = painel["causas"][0]
    assert (amb["com_coord"], amb["sem_assinar"]) == (50.0, 40.0)
