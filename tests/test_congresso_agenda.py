"""O coletor da agenda do Congresso (``qijournal/congresso/agenda.py``): o que está pautado na
Câmara, no Senado e no Congresso, gravado em ``data/congresso/agenda.json`` no formato de
``qijournal/agenda/contrato.py``.

Sem rede: as respostas são reais e recortadas (``tests/fixtures/congresso/agenda/``, com a origem
de cada uma no docstring de lá) e chegam por um ``fetch`` de teste (``fabrica.FetchDaAgenda``).
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from qijournal.agenda import contrato, regras as regras_da_agenda
from qijournal.config import load_config
from qijournal.congresso import agenda, cli
from qijournal.net import FetchError
from tests.fixtures.congresso import agenda as reais
from tests.fixtures.congresso import fabrica

HOJE = fabrica.AGENDA_HOJE  # terça, 24/11/2026
AGORA = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)
RAIZ = Path(__file__).resolve().parents[1]
REGRAS = regras_da_agenda.de_dict({})  # os padrões do código: CCJC e CFT; CCJ e CAE; CMO


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nenhum teste daqui acessa a rede: quem não injeta o ``fetch`` de teste falha na hora."""

    def sem_rede(url: str, **_opcoes: Any) -> Any:
        raise AssertionError(f"pedido de rede num teste: {url}")

    monkeypatch.setattr("qijournal.net.fetch", sem_rede)


def _rodar(
    pasta: Path, mapa: dict[str, bytes] | fabrica.FetchFalso, hoje: str = HOJE, *, apoio: bool = True, agora: datetime = AGORA, **opcoes: Any
) -> tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda | None]:
    """Uma rodada sobre as respostas guardadas. Devolve a rodada, o ``fetch`` (com os pedidos) e
    o arquivo gravado, lido pelo leitor do contrato."""
    destino = pasta / "data" / "congresso"
    destino.mkdir(parents=True, exist_ok=True)
    if apoio:
        for nome, conteudo in fabrica.apoio_da_agenda().items():
            (destino / nome).write_text(json.dumps(conteudo, ensure_ascii=False), encoding="utf-8")
    fetch = mapa if isinstance(mapa, fabrica.FetchFalso) else fabrica.FetchDaAgenda(mapa)
    opcoes.setdefault("regras", REGRAS)
    rodada = agenda.atualizar(None, pasta / "data", pasta / "cache", hoje=hoje, agora=agora, fetch=fetch, pausa=0.0, **opcoes)
    return rodada, fetch, contrato.ler(rodada.caminho, lado=contrato.LADO_CONGRESSO)


@pytest.fixture(scope="module")
def semana(tmp_path_factory: pytest.TempPathFactory) -> tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]:
    """A rodada de 24/11/2026 com todas as fontes no ar (janela de 23/11 a 06/12)."""
    rodada, fetch, gravada = _rodar(tmp_path_factory.mktemp("semana"), fabrica.respostas_da_agenda())
    assert gravada is not None
    return rodada, fetch, gravada


@pytest.fixture(scope="module")
def semana_anterior(tmp_path_factory: pytest.TempPathFactory) -> tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]:
    """A rodada de 19/11/2026 (janela de 16 a 29/11): sessões já encerradas nas duas Casas."""
    rodada, fetch, gravada = _rodar(tmp_path_factory.mktemp("anterior"), fabrica.respostas_da_semana_anterior(), fabrica.AGENDA_SEMANA_ANTERIOR)
    assert gravada is not None
    return rodada, fetch, gravada


def _sessao(gravada: contrato.Agenda, ident: str) -> contrato.SessaoAgenda:
    (sessao,) = [s for s in gravada.sessoes if s.id == ident]
    return sessao


def _item(sessao: contrato.SessaoAgenda, identificador: str) -> contrato.ItemAgenda:
    (item,) = [i for i in sessao.itens if i.identificador == identificador]
    return item


def _textos(valor: Any) -> list[str]:
    """Todos os textos gravados no arquivo (chaves e valores)."""
    if isinstance(valor, dict):
        return [t for k, v in valor.items() for t in (str(k), *_textos(v))]
    if isinstance(valor, list):
        return [t for v in valor for t in _textos(v)]
    return [valor] if isinstance(valor, str) else []


def _sem_placar(caminho: Path) -> None:
    """Nenhum campo gravado traz placar, e o par 127 e 347 (o requerimento de adiamento da sessão
    de 23/11, rejeitado) não aparece junto em campo nenhum."""
    for texto_ in _textos(json.loads(caminho.read_text(encoding="utf-8"))):
        assert not re.search(r"(?i)\b(sim|n[ãa]o|absten[çc][ãa]o|total)\s*:\s*\d", texto_), texto_
        assert not (re.search(r"\b127\b", texto_) and re.search(r"\b347\b", texto_)), texto_


# ── a rodada inteira ─────────────────────────────────────────────────────────


