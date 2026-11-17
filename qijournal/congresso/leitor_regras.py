"""O leitor de regras da régua "Projeto de país": o ponto de encaixe (ainda não lê).

Um leitor determinístico, sem IA, que marca os mesmos seis campos da régua (e o alerta de
iniciativa) a partir do que o leitor de IA recebe de cada proposição: o tipo, a ementa, a
ementa detalhada, as palavras-chave da indexação e o tema oficial. Lê todas as proposições a
cada atualização; a leitura dele não é gravada, e a de IA, quando existe, vale sobre ela
(:func:`qijournal.congresso.leitura.leitura_de`).

O contrato com quem faz as contas é um só: :func:`carregar` devolve um
:data:`~qijournal.congresso.leitura.LeitorDeRegras`, isto é, uma função
``(item: leitura.Item) -> leitura.Leitura | None`` em que a leitura devolvida traz

- ``codigos``: um código por campo da régua, na ordem do YAML (``a, e, m, ct, is, co``), todos
  do YAML, ou "nd" onde as regras não decidem;
- ``iniciativa`` (0 ou 1), ``confianca`` (1 a 3) e, em ``porque``, a regra que decidiu (o
  trecho ou a palavra-chave que casou);
- ``fonte`` = ``leitura.REGRA``;

ou ``None`` quando não há o que ler (proposição sem ementa).

Por enquanto não há regras: o leitor devolve sempre "sem leitura", e só o que a IA leu entra
nas contas. As regras ficarão em ``config/leitura_regras.yaml``.
"""

from __future__ import annotations

from pathlib import Path

from . import leitura
from .regras import ProjetoDePais


def carregar(regua: ProjetoDePais, root: Path) -> leitura.LeitorDeRegras:
    """O leitor de regras, pronto para ler: ``regua`` é a régua do config e ``root`` a raiz do
    repositório (as regras ficam em ``<root>/config/``). Ainda sem regras: não lê nada."""
    return leitura.sem_leitor
