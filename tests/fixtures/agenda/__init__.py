"""Dois arquivos de agenda de exemplo, um de cada lado, no formato do contrato
(``qijournal/agenda/contrato.py``), montados a partir dos dados reais da semana de 23 a
27/11/2026 e válidos (``contrato.validar`` não acha erro). Para os testes, sem rede.

- ``congresso.json`` (o formato de ``data/congresso/agenda.json``): respostas das APIs de dados
  abertos da Câmara e do Senado lidas em 23/11/2026 por volta de 22h45 de Brasília (por isso
  ``hoje`` é 23/11 e ``gerado_em`` já é 24/11 em UTC). Traz o Plenário da Câmara de 23/11 ainda
  em andamento, com o PLP 299/2026 em votação (sessão que não está encerrada não tem resultado)
  e, em ``estado.camara.fila``, o que ficou sem deliberação; a CCJC de 24/11 e a CFT de 25/11
  (requerimentos, redações finais e outorgas só em contagem); a CCJC de 25/11 com a pauta ainda
  não publicada; a comissão especial do PLP 108/2021; o Plenário do Senado de 24 e 25/11 (pauta
  confirmada; o "Estatuto do Aprendiz" é o nome curto que o próprio Senado publica); a CAE e a
  CCJ do Senado; os prazos das MPVs 1381/2026 (27/11) e 1382/2026 (28/11); nenhuma sessão
  conjunta convocada; e, como próximo evento do Congresso, o prazo da MPV 1395/2026 (07/12).
- ``tribunais.json`` (o formato de ``data/tribunais/agenda.json``): o Plenário presencial do STF
  de 25/11 (10 processos, entre eles o RE 1326541, Tema 1218, e o ARE 1502069, Tema 1324, que
  voltam de pedido de vista), o dia 26/11 que o calendário marca e ainda não tem processos, as
  contagens das sessões virtuais, e as 22 sessões do STJ de 24/11 a 03/12 do arquivo de pautas
  de 23/11 cruzado com os precedentes qualificados (a fonte não informa hora nem modalidade).
  Os dados do STF vêm da resposta real guardada em ``tests/fixtures/tribunais/stf/`` e dos
  dossiês publicados em ``data/tribunais/``; a ementa fica nula porque a amostra traz o resumo
  oficial cortado.

Não há nome de pessoa física entre as partes nem de advogado: partes só ente público ou
litigante do catálogo, e os processos em segredo só aparecem nas contagens (``fora.segredo``).
Os nomes que aparecem são os de quem relata no registro oficial (deputados, senadores e
ministros) e do ministro do pedido de vista ou do destaque. As ligações com a ficha do Congresso
(``ficha``, ``relevancia``, ``area``) vêm de ``data/congresso/materias-*.json`` de 23/11/2026.
O endereço da pauta de uma sessão do Plenário do Senado segue um padrão que não foi conferido ao
vivo; os demais links vêm das fontes ou seguem os padrões que o projeto já usa.

Uso::

    from tests.fixtures import agenda as fx
    congresso = fx.congresso()          # contrato.Agenda
    tribunais = fx.tribunais()
    encerrada = fx.camara_encerrada()   # variante: a sessão de 23/11 já encerrada, com o resultado
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from qijournal.agenda import contrato

PASTA = Path(__file__).resolve().parent
CONGRESSO = PASTA / "congresso.json"
TRIBUNAIS = PASTA / "tribunais.json"
HOJE = "2026-11-23"  # o dia da coleta dos dois arquivos
SEMANA = ("2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27")
CAMARA_PLENARIO = "camara-83419"  # a sessão deliberativa de 23/11
STF_PLENARIO = "stf-plenario-2026-11-25"
# A frase oficial do resultado do PLP 299/2026 no registro da sessão (o texto inteiro do registro
# traz seis placares, o primeiro de um requerimento de adiamento rejeitado: nenhum vai para a agenda).
RESULTADO_PLP_299 = "Aprovado o Projeto de Lei Complementar nº 299, de 2026, ressalvados os destaques."


def congresso() -> contrato.Agenda:
    """O lado do Congresso (Câmara, Senado e Congresso) como estava em 23/11/2026 à noite."""
    agenda = contrato.ler(CONGRESSO, lado=contrato.LADO_CONGRESSO)
    assert agenda is not None, "tests/fixtures/agenda/congresso.json fora do contrato"
    return agenda


def tribunais() -> contrato.Agenda:
    """O lado dos Tribunais (STF e STJ) como estava em 23/11/2026."""
    agenda = contrato.ler(TRIBUNAIS, lado=contrato.LADO_TRIBUNAIS)
    assert agenda is not None, "tests/fixtures/agenda/tribunais.json fora do contrato"
    return agenda


def camara_encerrada() -> contrato.Agenda:
    """Variante de teste do lado do Congresso: a sessão do Plenário da Câmara de 23/11 já
    encerrada e o PLP 299/2026 com o resultado (a frase é a do registro real; o encerramento é
    a hipótese do teste, porque na coleta de 23/11 a sessão ainda estava em andamento)."""
    agenda = congresso()
    sessoes = []
    for sessao in agenda.sessoes:
        if sessao.id == CAMARA_PLENARIO:
            itens = tuple(
                replace(item, estagio="urgencia", estagio_detalhe="Urgência (Art. 155, RICD)", outros_estagios=(),
                        resultado=contrato.Resultado(palavra="aprovado_ressalvados_destaques", texto=RESULTADO_PLP_299))
                if item.identificador == "PLP 299/2026" else item
                for item in sessao.itens
            )
            sessao = replace(sessao, situacao="encerrada", itens=itens)
        sessoes.append(sessao)
    return replace(agenda, sessoes=tuple(sessoes))
