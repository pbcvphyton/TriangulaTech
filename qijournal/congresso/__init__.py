"""Analítico do Congresso: quem está sentado na Câmara e no Senado, e o que faz lá.

O pacote lê os dados abertos das duas Casas, monta uma base compacta e publica,
em ``data/congresso/``, o que a aba **Congresso** (``edicoes/congresso.html``) e
as fichas dentro das matérias da edição mostram:

- :mod:`.fontes`: a coleta (arquivos em lote e API da Câmara, APIs do Senado),
  com cache em disco e ``fetch`` injetável;
- :mod:`.base`: a base de votações nominais, um arquivo por Casa e ano
  (``data/congresso/base/``); os anos fechados nunca mudam;
- :mod:`.perfil`: o perfil de cada parlamentar pelo que ele votou: presença,
  governismo, com que polo vota, fidelidade ao partido e os votos fora do padrão;
- :mod:`.projetos`: o que cada um propõe e relata, o que está parado e a fila da
  Casa (o que espera, com quem, e o que passou na frente);
- :mod:`.jornal`: o cruzamento com o banco do jornal (``data/busca/``): quem é
  citado nas notícias e nas matérias;
- :mod:`.dados`: a montagem e a gravação de ``data/congresso/``;
- :mod:`.cli`: ``python -m qijournal congresso atualizar``.

Nada aqui usa IA: tudo sai de contas sobre registros oficiais, e cada número
aponta para a votação ou a proposição de onde veio.
"""

VERSAO = 1  # formato de data/congresso/: outro número refaz tudo
