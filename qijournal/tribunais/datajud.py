"""DataJud (CNJ, API Pública): o cliente **opcional** da aba Tribunais, desligado por padrão.

Serve só para duas coisas, exibidas no "Como ler" quando ligado, nunca no caminho crítico:

- **frescor**: até quando vão os dados do CNJ (``max(@timestamp)`` do índice ``api_publica_stj``);
- **conferência**: os processos ativos por gabinete no DataJud (sem os movimentos de baixa,
  arquivamento, trânsito, devolução e cancelamento) contra o acervo do próprio STJ.

Por que desligado (``config/tribunais.yaml → datajud.ativo: false``): o Termo de Uso v1.2 diz
"exclusivamente para fins [...] não comerciais" (3.3), "não [...] distribuir [...] qualquer
informação derivada" (3.8), "dar ciência ao CNJ" do que for publicado (3.9) e "não realizar mais
de 120 requisições por minuto" (3.13). Ligar é decisão do dono, depois de avisar o CNJ. E o serviço
vive sobrecarregado: em 22 e 23/11/2026, 16 de 102 e 1 de 22 tentativas voltaram completas; o resto
foi 429 (``es_rejected_execution_exception``), 504 do balanceador aos 60 s, conexão derrubada ou
HTTP 200 **parcial** (``_shards.failed`` 1 ou 2 de 3: número errado sem erro HTTP).

O que o cliente faz, com os números do config (``datajud``):

- POST pelo :class:`~qijournal.tribunais.fontes.ColetorTribunais` (robots.txt, pausa do host,
  diário), cabeçalho ``Authorization: APIKey <chave>``: a chave pública do config, ou a variável
  ``DATAJUD_API_KEY`` quando existe (o CNJ avisa que pode trocá-la a qualquer momento);
- cada consulta tem até ``tentativas`` (6), com esperas de 5, 10, 20, 40 e 80 s, cada uma vezes um
  sorteio entre 0,8 e 1,2 (``variacao``), e ``timeout_s`` (90 s, acima dos 60 s do balanceador: o
  504 chega, não é cortado aqui) por tentativa;
- só vale **HTTP 200 com ``_shards.failed == 0``**, todos os shards bem-sucedidos e ``timed_out``
  falso; 429, 5xx, queda de conexão, JSON quebrado e resposta parcial valem nova tentativa;
- 400 (consulta errada) para a consulta; 401, 403, 404 e o robots.txt param o DataJud na execução;
- **disjuntor**: ``disjuntor`` (3) consultas seguidas sem resposta completa e nada mais é pedido ao
  DataJud nesta execução; o mesmo quando ``prazo_s`` (720 s) acaba;
- no máximo ``por_minuto`` (120) pedidos em qualquer janela de 60 s (item 3.13);
- os nomes de órgão chegam com mojibake (UTF-8 lido como Latin-1: ``PAULO SÃ\\x89RGIO``) e são
  corrigidos antes de casar com a composição.

Relógio, sono e sorteio são injetáveis: os testes não esperam nem acessam a rede. Quem chama
(``dados.py``) usa :func:`atualizar`, que devolve a entrada ``fontes.datajud`` do ``meta.json`` e a
conferência (para ``meta.json → cobertura.stj.datajud``); falha vira aviso e ficam os números do
último dia bom.
"""

from __future__ import annotations

import logging
import os
import random
import time
from collections import Counter, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, NoReturn
from urllib.parse import urlsplit

from ..net import FetchError
from .fontes import CaminhoVedado, RecusaDoHost, RespostaParcial, conferir_elasticsearch
from .modelo import Composicao, Fonte, Regras

log = logging.getLogger(__name__)

VARIAVEL_DA_CHAVE = "DATAJUD_API_KEY"
NOME = "CNJ · DataJud (API Pública)"
URL_LEITOR = "https://datajud-wiki.cnj.jus.br/api-publica/"
AVISO_DESLIGADO = (
    "Desligado: o termo de uso (itens 3.3, 3.8, 3.9 e 3.13) e a instabilidade do serviço; "
    "a página não usa número do DataJud."
)

