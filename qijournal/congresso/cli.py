"""Comandos ``python -m qijournal congresso atualizar|pagina``.

- ``atualizar``: baixa o que mudou nas duas Casas, refaz as contas, grava
  ``data/congresso/`` e a aba ``edicoes/congresso.html``. Roda todo dia antes da
  edição (``daily.yml``); se uma fonte estiver fora do ar, o que já existe fica
  como está.
- ``pagina``: só refaz a aba a partir de ``data/congresso/`` (sem rede).

Códigos de saída: 0 sucesso (mesmo com avisos); 1 erro.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from qijournal import pipeline
from qijournal.config import Config

from . import dados

log = logging.getLogger(__name__)

EXIT_OK, EXIT_ERROR = 0, 1
CACHE_PADRAO = ".cache/congresso"


def _hoje(args: argparse.Namespace, config: Config) -> str:
    agora = pipeline.as_utc(args.now) if getattr(args, "now", None) else datetime.now(timezone.utc)
    return pipeline.local_date(agora, config.site.timezone).isoformat()


def _resumo_do_actions(resultado: dados.Resultado, config: Config) -> None:
    caminho = (os.environ.get("GITHUB_STEP_SUMMARY") or "").strip()
    if not caminho:
        return
    linhas = [
        "### 🏛️ Congresso",
        "",
        "| | |",
        "|---|---|",
        f"| Deputados | {resultado.pessoas.get('cd', 0)} |",
        f"| Senadores | {resultado.pessoas.get('sf', 0)} |",
        f"| Votações nominais (Câmara / Senado) | {resultado.votacoes.get('cd', 0)} / {resultado.votacoes.get('sf', 0)} |",
        f"| Arquivos gravados | {resultado.gravados} |",
        f"| Requisições | {resultado.requisicoes} ({resultado.megabytes:.0f} MB) |",
        "",
        f"[Aba Congresso]({config.site.base_url}edicoes/congresso.html)",
    ]
    if resultado.avisos:
        linhas += ["", "#### Avisos", *[f"- {aviso}" for aviso in resultado.avisos]]
    try:
        with open(caminho, "a", encoding="utf-8") as handle:
            handle.write("\n".join(linhas) + "\n\n")
    except OSError as exc:
        log.warning("Não foi possível escrever o resumo do GitHub Actions: %s", exc)


def renderizar(config: Config, out_dir: Path) -> Path | None:
    """Grava ``edicoes/congresso.html`` a partir de ``data/congresso/`` (``None`` sem dados)."""
    from qijournal.render import congresso as pagina  # import tardio: a renderização carrega os modelos

    html = pagina.render_congresso_page(config, Path(out_dir) / pipeline.DATA_DIR, home_href="../", archive_href="./")
    if html is None:
        return None
    return pipeline.write_atomic(Path(out_dir) / pipeline.ARCHIVE_DIR / pagina.PAGINA, html)


def cmd_atualizar(args: argparse.Namespace, config: Config) -> int:
    if not config.congresso_ativo:
        log.info("Congresso desligado para a marca %s; nada a fazer", config.brand.key)
        return EXIT_OK
    out_dir = Path(args.dir)
    opcoes = dados.Opcoes(
        hoje=_hoje(args, config),
        completo=args.completo,
        projetos_desde=config.congresso.projetos_desde,
        noticias_dias=config.congresso.noticias_dias,
    )
    resultado = dados.atualizar(config.root, out_dir / pipeline.DATA_DIR, Path(args.cache), opcoes)
    caminho = renderizar(config, out_dir)
    if caminho is not None:
        log.info("Aba Congresso gravada em %s", caminho)
    _resumo_do_actions(resultado, config)
    return EXIT_OK


def cmd_pagina(args: argparse.Namespace, config: Config) -> int:
    caminho = renderizar(config, Path(args.dir))
    if caminho is None:
        log.error("Sem dados em data/congresso/: rode `python -m qijournal congresso atualizar` antes")
        return EXIT_ERROR
    log.info("Aba Congresso gravada em %s", caminho)
    return EXIT_OK


def add_parser(commands: argparse._SubParsersAction, common: argparse.ArgumentParser, iso_datetime) -> None:
    congresso = commands.add_parser("congresso", help="analítico do Congresso: dados da Câmara e do Senado e a aba Congresso")
    sub = congresso.add_subparsers(dest="congresso_command", required=True, metavar="ação")

    atualizar = sub.add_parser("atualizar", parents=[common], help="baixa o que mudou, refaz as contas e a aba")
    atualizar.add_argument("--dir", default=".", metavar="DIR", help="pasta publicada (padrão: .)")
    atualizar.add_argument("--cache", default=CACHE_PADRAO, metavar="DIR", help=f"pasta do que foi baixado (padrão: {CACHE_PADRAO})")
    atualizar.add_argument(
        "--completo", action="store_true", help="refaz também os anos fechados e relê todos os anos de proposições"
    )
    atualizar.add_argument("--now", type=iso_datetime, metavar="ISO", help="data/hora de referência (sem fuso = UTC)")
    atualizar.set_defaults(handler=cmd_atualizar)

    pagina = sub.add_parser("pagina", parents=[common], help="só refaz a aba a partir de data/congresso/ (sem rede)")
    pagina.add_argument("--dir", default=".", metavar="DIR", help="pasta publicada (padrão: .)")
    pagina.set_defaults(handler=cmd_pagina)
