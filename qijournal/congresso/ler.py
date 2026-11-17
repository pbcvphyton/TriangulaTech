"""A leitura diária dos projetos de lei novos (``python -m qijournal congresso ler``).

As proposições que existiam foram lidas de uma vez, por fora, e estão na guarda
(:mod:`qijournal.congresso.leitura`). Todo dia chegam proposições novas e matérias novas vão
a voto: este módulo as lê com a cadeia de IAs do jornal (a mesma da edição e do Termômetro:
``llm.providers``, cada editor com a sua chave) e grava na mesma guarda. No GitHub, roda num
fluxo à parte (``.github/workflows/leitura.yml``), que dispara quando a edição diária
termina: a edição, o e-mail e o Instagram não esperam a leitura, a edição usa as IAs
primeiro, e o que for lido entra nas contas na atualização seguinte do Congresso. Uma rodada:

1. **o que ler** (:func:`escolher`): do universo de que as contas precisam
   (:func:`leitura.coletar`), primeiro as proposições sem leitura (as matérias votadas; depois
   as do perfil de quem propõe; depois as que só esperam; em cada grupo, das mais novas para
   as mais antigas), até o teto do dia; depois, as segundas leituras que mudam a conta (o
   peso do topo e as matérias votadas: :func:`leitura.para_segunda`, sem as de pouca
   confiança), até outro teto;
2. **lotes cegos com controles** (:meth:`Rodada.lote`): cada chamada leva as instruções
   inteiras (``config/leitura_projetos.txt``) e um lote no formato dos lotes exportados
   (:func:`leitura.bloco`: id opaco, tipo, ementa, ementa detalhada, palavras-chave e tema;
   nunca número, autor, partido nem origem), com algumas **perguntas de controle** misturadas
   sem aviso (``config/leitura_referencia.jsonl``: proposições reais que dois leitores
   classificaram igual; quais entram sai da data e do número do lote, sem repetir no dia e
   com no máximo ``controles_minimo`` − 1 da mesma faixa por lote, para que responder a
   mesma coisa a tudo nunca baste: :meth:`Rodada.controles_do_lote`). A saída vem num esquema
   JSON e cada item passa por :func:`leitura.validar`;
3. **a nota do lote**: nos controles, em quantos a faixa (pela fórmula) é a da referência.
   Abaixo do mínimo, o lote inteiro é descartado: nada dele vai à guarda. No lote aceito,
   guarda-se o que veio válido; o que faltou ou veio fora do formato fica para a próxima rodada;
4. **segunda leitura**: uma chamada nova, que não vê a primeira. Quando a primeira foi desta
   rodada, vai a outro editor da cadeia, se houver. Quando é de outra rodada, a guarda não
   diz que editor a fez: a segunda evita o primeiro da cadeia (o que costuma fazer as
   primeiras leituras), sem garantia de que seja outro, e o registro conta essas à parte
   (``de_outra_rodada``). Mesma faixa: a leitura está confirmada (``r`` = 2). Faixas
   diferentes: uma terceira chamada decide pela regra dos três (:func:`leitura.conciliar`:
   ``r`` = 3, ou 0 se os três divergem), de um editor que ainda não leu a proposição, se
   houver; a segunda feita por quem fez a primeira na rodada entra em ``mesmo_editor``, e a
   terceira feita por quem se sabe que já tinha lido a proposição, em
   ``terceira_de_quem_leu`` (da primeira leitura de outra rodada não se sabe o editor). Sem
   terceira possível hoje, a leitura fica como estava (``r`` = 1) e a segunda, que
   divergiu, fica guardada com quem a fez (``a_decidir.json``, na pasta da guarda:
   :func:`leitura.abrir_a_decidir`). Na rodada seguinte não se pede outra segunda: a
   leitura nova é a terceira, e vale a regra dos três. Assim só sai confirmado o que os
   leitores disseram igual, com todas as leituras feitas na conta (sem isso, a segunda que
   divergia era esquecida e pedida de novo a cada dia, até uma concordar). Na matéria
   votada, a marca do caixa só vale se os leitores da faixa disseram a mesma
   (:func:`leitura.conciliar`);
5. **limites** (``projeto_de_pais.leitura.diaria``, em ``config/congresso.yaml``): teto de
   primeiras e de segundas leituras, tamanho do lote, prazo e teto de chamadas. A guarda é
   gravada a cada lote: prazo estourado ou rodada interrompida não perdem o que já foi lido;
6. **o registro** (``data/congresso/leitura/diario.json``, uma linha por dia: :func:`linha`):
   quantas leu, os lotes e os descartados, a nota nos controles, as confirmadas, decididas e
   instáveis, o que ficou pendente e os editores que responderam. O resumo dele vai a
   ``meta.json`` (:func:`leitura.resumo_do_diario`).

Nada aqui derruba a atualização nem a edição: sem chave de IA, a rodada registra "sem IA:
nada lido"; chamada que falha, lote descartado e resposta fora do formato viram contagem e
aviso. Só configuração quebrada é erro (:class:`ErroDeConfiguracao`). Nenhuma leitura
gravada é apagada: a rodada só acrescenta primeiras leituras e muda o estado (``r``) das
que relê, e não regrava o arquivo da guarda que não leu inteiro (de outra versão das
instruções, ilegível, ou com linha que não valida): as proposições dele esperam o conserto.
O mesmo vale para ``a_decidir.json``: se não for lido inteiro, a rodada não o regrava e não
faz segundas leituras.
"""

from __future__ import annotations

import itertools
import json
import logging
import random
import re
import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

import yaml

from . import leitura, regras as regras_mod
from .fontes import Coletor
from .leitura import Item, Leitura
from .regras import ND, ProjetoDePais, Regras

log = logging.getLogger(__name__)

DESLIGAR = "QIJ_NO_LEITURA"  # variável de ambiente que desliga a leitura diária sem mexer no config
SEMENTE = 20261117  # com a data e o número do lote, decide os controles e a ordem de cada lote
RESERVA_SEGUNDOS = 30  # sem esse tempo no prazo, o lote nem sai
TOKENS_POR_LEITURA = 160  # saída por proposição (medido: cerca de 110), com folga
OUTPUT_SHARE = 0.5  # parte da saída máxima para a resposta visível (o resto: raciocínio do modelo)
TITULOS_NO_LOG = 30  # quantas proposições a ler o log nomeia
AVISOS_NO_REGISTRO = 5
AVISO_MAX = 300  # caracteres do erro da IA guardados num aviso
PRIMEIRA, SEGUNDA, TERCEIRA = "primeira", "segunda", "terceira"
# O que a chamada acrescenta às instruções: elas pedem uma linha JSON por proposição, e a saída
# estruturada das APIs exige um objeto. O resto do pedido é o texto das instruções, sem tirar nada.
ENTREGA = (
    "\n\nNESTA CHAMADA\n"
    'Responda com um único objeto JSON, {"leituras": [ … ]}: um objeto por proposição recebida, na ordem '
    "recebida, com as chaves descritas em SAÍDA (i, a, e, m, ct, is, co, in, cf, du, pq). Nenhuma proposição "
    "pode faltar e nenhuma pode vir a mais."
)


