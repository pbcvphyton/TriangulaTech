"""O contrato da Agenda da semana (qijournal/agenda/contrato.py): o vocabulário, a validação, o
leitor tolerante, a ida e volta determinística, a mesclagem com o arquivo anterior e a janela.
Sem rede: os dois arquivos de exemplo estão em tests/fixtures/agenda/."""

from __future__ import annotations

import copy
import json
import random
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from qijournal.agenda import contrato
from qijournal.agenda.contrato import Agenda, EstadoInstituicao, ItemAgenda, SessaoAgenda
from tests.fixtures import agenda as fx

LADOS = {"congresso": fx.CONGRESSO, "tribunais": fx.TRIBUNAIS}


def _bruto(lado: str) -> dict[str, Any]:
    return json.loads(LADOS[lado].read_text(encoding="utf-8"))


@pytest.fixture()
def congresso() -> dict[str, Any]:
    return _bruto("congresso")


@pytest.fixture()
def tribunais() -> dict[str, Any]:
    return _bruto("tribunais")


def _sessao(dados: dict[str, Any], ident: str) -> dict[str, Any]:
    return next(s for s in dados["sessoes"] if s["id"] == ident)


def _item(dados: dict[str, Any], sessao: str, identificador: str) -> dict[str, Any]:
    return next(i for i in _sessao(dados, sessao)["itens"] if i["identificador"] == identificador)


# ── vocabulário ──────────────────────────────────────────────────────────────────────────────


def test_todo_o_vocabulario_tem_termo_por_extenso() -> None:
    pares = [
        (contrato.INSTITUICOES, contrato.NOME_INSTITUICAO), (contrato.FOROS, contrato.TERMO_FORO),
        (contrato.TIPOS_SESSAO, contrato.TERMO_TIPO_SESSAO), (contrato.SITUACOES_SESSAO, contrato.TERMO_SITUACAO),
        (contrato.MODALIDADES, contrato.TERMO_MODALIDADE), (contrato.ESTADOS_PAUTA, contrato.TERMO_PAUTA),
        (contrato.ALCANCES, contrato.TERMO_ALCANCE), (contrato.ESTAGIOS, contrato.TERMO_ESTAGIO),
        (contrato.PALAVRAS_RESULTADO, contrato.TERMO_RESULTADO), (contrato.TIPOS_TEMA, contrato.TERMO_TEMA),
        (contrato.MOTIVOS_FORA, contrato.TERMO_FORA),
    ]
    for vocabulario, termos in pares:
        assert len(set(vocabulario)) == len(vocabulario)
        assert set(termos) == set(vocabulario)
        assert all(t and "—" not in t and "_" not in t for t in termos.values())
    assert set(contrato.FOROS_DA_INSTITUICAO) == set(contrato.INSTITUICOES)
    assert {f for foros in contrato.FOROS_DA_INSTITUICAO.values() for f in foros} == set(contrato.FOROS)
    assert sorted(i for insts in contrato.INSTITUICOES_DO_LADO.values() for i in insts) == sorted(contrato.INSTITUICOES)
    assert contrato.termo("retorno_de_vista") == "retorno de vista"
    assert contrato.termo("aprovado_ressalvados_destaques") == "aprovado, ressalvados os destaques"
    assert contrato.termo("em_andamento") == "em andamento"
    assert contrato.termo("coisa_nova") == "coisa nova"


def test_o_vocabulario_nunca_fala_em_previsao() -> None:
    """O verbo é "pautado": nenhum termo promete voto ou julgamento."""
    textos = " ".join(" ".join(tabela.values()) for tabela in (contrato.TERMO_ESTAGIO, contrato.TERMO_RESULTADO, contrato.TERMO_SITUACAO)).lower()
    for proibido in ("vai votar", "será julgado", "será votado", "polêmic", "pauta-bomba"):
        assert proibido not in textos


def test_estagio_principal_segue_a_ordem_do_vocabulario() -> None:
    assert contrato.estagio_principal(["pautado", "destaque", "referendo_de_cautelar"]) == "referendo_de_cautelar"
    assert contrato.estagio_principal(["urgencia", "em_votacao"]) == "em_votacao"
    assert contrato.estagio_principal(["pauta_confirmada", "urgencia"]) == "urgencia"
    assert contrato.estagio_principal([]) == "pautado"
    assert contrato.estagio_principal(["inventado"]) == "pautado"


# ── os dois arquivos de exemplo ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lado", sorted(LADOS))
def test_os_arquivos_de_exemplo_sao_validos(lado: str) -> None:
    bruto = _bruto(lado)
    assert contrato.validar(bruto) == []
    agenda = contrato.ler(LADOS[lado], lado=lado)
    assert agenda is not None and agenda.lado == lado and agenda.versao == contrato.VERSAO
    assert agenda.hoje == fx.HOJE and agenda.janela == ("2026-11-23", "2026-12-06")
    assert {s.instituicao for s in agenda.sessoes} <= set(contrato.INSTITUICOES_DO_LADO[lado])
    for sessao in agenda.sessoes:
        assert contrato.link_oficial(sessao.link)
        for item in sessao.itens:
            assert contrato.link_oficial(item.link) and item.segredo is False
            assert all(p.tipo in contrato.TIPOS_PARTE for p in item.partes)


def test_o_exemplo_do_congresso_traz_a_semana_real(congresso: dict[str, Any]) -> None:
    agenda = fx.congresso()
    plenario = next(s for s in agenda.sessoes if s.id == fx.CAMARA_PLENARIO)
    assert (plenario.data, plenario.foro, plenario.situacao, plenario.modalidade) == ("2026-11-23", "plenario", "em_andamento", "semipresencial")
    plp = next(i for i in plenario.itens if i.identificador == "PLP 299/2026")
    assert plp.alcance == "plp" and plp.estagios == ("em_votacao", "urgencia") and plp.resultado is None  # sessão não encerrada: sem resultado
    assert plp.apelido == "Ajuste fiscal" and plp.ficha == "sf-PLP-299-2026" and plp.area == "economia"
    assert "nº 200" in plp.ementa and "?" not in plp.ementa  # o "nº?200" da API da Câmara foi limpo
    assert dict(plenario.fora) == {"requerimento": 1, "simbolica": 1} and len({i.identificador for i in plenario.itens}) == len(plenario.itens)
    senado = [s for s in agenda.sessoes_de("senado") if s.foro == "plenario"]
    assert [(s.data, s.hora, s.pauta_confirmada, len(s.itens)) for s in senado] == [
        ("2026-11-24", "14:00", True, 4), ("2026-11-25", "14:00", True, 3)]
    assert senado[0].itens[0].apelido == "Estatuto do Aprendiz" and senado[0].itens[0].estagio == "pauta_confirmada"
    ccjc = next(s for s in agenda.sessoes if s.id == "camara-83357")
    assert dict(ccjc.fora) == {"requerimento": 1, "redacao_final": 17, "outorga": 34} and ccjc.total_na_fonte == 77
    vazia = next(s for s in agenda.sessoes if s.id == "camara-83412")
    assert vazia.pauta == "nao_publicada" and not vazia.itens and not vazia.fora
    prazos = [s for s in agenda.sessoes if s.tipo == "prazo"]
    assert [(s.data, s.hora, s.itens[0].identificador, s.itens[0].estagio) for s in prazos] == [
        ("2026-11-27", None, "MPV 1381/2026", "prazo_vence"), ("2026-11-28", None, "MPV 1382/2026", "prazo_vence")]
    camara, cn = agenda.estado_de("camara"), agenda.estado_de("congresso")
    assert camara.fila_de == "2026-11-23" and len(camara.fila) == 12 and camara.pauta_publicada_ate == "2026-11-23"
    assert cn.sessao_conjunta_convocada is False and dict(cn.contagens) == {"vetos_tramitando": 113, "vetos_trancando": 112}
    assert cn.proximo.data == "2026-12-07" and cn.proximo.itens[0].identificador == "MPV 1395/2026"


