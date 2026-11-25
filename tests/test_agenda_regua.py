"""Testes da régua da Agenda da semana (``qijournal/agenda/regua.py``): a nota de 0 a 85."""

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

from qijournal.agenda import contrato, regras, regua
from tests.fixtures import agenda as fx

REGRAS = regras.carregar()


def _sessao(agenda: contrato.Agenda, sessao_id: str) -> contrato.SessaoAgenda:
    return next(s for s in agenda.sessoes if s.id == sessao_id)


def _item(sessao: contrato.SessaoAgenda, identificador: str) -> contrato.ItemAgenda:
    return next(i for i in sessao.itens if i.identificador == identificador)


def _nota(agenda: contrato.Agenda, sessao_id: str, identificador: str) -> regua.Nota:
    sessao = _sessao(agenda, sessao_id)
    return regua.nota_do_item(sessao, _item(sessao, identificador), REGRAS)


# ── as notas travadas (dados reais de 23 a 27/11/2026) ───────────────────────────────────────


@pytest.mark.parametrize(
    ("lado", "sessao_id", "identificador", "esperada", "partes"),
    [
        # PLP em votação no Plenário da Câmara
        ("congresso", fx.CAMARA_PLENARIO, "PLP 299/2026", 85, (30, 30, 25)),
        # tema de repercussão geral no Plenário do STF com retorno de vista
        ("tribunais", fx.STF_PLENARIO, "RE 1326541", 80, (30, 30, 20)),
        # ação direta de norma federal com destaque no Plenário do STF
        ("tribunais", fx.STF_PLENARIO, "ADI 6002", 75, (30, 30, 15)),
        # projeto de lei no Plenário do Senado com pauta confirmada
        ("congresso", "senado-594181", "PL 6461/2019", 63, (30, 18, 15)),
        # PEC na CCJ do Senado, pautada
        ("congresso", "senado-reuniao-15077", "PEC 1/2025", 55, (15, 30, 10)),
        # medida provisória com o prazo na semana
        ("congresso", "congresso-prazo-mpv-1381-2026", "MPV 1381/2026", 70, (15, 30, 25)),
    ],
)
def test_notas_esperadas(lado: str, sessao_id: str, identificador: str, esperada: int, partes: tuple[int, int, int]):
    agenda = fx.congresso() if lado == "congresso" else fx.tribunais()
    nota = _nota(agenda, sessao_id, identificador)
    assert nota.total == esperada
    assert (nota.foro, nota.alcance, nota.estagio) == partes
    assert nota.total == nota.foro + nota.alcance + nota.estagio


def test_a_conta_vem_por_extenso():
    nota = _nota(fx.congresso(), fx.CAMARA_PLENARIO, "PLP 299/2026")
    assert nota.conta == "Plenário (30) + projeto de lei complementar (30) + em votação (25) = 85"
    assert nota.estagio_usado == "em_votacao"
    vista = _nota(fx.tribunais(), fx.STF_PLENARIO, "RE 1326541")
    assert vista.conta == "Plenário (30) + repercussão geral (30) + retorno de vista (20) = 80"


def test_teto_de_85_em_todos_os_itens_reais():
    assert REGRAS.regua.maximo == 85
    for agenda in (fx.congresso(), fx.tribunais(), fx.camara_encerrada()):
        for sessao in agenda.sessoes:
            for nota, _item_ in regua.notas_da_sessao(sessao, REGRAS):
                assert 0 <= nota.total <= 85


def test_a_soma_nunca_passa_do_maximo_da_regua():
    # Pesos fora da escala (arquivo editado à mão) não fazem a nota passar do máximo declarado.
    inflada = replace(REGRAS, regua=replace(REGRAS.regua, foro=(("plenario", 30),), alcance=(("plp", 30),), estagio=(("em_votacao", 25), ("urgencia", 25))))
    sessao = _sessao(fx.congresso(), fx.CAMARA_PLENARIO)
    assert regua.nota_do_item(sessao, _item(sessao, "PLP 299/2026"), inflada).total == 85


def test_em_votacao_so_vale_com_a_sessao_em_andamento_nesta_coleta():
    sessao = _sessao(fx.congresso(), fx.CAMARA_PLENARIO)
    item = _item(sessao, "PLP 299/2026")
    assert "em_votacao" in item.estagios and sessao.situacao == "em_andamento"
    # Sessão mantida do arquivo anterior: o "em andamento" é velho e a régua não o conta.
    mantida = replace(sessao, como_pautado_em="2026-11-23")
    nota = regua.nota_do_item(mantida, item, REGRAS)
    assert nota.estagio_usado != "em_votacao"
    assert nota.total < 85
    assert "em_votacao" not in regua.estagios_validos(mantida, item)


def test_vale_o_maior_estagio_entre_os_do_item():
    sessao = _sessao(fx.tribunais(), fx.STF_PLENARIO)
    item = replace(_item(sessao, "ADI 6002"), estagio="referendo_de_cautelar", outros_estagios=("destaque", "retorno_de_vista"))
    nota = regua.nota_do_item(sessao, item, REGRAS)
    assert nota.estagio == 20 and nota.estagio_usado == "retorno_de_vista"


def test_linha_sem_itens_vale_so_o_foro():
    sessao = _sessao(fx.tribunais(), "stf-plenario-2026-11-26")
    assert not sessao.itens and sessao.pauta == "nao_publicada"
    nota = regua.nota_da_linha(sessao, REGRAS)
    assert (nota.total, nota.foro, nota.alcance, nota.estagio) == (30, 30, 0, 0)
    assert nota.conta == "Plenário (30) + pauta ainda não publicada (0) = 30"


def test_a_linha_vale_a_nota_do_melhor_item_e_a_relevancia_so_desempata():
    sessao = _sessao(fx.tribunais(), fx.STF_PLENARIO)
    assert regua.nota_da_linha(sessao, REGRAS).total == 80
    plenario = _sessao(fx.congresso(), "senado-594181")
    a, b = plenario.itens[0], plenario.itens[1]
    trocada = replace(plenario, itens=(replace(a, relevancia=10, ordem=1), replace(b, relevancia=90, ordem=2)))
    pares = regua.notas_da_sessao(trocada, REGRAS)
    assert pares[0][0].total == pares[1][0].total  # mesma nota: a relevância não soma
    assert pares[0][1].identificador == b.identificador  # e só desempata


def test_a_midia_nao_pontua():
    # A régua não recebe a ligação com a mídia: só a sessão, o item e as regras.
    assert list(inspect.signature(regua.nota_do_item).parameters) == ["sessao", "item", "regras"]
    fonte = inspect.getsource(regua)
    assert "ligar" not in fonte and "veiculo" not in fonte.lower()
