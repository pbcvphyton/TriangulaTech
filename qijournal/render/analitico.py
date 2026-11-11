"""Aba "Analítico Político" (``edicoes/analitico.html``): como a imprensa cobre a política.

A página é refeita a cada edição e se organiza pelo que os dados têm a dizer, não pela
ordem em que foram calculados. Abre com **a leitura** (:mod:`qijournal.render.achados`): a
manchete e os achados que passam do acaso, cada um com o número e o endereço da prova.
Depois vêm as três lentes, na ordem em que as diferenças entre os veículos aparecem:

1. **Manchetes** (:mod:`qijournal.render.midia`): o placar dos nomes (espaço, a curva das
   edições, tom e o que mudou em sete edições; cada linha abre o dossiê do nome) e a
   matriz veículo × nome;
2. **Enquadramento** (:mod:`qijournal.render.lados`): os assuntos em que os veículos
   racharam, quem fica com quem e quem destoa;
3. **Texto** (:mod:`qijournal.termometro`): o que o termômetro diz, os desvios (os T
   diferentes de zero e para que lado caem), os textos mais inclinados com a citação que
   pesou, onde estão os sinais e a qualidade da apuração.

Fecha com a **base completa** do termômetro, recolhida, nos termos da aba analítica da
especificação (:mod:`qijournal.termometro.agregacao`): para onde estamos indo (tendência
mensal de T e de Q, a janela recente comparada à base e quem puxou a mudança), as séries
semanais, os rankings por veículo e por editoria (T ajustado e qualidade), os recortes
por eixo, a tabela de cada dia e a lista das análises. Um bloco da base sobe para a
leitura quando tem o que dizer (uma tendência ou um desvio que passa do acaso).

Notícias e opinião não se misturam: as médias, séries e rankings são das notícias; a
opinião aparece à parte. Só entram análises da versão atual do cálculo (a agregação
filtra por versão). Todos os números do termômetro saem do cálculo de referência e da
agregação; aqui só se conta o que está guardado.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from math import erfc, sqrt
from typing import Any

from .. import text
from ..config import Config
from ..termometro import agregacao
from ..termometro.referencia import PARAMETROS, arredondar
from . import achados, filters
from .achados import InclinadoResumo, MatrizView, MudancaTexto, PlacarView, TextoResumo
from .lados import LadosView, holm
from .midia import DestaquesView
from .termometro import (
    ANALITICO_PAGINA,
    CLASSE,
    EIXOS,
    AnaliseView,
    ChecklistView,
    DistribuicaoView,
    EvidenciaView,
    GaugeView,
    SinaisView,
    _checklist_do_dia,
    _sinais,
    analise_view,
    contagem,
    distribuicao,
    fmt_dec,
    fmt_ic,
    fmt_q,
    fmt_t,
    gauge,
    porcento_txt,
    todas_validas,
)
from .web import brand_view

__all__ = ["ANALITICO_PAGINA", "render_analitico_page", "analitico_view"]

log = logging.getLogger(__name__)

P = agregacao.P_ANALITICO
PUXADORES_MAX = 8
DOMINIOS = (25, 50, 100)
# Rankings de veículos (T e qualidade): entram todos os veículos com ao menos esta
# quantidade de notícias com T. Os que ainda não chegaram ao mínimo da especificação
# (P["min_n"]) aparecem marcados como amostra pequena; o T ajustado da agregação já
# os puxa para a média geral. O ranking de editorias segue o mínimo da especificação.
MIN_N_VEICULOS = 1
# "Todas as análises": a página lista as mais recentes (com até 60 análises por
# edição, o histórico inteiro deixaria a página com vários MB); as demais ficam na
# Pesquisa analítica, que busca em todas, com o filtro de tipo Termômetro e o CSV. Os
# números da página (médias, séries, rankings) seguem calculados sobre todo o histórico.
LISTA_MAX = 1200
PESQUISA_ANALISES = "pesquisa.html?tipo=a"
# Lente do texto: os desvios (T diferente de zero) e os textos mais inclinados.
RECENTES_DIAS = 7  # "últimas sete edições": análises feitas nos sete dias até a edição
INCLINADOS = 4  # textos mais inclinados com a citação
RECORDES = 3  # os mais inclinados de todo o histórico, em uma linha
Z_LADO = 2.58  # teste de sinal dos desvios (esquerda × direita): 99%


# ── views ────────────────────────────────────────────────────────────────────


@dataclass
class TileView:
    rotulo: str
    valor: str
    detalhe: str = ""


@dataclass
class LeituraView:
    """Uma leitura de "para onde estamos indo" (tendência ou desvio). Quando passa do acaso,
    ganha ``mudanca``: a mesma conta numa frase, para a leitura da página e o resumo do bloco."""

    titulo: str
    leitura: str
    texto: str
    classe: str  # "esq" | "dir" | "est" | "ins" | "melhor" | "pior"
    mudanca: MudancaTexto | None = None
    p: float | None = None  # a chance do resultado sob o acaso (do intervalo de 95%); None sem dados


@dataclass
class PuxadorView:
    nome: str
    contribuicao: str
    inclinacao: str
    pauta: str
    leitura: str


@dataclass
class PontoView:
    left: float
    top: float
    title: str


@dataclass
class SerieView:
    titulo: str
    aria: str
    pontos: list[PontoView]
    linha: str  # path em coordenadas 0-1000 (preserveAspectRatio="none")
    faixa: str  # polígono do intervalo de 95%
    grade: list[tuple[float, str]]  # (top %, rótulo)
    eixo_x: list[tuple[float, str]]  # (left %, rótulo)
    zero: float | None = None  # top % da linha do zero (séries de T)


@dataclass
class RankItemView:
    nome: str
    valor: str
    pct: float
    ic_left: float | None
    ic_width: float | None
    rotulo: str
    classe: str
    detalhe: str
    title: str
    pequena: bool = False  # menos notícias com T que o mínimo da especificação (P["min_n"])


@dataclass
class RankingView:
    titulo: str
    itens: list[RankItemView]
    escala: list[tuple[float, str]]
    zero: float
    fora: str
    nota: str = ""  # como ler o ranking (veículos com amostra pequena)


@dataclass
class QualidadeItemView:
    nome: str
    valor: str
    pct: float
    rotulo: str
    detalhe: str
    pequena: bool = False


@dataclass
class LinhaTabelaView:
    nome: str
    href: str | None
    textos: int
    validas: int
    valor: str
    rotulo: str
    classe: str
    q: str


@dataclass
class MesView:
    rotulo: str
    analises: list[AnaliseView]
    aberto: bool = False


@dataclass
class SinalVeiculoView:
    nome: str
    textos: int
    com_sinal: int
    pct: str
    barra: float


@dataclass
class ResumoView:
    """O resultado do termômetro em linguagem clara (a resposta e o que ela quer dizer)
    e, para "Onde estamos", onde aparecem os sinais: por critério e lado (o mesmo
    painel do Termômetro do dia, sobre todo o histórico), os veículos com tratamento
    desigual e o checklist da qualidade (só das análises que guardam os itens)."""

    titulo: str
    frases: list[str]
    sinais: SinaisView
    veiculos: list[SinalVeiculoView]
    desiguais: int  # notícias com sinal de tratamento desigual (à esquerda ou à direita)
    noticias: int
    checklist: ChecklistView | None = None


@dataclass
class DesvioView:
    """Uma coluna do histograma dos desvios: quantas notícias com este T."""

    t: int
    n: int
    altura: float  # % da coluna mais alta
    rotulo: str  # a contagem escrita sobre a coluna (só nas mais altas de cada lado)
    title: str


@dataclass
class DesviosView:
    """Os T diferentes de zero das notícias: de que tamanho são e para que lado caem. Quase
    todas as notícias ficam exatamente em zero; o que sobra é pequeno, mas tem lado."""

    esquerda: list[DesvioView]  # de −dominio a −1 (ou em faixas), da ponta para o centro
    direita: list[DesvioView]  # de +1 a +dominio
    dominio: int
    passo: int  # largura de cada coluna, em pontos de T
    f1: int  # o limite da faixa de equilíbrio (±f1)
    zeros: int
    esq: int
    dir: int
    noticias: int
    dentro: int  # desvios dentro da faixa de equilíbrio
    faixa: float  # % de cada braço ocupada pela faixa de equilíbrio (do centro para fora)
    alem_do_acaso: bool
    titulo: str
    leitura: str
    aria: str


@dataclass
class InclinadoView:
    """Um texto entre os mais inclinados, com o sinal que mais pesou."""

    a: AnaliseView
    sinal: EvidenciaView | None
    outros: int  # sinais além do mostrado


@dataclass
class PartesView:
    """Quantas notícias em cada faixa da qualidade da apuração (uma barra em três partes)."""

    partes: list[tuple[str, str, int, float, str]]  # (classe, rótulo, n, largura %, parcela)
    total: int
    aria: str


@dataclass
class BaseView:
    """Um bloco da base completa: o título e o que tem dentro, em uma linha."""

    resumo: str
    aberto: bool = False


@dataclass
class AnaliticoView:
    vazio: bool
    periodo: str
    intro: str
    gauge: GaugeView | None
    resumo: str
    distribuicao: DistribuicaoView | None
    tiles: list[TileView]
    leituras: list[LeituraView]
    puxadores: list[tuple[str, list[PuxadorView]]]
    series: list[SerieView]
    series_msg: str
    rankings: list[RankingView]
    qualidade: list[QualidadeItemView]
    qualidade_fora: str
    eixos: list[LinhaTabelaView]
    dias: list[LinhaTabelaView]
    meses: list[MesView]
    metodo: list[tuple[str, str]]  # como ler: (título, texto)
    versao: str = PARAMETROS["versao"]
    atualizado: str = ""
    em_resumo: ResumoView | None = None
    lista_fora: int = 0  # análises do histórico fora da lista (as mais antigas; estão na Pesquisa)
    lista_total: int = 0
    pesquisa_href: str = PESQUISA_ANALISES
    base: str = ""  # "média de 373 notícias com dimensão política" (no placar)
    # A leitura e as três lentes
    leitura: achados.LeituraView | None = None
    placar: PlacarView | None = None
    matriz: MatrizView | None = None
    lados: LadosView | None = None
    desvios: DesviosView | None = None
    inclinados: list[InclinadoView] = field(default_factory=list)
    inclinados_titulo: str = ""
    recordes: list[AnaliseView] = field(default_factory=list)  # os mais inclinados do histórico, fora os de cima
    opiniao: list[AnaliseView] = field(default_factory=list)  # os textos de opinião mais inclinados (à parte)
    q_partes: PartesView | None = None
    q_firmes: list[QualidadeItemView] = field(default_factory=list)  # veículos com amostra
    q_pequenas: list[QualidadeItemView] = field(default_factory=list)
    q_minimo: int = P["min_n"]  # notícias com T para o veículo ter amostra
    ordens: dict[str, int] = field(default_factory=dict)  # o número de cada lente presente (manchetes, lados, texto)
    indo: BaseView = field(default_factory=lambda: BaseView(""))
    bases: dict[str, str] = field(default_factory=dict)  # resumo de cada bloco da base completa


# ── auxiliares ───────────────────────────────────────────────────────────────


def _dominio(valores: Iterable[float | None]) -> int:
    maior = max((abs(v) for v in valores if v is not None), default=0)
    return next((d for d in DOMINIOS if maior <= d), 100)


def _pos(valor: float, dominio: float) -> float:
    valor = max(-dominio, min(dominio, valor))
    return round((valor + dominio) / (2 * dominio) * 100, 2)


def _escala(dominio: int) -> list[tuple[float, str]]:
    meio = dominio // 2 if dominio > 25 else None
    marcas = [-dominio, *( [-meio] if meio else []), 0, *([meio] if meio else []), dominio]
    return [(_pos(m, dominio), fmt_t(m)) for m in marcas]


def _data_label(iso: str) -> str:
    try:
        return text.pt_short_date(date.fromisoformat(iso[:10]))
    except ValueError:
        return iso


def _mes_label(chave: str) -> str:
    try:
        ano, mes = chave.split("-")
        return f"{text.MONTHS_PT[int(mes) - 1].capitalize()} de {ano}"
    except (ValueError, IndexError):
        return chave


def _classe_t(leitura: str) -> str:
    return {
        "rumo à esquerda": "esq",
        "mudou para a esquerda": "esq",
        "rumo à direita": "dir",
        "mudou para a direita": "dir",
        "estável": "est",
        "dentro do histórico": "est",
        "melhorando": "melhor",
        "melhorou": "melhor",
        "piorando": "pior",
        "piorou": "pior",
    }.get(leitura, "ins")


# ── blocos ───────────────────────────────────────────────────────────────────


def _tendencias(linhas: list[dict[str, Any]], inicio: str, fim: str) -> list[LeituraView]:
    t = agregacao.tendencia(linhas, inicio, fim)
    leituras = []
    for chave, titulo, fmt_val, unidade in (
        ("T", "Tendência do termômetro", fmt_dec, "ponto"),
        ("Q", "Tendência da qualidade da apuração", fmt_dec, "ponto"),
    ):
        r = t[chave]
        if r["leitura"] == "dados insuficientes":
            texto = (
                f"{contagem(r['meses'], 'mês', 'meses')} com {P['min_n_mes']} ou mais análises; a tendência pede "
                f"{P['min_meses_tendencia']}."
            )
        else:
            texto = (
                f"{fmt_val(r['por_mes'])} {unidade}s por mês (intervalo de 95%: {fmt_ic(r['ic95'], fmt_dec)}); "
                f"{fmt_val(r['variacao_no_periodo'])} de {_mes_label(r['de']).lower()} a {_mes_label(r['ate']).lower()}, "
                f"em {contagem(r['meses'], 'mês', 'meses')}."
            )
        view = LeituraView(titulo, r["leitura"].capitalize(), texto, _classe_t(r["leitura"]))
        if r["leitura"] != "dados insuficientes":
            view.p = _p_do_intervalo(r["por_mes"], r["ic95"])
        if view.classe in MUDOU:
            quem = "O texto das notícias anda" if chave == "T" else "A qualidade da apuração está"
            meses = f"de {_mes_label(r['de']).lower()} a {_mes_label(r['ate']).lower()}"
            view.mudanca = MudancaTexto(
                frase=f"{quem} {r['leitura']}: {_pontos(r['por_mes'])} por mês, {meses}",
                texto=texto,
                numero=f"{fmt_dec(r['por_mes'])}/mês",
                forca=abs(r["variacao_no_periodo"]),
                medida=chave,
                p=view.p if view.p is not None else 1.0,
            )
        leituras.append(view)
    return leituras


def _desvio(linhas: list[dict[str, Any]], inicio: str, fim: str) -> tuple[list[LeituraView], tuple, tuple, bool]:
    recente, base = agregacao.janelas_padrao(inicio, fim)
    d = agregacao.desvio(linhas, recente, base)
    janela = f"{_data_label(recente[0])} a {_data_label(recente[1])}"
    base_txt = f"{_data_label(base[0])} a {_data_label(base[1])}" if base[0] <= base[1] else "sem base"
    leituras = []
    tem_dados = d["T"]["leitura"] != "dados insuficientes"
    for chave, titulo, fmt_val in (("T", "Janela recente × histórico: termômetro", fmt_t), ("Q", "Janela recente × histórico: qualidade", fmt_q)):
        r = d[chave]
        if r["leitura"] == "dados insuficientes":
            texto = (
                f"{r['n_recente']} análise(s) de {janela} e {r['n_base']} de {base_txt}; "
                f"a comparação pede {P['min_n']} em cada janela."
            )
        else:
            antes, agora = _par(r["base"], r["recente"], chave)
            texto = (
                f"{janela}: {agora}; {base_txt}: {antes}. Diferença de "
                f"{fmt_dec(r['diferenca'])} (intervalo de 95%: {fmt_ic(r['ic95'], fmt_dec)})."
            )
        view = LeituraView(titulo, r["leitura"].capitalize(), texto, _classe_t(r["leitura"]))
        if r["leitura"] != "dados insuficientes":
            view.p = _p_do_intervalo(r["diferenca"], r["ic95"])
        if view.classe in MUDOU:
            dias = (date.fromisoformat(recente[1]) - date.fromisoformat(recente[0])).days + 1
            if chave == "T":
                frase = f"O texto das notícias {r['leitura']} nos últimos {dias} dias: T {agora}, contra {antes} antes"
            else:
                frase = f"A qualidade da apuração {r['leitura']} nos últimos {dias} dias: {agora} de 100, contra {antes} antes"
            view.mudanca = MudancaTexto(
                frase=frase, texto=texto, numero=f"{antes} → {agora}", forca=abs(r["diferenca"]), medida=chave,
                p=view.p if view.p is not None else 1.0,
            )
        leituras.append(view)
    return leituras, recente, base, tem_dados


MUDOU = ("esq", "dir", "melhor", "pior")  # as leituras que passam do acaso


def _pontos(valor: float) -> str:
    """"+0,8 ponto", "−2,4 pontos" (plural a partir de 2, como se escreve em português)."""
    return f"{fmt_dec(valor)} {'ponto' if abs(round(valor, 1)) < 2 else 'pontos'}"


def _par(antes: float, agora: float, medida: str) -> tuple[str, str]:
    """Os dois valores de uma comparação, inteiros como no resto da página, salvo quando a
    distância entre os inteiros não seria a diferença arredondada ("87 → 84" ao lado de uma
    diferença de −2,3, ou "84 contra 84" para uma diferença real): aí, com uma casa."""
    inteiro = fmt_t if medida == "T" else fmt_q
    if arredondar(agora) - arredondar(antes) == arredondar(agora - antes) and inteiro(antes) != inteiro(agora):
        return inteiro(antes), inteiro(agora)
    sinal = medida == "T"
    return fmt_dec(antes, sinal=sinal), fmt_dec(agora, sinal=sinal)


def _p_do_intervalo(estimativa: float, ic: list[float] | None) -> float | None:
    """A chance (dois lados) de uma estimativa tão longe de zero sob o acaso, tirada do
    intervalo de 95% da agregação (o erro padrão é a meia largura ÷ 1,96)."""
    if not ic:
        return None
    erro = (ic[1] - ic[0]) / (2 * P["z"])
    return 1.0 if erro <= 0 else erfc(abs(estimativa) / erro / sqrt(2))


def _puxadores(linhas: list[dict[str, Any]], recente: tuple, base: tuple) -> list[tuple[str, list[PuxadorView]]]:
    saida = []
    for por, titulo in (("editoria", "Por editoria"), ("veiculo", "Por veículo")):
        px = agregacao.puxadores(linhas, por, recente, base)
        itens = [
            PuxadorView(
                nome=filters.plain(str(g["nome"])),
                contribuicao=fmt_dec(g["contribuicao_T"]),
                inclinacao=fmt_dec(g["efeito_inclinacao"]),
                pauta=fmt_dec(g["efeito_pauta"]),
                leitura=g["T"]["leitura"],
            )
            for g in px["grupos"][:PUXADORES_MAX]
        ]
        if itens:
            saida.append((titulo, itens))
    return saida


def _serie_t(linhas: list[dict[str, Any]], inicio: str, fim: str) -> SerieView | None:
    semanas = agregacao.serie(linhas, inicio, fim, "semana")
    if not semanas:
        return None
    dominio = _dominio([v for s in semanas for v in (s["ic95_T"] or [s["media_T"]])])
    total = len(semanas)
    pontos, sup, inf = [], [], []
    for i, s in enumerate(semanas):
        left = round(i / (total - 1) * 100, 2) if total > 1 else 50.0
        top = round(100 - _pos(s["media_T"], dominio), 2)
        ic = s["ic95_T"] or [s["media_T"], s["media_T"]]
        sup.append((left, round(100 - _pos(ic[1], dominio), 2)))
        inf.append((left, round(100 - _pos(ic[0], dominio), 2)))
        pontos.append(
            PontoView(
                left,
                top,
                f"Semana de {_data_label(s['periodo'])}: T {fmt_t(s['media_T'])} ({s['rotulo']}), "
                f"{contagem(s['n'], 'notícia', 'notícias')}",
            )
        )
    linha = " ".join(f"{'M' if i == 0 else 'L'}{p.left * 10:.1f},{p.top * 10:.1f}" for i, p in enumerate(pontos))
    faixa = (
        " ".join(f"{'M' if i == 0 else 'L'}{x * 10:.1f},{y * 10:.1f}" for i, (x, y) in enumerate(sup))
        + " "
        + " ".join(f"L{x * 10:.1f},{y * 10:.1f}" for x, y in reversed(inf))
        + " Z"
    )
    grade = [(round(100 - p, 2), r) for p, r in _escala(dominio)]
    rotulos_x = [0, total // 2, total - 1] if total > 2 else list(range(total))
    eixo_x = [(pontos[i].left, _data_label(semanas[i]["periodo"])) for i in dict.fromkeys(rotulos_x)]
    ultimo = semanas[-1]
    aria = (
        f"Média semanal de T das notícias, {total} semana(s); última: {fmt_t(ultimo['media_T'])} "
        f"({ultimo['rotulo']}). Escala de {fmt_t(-dominio)} a {fmt_t(dominio)}."
    )
    return SerieView(
        "Termômetro por semana (notícias)", aria, pontos, linha, faixa, grade, eixo_x, zero=round(100 - _pos(0, dominio), 2)
    )


def _serie_q(linhas: list[dict[str, Any]], inicio: str, fim: str) -> SerieView | None:
    semanas = [s for s in agregacao.serie(linhas, inicio, fim, "semana") if s["media_Q"] is not None]
    if not semanas:
        return None
    total = len(semanas)
    pontos, sup, inf = [], [], []
    for i, s in enumerate(semanas):
        left = round(i / (total - 1) * 100, 2) if total > 1 else 50.0
        ic = s["ic95_Q"] or [s["media_Q"], s["media_Q"]]
        sup.append((left, round(100 - max(0, min(100, ic[1])), 2)))
        inf.append((left, round(100 - max(0, min(100, ic[0])), 2)))
        pontos.append(
            PontoView(
                left,
                round(100 - max(0, min(100, s["media_Q"])), 2),
                f"Semana de {_data_label(s['periodo'])}: qualidade {fmt_q(s['media_Q'])} ({s['rotulo_Q']})",
            )
        )
    linha = " ".join(f"{'M' if i == 0 else 'L'}{p.left * 10:.1f},{p.top * 10:.1f}" for i, p in enumerate(pontos))
    faixa = (
        " ".join(f"{'M' if i == 0 else 'L'}{x * 10:.1f},{y * 10:.1f}" for i, (x, y) in enumerate(sup))
        + " "
        + " ".join(f"L{x * 10:.1f},{y * 10:.1f}" for x, y in reversed(inf))
        + " Z"
    )
    fraca, boa = P["qualidade_faixas"]
    grade = [(0.0, "100"), (round(100 - boa, 2), str(boa)), (round(100 - fraca, 2), str(fraca)), (100.0, "0")]
    rotulos_x = [0, total // 2, total - 1] if total > 2 else list(range(total))
    eixo_x = [(pontos[i].left, _data_label(semanas[i]["periodo"])) for i in dict.fromkeys(rotulos_x)]
    aria = f"Qualidade média da apuração por semana, {total} semana(s); última: {fmt_q(semanas[-1]['media_Q'])} de 100."
    return SerieView("Qualidade da apuração por semana (0 a 100)", aria, pontos, linha, faixa, grade, eixo_x)


def _ranking(
    linhas: list[dict[str, Any]], inicio: str, fim: str, por: str, titulo: str, *, min_n: int | None = None
) -> tuple[RankingView, dict]:
    """Ranking da agregação (T ajustado). ``min_n``: mínimo de notícias com T para
    entrar (padrão: o da especificação); abaixo dele, o grupo entra marcado como
    amostra pequena."""
    minimo = P["min_n"] if min_n is None else max(1, min_n)
    r = agregacao.ranking(linhas, inicio, fim, por=por, p={**P, "min_n": minimo})
    dominio = _dominio(
        [v for i in r["ranking"] for v in ([i["T_ajustado"]] + list(i["ic95_T"] or []))]
    )
    itens = []
    for i in r["ranking"]:
        ic = i["ic95_T"]
        left = _pos(ic[0], dominio) if ic else None
        nome = filters.plain(str(i["nome"]))
        pequena = i["n"] < P["min_n"]
        itens.append(
            RankItemView(
                nome=nome,
                valor=fmt_t(i["T_ajustado"]),
                pct=_pos(i["T_ajustado"], dominio),
                ic_left=left,
                ic_width=round(max(0.6, _pos(ic[1], dominio) - left), 2) if ic else None,
                rotulo=i["rotulo"],
                classe=CLASSE.get(i["rotulo"], "eq"),
                detalhe=(
                    f"{contagem(i['n'], 'notícia', 'notícias')} · média bruta {fmt_t(i['media_T'])}"
                    + (" · amostra pequena" if pequena else "")
                ),
                title=(
                    f"{nome}: T ajustado {fmt_t(i['T_ajustado'])} ({i['rotulo']}); média {fmt_t(i['media_T'])}"
                    + (f", intervalo de 95% {fmt_ic(ic)}" if ic else "")
                    + f"; {contagem(i['n'], 'notícia', 'notícias')}"
                    + (f" (amostra pequena: menos de {P['min_n']})" if pequena else "")
                ),
                pequena=pequena,
            )
        )
    fora = ""
    if r["fora_do_ranking"]:
        nomes = ", ".join(f"{filters.plain(str(f['nome']))} ({f['n']})" for f in r["fora_do_ranking"])
        if minimo == 1:
            fora = f"Sem notícia com T (só opinião, textos sem dimensão política ou inconclusivos): {nomes}."
        else:
            fora = f"Fora do ranking (menos de {minimo} notícias com T): {nomes}."
    nota = ""
    if any(item.pequena for item in itens):
        nota = (
            f"Todos os veículos com notícia analisada entram; os que têm menos de {P['min_n']} notícias com T "
            "aparecem esmaecidos (amostra pequena): o T ajustado os puxa para a média geral, então ficam perto do "
            "centro até acumularem textos."
        )
    return RankingView(titulo, itens, _escala(dominio), _pos(0, dominio), fora, nota), r


def _qualidade(r: Mapping[str, Any]) -> list[QualidadeItemView]:
    return [
        QualidadeItemView(
            nome=filters.plain(str(i["nome"])),
            valor=fmt_q(i["Q_ajustado"]),
            pct=round(max(0.0, min(100.0, i["Q_ajustado"])), 2),
            rotulo=i["rotulo_Q"],
            detalhe=(
                f"média bruta {fmt_q(i['media_Q'])} em {contagem(i['n_Q'], 'texto', 'textos')}"
                + (" · amostra pequena" if i["n"] < P["min_n"] else "")
            ),
            pequena=i["n"] < P["min_n"],
        )
        for i in r["ranking_qualidade"]
    ]


def _linha_tabela(nome: str, s: Mapping[str, Any], href: str | None = None) -> LinhaTabelaView:
    return LinhaTabelaView(
        nome=nome,
        href=href,
        textos=s["n_total"],
        validas=s["n"],
        valor=fmt_t(s["media_T"]),
        rotulo=s["rotulo"] or "—",
        classe=CLASSE.get(s["rotulo"] or "", "na"),
        q=fmt_q(s["media_Q"]),
    )



# ── em resumo ────────────────────────────────────────────────────────────────

MIN_TEXTOS_VEICULO = 10  # textos do veículo para a lista de "onde aparecem os sinais"


def _em_resumo(
    analises: list[Mapping[str, Any]], geral: Mapping[str, Any], *, outras_lentes: bool = True
) -> ResumoView | None:
    """O que o termômetro diz, sem jargão: a resposta, o que ela quer dizer e onde
    estão os sinais (das notícias com T; opinião à parte). Os sinais são contados como
    no Termômetro do dia: as evidências conferidas no texto, à esquerda, à direita ou
    de equilíbrio (contraponto ouvido, número com fonte), que não move o T."""
    noticias = [
        a for a in analises
        if agregacao.tipo_de(a) == "noticia" and a.get("estado") == "ok" and a.get("T") is not None
    ]
    n = len(noticias)
    if not n:
        return None
    rotulos = Counter(a.get("rotulo") or "" for a in noticias)
    equilibrado = rotulos.get("Equilibrado", 0)
    sinais = _sinais([dict(a) for a in noticias])

    def desigual(a: Mapping[str, Any]) -> bool:
        return any(
            isinstance(e, dict) and e.get("direcao") in ("esquerda", "direita") for e in a.get("evidencias") or []
        )

    desiguais = sum(1 for a in noticias if desigual(a))
    media = geral.get("media_T")
    frases = []
    if equilibrado >= 0.8 * n:
        titulo = "O texto das notícias sai equilibrado: o termômetro não encontra viés editorial relevante."
        frases.append(
            f"Em {text.format_number_pt(equilibrado, 0)} das {text.format_number_pt(n, 0)} notícias analisadas "
            f"({porcento_txt(equilibrado, n)}) o texto ficou “Equilibrado”: os sinais de tratamento desigual que o "
            "termômetro procura — rótulos, adjetivos, verbos, escolha e espaço das fontes, números, enquadramento — "
            "foram fracos ou não apareceram."
        )
    else:
        lado = _lado(media)
        titulo = "O termômetro encontra viés editorial em parte das notícias, " + (
            f"em média {lado}." if lado else "sem lado na média."
        )
        frases.append(
            f"{text.format_number_pt(n - equilibrado, 0)} das {text.format_number_pt(n, 0)} notícias analisadas "
            f"({porcento_txt(n - equilibrado, n)}) saíram fora de “Equilibrado”; a média é T {fmt_t(media or 0)}."
        )
    if sinais.esq or sinais.dir:
        frase = (
            f"Só {text.format_number_pt(desiguais, 0)} {'notícia' if desiguais == 1 else 'notícias'} "
            f"({porcento_txt(desiguais, n)}) tiveram sinal de tratamento desigual confirmado no texto: "
            f"{text.format_number_pt(sinais.esq, 0)} {'sinal' if sinais.esq == 1 else 'sinais'} para a esquerda e "
            f"{text.format_number_pt(sinais.dir, 0)} para a direita"
        )
        if sinais.eq:
            frase += (
                f"; outros {text.format_number_pt(sinais.eq, 0)} foram de equilíbrio (contraponto ouvido, número "
                "com fonte, verbo neutro), que não movem o T"
            )
        frases.append(frase + ". Um sinal isolado move pouco o T de um texto, por isso quase todos continuam “Equilibrado”.")
    por_veiculo = Counter(a.get("veiculo") or "" for a in noticias)
    if por_veiculo:
        topo = por_veiculo.most_common(2)
        soma = sum(k for _v, k in topo)
        if soma >= 0.5 * n and len(por_veiculo) > 2:
            frases.append(
                "A amostra é concentrada: "
                + " e ".join(f"{filters.plain(v)} ({porcento_txt(k, n)})" for v, k in topo)
                + f" somam {porcento_txt(soma, n)} das notícias, porque só entram textos com o corpo completo da "
                "matéria disponível (150 palavras ou mais); comparações entre veículos menores ainda pedem cautela."
            )
    if equilibrado >= 0.8 * n:
        frases.append(
            "O que isso quer dizer: medido pelo texto, os veículos relatam de forma parecida e neutra. Se há "
            "diferença entre eles, ela está antes do texto — no que cada um escolhe cobrir, quanto espaço dá a cada "
            "nome, em que contexto o nome aparece nas manchetes e que enfoque cada assunto ganha"
            + (": é o que as outras lentes desta página medem." if outras_lentes else ".")
        )

    com_sinal_v = Counter(a.get("veiculo") or "" for a in noticias if desigual(a))
    veiculos = [
        SinalVeiculoView(
            nome=filters.plain(v), textos=k, com_sinal=com_sinal_v[v], pct=porcento_txt(com_sinal_v[v], k),
            barra=round(100 * com_sinal_v[v] / k, 1),
        )
        for v, k in sorted(por_veiculo.items(), key=lambda kv: (-com_sinal_v[kv[0]] / kv[1], -kv[1], kv[0]))
        if k >= MIN_TEXTOS_VEICULO
    ]
    return ResumoView(
        titulo, frases, sinais, veiculos, desiguais, n, checklist=_checklist_do_dia([dict(a) for a in noticias])
    )

# ── lente do texto: desvios, textos mais inclinados e apuração ─────────────────


def _noticias_com_t(analises: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        a for a in analises
        if agregacao.tipo_de(a) == "noticia" and a.get("estado") == "ok" and isinstance(a.get("T"), (int, float))
    ]


def _passa_do_acaso(maior: int, total: int) -> bool:
    """Teste de sinal: com ``total`` desvios, ``maior`` de um lado só passa do que uma moeda
    daria (99%, dois lados)? Conta exata até mil desvios; acima disso, a aproximação normal."""
    if total < 1 or maior * 2 <= total:
        return False
    if total > 1000:
        return (2 * maior - total) / total**0.5 >= Z_LADO
    from math import comb

    cauda = sum(comb(total, k) for k in range(maior, total + 1)) / 2**total
    return 2 * cauda < 0.01


def _desvios(noticias: list[Mapping[str, Any]]) -> DesviosView | None:
    """O histograma dos T diferentes de zero das notícias e o teste de sinal dos lados."""
    valores = [arredondar(a["T"]) for a in noticias]
    fora = [v for v in valores if v]
    if not fora:
        return None
    f1 = P["faixas"][0]
    dominio = next((d for d in DOMINIOS if max(abs(v) for v in fora) <= d), 100)
    passo = dominio // 25
    colunas: Counter[int] = Counter()  # ±k: a k-ésima coluna de cada lado, do centro para fora
    for v in fora:
        k = -(-abs(v) // passo)
        colunas[k if v > 0 else -k] += 1
    topo = max(colunas.values())
    esq, dir_ = sum(1 for v in fora if v < 0), sum(1 for v in fora if v > 0)

    def coluna(k: int, lado: int) -> DesvioView:
        n = colunas[lado * k]
        de, ate = (k - 1) * passo + 1, k * passo
        faixa = fmt_t(lado * ate) if passo == 1 else f"{fmt_t(lado * de)} a {fmt_t(lado * ate)}"
        mais_alta = n and n == max(colunas[lado * j] for j in range(1, 26))
        return DesvioView(
            t=lado * ate,
            n=n,
            altura=round(100 * n / topo, 2) if n else 0.0,
            rotulo=str(n) if mais_alta else "",
            title=f"T {faixa}: {contagem(n, 'notícia', 'notícias')}",
        )

    maior, menor = max(esq, dir_), min(esq, dir_)
    alem = _passa_do_acaso(maior, esq + dir_)
    lado = "à esquerda" if esq > dir_ else "à direita"
    dentro = sum(1 for v in fora if abs(v) <= f1)
    k = len(fora)
    if alem:
        titulo = f"Quando o texto sai do zero, pende mais {lado}: {maior} {'notícia' if maior == 1 else 'notícias'} contra {menor}"
    elif esq == dir_:
        titulo = f"Os desvios do zero são poucos e sem lado: {esq} à esquerda, {dir_} à direita"
    else:
        titulo = f"Os desvios do zero são poucos e sem lado que passe do acaso: {esq} à esquerda, {dir_} à direita"
    leitura = (
        f"Das {contagem(len(valores), 'notícia', 'notícias')} com T, {text.format_number_pt(len(valores) - k, 0)} "
        f"ficaram exatamente em zero. Das {text.format_number_pt(k, 0)} que saíram do zero, "
        f"{text.format_number_pt(esq, 0)} penderam à esquerda e {text.format_number_pt(dir_, 0)} à direita"
        + (": a diferença passa do acaso (99%)." if alem else ": diferença dentro do que o acaso explica.")
        + (" Os desvios são pequenos: " if 2 * dentro >= k else " Só ")
        + f"{text.format_number_pt(dentro, 0)} {'fica' if dentro == 1 else 'ficam'} dentro da faixa de equilíbrio "
        f"(até ±{f1})."
    )
    return DesviosView(
        esquerda=[coluna(k_, -1) for k_ in range(25, 0, -1)],
        direita=[coluna(k_, 1) for k_ in range(1, 26)],
        dominio=dominio,
        passo=passo,
        f1=f1,
        zeros=len(valores) - k,
        esq=esq,
        dir=dir_,
        noticias=len(valores),
        dentro=dentro,
        faixa=round(100 * f1 / dominio, 2),
        alem_do_acaso=alem,
        titulo=titulo,
        leitura=leitura,
        aria=(
            f"Notícias com T diferente de zero: {esq} à esquerda e {dir_} à direita; "
            + "; ".join(f"T {fmt_t(v)}: {n}" for v, n in sorted(Counter(fora).items()))
        ),
    )


def _feita_desde(a: Mapping[str, Any], corte: str) -> bool:
    return str(a.get("analisado_em") or a.get("data_publicacao") or "")[:10] >= corte


def _mais_inclinados(lista: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Do T mais longe de zero para o mais perto; no empate, o texto mais recente."""
    com_t = [a for a in lista if arredondar(a["T"])]
    com_t.sort(key=lambda a: str(a.get("veiculo") or ""))
    com_t.sort(key=lambda a: str(a.get("data_publicacao") or ""), reverse=True)
    com_t.sort(key=lambda a: -abs(a["T"]))
    return com_t