class ErroDeConfiguracao(ValueError):
    """O config da leitura diária está quebrado (é o único erro que faz o comando falhar)."""


# ── os limites (projeto_de_pais.leitura.diaria) ──────────────────────────────


@dataclass(frozen=True)
class Limites:
    """``projeto_de_pais.leitura.diaria`` em ``config/congresso.yaml``. Sem o bloco, valem
    estes padrões."""

    ligada: bool = True
    primeiras: int = 150  # teto de primeiras leituras por rodada
    segundas: int = 60  # teto de segundas leituras por rodada
    lote: int = 30  # proposições por chamada (os controles vão a mais)
    controles: int = 10  # perguntas de controle em cada lote
    controles_minimo: int = 7  # acertos abaixo disto: o lote é descartado
    prazo_segundos: int = 480  # do primeiro lote ao último
    chamadas: int = 12  # teto de chamadas à IA por rodada
    editores: tuple[str, ...] = ()  # vazio: toda a cadeia de llm.providers; ou só estes, nesta ordem
    dias: int = 60  # dias guardados no registro


def limites_de(bruto: Any) -> Limites:
    """Os limites do bloco ``diaria`` (``None``: os padrões). Chave desconhecida, valor de
    outro tipo, número negativo, lote vazio ou mínimo de acertos maior que o número de
    controles: :class:`ErroDeConfiguracao`."""
    onde = "projeto_de_pais.leitura.diaria"
    if bruto is None:
        return Limites()
    if not isinstance(bruto, dict):
        raise ErroDeConfiguracao(f"{onde}: tem de ser um bloco de chaves (veio {type(bruto).__name__})")
    padrao = Limites()
    conhecidas = {f.name for f in fields(Limites)}
    estranhas = sorted(str(k) for k in bruto if k not in conhecidas)
    if estranhas:
        raise ErroDeConfiguracao(f"{onde}: chave(s) desconhecida(s): {', '.join(estranhas)}")
    valores: dict[str, Any] = {}
    for chave_, valor in bruto.items():
        if valor is None:
            continue
        if chave_ == "ligada":
            if not isinstance(valor, bool):
                raise ErroDeConfiguracao(f"{onde}.ligada: tem de ser true ou false (veio {valor!r})")
        elif chave_ == "editores":
            if not isinstance(valor, list) or not all(isinstance(e, str) and e.strip() for e in valor):
                raise ErroDeConfiguracao(f"{onde}.editores: tem de ser uma lista de nomes de llm.providers (veio {valor!r})")
            valor = tuple(e.strip() for e in valor)
        elif isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
            raise ErroDeConfiguracao(f"{onde}.{chave_}: tem de ser um inteiro, zero ou mais (veio {valor!r})")
        valores[chave_] = valor
    limites = replace(padrao, **valores)
    if limites.lote < 1 or limites.dias < 1:
        raise ErroDeConfiguracao(f"{onde}: lote e dias têm de ser 1 ou mais")
    if limites.controles_minimo > limites.controles:
        raise ErroDeConfiguracao(f"{onde}: controles_minimo ({limites.controles_minimo}) maior que controles ({limites.controles})")
    return limites


# ── as perguntas de controle ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Controle:
    """Uma pergunta de controle: uma proposição real que dois leitores classificaram igual.
    Vai no lote como qualquer outra; o que se confere é a faixa."""

    item: Item  # o que o leitor recebe
    faixa: str  # a faixa da leitura de referência, pela fórmula (vazio: fica sem leitura)


def faixa_da_formula(regua: ProjetoDePais, lida: Leitura) -> str:
    """O id da faixa que sai da fórmula para os códigos de uma leitura, seja qual for o estado
    dela (vazio: algum "nd" em campo de peso deixa a proposição sem leitura)."""
    return leitura.avaliar(regua, replace(lida, r=leitura.DUAS)).faixa_da_ementa


def carregar_controles(caminho: Path, regua: ProjetoDePais) -> tuple[Controle, ...]:
    """As perguntas de controle de ``config/leitura_referencia.jsonl``: uma por linha, com
    ``casa``, ``id``, ``tipo``, ``ementa``, ``explicacao``, ``palavras``, ``temas`` e a
    ``leitura`` de referência (os códigos de cada campo e o ``in``). A faixa de cada uma sai
    da fórmula de hoje, não do arquivo. Linha fora do formato ou com código que o YAML não
    tem: :class:`ErroDeConfiguracao` (com o número da linha)."""
    try:
        linhas = Path(caminho).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ErroDeConfiguracao(f"{caminho}: as perguntas de controle não puderam ser lidas ({exc})") from None
    controles: list[Controle] = []
    vistos: set[tuple[str, str]] = set()
    for n, bruta in enumerate(linhas, 1):
        if not bruta.strip():
            continue
        try:
            d = json.loads(bruta)
        except ValueError:
            raise ErroDeConfiguracao(f"{caminho}:{n}: a linha não é JSON") from None
        referencia = d.get("leitura") if isinstance(d, dict) else None
        if not isinstance(referencia, dict) or not all(isinstance(d.get(k), str) and d[k].strip() for k in ("casa", "id", "tipo", "ementa")):
            raise ErroDeConfiguracao(f"{caminho}:{n}: faltam casa, id, tipo, ementa ou a leitura de referência")
        lida, motivo = leitura.validar(regua, {**referencia, "cf": referencia.get("cf", 3), "r": leitura.UMA})
        if lida is None:
            raise ErroDeConfiguracao(f"{caminho}:{n}: {motivo}")
        temas = d.get("temas") if isinstance(d.get("temas"), list) else []
        item = Item(
            casa=d["casa"].strip(), chave=d["id"].strip(), titulo=str(d.get("titulo") or ""), tipo=d["tipo"].strip(), ano=0,
            ementa=" ".join(d["ementa"].split())[: leitura.EMENTA_MAX],
            explicacao=" ".join(str(d.get("explicacao") or "").split())[: leitura.EXPLICACAO_MAX],
            palavras=" ".join(str(d.get("palavras") or "").split())[: leitura.PALAVRAS_MAX],
            temas=tuple(str(t) for t in temas[: leitura.TEMAS]),
        )  # fmt: skip
        if (item.casa, item.chave) in vistos:
            raise ErroDeConfiguracao(f"{caminho}:{n}: a proposição {item.casa} {item.chave} aparece duas vezes")
        vistos.add((item.casa, item.chave))
        controles.append(Controle(item=item, faixa=faixa_da_formula(regua, lida)))
    return tuple(controles)


# ── o que a rodada precisa do config ─────────────────────────────────────────


@dataclass(frozen=True)
class Plano:
    """O que a leitura diária lê do config, já conferido."""

    regras: Regras
    regua: ProjetoDePais
    limites: Limites
    instrucoes: str  # config/leitura_projetos.txt, inteiro
    controles: tuple[Controle, ...]


