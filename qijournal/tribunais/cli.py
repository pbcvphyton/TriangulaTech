"""Comandos ``python -m qijournal tribunais atualizar|pagina``.

- ``atualizar --dir DIR --cache CACHE [--completo] [--so stj|stf]``: coleta as fontes do STJ e do
  STF (e o DataJud, se ligado), refaz as contas, grava ``data/tribunais/`` e a aba
  ``edicoes/tribunais.html``. Roda no fluxo próprio (``tribunais.yml``). Fonte que falha vira
  aviso (no ``meta.json``, no log e no resumo do GitHub Actions) e o comando sai 0; só erro
  inesperado de programa sai diferente de 0. ``--completo`` refaz as séries (as atas dos últimos
  30 dias e as íntegras disponíveis) e confere a versão da TPU; ``--so`` atualiza um tribunal só (o
  outro fica como está).
- ``pagina --dir DIR``: só refaz a aba a partir de ``data/tribunais/`` (sem rede).

Com a aba desligada para a marca (``config/site.yaml`` → ``tribunais``) ou ``QIJ_NO_TRIBUNAIS``,
os dois comandos não fazem nada e saem 0.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from qijournal import pipeline
from qijournal.config import Config

from . import dados

log = logging.getLogger(__name__)

EXIT_OK, EXIT_ERROR = 0, 1
CACHE_PADRAO = ".cache/tribunais"
ESTADOS = {True: "✅", False: "⚠️", None: "—"}


def _agora(args: argparse.Namespace) -> datetime:
    return pipeline.as_utc(args.now) if getattr(args, "now", None) else datetime.now(UTC)


def renderizar(config: Config, out_dir: Path) -> Path | None:
    """Grava ``edicoes/tribunais.html`` a partir de ``data/tribunais/`` (``None`` sem dados, com a
    aba desligada ou acima do teto de tamanho). Acende o botão cruzado do Congresso quando a aba
    dele existe."""
    # import tardio: a renderização carrega os modelos
    from qijournal.render import tribunais as pagina

    return pagina.gravar_pagina(Path(out_dir), config)


def _br(iso: str | None) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else "—"


def _numero(n: object) -> str:
    return f"{n:,}".replace(",", ".") if isinstance(n, int) else "—"


def resumo_markdown(resultado: dados.Resultado, config: Config) -> str:
    """O resumo da execução para o ``$GITHUB_STEP_SUMMARY``: uma linha por fonte e os números."""
    linhas = ["### ⚖️ Tribunais", "", "| Fonte | Estado | Dados de | Registros | Aviso |", "|---|---|---|---:|---|"]
    for f in resultado.fontes.values():
        aviso = str(f.get("aviso") or "").replace("|", "/")
        linhas.append(f"| {f.get('nome')} | {ESTADOS.get(f.get('ok'), '—')} | {_br(f.get('data_dados'))} | {_numero(f.get('registros'))} | {aviso} |")
    if resultado.numeros:
        linhas += ["", "| Tribunal | Acervo | Na grade | Na mesa | Ministros | Dossiês |", "|---|---:|---:|---:|---:|---:|"]
        for trib, n in resultado.numeros.items():
            linhas.append(f"| {trib.upper()} ({_br(n.get('data_dados'))}) | {_numero(n.get('processos'))} | {_numero(n.get('na_grade'))} | "
                          f"{_numero(n.get('na_mesa'))} | {_numero(n.get('ministros'))} | {_numero(n.get('dossies'))} |")
    refeitos = ", ".join(t.upper() for t in resultado.refeitos) or "nenhum (ficam os da última coleta boa)"
    linhas += [
        "",
        (f"Painéis refeitos: {refeitos}. Arquivos gravados: {resultado.gravados}; apagados: {resultado.apagados}. "
         f"Requisições: {resultado.requisicoes} ({resultado.megabytes:.0f} MB) em {resultado.segundos / 60:.1f} min."),
    ]
    if resultado.datajud:
        linhas += ["", resultado.datajud]
    linhas += ["", f"[Aba Tribunais]({config.site.base_url}edicoes/tribunais.html)"]
    if resultado.avisos:
        linhas += ["", "#### Avisos", *[f"- {aviso}" for aviso in resultado.avisos]]
    return "\n".join(linhas) + "\n\n"


def _resumo_do_actions(resultado: dados.Resultado, config: Config) -> None:
    caminho = (os.environ.get("GITHUB_STEP_SUMMARY") or "").strip()
    if not caminho:
        return
    try:
        with open(caminho, "a", encoding="utf-8") as handle:
            handle.write(resumo_markdown(resultado, config))
    except OSError as exc:
        log.warning("Não foi possível escrever o resumo do GitHub Actions: %s", exc)


def cmd_atualizar(args: argparse.Namespace, config: Config) -> int:
    if not config.tribunais_ativo:
        log.info("Tribunais desligado para a marca %s (ou QIJ_NO_TRIBUNAIS); nada a fazer", config.brand.key)
        return EXIT_OK
    out_dir = Path(args.dir)
    agora = _agora(args)
    opcoes = dados.Opcoes(
        hoje=pipeline.local_date(agora, config.site.timezone).isoformat(),
        agora=agora,
        completo=args.completo,
        so=args.so,
    )
    resultado = dados.atualizar(config.root, out_dir / pipeline.DATA_DIR, Path(args.cache), opcoes)
    for aviso in resultado.avisos:
        log.warning("Tribunais: %s", aviso)
    log.info(
        "Tribunais: painéis refeitos: %s; %d arquivo(s) gravado(s), %d apagado(s); %d requisições (%.0f MB) em %.1f min",
        ", ".join(resultado.refeitos) or "nenhum", resultado.gravados, resultado.apagados, resultado.requisicoes, resultado.megabytes,
        resultado.segundos / 60,
    )  # fmt: skip
    caminho = renderizar(config, out_dir)
    if caminho is not None:
        log.info("Aba Tribunais gravada em %s", caminho)
    else:
        log.warning("Tribunais: a aba não foi gravada (sem dados em data/tribunais/ ou acima do teto); fica a anterior")
    _resumo_do_actions(resultado, config)
    return EXIT_OK


def cmd_pagina(args: argparse.Namespace, config: Config) -> int:
    if not config.tribunais_ativo:
        log.info("Tribunais desligado para a marca %s (ou QIJ_NO_TRIBUNAIS); a aba não é gravada", config.brand.key)
        return EXIT_OK
    caminho = renderizar(config, Path(args.dir))
    if caminho is None:
        log.error("Sem dados válidos em data/tribunais/ (ou página acima do teto): rode `python -m qijournal tribunais atualizar` antes")
        return EXIT_ERROR
    log.info("Aba Tribunais gravada em %s", caminho)
    return EXIT_OK


def cmd_djen(args: argparse.Namespace, config: Config) -> int:
    """``tribunais djen``: o DJEN do STJ e do STF (só roda no Brasil: a API recusa quem está fora).
    ``--sondar`` faz poucos pedidos e diz o que a API dá; sem ele, coleta os dias que faltam e grava
    ``data/tribunais/djen.json``. Recusa e falha de fonte viram aviso e o comando sai 0."""
    if not config.tribunais_ativo:
        log.info("Tribunais desligado para a marca %s (ou QIJ_NO_TRIBUNAIS); nada a fazer", config.brand.key)
        return EXIT_OK
    from . import djen  # import tardio: só este comando usa

    agora = _agora(args)
    if args.sondar:
        relatorio = djen.sondar(config.root, Path(args.cache), agora=agora, dia=args.dia)
        print(djen.relatorio_da_sonda(relatorio))
        return EXIT_OK
    resultado = djen.executar(config.root, Path(args.dir), Path(args.cache), agora=agora, completo=args.completo, so=args.so,
                              acervo=[Path(a) for a in args.acervo], sem_acervo=args.sem_acervo)
    if not resultado.ativo:
        log.info("DJEN desligado em config/tribunais.yaml (djen.ativo); nada a fazer")
        return EXIT_OK
    for aviso in resultado.avisos:
        log.warning("%s", aviso)
    coletados = "; ".join(f"{t.upper()}: {len(d)} dia(s)" for t, d in resultado.coletados.items()) or "nenhum dia novo"
    log.info("DJEN: %s; %d requisições em %.1f min; djen.json %s", coletados, resultado.requisicoes, resultado.segundos / 60,
             f"gravado em {resultado.caminho}" if resultado.gravado else "sem mudança")
    return EXIT_OK


def add_parser(commands: argparse._SubParsersAction, common: argparse.ArgumentParser, iso_datetime) -> None:
    tribunais = commands.add_parser("tribunais", help="analítico dos Tribunais: acervo do STF e do STJ e a aba Tribunais")
    sub = tribunais.add_subparsers(dest="tribunais_command", required=True, metavar="ação")

    atualizar = sub.add_parser("atualizar", parents=[common], help="coleta as fontes, refaz as contas e a aba")
    atualizar.add_argument("--dir", default=".", metavar="DIR", help="pasta publicada (padrão: .)")
    atualizar.add_argument("--cache", default=CACHE_PADRAO, metavar="DIR", help=f"pasta do que foi baixado, fora do git (padrão: {CACHE_PADRAO})")
    atualizar.add_argument("--completo", action="store_true", help="refaz as séries (atas dos últimos 30 dias, íntegras disponíveis) e confere a TPU")
    atualizar.add_argument("--so", choices=("stj", "stf"), default=None, help="atualiza só um tribunal (o outro fica como está)")
    atualizar.add_argument("--now", type=iso_datetime, metavar="ISO", help="data/hora de referência (sem fuso = UTC)")
    atualizar.set_defaults(handler=cmd_atualizar)

    pagina = sub.add_parser("pagina", parents=[common], help="só refaz a aba a partir de data/tribunais/ (sem rede)")
    pagina.add_argument("--dir", default=".", metavar="DIR", help="pasta publicada (padrão: .)")
    pagina.set_defaults(handler=cmd_pagina)

    djen = sub.add_parser("djen", parents=[common], help="DJEN (CNJ): as publicações do STJ e do STF por ministro (roda no Brasil)")
    djen.add_argument("--dir", default=".", metavar="DIR", help="pasta publicada; grava DIR/data/tribunais/djen.json (padrão: .)")
    djen.add_argument("--cache", default=CACHE_PADRAO, metavar="DIR", help=f"pasta do que foi coletado, fora do git (padrão: {CACHE_PADRAO})")
    djen.add_argument("--sondar", action="store_true", help="poucos pedidos: a API responde? STJ e STF publicam? grava a amostra mascarada")
    djen.add_argument("--dia", default=None, metavar="AAAA-MM-DD", help="com --sondar: o dia a sondar (padrão: o último dia útil)")
    djen.add_argument("--completo", action="store_true", help="coleta todos os dias que faltam na janela (sem o teto por execução)")
    djen.add_argument("--so", choices=("stj", "stf"), default=None, help="só um tribunal")
    djen.add_argument("--acervo", action="append", default=[], metavar="DIR", help="pasta com retratos do acervo (retratos/<trib>-AAAA-MM-DD.tsv.gz)")
    djen.add_argument("--sem-acervo", action="store_true", help="não casa com o acervo (sai sem a mesa) e não baixa o acervo")
    djen.add_argument("--now", type=iso_datetime, metavar="ISO", help="data/hora de referência (sem fuso = UTC)")
    djen.set_defaults(handler=cmd_djen)