def test_o_exemplo_dos_tribunais_traz_a_semana_real() -> None:
    agenda = fx.tribunais()
    plenario = next(s for s in agenda.sessoes if s.id == fx.STF_PLENARIO)
    assert (plenario.hora, plenario.modalidade, len(plenario.itens)) == ("14:00", "presencial", 10)
    por_id = {i.identificador: i for i in plenario.itens}
    re, are = por_id["RE 1326541"], por_id["ARE 1502069"]
    assert (re.tema.numero, re.tema.tipo, re.alcance) == (1218, "repercussao_geral", "repercussao_geral")
    assert (re.estagio, re.estagio_detalhe) == ("retorno_de_vista", "Gilmar Mendes")
    assert (are.tema.numero, are.estagio, are.estagio_detalhe) == (1324, "retorno_de_vista", "Gilmar Mendes")
    assert por_id["ADI 7807"].estagios == ("referendo_de_cautelar", "destaque") and por_id["ADI 7807"].alcance == "controle_estadual"
    assert por_id["ADI 6002"].alcance == "controle_federal" and por_id["ADI 6002"].estagio_detalhe == "Flávio Dino"
    assert all(i.resultado is None for s in agenda.sessoes for i in s.itens)  # os tribunais não têm dado de resultado
    stj = agenda.sessoes_de("stj")
    assert len(stj) == 22 and all(s.hora is None and s.modalidade is None for s in stj)  # a fonte não informa nenhum dos dois
    semana = [s for s in stj if s.data <= "2026-11-27"]
    assert sum(s.total_na_fonte for s in semana) == 2219 and sum(len(s.itens) for s in semana) == 1
    corte = next(s for s in stj if s.id == "stj-ce-2026-11-25")
    assert dict(corte.fora) == {"segredo": 50, "recurso_interno": 60, "vice_presidencia": 215} and corte.itens[0].alcance == "afetacao"
    temas = {i.tema.numero for s in stj for i in s.itens if i.tema}
    assert {1254, 1448, 1404, 1466, 1256} <= temas
    stf, estado_stj = agenda.estado_de("stf"), agenda.estado_de("stj")
    assert [(v.de, v.ate, v.total) for v in stf.virtuais] == [
        ("2026-11-13", "2026-11-23", 627), ("2026-11-20", "2026-11-30", 662), ("2026-11-27", "2026-12-04", 546)]
    assert estado_stj.proximo.data == "2026-12-09" and estado_stj.proximo.itens[0].tema.numero == 1443
    assert agenda.data_dados_de("stj") == "2026-11-23" and agenda.refeita("stf") and agenda.fonte("stf_pauta").velho_dias == 4


# ── ida e volta determinística ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lado", sorted(LADOS))
def test_ida_e_volta_da_os_mesmos_bytes(lado: str) -> None:
    bruto = _bruto(lado)
    agenda = contrato.ler(LADOS[lado])
    canonico = json.dumps(bruto, ensure_ascii=False, separators=(",", ":")) + "\n"  # o arquivo, com as chaves na ordem em que está
    assert contrato.para_json(agenda) == canonico
    assert agenda.para_dict() == bruto
    assert Agenda.de_dict(agenda.para_dict()) == agenda
    assert contrato.para_json(contrato.ler_dados(json.loads(contrato.para_json(agenda)))) == canonico


@pytest.mark.parametrize("lado", sorted(LADOS))
def test_a_ordem_de_entrada_nao_muda_os_bytes(lado: str) -> None:
    """Mesmos dados, mesmos bytes: sessões, itens, fontes, estado e contagens embaralhados e
    chaves do dicionário em outra ordem dão o mesmo arquivo."""
    agenda = contrato.ler(LADOS[lado])
    sorteio = random.Random(7)

    def embaralhar(coisas: tuple) -> tuple:
        lista = list(coisas)
        sorteio.shuffle(lista)
        return tuple(lista)

    sessoes = tuple(replace(s, itens=embaralhar(s.itens), fora=embaralhar(s.fora)) for s in embaralhar(agenda.sessoes))
    estado = tuple(replace(e, fila=embaralhar(e.fila), virtuais=embaralhar(e.virtuais), contagens=embaralhar(e.contagens))
                   for e in embaralhar(agenda.estado))
    outra = replace(agenda, sessoes=sessoes, fontes=embaralhar(agenda.fontes), estado=estado)
    assert outra == agenda and contrato.para_json(outra) == contrato.para_json(agenda)

    def virar(valor: Any) -> Any:
        if isinstance(valor, dict):
            return {k: virar(valor[k]) for k in reversed(list(valor))}
        return [virar(v) for v in valor] if isinstance(valor, list) else valor

    do_avesso = contrato.ler_dados(virar(_bruto(lado)))
    assert do_avesso == agenda and contrato.para_json(do_avesso) == contrato.para_json(agenda)


def test_objetos_normalizam_o_que_recebem() -> None:
    item = ItemAgenda(identificador="ADI 7807", tipo="ADI", alcance="controle_estadual", estagio="referendo_de_cautelar",
                      link="https://portal.stf.jus.br/processos/listarProcessos.asp?classe=ADI&numeroProcesso=7807",
                      outros_estagios=["pautado", "destaque", "destaque", "referendo_de_cautelar"], relevancia=62.0)
    assert item.outros_estagios == ("destaque", "pautado") and item.estagios == ("referendo_de_cautelar", "destaque", "pautado")
    assert item.relevancia == 62 and isinstance(item.relevancia, int)
    assert replace(item, relevancia=61.26).relevancia == 61.3
    segundo = replace(item, identificador="ADI 7906", ordem=2)
    primeiro = replace(item, identificador="ADI 7869", ordem=1)
    sem_ordem = replace(item, identificador="ADI 7643")
    sessao = SessaoAgenda(id="stf-plenario-2026-11-25", instituicao="stf", orgao="Plenário", foro="plenario", tipo="julgamento", data="2026-11-25",
                          situacao="agendada", link="https://portal.stf.jus.br/pauta/", itens=[sem_ordem, segundo, primeiro],
                          fora={"outros": 2, "segredo": 1, "recurso_interno": 0})
    assert [i.identificador for i in sessao.itens] == ["ADI 7869", "ADI 7906", "ADI 7643"]  # a ordem da pauta; sem ordem depois
    assert sessao.fora == (("segredo", 1), ("outros", 2)) and sessao.fora_total == 3 and sessao.total_na_fonte == 6
    assert list(sessao.para_dict()) == list(contrato._CHAVES_SESSAO) and list(item.para_dict()) == list(contrato._CHAVES_ITEM)
    agenda = Agenda(lado="tribunais", gerado_em="2026-11-28T12:00:00Z", hoje="2026-11-28")
    assert agenda.janela == ("2026-11-23", "2026-12-06") and list(agenda.para_dict()) == list(contrato._CHAVES_AGENDA)


# ── segredo de justiça ───────────────────────────────────────────────────────────────────────