def preparar(root: Path) -> Plano:
    """Lê e confere o config da leitura diária: a régua (``config/congresso.yaml``, com o
    bloco ``projeto_de_pais`` e os limites em ``leitura.diaria``), as instruções ao leitor
    (que têm de listar, em cada campo, exatamente os códigos do YAML) e as perguntas de
    controle (no mínimo as de um lote). O que estiver quebrado: :class:`ErroDeConfiguracao`."""
    root = Path(root)
    arquivo = root / "config" / regras_mod.ARQUIVO
    try:
        bruto = yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
        regras = regras_mod.de_dict(bruto)
    except Exception as exc:  # noqa: BLE001 — arquivo ausente, YAML inválido ou régua fora do formato: tudo é config quebrado
        raise ErroDeConfiguracao(f"{arquivo}: {exc}") from None
    regua = regras.projeto_de_pais
    if regua is None:
        raise ErroDeConfiguracao(f"{arquivo}: sem o bloco projeto_de_pais, não há régua para a leitura dos projetos")
    bloco = bruto.get("projeto_de_pais") if isinstance(bruto, dict) else None
    da_leitura = bloco.get("leitura") if isinstance(bloco, dict) else None
    limites = limites_de(da_leitura.get("diaria") if isinstance(da_leitura, dict) else None)
    caminho = root / "config" / leitura.INSTRUCOES
    try:
        instrucoes = caminho.read_text(encoding="utf-8")
    except OSError as exc:
        raise ErroDeConfiguracao(f"{caminho}: as instruções ao leitor não puderam ser lidas ({exc})") from None
    citados = leitura.codigos_das_instrucoes(instrucoes, regua.chaves)
    for campo in regua.campos:
        if citados.get(campo.chave) != [*campo.ids, ND]:
            raise ErroDeConfiguracao(f"{caminho}: os códigos de \"{campo.chave}\" nas instruções não são os de {regras_mod.ARQUIVO}")
    controles: tuple[Controle, ...] = ()
    if limites.controles:
        controles = carregar_controles(root / "config" / leitura.REFERENCIA, regua)
        if len(controles) < limites.controles:
            raise ErroDeConfiguracao(f"config/{leitura.REFERENCIA}: {len(controles)} pergunta(s) de controle para {limites.controles} por lote")
    return Plano(regras=regras, regua=regua, limites=limites, instrucoes=instrucoes, controles=controles)


# ── o que ler hoje ───────────────────────────────────────────────────────────


def _numero(item: Item) -> int:
    """O número que ordena as proposições de um ano: na Câmara, o id (cresce com o tempo);
    no Senado, o número da matéria."""
    partes = re.findall(r"\d+", item.chave)
    return int(partes[0]) if partes else 0


def em_ordem(itens: Iterable[Item], classe: Callable[[Item], int]) -> list[Item]:
    """Os itens na ordem de leitura: por classe (0 primeiro) e, em cada uma, do ano mais novo
    para o mais antigo, as duas Casas intercaladas, cada uma do número mais alto para o mais
    baixo."""
    grupos: dict[tuple[int, int, str], list[Item]] = {}
    for item in itens:
        grupos.setdefault((classe(item), -item.ano, item.casa), []).append(item)
    saida: list[Item] = []
    for parte in sorted({k[:2] for k in grupos}):
        filas = [sorted(grupos[k], key=_numero, reverse=True) for k in sorted(grupos) if k[:2] == parte]
        for fileira in itertools.zip_longest(*filas):
            saida += [item for item in fileira if item is not None]
    return saida


def classe_da_primeira(item: Item) -> int:
    """A prioridade da primeira leitura: 0 = matéria votada; 1 = do perfil de quem propõe;
    2 = só espera."""
    return 0 if leitura.VOTO in item.universos else 1 if leitura.PERFIL in item.universos else 2


def faltam_ler(itens: Mapping[str, Mapping[str, Item]], guarda: leitura.Guarda, *, bloqueados: Collection[str] = ()) -> tuple[list[Item], int]:
    """As proposições do universo sem leitura na guarda, na ordem em que serão lidas, e
    quantas ficam de fora por não ter ementa (não há o que ler). ``bloqueados``: os arquivos
    da guarda que a rodada não regrava (de outra versão das instruções, ilegíveis, ou com
    linha que não valida): as proposições deles ficam de fora."""
    todas = [i for casa in sorted(itens) for i in leitura.faltam(itens[casa], guarda) if leitura.arquivo(Path(), i.casa, i.ano).name not in bloqueados]
    return em_ordem((i for i in todas if i.ementa), classe_da_primeira), sum(1 for i in todas if not i.ementa)


def pedem_segunda(regua: ProjetoDePais, itens: Mapping[str, Mapping[str, Item]], guarda: leitura.Guarda) -> list[Item]:
    """As leituras que esperam a segunda e mudam a conta (o peso do topo e as matérias
    votadas; as que só têm pouca confiança ficam de fora), na ordem: as votadas primeiro."""
    votadas: set[tuple[str, str]] = set()
    pedem: list[Item] = []
    for casa in sorted(itens):
        for item, motivos in leitura.para_segunda(regua, itens[casa], guarda):
            if not set(motivos) - {leitura.POUCA_CONFIANCA} or not item.ementa:
                continue
            pedem.append(item)
            if leitura.VOTADA in motivos:
                votadas.add((item.casa, item.chave))
    return em_ordem(pedem, lambda item: 0 if (item.casa, item.chave) in votadas else 1)


# ── o pedido à IA ────────────────────────────────────────────────────────────


def esquema(regua: ProjetoDePais, ids: Sequence[str]) -> dict[str, Any]:
    """O esquema JSON da resposta de um lote: ``{"leituras": [ … ]}``, um objeto por
    proposição, com o id recebido (``i``), o código de cada campo (os do YAML, ou "nd"), o
    alerta, a confiança, os campos em dúvida e o porquê."""
    propriedades: dict[str, Any] = {"i": {"type": "string", "enum": list(ids), "description": "O identificador recebido."}}
    for campo in regua.campos:
        propriedades[campo.chave] = {"type": "string", "enum": [*campo.ids, ND], "description": campo.rotulo}
    propriedades[regua.alerta_iniciativa.chave] = {"type": "integer", "description": "Alerta de iniciativa: 0 ou 1."}
    propriedades["cf"] = {"type": "integer", "description": "Confiança: 1, 2 ou 3."}
    propriedades["du"] = {"type": "string", "description": "As chaves dos campos em dúvida, separadas por vírgula; vazio se nenhum."}
    propriedades["pq"] = {"type": "string", "description": "O fato da ementa que decidiu os códigos, em até 120 caracteres."}
    item = {"type": "object", "additionalProperties": False, "required": list(propriedades), "properties": propriedades}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["leituras"],
        "properties": {"leituras": {"type": "array", "items": item, "description": "Uma leitura por proposição recebida."}},
    }


def respostas_do_lote(dados: Any, ids: Collection[str]) -> dict[str, dict[str, Any]]:
    """Da resposta da IA, o objeto de cada id do lote (vale o primeiro de cada id; o que não
    é do lote ou não é objeto fica de fora). Aceita ``{"leituras": [...]}`` ou a lista solta."""
    lista = dados.get("leituras") if isinstance(dados, dict) else dados
    saida: dict[str, dict[str, Any]] = {}
    for item in lista if isinstance(lista, list) else []:
        if isinstance(item, dict) and isinstance(item.get("i"), str) and item["i"] in ids:
            saida.setdefault(item["i"], item)
    return saida