def _inclinados(
    analises: list[Mapping[str, Any]], noticias: list[Mapping[str, Any]], fim: str
) -> tuple[list[InclinadoView], str, list[AnaliseView], list[AnaliseView], Mapping[str, Any] | None]:
    """Os textos mais inclinados (com o sinal que mais pesou em cada um), os recordes do
    histórico, a opinião à parte e a análise mais inclinada das últimas edições."""
    try:
        corte = (date.fromisoformat(fim[:10]) - timedelta(days=RECENTES_DIAS - 1)).isoformat()
    except ValueError:
        corte = ""
    todos = _mais_inclinados(noticias)
    recentes = [a for a in todos if _feita_desde(a, corte)] if corte else []
    if len(recentes) >= 2:
        escolhidos, titulo = recentes[:INCLINADOS], "Os textos mais inclinados das últimas sete edições"
        recordes = [a for a in todos if a not in escolhidos][:RECORDES]
    else:
        escolhidos, titulo, recordes, recentes = todos[:INCLINADOS], "Os textos mais inclinados do histórico", [], []
    views = []
    for a in escolhidos:
        v = analise_view(a)
        sinal = next((e for e in v.evidencias if e.seta != "eq"), v.evidencias[0] if v.evidencias else None)
        views.append(InclinadoView(a=v, sinal=sinal, outros=max(0, len(v.evidencias) - 1) if sinal else 0))
    opiniao = [
        a for a in analises
        if agregacao.tipo_de(a) == "opiniao" and a.get("estado") == "ok" and isinstance(a.get("T"), (int, float))
    ]
    return (
        views,
        titulo,
        [analise_view(a, evidencias=False) for a in recordes],
        [analise_view(a, evidencias=False) for a in _mais_inclinados(opiniao)[:RECORDES]],
        recentes[0] if recentes else None,
    )