def test_item_em_segredo_nunca_e_guardado_so_contado() -> None:
    publico = ItemAgenda(identificador="REsp 2034211", tipo="REsp", alcance="repetitivo", estagio="pautado",
                         link="https://processo.stj.jus.br/processo/pesquisa/?termo=1")
    secreto = replace(publico, identificador="REsp 999", segredo=True, ementa="não pode sair")
    sessao = SessaoAgenda(id="stj-ce-2026-12-02", instituicao="stj", orgao="Corte Especial", foro="corte_especial", tipo="julgamento",
                          data="2026-12-02", situacao="agendada", link="https://dadosabertos.web.stj.jus.br/dataset/pautas-futuras",
                          itens=(secreto, publico, secreto), fora={"segredo": 20})
    assert [i.identificador for i in sessao.itens] == ["REsp 2034211"]
    assert dict(sessao.fora) == {"segredo": 22}
    assert "não pode sair" not in json.dumps(sessao.para_dict(), ensure_ascii=False)
    assert SessaoAgenda.de_dict(sessao.para_dict()) == sessao
    estado = EstadoInstituicao(instituicao="stj", fila=(secreto, publico), proximo=sessao)
    assert [i.identificador for i in estado.fila] == ["REsp 2034211"]


def test_a_validacao_recusa_item_marcado_em_segredo(tribunais: dict[str, Any]) -> None:
    _sessao(tribunais, fx.STF_PLENARIO)["itens"][0]["segredo"] = True
    erros = contrato.validar(tribunais)
    assert len(erros) == 1 and "segredo de justiça" in erros[0] and "sessoes[" in erros[0]
    assert contrato.ler_dados(tribunais) is None


# ── validação: cada tipo de erro ─────────────────────────────────────────────────────────────


def _mudar(dados: dict[str, Any], caminho: str, valor: Any) -> None:
    """``"sessoes.0.itens.0.alcance"`` → troca o valor (``...`` apaga a chave)."""
    *partes, ultima = caminho.split(".")
    alvo: Any = dados
    for parte in partes:
        alvo = alvo[int(parte)] if isinstance(alvo, list) else alvo[parte]
    if valor is ...:
        del alvo[ultima]
    elif isinstance(alvo, list):
        alvo[int(ultima)] = valor
    else:
        alvo[ultima] = valor


# No exemplo do Congresso: sessoes.0 é o Plenário da Câmara de 23/11 e o item 0 é o PLP 299/2026.
ERROS_DO_CONGRESSO = [
    ("versao", 2, "versao: esperada 1"),
    ("versao", True, "versao: esperada 1"),
    ("versao", ..., "falta a chave versao"),
    ("lado", "executivo", "lado: 'executivo' fora do vocabulário"),
    ("gerado_em", "24/11/2026 01:49", "gerado_em"),
    ("hoje", "2026-13-40", "hoje: esperada uma data"),
    ("janela", {"de": "2026-11-23", "ate": "2026-11-27"}, "janela: esperada de 2026-11-23 a 2026-12-06"),
    ("janela.de", "2026-11-24", "janela: esperada"),
    ("fontes", {}, "fontes: esperado um objeto com ao menos uma fonte"),
    ("fontes.camara_eventos.instituicao", "stf", "stf não é do lado congresso"),
    ("fontes.camara_eventos.ok", "sim", "ok: esperado verdadeiro ou falso"),
    ("fontes.camara_eventos.data_dados", None, "fonte ok precisa de data_dados"),
    ("fontes.camara_eventos.ok", False, "fonte que falhou precisa dizer o que houve"),
    ("fontes.camara_eventos.url", "http://dadosabertos.camara.leg.br/api/v2/eventos", "link oficial obrigatório"),
    ("fontes.camara_eventos.velho_dias", 0, "velho_dias"),
    ("fontes.camara_eventos.resumo", "texto novo", "chave desconhecida resumo"),
    ("sessoes", {}, "sessoes: esperada uma lista"),
    ("sessoes.0", [], "sessoes[0]: esperado um objeto, veio list"),
    ("sessoes.0.orgao", ..., "falta a chave orgao"),
    ("sessoes.0.por_que_importa", "texto de IA", "chave desconhecida por_que_importa"),
    ("sessoes.0.id", "Câmara 83419", "id: esperado um id estável"),
    ("sessoes.0.instituicao", "stj", "stj não é do lado congresso"),
    ("sessoes.0.instituicao", "assembleia", "fora do vocabulário"),
    ("sessoes.0.foro", "corte_especial", "corte_especial não existe em camara"),
    ("sessoes.0.foro", "prazo", "anda junto do foro"),
    ("sessoes.0.tipo", "solene", "tipo: 'solene' fora do vocabulário"),
    ("sessoes.0.data", "2026-12-07", "fora da janela"),
    ("sessoes.0.data", "23/11/2026", "data: esperada uma data"),
    ("sessoes.0.hora", "18h", "hora: esperado HH:MM"),
    ("sessoes.0.hora", "24:00", "hora: esperado HH:MM"),
    ("sessoes.0.situacao", "votada", "situacao: 'votada' fora do vocabulário"),
    ("sessoes.0.modalidade", "híbrida", "modalidade"),
    ("sessoes.0.pauta", "nao_publicada", "nao_publicada com itens ou contagens"),
    ("sessoes.0.pauta_confirmada", "Sim", "pauta_confirmada"),
    ("sessoes.0.como_pautado_em", "2026-11-30", "como_pautado_em: data no futuro"),
    ("sessoes.0.link", "https://www.youtube.com/watch?v=abc", "link oficial obrigatório"),
    ("sessoes.0.link", None, "link oficial obrigatório"),
    ("sessoes.0.fora", {"motivo_novo": 3}, "fora do vocabulário"),
    ("sessoes.0.fora", {"requerimento": 0}, "inteiro a partir de 1"),
    ("sessoes.0.itens", None, "itens: esperada uma lista"),
    ("sessoes.0.itens.0.identificador", "PL 299/2026", "não começa pelo tipo"),
    ("sessoes.0.itens.0.identificador", "ajuste fiscal", "esperado sigla e número"),
    ("sessoes.0.itens.0.tipo", "PLP 299", "tipo: esperada a sigla"),
    ("sessoes.0.itens.0.alcance", "midia", "alcance: 'midia' fora do vocabulário"),
    ("sessoes.0.itens.0.estagio", "vai_votar", "estagio: 'vai_votar' fora do vocabulário"),
    ("sessoes.0.itens.0.outros_estagios", ["em_votacao"], "estágio repetido"),
    ("sessoes.0.itens.0.outros_estagios", ["urgencia", "urgencia"], "estágio repetido"),
    ("sessoes.0.itens.0.outros_estagios", "urgencia", "outros_estagios: esperada uma lista"),
    ("sessoes.0.itens.0.apelido", "", "apelido: esperado um texto não vazio"),
    ("sessoes.0.itens.0.ementa", "Altera a Lei Complementar nº\u00a0200.", "espaço especial"),
    ("sessoes.0.itens.0.ementa", "Altera a Lei\nComplementar.", "espaço especial"),
    ("sessoes.0.itens.0.ementa", "x" * 8001, "máximo 8000"),
    ("sessoes.0.itens.0.area", "Economia e Finanças", "area: esperado o id da área"),
    ("sessoes.0.itens.0.tema", {"numero": 0, "titulo": "Título", "tipo": "repetitivo"}, "tema.numero"),
    ("sessoes.0.itens.0.tema", {"numero": 5, "titulo": "Título", "tipo": "sumula"}, "tema.tipo"),
    ("sessoes.0.itens.0.resultado", {"palavra": "aprovadissimo", "texto": None}, "resultado.palavra"),
    ("sessoes.0.itens.0.resultado", {"palavra": "rejeitado", "texto": "Rejeitado o Requerimento. Sim: 127; Não: 347; Abstenção: 7; Total: 481."},
     "traz placar"),
    ("sessoes.0.itens.0.resultado", {"palavra": "aprovado", "texto": "x" * 501}, "máximo 500"),
    ("sessoes.0.itens.0.resultado", {"palavra": "aprovado"}, "falta a chave texto"),
    ("sessoes.0.itens.0.relator", {"nome": "Mauro Benevides Filho", "partido": "UNIÃO", "uf": "Ceará"}, "relator.uf"),
    ("sessoes.0.itens.0.relator", {"nome": "", "partido": None, "uf": None}, "relator.nome"),
    ("sessoes.0.itens.0.ordem", 0, "ordem"),
    ("sessoes.0.itens.0.ordem", "2", "ordem"),
    ("sessoes.0.itens.0.link", "https://exemplo.com/plp-299", "link oficial obrigatório"),
    ("sessoes.0.itens.0.link", ..., "falta a chave link"),
    ("sessoes.0.itens.0.partes", [{"polo": "ativo", "tipo": "pf", "nome": "Fulano de Tal"}], "pessoa física nunca"),
    ("sessoes.0.itens.0.partes", [{"polo": "réu", "tipo": "ente", "nome": "União"}], "partes[0].polo"),
    ("sessoes.0.itens.0.ficha", "PLP 299/2026", "ficha: esperado o id da ficha"),
    ("sessoes.0.itens.0.relevancia", 120, "nota de 0 a 100"),
    ("sessoes.0.itens.0.relevancia", True, "nota de 0 a 100"),
    ("sessoes.0.itens.0.ficha", None, "nota sem ficha"),
    ("sessoes.0.itens.0.segredo", ..., "falta a chave segredo"),
    ("sessoes.0.itens.0.resumo_ia", "Este projeto muda a regra fiscal.", "chave desconhecida resumo_ia"),
    ("estado", [], "estado: esperado um objeto por instituição"),
    ("estado.camara.fila_de", None, "a fila precisa do dia da sessão"),
    ("estado.camara.sessao_conjunta_convocada", False, "só o Congresso tem sessão conjunta"),
    ("estado.camara.recesso", {"nome": "recesso de fim de ano", "de": "2027-02-01", "ate": "2026-12-23"}, "começa depois de terminar"),
    ("estado.camara.contagens", {"audiencias": -1}, "contagens.audiencias"),
    ("estado.camara.contagens", {"Audiências Públicas": 3}, "nome de contagem inválido"),
    ("estado.camara.virtuais", [{"de": "2026-11-20", "ate": "2026-11-30", "plenario": 1, "turma1": 1, "turma2": 1}], "só o STF publica"),
    ("estado.congresso.proximo.data", "2026-12-01", "cai dentro da janela"),
    ("estado.congresso.proximo.instituicao", "senado", "de outra instituição"),
    ("estado.congresso.frase", "Congresso em recesso", "chave desconhecida frase"),
]