# ── o registro da rodada ─────────────────────────────────────────────────────


@dataclass
class Registro:
    """O que uma rodada fez: é o que vai, somado às outras rodadas do dia, à linha do dia em
    ``diario.json`` (:func:`linha`)."""

    data: str  # o dia da rodada (AAAA-MM-DD, no fuso do site)
    quando: str = ""  # a hora da rodada (ISO, UTC)
    primeiras: int = 0  # proposições que ganharam a primeira leitura
    segundas: int = 0  # proposições que ganharam uma segunda leitura válida
    terceiras: int = 0  # proposições que ganharam a terceira (as duas primeiras divergiram)
    lotes: int = 0  # lotes respondidos pela IA (os aceitos e os descartados)
    descartados: int = 0  # lotes descartados inteiros por errar nos controles
    falhas: int = 0  # lotes sem resposta (a chamada falhou em todos os editores)
    controles_certos: int = 0
    controles_total: int = 0
    confirmadas: int = 0  # segundas leituras na mesma faixa: r = 2
    decididas: int = 0  # divergentes que a terceira leitura decidiu: r = 3
    instaveis: int = 0  # três leituras, três faixas: r = 0
    invalidas: int = 0  # em lotes aceitos, proposições sem resposta ou com resposta fora do formato
    mesmo_editor: int = 0  # segundas leituras feitas pelo editor que fez a primeira nesta rodada (não havia outro); nunca passa de ``segundas``
    terceira_de_quem_leu: int = 0  # terceiras leituras feitas por um editor que se sabe que já tinha lido a proposição (a segunda, ou a primeira feita na rodada da segunda; da primeira de outra rodada a guarda não diz o editor, e ela não entra aqui): "duas de três" pode ser ele duas vezes
    de_outra_rodada: int = 0  # segundas leituras de primeira que não é desta rodada: a guarda não diz que editor a fez (pode ter sido o mesmo)
    chamadas: int = 0  # chamadas à cadeia de IAs
    tokens_entrada: int = 0
    tokens_saida: int = 0
    pendentes_primeiras: int = 0  # o que ficou sem a primeira leitura
    pendentes_segundas: int = 0  # o que ficou à espera da segunda
    pendentes_terceiras: int = 0  # dessas, as que já tiveram a segunda, em outra faixa, e esperam a terceira (a_decidir.json)
    provedor: str = ""  # os editores que responderam, na ordem: "gemini → gemini_lite"
    modelo: str = ""  # os modelos deles: "a + b"
    motivo: str = ""  # por que a rodada não leu, ou parou antes do fim
    avisos: list[str] = field(default_factory=list)
    # Só desta execução (não vão ao arquivo):
    faltavam: int = 0  # proposições sem leitura antes da rodada
    sem_ementa: int = 0
    escolhidas: list[Item] = field(default_factory=list)  # as primeiras leituras escolhidas para hoje
    passos: list[tuple[int, str, str]] = field(default_factory=list)  # cada lote mandado: (número, etapa, editor que respondeu; vazio se a chamada falhou)
    esperavam_segunda: int = 0
    gravados: list[Path] = field(default_factory=list)  # os arquivos da guarda que mudaram


def linha(registro: Registro) -> dict[str, Any]:
    """A linha de uma rodada no registro (``diario.json``)."""
    saida: dict[str, Any] = {
        "data": registro.data,
        "quando": registro.quando,
        "rodadas": 1,
        "primeiras": registro.primeiras,
        "segundas": registro.segundas,
        "terceiras": registro.terceiras,
        "lotes": registro.lotes,
        "descartados": registro.descartados,
        "falhas": registro.falhas,
        "controles": [registro.controles_certos, registro.controles_total],
        "confirmadas": registro.confirmadas,
        "decididas": registro.decididas,
        "instaveis": registro.instaveis,
        "invalidas": registro.invalidas,
        "mesmo_editor": registro.mesmo_editor,
        "terceira_de_quem_leu": registro.terceira_de_quem_leu,
        "de_outra_rodada": registro.de_outra_rodada,
        "chamadas": registro.chamadas,
        "tokens": {"entrada": registro.tokens_entrada, "saida": registro.tokens_saida},
        "pendentes": {"primeiras": registro.pendentes_primeiras, "segundas": registro.pendentes_segundas, "terceiras": registro.pendentes_terceiras},
        "provedor": registro.provedor,
        "modelo": registro.modelo,
        "motivo": registro.motivo,
    }
    if registro.avisos:
        saida["avisos"] = list(registro.avisos[:AVISOS_NO_REGISTRO])
    return saida


def _uniao(antes: Any, agora: Any, separador: str) -> str:
    nomes: list[str] = []
    for valor in (antes, agora):
        nomes += [n for n in str(valor or "").split(separador) if n and n not in nomes]
    return separador.join(nomes)


def _inteiro(valor: Any) -> int:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else 0


def juntar(antes: Mapping[str, Any] | None, agora: Mapping[str, Any]) -> dict[str, Any]:
    """A linha do dia com mais uma rodada: as contagens somam; a hora, o que ficou pendente,
    o motivo e os avisos são os da última; os editores e os modelos, os de todas."""
    if not antes:
        return dict(agora)
    saida = dict(agora)
    for chave_ in leitura.SOMAS_DO_DIARIO:
        saida[chave_] = _inteiro(antes.get(chave_)) + _inteiro(agora.get(chave_))
    par_antes, par_agora = antes.get("controles"), agora.get("controles")
    saida["controles"] = [
        _inteiro(par_antes[k] if isinstance(par_antes, list) and len(par_antes) == 2 else 0) + _inteiro(par_agora[k] if isinstance(par_agora, list) and len(par_agora) == 2 else 0)
        for k in (0, 1)
    ]
    tokens_antes = antes.get("tokens") if isinstance(antes.get("tokens"), dict) else {}
    tokens_agora = agora.get("tokens") if isinstance(agora.get("tokens"), dict) else {}
    saida["tokens"] = {k: _inteiro(tokens_antes.get(k)) + _inteiro(tokens_agora.get(k)) for k in ("entrada", "saida")}
    saida["provedor"] = _uniao(antes.get("provedor"), agora.get("provedor"), " → ")
    saida["modelo"] = _uniao(antes.get("modelo"), agora.get("modelo"), " + ")
    return saida


def serializar_diario(dias: Sequence[Mapping[str, Any]]) -> str:
    """O arquivo do registro: um dia por linha, do mais antigo ao mais novo (o git guarda só
    a linha que mudou)."""
    linhas = ",\n".join(json.dumps(dia, ensure_ascii=False, separators=(",", ":")) for dia in dias)
    return f'{{"v":{leitura.DIARIO_VERSAO},"dias":[\n{linhas}\n]}}\n'