def _q_partes(noticias_q: list[float]) -> PartesView | None:
    """Quantas notícias em cada faixa da qualidade da apuração (as faixas da agregação)."""
    if not noticias_q:
        return None
    contagens = Counter(agregacao.rotulo_Q(q, P) for q in noticias_q)
    total = len(noticias_q)
    partes = [
        (classe, rotulo, contagens[rotulo], round(100 * contagens[rotulo] / total, 2), porcento_txt(contagens[rotulo], total))
        for classe, rotulo in (("boa", "Boa"), ("regular", "Regular"), ("fraca", "Fraca"))
        if contagens[rotulo]
    ]
    return PartesView(
        partes=partes,
        total=total,
        aria="Qualidade da apuração das notícias: " + "; ".join(f"{r}: {n} ({s})" for _c, r, n, _w, s in partes),
    )


def _indo(leituras: list[LeituraView]) -> BaseView:
    """Para onde estamos indo, em uma linha; o bloco abre sozinho quando algo passou do acaso."""
    mudou = [r.mudanca for r in leituras if r.mudanca is not None]
    if mudou:
        return BaseView(" ".join(f"{m.frase}." for m in sorted(mudou, key=lambda m: -m.forca)), aberto=True)
    partes = []
    tendencia = [r for r in leituras if r.titulo.startswith("Tendência")]
    janela = [r for r in leituras if not r.titulo.startswith("Tendência")]
    if tendencia:
        if all(r.classe == "ins" for r in tendencia):
            partes.append("tendência mensal ainda sem base")
        else:
            partes.append("tendência mensal estável")
    if janela:
        if all(r.classe == "ins" for r in janela):
            partes.append("janela recente ainda sem base para comparar")
        else:
            partes.append("janela recente dentro do histórico")
    return BaseView("Nada mudou além do acaso: " + " e ".join(partes) + "." if partes else "")


