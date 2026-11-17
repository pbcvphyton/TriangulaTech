"""As contas estatísticas da visão por parlamentar, só com a biblioteca padrão.

Funções puras, sem nada do Congresso dentro: recebem números e devolvem números.

- **Posição**: :func:`mediana` e :func:`quartis`.
- **A faixa do acaso** (o funil de "Quem propõe"): com ``n`` projetos e a taxa ``p`` da Casa,
  entre quantos e quantos projetos de um tipo a pessoa pode ter só por acaso
  (:func:`faixa_do_acaso`: binomial exata, dois lados; quem chama divide o alfa pelo número de
  pessoas comparadas). Quem chama desenha um degrau por ``n`` presente nos dados.
- **Andar junto** (:func:`spearman`): correlação de postos, com o posto médio nos empates;
  :func:`spearman_dentro` faz a mesma conta com o posto de cada um descontado do posto médio
  do grupo dele (o partido); :func:`p_da_correlacao` dá a chance de um valor tão longe de
  zero só por acaso (t de Student).
- **Quanto o grupo explica** (:func:`eta2`): da variação entre pessoas numa medida, a parte
  que fica entre os grupos (os partidos), já descontado o que qualquer divisão em tantos
  grupos explicaria: ``1 − [SQ_dentro ÷ (n − k)] ÷ [SQ_total ÷ (n − 1)]``; negativo vira 0.
  :func:`eta2_intervalo` reamostra as pessoas (com reposição) e devolve o intervalo dos
  percentis, descontado o viés da reamostragem; :func:`eta2_acaso` embaralha os grupos entre as pessoas e devolve o percentil
  do que sai só por acaso. Os dois sorteios recebem o gerador (``random.Random``) de quem
  chama: com a mesma semente, o mesmo resultado.

O que fica de fora: qualquer modelo (não há regressão nem controle por outra variável), e a
causa: andar junto não é causa, e "o partido explica" quer dizer só que saber o partido reduz
a incerteza sobre a medida.
"""

from __future__ import annotations

import math
import random
from bisect import bisect_left
from collections.abc import Hashable, Sequence

# ── posição ──────────────────────────────────────────────────────────────────


def mediana(valores: Sequence[float]) -> float | None:
    """A mediana (a média dos dois do meio quando são pares); ``None`` sem valores."""
    if not valores:
        return None
    ordenados = sorted(valores)
    meio = len(ordenados) // 2
    return ordenados[meio] if len(ordenados) % 2 else (ordenados[meio - 1] + ordenados[meio]) / 2


def percentil(ordenados: Sequence[float], q: float) -> float:
    """O percentil ``q`` (0 a 100) de uma lista já ordenada, com interpolação linear entre os
    vizinhos (o método mais comum: posição ``q × (n − 1)``)."""
    if not ordenados:
        raise ValueError("percentil de lista vazia")
    posicao = (len(ordenados) - 1) * q / 100.0
    baixo = math.floor(posicao)
    alto = min(baixo + 1, len(ordenados) - 1)
    return ordenados[baixo] + (ordenados[alto] - ordenados[baixo]) * (posicao - baixo)


def quartis(valores: Sequence[float]) -> tuple[float, float, float] | None:
    """``(1º quartil, mediana, 3º quartil)``; ``None`` sem valores."""
    if not valores:
        return None
    ordenados = sorted(valores)
    return percentil(ordenados, 25), percentil(ordenados, 50), percentil(ordenados, 75)


# ── a faixa do acaso (binomial exata) ────────────────────────────────────────


def chances_binomiais(n: int, p: float) -> list[float]:
    """A chance de cada resultado (0 a ``n`` acertos) em ``n`` sorteios de chance ``p``. Em
    logaritmo (``lgamma``), para não estourar com ``n`` na casa do milhar."""
    if n < 0:
        raise ValueError("n negativo")
    if p <= 0.0:
        return [1.0] + [0.0] * n
    if p >= 1.0:
        return [0.0] * n + [1.0]
    lp, lq, ln = math.log(p), math.log1p(-p), math.lgamma(n + 1)
    return [math.exp(ln - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq) for i in range(n + 1)]