# Movimentos que tiram o processo do acervo em tramitação (código TPU): baixa definitiva (22),
# arquivamento definitivo (246), arquivamento (861), devolução dos autos à origem (12472),
# trânsito em julgado (848) e cancelamento de distribuição (488, 83). Medido em 22/11/2026: sobram
# 345.421 de 3.730.541 documentos.
CODIGOS_DE_BAIXA = (22, 246, 861, 12472, 848, 488, 83)
ATIVO: dict[str, Any] = {"bool": {"must_not": {"terms": {"movimentos.codigo": list(CODIGOS_DE_BAIXA)}}}}
CAPA_GENERICA = 12728  # "Superior Tribunal de Justiça": processo sem gabinete no DataJud (27.552 ativos)
PRESIDENCIA = 76789
VICE_PRESIDENCIA = 76790

# C.0: a consulta mais barata (size 0, dois máximos). Roda primeiro: se o índice não mudou desde o
# último dia bom, nenhuma outra consulta é feita.
CONSULTA_FRESCOR: dict[str, Any] = {
    "size": 0,
    "aggs": {"max_ts": {"max": {"field": "@timestamp"}}, "max_upd": {"max": {"field": "dataHoraUltimaAtualizacao"}}},
}


def consulta_ativos_por_gabinete(tamanho: int = 120) -> dict[str, Any]:
    """Ativos por órgão julgador, com o nome de cada órgão (86 órgãos no índice; 59 com ativos em
    23/11/2026). ``track_total_hits``: sem ele o total vem "≥ 10.000" e a conta não fecha."""
    return {
        "size": 0,
        "track_total_hits": True,
        "query": ATIVO,
        "aggs": {
            "orgao": {
                "terms": {"field": "orgaoJulgador.codigo", "size": tamanho},
                "aggs": {"nomes": {"terms": {"field": "orgaoJulgador.nome.keyword", "size": 2}}},
            }
        },
    }


# ── erros ────────────────────────────────────────────────────────────────────────────────────


class FalhaDataJud(FetchError):
    """O DataJud não deu resposta completa (base dos erros deste módulo)."""


class ConsultaSemResposta(FalhaDataJud):
    """As tentativas (ou o prazo) acabaram sem nenhuma resposta completa."""


class ConsultaRecusada(FalhaDataJud):
    """O DataJud recusou a consulta (HTTP 400 e afins): erro na consulta, não vale repetir."""


class DisjuntorAberto(FalhaDataJud):
    """O DataJud está parado nesta execução (falhas seguidas, prazo, chave ou robots.txt)."""


# ── texto ────────────────────────────────────────────────────────────────────────────────────


def corrigir_mojibake(texto: str | None) -> str | None:
    """Desfaz o UTF-8 lido como Latin-1 dos nomes de órgão (``PRESIDÃ\\x8aNCIA`` → ``PRESIDÊNCIA``,
    ``1Âª REGIÃ\\x83O`` → ``1ª REGIÃO``). Texto certo (``JOÃO``) fica como está: a volta falha."""
    if not texto or ("Ã" not in texto and "Â" not in texto):
        return texto
    try:
        return texto.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texto


def chave_da_api(regras_datajud: Mapping[str, Any], ambiente: Mapping[str, str] | None = None) -> str:
    """A chave da API: a variável ``DATAJUD_API_KEY`` (quando tem texto), senão a do config."""
    ambiente = os.environ if ambiente is None else ambiente
    return (ambiente.get(VARIAVEL_DA_CHAVE) or "").strip() or str(regras_datajud.get("chave") or "").strip()


def _instante(agregacao: Any) -> str | None:
    """O ``max`` de uma data do Elasticsearch como instante UTC ``AAAA-MM-DDTHH:MM:SSZ``."""
    if not isinstance(agregacao, Mapping):
        return None
    texto = agregacao.get("value_as_string")
    try:
        if isinstance(texto, str) and texto:
            quando = datetime.fromisoformat(texto)
        elif isinstance(agregacao.get("value"), (int, float)):
            quando = datetime.fromtimestamp(float(agregacao["value"]) / 1000, tz=UTC)
        else:
            return None
    except (ValueError, OverflowError, OSError):
        return None
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=UTC)
    return quando.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _motivo(exc: FetchError) -> str:
    """A mensagem do erro sem o endereço que o ``FetchError`` põe no fim."""
    return str(exc).removesuffix(f" ({exc.url})")


def _br(data_iso: str | None) -> str:
    return f"{data_iso[8:10]}/{data_iso[5:7]}" if data_iso else "?"