def _concentracao(noticias: list[Mapping[str, Any]]) -> str:
    """"Amostra concentrada em A e B (69% das notícias)", quando dois veículos somam metade ou mais."""
    por_veiculo = Counter(a.get("veiculo") or "" for a in noticias)
    topo = por_veiculo.most_common(2)
    soma = sum(k for _v, k in topo)
    if len(por_veiculo) > 2 and soma >= 0.5 * len(noticias):
        return (
            "Amostra concentrada em "
            + " e ".join(filters.plain(v) for v, _k in topo)
            + f" ({porcento_txt(soma, len(noticias))} das notícias)."
        )
    return ""


# ── view completa ────────────────────────────────────────────────────────────


def _metodo(nm: DestaquesView | None, ld: LadosView | None) -> list[tuple[str, str]]:
    """Como ler a página: uma entrada por lente (as das manchetes e do enquadramento só
    aparecem com os dados delas) e as do termômetro, com os parâmetros da agregação."""
    itens = [
        (
            "A leitura",
            "A manchete e os achados saem dos números das lentes, um achado por tipo. Só entra o que passa do acaso "
            "contando todas as comparações do mesmo tipo feitas na página (correção de Holm): com 12 nomes ou dezenas "
            "de pares veículo × nome, uma ou outra diferença passaria do acaso só por sorte. Cada tipo parte de um "
            "peso fixo e ganha peso com o tamanho do efeito (pontos de tom ou de espaço, distância do acaso) e, nos "
            "achados sobre um nome, com o espaço dele nas manchetes; o de maior peso vira a manchete. A linha fina é "
            "sempre o que o termômetro diz do texto.",
        )
    ]
    if nm is not None:
        itens += [
            (
                "Manchetes · espaço",
                "Os nomes saem das manchetes em português (palavras com inicial maiúscula) e são contados como no "
                "analítico do nome na Pesquisa: com a regra da busca, que acha o começo das palavras (“Senado” também "
                "conta “senador”), e a notícia coletada em mais de uma edição conta uma vez. Um nome pode reunir "
                "pessoas diferentes (“Bolsonaro”). Espaço: a parte das manchetes da editoria que cita o nome. A "
                "variação compara as últimas 7 edições com as 7 anteriores (com 10 ou mais manchetes do nome nas "
                "duas); a marca escura aparece quando a diferença passa do acaso (duas proporções, 99%) mesmo contando "
                "os nomes comparados, e a variação passa de 15%.",
            ),
            (
                "Manchetes · tom",
                "O tom é lido por palavras, sem IA, em cada manchete brasileira que cita o nome: palavras de contexto "
                "desfavorável (escândalo, investigação, derrota, crise, crítica recebida…) ou favorável (vitória, "
                "apoio, aprovação, liderança…) contam para o nome mais próximo delas no título; ações contra alguém "
                "(“critica”, “acusa”, “condena”…) põem o nome que vem depois como alvo. O saldo é favoráveis menos "
                "desfavoráveis (−100 a +100). Mede o contexto em que o nome aparece, não a opinião do veículo: o que "
                "diz algo é a comparação entre veículos sobre o mesmo nome. A leitura por palavras erra em parte das "
                "manchetes (ironia, negação, o nome errado na frase); por isso o dossiê mostra as manchetes de exemplo "
                "com as palavras que pesaram.",
            ),
            (
                "Manchetes · veículos",
                "“Mais crítico” e “mais favorável” aparecem quando a diferença para os demais veículos passa do que o "
                "acaso explica naquela comparação (99%; 10+ manchetes do veículo), como na Pesquisa; na leitura, só "
                "entram as que passam também contando todas as comparações veículo × nome. Na matriz, o ponto marca "
                "menos de 10 manchetes. "
                "“vs. esperado”: manchetes do veículo sobre o nome ÷ as que ele teria se desse ao nome o mesmo espaço "
                "que o conjunto dá, pelo que publica na editoria. Padrão semanal: semanas (de 7 dias, até a última "
                "edição) com 3+ manchetes do veículo e 10+ dos demais; “padrão” quando o veículo fica do mesmo lado em "
                "3 de cada 4 delas. O tom “piorou” ou “melhorou” quando as últimas 7 edições diferem das 7 anteriores "
                "além do acaso (95%; 20+ manchetes em cada); a marca escura, quando isso vale contando os nomes "
                "comparados.",
            ),
        ]
    if ld is not None:
        itens.append(
            (
                "Enquadramento",
                f"Entram os assuntos de {ld.secao_titulo} que dois ou mais veículos cobriram, nas matérias da edição "
                "e nos demais assuntos da cobertura comparada. Os lados são os dois enfoques de cada assunto, não “a "
                "favor” ou “contra” alguém; um assunto rachou quando teve veículos nos dois. Divergência = 2 × o lado "
                "menor ÷ (A + B): 100 = meio a meio. Em “quem fica com quem” e “quem destoa”, a contagem é comparada "
                "com a que o acaso daria dado o placar de cada assunto (os lados embaralhados entre os veículos que "
                "tomaram lado) e só vira afirmação quando passa de 99% contando todos os pares (ou veículos) "
                "comparados. Um assunto acompanhado em várias edições conta uma vez por edição.",
            )
        )
    itens += [
        (
            "Texto · o termômetro",
            f"Cada texto compilado nas edições (nas matérias e na cobertura comparada) passa pelo Termômetro de Viés "
            f"Editorial (versão {P['versao']} do cálculo): "
            "a IA aponta sinais com a citação literal e o cálculo confere cada citação, aplica os pesos dos 11 critérios "
            "e dá T de −100 (esquerda) a +100 (direita) e a qualidade da apuração Q de 0 a 100.",
        ),
        (
            "Texto · o que entra na média",
            "Médias, séries e rankings são das notícias; opinião (colunas, editoriais, análises) fica à parte. Um texto "
            "sem dimensão política ou inconclusivo conta nos totais, mas não na média de T.",
        ),
        (
            "Texto · os desvios",
            "Os desvios são as notícias com T diferente de zero. O teste de sinal pergunta se o número das que pendem "
            "para um lado passa do que uma moeda daria (99%). Vale para os textos analisados, não para tudo o que "
            "cada veículo publica: só entram textos com o corpo completo disponível (150 palavras ou mais), e os "
            "sinais são apontados por IA e conferidos pelo cálculo (a citação tem de estar no texto).",
        ),
        (
            "Texto · rankings",
            f"Ranking: editorias com {P['min_n']} ou mais notícias com T; nos veículos (termômetro e qualidade) entram "
            f"todos os que têm notícia com T, e os com menos de {P['min_n']} ficam marcados como amostra pequena. O T "
            f"ajustado puxa a média do grupo para a média geral com peso {P['peso_media_geral']}, o que segura grupos "
            "pequenos perto do centro. Intervalos de 95%.",
        ),
        (
            "Texto · para onde vamos",
            f"Para onde estamos indo: tendência mensal (regressão de todas as análises sobre o mês; meses com "
            f"{P['min_n_mes']}+ análises, mínimo de {P['min_meses_tendencia']} meses); janela recente dos últimos "
            f"{P['janela_recente_dias']} dias (no máximo metade do histórico) comparada ao resto; os puxadores dividem a "
            "mudança entre inclinação (o grupo mudou) e pauta (o grupo ganhou ou perdeu espaço).",
        ),
        (
            "Limites",
            "Mede o tratamento dado pelo texto (verbos, fontes, rótulos, enquadramento…), não a veracidade dos fatos "
            "nem a intenção do veículo.",
        ),
    ]
    return itens


