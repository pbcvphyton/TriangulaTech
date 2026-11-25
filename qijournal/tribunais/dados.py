"""A atualização da aba Tribunais: coleta, sanidade, contas e gravação de ``data/tribunais/``.

:func:`atualizar` roda na ordem do brief: a TPU (só com ``completo``), o **STJ** (acervo, pautas,
precedentes, atas e íntegras), o **STF** (planilha do acervo, atas, jurisprudência, repercussão
geral e pauta) e, por último, o **DataJud** (ligado para uso pessoal; nunca no caminho crítico): os
movimentos dos processos na mesa do STJ vão ao cache e viram a camada do CNJ (``cnj.json``, o
"concluso há N dias") **só** com ``datajud.publicar`` ou ``QIJ_DATAJUD_PUBLICAR=1``; sem isso, nada
derivado do DataJud entra em ``data/tribunais/`` (o repositório é público; termo de uso, item 3.8).

Regras da casa (as mesmas do Congresso, mais estritas onde o dado é mais frágil):

- **Fonte que falha vira aviso**, nunca erro: o comando sai 0 e o ``meta.json`` diz, fonte a fonte,
  a data do dado em uso (``data_dados``), quando ele foi coletado (``coletado_em``) e se a última
  tentativa deu certo (``ok``). Só erro de programa sobe.
- **O dia bom não é sobrescrito**: o acervo do dia com total fora de 0,85 a 1,15 do último dia bom,
  com data anterior à dele ou com problema de formato (``stj.ResumoAcervo.problemas``) é recusado e
  o painel daquele tribunal fica como estava (o ``stj.json``/``stf.json``, os dossiês e os achados
  da véspera). Fonte acessória que falha usa o que a última coleta boa deixou no cache
  (``<cache>/estado/``); sem isso, o bloco dela sai ``null`` com o aviso.
- **Gravação de uma vez**: tudo é montado e conferido em memória (tetos de 300 KB por JSON e 3 MB
  na pasta); o que mudou vai para uma pasta temporária e troca de lugar com ``os.replace``, os
  dossiês primeiro e o ``meta.json`` por último. Mesmos dados, mesmos bytes: rodar de novo não
  regrava nada.
- **Fora do git, no cache**: os arquivos baixados (o coletor), o estado da última coleta boa de cada
  fonte (``estado/``), o retrato compacto do acervo de hoje e da véspera (``retratos/``, para a
  comparação dia a dia) e o diário de cada pedido (``diario/``, artefato do fluxo).
- **A agenda da semana** (``agenda.json``, no formato de ``qijournal/agenda/contrato.py``) sai das
  pautas lidas nesta execução, ao lado dos achados, e **não depende do painel**: com o acervo
  recusado, a pauta é lida do mesmo jeito. Ela mantém a parte do tribunal que não foi refeito e as
  sessões já passadas da semana (as fontes só publicam pautas futuras), tem teto próprio (60 KB)
  e nunca impede os painéis: qualquer falha dela vira aviso de operação e fica o arquivo anterior;
  se a pasta chegar ao teto, quem não é gravada é ela.
- **Séries acumuladas**: as atas de distribuição dos últimos 30 dias e as íntegras dos 12 meses até
  o último arquivo publicado ficam no cache (cada arquivo tem endereço próprio e não muda); uma
  execução comum baixa só as mais novas (``SERIE_MAXIMO``), ``completo`` baixa o que faltar até o
  prazo.

O prazo interno (``PRAZO_S``) fica abaixo do tempo-limite do passo no ``tribunais.yml`` (30 min; 75
com ``completo``): as séries param de baixar quando sobra só a ``RESERVA_S`` para o STF, as contas
e a página; o que faltou fica para a próxima execução.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as hora
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError

from .. import net
from ..agenda import contrato as contrato_agenda
from ..agenda import regras as regras_agenda
from ..congresso.dados import _gravar, _json
from ..net import FetchError
from . import PASTA, VERSAO, contas, datajud, modelo, stf, stj
from . import achados as achados_mod
from . import agenda as pauta_mod
from . import djen as djen_mod
from . import tpu as tpu_mod
from .areas import Classificador
from .fontes import CaminhoVedado, ColetorTribunais, PrazoEsgotado, RecusaDoHost
from .modelo import Regras
from .partes import carregar_catalogo
from .tpu import TPU

log = logging.getLogger(__name__)

META = "meta.json"
ACHADOS = "achados.json"
# A agenda da semana (STF e STJ), no formato de qijournal/agenda/contrato.py. O nome AGENDA, mais
# abaixo, já é o calendário das fontes: este é PAUTA_SEMANAL.
PAUTA_SEMANAL = contrato_agenda.ARQUIVO  # data/tribunais/agenda.json
FONTES_DA_PAUTA = {"stf": ("stf_pauta",), "stj": ("stj_pautas", "stj_precedentes")}  # as fontes que a agenda declara, por tribunal
CNJ = "cnj.json"  # a camada do DataJud: só existe com datajud.publicar (ou QIJ_DATAJUD_PUBLICAR=1)
DJEN = "djen.json"  # a camada do DJe (DJEN): escrita só pela máquina do dono, no Brasil (djen.py); aqui só é lida
NOME_DJEN = "CNJ · DJe (DJEN)"
SEM_DJEN = "Coletado só no Brasil (a API do CNJ recusa quem está fora do país), na máquina do dono; ainda sem coleta."
RESERVA_DATAJUD_S = 5 * 60  # o que o DataJud deixa do prazo para a camada, a gravação e a página
MINISTROS = "ministros"
PASTA_TPU = "tpu"
TETO_JSON = 300_000  # contrato §9: cada JSON de data/tribunais/
TETO_PASTA = 3_000_000  # contrato §9: a pasta inteira (com a TPU)
SANIDADE = (0.85, 1.15)  # o total do acervo do dia contra o do último dia bom (limiares.recusa_arquivo_menor_que/maior_que)
PRAZO_S = {False: 25 * 60, True: 70 * 60}  # abaixo dos 30/75 min do passo no tribunais.yml
RESERVA_S = 10 * 60  # o que as séries do STJ deixam para o STF, as contas e a página
SERIE_MAXIMO = {False: {"atas": 6, "integras": 4}, True: {"atas": None, "integras": None}}  # downloads novos por execução
RETRATOS = 2  # retratos compactos do acervo guardados por tribunal (hoje e a véspera)
CAMPOS_INTEGRA = ("arquivo", "data", "registro", "id", "relator_id", "fora", "documento", "interno")  # o que contas.py lê

IDS_FONTES = (
    "stj_acervo", "stj_atas", "stj_pautas", "stj_precedentes", "stj_integras",
    "stf_acervo", "stf_atas", "stf_jurisprudencia", "stf_rg", "stf_pauta", "tpu", "datajud", "djen",
)  # fmt: skip
NOMES_FONTES = {
    "stj_acervo": "STJ · acervo em tramitação",
    "stj_atas": "STJ · atas de distribuição",
    "stj_pautas": "STJ · pautas de julgamento",
    "stj_precedentes": "STJ · precedentes qualificados",
    "stj_integras": "STJ · decisões publicadas (íntegras)",
    "stf_acervo": "STF · acervo (lista geral)",
    "stf_atas": "STF · atas de distribuição",
    "stf_jurisprudencia": "STF · pesquisa de jurisprudência",
    "stf_rg": "STF · repercussão geral",
    "stf_pauta": "STF · pauta das sessões",
    "tpu": "CNJ · Tabelas Processuais Unificadas",
}
CHAVE_VELHO = {  # fonte → chave de fontes.<trib>.velho_dias
    "stj_acervo": "acervo", "stj_atas": "atas", "stj_pautas": "pautas", "stj_precedentes": "precedentes", "stj_integras": "integras",
    "stf_acervo": "acervo", "stf_atas": "atas", "stf_jurisprudencia": "jurisprudencia", "stf_rg": "rg", "stf_pauta": "pauta",
}  # fmt: skip
VELHO_TPU = 120
# Quando cada fonte publica o dado seguinte (dias depois da data do dado em uso, hora UTC): o
# arquivo do acervo do STJ de D sai em D+1 à tarde (17:37 UTC no de 20/11/2026); a ata de D, às
# 22:30 UTC de D; a planilha do STF de D, às 17:40 UTC de D. Fonte atrasada: agora + 2 h.
# O STJ não publica acervo de sábado nem de domingo (o de sexta sai no sábado, o de segunda só na
# terça): o próximo dado do acervo é o do dia da semana seguinte, um dia depois (DIAS_DA_SEMANA).
AGENDA = {"stj_acervo": (2, hora(17, 45)), "stj_atas": (1, hora(22, 40)), "stf_acervo": (1, hora(17, 45))}
DIAS_DA_SEMANA = frozenset({"stj_acervo"})  # na AGENDA: o dado seguinte é o do próximo dia de segunda a sexta
ATRASADA_H = 2
PROXIMA_MIN_H, PROXIMA_MAX_H = 1, 30  # o "decidir" ignora proxima_execucao a mais de 36 h
# Erros de formato do lado da fonte (planilha corrompida, XML quebrado): viram aviso como a falha de rede.
ERROS_DE_FONTE: tuple[type[BaseException], ...] = (FetchError, stf.ErroDeFormato, zipfile.BadZipFile, ParseError, OSError, ValueError)


@dataclass
class Opcoes:
    """O que a linha de comando passa a :func:`atualizar`."""

    hoje: str  # data local do jornal (America/Sao_Paulo)
    agora: datetime | None = None  # instante da execução (UTC); padrão: o relógio
    completo: bool = False  # refaz as séries e confere a versão da TPU
    so: str | None = None  # "stj" ou "stf": só um tribunal (o outro fica como está)
    prazo_s: float | None = None  # padrão: PRAZO_S[completo]
    ambiente: Mapping[str, str] | None = None  # variáveis (a chave do DataJud); padrão: os.environ
    aceitar: tuple[str, ...] = ()  # "stj"/"stf": aceita uma vez o acervo fora da faixa da sanidade (mudança real, conferida à mão)


@dataclass
class Resultado:
    """O resumo de uma execução (log, ``$GITHUB_STEP_SUMMARY`` e testes)."""

    pasta: Path
    gravados: int = 0
    apagados: int = 0
    avisos: list[str] = field(default_factory=list)  # os de meta.json e os só de operação
    refeitos: list[str] = field(default_factory=list)  # tribunais com o painel refeito nesta execução
    numeros: dict[str, dict[str, Any]] = field(default_factory=dict)  # por tribunal: acervo, na mesa, ministros...
    fontes: dict[str, dict[str, Any]] = field(default_factory=dict)  # meta.json → fontes
    requisicoes: int = 0
    megabytes: float = 0.0
    segundos: float = 0.0
    datajud: str = ""


@dataclass
class Leitura:
    """O que uma fonte trouxe nesta execução ou, quando falhou, o que a última coleta boa deixou."""

    dados: Any = None  # o que vai às contas (None: nada)
    data_dados: str | None = None
    registros: int | None = None
    ok: bool | None = True  # False: falhou e valem os dados de data_dados (ou nenhum)
    aviso: str | None = None
    coletado_em: str | None = None  # só quando os dados vêm do estado (a coleta que os trouxe)
    extra: dict[str, Any] = field(default_factory=dict)  # atraso_dias…


@dataclass
class Tribunal:
    """O que um tribunal produziu: painel, dossiês, cobertura, sinais e as leituras das fontes."""

    trib: str
    leituras: dict[str, Leitura] = field(default_factory=dict)
    painel: dict[str, Any] | None = None  # None: o painel da véspera fica
    dossies: dict[str, dict[str, Any]] = field(default_factory=dict)
    cobertura: dict[str, Any] | None = None
    sinais: dict[str, Any] | None = None
    retrato: Path | None = None
    # O que a agenda da semana usa, guardado à parte porque NÃO depende do painel (um painel recusado
    # troca ``leituras``): ``leituras`` (as da pauta e dos temas), ``pautas`` e ``temas`` (o que as
    # contas também recebem) e ``acervo`` (contas.cruzamento_da_pauta, quando houve contas).
    semana: dict[str, Any] = field(default_factory=dict)


# ── utilidades ───────────────────────────────────────────────────────────────


def pasta_de(data_dir: Path) -> Path:
    return Path(data_dir) / PASTA


def ler_json(caminho: Path) -> Any | None:
    try:
        return json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _br(iso: str | None, ano: bool = True) -> str:
    if not iso:
        return "—"
    iso = str(iso)[:10]
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if ano else f"{iso[8:10]}/{iso[5:7]}"


def dias_da_semana(de: date, ate: date) -> int:
    """Quantos dias de segunda a sexta há depois de ``de`` até ``ate`` (inclusive): a idade de uma
    fonte que não publica no fim de semana (feriado conta: o STJ publicou o acervo de 20/11)."""
    if ate <= de:
        return 0
    return sum(1 for i in range(1, (ate - de).days + 1) if (de + timedelta(days=i)).weekday() < 5)


def _milhar(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _instante(momento: datetime) -> str:
    return momento.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _motivo(exc: BaseException) -> str:
    """O motivo de uma falha, curto e sem hora (vai ao meta.json: não pode mudar a cada execução)."""
    if isinstance(exc, PrazoEsgotado):
        return "o prazo da coleta acabou antes; fica para a próxima"
    if isinstance(exc, RecusaDoHost):
        return "o portal recusou o acesso (403) duas vezes"
    if isinstance(exc, CaminhoVedado):
        return "o robots.txt do tribunal veda o endereço"
    if isinstance(exc, FetchError):
        status = getattr(exc, "status", None)
        if isinstance(exc, stj.FormatoInesperado) or (status is None and "formato" in str(exc).lower()):
            return "o arquivo veio fora do formato esperado"
        if status:
            return f"o servidor respondeu {status}"
        return "o servidor não respondeu"
    if isinstance(exc, (stf.ErroDeFormato, zipfile.BadZipFile, ParseError, ValueError)):
        return "o arquivo veio fora do formato esperado"
    return "erro na leitura"


def _relativo(caminho: Path, raiz: Path) -> str:
    try:
        return str(Path(caminho).resolve().relative_to(Path(raiz).resolve()))
    except ValueError:
        return str(caminho)


def _no_cache(coletor: ColetorTribunais, url: str) -> Path | None:
    """O arquivo de ``url`` que o coletor guardou, vencido ou não (``None`` se não há)."""
    from urllib.parse import urlsplit

    sufixo = "".join(Path(urlsplit(url).path).suffixes[-2:]) or ".bin"
    caminho = coletor._caminho("arquivos", url, sufixo)  # o mesmo endereço que arquivo() usa
    return caminho if caminho.is_file() else None


PASTAS_CITAVEIS = frozenset({"arquivos", "api", "paginas", "robots", "retratos", "datajud"})  # o que um estado pode citar no cache


class Estado:
    """A última coleta boa de cada fonte, no cache (``<cache>/estado/<fonte>.json``): o que o
    fallback usa quando a fonte falha. Fica fora do git; perder o cache só perde o fallback."""

    def __init__(self, cache: Path) -> None:
        self.raiz = Path(cache)
        self.pasta = self.raiz / "estado"

    def ler(self, fid: str) -> dict[str, Any] | None:
        dados = ler_json(self.pasta / f"{fid}.json")
        return dados if isinstance(dados, dict) else None

    def gravar(self, fid: str, dados: Mapping[str, Any]) -> None:
        try:
            _gravar(self.pasta / f"{fid}.json", json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError as exc:  # sem o estado, só o fallback de amanhã se perde
            log.warning("Tribunais: estado de %s não gravado no cache (%s)", fid, exc)

    def caminho(self, relativo: str | None) -> Path | None:
        if not relativo:
            return None
        caminho = self.raiz / relativo
        return caminho if caminho.is_file() else None

    def citados(self) -> set[Path]:
        """Os arquivos do cache que algum estado cita (caminho, série, temas...): a poda não os toca."""
        saida: set[Path] = set()

        def andar(x: Any) -> None:
            if isinstance(x, str):
                # Só o que tem cara de caminho relativo do cache ("arquivos/…"): o estado também guarda
                # dados (temas da repercussão geral, com "/" no título), que não são arquivo nenhum.
                if len(x) < 400 and "\n" not in x and x.split("/", 1)[0] in PASTAS_CITAVEIS:
                    try:
                        c = self.caminho(x)
                    except OSError:
                        c = None
                    if c is not None:
                        saida.add(c)
            elif isinstance(x, Mapping):
                for v in x.values():
                    andar(v)
            elif isinstance(x, (list, tuple)):
                for v in x:
                    andar(v)

        for arquivo in sorted(self.pasta.glob("*.json")) if self.pasta.is_dir() else ():
            andar(ler_json(arquivo))
        return saida


class Retrato:
    """O retrato compacto do acervo do dia (uma linha por processo, sem parte nenhuma), gravado no
    cache enquanto as contas leem o acervo: hoje e a véspera ficam, para a comparação dia a dia
    ("o que saiu da mesa", fase 2)."""

    CAMPOS = ("id", "registro", "cnj", "relator", "orgao", "onde", "classe", "area", "assunto", "distribuicao", "ultima_decisao",
              "andamento", "sem_decisao_dias", "concluso_dias", "segredo", "sobrestado")

    def __init__(self, cache: Path, trib: str, data: str | None) -> None:
        self.pasta = Path(cache) / "retratos"
        self.trib, self.data = trib, data or "sem-data"
        self.final = self.pasta / f"{trib}-{self.data}.tsv.gz"
        self.pasta.mkdir(parents=True, exist_ok=True)
        fd, nome = tempfile.mkstemp(dir=self.pasta, prefix=f".{trib}-", suffix=".tmp")
        os.close(fd)
        self.tmp = Path(nome)
        self._arquivo = gzip.open(self.tmp, "wt", encoding="utf-8", compresslevel=6)  # noqa: SIM115 — fecha em fechar()
        self._arquivo.write("\t".join(self.CAMPOS) + "\n")
        self.linhas = 0

    def passar(self, processos: Iterable[Mapping[str, Any]]) -> Iterator[Mapping[str, Any]]:
        for p in processos:
            datas = p.get("datas") or {}
            flags = p.get("flags") or {}
            valores = (p.get("id"), p.get("registro"), p.get("cnj"), p.get("relator_id") or p.get("fora"), p.get("orgao"), p.get("onde"),
                       p.get("classe"), p.get("area"), (p.get("assunto") or {}).get("codigo"), datas.get("distribuicao"),
                       datas.get("ultima_decisao"), datas.get("andamento"), p.get("sem_decisao_dias"), p.get("concluso_dias"),
                       int(bool(p.get("segredo"))), int(bool(flags.get("sobrestado"))))
            self._arquivo.write("\t".join("" if v is None else str(v).replace("\t", " ").replace("\n", " ") for v in valores) + "\n")
            self.linhas += 1
            yield p

    def fechar(self, ok: bool) -> Path | None:
        self._arquivo.close()
        if not ok:
            self.tmp.unlink(missing_ok=True)
            return None
        os.replace(self.tmp, self.final)
        antigos = sorted(self.pasta.glob(f"{self.trib}-*.tsv.gz"))
        for velho in antigos[:-RETRATOS]:
            velho.unlink(missing_ok=True)
        return self.final


# ── a rodada ─────────────────────────────────────────────────────────────────


class Rodada:
    """Uma execução de :func:`atualizar` (o contexto que cada fonte usa)."""

    def __init__(self, pasta: Path, cache: Path, opcoes: Opcoes, regras: Regras, coletor: ColetorTribunais, relogio: Callable[[], float],
                 root: Path | None = None) -> None:
        self.pasta = pasta
        self.cache = Path(cache)
        self.opcoes = opcoes
        self.regras = regras
        self.coletor = coletor
        self.relogio = relogio
        self.inicio = relogio()
        self.prazo = self.inicio + float(opcoes.prazo_s if opcoes.prazo_s is not None else PRAZO_S[opcoes.completo])
        self.hoje = date.fromisoformat(opcoes.hoje)
        self.agora = opcoes.agora or datetime.now(UTC)
        self.instante = _instante(self.agora)
        self.anterior: dict[str, Any] = ler_json(pasta / META) or {}
        if self.anterior.get("v") != VERSAO:
            self.anterior = {}
        self.estado = Estado(self.cache)
        # O prazo vale para todo pedido (não só para as séries do STJ): um portal mudo não leva o
        # passo ao tempo-limite do fluxo (que mataria o processo antes da gravação).
        if getattr(coletor, "prazo", None) is None:
            coletor.prazo = self.prazo
        self.avisos_operacao: list[str] = []  # só log e Summary (com hora e motivo técnico)
        self.tpu = self._carregar_tpu()
        self.classificador = Classificador(regras, self.tpu)
        self.catalogo = carregar_catalogo(root)

    # TPU: a cópia versionada da pasta de saída; sem ela (testes), a do pacote.
    def _carregar_tpu(self) -> TPU:
        pasta = self.pasta / PASTA_TPU
        return TPU.carregar(pasta) if (pasta / "versao.json").is_file() else TPU.carregar()

    def fonte_anterior(self, fid: str) -> dict[str, Any]:
        f = (self.anterior.get("fontes") or {}).get(fid)
        return dict(f) if isinstance(f, dict) else {}

    def restante(self) -> float:
        return self.prazo - self.relogio()

    def prazo_das_series(self) -> float:
        return self.prazo - RESERVA_S

    def operacao(self, texto: str) -> None:
        log.warning("Tribunais: %s", texto)
        self.avisos_operacao.append(texto)

    def falha(self, fid: str, exc: BaseException, *, onde: str = "") -> str:
        """Registra a falha (log com o detalhe) e devolve o motivo curto, sem hora."""
        if not isinstance(exc, ERROS_DE_FONTE):
            log.error("Tribunais: %s: erro inesperado", fid, exc_info=exc)
        self.operacao(f"{NOMES_FONTES.get(fid, fid)}{(' (' + onde + ')') if onde else ''}: {exc}")
        return _motivo(exc)

    def do_estado(self, fid: str, motivo: str, carregar: Callable[[dict[str, Any]], Any]) -> Leitura:
        """A fonte falhou: os dados da última coleta boa (``estado/``), se ainda estão no cache."""
        guardado = self.estado.ler(fid)
        dados = None
        if guardado is not None:
            try:
                dados = carregar(guardado)
            except Exception as exc:  # noqa: BLE001 — o estado é só um socorro: ilegível, fica sem
                log.warning("Tribunais: estado de %s ilegível (%s)", fid, exc)
                dados = None
        hoje = _br(self.opcoes.hoje, ano=False)
        if dados is None:
            return Leitura(None, None, None, ok=False, aviso=f"A coleta falhou em {hoje} ({motivo}); o bloco sai sem estes dados.")
        data = guardado.get("data_dados")  # type: ignore[union-attr]
        return Leitura(dados, data, guardado.get("registros"), ok=False,  # type: ignore[union-attr]
                       aviso=f"A coleta falhou em {hoje} ({motivo}); ficam os dados de {_br(data)}.", coletado_em=guardado.get("coletado_em"))  # type: ignore[union-attr]

    def recuou(self, fid: str, data: str | None) -> bool:
        """A data da fonte andou para trás em relação ao último dia bom?"""
        antes = self.fonte_anterior(fid).get("data_dados")
        return bool(data and antes and str(data)[:10] < str(antes)[:10])

    def guardar(self, fid: str, leitura: Leitura, **extra: Any) -> None:
        """Guarda no estado a coleta boa desta execução (o que o fallback de amanhã usa)."""
        coletado = self._coletado_em(fid, leitura)
        self.estado.gravar(fid, {"data_dados": leitura.data_dados, "registros": leitura.registros, "coletado_em": coletado, **extra})

    def _coletado_em(self, fid: str, leitura: Leitura) -> str | None:
        """A coleta que trouxe os dados em uso: muda só quando chega dado novo."""
        if leitura.coletado_em:
            return leitura.coletado_em
        if not leitura.data_dados and not leitura.registros:
            return None
        antes = self.fonte_anterior(fid)
        if antes.get("coletado_em") and antes.get("data_dados") == leitura.data_dados and antes.get("registros") == leitura.registros:
            return str(antes["coletado_em"])
        return self.instante

    def entrada_meta(self, fid: str, leitura: Leitura | None, trib: str) -> dict[str, Any]:
        """A entrada ``meta.json → fontes.<fid>`` (contrato §3)."""
        cfg = (self.regras.fontes.get(trib) or {}) if trib in ("stj", "stf") else {}
        velho = int((cfg.get("velho_dias") or {}).get(CHAVE_VELHO.get(fid, ""), 0) or 0) or (VELHO_TPU if fid == "tpu" else 7)
        semana = CHAVE_VELHO.get(fid, "") in (cfg.get("dias_da_semana") or ())  # a validade conta de segunda a sexta
        base: dict[str, Any] = {
            "nome": NOMES_FONTES[fid], "trib": trib if trib in ("stj", "stf") else "cnj", "url": _url_da_fonte(self.regras, fid),
            "data_dados": None, "coletado_em": None, "ok": None, "aviso": None, "registros": None, "velho_dias": velho,
            "atribuicao": cfg.get("atribuicao") if cfg else "Fonte: CNJ, Sistema de Gestão de Tabelas (SGT)",
        }
        if fid == "stj_integras":
            base["atraso_dias"] = None
        if semana:
            base["dias_da_semana"] = True
        if leitura is None:  # não tentada nesta execução: fica a entrada anterior
            antes = self.fonte_anterior(fid)
            if antes:
                return {**base, **{k: v for k, v in antes.items() if k in base}}
            base["ok"] = False
            base["aviso"] = "Ainda não coletada."
            return base
        base.update(data_dados=leitura.data_dados, registros=leitura.registros, ok=leitura.ok, aviso=leitura.aviso)
        base.update({k: v for k, v in leitura.extra.items() if k in base})
        # A coleta que trouxe os dados em uso: a desta execução (se trouxe dado novo), a do estado
        # (fallback) ou nenhuma (falhou sem dado nenhum).
        if leitura.coletado_em:
            base["coletado_em"] = leitura.coletado_em
        elif leitura.ok or leitura.dados is not None:
            base["coletado_em"] = self._coletado_em(fid, leitura)
        if leitura.ok and leitura.data_dados and not base["aviso"]:
            dado = date.fromisoformat(str(leitura.data_dados)[:10])
            dias = (self.hoje - dado).days
            if (dias_da_semana(dado, self.hoje) if semana else dias) > velho:
                base["aviso"] = f"O dado mais recente desta fonte é de {_br(leitura.data_dados)} ({dias} dias)."
        return base


def fonte_djen(pasta: Path, hoje: date, root: Path | None = None) -> dict[str, Any]:
    """A entrada ``meta.json → fontes.djen`` a partir do ``djen.json`` que a máquina do dono
    enviou (contrato do DJEN, §0.5). Sem arquivo (ou ilegível, de outra versão): ``ok: null`` e a
    frase de que a coleta só roda no Brasil. Com o último dia a mais de ``validade_dias_uteis`` dias
    de segunda a sexta: o aviso de que a camada saiu da página. Nada aqui pede nada à rede."""
    cfg = djen_mod.carregar_config(root)
    validade = int(cfg.get("validade_dias_uteis") or 4)
    base: dict[str, Any] = {
        "nome": NOME_DJEN, "trib": "cnj", "url": str(cfg.get("consulta") or djen_mod.CONSULTA), "data_dados": None, "coletado_em": None,
        "ok": None, "aviso": SEM_DJEN, "registros": None, "velho_dias": validade, "dias_da_semana": True,
        "atribuicao": str(cfg.get("atribuicao") or ""), "ativo": bool(cfg.get("ativo")), "tribunais": [],
    }
    d = ler_json(pasta / DJEN)
    if not isinstance(d, dict) or d.get("v") != djen_mod.VERSAO_DJEN or not isinstance(d.get("tribunais"), dict):
        return base
    tribs = {k: t for k, t in d["tribunais"].items() if k in ("stj", "stf") and isinstance(t, dict)}
    ultimos = [str(t["ultimo_dia"]) for t in tribs.values() if t.get("ultimo_dia")]
    data = max(ultimos) if ultimos else None
    base.update(data_dados=data, coletado_em=d.get("gerado_em"), ok=True, aviso=None,
                registros=sum(int(((t.get("mes") or {}).get("itens")) or 0) for t in tribs.values()),
                tribunais=sorted(k for k, t in tribs.items() if t.get("publica") and t.get("ultimo_dia")))
    if data is None:
        base["aviso"] = "A coleta ainda não trouxe publicação do STJ nem do STF."
    elif dias_da_semana(date.fromisoformat(data[:10]), hoje) > validade:
        base["aviso"] = f"Sem atualização desde {_br(data)}: a camada do DJe sai da página até a próxima coleta (na máquina do dono)."
    return base


def _url_da_fonte(regras: Regras, fid: str) -> str:
    if fid == "tpu":
        return str((regras.fontes.get("tpu") or {}).get("versoes") or "https://www.cnj.jus.br/sgt/versoes.php")
    return achados_mod.url_da_fonte(regras, fid) or ""


# ── TPU ──────────────────────────────────────────────────────────────────────


def _tpu(r: Rodada) -> Leitura:
    """A versão da TPU: com ``completo``, confere a publicada (SOAP) e refaz a cópia se mudou."""
    pasta = r.pasta / PASTA_TPU
    versao = ler_json(pasta / "versao.json") or r.tpu.versao or {}
    if r.opcoes.completo:
        try:
            saida = tpu_mod.atualizar(r.coletor, r.regras, pasta=pasta)
            versao = saida.get("versao") or versao
            if saida.get("mudou"):
                log.info("Tribunais: TPU refeita (versão %s de %s)", versao.get("versao"), versao.get("data"))
                r.tpu = TPU.carregar(pasta)
                r.classificador = Classificador(r.regras, r.tpu)
        except Exception as exc:  # noqa: BLE001 — acessório: fica a cópia versionada
            motivo = r.falha("tpu", exc)
            return Leitura(None, versao.get("data"), versao.get("assuntos"), ok=False,
                           aviso=f"A conferência da versão falhou em {_br(r.opcoes.hoje, ano=False)} ({motivo}); vale a cópia versionada.")
    elif r.fonte_anterior("tpu"):
        antes = r.fonte_anterior("tpu")
        return Leitura(True, antes.get("data_dados") or versao.get("data"), antes.get("registros") or versao.get("assuntos"),
                       ok=antes.get("ok") if antes.get("ok") is not None else True, aviso=antes.get("aviso"), coletado_em=antes.get("coletado_em"))
    return Leitura(True, versao.get("data"), versao.get("assuntos"))


# ── STJ ──────────────────────────────────────────────────────────────────────


def _serie_do_estado(r: Rodada, guardado: dict[str, Any]) -> list[tuple[str, Path]]:
    arquivos = []
    for data, rel in guardado.get("arquivos") or []:
        caminho = r.estado.caminho(rel)
        if caminho is not None:
            arquivos.append((data, caminho))
    if not arquivos:
        raise FileNotFoundError("nenhum arquivo da série no cache")
    return arquivos


def _ler_atas(r: Rodada, leitor: stj.LeitorSTJ, arquivos: Iterable[tuple[str | None, Path]]) -> list[dict[str, Any]]:
    itens: list[dict[str, Any]] = []
    for data, caminho in arquivos:
        try:
            itens += stj.ler_ata(caminho, r.regras, r.classificador, data=data, leitor=leitor)
        except Exception as exc:  # noqa: BLE001 — um dia ilegível não derruba a série
            r.operacao(f"STJ, ata de {_br(data)} ilegível: {exc}")
    return itens


def _ler_integras(r: Rodada, leitor: stj.LeitorSTJ, arquivos: Iterable[tuple[str | None, Path]]) -> list[dict[str, Any]]:
    itens: list[dict[str, Any]] = []
    for data, caminho in arquivos:
        try:
            lidos = stj.ler_integras(caminho, r.regras, r.classificador, arquivo=_nome_integra(caminho, data), leitor=leitor)
        except Exception as exc:  # noqa: BLE001 — um dia ilegível não derruba a série
            r.operacao(f"STJ, íntegras de {_br(data)} ilegíveis: {exc}")
            continue
        itens += [{k: i.get(k) for k in CAMPOS_INTEGRA} for i in lidos]  # só o que as contas leem (12 meses: ~300 mil)
    return itens


def _nome_integra(caminho: Path, data: str | None) -> str:
    """``metadadosAAAAMMDD.json`` (o nome que dá o dia às contas)."""
    if data:
        return f"metadados{data.replace('-', '')}.json"
    return caminho.name


def _stj_auxiliares(r: Rodada, leitor: stj.LeitorSTJ, t: Tribunal, recusa: BaseException | None = None, *,
                    so_a_pauta: bool = False) -> dict[str, Any]:
    """Pautas, precedentes, atas e íntegras do STJ (cada uma com o seu fallback). Depois de uma
    recusa do portal (403 duas vezes), nada mais é pedido a ele: vale a última coleta boa.

    As pautas e os precedentes ficam também em ``t.semana`` (a agenda da semana, que não depende do
    painel). ``so_a_pauta``: só essas duas fontes (o acervo falhou sem socorro e não haverá contas)."""
    entradas: dict[str, Any] = {"atas": None, "pautas": None, "decisoes": None, "temas": None}
    recusado: list[BaseException] = [recusa] if recusa is not None else []

    def tentar(fid: str, coletar: Callable[[], Leitura], estado: Callable[[dict[str, Any]], Any]) -> Leitura:
        if recusado:
            leitura = r.do_estado(fid, _motivo(recusado[0]), estado)
        else:
            try:
                leitura = coletar()
            except Exception as exc:  # noqa: BLE001 — acessório: vira aviso e vale a última coleta boa
                if isinstance(exc, RecusaDoHost):
                    recusado.append(exc)
                leitura = r.do_estado(fid, r.falha(fid, exc), estado)
        t.leituras[fid] = leitura
        return leitura

    # pautas
    def pautas() -> Leitura:
        baixado = stj.baixar_pautas(r.coletor, r.regras)
        if r.recuou("stj_pautas", baixado.data):
            raise stj.FormatoInesperado(baixado.recurso.url, f"a pauta publicada ({baixado.data}) é anterior à que está em uso")
        itens = stj.ler_pautas(baixado.caminho, r.regras, r.classificador, leitor=leitor)
        leitura = Leitura({"data_dados": baixado.data, "itens": itens}, baixado.data, len(itens))
        r.guardar("stj_pautas", leitura, caminho=_relativo(baixado.caminho, r.cache))
        return leitura

    def pautas_estado(g: dict[str, Any]) -> Any:
        caminho = r.estado.caminho(g.get("caminho"))
        if caminho is None:
            raise FileNotFoundError("pauta fora do cache")
        return {"data_dados": g.get("data_dados"), "itens": stj.ler_pautas(caminho, r.regras, r.classificador, leitor=leitor)}

    entradas["pautas"] = tentar("stj_pautas", pautas, pautas_estado).dados

    # precedentes
    def precedentes() -> Leitura:
        p = stj.baixar_precedentes(r.coletor, r.regras)
        temas = stj.montar_temas(stj.ler_temas(p.temas), stj.ler_processos_precedentes(p.processos), r.regras, r.classificador, leitor=leitor)
        leitura = Leitura(temas, p.data_dados, len(temas))
        r.guardar("stj_precedentes", leitura, temas=_relativo(p.temas, r.cache), processos=_relativo(p.processos, r.cache))
        return leitura

    def precedentes_estado(g: dict[str, Any]) -> Any:
        temas, processos = r.estado.caminho(g.get("temas")), r.estado.caminho(g.get("processos"))
        if temas is None or processos is None:
            raise FileNotFoundError("precedentes fora do cache")
        return stj.montar_temas(stj.ler_temas(temas), stj.ler_processos_precedentes(processos), r.regras, r.classificador, leitor=leitor)

    entradas["temas"] = tentar("stj_precedentes", precedentes, precedentes_estado).dados
    t.semana.update(leituras={fid: t.leituras[fid] for fid in FONTES_DA_PAUTA["stj"]}, pautas=entradas["pautas"], temas=entradas["temas"])
    if so_a_pauta:
        return entradas

    maximo = dict(SERIE_MAXIMO[r.opcoes.completo])
    # Recuperação: a série já publicada cujo estado sumiu do cache (cache frio, o primeiro dia na main)
    # baixa o que couber no prazo das séries, sem o teto por execução (60 execuções viram ~4).
    for chave, fid in (("atas", "stj_atas"), ("integras", "stj_integras")):
        if maximo[chave] is not None and r.estado.ler(fid) is None and r.fonte_anterior(fid).get("registros"):
            maximo[chave] = None
            r.operacao(f"{NOMES_FONTES[fid]}: sem a série no cache (cache frio); modo de recuperação, sem o teto por execução")
    janelas = r.regras.limiares.get("janelas") or {}

    # atas (os últimos 30 dias, acumulados no cache)
    def atas() -> Leitura:
        serie = stj.baixar_atas(r.coletor, r.regras, dias=int(janelas.get("entradas_recentes_dias", 30)),
                                maximo=maximo["atas"], prazo=r.prazo_das_series())
        if not serie.baixados:
            raise serie.erro or stj.FormatoInesperado(stj.url_do_conjunto(r.regras, "atas"), "nenhuma ata na janela")
        itens = _ler_atas(r, leitor, [(b.data, b.caminho) for b in serie.baixados])
        aviso, ok = _aviso_da_serie(r, serie, "atas")
        leitura = Leitura(itens, serie.data_dados, len(itens), ok=ok, aviso=aviso)
        if ok:
            r.guardar("stj_atas", leitura, arquivos=[[b.data, _relativo(b.caminho, r.cache)] for b in serie.baixados])
        return leitura

    entradas["atas"] = tentar("stj_atas", atas, lambda g: _ler_atas(r, leitor, _serie_do_estado(r, g))).dados

    # íntegras (12 meses até o último arquivo publicado)
    def integras() -> Leitura:
        serie = stj.baixar_integras(r.coletor, r.regras, maximo=maximo["integras"], prazo=r.prazo_das_series())
        if not serie.baixados:
            raise serie.erro or stj.FormatoInesperado(stj.url_do_conjunto(r.regras, "integras"), "nenhum arquivo de íntegras na janela")
        itens = _ler_integras(r, leitor, [(b.data, b.caminho) for b in serie.baixados])
        aviso, ok = _aviso_da_serie(r, serie, "integras")
        atraso = stj.atraso_dias(serie.ultimo_publicado, r.hoje)
        if ok and atraso is not None and not aviso:
            aviso = f"O último arquivo publicado pelo STJ é de {_br(serie.ultimo_publicado)} ({atraso} dias)."
        leitura = Leitura({"itens": itens, "ultimo_arquivo": serie.data_dados}, serie.data_dados, len(itens), ok=ok, aviso=aviso,
                          extra={"atraso_dias": atraso})
        if ok:
            r.guardar("stj_integras", leitura, arquivos=[[b.data, _relativo(b.caminho, r.cache)] for b in serie.baixados],
                      ultimo_publicado=serie.ultimo_publicado)
        return leitura

    def integras_estado(g: dict[str, Any]) -> Any:
        arquivos = _serie_do_estado(r, g)
        return {"itens": _ler_integras(r, leitor, arquivos), "ultimo_arquivo": max(d for d, _c in arquivos)}

    entradas["decisoes"] = tentar("stj_integras", integras, integras_estado).dados
    return entradas


def _aviso_da_serie(r: Rodada, serie: stj.Serie, chave: str) -> tuple[str | None, bool]:
    """O aviso de uma série (atas, íntegras) e se ela está em dia: em dia quando o arquivo mais
    recente publicado está no disco (o que ficou pendente é o fundo da série, que vem depois)."""
    em_dia = bool(serie.data_dados) and serie.data_dados == serie.ultimo_publicado
    nome = "atas" if chave == "atas" else "arquivos de íntegras"
    if serie.erro is not None and not em_dia:
        return (f"A coleta parou em {_br(r.opcoes.hoje, ano=False)} ({_motivo(serie.erro)}); os dados vão até {_br(serie.data_dados)}.", False)
    if serie.pendentes:
        ini, fim = serie.pendentes[0].data, serie.pendentes[-1].data
        com_ano = chave == "integras" or str(ini or "")[:4] != str(fim or "")[:4]  # 12 meses: "de 16/10 a 08/10" se leria ao contrário
        texto = (f"A série tem {len(serie.baixados)} {nome}; {len(serie.pendentes)} (de {_br(ini, ano=com_ano)} a {_br(fim, ano=com_ano)}) "
                 "ficam para as próximas coletas.")
        return texto, True
    return None, True


def _stj(r: Rodada) -> Tribunal:
    t = Tribunal("stj")
    leitor = stj.LeitorSTJ(r.regras, r.classificador)
    # 1. o acervo do dia (o que decide se o painel é refeito)
    caminho: Path | None = None
    data: str | None = None
    recusa: BaseException | None = None
    leitura_acervo: Leitura
    try:
        baixado = stj.baixar_acervo(r.coletor, r.regras)
        caminho, data = baixado.caminho, baixado.data
        if r.recuou("stj_acervo", data):
            raise stj.FormatoInesperado(baixado.recurso.url, f"o arquivo publicado ({data}) é anterior ao que está em uso")
        leitura_acervo = Leitura(None, data, None)
    except Exception as exc:  # noqa: BLE001 — fonte: vira aviso; vale o arquivo da última coleta boa, se ainda está no cache
        motivo = r.falha("stj_acervo", exc)
        guardado = r.estado.ler("stj_acervo") or {}
        caminho = r.estado.caminho(guardado.get("caminho"))
        data = guardado.get("data_dados") if caminho is not None else None
        aviso = f"A coleta falhou em {_br(r.opcoes.hoje, ano=False)} ({motivo})"
        if caminho is None:
            antes = r.fonte_anterior("stj_acervo")
            # A agenda da semana não depende do painel: a pauta e os precedentes são lidos assim mesmo
            # (depois de uma recusa do portal, só do que a última coleta boa deixou no cache).
            _stj_auxiliares(r, leitor, t, exc if isinstance(exc, RecusaDoHost) else None, so_a_pauta=True)
            t.leituras["stj_acervo"] = Leitura(None, antes.get("data_dados"), antes.get("registros"), ok=False,
                                               coletado_em=antes.get("coletado_em"), aviso=aviso + "; fica o painel da última coleta boa.")
            return t
        leitura_acervo = Leitura(None, data, guardado.get("registros"), ok=False, coletado_em=guardado.get("coletado_em"),
                                 aviso=aviso + f"; as contas usam o arquivo de {_br(data)}.")
        if isinstance(exc, RecusaDoHost):  # o portal recusou: nada mais é pedido a ele nesta execução
            recusa = exc
    t.leituras["stj_acervo"] = leitura_acervo

    # 2. as fontes acessórias (pautas, precedentes, atas, íntegras)
    entradas = _stj_auxiliares(r, leitor, t, recusa)

    # 3. as contas, numa passada pelo acervo (com o retrato compacto para a véspera)
    resumo = stj.ResumoAcervo()
    retrato = Retrato(r.cache, "stj", data)
    ok = False
    try:
        processos = stj.ler_acervo(caminho, r.regras, r.classificador, data_dados=data, resumo=resumo, leitor=leitor)
        resultado = contas.montar("stj", retrato.passar(processos), r.regras, data_dados=data, hoje=r.hoje, catalogo=r.catalogo,
                                  atas=entradas["atas"], pautas=entradas["pautas"], decisoes=entradas["decisoes"], temas=entradas["temas"])
        t.semana["acervo"] = resultado.pauta
        ok = True
    except stj.FormatoInesperado as exc:
        motivo = r.falha("stj_acervo", exc)
        caminho.unlink(missing_ok=True)  # arquivo truncado: a próxima execução baixa de novo
        _recusar(r, t, "stj_acervo", f"O arquivo do acervo de {_br(data)} veio corrompido ({motivo}); fica o painel da última coleta boa.")
        return t
    finally:
        t.retrato = retrato.fechar(ok)
    problemas = resumo.problemas()
    if problemas:
        _recusar(r, t, "stj_acervo", f"O arquivo do acervo de {_br(data)} foi recusado: {' '.join(problemas)} Fica o painel da última coleta boa.")
        return t
    if not _sanidade(r, t, "stj_acervo", resumo.processos, data):
        return t
    t.leituras["stj_acervo"].registros = resumo.processos
    if t.leituras["stj_acervo"].ok:
        r.guardar("stj_acervo", t.leituras["stj_acervo"], caminho=_relativo(caminho, r.cache))
    t.painel, t.dossies, t.sinais = resultado.painel, resultado.dossies, resultado.sinais
    t.cobertura = {**resumo.para_meta(), "partes": resultado.cobertura.get("partes")}
    manter_series(r, t)
    log.info("Tribunais: STJ, acervo de %s: %d processos em %.0f s", data, resumo.processos, resumo.segundos)
    return t


SERIES_STJ = (("decisoes", "stj_integras", "documentos"), ("entradas", "stj_atas", "dias_com_ata"))


def manter_series(r: Rodada, t: Tribunal) -> list[str]:
    """A rede de segurança das séries do STJ (o cache frio, o portal que recusou): quando a série
    de hoje cobre menos que a publicada (menos documentos de íntegras, menos dias com ata), os blocos
    publicados ficam (no painel, em cada ministro e em cada dossiê) e a entrada da fonte no
    ``meta.json`` também (o meta descreve o dado em uso). Ministro sem bloco anterior (posse
    recente) fica com o de hoje. Devolve os blocos mantidos."""
    antigo = ler_json(r.pasta / "stj.json")
    if t.painel is None or not isinstance(antigo, dict) or antigo.get("v") != VERSAO:
        return []
    mantidos = []
    for bloco, fid, medida in SERIES_STJ:
        velho, novo = antigo.get(bloco), t.painel.get(bloco)
        if not isinstance(velho, dict) or not velho.get(medida):
            continue
        if isinstance(novo, dict) and int(novo.get(medida) or 0) >= int(velho.get(medida) or 0):
            continue
        t.painel[bloco] = velho
        antes_min = {m.get("id"): m for m in antigo.get("ministros") or [] if isinstance(m, dict)}
        for m in t.painel.get("ministros") or []:
            if bloco in (antes_min.get(m.get("id")) or {}):
                m[bloco] = antes_min[m["id"]][bloco]
        for mid, d in t.dossies.items():
            velho_d = ler_json(r.pasta / MINISTROS / f"{mid}.json")
            if isinstance(velho_d, dict) and bloco in velho_d:
                d[bloco] = velho_d[bloco]
        antes = r.fonte_anterior(fid)
        if antes:
            t.leituras[fid] = Leitura(None, antes.get("data_dados"), antes.get("registros"), ok=antes.get("ok"), aviso=antes.get("aviso"),
                                      coletado_em=antes.get("coletado_em"), extra={"atraso_dias": antes.get("atraso_dias")})
        r.operacao(f"{NOMES_FONTES[fid]}: a série de hoje cobre menos que a publicada ({(novo or {}).get(medida) if isinstance(novo, dict) else 0} "
                   f"contra {velho.get(medida)} em {medida}); fica o bloco publicado")
        mantidos.append(bloco)
    return mantidos


def _recusar(r: Rodada, t: Tribunal, fid: str, aviso: str) -> None:
    """O acervo do dia não serve: o painel da véspera fica, e as fontes do tribunal com ele."""
    antes = r.fonte_anterior(fid)
    t.leituras = {fid: Leitura(None, antes.get("data_dados"), antes.get("registros"), ok=False, aviso=aviso, coletado_em=antes.get("coletado_em"))}
    t.painel = None


def _sanidade(r: Rodada, t: Tribunal, fid: str, total: int, data: str | None) -> bool:
    """O total do dia contra o do último dia bom (``SANIDADE``): fora da faixa, o dia é recusado."""
    antes = r.fonte_anterior(fid)
    referencia = antes.get("registros")
    if not isinstance(referencia, int) or referencia <= 0 or antes.get("ok") is None:
        return True
    if fid.split("_", 1)[0] in r.opcoes.aceitar:  # o dono conferiu a mudança e mandou aceitar (workflow_dispatch)
        r.operacao(f"{NOMES_FONTES[fid]}: {total} processos em {data} contra {referencia} de {antes.get('data_dados')}; aceito à mão (--aceitar-acervo)")
        return True
    razao = total / referencia
    menor = float(r.regras.limiares.get("recusa_arquivo_menor_que", SANIDADE[0]))
    maior = float(r.regras.limiares.get("recusa_arquivo_maior_que", SANIDADE[1]))
    if menor <= razao <= maior:
        return True
    r.operacao(f"{NOMES_FONTES[fid]}: {total} processos em {data}, {razao:.0%} dos {referencia} de {antes.get('data_dados')}; recusado")
    _recusar(r, t, fid, f"O arquivo de {_br(data)} trouxe {_milhar(total)} processos, {razao:.0%} dos {_milhar(referencia)} de "
                        f"{_br(antes.get('data_dados'))}; foi recusado e fica o painel daquele dia.")
    return False


# ── STF ──────────────────────────────────────────────────────────────────────


def _stf_auxiliares(r: Rodada, t: Tribunal, *, so_a_pauta: bool = False) -> dict[str, Any]:
    """Atas, jurisprudência, repercussão geral e pauta do STF (cada uma com o seu fallback). A
    pauta e os temas ficam também em ``t.semana`` (a agenda da semana, que não depende do painel).
    ``so_a_pauta``: só essas duas fontes (o acervo foi recusado e não haverá contas)."""
    entradas: dict[str, Any] = {"atas": None, "pautas": None, "decisoes": None, "temas": None}
    recusado: set[str] = set()  # hosts que recusaram (403 duas vezes)

    def tentar(fid: str, host: str, coletar: Callable[[], Leitura]) -> Leitura:
        if host in recusado:
            leitura = r.do_estado(fid, "o portal recusou o acesso (403) duas vezes", lambda g: g.get("dados"))
        else:
            try:
                leitura = coletar()
            except Exception as exc:  # noqa: BLE001 — acessório: aviso e a última coleta boa
                if isinstance(exc, RecusaDoHost):
                    recusado.add(host)
                leitura = r.do_estado(fid, r.falha(fid, exc), lambda g: g.get("dados"))
        t.leituras[fid] = leitura
        return leitura

    def atas() -> Leitura:
        saida = stf.coletar_atas(r.coletor, r.regras, ate=r.hoje - timedelta(days=1))
        dias = saida.get("dias") or []
        if not dias:
            raise FetchError(r.regras.fontes["stf"]["atas_recupera"], "nenhum dia da janela respondeu")
        com_ata = [d["data"] for d in dias if d.get("atas")]
        registros = sum(len(a.get("processos") or ()) for d in dias for a in d.get("atas") or ())
        falhas = len(saida.get("avisos") or ())
        aviso = f"{falhas} dia(s) da janela sem resposta; ficam para a próxima coleta." if falhas else None
        leitura = Leitura(saida, max(com_ata) if com_ata else None, registros, aviso=aviso)
        r.guardar("stf_atas", leitura, dados=saida)
        return leitura

    if not so_a_pauta:
        entradas["atas"] = tentar("stf_atas", "portal.stf.jus.br", atas).dados

    def decisoes() -> Leitura:
        saida = stf.coletar_decisoes(r.coletor, r.regras, hoje=r.hoje)
        data = str(saida.get("data_dados") or "")[:10] or (saida.get("janela") or [None, None])[1]
        leitura = Leitura(saida, data, saida.get("documentos"))
        r.guardar("stf_jurisprudencia", leitura, dados=saida)
        return leitura

    if not so_a_pauta:
        entradas["decisoes"] = tentar("stf_jurisprudencia", "jurisprudencia.stf.jus.br", decisoes).dados

    def rg() -> Leitura:
        antes = r.coletor.requisicoes
        saida = stf.coletar_rg(r.coletor, r.regras)
        guardado = r.estado.ler("stf_rg") or {}
        # A lista de temas não declara data: vale o dia em que foi baixada (do cache, a da última vez).
        data = r.opcoes.hoje if r.coletor.requisicoes > antes or not guardado.get("data_dados") else guardado["data_dados"]
        leitura = Leitura(saida, data, len(saida.get("temas") or ()))
        r.guardar("stf_rg", leitura, dados=saida)
        return leitura

    entradas["temas"] = tentar("stf_rg", "portal.stf.jus.br", rg).dados

    def pauta() -> Leitura:
        saida = stf.coletar_pauta(r.coletor, r.regras, hoje=r.hoje)
        falhas = len(saida.get("avisos") or ())
        leitura = Leitura(saida, r.opcoes.hoje, len(saida.get("itens") or ()),
                          aviso=f"{falhas} consulta(s) da pauta sem resposta." if falhas else None)
        r.guardar("stf_pauta", leitura, dados=saida)
        return leitura

    entradas["pautas"] = tentar("stf_pauta", "portal.stf.jus.br", pauta).dados
    t.semana.update(leituras={fid: t.leituras[fid] for fid in FONTES_DA_PAUTA["stf"]}, pautas=entradas["pautas"],
                    temas=(entradas["temas"] or {}).get("temas") if isinstance(entradas["temas"], Mapping) else None)
    return entradas


def _stf(r: Rodada) -> Tribunal:
    t = Tribunal("stf")
    url = r.regras.fontes["stf"]["acervo"]
    try:
        acervo = stf.coletar_acervo(r.coletor, r.regras, tpu=r.tpu, classificador=r.classificador)
        if r.recuou("stf_acervo", acervo.data_dados):
            raise stf.ErroDeFormato(f"a planilha publicada ({acervo.data_dados}) é anterior à que está em uso")
        t.leituras["stf_acervo"] = Leitura(None, acervo.data_dados, len(acervo.processos))
    except ERROS_DE_FONTE as exc:
        motivo = r.falha("stf_acervo", exc)
        guardado = r.estado.ler("stf_acervo") or {}
        caminho = _no_cache(r.coletor, url) if guardado else None
        aviso = f"A coleta falhou em {_br(r.opcoes.hoje, ano=False)} ({motivo})"
        if caminho is not None and not isinstance(exc, FetchError):  # a planilha baixada veio quebrada: não serve de socorro
            caminho.unlink(missing_ok=True)
            caminho = None
        if caminho is None:
            _recusar(r, t, "stf_acervo", aviso + "; fica o painel da última coleta boa.")
            _stf_auxiliares(r, t, so_a_pauta=True)  # a agenda da semana não depende do painel
            return t
        try:
            acervo = stf.ler_acervo(caminho, r.regras, tpu=r.tpu, classificador=r.classificador)
        except ERROS_DE_FONTE:
            _recusar(r, t, "stf_acervo", aviso + "; fica o painel da última coleta boa.")
            _stf_auxiliares(r, t, so_a_pauta=True)
            return t
        t.leituras["stf_acervo"] = Leitura(None, acervo.data_dados, len(acervo.processos), ok=False, coletado_em=guardado.get("coletado_em"),
                                           aviso=aviso + f"; as contas usam a planilha de {_br(acervo.data_dados)}.")
    if not _sanidade(r, t, "stf_acervo", len(acervo.processos), acervo.data_dados):
        _stf_auxiliares(r, t, so_a_pauta=True)  # a agenda da semana não depende do painel
        return t
    if t.leituras["stf_acervo"].ok:
        caminho = _no_cache(r.coletor, url)
        r.guardar("stf_acervo", t.leituras["stf_acervo"], caminho=_relativo(caminho, r.cache) if caminho else None)
    entradas = _stf_auxiliares(r, t)
    retrato = Retrato(r.cache, "stf", acervo.data_dados)
    ok = False
    try:
        resultado = contas.montar("stf", retrato.passar(acervo.processos), r.regras, data_dados=acervo.data_dados, hoje=r.hoje,
                                  catalogo=r.catalogo, atas=entradas["atas"], pautas=entradas["pautas"], decisoes=entradas["decisoes"],
                                  temas=entradas["temas"])
        t.semana["acervo"] = resultado.pauta
        ok = True
    finally:
        t.retrato = retrato.fechar(ok)
    for aviso in acervo.avisos:
        r.operacao(aviso)
    t.painel, t.dossies, t.sinais = resultado.painel, resultado.dossies, resultado.sinais
    partes = {**(acervo.cobertura.get("partes") or {}), **(resultado.cobertura.get("partes") or {})}
    t.cobertura = {**acervo.cobertura, "partes": partes}
    log.info("Tribunais: STF, planilha de %s: %d processos", acervo.data_dados, len(acervo.processos))
    return t


# ── meta.json e a gravação ───────────────────────────────────────────────────


def proxima_execucao(fontes: Mapping[str, Mapping[str, Any]], agora: datetime) -> str:
    """Antes disto nenhuma fonte deve publicar dado novo (o job "decidir" do ``tribunais.yml``
    encerra cedo). Fonte atrasada ou que falhou: ``agora + ATRASADA_H``."""
    agora = agora.astimezone(UTC)
    candidatos = []
    for fid, (dias, quando) in AGENDA.items():
        f = fontes.get(fid) or {}
        if f.get("ok") is None and f.get("data_dados") is None:
            continue
        dado = f.get("data_dados")
        esperado = None
        if dado and f.get("ok"):
            base = date.fromisoformat(str(dado)[:10])
            if fid in DIAS_DA_SEMANA:  # o próximo dado é o do dia de semana seguinte; sai um dia depois dele
                seguinte = base + timedelta(days=1)
                while seguinte.weekday() >= 5:
                    seguinte += timedelta(days=1)
                esperado = datetime.combine(seguinte + timedelta(days=dias - 1), quando, UTC)
            else:
                esperado = datetime.combine(base + timedelta(days=dias), quando, UTC)
        if esperado is None or esperado <= agora:
            esperado = agora + timedelta(hours=ATRASADA_H)
        candidatos.append(esperado)
    if not candidatos:
        candidatos.append(agora + timedelta(hours=ATRASADA_H))
    proxima = min(max(min(candidatos), agora + timedelta(hours=PROXIMA_MIN_H)), agora + timedelta(hours=PROXIMA_MAX_H))
    return _instante(proxima.replace(second=0, microsecond=0))


def _achados(r: Rodada, refeitos: Mapping[str, Tribunal]) -> dict[str, Any]:
    """Os achados dos tribunais refeitos hoje e, dos outros, os da última execução boa."""
    paineis = {trib: t.painel for trib, t in refeitos.items() if t.painel is not None and t.sinais is not None}
    sinais = {trib: t.sinais for trib, t in refeitos.items() if t.painel is not None and t.sinais is not None}
    novos = achados_mod.montar(r.regras, paineis, sinais, hoje=r.hoje)  # type: ignore[arg-type]
    anteriores = ler_json(r.pasta / ACHADOS)
    if not isinstance(anteriores, dict) or anteriores.get("v") != VERSAO:
        anteriores = {"achados": []}
    mantidos = [a for a in anteriores.get("achados") or [] if isinstance(a, dict) and a.get("trib") not in paineis]
    if not mantidos:
        return novos
    todos = sorted([*novos["achados"], *mantidos], key=lambda a: (-float(a.get("peso") or 0), a.get("trib") or "", a.get("tipo") or "", a.get("alvo") or ""))
    vistos: set[str] = set()
    saida = []
    for a in todos:
        if a.get("ministro"):
            if a["ministro"] in vistos:
                continue
            vistos.add(a["ministro"])
        saida.append(a)
    return {"v": VERSAO, "hoje": r.opcoes.hoje, "achados": saida[: achados_mod.MAXIMO]}


def _tamanho_atual(pasta: Path, fora: Iterable[str]) -> int:
    """Bytes que ficam na pasta sem mudar (a TPU e os arquivos dos tribunais não refeitos)."""
    excluir = set(fora)
    total = 0
    for caminho in pasta.rglob("*"):
        if caminho.is_file() and not caminho.name.startswith(".") and str(caminho.relative_to(pasta)) not in excluir:
            total += caminho.stat().st_size
    return total


def _conferir_tamanhos(r: Rodada, tribunais: dict[str, Tribunal], arquivos_fixos: Mapping[str, str]) -> dict[str, dict[str, str]]:
    """Os arquivos de cada tribunal refeito, já em texto; tribunal com JSON acima do teto é recusado
    (fica o da véspera)."""
    por_trib: dict[str, dict[str, str]] = {}
    for trib, t in tribunais.items():
        if t.painel is None:
            continue
        arquivos = {f"{trib}.json": _json(t.painel)}
        for mid, dossie in sorted(t.dossies.items()):
            arquivos[f"{MINISTROS}/{mid}.json"] = _json(dossie)
        grandes = [(nome, len(texto.encode("utf-8"))) for nome, texto in arquivos.items() if len(texto.encode("utf-8")) > TETO_JSON]
        if grandes:
            lista = ", ".join(f"{n} ({b // 1000} KB)" for n, b in grandes)
            r.operacao(f"{trib.upper()}: acima do teto de {TETO_JSON // 1000} KB: {lista}; fica o painel da última coleta boa")
            _recusar(r, t, f"{trib}_acervo", f"Os números de {_br(r.opcoes.hoje, ano=False)} passaram do tamanho máximo publicado; fica o painel da última coleta boa.")
            continue
        por_trib[trib] = arquivos
    # a pasta inteira (o que fica + o novo)
    substituidos = {rel for arqs in por_trib.values() for rel in arqs}
    substituidos |= {f"{MINISTROS}/{c.name}" for trib in por_trib for c in (r.pasta / MINISTROS).glob(f"{trib}-*.json")}
    substituidos |= {f"{trib}.json" for trib in por_trib} | set(arquivos_fixos)
    substituidos.add(DJEN)  # o djen.json (até 150 KB, da máquina do dono) não conta: a aba nunca depende dele
    substituidos.add(PAUTA_SEMANAL)  # a agenda da semana não conta contra os painéis: no aperto, quem cai é ela (_cabe_a_pauta)
    total = _tamanho_atual(r.pasta, substituidos) + sum(len(x.encode("utf-8")) for arqs in por_trib.values() for x in arqs.values())
    total += sum(len(x.encode("utf-8")) for x in arquivos_fixos.values())
    if total > TETO_PASTA:
        r.operacao(f"data/tribunais/ teria {total // 1000} KB, acima do teto de {TETO_PASTA // 1000} KB; os painéis de hoje não foram gravados")
        for trib in list(por_trib):
            _recusar(r, tribunais[trib], f"{trib}_acervo", f"Os números de {_br(r.opcoes.hoje, ano=False)} passaram do tamanho máximo publicado; fica o painel da última coleta boa.")
            del por_trib[trib]
    return por_trib


# ── a agenda da semana (agenda.json) ─────────────────────────────────────────


def _entrada_da_semana(r: Rodada, t: Tribunal) -> pauta_mod.Entrada:
    """O que um tribunal traz para a agenda da semana: as fontes da pauta (como iriam ao
    ``meta.json``, com o ``velho_dias`` de cada uma) e, quando a pauta foi lida nesta execução, a
    pauta, os temas e o cruzamento com o acervo. Pauta que falhou (mesmo com socorro do cache) não
    refaz o tribunal: a parte dele fica como estava no arquivo anterior."""
    leituras = t.semana.get("leituras") or {}
    fontes = []
    for fid in FONTES_DA_PAUTA[t.trib]:
        leitura = leituras.get(fid)
        entrada = r.entrada_meta(fid, leitura, t.trib)
        if leitura is None:  # nem tentada: a entrada do meta é a anterior, não a desta execução
            entrada = {**entrada, "ok": False, "aviso": "A pauta não foi lida nesta execução; ficam os dados do último dia bom."}
        fontes.append(pauta_mod.fonte(fid, t.trib, entrada, coletado_em=r.instante))
    principal = leituras.get(FONTES_DA_PAUTA[t.trib][0])
    pauta = t.semana.get("pautas") if principal is not None and principal.ok is True else None
    if t.trib == "stj" and isinstance(pauta, Mapping):
        pauta = pauta.get("itens")
    return pauta_mod.Entrada(fontes=tuple(fontes), pauta=pauta, temas=t.semana.get("temas"), acervo=t.semana.get("acervo"))


def _pauta_semanal(r: Rodada, tribunais: Mapping[str, Tribunal], root: Path | None = None) -> str | None:
    """O texto novo de ``agenda.json`` (já validado pelo contrato e dentro do teto dele), ou
    ``None`` quando fica o arquivo que está lá: nada mudou além dos relógios (mesmos dados, mesmos
    bytes), nenhum tribunal veio, a agenda saiu fora do contrato ou acima do teto. O tribunal que
    não foi refeito e as sessões já passadas da semana vêm do arquivo anterior (``agenda.juntar``).
    Nunca levanta exceção: a agenda não pode impedir os painéis."""
    try:
        entradas = {trib: _entrada_da_semana(r, t) for trib, t in tribunais.items()}
        novo = pauta_mod.montar(r.regras, hoje=r.opcoes.hoje, gerado_em=r.instante, catalogo=r.catalogo,
                                stf=entradas.get("stf"), stj=entradas.get("stj"))
        if novo is None:
            return None
        anterior = contrato_agenda.ler(r.pasta / PAUTA_SEMANAL, lado=contrato_agenda.LADO_TRIBUNAIS)
        junto = pauta_mod.juntar(novo, anterior, r.opcoes.hoje)
        if contrato_agenda.conteudo_igual(junto, anterior):
            return None
        teto = regras_agenda.carregar(root).tetos.arquivos.tribunais_bytes
        saida, cortes = pauta_mod.texto(junto, teto)
        if cortes:
            r.operacao(f"Agenda da semana: para caber em {teto // 1000} KB, saíram {' e '.join(cortes)}")
        return saida
    except Exception as exc:  # noqa: BLE001 — a agenda é acessória: vira aviso de operação e fica o arquivo anterior
        if not isinstance(exc, (contrato_agenda.ErroDeContrato, pauta_mod.AcimaDoTeto)):
            log.error("Tribunais: agenda da semana: erro inesperado", exc_info=exc)
        r.operacao(f"Agenda da semana não gravada ({type(exc).__name__}: {exc}); fica o arquivo anterior")
        return None


def _cabe_a_pauta(r: Rodada, arquivos: Mapping[str, str], apagar: Iterable[Path], extra: int) -> bool:
    """A agenda da semana cabe na pasta junto com o que vai ser gravado? Os painéis já passaram
    pelo teto sem ela (``_conferir_tamanhos``): no aperto, quem não é gravada é a agenda."""
    fora = {*arquivos, PAUTA_SEMANAL, DJEN, META} | {_relativo(c, r.pasta) for c in apagar}
    total = _tamanho_atual(r.pasta, fora) + sum(len(x.encode("utf-8")) for x in arquivos.values()) + extra
    if total <= TETO_PASTA:
        return True
    r.operacao(f"data/tribunais/ teria {total // 1000} KB com a agenda da semana, mais que os {TETO_PASTA // 1000} KB da pasta; "
               "a agenda de hoje não foi gravada")
    return False


def _trocar(pasta: Path, arquivos: Mapping[str, str], apagar: Iterable[Path]) -> tuple[int, int]:
    """Grava ``arquivos`` (caminho relativo → texto) de uma vez: o que mudou vai para uma pasta
    temporária ao lado e troca de lugar com ``os.replace``, na ordem dada (o ``meta.json`` é o último);
    depois apaga ``apagar``. Devolve (gravados, apagados)."""
    mudaram = []
    for rel, texto in arquivos.items():
        try:
            if (pasta / rel).read_text(encoding="utf-8") == texto:
                continue
        except OSError:
            pass
        mudaram.append(rel)
    apagar = [c for c in apagar if c.is_file()]
    if not mudaram and not apagar:
        return 0, 0
    pasta.mkdir(parents=True, exist_ok=True)
    temporaria = Path(tempfile.mkdtemp(prefix=".novo-", dir=pasta))
    try:
        for rel in mudaram:
            _gravar(temporaria / rel, arquivos[rel])
        for rel in mudaram[:-1] if mudaram and mudaram[-1] == META else mudaram:
            (pasta / rel).parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporaria / rel, pasta / rel)
        for caminho in apagar:
            caminho.unlink(missing_ok=True)
        if mudaram and mudaram[-1] == META:
            os.replace(temporaria / META, pasta / META)
    finally:
        shutil.rmtree(temporaria, ignore_errors=True)
    return len(mudaram), len(apagar)


def _numeros(t: Tribunal) -> dict[str, Any]:
    p = t.painel or {}
    u = p.get("universo") or {}
    return {"data_dados": p.get("data_dados"), "processos": u.get("processos"), "na_grade": u.get("na_grade"), "na_mesa": u.get("na_mesa"),
            "ministros": len(p.get("ministros") or ()), "dossies": len(t.dossies)}


# ── DataJud (o gancho) ───────────────────────────────────────────────────────


def _retrato_stj(r: Rodada, tribunais: Mapping[str, Tribunal], painel: Mapping[str, Any] | None) -> Path | None:
    """O retrato do acervo do STJ que corresponde ao painel em uso (o de hoje ou o da véspera)."""
    data = (painel or {}).get("data_dados")
    if data:
        caminho = r.cache / "retratos" / f"stj-{data}.tsv.gz"
        if caminho.is_file():
            return caminho
    t = tribunais.get("stj")
    return t.retrato if t is not None and t.retrato is not None and t.retrato.is_file() else None


def _listados_stj(r: Rodada, tribunais: Mapping[str, Tribunal], painel: Mapping[str, Any] | None) -> set[str]:
    """Os ids dos processos que a página lista no STJ: a fila e os mais antigos de cada ministro
    (do painel e dos dossiês)."""
    ids = {str(f.get("id")) for f in ((painel or {}).get("fila") or {}).get("mais_antigos") or []}
    for m in (painel or {}).get("ministros") or []:
        ids |= {str(f.get("id")) for f in m.get("mais_antigos") or []}
    t = tribunais.get("stj")
    dossies: Iterable[Mapping[str, Any]]
    if t is not None and t.painel is not None:
        dossies = t.dossies.values()
    else:
        dossies = [d for d in (ler_json(c) for c in sorted((r.pasta / MINISTROS).glob("stj-*.json"))) if isinstance(d, dict)]
    for d in dossies:
        ids |= {str(f.get("id")) for f in d.get("mais_antigos") or []}
    ids.discard("None")
    return ids


def _cobertura_cnj(camada: Mapping[str, Any]) -> dict[str, Any]:
    """O resumo da camada do CNJ para ``meta.json → cobertura.stj.datajud.movimentos``."""
    u = camada.get("mesa_inteira") or {}
    f = camada.get("fonte") or {}
    return {"base": u.get("base"), "com_data": u.get("com_data"), "cobertura": u.get("cobertura"), "estados": u.get("estados"),
            "segredo": u.get("segredo"), "indice_ate": f.get("indice_ate"), "execucoes_restantes": f.get("execucoes_restantes")}


def _datajud(r: Rodada, tribunais: Mapping[str, Tribunal], painel_stj: Mapping[str, Any] | None) -> tuple[datajud.Resultado, dict[str, Any] | None]:
    """A rodada do DataJud e, quando ele trouxe a mesa, a camada do CNJ (``contas.camada_cnj``).

    A camada sempre vai para o cache (``<cache>/datajud/camada.json``: o uso pessoal); para a pasta
    pública (``cnj.json``) só com ``datajud.publicar`` ou ``QIJ_DATAJUD_PUBLICAR=1``."""
    regras = r.regras
    publicar = datajud.deve_publicar(regras, r.opcoes.ambiente)
    anterior_cob = ((r.anterior.get("cobertura") or {}).get("stj") or {})
    prazo = float(regras.datajud.get("prazo_s") or 720)
    prazo = max(0.0, min(prazo, r.restante() - RESERVA_DATAJUD_S))
    pasta_tpu = r.pasta / PASTA_TPU if (r.pasta / PASTA_TPU / tpu_mod.MOVIMENTOS).is_file() else None
    dj = datajud.atualizar(regras, r.coletor, agora=r.agora, hoje=r.hoje, anterior_fonte=r.fonte_anterior("datajud") or None,
                           anterior_conferencia=anterior_cob.get("datajud"), painel_stj=painel_stj, ambiente=r.opcoes.ambiente,
                           cache=r.cache, retrato=_retrato_stj(r, tribunais, painel_stj), pasta_tpu=pasta_tpu, publicar=publicar,
                           prazo_s=prazo)
    if dj.linhas is None or dj.registros is None or painel_stj is None:
        return dj, None
    camada = contas.camada_cnj(dj.linhas, dj.registros, regras, data_dados=painel_stj.get("data_dados"),
                               listados=_listados_stj(r, tribunais, painel_stj))
    frescor = dj.frescor or {}
    camada = {
        "v": VERSAO, "trib": "stj",
        "fonte": {"nome": datajud.NOME, "selo": datajud.SELO, "atribuicao": datajud.ATRIBUICAO, "url": datajud.URL_LEITOR,
                  "indice_ate": (frescor.get("max_timestamp") or "")[:10] or None, "max_atualizacao": frescor.get("max_atualizacao"),
                  "atualizacao_mais_nova": (dj.atualizacao_mais_nova or "")[:10] or None,
                  "coletado_em": dj.coletado_em, "execucoes_restantes": dj.execucoes},
        **camada,
    }
    try:  # a cópia de uso pessoal, no cache (fora do git)
        destino = r.cache / "datajud" / "camada.json"
        destino.parent.mkdir(parents=True, exist_ok=True)
        _gravar(destino, _json(camada))
    except OSError as exc:
        log.warning("Tribunais: a camada do DataJud não foi gravada no cache (%s)", exc)
    if not publicar:
        return dj, None
    # Publicado: a fonte diz de quando são os movimentos mesmo quando o frescor (uma agregação sobre o
    # índice inteiro) não respondeu hoje; o aviso não diz "sem número" quando a camada tem número.
    fonte, f = dict(dj.fonte), camada["fonte"]
    if not fonte.get("data_dados") and f.get("atualizacao_mais_nova"):
        fonte["data_dados"] = f["atualizacao_mais_nova"]
    if not fonte.get("coletado_em") and f.get("coletado_em"):
        fonte["coletado_em"] = f["coletado_em"]
    if fonte.get("ok") is False and (camada.get("mesa_inteira") or {}).get("com_data"):
        quando = _br(str(f.get("coletado_em") or "")[:10] or None, ano=False)
        fonte["aviso"] = (f"O DataJud não deu resposta completa em {_br(r.opcoes.hoje, ano=False)}; a camada do CNJ usa os movimentos já "
                          f"guardados (o último lote veio em {quando}).")
    dj.fonte = fonte  # type: ignore[assignment]
    return dj, camada


def atualizar(
    root: Path | None,
    data_dir: Path,
    cache_dir: Path,
    opcoes: Opcoes,
    *,
    fetch: net.Fetcher = net.fetch,
    regras: Regras | None = None,
    coletor: ColetorTribunais | None = None,
    relogio: Callable[[], float] = time.time,
    dormir: Callable[[float], None] = time.sleep,
) -> Resultado:
    """Coleta as fontes, refaz as contas e grava ``data/tribunais/`` (ver o topo do módulo).

    ``data_dir``: a pasta ``data/`` publicada; ``cache_dir``: fora do git. Devolve o
    :class:`Resultado`; fonte que falha vira aviso, nunca exceção."""
    regras = regras or modelo.carregar_regras(root)
    pasta = pasta_de(data_dir)
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    coletor = coletor or ColetorTribunais(cache, regras=regras, fetch=fetch, relogio=relogio, dormir=dormir)
    r = Rodada(pasta, cache, opcoes, regras, coletor, relogio, root)
    resultado = Resultado(pasta=pasta)

    leitura_tpu = _tpu(r)
    tribunais: dict[str, Tribunal] = {}
    for trib, rodar in (("stj", _stj), ("stf", _stf)):
        if opcoes.so and opcoes.so != trib:
            continue
        tribunais[trib] = rodar(r)

    # DataJud (o último): a conferência usa o painel do STJ em uso; os movimentos, a mesa dele.
    painel_stj = (tribunais.get("stj") and tribunais["stj"].painel) or ler_json(pasta / "stj.json")
    painel_stj = painel_stj if isinstance(painel_stj, dict) else None
    dj, camada = _datajud(r, tribunais, painel_stj)
    resultado.datajud = dj.log
    fixos = {CNJ: _json(camada)} if camada is not None else {}

    # Os arquivos de hoje (em memória), com os tetos conferidos.
    por_trib = _conferir_tamanhos(r, tribunais, fixos)
    refeitos = {trib: tribunais[trib] for trib in por_trib}
    # As fontes de cada tribunal: as de hoje quando o painel foi refeito; senão, só o aviso do
    # acervo, e as acessórias ficam como estavam (são as do painel que continua publicado).
    leituras: dict[str, Leitura | None] = {fid: None for fid in IDS_FONTES}
    leituras["tpu"] = leitura_tpu
    for trib, t in tribunais.items():
        if trib in refeitos:
            leituras.update(t.leituras)
        else:
            leituras[f"{trib}_acervo"] = t.leituras.get(f"{trib}_acervo")
    achados = _achados(r, refeitos)

    fontes: dict[str, Any] = {}
    for fid in IDS_FONTES:
        if fid == "datajud":
            fontes[fid] = dict(dj.fonte)
            continue
        if fid == "djen":
            fontes[fid] = fonte_djen(pasta, r.hoje, root)
            continue
        trib = fid.split("_", 1)[0] if fid != "tpu" else "cnj"
        fontes[fid] = r.entrada_meta(fid, leituras.get(fid), trib)
    cobertura: dict[str, Any] = {}
    anterior_cobertura = r.anterior.get("cobertura") or {}
    for trib in ("stj", "stf"):
        if trib in refeitos and refeitos[trib].cobertura is not None:
            cobertura[trib] = dict(refeitos[trib].cobertura)  # type: ignore[arg-type]
        elif isinstance(anterior_cobertura.get(trib), dict):
            cobertura[trib] = dict(anterior_cobertura[trib])
    if dj.conferencia is not None or camada is not None:
        cobertura.setdefault("stj", {})["datajud"] = {**(dj.conferencia or {}),
                                                     **({"movimentos": _cobertura_cnj(camada)} if camada is not None else {})}
    elif "stj" in cobertura:
        cobertura["stj"].pop("datajud", None)  # sem publicar: nada do DataJud no meta.json
    avisos = [f"{f['nome']}: {f['aviso']}" for f in fontes.values() if f.get("aviso") and f.get("ok") is not None]
    coletados = [f["coletado_em"] for f in fontes.values() if f.get("ok") and f.get("coletado_em")]
    meta = {
        "v": VERSAO,
        "hoje": opcoes.hoje,
        "atualizado_em": max(coletados) if coletados else r.anterior.get("atualizado_em"),
        "proxima_execucao": proxima_execucao(fontes, r.agora),
        "fontes": fontes,
        "cobertura": cobertura,
        "reguas": regras.reguas_para_meta(),
        "avisos": avisos,
        "correcoes": list(r.anterior.get("correcoes") or []),
    }

    # Só a proxima_execucao mudou (fonte atrasada: agora + 2 h a cada execução): fica a anterior e
    # nada é regravado (sem commit, sem build do Pages). Com a marca no passado, o "decidir" roda a
    # cada gatilho, e cada uma dessas execuções é barata (nenhum pedido novo, ~1,5 min).
    if r.anterior.get("proxima_execucao") and {k: v for k, v in meta.items() if k != "proxima_execucao"} == {
            k: v for k, v in json.loads(_json(r.anterior)).items() if k != "proxima_execucao"}:
        meta["proxima_execucao"] = r.anterior["proxima_execucao"]

    arquivos: dict[str, str] = {}
    apagar: list[Path] = []
    for trib, arqs in por_trib.items():  # os dossiês primeiro, o painel depois
        for rel in sorted(arqs, key=lambda x: (not x.startswith(MINISTROS), x)):
            arquivos[rel] = arqs[rel]
        novos = {Path(rel).name for rel in arqs if rel.startswith(MINISTROS)}
        apagar += [c for c in sorted((pasta / MINISTROS).glob(f"{trib}-*.json")) if c.name not in novos]
    if camada is not None and CNJ in fixos:
        arquivos[CNJ] = fixos[CNJ]
    else:
        apagar.append(pasta / CNJ)  # sem publicar, a camada do CNJ sai da pasta pública
    arquivos[ACHADOS] = _json(achados)
    # A agenda da semana, ao lado dos achados: por último (os painéis e o meta já estão decididos) e
    # sem poder impedi-los. O meta.json continua sendo o último arquivo a trocar de lugar.
    meta_texto = _json(meta)
    semanal = _pauta_semanal(r, tribunais, root)
    if semanal is not None and _cabe_a_pauta(r, arquivos, apagar, len(meta_texto.encode("utf-8")) + len(semanal.encode("utf-8"))):
        arquivos[PAUTA_SEMANAL] = semanal
    arquivos[META] = meta_texto
    for rel in (ACHADOS, META):
        if len(arquivos[rel].encode("utf-8")) > TETO_JSON:  # não deve acontecer (~15 KB e ~10 KB): avisa e grava
            r.operacao(f"{rel} com {len(arquivos[rel].encode('utf-8')) // 1000} KB, acima do teto de {TETO_JSON // 1000} KB")
    resultado.gravados, resultado.apagados = _trocar(pasta, arquivos, apagar)

    # O diário dos pedidos (artefato do fluxo) e a limpeza do cache.
    if coletor.diario:
        try:
            diario = cache / "diario" / f"{r.instante.replace(':', '')}.jsonl"
            diario.parent.mkdir(parents=True, exist_ok=True)
            diario.write_text("".join(json.dumps(linha, ensure_ascii=False) + "\n" for linha in coletor.diario), encoding="utf-8")
        except OSError as exc:
            log.warning("Tribunais: diário da coleta não gravado (%s)", exc)
    # A poda do cache roda sempre (o aviso informativo das íntegras não é falha) e nunca apaga o que
    # um estado cita: o socorro de cada fonte, as séries e o acervo do STJ em uso.
    citados = r.estado.citados()
    apagados = coletor.limpar(dias=10.0, proteger=citados)
    acervos = coletor.podar_grandes(proteger=citados)
    if apagados or acervos:
        log.info("Tribunais: %d arquivo(s) velhos e %d acervo(s) do STJ fora de uso apagados do cache", apagados, len(acervos))

    resultado.refeitos = sorted(refeitos)
    resultado.numeros = {trib: _numeros(t) for trib, t in refeitos.items()}
    resultado.fontes = fontes
    resultado.avisos = [*avisos, *[a for a in r.avisos_operacao if a not in avisos]]
    resultado.requisicoes = coletor.requisicoes
    resultado.megabytes = coletor.bytes / 1e6
    resultado.segundos = round(relogio() - r.inicio, 1)
    return resultado


__all__ = ["Estado", "Leitura", "Opcoes", "Resultado", "Retrato", "atualizar", "pasta_de", "proxima_execucao"]
