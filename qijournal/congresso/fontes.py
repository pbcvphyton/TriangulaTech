"""Coleta dos dados abertos do Congresso.

Três fontes, todas públicas e sem chave:

- **Câmara, arquivos em lote** (``dadosabertos.camara.leg.br/arquivos``): um CSV
  por conjunto e ano, refeito toda madrugada (separador ``;``, UTF-8 com BOM);
- **Câmara, API** (``…/api/v2``): JSON paginado (``{dados, links}``, até 100
  itens por página);
- **Senado, APIs** (``legis.senado.leg.br/dadosabertos`` e
  ``adm.senado.gov.br/adm-dadosabertos``): JSON pedido pelo cabeçalho ``Accept``;
  acima de 10 requisições por segundo o Senado devolve 429.

Tudo passa por um :class:`Coletor`, que guarda o que baixou numa pasta de cache
(fora do git) e recebe um ``fetch`` injetável, com a assinatura de
:func:`qijournal.net.fetch`: os testes rodam sem rede.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from .. import net, text
from ..net import Fetcher

log = logging.getLogger(__name__)

CAMARA_ARQUIVOS = "https://dadosabertos.camara.leg.br/arquivos"
CAMARA_API = "https://dadosabertos.camara.leg.br/api/v2"
SENADO_API = "https://legis.senado.leg.br/dadosabertos"
SENADO_ADM = "https://adm.senado.gov.br/adm-dadosabertos"

ARQUIVO_MAX_BYTES = 600_000_000  # o maior CSV anual (votos de 2021) tem 107 MB
API_MAX_BYTES = 120_000_000
CAMARA_ITENS = 100  # teto de itens por página da API da Câmara
CAMARA_MAX_PAGINAS = 400

_PASTAS_DO_CACHE = ("arquivos", "api")

csv.field_size_limit(1 << 30)  # ementas e despachos passam do limite padrão do módulo


class Coletor:
    """Baixa e guarda em disco (``cache``) o que as fontes devolvem.

    - :meth:`arquivo`: arquivo em lote → caminho no cache; baixa de novo quando o
      que há no cache passou da validade (os arquivos são refeitos todo dia).
    - :meth:`json`: chamada de API → objeto; com ``validade_h`` a resposta também
      fica no cache.

    - :meth:`limpar`: apaga do cache o que a execução não usou e já é velho (as
      consultas com a data na URL, os anos que não são mais lidos).

    As chamadas respeitam um intervalo mínimo entre si (``pausa``), inclusive entre
    threads. Erros passageiros são refeitos pelo ``fetch``.
    """

    def __init__(
        self,
        cache: Path,
        *,
        fetch: Fetcher = net.fetch,
        pausa: float = 0.15,
        relogio: Callable[[], float] = time.time,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache = Path(cache)
        self.fetch = fetch
        self.pausa = pausa
        self._relogio = relogio
        self._dormir = dormir
        self._trava = threading.Lock()
        self._proxima = 0.0
        self._usados: set[Path] = set()  # o que esta execução leu ou gravou no cache
        self.requisicoes = 0
        self.bytes = 0

    # ── cache ────────────────────────────────────────────────────────────────

    def _caminho(self, pasta: str, url: str, sufixo: str = "") -> Path:
        nome = re.sub(r"[^A-Za-z0-9._-]+", "_", url.split("://", 1)[-1])[:120].strip("_")
        caminho = self.cache / pasta / f"{nome}-{text.short_hash(url, 10)}{sufixo}"
        self._usados.add(caminho)
        return caminho

    def limpar(self, dias: float = 10.0) -> int:
        """Apaga do cache os arquivos que esta execução não usou e que têm mais de
        ``dias`` dias: consultas com a data na URL, anos fechados que não são mais
        lidos e sobras de download. Devolve quantos apagou. O prazo fica acima da maior
        validade dos arquivos relidos todo dia, para não apagar o que ainda vale."""
        limite = self._relogio() - dias * 86400
        apagados = 0
        for pasta in _PASTAS_DO_CACHE:
            raiz = self.cache / pasta
            if not raiz.is_dir():
                continue
            for caminho in raiz.iterdir():
                try:
                    if caminho in self._usados or not caminho.is_file() or caminho.stat().st_mtime >= limite:
                        continue
                    caminho.unlink()
                    apagados += 1
                except OSError:
                    continue
        return apagados

    def _valido(self, caminho: Path, validade_h: float) -> bool:
        if validade_h <= 0 or not caminho.is_file():
            return False
        return (self._relogio() - caminho.stat().st_mtime) < validade_h * 3600

    def _gravar(self, caminho: Path, conteudo: bytes) -> None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=caminho.parent, prefix=f".{caminho.name[:40]}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(conteudo)
            os.replace(tmp, caminho)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _esperar(self) -> None:
        with self._trava:
            agora = self._relogio()
            espera = self._proxima - agora
            self._proxima = max(agora, self._proxima) + self.pausa
        if espera > 0:
            self._dormir(espera)

    # ── leitura ──────────────────────────────────────────────────────────────

    def arquivo(self, url: str, *, validade_h: float = 20.0) -> Path:
        """Arquivo em lote no cache (baixado se ausente ou vencido)."""
        destino = self._caminho("arquivos", url, Path(url).suffix or ".bin")
        if self._valido(destino, validade_h):
            return destino
        self._esperar()
        resp = self.fetch(url, timeout=180.0, retries=3, headers={"Accept": "*/*"}, max_bytes=ARQUIVO_MAX_BYTES)
        self.requisicoes += 1
        self.bytes += len(resp.content)
        self._gravar(destino, resp.content)
        log.debug("Congresso: %s baixado (%.1f MB)", url.rsplit("/", 1)[-1], len(resp.content) / 1e6)
        return destino

    def json(self, url: str, *, validade_h: float = 0.0) -> Any:
        """Resposta JSON de uma API (do cache, se ``validade_h`` permitir)."""
        destino = self._caminho("api", url, ".json")
        if self._valido(destino, validade_h):
            try:
                return json.loads(destino.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass  # cache ilegível: baixa de novo
        self._esperar()
        resp = self.fetch(
            url, timeout=90.0, retries=4, headers={"Accept": "application/json"}, max_bytes=API_MAX_BYTES
        )
        self.requisicoes += 1
        self.bytes += len(resp.content)
        dados = resp.json()
        if validade_h > 0:
            self._gravar(destino, resp.content)
        return dados


# ── utilitários ──────────────────────────────────────────────────────────────


def csv_linhas(caminho: Path) -> Iterator[dict[str, str]]:
    """Linhas de um CSV da Câmara (``;``, UTF-8 com BOM) como dicionários."""
    with open(caminho, encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle, delimiter=";")


def _query(params: Mapping[str, Any]) -> str:
    limpos = {k: v for k, v in params.items() if v not in (None, "")}
    return urlencode(limpos, doseq=False)


def id_da_uri(uri: str | None) -> int | None:
    """``…/deputados/204445`` → ``204445`` (as URIs são a chave nos arquivos em lote)."""
    match = re.search(r"/(\d+)/?$", (uri or "").strip())
    return int(match.group(1)) if match else None


def inteiro(valor: Any) -> int | None:
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


# ── Câmara ───────────────────────────────────────────────────────────────────


def camara_arquivo(coletor: Coletor, conjunto: str, sufixo: str | int | None = None, *, validade_h: float = 20.0) -> Path:
    """``camara_arquivo(c, "votacoesVotos", 2026)`` → o CSV do conjunto e ano no cache.

    ``sufixo``: o ano (``votacoes-2026``), a legislatura (``"L57"``) ou nada
    (``deputados``).
    """
    nome = f"{conjunto}-{sufixo}" if sufixo not in (None, "") else conjunto
    return coletor.arquivo(f"{CAMARA_ARQUIVOS}/{conjunto}/csv/{nome}.csv", validade_h=validade_h)


def camara_api(
    coletor: Coletor, caminho: str, *, validade_h: float = 0.0, paginada: bool = True, **params: Any
) -> list[dict[str, Any]]:
    """Todos os itens de uma lista da API da Câmara (segue a paginação pelo ``next``).

    ``paginada=False`` para os recursos que não aceitam ``itens`` e respondem 400 com
    ele (como ``/deputados/<id>/historico``)."""
    if paginada:
        params.setdefault("itens", CAMARA_ITENS)
    url = f"{CAMARA_API}/{caminho.lstrip('/')}"
    consulta = _query(params)
    if consulta:
        url += f"?{consulta}"
    itens: list[dict[str, Any]] = []
    for _pagina in range(CAMARA_MAX_PAGINAS):
        dados = coletor.json(url, validade_h=validade_h)
        lote = dados.get("dados") if isinstance(dados, dict) else None
        if isinstance(lote, list):
            itens.extend(x for x in lote if isinstance(x, dict))
        elif isinstance(lote, dict):
            itens.append(lote)
        proxima = next(
            (link.get("href") for link in (dados.get("links") or []) if link.get("rel") == "next"), None
        ) if isinstance(dados, dict) else None
        if not proxima or proxima == url:
            break
        url = proxima
    return itens


# ── Senado ───────────────────────────────────────────────────────────────────


def senado_api(coletor: Coletor, caminho: str, *, validade_h: float = 0.0, **params: Any) -> Any:
    """Resposta JSON da API legislativa do Senado."""
    url = f"{SENADO_API}/{caminho.lstrip('/')}"
    consulta = _query(params)
    if consulta:
        url += f"?{consulta}"
    return coletor.json(url, validade_h=validade_h)


def lista(valor: Any) -> list[Any]:
    """O JSON antigo do Senado (XML convertido) devolve objeto quando a lista tem um
    item só e nada quando está vazia: isto devolve sempre uma lista."""
    if valor is None or valor == "":
        return []
    return valor if isinstance(valor, list) else [valor]
