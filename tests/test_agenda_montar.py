"""Testes da montagem do retrato da Agenda da semana (``qijournal/agenda/montar.py``), com os
dois arquivos de coleta reais da semana de 23 a 27/11/2026 (``tests/fixtures/agenda``)."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

import pytest

from qijournal.agenda import contrato, ligar, montar, regras, regua
from qijournal.agenda.retrato import LinhaTela, Retrato, ler_retrato
from tests.fixtures import agenda as fx
from tests.fixtures.pipeline.factory import make_config, make_edition, make_story

REGRAS = regras.carregar()
TERCA = "2026-11-24"  # a edição lê a coleta de 23/11 com 1 dia útil de idade
PROIBIDAS = ("vai votar", "será julgado", "deve aprovar", "polêmic", "pauta-bomba")
INSTITUICOES = ("STF", "STJ", "Câmara", "Senado", "Congresso")


def _com_dados_de(agenda: contrato.Agenda, dia: str, *, chaves: tuple[str, ...] | None = None) -> contrato.Agenda:
    """A mesma agenda, com o dia do dado das fontes trocado (todas, ou só as de ``chaves``)."""
    return replace(agenda, fontes=tuple(replace(f, data_dados=dia) if chaves is None or f.chave in chaves else f for f in agenda.fontes))


def _terca(**kwargs: Any) -> Retrato:
    return montar.montar(fx.camara_encerrada(), fx.tribunais(), TERCA, REGRAS, **kwargs)


def _textos(valor: Any, chave: str = "") -> Iterator[str]:
    """Todos os textos de tela de um retrato em dicionário (os endereços ficam de fora)."""
    if isinstance(valor, dict):
        for k, v in valor.items():
            yield from _textos(v, k)
    elif isinstance(valor, list):
        for v in valor:
            yield from _textos(v, chave)
    elif isinstance(valor, str) and chave not in ("link", "url"):
        yield valor


def _linhas_da_home(retrato: Retrato) -> dict[str, LinhaTela]:
    return {linha.id: linha for linha in retrato.linhas}


def _textos_da_home(retrato: Retrato) -> list[str]:
    dados = retrato.para_dict()
    dados.pop("pagina", None)
    return list(_textos(dados))


# ── a home da terça-feira, 24/11 ─────────────────────────────────────────────────────────────


def test_home_por_dia_com_a_hora_e_a_nota_no_empate():
    r = _terca()
    assert r.dia == TERCA and r.lados == ("congresso", "tribunais")
    assert r.janela == ("2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27", "2026-11-30")
    assert [(d.rotulo, [l.id for l in d.linhas]) for d in r.dias] == [
        ("ter 24", ["senado-594181", "camara-83393"]),
        ("qua 25", ["stf-plenario-2026-11-25", "senado-594182"]),  # as duas às 14h: a maior nota primeiro
        ("qui 26", []),
        ("sex 27", ["congresso-prazo-mpv-1381-2026"]),
        ("sáb 28", ["congresso-prazo-mpv-1382-2026"]),  # prazo que vence no sábado não some
        ("seg 30", []),
    ]
    assert [d.vazio for d in r.dias] == ["", "", "nada de peso pautado", "", "", "nada de peso pautado"]
    assert [d.principal for d in r.dias] == [True, True, False, False, False, False]
    assert r.dias[1].rotulo_longo == "quarta-feira, 25 de novembro"


def test_linha_traz_os_textos_de_tela_prontos():
    linhas = _linhas_da_home(_terca())
    stf = linhas["stf-plenario-2026-11-25"]
    assert (stf.data, stf.dia, stf.hora, stf.instituicao, stf.orgao, stf.cabeca) == ("2026-11-25", "qua 25", "14h", "STF", "Plenário", "STF · Plenário")
    assert stf.link.startswith("https://") and stf.nota == 80 and not stf.piso and stf.situacao == ""
    primeiro = stf.itens[0]
    assert primeiro.identificador == primeiro.identificador_tela == "ARE 1502069"
    assert primeiro.area == "Setor público"
    assert primeiro.nome == "Revisão de salário-base de professor municipal, com base no valor de atualização do piso…"
    assert primeiro.nome_curto == "Revisão de salário-base de professor municipal, com base…"
    assert primeiro.situacao == "volta ao julgamento depois do pedido de vista de Gilmar Mendes"
    assert primeiro.termo == "retorno de vista (Gilmar Mendes)"
    assert primeiro.link.startswith("https://") and primeiro.nota == 80
    assert primeiro.conta == "Plenário (30) + repercussão geral (30) + retorno de vista (20) = 80"
    assert (stf.mais, stf.mais_texto, stf.mais_celular, stf.mais_celular_texto) == (8, "mais 8", 9, "mais 9")

    senado = linhas["senado-594181"]
    assert senado.hora == "14h" and senado.cabeca == "Senado · Plenário"
    assert [(i.area, i.nome, i.identificador, i.situacao) for i in senado.itens] == [
        ("Trabalho", "Estatuto do Aprendiz", "PL 6461/2019", "pauta confirmada"),
        ("Política", "Institui o Plano Nacional de Cultura (PNC) para o decênio 2026-2036", "PL 5894/2025", "pauta confirmada"),
    ]

    prazo = linhas["congresso-prazo-mpv-1381-2026"]
    assert (prazo.hora, prazo.instituicao, prazo.orgao, prazo.cabeca) == ("", "Congresso", "", "Congresso")
    assert prazo.situacao == "Medida provisória perde a validade"
    assert prazo.itens[0].nome.startswith("Crédito Extraordinário") and prazo.itens[0].situacao == "" and prazo.mais_texto == ""


def test_hora_so_quando_a_fonte_informa():
    assert montar.hora_de_tela("14:00") == "14h" and montar.hora_de_tela("13:55") == "13h55" and montar.hora_de_tela("09:05") == "9h05"
    assert montar.hora_de_tela(None) == "" and montar.hora_de_tela("") == "" and montar.hora_de_tela("xx:yy") == ""
    assert montar.rotulo_do_dia("2026-11-25") == "qua 25" and montar.rotulo_do_dia("2026-11-28") == "sáb 28"
    assert montar.dia_mes("2026-12-02") == "02/12" and montar.dia_mes(None) == "" and montar.dia_mes("ontem") == ""
    domingo = montar.montar(_com_dados_de(fx.congresso(), "2026-11-27"), _com_dados_de(fx.tribunais(), "2026-11-27"), "2026-11-29", REGRAS)
    for linha in domingo.linhas:
        if linha.instituicao == "STJ":
            assert linha.hora == ""  # o STJ não informa a hora, e a agenda não afirma


# ── cortes e tetos ───────────────────────────────────────────────────────────────────────────


def test_so_entra_na_home_linha_com_nota_60_ou_mais_salvo_o_piso():
    r = _terca()
    for linha in r.linhas:
        assert linha.nota >= REGRAS.cortes.home or linha.piso
    # A CCJ do Senado (PEC pautada, 55) e a CCJC da Câmara (43) ficam para a página completa.
    assert "senado-reuniao-15077" not in _linhas_da_home(r) and "camara-83357" not in _linhas_da_home(r)
    na_pagina = {l.id: l for d in r.pagina.dias for l in d.linhas}
    assert na_pagina["senado-reuniao-15077"].nota == 55 and na_pagina["camara-83357"].nota == 43


def test_tetos_da_home():
    r = _terca()
    assert len(r.linhas) <= REGRAS.tetos.home.linhas == 8
    for linha in r.linhas:
        assert len(linha.itens) <= REGRAS.tetos.home.itens_por_linha == 2
        for item in linha.itens:
            assert 0 < len(item.nome) <= 90 and 0 < len(item.nome_curto) <= 60
    assert len(r.decidido) <= REGRAS.tetos.home.resultados == 2
    assert r.fora_do_teto == 0
    abertas = [l for l in r.linhas if l.aberta_no_celular]
    assert len(abertas) <= REGRAS.tetos.home.celular_linhas_hoje_amanha == 4
    assert all(l.data in r.janela[:2] for l in abertas)


def test_o_que_passa_do_teto_vira_contagem_e_o_piso_fica():
    # Doze sessões do Plenário do Senado no mesmo dia, todas com nota 63: só cabem as do teto.
    congresso = fx.congresso()
    base = next(s for s in congresso.sessoes if s.id == "senado-594181")
    copias = tuple(replace(base, id=f"senado-copia-{i}", hora=f"{8 + i:02d}:00") for i in range(12))
    cheia = replace(congresso, sessoes=(*congresso.sessoes, *copias))
    r = montar.montar(cheia, fx.tribunais(), TERCA, REGRAS)
    assert len(r.linhas) == 8
    assert r.fora_do_teto == 10  # 17 linhas acima do corte e a do piso da Câmara para 8 lugares
    assert {l.instituicao for l in r.linhas} >= {"STF", "Senado", "Câmara", "Congresso"}  # o piso não é espremido
    apertadas = replace(REGRAS, tetos=replace(REGRAS.tetos, home=replace(REGRAS.tetos.home, linhas=3, itens_por_linha=1)))
    r = montar.montar(cheia, fx.tribunais(), TERCA, apertadas)
    assert all(len(l.itens) <= 1 for l in r.linhas)
    assert {l.instituicao for l in r.linhas} >= {"STF", "Senado", "Câmara", "Congresso"}


def test_semana_fraca_mostra_menos_linhas_sem_completar_cota():
    so_comissoes = replace(fx.congresso(), sessoes=tuple(s for s in fx.congresso().sessoes if s.foro.startswith("comissao")))
    r = montar.montar(so_comissoes, None, TERCA, REGRAS)
    assert all(l.piso for l in r.linhas) and len(r.linhas) == 2  # só o piso da Câmara e o do Senado


# ── piso por instituição ─────────────────────────────────────────────────────────────────────


def test_as_cinco_instituicoes_aparecem_sempre():
    for retrato in (_terca(), montar.montar(fx.congresso(), fx.tribunais(), TERCA, REGRAS)):
        presentes = {l.instituicao for l in retrato.linhas} | {f.instituicao for f in retrato.frases}
        assert presentes >= set(INSTITUICOES)


def test_piso_com_o_melhor_item_nomeado_da_janela_em_uma_linha():
    piso = _linhas_da_home(_terca())["camara-83393"]
    assert piso.piso and piso.nota == 50 < REGRAS.cortes.home
    assert len(piso.itens) == 1 and piso.itens[0].identificador == "PLP 108/2021"
    assert piso.itens[0].nome.startswith("Permitir o enquadramento como Microempreendedor Individual (MEI)")


def test_sem_item_nomeado_na_janela_a_frase_traz_o_proximo_evento_com_o_assunto():
    r = _terca()
    assert not any(l.instituicao == "STJ" for l in r.linhas)  # na janela só há um agravo sem tema: não tem nome
    frase = next(f for f in r.frases if f.instituicao == "STJ")
    assert frase.tipo == "proximo"
    assert frase.texto == (
        "STJ: nada de alcance geral pautado até 30/11; em 02/12, Corte Especial, "
        "Definir se ocorre ou não a prescrição para a habilitação de herdeiros ou sucessores…"
    )
    assert frase.itens[0].identificador == "REsp 2034211" and frase.link.startswith("https://")


def test_proximo_evento_pode_ser_um_prazo_e_pode_nao_haver():
    domingo = montar.montar(_com_dados_de(fx.congresso(), "2026-11-27"), _com_dados_de(fx.tribunais(), "2026-11-27"), "2026-11-29", REGRAS)
    frases = {f.instituicao: f.texto for f in domingo.frases if f.tipo == "proximo"}
    assert frases["Congresso"].startswith("Congresso: nada de peso pautado até 04/12; em 07/12, Medida provisória perde a validade: Crédito extraordinário")
    assert frases["STF"] == "STF: nada de alcance geral pautado até 04/12."
    assert frases["Senado"] == "Senado: nada de peso pautado até 04/12."
    assert "Câmara" not in frases  # a frase da pauta não publicada já diz o que há


def test_sessao_marcada_sem_pauta_nao_vira_nada_pautado():
    # Na quinta, o STF só tem na janela a sessão de 26/11, ainda sem processos.
    tribunais = _com_dados_de(fx.tribunais(), "2026-11-25")
    r = montar.montar(None, tribunais, "2026-11-26", REGRAS)
    frase = next(f for f in r.frases if f.instituicao == "STF")
    assert frase.texto == "STF: pauta das sessões até 02/12 ainda não publicada."


# ── a Câmara: pauta não publicada e fila ─────────────────────────────────────────────────────


def test_camara_pauta_do_plenario_nao_publicada_e_a_fila_rotulada():
    frase = next(f for f in _terca().frases if f.tipo == "camara")
    assert frase.instituicao == "Câmara"
    assert frase.texto.startswith("Pauta do Plenário ainda não publicada pela Câmara. Ficaram sem votação na última sessão do Plenário, em 23/11: ")
    assert [i.identificador for i in frase.itens] == ["PL 7103/2014", "PL 5542/2026"]  # os 2 de maior nota que têm nome
    assert all(f"({i.identificador})" in frase.texto and i.nome in frase.texto for i in frase.itens)
    assert "não haverá" not in frase.texto and "sem sessão" not in frase.texto


def test_item_da_fila_ja_pautado_de_novo_nao_e_repetido():
    congresso = fx.congresso()
    fila = congresso.estado_de("camara").fila
    plenario = next(s for s in congresso.sessoes if s.id == fx.CAMARA_PLENARIO)
    hoje = replace(plenario, id="camara-hoje", data=TERCA, hora="13:55", situacao="agendada", itens=tuple(replace(i, estagio="urgencia", outros_estagios=()) for i in fila))
    estado = tuple(replace(e, pauta_publicada_ate=TERCA) if e.instituicao == "camara" else e for e in congresso.estado)
    r = montar.montar(replace(congresso, sessoes=(*congresso.sessoes, hoje), estado=estado), fx.tribunais(), TERCA, REGRAS)
    frase = next(f for f in r.frases if f.tipo == "camara")
    assert frase.texto == "Pauta do Plenário ainda não publicada pela Câmara para depois de 24/11."
    assert frase.itens == () and r.pagina.fila == ()
    assert "camara-hoje" in _linhas_da_home(r)


# ── frescor por fonte ────────────────────────────────────────────────────────────────────────


def test_fonte_velha_some_e_vira_frase_com_o_link_oficial():
    tribunais = _com_dados_de(fx.tribunais(), "2026-11-18", chaves=("stf_pauta",))  # 6 dias: acima do limite de 4 da fonte
    r = montar.montar(fx.camara_encerrada(), tribunais, TERCA, REGRAS)
    assert not any(l.instituicao == "STF" for l in r.linhas)
    assert not any(l.instituicao == "STF" for d in r.pagina.dias for l in d.linhas)
    frase = next(f for f in r.frases if f.instituicao == "STF")
    assert frase.tipo == "fonte"
    assert frase.texto == "STF: sem dado novo desde 18/11; os itens saíram da agenda."
    assert frase.link == "https://portal.stf.jus.br/pauta/" and frase.link_rotulo == "fonte oficial"
    assert not any(f.tipo == "proximo" and f.instituicao == "STF" for f in r.frases)  # sem dado, não se afirma "nada pautado"
    # O pé passa a trazer a data por fonte.
    assert r.pe.texto == "Conferido em 23/11. A pauta pode mudar."
    assert "STF: dados de 18/11" in r.pe.fontes and "Senado: dados de 23/11" in r.pe.fontes
    estado = {f.chave: f for f in r.pagina.fontes}
    assert estado["stf_pauta"].estado == "some" and estado["stj_pautas"].estado == "ok"
    assert estado["stf_pauta"].texto == "sem dado novo desde 18/11; os itens desta fonte saíram da agenda"


def test_uma_fonte_velha_entre_as_de_uma_casa_derruba_so_as_linhas_dela():
    congresso = _com_dados_de(fx.camara_encerrada(), "2026-11-10", chaves=("senado_comissoes",))
    r = montar.montar(congresso, fx.tribunais(), TERCA, REGRAS)
    na_pagina = {l.id for d in r.pagina.dias for l in d.linhas}
    assert "senado-reuniao-15077" not in na_pagina and "senado-594181" in na_pagina
    frase = next(f for f in r.frases if f.tipo == "fonte")
    assert frase.texto == "Senado: agenda das comissões: sem dado novo desde 10/11; os itens desta fonte saíram da agenda."
    assert frase.link.startswith("https://legis.senado.leg.br/")


def test_aviso_a_partir_de_dois_dias_uteis_mantem_os_itens_e_mostra_a_data():
    # Dado de sexta lido na terça: 2 dias úteis, 4 dias corridos (ainda dentro do limite).
    congresso, tribunais = _com_dados_de(fx.camara_encerrada(), "2026-11-23"), _com_dados_de(fx.tribunais(), "2026-11-20", chaves=("stf_pauta",))
    r = montar.montar(congresso, tribunais, TERCA, REGRAS)
    assert any(l.instituicao == "STF" for l in r.linhas)
    assert not any(f.tipo == "fonte" for f in r.frases)
    assert r.pe.fontes == ("Câmara: dados de 23/11", "Senado: dados de 23/11", "Congresso: dados de 23/11", "STF: dados de 20/11", "STJ: dados de 23/11")
    assert next(f for f in r.pagina.fontes if f.chave == "stf_pauta").texto == "dados de 20/11, pode ter mudado"


def test_pe_com_a_data_de_conferencia_e_sem_data_por_fonte_quando_tudo_esta_novo():
    r = _terca()
    assert r.pe.texto == "Conferido em 23/11. A pauta pode mudar."
    assert (r.pe.conferido, r.pe.conferido_em, r.pe.fontes) == ("23/11", "2026-11-23", ())


def test_limite_da_fonte_vem_de_meta_json_quando_o_arquivo_de_coleta_nao_traz():
    tribunais = fx.tribunais()
    sem_limite = replace(tribunais, fontes=tuple(replace(f, velho_dias=None, data_dados="2026-11-19") for f in tribunais.fontes))
    # Sem limite próprio vale o padrão (4 dias corridos): 5 dias some.
    assert not any(l.instituicao in ("STF", "STJ") for l in montar.montar(None, sem_limite, TERCA, REGRAS).linhas)
    com_meta = montar.montar(None, sem_limite, TERCA, REGRAS, velho_dias={"stf_pauta": 10, "stj_pautas": 10})
    assert any(l.instituicao == "STF" for l in com_meta.linhas)


def test_tudo_velho_vira_uma_frase_so_e_nenhum_dia():
    r = montar.montar(fx.congresso(), fx.tribunais(), "2026-12-05", REGRAS)
    assert r.dias == () and r.decidido == () and r.linhas == ()
    assert [f.texto for f in r.frases] == ["Sem pauta válida desde 23/11."]
    assert r.pagina.sem_pauta == "Sem pauta válida desde 23/11." and r.pagina.dias == ()
    assert not r.vazio  # a frase é o que a tela mostra


# ── um lado só ───────────────────────────────────────────────────────────────────────────────


def test_funciona_so_com_o_congresso():
    r = montar.montar(fx.camara_encerrada(), None, TERCA, REGRAS)
    assert r.lados == ("congresso",)
    assert {l.instituicao for l in r.linhas} == {"Câmara", "Senado", "Congresso"}
    assert r.frases[0].texto == "STF e STJ: pauta indisponível nesta edição." and r.frases[0].tipo == "fonte"
    assert r.pe.texto == "Conferido em 23/11. A pauta pode mudar."
    assert not any("STF:" in limite or "STJ:" in limite for limite in r.pagina.limites)


def test_funciona_so_com_os_tribunais():
    r = montar.montar(None, fx.tribunais(), TERCA, REGRAS)
    assert r.lados == ("tribunais",)
    assert {l.instituicao for l in r.linhas} == {"STF"}
    assert r.frases[0].texto == "Câmara, Senado e Congresso: pauta indisponível nesta edição."
    assert any(f.instituicao == "STJ" and f.tipo == "proximo" for f in r.frases)
    assert not any(f.tipo == "camara" for f in r.frases)


def test_sem_lado_nenhum_o_retrato_so_diz_que_nao_ha_pauta():
    r = montar.montar(None, None, TERCA, REGRAS)
    assert r.lados == () and r.dias == () and [f.texto for f in r.frases] == ["Sem pauta válida nesta edição."]
    assert r.pe.texto == "A pauta pode mudar."


# ── sábado e domingo ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("dia", ["2026-11-28", "2026-11-29"])
def test_edicao_de_fim_de_semana_comeca_na_segunda(dia: str):
    congresso, tribunais = _com_dados_de(fx.camara_encerrada(), "2026-11-27"), _com_dados_de(fx.tribunais(), "2026-11-27")
    r = montar.montar(congresso, tribunais, dia, REGRAS)
    assert r.janela == ("2026-11-30", "2026-12-01", "2026-12-02", "2026-12-03", "2026-12-04")
    datas = [d.data for d in r.dias]
    assert set(r.janela) <= set(datas)
    if dia == "2026-11-28":  # o prazo que vence no próprio sábado aparece com a sua data
        assert datas[0] == "2026-11-28" and r.dias[0].linhas[0].id == "congresso-prazo-mpv-1382-2026"
    else:
        assert datas == list(r.janela)
    assert [l.id for l in r.linhas if l.instituicao == "STJ"] == ["stj-ce-2026-12-02", "stj-s2-2026-12-03", "stj-s3-2026-12-03"]
    # A semana inteira já passou: vai para "Já decidido".
    assert [(l.dia, l.cabeca) for l in r.decidido] == [("seg 23", "Câmara · Plenário"), ("qua 25", "STF · Plenário")]
    passados = [d.data for d in r.pagina.decidido]
    assert passados[:3] == ["2026-11-23", "2026-11-24", "2026-11-25"] and all(d < dia for d in passados)
    assert all(d.passado and not d.principal for d in r.pagina.decidido)


# ── "Já decidido esta semana" ────────────────────────────────────────────────────────────────


def test_dia_passado_com_resultado_oficial_so_a_palavra():
    r = _terca()
    assert len(r.decidido) == 1
    linha = r.decidido[0]
    assert (linha.dia, linha.cabeca, linha.passado, linha.situacao, linha.mais_texto) == ("seg 23", "Câmara · Plenário", True, "", "")
    item = linha.itens[0]
    assert item.identificador_tela == "PLP 299/2026 (pacote fiscal)"  # o apelido aprovado, entre parênteses
    assert item.nome == "Ajuste fiscal"
    assert item.situacao == "texto principal aprovado; faltam os trechos votados em separado"
    assert item.resultado == "aprovado, ressalvados os destaques"
    # Nenhum placar: o "127 a 347" do requerimento de adiamento nunca sai como resultado.
    for texto in _textos(r.para_dict()):
        assert "127 a 347" not in texto and "347" not in texto and not re.search(r"(?i)\b(?:sim|não|placar)\s*:\s*\d", texto), texto


def test_palavra_aprovado_com_frase_de_destaques_e_lida_pela_frase():
    resultado = contrato.Resultado(palavra="aprovado", texto=fx.RESULTADO_PLP_299)
    assert montar.palavra_do_resultado(resultado) == "aprovado_ressalvados_destaques"
    assert montar.palavra_do_resultado(contrato.Resultado(palavra="aprovado", texto="Aprovado o Projeto.")) == "aprovado"
    assert montar.palavra_do_resultado(contrato.Resultado(palavra="rejeitado", texto=None)) == "rejeitado"


def test_sessao_que_nao_esta_encerrada_aparece_em_andamento_sem_resultado():
    r = montar.montar(fx.congresso(), fx.tribunais(), TERCA, REGRAS)  # na coleta de 23/11 a sessão ainda corria
    linha = r.decidido[0]
    assert (linha.cabeca, linha.situacao) == ("Câmara · Plenário", "em andamento")
    assert linha.itens[0].identificador == "PLP 299/2026" and linha.itens[0].situacao == "" and linha.itens[0].resultado == ""
    assert linha.itens[0].nota == 85  # a nota é a do registro: a sessão constava em andamento, com o item em votação


def test_sessao_mantida_do_arquivo_anterior_diz_como_pautado_em():
    congresso = fx.congresso()
    sessoes = tuple(replace(s, como_pautado_em="2026-11-23") if s.id == fx.CAMARA_PLENARIO else s for s in congresso.sessoes)
    r = montar.montar(replace(congresso, sessoes=sessoes), fx.tribunais(), TERCA, REGRAS)
    linha = r.decidido[0]
    assert linha.situacao == "como pautado em 23/11" and linha.itens[0].resultado == "" and linha.itens[0].situacao == ""


def test_tribunais_no_dia_passado_apontam_o_andamento_oficial():
    sexta = montar.montar(_com_dados_de(fx.camara_encerrada(), "2026-11-26"), _com_dados_de(fx.tribunais(), "2026-11-26"), "2026-11-27", REGRAS)
    stf = next(l for l in sexta.decidido if l.instituicao == "STF")
    assert stf.itens[0].situacao == "resultado no andamento oficial" and stf.itens[0].resultado == ""
    assert stf.itens[0].link.startswith("https://")
    # Até 2 na home; todos na página completa.
    assert len(sexta.decidido) == 2
    todos = [l for d in sexta.pagina.decidido for l in d.linhas]
    assert len(todos) > 2 and {l.data for l in todos} <= {"2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26"}
    palavras = {i.situacao for l in todos for i in l.itens}
    assert "resultado no andamento oficial" in palavras and "texto principal aprovado; faltam os trechos votados em separado" in palavras


def test_na_segunda_feira_nao_ha_dia_passado():
    r = montar.montar(fx.congresso(), fx.tribunais(), "2026-11-23", REGRAS)
    assert r.decidido == () and r.pagina.decidido == ()
    assert _linhas_da_home(r)[fx.CAMARA_PLENARIO].situacao == "em andamento"
    assert _linhas_da_home(r)[fx.CAMARA_PLENARIO].itens[0].situacao == "em votação"


# ── redação ──────────────────────────────────────────────────────────────────────────────────


def _varios() -> list[Retrato]:
    sexta_c, sexta_t = _com_dados_de(fx.camara_encerrada(), "2026-11-27"), _com_dados_de(fx.tribunais(), "2026-11-27")
    return [
        _terca(),
        montar.montar(fx.congresso(), fx.tribunais(), "2026-11-23", REGRAS),
        montar.montar(fx.congresso(), fx.tribunais(), TERCA, REGRAS),
        montar.montar(sexta_c, sexta_t, "2026-11-28", REGRAS),
        montar.montar(sexta_c, sexta_t, "2026-11-29", REGRAS),
        montar.montar(fx.camara_encerrada(), None, TERCA, REGRAS),
        montar.montar(None, fx.tribunais(), TERCA, REGRAS),
        montar.montar(fx.congresso(), fx.tribunais(), "2026-12-05", REGRAS),
        montar.montar(None, None, TERCA, REGRAS),
    ]


def test_nenhuma_expressao_proibida_em_nenhum_texto_de_tela():
    for retrato in _varios():
        for texto in _textos(retrato.para_dict()):
            baixo = texto.lower()
            for proibida in (*PROIBIDAS, *regras.EXPRESSOES_PROIBIDAS):
                assert proibida not in baixo, (proibida, texto)
            assert " " not in texto and "  " not in texto, texto


def test_o_verbo_e_pautado():
    situacoes = {i.situacao for r in _varios() for d in r.pagina.dias for l in d.linhas for i in l.itens}
    assert {"pautado", "pautado com urgência", "pauta confirmada"} <= situacoes
    frases = [f.texto for r in _varios() for f in r.frases]
    assert any("pautado até" in f for f in frases)


def test_home_sem_contagem_e_sem_frase_de_ausencia_de_cobertura():
    for retrato in _varios():
        for texto in _textos_da_home(retrato):
            assert not re.search(r"\d[\d.]*\s+(?:itens|processos|vetos|recursos|sess[õo]es|eventos|audi[êe]ncias)\b", texto) or texto.endswith("itens pautados"), texto
            assert "cobertura" not in texto.lower() and "sem menção" not in texto.lower(), texto
        assert all(f.tipo in ("fonte", "proximo", "camara", "recesso") for f in retrato.frases)


def test_item_sem_ligacao_fica_calado_e_item_ligado_ganha_a_marca():
    calado = _terca()
    for linha in (*calado.linhas, *calado.decidido):
        for item in linha.itens:
            assert (item.midia, item.midia_texto, item.midia_pagina, item.materia) == (0, "", "", "")
    ligacoes = {
        "RE 1326541": ligar.Ligacao(veiculos=("Conjur", "Folha de S.Paulo", "JOTA"), materias=("materia-2", "materia-9")),
        "PL 6461/2019": ligar.Ligacao(veiculos=("Valor Econômico",)),
        "PLP 299/2026": ligar.Ligacao(veiculos=("G1", "O Globo"), materias=("materia-1",)),
    }
    r = _terca(ligacoes=ligacoes)
    itens = {i.identificador: i for l in (*r.linhas, *r.decidido) for i in l.itens}
    assert (itens["RE 1326541"].midia, itens["RE 1326541"].midia_texto, itens["RE 1326541"].materia) == (3, "na mídia: 3 veículos", "materia-2")
    assert itens["RE 1326541"].midia_pagina == "menção direta encontrada em 3 veículos"
    assert (itens["PL 6461/2019"].midia_texto, itens["PL 6461/2019"].materia) == ("na mídia: 1 veículo", "")
    assert itens["ARE 1502069"].midia_texto == ""
    # A chamada da matéria ligada ganha "na agenda: qua 25" (só de item pautado na janela).
    assert [(m.materia, m.rotulo, m.data, m.identificador, m.linha) for m in r.materias] == [
        ("materia-2", "na agenda: qua 25", "2026-11-25", "RE 1326541", "stf-plenario-2026-11-25"),
        ("materia-9", "na agenda: qua 25", "2026-11-25", "RE 1326541", "stf-plenario-2026-11-25"),
    ]
    assert r.rotulo_da_materia("materia-2") == "na agenda: qua 25" and r.rotulo_da_materia("materia-1") == ""


def test_a_midia_nao_escolhe_o_que_entra():
    ligacoes = {a.identificador: ligar.Ligacao(veiculos=("A", "B", "C", "D")) for a in ligar.alvos_da_agenda(fx.congresso(), fx.tribunais())}
    sem, com = _terca(), _terca(ligacoes=ligacoes)
    assert [(l.id, l.nota, [i.identificador for i in l.itens]) for l in sem.linhas] == [(l.id, l.nota, [i.identificador for i in l.itens]) for l in com.linhas]
    assert [f.texto for f in sem.frases] == [f.texto for f in com.frases]


# ── a página completa ────────────────────────────────────────────────────────────────────────


def test_pagina_traz_os_itens_de_nota_40_ou_mais_com_a_conta_e_o_termo_oficial():
    pagina = _terca().pagina
    assert (pagina.corte, pagina.corte_home) == (40, 60)
    assert [d.data for d in pagina.dias] == ["2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27", "2026-11-28", "2026-11-30"]
    linhas = [l for d in pagina.dias for l in d.linhas]
    itens = [i for l in linhas for i in l.itens]
    assert len(itens) > 60
    for item in itens:
        assert item.nota >= 40 and item.conta.endswith(f"= {item.nota}") and item.termo and item.link.startswith("https://")
    assert all(l.mais == 0 and l.mais_texto == "" for l in linhas)
    stf = next(l for l in linhas if l.id == "stf-plenario-2026-11-25")
    assert len(stf.itens) == 10  # todos, com ou sem nome
    adi = next(i for i in stf.itens if i.identificador == "ADI 6002")
    assert (adi.nome, adi.nota, adi.termo) == ("", 75, "destaque (Flávio Dino)")
    assert adi.situacao == "pautado no Plenário depois de sair do julgamento virtual a pedido de Flávio Dino"
    urgente = next(i for l in linhas for i in l.itens if i.identificador == "PLP 108/2021")
    assert urgente.termo.lower().startswith("urgência") and urgente.termo.lower().count("urgência") == 1


def test_pagina_mostra_a_sessao_sem_pauta_e_o_que_ficou_fora():
    pagina = _terca().pagina
    linhas = {l.id: l for d in pagina.dias for l in d.linhas}
    assert linhas["stf-plenario-2026-11-26"].situacao == "pauta ainda não publicada" and linhas["stf-plenario-2026-11-26"].itens == ()
    assert linhas["camara-83412"].situacao == "pauta ainda não publicada"
    assert not any(l.id.startswith("stj-t") for l in linhas.values())  # as Turmas do STJ ficam só na contagem
    ccjc = linhas["camara-83357"]
    assert [c.texto for c in ccjc.fora] == ["requerimentos de rotina: 1", "redações finais: 17", "outorgas de rádio e TV: 34"]
    ce = linhas["stj-ce-2026-11-25"]
    assert {c.rotulo for c in ce.fora} == {"em segredo de justiça", "recursos internos em lista", "agravos contra decisões da Vice-Presidência"}
    assert all(c.n > 0 and c.texto.startswith(f"{c.rotulo}: ") for c in ce.fora)
    rotulos = [c.rotulo for c in pagina.fora]
    assert rotulos[:2] == ["em segredo de justiça", "recursos internos em lista"] and all(c.n > 0 for c in pagina.fora)
    assert pagina.abaixo_do_corte == sum(l.abaixo_do_corte for l in linhas.values()) + sum(l.abaixo_do_corte for d in (*pagina.decidido, *pagina.radar) for l in d.linhas)
    assert pagina.fora_do_teto == 0


def test_no_radar_o_que_tem_data_depois_da_janela():
    r = _terca()
    datas = [d.data for d in r.pagina.radar]
    assert datas == sorted(datas) and all(d > r.janela[-1] for d in datas)
    ids = [l.id for d in r.pagina.radar for l in d.linhas]
    assert "stj-ce-2026-12-02" in ids and "stj-s2-2026-12-09" in ids and "congresso-prazo-mpv-1395-2026" in ids  # os dois últimos vêm de "próximo"
    assert len(ids) == len(set(ids))
    assert all(i.nota >= 40 for d in r.pagina.radar for l in d.linhas for i in l.itens)


def test_pagina_limites_fontes_e_contagens():
    pagina = _terca().pagina
    limites = " ".join(pagina.limites)
    assert "STF: só o Plenário presencial" in limites and "não informa a hora nem o assunto" in limites
    assert "(CCJC e CFT)" in limites and "(CCJ e CAE)" in limites and "(CMO)" in limites
    assert "A cobertura da imprensa não pontua" in limites
    assert [f.chave for f in pagina.fontes] == sorted(f.chave for f in pagina.fontes) and len(pagina.fontes) == 8
    assert all(f.url.startswith("https://") and f.nome and f.data and f.texto for f in pagina.fontes)
    textos = [c.texto for c in pagina.contagens]
    assert "Congresso: nenhuma sessão conjunta convocada" in textos
    assert any(t.startswith("STF, julgamento virtual de ") and "na Primeira Turma" in t for t in textos)
    assert "Câmara, sessões solenes: 5" in textos and "Congresso, vetos que trancam a pauta: 112" in textos
    assert pagina.fila_texto == "Ficaram sem votação na última sessão do Plenário da Câmara, em 23/11"
    assert len(pagina.fila) == 12 and all(i.nota >= 40 for i in pagina.fila)
    assert [f.texto for f in pagina.frases] == [f.texto for f in _terca().frases]


def test_teto_de_linhas_da_pagina():
    apertadas = replace(REGRAS, tetos=replace(REGRAS.tetos, pagina=replace(REGRAS.tetos.pagina, linhas=3)))
    pagina = montar.montar(fx.camara_encerrada(), fx.tribunais(), TERCA, apertadas).pagina
    linhas = [l for d in pagina.dias for l in d.linhas]
    assert len(linhas) == 3 and pagina.fora_do_teto > 0
    assert sorted(l.nota for l in linhas) == [70, 70, 80]  # ficam as de maior nota


def test_sessao_cancelada_fica_fora_da_home_e_aparece_na_pagina():
    tribunais = fx.tribunais()
    sessoes = tuple(replace(s, situacao="cancelada") if s.id == fx.STF_PLENARIO else s for s in tribunais.sessoes)
    r = montar.montar(fx.camara_encerrada(), replace(tribunais, sessoes=sessoes), TERCA, REGRAS)
    assert fx.STF_PLENARIO not in _linhas_da_home(r)
    assert next(l for d in r.pagina.dias for l in d.linhas if l.id == fx.STF_PLENARIO).situacao == "cancelada"


# ── o retrato inteiro ────────────────────────────────────────────────────────────────────────


def test_retrato_real_faz_ida_e_volta_e_cabe_no_json_da_edicao():
    for retrato in _varios():
        texto = json.dumps(retrato.para_dict(), ensure_ascii=False, separators=(",", ":"))
        assert ler_retrato(json.loads(texto)) == retrato
        assert len(texto.encode("utf-8")) < 150_000


def test_montar_e_puro_e_deterministico():
    congresso, tribunais = fx.camara_encerrada(), fx.tribunais()
    antes = (contrato.para_json(congresso), contrato.para_json(tribunais))
    a, b = montar.montar(congresso, tribunais, TERCA, REGRAS), montar.montar(congresso, tribunais, TERCA, REGRAS)
    assert a == b and (contrato.para_json(congresso), contrato.para_json(tribunais)) == antes
    assert montar.montar(congresso, tribunais, TERCA) .janela == a.janela  # sem regras: os padrões do código


def test_notas_do_retrato_sao_as_da_regua():
    r = _terca()
    sessao = next(s for s in fx.tribunais().sessoes if s.id == fx.STF_PLENARIO)
    esperadas = {item.identificador: nota.total for nota, item in regua.notas_da_sessao(sessao, REGRAS)}
    linha = next(l for d in r.pagina.dias for l in d.linhas if l.id == fx.STF_PLENARIO)
    assert {i.identificador: i.nota for i in linha.itens} == esperadas


# ── a borda: leitura dos arquivos ────────────────────────────────────────────────────────────


def _pasta_de_dados(tmp_path: Path, *, congresso: bool = True, tribunais: bool = True) -> Path:
    dados = tmp_path / "data"
    if congresso:
        (dados / "congresso").mkdir(parents=True)
        shutil.copy(fx.CONGRESSO, dados / "congresso" / "agenda.json")
    if tribunais:
        (dados / "tribunais").mkdir(parents=True)
        shutil.copy(fx.TRIBUNAIS, dados / "tribunais" / "agenda.json")
    return dados


def _edicao(dia: str = TERCA):
    edicao = make_edition(date=dia)
    edicao.stories[0] = replace(make_story(1, headline="Supremo retoma o Tema 1218 sobre o piso do magistério"), id="piso")
    return edicao


def test_do_dia_le_os_arquivos_e_liga_com_a_edicao_e_com_a_busca(tmp_path: Path):
    dados = _pasta_de_dados(tmp_path)
    busca = dados / "busca"
    busca.mkdir()
    antigo = {"data": "2026-11-23", "veiculos": ["O Globo", "STF"], "noticias": [[0, -1, 0, "STF pauta o RE 1.326.541", "x", 1], [0, -1, 1, "Plenário julga o RE 1326541", "y", 2]]}
    (busca / "2026-11-23.json").write_text(json.dumps(antigo), encoding="utf-8")
    (busca / "2026-11-22.json").write_text("{quebrado", encoding="utf-8")  # ilegível: pulado
    (busca / "2026-11-24.json").write_text(json.dumps({**antigo, "veiculos": ["Não Conta", "STF"]}), encoding="utf-8")  # o do próprio dia não é lido
    (busca / "2026-11-10.json").write_text(json.dumps({**antigo, "veiculos": ["Velho Demais", "STF"]}), encoding="utf-8")
    for i in range(11, 18):  # sete edições mais novas que a de 10/11: ela sai da janela de 7
        (busca / f"2026-11-{i}.json").write_text(json.dumps({"data": f"2026-11-{i}", "veiculos": [], "noticias": []}), encoding="utf-8")
    r = montar.do_dia(_edicao(), dados, make_config())
    assert r is not None and r.lados == ("congresso", "tribunais")
    item = next(i for l in r.linhas for i in l.itens if i.identificador == "RE 1326541")
    assert item.midia_texto == "na mídia: 2 veículos" and item.materia == "piso"  # Valor (a matéria) e O Globo; o canal do STF não conta
    assert r.rotulo_da_materia("piso") == "na agenda: qua 25"


def test_do_dia_sem_arquivo_nenhum_devolve_none(tmp_path: Path):
    assert montar.do_dia(_edicao(), tmp_path / "data", make_config()) is None
    (tmp_path / "data" / "congresso").mkdir(parents=True)
    (tmp_path / "data" / "congresso" / "agenda.json").write_text("{não é json", encoding="utf-8")
    assert montar.do_dia(_edicao(), tmp_path / "data", make_config()) is None
    (tmp_path / "data" / "congresso" / "agenda.json").write_text(json.dumps({"versao": 99}), encoding="utf-8")
    assert montar.do_dia(_edicao(), tmp_path / "data", make_config()) is None


def test_do_dia_com_um_lado_so_e_sem_busca(tmp_path: Path):
    r = montar.do_dia(_edicao(), _pasta_de_dados(tmp_path, tribunais=False), make_config())
    assert r is not None and r.lados == ("congresso",)
    assert r.frases[0].texto == "STF e STJ: pauta indisponível nesta edição."
    r = montar.do_dia(_edicao(), _pasta_de_dados(tmp_path / "outro", congresso=False), None)  # sem configuração: sem a lista de feeds oficiais
    assert r is not None and r.lados == ("tribunais",)


def test_do_dia_sobrevive_a_falha_da_ligacao(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def quebrada(*args, **kwargs):
        raise RuntimeError("ligação quebrada")

    monkeypatch.setattr(ligar, "ligar", quebrada)
    r = montar.do_dia(_edicao(), _pasta_de_dados(tmp_path), make_config())
    assert r is not None and len(r.linhas) >= 4 and r.materias == ()


def test_limite_das_fontes_em_meta_json(tmp_path: Path):
    meta = tmp_path / "meta.json"
    assert montar.ler_velho_dias(meta) == {}
    meta.write_text("{lixo", encoding="utf-8")
    assert montar.ler_velho_dias(meta) == {}
    meta.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert montar.ler_velho_dias(meta) == {}
    meta.write_text(json.dumps({"fontes": {"stf_pauta": {"velho_dias": 4}, "stj_pautas": {"velho_dias": "4"}, "tpu": {"velho_dias": True}, "x": 3, "y": {"velho_dias": 0}}}), encoding="utf-8")
    assert montar.ler_velho_dias(meta) == {"stf_pauta": 4}


def test_instituicao_sem_registro_de_fonte():
    congresso = fx.camara_encerrada()
    sem_fonte = replace(congresso, fontes=tuple(f for f in congresso.fontes if f.instituicao != "senado"))
    # Sem o registro da fonte, mas com as sessões: valem as linhas, pela data do arquivo.
    r = montar.montar(sem_fonte, None, TERCA, REGRAS)
    assert "Senado" in {l.instituicao for l in r.linhas} and not any(f.instituicao == "Senado" for f in r.frases)
    # Sem a fonte e sem sessão nenhuma: a instituição não foi coletada.
    vazio = replace(sem_fonte, sessoes=tuple(s for s in sem_fonte.sessoes if s.instituicao != "senado"))
    r = montar.montar(vazio, None, TERCA, REGRAS)
    assert "Senado: pauta indisponível nesta edição." in [f.texto for f in r.frases]
    assert "Senado" not in {l.instituicao for l in r.linhas}