def binomial_bicaudal(k: int, n: int, p: float, chances: Sequence[float] | None = None) -> float:
    """A chance, sob o acaso, de um resultado tão ou mais improvável que ``k`` em ``n`` sorteios
    de chance ``p`` (exata, dois lados: soma as chances que não passam a de ``k``). É a mesma
    conta de ``dados._binomial_bicaudal``, feita em logaritmo."""
    if n <= 0:
        return 1.0
    chances = chances if chances is not None else chances_binomiais(n, p)
    limite = chances[k] * (1 + 1e-9)
    return min(1.0, math.fsum(c for c in chances if c <= limite))


def faixa_do_acaso(n: int, p: float, alfa: float) -> tuple[int, int]:
    """Entre quantos e quantos acertos em ``n`` o resultado **não** se distingue do acaso, com
    chance ``p`` por sorteio e nível ``alfa`` (dois lados): o menor e o maior ``k`` cuja
    chance bicaudal (:func:`binomial_bicaudal`) é ``alfa`` ou mais. Fora da faixa, "passa do
    acaso". Se nem o resultado mais provável alcança ``alfa`` (não acontece com alfa abaixo
    de 1), a faixa é só ele."""
    chances = chances_binomiais(n, p)
    dentro = [k for k in range(n + 1) if binomial_bicaudal(k, n, p, chances) >= alfa]
    if not dentro:
        moda = max(range(n + 1), key=chances.__getitem__)
        return moda, moda
    return dentro[0], dentro[-1]


def fora_da_faixa(k: int, n: int, p: float, alfa: float) -> int:
    """−1 se ``k`` em ``n`` fica abaixo do que o acaso explica, +1 se fica acima, 0 se cabe no
    acaso (chance bicaudal de ``alfa`` ou mais)."""
    if n <= 0 or binomial_bicaudal(k, n, p) >= alfa:
        return 0
    return 1 if k > n * p else -1


# ── andar junto (Spearman) ───────────────────────────────────────────────────


def postos(valores: Sequence[float]) -> list[float]:
    """O posto de cada valor (1 = o menor). Valores iguais levam o posto médio do grupo:
    ``[10, 20, 20, 30]`` → ``[1, 2.5, 2.5, 4]``."""
    ordem = sorted(range(len(valores)), key=valores.__getitem__)
    saida = [0.0] * len(valores)
    i = 0
    while i < len(ordem):
        j = i
        while j + 1 < len(ordem) and valores[ordem[j + 1]] == valores[ordem[i]]:
            j += 1
        medio = (i + j) / 2 + 1
        for k in range(i, j + 1):
            saida[ordem[k]] = medio
        i = j + 1
    return saida


def pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    """A correlação linear; ``None`` com menos de 3 pares ou quando um dos lados não varia."""
    n = len(x)
    if n != len(y):
        raise ValueError("listas de tamanhos diferentes")
    if n < 3:
        return None
    mx, my = math.fsum(x) / n, math.fsum(y) / n
    sxx = math.fsum((a - mx) ** 2 for a in x)
    syy = math.fsum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    """A correlação de postos: a de Pearson entre os postos (com o posto médio nos empates,
    que é a definição que vale com empates). ``None`` com menos de 3 pares ou sem variação."""
    if len(x) != len(y):
        raise ValueError("listas de tamanhos diferentes")
    return pearson(postos(x), postos(y))


def spearman_dentro(x: Sequence[float], y: Sequence[float], grupos: Sequence[Hashable]) -> float | None:
    """A correlação de postos **dentro dos grupos**: os postos são os da lista inteira, e de
    cada um se desconta o posto médio do grupo dele (o partido); a correlação é a desses
    desvios. Responde: entre colegas do mesmo grupo, quem tem mais de uma coisa tem mais da
    outra? Grupo de uma pessoa só não diz nada (o desvio dela é zero) e fica fora."""
    if not (len(x) == len(y) == len(grupos)):
        raise ValueError("listas de tamanhos diferentes")
    px, py = postos(x), postos(y)
    membros: dict[Hashable, list[int]] = {}
    for i, g in enumerate(grupos):
        membros.setdefault(g, []).append(i)
    dx: list[float] = []
    dy: list[float] = []
    for indices in membros.values():
        if len(indices) < 2:
            continue
        mx = math.fsum(px[i] for i in indices) / len(indices)
        my = math.fsum(py[i] for i in indices) / len(indices)
        dx += [px[i] - mx for i in indices]
        dy += [py[i] - my for i in indices]
    return pearson(dx, dy)