# ── as respostas ─────────────────────────────────────────────────────────────────────────────


def conferir_resposta(dados: Any, url: str = "") -> Mapping[str, Any]:
    """A resposta vale? Só a do Elasticsearch com todos os shards bem-sucedidos, nenhum falho e sem
    tempo esgotado; o contrário é :class:`RespostaParcial` (o número sairia errado sem erro HTTP)."""
    if not isinstance(dados, Mapping):
        raise RespostaParcial(url, "resposta do DataJud sem o formato do Elasticsearch")
    conferir_elasticsearch(dados, url)
    shards = dados.get("_shards")
    if not isinstance(shards, Mapping) or not shards.get("total") or shards.get("successful") != shards.get("total"):
        raise RespostaParcial(url, f"resposta parcial do DataJud (shards: {dict(shards) if isinstance(shards, Mapping) else shards})")
    if dados.get("timed_out") is not False:
        raise RespostaParcial(url, "resposta do DataJud sem timed_out = false")
    return dados


def ler_frescor(dados: Mapping[str, Any]) -> dict[str, Any]:
    """A resposta da C.0 → ``{max_timestamp, data, max_atualizacao}`` (``data``: o dia, em UTC, da
    última carga do índice; é o ``data_dados`` da fonte)."""
    aggs = dados.get("aggregations") or {}
    ts = _instante(aggs.get("max_ts"))
    return {"max_timestamp": ts, "data": ts[:10] if ts else None, "max_atualizacao": _instante(aggs.get("max_upd"))}


def ler_ativos(dados: Mapping[str, Any], url: str = "") -> dict[str, Any]:
    """A resposta de :func:`consulta_ativos_por_gabinete` → ``{total, gabinetes: [{codigo, nome,
    n}], fora_da_lista}`` (gabinetes do maior para o menor; nome sem mojibake). Total que não é exato
    ou baldes que somam mais que o total: :class:`RespostaParcial`."""
    total_bruto = (dados.get("hits") or {}).get("total")
    if isinstance(total_bruto, Mapping):
        if total_bruto.get("relation") not in (None, "eq"):
            raise RespostaParcial(url, "o total de ativos veio como limite (≥), não como conta exata")
        total = total_bruto.get("value")
    else:
        total = total_bruto
    if not isinstance(total, int):
        raise RespostaParcial(url, "resposta do DataJud sem o total de ativos")
    orgao = (dados.get("aggregations") or {}).get("orgao") or {}
    gabinetes: list[dict[str, Any]] = []
    for balde in orgao.get("buckets") or []:
        nomes = (balde.get("nomes") or {}).get("buckets") or []
        nome = corrigir_mojibake(str(nomes[0]["key"])) if nomes else None
        gabinetes.append({"codigo": int(balde["key"]), "nome": nome, "n": int(balde["doc_count"])})
    fora = int(orgao.get("sum_other_doc_count") or 0)
    if sum(g["n"] for g in gabinetes) + fora > total:
        raise RespostaParcial(url, f"os gabinetes somam mais que o total de ativos ({total})")
    gabinetes.sort(key=lambda g: (-g["n"], g["codigo"]))
    return {"total": total, "gabinetes": gabinetes, "fora_da_lista": fora}


