"""A agenda do Congresso para a Agenda da semana (``data/congresso/agenda.json``).

O que está pautado na Câmara, no Senado e no Congresso, da segunda-feira da semana corrente até
13 dias depois, no formato de :mod:`qijournal.agenda.contrato` (o mesmo arquivo que o fluxo
Tribunais grava para o STF e o STJ). Só registro oficial e sinais para a régua: nenhum texto é
escrito aqui, nenhuma conta de prazo é feita à mão e nenhum placar é guardado.

Fontes (dados abertos, sem chave), na ordem em que são pedidas:

1. **Plenários.** Câmara: ``/api/v2/eventos`` (a janela inteira, com todas as páginas) e
   ``/eventos/<id>/pauta`` de cada sessão deliberativa do Plenário; sem sessão encerrada na
   janela, ``/orgaos/<id>/eventos`` das duas semanas anteriores, para achar a última e guardar a
   **fila** (o que ficou sem votação). Senado: ``plenario/agenda/mes/<AAAAMMDD>`` (uma chamada
   por mês da janela) e, havendo sessão deliberativa encerrada, ``plenario/resultado/mes/<data>``.
   Congresso: ``plenario/agenda/cn/<de>/<ate>`` (vazia = nenhuma sessão conjunta convocada).
2. **Senado, comissões que decidem** (``config/agenda.yaml``: CCJ e CAE):
   ``comissao/agenda/<de>/<ate>``.
3. **Prazos de medida provisória**: ``processo/prazo?sigla=MPV&dataReferencia=<hoje>`` (o tipo
   "Deliberação da Medida Provisória" traz em ``fimPrazo`` o último dia de vigência), casado pelo
   ``idProcesso`` com ``processo?sigla=MPV&tramitando=S`` (o número, a ementa e o apelido oficial).
   Medida provisória anterior a 2002 fica fora.
4. **Câmara, comissões que decidem** (CCJC e CFT): a pauta de cada reunião deliberativa.
5. **O resto**: comissão mista que decide (a CMO aparece nos eventos da Câmara) e a contagem de
   vetos (``materia/vetos/aposrcn``).

Cuidados vistos nas respostas reais (cada um tem teste em ``tests/test_congresso_agenda.py``):

- em comissão a linha da pauta é o parecer e o projeto está na proposição relacionada: vale a
  proposição cujo tipo, número e ano são os do campo ``titulo``;
- linhas repetidas para o mesmo item viram um item; evento cancelado sai da seleção;
- a resposta do Senado é XML convertido: lista de um item só chega como objeto
  (:func:`qijournal.congresso.fontes.lista`); janela que vira o mês pede duas chamadas;
- todo texto oficial passa por :func:`qijournal.agenda.contrato.limpar_texto` (a Câmara devolve
  ``nº?200`` no lugar do espaço não separável);
- o Plenário da Câmara só registra a sessão deliberativa na véspera ou no dia: o estado da
  Câmara guarda até quando a pauta está publicada e a fila da última sessão encerrada;
- resultado só de sessão encerrada, só a palavra e a frase oficial (:func:`resultado_da_camara`,
  :func:`resultado_do_senado`): na Câmara, só quando o texto traz uma única frase "Aprovado (ou
  Rejeitado) o Projeto (a Proposta, o Substitutivo, a Medida Provisória) nº N, de AAAA" com o
  número do próprio item; com nenhuma ou mais de uma, sem resultado.

Rede: um prazo global por rodada (:data:`PRAZO_TOTAL`), um teto de pedidos (:data:`MAX_PEDIDOS`)
e um ``fetch`` próprio (:func:`fetch_com_prazo`: prazo curto por pedido e uma nova tentativa)
injetado no :class:`~qijournal.congresso.fontes.Coletor`. Quando o prazo acaba, a coleta para de
pedir e grava o parcial, com a situação de cada fonte. Fonte fora do ar mantém as sessões da
instituição que estavam no arquivo anterior (:func:`qijournal.agenda.contrato.mesclar_com_anterior`)
e nunca vira zero. A saída é validada pelo contrato antes de gravar: saída inválida não é
gravada (fica o arquivo anterior e o erro vai para o registro). Nada aqui depende da
legislatura configurada.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import net, text
from ..agenda import contrato, regras as regras_da_agenda
from ..agenda.contrato import Agenda, EstadoInstituicao, FonteAgenda, ItemAgenda, Recesso, Relator, Resultado, SessaoAgenda
from ..net import FetchError, Fetcher
from . import dados, fontes, materias, pauta as calendario_oficial, regras as regras_do_congresso
from .fontes import Coletor

log = logging.getLogger(__name__)

LADO = contrato.LADO_CONGRESSO
CAMARA, SENADO, CONGRESSO = contrato.CAMARA, contrato.SENADO, contrato.CONGRESSO

PRAZO_TOTAL = 150.0  # segundos de rede por rodada (o passo do fluxo diário tem 5 minutos)
PRAZO_PEDIDO = 18.0  # segundos por pedido (a pauta de uma comissão já levou 16 s para responder)
NOVAS_TENTATIVAS = 1  # uma nova tentativa por pedido; o padrão do Coletor (90 s e 4) comeria o passo inteiro
MAX_PEDIDOS = 40  # teto de pedidos de rede por rodada, contando as novas tentativas
PAUSA = 0.15  # intervalo mínimo entre pedidos (o Senado devolve 429 acima de 10 por segundo)
VALIDADE_VETOS_H = 20.0  # a lista de vetos (279 KB) só dá uma contagem: uma leitura por dia basta
DIAS_DA_FILA = 14  # até onde voltar para achar a última sessão deliberativa do Plenário da Câmara
PLENARIO_DA_CAMARA = 180  # o id do órgão "Plenário" na API da Câmara (confirmado na lista de eventos, quando ela traz)
MPV_DESDE = 2002  # medida provisória anterior à Emenda Constitucional 32 (de 2001) não tem prazo: fica fora
PRAZO_DE_MPV = "deliberacao da medida provisoria"  # o tipo de prazo cujo fim é o último dia de vigência
SEM_PRAZO = "não lida: o prazo de rede da rodada acabou antes"

# As fontes, como a tela as chama e onde o leitor confere.
FONTES = {
    "camara_eventos": (CAMARA, "Câmara: eventos e pautas", f"{fontes.CAMARA_API}/eventos"),
    "senado_plenario": (SENADO, "Senado: agenda do Plenário", f"{fontes.SENADO_API}/plenario/agenda/mes"),
    "senado_comissoes": (SENADO, "Senado: agenda das comissões", f"{fontes.SENADO_API}/comissao/agenda"),
    "congresso_sessoes": (CONGRESSO, "Congresso: sessões conjuntas e vetos", f"{fontes.SENADO_API}/plenario/agenda/cn"),
    "congresso_prazos": (CONGRESSO, "Congresso: prazos de medida provisória", f"{fontes.SENADO_API}/processo/prazo"),
    "congresso_comissoes": (CONGRESSO, "Congresso: comissões mistas (eventos na Câmara)", f"{fontes.CAMARA_API}/eventos"),
}

# Páginas oficiais.
LINK_EVENTO_DA_CAMARA = "https://www.camara.leg.br/evento-legislativo/{id}"
LINK_PROPOSICAO_DA_CAMARA = "https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={id}"
LINK_MATERIA_DO_SENADO = "https://www25.senado.leg.br/web/atividade/materias/-/materia/{id}"
# A Ordem do Dia do Plenário no dia (rota casa/:casa/:dia/:mes/:ano do Portal da Ordem do Dia).
LINK_ORDEM_DO_DIA = "https://legis.senado.leg.br/ordiaportal/casa/{casa}/{dia}/{mes}/{ano}"
LINK_REUNIAO_DO_SENADO = "https://legis.senado.leg.br/atividade/comissoes/comissao/{colegiado}/reuniao/{reuniao}"

# O alcance pela sigla (a segunda parte da régua); o que não está aqui vale "outro".
ALCANCE = {
    "PEC": "pec", "PLP": "plp", "PLS": "pl", "PLC": "pl", "PL": "pl", "MPV": "mpv", "PLV": "mpv", "VET": "veto",
    "PLN": "orcamento", "MSF": "indicacao", "PDL": "pdl", "PDC": "pdl", "PDS": "pdl", "PDN": "pdl",
}  # fmt: skip
REQUERIMENTOS = {"REQ", "RQS", "RQN", "RIC", "RCP", "REC", "INC", "RPD", "REL"}
# Numeração única nas duas Casas desde 2019: só para estas siglas vale procurar a ficha na outra Casa.
UNIFICADAS, UNIFICADAS_DESDE = {"PL", "PLP", "PDL", "MPV", "PLN", "VET", "PLV"}, 2019

_TITULO = re.compile(r"^([A-Z]{2,8}) 0*(\d{1,6})/(\d{4})\b")
_HORA = re.compile(r"^(\d{1,2}):(\d{2})")
_AREA = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_UF = re.compile(r"^[A-Z]{2}$")
_NUMERO_E_ANO = re.compile(r"(?i)n[º°o]s?\.?\s*([\d.]+)\s*,\s*de\s*(\d{4})")
# A decisão de mérito do próprio item, no começo de uma frase do registro da Câmara.
_DECISAO = re.compile(r"(Aprovad[oa]|Rejeitad[oa]) [oa] (?:Projeto|Proposta|Substitutivo|Subemenda Substitutiva|Medida Provisória)\b")
# O registro diz para onde a matéria vai depois de votada: os destaques já foram decididos.
_SEGUE = re.compile(r"(?:^|[.;:•!?]\s*)A [Mm]atéria vai (?:à|ao|a|às|aos)\b")
_ABREVIATURAS = {"art", "arts", "n", "nº", "no", "dep", "deps", "sr", "srs", "sra", "sras", "inc", "par", "al", "exmo", "exma"}
_NAO_APRECIADO = {"materia nao apreciada em face do encerramento da sessao", "nao deliberado", "nao apreciado", "nao apreciada"}
_RESULTADO_DA_MATERIA = re.compile(r"(?i)Resultado da matéria:\s*([^\n\r]+)")
_OBJETOS_DO_SENADO = ("projeto", "substitutivo", "proposta", "medida provisoria", "materia", "indicacao")
_RELATOR_DE_PLENARIO = re.compile(r"Designad[oa] Relator[a]? de Plenário [oa] Senador[a]? ([^.\n]+)\.")
_RELATOR_DE_COMISSAO = re.compile(r"Relator[a]?: Senador[a]? ([^,;:\n]+)")
# "Tramita em regime de urgência, nos termos do Requerimento nº 770, de 2026": a urgência já aprovada
# (o pedido ainda "pendente de deliberação" não é regime de urgência).
_EM_URGENCIA = re.compile(r"(?i)\b((?:tramita(?:ndo)? )?em regime de urgência(?:, nos termos do Requerimento nº [\d.]+, de \d{4})?)")
_RADIODIFUSAO = re.compile(r"\b(radiodifusao|retransmissao de televisao)\b")
_PLACAR = re.compile(r"(?i)\b(?:sim|n[ãa]o|absten[çc][ãa]o|total)\s*:\s*\d")  # nenhum placar sai daqui


# ── o prazo de rede ──────────────────────────────────────────────────────────


class PrazoEsgotado(Exception):
    """O prazo de rede (ou o teto de pedidos) da rodada acabou: o que falta fica para a próxima."""


@dataclass
class Orcamento:
    """O relógio e a conta de pedidos de uma rodada."""

    prazo: float = PRAZO_TOTAL
    maximo: int = MAX_PEDIDOS
    relogio: Callable[[], float] = time.monotonic
    pedidos: int = 0
    inicio: float = field(init=False)

    def __post_init__(self) -> None:
        self.inicio = self.relogio()

    def gasto(self) -> float:
        return self.relogio() - self.inicio

    def resta(self) -> float:
        return self.prazo - self.gasto()

    @property
    def esgotado(self) -> bool:
        return self.resta() <= 0 or self.pedidos >= self.maximo


def fetch_com_prazo(
    fetch: Fetcher,
    orcamento: Orcamento,
    *,
    prazo: float = PRAZO_PEDIDO,
    novas_tentativas: int = NOVAS_TENTATIVAS,
    dormir: Callable[[float], None] = time.sleep,
) -> Fetcher:
    """O ``fetch`` da agenda, para injetar no :class:`Coletor`: troca o prazo e as tentativas que
    o Coletor pede (90 s e 4) por ``prazo`` segundos e ``novas_tentativas``, e não pede mais nada
    depois que o orçamento da rodada acaba (:class:`PrazoEsgotado`). O prazo de cada pedido
    encolhe com o que resta da rodada, para um servidor lento não passar do prazo global."""

    def pedir(url: str, **opcoes: Any) -> Any:
        ultimo: Exception | None = None
        for tentativa in range(novas_tentativas + 1):
            if tentativa:
                dormir(1.0)
            if orcamento.esgotado:
                break
            orcamento.pedidos += 1
            opcoes["timeout"] = min(prazo, max(1.0, orcamento.resta() / net.TOTAL_DEADLINE_FACTOR))
            opcoes["retries"] = 0
            try:
                return fetch(url, **opcoes)
            except FetchError as exc:
                ultimo = exc
                if exc.status is not None and exc.status not in net.RETRY_STATUS:
                    break
        if ultimo is not None:
            raise ultimo
        raise PrazoEsgotado(url)

    return pedir


# ── o que a coleta lê de data/congresso/ e do config ─────────────────────────


@dataclass(frozen=True)
class Casada:
    """A ficha do Congresso que casou com um item: a âncora, a nota (só desempata), a área e o
    apelido oficial (o que o Senado publica; apelido da tabela do config não é oficial)."""

    chave: str | None = None
    nota: int | float | None = None
    area: str | None = None
    apelido: str | None = None
    simbolica: bool = False


@dataclass(frozen=True)
class Apoio:
    """O que enriquece cada item, tudo opcional (arquivo ausente ou ilegível vale vazio): as
    fichas das matérias (``materias-cd.json`` e ``materias-sf.json``), as pessoas de
    ``resumo.json`` (o partido e o estado do relator), os padrões de matéria simbólica e a tabela
    de apelidos de ``config/congresso.yaml`` (para NÃO tomar apelido de imprensa por oficial)."""

    fichas: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)  # {"cd": {título: ficha}, "sf": {…}}
    deputados: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)  # id → linha do resumo
    senadores: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)  # nome normalizado → linha do resumo
    simbolicas: tuple[re.Pattern[str], ...] = ()
    apelidos_da_tabela: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    calendario: tuple[Any, ...] = ()  # os marcos de config/congresso.yaml (recessos)

    def ficha(self, casa: str, titulo: str, id_na_casa: int | None) -> Casada:
        """A ficha da matéria: primeiro na da mesma Casa, conferindo o id (a proposição na Câmara,
        a matéria no Senado); depois na da outra Casa, só para as siglas de numeração única e de
        2019 em diante (antes disso PLS e PLC repetiam números). Sem casamento, tudo vazio."""
        outra = "sf" if casa == "cd" else "cd"
        achado = _TITULO.match(titulo)
        propria = (self.fichas.get(casa) or {}).get(titulo)
        alheia = (self.fichas.get(outra) or {}).get(titulo)
        if not (isinstance(alheia, Mapping) and achado and achado.group(1) in UNIFICADAS and int(achado.group(3)) >= UNIFICADAS_DESDE):
            alheia = None
        if isinstance(propria, Mapping) and id_na_casa is not None and fontes.inteiro(propria.get("id")) == id_na_casa:
            casada = self._casada(casa, titulo, propria)
            if alheia is not None:  # o que a ficha da própria Casa não traz (o tema, o apelido oficial) pode estar na da outra
                da_outra = self._casada(outra, titulo, alheia)
                casada = replace(casada, area=casada.area or da_outra.area, apelido=casada.apelido or da_outra.apelido)
            return casada
        return self._casada(outra, titulo, alheia) if alheia is not None else Casada()

    def _casada(self, casa: str, titulo: str, ficha: Mapping[str, Any]) -> Casada:
        chave = materias.chave(casa, titulo) or None
        rel = ficha.get("rel") if isinstance(ficha.get("rel"), Mapping) else {}
        nota = rel.get("n")
        if isinstance(nota, bool) or not isinstance(nota, (int, float)) or not 0 <= nota <= 100:
            nota = None
        temas = ficha.get("tm") if isinstance(ficha.get("tm"), list) else []
        area = next((t for t in temas if isinstance(t, str) and _AREA.match(t)), None)
        apelido = contrato.limpar_texto(ficha.get("ap")) if casa == "sf" else None
        if apelido and text.normalize(apelido) in {text.normalize(a) for a in self.apelidos_da_tabela.get(titulo, ())}:
            apelido = None  # veio da tabela do config (apelido de imprensa): serve para ligar, não para nomear
        return Casada(chave=chave, nota=nota if chave else None, area=area, apelido=apelido, simbolica=bool(rel.get("simb")))

    def simbolica(self, ementa: str | None, casada: Casada) -> bool:
        """Matéria simbólica (dia nacional, homenagem, denominação): só entra em contagem."""
        if casada.simbolica:
            return True
        alvo = text.normalize(ementa or "")
        return bool(alvo) and any(p.search(alvo) for p in self.simbolicas)

    def relator_da_camara(self, bruto: Any) -> Relator | None:
        if not isinstance(bruto, Mapping):
            return None
        nome = contrato.limpar_texto(bruto.get("nome"))
        if not nome:
            return None
        pessoa = self.deputados.get(fontes.inteiro(bruto.get("id")) or -1) or {}
        return _relator(nome, bruto.get("siglaPartido") or pessoa.get("partido"), bruto.get("siglaUf") or pessoa.get("uf"))

    def relator_do_senado(self, nome: Any, partido: Any = None, uf: Any = None) -> Relator | None:
        limpo = contrato.limpar_texto(re.sub(r"^Senador[a]?\s+", "", str(nome or "")).rstrip(" .;:"))
        if not limpo:
            return None
        pessoa = self.senadores.get(text.normalize(limpo)) or {}
        return _relator(contrato.limpar_texto(pessoa.get("nome")) or limpo, partido or pessoa.get("partido"), uf or pessoa.get("uf"))

    def recesso(self, de: str, ate: str) -> Recesso | None:
        """O recesso do calendário oficial que alcança a janela (o primeiro, se houver mais de um)."""
        try:
            marcos = calendario_oficial.calendario(self.calendario, de, ate)
        except Exception:  # noqa: BLE001 — calendário malformado no config: a agenda sai sem o recesso
            return None
        for marco in marcos:
            if "recesso" in text.normalize(str(marco.get("nome") or "")):
                nome = contrato.limpar_texto(marco.get("nome"))
                if nome:
                    return Recesso(nome=nome, de=marco["de"], ate=marco["ate"])
        return None


def _relator(nome: str, partido: Any, uf: Any) -> Relator:
    partido = contrato.limpar_texto(partido)
    uf = str(uf or "").strip().upper()
    return Relator(nome=nome[:200], partido=partido if partido and len(partido) <= 30 else None, uf=uf if _UF.match(uf) else None)


def ler_apoio(root: Path | str | None, pasta: Path | str) -> Apoio:
    """Monta o :class:`Apoio` com o que houver em ``pasta`` (``data/congresso/``) e em
    ``<root>/config/congresso.yaml``. Nunca levanta exceção: o que faltar fica vazio."""
    pasta = Path(pasta)
    fichas: dict[str, Mapping[str, Any]] = {}
    for casa in ("cd", "sf"):
        bruto = dados.ler_json(pasta / materias.ARQUIVO.format(casa=casa))
        conteudo = bruto.get("materias") if isinstance(bruto, dict) else None
        fichas[casa] = conteudo if isinstance(conteudo, dict) else {}
    deputados: dict[int, Mapping[str, Any]] = {}
    senadores: dict[str, Mapping[str, Any]] = {}
    resumo = dados.ler_json(pasta / dados.RESUMO)
    for pessoa in (resumo.get("pessoas") if isinstance(resumo, dict) else None) or []:
        if not isinstance(pessoa, dict):
            continue
        casa, _, ident = str(pessoa.get("k") or "").partition("-")
        if casa == "cd" and fontes.inteiro(ident) is not None:
            deputados[int(ident)] = pessoa
        elif casa == "sf" and pessoa.get("nome"):
            senadores.setdefault(text.normalize(str(pessoa["nome"])), pessoa)
    simbolicas: tuple[re.Pattern[str], ...] = ()
    apelidos: dict[str, tuple[str, ...]] = {}
    calendario: tuple[Any, ...] = ()
    try:
        regras = regras_do_congresso.carregar(Path(root)) if root is not None else regras_do_congresso.Regras()
    except Exception as exc:  # noqa: BLE001 — config/congresso.yaml ausente ou quebrado não para a agenda
        log.warning("Agenda do Congresso: config/congresso.yaml ilegível (%s); a coleta segue com os padrões", exc)
        regras = None
    padroes = regras.relevancia.simbolicas if regras is not None else regras_do_congresso.Relevancia().simbolicas
    try:
        simbolicas = tuple(re.compile(p) for p in padroes)
    except re.error:
        simbolicas = ()
    if regras is not None:
        apelidos = {titulo: tuple(nomes) for titulo, nomes in regras.apelidos.items()}
        calendario = tuple(regras.pauta.calendario)
    return Apoio(fichas=fichas, deputados=deputados, senadores=senadores, simbolicas=simbolicas, apelidos_da_tabela=apelidos, calendario=calendario)


# ── utilitários ──────────────────────────────────────────────────────────────


def meses_da_janela(de: str, ate: str) -> list[str]:
    """As datas de onde pedir os serviços do Senado que vão "da data até o fim do mês": o começo
    da janela e o dia 1º de cada mês seguinte que ela alcança (janela que vira o mês: duas)."""
    inicio, fim = date.fromisoformat(de), date.fromisoformat(ate)
    datas = [inicio]
    while True:
        proximo = (datas[-1].replace(day=1) + timedelta(days=32)).replace(day=1)
        if proximo > fim:
            break
        datas.append(proximo)
    return [d.isoformat() for d in datas]


def _compacta(dia: str) -> str:
    return dia.replace("-", "")


def _hora(valor: Any) -> str | None:
    """``"9:00"`` → ``"09:00"``; o que não for hora vale ``None`` (a agenda nunca inventa hora)."""
    achado = _HORA.match(str(valor or "").strip())
    if not achado or int(achado.group(1)) > 23 or int(achado.group(2)) > 59:
        return None
    return f"{int(achado.group(1)):02d}:{achado.group(2)}"


def _data_e_hora(valor: Any) -> tuple[str | None, str | None]:
    """``"2026-11-23T18:32"`` (hora de Brasília, sem fuso) → ``("2026-11-23", "18:32")``."""
    texto_ = str(valor or "")
    dia = texto_[:10]
    try:
        date.fromisoformat(dia)
    except ValueError:
        return None, None
    return dia, _hora(texto_[11:16])


def _modalidade(texto_: Any) -> str | None:
    """Só quando a fonte diz: "Semipresencial", "Presencial", "Virtual" ou "Remota"."""
    alvo = text.normalize(str(texto_ or ""))
    if "semipresencial" in alvo:
        return "semipresencial"
    if "presencial" in alvo:
        return "presencial"
    if "virtual" in alvo or "remota" in alvo:
        return "virtual"
    return None


def _situacao(texto_: Any) -> str | None:
    """A situação da sessão no vocabulário do contrato. ``None`` = cancelada (sai da seleção).
    Câmara: Convocada, Agendada, Em Andamento, Encerrada, "Encerrada (Final)", Cancelada. Senado:
    Agendada, "Em andamento", Encerrada, Realizada, Cancelada. Rótulo desconhecido vale agendada."""
    alvo = text.normalize(str(texto_ or ""))
    if alvo.startswith("cancelad") or "nao realizad" in alvo:
        return None
    if alvo.startswith(("encerrad", "realizad")):
        return "encerrada"
    if alvo.startswith(("em andamento", "abert", "suspens")):
        return "em_andamento"
    if alvo.startswith("adiad"):
        return "adiada"
    return "agendada"


def _identificador(titulo: Any) -> tuple[str, str, int, int] | None:
    """``"PL 3220/2019 (Substitutivo-CD)"`` → ``("PL 3220/2019", "PL", 3220, 2019)``."""
    achado = _TITULO.match(contrato.limpar_texto(titulo) or "")
    if not achado:
        return None
    sigla, numero, ano = achado.group(1), int(achado.group(2)), int(achado.group(3))
    return contrato.identificador_de(sigla, numero, ano), sigla, numero, ano


def _frase(texto_: str, inicio: int = 0) -> str:
    """A frase que começa em ``inicio``: vai até o primeiro ponto final que não é de número
    ("nº 1.300") nem de abreviatura ("art. 198", "Dep. Fulano")."""
    for ponto in re.finditer(r"\.", texto_[inicio:]):
        fim = inicio + ponto.end()
        if fim < len(texto_) and texto_[fim].isdigit():
            continue
        palavra = re.search(r"(\w+)$", texto_[inicio : fim - 1])
        if palavra and palavra.group(1).lower() in _ABREVIATURAS:
            continue
        return texto_[inicio:fim].strip()
    return texto_[inicio:].strip()


def _texto_do_resultado(frase: str | None) -> str | None:
    """A frase oficial que acompanha a palavra: limpa, sem placar e dentro do teto do contrato."""
    limpa = contrato.limpar_texto(frase)
    if not limpa or len(limpa) > contrato.TEXTO_MAXIMO["resultado"] or _PLACAR.search(limpa):
        return None
    return limpa


def _so_antes_do_fim(resultado: Resultado | None, situacao: str) -> Resultado | None:
    """Sessão que não está encerrada não tem resultado: antes do fim só cabem retirada e adiamento."""
    if resultado is None or situacao == "encerrada" or resultado.palavra in contrato.RESULTADOS_ANTES_DO_FIM:
        return resultado
    return None


# ── resultado (dias passados): só a palavra, com regra estrita ───────────────


def resultado_da_camara(textos: Iterable[Any], sigla: str, numero: int, ano: int) -> Resultado | None:
    """O resultado de um item da Câmara, do(s) texto(s) de ``situacaoItem`` das linhas dele.

    A palavra "aprovado" ou "rejeitado" só sai quando o registro traz UMA frase, no começo de um
    período, "Aprovado (Rejeitado) o Projeto (a Proposta, o Substitutivo, a Subemenda
    Substitutiva, a Medida Provisória) … nº N, de AAAA" com o número e o ano do próprio item. Com
    nenhuma ou com mais de uma frase assim, não há resultado: o texto de um item em votação
    junta requerimentos, emendas e destaques, cada um com a sua decisão. "Aprovado, ressalvados
    os destaques" vale enquanto o registro não diz para onde a matéria vai ("A matéria vai à
    Sanção", "vai ao Senado Federal"): depois disso os destaques já foram decididos e a palavra é
    "aprovado" (a frase oficial fica como está). Fora disso só valem
    três registros inteiros e sem ambiguidade: "Matéria não apreciada em face do encerramento da
    Sessão." (ou "Não deliberado."), o que começa por "Retirado de pauta" e o que começa por
    "Vista". Nenhum placar é lido nem devolvido."""
    distintos: list[str] = []
    for bruto in textos:
        limpo = contrato.limpar_texto(bruto)
        if limpo and limpo not in distintos:
            distintos.append(limpo)
    if not distintos:
        return None
    candidatas: dict[str, tuple[str, str, bool]] = {}
    for texto_ in distintos:
        for achado in _DECISAO.finditer(texto_):
            antes = texto_[: achado.start()].rstrip()
            if antes and antes[-1] not in ".;:•!?":
                continue  # não abre período: é citação dentro de outra frase
            frase = _frase(texto_, achado.start())
            numeros = {(fontes.inteiro(n.replace(".", "")), int(a)) for n, a in _NUMERO_E_ANO.findall(frase)}
            if (numero, ano) not in numeros:
                continue  # a frase não nomeia o próprio item
            segue = bool(_SEGUE.search(texto_[achado.start() :]))
            candidatas.setdefault(text.normalize(frase), (achado.group(1), frase, segue))
    if len(candidatas) > 1:
        return None
    if candidatas:
        verbo, frase, segue = next(iter(candidatas.values()))
        if verbo.lower().startswith("rejeitad"):
            palavra = "rejeitado"
        elif "ressalvad" in text.normalize(frase) and not segue:
            palavra = "aprovado_ressalvados_destaques"  # o texto principal passou e ainda há destaques por votar
        else:
            palavra = "aprovado"
        return Resultado(palavra=palavra, texto=_texto_do_resultado(frase))
    if len(distintos) != 1:
        return None
    inteiro_, alvo = distintos[0], text.normalize(distintos[0])
    if alvo in _NAO_APRECIADO:
        return Resultado(palavra="nao_apreciado", texto=_texto_do_resultado(inteiro_))
    if alvo.startswith(("retirado de pauta", "retirada de pauta")):
        return Resultado(palavra="retirado_de_pauta", texto=_texto_do_resultado(_frase(inteiro_)))
    if alvo.startswith(("vista ", "concedida vista", "pedido de vista")):
        return Resultado(palavra="pedido_de_vista", texto=_texto_do_resultado(_frase(inteiro_)))
    return None


def resultado_do_senado(item: Mapping[str, Any]) -> Resultado | None:
    """O resultado oficial de um item do Plenário do Senado (``plenario/resultado``): a frase da
    linha "Resultado da matéria:", quando há uma só e ela decide a matéria (o projeto, o
    substitutivo, a proposta, a medida provisória, a indicação), e não um requerimento. Sem essa
    linha, só o que o campo ``descricaoDeliberacao`` diz sem ambiguidade: "Retirado" e "Não
    apreciado". Quem chama só aceita o resultado de sessão encerrada."""
    frases: list[str] = []
    for bruta in _RESULTADO_DA_MATERIA.findall(str(item.get("textoResultado") or "")):
        limpa = contrato.limpar_texto(bruta)
        if limpa and limpa not in frases:
            frases.append(limpa)
    if len(frases) > 1:
        return None
    if frases:
        alvo = text.normalize(frases[0])
        palavra = None
        if alvo.startswith(("retirado de pauta", "retirada de pauta")):
            palavra = "retirado_de_pauta"
        elif alvo.startswith(("prejudicad",)):
            palavra = "prejudicado"
        elif alvo.startswith(("adiad",)):
            palavra = "adiado"
        elif alvo.startswith(("aprovad", "rejeitad")):
            resto = alvo.split(" ", 2)[2] if alvo.count(" ") >= 2 else ""
            if resto.startswith(_OBJETOS_DO_SENADO):
                if alvo.startswith("rejeitad"):
                    palavra = "rejeitado"
                else:
                    palavra = "aprovado_ressalvados_destaques" if "ressalvados os destaques" in alvo else "aprovado"
        return Resultado(palavra=palavra, texto=_texto_do_resultado(frases[0])) if palavra else None
    deliberacao = text.normalize(str(item.get("descricaoDeliberacao") or ""))
    if deliberacao.startswith("retirad"):
        return Resultado(palavra="retirado_de_pauta", texto=_texto_do_resultado(item.get("descricaoTipoPauta")))
    if deliberacao in _NAO_APRECIADO:
        return Resultado(palavra="nao_apreciado")
    return None