@pytest.mark.parametrize(("caminho", "valor", "esperado"), ERROS_DO_CONGRESSO, ids=[f"{c}={str(v)[:24]}" for c, v, _ in ERROS_DO_CONGRESSO])
def test_cada_erro_do_lado_do_congresso_e_apontado(congresso: dict[str, Any], caminho: str, valor: Any, esperado: str) -> None:
    assert congresso["sessoes"][0]["id"] == fx.CAMARA_PLENARIO and congresso["sessoes"][0]["itens"][0]["identificador"] == "PLP 299/2026"
    _mudar(congresso, caminho, valor)
    erros = contrato.validar(congresso)
    assert any(esperado in erro for erro in erros), erros
    assert contrato.ler_dados(congresso) is None  # formato inesperado vale como fonte ausente


ERROS_DOS_TRIBUNAIS = [
    ("hora", "14:00", "o STJ não informa hora nem modalidade"),
    ("modalidade", "virtual", "o STJ não informa hora nem modalidade"),
    ("foro", "plenario", "plenario não existe em stj"),
    ("instituicao", "camara", "camara não é do lado tribunais"),
]


@pytest.mark.parametrize(("campo", "valor", "esperado"), ERROS_DOS_TRIBUNAIS)
def test_cada_erro_de_sessao_do_stj_e_apontado(tribunais: dict[str, Any], campo: str, valor: Any, esperado: str) -> None:
    _sessao(tribunais, "stj-ce-2026-11-25")[campo] = valor
    assert any(esperado in erro for erro in contrato.validar(tribunais))


def test_os_tribunais_nao_tem_resultado_e_o_estado_e_conferido(tribunais: dict[str, Any]) -> None:
    com_resultado = copy.deepcopy(tribunais)
    _item(com_resultado, fx.STF_PLENARIO, "RE 1326541")["resultado"] = {"palavra": "aprovado", "texto": None}
    assert any("os tribunais não têm dado de resultado" in e for e in contrato.validar(com_resultado))

    sem_virtuais_no_stj = copy.deepcopy(tribunais)
    sem_virtuais_no_stj["estado"]["stj"]["virtuais"] = [{"de": "2026-11-20", "ate": "2026-11-30", "plenario": 1, "turma1": 2, "turma2": 3}]
    assert any("só o STF publica" in e for e in contrato.validar(sem_virtuais_no_stj))

    contagem_ruim = copy.deepcopy(tribunais)
    contagem_ruim["estado"]["stf"]["virtuais"][0]["plenario"] = -1
    assert any("virtuais[0].plenario" in e for e in contrato.validar(contagem_ruim))

    de_outro_lado = copy.deepcopy(tribunais)
    de_outro_lado["estado"]["senado"] = copy.deepcopy(de_outro_lado["estado"]["stf"])
    de_outro_lado["estado"]["senado"]["virtuais"] = []
    assert any("senado não é do lado tribunais" in e for e in contrato.validar(de_outro_lado))

    desconhecida = copy.deepcopy(tribunais)
    desconhecida["estado"]["tse"] = {}
    assert any("estado.tse: instituição desconhecida" in e for e in contrato.validar(desconhecida))


def test_sessao_que_nao_esta_encerrada_nao_tem_resultado(congresso: dict[str, Any]) -> None:
    """A sessão de 23/11 estava em andamento na coleta: o PLP 299/2026 aparece "em votação", sem
    resultado, mesmo com o registro já dizendo "Aprovado o Projeto"."""
    plenario = congresso["sessoes"][0]
    item = plenario["itens"][0]
    assert (plenario["situacao"], item["estagio"], item["resultado"]) == ("em_andamento", "em_votacao", None)
    item["resultado"] = {"palavra": "aprovado_ressalvados_destaques", "texto": fx.RESULTADO_PLP_299}
    assert any("sessão que não está encerrada não tem resultado" in e for e in contrato.validar(congresso))
    item["resultado"] = {"palavra": "retirado_de_pauta", "texto": None}  # sair da pauta dá para saber antes do fim
    assert contrato.validar(congresso) == []
    item["resultado"] = {"palavra": "aprovado_ressalvados_destaques", "texto": fx.RESULTADO_PLP_299}
    plenario["situacao"] = "encerrada"  # encerrada: o resultado entra, e "em votação" deixa de valer
    assert contrato.validar(congresso) == ["sessoes[0].itens[0].estagio: em_votacao só vale com a sessão em andamento"]
    item["estagio"], item["outros_estagios"] = "urgencia", []
    assert contrato.validar(congresso) == []
    assert contrato.validar(fx.camara_encerrada().para_dict()) == []

    na_fila = copy.deepcopy(congresso)
    na_fila["estado"]["camara"]["fila"][0]["resultado"] = {"palavra": "aprovado", "texto": None}
    assert any("item da fila ainda não foi deliberado" in e for e in contrato.validar(na_fila))