def conferir(
    ativos: Mapping[str, Any],
    composicao: Composicao,
    stj_por_ministro: Mapping[str, int] | None = None,
    *,
    total_stj: int | None = None,
) -> dict[str, Any]:
    """Os ativos do DataJud por gabinete ao lado do acervo do STJ, ministro a ministro.

    O gabinete casa com a composição pelo nome (``Composicao.do_gabinete``); o do DataJud guarda o
    nome de quem saiu (Og Fernandes, Marco Buzzi) e esses ficam sem ministro: o acervo já está com
    outro relator no STJ. ``stj_por_ministro``: processos do acervo do STJ por ``relator_id``.
    ``diferenca``: (DataJud − STJ) ÷ STJ, fração com 4 casas."""
    stj_por_ministro = stj_por_ministro or {}
    linhas: list[dict[str, Any]] = []
    for g in ativos.get("gabinetes") or []:
        m = composicao.do_gabinete(g.get("nome"))
        linhas.append({
            "codigo": g["codigo"], "nome": g.get("nome"), "ministro": m.id if m else None,
            "datajud": g["n"], "stj": stj_por_ministro.get(m.id) if m else None,
        })
    linhas.sort(key=lambda g: (-g["datajud"], g["codigo"]))
    total_dj = int(ativos.get("total") or 0)
    casados = [g for g in linhas if g["ministro"]]
    por_codigo = {g["codigo"]: g["datajud"] for g in linhas}
    return {
        "total_datajud": total_dj,
        "total_stj": total_stj,
        "diferenca": round((total_dj - total_stj) / total_stj, 4) if total_stj else None,
        "casados": {"gabinetes": len(casados), "datajud": sum(g["datajud"] for g in casados),
                    "stj": sum(g["stj"] or 0 for g in casados)},
        "sem_ministro": {"gabinetes": len(linhas) - len(casados), "datajud": total_dj - sum(g["datajud"] for g in casados)},
        "capa_generica": por_codigo.get(CAPA_GENERICA, 0),
        "presidencia": por_codigo.get(PRESIDENCIA, 0),
        "vice": por_codigo.get(VICE_PRESIDENCIA, 0),
        "gabinetes": linhas,
    }


# ── o cliente ────────────────────────────────────────────────────────────────────────────────


@dataclass
class Placar:
    """O que aconteceu com os pedidos ao DataJud nesta execução (log, Summary, diagnóstico)."""

    consultas: int = 0
    completas: int = 0
    tentativas: int = 0
    parciais: int = 0
    quedas: int = 0
    por_status: Counter[int] = field(default_factory=Counter)
    segundos: float = 0.0

    def texto(self) -> str:
        """``11 tentativas, 1 completa (2 parciais, 7×429, 1×504), 12 min``."""
        detalhes = [f"{self.parciais} parcia{'is' if self.parciais != 1 else 'l'}"] if self.parciais else []
        detalhes += [f"{n}×{s}" for s, n in sorted(self.por_status.items())]
        detalhes += [f"{self.quedas} queda{'s' if self.quedas != 1 else ''} de conexão"] if self.quedas else []
        tempo = f"{round(self.segundos / 60)} min" if self.segundos >= 90 else f"{round(self.segundos)} s"
        partes = f"{self.tentativas} tentativa{'s' if self.tentativas != 1 else ''}, {self.completas} completa{'s' if self.completas != 1 else ''}"
        return partes + (f" ({', '.join(detalhes)})" if detalhes else "") + f", {tempo}"

    def para_json(self) -> dict[str, Any]:
        return {"consultas": self.consultas, "completas": self.completas, "tentativas": self.tentativas, "parciais": self.parciais,
                "quedas": self.quedas, "por_status": {str(s): n for s, n in sorted(self.por_status.items())}, "segundos": round(self.segundos, 1)}