def resultado_de_comissao_do_senado(item: Mapping[str, Any]) -> Resultado | None:
    """O que a agenda das comissões do Senado registra para um item já chamado: só os tipos de
    resultado que não dependem de leitura ("Retirado de pauta", "Adiado", vista, não apreciado).
    "Aprovado o relatório" é a decisão sobre o parecer, não sobre o projeto: fica sem palavra."""
    situacao = item.get("situacao") if isinstance(item.get("situacao"), Mapping) else {}
    alvo = text.normalize(str(situacao.get("descricaoTipoResultado") or item.get("siglaResultado") or ""))
    palavra = None
    if alvo.startswith(("retirado de pauta", "retirada de pauta")):
        palavra = "retirado_de_pauta"
    elif alvo.startswith("adiad"):
        palavra = "adiado"
    elif alvo.startswith(("vista", "pedido de vista", "concedida vista")):
        palavra = "pedido_de_vista"
    elif alvo in _NAO_APRECIADO:
        palavra = "nao_apreciado"
    return Resultado(palavra=palavra, texto=_texto_do_resultado(item.get("descricaoResultado"))) if palavra else None


# ── itens ────────────────────────────────────────────────────────────────────


def _estagios(candidatos: Sequence[str], detalhes: Mapping[str, str | None]) -> tuple[str, str | None, tuple[str, ...]]:
    """O estágio que a linha mostra, o detalhe dele e os demais que valem (sem o "pautado", que
    todo item pautado tem)."""
    principal = contrato.estagio_principal(candidatos)
    detalhe = contrato.limpar_texto(detalhes.get(principal))
    if detalhe and len(detalhe) > contrato.TEXTO_MAXIMO["detalhe"]:
        detalhe = None
    return principal, detalhe, tuple(e for e in candidatos if e not in (principal, "pautado"))