def test_id_repetido_e_item_repetido_sao_erro(congresso: dict[str, Any]) -> None:
    repetida = copy.deepcopy(congresso)
    repetida["sessoes"].append(copy.deepcopy(repetida["sessoes"][0]))
    assert any("id repetido (camara-83419)" in e for e in contrato.validar(repetida))

    proximo_repetido = copy.deepcopy(congresso)
    proximo_repetido["estado"]["congresso"]["proximo"]["id"] = "camara-83419"
    assert any("id repetido" in e for e in contrato.validar(proximo_repetido))

    linha_dupla = copy.deepcopy(congresso)
    linha_dupla["sessoes"][0]["itens"].append(copy.deepcopy(linha_dupla["sessoes"][0]["itens"][0]))
    assert any("PLP 299/2026 repetido" in e for e in contrato.validar(linha_dupla))


def test_o_placar_do_requerimento_nunca_passa_como_resultado(congresso: dict[str, Any]) -> None:
    """O registro real da sessão de 23/11 traz "Sim: 127; Não: 347" (um requerimento de adiamento
    rejeitado) antes do placar do projeto. O contrato só aceita a frase do resultado, sem placar."""
    item = congresso["sessoes"][0]["itens"][0]
    item["resultado"] = {"palavra": "aprovado_ressalvados_destaques", "texto": fx.RESULTADO_PLP_299}
    item["estagio"], item["outros_estagios"] = "urgencia", []
    congresso["sessoes"][0]["situacao"] = "encerrada"
    assert contrato.validar(congresso) == []
    for placar in ("Sim: 127; Não: 347", "Sim: 353; Não: 128; Abstenção: 5; Total: 486.", "sim:353", "Total: 481"):
        item["resultado"]["texto"] = f"{fx.RESULTADO_PLP_299} {placar}"
        assert any("traz placar" in e for e in contrato.validar(congresso)), placar
    encerrada = fx.camara_encerrada()
    plp = next(i for s in encerrada.sessoes if s.id == fx.CAMARA_PLENARIO for i in s.itens if i.identificador == "PLP 299/2026")
    gravado = contrato.para_json(encerrada)
    assert plp.resultado.palavra == "aprovado_ressalvados_destaques" and plp.resultado.texto == fx.RESULTADO_PLP_299
    assert contrato._PLACAR.search(gravado) is None and "Sim: 127" not in gravado and "Não: 347" not in gravado


@pytest.mark.parametrize("coisa", [
    None, [], "agenda", 3, True, {}, {"versao": 1},
    {"versao": 1, "sessoes": [None, 3, "x", {"itens": [None, {"partes": "x"}]}]},
    {"versao": 1, "fontes": {"x": None}, "estado": {"stf": {"proximo": [], "virtuais": [None], "fila": [1]}}, "janela": 5},
])
def test_validar_nunca_levanta_excecao(coisa: Any) -> None:
    erros = contrato.validar(coisa)
    assert erros and all(isinstance(e, str) for e in erros)
    assert contrato.ler_dados(coisa) is None


# ── leitor tolerante ─────────────────────────────────────────────────────────────────────────


def test_ler_devolve_none_quando_o_arquivo_falta_ou_esta_ilegivel(tmp_path: Path) -> None:
    assert contrato.ler(tmp_path / "agenda.json") is None  # ausente
    assert contrato.ler(tmp_path) is None  # uma pasta no lugar do arquivo
    quebrado = tmp_path / "quebrado.json"
    quebrado.write_text('{"versao": 1, "lado": "congr', encoding="utf-8")
    assert contrato.ler(quebrado) is None
    binario = tmp_path / "binario.json"
    binario.write_bytes(b"\xff\xfe\x00agenda")
    assert contrato.ler(binario) is None
    vazio = tmp_path / "vazio.json"
    vazio.write_text("", encoding="utf-8")
    assert contrato.ler(vazio) is None


@pytest.mark.parametrize("lado", sorted(LADOS))
def test_ler_devolve_none_com_formato_inesperado(tmp_path: Path, lado: str) -> None:
    bruto = _bruto(lado)
    caminho = tmp_path / "agenda.json"

    def gravar(dados: Any) -> Path:
        caminho.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        return caminho

    assert contrato.ler(gravar(bruto)) is not None  # o arquivo bom, para comparar
    assert contrato.ler(gravar([bruto])) is None  # lista no lugar de dicionário
    assert contrato.ler(gravar("agenda")) is None
    assert contrato.ler(gravar(None)) is None
    sem_chave = {k: v for k, v in bruto.items() if k != "sessoes"}
    assert contrato.ler(gravar(sem_chave)) is None  # chave faltando
    assert contrato.ler(gravar({**bruto, "versao": contrato.VERSAO + 1})) is None  # versão maior
    assert contrato.ler(gravar({**bruto, "versao": 0})) is None
    assert contrato.ler(gravar({**bruto, "versao": "1"})) is None
    assert contrato.ler(gravar({k: v for k, v in bruto.items() if k != "versao"})) is None
    assert contrato.ler(gravar({**bruto, "sessoes": {"lista": bruto["sessoes"]}})) is None
    outro = "tribunais" if lado == "congresso" else "congresso"
    assert contrato.ler(gravar(bruto), lado=outro) is None  # o arquivo de um lado na pasta do outro
    assert contrato.ler(gravar(bruto), lado=lado) is not None


def test_ler_nunca_levanta_excecao_nem_com_defeito_interno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def explode(_dados: Any) -> list[str]:
        raise RuntimeError("defeito")

    monkeypatch.setattr(contrato, "validar", explode)
    with caplog.at_level("WARNING"):
        assert contrato.ler(fx.CONGRESSO) is None
    assert "a fonte fica ausente" in caplog.text


def test_cada_lado_mora_na_sua_pasta(tmp_path: Path) -> None:
    assert contrato.caminho_do_lado(tmp_path, "congresso") == tmp_path / "congresso" / "agenda.json"
    assert contrato.caminho_do_lado(tmp_path, "tribunais") == tmp_path / "tribunais" / "agenda.json"
    with pytest.raises(ValueError):
        contrato.caminho_do_lado(tmp_path, "executivo")
    assert contrato.ler_lado(tmp_path, "congresso") is None and contrato.ler_lado(tmp_path, "tribunais") is None  # sem arquivos
    assert contrato.ler_lado(tmp_path, "executivo") is None and contrato.ler_lado(None, "congresso") is None  # nunca levanta
    for lado, origem in LADOS.items():
        destino = contrato.caminho_do_lado(tmp_path, lado)
        destino.parent.mkdir(parents=True)
        destino.write_text(contrato.para_json(contrato.ler(origem)), encoding="utf-8")
    assert contrato.ler_lado(tmp_path, "congresso") == fx.congresso() and contrato.ler_lado(tmp_path, "tribunais") == fx.tribunais()
    trocado = contrato.caminho_do_lado(tmp_path, "tribunais")
    trocado.write_text(contrato.para_json(fx.congresso()), encoding="utf-8")  # o arquivo de um lado na pasta do outro
    assert contrato.ler_lado(tmp_path, "tribunais") is None


