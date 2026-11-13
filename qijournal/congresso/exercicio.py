"""Quando cada deputado esteve em exercício (``data/congresso/base/cd-exercicio.json``).

Os arquivos de votos da Câmara só listam quem votou: para saber se quem não
aparece **faltou** ou **não era deputado naquele dia** (suplente que ainda não
tinha assumido, titular licenciado para ser ministro), é preciso o histórico do
mandato (``/deputados/<id>/historico``): cada entrada diz a situação a partir
de uma data ("Exercício", "Licença", "VACANCIA", "FIM_MANDATO"…).

Um intervalo vai da entrada em exercício até a saída seguinte; aberto (fim
vazio) quando o deputado está em exercício hoje. O histórico de quem já saiu
não muda: só é consultado de novo quem entrou ou saiu da lista dos que estão em
exercício (uma chamada por dia traz essa lista).

Quem **preside a Câmara** só vota em caso de empate: o período em que cada um foi
presidente (``/legislaturas/<n>/mesa``) fica no mesmo arquivo, e as votações dele
não entram na conta da presença.

No Senado isto não é preciso: cada votação registra todos os senadores do dia,
com o motivo de quem não votou e quem presidia.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .. import net, text
from . import VERSAO, fontes
from .fontes import Coletor

log = logging.getLogger(__name__)

ARQUIVO = "cd-exercicio.json"
EXERCICIO = "exercicio"
ABERTO = ""  # fim de um intervalo em curso
PARALELO = 4

Intervalos = dict[int, list[tuple[str, str]]]


def do_historico(entradas: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """Os intervalos em exercício de um deputado, do histórico da API.

    As entradas sem situação (marcadores de início de legislatura) não contam;
    "Alteração de partido" mantém a situação "Exercício" e não quebra o intervalo.
    Sair e voltar no mesmo dia vira dois intervalos, como no registro.
    """
    validas = sorted(
        (
            (str(e.get("dataHora") or ""), text.normalize(str(e.get("situacao") or "")))
            for e in entradas
            if e.get("situacao") and e.get("dataHora")
        ),
        key=lambda par: par[0],
    )
    intervalos: list[tuple[str, str]] = []
    inicio: str | None = None
    for quando, situacao in validas:
        dia = quando[:10]
        if situacao == EXERCICIO:
            if inicio is None:
                inicio = dia
        elif inicio is not None:
            intervalos.append((inicio, dia))
            inicio = None
    if inicio is not None:
        intervalos.append((inicio, ABERTO))
    return intervalos


def em_aberto(intervalos: list[tuple[str, str]] | None) -> bool:
    return bool(intervalos) and intervalos[-1][1] == ABERTO


def fechados(intervalos: Mapping[int, list[tuple[str, str]]], hoje: str) -> Intervalos:
    """Os intervalos com o fim em aberto trocado por ``hoje`` (para comparar datas)."""
    return {pid: [(inicio, fim or hoje) for inicio, fim in lista] for pid, lista in intervalos.items()}


def atualizar(
    coletor: Coletor,
    existentes: Mapping[int, list[tuple[str, str]]],
    ids: Iterable[int],
    em_exercicio: Iterable[int] | None,
) -> Intervalos:
    """Completa os intervalos para ``ids`` (quem aparece na base de votos).

    Só consulta o histórico de quem ainda não tem intervalo guardado ou mudou de
    estado: está na lista ``em_exercicio`` de hoje sem intervalo aberto, ou tem
    intervalo aberto e saiu da lista. Sem a lista (``None``), só os que faltam.
    """
    atuais = set(em_exercicio) if em_exercicio is not None else None
    resultado: Intervalos = {int(k): [tuple(i) for i in v] for k, v in existentes.items()}  # type: ignore[misc]
    pedir = []
    for pid in sorted(set(ids)):
        if pid not in resultado:
            pedir.append(pid)
        elif atuais is not None and (pid in atuais) != em_aberto(resultado[pid]):
            pedir.append(pid)

    def historico(pid: int) -> tuple[int, list[tuple[str, str]] | None]:
        try:
            return pid, do_historico(fontes.camara_api(coletor, f"deputados/{pid}/historico", paginada=False))
        except Exception as exc:  # noqa: BLE001 — um histórico fora do ar não derruba a atualização
            log.warning("Câmara: histórico do deputado %s indisponível (%s)", pid, exc)
            return pid, None

    for pid, intervalos in net.parallel_map(historico, pedir, max_workers=PARALELO):
        if intervalos is not None:
            resultado[pid] = intervalos
    if pedir:
        log.info("Câmara: histórico de exercício de %d deputado(s) consultado", len(pedir))
    return resultado


def presidencia(coletor: Coletor, legislaturas: Iterable[int], *, validade_h: float = 24 * 3) -> Intervalos:
    """Quando cada deputado presidiu a Câmara nas ``legislaturas`` pedidas
    (``{id: [(início, fim)…]}``; fim vazio = preside hoje). Levanta o erro da fonte."""
    periodos: Intervalos = {}
    for numero in sorted(set(legislaturas)):
        for membro in fontes.camara_api(coletor, f"legislaturas/{numero}/mesa", paginada=False, validade_h=validade_h):
            pid = fontes.inteiro(membro.get("id"))
            inicio = str(membro.get("dataInicio") or "")[:10]
            if pid is None or not inicio or text.normalize(str(membro.get("titulo") or "")) != "presidente":
                continue
            intervalo = (inicio, str(membro.get("dataFim") or "")[:10])
            if intervalo not in periodos.setdefault(pid, []):
                periodos[pid].append(intervalo)
    return {pid: sorted(lista) for pid, lista in periodos.items()}


def em_exercicio_hoje(coletor: Coletor) -> set[int] | None:
    """Os ids dos deputados em exercício agora (``None`` se a lista não vier)."""
    try:
        lista = fontes.camara_api(coletor, "deputados", ordem="ASC", ordenarPor="nome")
    except Exception as exc:  # noqa: BLE001
        log.warning("Câmara: lista dos deputados em exercício indisponível (%s)", exc)
        return None
    ids = {int(d["id"]) for d in lista if d.get("id") is not None}
    return ids or None


# ── arquivo ──────────────────────────────────────────────────────────────────


def _linhas(intervalos: Mapping[int, list[tuple[str, str]]]) -> str:
    return ",\n".join(
        f'"{pid}":{json.dumps([list(i) for i in intervalos[pid]], separators=(",", ":"))}' for pid in sorted(intervalos)
    )


def serializar(
    intervalos: Mapping[int, list[tuple[str, str]]], presidentes: Mapping[int, list[tuple[str, str]]] | None = None
) -> str:
    """Um deputado por linha (o git guarda só o que mudou)."""
    cabeca = json.dumps({"v": VERSAO}, separators=(",", ":"))[:-1]
    return f'{cabeca},\n"presidencia":{{\n{_linhas(presidentes or {})}\n}},\n"intervalos":{{\n{_linhas(intervalos)}\n}}}}\n'


def _ler(pasta: Path, chave: str) -> Intervalos:
    try:
        dados = json.loads((Path(pasta) / ARQUIVO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(dados, dict) or dados.get("v") != VERSAO:
        return {}
    try:
        return {int(k): [(str(i[0]), str(i[1])) for i in v] for k, v in (dados.get(chave) or {}).items()}
    except (TypeError, ValueError, IndexError):
        return {}


def ler(pasta: Path) -> Intervalos:
    """Os períodos em exercício gravados (``{}`` sem o arquivo)."""
    return _ler(pasta, "intervalos")


def ler_presidencia(pasta: Path) -> Intervalos:
    """Os períodos de presidência da Câmara gravados (``{}`` sem o arquivo)."""
    return _ler(pasta, "presidencia")