def _ordem(valor: Any) -> int | None:
    numero = fontes.inteiro(valor)
    return numero if numero is not None and numero >= 1 else None


def _ementa(valor: Any) -> str | None:
    limpa = contrato.limpar_texto(valor)
    return limpa[: contrato.TEXTO_MAXIMO["ementa"]].rstrip() if limpa else None


def itens_da_camara(linhas: Iterable[Any], *, situacao: str, apoio: Apoio) -> tuple[tuple[ItemAgenda, ...], Counter[str]]:
    """Os itens de uma pauta da Câmara (``/eventos/<id>/pauta``) e o que ficou só em contagem.

    Linhas repetidas para o mesmo título viram um item (os textos de situação de todas elas
    contam para o resultado). O projeto é a proposição cujo tipo, número e ano são os do campo
    ``titulo``: em comissão é a ``proposicaoRelacionada_`` (a ``proposicao_`` é o parecer).
    Requerimentos, redações finais, outorgas de rádio e TV (art. 223) e matérias simbólicas só
    entram em ``fora``, cada linha pelo primeiro motivo que couber."""
    itens: list[ItemAgenda] = []
    fora: Counter[str] = Counter()
    for titulo, repetidas in _linhas_por_item(linhas).items():
        linha = repetidas[0]
        partes = _identificador(titulo)
        topico = text.normalize(str(linha.get("topico") or ""))
        regime = contrato.limpar_texto(linha.get("regime")) or ""
        if (partes and partes[1] in REQUERIMENTOS) or topico.startswith("requerimento"):
            fora["requerimento"] += 1
            continue
        if topico.startswith(("redacoes finais", "redacao final")):
            fora["redacao_final"] += 1
            continue
        if partes is None:
            fora["outros"] += 1
            continue
        identificador, sigla, numero, ano = partes
        projeto = _projeto_da_linha(repetidas, sigla, numero, ano)
        ementa = _ementa((projeto or {}).get("ementa"))
        if text.normalize(regime).startswith("art 223") or (sigla in ("PDL", "PDC", "TVR") and _RADIODIFUSAO.search(text.normalize(ementa or ""))):
            fora["outorga"] += 1
            continue
        if projeto is None:
            fora["outros"] += 1  # sem a proposição do título não há id para conferir nem link oficial
            continue
        prop_id = fontes.inteiro(projeto.get("id"))
        casada = apoio.ficha("cd", identificador, prop_id)
        if apoio.simbolica(ementa, casada):
            fora["simbolica"] += 1
            continue
        textos = [linha_.get("situacaoItem") for linha_ in repetidas]
        resultado = _so_antes_do_fim(resultado_da_camara(textos, sigla, numero, ano), situacao)
        candidatos, detalhes = ["pautado"], {}
        if text.normalize(regime).startswith("urgencia"):
            candidatos.append("urgencia")
            detalhes["urgencia"] = regime
        if situacao == "em_andamento" and resultado is None and any(contrato.limpar_texto(t) for t in textos):
            candidatos.append("em_votacao")
        estagio, detalhe, outros = _estagios(candidatos, detalhes)
        itens.append(
            ItemAgenda(
                identificador=identificador,
                tipo=sigla,
                alcance=ALCANCE.get(sigla, "outro"),
                estagio=estagio,
                estagio_detalhe=detalhe,
                outros_estagios=outros,
                link=LINK_PROPOSICAO_DA_CAMARA.format(id=prop_id),
                apelido=casada.apelido,
                ementa=ementa,
                area=casada.area,
                resultado=resultado,
                relator=next((r for r in (apoio.relator_da_camara(x.get("relator")) for x in repetidas) if r), None),
                ordem=_ordem(linha.get("ordem")),
                ficha=casada.chave,
                relevancia=casada.nota,
            )
        )
    return tuple(itens), fora