def test_para_json_recusa_agenda_fora_do_contrato() -> None:
    agenda = fx.tribunais()
    quebrada = replace(agenda, sessoes=tuple(replace(s, hora="14:00") if s.instituicao == "stj" else s for s in agenda.sessoes))
    with pytest.raises(contrato.ErroDeContrato) as erro:
        contrato.para_json(quebrada)
    assert len(erro.value.erros) == 22 and "o STJ não informa hora" in str(erro.value) and "e mais 19" in str(erro.value)
    assert isinstance(erro.value, ValueError)


# ── texto e link ─────────────────────────────────────────────────────────────────────────────


def test_limpar_texto_tira_espacos_especiais_e_o_numero_quebrado() -> None:
    limpo = contrato.limpar_texto("Altera a Lei Complementar nº?200, de 30 de agosto de 2023")
    assert limpo == "Altera a Lei Complementar nº 200, de 30 de agosto de 2023"
    assert contrato.limpar_texto("Leis nºs?8.212 e 8.213") == "Leis nºs 8.212 e 8.213"
    assert contrato.limpar_texto("  nº\u00a0200,\r\n de\t2023\u200b ") == "nº 200, de 2023"
    assert contrato.limpar_texto("igual ou inferior a                     R$ 130.000,00") == "igual ou inferior a R$ 130.000,00"
    assert contrato.limpar_texto("O que muda? Nada.") == "O que muda? Nada."  # interrogação de verdade fica
    assert contrato.limpar_texto("   ") is None and contrato.limpar_texto("") is None and contrato.limpar_texto(None) is None
    assert contrato.limpar_texto(1218) == "1218"


def test_identificador_e_id_de_sessao_tem_formato_unico() -> None:
    assert contrato.identificador_de("PL", "06461", 2019) == "PL 6461/2019"  # o Senado manda o número com zeros
    assert contrato.identificador_de("PLP", 299, "2026") == "PLP 299/2026"
    assert contrato.identificador_de("RE", 1326541) == "RE 1326541" and contrato.identificador_de(" REsp ", "2034211", "") == "REsp 2034211"
    assert contrato.identificador_de("MPV", "2161-35", 2001) == "MPV 2161-35/2001"
    assert contrato.id_de_sessao("camara", 83419) == "camara-83419"
    assert contrato.id_de_sessao("stj", "CE", "2026-11-25") == "stj-ce-2026-11-25"
    assert contrato.id_de_sessao("congresso", "prazo", "MPV 1381/2026") == "congresso-prazo-mpv-1381-2026"
    for agenda in (fx.congresso(), fx.tribunais()):  # os ids e os identificadores dos exemplos seguem o formato
        for sessao in agenda.sessoes:
            assert contrato.id_de_sessao(sessao.id) == sessao.id and sessao.id.startswith(sessao.instituicao + "-")
            for item in sessao.itens:
                numero, _, ano = item.identificador.split(" ", 1)[1].partition("/")
                assert contrato.identificador_de(item.tipo, numero, ano) == item.identificador


@pytest.mark.parametrize(("url", "oficial"), [
    ("https://www.camara.leg.br/evento-legislativo/83419", True),
    ("https://www25.senado.leg.br/web/atividade/materias/-/materia/174061", True),
    ("https://portal.stf.jus.br/processos/listarProcessos.asp?classe=RE&numeroProcesso=1326541", True),
    ("https://processo.stj.jus.br/processo/pesquisa/?termo=202500817086", True),
    ("https://legis.senado.gov.br/sdleg-getter/documento/abc", True),
    ("http://www.camara.leg.br/evento-legislativo/83419", False),  # sem https
    ("https://www.youtube.com/watch?v=JnMcX2L3MDk", False),
    ("https://camara.leg.br.exemplo.com/pauta", False),
    ("https://exemploleg.br/pauta", False),
    ("https://www.camara.leg.br/evento legislativo", False),
    ("www.camara.leg.br", False),
    ("", False),
    (None, False),
    (83419, False),
])
def test_link_oficial(url: Any, oficial: bool) -> None:
    assert contrato.link_oficial(url) is oficial


# ── a janela ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("dia", "segunda"), [
    ("2026-11-23", "2026-11-23"), ("2026-11-24", "2026-11-23"), ("2026-11-27", "2026-11-23"),
    ("2026-11-28", "2026-11-23"), ("2026-11-29", "2026-11-23"),  # sábado e domingo ainda são da semana corrente
    ("2026-11-30", "2026-11-30"), ("2026-12-01", "2026-11-30"), ("2027-01-01", "2026-12-28"),
])
def test_segunda_da_semana_e_janela_de_coleta(dia: str, segunda: str) -> None:
    assert contrato.segunda_da_semana(dia) == segunda
    de, ate = contrato.janela_de_coleta(dia)
    assert de == segunda and date.fromisoformat(de).weekday() == 0
    assert (date.fromisoformat(ate) - date.fromisoformat(de)).days == 13


@pytest.mark.parametrize(("dia", "janela"), [
    ("2026-11-23", ("2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27")),  # segunda: a semana
    ("2026-11-24", ("2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27", "2026-11-30")),  # terça: pula o fim de semana
    ("2026-11-27", ("2026-11-27", "2026-11-30", "2026-12-01", "2026-12-02", "2026-12-03")),  # sexta, com a virada do mês
    ("2026-11-28", ("2026-11-30", "2026-12-01", "2026-12-02", "2026-12-03", "2026-12-04")),  # sábado começa na segunda seguinte
    ("2026-11-29", ("2026-11-30", "2026-12-01", "2026-12-02", "2026-12-03", "2026-12-04")),  # domingo também
    ("2026-12-30", ("2026-12-30", "2026-12-31", "2027-01-01", "2027-01-04", "2027-01-05")),  # virada do ano (sem calendário de feriados)
])
def test_janela_da_edicao(dia: str, janela: tuple[str, ...]) -> None:
    assert contrato.janela_da_edicao(dia) == janela
    assert all(contrato.eh_dia_util(d) for d in janela)


def test_a_janela_da_edicao_cabe_sempre_na_de_coleta() -> None:
    dia = date(2026, 11, 1)
    for _ in range(120):
        hoje = dia.isoformat()
        de, ate = contrato.janela_de_coleta(hoje)
        mostrados = contrato.janela_da_edicao(hoje)
        assert len(mostrados) == contrato.DIAS_DA_EDICAO and de <= mostrados[0] and mostrados[-1] <= ate
        assert mostrados[0] >= hoje and list(mostrados) == sorted(mostrados)
        dia += timedelta(days=1)


@pytest.mark.parametrize(("dia", "passados"), [
    ("2026-11-23", ()),
    ("2026-11-25", ("2026-11-23", "2026-11-24")),
    ("2026-11-27", ("2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26")),
    ("2026-11-28", fx.SEMANA),
    ("2026-11-29", fx.SEMANA),
    ("2026-11-30", ()),  # virou a semana
    ("2026-12-02", ("2026-11-30", "2026-12-01")),  # virada do mês
])
def test_dias_passados_da_semana(dia: str, passados: tuple[str, ...]) -> None:
    assert contrato.dias_passados_da_semana(dia) == passados


@pytest.mark.parametrize(("de", "ate", "uteis"), [
    ("2026-11-23", "2026-11-23", 0), ("2026-11-23", "2026-11-24", 1), ("2026-11-20", "2026-11-23", 1),  # sexta → segunda
    ("2026-11-20", "2026-11-24", 2), ("2026-11-20", "2026-11-22", 0), ("2026-11-23", "2026-11-30", 5),
    ("2026-11-27", "2026-12-01", 2), ("2026-11-24", "2026-11-23", 0),
])
def test_dias_uteis_entre(de: str, ate: str, uteis: int) -> None:
    assert contrato.dias_uteis_entre(de, ate) == uteis