def _mudancas(leituras: list[LeituraView]) -> list[MudancaTexto]:
    """As tendências e desvios que passaram do acaso, da maior mudança para a menor; ``forte``
    quando passam também contando todos os testes de tendência e de janela feitos (Holm, 95%)."""
    familia = {i: r.p for i, r in enumerate(leituras) if r.p is not None}
    fortes = holm(familia, 1 - P_CONFIANCA)
    saida = []
    for i, r in enumerate(leituras):
        if r.mudanca is not None:
            r.mudanca.forte = i in fortes
            saida.append(r.mudanca)
    return sorted(saida, key=lambda m: -m.forca)


P_CONFIANCA = 0.95  # o nível dos intervalos da agregação (P["z"] = 1,96)


def _lado(media: float | None) -> str:
    """O lado da média, como ela aparece escrita: sem lado quando, arredondada, é zero."""
    if media is None or arredondar(media) == 0:
        return ""
    return "à esquerda" if media < 0 else "à direita"


def _texto_resumo(
    analises: list[Mapping[str, Any]],
    noticias: list[Mapping[str, Any]],
    geral: Mapping[str, Any],
    resumo: ResumoView | None,
    desvios: DesviosView | None,
    leituras: list[LeituraView],
    qualidade: list[QualidadeItemView],
    inclinado: Mapping[str, Any] | None,
) -> TextoResumo:
    """O que a lente do texto entrega aos achados (:mod:`qijournal.render.achados`)."""
    n = len(noticias)
    equilibrado = sum(1 for a in noticias if a.get("rotulo") == "Equilibrado")
    media = geral.get("media_T")
    tx = TextoResumo(
        textos=len(analises),
        veiculos=len({a["veiculo"] for a in analises}),
        noticias=n,
        equilibrado=equilibrado,
        desiguais=resumo.desiguais if resumo else 0,
        pct_equilibrado=porcento_txt(equilibrado, n),
        pct_desiguais=porcento_txt(resumo.desiguais, n) if resumo else "0%",
        esq=resumo.sinais.esq if resumo else 0,
        dir=resumo.sinais.dir if resumo else 0,
        media=fmt_t(media),
        rotulo=str(geral.get("rotulo") or ""),
        lado=_lado(media),
        q=fmt_q(geral.get("media_Q")),
        q_rotulo=str(geral.get("rotulo_Q") or ""),
        mudancas=_mudancas(leituras),
        n_mudancas=sum(1 for r in leituras if r.p is not None),
        q_veiculos=[(q.nome, q.valor) for q in qualidade if not q.pequena],
        concentracao=_concentracao(noticias),
    )
    if desvios is not None:
        tx.lado_esq, tx.lado_dir, tx.lado_dentro, tx.lado_alem = desvios.esq, desvios.dir, desvios.dentro, desvios.alem_do_acaso
    if inclinado is not None and inclinado.get("rotulo") not in ("Equilibrado", None, ""):
        v = analise_view(inclinado)
        sinal = next((e for e in v.evidencias if e.seta != "eq"), None)
        tx.inclinado = InclinadoResumo(
            t=float(inclinado["T"]),
            valor=v.valor,
            rotulo=v.rotulo,
            veiculo=v.veiculo,
            titulo=v.titulo,
            criterio=sinal.criterio if sinal else "",
            citacao=sinal.citacao if sinal else "",
        )
    return tx


