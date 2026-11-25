"""Respostas reais das APIs de dados abertos da Câmara e do Senado, recortadas, para os testes
do coletor da agenda do Congresso (``qijournal/congresso/agenda.py``). Nenhum teste acessa a rede.

Todas foram lidas em 24/11/2026, entre 15h45 e 16h00 UTC (12h45 a 13h00 de Brasília), salvo
indicação. "Recortada" quer dizer: a lista foi reduzida aos elementos que os testes usam e, onde
está dito, campos que a coleta não lê foram retirados; nenhum valor foi alterado.

Câmara (``https://dadosabertos.camara.leg.br/api/v2``):

- ``cd-eventos-20261123.json``: ``/eventos?dataInicio=2026-11-23&dataFim=2026-12-06&itens=100``,
  página 1 (96 eventos), recortada a 13: as duas sessões deliberativas do Plenário (83419, de
  23/11, encerrada; 85464, de 24/11, convocada no próprio dia), duas sessões solenes, as reuniões
  deliberativas da CCJC (83357 e 83412), da CFT (83359) e da CMO (83437, comissão mista), uma
  reunião deliberativa cancelada (83426), uma de outra comissão (83316), duas audiências e um
  evento com dois órgãos. A descrição dos eventos que não entram na seleção ficou só com a
  primeira linha. O link ``next`` é o da resposta real (a API devolve uma segunda página).
- ``cd-eventos-20261123-p2.json``: a página 2 da mesma consulta (vazia), inteira.
- ``cd-evento-83419-pauta.json``: ``/eventos/83419/pauta`` (sessão deliberativa de 23/11), inteira,
  com a sessão já encerrada: o PLP 299/2026 aparece em 6 linhas repetidas e o texto de situação
  dele traz 9 placares; o primeiro, "Sim: 127; Não: 347", é do requerimento de adiamento.
- ``cd-evento-83419-pauta-em-andamento.json``: a mesma consulta lida em 24/11/2026 às 02h47 UTC
  (23h47 de 23/11 em Brasília), com a sessão em andamento (o texto termina no meio de um
  destaque), inteira.
- ``cd-evento-85464-pauta.json``: ``/eventos/85464/pauta`` (sessão de 24/11), inteira: os 13 itens
  que ficaram sem votação em 23/11.
- ``cd-evento-83357-pauta.json``: ``/eventos/83357/pauta`` (CCJC, 77 linhas), recortada a 8: um
  requerimento, duas redações finais, duas outorgas de radiodifusão (art. 223) e três de mérito.
  Em todas a ``proposicao_`` é o parecer e o projeto está em ``proposicaoRelacionada_``.
- ``cd-evento-83412-pauta.json`` e ``cd-evento-83437-pauta.json``: pautas ainda não publicadas
  (``"dados": []``), inteiras; a primeira foi lida em 24/11/2026 às 01h46 UTC.
- ``cd-evento-83359-pauta.json``: ``/eventos/83359/pauta`` (CFT, lida em 24/11/2026 às 01h46 UTC),
  recortada a 5: dois requerimentos e três projetos.
- ``cd-eventos-ccjc-20261116.json``: ``/orgaos/2003/eventos?dataInicio=2026-11-16&dataFim=2026-11-20&itens=100``
  (as reuniões da CCJC na semana anterior: uma encerrada, uma cancelada, uma encerrada), inteira.
  Os testes a servem no lugar da lista geral de eventos daquela semana (o formato do evento é o mesmo).
- ``cd-evento-83282-pauta.json``: ``/eventos/83282/pauta`` (CCJC de 17/11, encerrada, 66 linhas),
  recortada a 12: uma linha de cada registro de situação ("Aprovado o Parecer.", "Retirado de
  pauta, de ofício…", "Vista ao Deputado…", "Não deliberado."…).
- ``cd-orgao-180-eventos-20261109.json``: ``/orgaos/180/eventos?dataInicio=2026-11-09&dataFim=2026-11-22&itens=100``
  (o Plenário nas duas semanas anteriores), inteira.
- ``cd-evento-83382-pauta.json``: ``/eventos/83382/pauta`` (sessão deliberativa de 18/11), inteira.

Senado (``https://legis.senado.leg.br/dadosabertos``; JSON convertido de XML, pedido pelo
cabeçalho ``Accept``):

- ``sf-plenario-agenda-mes-20261123.json``: ``/plenario/agenda/mes/20261123``, inteira.
- ``sf-plenario-agenda-mes-20261201.json``: ``/plenario/agenda/mes/20261201``, recortada às sessões
  até 08/12.
- ``sf-plenario-agenda-mes-20261116.json``: ``/plenario/agenda/mes/20261116``, recortada às sessões
  de 16 a 19/11 e à de 24/11 (594181).
- ``sf-plenario-resultado-mes-20261116.json``: ``/plenario/resultado/mes/20261116``, recortada às
  sessões 594178 (17/11), 594179 (18/11), 594181 (24/11, ainda não realizada: os itens vêm como
  "Não apreciado") e 571069 (sessão solene do Congresso).
- ``sf-comissao-agenda-20261123.json``: ``/comissao/agenda/20261123/20261206`` (17 reuniões, 1 MB),
  recortada a 5 reuniões: CAE (15013, realizada, com resultado por item), CCJ (15077), CCTVACIN
  (15069, um item só: chega como objeto), CAS (15068, sem parte deliberativa) e CCT (15050, só
  os 3 primeiros itens). De cada item ficaram só os campos que a coleta lê (saíram os textos da
  pauta, a autoria e os documentos).
- ``sf-plenario-agenda-cn-vazia.json``: ``/plenario/agenda/cn/20261123/20261206``, inteira: sem
  sessão conjunta, a resposta só traz os metadados.
- ``sf-plenario-agenda-cn-20261109.json``: ``/plenario/agenda/cn/20260801/20261120``, recortada a
  três sessões: a solene de 11/11, a conjunta de 12/11 (596834; só os 3 primeiros dos 26 itens)
  e a solene de 18/11.
- ``sf-processo-prazo-mpv.json``: ``/processo/prazo?sigla=MPV&dataReferencia=2026-11-24``, inteira
  (36 prazos; 20 do tipo "Deliberação da Medida Provisória").
- ``sf-processo-mpv-tramitando.json``: ``/processo?sigla=MPV&tramitando=S`` (64 medidas),
  recortada às 21 de 2026 e a 3 das 43 de 2001.
- ``sf-materia-vetos-aposrcn.json``: ``/materia/vetos/aposrcn`` (113 vetos, lida em 24/11/2026 às
  01h49 UTC), recortada a 4 vetos (3 que trancam a pauta e 1 que não) e, de cada um, ao código, à
  matéria e ao campo ``SobrestandoPauta``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PASTA = Path(__file__).parent


def bruto(nome: str) -> bytes:
    """O corpo de uma resposta guardada, como a rede o devolveria."""
    return (PASTA / nome).read_bytes()


def ler(nome: str) -> Any:
    return json.loads(bruto(nome).decode("utf-8"))