def gravar_diario(pasta: Path, anteriores: Sequence[Mapping[str, Any]], registro: Registro, dias: int) -> bool:
    """Grava o registro com a rodada: ``anteriores`` são os dias que o arquivo tinha antes
    dela (a linha do mesmo dia soma a rodada: :func:`juntar`); ficam os últimos ``dias``."""
    do_dia = next((d for d in anteriores if d.get("data") == registro.data), None)
    outros = [dict(d) for d in anteriores if d.get("data") != registro.data]
    todos = sorted([*outros, juntar(do_dia, linha(registro))], key=lambda d: str(d.get("data")))
    return leitura._gravar(Path(pasta) / leitura.DIARIO, serializar_diario(todos[-max(1, dias) :]))


# ── a cadeia de IAs ──────────────────────────────────────────────────────────


def montar_editores(config: Any, limites: Limites) -> tuple[list[Any], str]:
    """Os editores por IA disponíveis, como a edição os monta (``llm.providers``, cada um com
    a sua chave no ambiente), e, sem nenhum, o motivo. ``limites.editores`` restringe a
    cadeia a alguns nomes, na ordem dada."""
    from qijournal import edit  # import tardio: a edição carrega os SDKs das IAs
    from qijournal.edit.llm import LLMUnavailable

    pular = edit._llm_skip_reason(config, True)
    if pular is not None:
        return [], f"IA {pular[0]}"
    fabricas, ausentes = edit._backends(config, None)
    if limites.editores:
        por_nome = dict(fabricas)
        fabricas = [(nome, por_nome[nome]) for nome in limites.editores if nome in por_nome]
    editores: list[Any] = []
    falhas: list[str] = []
    for nome, fabrica in fabricas:
        try:
            editores.append(fabrica())
        except LLMUnavailable as exc:
            falhas.append(f"{nome}: {exc}")
            log.warning("Leitura diária: editor por IA %s indisponível (%s)", nome, exc)
        except Exception as exc:  # noqa: BLE001 — um editor que não sobe não derruba os outros
            falhas.append(f"{nome}: {exc}")
            log.exception("Leitura diária: erro inesperado ao preparar o editor por IA %s", nome)
    if editores:
        return editores, ""
    return [], "; ".join(dict.fromkeys([*ausentes, *falhas])) or "nenhum editor de llm.providers está na lista de editores da leitura"


class _Parar(Exception):
    """A rodada não manda mais lotes (prazo, teto de chamadas ou cadeia esgotada)."""


@dataclass
class Rodada:
    """Os lotes de uma rodada: manda cada um à cadeia de IAs, confere os controles e devolve
    as leituras válidas. Conta tudo no :class:`Registro`."""

    plano: Plano
    data: str
    editores: Sequence[Any]
    registro: Registro
    prazo: float  # ``relogio()`` a partir do qual nenhum lote sai
    nomes_dos_temas: Mapping[str, str] = field(default_factory=dict)
    relogio: Callable[[], float] = time.monotonic
    numero: int = 0  # lotes mandados até aqui
    reprovados: set[str] = field(default_factory=set)  # editores que tiveram lote descartado nesta rodada
    responderam: list[str] = field(default_factory=list)  # editores que responderam, na ordem
    _ordem_dos_controles: list[Controle] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self._ordem_dos_controles = list(self.plano.controles)
        random.Random(f"{SEMENTE}:{self.data}").shuffle(self._ordem_dos_controles)

    @property
    def tamanho(self) -> int:
        """Proposições por lote: o do config, sem passar do que cabe na saída do editor de
        menor limite da cadeia."""
        saida_max = min((getattr(e, "max_tokens_write", 16000) for e in self.editores), default=16000)
        cabem = int(saida_max * OUTPUT_SHARE) // TOKENS_POR_LEITURA - self.plano.limites.controles
        return max(1, min(self.plano.limites.lote, cabem))

    def controles_do_lote(self, numero: int, itens: Sequence[Item]) -> list[Controle]:
        """As perguntas de controle do lote ``numero`` do dia (a mesma data e o mesmo número dão
        as mesmas perguntas). Saem de uma fila, a lista embaralhada pela data: cada lote leva
        as seguintes da fila, sem passar de ``controles_minimo`` − 1 perguntas da mesma faixa
        (mais da metade das perguntas cai numa faixa só; com esse teto, quem responde a mesma
        coisa para tudo nunca chega ao mínimo de acertos). A que estouraria o teto da faixa
        fica na fila para os lotes seguintes, e nenhuma se repete no dia enquanto a fila
        completa os lotes; quando não completa, a lista dá outra volta. A pergunta que for uma
        das proposições do lote é pulada."""
        limites, ordem = self.plano.limites, self._ordem_dos_controles
        quantos = min(limites.controles, len(ordem))
        teto = max(1, limites.controles_minimo - 1) if limites.controles_minimo else quantos
        do_lote = {(i.casa, i.chave) for i in itens}
        fila = list(range(len(ordem)))  # posições na lista do dia
        escolhidos: list[int] = []
        for n in range(1, numero + 1):  # os lotes anteriores gastam a fila do mesmo jeito, seja qual for a ordem das chamadas
            escolhidos = []
            por_faixa: dict[str, int] = {}
            # 1: o que está na fila; 2: a lista dá outra volta; 3: sem o teto (a lista não tem como respeitá-lo).
            for passada in (1, 2, 3):
                if passada == 2:
                    fila += [k for k in range(len(ordem)) if k not in fila]
                for k in fila:
                    if len(escolhidos) == quantos:
                        break
                    controle = ordem[k]
                    if k in escolhidos or (n == numero and (controle.item.casa, controle.item.chave) in do_lote):
                        continue
                    if passada < 3 and por_faixa.get(controle.faixa, 0) >= teto:
                        continue
                    escolhidos.append(k)
                    por_faixa[controle.faixa] = por_faixa.get(controle.faixa, 0) + 1
                if len(escolhidos) == quantos:
                    break
            fila = [k for k in fila if k not in escolhidos]
        return [ordem[k] for k in escolhidos]

    def montar(self, numero: int, itens: Sequence[Item]) -> tuple[list[tuple[str, Item, Controle | None]], str]:
        """O lote ``numero``: as proposições e os controles misturados numa ordem que sai da
        data e do número, cada um com um id opaco, e o texto que o leitor recebe."""
        mistura: list[tuple[Item, Controle | None]] = [(item, None) for item in itens] + [(c.item, c) for c in self.controles_do_lote(numero, itens)]
        random.Random(f"{SEMENTE}:{self.data}:{numero}").shuffle(mistura)
        blocos = [(f"p{k:04d}", item, controle) for k, (item, controle) in enumerate(mistura, 1)]
        return blocos, "\n\n".join(leitura.bloco(pid, item, self.nomes_dos_temas) for pid, item, _controle in blocos) + "\n"

    def _fila(self, evitar: Collection[str]) -> list[Any]:
        """Os editores com limite, na ordem da cadeia; os que se quer evitar (o que fez a
        primeira leitura) vão para depois, e os que tiveram lote descartado, para o fim."""
        vivos = [e for e in self.editores if not getattr(e, "exhausted", False)]
        return sorted(vivos, key=lambda e: (getattr(e, "provider", "") in self.reprovados, getattr(e, "provider", "") in evitar))

    def _chamar(self, rotulo: str, texto: str, ids: Sequence[str], evitar: Collection[str]) -> tuple[Any, str] | None:
        """Uma chamada à cadeia: ``(resposta, editor que respondeu)`` ou ``None`` (falhou em
        todos). Cadeia esgotada: :class:`_Parar`."""
        from qijournal.edit.chain import BackendChain  # import tardio: a edição carrega os SDKs das IAs

        fila = self._fila(evitar)
        if not fila:
            raise _Parar("os editores por IA estouraram o limite")
        cadeia = BackendChain(fila)
        self.registro.chamadas += 1
        try:
            dados = cadeia.call(
                label=rotulo,
                system=self.plano.instrucoes.rstrip("\n") + ENTREGA,
                user_text=texto,
                schema=esquema(self.plano.regua, ids),
                max_tokens=cadeia.max_tokens_write,
                deadline=self.prazo,
            )
        except Exception as exc:  # noqa: BLE001 — LLMUnavailable ou erro inesperado: o lote fica para a próxima rodada
            log.warning("Leitura diária (%s): a IA falhou (%s)", rotulo, exc)
            self.registro.falhas += 1
            self.registro.avisos.append(f"{rotulo}: a IA falhou ({str(exc)[:AVISO_MAX]})")
            return None
        quem = cadeia.provider
        if quem not in self.responderam:
            self.responderam.append(quem)
        return dados, quem

    def lote(self, itens: Sequence[Item], etapa: str, evitar: Collection[str] = ()) -> tuple[dict[tuple[str, str], Leitura], str] | None:
        """Manda um lote e devolve ``({(casa, chave): leitura válida}, editor)``, ou ``None``
        se a chamada falhou ou o lote foi descartado pelos controles. Sem tempo, sem chamadas
        ou sem editor: :class:`_Parar`."""
        limites = self.plano.limites
        if self.registro.chamadas >= limites.chamadas:
            raise _Parar(f"teto de {limites.chamadas} chamadas")
        if self.relogio() > self.prazo - RESERVA_SEGUNDOS:
            raise _Parar(f"prazo de {limites.prazo_segundos} s")
        self.numero += 1
        rotulo = f"leitura {self.numero}"  # vai no pedido: não diz ao leitor se é a primeira, a segunda ou a terceira leitura
        blocos, texto = self.montar(self.numero, itens)
        chamada = self._chamar(rotulo, texto, [pid for pid, _i, _c in blocos], evitar)
        self.registro.passos.append((self.numero, etapa, chamada[1] if chamada is not None else ""))
        if chamada is None:
            return None
        dados, quem = chamada
        rotulo = f"{rotulo}, {etapa}"
        self.registro.lotes += 1
        respostas = respostas_do_lote(dados, {pid for pid, _i, _c in blocos})
        chaves = (*self.plano.regua.chaves, self.plano.regua.alerta_iniciativa.chave, "cf", "du", "pq")
        lidas: dict[str, Leitura] = {}
        for pid, _item, _controle in blocos:
            resposta = respostas.get(pid)
            if resposta is None:
                continue
            lida, motivo = leitura.validar(self.plano.regua, {k: resposta[k] for k in chaves if k in resposta})  # sem "r": é sempre uma leitura só
            if lida is None:
                log.warning("Leitura diária (%s): resposta fora do formato para %s: %s", rotulo, pid, motivo)
                continue
            lidas[pid] = lida
        controles = [(pid, controle) for pid, _item, controle in blocos if controle is not None]
        certos = sum(1 for pid, controle in controles if pid in lidas and faixa_da_formula(self.plano.regua, lidas[pid]) == controle.faixa)
        self.registro.controles_certos += certos
        self.registro.controles_total += len(controles)
        if certos < min(limites.controles_minimo, len(controles)):
            self.registro.descartados += 1
            self.reprovados.add(quem)
            self.registro.avisos.append(f"{rotulo}: lote descartado ({certos} de {len(controles)} controles certos; editor: {quem})")
            log.warning("Leitura diária (%s): %d de %d controle(s) certo(s), abaixo de %d: o lote de %d proposição(ões) foi descartado (%s)", rotulo, certos, len(controles), limites.controles_minimo, len(itens), quem)
            return None
        validas = {(item.casa, item.chave): lidas[pid] for pid, item, controle in blocos if controle is None and pid in lidas}
        self.registro.invalidas += len(itens) - len(validas)
        log.info("Leitura diária (%s): %d de %d proposição(ões) com leitura válida; %d de %d controle(s) certo(s) (%s)", rotulo, len(validas), len(itens), certos, len(controles), quem)
        return validas, quem