# ── mesclagem com o arquivo anterior ─────────────────────────────────────────────────────────


def _falhou(fonte: contrato.FonteAgenda, aviso: str = "HTTP 504 na coleta") -> contrato.FonteAgenda:
    return replace(fonte, ok=False, aviso=aviso, data_dados=None, coletado_em=None)


def _no_dia(agenda: Agenda, hoje: str, **mudancas: Any) -> Agenda:
    """A coleta do dia ``hoje``: a janela é refeita e as fontes que estão ``ok`` ganham o dia."""
    fontes = mudancas.pop("fontes", None) or tuple(replace(f, data_dados=hoje, coletado_em=f"{hoje}T12:00:00Z") if f.ok else f for f in agenda.fontes)
    return replace(agenda, hoje=hoje, janela=None, gerado_em=f"{hoje}T12:00:00Z", fontes=fontes, **mudancas)


def _valida(agenda: Agenda) -> Agenda:
    assert contrato.validar(agenda.para_dict()) == []
    return agenda


def test_mesclar_sem_arquivo_anterior_devolve_a_coleta() -> None:
    novo = fx.congresso()
    assert contrato.mesclar_com_anterior(novo, None, fx.HOJE) == novo
    assert contrato.mesclar_com_anterior(novo, fx.tribunais(), fx.HOJE) == novo  # o arquivo do outro lado não entra


def test_fonte_que_falhou_mantem_as_sessoes_da_instituicao_e_nunca_vira_zero() -> None:
    anterior = fx.congresso()
    hoje = "2026-11-24"
    fontes = tuple(_falhou(f) if f.instituicao == "camara" else replace(f, data_dados=hoje, coletado_em=f"{hoje}T12:00:00Z") for f in anterior.fontes)
    novo = _no_dia(anterior, hoje, fontes=fontes, sessoes=tuple(s for s in anterior.sessoes if s.instituicao != "camara"),
                   estado=tuple(e for e in anterior.estado if e.instituicao != "camara"))
    assert not novo.sessoes_de("camara") and not novo.refeita("camara") and novo.refeita("senado")

    junto = _valida(contrato.mesclar_com_anterior(novo, anterior, hoje))
    da_camara = junto.sessoes_de("camara")
    assert [s.id for s in da_camara] == [s.id for s in anterior.sessoes_de("camara")] and len(da_camara) == 5  # nunca vira zero
    assert all(s.como_pautado_em == "2026-11-23" for s in da_camara)  # com a data delas
    assert [replace(s, como_pautado_em=None) for s in da_camara] == list(anterior.sessoes_de("camara"))  # itens e contagens intactos
    assert junto.estado_de("camara") == anterior.estado_de("camara")  # a fila também fica
    fonte = junto.fonte("camara_eventos")
    assert (fonte.ok, fonte.aviso, fonte.data_dados, fonte.coletado_em) == (False, "HTTP 504 na coleta", "2026-11-23", "2026-11-24T01:46:00Z")
    assert all(s.como_pautado_em is None for s in junto.sessoes if s.instituicao != "camara")  # o que foi coletado agora não ganha marca
    assert junto.data_dados_de("senado") == hoje and junto.hoje == hoje

    de_novo = _valida(contrato.mesclar_com_anterior(_no_dia(novo, "2026-11-25"), junto, "2026-11-25"))  # a fonte segue fora no dia seguinte
    assert [s.como_pautado_em for s in de_novo.sessoes_de("camara")] == ["2026-11-23"] * 5  # a marca é a do último dia bom
    assert de_novo.fonte("camara_eventos").data_dados == "2026-11-23"


def test_falha_parcial_junta_o_que_veio_com_o_que_ja_havia() -> None:
    anterior = fx.congresso()
    hoje = "2026-11-24"
    plenario = next(s for s in anterior.sessoes if s.id == fx.CAMARA_PLENARIO)
    encerrada = replace(plenario, situacao="encerrada", itens=tuple(contrato._fora_de_votacao(i) for i in plenario.itens))
    nova = replace(plenario, id="camara-90001", data="2026-11-24", hora="14:00", situacao="agendada", pauta="nao_publicada", itens=(), fora=())
    fontes = tuple(_falhou(f, "prazo de rede esgotado antes das comissões") if f.instituicao == "camara" else f for f in anterior.fontes)
    novo = _no_dia(anterior, hoje, fontes=fontes, sessoes=(*(s for s in anterior.sessoes if s.instituicao != "camara"), encerrada, nova))

    junto = _valida(contrato.mesclar_com_anterior(novo, anterior, hoje))
    por_id = {s.id: s for s in junto.sessoes_de("camara")}
    assert set(por_id) == {s.id for s in anterior.sessoes_de("camara")} | {"camara-90001"}
    assert por_id[fx.CAMARA_PLENARIO].situacao == "encerrada" and por_id[fx.CAMARA_PLENARIO].como_pautado_em is None  # o que veio agora vale
    assert por_id["camara-83357"].como_pautado_em == "2026-11-23" and len(por_id["camara-83357"].itens) == 25  # a CCJC não lida fica como estava


def test_pauta_nao_coletada_fica_com_a_pauta_do_arquivo_anterior() -> None:
    anterior = fx.congresso()
    hoje = "2026-11-24"
    ccjc = next(s for s in anterior.sessoes if s.id == "camara-83357")
    sem_pauta = replace(ccjc, situacao="em_andamento", pauta="nao_coletada", itens=(), fora=())
    novo = _no_dia(anterior, hoje, sessoes=tuple(sem_pauta if s.id == ccjc.id else s for s in anterior.sessoes))

    junto = _valida(contrato.mesclar_com_anterior(novo, anterior, hoje))
    mantida = next(s for s in junto.sessoes if s.id == ccjc.id)
    assert mantida.pauta == "publicada" and mantida.itens == ccjc.itens and mantida.fora == ccjc.fora
    assert mantida.situacao == "em_andamento" and mantida.como_pautado_em == "2026-11-23"
    sozinho = contrato.mesclar_com_anterior(novo, None, hoje)  # sem arquivo anterior, fica dito que não foi lida
    assert next(s for s in sozinho.sessoes if s.id == ccjc.id).pauta == "nao_coletada"

    # A sessão que estava em andamento e agora consta encerrada, sem a pauta relida: o item deixa de
    # estar "em votação" (volta à urgência que tinha) e segue sem resultado, porque ele não foi lido.
    plenario = next(s for s in anterior.sessoes if s.id == fx.CAMARA_PLENARIO)
    assert plenario.itens[0].estagios == ("em_votacao", "urgencia")
    fechou = replace(plenario, situacao="encerrada", pauta="nao_coletada", itens=(), fora=())
    novo = _no_dia(anterior, hoje, sessoes=tuple(fechou if s.id == plenario.id else s for s in anterior.sessoes))
    junto = _valida(contrato.mesclar_com_anterior(novo, anterior, hoje))
    mantida = next(s for s in junto.sessoes if s.id == plenario.id)
    assert mantida.situacao == "encerrada" and mantida.itens[0].identificador == "PLP 299/2026"
    assert mantida.itens[0].estagios == ("urgencia",) and mantida.itens[0].resultado is None
    assert mantida.itens[1:] == plenario.itens[1:] and mantida.fora == plenario.fora


