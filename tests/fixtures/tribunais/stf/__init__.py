"""Amostras reais e pequenas das fontes do STF (testes de ``qijournal/tribunais/stf.py``, sem rede).

Origem de cada arquivo (baixados em 22–23/11/2026; pessoa física, advogados e servidores mascarados):

- ``acervo_amostra.xlsx``: recorte da planilha ``Acervo_STF_Lista_Geral.xlsx`` de 22/11/2026 (o arquivo
  inteiro tem 22.211 processos). O XML original de cada linha e de cada texto foi mantido (estilos,
  referências de célula, células vazias puladas, números em notação científica como ``1.34462e6``,
  ``&#x0D;`` entre as linhas de assunto, ``\\xa0`` em nomes): as linhas 1-4 (a data da atualização e o
  cabeçalho), 23 processos escolhidos (segredo, nomes colados, Presidente, Vice, sem relator, relator
  que saiu, PGR, outro gabinete, conclusão ao relator e ao substituto, vista, sobrestado, liminar,
  tutela, assunto que não casa com a TPU) e a linha final de soma. Sem os hyperlinks da coluna Link.
  Pessoas físicas viraram "PESSOA FÍSICA A".."H"; advogados, "ADVOGADO(A) 1".."38" (os
  institucionais ficaram).
- ``ata_recuperaata_2026-11-19.xml``, ``ata_recuperaata_2026-11-20.xml``, ``ata_recuperaata_2026-11-22_domingo.xml``: o índice do dia
  (``portal.stf.jus.br/atadistribuicao/recuperaata.asp``), inteiros.
- ``ata_2026-11-19_252_trecho.html``: a ata 252 (``listaratas.asp``) com 10 dos 228 processos (todas
  as categorias de parte: PACTE, COATOR, IMPDO, AUT. POL., LIT.PAS., INTDO, BENEF...) e o
  totalizador inteiro; o cabeçalho e os menus da página foram tirados.
- ``ata_2026-11-20_253.html``: a ata 253 inteira (3 processos), sem cabeçalho e menus.
- ``ata_feitos_2026-11-20.html``: os 8 feitos registrados à Presidência em 20/11 (``listarfeitos.asp``).
- ``juris_consulta_12m.json``: o corpo da consulta de agregação (``stf.consulta_decisoes`` de
  23/11/2025 a 22/11/2026), como foi mandado ao vivo em 23/11/2026.
- ``juris_agregacao_12m.json``: a resposta real dessa consulta (``_shards.failed == 0``): só contagens,
  nenhum documento.
- ``rg_todostemas_trecho.html``, ``rg_exportar_trecho.xls.html``: 14 dos 1.502 temas de
  ``todostemas.asp`` e ``exportarDados.asp`` (com os acentos quebrados da exportação: "HÃ¡").
- ``pauta_sessao-presencial_2026-11-25.json``: a pauta do Plenário de 25/11/2026
  (``calendario-service.asp?dados=sessao-presencial``), com os resumos cortados em 60 caracteres.
- ``pauta_calendarios_2026-11.json``: o calendário de novembro de 2026, inteiro.
"""

from pathlib import Path

PASTA = Path(__file__).resolve().parent


def ler(nome: str, encoding: str = "utf-8") -> str:
    return (PASTA / nome).read_text(encoding=encoding)