class ClienteDataJud:
    """POST ao índice do STJ no DataJud com espera crescente, disjuntor, prazo e teto por minuto
    (ver o topo do módulo). ``coletor``: um ``ColetorTribunais`` (ou algo com ``consulta`` e
    ``permitido``). ``relogio``/``dormir``/``aleatorio`` injetáveis."""

    def __init__(
        self,
        coletor: Any,
        *,
        url: str,
        chave: str,
        timeout_s: float = 90.0,
        tentativas: int = 6,
        espera_s: tuple[float, ...] | list[float] = (5, 10, 20, 40, 80),
        variacao: float = 0.2,
        disjuntor: int = 3,
        prazo_s: float = 720.0,
        por_minuto: int = 120,
        relogio: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], None] = time.sleep,
        aleatorio: Callable[[], float] = random.random,
    ) -> None:
        if not chave:
            raise ValueError("DataJud sem chave: config/tribunais.yaml → datajud.chave ou a variável DATAJUD_API_KEY")
        self.coletor = coletor
        self.url = url
        self._chave = chave
        self.timeout_s = float(timeout_s)
        self.tentativas = max(1, int(tentativas))
        self.espera_s = tuple(float(x) for x in espera_s) or (0.0,)
        self.variacao = float(variacao)
        self.limite_falhas = max(1, int(disjuntor))
        self.prazo_s = float(prazo_s)
        self.por_minuto = max(1, int(por_minuto))
        self._relogio = relogio
        self._dormir = dormir
        self._aleatorio = aleatorio
        self._inicio: float | None = None
        self._pedidos: deque[float] = deque()  # o fim dos últimos pedidos (teto por minuto)
        self._robots_conferido = False
        self.falhas_seguidas = 0
        self.motivo: str | None = None  # por que o disjuntor abriu (None: fechado)
        self.esperas: list[float] = []  # as esperas entre tentativas (não as do teto por minuto)
        self.placar = Placar()

    @classmethod
    def do_config(cls, regras: Regras, coletor: Any, *, ambiente: Mapping[str, str] | None = None, **kw: Any) -> ClienteDataJud:
        """O cliente com os números de ``config/tribunais.yaml → datajud`` (os parâmetros soltos vencem)."""
        cfg = regras.datajud
        base: dict[str, Any] = {
            "url": cfg.get("url") or "https://api-publica.datajud.cnj.jus.br/api_publica_stj/_search",
            "chave": chave_da_api(cfg, ambiente),
        }
        for nome in ("timeout_s", "tentativas", "espera_s", "variacao", "disjuntor", "prazo_s", "por_minuto"):
            if cfg.get(nome) is not None:
                base[nome] = cfg[nome]
        base.update(kw)
        return cls(coletor, **base)

    @property
    def aberto(self) -> bool:
        return self.motivo is not None

    def _abrir(self, motivo: str) -> None:
        if self.motivo is None:
            self.motivo = motivo
            log.warning("Tribunais: DataJud parado nesta execução: %s", motivo)

    def _restante(self) -> float:
        if self._inicio is None:
            return self.prazo_s
        return self.prazo_s - (self._relogio() - self._inicio)

    def _espera(self, indice: int) -> float:
        base = self.espera_s[min(indice, len(self.espera_s) - 1)]
        return base * (1.0 - self.variacao + 2.0 * self.variacao * self._aleatorio())

    def _respeitar_teto(self) -> None:
        """No máximo ``por_minuto`` pedidos em qualquer janela de 60 s (termo de uso, item 3.13).

        A conta usa o **fim** de cada pedido (o coletor ainda espera a pausa do host antes de
        mandar, e o envio nunca vem depois do fim): o próximo só sai 60 s depois do fim do
        ``por_minuto``-ésimo anterior, então nenhuma janela de 60 s tem pedidos demais."""
        while len(self._pedidos) >= self.por_minuto:
            espera = self._pedidos[-self.por_minuto] + 60.0 - self._relogio()
            if espera <= 0:
                break
            self._dormir(espera)
        while len(self._pedidos) >= self.por_minuto:
            self._pedidos.popleft()

    def _conferir_robots(self) -> None:
        if self._robots_conferido:
            return
        try:
            permitido = self.coletor.permitido(self.url)
        except FetchError as exc:
            self._abrir(f"robots.txt de {urlsplit(self.url).netloc} inacessível ({exc})")
            raise DisjuntorAberto(self.url, f"DataJud parado: {self.motivo}") from exc
        if not permitido:
            self._abrir(f"o robots.txt de {urlsplit(self.url).netloc} veda {urlsplit(self.url).path}")
            raise DisjuntorAberto(self.url, f"DataJud parado: {self.motivo}")
        self._robots_conferido = True

    def consultar(self, corpo: Mapping[str, Any], *, rotulo: str = "consulta", ler: Callable[[Mapping[str, Any]], Any] | None = None) -> Any:
        """Uma consulta, com as tentativas e as esperas do config. Devolve a resposta completa (ou o
        que ``ler`` tira dela: resposta que ``ler`` não entende vale como parcial e é pedida de
        novo) ou levanta :class:`FalhaDataJud` (:class:`DisjuntorAberto` sem pedir nada quando o
        DataJud já está parado nesta execução)."""
        if self.aberto:
            raise DisjuntorAberto(self.url, f"DataJud parado nesta execução: {self.motivo}")
        if self._inicio is None:
            self._inicio = self._relogio()
        self._conferir_robots()
        self.placar.consultas += 1
        ultimo: str = "sem tentativa"
        for indice in range(self.tentativas):
            if indice:
                espera = self._espera(indice - 1)
                if espera >= self._restante():
                    self._falhou(rotulo, f"o prazo do DataJud ({self.prazo_s:.0f} s) não cabe a próxima espera; a última falha: {ultimo}", prazo=True)
                self.esperas.append(round(espera, 3))
                self._dormir(espera)
            if self._restante() <= 0:
                self._falhou(rotulo, f"o prazo do DataJud ({self.prazo_s:.0f} s) acabou; a última falha: {ultimo}", prazo=True)
            self._respeitar_teto()
            inicio = self._relogio()
            self.placar.tentativas += 1
            try:
                dados = self.coletor.consulta(
                    self.url, dict(corpo), validade_h=0.0, elasticsearch=True, timeout=self.timeout_s, retries=0,
                    cabecalhos={"Authorization": f"APIKey {self._chave}"},
                )
                self._conferir_status()
                resposta: Any = conferir_resposta(dados, self.url)
                if ler is not None:
                    try:
                        resposta = ler(resposta)
                    except (KeyError, TypeError, ValueError) as exc:
                        raise RespostaParcial(self.url, f"resposta do DataJud fora do formato esperado ({type(exc).__name__}: {exc})") from exc
            except RespostaParcial as exc:
                self.placar.parciais += 1
                ultimo = f"resposta parcial ({exc})"
            except (CaminhoVedado, RecusaDoHost) as exc:
                self._abrir(f"o DataJud recusou o acesso ({exc})")
                raise DisjuntorAberto(self.url, f"DataJud parado: {self.motivo}", getattr(exc, "status", None)) from exc
            except FetchError as exc:
                status = exc.status
                if status is None:
                    self.placar.quedas += 1
                    ultimo = f"conexão derrubada ou sem resposta ({exc})"
                else:
                    self.placar.por_status[status] += 1
                    ultimo = f"HTTP {status}"
                    if status in (401, 403, 404):
                        self._abrir(f"HTTP {status}: chave recusada ou índice inexistente ({self.url})")
                        raise DisjuntorAberto(self.url, f"DataJud parado: {self.motivo}", status) from exc
                    if 400 <= status < 500 and status not in (408, 425, 429):
                        self._contar_falha(rotulo)
                        raise ConsultaRecusada(self.url, f"o DataJud recusou a consulta {rotulo} (HTTP {status})", status) from exc
            except ValueError as exc:  # JSON quebrado ou cortado
                ultimo = f"resposta ilegível ({exc})"
            else:
                self.placar.completas += 1
                self.falhas_seguidas = 0
                log.info("Tribunais: DataJud: %s: resposta completa na tentativa %d", rotulo, indice + 1)
                return resposta
            finally:
                fim = self._relogio()
                self._pedidos.append(fim)
                self.placar.segundos += max(0.0, fim - inicio)
            log.info("Tribunais: DataJud: %s, tentativa %d de %d: %s", rotulo, indice + 1, self.tentativas, ultimo)
        self._falhou(rotulo, f"{self.tentativas} tentativas sem resposta completa; a última: {ultimo}")

    def _conferir_status(self) -> None:
        """Só vale HTTP 200: o diário do coletor guarda o status do último pedido ao host."""
        host = urlsplit(self.url).netloc.lower()
        diario = getattr(self.coletor, "diario", None) or []
        ultimo = next((d for d in reversed(diario) if d.get("host") == host and not d.get("robots")), None)
        if ultimo is not None and ultimo.get("status") not in (200, None):
            raise RespostaParcial(self.url, f"o DataJud respondeu HTTP {ultimo.get('status')}, não 200")

    def _contar_falha(self, rotulo: str) -> None:
        self.falhas_seguidas += 1
        if self.falhas_seguidas >= self.limite_falhas:
            self._abrir(f"{self.falhas_seguidas} consultas seguidas sem resposta completa (a última: {rotulo})")

    def _falhou(self, rotulo: str, motivo: str, *, prazo: bool = False) -> NoReturn:
        self._contar_falha(rotulo)
        if prazo:
            self._abrir(f"o prazo do DataJud ({self.prazo_s:.0f} s) acabou")
        raise ConsultaSemResposta(self.url, f"DataJud, {rotulo}: {motivo}")

    # ── as consultas da aba ──────────────────────────────────────────────────────────────────

    def frescor(self) -> dict[str, Any]:
        """Até quando vão os dados do CNJ (C.0)."""
        return self.consultar(CONSULTA_FRESCOR, rotulo="frescor", ler=ler_frescor)  # type: ignore[no-any-return]

    def ativos_por_gabinete(self) -> dict[str, Any]:
        """Os processos ativos por gabinete (sem os movimentos de baixa), com o total exato."""
        return self.consultar(consulta_ativos_por_gabinete(), rotulo="ativos por gabinete", ler=lambda d: ler_ativos(d, self.url))  # type: ignore[no-any-return]