def test_a_semana_sai_no_formato_do_contrato(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    rodada, fetch, gravada = semana
    bruto = json.loads(rodada.caminho.read_text(encoding="utf-8"))
    assert contrato.validar(bruto) == [] and rodada.valido and rodada.gravado and rodada.erro is None
    assert (gravada.lado, gravada.hoje, gravada.janela, gravada.gerado_em) == ("congresso", HOJE, ("2026-11-23", "2026-12-06"), "2026-11-24T16:00:00Z")
    assert {f.chave: f.ok for f in gravada.fontes} == dict.fromkeys(agenda.FONTES, True)
    assert all(f.data_dados == HOJE and f.coletado_em == "2026-11-24T16:00:00Z" and f.aviso is None for f in gravada.fontes)
    assert [(s.data, s.hora, s.id, s.foro, s.pauta, len(s.itens)) for s in gravada.sessoes] == [
        ("2026-11-23", "18:32", "camara-83419", "plenario", "publicada", 13),
        ("2026-11-24", "10:00", "senado-reuniao-15013", "comissao_decide", "publicada", 7),
        ("2026-11-24", "13:55", "camara-85464", "plenario", "publicada", 12),
        ("2026-11-24", "14:00", "senado-594181", "plenario", "publicada", 4),
        ("2026-11-24", "14:30", "camara-83357", "comissao_decide", "publicada", 3),
        ("2026-11-25", "09:00", "senado-reuniao-15077", "comissao_decide", "publicada", 12),
        ("2026-11-25", "10:00", "camara-83412", "comissao_decide", "nao_publicada", 0),
        ("2026-11-25", "10:00", "camara-83359", "comissao_decide", "publicada", 3),
        ("2026-11-25", "11:00", "congresso-evento-83437", "comissao_decide", "nao_publicada", 0),
        ("2026-11-25", "14:00", "senado-594182", "plenario", "publicada", 3),
        ("2026-11-27", None, "congresso-prazo-mpv-1381-2026", "prazo", "publicada", 1),
        ("2026-11-28", None, "congresso-prazo-mpv-1382-2026", "prazo", "publicada", 1),
        ("2026-12-01", "14:00", "senado-594186", "plenario", "nao_publicada", 0),
        ("2026-12-02", "14:00", "senado-594184", "plenario", "nao_publicada", 0),
    ]
    assert rodada.sessoes == {"camara": 5, "senado": 6, "congresso": 3} and rodada.pedidos == len(fetch.pedidos) == 15
    assert all(contrato.link_oficial(s.link) and all(contrato.link_oficial(i.link) for i in s.itens) for s in gravada.sessoes)
    _sem_placar(rodada.caminho)


def test_a_ordem_dos_pedidos_e_plenarios_senado_prazos_ccjc_e_cft_e_o_resto(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    _rodada, fetch, _gravada = semana
    caminhos = [u.split(".leg.br", 1)[1] for u in fetch.pedidos]
    assert caminhos == [
        "/api/v2/eventos?dataInicio=2026-11-23&dataFim=2026-12-06&itens=100",
        "/api/v2/eventos?dataInicio=2026-11-23&dataFim=2026-12-06&pagina=2&itens=100",  # a lista tem duas páginas
        "/api/v2/eventos/83419/pauta",  # Plenário da Câmara
        "/api/v2/eventos/85464/pauta",
        "/dadosabertos/plenario/agenda/mes/20261123",  # Plenário do Senado
        "/dadosabertos/plenario/agenda/mes/20261201",
        "/dadosabertos/plenario/agenda/cn/20261123/20261206",  # sessão conjunta do Congresso
        "/dadosabertos/comissao/agenda/20261123/20261206",  # Senado: comissões que decidem
        f"/dadosabertos/processo/prazo?sigla=MPV&dataReferencia={HOJE}",  # prazos
        "/dadosabertos/processo?sigla=MPV&tramitando=S",
        "/api/v2/eventos/83357/pauta",  # CCJC e CFT
        "/api/v2/eventos/83359/pauta",
        "/api/v2/eventos/83412/pauta",
        "/api/v2/eventos/83437/pauta",  # o resto: a comissão mista e a contagem de vetos
        "/dadosabertos/materia/vetos/aposrcn",
    ]
    assert all(o["timeout"] == agenda.PRAZO_PEDIDO and o["retries"] == 0 for o in fetch.opcoes)  # o prazo próprio, não o do Coletor


# ── cuidados vistos nos dados reais ──────────────────────────────────────────


def test_em_comissao_o_projeto_esta_na_proposicao_relacionada(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    """Na CCJC a linha da pauta é o parecer (PRL, PAR): o item é o projeto do campo ``titulo``,
    com o id, a ementa e o link da proposição relacionada."""
    linhas = reais.ler("cd-evento-83357-pauta.json")["dados"]
    assert {linha["proposicao_"]["siglaTipo"] for linha in linhas} == {"REQ", "PAR", "PRL"}
    ccjc = _sessao(semana[2], "camara-83357")
    assert (ccjc.orgao, ccjc.orgao_sigla, ccjc.tipo) == ("Comissão de Constituição e Justiça e de Cidadania", "CCJC", "reuniao_deliberativa")
    assert [(i.ordem, i.identificador, i.tipo, i.alcance) for i in ccjc.itens] == [
        (19, "PRC 7/2025", "PRC", "outro"), (66, "PL 2310/2022", "PL", "pl"), (77, "PL 344/2026", "PL", "pl"),
    ]  # fmt: skip
    oab = _item(ccjc, "PL 344/2026")
    assert oab.link == "https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao=2601150"
    assert oab.ementa.startswith("Altera a Lei nº 8.906, de 4 de julho de 1994 (Estatuto da Advocacia e da OAB)")
    assert oab.relator == contrato.Relator(nome="Hildo Rocha", partido="MDB", uf="MA")
    # Requerimento, redações finais e outorgas de rádio e TV (art. 223) só em contagem.
    assert dict(ccjc.fora) == {"requerimento": 1, "redacao_final": 2, "outorga": 2} and ccjc.total_na_fonte == 8


def test_linhas_repetidas_viram_um_item(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    linhas = reais.ler("cd-evento-83419-pauta.json")["dados"]
    assert len(linhas) == 20 and sum(1 for linha in linhas if linha["titulo"] == "PLP 299/2026") == 6
    plenario = _sessao(semana[2], "camara-83419")
    assert [i.identificador for i in plenario.itens].count("PLP 299/2026") == 1 and len(plenario.itens) == 13
    assert dict(plenario.fora) == {"requerimento": 1, "simbolica": 1}  # o requerimento de urgência e um projeto de data comemorativa
    assert len({i.identificador for i in plenario.itens}) == len(plenario.itens)


def test_evento_cancelado_sai_da_selecao_e_o_resto_vira_contagem():
    eventos = reais.ler("cd-eventos-20261123.json")["dados"]
    assert [e["situacao"] for e in eventos if e["id"] == 83426] == ["Cancelada"]  # reunião deliberativa da CPD
    selecionados, contagens, plenario = agenda.eventos_da_camara(eventos, REGRAS, "2026-11-23", "2026-12-06")
    assert [(e.data, e.hora, e.id, e.sigla, e.instituicao, e.foro, e.situacao) for e in selecionados] == [
        ("2026-11-23", "18:32", 83419, "PLEN", "camara", "plenario", "encerrada"),
        ("2026-11-24", "13:55", 85464, "PLEN", "camara", "plenario", "agendada"),
        ("2026-11-24", "14:30", 83357, "CCJC", "camara", "comissao_decide", "agendada"),
        ("2026-11-25", "10:00", 83359, "CFT", "camara", "comissao_decide", "agendada"),
        ("2026-11-25", "10:00", 83412, "CCJC", "camara", "comissao_decide", "agendada"),
        ("2026-11-25", "11:00", 83437, "CMO", "congresso", "comissao_decide", "agendada"),  # comissão mista: Congresso
    ]
    assert selecionados[0].modalidade == "semipresencial" and plenario == 180  # a descrição da sessão diz; o id do órgão vem da lista
    assert dict(contagens) == {
        "eventos_cancelados": 1, "sessoes_solenes": 2, "audiencias": 2, "reunioes_de_outras_comissoes": 1, "outros_eventos": 1,
    }  # fmt: skip
    # A situação vem com rótulos que mudam ("Encerrada (Final)"); só "Cancelada" tira o evento.
    assert [agenda._situacao(s) for s in ("Convocada", "Agendada", "Em Andamento", "Encerrada", "Encerrada (Final)", "Cancelada", "Realizada", "?")] == [
        "agendada", "agendada", "em_andamento", "encerrada", "encerrada", None, "encerrada", "agendada",
    ]  # fmt: skip


def test_senado_lista_de_um_item_so_chega_como_objeto():
    """A resposta do Senado é XML convertido: com um elemento só, a lista vira objeto. Vale para
    a sessão, a matéria, a reunião, a parte e o item."""
    comissoes = reais.ler("sf-comissao-agenda-20261123.json")
    reunioes = comissoes["AgendaReuniao"]["reunioes"]["reuniao"]
    vacinas = next(r for r in reunioes if r["colegiadoCriador"]["sigla"] == "CCTVACIN")
    assert isinstance(vacinas["partes"], dict) and isinstance(vacinas["partes"]["itens"], dict)  # como a fonte mandou
    ccj = copy.deepcopy(next(r for r in reunioes if r["colegiadoCriador"]["sigla"] == "CCJ"))
    ccj["partes"]["itens"] = ccj["partes"]["itens"][1]  # um item só: a PEC 1/2025
    sessoes, _contagens = agenda.reunioes_do_senado({"AgendaReuniao": {"reunioes": {"reuniao": ccj}}}, REGRAS, "2026-11-23", "2026-12-06", agenda.Apoio())
    assert [(s.id, s.orgao_sigla, [i.identificador for i in s.itens]) for s in sessoes] == [("senado-reuniao-15077", "CCJ", ["PEC 1/2025"])]

    plenario = reais.ler("sf-plenario-agenda-mes-20261123.json")
    quarta = copy.deepcopy(next(s for s in plenario["AgendaPlenario"]["Sessoes"]["Sessao"] if s["CodigoSessao"] == "594182"))
    quarta["Materias"]["Materia"] = quarta["Materias"]["Materia"][0]
    so_uma = {"AgendaPlenario": {"Sessoes": {"Sessao": quarta}}}
    sessoes, _contagens = agenda.sessoes_do_plenario([so_uma], raiz="AgendaPlenario", casa="SF", de="2026-11-23", ate="2026-12-06", apoio=agenda.Apoio(), resultados={})
    assert [(s.id, [i.identificador for i in s.itens]) for s in sessoes] == [("senado-594182", ["PL 351/2019"])]
    vazia = {"AgendaPlenario": {"Sessoes": None}}  # nada vem quando a lista está vazia
    assert agenda.sessoes_do_plenario([vazia], raiz="AgendaPlenario", casa="SF", de="2026-11-23", ate="2026-12-06", apoio=agenda.Apoio(), resultados={})[0] == []


def test_janela_que_vira_o_mes_pede_duas_chamadas(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    _rodada, fetch, gravada = semana
    assert agenda.meses_da_janela("2026-11-23", "2026-12-06") == ["2026-11-23", "2026-12-01"]
    assert agenda.meses_da_janela("2026-11-09", "2026-11-22") == ["2026-11-09"]
    assert agenda.meses_da_janela("2026-12-28", "2027-01-10") == ["2026-12-28", "2027-01-01"]
    assert [u.rsplit("/", 1)[1] for u in fetch.pedidos if "plenario/agenda/mes" in u] == ["20261123", "20261201"]
    dezembro = [s for s in gravada.sessoes_de("senado") if s.data >= "2026-12-01"]
    assert [(s.data, s.modalidade, s.pauta, s.pauta_confirmada) for s in dezembro] == [
        ("2026-12-01", "presencial", "nao_publicada", False), ("2026-12-02", "presencial", "nao_publicada", False),
    ]  # fmt: skip
    assert all(s.data <= "2026-12-06" for s in gravada.sessoes)  # a resposta de dezembro vai além da janela (sessão de 08/12)


def test_espacos_especiais_e_o_numero_quebrado_das_ementas(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    """A Câmara devolve ``nº?200`` (o espaço não separável virou ``?``) e ementas com quebra de linha."""
    linhas = reais.ler("cd-evento-83419-pauta.json")["dados"]
    crua = next(linha["proposicaoRelacionada_"]["ementa"] for linha in linhas if linha["titulo"] == "PLP 299/2026")
    assert "nº?200" in crua
    fiscal = _item(_sessao(semana[2], "camara-83419"), "PLP 299/2026")
    assert fiscal.ementa.startswith("Altera a Lei Complementar nº 200, de 30 de agosto de 2023, a Lei Complementar nº 176,") and "?" not in fiscal.ementa
    for texto_ in _textos(json.loads(semana[0].caminho.read_text(encoding="utf-8"))):
        assert texto_ == texto_.strip() and not re.search(r"[   \t\r\n]| {2}", texto_), repr(texto_)


# ── o Plenário da Câmara: até quando a pauta está publicada, e a fila ─────────


def test_o_estado_da_camara_guarda_a_pauta_publicada_e_a_fila(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    camara = semana[2].estado_de("camara")
    assert camara is not None and camara.pauta_publicada_ate == "2026-11-24"  # a sessão de 24/11 foi registrada no próprio dia
    # A fila é o que ficou sem votação na última sessão ENCERRADA (23/11): não tem data marcada nem resultado.
    assert camara.fila_de == "2026-11-23" and len(camara.fila) == 12
    assert [i.identificador for i in camara.fila][:3] == ["PL 724/2003", "PL 7103/2014", "PL 294/2015"]
    assert "PLP 299/2026" not in {i.identificador for i in camara.fila}  # foi votado: não é fila
    assert all(i.resultado is None and i.estagio == "urgencia" for i in camara.fila)
    assert dict(camara.contagens)["sessoes_solenes"] == 2 and camara.recesso is None


def test_sem_sessao_na_janela_a_fila_vem_da_ultima_sessao_das_semanas_anteriores(tmp_path: Path):
    """Segunda de manhã a sessão deliberativa da semana ainda não foi registrada: a fila sai da
    última sessão encerrada do Plenário nas duas semanas anteriores (dois pedidos a mais)."""
    mapa = fabrica.respostas_da_agenda()
    chave = "/api/v2/eventos?dataInicio=2026-11-23&dataFim=2026-12-06&itens=100"
    lista = json.loads(mapa[chave])
    lista["dados"] = [e for e in lista["dados"] if e["id"] not in (83419, 85464)]
    mapa[chave] = json.dumps(lista).encode("utf-8")
    mapa["/api/v2/orgaos/180/eventos?dataInicio=2026-11-09&dataFim=2026-11-22&itens=100"] = reais.bruto("cd-orgao-180-eventos-20261109.json")
    mapa["/api/v2/eventos/83382/pauta"] = reais.bruto("cd-evento-83382-pauta.json")
    rodada, fetch, gravada = _rodar(tmp_path, mapa, "2026-11-23")
    assert gravada is not None and not [s for s in gravada.sessoes_de("camara") if s.foro == "plenario"]
    camara = gravada.estado_de("camara")
    assert (camara.pauta_publicada_ate, camara.fila_de) == (None, "2026-11-18")  # a pauta do Plenário ainda não foi publicada
    assert [i.identificador for i in camara.fila] == ["PL 724/2003", "PL 479/2015", "PL 5542/2026"]  # o PL 3965/2024 é data comemorativa
    assert [u.split("/api/v2", 1)[1] for u in fetch.pedidos[2:4]] == ["/orgaos/180/eventos?dataInicio=2026-11-09&dataFim=2026-11-22&itens=100", "/eventos/83382/pauta"]
    # No dia seguinte, com a mesma última sessão, a fila gravada vale: só a consulta da lista é refeita.
    fetch.pedidos.clear()
    _rodar(tmp_path, fetch, "2026-11-23", agora=datetime(2026, 11, 23, 20, 0, tzinfo=timezone.utc))
    assert not [u for u in fetch.pedidos if "83382" in u] and rodada.valido


# ── resultado: só a palavra, com regra estrita ───────────────────────────────


def test_resultado_da_camara_so_a_palavra_e_a_frase_do_proprio_item(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    """A resposta real da sessão de 23/11 (``/eventos/83419/pauta``): o texto do PLP 299/2026 tem
    9 placares, e o primeiro (Sim: 127; Não: 347) é do requerimento de adiamento, rejeitado."""
    linhas = reais.ler("cd-evento-83419-pauta.json")["dados"]
    texto_ = next(linha["situacaoItem"] for linha in linhas if linha["titulo"] == "PLP 299/2026")
    assert len(re.findall(r"Sim: \d+; Não: \d+", texto_)) == 9 and "Rejeitado o Requerimento. Sim: 127; Não: 347" in texto_
    plenario = _sessao(semana[2], "camara-83419")
    fiscal = _item(plenario, "PLP 299/2026")
    assert plenario.situacao == "encerrada"
    assert fiscal.resultado == contrato.Resultado(palavra="aprovado", texto="Aprovado o Projeto de Lei Complementar nº 299, de 2026, ressalvados os destaques.")
    assert (fiscal.estagio, fiscal.estagio_detalhe) == ("urgencia", "Urgência (Art. 155, RICD)")
    # Os demais itens não foram chamados: "não apreciado", com a frase oficial.
    assert {i.resultado.palavra for i in plenario.itens if i is not fiscal} == {"nao_apreciado"}
    assert _item(plenario, "PL 724/2003").resultado.texto == "Matéria não apreciada em face do encerramento da Sessão."
    _sem_placar(semana[0].caminho)
    gravado = semana[0].caminho.read_text(encoding="utf-8")
    assert "Rejeitado o Requerimento" not in gravado and "Sim: 353" not in gravado and "adiamento" not in gravado


def test_sessao_em_andamento_nao_tem_resultado(tmp_path: Path):
    """A mesma sessão lida na noite de 23/11, ainda em andamento: o PLP 299/2026 está "em
    votação", sem resultado, embora o texto já traga "Aprovado o Projeto…" e seis placares."""
    mapa = fabrica.respostas_da_agenda()
    chave = "/api/v2/eventos?dataInicio=2026-11-23&dataFim=2026-12-06&itens=100"
    lista = json.loads(mapa[chave])
    lista["dados"] = [dict(e, situacao="Em Andamento", dataHoraFim=None) if e["id"] == 83419 else e for e in lista["dados"] if e["id"] != 85464]
    mapa[chave] = json.dumps(lista).encode("utf-8")
    mapa["/api/v2/eventos/83419/pauta"] = reais.bruto("cd-evento-83419-pauta-em-andamento.json")
    rodada, _fetch, gravada = _rodar(tmp_path, mapa, "2026-11-23")
    plenario = _sessao(gravada, "camara-83419")
    fiscal = _item(plenario, "PLP 299/2026")
    assert plenario.situacao == "em_andamento" and all(i.resultado is None for i in plenario.itens)
    assert (fiscal.estagio, fiscal.outros_estagios, fiscal.estagio_detalhe) == ("em_votacao", ("urgencia",), None)
    assert {i.estagio for i in plenario.itens if i is not fiscal} == {"urgencia"}  # os demais ainda não foram chamados
    assert gravada.estado_de("camara").fila == ()  # sem sessão encerrada na janela nem antes dela (a consulta falha): sem fila
    _sem_placar(rodada.caminho)


def test_resultado_da_camara_com_nenhum_ou_mais_de_um_candidato_fica_sem_resultado():
    r = agenda.resultado_da_camara
    aprovado = "Votação em turno único. Aprovado o Projeto de Lei nº 10, de 2026. Sim: 300; Não: 100; Total: 400."
    assert r([aprovado], "PL", 10, 2026) == contrato.Resultado(palavra="aprovado", texto="Aprovado o Projeto de Lei nº 10, de 2026.")
    assert r([aprovado, aprovado], "PL", 10, 2026).palavra == "aprovado"  # as linhas repetidas trazem o mesmo texto
    assert r([aprovado], "PL", 11, 2026) is None  # a frase é de outro projeto
    assert r([aprovado], "PL", 10, 2025) is None
    assert r(["Rejeitada a Medida Provisória nº 1.300, de 2025."], "MPV", 1300, 2025).palavra == "rejeitado"  # o ponto do milhar
    assert r(["Aprovado o Substitutivo ao Projeto de Lei nº 10, de 2026, ressalvados os destaques."], "PL", 10, 2026).palavra == "aprovado_ressalvados_destaques"
    assert r(["Aprovado o Substitutivo ao Projeto de Lei nº 10, de 2026, ressalvados os destaques. Mantido o texto. A Matéria vai ao Senado Federal (PL 10-A/2026)."], "PL", 10, 2026).palavra == "aprovado"
    # Mais de uma decisão de mérito no mesmo registro (dois turnos, substitutivo e projeto): ambíguo.
    dois = "Aprovada a Proposta de Emenda à Constituição nº 3, de 2026, em primeiro turno. Aprovada a Proposta de Emenda à Constituição nº 3, de 2026, em segundo turno."
    assert r([dois], "PEC", 3, 2026) is None
    assert r(["Aprovado o Substitutivo ao Projeto de Lei nº 10, de 2026. Rejeitado o Projeto de Lei nº 10, de 2026."], "PL", 10, 2026) is None
    # O que não é decisão de mérito do item nunca vira resultado.
    for texto_ in (
        "Rejeitado o Requerimento. Sim: 127; Não: 347; Abstenção: 7; Total: 481.",
        "Aprovado o Requerimento de Urgência (Art. 155 do RICD). Sim: 318; Não: 135; Abstenção: 6; Total: 459.",
        "Rejeitadas as Emendas de Plenário. Sim: 129; Não: 319.",
        "Aprovada a Redação Final nos termos do art. 198, parágrafo 5º do Regimento Interno da Câmara dos Deputados.",
        "Aprovado o Parecer.",
        "Parecer que conclui pela aprovação do Projeto de Lei nº 10, de 2026.",
        "Votação do destaque para suprimir o art. 2º do Projeto de Lei nº 10, de 2026. Mantido o texto.",
        "",
    ):
        assert r([texto_], "PL", 10, 2026) is None, texto_
    # Três registros inteiros e sem ambiguidade, como a Câmara os escreve.
    assert r(["Matéria não apreciada em face do encerramento da Sessão."], "PL", 10, 2026).palavra == "nao_apreciado"
    assert r(["Não deliberado."], "PL", 10, 2026) == contrato.Resultado(palavra="nao_apreciado", texto="Não deliberado.")
    retirado = r(["Retirado de pauta, de ofício, a pedido do Relator. Prejudicados os Requerimentos de Retirada de Pauta."], "PL", 10, 2026)
    assert retirado == contrato.Resultado(palavra="retirado_de_pauta", texto="Retirado de pauta, de ofício, a pedido do Relator.")
    assert r(["Vista ao Deputado Luiz Couto. Dispensada a leitura do Parecer."], "PL", 10, 2026) == contrato.Resultado(palavra="pedido_de_vista", texto="Vista ao Deputado Luiz Couto.")


def test_comissao_da_camara_encerrada_so_palavras_sem_ambiguidade(semana_anterior: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    """A CCJC de 17/11 (encerrada): "Aprovado o Parecer." não é resultado do projeto (o parecer
    pode ser pela rejeição); retirada de pauta, vista e "não deliberado" são."""
    rodada, _fetch, gravada = semana_anterior
    ccjc = _sessao(gravada, "camara-83282")
    assert ccjc.situacao == "encerrada" and dict(ccjc.fora) == {"redacao_final": 2}
    palavras = {i.identificador: (i.resultado.palavra if i.resultado else None) for i in ccjc.itens}
    assert palavras["PDL 366/2025"] is None and palavras["PL 4256/2019"] is None  # "Aprovado o Parecer."
    assert palavras["PDL 756/2025"] == "retirado_de_pauta" and palavras["PL 4250/2024"] == "pedido_de_vista" and palavras["PL 2310/2022"] == "nao_apreciado"
    assert _item(ccjc, "PL 4250/2024").resultado.texto == "Vista ao Deputado Luiz Couto."
    assert "camara-83325" not in {s.id for s in gravada.sessoes}  # a reunião cancelada de 18/11
    # A pauta da reunião de 18/11 não veio: a sessão fica, marcada como não lida, e a fonte avisa.
    nao_lida = _sessao(gravada, "camara-83324")
    assert (nao_lida.pauta, nao_lida.itens, nao_lida.situacao) == ("nao_coletada", (), "encerrada")
    fonte = gravada.fonte("camara_eventos")
    assert fonte.ok and fonte.aviso == "1 pauta(s) não lida(s) nesta coleta"
    _sem_placar(rodada.caminho)


def test_resultado_do_senado_e_a_frase_oficial_so_de_sessao_encerrada(semana_anterior: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    _rodada, fetch, gravada = semana_anterior
    assert [u for u in fetch.pedidos if "plenario/resultado" in u] == ["https://legis.senado.leg.br/dadosabertos/plenario/resultado/mes/20261116"]
    terca, quarta = _sessao(gravada, "senado-594178"), _sessao(gravada, "senado-594179")
    assert terca.situacao == quarta.situacao == "encerrada"
    assert {i.identificador: (i.resultado.palavra, i.resultado.texto) if i.resultado else None for i in terca.itens} == {
        "PLP 299/2026": ("retirado_de_pauta", "Retirado de Pauta."),
        "MPV 1379/2026": ("aprovado", "Aprovado o projeto de lei de conversão, ficam prejudicadas a medida provisória e as demais emendas apresentadas."),
        "PL 6683/2025": ("aprovado", "Aprovado o projeto."),
    }
    palavras = {i.identificador: (i.resultado.palavra if i.resultado else None) for i in quarta.itens}
    assert palavras == {
        "PL 3481/2026": "aprovado", "PLP 299/2026": None, "PL 6461/2019": "retirado_de_pauta", "PL 5756/2026": "aprovado",
        "PL 4186/2021": "aprovado", "PL 6222/2025": "aprovado", "PL 3220/2019": "aprovado",
    }  # fmt: skip  (o PLP 299/2026 só teve destaques votados naquele dia: sem "Resultado da matéria", sem palavra)
    assert dict(quarta.fora) == {"requerimento": 3} and dict(terca.fora) == {"simbolica": 1}
    # A sessão de 24/11 ainda não aconteceu: o serviço de resultado já a lista com "Não apreciado", que não é resultado.
    futura = _sessao(gravada, "senado-594181")
    assert futura.situacao == "agendada" and all(i.resultado is None for i in futura.itens)
    assert _item(terca, "PLP 299/2026").estagio == "urgencia"
    assert _item(terca, "PLP 299/2026").estagio_detalhe == "Tramita em regime de urgência, nos termos do Requerimento nº 770, de 2026"


def test_resultado_do_senado_regra_estrita():
    r = agenda.resultado_do_senado
    assert r({"textoResultado": "Resultado da matéria: Aprovado o projeto.\n\nA matéria vai à sanção."}) == contrato.Resultado(palavra="aprovado", texto="Aprovado o projeto.")
    assert r({"textoResultado": "Resultado: Aprovado o requerimento.\n\nResultado da matéria: Rejeitado o projeto."}).palavra == "rejeitado"
    assert r({"textoResultado": "Resultado da matéria: Aprovado o substitutivo, ressalvados os destaques."}).palavra == "aprovado_ressalvados_destaques"
    assert r({"textoResultado": "Resultado da matéria: Aprovado o requerimento."}) is None  # decidiu o requerimento, não a matéria
    assert r({"textoResultado": "Resultado da matéria: Aprovado o projeto.\nResultado da matéria: Rejeitado o projeto."}) is None  # duas frases
    assert r({"textoResultado": "Objeto da deliberação: Emenda nº 40.\nResultado: Rejeitada a Emenda nº 40.\nVotação nominal:\nSim: 30\nNão: 47", "descricaoDeliberacao": "Apreciado"}) is None
    assert r({"descricaoDeliberacao": "Retirado", "descricaoTipoPauta": "Retirado de Pauta"}) == contrato.Resultado(palavra="retirado_de_pauta", texto="Retirado de Pauta")
    assert r({"descricaoDeliberacao": "Não apreciado"}) == contrato.Resultado(palavra="nao_apreciado")
    assert agenda._so_antes_do_fim(contrato.Resultado(palavra="nao_apreciado"), "agendada") is None
    assert agenda._so_antes_do_fim(contrato.Resultado(palavra="retirado_de_pauta"), "agendada") is not None


def test_comissao_do_senado_resultado_oficial_por_item(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    """A CAE de 24/11 (realizada): "Retirado de pauta" e "Adiado" são tipos oficiais de resultado;
    "Aprovado o relatório" é decisão sobre o parecer e fica sem palavra."""
    cae = _sessao(semana[2], "senado-reuniao-15013")
    assert (cae.orgao, cae.situacao, cae.modalidade) == ("Comissão de Assuntos Econômicos", "encerrada", "semipresencial")
    assert cae.link == "https://legis.senado.leg.br/atividade/comissoes/comissao/38/reuniao/15013"
    assert [(i.identificador, i.resultado.palavra if i.resultado else None) for i in cae.itens] == [
        ("PL 3079/2024", "retirado_de_pauta"), ("PL 1985/2019", "retirado_de_pauta"), ("PL 1505/2022", "retirado_de_pauta"),
        ("PL 2524/2022", "retirado_de_pauta"), ("PL 4133/2023", None), ("PL 675/2022", "adiado"), ("PL 3040/2025", "adiado"),
    ]  # fmt: skip
    assert _item(cae, "PL 3079/2024").resultado.texto == "Retirado de pauta para reexame."
    # Com a reunião em andamento, o item que a fonte marca como "em deliberação" está em votação.
    agora = copy.deepcopy(next(r for r in reais.ler("sf-comissao-agenda-20261123.json")["AgendaReuniao"]["reunioes"]["reuniao"] if r["codigo"] == "15013"))
    agora["situacao"] = "Em andamento"
    for linha in agora["partes"]["itens"]:
        linha.pop("situacao", None), linha.pop("siglaResultado", None)
        linha["emDeliberacao"] = "true" if linha["nome"] == "PL 4133/2023" else "false"
    (aberta,), _contagens = agenda.reunioes_do_senado({"AgendaReuniao": {"reunioes": {"reuniao": [agora]}}}, REGRAS, "2026-11-23", "2026-12-06", agenda.Apoio())
    assert aberta.situacao == "em_andamento" and {i.identificador: i.estagio for i in aberta.itens if i.estagio != "pautado"} == {"PL 4133/2023": "em_votacao"}
    assert contrato.validar(json.loads(contrato.para_json(dataclasses.replace(semana[2], sessoes=(aberta,))))) == []
    senado = semana[2].estado_de("senado")
    assert dict(senado.contagens) == {
        "sessoes_nao_deliberativas": 14, "reunioes_de_outras_comissoes": 2, "reunioes_nao_deliberativas": 1, "itens_terminativos_em_outras_comissoes": 1,
    }  # fmt: skip
    assert senado.pauta_publicada_ate == "2026-11-25"


# ── sinais para a régua e casamento com a ficha ──────────────────────────────


def test_sinais_para_a_regua(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    gravada = semana[2]
    assert {(s.instituicao, s.foro, s.tipo) for s in gravada.sessoes} == {
        ("camara", "plenario", "deliberativa"), ("camara", "comissao_decide", "reuniao_deliberativa"),
        ("senado", "plenario", "deliberativa"), ("senado", "comissao_decide", "reuniao_deliberativa"),
        ("congresso", "comissao_decide", "reuniao_deliberativa"), ("congresso", "prazo", "prazo"),
    }  # fmt: skip
    alcances = {i.tipo: i.alcance for s in gravada.sessoes for i in s.itens}
    assert alcances == {"PLP": "plp", "PL": "pl", "PDL": "pdl", "PEC": "pec", "MPV": "mpv", "PRC": "outro"}
    # Urgência: o regime, como a Câmara escreve. Pauta confirmada: o campo oficial do Senado.
    hoje = _sessao(gravada, "camara-85464")
    assert {(i.estagio, i.estagio_detalhe) for i in hoje.itens} == {("urgencia", "Urgência (Art. 155, RICD)"), ("urgencia", 'Urgência (Art. 151, I "j", RICD)')}
    senado = _sessao(gravada, "senado-594181")
    assert senado.pauta_confirmada is True and senado.modalidade == "semipresencial" and {i.estagio for i in senado.itens} == {"pauta_confirmada"}
    assert senado.link == "https://legis.senado.leg.br/ordiaportal/casa/SF/24/11/2026"
    # O pedido de urgência ainda "pendente de deliberação" não é regime de urgência.
    assert all("urgencia" not in i.estagios for s in gravada.sessoes_de("senado") for i in s.itens)
    # Terminativo (a comissão decide no lugar do Plenário): o registro vai no detalhe do estágio.
    ccj = _sessao(gravada, "senado-reuniao-15077")
    assert {i.identificador for i in ccj.itens if i.estagio_detalhe == "decisão terminativa"} == {"PL 2091/2023", "PL 1229/2024", "PL 4687/2023"}
    assert _item(ccj, "PEC 1/2025").relator == contrato.Relator(nome="Rogério Carvalho", partido="PT", uf="SE")
    # A régua de config/agenda.yaml soma o que o contrato guarda (30 + 18 + 15 = 63 no Plenário do Senado).
    regua, item = regras_da_agenda.carregar(RAIZ).regua, senado.itens[0]
    assert regua.pontos_foro(senado.foro) + regua.pontos_alcance(item.alcance) + regua.pontos_estagio(*item.estagios) == 63


def test_o_item_casa_com_a_ficha_conferindo_o_id(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    gravada = semana[2]
    camara = _sessao(gravada, "camara-85464")
    cor = _item(camara, "PL 7103/2014")  # mesmo título e mesmo id da ficha da Câmara
    assert (cor.ficha, cor.relevancia, cor.area) == ("cd-PL-7103-2014", 12, "saude")
    assert cor.link.endswith("idProposicao=606005")
    outro = _item(camara, "PL 479/2015")  # o título existe na ficha, com OUTRO id: não casa
    assert (outro.ficha, outro.relevancia, outro.area) == (None, None, None)
    # Sem ficha na Câmara, vale a do Senado para as siglas de numeração única (2019 em diante).
    fiscal = _item(_sessao(gravada, "camara-83419"), "PLP 299/2026")
    assert (fiscal.ficha, fiscal.relevancia, fiscal.area, fiscal.apelido) == ("sf-PLP-299-2026", 27, "economia", "Ajuste fiscal")
    assert fiscal.relator == contrato.Relator(nome="Mauro Benevides Filho", partido="UNIÃO", uf="CE")  # o partido veio de resumo.json
    aprendiz = _item(_sessao(gravada, "senado-594181"), "PL 6461/2019")
    assert (aprendiz.ficha, aprendiz.relevancia, aprendiz.area, aprendiz.apelido) == ("sf-PL-6461-2019", 62, "trabalho", "Estatuto do Aprendiz")
    assert aprendiz.link == "https://www25.senado.leg.br/web/atividade/materias/-/materia/174061"


def test_apelido_da_tabela_do_config_nao_e_tomado_por_oficial():
    """O apelido da ficha do Senado é o oficial ou, na falta dele, o da tabela de
    ``config/congresso.yaml`` (apelido de imprensa): só o primeiro nomeia o item."""
    fichas = {casa: fabrica.apoio_da_agenda()[f"materias-{casa}.json"]["materias"] for casa in ("cd", "sf")}
    sem_tabela = agenda.Apoio(fichas=fichas)
    com_tabela = agenda.Apoio(fichas=fichas, apelidos_da_tabela={"PL 5894/2025": ("Plano da Cultura", "PNC")})
    assert sem_tabela.ficha("sf", "PL 5894/2025", 175950).apelido == "Plano da Cultura"
    assert com_tabela.ficha("sf", "PL 5894/2025", 175950) == agenda.Casada(chave="sf-PL-5894-2025", nota=62, area=None, apelido=None)
    assert com_tabela.ficha("sf", "PL 5894/2025", 999) == agenda.Casada()  # o id da matéria não confere
    assert com_tabela.ficha("sf", "PEC 1/2025", 166999) == agenda.Casada()
    # PEC tem numeração própria em cada Casa: nunca casa pela ficha da outra.
    pec = agenda.Apoio(fichas={"cd": {"PEC 1/2025": {"id": 5, "rel": {"n": 90}}}, "sf": {}})
    assert pec.ficha("sf", "PEC 1/2025", 166999) == agenda.Casada() and pec.ficha("cd", "PEC 1/2025", 5).nota == 90
    assert agenda.ler_apoio(None, Path("/nao/existe")).fichas == {"cd": {}, "sf": {}}  # sem os arquivos, tudo vazio


# ── Congresso: prazos, sessão conjunta, vetos ────────────────────────────────


def test_prazos_de_medida_provisoria_vem_prontos_da_fonte(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    """O último dia de vigência é o ``fimPrazo`` do prazo "Deliberação da Medida Provisória"; o
    nome vem da lista de processos, pelo ``idProcesso``. Nada é calculado."""
    prazos, processos = reais.ler("sf-processo-prazo-mpv.json"), reais.ler("sf-processo-mpv-tramitando.json")
    cru = next(p for p in prazos if p["idProcesso"] == 9089704)
    assert (cru["tipoPrazo"], cru["fimPrazo"], cru["prorrogado"]) == ("Deliberação da Medida Provisória", "2026-11-27", "Sim")
    assert sum(1 for m in processos if m["identificacao"].endswith("/2001")) == 3  # medidas anteriores a 2002: fora
    linhas, proximo = agenda.prazos_de_mpv(prazos, processos, "2026-11-23", "2026-12-06")
    assert [(s.id, s.data, s.hora, s.foro, s.itens[0].identificador, s.itens[0].estagio) for s in linhas] == [
        ("congresso-prazo-mpv-1381-2026", "2026-11-27", None, "prazo", "MPV 1381/2026", "prazo_vence"),
        ("congresso-prazo-mpv-1382-2026", "2026-11-28", None, "prazo", "MPV 1382/2026", "prazo_vence"),
    ]
    assert linhas[0].itens[0].apelido == "Crédito Extraordinário - Subvenções à produção e à importação de combustíveis"
    assert linhas[0].link == "https://www25.senado.leg.br/web/atividade/materias/-/materia/175319"
    assert (proximo.id, proximo.data) == ("congresso-prazo-mpv-1395-2026", "2026-12-07")  # o primeiro depois da janela
    assert agenda.prazos_de_mpv(prazos, [], "2026-11-23", "2026-12-06") == ([], None)  # sem o nome não há linha
    de_2001 = [dict(processos[-1], id=9089704)]  # uma medida de 2001 com o prazo de outra: fica fora
    assert de_2001[0]["identificacao"].endswith("/2001") and agenda.prazos_de_mpv(prazos, de_2001, "2026-11-23", "2026-12-06") == ([], None)
    congresso = semana[2].estado_de("congresso")
    assert congresso.proximo.id == "congresso-prazo-mpv-1395-2026" and "MPV" not in {i.tipo for i in semana[2].estado_de("camara").fila}


def test_agenda_do_congresso_vazia_nao_e_erro_e_os_vetos_viram_contagem(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    gravada = semana[2]
    assert json.loads(reais.bruto("sf-plenario-agenda-cn-vazia.json"))["AgendaPlenarioCN"].keys() == {"noNamespaceSchemaLocation", "Metadados"}
    congresso = gravada.estado_de("congresso")
    assert gravada.fonte("congresso_sessoes").ok and congresso.sessao_conjunta_convocada is False
    assert dict(congresso.contagens) == {"vetos_tramitando": 4, "vetos_trancando": 3}
    cmo = _sessao(gravada, "congresso-evento-83437")  # a comissão mista aparece nos eventos da Câmara
    assert (cmo.orgao_sigla, cmo.foro, cmo.pauta, cmo.link) == ("CMO", "comissao_decide", "nao_publicada", "https://www.camara.leg.br/evento-legislativo/83437")
    assert agenda.contar_vetos({"outra": "coisa"}) is None


def test_sessao_conjunta_do_congresso(tmp_path: Path):
    """A sessão conjunta de 12/11 (resposta real): a hora vem como "9:00" e os itens são PLN."""
    rodada, _fetch, gravada = _rodar(tmp_path, fabrica.respostas_da_sessao_conjunta(), fabrica.AGENDA_SESSAO_CONJUNTA)
    (conjunta,) = gravada.sessoes
    assert (conjunta.id, conjunta.instituicao, conjunta.foro, conjunta.tipo, conjunta.data, conjunta.hora) == (
        "congresso-596834", "congresso", "plenario", "conjunta", "2026-11-12", "09:00",
    )  # fmt: skip
    assert conjunta.link == "https://legis.senado.leg.br/ordiaportal/casa/CN/12/11/2026" and conjunta.modalidade is None
    assert [(i.identificador, i.alcance, i.estagio) for i in conjunta.itens] == [
        ("PLN 1/2026", "orcamento", "pautado"), ("PLN 3/2026", "orcamento", "pautado"), ("PLN 4/2026", "orcamento", "pautado"),
    ]  # fmt: skip
    congresso = gravada.estado_de("congresso")
    assert congresso.sessao_conjunta_convocada is True and dict(congresso.contagens) == {"sessoes_solenes": 2}
    # As outras fontes falharam nesta rodada (não estão no mapa): cada uma diz o que houve, e nada vira zero.
    assert {f.chave for f in gravada.fontes if not f.ok} == {"camara_eventos", "congresso_comissoes", "congresso_prazos", "senado_comissoes", "senado_plenario"}
    assert all(f.aviso and f.data_dados is None for f in gravada.fontes if not f.ok) and rodada.valido


# ── rede: prazo próprio, prazo global, fonte fora do ar ──────────────────────


def test_fetch_com_prazo_troca_o_prazo_e_faz_uma_nova_tentativa():
    relogio = fabrica.Relogio()
    orcamento = agenda.Orcamento(prazo=150.0, relogio=relogio)
    fetch = fabrica.FetchDaAgenda({"/ok": b"{}"}, relogio=relogio, segundos=2.0)
    fetch.fora_do_ar.add("/lento")
    pedir = agenda.fetch_com_prazo(fetch, orcamento, dormir=relogio.dormir)
    assert pedir("https://x.leg.br/ok", timeout=90.0, retries=4, headers={"Accept": "application/json"}).status == 200
    assert fetch.opcoes[0] == {"timeout": 18.0, "retries": 0, "headers": {"Accept": "application/json"}}  # 90 s e 4 tentativas viram 18 s e 0
    with pytest.raises(FetchError, match="HTTP 504"):
        pedir("https://x.leg.br/lento", timeout=90.0, retries=4)
    assert fetch.pedidos.count("https://x.leg.br/lento") == 2 and orcamento.pedidos == 3  # 504 é passageiro: uma nova tentativa, e só uma
    fetch.status = 404
    with pytest.raises(FetchError, match="HTTP 404"):
        pedir("https://x.leg.br/lento")
    assert fetch.pedidos.count("https://x.leg.br/lento") == 3  # 404 não se tenta de novo
    # Perto do fim do prazo, o prazo de cada pedido encolhe; depois dele, nada mais é pedido.
    relogio.agora += 150.0 - orcamento.gasto() - 6.0
    pedir("https://x.leg.br/ok")
    assert fetch.opcoes[-1]["timeout"] == pytest.approx(2.0)
    relogio.agora += 10.0
    with pytest.raises(agenda.PrazoEsgotado):
        pedir("https://x.leg.br/ok")
    cheio = agenda.Orcamento(maximo=1, relogio=relogio)
    cheio.pedidos = 1
    with pytest.raises(agenda.PrazoEsgotado):
        agenda.fetch_com_prazo(fetch, cheio)("https://x.leg.br/ok")


def test_fonte_lenta_termina_no_prazo_e_grava_o_parcial(tmp_path: Path):
    """Cada resposta leva 20 s: a rodada para de pedir aos 150 s e grava o que deu tempo, com a
    situação de cada fonte."""
    relogio = fabrica.Relogio()
    fetch = fabrica.FetchDaAgenda(fabrica.respostas_da_agenda(), relogio=relogio, segundos=20.0)
    rodada, _fetch, gravada = _rodar(tmp_path, fetch, relogio=relogio, dormir=relogio.dormir)
    assert rodada.prazo_esgotado and rodada.pedidos == 8 and 150.0 <= rodada.segundos <= 170.0  # o último pedido começou antes do fim do prazo
    assert rodada.valido and rodada.gravado and contrato.validar(json.loads(rodada.caminho.read_text(encoding="utf-8"))) == []
    # A ordem de prioridade decidiu o que veio: os Plenários e as comissões do Senado.
    assert {f.chave: f.ok for f in gravada.fontes} == {
        "camara_eventos": True, "congresso_comissoes": True, "senado_plenario": True, "congresso_sessoes": True, "senado_comissoes": True, "congresso_prazos": False,
    }  # fmt: skip
    assert gravada.fonte("congresso_prazos").aviso == agenda.SEM_PRAZO
    assert gravada.fonte("camara_eventos").aviso == "4 pauta(s) não lida(s) nesta coleta"
    assert {s.id: s.pauta for s in gravada.sessoes_de("camara") if s.foro != "plenario"} == dict.fromkeys(("camara-83357", "camara-83359", "camara-83412"), "nao_coletada")
    assert _sessao(gravada, "camara-83419").pauta == "publicada" and not [s for s in gravada.sessoes if s.tipo == "prazo"]


def test_fonte_com_504_mantem_o_anterior_e_nunca_grava_zero(tmp_path: Path):
    """A Câmara fora do ar no dia seguinte: as sessões dela ficam como estavam, com a data do
    dado, o aviso vai para a fonte e o Senado e o Congresso são refeitos."""
    _rodada, _fetch, antes = _rodar(tmp_path, fabrica.respostas_da_agenda())
    fora = fabrica.FetchDaAgenda(fabrica.respostas_da_agenda(), status=504)
    fora.mapa["/dadosabertos/processo/prazo?sigla=MPV&dataReferencia=2026-11-25"] = reais.bruto("sf-processo-prazo-mpv.json")
    fora.fora_do_ar.add("dadosabertos.camara.leg.br")
    rodada, _fetch, depois = _rodar(tmp_path, fora, "2026-11-25", agora=datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc))
    assert rodada.valido and rodada.gravado and depois.hoje == "2026-11-25"
    assert fora.pedidos.count("https://dadosabertos.camara.leg.br/api/v2/eventos?dataInicio=2026-11-23&dataFim=2026-12-06&itens=100") == 2  # a nova tentativa
    camara = depois.fonte("camara_eventos")
    assert (camara.ok, camara.data_dados, camara.coletado_em) == (False, HOJE, "2026-11-24T16:00:00Z") and "HTTP 504" in camara.aviso
    assert [(s.id, len(s.itens), s.como_pautado_em) for s in depois.sessoes_de("camara")] == [(s.id, len(s.itens), HOJE) for s in antes.sessoes_de("camara")]
    assert depois.estado_de("camara") == antes.estado_de("camara")  # a fila e a pauta publicada do último dia bom
    assert _sessao(depois, "congresso-evento-83437").como_pautado_em == HOJE  # a comissão mista vem da mesma lista
    assert depois.fonte("senado_plenario").data_dados == "2026-11-25" and all(s.como_pautado_em is None for s in depois.sessoes_de("senado"))
    assert len(depois.sessoes) == len(antes.sessoes) and rodada.sessoes["camara"] == 5


def test_resultado_do_senado_fora_do_ar_mantem_a_sessao_do_arquivo_anterior(tmp_path: Path):
    mapa = fabrica.respostas_da_semana_anterior()
    _rodada, _fetch, antes = _rodar(tmp_path, mapa, fabrica.AGENDA_SEMANA_ANTERIOR)
    fora = fabrica.FetchDaAgenda(mapa)
    fora.fora_do_ar.add("plenario/resultado")
    rodada, _fetch, depois = _rodar(tmp_path, fora, fabrica.AGENDA_SEMANA_ANTERIOR)
    fonte = depois.fonte("senado_plenario")
    assert fonte.ok and fonte.aviso.startswith("resultado das sessões encerradas não lido (HTTP 504")
    terca = _sessao(depois, "senado-594178")
    assert terca.como_pautado_em == fabrica.AGENDA_SEMANA_ANTERIOR and [i.resultado for i in terca.itens] == [i.resultado for i in _sessao(antes, "senado-594178").itens]
    assert rodada.valido


def test_rodar_de_novo_sem_mudanca_nao_regrava(tmp_path: Path):
    rodada, fetch, _gravada = _rodar(tmp_path, fabrica.respostas_da_agenda())
    antes = rodada.caminho.read_bytes()
    carimbo = rodada.caminho.stat().st_mtime_ns
    de_novo, _fetch, _gravada = _rodar(tmp_path, fetch, agora=datetime(2026, 11, 24, 18, 30, tzinfo=timezone.utc))
    assert rodada.gravado and not de_novo.gravado and de_novo.valido  # só os relógios mudaram: o commit diário não leva o arquivo
    assert rodada.caminho.read_bytes() == antes and rodada.caminho.stat().st_mtime_ns == carimbo
    assert de_novo.sessoes == rodada.sessoes and de_novo.bytes == len(antes)


def test_saida_fora_do_contrato_nao_e_gravada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    rodada, fetch, _gravada = _rodar(tmp_path, fabrica.respostas_da_agenda())
    antes = rodada.caminho.read_bytes()
    monkeypatch.setattr(agenda, "LINK_EVENTO_DA_CAMARA", "http://exemplo.com/evento/{id}")  # link que não é oficial
    ruim, _fetch, _gravada = _rodar(tmp_path, fetch, "2026-11-25", agora=datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc))
    assert not ruim.valido and not ruim.gravado and "link oficial obrigatório" in ruim.erro
    assert rodada.caminho.read_bytes() == antes  # fica o arquivo anterior
    assert any("fora do contrato, nada foi gravado" in r.getMessage() for r in caplog.records)


def test_a_hora_marcada_e_a_do_arquivo_anterior(tmp_path: Path):
    """Depois que a sessão começa, a lista troca a hora marcada pela hora real de início."""
    mapa = fabrica.respostas_da_agenda()
    chave = "/api/v2/eventos?dataInicio=2026-11-23&dataFim=2026-12-06&itens=100"
    cedo = json.loads(mapa[chave])
    cedo["dados"] = [dict(e, situacao="Convocada", dataHoraInicio="2026-11-23T18:00", dataHoraFim=None) if e["id"] == 83419 else e for e in cedo["dados"]]
    _rodada, _fetch, antes = _rodar(tmp_path, {**mapa, chave: json.dumps(cedo).encode("utf-8")}, "2026-11-23")
    assert (_sessao(antes, "camara-83419").hora, _sessao(antes, "camara-83419").situacao) == ("18:00", "agendada")
    _rodada, _fetch, depois = _rodar(tmp_path, mapa)
    assert (_sessao(depois, "camara-83419").hora, _sessao(depois, "camara-83419").situacao) == ("18:00", "encerrada")  # a lista de hoje diz 18:32


def test_prazo_que_venceu_na_semana_fica_ate_a_virada(tmp_path: Path):
    """A fonte só lista o prazo em vigor: a linha da medida que venceu ontem é mantida do
    arquivo anterior, com a data do dado, e some na semana seguinte."""
    _rodada, _fetch, antes = _rodar(tmp_path, fabrica.respostas_da_agenda())
    sabado = fabrica.respostas_da_agenda()
    sabado["/dadosabertos/processo/prazo?sigla=MPV&dataReferencia=2026-11-28"] = json.dumps(
        [p for p in reais.ler("sf-processo-prazo-mpv.json") if p["fimPrazo"] != "2026-11-27"]
    ).encode("utf-8")
    _rodada, _fetch, depois = _rodar(tmp_path, sabado, "2026-11-28", agora=datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc))
    vencida = _sessao(depois, "congresso-prazo-mpv-1381-2026")
    assert (vencida.data, vencida.como_pautado_em) == ("2026-11-27", HOJE) and vencida.itens == _sessao(antes, vencida.id).itens
    assert _sessao(depois, "congresso-prazo-mpv-1382-2026").como_pautado_em is None  # a que vence hoje ainda vem da fonte
    segunda = {"/dadosabertos/plenario/agenda/cn/20261130/20261213": reais.bruto("sf-plenario-agenda-cn-vazia.json")}
    _rodada, _fetch, virada = _rodar(tmp_path, segunda, "2026-11-30", agora=datetime(2026, 11, 30, 12, 0, tzinfo=timezone.utc))
    assert virada.janela == ("2026-11-30", "2026-12-13") and all(s.data >= "2026-11-30" for s in virada.sessoes)  # a poda da semana


def test_acima_do_teto_as_comissoes_perdem_a_ementa(semana: tuple[agenda.Rodada, fabrica.FetchFalso, contrato.Agenda]):
    inteira = semana[2]
    tamanho = len(contrato.para_json(inteira).encode("utf-8"))
    igual, texto_ = agenda._dentro_do_teto(inteira, tamanho)
    assert igual == inteira and len(texto_.encode("utf-8")) == tamanho
    cortada, texto_ = agenda._dentro_do_teto(inteira, tamanho - 3000)
    assert len(texto_.encode("utf-8")) <= tamanho - 3000 and contrato.validar(json.loads(texto_)) == []
    assert all(i.ementa for s in cortada.sessoes if s.foro in ("plenario", "prazo") for i in s.itens)  # o Plenário e os prazos ficam inteiros
    sem = [s.id for s in cortada.sessoes if s.itens and not any(i.ementa for i in s.itens)]
    assert sem and all(_sessao(cortada, ident).foro == "comissao_decide" for ident in sem)
    assert [[i.identificador for i in s.itens] for s in cortada.sessoes] == [[i.identificador for i in s.itens] for s in inteira.sessoes]


# ── recesso e legislatura ────────────────────────────────────────────────────


def test_recesso_vira_campo_e_resposta_vazia_fora_dele_e_fonte_fora_do_ar(tmp_path: Path):
    """Em recesso a agenda vazia é o esperado e o arquivo diz "recesso"; fora dele, uma lista de
    eventos vazia numa janela com dias úteis é tratada como fonte fora do ar."""
    vazias = {
        "/api/v2/eventos?dataInicio=2027-01-04&dataFim=2027-01-17&itens=100": b'{"dados": [], "links": []}',
        "/api/v2/orgaos/180/eventos?dataInicio=2026-12-21&dataFim=2027-01-03&itens=100": b'{"dados": [], "links": []}',
        "/dadosabertos/plenario/agenda/mes/20270104": json.dumps({"AgendaPlenario": {"Metadados": {}}}).encode("utf-8"),
        "/dadosabertos/plenario/agenda/cn/20270104/20270117": reais.bruto("sf-plenario-agenda-cn-vazia.json"),
    }
    destino = tmp_path / "recesso"
    rodada = agenda.atualizar(RAIZ, destino / "data", destino / "cache", hoje="2027-01-05", agora=AGORA, fetch=fabrica.FetchDaAgenda(vazias), pausa=0.0)
    gravada = contrato.ler(rodada.caminho, lado="congresso")
    assert rodada.valido and gravada.sessoes == ()
    assert gravada.fonte("camara_eventos").ok and gravada.fonte("senado_plenario").ok
    assert gravada.estado_de("camara").recesso == contrato.Recesso(nome="recesso de fim de ano", de="2026-12-23", ate="2027-02-01")
    assert gravada.estado_de("congresso").recesso is not None and gravada.estado_de("senado").recesso is not None
    # A mesma resposta vazia numa semana comum: a fonte é marcada como fora do ar.
    comuns = {chave.replace("2027-01-04", "2026-11-23").replace("2027-01-17", "2026-12-06"): corpo for chave, corpo in vazias.items()}
    _rodada, _fetch, comum = _rodar(tmp_path / "comum", comuns)
    assert (comum.fonte("camara_eventos").ok, comum.fonte("camara_eventos").aviso) == (False, "a lista de eventos veio vazia")
    assert comum.estado_de("camara") is None


def test_a_coleta_nao_depende_da_legislatura_configurada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """O ``congresso atualizar`` para quando ``config/congresso.yaml`` não tem legislatura para o
    dia; a agenda nem pergunta: roda igual depois do fim da última legislatura configurada."""
    from qijournal.congresso import regras as regras_do_congresso

    def nao_perguntar(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a agenda não depende da legislatura")

    monkeypatch.setattr(regras_do_congresso.Regras, "legislatura_atual", nao_perguntar)
    mapa = {
        "/dadosabertos/plenario/agenda/cn/20270301/20270314": reais.bruto("sf-plenario-agenda-cn-vazia.json"),
        "/dadosabertos/processo/prazo?sigla=MPV&dataReferencia=2027-03-02": reais.bruto("sf-processo-prazo-mpv.json"),
        "/dadosabertos/processo?sigla=MPV&tramitando=S": reais.bruto("sf-processo-mpv-tramitando.json"),
    }
    rodada = agenda.atualizar(RAIZ, tmp_path / "data", tmp_path / "cache", hoje="2027-03-02", agora=AGORA, fetch=fabrica.FetchDaAgenda(mapa), pausa=0.0)
    gravada = contrato.ler(rodada.caminho, lado="congresso")
    assert rodada.valido and rodada.gravado and [s.id for s in gravada.sessoes] == ["congresso-prazo-mpv-1392-2026", "congresso-prazo-mpv-1393-2026", "congresso-prazo-mpv-1394-2026"]


# ── o comando ────────────────────────────────────────────────────────────────


def _args(pasta: Path, agora: str = "2026-11-24T16:00:00") -> argparse.Namespace:
    return argparse.Namespace(dir=str(pasta), cache=str(pasta / "cache"), now=datetime.fromisoformat(agora))


def test_o_comando_grava_o_arquivo_e_sai_0_com_fonte_fora_do_ar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = load_config(env={})
    assert config.agenda_ativo
    fetch = fabrica.FetchDaAgenda(fabrica.respostas_da_agenda())
    fetch.fora_do_ar |= {"dadosabertos.camara.leg.br", "plenario/agenda/mes"}
    monkeypatch.setattr(agenda.net, "fetch", fetch)
    monkeypatch.setattr(agenda, "PAUSA", 0.0)
    monkeypatch.setattr(agenda.time, "sleep", lambda _segundos: None)
    resumo = tmp_path / "resumo.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(resumo))
    assert cli.cmd_agenda(_args(tmp_path), config) == cli.EXIT_OK
    gravada = contrato.ler(tmp_path / "data" / "congresso" / "agenda.json", lado="congresso")
    assert gravada is not None and gravada.hoje == HOJE and not gravada.fonte("camara_eventos").ok and gravada.fonte("senado_comissoes").ok
    texto_ = resumo.read_text(encoding="utf-8")
    assert "### 🗓️ Agenda do Congresso" in texto_ and "| Câmara: eventos e pautas | falhou: HTTP 504" in texto_ and "| Senado: agenda das comissões | ok |" in texto_


def test_o_comando_respeita_o_liga_desliga_e_nunca_devolve_erro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def sem_rede(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a agenda desligada não acessa a rede")

    monkeypatch.setattr(agenda.net, "fetch", sem_rede)
    desligada = load_config(env={"QIJ_NO_AGENDA": "1"})
    assert not desligada.agenda_ativo and cli.cmd_agenda(_args(tmp_path), desligada) == cli.EXIT_OK
    ligada = load_config(env={})
    outra_marca = dataclasses.replace(ligada, agenda=dataclasses.replace(ligada.agenda, marcas=["outra"]))
    assert cli.cmd_agenda(_args(tmp_path), outra_marca) == cli.EXIT_OK
    assert not (tmp_path / "data").exists()

    def quebra(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("defeito inesperado")

    monkeypatch.setattr(agenda, "atualizar", quebra)
    assert cli.cmd_agenda(_args(tmp_path), ligada) == cli.EXIT_OK  # acessório: nem um defeito aqui derruba a edição


def test_o_comando_esta_na_linha_de_comando():
    from qijournal.__main__ import build_parser

    args = build_parser().parse_args(["congresso", "agenda", "--dir", "/tmp/x", "--cache", "/tmp/c", "--now", "2026-11-24T12:00:00"])
    assert (args.handler, args.dir, args.cache) == (cli.cmd_agenda, "/tmp/x", "/tmp/c") and args.now == datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    padrao = build_parser().parse_args(["congresso", "agenda"])
    assert (padrao.dir, padrao.cache, padrao.now) == (".", cli.CACHE_PADRAO, None)


# ── o passo no fluxo diário ──────────────────────────────────────────────────


def test_o_fluxo_diario_coleta_a_agenda_antes_do_passo_pesado():
    fluxo = yaml.safe_load((RAIZ / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8"))
    passos = fluxo["jobs"]["edition"]["steps"]
    nomes = [p.get("name") for p in passos]
    assert nomes.index("Agenda do Congresso") + 1 == nomes.index("Atualizar o Congresso") < nomes.index("Gerar a edição")
    passo, vizinho = passos[nomes.index("Agenda do Congresso")], passos[nomes.index("Atualizar o Congresso")]
    assert passo["continue-on-error"] is True and passo["timeout-minutes"] == 5
    assert passo["run"] == 'python -m qijournal congresso agenda --dir . --cache "$RUNNER_TEMP/congresso"'
    assert passo["env"] == {**vizinho["env"], "QIJ_NO_AGENDA": "${{ vars.QIJ_NO_AGENDA }}"}  # as variáveis do passo vizinho mais a da agenda
    assert agenda.PRAZO_TOTAL < passo["timeout-minutes"] * 60 / 1.5  # o prazo de rede cabe com folga no prazo do passo
    edicao = passos[nomes.index("Gerar a edição")]
    assert edicao["env"]["QIJ_NO_AGENDA"] == "${{ vars.QIJ_NO_AGENDA }}"
    # O commit da edição leva data/ e edicoes/ inteiros, fora o que é dos Tribunais: a agenda vai junto.
    publicar = passos[nomes.index("Publicar (commit e push)")]["run"]
    assert 'git add "$@" -- index.html edicoes data' in publicar and "agenda" not in publicar
