"""Comandos ``python -m qijournal congresso atualizar|agenda|pagina|ler|leitura``.

- ``atualizar``: baixa o que mudou nas duas Casas, refaz as contas, grava
  ``data/congresso/`` e a aba ``edicoes/congresso.html``. Roda todo dia antes da
  edição (``daily.yml``); se uma fonte estiver fora do ar, o que já existe fica
  como está.
- ``agenda``: coleta o que está pautado na Câmara, no Senado e no Congresso (da segunda-feira
  da semana corrente até 13 dias depois) e grava ``data/congresso/agenda.json``, no formato de
  :mod:`qijournal.agenda.contrato`. Roda todo dia num passo próprio, antes do ``atualizar``
  (``daily.yml``), com prazo de rede curto. Fonte fora do ar mantém o que o arquivo já tinha; o
  comando devolve 0 com qualquer falha de fonte e não depende da legislatura configurada.
- ``pagina``: só refaz a aba a partir de ``data/congresso/`` (sem rede).
- ``ler``: a leitura diária dos projetos de lei novos, com a cadeia de IAs do jornal: lê as
  proposições que ainda não têm leitura e relê as que mudam a conta, em lotes cegos com
  perguntas de controle, e grava em ``data/congresso/leitura/`` (a guarda e o registro do
  dia, ``diario.json``). Roda todo dia num fluxo à parte (``leitura.yml``), depois da edição
  diária, que não espera a leitura; o que lê entra nas contas no ``atualizar`` seguinte. Sem
  chave de IA, não lê nada.
- ``leitura importar``: traz para ``data/congresso/leitura/`` as leituras de IA dos projetos
  de lei (uma por linha, em JSON), conferidas contra os códigos de ``config/congresso.yaml``.
- ``leitura exportar``: grava, em lotes cegos, as proposições que precisam da leitura de IA e
  ainda não a têm (as matérias votadas e as candidatas ao topo pela leitura de regras) e, com
  ``--segunda``, as que esperam a segunda leitura. Com ``--tudo``, todo o universo que a IA
  ainda não leu.

Códigos de saída: 0 sucesso (mesmo com avisos); 1 erro. ``ler`` só devolve 1 por configuração
quebrada: sem IA, com a IA fora do ar ou com qualquer outro erro, avisa e devolve 0. ``agenda``
devolve sempre 0: desligada, com fonte fora do ar, com saída inválida (que não é gravada) ou com
erro inesperado, o arquivo anterior fica como está e a edição segue.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from qijournal import pipeline
from qijournal.config import Config

from . import agenda, dados
from . import leitura, ler, regras as regras_mod
from .fontes import Coletor

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
    """Grava ``edicoes/congresso.html`` a partir de ``data/congresso/`` (``None`` sem dados). O botão
    "Tribunais" do cabeçalho acende quando a aba dos Tribunais existe (``edicoes/tribunais.html``)."""
    from qijournal.render import congresso as pagina  # import tardio: a renderização carrega os modelos
    from qijournal.render import tribunais as pagina_tribunais

    pagina_tribunais.marcar_publicada(config, Path(out_dir))
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
    try:
        resultado = dados.atualizar(config.root, out_dir / pipeline.DATA_DIR, Path(args.cache), opcoes)
    except dados.SemExercicio as exc:  # sem a lista de hoje e sem o último dia bom: o que está publicado fica como está
        log.error("Congresso: %s", exc)
        return EXIT_ERROR
    caminho = renderizar(config, out_dir)
    if caminho is not None:
        log.info("Aba Congresso gravada em %s", caminho)
    _resumo_do_actions(resultado, config)
    return EXIT_OK


def _resumo_da_agenda(rodada: agenda.Rodada | None, motivo: str = "") -> None:
    """A rodada da agenda no resumo do GitHub Actions."""
    caminho = (os.environ.get("GITHUB_STEP_SUMMARY") or "").strip()
    if not caminho:
        return
    linhas = ["### 🗓️ Agenda do Congresso", ""]
    if rodada is None:
        linhas.append(motivo)
    else:
        por_casa = ", ".join(f"{nome} {rodada.sessoes.get(chave, 0)}" for chave, nome in (("camara", "Câmara"), ("senado", "Senado"), ("congresso", "Congresso")))
        linhas += [
            "| | |",
            "|---|---|",
            f"| Sessões, reuniões e prazos na janela | {por_casa} |",
            f"| Itens guardados | {sum(rodada.itens.values())} |",
            f"| Pedidos de rede | {rodada.pedidos} em {rodada.segundos:.0f} s{' (prazo esgotado: coleta parcial)' if rodada.prazo_esgotado else ''} |",
            f"| Arquivo | {'gravado' if rodada.gravado else 'sem mudança'} ({rodada.bytes} bytes) |",
            "",
            "| Fonte | Situação |",
            "|---|---|",
            *[f"| {f.nome} | {'ok' if f.ok else 'falhou'}{f': {f.aviso}' if f.aviso else ''} |" for f in rodada.fontes],
        ]
        if not rodada.valido:
            linhas += ["", f"Saída fora do contrato, nada foi gravado: {rodada.erro}"]
    try:
        with open(caminho, "a", encoding="utf-8") as handle:
            handle.write("\n".join(linhas) + "\n\n")
    except OSError as exc:
        log.warning("Não foi possível escrever o resumo do GitHub Actions: %s", exc)


def cmd_agenda(args: argparse.Namespace, config: Config) -> int:
    """A coleta da agenda do Congresso (:mod:`qijournal.congresso.agenda`). Devolve sempre 0."""
    if not config.agenda_ativo:
        log.info("Agenda da semana desligada para a marca %s (ou por QIJ_NO_AGENDA); nada a coletar", config.brand.key)
        return EXIT_OK
    agora = pipeline.as_utc(args.now) if getattr(args, "now", None) else datetime.now(timezone.utc)
    try:
        rodada = agenda.atualizar(config.root, Path(args.dir) / pipeline.DATA_DIR, Path(args.cache), hoje=_hoje(args, config), agora=agora)
    except Exception as exc:  # noqa: BLE001 — acessório: a agenda que falha não derruba a edição
        log.exception("Agenda do Congresso: erro inesperado; o arquivo anterior fica como está")
        _resumo_da_agenda(None, f"Erro inesperado: {exc}")
        return EXIT_OK
    _resumo_da_agenda(rodada)
    return EXIT_OK


def cmd_pagina(args: argparse.Namespace, config: Config) -> int:
    caminho = renderizar(config, Path(args.dir))
    if caminho is None:
        log.error("Sem dados em data/congresso/: rode `python -m qijournal congresso atualizar` antes")
        return EXIT_ERROR
    log.info("Aba Congresso gravada em %s", caminho)
    return EXIT_OK


# ── a leitura diária dos projetos de lei novos ───────────────────────────────


def _resumo_da_leitura(registro: ler.Registro | None, motivo: str = "") -> None:
    """A rodada da leitura diária no resumo do GitHub Actions."""
    caminho = (os.environ.get("GITHUB_STEP_SUMMARY") or "").strip()
    if not caminho:
        return
    linhas = ["### 📖 Leitura dos projetos de lei", ""]
    if registro is None:
        linhas.append(motivo)
    else:
        linhas += [
            "| | |",
            "|---|---|",
            f"| Sem leitura antes da rodada | {registro.faltavam} (fora {registro.sem_ementa} sem ementa) |",
            f"| Primeiras leituras | {registro.primeiras} |",
            f"| Segundas leituras | {registro.segundas} ({registro.confirmadas} confirmadas, {registro.decididas} decididas pela terceira, {registro.instaveis} instáveis) |",
            f"| Segundas pela mesma IA que fez a primeira na rodada | {registro.mesmo_editor} |",
            f"| Terceiras por uma IA que se sabe que já tinha lido a proposição (da primeira leitura de outra rodada a guarda não diz a IA) | {registro.terceira_de_quem_leu} |",
            f"| Segundas de primeira leitura de outra rodada (a guarda não diz de que IA) | {registro.de_outra_rodada} |",
            f"| Lotes | {registro.lotes} ({registro.descartados} descartados pelos controles, {registro.falhas} sem resposta) |",
            f"| Controles certos | {registro.controles_certos} de {registro.controles_total} |",
            f"| Respostas fora do formato | {registro.invalidas} |",
            f"| Ficam para a próxima rodada | {registro.pendentes_primeiras} sem leitura, {registro.pendentes_segundas} à espera da segunda ({registro.pendentes_terceiras} delas já com a segunda, em outra faixa, à espera da terceira) |",
            f"| Chamadas à IA | {registro.chamadas} ({registro.tokens_entrada} tokens de entrada, {registro.tokens_saida} de saída) |",
            f"| Editores | {registro.provedor or 'nenhum'}{f' ({registro.modelo})' if registro.modelo else ''} |",
        ]
        if registro.motivo:
            linhas += ["", registro.motivo[:1].upper() + registro.motivo[1:] + "."]
        if registro.avisos:
            linhas += ["", "#### Avisos", *[f"- {aviso}" for aviso in registro.avisos]]
    try:
        with open(caminho, "a", encoding="utf-8") as handle:
            handle.write("\n".join(linhas) + "\n\n")
    except OSError as exc:
        log.warning("Não foi possível escrever o resumo do GitHub Actions: %s", exc)


def cmd_ler(args: argparse.Namespace, config: Config) -> int:
    """A rodada do dia da leitura dos projetos de lei (:mod:`qijournal.congresso.ler`)."""
    if not config.congresso_ativo:
        log.info("Congresso desligado para a marca %s; nada a ler", config.brand.key)
        return EXIT_OK
    if (os.environ.get(ler.DESLIGAR) or "").strip().lower() in {"1", "true", "yes", "sim", "on"}:
        log.info("Leitura diária desligada (%s); nada a ler", ler.DESLIGAR)
        _resumo_da_leitura(None, f"Desligada ({ler.DESLIGAR}).")
        return EXIT_OK
    try:
        plano = ler.preparar(config.root)
    except ler.ErroDeConfiguracao as exc:
        log.error("Leitura diária: configuração quebrada: %s", exc)
        _resumo_da_leitura(None, f"Configuração quebrada: {exc}")
        return EXIT_ERROR
    if not plano.limites.ligada:
        log.info("Leitura diária desligada em config/congresso.yaml (projeto_de_pais.leitura.diaria.ligada); nada a ler")
        _resumo_da_leitura(None, "Desligada no config.")
        return EXIT_OK
    agora = pipeline.as_utc(args.now) if getattr(args, "now", None) else datetime.now(timezone.utc)
    pasta = dados.pasta_de(Path(args.dir) / pipeline.DATA_DIR)
    try:
        registro = ler.executar(config, plano, pasta, Coletor(Path(args.cache)), _hoje(args, config), agora=agora.isoformat(timespec="seconds"), maximo=args.max)
    except Exception as exc:  # noqa: BLE001 — acessório: a leitura que falha não derruba a atualização nem a edição
        log.exception("Leitura diária: erro inesperado; o que já estava na guarda fica como está")
        _resumo_da_leitura(None, f"Erro inesperado: {exc}")
        return EXIT_OK
    for caminho in registro.gravados:
        log.info("Leitura diária: gravado %s", caminho)
    _resumo_da_leitura(registro)
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

    pautado = sub.add_parser("agenda", parents=[common], help="coleta o que está pautado na Câmara, no Senado e no Congresso (data/congresso/agenda.json)")
    pautado.add_argument("--dir", default=".", metavar="DIR", help="pasta publicada (padrão: .)")
    pautado.add_argument("--cache", default=CACHE_PADRAO, metavar="DIR", help=f"pasta do que foi baixado (padrão: {CACHE_PADRAO})")
    pautado.add_argument("--now", type=iso_datetime, metavar="ISO", help="data/hora de referência (sem fuso = UTC)")
    pautado.set_defaults(handler=cmd_agenda)

    pagina = sub.add_parser("pagina", parents=[common], help="só refaz a aba a partir de data/congresso/ (sem rede)")
    pagina.add_argument("--dir", default=".", metavar="DIR", help="pasta publicada (padrão: .)")
    pagina.set_defaults(handler=cmd_pagina)

    diaria = sub.add_parser("ler", parents=[common], help="a leitura diária: lê por IA os projetos de lei novos e relê os que mudam a conta")
    diaria.add_argument("--dir", default=".", metavar="DIR", help="pasta publicada (padrão: .)")
    diaria.add_argument("--cache", default=CACHE_PADRAO, metavar="DIR", help=f"pasta do que foi baixado (padrão: {CACHE_PADRAO})")
    diaria.add_argument(
        "--max", type=int, default=None, metavar="N", help="teto de leituras desta rodada, primeiras e segundas somadas (padrão: os tetos do config; 0: só diz o que leria)"
    )
    diaria.add_argument("--now", type=iso_datetime, metavar="ISO", help="data/hora de referência (sem fuso = UTC)")
    diaria.set_defaults(handler=cmd_ler)
    _parser_da_leitura(sub, common, iso_datetime)


# ── a leitura dos projetos de lei (a régua "Projeto de país") ────────────────


def _regua_ou_erro(config: Config) -> tuple[regras_mod.Regras, regras_mod.ProjetoDePais | None]:
    regras = regras_mod.carregar(config.root)
    if regras.projeto_de_pais is None:
        log.error("config/congresso.yaml sem o bloco projeto_de_pais: não há régua para a leitura dos projetos")
    return regras, regras.projeto_de_pais


def cmd_leitura_importar(args: argparse.Namespace, config: Config) -> int:
    """Traz as leituras dos arquivos (uma por linha, em JSON) para ``data/congresso/leitura/``."""
    _regras, regua = _regua_ou_erro(config)
    if regua is None:
        return EXIT_ERROR
    arquivos = [Path(a) for a in args.arquivos]
    ausentes = [str(a) for a in arquivos if not a.is_file()]
    if ausentes:
        log.error("Leitura: arquivo(s) não encontrado(s): %s", ", ".join(ausentes))
        return EXIT_ERROR
    # O ano das proposições da Câmara (que decide o arquivo) vem dos mapas dos lotes (os de
    # --mapa ou, sem ele, os que estiverem ao lado dos arquivos importados) e, para as
    # matérias votadas, das fichas já publicadas em data/congresso/.
    mapas = [Path(m) for m in args.mapa] if args.mapa else sorted({a.resolve().parent for a in arquivos})
    publicado = dados.pasta_de(Path(args.dir) / pipeline.DATA_DIR)
    pasta = leitura.pasta_da_guarda(publicado)
    resultado = leitura.importar(pasta, regua, arquivos, anos={**leitura.anos_das_materias(publicado), **leitura.anos_dos_mapas(mapas)})
    log.info(
        "Leitura: %d linha(s): %d aceita(s) (%d proposição(ões) nova(s), %d trocada(s), %d igual(is)) e %d recusada(s); %d arquivo(s) gravado(s) em %s",
        resultado.linhas, resultado.aceitas, resultado.novas, resultado.trocadas, resultado.iguais, resultado.recusadas, len(resultado.gravados), pasta,
    )  # fmt: skip
    for caminho in resultado.gravados:
        log.info("Leitura: gravado %s", caminho)
    for motivo, quantas in resultado.motivos.most_common():
        log.warning("Leitura: %d linha(s) recusada(s): %s", quantas, motivo)
    for exemplo in resultado.exemplos:
        log.warning("Leitura: recusada %s", exemplo)
    if resultado.ignorados:
        log.warning("Leitura: arquivo(s) da guarda de outra versão das instruções, que não valem mais: %s", ", ".join(resultado.ignorados))
    return EXIT_ERROR if resultado.linhas and not resultado.aceitas else EXIT_OK


def _por_casa(lista: list[leitura.Item]) -> str:
    contas = leitura.contar(lista)
    return "; ".join(
        f"{casa}: {c['total']} (perfil {c[leitura.PERFIL]}, espera {c[leitura.ESPERA]}, voto {c[leitura.VOTO]}; sem ementa {c['sem_ementa']})" for casa, c in sorted(contas.items())
    ) or "nenhuma"


def _motivos(pares: list[tuple[leitura.Item, tuple[str, ...]]]) -> dict[str, int]:
    conta: dict[str, int] = {}
    for _item, motivos in pares:
        for motivo in motivos:
            conta[motivo] = conta.get(motivo, 0) + 1
    return conta


def cmd_leitura_exportar(args: argparse.Namespace, config: Config) -> int:
    """Grava em ``--saida`` os lotes cegos do que precisa da leitura de IA e ainda não a tem
    (``lote-NNNN``) e, com ``--segunda``, do que espera a segunda leitura (``segunda-NNNN``)."""
    regras, regua = _regua_ou_erro(config)
    if regua is None:
        return EXIT_ERROR
    pasta = dados.pasta_de(Path(args.dir) / pipeline.DATA_DIR)
    avisos: list[str] = []
    itens = leitura.coletar(regras, pasta, Coletor(Path(args.cache)), _hoje(args, config), projetos_desde=config.congresso.projetos_desde, avisos=avisos)
    leitor = leitura.abrir(config.root, pasta, regras, avisos=avisos)
    assert leitor is not None  # a régua existe
    guarda = leitor.guarda
    nomes_dos_temas = {t.id: t.nome for t in regras.temas}
    saida = Path(args.saida)

    universo = [i for casa in sorted(itens) for i in itens[casa].values()]
    log.info("Leitura: o universo (uma proposição pode estar em mais de um): %s", _por_casa(universo))
    for casa in sorted(itens):
        c = leitura.cobertura(regua, (leitor.de(i) for i in itens[casa].values()))
        na_guarda = guarda.da_casa(casa)
        log.info(
            "Leitura: cobertura %s: %d de %d com leitura (%d por regras, %d por IA, das quais %d confirmadas); %d sem leitura. Na guarda: %d leitura(s) de IA, %d delas fora do universo de hoje",
            casa, c["lidos"], c["total"], c[leitura.REGRA], c[leitura.IA], c["confirmados"], c["sem_leitura"], len(na_guarda), sum(1 for k in na_guarda if k not in itens[casa]),
        )  # fmt: skip
    precisam = [par for casa in sorted(itens) for par in leitura.para_ia(regua, itens[casa], guarda, leitor.regras)]
    motivos = _motivos(precisam)
    log.info(
        "Leitura: precisam da leitura de IA e ainda não a têm: %s (votada %d, candidata %d)",
        _por_casa([item for item, _m in precisam]), motivos.get(leitura.VOTADA, 0), motivos.get(leitura.CANDIDATA, 0),
    )  # fmt: skip
    if args.tudo:
        # Tudo o que a IA ainda não leu, em três levas: o que precisa dela, o perfil de quem
        # propõe (a legislatura) e, por fim, o estoque antigo que ainda tramita.
        primeiras = {(item.casa, item.chave) for item, _m in precisam}
        falta = [i for casa in sorted(itens) for i in leitura.faltam(itens[casa], guarda)]
        lotes = leitura.gravar_lotes(
            saida, falta, nomes_dos_temas, tamanho=args.lote, leva=lambda item: 0 if (item.casa, item.chave) in primeiras else 1 if leitura.PERFIL in item.universos else 2
        )
        log.info("Leitura: --tudo: todo o universo que a IA ainda não leu: %s", _por_casa(falta))
    else:
        lotes = leitura.gravar_lotes(saida, [item for item, _m in precisam], nomes_dos_temas, tamanho=args.lote)
    log.info("Leitura: %d lote(s) de até %d em %s (lote-NNNN.txt e lote-NNNN.map.json)", lotes, args.lote, saida)
    if args.segunda:
        pedem = [par for casa in sorted(itens) for par in leitura.para_segunda(regua, itens[casa], guarda)]
        motivos = _motivos(pedem)
        # Primeiro o que muda a conta (o peso do topo e as votadas); depois, as de pouca confiança.
        so_duvida = {(item.casa, item.chave) for item, m in pedem if set(m) == {leitura.POUCA_CONFIANCA}}
        lotes = leitura.gravar_lotes(
            saida, [item for item, _m in pedem], nomes_dos_temas, tamanho=args.lote, nome="segunda", prefixo="s", leva=lambda item: int((item.casa, item.chave) in so_duvida)
        )
        log.info(
            "Leitura: esperam a segunda leitura: %s (peso alto %d, pouca confiança %d, votada %d); %d lote(s) em %s (segunda-NNNN.txt e segunda-NNNN.map.json); as %d primeiras são as de peso alto e as votadas",
            _por_casa([item for item, _m in pedem]), motivos.get(leitura.PESO_ALTO, 0), motivos.get(leitura.POUCA_CONFIANCA, 0), motivos.get(leitura.VOTADA, 0), lotes, saida, len(pedem) - len(so_duvida),
        )  # fmt: skip
    if guarda.ignorados:
        log.warning("Leitura: arquivo(s) da guarda de outra versão das instruções, que não valem mais: %s", ", ".join(guarda.ignorados))
    for aviso in avisos:
        log.warning("Leitura: %s", aviso)
    return EXIT_OK


def _parser_da_leitura(sub: argparse._SubParsersAction, common: argparse.ArgumentParser, iso_datetime) -> None:
    grupo = sub.add_parser("leitura", help="a leitura dos projetos de lei (régua \"Projeto de país\"): importar leituras de IA e exportar o que falta mandar à IA")
    acoes = grupo.add_subparsers(dest="leitura_command", required=True, metavar="ação")

    importar = acoes.add_parser("importar", parents=[common], help="traz leituras de IA (uma por linha, em JSON) para data/congresso/leitura/")
    importar.add_argument("--dir", default=".", metavar="DIR", help="pasta publicada (padrão: .)")
    importar.add_argument(
        "--mapa", action="append", metavar="CAMINHO", help="mapa de lote (*.map.json) ou pasta com eles: dá o ano das proposições da Câmara (padrão: a pasta de cada arquivo)"
    )
    importar.add_argument("arquivos", nargs="+", metavar="ARQUIVO.jsonl", help="leituras, uma por linha: {\"casa\":\"cd\",\"id\":\"2491234\",\"a\":…,\"r\":1}")
    importar.set_defaults(handler=cmd_leitura_importar)

    exportar = acoes.add_parser("exportar", parents=[common], help="grava em lotes cegos o que precisa da leitura de IA e ainda não a tem (votadas e candidatas ao topo)")
    exportar.add_argument("--dir", default=".", metavar="DIR", help="pasta publicada (padrão: .)")
    exportar.add_argument("--cache", default=CACHE_PADRAO, metavar="DIR", help=f"pasta do que foi baixado (padrão: {CACHE_PADRAO})")
    exportar.add_argument("--saida", required=True, metavar="PASTA", help="onde gravar os lotes")
    exportar.add_argument("--lote", type=int, default=leitura.LOTE, metavar="N", help=f"proposições por lote (padrão: {leitura.LOTE})")
    exportar.add_argument(
        "--tudo", action="store_true", help="exporta todo o universo que a IA ainda não leu, não só o que precisa dela (as votadas e as candidatas saem primeiro)"
    )
    exportar.add_argument("--segunda", action="store_true", help="exporta também as que esperam a segunda leitura (segunda-NNNN.*)")
    exportar.add_argument("--now", type=iso_datetime, metavar="ISO", help="data/hora de referência (sem fuso = UTC)")
    exportar.set_defaults(handler=cmd_leitura_exportar)
