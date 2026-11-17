"""A visão por parlamentar (qijournal.congresso.pais, pais_voto, pais_cruzar e pais_estat): cada
conta num punhado de projetos e votos feitos à mão, e a atualização de ponta a ponta."""

from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

import pytest

from qijournal.congresso import base, dados, leitura, pais, pais_cruzar, pais_estat as estat, pais_voto, projetos, regras
from qijournal.congresso.ficha import Cadastro
from qijournal.config import ROOT
from tests.fixtures.congresso import fabrica

HOJE = "2026-11-15"
INICIO = "2023-02-01"


@pytest.fixture(scope="module")
def r() -> regras.Regras:
    """As réguas de verdade, de config/congresso.yaml (com o bloco ``projeto_de_pais``)."""
    return regras.carregar(ROOT)


@pytest.fixture(scope="module")
def regua(r: regras.Regras) -> regras.ProjetoDePais:
    assert r.projeto_de_pais is not None
    return r.projeto_de_pais


def _lida(codigos: str, **campos) -> leitura.Leitura:
    """``"fiscal geral ajuste neutro neutro afrouxa"`` → a leitura (a, e, m, ct, is, co)."""
    return leitura.Leitura(codigos=tuple(codigos.split()), **campos)


def _prop(pid: int, titulo: str, ementa: str = "", *, primeiro: int | None = 1, autores: list[int] | None = None, casa: str = "cd", **campos) -> projetos.Proposicao:
    """``"PL 12/2025"`` → a proposição, tramitando à espera de relator, apresentada em 2025."""
    sigla, resto = titulo.split(" ")
    numero, ano = resto.split("/")
    campos = {"estado": projetos.TRAMITANDO, "fila": projetos.F_RELATOR, "apresentada": f"{ano}-03-01", "movimento": "2026-10-01", "origem": "parlamentar", **campos}
    if campos["estado"] != projetos.TRAMITANDO:
        campos["fila"] = ""
    return projetos.Proposicao(
        id=pid, casa=casa, sigla=sigla, numero=int(numero), ano=int(ano), ementa=ementa or f"Ementa do projeto {pid}.", primeiro=primeiro,
        autores=autores if autores is not None else ([primeiro] if primeiro is not None else []), **campos,
    )


def _cadastros(partidos: dict[int, str], casa: str = "cd", fora: tuple[int, ...] = ()) -> dict[int, Cadastro]:
    return {pid: Cadastro(id=pid, casa=casa, nome=f"Pessoa {pid:02d}", partido=partido, uf="SP", exercicio=pid not in fora) for pid, partido in partidos.items()}


def _ctx(r: regras.Regras, lidas: dict[str, leitura.Leitura], cadastros: dict[int, Cadastro] | None = None, *, casa: str = "cd", fichas: tuple[str, ...] = ()) -> pais.Contexto:
    """O contexto de uma Casa com a guarda feita à mão: chave da proposição → leitura."""
    regua = r.projeto_de_pais
    assert regua is not None
    guarda = leitura.Guarda(regua)
    for chave, lida in lidas.items():
        guarda.registrar(casa, 2025, chave, lida)
    return pais.Contexto(
        casa=casa, hoje=HOJE, inicio=INICIO, regras=r, regua=regua, leitor=leitura.Leitor(regua=regua, guarda=guarda),
        cadastros=cadastros if cadastros is not None else _cadastros({1: "PT"}), fichas=frozenset(fichas),
    )


TOPO_2 = "fiscal geral sistema corta neutro neutro"  # peso 100, no núcleo: do topo; marca do caixa +1
TOPO_1 = "infra setor sistema neutro neutro neutro"  # peso 60, no núcleo: do topo pela ementa
MEIO = "produtividade grupo ajuste gasto profissao neutro"  # peso 24; caixa −1; exceção −1
PONTUAL = "social grupo servico gasto neutro neutro"  # peso 12; caixa −1
MINIMO = "outro local simbolico neutro neutro neutro"  # peso 0


# ── a estatística ────────────────────────────────────────────────────────────


def test_a_faixa_do_acaso_e_a_binomial_exata_de_dois_lados():
    """Em 10 sorteios de chance ½: 1 ou 9 acertos têm chance 2 × 11/1024 = 2,1% (fora, a 5%);
    2 ou 8, 2 × 56/1024 = 10,9% (dentro). A faixa vai de 2 a 8."""
    assert estat.binomial_bicaudal(1, 10, 0.5) == pytest.approx(22 / 1024)
    assert estat.binomial_bicaudal(2, 10, 0.5) == pytest.approx(112 / 1024)
    assert estat.faixa_do_acaso(10, 0.5, 0.05) == (2, 8)
    assert [estat.fora_da_faixa(k, 10, 0.5, 0.05) for k in (1, 2, 5, 8, 9)] == [-1, 0, 0, 0, 1]
    # A mesma conta que a página já usa (dados._binomial_bicaudal), sem estourar com n grande.
    for k, n, p in ((3, 20, 0.1), (0, 15, 0.3), (7, 61, 0.031), (50, 61, 0.78)):
        assert estat.binomial_bicaudal(k, n, p) == pytest.approx(dados._binomial_bicaudal(k, n, p))
    assert estat.binomial_bicaudal(600, 1200, 0.5) == pytest.approx(1.0) and estat.binomial_bicaudal(100, 1224, 0.03) < 1e-12
    # Com mais projetos a faixa aperta (em proporção), e a taxa da Casa fica sempre dentro.
    de10, ate10 = estat.faixa_do_acaso(10, 0.03, 0.0001)
    de500, ate500 = estat.faixa_do_acaso(500, 0.03, 0.0001)
    assert de10 == 0 and ate10 / 10 > ate500 / 500 > 0.03 > de500 / 500 > 0