def _linhas_por_item(linhas: Iterable[Any]) -> dict[str, list[Mapping[str, Any]]]:
    """As linhas de uma pauta da Câmara agrupadas pelo item (o identificador do título; título
    sem identificador fica como veio), na ordem em que cada item aparece pela primeira vez."""
    grupos: dict[str, list[Mapping[str, Any]]] = {}
    for linha in linhas:
        if isinstance(linha, Mapping):
            partes = _identificador(linha.get("titulo"))
            grupos.setdefault(partes[0] if partes else (contrato.limpar_texto(linha.get("titulo")) or ""), []).append(linha)
    return grupos


def pendentes_da_camara(linhas: Iterable[Any]) -> set[str]:
    """Os identificadores dos itens que ficaram sem apreciação numa pauta da Câmara: nenhuma
    linha do item tem registro de deliberação, ou o registro é só "Matéria não apreciada em
    face do encerramento da Sessão." (item com qualquer outro registro foi chamado: não é fila)."""
    pendentes = set()
    for identificador, repetidas in _linhas_por_item(linhas).items():
        textos = {text.normalize(t) for t in (contrato.limpar_texto(x.get("situacaoItem")) for x in repetidas) if t}
        if textos <= _NAO_APRECIADO:
            pendentes.add(identificador)
    return pendentes


def _projeto_da_linha(linhas: Iterable[Mapping[str, Any]], sigla: str, numero: int, ano: int) -> Mapping[str, Any] | None:
    """A proposição (relacionada ou a própria) cujo tipo, número e ano são os do título."""
    for linha in linhas:
        for chave in ("proposicaoRelacionada_", "proposicao_"):
            p = linha.get(chave)
            if (
                isinstance(p, Mapping)
                and str(p.get("siglaTipo") or "").strip().upper() == sigla
                and fontes.inteiro(p.get("numero")) == numero
                and fontes.inteiro(p.get("ano")) == ano
                and fontes.inteiro(p.get("id")) is not None
            ):
                return p
    return None


def _item_do_senado(
    *,
    titulo: Any,
    codigo: Any,
    ementa: Any,
    apoio: Apoio,
    candidatos: Sequence[str],
    detalhes: Mapping[str, str | None],
    apelido: Any = None,
    relator: Relator | None = None,
    ordem: Any = None,
    resultado: Resultado | None = None,
) -> tuple[ItemAgenda | None, str | None]:
    """Um item de pauta do Senado ou do Congresso. Devolve ``(item, None)`` ou ``(None, motivo)``
    quando a linha só entra em contagem."""
    partes = _identificador(titulo)
    if partes is None:
        return None, "outros"
    identificador, sigla, _numero, _ano = partes
    if sigla in REQUERIMENTOS:
        return None, "requerimento"
    materia = fontes.inteiro(codigo)
    if materia is None:
        return None, "outros"
    limpa = _ementa(ementa)
    if sigla in ("PDL", "PDS") and _RADIODIFUSAO.search(text.normalize(limpa or "")):
        return None, "outorga"
    casada = apoio.ficha("sf", identificador, materia)
    if apoio.simbolica(limpa, casada):
        return None, "simbolica"
    estagio, detalhe, outros = _estagios(candidatos, detalhes)
    oficial = contrato.limpar_texto(re.sub(r"^\((.*)\)$", r"\1", str(apelido or "").strip())) or casada.apelido
    if oficial and len(oficial) > contrato.TEXTO_MAXIMO["apelido"]:
        oficial = None
    return (
        ItemAgenda(
            identificador=identificador,
            tipo=sigla,
            alcance=ALCANCE.get(sigla, "outro"),
            estagio=estagio,
            estagio_detalhe=detalhe,
            outros_estagios=outros,
            link=LINK_MATERIA_DO_SENADO.format(id=materia),
            apelido=oficial,
            ementa=limpa,
            area=casada.area,
            resultado=resultado,
            relator=relator,
            ordem=_ordem(ordem),
            ficha=casada.chave,
            relevancia=casada.nota,
        ),
        None,
    )


def _relator_do_plenario(materia: Mapping[str, Any], apoio: Apoio) -> Relator | None:
    """O relator de um item do Plenário do Senado: o campo ``NomeRelator`` ou, no texto livre do
    parecer, o relator de Plenário designado ou o único relator de comissão citado."""
    parecer = str(materia.get("Parecer") or "")
    nome = materia.get("NomeRelator")
    if not nome:
        designado = _RELATOR_DE_PLENARIO.search(parecer)
        de_comissao = {n.strip() for n in _RELATOR_DE_COMISSAO.findall(parecer)}
        nome = designado.group(1) if designado else (next(iter(de_comissao)) if len(de_comissao) == 1 else None)
    return apoio.relator_do_senado(nome)


def _itens_do_plenario(
    sessao: Mapping[str, Any], *, situacao: str, confirmada: bool | None, resultados: Mapping[int, Mapping[str, Any]] | None, apoio: Apoio
) -> tuple[tuple[ItemAgenda, ...], Counter[str]]:
    """Os itens de uma sessão do Plenário (Senado ou sessão conjunta), da lista ``Materias``."""
    itens: list[ItemAgenda] = []
    fora: Counter[str] = Counter()
    vistos: set[str] = set()
    materias_ = sessao.get("Materias") if isinstance(sessao.get("Materias"), Mapping) else {}
    for materia in fontes.lista(materias_.get("Materia")):
        if not isinstance(materia, Mapping):
            continue
        parecer = contrato.limpar_texto(materia.get("Parecer")) or ""
        candidatos, detalhes = ["pautado"], {}
        if confirmada:
            candidatos.append("pauta_confirmada")
        urgencia = _EM_URGENCIA.search(parecer)
        if urgencia and "pendente" not in text.normalize(parecer[max(0, urgencia.start() - 80) : urgencia.start()]):
            candidatos.append("urgencia")
            detalhes["urgencia"] = urgencia.group(1)
        resultado = None
        if resultados is not None:
            oficial = resultados.get(fontes.inteiro(materia.get("CodigoMateria")) or -1)
            resultado = _so_antes_do_fim(resultado_do_senado(oficial), situacao) if oficial else None
        item, motivo = _item_do_senado(
            titulo=materia.get("DescricaoIdentificacaoMateria") or f"{materia.get('SiglaMateria')} {materia.get('NumeroMateria')}/{materia.get('AnoMateria')}",
            codigo=materia.get("CodigoMateria"),
            ementa=materia.get("Ementa"),
            apelido=materia.get("Cabecalho"),
            relator=_relator_do_plenario(materia, apoio),
            ordem=materia.get("SequenciaOrdem"),
            resultado=resultado,
            candidatos=candidatos,
            detalhes=detalhes,
            apoio=apoio,
        )
        if item is None or item.identificador in vistos:
            fora[motivo or "outros"] += 1
            continue
        vistos.add(item.identificador)
        itens.append(item)
    return tuple(itens), fora