# ── a fonte no meta.json ─────────────────────────────────────────────────────────────────────


@dataclass
class Resultado:
    """O que :func:`atualizar` devolve para ``dados.py``."""

    fonte: Fonte  # meta.json → fontes.datajud
    conferencia: dict[str, Any] | None  # meta.json → cobertura.stj.datajud (o "Como ler" mostra quando ligado)
    placar: Placar
    mudou: bool  # a conferência foi refeita com dados novos do DataJud
    log: str  # uma linha para o log e o Summary


def fonte_desligada(regras: Regras) -> Fonte:
    """A entrada ``fontes.datajud`` com o DataJud desligado no config (nenhum pedido é feito)."""
    return {
        "nome": NOME, "trib": "cnj", "url": URL_LEITOR, "data_dados": None, "coletado_em": None, "ok": None, "ativo": False,
        "aviso": AVISO_DESLIGADO, "registros": None, "velho_dias": int(regras.datajud.get("velho_dias") or 15),
    }


def _contagens_do_painel(painel: Mapping[str, Any] | None) -> tuple[dict[str, int], int | None]:
    if not painel:
        return {}, None
    por_ministro = {str(m["id"]): int(m.get("processos") or 0) for m in painel.get("ministros") or [] if m.get("id")}
    total = (painel.get("universo") or {}).get("processos")
    return por_ministro, int(total) if isinstance(total, int) else None