def analitico_view(
    registros: Iterable[Mapping[str, Any]],
    *,
    edicoes: Iterable[str] = (),
    edition_date: str | None = None,
    destaques: DestaquesView | None = None,
    enquadramento: LadosView | None = None,
) -> AnaliticoView:
    """A página inteira: a leitura, as três lentes e a base do termômetro (``registros``: um
    por dia, de :mod:`qijournal.termometro.historico`). ``edicoes``: datas com edição
    arquivada (link do dia na tabela). ``destaques``: os nomes em destaque das manchetes
    (:mod:`qijournal.render.midia`); ``enquadramento``: onde os veículos racharam
    (:mod:`qijournal.render.lados`). As duas lentes são opcionais: sem elas, a página é a
    do termômetro."""
    registros = list(registros)
    nm = destaques if destaques is not None and destaques.nomes else None
    lentes = {
        "placar": achados.placar(nm) if nm else None,
        "matriz": achados.matriz(nm) if nm else None,
        "lados": enquadramento,
        "ordens": achados.ordens(nm, enquadramento),
    }
    analises = [a for a in todas_validas(registros) if a["versao_calculo"] == P["versao"]]
    metodo = _metodo(nm, enquadramento)
    if not analises:
        return AnaliticoView(
            vazio=True, periodo="", intro="Nenhuma análise no histórico ainda.", gauge=None, resumo="",
            distribuicao=None, tiles=[], leituras=[], puxadores=[], series=[], series_msg="", rankings=[],
            qualidade=[], qualidade_fora="", eixos=[], dias=[], meses=[], metodo=metodo,
            atualizado=_data_label(edition_date) if edition_date else "",
            leitura=achados.leitura(
                nm, enquadramento, None, periodo=f"edições de {nm.periodo}" if nm else "",
                tem_matriz=lentes["matriz"] is not None,
            ),
            **lentes,
        )

    linhas = agregacao.acumular(analises)
    datas = sorted(a["data_publicacao"][:10] for a in analises)
    inicio, fim = datas[0], max(datas[-1], (edition_date or datas[-1])[:10])
    geral = agregacao.resumo(linhas)
    tudo = agregacao.resumo(linhas, incluir_opiniao=True)
    opiniao = agregacao.resumo([l for l in linhas if l["tipo"] == "opiniao"], incluir_opiniao=True)
    veiculos = len({a["veiculo"] for a in analises})
    editorias = len({a["editoria"] for a in analises})
    dias_com = len({a["data_publicacao"][:10] for a in analises})

    medidor = gauge(geral["media_T"], geral["ic95_T"], geral["rotulo"], descricao="Média de todo o histórico (notícias)")
    base_media = f"{contagem(geral['n'], 'notícia', 'notícias')} com dimensão política"
    resumo = (
        f"{medidor.rotulo} · T {medidor.valor} na média de {base_media}"
        if medidor
        else "Ainda não há notícia com T (só textos sem dimensão política ou inconclusivos)"
    )
    tiles = [
        TileView("Textos analisados", text.format_number_pt(tudo["n_total"], 0), f"{veiculos} veículos · {editorias} editorias · {dias_com} dias"),
        TileView(
            "Notícias com T",
            text.format_number_pt(geral["n"], 0),
            f"{geral['n_nao_se_aplica']} sem dimensão política · {geral['n_inconclusivo']} inconclusivas",
        ),
        TileView(
            "Qualidade da apuração",
            fmt_q(geral["media_Q"]),
            f"de 100 · {geral['rotulo_Q']}" if geral["rotulo_Q"] else "sem avaliação",
        ),
        TileView(
            "Opinião (à parte)",
            text.format_number_pt(opiniao["n_total"], 0),
            f"T médio {fmt_t(opiniao['media_T'])}" if opiniao["media_T"] is not None else "sem T",
        ),
    ]

    leituras = _tendencias(linhas, inicio, fim)
    desvios, recente, base, tem_desvio = _desvio(linhas, inicio, fim)
    leituras += desvios
    puxadores = _puxadores(linhas, recente, base) if tem_desvio else []

    series = [s for s in (_serie_t(linhas, inicio, fim), _serie_q(linhas, inicio, fim)) if s]
    series_msg = (
        ""
        if series
        else f"As séries semanais começam quando uma semana reúne {P['min_n_semana']} ou mais notícias com T."
    )
    rank_v, r_v = _ranking(linhas, inicio, fim, "veiculo", "Veículos", min_n=MIN_N_VEICULOS)
    rank_e, _r_e = _ranking(linhas, inicio, fim, "editoria", "Editorias")
    qualidade_fora = "" if r_v["ranking_qualidade"] else "O ranking de qualidade começa com a primeira notícia com T."

    eixos = []
    for chave, nome in EIXOS.items():
        s = agregacao.resumo(linhas, eixo=chave)
        if s["n_total"]:
            eixos.append(_linha_tabela(nome, s))

    disponiveis = set(edicoes)
    # Os textos publicados num dia aparecem no Termômetro do dia da edição em que
    # foram analisados (em geral a do dia seguinte): o link vai para essa edição.
    edicao_do_dia: dict[str, Counter[str]] = {}
    for a in analises:
        if a.get("analisado_em") in disponiveis:
            edicao_do_dia.setdefault(a["data_publicacao"][:10], Counter())[a["analisado_em"]] += 1
    dias = []
    for dia in sorted({l["dia"] for l in linhas}, reverse=True):
        s = agregacao.resumo(linhas, dia, dia)
        s_tudo = agregacao.resumo(linhas, dia, dia, incluir_opiniao=True)
        edicao = edicao_do_dia[dia].most_common(1)[0][0] if dia in edicao_do_dia else None
        linha = _linha_tabela(_data_label(dia), s, f"{edicao}.html#termometro" if edicao else None)
        linha.textos = s_tudo["n_total"]
        dias.append(linha)

    por_mes: dict[str, list[dict[str, Any]]] = {}
    recentes = sorted(analises, key=lambda a: (a["data_publicacao"], a.get("veiculo") or ""), reverse=True)
    for a in recentes[:LISTA_MAX]:
        por_mes.setdefault(a["data_publicacao"][:7], []).append(a)
    meses = [
        MesView(f"{_mes_label(m)} · {contagem(len(lista), 'texto', 'textos')}", [analise_view(a, evidencias=False) for a in lista], aberto=i == 0)
        for i, (m, lista) in enumerate(por_mes.items())
    ]

    noticias = _noticias_com_t(analises)
    em_resumo = _em_resumo(analises, geral, outras_lentes=nm is not None or enquadramento is not None)
    desvios = _desvios(noticias)
    qualidade = _qualidade(r_v)
    inclinados, inclinados_titulo, recordes, opiniao_top, inclinado = _inclinados(analises, noticias, fim)
    semanas = agregacao.serie(linhas, inicio, fim, "semana")
    bases = {
        "faixas": " · ".join(
            f"{text.format_number_pt(k, 0)} {rotulo}"
            for rotulo, k in sorted(geral["rotulos"].items(), key=lambda kv: -kv[1])
            if k
        ),
        "series": (
            f"{contagem(len(semanas), 'semana', 'semanas')} · T médio de {fmt_t(min(s['media_T'] for s in semanas))} a "
            f"{fmt_t(max(s['media_T'] for s in semanas))}"
            if semanas
            else "ainda sem semana com notícias suficientes"
        ),
        "veiculos": (
            f"{contagem(len(rank_v.itens), 'veículo', 'veículos')} · T ajustado de {rank_v.itens[0].valor} a {rank_v.itens[-1].valor}"
            if rank_v.itens
            else "sem veículo no ranking ainda"
        ),
        "editorias": (
            f"{contagem(len(rank_e.itens), 'editoria', 'editorias')} · T ajustado de {rank_e.itens[0].valor} a {rank_e.itens[-1].valor}"
            if rank_e.itens
            else f"nenhuma editoria com {P['min_n']} notícias com T ainda"
        ),
        "qualidade": (
            f"{contagem(len(qualidade), 'veículo', 'veículos')} · de {qualidade[-1].valor} a {qualidade[0].valor}"
            if qualidade
            else ""
        ),
        "eixos": contagem(len(eixos), "eixo", "eixos"),
        "dias": contagem(len(dias), "dia de publicação", "dias de publicação"),
        "todas": contagem(len(recentes), "texto", "textos")
        + (f" · os {text.format_number_pt(LISTA_MAX, 0)} mais recentes aqui" if len(recentes) > LISTA_MAX else ""),
    }
    periodo = f"{_data_label(inicio)} a {_data_label(fim)}"
    tx = _texto_resumo(analises, noticias, geral, em_resumo, desvios, leituras, qualidade, inclinado)

    return AnaliticoView(
        vazio=False,
        periodo=periodo,
        intro=(
            f"Todo o histórico do termômetro: {contagem(tudo['n_total'], 'texto analisado', 'textos analisados')} "
            f"de {contagem(veiculos, 'veículo', 'veículos')}, publicados de {_data_label(inicio)} a {_data_label(fim)}."
        ),
        gauge=medidor,
        resumo=resumo,
        distribuicao=distribuicao(
            geral["rotulos"],
            fora=" · ".join(
                p
                for p in (
                    f"{geral['n_nao_se_aplica']} sem dimensão política" if geral["n_nao_se_aplica"] else "",
                    contagem(geral["n_inconclusivo"], "inconclusiva", "inconclusivas") if geral["n_inconclusivo"] else "",
                )
                if p
            ),
        ),
        base=f"média de {base_media}",
        tiles=tiles,
        leituras=leituras,
        puxadores=puxadores,
        series=series,
        series_msg=series_msg,
        rankings=[rank_v, rank_e],
        qualidade=qualidade,
        qualidade_fora=qualidade_fora,
        eixos=eixos,
        dias=dias,
        meses=meses,
        metodo=metodo,
        atualizado=_data_label(edition_date) if edition_date else "",
        em_resumo=em_resumo,
        lista_fora=max(0, len(recentes) - LISTA_MAX),
        lista_total=len(recentes),
        leitura=achados.leitura(
            nm, enquadramento, tx, periodo=f"edições de {nm.periodo}" if nm else f"textos de {periodo}",
            tem_matriz=lentes["matriz"] is not None,
        ),
        desvios=desvios,
        inclinados=inclinados,
        inclinados_titulo=inclinados_titulo,
        recordes=recordes,
        opiniao=opiniao_top,
        q_partes=_q_partes(
            [a["qualidade_apuracao"]["Q"] for a in noticias if isinstance((a.get("qualidade_apuracao") or {}).get("Q"), (int, float))]
        ),
        q_firmes=[q for q in qualidade if not q.pequena],
        q_pequenas=[q for q in qualidade if q.pequena],
        indo=_indo(leituras),
        bases=bases,
        **lentes,
    )