# ── Câmara ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EventoSelecionado:
    """Um evento da Câmara que entra na seleção: sessão deliberativa do Plenário ou reunião
    deliberativa de comissão que decide (da Câmara, ou mista: a instituição é o Congresso)."""

    id: int
    instituicao: str
    orgao: str
    sigla: str
    foro: str
    tipo: str
    data: str
    hora: str | None
    situacao: str
    modalidade: str | None = None

    @property
    def id_da_sessao(self) -> str:
        return contrato.id_de_sessao(CAMARA, self.id) if self.instituicao == CAMARA else contrato.id_de_sessao(CONGRESSO, "evento", self.id)

    @property
    def plenario(self) -> bool:
        return self.foro == "plenario"


def eventos_da_camara(
    eventos: Iterable[Any], regras: regras_da_agenda.Regras, de: str, ate: str
) -> tuple[list[EventoSelecionado], Counter[str], int | None]:
    """Da lista de eventos da Câmara: os que entram na seleção (em ordem de dia e hora), as
    contagens do que ficou fora e o id do órgão Plenário, quando a lista o traz.

    Entram a sessão deliberativa do Plenário e a reunião deliberativa das comissões que decidem
    (``config/agenda.yaml``). Evento cancelado não entra (é contado). Sessões solenes, audiências
    e as reunições das demais comissões viram contagem. A API não respeita a ordenação pedida: a
    ordem é feita aqui."""
    selecionados: dict[int, EventoSelecionado] = {}
    contagens: Counter[str] = Counter()
    plenario_id: int | None = None
    for evento in eventos:
        if not isinstance(evento, Mapping):
            continue
        ident = fontes.inteiro(evento.get("id"))
        dia, hora = _data_e_hora(evento.get("dataHoraInicio"))
        if ident is None or dia is None or not de <= dia <= ate or ident in selecionados:
            continue
        orgaos = [o for o in evento.get("orgaos") or [] if isinstance(o, Mapping)]
        siglas = [str(o.get("sigla") or "").strip().upper() for o in orgaos]
        for orgao, sigla in zip(orgaos, siglas, strict=True):
            if sigla == "PLEN" and fontes.inteiro(orgao.get("id")) is not None:
                plenario_id = int(orgao["id"])
        tipo = text.normalize(str(evento.get("descricaoTipo") or ""))
        situacao = _situacao(evento.get("situacao"))
        if situacao is None:
            contagens["eventos_cancelados"] += 1
            continue
        if tipo == "sessao deliberativa" and "PLEN" in siglas:
            orgao = orgaos[siglas.index("PLEN")]
            selecionados[ident] = EventoSelecionado(
                id=ident, instituicao=CAMARA, orgao=contrato.limpar_texto(orgao.get("nomePublicacao") or orgao.get("nome")) or "Plenário",
                sigla="PLEN", foro="plenario", tipo="deliberativa", data=dia, hora=hora, situacao=situacao,
                modalidade=_modalidade(str(evento.get("descricao") or "").splitlines()[0] if evento.get("descricao") else ""),
            )  # fmt: skip
            continue
        if tipo == "reuniao deliberativa":
            escolhido = next(
                ((o, s, inst) for o, s in zip(orgaos, siglas, strict=True) for inst in (CAMARA, CONGRESSO) if regras.comissao_decide(inst, s)), None
            )
            if escolhido is not None:
                orgao, sigla, instituicao = escolhido
                nome = contrato.limpar_texto(orgao.get("nomePublicacao") or orgao.get("nome"))
                if nome and len(sigla) <= 20:
                    selecionados[ident] = EventoSelecionado(
                        id=ident, instituicao=instituicao, orgao=nome[: contrato.TEXTO_MAXIMO["orgao"]], sigla=sigla, foro="comissao_decide",
                        tipo="reuniao_deliberativa", data=dia, hora=hora, situacao=situacao,
                    )  # fmt: skip
                    continue
            contagens["reunioes_de_outras_comissoes"] += 1
        elif tipo.startswith("sessao nao deliberativa solene") or tipo.startswith("sessao solene"):
            contagens["sessoes_solenes"] += 1
        elif tipo.startswith("sessao nao deliberativa"):
            contagens["sessoes_nao_deliberativas"] += 1
        elif tipo.startswith("audiencia publica"):
            contagens["audiencias"] += 1
        else:
            contagens["outros_eventos"] += 1
    ordenados = sorted(selecionados.values(), key=lambda e: (e.data, e.hora or "99:99", e.id))
    return ordenados, contagens, plenario_id


def sessao_da_camara(evento: EventoSelecionado, linhas: Iterable[Any] | None, apoio: Apoio) -> SessaoAgenda:
    """A sessão de um evento da Câmara com a pauta lida (``linhas``; lista vazia = a Casa ainda
    não publicou) ou não lida nesta coleta (``None``)."""
    itens: tuple[ItemAgenda, ...] = ()
    fora: Counter[str] = Counter()
    if linhas is None:
        estado = "nao_coletada"
    else:
        lidas = list(linhas)
        itens, fora = itens_da_camara(lidas, situacao=evento.situacao, apoio=apoio)
        estado = "publicada" if lidas else "nao_publicada"
    return SessaoAgenda(
        id=evento.id_da_sessao,
        instituicao=evento.instituicao,
        orgao=evento.orgao,
        orgao_sigla=evento.sigla,
        foro=evento.foro,
        tipo=evento.tipo,
        data=evento.data,
        hora=evento.hora,
        situacao=evento.situacao,
        modalidade=evento.modalidade,
        pauta=estado,
        link=LINK_EVENTO_DA_CAMARA.format(id=evento.id),
        itens=itens,
        fora=dict(fora),
    )


def fila_de(sessao: SessaoAgenda, linhas: Iterable[Any]) -> tuple[ItemAgenda, ...]:
    """A fila que sobrou de uma sessão encerrada do Plenário da Câmara: os itens da seleção que
    ficaram sem apreciação (:func:`pendentes_da_camara`), sem o resultado (a fila não tem data
    marcada nem resultado: é o que continua esperando)."""
    pendentes = pendentes_da_camara(linhas)
    return tuple(replace(item, resultado=None) for item in sessao.itens if item.identificador in pendentes)


def ultima_deliberativa(eventos: Iterable[Any]) -> tuple[int, str] | None:
    """Entre os eventos do Plenário da Câmara, a última sessão deliberativa encerrada: ``(id, dia)``."""
    encerradas = []
    for evento in eventos:
        if not isinstance(evento, Mapping):
            continue
        ident, (dia, hora) = fontes.inteiro(evento.get("id")), _data_e_hora(evento.get("dataHoraInicio"))
        if ident is None or dia is None or text.normalize(str(evento.get("descricaoTipo") or "")) != "sessao deliberativa":
            continue
        if _situacao(evento.get("situacao")) == "encerrada":
            encerradas.append((dia, hora or "", ident))
    if not encerradas:
        return None
    dia, _hora_, ident = max(encerradas)
    return ident, dia


# ── Senado e Congresso ───────────────────────────────────────────────────────


def _deliberativa(tipo: Any) -> bool:
    alvo = text.normalize(str(tipo or ""))
    return "deliberativa" in alvo and "nao deliberativa" not in alvo


def _sessoes_brutas(resposta: Any, raiz: str) -> list[Mapping[str, Any]]:
    corpo = resposta.get(raiz) if isinstance(resposta, Mapping) else None
    sessoes = corpo.get("Sessoes") if isinstance(corpo, Mapping) else None
    return [s for s in fontes.lista(sessoes.get("Sessao") if isinstance(sessoes, Mapping) else None) if isinstance(s, Mapping)]


def resultados_do_senado(respostas: Iterable[Any]) -> dict[int, dict[int, Mapping[str, Any]]]:
    """``plenario/resultado``: ``{código da sessão: {código da matéria: item}}``."""
    saida: dict[int, dict[int, Mapping[str, Any]]] = {}
    for resposta in respostas:
        for sessao in _sessoes_brutas(resposta, "ResultadoPlenario"):
            codigo = fontes.inteiro(sessao.get("codigoSessao"))
            itens = sessao.get("Itens") if isinstance(sessao.get("Itens"), Mapping) else {}
            for item in fontes.lista(itens.get("Item")):
                materia = fontes.inteiro(item.get("codigoMateria")) if isinstance(item, Mapping) else None
                if codigo is not None and materia is not None:
                    saida.setdefault(codigo, {}).setdefault(materia, item)
    return saida


def sessoes_do_plenario(
    respostas: Iterable[Any],
    *,
    raiz: str,
    casa: str,
    de: str,
    ate: str,
    apoio: Apoio,
    resultados: Mapping[int, Mapping[int, Mapping[str, Any]]] | None = None,
) -> tuple[list[SessaoAgenda], Counter[str]]:
    """As sessões deliberativas do Plenário do Senado (``casa="SF"``, de ``plenario/agenda/mes``)
    ou as sessões conjuntas do Congresso (``casa="CN"``, de ``plenario/agenda/cn``) dentro da
    janela, e a contagem das demais. ``resultados`` (do Senado) só vale para sessão encerrada;
    ``None`` quando o resultado não foi lido: a sessão encerrada fica como "não coletada", para a
    mesclagem manter o que o arquivo anterior tinha."""
    instituicao = SENADO if casa == "SF" else CONGRESSO
    sessoes: dict[str, SessaoAgenda] = {}
    contagens: Counter[str] = Counter()
    for resposta in respostas:
        for bruta in _sessoes_brutas(resposta, raiz):
            dia, codigo = str(bruta.get("Data") or "")[:10], fontes.inteiro(bruta.get("CodigoSessao"))
            if codigo is None or str(bruta.get("Casa") or "").strip().upper() != casa:
                continue
            try:
                date.fromisoformat(dia)
            except ValueError:
                continue
            if not de <= dia <= ate:
                continue
            situacao = _situacao(bruta.get("SituacaoSessao"))
            tipo = bruta.get("TipoSessao")
            conjunta = casa == "CN" and "conjunta" in text.normalize(str(tipo or ""))
            if situacao is None:
                contagens["sessoes_canceladas"] += 1
                continue
            if not (_deliberativa(tipo) or conjunta):
                contagens["sessoes_solenes" if casa == "CN" else "sessoes_nao_deliberativas"] += 1
                continue
            ident = contrato.id_de_sessao(instituicao, codigo)
            if ident in sessoes:
                continue
            confirmada = {"sim": True, "nao": False}.get(text.normalize(str(bruta.get("PautaConfirmada") or "")))
            ano, mes, dia_ = dia.split("-")
            comum = dict(
                id=ident, instituicao=instituicao, orgao="Plenário", orgao_sigla="PLEN", foro="plenario",
                tipo="conjunta" if casa == "CN" else "deliberativa", data=dia, hora=_hora(bruta.get("Hora")), situacao=situacao,
                modalidade=_modalidade(bruta.get("DescricaoTipoPresenca")), pauta_confirmada=confirmada,
                link=LINK_ORDEM_DO_DIA.format(casa=casa, dia=dia_, mes=mes, ano=ano),
            )  # fmt: skip
            if situacao == "encerrada" and casa == "SF" and resultados is None:
                sessoes[ident] = SessaoAgenda(pauta="nao_coletada", **comum)
                continue
            da_sessao = (resultados or {}).get(codigo) if casa == "SF" else None
            itens, fora = _itens_do_plenario(bruta, situacao=situacao, confirmada=confirmada, resultados=da_sessao, apoio=apoio)
            sessoes[ident] = SessaoAgenda(pauta="publicada" if itens or fora else "nao_publicada", itens=itens, fora=dict(fora), **comum)
    return list(sessoes.values()), contagens


