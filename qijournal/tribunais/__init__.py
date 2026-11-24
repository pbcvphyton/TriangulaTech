"""Analítico dos Tribunais: a aba "Tribunais" (STF e STJ), ``edicoes/tribunais.html``.

A pergunta da aba: que ministro está com o quê, há quanto tempo, de que área e com quem
de cada lado. As contas saem do que os próprios tribunais publicam em dados abertos:

- **STJ**: o Portal de Dados Abertos (CKAN, licença CC-BY): o acervo em tramitação do dia
  (um registro por processo, com relator, local, assuntos da TPU e partes), as atas de
  distribuição, as pautas futuras, os precedentes qualificados e os metadados das íntegras
  publicadas no Diário da Justiça. O caminho ``/api/`` do portal é vedado pelo robots.txt
  e não é usado: os links de download saem da página HTML de cada conjunto.
- **STF**: a planilha do acervo (``Acervo_STF_Lista_Geral.xlsx``, refeita todo dia), as
  atas de distribuição, a pesquisa de jurisprudência e a lista de temas de repercussão
  geral. A consulta processual (``portal.stf.jus.br/processos``) é vedada pelo robots.txt.
- **CNJ**: as Tabelas Processuais Unificadas (TPU), versionadas em ``data/tribunais/tpu/``
  e refeitas do dump oficial do SGT quando o CNJ publica versão nova; o DataJud fica
  desligado por padrão (termo de uso, itens 3.3, 3.8, 3.9 e 3.13; ver
  ``config/tribunais.yaml``).

Módulos: ``modelo`` (o registro normalizado de um processo, os formatos de saída e a
leitura de ``config/tribunais.yaml``), ``fontes`` (o coletor: cache, robots.txt, pausa por
host, recusa do WAF, POST), ``tpu`` (a árvore de assuntos e as classes), ``areas``
(macroárea, subárea e recortes de um processo), ``stj`` e ``stf`` (as fontes de cada
tribunal), ``partes``, ``contas``, ``achados``, ``datajud``, ``dados`` (``atualizar``) e
``cli``. O contrato entre eles (cada campo e cada arquivo de ``data/tribunais/``) está em
``modelo.py``.

Nada aqui usa IA: cada número é uma contagem sobre registro oficial, com a fonte e a data.
"""

VERSAO = 1  # formato de data/tribunais/: outro número faz a página recusar os dados e refazer tudo
PASTA = "tribunais"  # data/tribunais/ e .cache/tribunais/
PAGINA = "tribunais.html"  # edicoes/tribunais.html
TRIBUNAIS = ("stf", "stj")  # ordem de exibição (hierarquia)