def render_analitico_page(
    registros: Iterable[Mapping[str, Any]],
    config: Config,
    *,
    home_href: str,
    archive_href: str,
    edition_date: str | None = None,
    edicoes: Iterable[str] = (),
    destaques: DestaquesView | None = None,
    enquadramento: LadosView | None = None,
) -> str:
    """Página ``edicoes/analitico.html`` (autocontida, como as demais). ``destaques``: os
    nomes em destaque da Política (:mod:`qijournal.render.midia`), a lente das manchetes;
    ``enquadramento``: onde os veículos racharam (:mod:`qijournal.render.lados`)."""
    view = analitico_view(
        registros, edicoes=edicoes, edition_date=edition_date, destaques=destaques, enquadramento=enquadramento
    )
    brand = brand_view(config)
    template = filters.environment().get_template("analitico.html.j2")
    html = template.render(
        a=view,
        nm=destaques if destaques is not None and destaques.nomes else None,
        brand=brand,
        home_href=filters.safe_href(home_href) or "../",
        archive_href=filters.safe_href(archive_href) or "./",
        base_url=config.site.base_url,
        repo_url=filters.safe_url(config.site.repo_url) or "",
    )
    log.info(
        "Analítico Político renderizado: %d texto(s) no histórico, %d KB",
        sum(len(m.analises) for m in view.meses),
        len(html.encode("utf-8")) // 1024,
    )
    return html