# ── a rodada ─────────────────────────────────────────────────────────────────


def _lotes(itens: Sequence[Item], tamanho: int) -> list[Sequence[Item]]:
    return [itens[k : k + tamanho] for k in range(0, len(itens), tamanho)]


def _titulos(itens: Sequence[Item]) -> str:
    nomes = ", ".join(f"{i.titulo} ({i.casa})" for i in itens[:TITULOS_NO_LOG])
    return nomes + (f" … e mais {len(itens) - TITULOS_NO_LOG}" if len(itens) > TITULOS_NO_LOG else "")


def _consumo(editores: Sequence[Any]) -> tuple[int, int]:
    return (
        sum(getattr(getattr(e, "usage", None), "input_tokens", 0) or 0 for e in editores),
        sum(getattr(getattr(e, "usage", None), "output_tokens", 0) or 0 for e in editores),
    )


def ler_universo(
    plano: Plano,
    pasta: Path,
    itens: Mapping[str, Mapping[str, Item]],
    hoje: str,
    *,
    agora: str = "",
    editores: Sequence[Any] | Callable[[], tuple[Sequence[Any], str]] = (),
    maximo: int | None = None,
    relogio: Callable[[], float] = time.monotonic,
    avisos: Sequence[str] = (),
) -> Registro:
    """Uma rodada da leitura diária sobre um universo já carregado (``casa`` → chave →
    :class:`Item`): escolhe, lê, grava a guarda (a cada lote) e o registro do dia.

    ``pasta``: ``data/congresso``. ``editores``: os editores por IA, na ordem da cadeia, ou
    uma função que os monta e diz o motivo quando não há nenhum. ``maximo``: teto de leituras desta rodada, primeiras e segundas somadas (``None``:
    os tetos do config; 0: só diz o que leria, sem chamar a IA e sem gravar nada). ``relogio``: o relógio do prazo, na escala de
    ``time.monotonic`` (é a que os editores usam para o mesmo prazo, que vai em cada chamada)."""
    regua, limites = plano.regua, plano.limites
    pasta_da_guarda = leitura.pasta_da_guarda(pasta)
    registro = Registro(data=hoje, quando=agora, avisos=list(avisos))
    anteriores = leitura.ler_diario(pasta_da_guarda)
    guarda = leitura.Guarda.ler(pasta_da_guarda, regua)
    bloqueados = set(guarda.ignorados)
    if bloqueados:
        registro.avisos.append(f"arquivo(s) da guarda que não foram lidos (de outra versão das instruções, ou ilegíveis); a rodada não grava por cima deles: {', '.join(sorted(bloqueados))}")
        log.warning("Leitura diária: %s", registro.avisos[-1])
    # O arquivo com linha que não valida (um código que a régua não tem mais, sem subir a versão
    # das instruções; ou uma linha editada à mão) também fica de fora: regravá-lo apagaria essas
    # linhas, e com elas leituras confirmadas. A rodada não o toca nem lê as proposições dele.
    com_invalidas = dict(sorted(guarda.com_invalidas.items()))
    if com_invalidas:
        for nome in com_invalidas:
            guarda.deixar_de_fora(nome)
        bloqueados |= set(com_invalidas)
        registro.avisos.append(
            "arquivo(s) da guarda com linha que não valida (fora do formato, ou com código que a régua não tem mais); a rodada não os regrava, para não apagar essas linhas, e as proposições deles ficam de fora: "
            + ", ".join(f"{nome} ({quantas} linha{'s' if quantas > 1 else ''})" for nome, quantas in com_invalidas.items())
        )
        log.warning("Leitura diária: %s", registro.avisos[-1])
    # As segundas leituras que divergiram da primeira em outra rodada e esperam a terceira. A que
    # já não espera (a leitura da guarda deixou de ser de um leitor só) sai da lista. O arquivo
    # que não foi lido inteiro não é regravado, e a rodada não faz segundas leituras: sem ele,
    # a segunda que diverge seria esquecida, e pedida de novo até uma concordar.
    espera, problema_da_espera = leitura.abrir_a_decidir(pasta_da_guarda, regua)
    espera = {k: v for k, v in espera.items() if (ja := guarda.de(*k)) is None or ja.r == leitura.UMA}
    if problema_da_espera:
        registro.avisos.append(f"{leitura.A_DECIDIR} (as segundas leituras que esperam a terceira) não foi lido inteiro ({problema_da_espera}); a rodada não o regrava e não faz segundas leituras até o conserto")
        log.warning("Leitura diária: %s", registro.avisos[-1])
    gravados: set[Path] = set()

    def pendentes() -> None:
        registro.pendentes_primeiras = len(faltam_ler(itens, guarda, bloqueados=bloqueados)[0])
        esperam_a_segunda = pedem_segunda(regua, itens, guarda)
        registro.pendentes_segundas = len(esperam_a_segunda)
        # Só as que estão na fila de hoje: a segunda guardada de proposição que não está no universo de hoje fica no arquivo, sem entrar na conta.
        registro.pendentes_terceiras = sum(1 for i in esperam_a_segunda if (i.casa, i.chave) in espera)

    def salvar() -> None:
        """A guarda e o registro do dia, como estão agora: um lote lido não se perde se a
        rodada for interrompida depois."""
        gravados.update(guarda.gravar(pasta_da_guarda))
        if not problema_da_espera and leitura.gravar_a_decidir(pasta_da_guarda, regua, espera):
            gravados.add(pasta_da_guarda / leitura.A_DECIDIR)
        registro.gravados = sorted(gravados)
        registro.provedor = " → ".join(rodada.responderam) if rodada is not None else ""
        if rodada is not None:
            respondeu = set(rodada.responderam)
            registro.modelo = " + ".join(dict.fromkeys(str(getattr(e, "model", "")) for e in rodada.editores if getattr(e, "provider", None) in respondeu))
            entrada, saida = _consumo(rodada.editores)
            registro.tokens_entrada, registro.tokens_saida = entrada - consumo_antes[0], saida - consumo_antes[1]
        pendentes()
        gravar_diario(pasta_da_guarda, anteriores, registro, limites.dias)

    rodada: Rodada | None = None
    consumo_antes = (0, 0)

    # 1. O que ler hoje.
    faltam, registro.sem_ementa = faltam_ler(itens, guarda, bloqueados=bloqueados)
    registro.faltavam = len(faltam)
    teto_das_primeiras = limites.primeiras if maximo is None else min(limites.primeiras, max(0, maximo))
    primeiras = faltam[:teto_das_primeiras]
    registro.escolhidas = list(primeiras)
    esperam = pedem_segunda(regua, itens, guarda)
    registro.esperavam_segunda = len(esperam)
    por_classe = [sum(1 for i in faltam if classe_da_primeira(i) == c) for c in (0, 1, 2)]
    contas = leitura.contar(i for casa in sorted(itens) for i in itens[casa].values())
    log.info(
        "Leitura diária: o universo (uma proposição pode estar em mais de um): %s; na guarda: %d leitura(s)",
        "; ".join(f"{casa}: {c['total']} (perfil {c[leitura.PERFIL]}, espera {c[leitura.ESPERA]}, voto {c[leitura.VOTO]})" for casa, c in sorted(contas.items())) or "vazio",
        len(guarda),
    )  # fmt: skip
    log.info(
        "Leitura diária (%s): %d proposição(ões) sem leitura (%d votada(s), %d do perfil de quem propõe, %d que só esperam; fora as %d sem ementa); hoje, as primeiras %d (teto: %d). Esperam a segunda leitura (peso do topo e votadas): %d (teto: %d)",
        hoje, len(faltam), *por_classe, registro.sem_ementa, len(primeiras), teto_das_primeiras, len(esperam), limites.segundas,
    )  # fmt: skip
    if primeiras:
        log.info("Leitura diária: a ler hoje: %s", _titulos(primeiras))
    if esperam:
        log.info("Leitura diária: esperam a segunda leitura: %s", _titulos(esperam))
    if maximo is not None and maximo <= 0:  # só diz o que leria: nem a guarda nem o registro do dia são gravados
        registro.motivo = "teto de 0 leituras nesta rodada (--max 0): nada lido"
        log.info("Leitura diária: %s", registro.motivo)
        pendentes()
        return registro

    # 2. A cadeia de IAs. Sem nenhuma, o registro diz isso, haja ou não o que ler.
    motivo_sem_ia = ""
    if callable(editores):
        prontos, motivo_sem_ia = editores()
    else:
        prontos = editores
    prontos = [e for e in prontos if not getattr(e, "exhausted", False)]
    if not prontos:
        registro.motivo = "sem IA: nada lido" + (f" ({motivo_sem_ia})" if motivo_sem_ia else "")
        log.warning("Leitura diária: %s", registro.motivo)
        salvar()
        return registro
    if not primeiras and not esperam:
        registro.motivo = "nada a ler: a guarda está em dia"
        log.info("Leitura diária: %s", registro.motivo)
        salvar()
        return registro
    consumo_antes = _consumo(prontos)
    nomes_dos_temas = {t.id: t.nome for t in plano.regras.temas}
    rodada = Rodada(plano=plano, data=hoje, editores=prontos, registro=registro, prazo=relogio() + max(0, limites.prazo_segundos), nomes_dos_temas=nomes_dos_temas, relogio=relogio)
    tamanho = rodada.tamanho
    quem_leu: dict[tuple[str, str], str] = {}  # o editor da primeira leitura feita nesta rodada
    # A guarda não diz que editor fez cada leitura. Da primeira leitura de outra rodada, supõe-se o
    # primeiro da cadeia (é quem faz as primeiras leituras, salvo quando falha): a segunda o evita.
    primeiro_da_cadeia = str(getattr(prontos[0], "provider", "") or "")

    try:
        # 3. As primeiras leituras.
        for bloco in _lotes(primeiras, tamanho):
            resultado = rodada.lote(bloco, PRIMEIRA)
            if resultado is None:
                continue
            validas, quem = resultado
            for item in bloco:
                lida = validas.get((item.casa, item.chave))
                if lida is None or guarda.de(item.casa, item.chave) is not None:  # nunca por cima do que já existe
                    continue
                guarda.registrar(item.casa, item.ano, item.chave, lida)
                quem_leu[(item.casa, item.chave)] = quem
                registro.primeiras += 1
            salvar()

        # 4. As segundas leituras que mudam a conta (também as das que acabaram de ser lidas).
        teto_das_segundas = limites.segundas if maximo is None else min(limites.segundas, max(0, maximo - len(primeiras)))
        segundas = pedem_segunda(regua, itens, guarda)[: 0 if problema_da_espera else teto_das_segundas]
        por_editor: dict[str, list[Item]] = {}  # o editor que a segunda leitura evita: o da primeira (o suposto, se ela não foi nesta rodada)
        # Por conjunto de editores que a terceira evita: (a proposição, as duas leituras, quem se sabe que já a leu).
        divergentes: dict[frozenset[str], list[tuple[Item, Leitura, Leitura, frozenset[str]]]] = {}
        for item in segundas:
            guardada, da_guarda = espera.get((item.casa, item.chave)), guarda.de(item.casa, item.chave)
            if guardada is not None and da_guarda is not None:
                # A segunda já foi feita, em outra rodada, e caiu em outra faixa: não se pede outra. A leitura de hoje é a terceira.
                ja_leram = frozenset(guardada.editores)
                divergentes.setdefault(ja_leram | ({primeiro_da_cadeia} - {""}), []).append((item, da_guarda, guardada.leitura, ja_leram))
                continue
            por_editor.setdefault(quem_leu.get((item.casa, item.chave)) or primeiro_da_cadeia, []).append(item)
        for da_primeira, grupo in por_editor.items():
            for bloco in _lotes(grupo, tamanho):
                resultado = rodada.lote(bloco, SEGUNDA, evitar={da_primeira} - {""})
                if resultado is None:
                    continue
                validas, quem = resultado
                for item in bloco:
                    segunda = validas.get((item.casa, item.chave))
                    primeira = guarda.de(item.casa, item.chave)
                    if segunda is None or primeira is None or primeira.r != leitura.UMA:
                        continue
                    desta_rodada = quem_leu.get((item.casa, item.chave), "")  # vazio: a primeira é de outra rodada, de editor que a guarda não diz
                    registro.segundas += 1
                    registro.mesmo_editor += quem == desta_rodada
                    registro.de_outra_rodada += not desta_rodada
                    if faixa_da_formula(regua, primeira) == faixa_da_formula(regua, segunda):
                        guarda.registrar(item.casa, guarda.ano_de(item.casa, item.chave) or item.ano, item.chave, leitura.conciliar(regua, primeira, segunda, votada=leitura.VOTO in item.universos))
                        registro.confirmadas += 1
                    else:  # a leitura fica como está (r = 1), e a segunda, guardada até a terceira decidir: nesta rodada, se couber; senão, na próxima
                        espera[(item.casa, item.chave)] = leitura.ADecidir(segunda, tuple(dict.fromkeys(e for e in (desta_rodada, quem) if e)))
                        divergentes.setdefault(frozenset({da_primeira, quem} - {""}), []).append((item, primeira, segunda, frozenset({desta_rodada, quem} - {""})))
                salvar()

        # 5. As terceiras: onde as duas divergiram, a regra dos três. Vai a quem ainda não leu a
        # proposição, se houver; com dois editores na cadeia, volta a um dos dois, e o registro conta.
        for evitar, casos in divergentes.items():
            for bloco in _lotes(casos, tamanho):
                resultado = rodada.lote([item for item, _p, _s, _j in bloco], TERCEIRA, evitar=evitar)
                if resultado is None:
                    continue
                validas, quem = resultado
                for item, primeira, segunda, ja_leram in bloco:
                    terceira = validas.get((item.casa, item.chave))
                    if terceira is None or guarda.de(item.casa, item.chave) != primeira:
                        continue
                    final = leitura.conciliar(regua, primeira, segunda, terceira, votada=leitura.VOTO in item.universos)
                    guarda.registrar(item.casa, guarda.ano_de(item.casa, item.chave) or item.ano, item.chave, final)
                    espera.pop((item.casa, item.chave), None)
                    registro.terceiras += 1
                    registro.terceira_de_quem_leu += quem in ja_leram
                    if final.r == leitura.TRES:
                        registro.decididas += 1
                    elif final.r == leitura.DUAS:  # a primeira mudou desde a segunda guardada (importada por fora) e agora as duas caem na mesma faixa
                        registro.confirmadas += 1
                    else:
                        registro.instaveis += 1
                salvar()
    except _Parar as parada:
        registro.motivo = f"parou antes do fim: {parada}"
        log.warning("Leitura diária: %s", registro.motivo)
    salvar()
    log.info(
        "Leitura diária (%s): %d primeira(s) e %d segunda(s) leitura(s) em %d lote(s) (%d descartado(s), %d sem resposta); controles: %d de %d; %d confirmada(s), %d decidida(s) pela terceira, %d instável(is); ficam %d sem leitura e %d à espera da segunda; editores: %s",
        hoje, registro.primeiras, registro.segundas, registro.lotes, registro.descartados, registro.falhas, registro.controles_certos, registro.controles_total,
        registro.confirmadas, registro.decididas, registro.instaveis, registro.pendentes_primeiras, registro.pendentes_segundas, registro.provedor or "nenhum respondeu",
    )  # fmt: skip
    return registro


def executar(
    config: Any,
    plano: Plano,
    pasta: Path,
    coletor: Coletor,
    hoje: str,
    *,
    agora: str = "",
    editores: Sequence[Any] | None = None,
    maximo: int | None = None,
    relogio: Callable[[], float] = time.monotonic,
) -> Registro:
    """A rodada do dia: carrega o universo como a atualização o carrega (as mesmas funções e
    o mesmo cache: :func:`leitura.coletar`) e lê (:func:`ler_universo`). ``editores``: os
    editores por IA já prontos (``None``: os de ``llm.providers`` que tiverem chave no
    ambiente). Uma fonte fora do ar vira aviso, e a parte dela fica para outro dia."""
    avisos: list[str] = []
    itens = leitura.coletar(plano.regras, Path(pasta), coletor, hoje, projetos_desde=config.congresso.projetos_desde, avisos=avisos)
    for aviso in avisos:
        log.warning("Leitura diária: %s", aviso)
    return ler_universo(
        plano, Path(pasta), itens, hoje, agora=agora, maximo=maximo, relogio=relogio, avisos=avisos,
        editores=editores if editores is not None else (lambda: montar_editores(config, plano.limites)),
    )  # fmt: skip