def test_sessao_passada_da_semana_e_mantida_do_arquivo_anterior() -> None:
    """A pauta dos tribunais só lista o futuro: na quinta, a sessão de quarta some da fonte e
    continua no arquivo, "como pautado em 23/11", até a virada da semana."""
    anterior = fx.tribunais()
    quinta = "2026-11-26"
    novo = _no_dia(anterior, quinta, sessoes=tuple(s for s in anterior.sessoes if s.data >= quinta))
    assert fx.STF_PLENARIO not in {s.id for s in novo.sessoes} and novo.refeita("stf") and novo.refeita("stj")

    junto = _valida(contrato.mesclar_com_anterior(novo, anterior, quinta))
    plenario = next(s for s in junto.sessoes if s.id == fx.STF_PLENARIO)
    assert plenario.como_pautado_em == "2026-11-23" and len(plenario.itens) == 10
    assert replace(plenario, como_pautado_em=None) == next(s for s in anterior.sessoes if s.id == fx.STF_PLENARIO)
    passadas = [s for s in junto.sessoes if s.data < quinta]
    assert {s.id for s in passadas} == {s.id for s in anterior.sessoes if s.data < quinta} and len(passadas) == 5
    assert all(s.como_pautado_em == "2026-11-23" for s in passadas)
    assert all(s.como_pautado_em is None for s in junto.sessoes if s.data >= quinta)

    na_sexta = _no_dia(novo, "2026-11-27", sessoes=tuple(s for s in anterior.sessoes if s.data >= "2026-11-27"))
    sexta = _valida(contrato.mesclar_com_anterior(na_sexta, junto, "2026-11-27"))
    assert next(s for s in sexta.sessoes if s.id == fx.STF_PLENARIO).como_pautado_em == "2026-11-23"  # a marca não anda
    de_quinta = [s for s in sexta.sessoes if s.data == quinta]
    assert de_quinta and all(s.como_pautado_em == quinta for s in de_quinta)  # o que a fonte ainda trazia na quinta

    sem_acumular = contrato.mesclar_com_anterior(novo, anterior, quinta, acumular_passadas=False)
    assert not [s for s in sem_acumular.sessoes if s.data < quinta]


def test_sessao_futura_que_a_fonte_deixou_de_trazer_some() -> None:
    anterior = fx.tribunais()
    hoje = "2026-11-24"
    retirada = "stj-ce-2026-12-02"
    novo = _no_dia(anterior, hoje, sessoes=tuple(s for s in anterior.sessoes if s.id != retirada))
    junto = _valida(contrato.mesclar_com_anterior(novo, anterior, hoje))
    assert retirada not in {s.id for s in junto.sessoes}  # a fonte respondeu e não traz mais: saiu da pauta
    assert len(junto.sessoes) == len(anterior.sessoes) - 1


def test_a_poda_e_na_virada_da_semana() -> None:
    anterior = fx.tribunais()
    domingo, segunda = "2026-11-29", "2026-11-30"
    coleta_de_domingo = _no_dia(anterior, domingo, sessoes=tuple(s for s in anterior.sessoes if s.data >= domingo))
    no_domingo = _valida(contrato.mesclar_com_anterior(coleta_de_domingo, anterior, domingo))
    assert fx.STF_PLENARIO in {s.id for s in no_domingo.sessoes}  # domingo ainda é a semana corrente

    futuras = tuple(s for s in anterior.sessoes if s.data >= segunda)
    virou = _valida(contrato.mesclar_com_anterior(_no_dia(anterior, segunda, sessoes=futuras, estado=()), no_domingo, segunda))
    assert virou.janela == ("2026-11-30", "2026-12-13")
    assert min(s.data for s in virou.sessoes) == "2026-12-01" and {s.id for s in virou.sessoes} == {s.id for s in futuras}

    fora_do_ar = tuple(_falhou(f) for f in anterior.fontes)  # nem com a fonte fora do ar a semana velha sobrevive
    sem_fonte = _valida(contrato.mesclar_com_anterior(_no_dia(anterior, segunda, fontes=fora_do_ar, sessoes=(), estado=()), no_domingo, segunda))
    proximo_do_stj = anterior.estado_de("stj").proximo.id  # o evento de 9/12 passou a caber na janela e virou sessão
    assert {s.id for s in sem_fonte.sessoes} == {s.id for s in futuras} | {proximo_do_stj}  # só o que cabe na janela nova
    assert min(s.data for s in sem_fonte.sessoes) == "2026-12-01"
    assert all(s.como_pautado_em == domingo for s in sem_fonte.sessoes)  # o último dia em que a fonte as trouxe


def test_instituicao_que_a_rodada_nao_refez_fica_como_estava() -> None:
    """O fluxo Tribunais pode refazer só um tribunal: a parte do outro é mantida."""
    anterior = fx.tribunais()
    hoje = "2026-11-24"
    fontes_do_stj = tuple(replace(f, data_dados=hoje, coletado_em=f"{hoje}T12:00:00Z") for f in anterior.fontes if f.instituicao == "stj")
    so_stj = _no_dia(anterior, hoje, fontes=fontes_do_stj, sessoes=anterior.sessoes_de("stj"),
                     estado=tuple(e for e in anterior.estado if e.instituicao == "stj"))
    junto = _valida(contrato.mesclar_com_anterior(so_stj, anterior, hoje))
    assert junto.fonte("stf_pauta") == anterior.fonte("stf_pauta") and junto.data_dados_de("stf") == "2026-11-23"
    assert [s.id for s in junto.sessoes_de("stf")] == [s.id for s in anterior.sessoes_de("stf")]
    assert all(s.como_pautado_em == "2026-11-23" for s in junto.sessoes_de("stf"))
    assert junto.estado_de("stf") == anterior.estado_de("stf") and len(junto.estado_de("stf").virtuais) == 3
    assert all(s.como_pautado_em is None for s in junto.sessoes_de("stj")) and junto.data_dados_de("stj") == hoje


def test_o_proximo_evento_vira_sessao_quando_a_semana_vira_e_a_fonte_falha() -> None:
    anterior = fx.tribunais()
    proximo = anterior.estado_de("stj").proximo
    assert proximo.data == "2026-12-09"
    segunda = "2026-11-30"  # a janela passa a ir até 13/12: o evento de 9/12 agora cabe nela
    fora_do_ar = tuple(_falhou(f) if f.instituicao == "stj" else f for f in anterior.fontes)
    novo = _no_dia(anterior, segunda, fontes=fora_do_ar, sessoes=tuple(s for s in anterior.sessoes_de("stf") if s.data >= segunda),
                   estado=tuple(e for e in anterior.estado if e.instituicao == "stf"))
    junto = _valida(contrato.mesclar_com_anterior(novo, anterior, segunda))
    assert junto.estado_de("stj").proximo is None
    virou = next(s for s in junto.sessoes if s.id == proximo.id)
    assert virou.como_pautado_em == "2026-11-23" and virou.itens == proximo.itens


def test_conteudo_igual_ignora_os_relogios() -> None:
    agenda = fx.congresso()
    mais_tarde = replace(agenda, gerado_em="2026-11-24T05:00:00Z",
                         fontes=tuple(replace(f, coletado_em="2026-11-24T05:00:00Z") for f in agenda.fontes))
    assert contrato.conteudo_igual(agenda, mais_tarde) and contrato.para_json(agenda) != contrato.para_json(mais_tarde)
    outro_dia = replace(agenda, fontes=tuple(replace(f, data_dados="2026-11-24") for f in agenda.fontes))
    assert not contrato.conteudo_igual(agenda, outro_dia)
    menos = replace(agenda, sessoes=agenda.sessoes[1:])
    assert not contrato.conteudo_igual(agenda, menos)
    assert not contrato.conteudo_igual(agenda, None) and contrato.conteudo_igual(None, None)