def reunioes_do_senado(
    resposta: Any, regras: regras_da_agenda.Regras, de: str, ate: str, apoio: Apoio
) -> tuple[list[SessaoAgenda], Counter[str]]:
    """As reuniões deliberativas das comissões do Senado que decidem (``comissao/agenda``) e a
    contagem das demais. O item terminativo (a comissão decide no lugar do Plenário) leva o
    registro no detalhe do estágio; com a reunião em andamento, o item que a fonte marca como
    "em deliberação" está em votação; o resultado é o tipo oficial de cada item já chamado."""
    corpo = resposta.get("AgendaReuniao") if isinstance(resposta, Mapping) else None
    reunioes = corpo.get("reunioes") if isinstance(corpo, Mapping) else None
    sessoes: dict[str, SessaoAgenda] = {}
    contagens: Counter[str] = Counter()
    for reuniao in fontes.lista(reunioes.get("reuniao") if isinstance(reunioes, Mapping) else None):
        if not isinstance(reuniao, Mapping):
            continue
        colegiado = reuniao.get("colegiadoCriador") if isinstance(reuniao.get("colegiadoCriador"), Mapping) else {}
        sigla = str(colegiado.get("sigla") or "").strip().upper()
        codigo, cod_colegiado = fontes.inteiro(reuniao.get("codigo")), fontes.inteiro(colegiado.get("codigo"))
        dia, hora = _data_e_hora(reuniao.get("dataInicio"))
        if codigo is None or dia is None or not de <= dia <= ate:
            continue
        partes = [p for p in fontes.lista(reuniao.get("partes")) if isinstance(p, Mapping) and str(p.get("isDeliberativa")).lower() == "true"]
        situacao = _situacao(reuniao.get("situacao"))
        if situacao is None:
            contagens["reunioes_canceladas"] += 1
            continue
        if not partes:
            contagens["reunioes_nao_deliberativas"] += 1
            continue
        linhas = [i for p in partes for i in fontes.lista(p.get("itens")) if isinstance(i, Mapping)]
        nome = contrato.limpar_texto(colegiado.get("nome"))
        decide = str(colegiado.get("siglaCasa") or "").strip().upper() == "SF" and regras.comissao_decide(SENADO, sigla)
        if not decide or not nome or cod_colegiado is None or len(sigla) > 20:
            contagens["reunioes_de_outras_comissoes"] += 1
            contagens["itens_terminativos_em_outras_comissoes"] += sum(1 for i in linhas if str(i.get("isTerminativo")).lower() == "true")
            continue
        itens: list[ItemAgenda] = []
        fora: Counter[str] = Counter()
        vistos: set[str] = set()
        for linha in linhas:
            doma = linha.get("doma") if isinstance(linha.get("doma"), Mapping) else {}
            if str(linha.get("tipo") or "").upper() != "MATE" or not doma:
                fora["requerimento"] += 1
                continue
            candidatos, detalhes = ["pautado"], {}
            if str(linha.get("isTerminativo")).lower() == "true":
                detalhes["pautado"] = "decisão terminativa"
            if situacao == "em_andamento" and str(linha.get("emDeliberacao")).lower() == "true":
                candidatos.append("em_votacao")  # o campo oficial do item que está sendo deliberado agora
            relatorias = doma.get("relatorias") if isinstance(doma.get("relatorias"), Mapping) else {}
            relator = next((r for r in fontes.lista(relatorias.get("relator")) if isinstance(r, Mapping)), {})
            parlamentar = relator.get("parlamentar") if isinstance(relator.get("parlamentar"), Mapping) else {}
            item, motivo = _item_do_senado(
                titulo=linha.get("nome") or doma.get("identificacao"),
                codigo=doma.get("codigoMateria"),
                ementa=doma.get("ementa"),
                relator=apoio.relator_do_senado(parlamentar.get("nomeComCargo") or parlamentar.get("nome"), parlamentar.get("siglaPartido"), parlamentar.get("ufPartido")),
                ordem=linha.get("ordem"),
                resultado=_so_antes_do_fim(resultado_de_comissao_do_senado(linha), situacao),
                candidatos=candidatos,
                detalhes=detalhes,
                apoio=apoio,
            )
            if item is None or item.identificador in vistos:
                fora[motivo or "outros"] += 1
                continue
            vistos.add(item.identificador)
            itens.append(item)
        ident = contrato.id_de_sessao(SENADO, "reuniao", codigo)
        sessoes[ident] = SessaoAgenda(
            id=ident,
            instituicao=SENADO,
            orgao=nome[: contrato.TEXTO_MAXIMO["orgao"]],
            orgao_sigla=sigla,
            foro="comissao_decide",
            tipo="reuniao_deliberativa",
            data=dia,
            hora=hora,
            situacao=situacao,
            modalidade=_modalidade(reuniao.get("tipoPresenca")),
            pauta="publicada" if itens or fora else "nao_publicada",
            link=LINK_REUNIAO_DO_SENADO.format(colegiado=cod_colegiado, reuniao=codigo),
            itens=tuple(itens),
            fora=dict(fora),
        )
    return list(sessoes.values()), contagens


def prazos_de_mpv(prazos: Any, processos: Any, de: str, ate: str) -> tuple[list[SessaoAgenda], SessaoAgenda | None]:
    """As linhas de prazo das medidas provisórias: uma por medida cujo último dia de vigência
    (``fimPrazo`` do prazo "Deliberação da Medida Provisória") cai na janela, e a primeira depois
    dela (o próximo evento). O prazo vem pronto da fonte; aqui nada é calculado. Só entram as
    medidas que a lista de processos em tramitação nomeia, de 2002 em diante."""
    nomes: dict[int, Mapping[str, Any]] = {}
    for processo in processos if isinstance(processos, list) else []:
        if isinstance(processo, Mapping) and fontes.inteiro(processo.get("id")) is not None:
            nomes[int(processo["id"])] = processo
    linhas: dict[str, SessaoAgenda] = {}
    for prazo in prazos if isinstance(prazos, list) else []:
        if not isinstance(prazo, Mapping) or text.normalize(str(prazo.get("tipoPrazo") or "")) != PRAZO_DE_MPV:
            continue
        processo = nomes.get(fontes.inteiro(prazo.get("idProcesso")) or -1)
        fim = str(prazo.get("fimPrazo") or "")[:10]
        partes = _identificador(processo.get("identificacao")) if processo else None
        materia = fontes.inteiro(processo.get("codigoMateria")) if processo else None
        try:
            date.fromisoformat(fim)
        except ValueError:
            continue
        if partes is None or partes[1] != "MPV" or partes[3] < MPV_DESDE or materia is None or fim < de:
            continue
        identificador = partes[0]
        link = LINK_MATERIA_DO_SENADO.format(id=materia)
        apelido = contrato.limpar_texto(processo.get("apelido"))
        item = ItemAgenda(
            identificador=identificador,
            tipo="MPV",
            alcance="mpv",
            estagio="prazo_vence",
            link=link,
            apelido=apelido if apelido and len(apelido) <= contrato.TEXTO_MAXIMO["apelido"] else None,
            ementa=_ementa(processo.get("ementa")),
            ordem=1,
        )
        ident = contrato.id_de_sessao(CONGRESSO, "prazo", identificador)
        linhas.setdefault(
            ident,
            SessaoAgenda(id=ident, instituicao=CONGRESSO, orgao="Congresso Nacional", foro="prazo", tipo="prazo", data=fim, situacao="agendada", link=link, itens=(item,)),
        )
    ordenadas = sorted(linhas.values(), key=lambda s: (s.data, s.id))
    return [s for s in ordenadas if s.data <= ate], next((s for s in ordenadas if s.data > ate), None)


def contar_vetos(resposta: Any) -> dict[str, int] | None:
    """``materia/vetos/aposrcn``: quantos vetos tramitam e quantos já trancam a pauta do
    Congresso. ``None`` quando a resposta não tem o formato esperado."""
    corpo = resposta.get("VetosAposRcnTramitandoCN") if isinstance(resposta, Mapping) else None
    vetos = corpo.get("Vetos") if isinstance(corpo, Mapping) else None
    if not isinstance(vetos, Mapping):
        return None
    lista = [v for v in fontes.lista(vetos.get("Veto")) if isinstance(v, Mapping)]
    return {"vetos_tramitando": len(lista), "vetos_trancando": sum(1 for v in lista if text.normalize(str(v.get("SobrestandoPauta") or "")) == "sim")}


# ── a rodada ─────────────────────────────────────────────────────────────────


@dataclass
class Rodada:
    """O que uma rodada da coleta fez (para o registro e para o resumo do GitHub Actions)."""

    caminho: Path
    hoje: str = ""
    gravado: bool = False
    valido: bool = True
    erro: str | None = None
    pedidos: int = 0
    segundos: float = 0.0
    prazo_esgotado: bool = False
    bytes: int = 0
    fontes: tuple[FonteAgenda, ...] = ()
    sessoes: dict[str, int] = field(default_factory=dict)
    itens: dict[str, int] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)


def _aviso(exc: BaseException | str) -> str:
    limpo = contrato.limpar_texto(str(exc)) or "falha sem descrição"
    return limpo[:300]


