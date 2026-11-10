"""Enquadramento: onde a imprensa rachou (a segunda lente do Analítico Político).

A cobertura comparada de cada edição diz, para cada assunto visto por dois ou mais
veículos, os dois lados do debate (enfoques neutros, como "Destaca o risco fiscal" ×
"Destaca a arrecadação recorde") e o lado que cada veículo seguiu. Este módulo junta,
nas edições de uma janela, os assuntos de uma editoria que tiveram **veículos nos dois
lados** — nas matérias da edição e nos demais assuntos comparados — e responde:

- **onde rachou**: os assuntos mais divididos (mais veículos no lado menor; depois o
  mais perto de meio a meio), com os dois enfoques e quem ficou em cada um;
- **quem fica com quem**: para cada par de veículos, em quantos assuntos divididos os
  dois tomaram o mesmo lado, contra o que o acaso daria;
- **quem destoa**: quantas vezes cada veículo ficou no lado com menos veículos, também
  contra o acaso.

O acaso não é "metade das vezes": num assunto rachado em 2 × 2, dois veículos sorteados
só caem do mesmo lado 1 vez em 3. Por isso cada contagem é comparada com a esperada
**dado o placar de cada assunto** (os lados embaralhados entre os veículos que tomaram
lado), e só vira afirmação quando a chance de uma contagem tão extrema é menor que
``ALFA`` (99%, porque muitos pares são comparados de uma vez). A conta é exata (a
distribuição da soma dos sorteios de cada assunto), não uma aproximação: com poucos
assuntos em comum, a curva normal erraria justamente nas pontas.

Os lados são os enfoques de cada assunto, não "a favor" ou "contra" alguém: ficar do
mesmo lado quer dizer enquadrar o assunto do mesmo jeito.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import combinations
from math import comb

from .. import text
from ..models import Coverage, Edition
from . import filters
from .web import coverage_view

__all__ = ["SECAO", "Assunto", "DiaLados", "LadosView", "do_dia", "lados"]

log = logging.getLogger(__name__)

SECAO = "politica"  # a editoria dos nomes em destaque (qijournal/render/midia.py)
JANELA_DIAS = 30  # as mesmas edições dos nomes em destaque
SEMANA = 7  # "recentes": os assuntos das últimas 7 edições
RECENTES = 4  # assuntos recentes abertos
PERIODO = 8  # os mais divididos do resto do período (num "ver mais")
MIN_PAR = 8  # assuntos divididos em que os dois veículos tomaram lado, para o par entrar
MIN_DESTOA = 8  # assuntos com maioria em que o veículo tomou lado, para entrar em "quem destoa"
PARES = 10  # pares na lista (os mais longe do acaso primeiro)
ALFA = 0.01  # 99% (dois lados): muitos pares e veículos comparados de uma vez


# ── um dia ───────────────────────────────────────────────────────────────────


@dataclass
class Assunto:
    """Um assunto com veículos nos dois lados, numa edição."""

    data: str  # AAAA-MM-DD da edição
    titulo: str
    lado_a: str
    lado_b: str
    a: list[str]  # veículos do lado A (uma vez cada, na ordem da edição)
    b: list[str]
    neutros: int
    conclusao: str = ""
    materia: str = ""  # id da matéria na edição; vazio = assunto só da cobertura comparada

    @property
    def lados(self) -> dict[str, str]:
        """Veículo → lado, só de quem ficou em um lado só (quem aparece nos dois não entra)."""
        nos_dois = set(self.a) & set(self.b)
        return {v: lado for lado, lista in (("a", self.a), ("b", self.b)) for v in lista if v not in nos_dois}

    @property
    def divergencia(self) -> float:
        """2 × o lado menor ÷ (A + B): 1 = meio a meio (a conta da Cobertura comparada)."""
        return 2 * min(len(self.a), len(self.b)) / (len(self.a) + len(self.b))


@dataclass
class DiaLados:
    """O que uma edição oferece à lente: quantos assuntos da editoria foram comparados
    (dois ou mais veículos) e quais tiveram veículos nos dois lados."""

    data: str
    comparados: int = 0
    divididos: list[Assunto] = field(default_factory=list)


def _unicos(nomes: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(n for n in nomes if n))


def _assunto(data: str, coverage: Coverage | None, *, titulo: str = "", materia: str = "") -> tuple[bool, Assunto | None]:
    """(conta como assunto comparado?, o assunto se rachou)."""
    view = coverage_view(coverage)
    if view is None or view.total < 2:
        return False, None
    a, b = _unicos(o.name for o in view.a), _unicos(o.name for o in view.b)
    if not (view.side_a and view.side_b and a and b):
        return True, None
    return True, Assunto(
        data=data,
        titulo=filters.plain(titulo) or view.topic,
        lado_a=view.side_a,
        lado_b=view.side_b,
        a=a,
        b=b,
        neutros=len(_unicos(o.name for o in view.neutral)),
        conclusao=view.conclusion,
        materia=materia,
    )


def do_dia(edition: Edition, secao: str = SECAO) -> DiaLados:
    """Os assuntos da editoria ``secao`` com cobertura comparada numa edição: as matérias
    com análise da cobertura e os demais assuntos comparados (como na seção Cobertura
    comparada da edição)."""
    dia = DiaLados(data=str(edition.date or "")[:10])
    itens = [(s.coverage, s.headline, s.id) for s in edition.stories if s.coverage is not None and s.section == secao]
    itens += [(c, "", "") for c in edition.compared if c.section == secao]
    for coverage, titulo, materia in itens:
        conta, assunto = _assunto(dia.data, coverage, titulo=titulo, materia=materia)
        dia.comparados += conta
        if assunto is not None:
            dia.divididos.append(assunto)
    return dia


# ── a janela ─────────────────────────────────────────────────────────────────


@dataclass
class AssuntoView:
    titulo: str
    data: str  # "06/11/2026"
    href: str  # a matéria ou a Cobertura comparada da edição ("" se a edição saiu do arquivo)
    materia: bool
    lado_a: str
    lado_b: str
    a: list[str]
    b: list[str]
    neutros: int
    pct_a: float  # larguras do medidor (somam 100)
    pct_n: float
    pct_b: float
    divergencia: str  # 0 a 100 (100 = meio a meio)
    conclusao: str
    aria: str


@dataclass
class ParView:
    a: str
    b: str
    juntos: int
    total: int
    esperado: str  # "6,5": quantos assuntos o acaso daria do mesmo lado
    pct: str  # "18%"
    barra: float  # % do mesmo lado (0 a 100)
    marca: float  # % esperado pelo acaso (o traço na barra)
    classe: str  # "op" (opostos além do acaso) | "ju" (juntos além do acaso) | ""
    leitura: str
    z: float | None
    title: str


@dataclass
class DestoaView:
    nome: str
    minoria: int
    total: int  # assuntos com maioria em que o veículo tomou lado
    esperado: str
    pct: str
    barra: float
    marca: float
    classe: str  # "mais" (destoa além do acaso) | "menos" (acompanha a maioria além do acaso) | ""
    title: str


@dataclass
class LadosView:
    secao: str
    secao_titulo: str
    periodo: str
    edicoes: int
    comparados: int  # assuntos da editoria com cobertura comparada
    divididos: int  # com veículos nos dois lados
    pct_divididos: str
    empates: int  # divididos com o mesmo número de veículos de cada lado
    materias: int  # divididos que são matéria da edição
    recentes: list[AssuntoView]
    recentes_n: int  # assuntos divididos nas últimas SEMANA edições
    demais: list[AssuntoView]  # os mais divididos do resto do período
    pares: list[ParView]
    pares_fora: int  # pares com menos de MIN_PAR assuntos em comum (fora da lista)
    pares_mais: int  # pares com MIN_PAR ou mais que não couberam na lista
    par_destaque: ParView | None  # o par mais longe do acaso, entre os que passam dele
    destoam: list[DestoaView]
    leitura: list[str]
    min_par: int = MIN_PAR


def _pct(parte: float, todo: float) -> str:
    if not todo:
        return "—"
    valor = 100 * parte / todo
    return f"{text.format_number_pt(valor, 1 if 0 < valor < 10 else 0)}%"


def _data(iso: str) -> str:
    try:
        return text.pt_short_date(date.fromisoformat(iso[:10]))
    except ValueError:
        return iso


def _acaso_par(na: int, nb: int) -> float:
    """Chance de dois veículos sorteados entre os ``na + nb`` que tomaram lado caírem do mesmo."""
    return (comb(na, 2) + comb(nb, 2)) / comb(na + nb, 2)


def _z(observado: int, probabilidades: list[float]) -> tuple[float, float | None]:
    """(esperado, z) de uma contagem contra a soma de sorteios independentes de chances diferentes
    (o z serve para ordenar; quem decide se passa do acaso é :func:`_p`)."""
    esperado = sum(probabilidades)
    variancia = sum(p * (1 - p) for p in probabilidades)
    return esperado, None if variancia <= 0 else (observado - esperado) / variancia**0.5


def _p(observado: int, probabilidades: list[float]) -> float:
    """A chance, sob o acaso, de uma contagem tão ou mais extrema que a observada (dois lados):
    a distribuição exata da soma de sorteios independentes de chances diferentes."""
    dist = [1.0]
    for p in probabilidades:
        nova = [0.0] * (len(dist) + 1)
        for k, v in enumerate(dist):
            nova[k] += v * (1 - p)
            nova[k + 1] += v * p
        dist = nova
    return min(1.0, 2 * min(sum(dist[observado:]), sum(dist[: observado + 1])))


def _ordem(a: Assunto) -> tuple:
    """Mais disputados primeiro: mais veículos no lado menor (um 1 × 1 não vale um 3 × 4),
    depois o mais perto de meio a meio, o com mais veículos e o mais recente."""
    return (-min(len(a.a), len(a.b)), -a.divergencia, -(len(a.a) + len(a.b) + a.neutros), _inverso(a.data), a.titulo)


def _inverso(iso: str) -> int:
    try:
        return -date.fromisoformat(iso[:10]).toordinal()
    except ValueError:
        return 0


def _assunto_view(a: Assunto, edicoes: set[str]) -> AssuntoView:
    na, nb, nn = len(a.a), len(a.b), a.neutros
    total = na + nb + nn
    href = ""
    if a.data in edicoes:
        href = f"{a.data}.html#s-{a.materia}" if a.materia else f"{a.data}.html#cobertura"
    return AssuntoView(
        titulo=a.titulo,
        data=_data(a.data),
        href=href,
        materia=bool(a.materia),
        lado_a=a.lado_a,
        lado_b=a.lado_b,
        a=a.a,
        b=a.b,
        neutros=nn,
        pct_a=round(100 * na / total, 2),
        pct_n=round(100 * nn / total, 2),
        pct_b=round(100 * nb / total, 2),
        divergencia=text.format_number_pt(100 * a.divergencia, 0),
        conclusao=a.conclusao,
        aria=f"{a.lado_a}: {na}; neutros: {nn}; {a.lado_b}: {nb}",
    )


def _pares(assuntos: list[Assunto]) -> tuple[list[ParView], int]:
    """Cada par de veículos nos assuntos em que os dois tomaram lado: do mesmo lado em
    quantos, contra o esperado pelo placar de cada assunto."""
    juntos: Counter[tuple[str, str]] = Counter()
    chances: dict[tuple[str, str], list[float]] = {}
    for assunto in assuntos:
        lados_ = assunto.lados
        na = sum(1 for lado in lados_.values() if lado == "a")
        nb = len(lados_) - na
        if na + nb < 2:
            continue
        p = _acaso_par(na, nb)
        for x, y in combinations(sorted(lados_), 2):
            chances.setdefault((x, y), []).append(p)
            juntos[(x, y)] += lados_[x] == lados_[y]
    pares = []
    for par, ps in chances.items():
        total = len(ps)
        if total < MIN_PAR:
            continue
        k = juntos[par]
        esperado, z = _z(k, ps)
        classe = ("ju" if k > esperado else "op") if _p(k, ps) < ALFA else ""
        leitura = {
            "op": "em lados opostos além do acaso",
            "ju": "do mesmo lado além do acaso",
        }.get(classe, "dentro do que o acaso explica")
        esperado_txt = text.format_number_pt(esperado, 1)
        pares.append(
            ParView(
                a=par[0],
                b=par[1],
                juntos=k,
                total=total,
                esperado=esperado_txt,
                pct=_pct(k, total),
                barra=round(100 * k / total, 1),
                marca=round(100 * esperado / total, 1),
                classe=classe,
                leitura=leitura,
                z=z,
                title=(
                    f"{par[0]} e {par[1]}: do mesmo lado em {k} de {total} assuntos divididos em que os dois "
                    f"tomaram lado; o acaso daria {esperado_txt} ({leitura})"
                ),
            )
        )
    # Os mais longe do acaso primeiro (para qualquer lado); no empate, o par com mais assuntos.
    pares.sort(key=lambda p: (-abs(p.z or 0.0), -p.total, p.a, p.b))
    return pares, sum(1 for ps in chances.values() if len(ps) < MIN_PAR)


def _destoam(assuntos: list[Assunto]) -> list[DestoaView]:
    """Em quantos assuntos com maioria cada veículo ficou no lado menor, contra o esperado."""
    minoria: Counter[str] = Counter()
    chances: dict[str, list[float]] = {}
    for assunto in assuntos:
        lados_ = assunto.lados
        na = sum(1 for lado in lados_.values() if lado == "a")
        nb = len(lados_) - na
        if na == nb or not na or not nb:
            continue  # empate (ou lado vazio depois de tirar quem ficou nos dois): não há maioria
        menor = "a" if na < nb else "b"
        p = min(na, nb) / (na + nb)
        for veiculo, lado in lados_.items():
            chances.setdefault(veiculo, []).append(p)
            minoria[veiculo] += lado == menor
    saida = []
    for veiculo, ps in chances.items():
        total = len(ps)
        if total < MIN_DESTOA:
            continue
        k = minoria[veiculo]
        esperado, _z_veiculo = _z(k, ps)
        classe = ("mais" if k > esperado else "menos") if _p(k, ps) < ALFA else ""
        esperado_txt = text.format_number_pt(esperado, 1)
        saida.append(
            DestoaView(
                nome=veiculo,
                minoria=k,
                total=total,
                esperado=esperado_txt,
                pct=_pct(k, total),
                barra=round(100 * k / total, 1),
                marca=round(100 * esperado / total, 1),
                classe=classe,
                title=(
                    f"{veiculo}: no lado com menos veículos em {k} de {total} assuntos com maioria; "
                    f"o acaso daria {esperado_txt}"
                ),
            )
        )
    saida.sort(key=lambda d: (-d.barra, -d.total, d.nome))
    return saida


def _leitura(view: LadosView, mais: Assunto | None) -> list[str]:
    frases = [
        f"{text.format_number_pt(view.divididos, 0)} dos {text.format_number_pt(view.comparados, 0)} assuntos de "
        f"{view.secao_titulo} vistos por dois ou mais veículos ({view.pct_divididos}) tiveram veículos nos dois lados"
        + (f"; em {text.format_number_pt(view.empates, 0)} deles o placar foi empate" if view.empates else "")
        + "."
    ]
    if mais is not None:
        frases.append(
            f"O mais dividido: “{mais.titulo}” ({_data(mais.data)}), {len(mais.a)} × {len(mais.b)}: "
            f"{', '.join(mais.a)} em “{mais.lado_a}”; {', '.join(mais.b)} em “{mais.lado_b}”."
        )
    par = view.par_destaque
    if par is not None:
        como = "em lados opostos" if par.classe == "op" else "do mesmo lado"
        k = par.total - par.juntos if par.classe == "op" else par.juntos
        frases.append(
            f"{par.a} e {par.b} ficaram {como} em {k} de {par.total} assuntos divididos em que os dois tomaram lado "
            f"(o acaso daria {par.esperado} do mesmo lado): a diferença passa do acaso."
        )
    elif view.pares:
        frases.append(
            "Nenhum par de veículos fica do mesmo lado ou em lados opostos além do que o acaso explica: "
            "os alinhamentos mudam de assunto para assunto."
        )
    destoa = next((d for d in view.destoam if d.classe == "mais"), None)
    if destoa is not None:
        frases.append(
            f"Quem mais destoa: {destoa.nome}, no lado com menos veículos em {destoa.minoria} de {destoa.total} "
            f"assuntos com maioria (o acaso daria {destoa.esperado})."
        )
    return frases


def lados(
    dias: Iterable[DiaLados],
    *,
    fim: str | None = None,
    secao: str = SECAO,
    secao_titulo: str = "Política",
    edicoes: Iterable[str] = (),
    janela: int = JANELA_DIAS,
) -> LadosView | None:
    """A lente do enquadramento nas edições dos últimos ``janela`` dias até ``fim`` (padrão:
    a edição mais recente). ``edicoes``: as datas com edição no arquivo (para os links).
    ``None`` sem nenhum assunto comparado na editoria."""
    todos = sorted((d for d in dias if d is not None and d.data), key=lambda d: d.data)
    if not todos:
        return None
    ultimo = (fim or todos[-1].data)[:10]
    try:
        inicio = (date.fromisoformat(ultimo) - timedelta(days=janela - 1)).isoformat()
    except ValueError:
        return None
    na_janela = [d for d in todos if inicio <= d.data <= ultimo]
    comparados = sum(d.comparados for d in na_janela)
    if not comparados:
        return None
    assuntos = [a for d in na_janela for a in d.divididos]
    arquivo = set(edicoes)
    datas = [d.data for d in na_janela]
    recentes_datas = set(datas[-SEMANA:])
    ordenados = sorted(assuntos, key=_ordem)
    recentes = [a for a in ordenados if a.data in recentes_datas]
    abertos = recentes[:RECENTES]
    resto = [a for a in ordenados if a not in abertos][:PERIODO]
    pares, pares_fora = _pares(assuntos)
    destaque = next((par for par in pares if par.classe), None)
    primeira, ultima = _data(na_janela[0].data), _data(na_janela[-1].data)
    view = LadosView(
        secao=secao,
        secao_titulo=secao_titulo,
        periodo=primeira if primeira == ultima else f"{primeira} a {ultima}",
        edicoes=len(na_janela),
        comparados=comparados,
        divididos=len(assuntos),
        pct_divididos=_pct(len(assuntos), comparados),
        empates=sum(1 for a in assuntos if len(a.a) == len(a.b)),
        materias=sum(1 for a in assuntos if a.materia),
        recentes=[_assunto_view(a, arquivo) for a in abertos],
        recentes_n=len(recentes),
        demais=[_assunto_view(a, arquivo) for a in resto],
        pares=pares[:PARES],
        pares_fora=pares_fora,
        pares_mais=max(0, len(pares) - PARES),
        par_destaque=destaque,
        destoam=_destoam(assuntos),
        leitura=[],
    )
    view.leitura = _leitura(view, ordenados[0] if ordenados else None)
    return view
