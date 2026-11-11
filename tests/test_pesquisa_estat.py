"""Estatística da Pesquisa analítica (``pesquisa_estat.js.j2``): as contas da aba Analítico
(``achados``, ``lados``, ``analitico`` e ``midia``) rodadas no Node e comparadas com o Python,
e as caudas da Poisson e da binomial comparadas com a soma exata."""

from __future__ import annotations

import json
import math
import random
import shutil
import subprocess
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from qijournal import text
from qijournal.render import achados, analitico, lados, midia
from qijournal.render.filters import environment

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="sem Node.js para rodar o JavaScript da página")


def _node(tmp_path, dados, expressao: str):
    """O valor (JSON) de ``expressao`` no Node, com ``E = estatCriar()`` e ``D = dados``; o módulo
    roda dentro de uma função em modo estrito, como no IIFE de pesquisa.js.j2."""
    js = environment().get_template("pesquisa_estat.js.j2").render()
    script = tmp_path / "estat.js"
    script.write_text(
        "(function () {\n'use strict';\n" + js + f"\nvar E = estatCriar(), D = {json.dumps(dados)};\n"
        f"console.log(JSON.stringify({expressao}));\n}})();\n",
        encoding="utf-8",
    )
    saida = subprocess.run(["node", str(script)], capture_output=True, text=True, check=True, timeout=120)
    return json.loads(saida.stdout)