class _Coleta:
    """O estado de uma rodada: pede na ordem de prioridade e monta a agenda nova."""

    def __init__(
        self,
        coletor: Coletor,
        orcamento: Orcamento,
        *,
        hoje: str,
        agora: str,
        regras: regras_da_agenda.Regras,
        apoio: Apoio,
        anterior: Agenda | None,
    ) -> None:
        self.coletor, self.orcamento = coletor, orcamento
        self.hoje, self.agora, self.regras, self.apoio, self.anterior = hoje, agora, regras, apoio, anterior
        self.de, self.ate = contrato.janela_de_coleta(hoje)
        self.recesso = apoio.recesso(self.de, self.ate)
        self.fontes: dict[str, FonteAgenda] = {}
        self.sessoes: dict[str, SessaoAgenda] = {}
        self.estado: dict[str, EstadoInstituicao] = {}
        self.avisos: list[str] = []

    # ── pedidos ──────────────────────────────────────────────────────────────

    def pedir(self, o_que: str, funcao: Callable[[], Any]) -> tuple[bool, Any]:
        """Um pedido de rede. Devolve ``(True, valor)`` ou ``(False, aviso)``: a falha de uma
        fonte nunca derruba a rodada."""
        if self.orcamento.esgotado:
            return False, SEM_PRAZO
        try:
            return True, funcao()
        except PrazoEsgotado:
            return False, SEM_PRAZO
        except Exception as exc:  # noqa: BLE001 — fonte fora do ar ou resposta fora do formato
            log.warning("Agenda do Congresso: %s indisponível (%s)", o_que, exc)
            return False, _aviso(exc)

    def fonte(self, chave: str, ok: bool, aviso: str | None = None) -> None:
        instituicao, nome, url = FONTES[chave]
        self.fontes[chave] = FonteAgenda(
            chave=chave, instituicao=instituicao, nome=nome, url=url, ok=ok,
            data_dados=self.hoje if ok else None, coletado_em=self.agora if ok else None, aviso=_aviso(aviso) if aviso else None,
        )  # fmt: skip
        if aviso:
            self.avisos.append(f"{nome}: {_aviso(aviso)}")

    def guardar(self, sessoes: Iterable[SessaoAgenda]) -> None:
        for sessao in sessoes:
            self.sessoes.setdefault(sessao.id, self._com_a_hora_marcada(sessao))

    def _com_a_hora_marcada(self, sessao: SessaoAgenda) -> SessaoAgenda:
        """Depois que a sessão começa, a lista da Câmara troca a hora marcada pela hora real de
        início: vale a hora que o arquivo anterior já trazia para a mesma sessão."""
        if self.anterior is None or sessao.situacao == "agendada":
            return sessao
        antiga = next((s for s in self.anterior.sessoes if s.id == sessao.id and s.data == sessao.data), None)
        return replace(sessao, hora=antiga.hora) if antiga is not None and antiga.hora else sessao

    def _estado_anterior(self, instituicao: str) -> EstadoInstituicao | None:
        return self.anterior.estado_de(instituicao) if self.anterior is not None else None

    def _dia_util_fora_do_recesso(self) -> bool:
        """Há na janela ao menos um dia útil fora do recesso (resposta vazia aí é suspeita)."""
        dia, fim = date.fromisoformat(self.de), date.fromisoformat(self.ate)
        while dia <= fim:
            texto_ = dia.isoformat()
            if dia.weekday() < 5 and not (self.recesso and self.recesso.de <= texto_ <= self.recesso.ate):
                return True
            dia += timedelta(days=1)
        return False

    # ── Câmara ───────────────────────────────────────────────────────────────

    def camara_eventos(self) -> list[EventoSelecionado]:
        """A lista de eventos da janela. Devolve as reuniões de comissão que ficam para depois."""
        veio, eventos = self.pedir("lista de eventos da Câmara", lambda: fontes.camara_api(self.coletor, "eventos", dataInicio=self.de, dataFim=self.ate))
        if veio and not eventos and self._dia_util_fora_do_recesso():
            veio, eventos = False, "a lista de eventos veio vazia"
        if not veio:
            self.fonte("camara_eventos", False, eventos)
            self.fonte("congresso_comissoes", False, eventos)
            return []
        selecionados, contagens, plenario_id = eventos_da_camara(eventos, self.regras, self.de, self.ate)
        self._nao_lidas = 0
        self._linhas: dict[str, list[Any]] = {}  # as linhas de cada pauta lida do Plenário (para a fila)
        plenarios = [e for e in selecionados if e.plenario]
        self.guardar(self._pauta(e) for e in plenarios)
        fila, fila_dia = self._fila(plenarios, plenario_id or PLENARIO_DA_CAMARA)
        publicadas = [s.data for s in self.sessoes.values() if s.instituicao == CAMARA and s.foro == "plenario" and s.pauta == "publicada"]
        self.estado[CAMARA] = EstadoInstituicao(
            instituicao=CAMARA, recesso=self.recesso, pauta_publicada_ate=max(publicadas, default=None),
            fila=fila, fila_de=fila_dia if fila else None, contagens=dict(contagens),
        )  # fmt: skip
        return [e for e in selecionados if not e.plenario]

    def _pauta(self, evento: EventoSelecionado) -> SessaoAgenda:
        veio, linhas = self.pedir(f"pauta do evento {evento.id} da Câmara", lambda: fontes.camara_api(self.coletor, f"eventos/{evento.id}/pauta", paginada=False))
        if not veio:
            self._nao_lidas += 1
        elif evento.plenario:
            self._linhas[evento.id_da_sessao] = list(linhas)
        return sessao_da_camara(evento, linhas if veio else None, self.apoio)

    def _fila(self, plenarios: Sequence[EventoSelecionado], plenario_id: int) -> tuple[tuple[ItemAgenda, ...], str | None]:
        """A fila da última sessão deliberativa encerrada do Plenário: da janela, se houver; senão
        das duas semanas anteriores (dois pedidos, ou um se o arquivo anterior já a traz)."""
        antigo = self._estado_anterior(CAMARA)
        encerradas = [e for e in plenarios if e.situacao == "encerrada"]
        if encerradas:
            ultima = max(encerradas, key=lambda e: (e.data, e.hora or "", e.id))
            sessao = self.sessoes.get(ultima.id_da_sessao)
            if sessao is not None and sessao.pauta == "publicada":
                return fila_de(sessao, self._linhas.get(sessao.id, ())), ultima.data
            return (antigo.fila, antigo.fila_de) if antigo else ((), None)  # a pauta dela não veio: vale a fila já gravada, com a data dela
        inicio = (date.fromisoformat(self.de) - timedelta(days=DIAS_DA_FILA)).isoformat()
        fim = (date.fromisoformat(self.de) - timedelta(days=1)).isoformat()
        veio, eventos = self.pedir(
            "eventos anteriores do Plenário da Câmara", lambda: fontes.camara_api(self.coletor, f"orgaos/{plenario_id}/eventos", dataInicio=inicio, dataFim=fim)
        )
        achada = ultima_deliberativa(eventos) if veio else None
        if not veio:
            return (antigo.fila, antigo.fila_de) if antigo else ((), None)  # sem a consulta, vale a fila já gravada
        if achada is None:
            return (), None
        ident, dia = achada
        if antigo and antigo.fila and antigo.fila_de == dia:
            return antigo.fila, dia
        evento = EventoSelecionado(id=ident, instituicao=CAMARA, orgao="Plenário", sigla="PLEN", foro="plenario", tipo="deliberativa", data=dia, hora=None, situacao="encerrada")
        veio, linhas = self.pedir(f"pauta do evento {ident} da Câmara", lambda: fontes.camara_api(self.coletor, f"eventos/{ident}/pauta", paginada=False))
        if not veio:
            return (antigo.fila, antigo.fila_de) if antigo else ((), None)
        return fila_de(sessao_da_camara(evento, linhas, self.apoio), linhas), dia

    def camara_comissoes(self, eventos: Sequence[EventoSelecionado]) -> None:
        self.guardar(self._pauta(e) for e in eventos)

    def fechar_camara(self) -> None:
        if "camara_eventos" in self.fontes:
            return  # a lista falhou: as duas fontes já estão marcadas
        aviso = f"{self._nao_lidas} pauta(s) não lida(s) nesta coleta" if self._nao_lidas else None
        self.fonte("camara_eventos", True, aviso)
        self.fonte("congresso_comissoes", True)

    # ── Senado ───────────────────────────────────────────────────────────────

    def senado_plenario(self) -> None:
        respostas = []
        for inicio in meses_da_janela(self.de, self.ate):
            veio, resposta = self.pedir("agenda do Plenário do Senado", lambda inicio=inicio: fontes.senado_api(self.coletor, f"plenario/agenda/mes/{_compacta(inicio)}"))
            if not veio:
                self.fonte("senado_plenario", False, resposta)
                return
            respostas.append(resposta)
        brutas = [s for r in respostas for s in _sessoes_brutas(r, "AgendaPlenario")]
        if not brutas and self._dia_util_fora_do_recesso():
            self.fonte("senado_plenario", False, "a agenda do Plenário veio vazia")
            return
        encerrada = any(
            str(s.get("Casa") or "").upper() == "SF" and _deliberativa(s.get("TipoSessao")) and _situacao(s.get("SituacaoSessao")) == "encerrada"
            and self.de <= str(s.get("Data") or "")[:10] <= self.ate
            for s in brutas
        )  # fmt: skip
        resultados: dict[int, dict[int, Mapping[str, Any]]] | None = {}
        aviso = None
        if encerrada:
            lidos = []
            for inicio in (m for m in meses_da_janela(self.de, self.ate) if m <= self.hoje):
                veio, resposta = self.pedir("resultado do Plenário do Senado", lambda inicio=inicio: fontes.senado_api(self.coletor, f"plenario/resultado/mes/{_compacta(inicio)}"))
                if not veio:
                    resultados, aviso = None, f"resultado das sessões encerradas não lido ({resposta})"
                    break
                lidos.append(resposta)
            if resultados is not None:
                resultados = resultados_do_senado(lidos)
        sessoes, contagens = sessoes_do_plenario(respostas, raiz="AgendaPlenario", casa="SF", de=self.de, ate=self.ate, apoio=self.apoio, resultados=resultados)
        self.guardar(sessoes)
        self._contagens_do_senado = Counter(contagens)
        self._publicadas_no_senado = [s.data for s in sessoes if s.pauta == "publicada"]
        self.fonte("senado_plenario", True, aviso)

    def senado_comissoes(self) -> None:
        veio, resposta = self.pedir("agenda das comissões do Senado", lambda: fontes.senado_api(self.coletor, f"comissao/agenda/{_compacta(self.de)}/{_compacta(self.ate)}"))
        if veio and not (isinstance(resposta, Mapping) and isinstance(resposta.get("AgendaReuniao"), Mapping)):
            veio, resposta = False, "resposta fora do formato esperado"
        if not veio:
            self.fonte("senado_comissoes", False, resposta)
            return
        sessoes, contagens = reunioes_do_senado(resposta, self.regras, self.de, self.ate, self.apoio)
        self.guardar(sessoes)
        self._contagens_das_comissoes = Counter(contagens)
        self.fonte("senado_comissoes", True)

    def fechar_senado(self) -> None:
        antigo = self._estado_anterior(SENADO)
        plenario_ok = self.fontes.get("senado_plenario") is not None and self.fontes["senado_plenario"].ok
        comissoes_ok = self.fontes.get("senado_comissoes") is not None and self.fontes["senado_comissoes"].ok
        if not plenario_ok and not comissoes_ok:
            return  # nada do Senado veio: o estado anterior fica inteiro
        contagens: Counter[str] = Counter()
        nomes_do_plenario = ("sessoes_nao_deliberativas", "sessoes_canceladas")
        if plenario_ok:
            contagens.update(self._contagens_do_senado)
            publicada_ate = max(self._publicadas_no_senado, default=None)
        else:
            contagens.update({n: v for n, v in (antigo.contagens if antigo else ()) if n in nomes_do_plenario})
            publicada_ate = antigo.pauta_publicada_ate if antigo else None
        if comissoes_ok:
            contagens.update(self._contagens_das_comissoes)
        else:
            contagens.update({n: v for n, v in (antigo.contagens if antigo else ()) if n not in nomes_do_plenario})
        self.estado[SENADO] = EstadoInstituicao(instituicao=SENADO, recesso=self.recesso, pauta_publicada_ate=publicada_ate, contagens=dict(contagens))

    # ── Congresso ────────────────────────────────────────────────────────────

    def congresso_sessoes(self) -> None:
        veio, resposta = self.pedir(
            "agenda das sessões conjuntas do Congresso", lambda: fontes.senado_api(self.coletor, f"plenario/agenda/cn/{_compacta(self.de)}/{_compacta(self.ate)}")
        )
        if veio and not (isinstance(resposta, Mapping) and isinstance(resposta.get("AgendaPlenarioCN"), Mapping)):
            veio, resposta = False, "resposta fora do formato esperado"
        if not veio:
            self.fonte("congresso_sessoes", False, resposta)
            return
        sessoes, contagens = sessoes_do_plenario([resposta], raiz="AgendaPlenarioCN", casa="CN", de=self.de, ate=self.ate, apoio=self.apoio)
        self.guardar(sessoes)
        self._conjunta = any(s.data >= self.hoje for s in sessoes)  # agenda vazia não é erro: nenhuma sessão conjunta convocada
        self._contagens_do_congresso = Counter(contagens)
        self.fonte("congresso_sessoes", True)

    def congresso_prazos(self) -> None:
        veio, prazos = self.pedir("prazos de medida provisória", lambda: fontes.senado_api(self.coletor, "processo/prazo", sigla="MPV", dataReferencia=self.hoje))
        if veio:
            veio2, processos = self.pedir("medidas provisórias em tramitação", lambda: fontes.senado_api(self.coletor, "processo", sigla="MPV", tramitando="S"))
            if not veio2:
                veio, prazos = False, processos
            elif not isinstance(prazos, list) or not isinstance(processos, list):
                veio, prazos = False, "resposta fora do formato esperado"
        if not veio:
            self.fonte("congresso_prazos", False, prazos)
            return
        linhas, proximo = prazos_de_mpv(prazos, processos, self.de, self.ate)
        self.guardar(linhas)
        self._proximo_prazo = proximo
        self.fonte("congresso_prazos", True)

    def congresso_vetos(self) -> None:
        veio, resposta = self.pedir("vetos em tramitação", lambda: fontes.senado_api(self.coletor, "materia/vetos/aposrcn", validade_h=VALIDADE_VETOS_H))
        self._vetos = contar_vetos(resposta) if veio else None

    def fechar_congresso(self) -> None:
        antigo = self._estado_anterior(CONGRESSO)
        sessoes_ok = self.fontes.get("congresso_sessoes") is not None and self.fontes["congresso_sessoes"].ok
        prazos_ok = self.fontes.get("congresso_prazos") is not None and self.fontes["congresso_prazos"].ok
        if not sessoes_ok and not prazos_ok:
            return  # nada do Congresso veio: o estado anterior fica inteiro
        contagens: Counter[str] = Counter()
        antigas = dict(antigo.contagens) if antigo else {}
        vetos = getattr(self, "_vetos", None)
        contagens.update(vetos if vetos is not None else {n: v for n, v in antigas.items() if n.startswith("vetos_")})
        if sessoes_ok:
            contagens.update(self._contagens_do_congresso)
            conjunta: bool | None = self._conjunta
        else:
            contagens.update({n: v for n, v in antigas.items() if not n.startswith("vetos_")})
            conjunta = antigo.sessao_conjunta_convocada if antigo else None
        if prazos_ok:
            proximo = self._proximo_prazo
        else:
            proximo = antigo.proximo if antigo and antigo.proximo is not None and antigo.proximo.data > self.ate else None
        self.estado[CONGRESSO] = EstadoInstituicao(
            instituicao=CONGRESSO, recesso=self.recesso, sessao_conjunta_convocada=conjunta, proximo=proximo, contagens=dict(contagens)
        )

    # ── a ordem de prioridade ────────────────────────────────────────────────

    def executar(self) -> Agenda:
        """Plenários, Senado, prazos, CCJC e CFT, o resto. Quando o prazo acaba, o que não foi
        pedido fica marcado como não lido e a agenda sai parcial."""
        comissoes = self.camara_eventos()  # Plenário da Câmara (e a fila)
        self.senado_plenario()
        self.congresso_sessoes()
        self.senado_comissoes()
        self.congresso_prazos()
        self.camara_comissoes([e for e in comissoes if e.instituicao == CAMARA])
        self.camara_comissoes([e for e in comissoes if e.instituicao != CAMARA])
        self.congresso_vetos()
        self.fechar_camara()
        self.fechar_senado()
        self.fechar_congresso()
        return Agenda(
            lado=LADO, gerado_em=self.agora, hoje=self.hoje, fontes=tuple(self.fontes.values()),
            sessoes=tuple(self.sessoes.values()), estado=tuple(self.estado.values()),
        )  # fmt: skip


