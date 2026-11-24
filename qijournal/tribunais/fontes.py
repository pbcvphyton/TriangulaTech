"""Coleta dos dados abertos do STF, do STJ e do CNJ: o :class:`ColetorTribunais`.

Toda requisição da aba passa por ele. Além do que o coletor do Congresso já faz (cache em disco com
validade, gravação atômica, ``fetch`` injetável, contagem de pedidos e bytes), ele:

- **lê e respeita o robots.txt de cada host** (leitor próprio, pela RFC 9309: o
  ``urllib.robotparser`` da stdlib casa só prefixo e joga fora o ``$``, e o robots.txt do STJ usa
  ``Disallow: /dataset/*/history``; a regra que casa o trecho mais longo vence, ``Allow`` no
  empate; ``Crawl-delay`` lido). O robots.txt fica no cache por um dia. Caminho vedado: :class:`CaminhoVedado`, e **nada é pedido**. robots.txt que
  responde 404: tudo permitido. Que responde 401/403 duas vezes: o host recusou
  (:class:`RecusaDoHost`). Que não responde (5xx, rede): nada é pedido ao host nesta execução;
- recusa sempre os endereços de ``fontes.proibidos`` do config, mesmo que o robots.txt permita
  (``processo.stj.jus.br``, a consulta processual do STF, a ``/api/`` do portal do STJ);
- espera uma **pausa mínima por host** entre um pedido e o seguinte: a do config ou o
  ``Crawl-Delay`` do robots.txt, o que for maior (portal de dados abertos do STJ: 10 s; STF: 1 s);
- trata o **403 de WAF** (o Cloudflare do STJ recusa às vezes, por taxa): uma nova tentativa depois
  de ``recusa_espera_s`` (120 s); se repetir, :class:`RecusaDoHost` (quem chama vira aviso e mantém
  o último dia bom). Nada é contornado: mesmo pedido, mesmos cabeçalhos;
- baixa **arquivo grande em fluxo para o disco** (o acervo do STJ tem 82 MB com gzip);
- faz **POST** (consulta Elasticsearch da jurisprudência do STF, DataJud, SOAP do SGT) com cache
  pela URL **mais o corpo**; resposta Elasticsearch com ``_shards.failed > 0`` ou ``timed_out``
  vira :class:`RespostaParcial` e não vai ao cache;
- tem um **disjuntor por host**: um pedido que esgota as tentativas sem resposta nenhuma (tempo
  esgotado, conexão recusada ou derrubada) põe o host fora do ar até o fim da execução, e os pedidos
  seguintes a ele falham na hora (um portal mudo não come o prazo da coleta inteira). Os hosts de
  API (``robots: api``, o DataJud) ficam de fora: lá a queda vale nova tentativa, com disjuntor
  próprio (``datajud.py``);
- respeita um **prazo** (``prazo``, instante no relógio do coletor; ``None`` = sem prazo, o padrão):
  perto dele cada pedido encurta o tempo-limite e as tentativas, e depois dele nada é pedido
  (:class:`PrazoEsgotado`, que não conta no disjuntor). ``timeout_s`` e ``tentativas`` por host vêm de
  ``fontes.hosts`` do config.

O User-Agent é o do projeto (``net.USER_AGENT``), que se identifica como robô e dá o endereço.

Recusas registradas na fase de descoberta (22–23/11/2026): o portal de dados abertos do STJ dá 403
intermitente do Cloudflare (35 respostas 200 e 23 recusas em 58 pedidos; libera em 1 a 7 min); o
DJEN responde 403 geográfico fora do Brasil (não usado); ``processo.stj.jus.br`` veda tudo no
robots.txt; o STF veda ``/processos``; o DataJud devolve 429, 504 e resultados parciais.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .. import net
from ..congresso.fontes import Coletor
from ..net import Fetcher, FetchError

log = logging.getLogger(__name__)

ARQUIVO_MAX_BYTES = 600_000_000  # ata do STJ: até 37 MB; dump do SGT: 35 MB
GRANDE_MAX_BYTES = 2_000_000_000  # acervo do STJ: 82 MB com gzip (518 MB aberto, mas não é aberto aqui)
API_MAX_BYTES = 120_000_000
BLOCO = 1 << 20  # 1 MB por leitura no download em fluxo
PASTAS_DO_CACHE = ("arquivos", "api", "paginas", "robots")
GRANDE_PODA_BYTES = 40_000_000  # acima disto, arquivo fora de uso sai do cache já (o acervo do STJ: 82 MB por dia)

Baixador = Callable[..., int]  # (url, destino, *, timeout, headers, max_bytes) -> bytes gravados


class CaminhoVedado(FetchError):
    """O robots.txt do host ou a lista ``fontes.proibidos`` veda o endereço: nada foi pedido."""


class RecusaDoHost(FetchError):
    """O host recusou o acesso (403) duas vezes seguidas, com a espera entre elas."""


class RespostaParcial(FetchError):
    """Resposta Elasticsearch incompleta (``_shards.failed > 0`` ou ``timed_out``): o número
    estaria errado sem erro HTTP, então não vale."""


class PrazoEsgotado(FetchError):
    """O prazo desta execução acabou (o nosso, não o do servidor): nada foi pedido, ou o pedido foi
    cortado. Não põe o host fora do ar; o que faltou fica para a próxima execução."""


def _queda(exc: FetchError) -> bool:
    """Falha de rede depois das tentativas (sem status HTTP): tempo esgotado, conexão recusada ou
    derrubada. Não é queda: o nosso prazo, resposta grande demais, formato, URL recusada."""
    if exc.status is not None or isinstance(exc, (PrazoEsgotado, RespostaParcial, CaminhoVedado, RecusaDoHost)):
        return False
    texto = str(exc).lower()
    return not any(x in texto for x in ("maior que", "esquema de url", "formato"))


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _casa(padrao: str, caminho: str) -> bool:
    """Um caminho do robots.txt (com ``*`` e ``$`` final) contra o caminho pedido, a partir do início."""
    ancora = padrao.endswith("$")
    corpo = padrao[:-1] if ancora else padrao
    regex = "".join(".*" if c == "*" else re.escape(c) for c in corpo)
    return re.match(regex + ("$" if ancora else ""), caminho) is not None


def _tokens_do_agente(agente: str) -> list[str]:
    """Os nomes de produto do User-Agent ("Mozilla/5.0 (...) QIJournalBot/2.0" → mozilla, qijournalbot)."""
    return [t.lower() for t in re.findall(r"([A-Za-z][\w.-]*)/", agente or "")] or [(agente or "").lower()]


@dataclass
class GrupoRobots:
    """Um grupo do robots.txt: os agentes, as regras ``(permite, caminho)`` e o Crawl-delay."""

    agentes: list[str] = field(default_factory=list)
    regras: list[tuple[bool, str]] = field(default_factory=list)
    atraso: float | None = None


@dataclass
class Robots:
    grupos: list[GrupoRobots] = field(default_factory=list)


def ler_robots(texto: str) -> Robots:
    """Lê um robots.txt (RFC 9309): linhas ``User-agent`` seguidas formam um grupo; uma
    ``User-agent`` depois de regras abre outro. Comentários (``#``), linhas sem ``:`` e HTML
    (servidor que responde a página no lugar do arquivo) não contam."""
    robots = Robots()
    atual: GrupoRobots | None = None
    em_regras = False
    for linha in (texto or "").splitlines():
        linha = linha.split("#", 1)[0].strip()
        if ":" not in linha:
            continue
        chave, valor = (x.strip() for x in linha.split(":", 1))
        chave = chave.lower()
        if chave == "user-agent":
            if atual is None or em_regras:
                atual = GrupoRobots()
                robots.grupos.append(atual)
                em_regras = False
            atual.agentes.append(valor)
        elif chave in ("allow", "disallow") and atual is not None:
            em_regras = True
            if valor:  # "Disallow:" vazio = nada vedado
                atual.regras.append((chave == "allow", unquote(valor)))
        elif chave == "crawl-delay" and atual is not None:
            em_regras = True
            try:
                atual.atraso = float(valor)
            except ValueError:
                pass
    return robots


def regras_do_robots(robots: Robots, agente: str) -> GrupoRobots | None:
    """O grupo que vale para ``agente``: os grupos que citam um dos nomes de produto do agente
    (juntos); senão os grupos ``*``; senão nenhum (tudo permitido)."""
    tokens = _tokens_do_agente(agente)
    nomeados = [g for g in robots.grupos if any(a != "*" and a.lower() in tokens for a in g.agentes)]
    escolhidos = nomeados or [g for g in robots.grupos if "*" in g.agentes]
    if not escolhidos:
        return None
    atrasos = [g.atraso for g in escolhidos if g.atraso is not None]
    return GrupoRobots(agentes=[a for g in escolhidos for a in g.agentes], regras=[r for g in escolhidos for r in g.regras],
                       atraso=max(atrasos) if atrasos else None)


def robots_permite(robots: Robots, agente: str, url: str) -> bool:
    """O robots.txt deixa pedir ``url``? A regra que casa o trecho mais longo vence; empate, Allow."""
    grupo = regras_do_robots(robots, agente)
    if grupo is None:
        return True
    partes = urlsplit(url)
    caminho = unquote(partes.path or "/") + (f"?{unquote(partes.query)}" if partes.query else "")
    melhor: tuple[int, bool] | None = None
    for permite, padrao in grupo.regras:
        if _casa(padrao, caminho):
            chave = (len(padrao), permite)
            if melhor is None or chave > melhor:
                melhor = chave
    return True if melhor is None else melhor[1]


def baixar_em_fluxo(
    url: str,
    destino: Path,
    *,
    timeout: float = 180.0,
    headers: Mapping[str, str] | None = None,
    max_bytes: int = GRANDE_MAX_BYTES,
    tentativas: int = 3,
    dormir: Callable[[float], None] = time.sleep,
    prazo_s: float | None = None,
) -> int:
    """Baixa ``url`` direto para ``destino`` (temporário na mesma pasta + ``os.replace``), em blocos
    de 1 MB, sem guardar o arquivo inteiro na memória. Confere o ``Content-Length``; corpo truncado,
    5xx e queda de conexão valem nova tentativa (1, 2, 4 s). HTTP ≥ 400 fora disso:
    :class:`net.FetchError` com o ``status`` (o 403 é tratado por quem chama). ``prazo_s``: o tempo
    total do download, tentativas incluídas (passou dele: :class:`PrazoEsgotado`). Devolve os bytes."""
    cabecalhos = {**net.DEFAULT_HEADERS, "Accept": "*/*", "Accept-Encoding": "identity", **dict(headers or {})}
    destino.parent.mkdir(parents=True, exist_ok=True)
    erro: FetchError | None = None
    fim = time.monotonic() + prazo_s if prazo_s is not None else None

    def conferir_prazo() -> None:
        if fim is not None and time.monotonic() >= fim:
            raise PrazoEsgotado(url, "o prazo desta execução acabou no meio do download")

    for tentativa in range(tentativas + 1):
        if tentativa:
            dormir(min(2 ** (tentativa - 1), 8))
        conferir_prazo()
        fd, tmp = tempfile.mkstemp(dir=destino.parent, prefix=f".{destino.name[:40]}.", suffix=".tmp")
        try:
            total = 0
            with os.fdopen(fd, "wb") as saida:
                req = urllib.request.Request(url, headers=cabecalhos)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    anunciado = (resp.headers.get("Content-Length") or "").strip()
                    while True:
                        conferir_prazo()
                        bloco = resp.read(BLOCO)
                        if not bloco:
                            break
                        total += len(bloco)
                        if total > max_bytes:
                            raise FetchError(url, f"arquivo maior que {max_bytes} bytes")
                        saida.write(bloco)
            if anunciado.isdigit() and total < int(anunciado):
                raise ConnectionError(f"corpo truncado ({total}/{anunciado} bytes)")
            os.replace(tmp, destino)
            return total
        except urllib.error.HTTPError as exc:
            Path(tmp).unlink(missing_ok=True)
            erro = FetchError(url, f"HTTP {exc.code}", status=exc.code)
            if exc.code not in net.RETRY_STATUS:
                raise erro from exc
        except FetchError:
            Path(tmp).unlink(missing_ok=True)
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            Path(tmp).unlink(missing_ok=True)
            erro = FetchError(url, f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}")
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        log.debug("Tribunais: tentativa %d do download falhou (%s): %s", tentativa + 1, url, erro)
    assert erro is not None
    raise erro


def conferir_elasticsearch(dados: Any, url: str = "") -> None:
    """Levanta :class:`RespostaParcial` se a resposta (ou o envelope ``result`` do STF) diz que
    algum shard falhou ou que o tempo esgotou."""
    for corpo in (dados, dados.get("result") if isinstance(dados, Mapping) else None):
        if not isinstance(corpo, Mapping):
            continue
        shards = corpo.get("_shards") or {}
        falhos = int(shards.get("failed") or 0) if isinstance(shards, Mapping) else 0
        if falhos > 0 or corpo.get("timed_out") is True:
            total = shards.get("total") if isinstance(shards, Mapping) else None
            raise RespostaParcial(url, f"resposta parcial do Elasticsearch ({falhos} de {total} shards falharam)")


class ColetorTribunais(Coletor):
    """O coletor dos Tribunais (ver o topo do módulo). Tudo injetável: ``fetch`` (assinatura de
    :func:`net.fetch`), ``baixar`` (assinatura de :func:`baixar_em_fluxo`), ``relogio`` e
    ``dormir``. Com ``regras`` (``modelo.Regras``), pausas, espera do 403, validade do robots.txt e
    endereços proibidos vêm de ``config/tribunais.yaml``; os parâmetros soltos vencem o config."""

    def __init__(
        self,
        cache: Path,
        *,
        regras: Any | None = None,
        fetch: Fetcher = net.fetch,
        baixar: Baixador | None = None,
        relogio: Callable[[], float] = time.time,
        dormir: Callable[[float], None] = time.sleep,
        pausas: Mapping[str, float] | None = None,
        pausa_padrao: float | None = None,
        recusa_espera_s: float | None | bool = True,
        robots_validade_h: float | None = None,
        proibidos: Iterable[str] | None = None,
        agente: str | None = None,
    ) -> None:
        super().__init__(cache, fetch=fetch, pausa=0.0, relogio=relogio, dormir=dormir)
        fontes: Mapping[str, Any] = getattr(regras, "fontes", None) or {}
        hosts_cfg: Mapping[str, Any] = fontes.get("hosts") or {}
        self._pausas = {h.lower(): float((c or {}).get("pausa_s", fontes.get("pausa_s", 1.0))) for h, c in hosts_cfg.items()}
        self._pausas.update({h.lower(): float(v) for h, v in (pausas or {}).items()})
        self.pausa_padrao = float(pausa_padrao if pausa_padrao is not None else fontes.get("pausa_s", 1.0))
        padrao_recusa = fontes.get("recusa_espera_s", 120)
        self._recusa_padrao: float | None = (
            (float(padrao_recusa) if padrao_recusa is not None else None) if recusa_espera_s is True
            else (float(recusa_espera_s) if recusa_espera_s is not None and recusa_espera_s is not False else None)
        )
        self._recusa_host: dict[str, float | None] = {}
        if recusa_espera_s is True:
            for h, c in hosts_cfg.items():
                if c and "recusa_espera_s" in c:
                    v = c["recusa_espera_s"]
                    self._recusa_host[h.lower()] = float(v) if v is not None else None
        self.robots_validade_h = float(robots_validade_h if robots_validade_h is not None else fontes.get("robots_validade_h", 24))
        # Hosts de API sem arquivo de regras (``robots: api`` no config): o /robots.txt responde 4xx
        # porque a API pede credencial em todo caminho (o DataJud é um Elasticsearch: 401 sem a chave,
        # 403 com ela, "robots.txt" lido como nome de índice). RFC 9309, §2.3.1.3: 4xx = sem regras.
        # Os outros hosts seguem como sempre: 401/403/429 no robots.txt param o host.
        self._robots_api = {h.lower() for h, c in hosts_cfg.items() if (c or {}).get("robots") == "api"}
        # Tempo-limite e tentativas por host (fontes.hosts: timeout_s, tentativas): a planilha do STF
        # (5,8 MB em ~2 s) não precisa de 4 × 180 s quando o servidor está mudo.
        self._timeout_host = {h.lower(): float(c["timeout_s"]) for h, c in hosts_cfg.items() if (c or {}).get("timeout_s")}
        self._tentativas_host = {h.lower(): int(c["tentativas"]) for h, c in hosts_cfg.items() if (c or {}).get("tentativas") is not None}
        self.prazo: float | None = None  # instante (no relógio do coletor) depois do qual nada é pedido
        self.proibidos = tuple(proibidos if proibidos is not None else (fontes.get("proibidos") or ()))
        self.agente = agente or net.USER_AGENT
        self._baixar = baixar
        self._proximo: dict[str, float] = {}  # host -> instante a partir do qual o próximo pedido pode sair
        self._trava_hosts = threading.Lock()
        self._robots: dict[str, Robots] = {}
        self._hosts_fora: dict[str, FetchError] = {}  # robots.txt recusado ou inacessível nesta execução
        self.recusas: list[dict[str, Any]] = []
        self.diario: list[dict[str, Any]] = []

    # ── regras do host ───────────────────────────────────────────────────────────────────────

    def pausa_de(self, host: str) -> float:
        """A pausa mínima do host: a do config (ou a padrão) e o Crawl-Delay do robots.txt já lido."""
        base = self._pausas.get(host.lower(), self.pausa_padrao)
        robots = self._robots.get(host.lower())
        grupo = regras_do_robots(robots, self.agente) if robots is not None else None
        if grupo is not None and grupo.atraso:
            base = max(base, grupo.atraso)
        return base

    def _espera_da_recusa(self, host: str) -> float | None:
        return self._recusa_host.get(host.lower(), self._recusa_padrao)

    def _proibido(self, url: str) -> bool:
        return any(url.startswith(p) for p in self.proibidos if p)

    def _ler_robots(self, url: str) -> Robots:
        partes = urlsplit(url)
        host = partes.netloc.lower()
        if host in self._hosts_fora:
            raise self._hosts_fora[host]
        if host in self._robots:
            return self._robots[host]
        endereco = f"{partes.scheme}://{partes.netloc}/robots.txt"
        caminho = self._caminho("robots", endereco, ".txt")
        texto: str | None = None
        if self._valido(caminho, self.robots_validade_h):
            try:
                texto = caminho.read_text(encoding="utf-8")
            except OSError:
                texto = None
        if texto is None:
            try:
                resp = self._com_recusa(endereco, lambda: self.fetch(endereco, timeout=30.0, retries=1, headers={"Accept": "text/plain, */*"}), robots=True)
                texto = resp.text()
            except PrazoEsgotado:
                raise  # o nosso prazo: o host não fica marcado
            except RecusaDoHost as exc:
                if host in self._robots_api:
                    texto = self._sem_regras(host, exc)
                else:
                    self._hosts_fora[host] = exc
                    raise
            except FetchError as exc:
                if host in self._robots_api and exc.status is not None and 400 <= exc.status < 500 and exc.status != 429:
                    texto = self._sem_regras(host, exc)
                elif exc.status is not None and 400 <= exc.status < 500 and exc.status not in (401, 403, 429):
                    texto = ""  # robots.txt ausente (404): tudo permitido
                else:
                    erro = FetchError(url, f"robots.txt de {host} inacessível ({exc}); nada foi pedido ao host", exc.status)
                    self._hosts_fora[host] = erro
                    raise erro from exc
            self._gravar(caminho, texto.encode("utf-8"))
        robots = ler_robots(texto)
        self._robots[host] = robots
        return robots

    def _sem_regras(self, host: str, exc: FetchError) -> str:
        log.info("Tribunais: %s é uma API sem robots.txt (HTTP %s no /robots.txt): sem regras (RFC 9309, §2.3.1.3); valem as do termo de uso",
                 host, exc.status)
        return ""

    def permitido(self, url: str) -> bool:
        """``url`` pode ser pedida (lista de proibidos e robots.txt)? Lê o robots.txt se preciso."""
        try:
            self._conferir(url)
        except CaminhoVedado:
            return False
        return True

    def _conferir(self, url: str) -> None:
        if not url.lower().startswith(("http://", "https://")):
            raise FetchError(url, "esquema de URL não suportado")
        if self._proibido(url):
            raise CaminhoVedado(url, "endereço proibido pelas regras da aba (fontes.proibidos); nada foi pedido")
        robots = self._ler_robots(url)
        if not robots_permite(robots, self.agente, url):
            raise CaminhoVedado(url, f"o robots.txt de {_host(url)} veda este caminho; nada foi pedido")

    # ── o pedido ─────────────────────────────────────────────────────────────────────────────

    def _esperar_host(self, host: str) -> None:
        with self._trava_hosts:
            agora = self._relogio()
            espera = self._proximo.get(host, 0.0) - agora
            self._proximo[host] = max(agora, self._proximo.get(host, 0.0)) + self.pausa_de(host)
        if espera > 0:
            self._dormir(espera)

    def _terminou(self, host: str) -> None:
        """A pausa conta também do fim do pedido (um download de 30 s não encurta a pausa)."""
        with self._trava_hosts:
            self._proximo[host] = max(self._proximo.get(host, 0.0), self._relogio() + self.pausa_de(host))

    def restante(self) -> float | None:
        """Segundos até o prazo (``None``: sem prazo)."""
        return None if self.prazo is None else self.prazo - self._relogio()

    def _conferir_prazo(self, url: str, espera: float = 0.0) -> None:
        restante = self.restante()
        if restante is not None and restante - espera <= 0:
            raise PrazoEsgotado(url, "o prazo desta execução acabou; nada mais é pedido (fica para a próxima)")

    def _limites(self, url: str, timeout: float, retries: int) -> tuple[float, int]:
        """O tempo-limite e as tentativas de um pedido: os do host (config), e perto do prazo menos
        (cada tentativa pode ler até 3 × o tempo-limite com servidor que pinga devagar)."""
        host = _host(url)
        timeout = self._timeout_host.get(host, timeout)
        retries = self._tentativas_host.get(host, retries)
        restante = self.restante()
        if restante is not None:
            if restante < 3 * timeout:
                retries = 0
            timeout = max(5.0, min(timeout, restante / ((retries + 1) * 3)))
        return timeout, retries

    def _com_recusa(self, url: str, chamada: Callable[[], Any], *, robots: bool = False) -> Any:
        host = _host(url)
        tentativa = 0
        while True:
            self._conferir_prazo(url)
            self._esperar_host(host)
            inicio = self._relogio()
            try:
                resultado = chamada()
            except FetchError as exc:
                self._terminou(host)
                self._anotar(url, exc.status, inicio, 0, tentativa, robots)
                if _queda(exc) and host not in self._robots_api and not robots:
                    # O disjuntor: o host não respondeu depois das tentativas; nada mais é pedido a ele.
                    self._hosts_fora[host] = FetchError(url, f"{host} não respondeu ({exc}); nada mais é pedido ao host nesta execução")
                    log.warning("Tribunais: %s não respondeu; o host fica fora até o fim da execução", host)
                espera = self._espera_da_recusa(host)
                if exc.status == 403 and tentativa == 0 and espera is not None:
                    self._conferir_prazo(url, espera)  # a espera do 403 não passa do prazo
                    tentativa += 1
                    log.warning("Tribunais: %s recusou o acesso (403); nova tentativa em %.0f s", host, espera)
                    self._dormir(espera)
                    continue
                if exc.status == 403:
                    vezes = tentativa + 1
                    self.recusas.append({"host": host, "url": url, "status": 403, "vezes": vezes})
                    raise RecusaDoHost(
                        url, f"{host} recusou o acesso (403) {vezes} vez{'es' if vezes > 1 else ''}; nada foi contornado", 403
                    ) from exc
                raise
            self._terminou(host)
            tamanho = len(getattr(resultado, "content", b"") or b"") if not isinstance(resultado, int) else resultado
            self._anotar(url, getattr(resultado, "status", 200), inicio, tamanho, tentativa, robots)
            self.requisicoes += 1
            self.bytes += tamanho
            return resultado

    def _anotar(self, url: str, status: int | None, inicio: float, tamanho: int, tentativa: int, robots: bool) -> None:
        partes = urlsplit(url)
        self.diario.append({
            "host": partes.netloc.lower(), "caminho": partes.path, "status": status,
            "segundos": round(max(0.0, self._relogio() - inicio), 2), "bytes": tamanho,
            "tentativa": tentativa + 1, "robots": robots,
        })

    def _pedir(self, url: str, **kwargs: Any) -> net.Response:
        self._conferir(url)
        kwargs["timeout"], kwargs["retries"] = self._limites(url, float(kwargs.get("timeout", 20.0)), int(kwargs.get("retries", 2)))
        return self._com_recusa(url, lambda: self.fetch(url, **kwargs))

    # ── leitura ──────────────────────────────────────────────────────────────────────────────

    def texto(self, url: str, *, validade_h: float = 0.0, cabecalhos: Mapping[str, str] | None = None) -> str:
        """Uma página (HTML, XML, CSV) decodificada; com ``validade_h`` fica no cache."""
        destino = self._caminho("paginas", url, ".txt")
        if self._valido(destino, validade_h):
            try:
                return destino.read_text(encoding="utf-8")
            except OSError:
                pass
        resp = self._pedir(url, timeout=60.0, retries=2, headers=dict(cabecalhos or {}), max_bytes=API_MAX_BYTES)
        conteudo = resp.text()
        if validade_h > 0:
            self._gravar(destino, conteudo.encode("utf-8"))
        return conteudo

    def json(self, url: str, *, validade_h: float = 0.0, cabecalhos: Mapping[str, str] | None = None) -> Any:
        """Resposta JSON de um GET (do cache, se ``validade_h`` permitir)."""
        destino = self._caminho("api", url, ".json")
        if self._valido(destino, validade_h):
            try:
                return json.loads(destino.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        resp = self._pedir(url, timeout=90.0, retries=2, headers={"Accept": "application/json", **dict(cabecalhos or {})},
                           max_bytes=API_MAX_BYTES)
        dados = resp.json()
        if validade_h > 0:
            self._gravar(destino, resp.content)
        return dados

    def arquivo(self, url: str, *, validade_h: float = 20.0, cabecalhos: Mapping[str, str] | None = None) -> Path:
        """Arquivo (ata, pauta, CSV, planilha) no cache; baixado se ausente ou vencido."""
        sufixo = "".join(Path(urlsplit(url).path).suffixes[-2:]) or ".bin"
        destino = self._caminho("arquivos", url, sufixo)
        if self._valido(destino, validade_h):
            return destino
        resp = self._pedir(url, timeout=180.0, retries=3, headers={"Accept": "*/*", **dict(cabecalhos or {})},
                           max_bytes=ARQUIVO_MAX_BYTES)
        self._gravar(destino, resp.content)
        log.debug("Tribunais: %s baixado (%.1f MB)", url.rsplit("/", 1)[-1], len(resp.content) / 1e6)
        return destino

    def arquivo_grande(self, url: str, *, validade_h: float = 20.0, max_bytes: int = GRANDE_MAX_BYTES) -> Path:
        """Arquivo grande (o acervo do STJ, o dump do SGT) baixado **em fluxo** para o cache.

        Sem ``baixar`` injetado: :func:`baixar_em_fluxo` quando o ``fetch`` é o da rede; com um
        ``fetch`` falso (testes), o arquivo passa por ele."""
        sufixo = "".join(Path(urlsplit(url).path).suffixes[-2:]) or ".bin"
        destino = self._caminho("arquivos", url, sufixo)
        if self._valido(destino, validade_h):
            return destino
        self._conferir(url)
        baixar = self._baixar
        if baixar is None and self.fetch is net.fetch:
            baixar = baixar_em_fluxo
        timeout, retries = self._limites(url, 180.0, 3)
        if baixar is baixar_em_fluxo and self.prazo is not None:  # o download inteiro cabe no que resta do prazo
            self._com_recusa(url, lambda: baixar_em_fluxo(url, destino, timeout=timeout, headers={"Accept": "*/*"}, max_bytes=max_bytes,
                                                          tentativas=retries, prazo_s=max(1.0, self.restante() or 0.0)))
        elif baixar is not None:
            self._com_recusa(url, lambda: baixar(url, destino, timeout=timeout, headers={"Accept": "*/*"}, max_bytes=max_bytes))
        else:
            resp = self._com_recusa(url, lambda: self.fetch(url, timeout=timeout, retries=retries, headers={"Accept": "*/*"}, max_bytes=max_bytes))
            self._gravar(destino, resp.content)
        log.info("Tribunais: %s baixado (%.1f MB)", url.rsplit("/", 1)[-1], destino.stat().st_size / 1e6)
        return destino

    def consulta(
        self,
        url: str,
        corpo: Any,
        *,
        validade_h: float = 0.0,
        elasticsearch: bool = False,
        cabecalhos: Mapping[str, str] | None = None,
        formato: str = "json",
        timeout: float = 90.0,
        retries: int = 2,
    ) -> Any:
        """POST com cache pela URL **e o corpo**. ``corpo`` dicionário ou lista vai como JSON
        canônico (chaves ordenadas, sem espaços); ``bytes``/``str`` vão como estão (SOAP).
        ``formato``: ``"json"`` (padrão) ou ``"texto"``. ``elasticsearch=True`` recusa resposta
        parcial (:class:`RespostaParcial`, fora do cache)."""
        if isinstance(corpo, (bytes, bytearray)):
            dados, tipo = bytes(corpo), None
        elif isinstance(corpo, str):
            dados, tipo = corpo.encode("utf-8"), None
        else:
            dados, tipo = json.dumps(corpo, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"), "application/json"
        chave = url + "#" + dados.decode("utf-8", "replace")
        destino = self._caminho("api", chave, ".json" if formato == "json" else ".txt")
        if self._valido(destino, validade_h):
            try:
                texto = destino.read_text(encoding="utf-8")
                return json.loads(texto) if formato == "json" else texto
            except (OSError, ValueError):
                pass
        cab = {"Accept": "application/json" if formato == "json" else "*/*"}
        if tipo:
            cab["Content-Type"] = tipo
        cab.update(dict(cabecalhos or {}))
        resp = self._pedir(url, timeout=timeout, retries=retries, headers=cab, max_bytes=API_MAX_BYTES, data=dados, method="POST")
        if formato == "json":
            resultado: Any = resp.json()
            if elasticsearch:
                conferir_elasticsearch(resultado, url)
            if validade_h > 0:
                self._gravar(destino, json.dumps(resultado, ensure_ascii=False).encode("utf-8"))
            return resultado
        texto = resp.text()
        if validade_h > 0:
            self._gravar(destino, texto.encode("utf-8"))
        return texto

    # ── cache ────────────────────────────────────────────────────────────────────────────────

    def limpar(self, dias: float = 10.0, *, proteger: Iterable[Path] = ()) -> int:
        """Apaga do cache o que esta execução não usou, nenhum estado cita (``proteger``: o socorro
        de cada fonte, a série das atas e das íntegras) e tem mais de ``dias`` dias."""
        limite = self._relogio() - dias * 86400
        guardar = {Path(c).resolve() for c in proteger}
        apagados = 0
        for pasta in PASTAS_DO_CACHE:
            raiz = self.cache / pasta
            if not raiz.is_dir():
                continue
            for caminho in raiz.iterdir():
                try:
                    if (caminho in self._usados or not caminho.is_file() or caminho.stat().st_mtime >= limite
                            or caminho.resolve() in guardar):
                        continue
                    caminho.unlink()
                    apagados += 1
                except OSError:
                    continue
        return apagados

    def podar_grandes(self, minimo_bytes: int = GRANDE_PODA_BYTES, *, proteger: Iterable[Path] = ()) -> list[Path]:
        """Apaga de ``arquivos/`` os arquivos grandes (o acervo do STJ do dia, 82 MB) que esta
        execução não usou e nenhum estado cita, qualquer que seja a idade: o acervo em uso fica (o
        publicado, de sexta a terça), e o recusado de hoje também (não é baixado de novo a cada hora).
        O nome no cache não diz o que o arquivo é (o endereço é cortado em 120 letras): vale o tamanho."""
        guardar = {Path(c).resolve() for c in proteger}
        raiz = self.cache / "arquivos"
        apagados: list[Path] = []
        if not raiz.is_dir():
            return apagados
        for caminho in sorted(raiz.iterdir()):
            try:
                if (not caminho.is_file() or caminho in self._usados or caminho.resolve() in guardar
                        or caminho.stat().st_size < minimo_bytes):
                    continue
                caminho.unlink()
                apagados.append(caminho)
            except OSError:
                continue
        return apagados
