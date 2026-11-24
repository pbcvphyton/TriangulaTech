"""Agenda da semana: o que está pautado no STF, no STJ, na Câmara, no Senado e no Congresso.

A agenda junta dois arquivos de coleta, escritos por fluxos diferentes (um escritor por pasta) e
no MESMO formato:

- ``data/congresso/agenda.json``: escrito pela edição diária (Câmara, Senado e Congresso);
- ``data/tribunais/agenda.json``: escrito pelo fluxo Tribunais (STF e STJ).

Módulos:

- :mod:`.contrato`: o formato único dos dois arquivos (campo ``versao``), o vocabulário fechado,
  os objetos tipados de sessão e de item, a validação, o leitor tolerante (arquivo ausente,
  ilegível, de formato inesperado ou de versão desconhecida vale como fonte ausente), a
  mesclagem com o arquivo anterior e as funções de janela (semana corrente, dias da edição);
- :mod:`.regras`: as escolhas de ``config/agenda.yaml`` (pesos da régua, cortes, tetos, comissões
  que decidem, frescor, dicionário de situações, palavras de área, nomes e apelidos aprovados por
  pessoa, feeds que não contam como imprensa), com padrões seguros quando o arquivo falta.

Nada aqui usa IA nem rede: cada campo de um item é registro oficial ou sinal para a régua, e
nenhum texto é escrito por IA. A edição diária só LÊ os dois arquivos; nada neste pacote pode
derrubar a edição.
"""
