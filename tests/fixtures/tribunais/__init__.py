"""Amostras reais e pequenas das fontes da aba Tribunais (para os testes, sem rede).

Origem de cada arquivo (todos baixados na fase de descoberta, 22–23/11/2026):

- ``robots_dadosabertos_stj.txt``: robots.txt do Portal de Dados Abertos do STJ (``Disallow: /api/``,
  ``Disallow: /dataset/*/history``, ``Crawl-Delay: 10``).
- ``robots_portal_stf.txt``: robots.txt de portal.stf.jus.br (``Disallow: /processos``).
- ``robots_processo_stj.txt``: robots.txt de processo.stj.jus.br (``Disallow: /``, latin-1).
- ``recusa_403_cloudflare.html``: a página que o Cloudflare do STJ devolve com o 403.
- ``ckan_dataset_acervo.html``: a página HTML do conjunto ``acervo-em-tramitacao`` (CKAN 2.10.11),
  com os links ``/dataset/<uuid>/resource/<uuid>/download/<arquivo>``.
- ``sgt_dump_mini.sql``: recorte do dump oficial do SGT (versão 85) com os vícios do arquivo
  (latin-1, ``''`` e ``\\'``/``\\"``, ``textoGrande :=``, ``to_date``, ``END;``); os textos longos
  foram encurtados e os nomes de usuário do CNJ, mascarados.
- ``sgt_getDataUltimaVersao.xml``: resposta SOAP real (``05/11/2026``).
- ``sgt_versoes_trecho.html``: o bloco da página de versões do SGT com o link do dump.
- ``stj_acervo_casos.json``: 18 registros reais do acervo do STJ de 20/11/2026, um por caso difícil
  (família em segredo, HC com tribunal impetrado, precatório, MEI, herança, ex-ministro, prefixo 9...),
  separados pelo analista jurídico; nomes de pessoa física, MEI e advogados e números de OAB
  **mascarados**.
- ``stf_assuntos_amostra.json``: 106 processos reais da planilha do acervo do STF de 22/11/2026, só
  com Classe, Ramo e Assuntos (nenhuma parte), escolhidos para cobrir cada caminho da classificação.
"""

from pathlib import Path

PASTA = Path(__file__).resolve().parent


def ler(nome: str, encoding: str = "utf-8") -> str:
    return (PASTA / nome).read_text(encoding=encoding)