def _prazos_passados(junta: Agenda, anterior: Agenda | None, hoje: str) -> Agenda:
    """O prazo que venceu nesta semana some da fonte no dia seguinte (ela só lista o que está em
    vigor): a linha gravada antes é mantida até a virada da semana, com o dia do dado."""
    if anterior is None:
        return junta
    de, _ate = contrato.janela_de_coleta(hoje)
    presentes = {s.id for s in junta.sessoes}
    dia_do_dado = anterior.data_dados_de(CONGRESSO) or anterior.hoje
    mantidas = [
        replace(s, como_pautado_em=s.como_pautado_em or dia_do_dado)
        for s in anterior.sessoes
        if s.tipo == "prazo" and de <= s.data < hoje and s.id not in presentes
    ]
    return replace(junta, sessoes=(*junta.sessoes, *mantidas)) if mantidas else junta


def _dentro_do_teto(agenda: Agenda, teto: int) -> tuple[Agenda, str]:
    """O texto do arquivo, dentro do teto de bytes de ``config/agenda.yaml``. Acima dele, as
    comissões perdem a ementa dos itens, da última reunião para a primeira (o Plenário, os prazos
    e a fila nunca são cortados); o identificador e o link de cada item ficam."""
    texto_ = contrato.para_json(agenda)
    if teto <= 0 or len(texto_.encode("utf-8")) <= teto:
        return agenda, texto_
    sessoes = list(agenda.sessoes)
    for indice in range(len(sessoes) - 1, -1, -1):
        sessao = sessoes[indice]
        if sessao.foro == "plenario" or sessao.tipo == "prazo" or not any(i.ementa for i in sessao.itens):
            continue
        sessoes[indice] = replace(sessao, itens=tuple(replace(i, ementa=None) for i in sessao.itens))
        agenda = replace(agenda, sessoes=tuple(sessoes))
        texto_ = contrato.para_json(agenda)
        if len(texto_.encode("utf-8")) <= teto:
            break
    return agenda, texto_


def atualizar(
    root: Path | str | None,
    data_dir: Path | str,
    cache_dir: Path | str,
    *,
    hoje: str,
    agora: datetime | None = None,
    fetch: Fetcher | None = None,
    regras: regras_da_agenda.Regras | None = None,
    prazo: float = PRAZO_TOTAL,
    relogio: Callable[[], float] | None = None,
    dormir: Callable[[float], None] | None = None,
    pausa: float | None = None,
) -> Rodada:
    """Uma rodada: coleta, mescla com o arquivo anterior, valida e grava ``data/congresso/agenda.json``
    (só quando o conteúdo muda). Nunca levanta exceção por causa de fonte: o que não veio fica
    como estava, com o aviso em ``fontes``. Saída inválida não é gravada. ``fetch`` é o cliente
    de rede (padrão: :func:`qijournal.net.fetch`); ``relogio``, ``dormir`` e ``pausa`` existem
    para os testes (os padrões são lidos na hora da chamada)."""
    hoje = hoje[:10]
    instante = (agora or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    caminho = contrato.caminho_do_lado(data_dir, LADO)
    rodada = Rodada(caminho=caminho, hoje=hoje)
    regras = regras if regras is not None else regras_da_agenda.carregar(root)
    anterior = contrato.ler(caminho, lado=LADO)
    apoio = ler_apoio(root, caminho.parent)
    dormir = dormir or time.sleep
    orcamento = Orcamento(prazo=prazo, relogio=relogio or time.monotonic)
    coletor = Coletor(
        Path(cache_dir), fetch=fetch_com_prazo(fetch or net.fetch, orcamento, dormir=dormir), pausa=PAUSA if pausa is None else pausa, dormir=dormir
    )
    coleta = _Coleta(coletor, orcamento, hoje=hoje, agora=instante, regras=regras, apoio=apoio, anterior=anterior)
    nova = coleta.executar()
    rodada.pedidos, rodada.segundos, rodada.prazo_esgotado = orcamento.pedidos, round(orcamento.gasto(), 1), orcamento.esgotado
    rodada.avisos = list(coleta.avisos)
    try:
        junta = _prazos_passados(contrato.mesclar_com_anterior(nova, anterior, hoje, acumular_passadas=False), anterior, hoje)
        junta, texto_ = _dentro_do_teto(junta, regras.tetos.arquivos.congresso_bytes)
    except contrato.ErroDeContrato as exc:
        rodada.valido, rodada.erro = False, str(exc)
        log.error("Agenda do Congresso: saída fora do contrato, nada foi gravado (fica o arquivo anterior): %s", exc)
        for erro in exc.erros[:10]:
            log.error("Agenda do Congresso: %s", erro)
        return rodada
    rodada.fontes = junta.fontes
    rodada.sessoes = dict(Counter(s.instituicao for s in junta.sessoes))
    rodada.itens = dict(Counter({inst: sum(len(s.itens) for s in junta.sessoes_de(inst)) for inst in junta.instituicoes}))
    rodada.bytes = len(texto_.encode("utf-8"))
    if not contrato.conteudo_igual(junta, anterior):
        rodada.gravado = dados._gravar(caminho, texto_)
    log.info(
        "Agenda do Congresso: %d sessões (%s); %d pedidos em %.0f s%s; arquivo %s",
        len(junta.sessoes),
        ", ".join(f"{contrato.NOME_INSTITUICAO[i]} {n}" for i, n in sorted(rodada.sessoes.items())) or "nenhuma",
        rodada.pedidos,
        rodada.segundos,
        " (prazo de rede esgotado)" if rodada.prazo_esgotado else "",
        "gravado" if rodada.gravado else "sem mudança",
    )
    for aviso in rodada.avisos:
        log.warning("Agenda do Congresso: %s", aviso)
    return rodada
