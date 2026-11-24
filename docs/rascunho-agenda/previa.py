"""Prévia local da home e da cópia arquivada de uma edição já publicada.

Uso (a partir da raiz do repositório):

    python <este arquivo> 2026-11-23 <pasta de saída> [--marca tech]

Lê ``data/AAAA-MM-DD.json`` do repositório (não escreve nada nele) e grava,
na pasta de saída, ``index.html`` (a home) e ``edicoes/AAAA-MM-DD.html`` (a
cópia arquivada), renderizados com o código atual da árvore de trabalho.
As demais páginas de ``edicoes/`` e ``data/`` são ligadas por symlink para os
links da prévia abrirem.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RAIZ = Path("/home/user/TriangulaTech")
sys.path.insert(0, str(RAIZ))

from qijournal.config import load_config  # noqa: E402
from qijournal.models import Edition  # noqa: E402
from qijournal.render import web  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    dia, saida = sys.argv[1], Path(sys.argv[2])
    marca = "tech"
    if "--marca" in sys.argv:
        marca = sys.argv[sys.argv.index("--marca") + 1]
    env = dict(os.environ)
    env["QIJ_BRAND"] = marca
    config = load_config(RAIZ, env)
    dados = json.loads((RAIZ / "data" / f"{dia}.json").read_text(encoding="utf-8"))
    edition = Edition.from_dict(dados)
    saida.mkdir(parents=True, exist_ok=True)
    (saida / "edicoes").mkdir(exist_ok=True)
    home = web.render_edition_page(edition, config, home_href="./", archive_href="edicoes/")
    copia = web.render_edition_page(edition, config, home_href="../", archive_href="./", is_archive=True)
    (saida / "index.html").write_text(home, encoding="utf-8")
    (saida / "edicoes" / f"{dia}.html").write_text(copia, encoding="utf-8")
    # symlinks para o resto do site (somente leitura)
    for nome in os.listdir(RAIZ / "edicoes"):
        alvo = saida / "edicoes" / nome
        if nome == f"{dia}.html" or alvo.exists() or alvo.is_symlink():
            continue
        alvo.symlink_to(RAIZ / "edicoes" / nome)
    for pasta in ("data", "assets"):
        alvo = saida / pasta
        if not (alvo.exists() or alvo.is_symlink()) and (RAIZ / pasta).exists():
            alvo.symlink_to(RAIZ / pasta)
    print(f"home: {saida / 'index.html'} ({len(home.encode()) / 1024:.0f} KB)")
    print(f"cópia: {saida / 'edicoes' / (dia + '.html')} ({len(copia.encode()) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
