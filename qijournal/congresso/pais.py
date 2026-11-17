"""A visão por parlamentar: os projetos de lei de cada um, pesados pela régua "Projeto de país".

Tudo aqui parte de duas coisas que já existem: o **registro oficial** (as proposições, a
autoria, a porta em que cada uma está, as votações) e a **leitura** de cada projeto
(:mod:`.leitura`: seis códigos marcados por IA na ementa, guardados em
``data/congresso/leitura/``, de onde saem o peso de 0 a 100, a faixa e as três marcas). Este
módulo só conta. Proposição sem leitura na guarda fica **sem leitura**: fora de toda conta,
dentro da cobertura.

- **O estoque** (:func:`estoque`, "Os projetos"): do que tramita na Casa e tem leitura,
  quantos em cada faixa, quantos na faixa do topo e, deles, quantos parados (sem andamento
  oficial há mais de ``limiares.dias_parado`` dias), por porta (as filas de :mod:`.projetos`)
  e por alavanca; os órgãos e os relatores com mais projetos do topo na mão; o que espera ×
  o que virou norma; e os projetos que vão ao gráfico (``projetos-<casa>.json``).
- **Quem propõe** (:func:`autores`): por parlamentar em exercício, a composição por faixa,
  a mediana do peso, os três de maior peso, as marcas, o saldo de contas e a taxa de exceção,
  o andamento, a parte sem leitura e a faixa do acaso do funil (``autores-<casa>.json`` e
  ``autoria/<casa>-<id>.json``).
- **Na hora do voto** e **Pessoa ou partido?** ficam em :mod:`.pais_voto` e
  :mod:`.pais_cruzar`; :func:`integrar` junta tudo, grava o bloco ``lp`` de
  ``casa-<casa>.json``, os arquivos novos e ``meta.json`` → ``leitura_projetos``.

Duas palavras que este módulo usa e a régua define:

- **faixa do topo**: a faixa que só conta depois de confirmada por duas leituras
  (``segunda_leitura.peso_minimo``). **Confirmado** (``estr`` nos dados): duas leituras de IA
  na mesma faixa (``r`` ≥ 2). **Pela ementa** (``topo``): o peso do topo com uma leitura ou
  com duas; o que ainda não foi confirmado conta na faixa de baixo e leva o alerta
  ``a_confirmar``. Os dados levam as duas contagens. O gráfico dos projetos mostra o topo
  pela ementa, cada um marcado; **o que compara pessoas** (o funil, as listas e "Pessoa ou
  partido?") conta só o confirmado: a régua não deixa o topo pela ementa entrar em ranking, e
  a segunda leitura tirou do topo mais da metade do que a primeira tinha posto lá.
- **de peso**: a partir de ``limiares.andamento_peso``; abaixo disso ficam as faixas baixas
  (``simb``), das quais a primeira é a faixa mínima (``sb``).

Regras de autoria (régua, seção 4, com o que o adendo decidiu): só a autoria principal (quem
assina primeiro); só PL, PLP e PEC apresentados na legislatura; proposição com ementa
**idêntica** (normalizada) à de outra anterior na mesma Casa é "reapresentada" e conta uma vez,
para quem apresentou primeiro; projeto **de bancada** (``autoria.bancada_coautores`` coautores
ou mais do mesmo partido, ou ``autoria.bancada_fracao`` da bancada com ao menos dois) fica fora
do perfil individual; a PEC conta para o primeiro signatário (as assinaturas são apoio).

O que fica de fora: o texto integral (a leitura é da ementa), o custo em reais, o mérito, a
relatoria e a negociação (só a autoria), o parecer de adequação financeira, e o **porquê** de
um projeto não andar: o registro diz a porta e quem está com ela, não o motivo. A regra da
ementa idêntica também tira do perfil projetos diferentes com ementa genérica igual ("Altera a
Lei nº X"): eles aparecem na lista de cada um, marcados, e não entram nos números.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import text
from . import VERSAO, leitura, materias, pais_estat as estat, projetos
from .base import AnoVotos
from .ficha import Cadastro
from .leitura import Avaliacao, Leitor, Leitura
from .projetos import Proposicao, Relatoria
from .regras import MarcaDaRegua, ProjetoDePais, Regras

log = logging.getLogger(__name__)

PROJETOS = "projetos-{casa}.json"  # os projetos que vão ao gráfico de "Os projetos"
AUTORES = "autores-{casa}.json"  # uma linha por parlamentar em exercício
DECIDE = "decide-{casa}.json"  # as votações de "Na hora do voto" e o voto de cada um
AUTORIA = "autoria"  # autoria/<casa>-<id>.json: todos os projetos de cada um
# As portas na ordem do processo (os ids são as filas de projetos.py).
PORTAS = (projetos.F_MESA, projetos.F_COMISSAO, projetos.F_RELATOR, projetos.F_PARECER, projetos.F_PAUTA, projetos.F_RECURSO, projetos.F_OUTRA)
APOIO = frozenset(("PEC",))  # as assinaturas de uma PEC são apoio: ela conta para o primeiro signatário e não é "de bancada"
TOPO_LISTA = 20  # "Os que mais pesam e não andam"
COM_QUEM = 10  # órgãos e relatores com mais projetos do topo na mão
LISTA = 10  # as listas de "Quem propõe"
MAIORES = 3  # os de maior peso, no cartão da pessoa
NORMAS_NA_LISTA = 4  # títulos mostrados em "quem fez virar norma o que pesa"
ALFA_FUNIL = 0.01  # a faixa do acaso do funil: 99%, dividido pelo número de pessoas comparadas (Bonferroni)
EMENTA_CARTAO, EMENTA_LISTA, EMENTA_AUTORIA, EMENTA_CURTA, PORQUE_MAX = 280, 140, 160, 100, 160
REAPRESENTADA, DE_BANCADA = "reap", "banc"  # por que um projeto fica fora do perfil individual
SEM_LER = "falta"  # o motivo de quem não tem leitura na guarda (os outros: "nd" e "instavel", de leitura.py)


# ── o que é comum às contas ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Faixas:
    """Como as faixas da régua se agrupam nas contagens, sem escrever o nome de nenhuma:
    ``topo`` (as que pedem confirmação), ``baixas`` (as que começam abaixo de
    ``limiares.andamento_peso``) e ``piso`` (a primeira). O resto é o meio."""

    topo: frozenset[str]
    baixas: frozenset[str]
    piso: str

    @classmethod
    def da(cls, regua: ProjetoDePais) -> Faixas:
        topo = frozenset(f.id for f in regua.faixas if leitura.pede_confirmacao(regua, f))
        baixas = frozenset(f.id for f in regua.faixas if f.de < regua.limiares.andamento_peso and f.id not in topo)
        return cls(topo=topo, baixas=baixas, piso=regua.faixas[0].id)


@dataclass
class Projeto:
    """Uma proposição com a leitura dela."""

    p: Proposicao
    a: Avaliacao
    lida: Leitura | None  # a leitura que vale (``None``: ninguém leu)
    k: str  # a chave da matéria na página ("cd-PLP-108-2024")

    @property
    def motivo(self) -> str:
        """Por que está sem leitura: "falta" (ninguém leu), "nd" ou "instavel"; vazio se tem."""
        return "" if self.a.lida else (self.a.motivo or SEM_LER)


@dataclass
class Contexto:
    """O que as contas de uma Casa precisam saber."""

    casa: str
    hoje: str
    inicio: str  # o começo da legislatura (a janela do perfil)
    regras: Regras
    regua: ProjetoDePais
    leitor: Leitor
    cadastros: Mapping[int, Cadastro]
    nomes: Mapping[int, str] = field(default_factory=dict)  # id → nome, também de quem já saiu
    fichas: frozenset[str] = frozenset()  # os títulos que têm ficha em materias-<casa>.json
    faixas: Faixas = field(init=False)
    tipos: frozenset[str] = field(init=False)
    _lidas: dict[str, tuple[Leitura | None, Avaliacao]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.faixas = Faixas.da(self.regua)
        self.tipos = frozenset(self.regua.leitura.tipos)

    @property
    def caixa(self) -> MarcaDaRegua | None:
        """A marca do caixa (a "direção" da tela)."""
        return self.regua.marca(self.regua.papeis.caixa) if self.regua.papeis.caixa else None

    def projeto(self, p: Proposicao) -> Projeto:
        """A proposição com a leitura (a conta é feita uma vez por proposição: a mesma aparece
        na lista do que tramita e na de quem a propôs)."""
        chave = leitura.chave(p)
        par = self._lidas.get(chave)
        if par is None:
            lida = self.leitor.de(p)
            par = self._lidas[chave] = (lida, leitura.avaliar(self.regua, lida, tipo=p.sigla, origem=p.origem))
        return Projeto(p=p, a=par[1], lida=par[0], k=materias.chave(p.casa, p.titulo))

    def chave_da_pessoa(self, pid: int | None) -> str:
        """``cd-204536`` para quem tem dossiê na página (está nos cadastros); vazio para os demais."""
        return f"{self.casa}-{pid}" if pid is not None and pid in self.cadastros else ""

    def nome(self, pid: int | None) -> str:
        if pid is None:
            return ""
        cadastro = self.cadastros.get(pid)
        return cadastro.nome if cadastro else self.nomes.get(pid, "")

    def parado(self, p: Proposicao) -> bool:
        """Tramita e não tem andamento oficial há mais de ``limiares.dias_parado`` dias (a mesma
        definição da página: sem a data do andamento, vale a da apresentação)."""
        return p.estado == projetos.TRAMITANDO and (p.dias_sem_andar(self.hoje) or 0) > self.regras.limiares.dias_parado

    def no_topo(self, a: Avaliacao) -> bool:
        """Peso do topo pela ementa (confirmado ou à espera da segunda leitura)."""
        return a.lida and a.faixa_da_ementa in self.faixas.topo

    def confirmado(self, a: Avaliacao) -> bool:
        """Na faixa do topo com duas leituras: o "estruturante confirmado" da régua."""
        return a.lida and a.faixa in self.faixas.topo

    def direcao(self, a: Avaliacao) -> str:
        """O id do valor da marca do caixa (a "direção" da tela); vazio sem leitura."""
        return a.sinais.get(self.regua.papeis.caixa, "") if a.lida else ""


def contar_faixas(ctx: Contexto, avaliacoes: Iterable[Avaliacao]) -> dict[str, int]:
    """Quantos projetos lidos em cada grupo de faixa: ``n`` (lidos), ``estr`` (do topo,
    confirmados), ``topo`` (do topo pela ementa: confirmados ou não), ``set`` (o meio; o do
    topo ainda não confirmado conta aqui), ``simb`` (as faixas baixas) e ``sb`` (só a mínima).
    ``estr + set + simb = n``."""
    c = {"n": 0, "estr": 0, "topo": 0, "set": 0, "simb": 0, "sb": 0}
    for a in avaliacoes:
        if not a.lida:
            continue
        c["n"] += 1
        c["topo"] += a.faixa_da_ementa in ctx.faixas.topo
        if a.faixa in ctx.faixas.topo:
            c["estr"] += 1
        elif a.faixa in ctx.faixas.baixas:
            c["simb"] += 1
            c["sb"] += a.faixa == ctx.faixas.piso
        else:
            c["set"] += 1
    return c


def direcoes(ctx: Contexto, avaliacoes: Iterable[Avaliacao]) -> list[int]:
    """Quantos em cada valor da marca do caixa, na ordem do YAML (``marcas.<caixa>.valores``)."""
    marca = ctx.caixa
    if marca is None:
        return []
    conta = Counter(ctx.direcao(a) for a in avaliacoes if a.lida)
    return [conta.get(v.id, 0) for v in marca.valores]


def _curta(texto: str, limite: int) -> str:
    return text.truncate(" ".join((texto or "").split()), limite)


def _ementa(p: Proposicao) -> str:
    return p.inteira or p.ementa


def _pct(parte: float, todo: float, casas: int = 1) -> float | None:
    return round(100.0 * parte / todo, casas) if todo else None


def _desde(p: Proposicao) -> str:
    """A data que ordena "há quanto tempo não anda": o último andamento ou, sem ele, a apresentação."""
    return p.movimento or p.apresentada or "9999"


# ── o estoque ("Os projetos") ────────────────────────────────────────────────


@dataclass
class Autor:
    """De quem é um projeto, como a tela mostra: a chave (só de quem tem dossiê), o nome, o
    partido e a UF."""

    k: str = ""
    nome: str = ""
    partido: str = ""
    uf: str = ""


def _autor(ctx: Contexto, p: Proposicao, principais: Mapping[str, int]) -> Autor:
    """O autor principal. Na Câmara é quem assina primeiro; no Senado, a lista do que tramita
    não diz quem é (só o texto da autoria): ``principais`` traz a chave da matéria → o senador
    em exercício que a assina primeiro."""
    pid = p.primeiro if p.primeiro is not None else principais.get(leitura.chave(p))
    if pid is None:
        return Autor(nome=p.autoria, partido=p.partidos[0] if len(p.partidos) == 1 else "")
    cadastro = ctx.cadastros.get(pid)
    if cadastro is None:
        return Autor(nome=ctx.nome(pid), partido=p.partidos[0] if len(p.partidos) == 1 else "")
    return Autor(k=ctx.chave_da_pessoa(pid), nome=cadastro.nome, partido=cadastro.partido, uf=cadastro.uf)


def relatores_do_senado(relatorias: Mapping[int, Iterable[Relatoria]]) -> dict[str, tuple[int, str, str]]:
    """Título da matéria → ``(senador, colegiado, designado em)`` da relatoria em aberto mais
    recente (a lista do que tramita no Senado não traz o relator nem o colegiado)."""
    saida: dict[str, tuple[int, str, str]] = {}
    for pid, lista in relatorias.items():
        for r in lista:
            titulo = materias.titulo_da_materia(r.prop)
            if titulo not in saida or (r.desde or "") > saida[titulo][2]:
                saida[titulo] = (pid, r.orgao, r.desde or "")
    return saida


@dataclass
class NaPorta:
    """Um projeto do estoque com o que o registro diz de onde ele está."""

    pr: Projeto
    autor: Autor
    porta: str
    orgao: str
    relator: int | None
    parado: bool
    confirmado: bool


# As colunas de projetos-<casa>.json: as do contrato da tela, na ordem dele, e, a mais: a faixa pela
# ementa ("fe"), a fonte da leitura ("fo"), a confiança do leitor ("cf"), as três marcas ("mk", na
# ordem do YAML) e a origem do projeto ("og").
COLUNAS_DO_PROJETO = ["k", "t", "e", "a", "an", "ap", "au", "d", "pp", "org", "rl", "rln", "ult", "tm", "w", "fx", "ex", "dir", "cod", "conta", "j", "al", "u", "f", "fe", "fo", "cf", "mk", "og"]  # fmt: skip


def _linha_do_projeto(ctx: Contexto, x: NaPorta) -> list[Any]:
    p, a = x.pr.p, x.pr.a
    return [
        x.pr.k,
        p.titulo,
        _curta(_ementa(p), EMENTA_CARTAO),
        x.autor.k,
        x.autor.nome,
        x.autor.partido,
        x.autor.uf,
        p.apresentada,
        x.porta,
        x.orgao,
        ctx.chave_da_pessoa(x.relator) or None,
        ctx.nome(x.relator),
        p.movimento,
        p.temas[0] if p.temas else "",
        a.peso,
        a.faixa,
        a.alavanca,
        ctx.direcao(a),
        [list(par) for par in a.cod],
        a.conta,
        _curta(x.pr.lida.porque if x.pr.lida else "", PORQUE_MAX),
        list(a.alertas),
        p.url,
        1 if p.titulo in ctx.fichas else 0,
        a.faixa_da_ementa,
        a.fo,
        x.pr.lida.confianca if x.pr.lida else None,
        [a.sinais.get(m.id, "") for m in ctx.regua.marcas],
        p.origem,
    ]


def _item_do_topo(ctx: Contexto, x: NaPorta) -> dict[str, Any]:
    p, a = x.pr.p, x.pr.a
    return {
        "k": x.pr.k,
        "t": p.titulo,
        "e": _curta(_ementa(p), EMENTA_LISTA),
        "w": a.peso,
        "dir": ctx.direcao(a),
        "a": x.autor.k,
        "an": x.autor.nome,
        "ap": x.autor.partido,
        "au": x.autor.uf,
        "pp": x.porta,
        "org": x.orgao,
        "rl": ctx.chave_da_pessoa(x.relator) or None,
        "rln": ctx.nome(x.relator),
        "ult": p.movimento,
        "u": p.url,
        "f": 1 if p.titulo in ctx.fichas else 0,
        "ac": 0 if x.confirmado else 1,
        "fo": a.fo,
    }


def _por_peso(x: NaPorta) -> tuple[Any, ...]:
    """A ordem das listas e do gráfico: peso; no empate, o confirmado antes; depois, o que está
    há mais tempo sem andar."""
    return (-(x.pr.a.peso or 0), not x.confirmado, _desde(x.pr.p), x.pr.p.titulo)


def estoque(
    ctx: Contexto,
    props: Iterable[Proposicao],
    *,
    comissoes: Mapping[str, tuple[int, str]] | None = None,
    relatorias: Mapping[int, Iterable[Relatoria]] | None = None,
    principais: Mapping[str, int] | None = None,
    normas: Iterable[Proposicao] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """O que espera na Casa, pesado: ``(bloco "estoque" do lp, conteúdo de projetos-<casa>.json)``.

    O universo é o que **tramita** (as apensadas seguem a principal e ficam de fora) entre os
    tipos lidos (PL, PLP, PEC), de qualquer origem. Contam só os que têm leitura (``lidos`` de
    ``tramitando``). Em cada conta vão dois números: ``estr`` (do topo, confirmados por duas
    leituras) e ``topo`` (do topo pela ementa, confirmados ou não).

    Ao gráfico (``plota`` = "topo") vão os do topo pela ementa, até ``limiares.teto_pontos``:
    primeiro os confirmados, depois por peso e, no empate, o que está há mais tempo sem andar.
    Cada um leva o alerta ``a_confirmar`` enquanto espera a segunda leitura.

    ``comissoes``: sigla → (id, nome) de quem preside (Câmara). ``relatorias``: as relatorias em
    aberto por senador (o Senado não traz o relator na lista do que tramita). ``principais``:
    chave da matéria → senador que a assina primeiro. ``normas``: de onde sai "o que virou
    norma" (padrão: ``props``).

    Não diz por que um projeto não anda: só a porta e quem está com ela.
    """
    props = list(props)
    principais = principais or {}
    do_senado = relatores_do_senado(relatorias or {}) if relatorias is not None else {}
    tramitam = [p for p in props if p.casa == ctx.casa and p.estado == projetos.TRAMITANDO and p.sigla in ctx.tipos]
    itens: list[NaPorta] = []
    for p in tramitam:
        pr = ctx.projeto(p)
        if not pr.a.lida:
            continue
        relator, orgao = p.relator, p.orgao
        if p.titulo in do_senado:
            relator = relator if relator is not None else do_senado[p.titulo][0]
            orgao = orgao or do_senado[p.titulo][1]
        itens.append(NaPorta(pr=pr, autor=_autor(ctx, p, principais), porta=p.fila or projetos.F_OUTRA, orgao=orgao, relator=relator, parado=ctx.parado(p), confirmado=ctx.confirmado(pr.a)))
    do_topo = sorted((x for x in itens if ctx.no_topo(x.pr.a)), key=_por_peso)
    teto = ctx.regua.limiares.teto_pontos
    plotados = sorted(sorted(do_topo, key=lambda x: (not x.confirmado, *_por_peso(x)))[:teto], key=_por_peso)
    conta = contar_faixas(ctx, (x.pr.a for x in itens))

    por_porta = []
    total_na_porta = Counter(p.fila or projetos.F_OUTRA for p in tramitam)
    for porta in PORTAS:
        aqui = [x for x in itens if x.porta == porta]
        topo_aqui = [x for x in aqui if ctx.no_topo(x.pr.a)]
        dias = [d for d in (x.pr.p.dias_sem_andar(ctx.hoje) for x in topo_aqui) if d is not None]
        mediana = estat.mediana(dias)
        c = contar_faixas(ctx, (x.pr.a for x in aqui))
        por_porta.append(
            {
                "id": porta,
                "n": total_na_porta.get(porta, 0),
                "lidos": c["n"],
                "estr": c["estr"],
                "topo": c["topo"],
                "setorial": c["set"],
                "mediana_dias": round(mediana) if mediana is not None else None,
                "estr_parados": sum(1 for x in topo_aqui if x.confirmado and x.parado),
                "topo_parados": sum(1 for x in topo_aqui if x.parado),
            }
        )
    por_eixo = []
    for codigo in ctx.regua.alavanca.codigos:
        aqui = [x for x in itens if x.pr.a.alavanca == codigo.id]
        c = contar_faixas(ctx, (x.pr.a for x in aqui))
        por_eixo.append(
            {
                "id": codigo.id,
                "n": c["n"],
                "estr": c["estr"],
                "topo": c["topo"],
                "setorial": c["set"],
                "estr_parados": sum(1 for x in aqui if x.confirmado and x.parado),
                "topo_parados": sum(1 for x in aqui if ctx.no_topo(x.pr.a) and x.parado),
            }
        )
    # A porta que mais segura o topo: pelos confirmados; no empate, pelo topo pela ementa.
    porta_maior = max(por_porta, key=lambda b: (b["estr"], b["topo"]))["id"] if do_topo else ""

    # O foco inicial do gráfico: o parado de maior peso que alivia o caixa (marca +1); sem
    # nenhum assim, o parado de maior peso; sem parado, o de maior peso.
    foco = next((x for x in plotados if x.parado and x.pr.a.marcas.get(ctx.regua.papeis.caixa) == 1), None)
    foco = foco or next((x for x in plotados if x.parado), None) or (plotados[0] if plotados else None)

    # Com quem está: os órgãos e os relatores com mais projetos do topo na mão (a ordem é pelos
    # confirmados; o topo pela ementa só desempata).
    por_orgao: dict[str, list[NaPorta]] = defaultdict(list)
    por_relator: dict[int, list[NaPorta]] = defaultdict(list)
    for x in do_topo:
        if x.orgao:
            por_orgao[x.orgao].append(x)
        # Na Câmara, o relator do último andamento só "está com" a matéria na porta do parecer
        # (como em projetos.camara_relatorias); no Senado, vale a relatoria em aberto.
        if x.relator is not None and (x.porta == projetos.F_PARECER or x.pr.p.titulo in do_senado):
            por_relator[x.relator].append(x)
    orgaos = []
    for sigla, lista in por_orgao.items():
        presidente = (comissoes or {}).get(sigla)
        orgaos.append(
            {
                "org": sigla,
                "estr": sum(1 for x in lista if x.confirmado),
                "topo": len(lista),
                "mais_antiga": min(_desde(x.pr.p) for x in lista),
                "presidente": presidente[1] if presidente else "",
                "presidente_k": ctx.chave_da_pessoa(presidente[0]) if presidente else "",
            }
        )
    orgaos.sort(key=lambda o: (-o["estr"], -o["topo"], o["mais_antiga"], o["org"]))
    relatores = []
    for pid, lista in por_relator.items():
        cadastro = ctx.cadastros.get(pid)
        relatores.append(
            {
                "k": ctx.chave_da_pessoa(pid),
                "nome": ctx.nome(pid),
                "pt": cadastro.partido if cadastro else "",
                "uf": cadastro.uf if cadastro else "",
                "estr": sum(1 for x in lista if x.confirmado),
                "topo": len(lista),
                "mais_antiga": min(_desde(x.pr.p) for x in lista),
            }
        )
    relatores.sort(key=lambda r: (-r["estr"], -r["topo"], r["mais_antiga"], r["nome"]))

    # As apensadas do topo: seguem a porta da principal, ficam fora das faixas e são só contadas.
    apensadas = [ctx.projeto(p).a for p in props if p.casa == ctx.casa and p.estado == projetos.APENSADA and p.sigla in ctx.tipos]
    bloco = {
        "tramitando": len(tramitam),
        "lidos": conta["n"],
        "estr": conta["estr"],
        "topo": conta["topo"],
        "setorial": conta["set"],
        "baixo": conta["simb"],
        "sb": conta["sb"],
        "estr_parados": sum(1 for x in do_topo if x.confirmado and x.parado),
        "topo_parados": sum(1 for x in do_topo if x.parado),
        "estr_apensados": sum(1 for a in apensadas if ctx.confirmado(a)),
        "topo_apensados": sum(1 for a in apensadas if ctx.no_topo(a)),
        "plota": "topo",  # o que vai ao gráfico: o topo pela ementa (os confirmados e os "a confirmar", marcados)
        "plotados": len(plotados),
        "plotaveis": len(do_topo),  # antes do teto de pontos
        "sem_data": sum(1 for x in plotados if not x.pr.p.movimento),
        "dir": dict(zip([v.id for v in ctx.caixa.valores], direcoes(ctx, (x.pr.a for x in do_topo if x.confirmado)), strict=True)) if ctx.caixa else {},
        "dir_topo": dict(zip([v.id for v in ctx.caixa.valores], direcoes(ctx, (x.pr.a for x in do_topo)), strict=True)) if ctx.caixa else {},
        "por_porta": por_porta,
        "por_eixo": por_eixo,
        "porta_maior": porta_maior,
        "foco": foco.pr.k if foco else "",
        "top": [_item_do_topo(ctx, x) for x in do_topo if x.parado][:TOPO_LISTA],
        "orgaos": orgaos[:COM_QUEM],
        "relatores": relatores[:COM_QUEM],
        "espera_x_norma": espera_x_norma(ctx, tramitam, props if normas is None else normas),
    }
    arquivo = {
        "v": VERSAO,
        "casa": ctx.casa,
        "hoje": ctx.hoje,
        "plota": "topo",
        "cols": COLUNAS_DO_PROJETO,
        "linhas": [_linha_do_projeto(ctx, x) for x in plotados],
    }
    return bloco, arquivo


def espera_x_norma(ctx: Contexto, tramitam: Iterable[Proposicao], normas: Iterable[Proposicao]) -> dict[str, Any]:
    """A composição por faixa do que **espera** e do que **virou norma**, só nos projetos de
    origem parlamentar (para comparar o mesmo tipo de projeto dos dois lados). ``norma``: os
    apresentados na legislatura que viraram norma. Em cada lado, ``total`` (todos) e ``n``
    (os lidos, sobre os quais vão as contagens: ``baixo``, ``sb``, ``setorial``, ``estr``,
    ``topo``). No Senado, as normas saem das listas de autoria dos senadores em exercício."""

    def lado(lista: Iterable[Proposicao]) -> dict[str, int]:
        vistos: dict[str, Proposicao] = {}
        for p in lista:
            if p.casa == ctx.casa and p.sigla in ctx.tipos and p.origem == "parlamentar":
                vistos.setdefault(leitura.chave(p), p)
        c = contar_faixas(ctx, (ctx.projeto(p).a for p in vistos.values()))
        return {"total": len(vistos), "n": c["n"], "baixo": c["simb"], "sb": c["sb"], "setorial": c["set"], "estr": c["estr"], "topo": c["topo"]}

    return {
        "origem": "parlamentar",
        "espera": lado(tramitam),
        "norma": lado(p for p in normas if p.estado == projetos.NORMA and p.apresentada >= ctx.inicio),
    }


# ── quem propõe ──────────────────────────────────────────────────────────────


def reapresentadas(props: Iterable[Proposicao], tipos: Iterable[str]) -> dict[str, str]:
    """Chave da proposição → título da proposição **anterior** da mesma Casa com a ementa
    idêntica (depois de normalizada: minúsculas, sem acento nem pontuação). A primeira de cada
    ementa (pela data de apresentação; no empate, pelo número) não entra: é para ela que a
    repetição conta. Proposição sem ementa nunca é repetição.

    É a regra do adendo no lugar dos "90% de semelhança" da régua. Ela não distingue o projeto
    reapresentado de dois projetos diferentes com a mesma ementa genérica ("Altera a Lei nº
    X."): os dois ficam fora do perfil de quem veio depois."""
    tipos = set(tipos)
    todas: dict[tuple[str, str], Proposicao] = {}
    for p in props:
        if p.sigla in tipos:
            todas.setdefault((p.casa, leitura.chave(p)), p)
    primeira: dict[tuple[str, str], Proposicao] = {}
    saida: dict[str, str] = {}
    for p in sorted(todas.values(), key=lambda p: (p.apresentada or "9999", p.ano, p.numero, p.id)):
        ementa = text.normalize(_ementa(p))
        if not ementa:
            continue
        original = primeira.setdefault((p.casa, ementa), p)
        if original is not p:
            saida[leitura.chave(p)] = original.titulo
    return saida


def de_bancada(regua: ProjetoDePais, sigla: str, partidos: Iterable[str], bancadas: Mapping[str, int]) -> bool:
    """Projeto de bancada: ``autoria.bancada_coautores`` (10) coautores ou mais do mesmo
    partido, ou ``autoria.bancada_fracao`` (metade) da bancada desse partido em exercício,
    desde que sejam ao menos dois (um deputado sozinho não é "a bancada", ainda que o partido
    só tenha ele). A PEC nunca é de bancada: as assinaturas dela são apoio.
    ``partidos``: o partido de cada coautor, com repetição."""
    if sigla in APOIO:
        return False
    regra = regua.autoria
    for partido, quantos in Counter(p for p in partidos if p).items():
        if quantos >= regra.bancada_coautores:
            return True
        tamanho = bancadas.get(partido, 0)
        if quantos >= 2 and tamanho and quantos >= math.ceil(regra.bancada_fracao * tamanho):
            return True
    return False


@dataclass
class Perfil:
    """Os projetos de uma pessoa: os de autoria principal na legislatura (``todos``), os que
    ficam fora do perfil individual (``fora``: chave → (motivo, título da anterior)) e, do que
    sobra, os lidos."""

    pid: int
    cadastro: Cadastro
    todos: list[Projeto] = field(default_factory=list)
    fora: dict[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def contam(self) -> list[Projeto]:
        return [pr for pr in self.todos if leitura.chave(pr.p) not in self.fora]

    @property
    def lidos(self) -> list[Projeto]:
        return [pr for pr in self.contam if pr.a.lida]


def perfis(
    ctx: Contexto,
    autoria: Mapping[int, Iterable[Proposicao]],
    *,
    repetidas: Mapping[str, str],
    coautores: Mapping[str, Iterable[int]] | None = None,
) -> dict[int, Perfil]:
    """O perfil de cada parlamentar **em exercício**: os PL, PLP e PEC em que assina primeiro,
    apresentados na legislatura. ``repetidas``: de :func:`reapresentadas`. ``coautores``: chave
    da matéria → quem assina (no Senado, onde a proposição não traz a lista; na Câmara vale
    ``Proposicao.autores``)."""
    bancadas = Counter(c.partido for c in ctx.cadastros.values() if c.exercicio and c.partido)
    saida: dict[int, Perfil] = {}
    for pid, cadastro in ctx.cadastros.items():
        if not cadastro.exercicio:
            continue
        perfil = saida[pid] = Perfil(pid=pid, cadastro=cadastro)
        vistas: set[str] = set()
        for p in autoria.get(pid) or ():
            chave = leitura.chave(p)
            if p.primeiro != pid or p.sigla not in ctx.tipos or p.apresentada < ctx.inicio or chave in vistas:
                continue
            vistas.add(chave)
            perfil.todos.append(ctx.projeto(p))
            assinam = coautores.get(chave, ()) if coautores is not None else p.autores
            partidos = [ctx.cadastros[a].partido for a in assinam if a in ctx.cadastros]
            if chave in repetidas:
                perfil.fora[chave] = (REAPRESENTADA, repetidas[chave])
            elif de_bancada(ctx.regua, p.sigla, partidos, bancadas):
                perfil.fora[chave] = (DE_BANCADA, "")
    return saida


def saldo_de_contas(mais: int, menos: int, minimo: int) -> int | None:
    """(projetos de marca +1 − projetos de marca −1) ÷ projetos com marca ±1, de −100 a +100.
    ``None`` com menos de ``minimo`` projetos de marca ±1 (os de marca 0 e os que a ementa não
    permite dizer ficam fora por construção: penal, declaratório, simbólico)."""
    total = mais + menos
    return round(100.0 * (mais - menos) / total) if total >= max(1, minimo) else None


def _maior_peso(ctx: Contexto, pr: Projeto) -> tuple[Any, ...]:
    return (-(pr.a.peso or 0), not ctx.confirmado(pr.a), pr.p.apresentada or "", pr.p.titulo)


def linha_do_autor(ctx: Contexto, perfil: Perfil) -> dict[str, Any]:
    """Os números de uma pessoa (uma linha de ``autores-<casa>.json``, sem o voto e sem o que
    vem de ``resumo.json``). Mínimos da régua: mediana e taxa de exceção com
    ``limiares.perfil_minimo`` lidos ou mais; saldo de contas com ``limiares.saldo_minimo``
    projetos de marca ±1 ou mais. Abaixo disso o campo sai nulo e ficam as contagens."""
    regua, lim = ctx.regua, ctx.regua.limiares
    contam, lidos = perfil.contam, perfil.lidos
    c = contar_faixas(ctx, (pr.a for pr in lidos))
    n = c["n"]
    do_topo = [pr for pr in lidos if ctx.no_topo(pr.a)]
    por_alavanca = Counter(pr.a.alavanca for pr in do_topo)
    marcas = []
    for marca in regua.marcas:
        conta = Counter(pr.a.marcas.get(marca.id) for pr in lidos)
        marcas.append([conta.get(1, 0), conta.get(0, 0), conta.get(-1, 0), conta.get(None, 0)])
    caixa = Counter(pr.a.marcas.get(regua.papeis.caixa) for pr in lidos) if regua.papeis.caixa else Counter()
    com_excecao = [pr for pr in lidos if regua.papeis.excecao and pr.a.marcas.get(regua.papeis.excecao) == -1]
    campo_da_excecao = next((campo for campo in regua.campos if campo.marca and campo.marca == regua.papeis.excecao), None)
    para_quem = Counter(dict(pr.a.cod).get(campo_da_excecao.id) for pr in com_excecao) if campo_da_excecao else Counter()
    de_peso = [pr for pr in lidos if (pr.a.peso or 0) >= lim.andamento_peso]
    estados = Counter(pr.p.estado for pr in lidos)
    mais, menos = caixa.get(1, 0), caixa.get(-1, 0)
    return {
        "k": f"{ctx.casa}-{perfil.pid}",
        "nome": perfil.cadastro.nome,
        "pt": perfil.cadastro.partido,
        "uf": perfil.cadastro.uf,
        "n": n,
        "estr": c["estr"],
        "topo": c["topo"],
        "set": c["set"],
        "simb": c["simb"],
        "sb": c["sb"],
        "de": direcoes(ctx, (pr.a for pr in do_topo)),
        "ex": [por_alavanca.get(codigo.id, 0) for codigo in regua.alavanca.codigos],
        "tip": estat.mediana([pr.a.peso or 0 for pr in lidos]) if n >= lim.perfil_minimo else None,
        "andou": estados.get(projetos.APROVADA, 0) + estados.get(projetos.NORMA, 0),
        "norma": estados.get(projetos.NORMA, 0),
        "parados": sum(1 for pr in lidos if ctx.parado(pr.p)),
        "top": [
            [pr.k, pr.p.titulo, pr.a.peso, ctx.direcao(pr.a), _curta(_ementa(pr.p), EMENTA_CURTA), 1 if pr.p.titulo in ctx.fichas else 0, 0 if ctx.confirmado(pr.a) or not ctx.no_topo(pr.a) else 1]
            for pr in sorted(lidos, key=lambda pr: _maior_peso(ctx, pr))[:MAIORES]
        ],
        "sc": saldo_de_contas(mais, menos, lim.saldo_minimo),
        "sc_n": mais + menos,
        "tx": _pct(len(com_excecao), n) if n >= lim.perfil_minimo else None,
        "sl": _pct(len(contam) - n, len(contam)),
        "tot": len(contam),
        "mk": marcas,
        "tq": [para_quem.get(codigo.id, 0) for codigo in campo_da_excecao.codigos if codigo.valor == -1] if campo_da_excecao else [],
        "ad": [
            len(de_peso),
            sum(1 for pr in de_peso if pr.p.estado == projetos.NORMA),
            sum(1 for pr in de_peso if pr.p.estado == projetos.APROVADA),
            sum(1 for pr in de_peso if ctx.parado(pr.p)),
            sum(1 for pr in de_peso if pr.p.estado == projetos.APENSADA),
        ],
        "bc": sum(1 for motivo, _t in perfil.fora.values() if motivo == DE_BANCADA),
        "rp": sum(1 for motivo, _t in perfil.fora.values() if motivo == REAPRESENTADA),
    }


# (medida do funil, a coluna com a contagem dela). A parte do topo conta só o confirmado ("estr"): o
# topo pela ementa, à espera da segunda leitura, não entra em comparação entre pessoas.
FUNIL = (("topo", "estr"), ("sb", "sb"))


def funil(linhas: list[dict[str, Any]], minimo: int, alfa: float = ALFA_FUNIL) -> dict[str, Any]:
    """A faixa do acaso de "Quem propõe", para cada medida (a parte do topo **confirmado** e a
    parte da faixa mínima nos projetos lidos de cada um; as medidas e as colunas de onde saem
    estão em ``FUNIL``).

    Entram os que têm ``minimo`` projetos lidos ou mais. A taxa da Casa é a soma dos projetos
    do tipo sobre a soma dos lidos dessas pessoas. Para cada pessoa, testa-se se a parte dela
    difere da taxa da Casa mais do que o acaso explica: binomial exata, dois lados, com o alfa
    (1%) **dividido pelo número de pessoas comparadas** (Bonferroni). Grava em cada linha
    ``fz_<medida>``: −1 abaixo, 0 dentro, +1 acima; ``None`` para quem não entra.

    Devolve ``taxa`` (%), ``ymax`` (o primeiro de 20, 40… 100 que cobre a maior parte),
    ``xmax`` (o maior número de lidos), ``comparados`` e ``faixa`` (degraus ``[n, de %, até %]``:
    com ``n`` projetos, a parte que cabe no acaso), pelo mesmo teste: fora da faixa no desenho
    e "passa do acaso" na lista são a mesma coisa. Há **um degrau para cada número de lidos
    que alguém comparado tem**, em ordem: a faixa em % sobe e desce de um ``n`` para o outro
    (os projetos são inteiros), e um degrau emprestado do vizinho deixaria de fora quem está
    dentro. Cada pessoa comparada acha o degrau do seu ``n``, e está entre ``de`` e ``até``
    (a menos do arredondamento, de 0,1 ponto) se, e só se, ``fz`` é 0.

    O teste supõe que cada projeto de uma pessoa cai no tipo com a chance da Casa, um
    independente do outro. Projetos da mesma pessoa se parecem: a faixa real é um pouco mais
    larga do que a desenhada, e quem está na borda não deve ser lido como fora."""
    dentro = [linha for linha in linhas if linha["n"] >= minimo]
    saida: dict[str, Any] = {"taxa": {}, "ymax": {}, "xmax": max((linha["n"] for linha in dentro), default=0), "comparados": len(dentro), "faixa": {}}
    for medida, coluna in FUNIL:
        for linha in linhas:
            linha[f"fz_{medida}"] = None
        lidos = sum(linha["n"] for linha in dentro)
        if not dentro or not lidos:
            saida["taxa"][medida], saida["ymax"][medida], saida["faixa"][medida] = None, 20, []
            continue
        taxa = sum(linha[coluna] for linha in dentro) / lidos
        corrigido = alfa / len(dentro)
        for linha in dentro:
            linha[f"fz_{medida}"] = estat.fora_da_faixa(linha[coluna], linha["n"], taxa, corrigido)
        maior = max(100.0 * linha[coluna] / linha["n"] for linha in dentro)
        saida["taxa"][medida] = round(100.0 * taxa, 1)
        saida["ymax"][medida] = next((teto for teto in (20, 40, 60, 80, 100) if teto >= maior), 100)
        degraus = []
        for n in sorted({linha["n"] for linha in dentro}):
            de, ate = estat.faixa_do_acaso(n, taxa, corrigido)
            degraus.append([n, round(100.0 * de / n, 1), round(100.0 * ate / n, 1)])
        saida["faixa"][medida] = degraus
    return saida


def _da_lista(linha: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    return {"k": linha["k"], "nome": linha["nome"], "pt": linha["pt"], "uf": linha["uf"], **extra}


def autores(ctx: Contexto, dos: Mapping[int, Perfil]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """"Quem propõe": ``(bloco "autores" do lp, as linhas das pessoas, o funil)``.

    As listas seguem a régua: ordenam por **proporção** (nunca por soma), só entre quem tem
    ``limiares.perfil_minimo`` lidos ou mais. ``mais_estr``: a maior parte do topo confirmado
    (``pct`` = ``estr`` ÷ ``n``; primeiro quem passa do acaso; cada linha diz em ``fz`` se
    passa, e leva também ``topo``, a contagem pela ementa);
    ``mais_simb``: a maior parte da faixa mínima, só quem passa do acaso; ``norma_peso``: quem
    tem projeto de peso (``limiares.andamento_peso`` ou mais) que virou norma, com os títulos
    (é fato do registro, não ranking: a ordem é pela quantidade e pelo nome)."""
    lim = ctx.regua.limiares
    linhas = [linha_do_autor(ctx, perfil) for perfil in sorted(dos.values(), key=lambda perfil: (text.normalize(perfil.cadastro.nome), perfil.pid))]
    medido = funil(linhas, lim.perfil_minimo)
    dentro = [linha for linha in linhas if linha["n"] >= lim.perfil_minimo]
    # Proporção com poucos projetos oscila muito: quem passa do acaso vem antes; depois, a maior parte.
    mais_estr = sorted((linha for linha in dentro if linha["estr"]), key=lambda linha: (linha["fz_topo"] != 1, -linha["estr"] / linha["n"], -linha["n"], linha["nome"]))
    mais_simb = sorted((linha for linha in dentro if linha["fz_sb"] == 1), key=lambda linha: (-linha["sb"] / linha["n"], -linha["n"], linha["nome"]))
    normas = []
    for perfil in dos.values():
        viraram = sorted((pr for pr in perfil.lidos if pr.p.estado == projetos.NORMA and (pr.a.peso or 0) >= lim.andamento_peso), key=lambda pr: _maior_peso(ctx, pr))
        if viraram:
            normas.append((perfil, viraram))
    normas.sort(key=lambda par: (-len(par[1]), text.normalize(par[0].cadastro.nome)))
    por_chave = {linha["k"]: linha for linha in linhas}
    bloco = {
        "em_exercicio": len(linhas),
        "plotados": len(dentro),
        "poucos": len(linhas) - len(dentro),  # menos do que o mínimo de lidos (inclui os "sem")
        "sem": sum(1 for linha in linhas if not linha["n"]),
        "com_estr": sum(1 for linha in linhas if linha["estr"]),
        "com_topo": sum(1 for linha in linhas if linha["topo"]),
        "acima_topo": sum(1 for linha in dentro if linha["fz_topo"] == 1),
        "abaixo_topo": sum(1 for linha in dentro if linha["fz_topo"] == -1),
        "acima_sb": sum(1 for linha in dentro if linha["fz_sb"] == 1),
        "abaixo_sb": sum(1 for linha in dentro if linha["fz_sb"] == -1),
        "taxa": medido["taxa"],
        "lidos": sum(linha["n"] for linha in linhas),
        "total": sum(linha["tot"] for linha in linhas),
        "fora": {REAPRESENTADA: sum(linha["rp"] for linha in linhas), DE_BANCADA: sum(linha["bc"] for linha in linhas)},
        "listas": {
            "mais_estr": [
                _da_lista(linha, estr=linha["estr"], topo=linha["topo"], n=linha["n"], pct=_pct(linha["estr"], linha["n"]), fz=linha["fz_topo"], dir=linha["de"])
                for linha in mais_estr[:LISTA]
            ],
            "mais_simb": [_da_lista(linha, sb=linha["sb"], simb=linha["simb"], n=linha["n"], pct=_pct(linha["sb"], linha["n"])) for linha in mais_simb[:LISTA]],
            "norma_peso": [
                _da_lista(por_chave[f"{ctx.casa}-{perfil.pid}"], qt=len(viraram), t=[pr.p.titulo for pr in viraram[:NORMAS_NA_LISTA]]) for perfil, viraram in normas[:LISTA]
            ],
        },
    }
    return bloco, linhas, medido


COLUNAS_DA_AUTORIA = ["k", "t", "e", "d", "st", "pp", "ult", "w", "fx", "ex", "dir", "u", "f", "fe", "fo", "al", "x", "xo", "sl"]


def todos_os_projetos(ctx: Contexto, perfil: Perfil) -> dict[str, Any]:
    """``autoria/<casa>-<id>.json``: todos os projetos de autoria principal da pessoa na
    legislatura, um por linha, com a leitura de cada um. Os que não entram nos números vêm
    marcados: ``x`` = "reap" (ementa igual à de proposição anterior, cujo título vai em ``xo``)
    ou "banc" (de bancada); ``sl`` = por que está sem leitura ("falta": ninguém leu ainda;
    "nd": a ementa não permite dizer; "instavel": os leitores não se entenderam). Do maior
    peso para o menor; os sem leitura por último."""
    linhas = []
    for pr in sorted(perfil.todos, key=lambda pr: (not pr.a.lida, *_maior_peso(ctx, pr))):
        p, a = pr.p, pr.a
        motivo, original = perfil.fora.get(leitura.chave(p), ("", ""))
        linhas.append(
            [
                pr.k,
                p.titulo,
                _curta(_ementa(p), EMENTA_AUTORIA),
                p.apresentada,
                p.estado,
                p.fila if p.estado == projetos.TRAMITANDO else "",
                p.movimento,
                a.peso,
                a.faixa,
                a.alavanca,
                ctx.direcao(a),
                p.url,
                1 if p.titulo in ctx.fichas else 0,
                a.faixa_da_ementa,
                a.fo,
                list(a.alertas),
                motivo,
                original,
                pr.motivo,
            ]
        )
    return {"v": VERSAO, "k": f"{ctx.casa}-{perfil.pid}", "hoje": ctx.hoje, "cols": COLUNAS_DA_AUTORIA, "linhas": linhas}


# ── tudo junto: o bloco "lp", os arquivos e o meta ───────────────────────────

# As colunas de autores-<casa>.json: primeiro as do contrato da tela, na ordem dele (sem "sel",
# que a régua não calcula; "fz_topo" e "fz_sb" são o "fora da faixa do acaso" das duas medidas do
# funil: a parte do topo confirmado, "estr" ÷ "n", e a parte da faixa mínima, "sb" ÷ "n"); depois,
# as que a régua pede a mais ("topo" é a contagem do topo pela ementa, confirmado ou não).
COLUNAS_DO_AUTOR = [
    "k", "nome", "pt", "uf", "n", "estr", "set", "simb", "sb", "de", "ex", "tip", "andou", "norma", "parados", "fz_topo", "fz_sb", "top",
    "va", "vn", "div", "div_cont", "saldo", "gov", "fid", "pres", "ct",
    "topo", "tot", "sl", "sc", "sc_n", "tx", "tq", "mk", "ad", "bc", "rp", "wn", "pp", "pp_n", "pd", "pd_n", "fp", "fp_n", "fd", "fd_n", "prop",
]  # fmt: skip
DO_RESUMO = ("saldo", "gov", "fid", "pres", "ct", "prop")  # o que vem de resumo.json, repetido para a vista não baixar o arquivo inteiro


def _json(dados: Any) -> str:
    return json.dumps(dados, ensure_ascii=False, separators=(",", ":"))


def por_linha(dados: Mapping[str, Any], *grandes: str) -> str:
    """O arquivo com um item por linha nas chaves de ``grandes`` (as tabelas e os dicionários
    de pessoas), para o git guardar só a diferença de um dia para o outro."""
    partes = [_json({k: v for k, v in dados.items() if k not in grandes})[:-1]]
    for chave in grandes:
        valor = dados.get(chave)
        if isinstance(valor, Mapping):
            corpo = ",\n".join(f"{_json(k)}:{_json(v)}" for k, v in valor.items())
            partes.append(f"{_json(chave)}:{{\n{corpo}\n}}")
        else:
            corpo = ",\n".join(_json(item) for item in valor or [])
            partes.append(f"{_json(chave)}:[\n{corpo}\n]")
    return ",".join(partes) + "}\n"


def cobertura(ctx: Contexto, itens: Mapping[str, leitura.Item]) -> dict[str, Any]:
    """Quantas proposições do universo da Casa têm leitura: ``total``, ``lidos``, ``ia``,
    ``confirmados`` e ``sem_leitura`` (de :func:`leitura.cobertura`), e o mesmo recorte
    (``total`` e ``lidos``) em cada universo: ``perfil`` (o que cada um propõe), ``espera`` (o
    que tramita) e ``voto`` (as matérias votadas). Uma proposição pode estar em mais de um."""
    lidas = {chave: ctx.leitor.de(item) for chave, item in itens.items()}
    saida: dict[str, Any] = dict(leitura.cobertura(ctx.regua, lidas.values()))
    for motivo in leitura.UNIVERSOS:
        parte = leitura.cobertura(ctx.regua, (lidas[chave] for chave, item in itens.items() if motivo in item.universos))
        saida[motivo] = {"total": parte["total"], "lidos": parte["lidos"]}
    return saida


def da_casa(
    ctx: Contexto,
    *,
    props: list[Proposicao],
    autoria: Mapping[int, list[Proposicao]],
    relatorias: Mapping[int, list[Relatoria]] | None = None,
    comissoes: Mapping[str, tuple[int, str]] | None = None,
    votos: list[AnoVotos],
    resumo: Mapping[str, Mapping[str, Any]],
    fichas_das_materias: Mapping[str, Mapping[str, Any]] | None = None,
    exercicio: Mapping[int, list[tuple[str, str]]] | None = None,
    presidencia: Mapping[int, list[tuple[str, str]]] | None = None,
) -> dict[str, Any]:
    """As contas de uma Casa: ``{"lp", "projetos", "autores", "decide", "autoria", "cobertura"}``
    (o bloco do painel, o conteúdo dos três arquivos, os arquivos de autoria por id de pessoa e
    a cobertura da leitura).

    ``props``: as proposições da Casa (na Câmara, todas as carregadas; no Senado, o que
    tramita). ``autoria``: por parlamentar, as proposições que ele assina. ``relatorias``: só
    no Senado (a lista do que tramita não traz o relator). ``resumo``: chave da pessoa → a
    linha dela em ``resumo.json`` (de onde vêm o saldo, o governismo, a fidelidade, a presença,
    a cota e o número de propostas)."""
    from . import pais_cruzar, pais_voto  # import tardio: os dois usam este módulo

    das_pessoas = [p for lista in autoria.values() for p in lista]
    if ctx.casa == "sf":
        # No Senado, quem assina o quê só sai das listas de cada senador em exercício.
        todas = [*props, *das_pessoas]
        principais = {leitura.chave(p): pid for pid, lista in autoria.items() for p in lista if p.primeiro == pid}
        coautores: dict[str, list[int]] | None = defaultdict(list)
        for pid, lista in autoria.items():
            for p in lista:
                coautores[leitura.chave(p)].append(pid)
        autorais, normas = das_pessoas, das_pessoas
    else:
        todas, principais, coautores, autorais, normas = props, {}, None, props, props

    bloco_estoque, arquivo_projetos = estoque(ctx, props, comissoes=comissoes, relatorias=relatorias, principais=principais, normas=normas)
    dos = perfis(ctx, autoria, repetidas=reapresentadas(todas, ctx.tipos), coautores=coautores)
    bloco_autores, linhas, medido = autores(ctx, dos)
    bloco_decide, votos_de_cada_um, arquivo_decide = pais_voto.calcular(ctx, votos, exercicio=exercicio, presidencia=presidencia)
    for linha in linhas:
        pid = int(linha["k"].split("-", 1)[1])
        linha.update(votos_de_cada_um.get(pid) or {})
        do_resumo = resumo.get(linha["k"]) or {}
        linha.update({campo: do_resumo.get(campo) for campo in DO_RESUMO})
    itens = leitura.universo(ctx.regua, ctx.casa, tramitam=props, autorais=autorais, anos=votos, inicio=ctx.inicio, fichas=fichas_das_materias)
    coberto = cobertura(ctx, itens)
    return {
        "lp": {
            "cobertura": coberto,
            "estoque": bloco_estoque,
            "autores": bloco_autores,
            "decide": bloco_decide,
            "cruzar": pais_cruzar.calcular(linhas, ctx.regua, ctx.casa),
        },
        "projetos": arquivo_projetos,
        "autores": {
            "v": VERSAO,
            "casa": ctx.casa,
            "hoje": ctx.hoje,
            "taxa": medido["taxa"],
            "ymax": medido["ymax"],
            "xmax": medido["xmax"],
            "comparados": medido["comparados"],
            "faixa": medido["faixa"],
            "cols": COLUNAS_DO_AUTOR,
            "linhas": [[linha.get(coluna) for coluna in COLUNAS_DO_AUTOR] for linha in linhas],
        },
        "decide": arquivo_decide,
        "autoria": {pid: todos_os_projetos(ctx, perfil) for pid, perfil in dos.items() if perfil.todos},
        "cobertura": coberto,
    }


def cortes(regras: Regras, regua: ProjetoDePais) -> dict[str, Any]:
    """Os cortes e os mínimos em uso, como vão ao ``meta.json`` e ao manifesto da página."""
    lim = regua.limiares
    return {
        "faixas": [[f.id, f.de] for f in regua.faixas],
        "confirma": regua.segunda_leitura.peso_minimo,  # a faixa que começa aqui ou acima só conta com duas leituras
        "de_peso": lim.andamento_peso,
        "voto_peso": lim.voto_peso,
        "min_proj": lim.perfil_minimo,
        "min_saldo": lim.saldo_minimo,
        "min_votos": lim.voto_minimo,
        "min_partido": lim.partido_minimo,
        "min_pessoas": lim.pessoas_minimo,
        "quase_unanime": lim.voto_quase_unanime,
        "longe": lim.longe_do_partido,
        "teto_pontos": lim.teto_pontos,
        "dias_parado": regras.limiares.dias_parado,
        "partido_fechado": regras.limiares.coesao_partido,
        "funil_alfa": ALFA_FUNIL,
    }


def manifesto(regras: Regras) -> dict[str, Any] | None:
    """O que a página precisa para rotular a visão por parlamentar sem escrever nome de
    categoria: tudo sai de ``config/congresso.yaml`` (``projeto_de_pais``) e das constantes
    deste pacote. ``None`` sem o bloco no config.

    - ``arq``: os nomes dos arquivos (``{casa}`` no lugar da Casa; ``autoria`` é a pasta);
    - ``faixas``: ``[id, rótulo, piso]``; ``grupos``: quais faixas são o topo (``estr`` e
      ``topo`` nas contagens), quais são as baixas (``simb``) e qual é a mínima (``sb``);
    - ``eixos``: as alavancas, ``[id, rótulo, nível]``, na ordem das colunas ``ex``;
    - ``direcoes``: os valores da marca do caixa, ``[id, rótulo, forma, valor]``, na ordem das
      colunas ``de``; ``marcas``: as três, na ordem de ``mk``, com os valores de cada uma;
    - ``campos``: campo → ``[rótulo, {código: rótulo}]`` (para os pares de ``cod``);
    - ``excecao_para``: os códigos da exceção, ``[id, rótulo]``, na ordem de ``tq``;
    - ``alertas``, ``fontes``, ``selo``, ``nd``, ``objetivo`` e ``leituras`` (como a página diz
      cada estado ``r``): os textos do config;
    - ``portas``: ``[id, o que espera, quem segura]``, na ordem do processo; ``estados``;
    - ``medidas`` (``[id, grupo]``) e ``pares``: os de "Pessoa ou partido?";
    - ``funil``: ``[medida, coluna da contagem]`` de cada medida do funil de "Quem propõe" (a
      altura de cada pessoa no gráfico é essa coluna ÷ ``n``);
    - ``cortes``: os de :func:`cortes`."""
    from . import pais_cruzar  # import tardio: ele usa este módulo

    regua = regras.projeto_de_pais
    if regua is None:
        return None
    faixas = Faixas.da(regua)
    caixa = regua.marca(regua.papeis.caixa) if regua.papeis.caixa else None
    campo_da_excecao = next((campo for campo in regua.campos if campo.marca and campo.marca == regua.papeis.excecao), None)
    return {
        "arq": {"projetos": PROJETOS, "autores": AUTORES, "decide": DECIDE, "autoria": AUTORIA},
        "selo": regua.selo,
        "objetivo": regua.objetivo,
        "nd": regua.nd,
        "cortes": cortes(regras, regua),
        "faixas": [[f.id, f.rotulo, f.de] for f in regua.faixas],
        "grupos": {"topo": [f.id for f in regua.faixas if f.id in faixas.topo], "baixas": [f.id for f in regua.faixas if f.id in faixas.baixas], "piso": faixas.piso},
        "eixos": [[c.id, c.rotulo, c.nivel] for c in regua.alavanca.codigos],
        "niveis": [[nome, rotulo] for nome, _peso, rotulo in regua.niveis],
        "direcoes": [[v.id, v.rotulo, v.forma, v.valor] for v in caixa.valores] if caixa else [],
        "marcas": [[m.id, m.rotulo, [[v.id, v.rotulo, v.forma, v.valor] for v in m.valores]] for m in regua.marcas],
        "papeis": {"caixa": regua.papeis.caixa, "excecao": regua.papeis.excecao, "controle": regua.papeis.controle},
        "campos": {campo.id: [campo.rotulo, {codigo.id: codigo.rotulo for codigo in campo.codigos}] for campo in regua.campos},
        "excecao_para": [[codigo.id, codigo.rotulo] for codigo in campo_da_excecao.codigos if codigo.valor == -1] if campo_da_excecao else [],
        "alertas": dict(regua.alertas),
        "fontes": dict(regua.fontes),
        "leituras": {str(r): rotulo for r, rotulo in regua.leitura.estados},
        "portas": [[porta, projetos.FILAS[porta], projetos.QUEM_SEGURA.get(porta, "")] for porta in PORTAS],
        "estados": dict(projetos.ESTADOS),
        "fora_do_perfil": [REAPRESENTADA, DE_BANCADA],
        "medidas": [[ident, grupo] for ident, grupo, _valor in pais_cruzar.MEDIDAS],
        "funil": [list(par) for par in FUNIL],
        "pares": [list(par) for par in pais_cruzar.PARES],
    }


def do_meta(regras: Regras, regua: ProjetoDePais, hoje: str, coberturas: Mapping[str, Any], auditoria: Any) -> dict[str, Any]:
    """``meta.json`` → ``leitura_projetos``: a versão das instruções ao leitor, a data, os cortes
    e os mínimos em uso, a cobertura por Casa e a auditoria (a concordância entre leitores, medida
    por fora e só repassada; ausente se o arquivo não existe)."""
    saida: dict[str, Any] = {
        "versao_da_regua": regua.leitura.versao,
        "data": hoje,
        "objetivo": regua.objetivo,
        "selo": regua.selo,
        "cortes": cortes(regras, regua),
        "cobertura": dict(coberturas),
    }
    if auditoria is not None:
        saida["auditoria"] = auditoria
        if isinstance(auditoria, Mapping) and auditoria.get("modelo"):
            saida["modelo"] = auditoria["modelo"]
    return saida


def integrar(
    root: Path,
    pasta: Path,
    regras: Regras,
    hoje: str,
    paineis: dict[str, dict[str, Any]],
    meta: dict[str, Any],
    avisos: list[str],
    *,
    gravar: Callable[[Path, str], bool],
    props: Mapping[str, list[Proposicao]],
    autoria: Mapping[str, Mapping[int, list[Proposicao]]],
    relatorias: Mapping[str, Mapping[int, list[Relatoria]]],
    comissoes: Mapping[str, tuple[int, str]],
    cadastros: Mapping[str, Mapping[int, Cadastro]],
    votos: Mapping[str, list[AnoVotos]],
    resumo: Iterable[Mapping[str, Any]],
    fichas_das_materias: Mapping[str, Mapping[str, Any]],
    exercicio: Mapping[int, list[tuple[str, str]]] | None = None,
    presidencia: Mapping[int, list[tuple[str, str]]] | None = None,
) -> int:
    """Faz as contas da visão por parlamentar com o que a atualização já carregou e grava o que
    é dela: o bloco ``lp`` em ``paineis[casa]`` (vai para ``casa-<casa>.json``),
    ``meta["leitura_projetos"]``, ``projetos-``, ``autores-`` e ``decide-<casa>.json`` e
    ``autoria/<casa>-<id>.json``. Devolve quantos arquivos gravou.

    Sem o bloco ``projeto_de_pais`` no config, ou sem nenhuma leitura na guarda
    (``data/congresso/leitura/``): não faz nada, e os JSON saem como sempre saíram. Casa sem
    leitura nenhuma fica sem o bloco. As contas das duas Casas terminam antes de qualquer
    gravação: um erro no meio não deixa a visão pela metade."""
    leitor = leitura.abrir(root, pasta, regras, avisos=avisos)
    if leitor is None or not len(leitor.guarda):
        return 0
    comeco = time.perf_counter()
    regua = leitor.regua
    legislatura = regras.legislatura_atual(hoje)
    linhas_do_resumo = {str(linha.get("k")): linha for linha in resumo}
    saidas: dict[str, dict[str, Any]] = {}
    for casa in ("cd", "sf"):
        if not leitor.guarda.da_casa(casa):
            continue
        nomes = {c.id: c.nome for c in cadastros[casa].values()}
        for ano in votos.get(casa) or []:
            for pessoa in ano.pessoas:
                nomes.setdefault(pessoa.id, pessoa.nome)
        fichas = (fichas_das_materias.get(casa) or {}).get("materias") or {}
        ctx = Contexto(
            casa=casa, hoje=hoje, inicio=legislatura.inicio if legislatura else "", regras=regras, regua=regua, leitor=leitor,
            cadastros=cadastros[casa], nomes=nomes, fichas=frozenset(fichas),
        )
        saidas[casa] = da_casa(
            ctx, props=list(props.get(casa) or []), autoria=autoria.get(casa) or {}, relatorias=relatorias.get(casa) if casa == "sf" else None,
            comissoes=comissoes if casa == "cd" else None, votos=list(votos.get(casa) or []), resumo=linhas_do_resumo, fichas_das_materias=fichas,
            exercicio=exercicio if casa == "cd" else None, presidencia=presidencia if casa == "cd" else None,
        )
    if not saidas:
        return 0
    try:
        auditoria = json.loads((leitura.pasta_da_guarda(pasta) / leitura.AUDITORIA).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        auditoria = None
    gravados = 0
    atuais: set[str] = set()
    for casa, saida in saidas.items():
        paineis[casa]["lp"] = saida["lp"]
        gravados += gravar(pasta / PROJETOS.format(casa=casa), por_linha(saida["projetos"], "linhas"))
        gravados += gravar(pasta / AUTORES.format(casa=casa), por_linha(saida["autores"], "linhas"))
        gravados += gravar(pasta / DECIDE.format(casa=casa), por_linha(saida["decide"], "votacoes", "demais", "votos", "contra"))
        for pid, arquivo in saida["autoria"].items():
            nome = f"{casa}-{pid}.json"
            atuais.add(nome)
            gravados += gravar(pasta / AUTORIA / nome, por_linha(arquivo, "linhas"))
    if (pasta / AUTORIA).is_dir():  # quem saiu do exercício, ou ficou sem projeto, não deixa arquivo velho
        for antigo in (pasta / AUTORIA).glob("*.json"):
            if antigo.name not in atuais and antigo.name.split("-", 1)[0] in saidas:
                antigo.unlink(missing_ok=True)
    meta["leitura_projetos"] = do_meta(regras, regua, hoje, {casa: saida["cobertura"] for casa, saida in saidas.items()}, auditoria)
    # A leitura diária (``congresso ler``): o resumo do registro dela (``leitura/diario.json``), se
    # existir. Só repassado, como a auditoria: quando rodou por último, quantas leu, a nota nos controles.
    diaria = leitura.resumo_do_diario(leitura.pasta_da_guarda(pasta))
    if diaria is not None:
        meta["leitura_projetos"]["diaria"] = diaria
    log.info(
        "Visão por parlamentar (%.0f s): %s",
        time.perf_counter() - comeco,
        "; ".join(f"{casa}: {s['cobertura']['lidos']} de {s['cobertura']['total']} proposições lidas, {len(s['projetos']['linhas'])} no gráfico, {len(s['autoria'])} perfis" for casa, s in saidas.items()),
    )
    return gravados
