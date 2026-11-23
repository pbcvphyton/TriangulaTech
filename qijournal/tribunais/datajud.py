"""DataJud (CNJ, API Pública): o cliente da aba Tribunais, ligado **para uso pessoal**.

O que ele traz (``config/tribunais.yaml → datajud``):

- **frescor**: até quando vão os dados do CNJ (``max(@timestamp)`` do índice ``api_publica_stj``);
- **conferência**: os processos ativos por gabinete no DataJud (sem os movimentos de baixa,
  arquivamento, trânsito, devolução e cancelamento) contra o acervo do próprio STJ;
- **os movimentos dos processos na mesa** (o ganho de verdade): o acervo aberto do STJ não diz
  desde quando o processo está concluso (o do STF diz). Para cada processo na mesa, o DataJud dá a
  última **Conclusão** (TPU 51), o último **ato do magistrado** (a raiz 1 da TPU de movimentos:
  decisão, despacho, julgamento), a última decisão ou julgamento e o último movimento. Daí sai, no
  STJ, o "concluso há N dias (CNJ)" ao lado do "sem decisão registrada" (a régua em
  ``contas.camada_cnj``).

**Uso pessoal × publicar.** O dono ligou o DataJud (``ativo: true``) para uso pessoal. O Termo de
Uso v1.2 diz "exclusivamente para fins [...] não comerciais" (3.3), "não [...] distribuir [...]
qualquer informação derivada" (3.8), "dar ciência ao CNJ" do que for publicado (3.9) e "não
realizar mais de 120 requisições por minuto" (3.13). O repositório e o site são públicos: com
``datajud.publicar: false`` (o padrão) o DataJud é consultado, o resultado fica só no cache (fora do
git) e **nada** derivado dele vai a ``data/tribunais/`` nem à página; no ``meta.json`` a fonte sai
como "ligada para uso pessoal; não publicada", sem número. ``publicar: true`` (ou a variável
``QIJ_DATAJUD_PUBLICAR=1`` numa execução) põe a camada do CNJ na página, sempre com o selo "CNJ ·
DataJud" (para a prévia privada ou depois de dar ciência ao CNJ, item 3.9).

**O serviço** vive sobrecarregado: em 22 e 23/11/2026, 16 de 102 e 1 de 22 tentativas voltaram
completas; o resto foi 429 (``es_rejected_execution_exception``: a fila de busca do cluster cheia),
504 do balanceador aos 60 s, conexão derrubada ou HTTP 200 **parcial** (``_shards.failed`` 1 ou 2 de
3: número errado sem erro HTTP). Por isso:

- POST pelo :class:`~qijournal.tribunais.fontes.ColetorTribunais` (pausa do host, diário),
  cabeçalho ``Authorization: APIKey <chave>``: a chave pública do config, ou a variável
  ``DATAJUD_API_KEY`` quando existe (o CNJ avisa que pode trocá-la a qualquer momento). O host é um
  Elasticsearch puro: ``/robots.txt`` responde 401 sem a chave e 403 com ela (o ES lê
  "robots.txt" como nome de índice); não há arquivo de regras (RFC 9309, §2.3.1.3: 4xx = sem
  regras), e as regras do host são as do termo (``fontes.hosts`` no config);
- cada consulta tem até ``tentativas``, com esperas crescentes (cada uma vezes um sorteio entre
  0,8 e 1,2) e ``timeout_s`` (90 s, acima dos 60 s do balanceador: o 504 chega, não é cortado aqui);
- só vale **HTTP 200 com ``_shards.failed == 0``**, todos os shards bem-sucedidos e ``timed_out``
  falso; 429, 5xx, queda de conexão, JSON quebrado e resposta parcial valem nova tentativa;
- 400 (consulta errada) para a consulta; 401, 403 e 404 param o DataJud na execução;
- **disjuntor**: ``disjuntor`` (3) consultas seguidas sem resposta completa e nada mais é pedido ao
  DataJud nesta execução; o mesmo quando ``prazo_s`` acaba;
- no máximo ``por_minuto`` (120) pedidos em qualquer janela de 60 s (item 3.13), um pedido por vez;
- os nomes de órgão chegam com mojibake (UTF-8 lido como Latin-1: ``PAULO SÃ\\x89RGIO``) e são
  corrigidos antes de casar com a composição.

**A carga dos movimentos** (:func:`carregar_movimentos`), medida ao vivo em 23/11/2026 (relatório da
rodada): consultas por **lote de números** (``terms`` em ``numeroProcesso.keyword``, o número
único CNJ que o acervo do STJ traz em ``numeroUnico``) com ``_source`` filtrado (número, última
atualização, órgão, ``movimentos.codigo`` e ``movimentos.dataHora``), na ordem de quem espera há
mais tempo (o que decide as faixas de 180 e 365 dias vem primeiro); depois da carga inicial, o
**incremental** por ``dataHoraUltimaAtualizacao`` (``range`` + ``search_after``, a marca e o cursor
em ``<cache>/estado/datajud.json``). Cada resultado vai, na hora, para o diário
``<cache>/datajud/movimentos.jsonl`` (o passo do GitHub pode ser cortado pelo tempo-limite: nada
do que voltou se perde). Prazo por execução e disjuntor: a carga anda em fatias, execução a
execução.

Relógio, sono e sorteio são injetáveis: os testes não esperam nem acessam a rede. Quem chama
(``dados.py``) usa :func:`atualizar`; falha vira aviso e fica o último dia bom.
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import os
import random
import re
import tempfile
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, cast
from urllib.parse import urlsplit

from ..net import FetchError
from .fontes import CaminhoVedado, RecusaDoHost, RespostaParcial, conferir_elasticsearch
from .modelo import Composicao, Fonte, Regras

log = logging.getLogger(__name__)

VARIAVEL_DA_CHAVE = "DATAJUD_API_KEY"
VARIAVEL_PUBLICAR = "QIJ_DATAJUD_PUBLICAR"  # "1" numa execução: a camada do CNJ vai à página (a prévia)
NOME = "CNJ · DataJud (API Pública)"
SELO = "CNJ · DataJud"
ATRIBUICAO = "Fonte: CNJ, DataJud (API Pública), metadados processuais do STJ"
URL_LEITOR = "https://datajud-wiki.cnj.jus.br/api-publica/"
AVISO_DESLIGADO = (
    "Desligado: o termo de uso (itens 3.3, 3.8, 3.9 e 3.13) e a instabilidade do serviço; "
    "a página não usa número do DataJud."
)
AVISO_PESSOAL = (
    "Ligado para uso pessoal; não publicado: o termo de uso do DataJud (item 3.8) veda distribuir informação derivada, "
    "e esta página é pública. Nenhum número da página vem do DataJud."
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


def _aproveitavel(dados: Any) -> bool:
    """Uma resposta parcial que ainda traz documentos inteiros: algum shard respondeu e há a lista
    de ``hits`` (a da consulta por lote)."""
    if not isinstance(dados, Mapping):
        return False
    shards = dados.get("_shards")
    hits = (dados.get("hits") or {}).get("hits") if isinstance(dados.get("hits"), Mapping) else None
    return isinstance(shards, Mapping) and int(shards.get("successful") or 0) >= 1 and isinstance(hits, list)


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
    aproveitadas: int = 0  # respostas parciais de lote aproveitadas (os documentos dos shards que responderam)

    def texto(self) -> str:
        """``11 tentativas, 1 completa (2 parciais, 7×429, 1×504), 12 min``."""
        detalhes = [f"{self.parciais} parcia{'is' if self.parciais != 1 else 'l'}"] if self.parciais else []
        detalhes += [f"{self.aproveitadas} aproveitada{'s' if self.aproveitadas != 1 else ''}"] if self.aproveitadas else []
        detalhes += [f"{n}×{s}" for s, n in sorted(self.por_status.items())]
        detalhes += [f"{self.quedas} queda{'s' if self.quedas != 1 else ''} de conexão"] if self.quedas else []
        tempo = f"{round(self.segundos / 60)} min" if self.segundos >= 90 else f"{round(self.segundos)} s"
        partes = f"{self.tentativas} tentativa{'s' if self.tentativas != 1 else ''}, {self.completas} completa{'s' if self.completas != 1 else ''}"
        return partes + (f" ({', '.join(detalhes)})" if detalhes else "") + f", {tempo}"

    def para_json(self) -> dict[str, Any]:
        return {"consultas": self.consultas, "completas": self.completas, "tentativas": self.tentativas, "parciais": self.parciais,
                "aproveitadas": self.aproveitadas, "quedas": self.quedas, "por_status": {str(s): n for s, n in sorted(self.por_status.items())},
                "segundos": round(self.segundos, 1)}


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

    def _espera(self, indice: int, esperas: Sequence[float] | None = None) -> float:
        esperas = tuple(esperas) if esperas else self.espera_s
        base = esperas[min(indice, len(esperas) - 1)]
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

    def consultar(self, corpo: Mapping[str, Any], *, rotulo: str = "consulta", ler: Callable[[Mapping[str, Any]], Any] | None = None,
                  tentativas: int | None = None, espera_s: Sequence[float] | None = None, parcial_ok: bool = False) -> Any:
        """Uma consulta, com as tentativas e as esperas do config (``tentativas``/``espera_s``
        trocam as da consulta: os lotes dos movimentos usam as de ``datajud.movimentos``). Devolve
        a resposta completa (ou o que ``ler`` tira dela: resposta que ``ler`` não entende vale como
        parcial e é pedida de novo) ou levanta :class:`FalhaDataJud` (:class:`DisjuntorAberto` sem
        pedir nada quando o DataJud já está parado nesta execução).

        ``parcial_ok`` (só a consulta por lote de números): HTTP 200 com algum shard falho, mas com
        os documentos dos que responderam, vale — cada documento vem inteiro do shard dele; o que
        falta é o que mora no shard que falhou. A resposta chega a ``ler`` com ``"_parcial": True``
        (quem chama não dá como ausente o que não veio). Para contagem, agregação e paginação,
        nunca."""
        if self.aberto:
            raise DisjuntorAberto(self.url, f"DataJud parado nesta execução: {self.motivo}")
        if self._inicio is None:
            self._inicio = self._relogio()
        self._conferir_robots()
        self.placar.consultas += 1
        ultimo: str = "sem tentativa"
        vezes = max(1, int(tentativas)) if tentativas else self.tentativas
        for indice in range(vezes):
            if indice:
                espera = self._espera(indice - 1, espera_s)
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
                    self.url, dict(corpo), validade_h=0.0, elasticsearch=not parcial_ok, timeout=self.timeout_s, retries=0,
                    cabecalhos={"Authorization": f"APIKey {self._chave}"},
                )
                self._conferir_status()
                parcial = False
                try:
                    resposta: Any = conferir_resposta(dados, self.url)
                except RespostaParcial:
                    if not (parcial_ok and _aproveitavel(dados)):
                        raise
                    parcial, resposta = True, {**dados, "_parcial": True}
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
                self.falhas_seguidas = 0
                if parcial:
                    self.placar.aproveitadas += 1
                    log.info("Tribunais: DataJud: %s: resposta parcial aproveitada na tentativa %d", rotulo, indice + 1)
                else:
                    self.placar.completas += 1
                    log.info("Tribunais: DataJud: %s: resposta completa na tentativa %d", rotulo, indice + 1)
                return resposta
            finally:
                fim = self._relogio()
                self._pedidos.append(fim)
                self.placar.segundos += max(0.0, fim - inicio)
            log.info("Tribunais: DataJud: %s, tentativa %d de %d: %s", rotulo, indice + 1, vezes, ultimo)
        self._falhou(rotulo, f"{vezes} tentativas sem resposta completa; a última: {ultimo}")

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

    def restante(self) -> float:
        """Segundos que sobram do prazo do DataJud nesta execução."""
        return self._restante()


# ── os movimentos dos processos na mesa ──────────────────────────────────────────────────────

# Conferido na TPU de movimentos (dump do SGT, versão 85 de 05/11/2026; data/tribunais/tpu/movimentos.json):
# 51 "Conclusão" fica em 14 Serventuário › 48 Escrivão/Diretor de Secretaria/Secretário Jurídico e
# não tem filhos; o texto do movimento é "Conclusos #{tipo_de_conclusao}" e o tipo vem no
# complemento tabelado (para despacho, para decisão, para julgamento, para admissibilidade
# recursal, ao revisor, para voto vista). Ato do magistrado: a raiz 1 "Magistrado" (675 códigos),
# com 3 "Decisão", 193 "Julgamento" e 11009 "Despacho". No índice do STJ aparecem 247 códigos; 2
# fora da TPU nacional (300154 e 300218, "Ato ordinatório").
CONCLUSAO = 51
RAIZ_MAGISTRADO = 1
RAIZES_DECISAO = (3, 193)  # Decisão e Julgamento (o despacho, 11009, é ato do magistrado, não decisão)
CAMPOS_MOVIMENTOS = ("numeroProcesso", "dataHoraUltimaAtualizacao", "orgaoJulgador.codigo", "movimentos.codigo", "movimentos.dataHora")
ARQUIVO_MOVIMENTOS = "movimentos.json"
_INSTANTE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?")
_NUMERO_CNJ = re.compile(r"^\d{20}$")


class Movimentos:
    """A árvore de movimentos da TPU (``data/tribunais/tpu/movimentos.json``): quem é ato do
    magistrado, quem é decisão ou julgamento, quem é a conclusão."""

    def __init__(self, itens: Iterable[Sequence[Any]]) -> None:
        self.pai: dict[int, int | None] = {}
        self.nome: dict[int, str] = {}
        for item in itens:
            codigo = int(item[0])
            self.pai[codigo] = int(item[1]) if item[1] is not None else None
            self.nome[codigo] = str(item[2])
        self._raiz: dict[int, tuple[int, ...]] = {}

    @classmethod
    def carregar(cls, pasta: Path | str) -> Movimentos:
        return cls(json.loads((Path(pasta) / ARQUIVO_MOVIMENTOS).read_text(encoding="utf-8")))

    def __contains__(self, codigo: object) -> bool:
        return codigo in self.pai

    def __len__(self) -> int:
        return len(self.pai)

    def cadeia(self, codigo: int) -> tuple[int, ...]:
        """Da raiz ao código (``51`` → ``(14, 48, 51)``); código fora da TPU: ``()``."""
        if codigo in self._raiz:
            return self._raiz[codigo]
        cadeia: list[int] = []
        atual: int | None = codigo
        while atual is not None and atual in self.pai and len(cadeia) < 20:
            cadeia.append(atual)
            atual = self.pai[atual]
        saida = tuple(reversed(cadeia)) if codigo in self.pai else ()
        self._raiz[codigo] = saida
        return saida

    def do_magistrado(self, codigo: int) -> bool:
        c = self.cadeia(codigo)
        return bool(c) and c[0] == RAIZ_MAGISTRADO

    def decisao(self, codigo: int) -> bool:
        c = self.cadeia(codigo)
        return len(c) >= 2 and c[0] == RAIZ_MAGISTRADO and c[1] in RAIZES_DECISAO


def _instante_mov(valor: Any, limite: str) -> str | None:
    """``"2026-07-30T13:44:49.000Z"`` → ``"2026-07-30T13:44:49"`` (o texto como o índice traz; a
    comparação é entre movimentos do mesmo processo). Data impossível (antes de 1980 ou depois de
    ``limite``; o índice tem movimento no ano 9260) → ``None``."""
    m = _INSTANTE.match(str(valor or ""))
    if not m:
        return None
    ano, mes, dia, hh, mm, ss = m.groups()
    texto = f"{ano}-{mes}-{dia}T{hh or '00'}:{mm or '00'}:{ss or '00'}"
    if texto[:4] < "1980" or texto[:10] > limite:
        return None
    return texto


def resumir_documento(fonte: Mapping[str, Any], mov: Movimentos, *, hoje: date, em: str | None = None) -> dict[str, Any] | None:
    """Um documento do DataJud → o registro compacto do diário (``None`` sem número CNJ válido).

    ``c``: dia da última Conclusão (51); ``a``/``ac``: dia e código do último ato do magistrado;
    ``d``: dia da última decisão ou julgamento; ``u``/``uc``: dia e código do último movimento;
    ``ab``: 1 quando a última conclusão veio **depois** do último ato do magistrado (o processo
    espera o relator desde ``c``); ``x``: 1 quando há movimento fora da TPU depois da última
    conclusão (não dá para dizer se foi ato do magistrado); ``upd``: ``dataHoraUltimaAtualizacao``;
    ``o``: o órgão (gabinete) do documento; ``em``: o dia da consulta."""
    numero = str(fonte.get("numeroProcesso") or "").strip()
    if not _NUMERO_CNJ.match(numero):
        return None
    limite = (hoje + timedelta(days=1)).isoformat()
    conclusao = ato = decisao = ultimo = None
    ato_cod = ultimo_cod = None
    instantes: list[tuple[str, int]] = []
    for m in fonte.get("movimentos") or ():
        if not isinstance(m, Mapping):
            continue
        quando = _instante_mov(m.get("dataHora"), limite)
        try:
            codigo = int(m.get("codigo"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if quando is None:
            continue
        instantes.append((quando, codigo))
        if ultimo is None or (quando, codigo) > (ultimo, ultimo_cod or 0):
            ultimo, ultimo_cod = quando, codigo
        if codigo == CONCLUSAO:
            conclusao = max(conclusao or quando, quando)
        elif mov.do_magistrado(codigo):
            if ato is None or quando > ato or (quando == ato and codigo > (ato_cod or 0)):
                ato, ato_cod = quando, codigo
            if mov.decisao(codigo):
                decisao = max(decisao or quando, quando)
    aberta = bool(conclusao and (ato is None or conclusao > ato))
    fora = bool(conclusao and any(q > conclusao and c not in mov for q, c in instantes))
    orgao = (fonte.get("orgaoJulgador") or {}).get("codigo") if isinstance(fonte.get("orgaoJulgador"), Mapping) else None
    upd = fonte.get("dataHoraUltimaAtualizacao")
    return {
        "n": numero, "c": conclusao[:10] if conclusao else None, "a": ato[:10] if ato else None, "ac": ato_cod,
        "d": decisao[:10] if decisao else None, "u": ultimo[:10] if ultimo else None, "uc": ultimo_cod,
        "ab": int(aberta), "x": int(fora), "upd": str(upd) if upd else None,
        "o": int(orgao) if isinstance(orgao, (int, str)) and str(orgao).lstrip("-").isdigit() else None,
        "em": em or hoje.isoformat(),
    }


def consulta_lote(numeros: Sequence[str], campos: Sequence[str] = CAMPOS_MOVIMENTOS) -> dict[str, Any]:
    """Os documentos de um lote de números CNJ (``terms`` no ``.keyword``: busca exata e barata, sem
    ordenação nem varredura), com o ``_source`` só do que a conta usa."""
    return {"size": len(numeros), "_source": list(campos), "query": {"terms": {"numeroProcesso.keyword": list(numeros)}}}


def consulta_incremental(desde: str, tamanho: int, depois: Sequence[Any] | None = None, *, campo: str = "dataHoraUltimaAtualizacao",
                         campos: Sequence[str] = CAMPOS_MOVIMENTOS) -> dict[str, Any]:
    """Os documentos atualizados depois de ``desde`` (``range`` em ``campo``), em ordem de
    atualização e de ``id`` (desempate estável), uma página de ``tamanho`` depois de ``depois``
    (o ``sort`` do último documento da página anterior: ``search_after``)."""
    corpo: dict[str, Any] = {
        "size": int(tamanho), "_source": list(campos), "query": {"range": {campo: {"gt": desde}}},
        "sort": [{campo: {"order": "asc"}}, {"id.keyword": {"order": "asc"}}],
    }
    if depois:
        corpo["search_after"] = list(depois)
    return corpo


def ler_lote(dados: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Os documentos de um lote e se a resposta veio completa (``False``: parcial aproveitada)."""
    return ler_documentos(dados), not dados.get("_parcial")