def _beta_incompleta(a: float, b: float, x: float) -> float:
    """A beta incompleta regularizada ``I_x(a, b)``, por fração contínua (Lentz)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    frente = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x > (a + 1.0) / (a + b + 2.0):  # a fração converge mais depressa do outro lado
        return 1.0 - _beta_incompleta(b, a, 1.0 - x)
    minusculo = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > minusculo else minusculo)
    h = d
    for m in range(1, 400):
        for numerador in (m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)), -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))):
            d = 1.0 + numerador * d
            d = 1.0 / (d if abs(d) > minusculo else minusculo)
            c = 1.0 + numerador / c
            c = c if abs(c) > minusculo else minusculo
            h *= d * c
        if abs(d * c - 1.0) < 1e-12:
            break
    return frente * h / a


def p_da_correlacao(r: float | None, n: int) -> float:
    """A chance, sob o acaso, de uma correlação tão longe de zero quanto ``r`` com ``n`` pares
    (dois lados; ``t = r·√((n − 2) ÷ (1 − r²))`` com ``n − 2`` graus de liberdade). Para a de
    postos é a aproximação usual, boa com algumas dezenas de pares. Sem ``r`` ou com menos de
    4 pares: 1 (nada a dizer)."""
    if r is None or n < 4:
        return 1.0
    if abs(r) >= 1.0:
        return 0.0
    gl = n - 2
    t2 = r * r * gl / (1.0 - r * r)
    return _beta_incompleta(gl / 2.0, 0.5, gl / (gl + t2))


def holm(pvalores: dict[Hashable, float], alfa: float) -> set[Hashable]:
    """As chaves que passam do acaso contando todas as comparações (Holm): a de menor chance
    contra ``alfa ÷ m``, a seguinte contra ``alfa ÷ (m − 1)``… até a primeira que não passa."""
    m = len(pvalores)
    passam: set[Hashable] = set()
    for i, (chave, p) in enumerate(sorted(pvalores.items(), key=lambda kv: kv[1])):
        if p > alfa / (m - i):
            break
        passam.add(chave)
    return passam


# ── quanto o grupo explica (η² ajustado) ─────────────────────────────────────


def _eta2_das_somas(n: int, k: int, soma: float, soma2: float, entre: float) -> float | None:
    """η² ajustado a partir das somas: ``entre`` = Σ (soma do grupo)² ÷ tamanho do grupo."""
    if n - k <= 0 or n < 2:
        return None
    total = soma2 - soma * soma / n
    if total <= 1e-12 * max(1.0, soma2):
        return None  # ninguém difere de ninguém: não há variação a explicar
    dentro = max(0.0, soma2 - entre)
    return max(0.0, 1.0 - (dentro / (n - k)) / (total / (n - 1)))


def _por_grupo(valores: Sequence[float], grupos: Sequence[Hashable], minimo: int) -> tuple[list[float], list[tuple[int, int]]]:
    """Os valores reordenados grupo a grupo (só os grupos com ``minimo`` pessoas ou mais, em
    ordem do nome, para o resultado não depender da ordem de entrada) e o trecho de cada grupo."""
    if len(valores) != len(grupos):
        raise ValueError("listas de tamanhos diferentes")
    membros: dict[Hashable, list[float]] = {}
    for v, g in zip(valores, grupos, strict=True):
        membros.setdefault(g, []).append(float(v))
    juntos: list[float] = []
    trechos: list[tuple[int, int]] = []
    for g in sorted((g for g, m in membros.items() if len(m) >= minimo), key=str):
        trechos.append((len(juntos), len(juntos) + len(membros[g])))
        juntos += membros[g]
    return juntos, trechos


def eta2(valores: Sequence[float], grupos: Sequence[Hashable], *, minimo: int = 2) -> tuple[float | None, int, int]:
    """``(η², n, k)``: a parte da variância entre pessoas que o grupo explica, já descontado o
    que qualquer divisão em ``k`` grupos explicaria por acaso::

        η² = 1 − [SQ_dentro ÷ (n − k)] ÷ [SQ_total ÷ (n − 1)]      (negativo vira 0)

    com ``n`` pessoas em ``k`` grupos de ``minimo`` pessoas ou mais (os menores ficam fora: um
    grupo de um só não tem variação dentro). 1 = dentro de cada grupo todos são iguais; 0 = os
    grupos não diferem mais do que grupos sorteados. ``None`` quando não dá para calcular
    (menos de dois grupos, nenhuma sobra dentro deles, ou ninguém difere de ninguém)."""
    x, trechos = _por_grupo(valores, grupos, minimo)
    n, k = len(x), len(trechos)
    if k < 2:
        return None, n, k
    entre = math.fsum(math.fsum(x[a:b]) ** 2 / (b - a) for a, b in trechos)
    return _eta2_das_somas(n, k, math.fsum(x), math.fsum(v * v for v in x), entre), n, k


def eta2_intervalo(
    valores: Sequence[float], grupos: Sequence[Hashable], *, reamostras: int, gerador: random.Random, confianca: float = 0.95, minimo: int = 2
) -> tuple[float, float] | None:
    """O intervalo de ``confianca`` de :func:`eta2`, por reamostragem: sorteiam-se ``n``
    pessoas com reposição (cada uma leva o seu grupo), refaz-se a conta, ``reamostras`` vezes.

    O intervalo é o dos percentis (de ``(1 − confianca) ÷ 2`` ao simétrico) **deslocado pelo
    viés da reamostragem**: a mediana das reamostras menos o valor observado. Sortear com
    reposição repete pessoas, o que aperta os grupos e infla o número; sem o desconto, quando
    o grupo explica pouco, o intervalo fica inteiro acima do valor verdadeiro em até metade
    das vezes (conferido por simulação com 500 pessoas em 20 grupos e com 75 em 10: com o
    desconto, o intervalo cobre o valor verdadeiro em 95% das vezes ou mais). O intervalo
    sempre contém o valor observado e fica entre 0 e 1.

    Os grupos são tratados como dados (os partidos são os que existem, não uma amostra de
    partidos): a incerteza é a de quais pessoas estão em cada um. Na reamostra, grupo que
    ficou sem ninguém sai da conta. ``None`` se a conta original não existe ou se menos de
    metade das reamostras dá para calcular."""
    x, trechos = _por_grupo(valores, grupos, minimo)
    n = len(x)
    observado = eta2(valores, grupos, minimo=minimo)[0]
    if len(trechos) < 2 or observado is None or reamostras <= 0:
        return None
    x2 = [v * v for v in x]
    indices = range(n)
    pegar, pegar2 = x.__getitem__, x2.__getitem__
    achados: list[float] = []
    for _ in range(reamostras):
        sorteados = sorted(gerador.choices(indices, k=n))
        soma = sum(map(pegar, sorteados))
        soma2 = sum(map(pegar2, sorteados))
        entre, k = 0.0, 0
        inicio = 0
        for _a, b in trechos:
            fim = bisect_left(sorteados, b, inicio)
            if fim > inicio:
                k += 1
                entre += sum(map(pegar, sorteados[inicio:fim])) ** 2 / (fim - inicio)
            inicio = fim
        valor = _eta2_das_somas(n, k, soma, soma2, entre) if k >= 2 else None
        if valor is not None:
            achados.append(valor)
    if len(achados) < reamostras / 2:
        return None
    achados.sort()
    cauda = (1.0 - confianca) / 2.0 * 100.0
    vies = percentil(achados, 50.0) - observado
    return min(1.0, max(0.0, percentil(achados, cauda) - vies)), min(1.0, max(0.0, percentil(achados, 100.0 - cauda) - vies))


def eta2_acaso(
    valores: Sequence[float], grupos: Sequence[Hashable], *, permutacoes: int, gerador: random.Random, percentil_do_acaso: float = 95.0, minimo: int = 2
) -> float | None:
    """Quanto :func:`eta2` daria **só por acaso**: embaralham-se os grupos entre as pessoas
    (os tamanhos dos grupos ficam; quem está em qual, não), refaz-se a conta, ``permutacoes``
    vezes; devolve o percentil ``percentil_do_acaso`` (95) dos resultados. Um η² observado que
    não passa disso não se distingue de partidos sorteados."""
    x, trechos = _por_grupo(valores, grupos, minimo)
    n, k = len(x), len(trechos)
    if k < 2 or eta2(valores, grupos, minimo=minimo)[0] is None or permutacoes <= 0:
        return None
    soma, soma2 = math.fsum(x), math.fsum(v * v for v in x)
    embaralhados = list(x)
    achados: list[float] = []
    for _ in range(permutacoes):
        gerador.shuffle(embaralhados)
        entre = sum(sum(embaralhados[a:b]) ** 2 / (b - a) for a, b in trechos)
        achados.append(_eta2_das_somas(n, k, soma, soma2, entre) or 0.0)
    achados.sort()
    return percentil(achados, percentil_do_acaso)
