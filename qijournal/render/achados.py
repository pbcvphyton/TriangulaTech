"""A leitura do Analítico Político: o que os dados têm a dizer, em ordem de importância.

A aba cruza três lentes, na ordem em que as diferenças entre os veículos aparecem:

1. **Manchetes** (:mod:`qijournal.render.midia`): de quem a imprensa fala e em que tom;
2. **Enquadramento** (:mod:`qijournal.render.lados`): em que assuntos os veículos racham
   e quem fica com quem;
3. **Texto** (:mod:`qijournal.termometro`): como as reportagens escrevem (o termômetro)
   e a qualidade da apuração.

Este módulo não calcula nada de novo sobre as notícias: lê o que as três lentes já
calcularam e decide **o que vai para o alto da página**. Cada achado é uma frase com o
número que a sustenta e o endereço da prova na própria página. Só entra o que passa do
acaso pelo critério de cada lente (os mesmos limiares das tabelas); o peso de um achado
é a força do efeito vezes a importância do nome (a parte das manchetes que ele ocupa),
de modo que a ordem pode ser refeita à mão a partir dos números mostrados.

Também monta as duas peças de leitura rápida da lente das manchetes: o **placar dos
nomes** (espaço, a curva das 30 edições, tom e o que mudou em sete edições) e a
**matriz veículo × nome** (o saldo do tom de cada veículo sobre cada nome).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import ceil, erfc, sqrt

from .. import text
from . import midia
from .lados import LadosView, holm
from .midia import DestaquesView, NomeView, VeiculoTomView

__all__ = [
    "AchadoView",
    "LeituraView",
    "LenteView",
    "MatrizView",
    "MudancaTexto",
    "PlacarView",
    "TextoResumo",
    "leitura",
    "matriz",
    "ordens",
    "placar",
]

ACHADOS = 6  # achados na abertura além da manchete (um por tipo, os de maior peso)
PISO_CURVA = 0.10  # a curva de um nome usa a escala dele, mas nunca menor que 10% das manchetes
MATRIZ_VEICULOS = 12  # veículos (linhas) da matriz
MATRIZ_MIN_NOMES = 2  # nomes com amostra (MIN_TOM_VEICULO manchetes) para o veículo entrar na matriz
DISTANCIA_MIN = 30  # manchetes do veículo sobre cada nome para ele ser citado como "a maior distância"
# Degraus do fundo de uma célula da matriz, pelo tamanho do saldo (em pontos).
NIVEIS = (5, 10, 20)
# O que vai para a leitura (e para as marcas escuras do placar) passa do acaso contando todas as
# comparações da mesma família feitas na página (correção de Holm), no nível de cada teste:
ALFA_TOM = 0.05  # a tendência do tom de cada nome é dita a 95% (midia.Z_TENDENCIA)
ALFA_ESPACO = 0.01  # o espaço de cada nome, a 99% (midia.Z_ESPACO)
ALFA_VEICULO = 0.01  # cada veículo contra os demais, sobre cada nome, a 99% (midia.Z_VEICULO)


# ── formatos ─────────────────────────────────────────────────────────────────


def _pct(valor: float | None) -> str:
    """Fração (0 a 1) como porcentagem: uma casa abaixo de 10% ("8,3%"), inteira acima."""
    if valor is None:
        return "—"
    v = 100 * valor
    return f"{text.format_number_pt(v, 1 if 0 < v < 10 else 0)}%"


def _saldo(valor: float | None) -> str:
    return midia._saldo(valor)


def _num(valor: int) -> str:
    return text.format_number_pt(valor, 0)


def _data(iso: str) -> str:
    try:
        return text.pt_short_date(date.fromisoformat(iso[:10]))
    except ValueError:
        return iso


def _lista(itens: list[str]) -> str:
    """"a", "a e b", "a, b e c"."""
    if len(itens) <= 1:
        return "".join(itens)
    return ", ".join(itens[:-1]) + " e " + itens[-1]


def _importancia(nome: NomeView, maior: float) -> float:
    """Quanto o nome pesa na pauta: raiz da parte das manchetes dele sobre a do nome mais citado."""
    return (nome.participacao_valor / maior) ** 0.5 if maior > 0 else 0.0


def _peso_tom(delta: float, importancia: float) -> float:
    """Peso de uma virada de tom: quantos pontos o saldo mudou (30 ou mais = força máxima)."""
    return 60 + 40 * min(1.0, delta / 30) * importancia


def _peso_espaco(pontos: float, importancia: float) -> float:
    """Peso de uma mudança de espaço: quantos pontos percentuais das manchetes (20 ou mais = força máxima)."""
    return 50 + 40 * min(1.0, pontos / 20) * importancia


def _p_normal(z: float) -> float:
    """A chance (dois lados) de um z tão extremo sob o acaso."""
    return erfc(abs(z) / sqrt(2))


@dataclass
class Significancia:
    """O que passa do acaso contando todas as comparações da família (Holm). As tabelas de
    detalhe (dossiê, matriz) mostram cada comparação isolada, como a Pesquisa; a leitura e as
    marcas escuras do placar só usam o que está aqui."""

    tom: set[str]  # nomes cuja tendência do tom passa do acaso
    espaco: set[str]  # nomes cujo espaço mudou além do acaso
    veiculos: set[tuple[str, str]]  # (nome, veículo): veículo diferente dos demais sobre o nome
    n_tom: int  # comparações feitas em cada família
    n_espaco: int
    n_veiculos: int


def significancia(nm: DestaquesView) -> Significancia:
    tom = {n.nome: _p_normal(n.tom.z_tendencia) for n in nm.nomes if n.tom is not None and n.tom.z_tendencia is not None}
    espaco = {n.nome: _p_normal(n.espaco.z) for n in nm.nomes if n.espaco is not None and n.espaco.z is not None}
    veiculos = {
        (n.nome, v.nome): _p_normal(v.z)
        for n in nm.nomes
        if n.tom is not None
        for v in n.tom.todos
        if v.z is not None
    }
    return Significancia(
        tom=holm(tom, ALFA_TOM),
        espaco=holm(espaco, ALFA_ESPACO),
        veiculos=holm(veiculos, ALFA_VEICULO),
        n_tom=len(tom),
        n_espaco=len(espaco),
        n_veiculos=len(veiculos),
    )


def _contando(k: int, coisas: str, como: str = "comparados") -> str:
    """", mesmo contando os 12 nomes comparados" (nada com uma comparação só)."""
    return f", mesmo contando os {_num(k)} {coisas} {como}" if k > 1 else ""


def _aspas(texto: str) -> str:
    """O texto entre aspas; se ele já traz aspas duplas, vão as simples por fora."""
    return f"‘{texto}’" if any(c in texto for c in "“”\"") else f"“{texto}”"


# ── placar dos nomes ─────────────────────────────────────────────────────────


@dataclass
class CurvaView:
    """A parte das manchetes da editoria que cita o nome, edição a edição (SVG de 100 × 100,
    esticado pela página). ``recente``: o trecho das últimas ``SEMANA`` edições."""

    linha: str
    recente: str
    fim: float  # altura (%) do último ponto, de cima para baixo
    title: str


@dataclass
class LinhaPlacar:
    ordem: int
    id: str  # âncora da linha (e do dossiê do nome)
    n: NomeView
    barra: float  # % da maior participação
    curva: CurvaView | None
    # espaço: últimas 7 edições × 7 anteriores
    esp_antes: str  # "37%"
    esp_agora: str  # "8,3%"
    esp_forte: bool  # a mudança passa do acaso
    esp_title: str
    # tom
    tom_neg: float  # largura (%) da meia barra das desfavoráveis
    tom_pos: float
    tom_antes: str  # "+22"
    tom_agora: str  # "−6"
    tom_antes_x: float | None  # posição (%) na escala de −50 a +50
    tom_agora_x: float | None
    tom_de: float  # o traço que liga o antes ao agora: começo (%) e largura (%)
    tom_larg: float
    tom_forte: bool  # o tom mudou além do acaso, contando os nomes comparados (a marca escura)
    tom_mudou: bool  # o tom mudou na comparação isolada do nome (o chip, claro quando não é forte)
    destoam: list[tuple[str, str, str]]  # (veículo, "mais crítico" | "mais favorável", "neg" | "pos")


@dataclass
class MudancaView:
    """Uma mudança que passou do acaso nas últimas sete edições (espaço ou tom de um nome)."""

    nome: str
    href: str  # a linha do nome no placar
    tipo: str  # "espaço" | "tom"
    antes: str
    agora: str
    selo: str  # "▼ 78%" | "piorou"
    classe: str  # neg | pos | "" (só o tom tem lado)
    peso: float


@dataclass
class PlacarView:
    linhas: list[LinhaPlacar]
    tom_escala: int  # cada meia barra do tom vai de 0 a tantos % das manchetes
    mudaram: int  # nomes em que o espaço ou o tom mudou além do acaso
    mudancas: list[MudancaView] = field(default_factory=list)  # as mudanças além do acaso, da maior para a menor


def _curva(n: NomeView, datas: list[str], secao_titulo: str) -> CurvaView | None:
    serie = n.serie
    if len(serie) < 2 or len(serie) != len(datas) or not any(serie):
        return None
    topo = max(max(serie), PISO_CURVA)
    ultimo = len(serie) - 1
    pontos = [(round(100 * i / ultimo, 2), round(94 - 88 * v / topo, 2)) for i, v in enumerate(serie)]

    def caminho(ps: list[tuple[float, float]]) -> str:
        return " ".join(f"{'M' if i == 0 else 'L'}{x:g},{y:g}" for i, (x, y) in enumerate(ps))

    pico = max(range(len(serie)), key=lambda i: (serie[i], i))
    return CurvaView(
        linha=caminho(pontos),
        recente=caminho(pontos[-midia.SEMANA :]) if len(serie) >= 2 * midia.SEMANA else "",
        fim=pontos[-1][1],
        title=(
            f"Parte das manchetes de {secao_titulo} que cita {n.nome}, edição a edição, de {_data(datas[0])} a "
            f"{_data(datas[-1])}: pico de {_pct(serie[pico])} em {_data(datas[pico])}; última edição, {_pct(serie[-1])}"
        ),
    )


def _x_tom(saldo: float | None) -> float | None:
    """Posição (0 a 100%) de um saldo na escala de −BARRA_TOM a +BARRA_TOM."""
    if saldo is None:
        return None
    return round(50 + max(-midia.BARRA_TOM, min(midia.BARRA_TOM, saldo)) * 50 / midia.BARRA_TOM, 2)


def placar(nm: DestaquesView, sig: Significancia | None = None) -> PlacarView:
    """Uma linha por nome em destaque, com o que a tabela dizia e o que faltava ver de
    relance: a curva das edições, o antes e depois do espaço e do tom, e quem destoa. As
    marcas escuras (e "o que mudou") são as mudanças que passam do acaso contando os nomes
    comparados (``sig``)."""
    sig = sig or significancia(nm)
    maior = max((n.participacao_valor for n in nm.nomes), default=0.0)
    partes = [
        100 * k / n.tom.manchetes
        for n in nm.nomes
        if n.tom is not None and n.tom.manchetes
        for k in (n.tom.n_desfavoraveis, n.tom.n_favoraveis)
    ]
    escala = max(20, 5 * ceil(max(partes, default=0.0) / 5))  # múltiplo de 5 que cobre a maior parte
    linhas = []
    for i, n in enumerate(nm.nomes, start=1):
        t, e = n.tom, n.espaco
        destoam = []
        if t is not None:
            for v in t.todos:
                if v.destaque:
                    destoam.append((v.nome, v.destaque, v.classe))
        antes_x = _x_tom(t.saldo_anterior) if t else None
        agora_x = _x_tom(t.saldo_recente) if t else None
        esp_forte = bool(e and n.nome in sig.espaco and n.tendencia_classe in ("sobe", "cai"))
        esp_title = n.tendencia_titulo
        if e is not None:
            if esp_forte:
                esp_title += ": a diferença passa do acaso" + _contando(sig.n_espaco, "nomes")
            elif n.nome in sig.espaco:
                esp_title += ": passa do acaso, mas é pequena (até 15%) e conta como estável"
            elif e.alem_do_acaso:
                esp_title += f": passa do acaso nesta comparação, mas não contando os {_num(sig.n_espaco)} nomes comparados"
            else:
                esp_title += ": diferença dentro do que o acaso explica"
        linhas.append(
            LinhaPlacar(
                ordem=i,
                id=f"qf-{i}",
                n=n,
                barra=round(100 * n.participacao_valor / maior, 1) if maior else 0.0,
                curva=_curva(n, nm.datas, nm.secao_titulo),
                esp_antes=_pct(e.anterior) if e else "",
                esp_agora=_pct(e.recente) if e else "",
                esp_forte=esp_forte,
                esp_title=esp_title,
                tom_neg=round(min(100.0, 100 * (100 * t.n_desfavoraveis / t.manchetes) / escala), 1) if t else 0.0,
                tom_pos=round(min(100.0, 100 * (100 * t.n_favoraveis / t.manchetes) / escala), 1) if t else 0.0,
                tom_antes=_saldo(t.saldo_anterior) if t and t.saldo_anterior is not None else "",
                tom_agora=_saldo(t.saldo_recente) if t and t.saldo_recente is not None else "",
                tom_antes_x=antes_x,
                tom_agora_x=agora_x,
                tom_de=min(antes_x, agora_x) if antes_x is not None and agora_x is not None else 0.0,
                tom_larg=round(abs(antes_x - agora_x), 2) if antes_x is not None and agora_x is not None else 0.0,
                tom_forte=bool(t and t.tendencia in ("piorou", "melhorou") and n.nome in sig.tom),
                tom_mudou=bool(t and t.tendencia in ("piorou", "melhorou")),
                destoam=destoam,
            )
        )
    mudancas = []
    for l in linhas:
        n, t, e = l.n, l.n.tom, l.n.espaco
        peso_nome = _importancia(n, maior)
        if l.esp_forte and e is not None:
            pontos = abs(e.recente - e.anterior) * 100
            mudancas.append(
                MudancaView(n.nome, f"#{l.id}", "espaço", l.esp_antes, l.esp_agora, n.tendencia, "", _peso_espaco(pontos, peso_nome))
            )
        if l.tom_forte and t is not None:
            delta = abs(t.saldo_recente - t.saldo_anterior)
            mudancas.append(
                MudancaView(
                    n.nome, f"#{l.id}", "tom", l.tom_antes, l.tom_agora, t.tendencia,
                    "neg" if t.tendencia == "piorou" else "pos", _peso_tom(delta, peso_nome),
                )
            )
    mudancas.sort(key=lambda m: -m.peso)
    return PlacarView(
        linhas=linhas,
        tom_escala=escala,
        mudaram=sum(1 for l in linhas if l.esp_forte or l.tom_forte),
        mudancas=mudancas,
    )


# ── matriz veículo × nome ────────────────────────────────────────────────────


@dataclass
class CelulaView:
    valor: str  # "−13" (vazio sem manchete)
    nivel: str  # n3 | n2 | n1 | z | p1 | p2 | p3 (o fundo); vazio sem cor
    pequena: bool  # menos de MIN_TOM_VEICULO manchetes: sem cor, esmaecida
    destaque: str  # "mais crítico" | "mais favorável" | ""
    classe: str  # neg | pos | ""
    title: str


@dataclass
class LinhaMatriz:
    veiculo: str
    manchetes: int  # manchetes do veículo que citam algum dos nomes da matriz
    celulas: list[CelulaView]


@dataclass
class MatrizView:
    nomes: list[tuple[str, str]]  # (nome, âncora do dossiê)
    geral: list[CelulaView]  # todos os veículos juntos (a referência de cada coluna)
    linhas: list[LinhaMatriz]
    fora: int  # veículos com menos manchetes, fora da matriz
    leitura: list[str]
    destaques: int  # células além do acaso


def _nivel(saldo: float | None) -> str:
    if saldo is None:
        return ""
    v = round(saldo)
    grau = sum(1 for corte in NIVEIS if abs(v) >= corte)
    return "z" if not grau else f"{'n' if v < 0 else 'p'}{grau}"


def _celula(nome: str, v: VeiculoTomView | None) -> CelulaView:
    if v is None:
        return CelulaView("", "", False, "", "", "")
    dica = (
        f"{v.nome} sobre {nome}: saldo {v.saldo} em {_num(v.manchetes)} "
        f"{'manchete' if v.manchetes == 1 else 'manchetes'} ({v.desfavoraveis} desfavoráveis, {v.favoraveis} favoráveis); "
        f"demais veículos: {v.demais}."
    )
    if v.pequena:
        dica += f" Amostra pequena: menos de {midia.MIN_TOM_VEICULO} manchetes."
    elif v.destaque:
        dica += f" {v.destaque.capitalize()} que os demais na comparação isolada (99%)."
    return CelulaView(
        valor="·" if v.pequena else v.saldo,  # com poucas manchetes o saldo é ruído: fica só na dica
        nivel="" if v.pequena else _nivel(v.saldo_valor),
        pequena=v.pequena,
        destaque=v.destaque,
        classe=v.classe,
        title=dica,
    )


def _distancia(a: NomeView, b: NomeView) -> tuple[int, int, int, str, int, bool] | None:
    """Nos veículos com amostra para os dois nomes, pelos saldos como aparecem escritos: em
    quantos ``a`` fica acima de ``b``, em quantos abaixo (os empates não contam para nenhum),
    de quantos, onde a distância é maior (veículo, pontos) e se ela foi procurada só entre os
    veículos com ``DISTANCIA_MIN`` manchetes ou mais sobre cada nome."""
    if a.tom is None or b.tom is None:
        return None
    de_b = {v.nome: v for v in b.tom.todos if not v.pequena and v.saldo_valor is not None}
    pares = [
        (v.nome, round(v.saldo_valor) - round(de_b[v.nome].saldo_valor), min(v.manchetes, de_b[v.nome].manchetes))
        for v in a.tom.todos
        if not v.pequena and v.saldo_valor is not None and v.nome in de_b
    ]
    if len(pares) < 3:
        return None
    # A maior distância sai dos veículos que mais falam dos dois (com poucas manchetes ela é ruído).
    firmes = [p for p in pares if p[2] >= DISTANCIA_MIN]
    onde, quanto, _k = max(firmes or pares, key=lambda p: (abs(p[1]), p[0]))
    acima = sum(1 for _v, d, _k in pares if d > 0)
    abaixo = sum(1 for _v, d, _k in pares if d < 0)
    return acima, abaixo, len(pares), onde, quanto, bool(firmes) and len(firmes) < len(pares)


def _frase_distancia(a: NomeView, b: NomeView) -> str:
    """"Lula aparece em contexto melhor que Flávio em 12 dos 12 veículos…; a maior distância…"."""
    d = _distancia(a, b)
    if d is None:
        return ""
    acima, abaixo, de, onde, quanto, filtrado = d
    melhor, pior, k = (a.nome, b.nome, acima) if acima >= abaixo else (b.nome, a.nome, abaixo)
    if not k:
        return ""
    quantos = f"em todos os {de} veículos" if k == de else f"em {k} dos {de} veículos"
    entre = f" entre os que têm {DISTANCIA_MIN} ou mais manchetes sobre cada um" if filtrado else ""
    return (
        f"{melhor} aparece em contexto melhor que {pior} {quantos} com {midia.MIN_TOM_VEICULO} ou mais manchetes sobre "
        f"cada um; a maior distância{entre}, {onde} ({_num(abs(quanto))} pontos)."
    )


def matriz(nm: DestaquesView, sig: Significancia | None = None) -> MatrizView | None:
    """O saldo do tom de cada veículo sobre cada nome com tom (20+ manchetes brasileiras). As
    linhas são os veículos com amostra (``MIN_TOM_VEICULO`` manchetes) para ``MATRIZ_MIN_NOMES``
    nomes ou mais e os que destoam em algum, os que mais falam primeiro; ``None`` sem nenhum."""
    com_tom = [(i, n) for i, n in enumerate(nm.nomes, start=1) if n.tom is not None]
    if not com_tom:
        return None
    por_nome = [{v.nome: v for v in n.tom.todos} for _i, n in com_tom]
    total: dict[str, int] = {}
    com_amostra: dict[str, int] = {}
    destoam: set[str] = set()
    for coluna in por_nome:
        for veiculo, v in coluna.items():
            total[veiculo] = total.get(veiculo, 0) + v.manchetes
            if not v.pequena:
                com_amostra[veiculo] = com_amostra.get(veiculo, 0) + 1
            if v.destaque:
                destoam.add(veiculo)
    # Entram os veículos com amostra para MATRIZ_MIN_NOMES nomes e os que destoam em algum; se
    # sobrarem menos de três (poucos nomes, veículos de um nome só), entram todos os com amostra.
    minimo = min(MATRIZ_MIN_NOMES, len(com_tom))
    linhas_de = {v for v, k in com_amostra.items() if k >= minimo} | destoam
    if len(linhas_de) < 3:
        linhas_de = set(com_amostra)
    por_volume = sorted(linhas_de, key=lambda v: (-total[v], midia.norm(v)))
    if not por_volume:
        return None
    # Quem destoa sempre entra (é a prova do contorno e dos achados); os demais completam pelo volume.
    vagas = max(0, MATRIZ_VEICULOS - len(destoam))
    completam = set([v for v in por_volume if v not in destoam][:vagas])
    ordem = [v for v in por_volume if v in destoam or v in completam]
    linhas = [
        LinhaMatriz(
            veiculo=veiculo,
            manchetes=total[veiculo],
            celulas=[_celula(n.nome, coluna.get(veiculo)) for (_i, n), coluna in zip(com_tom, por_nome)],
        )
        for veiculo in ordem
    ]
    geral = [
        CelulaView(
            valor=n.tom.saldo,
            nivel=_nivel(n.tom.saldo_valor),
            pequena=False,
            destaque="",
            classe="",
            title=(
                f"Todos os veículos sobre {n.nome}: saldo {n.tom.saldo} em {_num(n.tom.manchetes)} manchetes brasileiras "
                f"({n.tom.desfavoraveis} desfavoráveis, {n.tom.favoraveis} favoráveis)."
            ),
        )
        for _i, n in com_tom
    ]
    sig = sig or significancia(nm)
    frases = []
    if len(com_tom) >= 2 and nm.nomes[0].tom is not None and nm.nomes[1].tom is not None:
        frase = _frase_distancia(nm.nomes[0], nm.nomes[1])
        if frase:
            frases.append("Os dois nomes mais citados: " + frase)
    marcados = [(n, v) for _i, n in com_tom for v in n.tom.todos if v.destaque]
    if marcados:
        frases.append(
            "Marcados na tabela, cada um comparado sozinho com os demais veículos (99%): "
            + "; ".join(f"{v.nome} {v.destaque} a {n.nome} ({v.saldo}, contra {v.demais} nos demais)" for n, v in marcados)
            + "."
        )
        firmes = [f"{v.nome} sobre {n.nome}" for n, v in marcados if (n.nome, v.nome) in sig.veiculos]
        if firmes:
            frases.append(
                f"Contando todas as {_num(sig.n_veiculos)} comparações da tabela, continua de pé: {_lista(firmes)}."
            )
        elif sig.n_veiculos > 1:
            frases.append(
                f"Contando todas as {_num(sig.n_veiculos)} comparações da tabela, nenhuma dessas diferenças se sustenta: "
                "com tantas comparações, é de esperar que uma ou outra passe do acaso só por sorte."
            )
    else:
        frases.append(
            "Nenhum veículo destoa dos demais além do acaso: as diferenças de tom entre eles, nome a nome, cabem na "
            "variação esperada."
        )
    return MatrizView(
        nomes=[(n.nome, f"qf-{i}") for i, n in com_tom],
        geral=geral,
        linhas=linhas,
        fora=max(0, len(total) - len(linhas)),
        leitura=frases,
        destaques=len(marcados),
    )


# ── a leitura ────────────────────────────────────────────────────────────────


@dataclass
class MudancaTexto:
    """Uma tendência ou um desvio do termômetro que passou do acaso, numa frase (quem
    preenche é :mod:`qijournal.render.analitico`, com os números da agregação)."""

    frase: str  # "A qualidade da apuração piorou nos últimos 14 dias: 84 de 100, contra 87 antes"
    texto: str  # a conta completa, com o intervalo de 95%
    numero: str  # "87 → 84"
    forca: float  # o tamanho da mudança, em pontos (de T ou de Q)
    medida: str  # "T" | "Q"
    p: float = 1.0  # a chance dela sob o acaso (do intervalo de 95%)
    forte: bool = True  # passa do acaso contando os testes de tendência e de janela feitos (Holm)


@dataclass
class InclinadoResumo:
    t: float
    valor: str  # "+14"
    rotulo: str
    veiculo: str
    titulo: str
    criterio: str = ""  # do sinal que mais pesou
    citacao: str = ""


@dataclass
class TextoResumo:
    """O que a lente do texto (o termômetro) entrega aos achados; quem preenche é
    :mod:`qijournal.render.analitico`, com os números da agregação."""

    textos: int  # textos analisados (notícias e opinião)
    veiculos: int
    noticias: int  # notícias com T
    equilibrado: int
    desiguais: int  # notícias com sinal de tratamento desigual (à esquerda ou à direita)
    pct_equilibrado: str
    pct_desiguais: str
    esq: int  # sinais à esquerda
    dir: int
    media: str  # "0" | "−3"
    rotulo: str
    lado: str  # "à esquerda" | "à direita" (da média); "" quando a média, arredondada, é zero
    q: str  # "86" | "—"
    q_rotulo: str
    # tendências e desvios da janela recente que passaram do acaso, da maior mudança para a menor
    mudancas: list[MudancaTexto] = field(default_factory=list)
    n_mudancas: int = 0  # testes de tendência e de janela com dados (a família da correção)
    inclinado: InclinadoResumo | None = None  # o texto mais inclinado das últimas edições, fora de "Equilibrado"
    q_veiculos: list[tuple[str, str]] = field(default_factory=list)  # (veículo, Q) com amostra, do melhor ao pior
    # Os desvios: notícias com T diferente de zero, à esquerda e à direita; quantas dentro da faixa de
    # equilíbrio; e se a diferença de lado passa do acaso (teste de sinal).
    lado_esq: int = 0
    lado_dir: int = 0
    lado_dentro: int = 0
    lado_alem: bool = False
    concentracao: str = ""  # "Amostra concentrada em A e B (69% das notícias)."

    @property
    def equilibrio(self) -> bool:
        return self.noticias > 0 and self.equilibrado >= 0.8 * self.noticias


@dataclass
class AchadoView:
    # tom | espaco | veiculos | distancia | presenca | lados | texto | assimetria | inclinado | tendencia | apuracao
    tipo: str
    selo: str  # o rótulo curto da lente ("Tom", "Espaço"…)
    lente: int  # 1 manchetes | 2 enquadramento | 3 texto
    titulo: str  # a frase do achado, sem ponto final
    texto: str  # a prova, em uma linha
    numero: str  # o número de destaque
    href: str  # onde está a prova na página
    peso: float


@dataclass
class LenteView:
    ordem: int
    nome: str
    pergunta: str
    numero: str
    unidade: str
    base: str
    href: str


@dataclass
class LeituraView:
    chapeu: str
    manchete: str  # a frase do achado de maior peso
    linha_fina: str  # o que o termômetro diz do texto (ou a prova da manchete, quando ela é do texto)
    principal: AchadoView  # o achado da manchete: a prova e onde ela está
    achados: list[AchadoView]  # os demais, do mais forte para o mais fraco
    lentes: list[LenteView]


def _achado_presenca(nm: DestaquesView) -> AchadoView | None:
    """De quem a imprensa fala: sempre há o que dizer, mesmo sem nada que passe do acaso."""
    if not nm.nomes:
        return None
    a = nm.nomes[0]
    titulo = f"{a.nome} está em {a.participacao} das manchetes de {nm.secao_titulo}"
    if len(nm.nomes) > 1:
        titulo += f"; {nm.nomes[1].nome}, em {nm.nomes[1].participacao}"
    return AchadoView(
        tipo="presenca",
        selo="Presença",
        lente=1,
        titulo=titulo,
        texto=(
            f"Os nomes mais citados nas {_num(nm.manchetes)} manchetes de {nm.secao_titulo} das edições de {nm.periodo}: "
            + _lista([f"{n.nome} ({n.participacao})" for n in nm.nomes[:5]])
            + "."
        ),
        numero=a.participacao,
        href="#an-nomes",
        peso=20 + 15 * min(1.0, a.participacao_valor),
    )


def _achado_tom(nm: DestaquesView, maior: float, sig: Significancia) -> AchadoView | None:
    candidatos = []
    for i, n in enumerate(nm.nomes, start=1):
        t = n.tom
        if t is None or t.tendencia not in ("piorou", "melhorou") or t.saldo_recente is None or t.saldo_anterior is None:
            continue
        if n.nome not in sig.tom:
            continue
        delta = abs(t.saldo_recente - t.saldo_anterior)
        candidatos.append((_peso_tom(delta, _importancia(n, maior)), i, n))
    if not candidatos:
        return None
    peso, i, n = max(candidatos, key=lambda c: (c[0], -c[1]))
    t = n.tom
    antes, agora = _saldo(t.saldo_anterior), _saldo(t.saldo_recente)
    return AchadoView(
        tipo="tom",
        selo="Tom",
        lente=1,
        titulo=f"O tom das manchetes sobre {n.nome} {t.tendencia}: saldo de {antes} para {agora} em sete edições",
        texto=(
            f"Favoráveis menos desfavoráveis, em pontos: {antes} nas {_num(t.n_anterior)} manchetes brasileiras das "
            f"{midia.SEMANA} edições anteriores, {agora} nas {_num(t.n_recente)} das últimas {midia.SEMANA}. "
            f"A diferença passa do acaso{_contando(sig.n_tom, 'nomes')}."
        ),
        numero=f"{antes} → {agora}",
        href=f"#qf-{i}",
        peso=peso,
    )


def _frase_espaco(n: NomeView, secao_titulo: str) -> str:
    e = n.espaco
    antes, agora = _pct(e.anterior), _pct(e.recente)
    if not e.manchetes_anterior:
        return (
            f"{n.nome} entra nas manchetes: {agora} das manchetes de {secao_titulo} nas últimas sete edições, "
            "nenhuma nas sete anteriores"
        )
    if not e.manchetes_recente:
        return (
            f"{n.nome} sai das manchetes: nenhuma nas últimas sete edições, {antes} das manchetes de {secao_titulo} "
            "nas sete anteriores"
        )
    verbo = "ganha" if e.recente > e.anterior else "perde"
    return f"{n.nome} {verbo} espaço: de {antes} para {agora} das manchetes de {secao_titulo} em sete edições"


def _achado_espaco(nm: DestaquesView, maior: float, sig: Significancia) -> AchadoView | None:
    candidatos = []
    for i, n in enumerate(nm.nomes, start=1):
        e = n.espaco
        if e is None or n.nome not in sig.espaco or n.tendencia_classe not in ("sobe", "cai"):
            continue
        pontos = abs(e.recente - e.anterior) * 100
        candidatos.append((_peso_espaco(pontos, _importancia(n, maior)), i, n))
    if not candidatos:
        return None
    peso, i, n = max(candidatos, key=lambda c: (c[0], -c[1]))
    e = n.espaco
    texto = (
        f"{_num(e.manchetes_anterior)} {'manchete' if e.manchetes_anterior == 1 else 'manchetes'} nas {midia.SEMANA} "
        f"edições anteriores, {_num(e.manchetes_recente)} nas últimas {midia.SEMANA}; a diferença passa do acaso"
        f"{_contando(sig.n_espaco, 'nomes')}."
    )
    sobe = e.recente > e.anterior
    contrario = [c for c in candidatos if (c[2].espaco.recente > c[2].espaco.anterior) != sobe]
    if contrario:
        _p, _i, outro = max(contrario, key=lambda c: (c[0], -c[1]))
        o = outro.espaco
        texto += (
            f" No sentido contrário, {outro.nome} {'sobe' if o.recente > o.anterior else 'cai'} de {_pct(o.anterior)} "
            f"para {_pct(o.recente)}."
        )
    return AchadoView(
        tipo="espaco",
        selo="Espaço",
        lente=1,
        titulo=_frase_espaco(n, nm.secao_titulo),
        texto=texto,
        numero=f"{_pct(e.anterior)} → {_pct(e.recente)}",
        href=f"#qf-{i}",
        peso=peso,
    )


def _achado_veiculos(nm: DestaquesView, maior: float, prova: str, sig: Significancia) -> AchadoView | None:
    candidatos = []
    for i, n in enumerate(nm.nomes, start=1):
        if n.tom is None:
            continue
        marcados = [
            v for v in n.tom.todos if v.destaque and v.saldo_valor is not None and (n.nome, v.nome) in sig.veiculos
        ]
        if not marcados:
            continue
        critico = min((v for v in marcados if v.classe == "neg"), key=lambda v: v.saldo_valor, default=None)
        favoravel = max((v for v in marcados if v.classe == "pos"), key=lambda v: v.saldo_valor, default=None)
        forca = max(abs(v.saldo_valor - (v.demais_valor or 0.0)) for v in marcados)
        candidatos.append((40 + 40 * min(1.0, forca / 20) * _importancia(n, maior), i, n, critico, favoravel))
    if not candidatos:
        return None
    peso, i, n, critico, favoravel = max(candidatos, key=lambda c: (c[0], -c[1]))
    # O teste compara o veículo com os demais (não diz que ele é o mais crítico de todos).
    if critico and favoravel:
        titulo = (
            f"Sobre {n.nome}, {favoravel.nome} tem manchetes mais favoráveis que os demais veículos, e "
            f"{critico.nome}, mais críticas"
        )
        numero = f"{favoravel.saldo} × {critico.saldo}"
    elif critico:
        titulo = f"{critico.nome} tem manchetes mais críticas a {n.nome} que os demais veículos"
        numero = critico.saldo
    else:
        titulo = f"{favoravel.nome} tem manchetes mais favoráveis a {n.nome} que os demais veículos"
        numero = favoravel.saldo
    partes = [
        f"{v.nome}: saldo {v.saldo} em {_num(v.manchetes)} manchetes, contra {v.demais} nos demais veículos"
        for v in (favoravel, critico)
        if v is not None
    ]
    return AchadoView(
        tipo="veiculos",
        selo="Veículos",
        lente=1,
        titulo=titulo,
        texto="; ".join(partes) + f". Diferenças além do acaso{_contando(sig.n_veiculos, 'pares veículo × nome')}.",
        numero=numero,
        href=prova or f"#qf-{i}",
        peso=peso,
    )


def _achado_distancia(nm: DestaquesView, prova: str) -> AchadoView | None:
    com_tom = [n for n in nm.nomes if n.tom is not None]
    if len(nm.nomes) < 2 or nm.nomes[0].tom is None or nm.nomes[1].tom is None or len(com_tom) < 2:
        return None
    a, b = nm.nomes[0], nm.nomes[1]
    ta, tb = a.tom, b.tom
    notas_a = [-1] * ta.n_desfavoraveis + [1] * ta.n_favoraveis + [0] * (ta.manchetes - ta.n_desfavoraveis - ta.n_favoraveis)
    notas_b = [-1] * tb.n_desfavoraveis + [1] * tb.n_favoraveis + [0] * (tb.manchetes - tb.n_desfavoraveis - tb.n_favoraveis)
    z = midia.comparar(notas_a, notas_b)
    distancia = abs(round(ta.saldo_valor) - round(tb.saldo_valor))  # dos saldos como aparecem escritos
    if z is None or abs(z) < midia.Z_VEICULO or distancia < NIVEIS[0]:
        return None
    texto = f"Saldo do tom nas manchetes brasileiras de {nm.secao_titulo}: {a.nome} {ta.saldo}, {b.nome} {tb.saldo}."
    frase = _frase_distancia(a, b)
    if frase:
        texto += f" {frase}"
    return AchadoView(
        tipo="distancia",
        selo="Distância",
        lente=1,
        titulo=(
            f"{a.nome} {ta.saldo}, {b.nome} {tb.saldo}: {_num(distancia)} pontos separam o tom das manchetes "
            "sobre os dois nomes mais citados"
        ),
        texto=texto,
        numero=f"{_num(distancia)} pts",
        href=prova or "#an-quem",
        peso=40 + 30 * min(1.0, distancia / 30),
    )


def _achado_lados(ld: LadosView) -> AchadoView | None:
    if not ld.divididos:
        return None
    par = ld.par_destaque
    if par is not None and par.z is not None:
        if par.classe == "op":
            k = par.total - par.juntos
            titulo = f"{par.a} e {par.b} ficam em lados opostos: {k} de {par.total} assuntos divididos"
            numero = f"{k} de {par.total}"
        else:
            titulo = f"{par.a} e {par.b} enquadram do mesmo jeito: mesmo lado em {par.juntos} de {par.total} assuntos divididos"
            numero = f"{par.juntos} de {par.total}"
        acaso = f"{par.opostos} em lados opostos" if par.classe == "op" else f"{par.esperado} do mesmo lado"
        testados = len(ld.pares) + ld.pares_mais
        return AchadoView(
            tipo="lados",
            selo="Lados",
            lente=2,
            titulo=titulo,
            texto=(
                f"O acaso daria {acaso}; a diferença passa dele{_contando(testados, 'pares')}. No período, "
                f"{_num(ld.divididos)} dos {_num(ld.comparados)} assuntos de {ld.secao_titulo} comparados tiveram "
                "veículos nos dois lados."
            ),
            numero=numero,
            href="#an-pares",
            peso=55 + 30 * min(1.0, abs(par.z) / 4),
        )
    mais = (ld.recentes or ld.demais or [None])[0]
    testados = len(ld.pares) + ld.pares_mais
    if testados:
        texto = (
            "Nenhum par de veículos fica do mesmo lado ou em lados opostos além do que o acaso explica"
            f"{_contando(testados, 'pares')}."
        )
    else:
        texto = f"Nenhum par de veículos tem ainda {ld.min_par} assuntos divididos em comum para comparar com o acaso."
    if mais is not None:
        quando = "das últimas edições" if ld.recentes else "do período"
        texto = f"O mais dividido {quando}: {_aspas(mais.titulo)} ({len(mais.a)} × {len(mais.b)}). " + texto
    return AchadoView(
        tipo="lados",
        selo="Lados",
        lente=2,
        titulo=(
            f"Os veículos racharam em {_num(ld.divididos)} dos {_num(ld.comparados)} "
            f"{'assunto' if ld.comparados == 1 else 'assuntos'} de {ld.secao_titulo} que mais de um cobriu"
        ),
        texto=texto,
        numero=ld.pct_divididos,
        href="#an-lados",
        peso=45.0,
    )


def _achado_texto(tx: TextoResumo) -> AchadoView | None:
    if not tx.noticias:
        return None
    sinais = ""
    if tx.esq or tx.dir:
        sinais = (
            f" Só {_num(tx.desiguais)} ({tx.pct_desiguais}) tiveram sinal de tratamento desigual confirmado no texto: "
            f"{_num(tx.esq)} {'sinal' if tx.esq == 1 else 'sinais'} à esquerda e {_num(tx.dir)} à direita."
        )
    if tx.equilibrio:
        return AchadoView(
            tipo="texto",
            selo="Texto",
            lente=3,
            titulo="No texto das notícias, o termômetro não encontra viés editorial relevante",
            texto=(
                f"{_num(tx.equilibrado)} das {_num(tx.noticias)} notícias analisadas ({tx.pct_equilibrado}) ficaram em "
                f"“Equilibrado”." + sinais
            ),
            numero=tx.pct_equilibrado,
            href="#an-texto",
            peso=50.0,
        )
    fora = tx.noticias - tx.equilibrado
    return AchadoView(
        tipo="texto",
        selo="Texto",
        lente=3,
        titulo="O termômetro encontra viés editorial em parte das notícias, "
        + (f"em média {tx.lado}" if tx.lado else "sem lado na média"),
        texto=(
            f"{_num(fora)} das {_num(tx.noticias)} notícias analisadas saíram de “Equilibrado”; a média é T {tx.media} "
            f"({tx.rotulo})." + sinais
        ),
        numero=f"T {tx.media}",
        href="#an-texto",
        peso=90.0,
    )


def _achado_assimetria(tx: TextoResumo) -> AchadoView | None:
    """Os desvios do zero têm lado? Só entra quando o teste de sinal passa."""
    if not tx.lado_alem:
        return None
    maior, menor = max(tx.lado_esq, tx.lado_dir), min(tx.lado_esq, tx.lado_dir)
    total = maior + menor
    lado = "à esquerda" if tx.lado_esq > tx.lado_dir else "à direita"
    if 2 * tx.lado_dentro >= total:
        texto = (
            f"Os desvios são pequenos ({_num(tx.lado_dentro)} dos {_num(total)} dentro da faixa de equilíbrio), mas a "
            "diferença de lado passa do acaso."
        )
    else:
        texto = (
            f"A diferença de lado passa do acaso, e só {_num(tx.lado_dentro)} dos {_num(total)} desvios ficam dentro "
            "da faixa de equilíbrio."
        )
    if tx.concentracao:
        texto += f" {tx.concentracao}"
    return AchadoView(
        tipo="assimetria",
        selo="Texto",
        lente=3,
        titulo=(
            f"Quando o texto sai do zero, pende mais {lado}: {_num(maior)} "
            f"{'notícia' if maior == 1 else 'notícias'} contra {_num(menor)}"
        ),
        texto=texto,
        numero=f"{_num(maior)} × {_num(menor)}",
        href="#an-desvios",
        peso=45 + 25 * min(1.0, 2 * (maior - menor) / total),
    )


def _achado_tendencia(tx: TextoResumo) -> AchadoView | None:
    """A maior mudança do termômetro que passou do acaso. O peso cresce com o tamanho dela
    (10 pontos de T ou de Q = força máxima) e fica abaixo do viés no texto (90), que é a
    notícia maior quando existe."""
    fortes = [m for m in tx.mudancas if m.forte]
    if not fortes:
        return None
    m = max(fortes, key=lambda m: m.forca)
    return AchadoView(
        tipo="tendencia",
        selo="Apuração" if m.medida == "Q" else "Texto",
        lente=3,
        titulo=m.frase,
        texto=f"{m.texto} A diferença passa do acaso{_contando(tx.n_mudancas, 'testes de tendência', 'feitos')}.",
        numero=m.numero,
        href="#an-indo",
        peso=45 + 43 * min(1.0, m.forca / 10),
    )


def _achado_inclinado(tx: TextoResumo) -> AchadoView | None:
    if tx.inclinado is None:
        return None
    i = tx.inclinado
    texto = f"{_aspas(i.titulo)}."
    if i.citacao:
        texto += f" O sinal que mais pesou ({i.criterio[:1].lower() + i.criterio[1:]}): {_aspas(i.citacao)}."
    return AchadoView(
        tipo="inclinado",
        selo="Texto",
        lente=3,
        titulo=f"O texto mais inclinado das últimas sete edições: T {i.valor} ({i.rotulo.lower()}), em {i.veiculo}",
        texto=texto,
        numero=f"T {i.valor}",
        href="#an-pendeu",
        peso=35 + 30 * min(1.0, abs(i.t) / 30),
    )


def _achado_apuracao(tx: TextoResumo) -> AchadoView | None:
    if tx.q == "—":
        return None
    texto = f"{tx.q_rotulo} na média das notícias." if tx.q_rotulo else ""
    if len(tx.q_veiculos) >= 2:
        texto += " Entre os veículos com amostra: " + ", ".join(f"{v} {q}" for v, q in tx.q_veiculos) + "."
    return AchadoView(
        tipo="apuracao",
        selo="Apuração",
        lente=3,
        titulo=f"Qualidade da apuração: {tx.q} de 100 na média das notícias",
        texto=texto.strip(),
        numero=f"{tx.q}/100",
        href="#an-q",
        peso=30.0,
    )


def ordens(nm: DestaquesView | None, ld: LadosView | None) -> dict[str, int]:
    """O número de cada lente na página: só as presentes são numeradas, em sequência (a do
    texto está sempre; sem manchetes nem enquadramento, ela é a lente 1)."""
    presentes = [chave for chave, tem in (("manchetes", nm is not None and bool(nm.nomes)), ("lados", ld is not None)) if tem]
    return {chave: i for i, chave in enumerate([*presentes, "texto"], start=1)}


def _lentes(nm: DestaquesView | None, ld: LadosView | None, tx: TextoResumo | None) -> list[LenteView]:
    numero = ordens(nm, ld)
    lentes = []
    if nm is not None and nm.nomes:
        lentes.append(
            LenteView(
                ordem=numero["manchetes"],
                nome="Manchetes",
                pergunta="De quem a imprensa fala, e em que tom",
                numero=_num(nm.manchetes),
                unidade=f"{'manchete' if nm.manchetes == 1 else 'manchetes'} de {nm.secao_titulo}",
                base=(
                    f"{_num(nm.edicoes)} {'edição' if nm.edicoes == 1 else 'edições'} · {_num(nm.veiculos)} "
                    f"{'veículo' if nm.veiculos == 1 else 'veículos'}"
                ),
                href="#an-nomes",
            )
        )
    if ld is not None:
        lentes.append(
            LenteView(
                ordem=numero["lados"],
                nome="Enquadramento",
                pergunta="Onde os veículos racham, e quem fica com quem",
                numero=_num(ld.divididos),
                unidade=f"{'assunto rachou' if ld.divididos == 1 else 'assuntos racharam'} os veículos",
                base=f"de {_num(ld.comparados)} {'comparado' if ld.comparados == 1 else 'comparados'} em {ld.secao_titulo}",
                href="#an-lados",
            )
        )
    if tx is not None and tx.textos:
        lentes.append(
            LenteView(
                ordem=numero["texto"],
                nome="Texto",
                pergunta="Como as reportagens escrevem",
                numero=_num(tx.textos),
                unidade="texto no termômetro" if tx.textos == 1 else "textos no termômetro",
                base=(
                    f"T médio {tx.media} · {tx.rotulo}" + (f" · apuração {tx.q}/100" if tx.q != "—" else "")
                    if tx.noticias
                    else f"{_num(tx.veiculos)} {'veículo' if tx.veiculos == 1 else 'veículos'}"
                ),
                href="#an-texto",
            )
        )
    return lentes


def _antes_do_texto(achados: list[AchadoView], nm: DestaquesView | None, ld: LadosView | None) -> str:
    """O que a leitura achou antes do texto (só o que passou do acaso), numa frase; sem
    nada, diz isso, nas lentes que estão na página."""
    tipos = {a.tipo for a in achados}
    onde = []
    if "espaco" in tipos:
        onde.append("no espaço que cada nome ganha")
    if tipos & {"tom", "distancia"}:
        onde.append("no tom das manchetes sobre cada nome")
    if "veiculos" in tipos:
        onde.append("no tom de cada veículo")
    if ld is not None and ld.par_destaque is not None:
        onde.append("no enquadramento dos assuntos")
    if onde:
        return f" As diferenças aparecem antes do texto: {_lista(onde)}."
    lentes = [x for x, tem in (("nas manchetes", nm is not None and bool(nm.nomes)), ("no enquadramento", ld is not None)) if tem]
    return f" {_lista(lentes)[:1].upper()}{_lista(lentes)[1:]}, nada passa do acaso no período." if lentes else ""


def leitura(
    nm: DestaquesView | None,
    ld: LadosView | None,
    tx: TextoResumo | None,
    *,
    periodo: str = "",
    tem_matriz: bool = True,
) -> LeituraView | None:
    """A abertura da página: a manchete (o achado de maior peso), a linha fina (o que o
    termômetro diz do texto) e os achados, um por tipo, do mais forte para o mais fraco.
    ``tem_matriz``: a página tem a matriz veículo × nome (a prova dos achados de veículos
    e de distância; sem ela, a prova é o placar). ``None`` quando nenhuma lente tem dados."""
    candidatos: list[AchadoView | None] = []
    if nm is not None and nm.nomes:
        maior = max((n.participacao_valor for n in nm.nomes), default=0.0)
        sig = significancia(nm)
        candidatos += [
            _achado_tom(nm, maior, sig),
            _achado_espaco(nm, maior, sig),
            _achado_veiculos(nm, maior, "#an-matriz" if tem_matriz else "", sig),
            _achado_distancia(nm, "#an-matriz" if tem_matriz else ""),
            _achado_presenca(nm),
        ]
    if ld is not None:
        candidatos.append(_achado_lados(ld))
    if tx is not None:
        tendencia = _achado_tendencia(tx)
        candidatos += [
            _achado_texto(tx),
            _achado_assimetria(tx),
            tendencia,
            _achado_inclinado(tx),
            # a média da apuração sai quando a mudança dela já está na leitura
            None if tendencia is not None and tendencia.selo == "Apuração" else _achado_apuracao(tx),
        ]
    achados = sorted((a for a in candidatos if a is not None), key=lambda a: (-a.peso, a.lente, a.tipo))
    lentes = _lentes(nm, ld, tx)
    if not achados or not lentes:
        return None
    principal, demais = achados[0], achados[1:]
    do_texto = next((a for a in demais if a.tipo == "texto"), None)
    linha_fina = ""
    if do_texto is not None:
        # A linha fina é sempre o veredito do termômetro; ele sai da lista para não se repetir.
        demais = [a for a in demais if a is not do_texto]
        if tx is not None and tx.equilibrio:
            linha_fina = (
                f"No texto das notícias, o termômetro segue em equilíbrio: {_num(tx.equilibrado)} de "
                f"{_num(tx.noticias)} ficam em “Equilibrado”." + _antes_do_texto(achados, nm, ld)
            )
        else:
            linha_fina = f"{do_texto.titulo}. {do_texto.texto}"
    return LeituraView(
        chapeu="A leitura" + (f" · {periodo}" if periodo else ""),
        manchete=principal.titulo,
        linha_fina=linha_fina,
        principal=principal,
        achados=demais[:ACHADOS],
        lentes=lentes,
    )