def ler_documentos(dados: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Os ``hits`` da resposta: ``[{"fonte": _source, "sort": [...]}]``. Fora do formato: ``TypeError``
    (o cliente pede de novo)."""
    hits = (dados.get("hits") or {}).get("hits")
    if not isinstance(hits, list):
        raise TypeError("resposta sem hits.hits")
    saida = []
    for h in hits:
        if not isinstance(h, Mapping) or not isinstance(h.get("_source"), Mapping):
            raise TypeError("documento sem _source")
        saida.append({"fonte": h["_source"], "sort": h.get("sort")})
    return saida


# ── o diário dos resultados e o estado (no cache, fora do git) ───────────────────────────────


class Guarda:
    """O que o DataJud trouxe de cada processo: ``<cache>/datajud/movimentos.jsonl``, uma linha por
    registro, a última vale. Cada lote é gravado assim que volta (o passo pode ser cortado pelo
    tempo-limite sem perder nada). Processo consultado e ausente do índice (em segredo, ou ainda
    não carregado pelo tribunal): ``{"n", "aus": 1, "em"}``."""

    def __init__(self, cache: Path | str) -> None:
        self.pasta = Path(cache) / "datajud"
        self.arquivo = self.pasta / "movimentos.jsonl"
        self.registros: dict[str, dict[str, Any]] = {}
        self.linhas = 0
        self._ler()

    def _ler(self) -> None:
        try:
            with self.arquivo.open(encoding="utf-8") as f:
                for linha in f:
                    linha = linha.strip()
                    if not linha:
                        continue
                    try:
                        r = json.loads(linha)
                    except ValueError:  # a última linha cortada no meio pelo tempo-limite
                        continue
                    if isinstance(r, dict) and r.get("n"):
                        self.registros[str(r["n"])] = r
                        self.linhas += 1
        except OSError:
            pass

    def __contains__(self, numero: object) -> bool:
        return numero in self.registros

    def __len__(self) -> int:
        return len(self.registros)

    def get(self, numero: str) -> dict[str, Any] | None:
        return self.registros.get(numero)

    def gravar(self, registros: Iterable[Mapping[str, Any]]) -> int:
        novos = [dict(r) for r in registros if r.get("n")]
        if not novos:
            return 0
        self.pasta.mkdir(parents=True, exist_ok=True)
        with self.arquivo.open("a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in novos))
            f.flush()
            os.fsync(f.fileno())
        for r in novos:
            self.registros[str(r["n"])] = r
        self.linhas += len(novos)
        return len(novos)

    def compactar(self, manter: Iterable[str] | None = None, *, folga: float = 1.5) -> bool:
        """Reescreve o diário com uma linha por processo quando ele passa de ``folga`` × os
        registros (ou quando ``manter`` tira os processos que saíram da mesa)."""
        if manter is not None:
            manter = set(manter)
            fora = [n for n in self.registros if n not in manter]
            for n in fora:
                del self.registros[n]
        else:
            fora = []
        if not fora and self.linhas <= folga * max(1, len(self.registros)):
            return False
        self.pasta.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.pasta, prefix=".movimentos-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for n in sorted(self.registros):
                    f.write(json.dumps(self.registros[n], ensure_ascii=False, separators=(",", ":")) + "\n")
            os.replace(tmp, self.arquivo)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self.linhas = len(self.registros)
        return True


class EstadoPrivado:
    """``<cache>/estado/datajud.json``: a última fonte e a conferência (o que o ``meta.json``
    público não guarda quando o DataJud não é publicado), a marca e o cursor do incremental, o
    histórico curto das execuções (quantos processos por execução: a estimativa de quantas faltam)."""

    def __init__(self, cache: Path | str) -> None:
        self.caminho = Path(cache) / "estado" / "datajud.json"
        try:
            dados = json.loads(self.caminho.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            dados = {}
        self.dados: dict[str, Any] = dados if isinstance(dados, dict) else {}

    @property
    def movimentos(self) -> dict[str, Any]:
        return self.dados.setdefault("movimentos", {})

    def gravar(self) -> None:
        try:
            self.caminho.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.caminho.parent, prefix=".datajud-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.dados, f, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, self.caminho)
        except OSError as exc:
            log.warning("Tribunais: estado do DataJud não gravado no cache (%s)", exc)


# ── o retrato do acervo do STJ (a mesa de hoje) ──────────────────────────────────────────────


def ler_retrato(caminho: Path | str) -> list[dict[str, Any]]:
    """As linhas do retrato compacto do acervo (``<cache>/retratos/stj-<data>.tsv.gz``, gravado por
    ``dados.Retrato``) que estão **na mesa** (``onde == "gabinete"``), com os campos da conta."""
    saida = []
    with gzip.open(caminho, "rt", encoding="utf-8", newline="") as f:
        for x in csv.DictReader(f, delimiter="\t"):
            if x.get("onde") != "gabinete":
                continue
            sd = x.get("sem_decisao_dias") or ""
            saida.append({
                "id": x.get("id") or "", "cnj": x.get("cnj") or "", "relator": x.get("relator") or "",
                "distribuicao": x.get("distribuicao") or None, "ultima_decisao": x.get("ultima_decisao") or None,
                "sem_decisao_dias": int(sd) if sd.lstrip("-").isdigit() else None,
                "segredo": x.get("segredo") == "1", "sobrestado": x.get("sobrestado") == "1",
            })
    return saida


def a_consultar(linhas: Iterable[Mapping[str, Any]]) -> list[str]:
    """Os números a pedir ao DataJud, na ordem de quem espera há mais tempo ("sem decisão" do
    acervo, do maior): os processos públicos (o índice público não tem os em segredo de justiça),
    não suspensos, com número CNJ válido e com a data da última distribuição ou decisão."""
    vistos: set[str] = set()
    fila = []
    for x in linhas:
        n = str(x.get("cnj") or "")
        if x.get("segredo") or x.get("sobrestado") or not _NUMERO_CNJ.match(n) or x.get("sem_decisao_dias") is None or n in vistos:
            continue
        vistos.add(n)
        fila.append((-int(x["sem_decisao_dias"]), n))
    fila.sort()
    return [n for _, n in fila]


# ── a carga (inicial em lotes, depois incremental) ───────────────────────────────────────────


@dataclass
class Carga:
    """O que a carga dos movimentos fez nesta execução (log, Summary, estimativa, relatório)."""

    pedidos_lote: int = 0  # lotes que voltaram (completos ou parciais aproveitados)
    parciais_lote: int = 0  # dos quais parciais: o que faltou volta para a fila
    falhas_lote: int = 0
    consultados: int = 0  # números pedidos em lotes que voltaram
    achados: int = 0
    ausentes: int = 0
    paginas: int = 0  # páginas do incremental que voltaram
    atualizados: int = 0  # documentos do incremental que eram da mesa
    incremental_fim: bool = False
    pendentes: int = 0  # números da mesa ainda sem registro, no fim
    conjunto: int = 0
    segundos: float = 0.0
    parou: str | None = None  # por que parou antes do fim (prazo, disjuntor)
    codigos_fora: Counter[int] = field(default_factory=Counter)  # movimentos fora da TPU vistos

    def texto(self) -> str:
        partes = [f"{self.consultados} consultados em {self.pedidos_lote} lotes ({self.achados} no índice, {self.ausentes} fora"
                  + (f"; {self.parciais_lote} lote(s) parcial(is) aproveitado(s)" if self.parciais_lote else "") + ")"]
        if self.falhas_lote:
            partes.append(f"{self.falhas_lote} lote(s) sem resposta")
        if self.paginas:
            partes.append(f"incremental: {self.paginas} página(s), {self.atualizados} da mesa" + (" (fim)" if self.incremental_fim else ""))
        partes.append(f"faltam {self.pendentes} de {self.conjunto}")
        if self.parou:
            partes.append(f"parou: {self.parou}")
        return "; ".join(partes)

    def para_json(self) -> dict[str, Any]:
        return {"lotes": self.pedidos_lote, "parciais_lote": self.parciais_lote, "falhas_lote": self.falhas_lote, "consultados": self.consultados, "achados": self.achados,
                "ausentes": self.ausentes, "paginas": self.paginas, "atualizados": self.atualizados, "incremental_fim": self.incremental_fim,
                "pendentes": self.pendentes, "conjunto": self.conjunto, "segundos": round(self.segundos, 1), "parou": self.parou,
                "codigos_fora": {str(c): n for c, n in sorted(self.codigos_fora.items())}}


def _cfg_movimentos(regras: Regras) -> dict[str, Any]:
    cfg = dict(regras.datajud.get("movimentos") or {})
    cfg.setdefault("ativo", True)
    cfg.setdefault("lote", 500)
    cfg.setdefault("pagina", 1000)
    cfg.setdefault("tentativas", 4)
    cfg.setdefault("espera_s", [5, 15, 30])
    cfg.setdefault("incremental_fracao", 0.5)
    cfg.setdefault("campo_incremental", "dataHoraUltimaAtualizacao")
    cfg.setdefault("margem_h", 24)
    cfg.setdefault("reconsultar_ausentes_dias", 7)
    cfg.setdefault("cobertura_minima", 0.80)
    cfg.setdefault("folga_s", 100)
    return cfg


def _normalizar_instante(valor: Any) -> str | None:
    """Um instante do índice (``2026-11-09T03:21:46.058000Z``, ``2025-03-01T22:50:21.309Z``) em
    ``AAAA-MM-DDTHH:MM:SSZ`` (UTC, sem fração): a marca do incremental compara como texto."""
    try:
        quando = datetime.fromisoformat(str(valor))
    except ValueError:
        return None
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=UTC)
    return quando.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _menos_horas(instante: str, horas: float) -> str:
    try:
        quando = datetime.fromisoformat(str(instante))
    except ValueError:
        return str(instante)
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=UTC)
    return (quando.astimezone(UTC) - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%SZ")


def carregar_movimentos(
    cliente: ClienteDataJud,
    guarda: Guarda,
    numeros: Sequence[str],
    estado: dict[str, Any],
    mov: Movimentos,
    regras: Regras,
    *,
    hoje: date,
    frescor: Mapping[str, Any] | None,
    gravar_estado: Callable[[], None] = lambda: None,
) -> Carga:
    """Uma fatia da carga: o incremental (quando o índice andou depois da marca) e os lotes dos
    números ainda sem registro, até o prazo do cliente ou o disjuntor.

    ``numeros``: a mesa de hoje, na ordem de prioridade (:func:`a_consultar`). ``estado``: o bloco
    ``movimentos`` do :class:`EstadoPrivado` (marca, cursor, início da carga), atualizado aqui e
    gravado por ``gravar_estado`` a cada página do incremental. ``frescor``: a resposta da C.0 de
    hoje (``max_atualizacao`` diz se o índice andou)."""
    cfg = _cfg_movimentos(regras)
    carga = Carga(conjunto=len(numeros))
    inicio = time.monotonic()
    folga = float(cfg["folga_s"])
    hoje_txt = hoje.isoformat()
    tamanho_lote = max(1, int(cfg["lote"]))
    tentativas, esperas = int(cfg["tentativas"]), tuple(float(x) for x in cfg["espera_s"])
    campo = str(cfg["campo_incremental"])
    alto = _normalizar_instante((frescor or {}).get("max_atualizacao")) if (frescor or {}).get("max_atualizacao") else None
    if estado.get("marca"):
        estado["marca"] = _normalizar_instante(estado["marca"]) or estado["marca"]
    if not estado.get("marca") and alto:
        # Primeira carga: tudo o que vier em lote é o índice de agora ou mais novo; o incremental
        # parte daqui quando a carga acabar.
        estado["marca"] = alto
        estado.setdefault("carga_desde", hoje_txt)
        gravar_estado()

    def tem_tempo() -> bool:
        return not cliente.aberto and cliente.restante() > folga

    def guardar(registros: list[dict[str, Any]]) -> None:
        guarda.gravar(registros)

    # 1. o incremental: só depois da carga inicial e quando o índice andou
    pendentes_antes = [n for n in numeros if n not in guarda]
    marca = estado.get("marca")
    if marca and alto and str(alto) > str(marca) and tem_tempo():
        estado.setdefault("alvo", alto)
        teto_inc = cliente.restante() * (float(cfg["incremental_fracao"]) if pendentes_antes else 1.0)
        inicio_inc = cliente.restante()
        conjunto = set(numeros)
        desde = _menos_horas(str(marca), float(cfg["margem_h"]))
        while tem_tempo() and (inicio_inc - cliente.restante()) < teto_inc:
            corpo = consulta_incremental(desde, int(cfg["pagina"]), estado.get("cursor"), campo=campo)
            try:
                docs = cliente.consultar(corpo, rotulo=f"incremental desde {desde[:10]}", ler=ler_documentos,
                                         tentativas=tentativas, espera_s=esperas)
            except FetchError as exc:
                carga.parou = cliente.motivo or str(exc)
                break
            carga.paginas += 1
            registros = []
            for d in docs:
                r = resumir_documento(d["fonte"], mov, hoje=hoje)
                if r and (r["n"] in conjunto or r["n"] in guarda):
                    registros.append(r)
            guardar(registros)
            carga.atualizados += len(registros)
            if len(docs) < int(cfg["pagina"]) or not docs or not docs[-1].get("sort"):
                estado["marca"], estado["cursor"] = estado.pop("alvo", alto), None
                carga.incremental_fim = True
                gravar_estado()
                break
            estado["cursor"] = docs[-1]["sort"]
            gravar_estado()

    # 2. os lotes: quem ainda não tem registro (e os ausentes antigos), na ordem de prioridade
    corte_aus = (hoje - timedelta(days=int(cfg["reconsultar_ausentes_dias"]))).isoformat()
    fila = deque(n for n in numeros if n not in guarda or ((guarda.get(n) or {}).get("aus") and str((guarda.get(n) or {}).get("em") or "") < corte_aus))
    voltas: Counter[str] = Counter()  # quantas vezes o número voltou para a fila (resposta parcial)
    while fila:
        if not tem_tempo():
            carga.parou = carga.parou or (cliente.motivo or "o prazo do DataJud nesta execução")
            break
        lote = [fila.popleft() for _ in range(min(tamanho_lote, len(fila)))]
        try:
            resposta = cliente.consultar(consulta_lote(lote), rotulo=f"lote de {len(lote)}", ler=ler_lote, tentativas=tentativas,
                                         espera_s=esperas, parcial_ok=True)
        except FetchError as exc:
            carga.falhas_lote += 1
            if cliente.aberto:
                carga.parou = cliente.motivo or str(exc)
                break
            continue
        docs, completa = resposta
        carga.pedidos_lote += 1
        carga.parciais_lote += 0 if completa else 1
        if not estado.get("marca"):
            # O frescor nunca respondeu: a marca do incremental é a atualização mais nova deste
            # primeiro lote (não passa da do índice inteiro; marca menor só faz o incremental reler mais).
            altos = [x for x in (_normalizar_instante(d["fonte"].get("dataHoraUltimaAtualizacao")) for d in docs) if x]
            if altos:
                estado["marca"], estado["marca_de"] = max(altos), "lote"
                estado.setdefault("carga_desde", hoje_txt)
                gravar_estado()
        registros = []
        vistos: set[str] = set()
        pedidos = set(lote)
        for d in docs:
            r = resumir_documento(d["fonte"], mov, hoje=hoje)
            if r and r["n"] in pedidos:
                registros.append(r)
                vistos.add(r["n"])
                for m in d["fonte"].get("movimentos") or ():
                    try:
                        c = int(m.get("codigo"))
                    except (TypeError, ValueError, AttributeError):
                        continue
                    if c not in mov:
                        carga.codigos_fora[c] += 1
        faltam = [n for n in lote if n not in vistos]
        if completa:  # só a resposta completa diz que o número não está no índice
            registros += [{"n": n, "aus": 1, "em": hoje_txt} for n in faltam]
            carga.ausentes += len(faltam)
            carga.consultados += len(lote)
        else:  # o que faltou mora num shard que não respondeu: volta para o fim da fila (duas vezes no máximo)
            carga.consultados += len(vistos)
            for n in faltam:
                voltas[n] += 1
                if voltas[n] <= 2:
                    fila.append(n)
        guardar(registros)
        carga.achados += len(vistos)
    carga.pendentes = sum(1 for n in numeros if n not in guarda)
    carga.segundos = time.monotonic() - inicio
    historico = list(estado.get("historico") or [])[-9:]
    historico.append({"dia": hoje_txt, "consultados": carga.consultados, "lotes": carga.pedidos_lote, "falhas": carga.falhas_lote,
                      "paginas": carga.paginas, "segundos": round(carga.segundos)})
    estado["historico"] = historico
    gravar_estado()
    return carga


def execucoes_restantes(pendentes: int, historico: Sequence[Mapping[str, Any]]) -> int | None:
    """Quantas execuções até a carga inicial cobrir a mesa, pela média de processos consultados
    nas últimas execuções que consultaram algum (``None`` sem base)."""
    if pendentes <= 0:
        return 0
    feitos = [int(h.get("consultados") or 0) for h in historico if int(h.get("consultados") or 0) > 0]
    if not feitos:
        return None
    media = sum(feitos) / len(feitos)
    return -(-pendentes // max(1, int(media)))


# ── a fonte no meta.json ─────────────────────────────────────────────────────────────────────


@dataclass
class Resultado:
    """O que :func:`atualizar` devolve para ``dados.py``."""

    fonte: Fonte  # meta.json → fontes.datajud (sem número quando não publicado)
    conferencia: dict[str, Any] | None  # meta.json → cobertura.stj.datajud (só publicado)
    placar: Placar
    mudou: bool  # a conferência foi refeita com dados novos do DataJud
    log: str  # uma linha para o log e o Summary (só números de operação: pedidos, falhas, processos)
    publicar: bool = False
    linhas: list[dict[str, Any]] | None = None  # a mesa de hoje (o retrato), para contas.camada_cnj
    registros: Mapping[str, Mapping[str, Any]] | None = None  # numero CNJ → registro do diário
    carga: Carga | None = None
    frescor: dict[str, Any] | None = None  # max_timestamp e max_atualizacao do índice (a idade dele)
    execucoes: int | None = None  # execuções até a carga inicial cobrir a mesa (estimativa)
    coletado_em: str | None = None  # a última execução que trouxe movimento novo (não muda sem dado novo)


def deve_publicar(regras: Regras, ambiente: Mapping[str, str] | None = None) -> bool:
    """A camada do CNJ vai à página? ``QIJ_DATAJUD_PUBLICAR`` (``1``/``0``) numa execução; sem ela,
    ``datajud.publicar`` do config (padrão ``false``: o repositório é público e o item 3.8 do termo
    veda distribuir informação derivada)."""
    ambiente = os.environ if ambiente is None else ambiente
    valor = str(ambiente.get(VARIAVEL_PUBLICAR) or "").strip().lower()
    if valor in ("1", "true", "sim", "yes", "on"):
        return True
    if valor in ("0", "false", "nao", "não", "no", "off"):
        return False
    return bool(regras.datajud.get("publicar", False))


def fonte_desligada(regras: Regras) -> Fonte:
    """A entrada ``fontes.datajud`` com o DataJud desligado no config (nenhum pedido é feito)."""
    return {
        "nome": NOME, "trib": "cnj", "url": URL_LEITOR, "data_dados": None, "coletado_em": None, "ok": None, "ativo": False,
        "aviso": AVISO_DESLIGADO, "registros": None, "velho_dias": int(regras.datajud.get("velho_dias") or 15),
    }


def fonte_pessoal(regras: Regras) -> Fonte:
    """A entrada ``fontes.datajud`` do ``meta.json`` público com o DataJud ligado para uso pessoal:
    nenhum número, nenhuma data (a mesma em toda execução: não regrava o arquivo)."""
    return cast(Fonte, {
        "nome": NOME, "trib": "cnj", "url": URL_LEITOR, "data_dados": None, "coletado_em": None, "ok": None, "ativo": True,
        "publicado": False, "aviso": AVISO_PESSOAL, "registros": None, "velho_dias": int(regras.datajud.get("velho_dias") or 15),
    })


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


def _publico(r: Resultado, regras: Regras) -> Resultado:
    """Sem publicar: a fonte do ``meta.json`` vira a de uso pessoal (sem número nem data) e a
    conferência fica só no cache. Publicado: a fonte leva o selo e a atribuição."""
    if r.publicar:
        if r.fonte.get("ativo") and not r.fonte.get("publicado"):
            r.fonte = cast(Fonte, {**r.fonte, "publicado": True, "atribuicao": ATRIBUICAO})
        return r
    r.fonte, r.conferencia = fonte_pessoal(regras), None
    return r


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
    cache: Path | str | None = None,
    retrato: Path | str | None = None,
    pasta_tpu: Path | str | None = None,
    publicar: bool | None = None,
    prazo_s: float | None = None,
    **kw: Any,
) -> Resultado:
    """A rodada do DataJud numa execução (a última da ordem STJ → STF → DataJud).

    Desligado no config: nenhum pedido; a fonte sai como em :func:`fonte_desligada`. Ligado: a C.0
    (frescor) primeiro; se ``max(@timestamp)`` é o mesmo da conferência anterior, a conferência é
    refeita com o acervo do STJ do dia sem pedir os ativos; senão, os ativos por gabinete. Depois,
    com ``cache`` e ``retrato`` (o acervo de hoje), a fatia da carga dos movimentos
    (:func:`carregar_movimentos`). Falha (qualquer :class:`FalhaDataJud`) ou total que muda mais que
    ``variacao_max`` (10%) contra o anterior: a fonte fica ``ok: false`` com o aviso e a conferência
    anterior fica como estava.

    ``publicar`` (padrão: :func:`deve_publicar`): sem ele, a fonte devolvida é a de uso pessoal
    (:func:`fonte_pessoal`) e ``conferencia`` vem ``None``; o que o DataJud trouxe fica no cache
    (``<cache>/estado/datajud.json`` e o diário dos movimentos). Com ``cache``, a fonte e a
    conferência anteriores vêm do estado privado (o ``meta.json`` público não as tem).

    ``stj_por_ministro``/``total_stj``: o acervo do STJ do dia por ``relator_id`` e o total (ou
    ``painel_stj``: o ``stj.json`` montado). ``prazo_s``: o teto do DataJud nesta execução (padrão:
    ``datajud.prazo_s``). ``kw`` vai ao cliente (``relogio``, ``dormir``, ``aleatorio``...)."""
    agora = agora or datetime.now(UTC)
    instante = agora.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    hoje = hoje or agora.date()
    publicar = deve_publicar(regras, ambiente) if publicar is None else bool(publicar)
    if not regras.datajud.get("ativo"):
        return Resultado(fonte_desligada(regras), None, Placar(), False, "DataJud: desligado no config (nenhum pedido)", publicar=False)
    estado = EstadoPrivado(cache) if cache is not None else None
    if estado is not None:
        anterior_fonte = estado.dados.get("fonte") or anterior_fonte
        anterior_conferencia = estado.dados.get("conferencia") or anterior_conferencia
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
            if prazo_s is not None:
                kw.setdefault("prazo_s", max(0.0, float(prazo_s)))
            cliente = ClienteDataJud.do_config(regras, coletor, ambiente=ambiente, **kw)
        except ValueError as exc:  # ligado sem chave no config nem na variável
            base["aviso"] = f"O DataJud está ligado sem chave ({exc}); a página não tem número do DataJud."
            log.warning("Tribunais: DataJud: %s", exc)
            return _publico(Resultado(base, dict(anterior_conferencia) if anterior_conferencia else None, Placar(), False,
                                      f"DataJud: sem chave ({exc})", publicar=publicar), regras)

    def guardar(fonte: Fonte, conferencia: Mapping[str, Any] | None) -> None:
        if estado is not None:
            estado.dados["fonte"] = {k: v for k, v in fonte.items() if k not in ("publicado", "atribuicao")}
            if conferencia is not None:
                estado.dados["conferencia"] = dict(conferencia)
            estado.gravar()

    def com_movimentos(r: Resultado, frescor: Mapping[str, Any] | None) -> Resultado:
        """A fatia da carga dos movimentos (quando há cache, retrato e a árvore da TPU)."""
        r.frescor = dict(frescor) if frescor else (estado.dados.get("frescor") if estado is not None else None)
        cfg = _cfg_movimentos(regras)
        if estado is None or retrato is None or not cfg.get("ativo"):
            return _publico(r, regras)
        try:
            linhas = ler_retrato(retrato)
        except (OSError, EOFError, ValueError, csv.Error) as exc:
            log.warning("Tribunais: DataJud: retrato do acervo ilegível (%s); os movimentos ficam para a próxima", exc)
            return _publico(r, regras)
        from . import tpu as tpu_mod  # a pasta padrão da TPU

        pasta = Path(pasta_tpu) if pasta_tpu is not None else tpu_mod.PASTA
        try:
            mov = Movimentos.carregar(pasta)
        except (OSError, ValueError) as exc:
            log.warning("Tribunais: DataJud: sem a árvore de movimentos da TPU (%s); os movimentos ficam para a próxima", exc)
            return _publico(r, regras)
        guarda = Guarda(cache)  # type: ignore[arg-type]
        numeros = a_consultar(linhas)
        carga: Carga | None = None
        if not cliente.aberto:
            carga = carregar_movimentos(cliente, guarda, numeros, estado.movimentos, mov, regras, hoje=hoje,
                                        frescor=frescor or estado.dados.get("frescor"), gravar_estado=estado.gravar)
            # O diário guarda a mesa de hoje e, por 90 dias, quem saiu dela (pode voltar: redistribuição).
            corte = (hoje - timedelta(days=90)).isoformat()
            guarda.compactar(set(numeros) | {n for n, g in guarda.registros.items() if not g.get("aus") and str(g.get("em") or "") >= corte})
            r.log += f"; movimentos: {carga.texto()}"
            if carga.consultados or carga.atualizados:
                estado.movimentos["coletado_em"] = instante
                estado.gravar()
        r.coletado_em = estado.movimentos.get("coletado_em")
        r.linhas, r.registros, r.carga = linhas, guarda.registros, carga
        pendentes = sum(1 for n in numeros if n not in guarda)
        r.execucoes = execucoes_restantes(pendentes, estado.movimentos.get("historico") or [])
        return _publico(r, regras)

    def falha(motivo: str) -> Resultado:
        # O aviso (vai ao meta.json) tem só a data: falhar de novo no mesmo dia não regrava o
        # arquivo. O motivo e o placar vão ao log e ao Summary.
        base["ok"] = False
        dados_de = f"ficam os números de {_br(base['data_dados'])}" if base["data_dados"] else "a página não tem número do DataJud"
        base["aviso"] = f"O DataJud não deu resposta completa em {hoje.strftime('%d/%m')}; {dados_de}."
        linha = f"DataJud: falhou ({motivo}), {cliente.placar.texto()}; {dados_de}"
        log.warning("Tribunais: %s", linha)
        guardar(base, None)
        return Resultado(base, dict(anterior_conferencia) if anterior_conferencia else None, cliente.placar, False, linha, publicar=publicar)

    def com_aviso_de_atraso(fonte: Fonte) -> Fonte:
        if fonte.get("data_dados"):
            dias = (hoje - date.fromisoformat(str(fonte["data_dados"]))).days
            if dias > velho_dias:
                fonte["aviso"] = f"Os dados do CNJ vão até {_br(fonte['data_dados'])}/{str(fonte['data_dados'])[:4]} ({dias} dias)."
        return fonte

    try:
        frescor = cliente.frescor()
    except FetchError as exc:
        return com_movimentos(falha(cliente.motivo or _motivo(exc)), None)
    if not frescor["max_timestamp"]:
        return com_movimentos(falha("a resposta do frescor veio sem data"), None)
    if estado is not None:
        estado.dados["frescor"] = dict(frescor)
    mesmo_indice = bool(anterior_conferencia) and anterior_conferencia.get("max_timestamp") == frescor["max_timestamp"]  # type: ignore[union-attr]
    if mesmo_indice:
        assert anterior_conferencia is not None
        conferencia = {"max_timestamp": frescor["max_timestamp"], "data_dados": frescor["data"],
                       **conferir(_de_anterior(anterior_conferencia), composicao, stj_por_ministro, total_stj=total_stj)}
        base["ok"], base["data_dados"], base["registros"] = True, frescor["data"], conferencia["total_datajud"]
        base["coletado_em"] = base["coletado_em"] or instante
        linha = f"DataJud: sem carga nova desde {_br(frescor['data'])} (1 consulta); conferência refeita com o acervo do STJ do dia"
        log.info("Tribunais: %s", linha)
        fonte = com_aviso_de_atraso(base)
        guardar(fonte, conferencia)
        return com_movimentos(Resultado(fonte, conferencia, cliente.placar, False, linha, publicar=publicar), frescor)
    # Índice novo: os movimentos primeiro (o ganho de verdade), a conferência (uma agregação pesada
    # sobre o índice inteiro) com o prazo que sobrar.
    mov = com_movimentos(Resultado(base, None, cliente.placar, False, "", publicar=True), frescor)
    sufixo = mov.log

    def com_mov(r: Resultado) -> Resultado:
        r.linhas, r.registros, r.carga, r.frescor, r.execucoes, r.coletado_em = (mov.linhas, mov.registros, mov.carga, mov.frescor,
                                                                                 mov.execucoes, mov.coletado_em)
        r.log += sufixo
        return _publico(r, regras)

    if cliente.aberto:
        return com_mov(falha(cliente.motivo or "o DataJud parou nesta execução"))
    if cliente.restante() <= float(_cfg_movimentos(regras)["folga_s"]):
        # O frescor respondeu e os movimentos usaram o prazo: a conferência fica para a próxima.
        base["ok"], base["data_dados"] = True, frescor["data"]
        base["coletado_em"] = base["coletado_em"] or instante
        linha = f"DataJud: índice até {_br(frescor['data'])}; a conferência (ativos por gabinete) fica para a próxima execução"
        fonte = com_aviso_de_atraso(base)
        guardar(fonte, None)
        return com_mov(Resultado(fonte, dict(anterior_conferencia) if anterior_conferencia else None, cliente.placar, False, linha,
                                 publicar=publicar))
    try:
        ativos = cliente.ativos_por_gabinete()
    except FetchError as exc:
        return com_mov(falha(cliente.motivo or _motivo(exc)))
    antes = int((anterior_conferencia or {}).get("total_datajud") or 0)
    limite = float(regras.datajud.get("variacao_max") or 0.10)
    if antes and abs(ativos["total"] / antes - 1) > limite:
        return com_mov(falha(f"o total de ativos foi de {antes} para {ativos['total']}, variação acima de {limite:.0%}"))
    conferencia = {"max_timestamp": frescor["max_timestamp"], "data_dados": frescor["data"],
                   **conferir(ativos, composicao, stj_por_ministro, total_stj=total_stj)}
    base["ok"], base["data_dados"], base["coletado_em"], base["registros"] = True, frescor["data"], instante, ativos["total"]
    linha = (f"DataJud: carga de {_br(frescor['data'])}, {ativos['total']} ativos em {len(ativos['gabinetes'])} órgãos, "
             f"{cliente.placar.texto()}")
    log.info("Tribunais: %s", linha)
    fonte = com_aviso_de_atraso(base)
    guardar(fonte, conferencia)
    return com_mov(Resultado(fonte, conferencia, cliente.placar, True, linha, publicar=publicar))
