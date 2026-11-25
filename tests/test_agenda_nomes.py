"""Testes do nome de tela dos itens da Agenda da semana (``qijournal/agenda/nomes.py``), com as
ementas e os títulos reais das fixtures (semana de 23 a 27/11/2026)."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from qijournal.agenda import contrato, nomes, regras
from tests.fixtures import agenda as fx

REGRAS = regras.carregar()


def _itens() -> dict[str, contrato.ItemAgenda]:
    itens: dict[str, contrato.ItemAgenda] = {}
    for agenda in (fx.congresso(), fx.tribunais()):
        sessoes = [*agenda.sessoes, *(e.proximo for e in agenda.estado if e.proximo is not None)]
        for item in [i for s in sessoes for i in s.itens] + [i for e in agenda.estado for i in e.fila]:
            itens.setdefault(item.identificador, item)
    return itens


ITENS = _itens()


def _nome(identificador: str, **mudancas) -> nomes.Nome:
    item = ITENS[identificador]
    return nomes.nome_do_item(replace(item, **mudancas) if mudancas else item, REGRAS)


# ── a ordem das fontes do nome ───────────────────────────────────────────────────────────────


def test_primeiro_o_nome_curto_que_a_fonte_publica():
    nome = _nome("PL 6461/2019")
    assert (nome.texto, nome.curto, nome.origem) == ("Estatuto do Aprendiz", "Estatuto do Aprendiz", "apelido")
    assert _nome("PLP 299/2026").texto == "Ajuste fiscal"
    assert _nome("MPV 1381/2026").texto == "Crédito Extraordinário - Subvenções à produção e à importação de combustíveis"


def test_depois_o_nome_aprovado_em_config():
    aprovadas = replace(REGRAS, nomes_aprovados=(("PL 5807/2025", "Falsificação de alimentos, bebidas e suplementos"),))
    nome = nomes.nome_do_item(ITENS["PL 5807/2025"], aprovadas)
    assert (nome.texto, nome.origem) == ("Falsificação de alimentos, bebidas e suplementos", "aprovado")
    # O nome que a fonte publica continua na frente do aprovado.
    com_apelido = replace(REGRAS, nomes_aprovados=(("PL 6461/2019", "Outro nome"),))
    assert nomes.nome_do_item(ITENS["PL 6461/2019"], com_apelido).origem == "apelido"


def test_depois_o_titulo_do_tema_cortado_no_fim_de_palavra():
    nome = _nome("ARE 1502069")
    assert nome.origem == "tema"
    assert nome.texto == "Revisão de salário-base de professor municipal, com base no valor de atualização do piso…"
    assert nome.curto == "Revisão de salário-base de professor municipal, com base…"
    repetitivo = _nome("REsp 2034211")
    assert repetitivo.texto == "Definir se ocorre ou não a prescrição para a habilitação de herdeiros ou sucessores…"


@pytest.mark.parametrize(
    ("identificador", "esperado"),
    [
        # "Altera <norma>, para X": mostrado a partir de X, com a primeira letra maiúscula
        ("PL 724/2003", "Dispor sobre circulação de veículos de tração e crimes de trânsito"),
        ("PL 479/2015", "Dispor sobre educação em tempo integral"),
        ("PL 2091/2023", "Tipificar novos crimes cometidos no mercado de valores mobiliários"),
        ("PL 294/2015", "Incluir o ministro de confissão religiosa de matriz africana como segurado individual"),
        ("PL 7103/2014", "Incluir o quesito cor ou raça nos prontuários, registros e cadastramentos do Sistema…"),
        ("PEC 35/2024", "Incluir o Plano Pluriquadrienal como norteador das despesas e investimentos…"),
        ("PL 5807/2025", "Tipificar o crime de posse de artefatos e embalagens para falsificação de produtos…"),
        # "Institui", "Cria", "Dispõe sobre": corte normal, só a primeira oração
        ("PL 351/2019", "Institui a Força Nacional do Sistema Único de Saúde (FN-SUS)"),
        ("PL 5894/2025", "Institui o Plano Nacional de Cultura (PNC) para o decênio 2026-2036"),
        ("PL 4958/2023", "Cria a Zona Franca da Bioeconomia"),
        ("PL 1584/2021", "Dispõe sobre a reciclagem de embarcações"),
        ("PL 4250/2024", "Dispõe sobre a regulamentação da compra, venda, furto e receptação de fios de cobre"),
        ("PL 3762/2019", "Regulamenta a profissão de Biotecnologista"),
        # o travessão da fonte não vai para a tela
        ("PL 3079/2024", "Dispõe sobre a criação do Programa de Medicamentos do Trabalhador, PMT"),
    ],
)
def test_nome_tirado_da_ementa(identificador: str, esperado: str):
    nome = _nome(identificador)
    assert nome.origem == "ementa"
    assert nome.texto == esperado


def test_o_exemplo_do_que_nao_pode_sair():
    # Sem o nome curto da fonte, a ementa do PLP 299/2026 começa por seis leis: o nome é a finalidade.
    sem_apelido = _nome("PLP 299/2026", apelido=None)
    assert sem_apelido.texto == "Conferir ao Poder Executivo federal novos instrumentos de gestão, com vistas a garantir…"
    assert not sem_apelido.texto.startswith("Altera")
    # E quando a ementa só cita normas, o item fica sem nome.
    so_normas = _nome("PLP 299/2026", apelido=None, ementa="Altera a Lei Complementar nº 200, de 30 de agosto de 2023, a Lei Complementar nº 176, de 29 de dezembro de 2020.")
    assert so_normas == nomes.SEM_NOME and not so_normas


@pytest.mark.parametrize("identificador", ["PL 3984/2025", "PL 2559/2022", "PL 1024/2020", "ADI 6002", "AREsp 3296604"])
def test_sem_texto_aproveitavel_o_item_fica_sem_nome(identificador: str):
    nome = _nome(identificador)
    assert (nome.texto, nome.curto, nome.origem) == ("", "", "")


# ── o que vale para todos os nomes reais ─────────────────────────────────────────────────────

_NORMA_PELA_METADE = re.compile(r"(?:Lei|Decreto|Decreto-Lei|Lei Complementar)\s+(?:n[ºo°.]{0,2}\s*)?[\d.]*(?:,\s*de(?:\s+\d{1,2})?(?:\s+de)?(?:\s+\w+)?(?:\s+de)?)?…$")
_COMECO_DE_REFERENCIA = re.compile(r"^(?:Altera|Modifica|Acrescenta)\s+(?:a|o|as|os)\s+(?:Lei|Leis|Decreto|Decretos|art\.|Constitui)", re.IGNORECASE)


def test_nenhum_nome_real_e_uma_referencia_legal_cortada():
    nomeados = 0
    for identificador, item in ITENS.items():
        nome = nomes.nome_do_item(item, REGRAS)
        if not nome:
            continue
        nomeados += 1
        for texto in (nome.texto, nome.curto):
            assert not _COMECO_DE_REFERENCIA.match(texto), (identificador, texto)
            assert not _NORMA_PELA_METADE.search(texto), (identificador, texto)
            assert "–" not in texto and "—" not in texto, (identificador, texto)
            assert not texto.endswith((" de…", " da…", " do…", " e…", " a…", " que…", " para…", "(…")), (identificador, texto)
            assert texto == texto.strip() and "  " not in texto and " " not in texto
            assert texto[0] == texto[0].upper()
    assert nomeados >= 80  # a maior parte dos itens reais tem nome


def test_tetos_de_90_e_de_60_caracteres():
    for identificador, item in ITENS.items():
        nome = nomes.nome_do_item(item, REGRAS)
        assert len(nome.texto) <= 90, identificador
        assert len(nome.curto) <= 60, identificador
        assert bool(nome.texto) == bool(nome.curto)
    curto = nomes.nome_do_item(ITENS["PL 724/2003"], REGRAS, ate=40, curto_ate=20)
    assert len(curto.texto) <= 40 and len(curto.curto) <= 20 and curto.texto.endswith("…")


# ── as peças ─────────────────────────────────────────────────────────────────────────────────


def test_limpeza_de_espacos_e_do_numero_quebrado():
    assert nomes.limpar("Institui o  Programa X –  PX.") == "Institui o Programa X, PX"
    assert nomes.limpar("Modifica a Lei nº 12.288,de 20 de julho de 2010") == "Modifica a Lei nº 12.288, de 20 de julho de 2010"
    assert nomes.limpar(None) == "" and nomes.limpar("   ") == ""


def test_corte_nao_deixa_referencia_legal_pela_metade_nem_palavra_pendurada():
    texto = "Caracterizar como benefício eventual a Renda Emergencial de que trata a Lei n° 13.982, de 2 de abril de 2020, e assegurar a continuidade"
    assert nomes.cortar(texto, 90) == "Caracterizar como benefício eventual a Renda Emergencial…"
    assert nomes.cortar("Institui a Política Nacional para as Comissões de Apoio à Cinematografia (film commissions) no país", 90) == (
        "Institui a Política Nacional para as Comissões de Apoio à Cinematografia…"
    )
    assert nomes.cortar("Texto curto que cabe", 90) == "Texto curto que cabe"
    assert nomes.cortar("Curto", 90) == ""  # curto demais não diz o assunto
    # Uma referência inteira, que cabe, pode ficar.
    assert nomes.cortar("Definição da natureza do crime previsto no art. 14 da Lei 10.826/2003", 90).endswith("Lei 10.826/2003")


def test_assunto_da_ementa():
    assert nomes.assunto_da_ementa("Altera a Lei 9.096, de 1995 (Lei dos Partidos Políticos) para simplificar o processo de desfiliação partidária") == (
        "Simplificar o processo de desfiliação partidária"
    )
    assert nomes.assunto_da_ementa("Altera a Lei nº 7.347, de 24 de julho de 1985, a fim de prever a condenação em custas.") == "Prever a condenação em custas"
    # "para o Desenvolvimento" não é finalidade: só "para" seguido de verbo.
    assert nomes.assunto_da_ementa("Altera a Lei nº 1, de 2 de março de 2000, que cria o Fundo para o Desenvolvimento, para ampliar o prazo.") == "Ampliar o prazo"
    assert nomes.assunto_da_ementa("Cria o Protocolo de Acolhimento, altera as Leis nº 13.431, de 4 de abril de 2017, e dá outras providências.") == "Cria o Protocolo de Acolhimento"
    assert nomes.assunto_da_ementa("Acrescenta o procedimento judicial de homologação de acordo extrajudicial e dá outras providências.") == (
        "Acrescenta o procedimento judicial de homologação de acordo extrajudicial"
    )
    assert nomes.assunto_da_ementa("Altera dispositivos do Decreto-Lei nº 3.689, de 3 de outubro de 1941, relativos ao cabimento.") == ""
    assert nomes.assunto_da_ementa("") == "" and nomes.assunto_da_ementa(None) == ""