def test_spearman_com_empates_usa_o_posto_medio():
    """x = 1, 2, 2, 3 tem postos 1; 2,5; 2,5; 4. Com y de postos 1, 3, 2, 4: Σdxdy = 4,5,
    Σdx² = 4,5, Σdy² = 5: r = 4,5 ÷ √22,5 = 0,9487."""
    assert estat.postos([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]
    assert estat.postos([5, 5, 5]) == [2.0, 2.0, 2.0]
    assert estat.spearman([1, 2, 2, 3], [1, 3, 2, 4]) == pytest.approx(4.5 / 22.5**0.5)
    assert estat.spearman([1, 2, 3, 4], [10, 100, 1000, 10000]) == pytest.approx(1.0)  # só a ordem conta
    assert estat.spearman([1, 2, 2, 3], [9, 5, 5, 1]) == pytest.approx(-1.0)
    assert estat.spearman([1, 1, 1], [1, 2, 3]) is None and estat.spearman([1, 2], [2, 1]) is None  # sem variação; poucos pares


def test_a_correlacao_dentro_dos_partidos_pode_ter_o_sinal_contrario_ao_da_casa():
    """Dois partidos: dentro de cada um, quem tem mais x tem mais y; mas o partido de x alto
    é o de y baixo. Na Casa a correlação é negativa; dentro dos partidos, +1."""
    x = [1, 2, 3, 4, 11, 12, 13, 14]
    y = [11, 12, 13, 14, 1, 2, 3, 4]
    grupos = ["A"] * 4 + ["B"] * 4
    assert estat.spearman(x, y) < -0.5
    assert estat.spearman_dentro(x, y, grupos) == pytest.approx(1.0)
    # A chance sob o acaso (t de Student): r = 0,5 com 20 pares → t = 2,449 com 18 graus → 2,5%.
    assert estat.p_da_correlacao(0.5, 20) == pytest.approx(0.0248, abs=2e-4)
    assert estat.p_da_correlacao(None, 20) == 1.0 and estat.p_da_correlacao(1.0, 20) == 0.0
    assert estat.holm({"a": 0.001, "b": 0.004, "c": 0.5}, 0.01) == {"a", "b"}  # 0,001 ≤ 0,01/3; 0,004 ≤ 0,01/2; 0,5 > 0,01


def test_quanto_o_partido_explica_tudo_ou_nada():
    """Tudo: dentro de cada partido todos são iguais (SQ_dentro = 0 → 1). Nada: os três partidos
    têm os mesmos valores (SQ_entre = 0 → o ajuste dá negativo, que vira 0)."""
    partidos = ["A"] * 4 + ["B"] * 4 + ["C"] * 4
    assert estat.eta2([10] * 4 + [20] * 4 + [60] * 4, partidos) == (1.0, 12, 3)
    assert estat.eta2([1, 2, 3, 4] * 3, partidos) == (0.0, 12, 3)
    # Um caso do meio, à mão: A = 1, 3; B = 5, 7. SQ_total = 20, SQ_dentro = 4: 1 − (4/2) ÷ (20/3) = 0,7.
    assert estat.eta2([1, 3, 5, 7], ["A", "A", "B", "B"])[0] == pytest.approx(0.7)
    # Partido de uma pessoa só fica fora; sem dois partidos, ou sem variação, não há número.
    assert estat.eta2([1, 3, 5, 7, 99], ["A", "A", "B", "B", "Z"]) == (pytest.approx(0.7), 4, 2)
    assert estat.eta2([1, 2, 3], ["A", "A", "A"])[0] is None and estat.eta2([5, 5, 5, 5], ["A", "A", "B", "B"])[0] is None


def test_o_intervalo_e_o_acaso_saem_iguais_com_a_mesma_semente():
    sorteio = random.Random(3)
    partidos = [f"P{i % 6}" for i in range(120)]
    x = [(i % 6) * 0.8 + sorteio.gauss(0, 1) for i in range(120)]
    observado = estat.eta2(x, partidos)[0]
    a = estat.eta2_intervalo(x, partidos, reamostras=300, gerador=random.Random("57:cd:x"))
    b = estat.eta2_intervalo(x, partidos, reamostras=300, gerador=random.Random("57:cd:x"))
    assert a == b and a[0] <= observado <= a[1] and 0.0 <= a[0] < a[1] <= 1.0
    acaso = estat.eta2_acaso(x, partidos, permutacoes=300, gerador=random.Random("57:cd:x"))
    assert acaso == estat.eta2_acaso(x, partidos, permutacoes=300, gerador=random.Random("57:cd:x"))
    assert acaso < a[0]  # o partido explica mais do que partidos sorteados
    # Sem efeito nenhum, o intervalo encosta no zero e o observado não passa do acaso.
    ruido = [sorteio.gauss(0, 1) for _ in range(120)]
    ic = estat.eta2_intervalo(ruido, partidos, reamostras=300, gerador=random.Random(1))
    assert ic[0] <= estat.eta2_acaso(ruido, partidos, permutacoes=300, gerador=random.Random(1))


# ── o estoque ────────────────────────────────────────────────────────────────


def test_o_estoque_conta_so_o_que_tramita_e_tem_leitura(r: regras.Regras, regua):
    """Sete proposições: A (peso 100, confirmada, parada desde 2024, na CFT, sem relator); B
    (peso 60 com um leitor só: a confirmar; com o relator 7); C (peso 0, na Mesa); D (sem
    leitura); E (peso 100, mas anda junto com outra); F (decreto legislativo: tipo que não se
    lê); G (peso 12, virou norma)."""
    props = [
        _prop(1, "PLP 1/2024", "Limita a despesa.", orgao="CFT", movimento="2024-01-10"),
        _prop(2, "PL 2/2025", "Marco das ferrovias.", fila=projetos.F_PARECER, orgao="CVT", relator=7),
        _prop(3, "PL 3/2025", "Dia do leitor.", fila=projetos.F_MESA),
        _prop(4, "PL 4/2025"),
        _prop(5, "PL 5/2025", estado=projetos.APENSADA),
        _prop(6, "PDL 6/2025"),
        _prop(7, "PL 7/2023", estado=projetos.NORMA),
    ]
    lidas = {"1": _lida(TOPO_2, r=2, porque="Limita a despesa primária."), "2": _lida(TOPO_1), "3": _lida(MINIMO), "5": _lida(TOPO_2), "6": _lida(TOPO_2, r=2), "7": _lida(PONTUAL)}
    ctx = _ctx(r, lidas, _cadastros({1: "PT", 7: "PP"}), fichas=("PLP 1/2024",))
    bloco, arquivo = pais.estoque(ctx, props, comissoes={"CFT": (7, "Pessoa 07")})
    esperado = {"tramitando": 4, "lidos": 3, "estr": 1, "topo": 2, "setorial": 1, "baixo": 1, "sb": 1, "estr_parados": 1, "topo_parados": 1, "estr_apensados": 0, "topo_apensados": 1}
    assert {k: bloco[k] for k in esperado} == esperado
    assert (bloco["plota"], bloco["plotados"], bloco["plotaveis"], bloco["sem_data"]) == ("topo", 2, 2, 0)
    caixa = regua.marca(regua.papeis.caixa)
    mais, zero = caixa.de(1).id, caixa.de(0).id
    assert bloco["dir"][mais] == 1 and sum(bloco["dir"].values()) == 1  # só o confirmado
    assert bloco["dir_topo"][mais] == 1 and bloco["dir_topo"][zero] == 1
    portas = {b["id"]: b for b in bloco["por_porta"]}
    assert [b["id"] for b in bloco["por_porta"]] == list(pais.PORTAS)
    assert (portas["relator"]["n"], portas["relator"]["lidos"], portas["relator"]["estr"], portas["relator"]["topo"], portas["relator"]["estr_parados"]) == (2, 1, 1, 1, 1)
    assert portas["relator"]["mediana_dias"] == (date(2026, 11, 15) - date(2024, 1, 10)).days
    assert (portas["parecer"]["topo"], portas["parecer"]["estr"], portas["mesa"]["topo"], portas["mesa"]["lidos"]) == (1, 0, 0, 1)
    eixos = {e["id"]: e for e in bloco["por_eixo"]}
    assert [e["id"] for e in bloco["por_eixo"]] == list(regua.alavanca.ids)
    assert sum(e["topo"] for e in eixos.values()) == 2 and sum(e["estr"] for e in eixos.values()) == 1
    assert bloco["porta_maior"] in ("relator", "parecer") and bloco["foco"] == "cd-PLP-1-2024"
    assert [(t["k"], t["w"], t["ac"], t["org"], t["a"]) for t in bloco["top"]] == [("cd-PLP-1-2024", 100, 0, "CFT", "cd-1")]  # só os parados
    assert bloco["orgaos"][0] == {"org": "CFT", "estr": 1, "topo": 1, "mais_antiga": "2024-01-10", "presidente": "Pessoa 07", "presidente_k": "cd-7"}
    assert bloco["relatores"] == [{"k": "cd-7", "nome": "Pessoa 07", "pt": "PP", "uf": "SP", "estr": 0, "topo": 1, "mais_antiga": "2026-10-01"}]
    assert bloco["espera_x_norma"]["espera"] == {"total": 4, "n": 3, "baixo": 1, "sb": 1, "setorial": 1, "estr": 1, "topo": 2}
    assert bloco["espera_x_norma"]["norma"] == {"total": 1, "n": 1, "baixo": 1, "sb": 0, "setorial": 0, "estr": 0, "topo": 0}
    # O arquivo do gráfico: os do topo pela ementa, do maior peso para o menor; o não confirmado vem marcado.
    linhas = [dict(zip(arquivo["cols"], linha, strict=True)) for linha in arquivo["linhas"]]
    assert [(x["k"], x["w"], x["al"], x["fo"], x["f"]) for x in linhas] == [("cd-PLP-1-2024", 100, [], "ia2", 1), ("cd-PL-2-2025", 60, ["a_confirmar"], "ia", 0)]
    a, b = linhas
    assert a["fx"] == a["fe"] and b["fx"] != b["fe"] and b["fe"] == a["fe"]  # o "a confirmar" conta na faixa de baixo
    assert (a["a"], a["an"], a["ap"], a["pp"], a["org"], a["rl"], a["ult"], a["dir"], a["j"]) == ("cd-1", "Pessoa 01", "PT", "relator", "CFT", None, "2024-01-10", mais, "Limita a despesa primária.")
    assert (b["rl"], b["rln"], b["og"]) == ("cd-7", "Pessoa 07", "parlamentar") and a["conta"].endswith("= 100")
    assert [par[0] for par in a["cod"]] == [c.id for c in regua.campos if c.id != regua.alavanca.id] and len(a["mk"]) == len(regua.marcas)


def test_o_teto_de_pontos_fica_com_os_confirmados_e_os_de_maior_peso(r: regras.Regras, regua):
    import dataclasses

    apertada = dataclasses.replace(regua, limiares=dataclasses.replace(regua.limiares, teto_pontos=2))
    props = [_prop(1, "PL 1/2025"), _prop(2, "PL 2/2025"), _prop(3, "PL 3/2025", movimento="2025-01-01")]
    ctx = _ctx(dataclasses.replace(r, projeto_de_pais=apertada), {"1": _lida(TOPO_1, r=2), "2": _lida(TOPO_2), "3": _lida(TOPO_2)})
    bloco, arquivo = pais.estoque(ctx, props)
    # Três do topo, cabem dois: o confirmado (peso 60) entra antes do de peso 100 mais novo; o mais antigo ganha o empate.
    assert (bloco["topo"], bloco["plotaveis"], bloco["plotados"]) == (3, 3, 2)
    assert [linha[0] for linha in arquivo["linhas"]] == ["cd-PL-3-2025", "cd-PL-1-2025"]


# ── quem propõe ──────────────────────────────────────────────────────────────


def test_a_reapresentada_conta_uma_vez_para_quem_apresentou_primeiro(r: regras.Regras):
    """A mesma ementa (com outra caixa, acento e pontuação) em três projetos: o de 2019, de
    outro deputado, é o original; os de 2023 e de 2025 são reapresentações e saem do perfil."""
    original = _prop(10, "PL 10/2019", "Institui o Dia Nacional do Leitor.", primeiro=2, apresentada="2019-05-01")
    repetida = _prop(11, "PL 11/2023", "INSTITUI O DIA NACIONAL DO LEITOR", primeiro=1, apresentada="2023-05-01")
    de_novo = _prop(12, "PL 12/2025", "Institui o dia nacional do leitor;", primeiro=1, apresentada="2025-05-01")
    outra = _prop(13, "PL 13/2025", "Institui o Dia Nacional do Escritor.", primeiro=1)
    sem_ementa = [_prop(14, "PL 14/2025", " ", primeiro=1), _prop(15, "PL 15/2025", " ", primeiro=1)]
    props = [original, repetida, de_novo, outra, *sem_ementa]
    for p in sem_ementa:
        p.ementa = ""
    repetidas = pais.reapresentadas(props, ("PL",))
    assert repetidas == {"11": "PL 10/2019", "12": "PL 10/2019"}  # sem ementa nunca é repetição
    ctx = _ctx(r, {str(p.id): _lida(MINIMO) for p in props}, _cadastros({1: "PT", 2: "PL"}))
    dos = pais.perfis(ctx, {1: [repetida, de_novo, outra, *sem_ementa], 2: [original]}, repetidas=repetidas)
    assert dos[1].fora == {"11": (pais.REAPRESENTADA, "PL 10/2019"), "12": (pais.REAPRESENTADA, "PL 10/2019")}
    assert len(dos[1].todos) == 5 and len(dos[1].lidos) == 3
    # O original é de antes da legislatura: não entra no perfil de ninguém, mas é para ele que a repetição conta.
    assert dos[2].todos == []
    linha = pais.linha_do_autor(ctx, dos[1])
    assert (linha["n"], linha["tot"], linha["rp"], linha["bc"]) == (3, 3, 2, 0)
    arquivo = pais.todos_os_projetos(ctx, dos[1])
    marcadas = {linha[1]: (linha[arquivo["cols"].index("x")], linha[arquivo["cols"].index("xo")]) for linha in arquivo["linhas"]}
    assert marcadas["PL 11/2023"] == ("reap", "PL 10/2019") and marcadas["PL 13/2025"] == ("", "") and len(arquivo["linhas"]) == 5


def test_o_projeto_de_bancada_fica_fora_do_perfil_individual(r: regras.Regras, regua):
    """PT com 12 em exercício, PL com 4, NOVO com 1. De bancada: 10 coautores do PT; ou metade
    da bancada do PL (2 de 4). Não é: 9 do PT; um deputado sozinho do NOVO (que é "toda a
    bancada"); a PEC com 12 assinaturas do PT (assinatura de PEC é apoio)."""
    partidos = {**{i: "PT" for i in range(1, 13)}, **{i: "PL" for i in range(21, 25)}, 31: "NOVO"}
    bancadas = {"PT": 12, "PL": 4, "NOVO": 1}
    assert pais.de_bancada(regua, "PL", ["PT"] * 10, bancadas) and not pais.de_bancada(regua, "PL", ["PT"] * 5 + ["PL"], bancadas)
    assert pais.de_bancada(regua, "PL", ["PT"] * 6, bancadas)  # metade da bancada (6 de 12)
    assert pais.de_bancada(regua, "PL", ["PL", "PL"], bancadas) and not pais.de_bancada(regua, "PL", ["PL", "PT"], bancadas)
    assert not pais.de_bancada(regua, "PL", ["NOVO"], bancadas) and not pais.de_bancada(regua, "PEC", ["PT"] * 12, bancadas)
    props = [
        _prop(1, "PL 1/2025", autores=list(range(1, 11))),  # dez do PT
        _prop(2, "PL 2/2025", autores=[1, 2, 21]),  # dois do PT e um do PL: de ninguém
        _prop(3, "PEC 3/2025", autores=list(range(1, 13))),  # PEC: conta para o primeiro signatário
        _prop(4, "PL 4/2025", primeiro=21, autores=[21, 22]),  # metade da bancada do PL
        _prop(5, "PL 5/2025", primeiro=31, autores=[31]),
    ]
    ctx = _ctx(r, {str(p.id): _lida(MINIMO) for p in props}, _cadastros(partidos))
    autoria = {pid: [p for p in props if pid in p.autores] for pid in partidos}
    dos = pais.perfis(ctx, autoria, repetidas={})
    assert dos[1].fora == {"1": (pais.DE_BANCADA, "")} and [pr.p.titulo for pr in dos[1].lidos] == ["PL 2/2025", "PEC 3/2025"]
    assert dos[21].fora == {"4": (pais.DE_BANCADA, "")} and dos[21].lidos == []
    assert dos[31].fora == {} and len(dos[31].lidos) == 1
    assert dos[2].todos == []  # coautoria não é autoria principal
    assert pais.linha_do_autor(ctx, dos[1])["bc"] == 1


def test_o_saldo_de_contas_e_os_minimos(r: regras.Regras, regua):
    """Saldo = (marca +1 − marca −1) ÷ (os dois), de −100 a +100, só com 5 projetos de marca
    ±1 ou mais (``limiares.saldo_minimo``); mediana e taxa de exceção só com 10 lidos."""
    assert regua.limiares.saldo_minimo == 5 and regua.limiares.perfil_minimo == 10
    assert pais.saldo_de_contas(4, 1, 5) == 60 and pais.saldo_de_contas(0, 5, 5) == -100 and pais.saldo_de_contas(5, 0, 5) == 100
    assert pais.saldo_de_contas(3, 1, 5) is None and pais.saldo_de_contas(0, 0, 0) is None
    # Doze projetos: 1 do topo confirmado (+1), 1 do topo a confirmar (0), 3 do meio (−1, com exceção),
    # 2 pontuais (−1), 4 mínimos (0), e 1 que a ementa não permite ler; e 1 sem leitura nenhuma.
    codigos = [TOPO_2, TOPO_1, MEIO, MEIO, MEIO, PONTUAL, PONTUAL, MINIMO, MINIMO, MINIMO, MINIMO, "fiscal nd nd nd nd nd"]
    props = [_prop(i, f"PL {i}/2025", estado=projetos.NORMA if i == 3 else projetos.TRAMITANDO, movimento="2025-01-01" if i == 4 else "2026-10-01") for i in range(1, 14)]
    lidas = {str(i): _lida(c, r=2 if i == 1 else 1) for i, c in enumerate(codigos, start=1)}
    ctx = _ctx(r, lidas)
    perfil = pais.perfis(ctx, {1: props}, repetidas={})[1]
    linha = pais.linha_do_autor(ctx, perfil)
    assert (linha["n"], linha["tot"], linha["estr"], linha["topo"], linha["set"], linha["simb"], linha["sb"]) == (11, 13, 1, 2, 4, 6, 4)
    assert linha["estr"] + linha["set"] + linha["simb"] == linha["n"]
    assert linha["tip"] == 12  # a mediana de 100, 60, 24, 24, 24, 12, 12, 0, 0, 0, 0
    assert (linha["sc"], linha["sc_n"]) == (round(100 * (1 - 5) / 6), 6)  # +1: o do topo; −1: os 3 do meio e os 2 pontuais
    assert linha["tx"] == round(100 * 3 / 11, 1) and linha["sl"] == round(100 * 2 / 13, 1)
    assert sum(linha["tq"]) == 3 and linha["tq"][0] == 3  # a exceção é para uma profissão
    assert linha["mk"][0] == [1, 5, 5, 0] and [sum(m) for m in linha["mk"]] == [11, 11, 11]
    assert (linha["andou"], linha["norma"], linha["parados"]) == (1, 1, 1)
    assert linha["ad"] == [5, 1, 0, 1, 0]  # de peso 20 ou mais: 5; um virou norma; um parado
    assert sum(linha["de"]) == 2 and sum(linha["ex"]) == 2
    assert [(t[1], t[2], t[6]) for t in linha["top"]] == [("PL 1/2025", 100, 0), ("PL 2/2025", 60, 1), ("PL 3/2025", 24, 0)]
    # Com 9 lidos: sem mediana e sem taxa; com 4 projetos de marca ±1: sem saldo. As contagens ficam.
    poucos = pais.linha_do_autor(ctx, pais.perfis(ctx, {1: [p for p in props if p.id not in (3, 4)]}, repetidas={})[1])
    assert (poucos["n"], poucos["tip"], poucos["tx"], poucos["sc"], poucos["sc_n"]) == (9, None, None, None, 4)
    # O arquivo de todos os projetos: do maior peso para o menor; sem leitura por último, com o motivo.
    arquivo = pais.todos_os_projetos(ctx, perfil)
    cols = arquivo["cols"]
    assert arquivo["k"] == "cd-1" and len(arquivo["linhas"]) == 13
    assert [linha[cols.index("w")] for linha in arquivo["linhas"]] == [100, 60, 24, 24, 24, 12, 12, 0, 0, 0, 0, None, None]
    assert sorted(linha[cols.index("sl")] for linha in arquivo["linhas"][-2:]) == ["falta", "nd"]


def test_a_faixa_do_acaso_do_funil(r: regras.Regras):
    """Quatro pessoas com 40 lidos: uma com 20 do topo confirmado, duas sem nenhum, uma com 1;
    e uma com 5 lidos, que não entra. A taxa da Casa é 21 de 160 = 13,1%; o alfa, 1% ÷ 4
    pessoas. O topo pela ementa (``topo``) não entra na conta: a pessoa "b" tem 30 à espera da
    segunda leitura e fica onde está."""
    linhas = [
        {"k": "a", "n": 40, "estr": 20, "topo": 20, "sb": 10},
        {"k": "b", "n": 40, "estr": 0, "topo": 30, "sb": 10},
        {"k": "c", "n": 40, "estr": 0, "topo": 0, "sb": 10},
        {"k": "d", "n": 40, "estr": 1, "topo": 1, "sb": 10},
        {"k": "e", "n": 5, "estr": 5, "topo": 5, "sb": 0},
    ]
    medido = pais.funil(linhas, minimo=10)
    assert medido["comparados"] == 4 and medido["xmax"] == 40 and medido["taxa"] == {"topo": 13.1, "sb": 25.0}
    assert medido["ymax"] == {"topo": 60, "sb": 40}  # a maior parte é 50% (o primeiro degrau que cobre é 60) e 25%
    assert [linha["fz_topo"] for linha in linhas] == [1, 0, 0, 0, None]  # 0 em 40 com 13%: chance de 0,4%, acima de 0,25%
    assert [linha["fz_sb"] for linha in linhas] == [0, 0, 0, 0, None]
    # O degrau de 40 projetos é a mesma conta: 20 fica fora; 0 e 1 ficam dentro.
    n, de, ate = medido["faixa"]["topo"][-1]
    assert n == 40 and de == 0.0 and ate < 50.0 and [d[0] for d in medido["faixa"]["topo"]] == [40]  # um degrau por número de lidos presente
    assert estat.fora_da_faixa(round(ate * 40 / 100), 40, 21 / 160, 0.01 / 4) == 0 and estat.fora_da_faixa(round(ate * 40 / 100) + 1, 40, 21 / 160, 0.01 / 4) == 1
    # Ninguém com o mínimo: não há taxa nem faixa.
    vazio = pais.funil([{"k": "e", "n": 5, "estr": 5, "topo": 5, "sb": 0}], minimo=10)
    assert vazio["taxa"] == {"topo": None, "sb": None} and vazio["faixa"] == {"topo": [], "sb": []}


def test_a_faixa_do_funil_tem_um_degrau_por_numero_de_lidos_e_contem_quem_nao_passa_do_acaso(r: regras.Regras):
    """A faixa em % sobe e desce de um ``n`` para o outro (os projetos são inteiros): com 30%
    na Casa, quem tem 86 lidos não pode ser julgado pelo degrau de quem tem 81 ou 91. Cada
    pessoa comparada tem o degrau do seu ``n``, e cabe nele se, e só se, ``fz`` é 0: é o que
    faz o desenho (a faixa cinza) e as listas ("passa do acaso") dizerem a mesma coisa."""
    sorteio = random.Random("funil")
    linhas = []
    for i, n in enumerate([*range(10, 61), 64, 68, 73, 77, 81, 86, 86, 91, 97, 150, 400, 1180, 7]):
        sb = min(n, max(0, round(n * sorteio.choice((0.0, 0.1, 0.3, 0.3, 0.3, 0.5, 0.8)))))
        estr = min(n - sb, sorteio.choice((0, 0, 0, 1, 2)))
        linhas.append({"k": str(i), "n": n, "estr": estr, "topo": estr, "sb": sb})
    medido = pais.funil(linhas, minimo=10)
    presentes = sorted({linha["n"] for linha in linhas if linha["n"] >= 10})
    assert medido["comparados"] == len(linhas) - 1 and medido["xmax"] == 1180
    vistos = {0: 0, 1: 0, -1: 0}
    for medida, coluna in pais.FUNIL:
        degraus = {n: (de, ate) for n, de, ate in medido["faixa"][medida]}
        assert [d[0] for d in medido["faixa"][medida]] == presentes  # em ordem, sem repetir (dois têm 86), sem quem não entra (7)
        for linha in linhas:
            if linha["n"] < 10:
                assert linha[f"fz_{medida}"] is None
                continue
            de, ate = degraus[linha["n"]]
            parte = round(100.0 * linha[coluna] / linha["n"], 1)
            dentro = de <= parte <= ate
            assert dentro == (linha[f"fz_{medida}"] == 0), (medida, linha)
            assert (parte > ate) == (linha[f"fz_{medida}"] == 1) and (parte < de) == (linha[f"fz_{medida}"] == -1)
            vistos[linha[f"fz_{medida}"]] += 1
    assert vistos[0] and vistos[1] and vistos[-1]  # o exemplo tem gente dentro, acima e abaixo


def test_as_listas_ordenam_por_proporcao_e_so_com_o_minimo(r: regras.Regras):
    """A pessoa 1 tem 12 lidos, 3 do topo (2 confirmados por duas leituras, 1 à espera da
    segunda); a 2 tem 30, todos mínimos; a 3 tem 3 lidos, todos do topo (poucos projetos: fora
    das listas, dentro das contagens). O que compara pessoas conta só o confirmado."""
    props, lidas = {1: [], 2: [], 3: []}, {}
    for pid, codigos in ((1, [TOPO_1] * 3 + [MEIO] * 9), (2, [MINIMO] * 30), (3, [TOPO_1] * 3)):
        for i, codigo in enumerate(codigos):
            ident = pid * 100 + i
            props[pid].append(_prop(ident, f"PL {ident}/2025", f"Ementa {ident}.", primeiro=pid, estado=projetos.NORMA if (pid, i) == (1, 3) else projetos.TRAMITANDO))
            lidas[str(ident)] = _lida(codigo, r=2 if (pid, i) in ((1, 0), (1, 1)) else 1)
    ctx = _ctx(r, lidas, _cadastros({1: "PT", 2: "PL", 3: "PP", 4: "PP", 5: "PP"}, fora=(5,)))
    bloco, linhas, medido = pais.autores(ctx, pais.perfis(ctx, props, repetidas={}))
    assert [linha["k"] for linha in linhas] == ["cd-1", "cd-2", "cd-3", "cd-4"]  # só quem está em exercício
    esperado = {"em_exercicio": 4, "plotados": 2, "poucos": 2, "sem": 1, "com_estr": 1, "com_topo": 2, "lidos": 45, "total": 45}
    assert {k: bloco[k] for k in esperado} == esperado
    assert (linhas[0]["estr"], linhas[0]["topo"], linhas[0]["set"]) == (2, 3, 10)  # o que espera a segunda leitura conta na faixa de baixo
    assert bloco["taxa"] == medido["taxa"] == {"topo": round(100 * 2 / 42, 1), "sb": round(100 * 30 / 42, 1)}
    assert [(x["k"], x["estr"], x["topo"], x["n"], x["pct"]) for x in bloco["listas"]["mais_estr"]] == [("cd-1", 2, 3, 12, 16.7)]
    assert [x["k"] for x in bloco["listas"]["mais_simb"]] == (["cd-2"] if linhas[1]["fz_sb"] == 1 else [])
    assert bloco["listas"]["norma_peso"] == [{"k": "cd-1", "nome": "Pessoa 01", "pt": "PT", "uf": "SP", "qt": 1, "t": ["PL 103/2025"]}]


# ── na hora do voto ──────────────────────────────────────────────────────────


def _votacao(vid: str, data: str, prop: str, pid: int, votos: str, *, tipo: str = base.MERITO, descricao: str = "Aprovado o Projeto.", aprovada: bool | None = True) -> base.Votacao:
    return base.Votacao(id=vid, data=data, orgao=base.PLENARIO, descricao=descricao, tipo=tipo, votos=votos, hora="15:00", prop=prop, prop_id=pid, ementa=f"Ementa de {prop}.", aprovada=aprovada)


# Dez deputados: 1 a 5 do PT, 6 a 10 do PL. Um caractere por pessoa, nessa ordem.
PESSOAS = [base.Pessoa(id=i, nome=f"Pessoa {i:02d}", uf="SP", partidos=[(0, "PT" if i <= 5 else "PL")]) for i in range(1, 11)]
VOTADAS = {
    "1": _lida("fiscal geral ajuste corta neutro neutro", r=2),  # peso 60, confirmada, caixa +1: voto fiscal +1
    "2": _lida("social geral beneficio gasto neutro neutro", r=2),  # peso 24, confirmada, caixa −1: voto fiscal −1
    "3": _lida("fiscal geral ajuste corta neutro neutro", r=1),  # igual à primeira, com um leitor só: de peso, fora do voto fiscal
    "4": _lida("fiscal geral ajuste corta neutro neutro", r=2),  # quase unânime: fora de tudo
    "5": _lida("fiscal geral ajuste corta neutro afrouxa", r=2),  # caixa +1 e controle −1 na mesma matéria: fora do voto fiscal
    "6": _lida("fiscal grupo diretriz neutro neutro aperta", r=2),  # peso 6, mas na alavanca do caixa, com controle +1: entra
    "7": _lida("fiscal local diretriz neutro neutro aperta", r=2),  # a mesma coisa para um caso só: fora
    "8": _lida("justica grupo pena prisional neutro neutro", r=2),  # peso 10, sem marca: só nas "demais"
    "9": _lida("estado geral ajuste neutro neutro aperta", r=2),  # controle +1 fora da alavanca do caixa: não é marca fiscal
}


def _base_de_votos() -> base.AnoVotos:
    return base.AnoVotos(
        casa="cd",
        ano=2025,
        pessoas=list(PESSOAS),
        votacoes=[
            _votacao("v1", "2025-03-01", "PL 1/2025", 1, "SSSSNNNNNS"),  # PT: 4 S × 1 N (fechado: 80%); PL: 4 N × 1 S (fechado)
            _votacao("v2", "2025-03-02", "PL 2/2025", 2, "SSSSSNNNNN"),
            _votacao("v3", "2025-03-03", "PL 3/2025", 3, "SSSSSNNNNN"),
            _votacao("v4", "2025-03-04", "PL 4/2025", 4, "SSSSSSSSSN"),  # 90% do mesmo lado: fora
            _votacao("v5", "2025-03-05", "PL 5/2025", 5, "SSSSSNNNNN"),
            _votacao("v6", "2025-03-06", "PL 6/2025", 6, "SSSSSNNNNN"),
            _votacao("v7", "2025-03-07", "PL 7/2025", 7, "SSSSSNNNNN"),
            _votacao("v8", "2025-03-08", "PL 8/2025", 8, "SSSSSNNNNN"),
            _votacao("v9", "2025-03-09", "PL 9/2025", 9, "AO-SSNNNNN"),  # abstenção, obstrução e ausência não são voto
            _votacao("v1d", "2025-03-10", "PL 1/2025", 1, "NNNNNSSSSS", tipo=base.DESTAQUE),  # destaque: fora do universo
            _votacao("v1r", "2025-03-11", "PL 1/2025", 1, "SSSSSSSSSS", descricao="Aprovada a Redação Final."),  # redação final: não é a do texto
            _votacao("v10", "2025-03-12", "PL 10/2025", 10, "SSSSSNNNNN"),  # sem leitura
            _votacao("v11", "2025-03-13", "PDL 11/2025", 11, "SSSSSNNNNN"),  # decreto legislativo: tipo que não entra
        ],
    )


def test_a_marca_fiscal_so_com_leitura_confirmada_e_marca_legivel(r: regras.Regras, regua):
    ctx = _ctx(r, VOTADAS)

    def marca(chave: str) -> int:
        lida = VOTADAS[chave]
        return pais_voto.marca_fiscal(ctx, leitura.avaliar(regua, lida, tipo="PL"), lida)

    assert [marca(k) for k in "123456789"] == [1, -1, 0, 1, 0, 1, 0, 0, 0]
    assert pais_voto.marca_fiscal(ctx, leitura.avaliar(regua, None), None) == 0


def test_o_universo_do_voto_e_o_voto_fiscal(r: regras.Regras, regua):
    """As 13 votações de cima: 10 matérias com votação do texto principal (o PL 1 tem três
    votações: vale a do texto, não o destaque nem a redação final; o PDL não entra); 1 quase
    unânime; das 9 que ficam, 8 lidas. De peso (20 ou mais): PL 1, 2, 3, 5 e 9. No voto fiscal:
    PL 1 (+1), PL 2 (−1) e PL 6 (+1, pelo controle na alavanca do caixa)."""
    ctx = _ctx(r, VOTADAS, _cadastros({i: "PT" if i <= 5 else "PL" for i in range(1, 11)}))
    votadas, conta = pais_voto.universo(ctx, [_base_de_votos()])
    assert conta == {"materias": 10, "sem_texto": 0, "quase_unanimes": 1, "universo": 9, "lidas": 8, "sem_leitura": 1}
    por_titulo = {x.titulo: x for x in votadas}
    assert por_titulo["PL 1/2025"].v.id == "v1" and "PL 4/2025" not in por_titulo and "PDL 11/2025" not in por_titulo
    assert sorted(x.titulo for x in votadas if x.de_peso) == ["PL 1/2025", "PL 2/2025", "PL 3/2025", "PL 5/2025", "PL 9/2025"]
    assert {x.titulo: x.marca for x in votadas if x.marca} == {"PL 1/2025": 1, "PL 2/2025": -1, "PL 6/2025": 1}
    assert [x.titulo for x in votadas][:2] == ["PL 10/2025", "PL 9/2025"]  # da mais recente para a mais antiga
    # O lado de cada partido só com Sim e Não: no PL 9, o PT tem 2 votos (abaixo do mínimo de 4): sem lado.
    assert por_titulo["PL 1/2025"].lados == {"PT": (base.SIM, 0.8), "PL": (base.NAO, 0.8)} and por_titulo["PL 1/2025"].contra == 2
    assert por_titulo["PL 9/2025"].lados == {"PL": (base.NAO, 1.0)}

    bloco, colunas, arquivo = pais_voto.calcular(ctx, [_base_de_votos()])
    assert (bloco["n_votacoes"], bloco["de_peso"], bloco["demais"], bloco["fiscais"], bloco["confirmadas"]) == (6, 5, 3, 3, 7)
    assert (bloco["favorece"], bloco["contraria"], bloco["aprovadas_favorece"], bloco["aprovadas_contraria"]) == (2, 1, 2, 1)
    assert [v[arquivo["cols"].index("t")] for v in arquivo["votacoes"]] == ["PL 9/2025", "PL 6/2025", "PL 5/2025", "PL 3/2025", "PL 2/2025", "PL 1/2025"]
    # As demais (lidas, de peso menor) são os PL 6, 7 e 8; o PL 6 está no voto fiscal e por isso vai na lista de cima.
    assert [v[3] for v in arquivo["demais"]] == ["PL 8/2025", "PL 7/2025"]
    # Pessoa 1 (PT): Sim no +1 (PL 1), Sim no −1 (PL 2), Sim no +1 (PL 6): 2 de 3 com a marca.
    assert (colunas[1]["va"], colunas[1]["vn"]) == (2, 3)
    # Pessoa 6 (PL): Não no +1, Não no −1, Não no +1: 1 de 3. Pessoa 5 (PT): Não no PL 1 (contra o PT fechado e contra a marca).
    assert (colunas[6]["va"], colunas[6]["vn"]) == (1, 3)
    assert (colunas[5]["va"], colunas[5]["vn"], colunas[5]["div"], colunas[5]["div_cont"]) == (1, 3, 1, 0)
    # Pessoa 10 (PL): Sim no PL 1 contra o PL fechado no Não, e com a marca (+1).
    assert (colunas[10]["va"], colunas[10]["vn"], colunas[10]["div"], colunas[10]["div_cont"]) == (2, 3, 1, 1)
    # Abstenção e obstrução não são voto: no PL 9, as pessoas 1 e 2 não têm voto dado (a 3 faltou).
    assert (colunas[1]["wn"], colunas[2]["wn"], colunas[3]["wn"], colunas[4]["wn"]) == (5, 5, 5, 6)
    assert arquivo["votos"]["cd-1"] == "ASSSSS" and arquivo["votos"]["cd-2"] == "OSSSSS" and arquivo["votos"]["cd-3"] == "-SSSSS"
    assert arquivo["contra"] == {"cd-10": [5], "cd-5": [5]}  # a posição do PL 1 na lista
    # Menos de 10 votos: as porcentagens por pessoa não saem (ficam as contagens), e a potência diz isso.
    assert colunas[1]["fp"] is None and colunas[1]["fp_n"] == 4 and colunas[1]["pp"] is None
    assert bloco["potencia"] == {"minimo_votos": 10, "minimo_pessoas": 50, "peso": 0, "fiscal": 0, "por_pessoa": {"peso": False, "fiscal": False}}
    # Com o partido fechado: 10 votos no PL 1 (8 seguiram), 10 em cada um dos PL 2, 3, 5 e 6, e os 5 do PL no PL 9.
    assert (bloco["com_partido_n"], bloco["com_partido_pct"]) == (55, round(100 * 53 / 55, 1))
    # Por partido: em quantas votações do voto fiscal a maioria da bancada ficou com a marca.
    partidos = {p["pt"]: p for p in bloco["por_partido"]}
    assert partidos["PT"]["mm"] == [2, 3] and partidos["PL"]["mm"] == [1, 3] and partidos["PT"]["vf"] is None
    assert [d["k"] for d in bloco["divergentes"]] == []  # ninguém com 10 votos dados
    assert "sel" not in json.dumps(bloco)


def test_a_emenda_da_outra_casa_nao_e_o_texto_principal(r: regras.Regras):
    """O PL 1 (voto fiscal +1) tem a votação do texto e, meses depois, a da emenda do Senado:
    vale a do texto (na emenda, o Não rejeita a emenda, não a matéria). O PL 2 só tem a do
    substitutivo do Senado: fica fora do universo, contado em ``sem_texto``."""
    ano = base.AnoVotos(
        casa="cd",
        ano=2025,
        pessoas=list(PESSOAS),
        votacoes=[
            _votacao("v1", "2025-03-01", "PL 1/2025", 1, "SSSSSNNNNN", descricao="Aprovada a Subemenda Substitutiva Global ao Projeto de Lei nº 1, de 2025, ressalvados os destaques."),
            _votacao("v1s", "2025-09-01", "PL 1/2025", 1, "NNNNNNNSSS", descricao="Rejeitada a Emenda do Senado Federal nº 1. Sim: 3; não: 7; total: 10.", aprovada=False),
            _votacao("v2s", "2025-09-02", "PL 2/2025", 2, "SSSSSNNNNN", descricao="Aprovado o Substitutivo do Senado Federal ao Projeto de Lei nº 2, de 2025."),
        ],
    )
    ctx = _ctx(r, VOTADAS, _cadastros({i: "PT" if i <= 5 else "PL" for i in range(1, 11)}))
    votadas, conta = pais_voto.universo(ctx, [ano])
    assert [(x.titulo, x.v.id, x.marca) for x in votadas] == [("PL 1/2025", "v1", 1)]
    assert (conta["materias"], conta["sem_texto"], conta["universo"]) == (1, 1, 1)
    _bloco, colunas, _arquivo = pais_voto.calcular(ctx, [ano])
    # Pessoa 6 (PL): Não no texto (contra a marca +1); o Não dela à emenda do Senado não conta como voto fiscal.
    assert (colunas[1]["va"], colunas[1]["vn"], colunas[6]["va"], colunas[6]["vn"]) == (1, 1, 0, 1)


def test_o_partido_que_obstrui_nao_tem_lado(r: regras.Regras):
    """Seis do partido em obstrução e quatro votando Sim: os quatro não são "o partido"."""
    v = _votacao("v", "2025-03-01", "PL 1/2025", 1, "OOOOOOSSSS")
    assert pais_voto.lados_dos_partidos(v, ["PT"] * 10, 4) == {}
    assert pais_voto.lados_dos_partidos(_votacao("v", "2025-03-01", "PL 1/2025", 1, "OOSSSSSSSN"), ["PT"] * 10, 4) == {"PT": (base.SIM, 0.875)}
    assert pais_voto.lados_dos_partidos(_votacao("v", "2025-03-01", "PL 1/2025", 1, "SSNN"), ["PT"] * 4, 4) == {}  # empate


def test_quando_pesa_presenca_e_voto_com_o_partido_ao_lado_das_demais(r: regras.Regras, regua):
    """Com 12 votações de peso e 10 das demais, as porcentagens saem. A pessoa 1 falta a 3 das
    de peso (presença 75%) e a nenhuma das demais; a pessoa 5 vota contra a maioria do PT em 6
    das 12, e em 5 delas o PT estava fechado (na que a pessoa 1 faltou, eram 3 contra 1: 75%)."""
    lidas, votacoes = {}, []
    for i in range(1, 23):
        de_peso = i <= 12
        lidas[str(i)] = _lida("produtividade geral ajuste neutro neutro neutro" if de_peso else PONTUAL)
        votos = list("SSSSSNNNNN")
        if de_peso and i <= 3:
            votos[0] = "-"
        if de_peso and i % 2 == 0:
            votos[4] = "N"
        votacoes.append(_votacao(f"v{i}", f"2025-04-{i:02d}", f"PL {i}/2025", i, "".join(votos)))
    ctx = _ctx(r, lidas, _cadastros({i: "PT" if i <= 5 else "PL" for i in range(1, 11)}))
    exercicio = {i: [("2025-01-01", "2026-12-31")] for i in range(1, 11)}
    bloco, colunas, _arquivo = pais_voto.calcular(ctx, [base.AnoVotos(casa="cd", ano=2025, pessoas=list(PESSOAS), votacoes=votacoes)], exercicio=exercicio)
    assert (bloco["de_peso"], bloco["demais"], bloco["fiscais"]) == (12, 10, 0)
    assert (colunas[1]["pp"], colunas[1]["pp_n"], colunas[1]["pd"], colunas[1]["pd_n"]) == (75.0, 12, 100.0, 10)
    assert (colunas[5]["fp"], colunas[5]["fp_n"], colunas[5]["fd"], colunas[5]["fd_n"], colunas[5]["div"], colunas[5]["wn"]) == (50.0, 12, 100.0, 10, 5, 12)
    assert bloco["quando_pesa"] == {"pres": [round(100 * 117 / 120, 1), 100.0], "fid": [round(100 * 111 / 117, 1), 100.0]}
    assert bloco["potencia"]["peso"] == 9 and bloco["potencia"]["por_pessoa"]["peso"] is False  # 9 pessoas com 10 votos (a 1 deu 9): menos que as 50 do mínimo
    assert bloco["divergentes"] == [{"k": "cd-5", "nome": "Pessoa 05", "pt": "PT", "uf": "SP", "div": 5, "div_cont": 0, "vn": 12}]
    # Por partido: a mediana e os quartis só com 5 pessoas medidas (limiares.partido_minimo). No PT a pessoa 1 deu
    # 9 votos nas de peso (não tem o percentual de voto com o partido): sobram 4, e a distribuição não sai.
    partidos = {p["pt"]: p for p in bloco["por_partido"]}
    assert partidos["PT"]["fp"] is None and partidos["PL"]["fp"] == [5, 100.0, 100.0, 100.0]
    assert partidos["PT"]["pp"][0] == 5 and partidos["PT"]["vf"] is None
    assert pais_voto._distribuicao([10.0, 20.0, 30.0, 40.0], 5) is None and pais_voto._distribuicao([10.0, 20.0, 30.0, 40.0, 50.0], 5) == [5, 30.0, 20.0, 40.0]


# ── pessoa ou partido? ───────────────────────────────────────────────────────


def _linhas_de(valores: dict[str, list[float]], coluna: str = "saldo") -> list[dict]:
    return [{"k": f"cd-{i}", "pt": partido, "n": 0, coluna: v} for i, (partido, v) in enumerate((p, v) for p, vs in valores.items() for v in vs)]


def test_a_regra_de_potencia_sem_50_pessoas_nao_ha_numero(regua):
    """48 pessoas em 4 partidos em que o partido explica tudo: ainda assim, com menos de 50
    (``limiares.pessoas_minimo``), o dado diz "sem como dizer". Com 60, o número sai."""
    assert regua.limiares.pessoas_minimo == 50
    poucas = _linhas_de({"A": [10.0] * 12, "B": [20.0] * 12, "C": [30.0] * 12, "D": [40.0] * 12})
    m = pais_cruzar.medida("saldo", "voto", [linha["saldo"] for linha in poucas], [linha["pt"] for linha in poucas], regua, "cd")
    assert (m["n"], m["k"], m["e2"], m["ic"], m["acaso"], m["zona"], m["distingue"], m["motivo"]) == (48, 4, None, None, None, "sem_como_dizer", False, "poucas_pessoas")
    bloco = pais_cruzar.calcular(poucas, regua, "cd")
    assert bloco["frase"]["caso"] == "sem_como_dizer" and (bloco["frase"]["n"], bloco["frase"]["k"]) == (48, 4)
    assert all(par["rho"] is None and par["sem"] == 1 and not par["passa"] for par in bloco["pares"])


def test_o_partido_explica_tudo_no_voto_e_nada_nos_projetos(regua):
    """60 pessoas em 4 partidos. Lado em que vota: igual dentro do partido (o partido explica
    tudo). Parte do topo nos projetos: os mesmos valores em todos os partidos (nada)."""
    linhas = []
    for partido, saldo in (("A", -80.0), ("B", -20.0), ("C", 30.0), ("D", 90.0)):
        for i in range(15):
            linhas.append({"k": f"cd-{partido}{i}", "pt": partido, "saldo": saldo, "n": 20, "estr": i % 5, "topo": 10, "sb": 0, "fid": 90.0})
    bloco = pais_cruzar.calcular(linhas, regua, "cd")
    por_id = {m["id"]: m for m in bloco["medidas"]}
    assert [m["id"] for m in bloco["medidas"]] == [ident for ident, _g, _v in pais_cruzar.MEDIDAS]
    v, p = por_id["saldo"], por_id["topo"]
    assert (v["n"], v["k"], v["e2"], v["zona"], v["distingue"]) == (60, 4, 100.0, "quase_tudo", True) and v["ic"] == [100.0, 100.0] and v["acaso"] < 20
    assert (p["n"], p["e2"], p["zona"], p["distingue"]) == (60, 0.0, "pouco", False) and p["ic"][0] == 0.0
    assert por_id["fid"]["zona"] == "sem_como_dizer" and por_id["fid"]["motivo"] == "sem_variacao"  # todos iguais: nada a explicar
    assert por_id["vf"]["motivo"] == "poucas_pessoas"  # ninguém tem voto fiscal
    assert bloco["frase"] == {"caso": "voto_sim_projetos_nao", "voto": 100.0, "projetos": 0.0, "voto_distingue": True, "projetos_distingue": False, "n": 60, "k": 4}
    assert (bloco["pessoas"], bloco["partidos"]) == (60, 4)
    assert bloco["parametros"] == {"zonas": [30, 70], "permutacoes": 1000, "reamostras": 2000, "semente": 57, "confianca": 0.95, "pessoas_minimo": 50, "alfa_pares": 0.01}
    # De um dia para o outro, com os mesmos dados, os mesmos números.
    assert pais_cruzar.calcular(linhas, regua, "cd")["medidas"] == bloco["medidas"]
    # Por partido: a mediana de cada medida (com 5 pessoas ou mais).
    assert bloco["por_partido"][0] == {"pt": "A", "n": 15, "med": {**{ident: None for ident, _g, _v in pais_cruzar.MEDIDAS}, "saldo": -80.0, "fid": 90.0, "prop": 0, "topo": 10.0, "sb": 0.0}}
    # O par "lado em que vota × parte do topo": sem relação nenhuma (rho 0), e não passa do acaso.
    par = next(x for x in bloco["pares"] if (x["a"], x["b"]) == ("saldo", "topo"))
    # Dentro do partido todos votam igual: não há o que correlacionar ("dentro" fica nulo).
    assert par["n"] == 60 and abs(par["rho"]) < 0.01 and par["dentro"] is None and not par["passa"] and par["p"] > 0.9


def test_os_casos_da_frase():
    zonas = (30.0, 70.0)

    def caso(v: float | None, p: float | None, *, v_distingue: bool = True, p_distingue: bool = True) -> tuple[str, bool]:
        medidas = [{"id": "saldo", "e2": v, "distingue": v_distingue, "n": 500, "k": 20}, {"id": "topo", "e2": p, "distingue": p_distingue}]
        f = pais_cruzar.frase(medidas, zonas)
        return f["caso"], f["projetos_distingue"]

    assert caso(76.0, 4.0) == ("voto_sim_projetos_nao", True)
    assert caso(76.0, 45.0) == ("voto_sim_projetos_parte", True)
    assert caso(76.0, 70.0) == ("os_dois", True)
    assert caso(69.9, 4.0) == ("nem_o_voto", True)
    assert caso(76.0, 4.0, v_distingue=False) == ("sem_como_dizer", True)
    assert caso(None, 4.0, v_distingue=False) == ("sem_como_dizer", True)
    assert caso(76.0, None, p_distingue=False) == ("voto_sim_projetos_nao", False)  # a tela troca a segunda metade da frase
    assert pais_cruzar.zona(70.0, zonas) == "quase_tudo" and pais_cruzar.zona(30.0, zonas) == "parte" and pais_cruzar.zona(29.9, zonas) == "pouco"


def test_o_manifesto_tira_os_rotulos_do_config_e_segue_a_ordem_das_colunas(r: regras.Regras, regua):
    """O que a página usa para rotular: nada escrito no Python. A ordem das listas é a das
    colunas dos arquivos (``ex``, ``de``, ``mk``, ``tq``)."""
    m = pais.manifesto(r)
    assert m["selo"] == regua.selo and m["fontes"] == regua.fontes and m["alertas"] == regua.alertas
    assert [f[0] for f in m["faixas"]] == [f.id for f in regua.faixas] and [f[2] for f in m["faixas"]] == [0, 10, 20, 60]
    # As faixas agrupadas pela estrutura da régua: o topo é a que pede confirmação; a mínima, a primeira.
    assert m["grupos"] == {"topo": [regua.faixas[-1].id], "baixas": [regua.faixas[0].id, regua.faixas[1].id], "piso": regua.faixas[0].id}
    assert [e[0] for e in m["eixos"]] == list(regua.alavanca.ids) and len(m["eixos"]) == 10
    assert [d[3] for d in m["direcoes"]] == [1, 0, -1, None] and [d[2] for d in m["direcoes"]] == ["cheio", "vazado", "losango", "vazado"]
    assert [marca[0] for marca in m["marcas"]] == [marca.id for marca in regua.marcas] and len(m["excecao_para"]) == 3
    assert [p[0] for p in m["portas"]] == list(pais.PORTAS) and m["cortes"]["voto_peso"] == regua.limiares.voto_peso
    assert set(m["campos"]) == {c.id for c in regua.campos} and m["arq"]["autores"] == "autores-{casa}.json"
    assert [ident for ident, _grupo in m["medidas"]] == [ident for ident, _g, _v in pais_cruzar.MEDIDAS]
    # O funil diz de que coluna sai cada medida: a parte do topo conta só o confirmado ("estr"), e as duas colunas existem no arquivo.
    assert m["funil"] == [["topo", "estr"], ["sb", "sb"]] and all(coluna in pais.COLUNAS_DO_AUTOR and f"fz_{medida}" in pais.COLUNAS_DO_AUTOR for medida, coluna in m["funil"])
    sem_bloco = fabrica.regras_de_teste()
    assert pais.manifesto(sem_bloco) is None
    json.dumps(m)  # vai inteiro para a página


# ── a atualização de ponta a ponta ───────────────────────────────────────────

NOVOS = ("projetos-cd.json", "autores-cd.json", "decide-cd.json", "projetos-sf.json", "autores-sf.json", "decide-sf.json")


def _arquivos(pasta: Path) -> dict[str, str]:
    return {str(a.relative_to(pasta)): a.read_text(encoding="utf-8") for a in sorted(pasta.rglob("*.json")) if "leitura" not in a.relative_to(pasta).parts}


def test_sem_leituras_nada_e_gravado_e_com_elas_nada_do_que_existia_muda(tmp_path: Path, r: regras.Regras, regua):
    """O Congresso de mentira (tests/fixtures/congresso). Com o bloco da régua no config e a
    guarda vazia, a atualização sai idêntica à de sempre. Com seis leituras da Câmara na guarda,
    entram o bloco ``lp`` da Câmara, os arquivos novos e ``meta.leitura_projetos``, e todo o
    resto fica igual, byte a byte. O Senado, sem leitura nenhuma, fica sem o bloco."""
    com_regua = fabrica.regras_de_teste()
    com_regua.projeto_de_pais = regua
    opcoes = dados.Opcoes(hoje=fabrica.HOJE, projetos_desde=2026)
    data_dir = tmp_path / "data"
    pasta = data_dir / "congresso"

    sem_bloco = tmp_path / "sem-bloco"
    dados.atualizar(sem_bloco, sem_bloco / "data", sem_bloco / "cache", opcoes, fetch=fabrica.FetchFalso(), regras=fabrica.regras_de_teste())
    resultado = dados.atualizar(tmp_path, data_dir, tmp_path / "cache", opcoes, fetch=fabrica.FetchFalso(), regras=com_regua)
    antes = _arquivos(pasta)
    assert resultado.avisos == [] and antes == _arquivos(sem_bloco / "data" / "congresso")
    assert not any((pasta / nome).exists() for nome in NOVOS) and not (pasta / "autoria").exists()
    assert "lp" not in json.loads(antes["casa-cd.json"]) and "leitura_projetos" not in json.loads(antes["meta.json"])

    # Seis leituras da Câmara: o PL 10 (votado, 6 × 4; de Ana Lima) confirmado no topo e com marca +1;
    # o PL 11 (votado, 7 × 3) pontual; o PL 12 (do Executivo, virou norma, votado por unanimidade);
    # a PEC 1 (peso 60 fora do núcleo: meio); o PL 13 (arquivado, de dois do PT: de bancada) e o PL 14
    # (peso 60 no núcleo com um leitor só: a confirmar).
    guarda = leitura.Guarda(regua)
    for chave, lida in {
        "1010": _lida("fiscal geral ajuste corta neutro neutro", r=2, porque="Corrige a tabela do imposto."),
        "1011": _lida("justica grupo pena prisional neutro neutro"),
        "1012": _lida("infra local servico investe neutro neutro"),
        "1013": _lida("institucional geral sistema neutro neutro neutro"),
        "1014": _lida(MINIMO),
        "1015": _lida("produtividade setor sistema neutro neutro neutro"),
    }.items():
        guarda.registrar("cd", 2026, chave, lida)
    guarda.gravar(pasta / "leitura")
    (pasta / "leitura" / "auditoria.json").write_text(json.dumps({"modelo": "leitor de teste", "amostra": 3, "concordancia": 0.9}), encoding="utf-8")

    resultado = dados.atualizar(tmp_path, data_dir, tmp_path / "cache", opcoes, fetch=fabrica.FetchFalso(), regras=com_regua)
    assert resultado.avisos == []
    depois = _arquivos(pasta)
    # Nada do que existia muda: os mesmos arquivos, com o mesmo conteúdo, tirados só o bloco e a chave novos.
    casa_cd, meta = json.loads(depois["casa-cd.json"]), json.loads(depois["meta.json"])
    lp, lido = casa_cd.pop("lp"), meta.pop("leitura_projetos")
    assert list(casa_cd) == list(json.loads(antes["casa-cd.json"])) and casa_cd == json.loads(antes["casa-cd.json"])
    assert meta == json.loads(antes["meta.json"])
    novos = set(depois) - set(antes)
    assert novos == {"projetos-cd.json", "autores-cd.json", "decide-cd.json", "autoria/cd-1.json", "autoria/cd-4.json"}
    assert all(depois[nome] == antes[nome] for nome in antes if nome not in ("casa-cd.json", "meta.json"))
    assert "lp" not in json.loads(depois["casa-sf.json"])

    # O estoque: tramitam o PL 10, o PL 11, a PEC 1 e o PL 14 (o PL 15 anda junto; o PL 13 foi arquivado).
    estoque = lp["estoque"]
    assert {k: estoque[k] for k in ("tramitando", "lidos", "estr", "topo", "setorial", "baixo", "plotados")} == {"tramitando": 4, "lidos": 4, "estr": 1, "topo": 2, "setorial": 2, "baixo": 1, "plotados": 2}
    projetos_cd = json.loads(depois["projetos-cd.json"])
    linhas = [dict(zip(projetos_cd["cols"], linha, strict=True)) for linha in projetos_cd["linhas"]]
    assert [(x["t"], x["w"], x["fo"], x["al"], x["a"], x["an"], x["f"]) for x in linhas] == [
        ("PL 10/2026", 60, "ia2", [], "cd-1", "Ana Lima", 1),
        ("PL 14/2026", 60, "ia", ["a_confirmar"], "cd-1", "Ana Lima", 1),  # pronto para a pauta: tem ficha na página
    ]
    assert lp["cobertura"]["total"] == 6 and lp["cobertura"]["lidos"] == 6 and lp["cobertura"]["confirmados"] == 1
    assert lp["cobertura"]["voto"] == {"total": 3, "lidos": 3} and lp["cobertura"]["espera"] == {"total": 4, "lidos": 4}

    # Quem propõe: Ana Lima (1) assina o PL 10, o PL 13 (de bancada: dois dos três do PT) e o PL 14.
    autores = json.loads(depois["autores-cd.json"])
    assert autores["cols"] == pais.COLUNAS_DO_AUTOR and len(autores["linhas"]) == 11
    ana = dict(zip(autores["cols"], next(linha for linha in autores["linhas"] if linha[0] == "cd-1"), strict=True))
    assert (ana["nome"], ana["pt"], ana["n"], ana["estr"], ana["topo"], ana["set"], ana["simb"], ana["bc"], ana["tot"], ana["fz_topo"]) == ("Ana Lima", "PT", 2, 1, 2, 1, 0, 1, 2, None)
    resumo = {p["k"]: p for p in json.loads(depois["resumo.json"])["pessoas"]}
    assert all(ana[campo] == resumo["cd-1"].get(campo) for campo in pais.DO_RESUMO)
    autoria = json.loads(depois["autoria/cd-1.json"])
    assert [(linha[1], linha[autoria["cols"].index("x")]) for linha in autoria["linhas"]] == [("PL 10/2026", ""), ("PL 14/2026", ""), ("PL 13/2026", "banc")]

    # Na hora do voto: três matérias votadas no texto principal; a do PL 12 é unânime e sai; o PL 10 é de
    # peso e está no voto fiscal (+1); o PL 11 fica nas demais.
    decide = lp["decide"]
    assert {k: decide[k] for k in ("materias", "quase_unanimes", "universo", "lidas", "n_votacoes", "de_peso", "demais", "fiscais", "favorece")} == {
        "materias": 3, "quase_unanimes": 1, "universo": 2, "lidas": 2, "n_votacoes": 1, "de_peso": 1, "demais": 1, "fiscais": 1, "favorece": 1,
    }  # fmt: skip
    arquivo = json.loads(depois["decide-cd.json"])
    votacao = dict(zip(arquivo["cols"], arquivo["votacoes"][0], strict=True))
    assert (votacao["id"], votacao["t"], votacao["w"], votacao["mf"], votacao["sim"], votacao["nao"], votacao["res"], votacao["contra_partido"]) == ("1010-1", "PL 10/2026", 60, 1, 6, 4, "aprovado", 1)
    assert arquivo["votos"]["cd-1"] == "S" and arquivo["votos"]["cd-4"] == "N" and arquivo["contra"] == {"cd-10": [0]}  # o 10 vota Não com o PP fechado no Sim
    assert (ana["va"], ana["vn"], ana["wn"], ana["div"]) == (1, 1, 1, 0)
    assert [linha[3] for linha in arquivo["demais"]] == ["PL 11/2026"]
    # Com 11 deputados não há como responder por pessoa: nenhuma medida tem número.
    assert all(m["zona"] == "sem_como_dizer" for m in lp["cruzar"]["medidas"]) and lp["cruzar"]["frase"]["caso"] == "sem_como_dizer"
    # O meta repassa a auditoria escrita por fora e diz a cobertura e os cortes.
    assert lido["auditoria"] == {"modelo": "leitor de teste", "amostra": 3, "concordancia": 0.9} and lido["modelo"] == "leitor de teste"
    assert lido["versao_da_regua"] == regua.leitura.versao and lido["data"] == fabrica.HOJE and list(lido["cobertura"]) == ["cd"]
    assert lido["cobertura"]["cd"] == lp["cobertura"] and lido["cortes"]["faixas"] == [[f.id, f.de] for f in regua.faixas]

    # Rodar de novo sem mudança não regrava nada; sem o bloco no config, nada novo sai (o que já estava fica).
    assert dados.atualizar(tmp_path, data_dir, tmp_path / "cache", opcoes, fetch=fabrica.FetchFalso(), regras=com_regua).gravados == 0


def test_um_erro_na_visao_vira_aviso_e_o_resto_sai(tmp_path: Path, regua, monkeypatch: pytest.MonkeyPatch):
    com_regua = fabrica.regras_de_teste()
    com_regua.projeto_de_pais = regua
    pasta = tmp_path / "data" / "congresso"
    guarda = leitura.Guarda(regua)
    guarda.registrar("cd", 2026, "1010", _lida(MINIMO))
    guarda.gravar(pasta / "leitura")

    def quebra(*_a, **_k):
        raise RuntimeError("conta quebrada")

    monkeypatch.setattr(pais, "da_casa", quebra)
    resultado = dados.atualizar(tmp_path, tmp_path / "data", tmp_path / "cache", dados.Opcoes(hoje=fabrica.HOJE, projetos_desde=2026), fetch=fabrica.FetchFalso(), regras=com_regua)
    assert resultado.avisos == ["Visão por parlamentar indisponível (conta quebrada)"]
    meta = json.loads((pasta / "meta.json").read_text(encoding="utf-8"))
    assert meta["avisos"] == resultado.avisos and "leitura_projetos" not in meta
    assert "lp" not in json.loads((pasta / "casa-cd.json").read_text(encoding="utf-8")) and not (pasta / "projetos-cd.json").exists()
    assert len(list((pasta / "fichas").glob("*.json"))) == 15  # o resto saiu inteiro
