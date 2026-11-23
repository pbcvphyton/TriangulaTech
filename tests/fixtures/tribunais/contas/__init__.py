"""Amostras das contas da aba Tribunais (partes, contas, achados).

- ``casos_dificeis_stj.json``: a fixture do analista jurídico (um registro real do acervo do STJ de
  20/11/2026 por caso difícil: família em segredo, HC com o tribunal como impetrado, precatório com o
  STJ requisitante, carta rogatória, Banco do Brasil com raiz 00000000, MEI, herança, ex-ministro,
  Presidência na ARP, "Não informado", prefixo 9, qualificador, ExeMS, sobrestado por RG, convocado).
  Nomes de pessoa física, MEI e advogados mascarados; o MEI guarda a forma do nome real (nome + 11
  dígitos de CPF fictícios) para o teste "PJ com CPF no nome = pessoa física".

Os testes de contas e achados também usam processos sintéticos (montados nos próprios testes) e as
amostras reais de ``tests/fixtures/tribunais/stj/`` e ``stf/`` (atas, íntegras, temas, a planilha do
STF com 23 processos).
"""

from pathlib import Path

PASTA = Path(__file__).resolve().parent
