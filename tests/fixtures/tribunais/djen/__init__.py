"""Amostras do DJEN (``comunicaapi.pje.jus.br``) para os testes de ``qijournal/tribunais/djen.py``.

A API recusa (403 geográfico, CloudFront) quem está fora do Brasil, e nada aqui foi capturado dela:

- ``estrutura_terceiros.json``: a estrutura real de um item de resposta, copiada da amostra de
  terceiros (projeto juscraper, licença MIT, ``tests/comunica_cnj/samples/listar_comunicacoes/
  single_page.json``, commit 871d1413 de 17/11/2026), de um TJ, já anonimizada na origem; aqui o
  número do processo também foi trocado.
- ``stj_2026-11-20_p1.json`` e ``stj_2026-11-20_p2.json``: **sintéticas**, no formato acima, com o
  que se espera do STJ (gabinete no ``nomeOrgao``, Turma, coordenadoria, Presidência, secretaria;
  decisão, acórdão, pauta, despacho; um processo em segredo; uma publicação cancelada; um item
  repetido entre as páginas). Os ministros são os da composição (autoridades no cargo); as partes
  pessoa física são inventadas ("CARLOS EXEMPLO SOUZA", "MARIA MODELO" com CPF de MEI) e os
  números de processo têm dígito verificador 00 (não existem).
- ``vazio.json``: dia sem publicação. ``filtro_ignorado.json``: a API devolvendo outro tribunal.

Trocar as sintéticas pela amostra mascarada da primeira sonda no Brasil
(``python -m qijournal tribunais djen --sondar`` grava em ``<cache>/djen/sonda/``).
"""
