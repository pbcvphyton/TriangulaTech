"""``qijournal/tribunais/agenda.py``: a agenda da semana dos Tribunais, função pura, sem rede.

Confere: a seleção do STJ sobre o arquivo completo de pautas (o que entra, o que fica só em
contagem e por quê); os sinais do Plenário presencial do STF (vista, referendo, destaque, tema de
repercussão geral, hora, ordem, dia sem pauta, dia não lido); as contagens das sessões virtuais; a
privacidade (pessoa física, advogado, empresa fora do catálogo e processo em segredo nunca saem); a
semana acumulada (rodar na quinta mantém a sessão de quarta); o teto de tamanho; e que os mesmos
dados dão os mesmos bytes.

Amostras (todas com pessoas mascaradas):

- ``tests/fixtures/tribunais/stj/pautas_futuras_20261124_agenda.json.gz``: 17 itens reais do
  arquivo ``pautas_futuras_20261124.json.gz`` (Portal de Dados Abertos do STJ, baixado em
  24/11/2026): Primeira e Quarta Turmas de 24/11; Corte Especial de 25/11 (a proposta de
  relevância do AREsp 3296604, dois agravos da Vice-Presidência, um agravo interno e um processo em
  segredo); o lote de recursos principais da Corte Especial de 02/12 (REsp 2034211, Tema 1254; REsp
  2235680, Tema 1448; APn 1131; EAREsp 2616058); a Segunda Seção de 03/12 (REsp 2226097, Tema 1404)
  e a de 09/12 (REsp 2272537, Tema 1443). Pessoa física, advogado, número de OAB e empresa fora do
  catálogo de litigantes foram mascarados; os processos em segredo perderam também o número.
- ``stj/temas_agenda.csv`` e ``stj/processos_agenda.csv``: as linhas reais de ``Temas.csv`` e
  ``Processos.csv`` (baixados em 24/11/2026) dos precedentes ligados a esses processos: os Temas
  1254, 1404, 1443 e 1448 e as quatro controvérsias já vinculadas a eles.
- ``stf/pauta_sessao-presencial_2026-11-25.json`` e ``stf/pauta_calendarios_2026-11.json``: as
  respostas reais do serviço de pauta do STF (o resumo oficial vem cortado em 60 caracteres na
  amostra). A resposta de um dia sem processos e o calendário de dezembro são HIPÓTESES do teste:
  o portal recusa o acesso de fora do fluxo e não há resposta real guardada.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date
from typing import Any

import pytest

from qijournal.agenda import contrato
from qijournal.net import FetchError
from qijournal.tribunais import agenda, modelo, partes, stf, stj
from qijournal.tribunais.areas import Classificador
from qijournal.tribunais.tpu import TPU
from tests.fixtures import tribunais as fx
from tests.fixtures.tribunais import fabrica
from tests.fixtures.tribunais import stf as fx_stf

STJ = fx.PASTA / "stj"
HOJE = "2026-11-24"  # terça-feira; a janela de coleta vai de 23/11 a 06/12
JANELA = ("2026-11-23", "2026-12-06")
GERADO = "2026-11-24T16:30:00Z"
LINK_STF = "https://portal.stf.jus.br/pauta/"
LINK_STJ = "https://dadosabertos.web.stj.jus.br/dataset/pautas-futuras"
DIA_SEM_PROCESSOS = {"data": "26/11/2026", "tipo": "PRESENCIAL", "colegiados": []}  # hipótese: não há resposta real guardada
CALENDARIO_VAZIO = {"diasPresenciais": [], "julgamentosVirtuais": []}  # hipótese (dezembro)


@pytest.fixture(scope="module")
def regras() -> modelo.Regras:
    return modelo.carregar_regras()


@pytest.fixture(scope="module")
def catalogo() -> partes.Catalogo:
    return partes.carregar_catalogo()


@pytest.fixture(scope="module")
def pauta_stj(regras: modelo.Regras) -> list[dict[str, Any]]:
    """O arquivo de pautas lido como na coleta: sem tipificar as partes (quem tipifica é a agenda)."""
    return stj.ler_pautas(STJ / "pautas_futuras_20261124_agenda.json.gz", regras, Classificador(regras, TPU.carregar()))


@pytest.fixture(scope="module")
def temas_stj(regras: modelo.Regras) -> list[dict[str, Any]]:
    return stj.montar_temas(stj.ler_temas(STJ / "temas_agenda.csv"), stj.ler_processos_precedentes(STJ / "processos_agenda.csv"), regras,
                            Classificador(regras, TPU.carregar()))


class Servico:
    """O serviço de pauta do STF de mentira: as duas respostas reais e, para o resto, o que o teste diz."""

    def __init__(self, extras: dict[str, Any] | None = None) -> None:
        # o que o teste diz vem primeiro; depois, as duas hipóteses declaradas no topo
        padrao = {"calendarios&mes=12&ano=2026": CALENDARIO_VAZIO, "sessao-presencial&data=26/11/2026": DIA_SEM_PROCESSOS}
        self.respostas = [*(extras or {}).items(), *padrao.items()]

    def json(self, url: str, *, validade_h: float) -> Any:
        for pedaco, resposta in self.respostas:
            if pedaco in url:
                if isinstance(resposta, Exception):
                    raise resposta
                return resposta
        if "calendarios&mes=11&ano=2026" in url:
            return json.loads(fx_stf.ler("pauta_calendarios_2026-11.json"))
        if "sessao-presencial&data=25/11/2026" in url:
            return json.loads(fx_stf.ler("pauta_sessao-presencial_2026-11-25.json"))
        raise FetchError(url, "HTTP 404", status=404)


@pytest.fixture(scope="module")
def pauta_stf(regras: modelo.Regras) -> dict[str, Any]:
    return stf.coletar_pauta(Servico(), regras, hoje=date(2026, 11, 23), dias=14)


# O que o acervo sabe dos processos do Plenário de 25/11 (como contas.cruzamento_da_pauta entrega;
# os grupos e as áreas são os dos dossiês publicados em 23/11/2026, sem nome de pessoa).
ACERVO_STF = {
    "processos": {
        "stf-ADI-6002": {"area": "constitucional", "pessoa_fisica": False, "partes": [
            {"polo": "ativo", "tipo": "pj", "grupo": "nome-ordem-dos-advogados-do-brasil-conselho-federal", "nome": "ORDEM DOS ADVOGADOS DO BRASIL CONSELHO FEDERAL"},
            {"polo": "passivo", "tipo": "ente", "grupo": "congresso-nacional", "nome": "CONGRESSO NACIONAL"}]},
        "stf-RE-1326541": {"area": "administrativo", "pessoa_fisica": True, "partes": [
            {"polo": "ativo", "tipo": "ente", "grupo": "estado-sp", "nome": "ESTADO DE SÃO PAULO"}]},
        "stf-ARE-1502069": {"area": "administrativo", "pessoa_fisica": True, "partes": [
            {"polo": "ativo", "tipo": "ente", "grupo": "municipio-riolandia", "nome": "MUNICIPIO DE RIOLANDIA"}]},
        "stf-ADI-7807": {"area": "constitucional", "pessoa_fisica": False, "partes": [
            {"polo": "ativo", "tipo": "ente", "grupo": "estado-mt", "nome": "ESTADO DE MATO GROSSO"},
            {"polo": "passivo", "tipo": "ente", "grupo": "assembleia-mt", "nome": "ASSEMBLEIA LEGISLATIVA DO ESTADO DE MATO GROSSO"}]},
    },
    "segredo": [],
}
# Os dois temas de repercussão geral, como stf.temas_rg os entrega (o título é o oficial).
TEMAS_STF = [
    {"numero": 1218, "leading_case": {"classe": "RE", "numero": "1326541", "incidente": 6190000},
     "titulo": "Adoção do piso nacional estipulado pela Lei federal 11.738/2008 como base para o vencimento inicial da carreira do magistério "
               "da Educação Básica estadual, com reflexos nos demais níveis, faixas e classes da carreira escalonada."},
    {"numero": 1324, "leading_case": {"classe": "ARE", "numero": "1502069", "incidente": 6950000},
     "titulo": "Revisão de salário-base de professor municipal, com base no valor de atualização do piso nacional da educação fixado em "
               "Portaria do Ministério da Educação – MEC."},
]


def _fonte(chave: str, inst: str, data: str = "2026-11-24", **mais: Any) -> contrato.FonteAgenda:
    return agenda.fonte(chave, inst, {"url": LINK_STF if inst == "stf" else LINK_STJ, "ok": True, "data_dados": data, "coletado_em": GERADO,
                                      "aviso": None, "velho_dias": 4, **mais})


def _entrada_stf(pauta: Any, **mais: Any) -> agenda.Entrada:
    return agenda.Entrada(fontes=(_fonte("stf_pauta", "stf"),), pauta=pauta, temas=TEMAS_STF, acervo=ACERVO_STF, **mais)


def _entrada_stj(pauta: Any, temas: Any) -> agenda.Entrada:
    return agenda.Entrada(fontes=(_fonte("stj_pautas", "stj"), _fonte("stj_precedentes", "stj", "2026-11-23", velho_dias=8)), pauta=pauta, temas=temas)


def _montar(regras: modelo.Regras, catalogo: partes.Catalogo, *, hoje: str = HOJE, **entradas: agenda.Entrada) -> contrato.Agenda:
    saida = agenda.montar(regras, hoje=hoje, gerado_em=GERADO, catalogo=catalogo, **entradas)
    assert saida is not None
    return saida


def _sessao(a: contrato.Agenda, ident: str) -> contrato.SessaoAgenda:
    return next(s for s in a.sessoes if s.id == ident)


def _item(s: contrato.SessaoAgenda, identificador: str) -> contrato.ItemAgenda:
    return next(i for i in s.itens if i.identificador == identificador)


# ── STJ: a seleção sobre o arquivo completo ──────────────────────────────────────────────────


def test_stj_entra_tema_afetado_proposta_e_principal_de_corte_e_secao(regras, catalogo, pauta_stj, temas_stj) -> None:
    sessoes, estado = agenda.sessoes_do_stj(pauta_stj, regras, catalogo, janela=JANELA, link=LINK_STJ, temas=temas_stj)
    por_id = {s.id: s for s in sessoes}
    assert list(por_id) == ["stj-t1-2026-11-24", "stj-t4-2026-11-24", "stj-ce-2026-11-25", "stj-ce-2026-12-02", "stj-s2-2026-12-03"]
    # Turmas: nada entra (recurso sem tema, agravo interno, segredo, pedido de reconsideração): só contagem
    assert not por_id["stj-t1-2026-11-24"].itens and dict(por_id["stj-t1-2026-11-24"].fora) == {"segredo": 1, "recurso_interno": 1, "sem_tema": 1}
    assert not por_id["stj-t4-2026-11-24"].itens and dict(por_id["stj-t4-2026-11-24"].fora) == {"outros": 1}
    # Corte Especial de 25/11: só a proposta de relevância; os agravos da Vice contam à parte dos recursos internos
    ce = por_id["stj-ce-2026-11-25"]
    assert [(i.identificador, i.alcance, i.estagio) for i in ce.itens] == [("AREsp 3296604", "afetacao", "pautado")]
    assert dict(ce.fora) == {"segredo": 1, "recurso_interno": 1, "vice_presidencia": 2} and ce.total_na_fonte == 5
    assert (ce.orgao, ce.orgao_sigla, ce.foro, ce.tipo, ce.situacao, ce.pauta) == ("Corte Especial", "CE", "corte_especial", "julgamento", "agendada", "publicada")
    # Corte Especial de 02/12: os dois repetitivos, a ação penal e os embargos de divergência
    lote = por_id["stj-ce-2026-12-02"]
    assert {i.identificador: i.alcance for i in lote.itens} == {
        "REsp 2034211": "repetitivo", "REsp 2235680": "repetitivo", "APn 1131": "acao_originaria", "EAREsp 2616058": "embargos_de_divergencia"}
    assert dict(lote.fora) == {"vice_presidencia": 1}
    # Segunda Seção de 03/12: o repetitivo e o recurso principal sem tema (caso individual, mas de Seção)
    assert {i.identificador: i.alcance for i in por_id["stj-s2-2026-12-03"].itens} == {"REsp 2226097": "repetitivo", "REsp 2026255": "individual"}
    assert dict(estado.contagens) == {"itens": 16, "propostas": 1, "repetitivos": 3, "sessoes": 5}
    assert estado.pauta_publicada_ate == "2026-12-09"


def test_stj_o_registro_liga_a_pauta_ao_tema_e_a_controversia_nao_conta(regras, catalogo, pauta_stj, temas_stj) -> None:
    sessoes, _estado = agenda.sessoes_do_stj(pauta_stj, regras, catalogo, janela=JANELA, link=LINK_STJ, temas=temas_stj)
    lote = next(s for s in sessoes if s.id == "stj-ce-2026-12-02")
    herdeiros = _item(lote, "REsp 2034211")
    assert herdeiros.tema is not None and (herdeiros.tema.numero, herdeiros.tema.tipo) == (1254, "repetitivo")
    assert herdeiros.tema.titulo.startswith("Definir se ocorre ou não a prescrição para a habilitação de herdeiros")
    assert herdeiros.relator == contrato.Relator(nome="Humberto Martins")
    assert herdeiros.link == "https://processo.stj.jus.br/processo/pesquisa/?tipoPesquisa=tipoPesquisaNumeroRegistro&termo=202203340439"
    assert _item(lote, "REsp 2235680").tema.numero == 1448  # type: ignore[union-attr]
    # a controvérsia 372 (já vinculada ao Tema 1254) traz o mesmo registro: não é tema afetado
    assert {t["numero"] for t in stj.temas_por_registro(temas_stj)["202203340439"]} == {1254}
    # sem os precedentes ligados, o mesmo recurso é só um caso individual de Corte Especial
    sem_temas, _ = agenda.sessoes_do_stj(pauta_stj, regras, catalogo, janela=JANELA, link=LINK_STJ, temas=[])
    solto = _item(next(s for s in sem_temas if s.id == "stj-ce-2026-12-02"), "REsp 2034211")
    assert (solto.alcance, solto.tema) == ("individual", None)


def test_stj_nao_afirma_hora_modalidade_assunto_nem_ordem(regras, catalogo, pauta_stj, temas_stj) -> None:
    sessoes, estado = agenda.sessoes_do_stj(pauta_stj, regras, catalogo, janela=JANELA, link=LINK_STJ, temas=temas_stj)
    todas = [*sessoes, estado.proximo]
    assert all(s is not None and s.hora is None and s.modalidade is None for s in todas)
    itens = [i for s in todas if s is not None for i in s.itens]
    assert itens and all(i.ementa is None and i.ordem is None and i.resultado is None and i.estagio == "pautado" for i in itens)
    # a área só existe quando vem do tema (ou do acervo): a pauta não traz assunto
    assert {i.identificador: i.area for i in itens}["REsp 2226097"] == "civil" and {i.identificador: i.area for i in itens}["APn 1131"] is None
    com_acervo, _ = agenda.sessoes_do_stj(pauta_stj, regras, catalogo, janela=JANELA, link=LINK_STJ, temas=temas_stj,
                                          acervo={"processos": {"202502771756": {"area": "penal", "partes": [], "pessoa_fisica": True}}, "segredo": []})
    assert _item(next(s for s in com_acervo if s.id == "stj-ce-2026-12-02"), "APn 1131").area == "penal"


def test_stj_o_proximo_repetitivo_depois_da_janela_fica_no_estado(regras, catalogo, pauta_stj, temas_stj) -> None:
    _sessoes, estado = agenda.sessoes_do_stj(pauta_stj, regras, catalogo, janela=JANELA, link=LINK_STJ, temas=temas_stj)
    proximo = estado.proximo
    assert proximo is not None and (proximo.id, proximo.data, proximo.orgao) == ("stj-s2-2026-12-09", "2026-12-09", "Segunda Seção")
    (item,) = proximo.itens
    assert (item.identificador, item.alcance, item.tema.numero) == ("REsp 2272537", "repetitivo", 1443)  # type: ignore[union-attr]
    assert "locação de unidades autônomas por curto período" in item.tema.titulo  # type: ignore[union-attr]
    # na semana seguinte a mesma sessão cabe na janela e sai do estado para as sessões
    sessoes, depois = agenda.sessoes_do_stj(pauta_stj, regras, catalogo, janela=("2026-11-30", "2026-12-13"), link=LINK_STJ, temas=temas_stj)
    assert "stj-s2-2026-12-09" in {s.id for s in sessoes} and depois.proximo is None


@pytest.mark.parametrize("item, foro, com_tema, motivo", [
    ({"segredo": True, "interno": True, "fora": "vice", "tipo": "AgInt"}, "corte_especial", False, "segredo"),
    ({"segredo": False, "interno": True, "fora": "vice", "tipo": "AgInt"}, "corte_especial", False, "vice_presidencia"),
    ({"segredo": False, "interno": True, "fora": "presidencia", "tipo": "AgRg"}, "corte_especial", False, "vice_presidencia"),
    ({"segredo": False, "interno": True, "fora": None, "tipo": "EDcl"}, "secao", True, "recurso_interno"),
    ({"segredo": False, "interno": False, "fora": None, "tipo": "principal"}, "turma", False, "sem_tema"),
    ({"segredo": False, "interno": False, "fora": None, "tipo": "principal"}, "turma", True, None),
    ({"segredo": False, "interno": False, "fora": None, "tipo": "principal"}, "secao", False, None),
    ({"segredo": False, "interno": False, "fora": None, "tipo": "ProRQF", "peticao": "PROPOSTA DE RELEVÂNCIA"}, "turma", False, None),
    ({"segredo": False, "interno": False, "fora": None, "tipo": "ProAfR"}, "secao", False, None),
    ({"segredo": False, "interno": False, "fora": None, "tipo": "XX", "peticao": "PROPOSTA DE AFETAÇÃO"}, "secao", False, None),
    ({"segredo": False, "interno": False, "fora": None, "tipo": "QO", "peticao": "QUESTÃO DE ORDEM"}, "corte_especial", False, "outros"),
    ({"segredo": False, "interno": False, "fora": None, "tipo": "RCD"}, "turma", True, "outros"),
])
def test_stj_cada_item_fora_conta_uma_vez_pelo_primeiro_motivo(regras, item, foro, com_tema, motivo) -> None:
    assert agenda.motivo_fora_do_stj(item, foro, com_tema, regras.limiares["agenda"]) == motivo


def test_stj_segredo_no_acervo_tambem_tira_o_item(regras, catalogo, pauta_stj, temas_stj) -> None:
    """O processo que a pauta não marca e o acervo diz que está em segredo só entra na contagem."""
    acervo = {"processos": {}, "segredo": ["202203340439"]}
    sessoes, _ = agenda.sessoes_do_stj(pauta_stj, regras, catalogo, janela=JANELA, link=LINK_STJ, temas=temas_stj, acervo=acervo)
    lote = next(s for s in sessoes if s.id == "stj-ce-2026-12-02")
    assert "REsp 2034211" not in {i.identificador for i in lote.itens} and dict(lote.fora) == {"segredo": 1, "vice_presidencia": 1}


# ── STF: o Plenário presencial ───────────────────────────────────────────────────────────────


def test_stf_plenario_com_hora_ordem_e_os_sinais_da_pauta(regras, catalogo, pauta_stf) -> None:
    sessoes, estado = agenda.sessoes_do_stf(pauta_stf, regras, catalogo, janela=JANELA, link=LINK_STF, temas=TEMAS_STF, acervo=ACERVO_STF)
    assert [s.id for s in sessoes] == ["stf-plenario-2026-11-25", "stf-plenario-2026-11-26"]
    quarta = sessoes[0]
    assert (quarta.orgao, quarta.orgao_sigla, quarta.foro, quarta.tipo, quarta.hora, quarta.modalidade, quarta.pauta, quarta.situacao) == (
        "Plenário", "PLEN", "plenario", "julgamento", "14:00", "presencial", "publicada", "agendada")
    assert [i.ordem for i in quarta.itens] == list(range(1, 11)) and not quarta.fora
    assert [i.identificador for i in quarta.itens][:3] == ["ADI 6002", "ARE 1502069", "RE 1326541"]
    sinais = {i.identificador: (i.estagio, i.estagio_detalhe, i.outros_estagios) for i in quarta.itens}
    assert sinais["ADI 6002"] == ("destaque", "Flávio Dino", ())
    assert sinais["RE 1326541"] == ("retorno_de_vista", "Gilmar Mendes", ())
    assert sinais["ADI 7717"] == ("retorno_de_vista", "Alexandre de Moraes", ())
    assert sinais["ADI 7643"] == ("pautado", None, ())
    assert sinais["ADI 7807"] == ("referendo_de_cautelar", None, ("destaque",))  # o incidente diz "referendo"; o destaque fica nos demais
    assert _item(quarta, "ADI 7807").relator == contrato.Relator(nome="Dias Toffoli")
    assert _item(quarta, "ADI 6002").link == "https://portal.stf.jus.br/processos/listarProcessos.asp?classe=ADI&numeroProcesso=6002"
    assert all(i.resultado is None for i in quarta.itens)
    assert estado.pauta_publicada_ate == "2026-11-25" and dict(estado.contagens) == {"itens": 10, "sessoes": 2} and estado.proximo is None


def test_stf_o_tema_de_repercussao_geral_entra_pelo_processo_paradigma(regras, catalogo, pauta_stf) -> None:
    sessoes, _ = agenda.sessoes_do_stf(pauta_stf, regras, catalogo, janela=JANELA, link=LINK_STF, temas=TEMAS_STF, acervo=ACERVO_STF)
    re_, are = _item(sessoes[0], "RE 1326541"), _item(sessoes[0], "ARE 1502069")
    assert (re_.alcance, re_.tema.numero, re_.tema.tipo) == ("repercussao_geral", 1218, "repercussao_geral")  # type: ignore[union-attr]
    assert re_.tema.titulo.startswith("Adoção do piso nacional estipulado pela Lei federal 11.738/2008")  # type: ignore[union-attr]
    assert (are.alcance, are.tema.numero) == ("repercussao_geral", 1324)  # type: ignore[union-attr]
    assert (re_.area, are.area) == ("administrativo", "administrativo")
    # sem a lista de temas o processo continua de repercussão geral (a pauta diz), só sem o tema
    sem, _ = agenda.sessoes_do_stf(pauta_stf, regras, catalogo, janela=JANELA, link=LINK_STF, temas=None, acervo=ACERVO_STF)
    assert (_item(sem[0], "RE 1326541").alcance, _item(sem[0], "RE 1326541").tema) == ("repercussao_geral", None)
    assert agenda.temas_do_stf([{"numero": 7, "titulo": "", "leading_case": {"classe": "RE", "numero": "1"}}, {"numero": 8, "titulo": "x"}, "lixo"]) == {}


def test_stf_norma_federal_ou_estadual_pelo_polo_passivo_e_as_partes_publicas(regras, catalogo, pauta_stf) -> None:
    sessoes, _ = agenda.sessoes_do_stf(pauta_stf, regras, catalogo, janela=JANELA, link=LINK_STF, temas=TEMAS_STF, acervo=ACERVO_STF)
    oab, mt, sem_acervo = _item(sessoes[0], "ADI 6002"), _item(sessoes[0], "ADI 7807"), _item(sessoes[0], "ADI 7906")
    assert oab.alcance == "controle_federal" and mt.alcance == "controle_estadual"
    # a OAB vem do acervo como grupo feito na hora (fora do catálogo): não é nomeada
    assert oab.partes == (contrato.Parte(polo="passivo", tipo="ente", nome="Congresso Nacional"),)
    assert mt.partes == (contrato.Parte(polo="ativo", tipo="ente", nome="Estado de Mato Grosso"),
                         contrato.Parte(polo="passivo", tipo="ente", nome="Assembleia Legislativa de Mato Grosso"))
    # o município entra pela família do catálogo, reconhecida pelo nome que o acervo traz
    assert _item(sessoes[0], "ARE 1502069").partes == (contrato.Parte(polo="ativo", tipo="ente", nome="Município de Riolandia"),)
    # processo que o acervo não conhece: sem parte, sem área e, sem polo passivo federal, norma estadual
    assert (sem_acervo.alcance, sem_acervo.partes, sem_acervo.area) == ("controle_estadual", (), None)


def test_stf_resumo_oficial_so_no_controle_concentrado_e_na_repercussao_geral_sem_pessoa_fisica(regras, catalogo, pauta_stf) -> None:
    sessoes, _ = agenda.sessoes_do_stf(pauta_stf, regras, catalogo, janela=JANELA, link=LINK_STF, temas=TEMAS_STF, acervo=ACERVO_STF)
    quarta = sessoes[0]
    assert _item(quarta, "ADI 6002").ementa == "1. Trata-se de Ação Direta de Inconstitucionalidade, com ped…"  # cortado na amostra
    assert all(i.ementa for i in quarta.itens if i.tipo == "ADI")
    # os dois recursos têm pessoa física do outro lado: o resumo não é publicado (pode trazer o nome)
    assert _item(quarta, "RE 1326541").ementa is None and _item(quarta, "ARE 1502069").ementa is None
    so_entes = {**ACERVO_STF, "processos": {**ACERVO_STF["processos"], "stf-RE-1326541": {**ACERVO_STF["processos"]["stf-RE-1326541"], "pessoa_fisica": False}}}
    com, _ = agenda.sessoes_do_stf(pauta_stf, regras, catalogo, janela=JANELA, link=LINK_STF, temas=TEMAS_STF, acervo=so_entes)
    assert _item(com[0], "RE 1326541").ementa == "1. Trata-se de Recurso Extraordinário interposto contra acór…"
    # um processo de outra classe nunca leva resumo, nem que a leitura o traga
    item = {"data": "2026-11-25", "orgao": "PL", "sessao": "1", "classe": "HC", "numero": "123456", "id": "stf-HC-123456", "resumo": "texto com nome",
            "ordem": 1, "relator_id": None, "vista": None, "destaque": None, "repercussao_geral": False, "incidente": None}
    hc = agenda.item_do_stf(item, regras, catalogo, temas={}, acervo=None)
    assert hc is not None and (hc.identificador, hc.alcance, hc.ementa, hc.relator) == ("HC 123456", "individual", None, None)


def test_stf_dia_sem_processos_e_dia_nao_lido(regras, catalogo, pauta_stf) -> None:
    sessoes, _ = agenda.sessoes_do_stf(pauta_stf, regras, catalogo, janela=JANELA, link=LINK_STF)
    quinta = sessoes[1]  # o calendário marca 26/11 e a fonte ainda não lista processos
    assert (quinta.data, quinta.pauta, quinta.hora, quinta.modalidade, quinta.itens, quinta.fora) == ("2026-11-26", "nao_publicada", None, "presencial", (), ())
    falhou = stf.coletar_pauta(Servico({"sessao-presencial&data=26/11/2026": FetchError("u", "HTTP 500", status=500)}), regras, hoje=date(2026, 11, 23))
    sessoes, _ = agenda.sessoes_do_stf(falhou, regras, catalogo, janela=JANELA, link=LINK_STF)
    assert [(s.data, s.pauta) for s in sessoes] == [("2026-11-25", "publicada"), ("2026-11-26", "nao_coletada")]  # a agenda não afirma o que não leu
    # a sessão que a fonte já lista, com hora, mas ainda sem processos
    so_a_hora = {"data": "26/11/2026", "colegiados": [{"codigo": "TP", "descricao": "Plenário", "sessoes": [{"id": "87290", "dataInicio": "26/11/2026 14:00", "incidentes": []}]}]}
    com_hora = stf.coletar_pauta(Servico({"sessao-presencial&data=26/11/2026": so_a_hora}), regras, hoje=date(2026, 11, 23))
    sessoes, _ = agenda.sessoes_do_stf(com_hora, regras, catalogo, janela=JANELA, link=LINK_STF)
    assert (sessoes[1].pauta, sessoes[1].hora, sessoes[1].itens) == ("nao_publicada", "14:00", ())


def test_stf_contagens_das_sessoes_virtuais_vao_para_o_estado(regras, catalogo, pauta_stf) -> None:
    _, estado = agenda.sessoes_do_stf(pauta_stf, regras, catalogo, janela=JANELA, link=LINK_STF)
    assert [(v.de, v.ate, v.plenario, v.turma1, v.turma2, v.total) for v in estado.virtuais] == [
        ("2026-11-13", "2026-11-23", 132, 218, 277, 627), ("2026-11-20", "2026-11-30", 179, 209, 274, 662),
        ("2026-11-27", "2026-12-04", 165, 187, 194, 546)]  # só as que tocam a janela (as de outubro ficam fora)


def test_stf_processo_em_segredo_so_conta_e_turma_fica_fora(regras, catalogo, pauta_stf) -> None:
    acervo = {**ACERVO_STF, "segredo": ["stf-ADI-7643"]}
    sessoes, _ = agenda.sessoes_do_stf(pauta_stf, regras, catalogo, janela=JANELA, link=LINK_STF, acervo=acervo)
    assert "ADI 7643" not in {i.identificador for i in sessoes[0].itens} and dict(sessoes[0].fora) == {"segredo": 1}
    assert len(sessoes[0].itens) == 9 and sessoes[0].total_na_fonte == 10
    # sessão presencial de Turma: a agenda só cobre o Plenário; a Turma vira contagem no estado
    turma = json.loads(json.dumps(pauta_stf))
    for i in turma["itens"][:4]:
        i["orgao"] = "T1"
    turma["dias"][0]["sessoes"].append({"data": "2026-11-25", "orgao": "T1", "sessao": "9", "hora": "14:00", "itens": 4})
    sessoes, estado = agenda.sessoes_do_stf(turma, regras, catalogo, janela=JANELA, link=LINK_STF)
    assert all(s.foro == "plenario" for s in sessoes) and len(sessoes[0].itens) == 6
    assert dict(estado.contagens) == {"itens": 6, "itens_de_turma": 4, "sessoes": 2, "sessoes_de_turma": 1}


def test_stf_sessao_depois_da_janela_vira_o_proximo_e_coleta_antiga_sem_dias(regras, catalogo, pauta_stf) -> None:
    sessoes, estado = agenda.sessoes_do_stf(pauta_stf, regras, catalogo, janela=("2026-11-09", "2026-11-22"), link=LINK_STF)
    assert sessoes == () and estado.proximo is not None and (estado.proximo.id, len(estado.proximo.itens)) == ("stf-plenario-2026-11-25", 10)
    antiga = {k: v for k, v in pauta_stf.items() if k not in ("dias", "calendarios")}  # o estado guardado por uma coleta de antes
    sessoes, _ = agenda.sessoes_do_stf(antiga, regras, catalogo, janela=JANELA, link=LINK_STF)
    assert [(s.id, s.hora, len(s.itens)) for s in sessoes] == [("stf-plenario-2026-11-25", "14:00", 10)]


# ── privacidade ──────────────────────────────────────────────────────────────────────────────


def test_partes_so_ente_publico_ou_pessoa_juridica_do_catalogo(catalogo) -> None:
    brutas = [
        {"polo": "ativo", "tipo": "pj", "grupo": "nome-fulano-de-tal-advogados", "nome": "FULANO DE TAL ADVOGADOS ASSOCIADOS"},  # fora do catálogo
        {"polo": "ativo", "tipo": "pj", "grupo": "cnpj-12345678", "nome": "COMERCIAL BELTRANO LTDA"},  # fora do catálogo
        {"polo": "passivo", "tipo": "pf", "grupo": None, "nome": None},  # pessoa física nunca tem nome
        {"polo": "passivo", "tipo": "ente", "grupo": "nome-fulano-de-tal-defensoria-publica", "nome": "FULANO DE TAL - DEFENSORIA PUBLICA"},  # gente antes do órgão
        {"polo": None, "tipo": "autoridade", "grupo": None, "nome": "TRIBUNAL DE JUSTIÇA DO ESTADO DE SÃO PAULO"},
        {"polo": "passivo", "tipo": "ente", "grupo": "uniao", "nome": "UNIÃO"},
        {"polo": "passivo", "tipo": "ente", "grupo": "uniao", "nome": "UNIÃO FEDERAL"},  # repetida
        {"polo": "outro", "tipo": "ente", "grupo": "nome-agencia-nacional-de-protecao-de-dados-anpd", "nome": "AGENCIA NACIONAL DE PROTECAO DE DADOS - ANPD"},
        {"polo": "ativo", "tipo": "pj", "grupo": "bmg", "nome": "BANCO BMG S.A"},
    ]
    assert agenda.partes_publicas(brutas, catalogo) == (
        contrato.Parte(polo="passivo", tipo="ente", nome="União"),
        contrato.Parte(polo="outro", tipo="ente", nome="Agencia Nacional de Protecao de Dados"),
        contrato.Parte(polo="ativo", tipo="pj", nome="Banco BMG"),
    )
    assert agenda.partes_publicas(None, catalogo) == () and agenda.partes_publicas(["lixo", {"polo": "ativo"}], catalogo) == ()
    muitas = [{"polo": "outro", "tipo": "ente", "grupo": f"estado-{uf}", "nome": None} for uf in ("ac", "al", "am", "ba", "ce", "go", "pa", "pr")]
    assert len(agenda.partes_publicas(muitas, catalogo)) == agenda.PARTES_MAXIMO


def test_nenhum_nome_de_pessoa_advogado_empresa_mascarada_nem_processo_em_segredo(regras, catalogo, pauta_stj, temas_stj, pauta_stf) -> None:
    a = _montar(regras, catalogo, stf=_entrada_stf(pauta_stf), stj=_entrada_stj(pauta_stj, temas_stj))
    texto, cortes = agenda.texto(agenda.juntar(a, None, HOJE))
    assert cortes == [] and contrato.validar(json.loads(texto)) == []
    for proibido in ("PESSOA FÍSICA", "Pessoa Física", "ADVOGADO", "Advogado", "mascarado", "EMPRESA ", "Empresa ", "SEGREDO", "OAB", "000000"):
        assert proibido not in texto, proibido
    assert "9000001" not in texto and "9000002" not in texto  # os dois processos em segredo da amostra: nem o número
    dados = json.loads(texto)
    itens = [i for s in dados["sessoes"] for i in s["itens"]] + [i for e in dados["estado"].values() if e["proximo"] for i in e["proximo"]["itens"]]
    assert len(itens) == 18 and all(i["segredo"] is False and i["resultado"] is None for i in itens)
    partes_ = [p for i in itens for p in i["partes"]]
    assert partes_ and {p["tipo"] for p in partes_} <= {"ente", "pj"}
    nomes = {p["nome"] for p in partes_}
    assert {"Banco BMG", "União", "Ministério Público Federal", "Aymoré Financiamentos", "Congresso Nacional"} <= nomes
    # quem aparece com nome além das partes: só ministro da composição (relator, vista, destaque)
    ministros = {m.nome for trib in ("stf", "stj") for m in regras.composicao[trib].ministros}
    assert {i["relator"]["nome"] for i in itens if i["relator"]} <= ministros
    assert {i["estagio_detalhe"] for i in itens if i["estagio_detalhe"]} <= ministros
    assert sum(s["fora"].get("segredo", 0) for s in dados["sessoes"]) == 2


def test_relator_que_nao_e_ministro_da_composicao_nao_e_nomeado(regras, catalogo) -> None:
    item = {"sessao": "2026-12-02", "orgao": "S2", "classe": "REsp", "numero": "2284957", "registro": "202602916421", "tipo": "ProRQF",
            "peticao": "PROPOSTA DE RELEVÂNCIA", "interno": False, "segredo": False, "fora": "cogepac", "relator_id": None,
            "relator": "Comissão Gestora de Precedentes | 2ª Seção - integrante 1", "partes": []}
    saida = agenda.item_do_stj(item, regras, catalogo, temas=(), link=LINK_STJ)
    assert saida is not None and (saida.alcance, saida.relator) == ("afetacao", None)
    assert agenda.item_do_stj({**item, "classe": "", "numero": ""}, regras, catalogo, temas=(), link=LINK_STJ) is None


# ── o arquivo: montar, juntar, texto ─────────────────────────────────────────────────────────


def test_montar_junta_os_dois_tribunais_e_passa_no_contrato(regras, catalogo, pauta_stj, temas_stj, pauta_stf) -> None:
    a = _montar(regras, catalogo, stf=_entrada_stf(pauta_stf), stj=_entrada_stj(pauta_stj, temas_stj))
    assert (a.lado, a.hoje, a.gerado_em, a.janela) == ("tribunais", HOJE, GERADO, JANELA)
    assert [f.chave for f in a.fontes] == ["stf_pauta", "stj_pautas", "stj_precedentes"] and all(f.ok for f in a.fontes)
    assert [s.id for s in a.sessoes] == ["stj-t1-2026-11-24", "stj-t4-2026-11-24", "stf-plenario-2026-11-25", "stj-ce-2026-11-25",
                                         "stf-plenario-2026-11-26", "stj-ce-2026-12-02", "stj-s2-2026-12-03"]
    assert [e.instituicao for e in a.estado] == ["stf", "stj"] and a.refeita("stf") and a.refeita("stj")
    assert contrato.validar(a.para_dict()) == []
    assert agenda.montar(regras, hoje=HOJE, gerado_em=GERADO, catalogo=catalogo) is None  # nenhum tribunal: nada a gravar


def test_mesmos_dados_mesmos_bytes(regras, catalogo, pauta_stj, temas_stj, pauta_stf) -> None:
    def gerar() -> str:
        cat = partes.carregar_catalogo()  # um catálogo novo a cada vez: nada depende do que ele já viu
        a = agenda.montar(regras, hoje=HOJE, gerado_em=GERADO, catalogo=cat, stf=_entrada_stf(json.loads(json.dumps(pauta_stf))),
                          stj=_entrada_stj(list(reversed(pauta_stj)), list(reversed(temas_stj))))
        assert a is not None
        return agenda.texto(agenda.juntar(a, None, HOJE))[0]

    um, dois = gerar(), gerar()
    assert um == dois and um.endswith("\n") and "\n" not in um[:-1]
    assert um == agenda.texto(_montar(regras, catalogo, stf=_entrada_stf(pauta_stf), stj=_entrada_stj(pauta_stj, temas_stj)))[0]  # a ordem do arquivo não muda nada
    lida = contrato.ler_dados(json.loads(um), lado="tribunais")
    assert lida is not None and agenda.texto(lida)[0] == um  # ida e volta


def test_tribunal_cuja_pauta_falhou_nao_e_refeito_e_fica_como_estava(regras, catalogo, pauta_stj, temas_stj, pauta_stf) -> None:
    anterior = _montar(regras, catalogo, hoje="2026-11-23", stf=_entrada_stf(pauta_stf), stj=_entrada_stj(pauta_stj, temas_stj))
    falha = agenda.fonte("stj_pautas", "stj", {"url": LINK_STJ, "ok": False, "data_dados": "2026-11-22", "velho_dias": 4,
                                               "aviso": "A coleta falhou em 24/11 (o servidor respondeu 503); ficam os dados de 22/11/2026."})
    assert (falha.ok, falha.data_dados, falha.coletado_em) == (False, None, None)
    novo = _montar(regras, catalogo, stf=_entrada_stf(pauta_stf), stj=agenda.Entrada(fontes=(falha, _fonte("stj_precedentes", "stj")), pauta=None, temas=temas_stj))
    assert not novo.sessoes_de("stj") and novo.estado_de("stj") is None and not novo.refeita("stj")
    junto = agenda.juntar(novo, anterior, HOJE)
    assert [s.id for s in junto.sessoes_de("stj")] == [s.id for s in anterior.sessoes_de("stj")]  # nunca vira zero
    assert all(s.como_pautado_em == "2026-11-24" for s in junto.sessoes_de("stj"))  # a data do dado em que a fonte as trazia
    assert junto.estado_de("stj") == anterior.estado_de("stj")
    f = junto.fonte("stj_pautas")
    assert f is not None and (f.ok, f.data_dados, "503" in (f.aviso or "")) == (False, "2026-11-24", True)  # a data do último dia bom
    assert junto.refeita("stf") and all(s.como_pautado_em is None for s in junto.sessoes_de("stf"))
    assert contrato.validar(junto.para_dict()) == []
    # só um tribunal na rodada (--so stf): o outro nem é citado e fica inteiro, com as fontes dele
    so_stf = agenda.juntar(_montar(regras, catalogo, stf=_entrada_stf(pauta_stf)), anterior, HOJE)
    assert so_stf.fonte("stj_pautas") == anterior.fonte("stj_pautas") and len(so_stf.sessoes_de("stj")) == 5
    assert so_stf.estado_de("stj") == anterior.estado_de("stj")


def test_arquivo_do_stj_vazio_ou_sem_precedentes_nao_refaz(regras, catalogo, pauta_stj) -> None:
    vazio = _montar(regras, catalogo, stj=_entrada_stj([], []))
    f = vazio.fonte("stj_pautas")
    assert f is not None and f.ok is False and "vazio" in (f.aviso or "") and not vazio.sessoes and not vazio.estado
    sem_temas = _montar(regras, catalogo, stj=_entrada_stj(pauta_stj, None))  # sem os precedentes, um repetitivo sairia como caso individual
    assert not sem_temas.sessoes and not sem_temas.estado


def test_calendario_do_stf_parcial_ou_mudo(regras, catalogo) -> None:
    parcial = stf.coletar_pauta(Servico({"calendarios&mes=12&ano=2026": FetchError("u", "HTTP 500", status=500)}), regras, hoje=date(2026, 11, 23))
    assert parcial["calendarios"] == {"lidos": 1, "falhas": 1}
    a = _montar(regras, catalogo, stf=_entrada_stf(parcial))
    f = a.fonte("stf_pauta")
    # o que foi lido entra, com a data de hoje; a fonte fica marcada, para as sessões de dezembro não virarem zero
    assert f is not None and (f.ok, f.data_dados) == (False, "2026-11-24") and "Parte do calendário" in (f.aviso or "")
    assert [s.id for s in a.sessoes] == ["stf-plenario-2026-11-25", "stf-plenario-2026-11-26"] and a.estado_de("stf") is not None
    assert contrato.validar(a.para_dict()) == []
    mudo = stf.coletar_pauta(Servico({"calendarios&mes": FetchError("u", "HTTP 500", status=500)}), regras, hoje=date(2026, 11, 23))
    b = _montar(regras, catalogo, stf=_entrada_stf(mudo))
    f = b.fonte("stf_pauta")
    assert f is not None and (f.ok, f.data_dados) == (False, None) and not b.sessoes and not b.estado


def test_rodar_na_quinta_mantem_a_sessao_de_quarta(regras, catalogo, pauta_stj, temas_stj, pauta_stf) -> None:
    """As duas fontes só publicam pautas futuras: na quinta, o Plenário e a Corte Especial de quarta
    já saíram delas e ficam do arquivo anterior, marcados "como pautado em"."""
    quarta = _montar(regras, catalogo, hoje="2026-11-25", stf=_entrada_stf(pauta_stf), stj=_entrada_stj(pauta_stj, temas_stj))
    assert {"stf-plenario-2026-11-25", "stj-ce-2026-11-25"} <= {s.id for s in quarta.sessoes}
    # a coleta de quinta: o STF só responde de quinta em diante e o arquivo do STJ não traz mais terça nem quarta
    stf_quinta = stf.coletar_pauta(Servico(), regras, hoje=date(2026, 11, 26))
    assert [d["data"] for d in stf_quinta["dias"]] == ["2026-11-26"] and not stf_quinta["itens"]
    stj_quinta = [i for i in pauta_stj if i["sessao"] >= "2026-11-26"]
    novo = _montar(regras, catalogo, hoje="2026-11-26", stf=agenda.Entrada(fontes=(_fonte("stf_pauta", "stf", "2026-11-26"),), pauta=stf_quinta),
                   stj=agenda.Entrada(fontes=(_fonte("stj_pautas", "stj", "2026-11-26"), _fonte("stj_precedentes", "stj", "2026-11-25")),
                                      pauta=stj_quinta, temas=temas_stj))
    assert not [s for s in novo.sessoes if s.data < "2026-11-26"]
    junto = agenda.juntar(novo, quarta, "2026-11-26")
    plenario, corte = _sessao(junto, "stf-plenario-2026-11-25"), _sessao(junto, "stj-ce-2026-11-25")
    assert (plenario.como_pautado_em, len(plenario.itens), plenario.hora) == ("2026-11-24", 10, "14:00")  # a data do dado da véspera
    assert corte.como_pautado_em == "2026-11-24" and [i.identificador for i in corte.itens] == ["AREsp 3296604"]
    assert {"stj-t1-2026-11-24", "stj-t4-2026-11-24"} <= {s.id for s in junto.sessoes}  # terça também fica
    assert _sessao(junto, "stf-plenario-2026-11-26").como_pautado_em is None  # a de hoje é dado de hoje
    assert all(i.resultado is None for s in junto.sessoes for i in s.itens)  # passada não é julgada: não há dado de resultado
    assert contrato.validar(junto.para_dict()) == []
    # na segunda-feira seguinte a semana virou: a poda tira as sessões da semana passada
    segunda = agenda.juntar(_montar(regras, catalogo, hoje="2026-11-30", stf=agenda.Entrada(fontes=(_fonte("stf_pauta", "stf", "2026-11-30"),), pauta={
        "itens": [], "dias": [], "virtuais": [], "calendarios": {"lidos": 2, "falhas": 0}})), junto, "2026-11-30")
    assert not [s for s in segunda.sessoes if s.data < "2026-11-30"]


def test_a_sessao_de_hoje_que_a_fonte_deixou_de_trazer_tambem_fica(regras, catalogo, pauta_stj, temas_stj) -> None:
    manha = _montar(regras, catalogo, hoje="2026-11-25", stj=_entrada_stj(pauta_stj, temas_stj))
    de_amanha_em_diante = [i for i in pauta_stj if i["sessao"] > "2026-11-25"]
    noite = _montar(regras, catalogo, hoje="2026-11-25", stj=_entrada_stj(de_amanha_em_diante, temas_stj))
    assert "stj-ce-2026-11-25" not in {s.id for s in noite.sessoes}
    assert "stj-ce-2026-11-25" not in {s.id for s in contrato.mesclar_com_anterior(noite, manha, "2026-11-25").sessoes}  # a mesclagem do contrato a perderia
    junto = agenda.juntar(noite, manha, "2026-11-25")
    corte = _sessao(junto, "stj-ce-2026-11-25")
    assert corte.como_pautado_em == "2026-11-24" and len(corte.itens) == 1 and contrato.validar(junto.para_dict()) == []
    assert agenda.juntar(junto, junto, "2026-11-25") == junto  # rodar de novo não muda nada


def test_teto_de_tamanho_emagrece_e_depois_recusa(regras, catalogo, pauta_stj, temas_stj, pauta_stf) -> None:
    a = _montar(regras, catalogo, stf=_entrada_stf(pauta_stf), stj=_entrada_stj(pauta_stj, temas_stj))
    inteiro, cortes = agenda.texto(a)
    assert cortes == [] and len(inteiro.encode()) < agenda.TETO_BYTES
    sem_resumo, cortes = agenda.texto(a, len(inteiro.encode()) - 1)
    assert cortes == ["os resumos oficiais"] and len(sem_resumo.encode()) < len(inteiro.encode())
    dados = json.loads(sem_resumo)
    assert all(i["ementa"] is None for s in dados["sessoes"] for i in s["itens"]) and any(i["partes"] for s in dados["sessoes"] for i in s["itens"])
    sem_partes, cortes = agenda.texto(a, len(sem_resumo.encode()) - 1)
    assert cortes == ["os resumos oficiais", "as partes"] and contrato.validar(json.loads(sem_partes)) == []
    dados = json.loads(sem_partes)
    assert not any(i["partes"] for s in dados["sessoes"] for i in s["itens"]) and not dados["estado"]["stj"]["proximo"]["itens"][0]["partes"]
    with pytest.raises(agenda.AcimaDoTeto):
        agenda.texto(a, 2_000)
    with pytest.raises(contrato.ErroDeContrato):  # agenda fora do contrato nunca vira texto
        agenda.texto(dataclasses.replace(a, gerado_em="ontem"))


def test_fonte_da_agenda_a_partir_da_entrada_do_meta() -> None:
    boa = agenda.fonte("stj_pautas", "stj", {"nome": "STJ · pautas de julgamento", "url": LINK_STJ, "ok": True, "data_dados": "2026-11-24",
                                             "coletado_em": None, "aviso": None, "velho_dias": 4, "registros": 8546, "atribuicao": "x"}, coletado_em=GERADO)
    assert boa == contrato.FonteAgenda(chave="stj_pautas", instituicao="stj", nome="STJ: pautas de julgamento", url=LINK_STJ, ok=True,
                                       data_dados="2026-11-24", coletado_em=GERADO, aviso=None, velho_dias=4)
    com_aviso = agenda.fonte("stf_pauta", "stf", {"url": LINK_STF, "ok": True, "data_dados": "2026-11-24", "coletado_em": GERADO, "velho_dias": 0,
                                                  "aviso": "1 consulta(s) da pauta sem resposta."})
    assert (com_aviso.ok, com_aviso.aviso, com_aviso.velho_dias) == (True, "1 consulta(s) da pauta sem resposta.", None)
    sem_dado = agenda.fonte("stf_pauta", "stf", {"url": LINK_STF, "ok": True, "data_dados": None})  # "ok" sem data não é fonte boa
    assert (sem_dado.ok, sem_dado.data_dados) == (False, None) and sem_dado.aviso
    assert agenda.fonte("stf_pauta", "stf", None).ok is False
    outra = agenda.fonte("stf_rg", "stf", {"nome": "STF · repercussão geral", "url": LINK_STF, "ok": None})
    assert (outra.nome, outra.ok) == ("STF: repercussão geral", False)


def test_a_agenda_inventada_da_fabrica_cumpre_o_contrato() -> None:
    dados = fabrica.agenda()
    assert contrato.validar(dados) == []
    a = contrato.ler_dados(dados, lado="tribunais")
    assert a is not None and a.hoje == fabrica.HOJE and {s.instituicao for s in a.sessoes} == {"stf", "stj"}
    assert a.estado_de("stf").virtuais and a.estado_de("stj").proximo is not None  # type: ignore[union-attr]
    assert agenda.texto(a)[0] == fabrica._json(dados)  # a fábrica grava no mesmo formato compacto