def _de_anterior(conferencia: Mapping[str, Any]) -> dict[str, Any]:
    """Os ativos guardados na conferência anterior, para refazer a conta com o acervo do STJ do dia."""
    return {"total": int(conferencia.get("total_datajud") or 0),
            "gabinetes": [{"codigo": g["codigo"], "nome": g.get("nome"), "n": g["datajud"]} for g in conferencia.get("gabinetes") or []]}


def atualizar(
    regras: Regras,
    coletor: Any,
    *,
    agora: datetime | None = None,
    hoje: date | None = None,
    anterior_fonte: Mapping[str, Any] | None = None,
    anterior_conferencia: Mapping[str, Any] | None = None,
    stj_por_ministro: Mapping[str, int] | None = None,
    total_stj: int | None = None,
    painel_stj: Mapping[str, Any] | None = None,
    ambiente: Mapping[str, str] | None = None,
    cliente: ClienteDataJud | None = None,
    **kw: Any,
) -> Resultado:
    """A rodada do DataJud numa execução (a última da ordem STJ → STF → DataJud).

    Desligado no config: nenhum pedido; a fonte sai como em :func:`fonte_desligada`. Ligado: a C.0
    (frescor) primeiro; se ``max(@timestamp)`` é o mesmo da conferência anterior, nada mais é pedido
    e a conta é refeita com o acervo do STJ do dia; senão, os ativos por gabinete. Falha (qualquer
    :class:`FalhaDataJud`) ou total que muda mais que ``variacao_max`` (10%) contra o anterior: a
    fonte fica ``ok: false`` com o aviso e a conferência anterior fica como estava.

    ``stj_por_ministro``/``total_stj``: o acervo do STJ do dia por ``relator_id`` e o total (ou
    ``painel_stj``: o ``stj.json`` montado, de onde as duas contas saem). ``kw`` vai ao cliente
    (``relogio``, ``dormir``, ``aleatorio``...)."""
    agora = agora or datetime.now(UTC)
    instante = agora.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    hoje = hoje or agora.date()
    if not regras.datajud.get("ativo"):
        return Resultado(fonte_desligada(regras), None, Placar(), False, "DataJud: desligado no config (nenhum pedido)")
    if painel_stj is not None and stj_por_ministro is None:
        stj_por_ministro, total_painel = _contagens_do_painel(painel_stj)
        total_stj = total_stj if total_stj is not None else total_painel
    composicao = regras.composicao["stj"]
    velho_dias = int(regras.datajud.get("velho_dias") or 15)
    anterior_fonte = dict(anterior_fonte or {})
    base: Fonte = {
        "nome": NOME, "trib": "cnj", "url": URL_LEITOR, "data_dados": anterior_fonte.get("data_dados"),
        "coletado_em": anterior_fonte.get("coletado_em"), "ok": False, "ativo": True, "aviso": None,
        "registros": anterior_fonte.get("registros"), "velho_dias": velho_dias,
    }
    if cliente is None:
        try:
            cliente = ClienteDataJud.do_config(regras, coletor, ambiente=ambiente, **kw)
        except ValueError as exc:  # ligado sem chave no config nem na variável
            base["aviso"] = f"O DataJud está ligado sem chave ({exc}); a página não tem número do DataJud."
            log.warning("Tribunais: DataJud: %s", exc)
            return Resultado(base, dict(anterior_conferencia) if anterior_conferencia else None, Placar(), False, f"DataJud: sem chave ({exc})")

    def falha(motivo: str) -> Resultado:
        # O aviso (vai ao meta.json) tem só a data: falhar de novo no mesmo dia não regrava o
        # arquivo. O motivo e o placar vão ao log e ao Summary.
        dados_de = f"ficam os números de {_br(base['data_dados'])}" if base["data_dados"] else "a página não tem número do DataJud"
        base["aviso"] = f"O DataJud não deu resposta completa em {hoje.strftime('%d/%m')}; {dados_de}."
        linha = f"DataJud: falhou ({motivo}), {cliente.placar.texto()}; {dados_de}"
        log.warning("Tribunais: %s", linha)
        return Resultado(base, dict(anterior_conferencia) if anterior_conferencia else None, cliente.placar, False, linha)

    def com_aviso_de_atraso(fonte: Fonte) -> Fonte:
        if fonte.get("data_dados"):
            dias = (hoje - date.fromisoformat(str(fonte["data_dados"]))).days
            if dias > velho_dias:
                fonte["aviso"] = f"Os dados do CNJ vão até {_br(fonte['data_dados'])}/{str(fonte['data_dados'])[:4]} ({dias} dias)."
        return fonte

    try:
        frescor = cliente.frescor()
    except FetchError as exc:
        return falha(cliente.motivo or _motivo(exc))
    if not frescor["max_timestamp"]:
        return falha("a resposta do frescor veio sem data")
    mesmo_indice = bool(anterior_conferencia) and anterior_conferencia.get("max_timestamp") == frescor["max_timestamp"]  # type: ignore[union-attr]
    if mesmo_indice:
        assert anterior_conferencia is not None
        conferencia = {"max_timestamp": frescor["max_timestamp"], "data_dados": frescor["data"],
                       **conferir(_de_anterior(anterior_conferencia), composicao, stj_por_ministro, total_stj=total_stj)}
        base["ok"], base["data_dados"], base["registros"] = True, frescor["data"], conferencia["total_datajud"]
        base["coletado_em"] = base["coletado_em"] or instante
        linha = f"DataJud: sem carga nova desde {_br(frescor['data'])} (1 consulta); conferência refeita com o acervo do STJ do dia"
        log.info("Tribunais: %s", linha)
        return Resultado(com_aviso_de_atraso(base), conferencia, cliente.placar, False, linha)
    try:
        ativos = cliente.ativos_por_gabinete()
    except FetchError as exc:
        return falha(cliente.motivo or _motivo(exc))
    antes = int((anterior_conferencia or {}).get("total_datajud") or 0)
    limite = float(regras.datajud.get("variacao_max") or 0.10)
    if antes and abs(ativos["total"] / antes - 1) > limite:
        return falha(f"o total de ativos foi de {antes} para {ativos['total']}, variação acima de {limite:.0%}")
    conferencia = {"max_timestamp": frescor["max_timestamp"], "data_dados": frescor["data"],
                   **conferir(ativos, composicao, stj_por_ministro, total_stj=total_stj)}
    base["ok"], base["data_dados"], base["coletado_em"], base["registros"] = True, frescor["data"], instante, ativos["total"]
    linha = (f"DataJud: carga de {_br(frescor['data'])}, {ativos['total']} ativos em {len(ativos['gabinetes'])} órgãos, "
             f"{cliente.placar.texto()}")
    log.info("Tribunais: %s", linha)
    return Resultado(com_aviso_de_atraso(base), conferencia, cliente.placar, True, linha)