def _binomial_acima(k: int, n: int, p: Fraction) -> float:
    """P(X ≥ k), X ~ Binomial(n, p), exata: inteiros, uma divisão no fim."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    a, d = p.numerator, p.denominator
    termo, soma = math.comb(n, k) * a**k * (d - a) ** (n - k), 0
    for j in range(k, n + 1):
        soma += termo
        termo = termo * (n - j) * a // ((j + 1) * (d - a))  # o próximo termo, também inteiro
    return soma / d**n


def _poisson_caudas(k: int, mu: float) -> tuple[float, float]:
    """(P(X ≥ k), P(X ≤ k)), X ~ Poisson(mu), somando os termos com 60 dígitos."""
    with localcontext() as ctx:
        ctx.prec = 60
        m = Decimal(mu)
        termo, acima, abaixo = (-m).exp(), Decimal(0), Decimal(0)
        for j in range(int(max(k, mu) + 60 * math.sqrt(mu) + 100)):
            abaixo += termo if j <= k else 0
            acima += termo if j >= k else 0
            termo = termo * m / (j + 1)
        return float(acima), float(abaixo)


def test_p_normal_e_erfc(tmp_path):
    zs = [i / 4 for i in range(33)] + [-0.5, -1.96, -2.58, -3.3, -8.0]
    xs = [i / 8 for i in range(-24, 49)]
    pn, ec, sem_z = _node(
        tmp_path, {"z": zs, "x": xs}, "[D.z.map(E.pNormal), D.x.map(E.erfc), [null, undefined, NaN].map(E.pNormal)]"
    )
    for z, p in zip(zs, pn, strict=True):
        ref = achados._p_normal(z)
        assert abs(p - ref) < 1e-6 * ref or abs(p - ref) < 1e-12, z
    for x, e in zip(xs, ec, strict=True):
        assert math.isclose(e, math.erfc(x), rel_tol=1.2e-7), x
    assert sem_z == [1, 1, 1]


def test_lgamma(tmp_path):
    xs = [1e-12, 1e-6, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 2.5, 3.7, 7, 10, 33.3, 101, 1000.25, 1801, 5001, 12345.6, 1e5 + 1, 5e5, 1e6]
    for x, v in zip(xs, _node(tmp_path, xs, "D.map(E.lgamma)"), strict=True):
        assert abs(v - math.lgamma(x)) <= 1e-10 * max(1.0, abs(math.lgamma(x))), x  # absoluto nos zeros (1 e 2)


def test_caudas_da_poisson(tmp_path):
    ks = {
        0.5: [0, 1, 2, 3, 5, 10, 20, 40],
        3: [0, 1, 2, 3, 4, 6, 10, 20, 50],
        62: [0, 10, 30, 50, 61, 62, 63, 75, 100, 150, 300],
        676: [0, 1, 300, 500, 600, 650, 675, 676, 677, 700, 800, 1000, 1500],
        2000: [0, 1000, 1500, 1800, 1950, 1999, 2000, 2001, 2100, 2300, 2600, 3000],
    }
    casos = [[k, mu] for mu, lista in ks.items() for k in lista]
    saida, zero = _node(
        tmp_path,
        casos,
        "[D.map(function (c) { var r = E.poisCaudas(c[0], c[1]); return [r.acima, r.abaixo, E.pPoisson2(c[0], c[1])]; }),"
        " [E.poisCaudas(0, 0), E.poisCaudas(3, 0)]]",
    )
    for (k, mu), valores in zip(casos, saida, strict=True):
        acima, abaixo = _poisson_caudas(k, mu)
        for v, ref in zip(valores, (acima, abaixo, min(1.0, 2 * min(acima, abaixo))), strict=True):
            assert math.isclose(v, ref, rel_tol=1e-6, abs_tol=1e-300), (k, mu, v, ref)
    assert _poisson_caudas(1500, 2000)[1] < 1e-30  # a cauda pequena sai precisa mesmo minúscula
    assert zero == [{"acima": 1, "abaixo": 1}, {"acima": 0, "abaixo": 1}]


def test_binomial_acima(tmp_path):
    ks = {
        20: [0, 1, 2, 3, 5, 8, 10, 15, 20, 21],
        1108: [1, 2, 5, 10, 30, 100, 300, 410, 554, 600, 700, 1000, 1108],
        1800: [1, 3, 10, 50, 180, 450, 666, 900, 1000, 1500, 1800],
    }
    ps = ["0.001", "0.01", "0.05", "0.1", "0.25", "0.37", "0.5"]
    casos = [[k, n, float(p)] for n, lista in ks.items() for p in ps for k in lista]
    extremos = [[0, 10, 0.3, 1], [11, 10, 0.3, 0], [1, 10, 0, 0], [0, 10, 0, 1], [10, 10, 1, 1], [11, 10, 1, 0]]
    saida, bordas = _node(
        tmp_path,
        {"c": casos, "e": extremos},
        "[D.c.map(function (c) { return E.binAcima(c[0], c[1], c[2]); }),"
        " D.e.map(function (c) { return E.binAcima(c[0], c[1], c[2]); })]",
    )
    for (k, n, p), v in zip(casos, saida, strict=True):
        ref = _binomial_acima(k, n, Fraction(str(p)))
        assert math.isclose(v, ref, rel_tol=1e-6, abs_tol=1e-300), (k, n, p, v, ref)
    assert bordas == [e[3] for e in extremos]


def test_teste_de_sinal_da_o_veredito_do_analitico(tmp_path):
    pares = [[a, n - a] for n in range(61) for a in range(n + 1)]
    for n in (1000, 5000):
        for m in (n // 2, n // 2 + 1, n // 2 + 20, n // 2 + 41, n // 2 + 42, n // 2 + 100, n - 1, n):
            pares += [[m, n - m], [n - m, m]]
    pares.append([542, 460])  # 1.002 desvios (o último assert)
    saida = _node(tmp_path, pares, "D.map(function (c) { return E.pSinal(c[0], c[1]); })")
    for (a, b), p in zip(pares, saida, strict=True):
        maior, n = max(a, b), a + b
        assert math.isclose(p, min(1.0, 2 * _binomial_acima(maior, n, Fraction(1, 2))), rel_tol=1e-9), (a, b)
        if n <= 1000:  # o Analítico conta exato até mil desvios
            assert (p < 0.01) == analitico._passa_do_acaso(maior, n), (a, b)
    # Acima de mil, o Analítico usa a aproximação normal, um tico mais frouxa: no total limítrofe
    # os vereditos diferem (documentado no módulo).
    assert analitico._passa_do_acaso(542, 1002) and saida[-1] > 0.01


def test_holm_igual_ao_de_lados(tmp_path):
    rng = random.Random(7)
    casos = []
    for _ in range(60):
        n = rng.randint(0, 12)
        m = rng.choice([None, None, n + rng.randint(1, 6), rng.randint(0, n)])
        alfa = rng.choice([0.01, 0.05, 1 - 0.95])
        topo = n if m is None else max(m, n)
        ps: list[float] = []
        for _ in range(n):
            sorte = rng.random()
            if ps and sorte < 0.3:
                ps.append(rng.choice(ps))  # empate
            elif sorte < 0.45:
                ps.append(alfa / (topo - rng.randrange(topo)))  # bem no limiar de um degrau
            else:
                ps.append(0.1 * rng.random() ** 4)
        casos.append({"p": {f"v{i}": p for i, p in enumerate(ps)}, "alfa": alfa, "m": m})
    saida = _node(tmp_path, casos, "D.map(function (c) { return Object.keys(E.holm(c.p, c.alfa, c.m)).sort(); })")
    esperado = [sorted(lados.holm(c["p"], c["alfa"], c["m"])) for c in casos]
    assert saida == esperado
    assert 0 < sum(map(len, esperado)) < sum(len(c["p"]) for c in casos)  # passa uns, barra outros


def test_soma_de_sorteios_e_acaso_do_par_iguais_aos_de_lados(tmp_path):
    rng = random.Random(11)
    pares = [[na, nb] for na in range(1, 16) for nb in range(1, 16)]
    casos = []
    for _ in range(80):
        ps = [lados._acaso_par(*rng.choice(pares)) if rng.random() < 0.5 else rng.randint(1, 6) / rng.randint(7, 14)
              for _ in range(rng.randint(1, 40))]
        casos.append([rng.choice([0, len(ps), rng.randint(0, len(ps))]), ps])
    acaso, chances = _node(
        tmp_path,
        {"pares": pares, "casos": casos},
        "[D.pares.map(function (c) { return E.acasoPar(c[0], c[1]); }), D.casos.map(function (c) { return E.pPB(c[0], c[1]); })]",
    )
    assert acaso == [lados._acaso_par(na, nb) for na, nb in pares]
    for (obs, ps), p in zip(casos, chances, strict=True):
        assert math.isclose(p, lados._p(obs, ps), rel_tol=1e-12), (obs, ps)


def test_z_de_duas_proporcoes_igual_ao_do_espaco(tmp_path):
    rng = random.Random(3)
    casos = []
    for _ in range(60):
        n1, n2 = rng.randint(50, 3000), rng.randint(50, 3000)
        k1, k2 = rng.randint(0, n1 // rng.choice([1, 4, 30])), rng.randint(0, n2 // rng.choice([1, 4, 30]))
        casos.append([k1, n1, k2 + 10 if k1 + k2 < 10 else k2, n2])
    casos += [[5, 50, 5, 50], [0, 50, 10, 60], [50, 50, 40, 50], [50, 50, 60, 60], [1, 3000, 30, 900]]
    sem_z = [[0, 50, 0, 50], [3, 0, 1, 10], [1, 10, 2, 0]]  # sem variação; sem amostra
    zs = _node(tmp_path, casos + sem_z, "D.map(function (c) { return E.zProp(c[0], c[1], c[2], c[3]); })")
    dias = [f"2026-10-{d:02d}" for d in range(1, 15)]  # as 7 edições recentes e as 7 anteriores
    for (k1, n1, k2, n2), z in zip(casos, zs[: len(casos)], strict=True):
        e = midia._espaco({dias[-1]: k1, dias[0]: k2}, {dias[-1]: n1, dias[0]: n2}, dias)
        assert e is not None, (k1, n1, k2, n2)
        assert z is None if e.z is None else math.isclose(z, e.z, rel_tol=1e-12, abs_tol=1e-15), (k1, n1, k2, n2)
    assert zs[len(casos) :] == [None] * len(sem_z)


ARRED = [
    (12.5, 0, 12), (13.5, 0, 14), (-2.5, 0, -2), (0.5, 0, 0), (1.5, 0, 2), (2.345, 2, 2.35), (1.115, 2, 1.11),
    (-1.115, 2, -1.11), (2.675, 2, 2.67), (1.005, 2, 1.0), (0.125, 2, 0.12), (0.375, 2, 0.38), (6.45, 1, 6.5),
    (0.995, 2, 0.99), (9.95, 1, 9.9), (57.49999999999999, 0, 57), (7, 2, 7),
]
# Os empates (0,995%, 9,95%, 6,45%, 57,5%) se resolvem pelo valor binário, como no Python:
# 199/20000 dá 0,9950000000000001 (sobe); 9.95/100 dá 9,9499999… (desce).
PCT = [
    ((0, 0), "—"), ((3, 0), "—"), ((0, 50), "0%"), ((1, 20000), "<0,01%"), ((1, 70000), "<0,01%"), ((1, 10000), "0,01%"),
    ((11, 10000), "0,11%"), ((68, 10000), "0,68%"), ((99, 10000), "0,99%"), ((199, 20000), "1,00%"), ((1, 100), "1,0%"),
    ((64, 1000), "6,4%"), ((129, 2000), "6,5%"), ((9.95, 100), "9,9%"), ((199, 2000), "10,0%"), ((1, 10), "10%"),
    ((26, 100), "26%"), ((23, 40), "57%"), ((2, 3), "67%"), ((1500, 100), "1.500%"),
]


def test_arredondamento_e_porcentagem_como_no_python(tmp_path):
    rng = random.Random(5)
    xs = [k / 1000 for k in range(-3000, 3001, 7)] + [k / 8 for k in range(-80, 81)]  # k/8: empates de verdade
    xs += [rng.uniform(-100, 100) for _ in range(300)] + [100 * (p / t) for t in (7, 40, 200, 2000) for p in range(t + 1)]
    pares = [[p, t] for t in range(1, 201) for p in range(t + 1)]
    pares += [[p, t] for t in (1000, 2000, 7000, 20000, 70000) for p in (1, 2, 7, 11, 68, 99, 100, 129, 199, 995, 1000)]
    tabela, arredondados, textos, porcentagens = _node(
        tmp_path,
        {"a": [[x, c] for x, c, _ in ARRED], "x": xs, "p": [list(c) for c, _ in PCT], "pares": pares},
        "[D.a.map(function (c) { return E.arred(c[0], c[1]); }),"
        " D.x.map(function (x) { return [0, 1, 2, 3].map(function (c) { return E.arred(x, c); }); }),"
        " D.p.map(function (c) { return E.pct(c[0], c[1]); }), D.pares.map(function (c) { return E.pct(c[0], c[1]); })]",
    )
    assert tabela == [r for _, _, r in ARRED] == [round(x, c) for x, c, _ in ARRED]
    assert arredondados == [[round(x, c) for c in range(4)] for x in xs]
    assert textos == [t for _, t in PCT]
    for (parte, todo), t in zip(pares, porcentagens, strict=True):
        v = 100 * (parte / todo)
        if v >= 1:
            assert t == achados._pct(parte / todo), (parte, todo)  # o mesmo texto do Analítico
        else:
            assert t == ("0%" if v == 0 else "<0,01%" if v < 0.01 else f"{text.format_number_pt(v, 2)}%"), (parte, todo)
