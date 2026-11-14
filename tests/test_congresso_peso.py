"""Saldo pesado pela relevância da matéria (qijournal.congresso.peso)."""

from __future__ import annotations

from qijournal.congresso import base, peso, regras


def _regras() -> regras.Regras:
    return regras.de_dict(
        {
            "legislaturas": [{"numero": 57, "inicio": "2026-01-01", "fim": "2027-01-31"}],
            "polos": [{"inicio": "2019-01-01", "esquerda": ["PT"], "direita": ["PL"]}],
            "limiares": {"coesao_polo": 0.75, "minimo_polo": 2, "minimo_divisivas": 2, "minimo_partido": 2},
        }
    )


def _ano(votacoes: list[tuple[str, str, str]]) -> base.AnoVotos:
    """Dois do PT, dois do PL e o deputado 5 (PP). Cada votação: (matéria, tipo, votos)."""
    pessoas = [base.Pessoa(i, f"Deputado {i}", "SP", [(0, sigla)]) for i, sigla in ((1, "PT"), (2, "PT"), (3, "PL"), (4, "PL"), (5, "PP"))]
    return base.AnoVotos(
        casa="cd",
        ano=2026,
        pessoas=pessoas,
        votacoes=[
            base.Votacao(id=f"v{k}", data=f"2026-03-{k + 1:02d}", orgao=base.PLENARIO, descricao="Aprovado o Projeto.", tipo=tipo, votos=votos, prop=prop)
            for k, (prop, tipo, votos) in enumerate(votacoes)
        ],
    )


def test_cada_voto_pesa_a_nota_da_materia():
    """O deputado 5 vota com a esquerda em três matérias pequenas (nota 10) e com a direita
    numa grande (nota 90): o saldo simples é −50 (1 × 3), o pesado é +50 ((90 − 30) ÷ 120).
    A mediana das notas (10) separa as metades: nota 10 ou mais é "alta" (as quatro)."""
    r = _regras()
    ano = _ano(
        [
            ("PL 1/2026", base.MERITO, "SSNNS"),
            ("PL 2/2026", base.MERITO, "SSNNS"),
            ("PL 3/2026", base.MERITO, "SSNNS"),
            ("PEC 4/2026", base.MERITO, "SSNNN"),
        ]
    )
    notas = {"PL 1/2026": 10, "PL 2/2026": 10, "PL 3/2026": 10, "PEC 4/2026": 90}
    pessoas, cobertura = peso.calcular([ano], r, 57, notas)
    cinco = pessoas[5]
    assert (cinco.eixo.esq, cinco.eixo.dir, cinco.eixo.saldo) == (3, 1, -50.0)
    assert cinco.saldo == 50.0
    assert cobertura == {"divisivas": 4, "com_nota": 4, "corte": 10.0, "alta": 4, "baixa": 0}
    resumo = peso.resumo(cinco, minimo=2)
    assert resumo["saldo"] == 50 and resumo["n"] == 4 and resumo["de"] == 4
    assert "faixas" not in resumo  # menos de 5 votações numa metade: sem o saldo dela
    # Quem vota sempre do mesmo lado fica onde está, com ou sem peso.
    assert pessoas[1].saldo == -100.0 and pessoas[3].saldo == 100.0


def test_so_entram_votacoes_divisivas_de_conteudo_com_nota():
    """A matéria sem nota (a MP) conta em "de", não no saldo; a de consenso e o requerimento
    não contam em nada."""
    r = _regras()
    ano = _ano(
        [
            ("PL 1/2026", base.MERITO, "SSNNS"),  # divisiva, com nota
            ("MPV 2/2026", base.MERITO, "SSNNN"),  # divisiva, sem nota
            ("PL 3/2026", base.MERITO, "SSSSN"),  # consenso: não divide
            ("PL 4/2026", base.PROCEDIMENTO, "SSNNN"),  # requerimento: não é conteúdo
        ]
    )
    pessoas, cobertura = peso.calcular([ano], r, 57, {"PL 1/2026": 50, "PL 3/2026": 50, "PL 4/2026": 50})
    assert (pessoas[5].todas, pessoas[5].eixo.n, pessoas[5].saldo) == (2, 1, -100.0)
    assert cobertura["divisivas"] == 2 and cobertura["com_nota"] == 1
    # Com menos votações com nota que o mínimo, a ficha não traz o número (só a contagem).
    assert "saldo" not in peso.resumo(pessoas[5], minimo=2)


def test_as_metades_cortam_na_mediana_da_casa():
    """Seis votações: três de nota 10 (com a esquerda) e três de nota 80 (com a direita)…
    e mais quatro de cada, para passar do mínimo de cinco por metade. Mediana: 45."""
    r = _regras()
    votacoes = [(f"PL {k}/2026", base.MERITO, "SSNNS") for k in range(1, 8)] + [(f"PEC {k}/2026", base.MERITO, "SSNNN") for k in range(1, 8)]
    notas = {f"PL {k}/2026": 10 for k in range(1, 8)} | {f"PEC {k}/2026": 80 for k in range(1, 8)}
    pessoas, cobertura = peso.calcular([_ano(votacoes)], r, 57, notas)
    assert cobertura["corte"] == 45.0 and cobertura["alta"] == 7 and cobertura["baixa"] == 7
    resumo = peso.resumo(pessoas[5], minimo=2)
    assert resumo["faixas"]["alta"] == {"esq": 0, "dir": 7, "n": 7, "saldo": 100}
    assert resumo["faixas"]["baixa"] == {"esq": 7, "dir": 0, "n": 7, "saldo": -100}
    assert pessoas[5].eixo.saldo == 0.0 and round(pessoas[5].saldo) == 78  # (560 − 70) ÷ 630


def test_nota_zero_nao_pesa():
    r = _regras()
    ano = _ano([("PL 1/2026", base.MERITO, "SSNNS"), ("PL 2/2026", base.MERITO, "SSNNN")])
    pessoas, _ = peso.calcular([ano], r, 57, {"PL 1/2026": 0, "PL 2/2026": 40})
    assert pessoas[5].saldo == 100.0 and pessoas[5].eixo.n == 2


def test_notas_das_materias_e_muda_quando_pesa():
    materias = {"PL 1/2026": {"rel": {"n": 72}}, "MPV 2/2026": {"e": "sem nota"}, "PL 3/2026": {"rel": {"q": "votada"}}}
    assert peso.notas_das_materias(materias) == {"PL 1/2026": 72.0}
    fichas = [
        {"k": "cd-5", "nome": "Cinco", "exercicio": True, "campo": {"saldo": -80}, "peso": {"saldo": 50, "simples": -50, "faixas": {"alta": {"saldo": 100, "n": 6}}}},
        {"k": "cd-6", "nome": "Seis", "exercicio": True, "campo": {"saldo": -60}, "peso": {"saldo": 20, "simples": 10}},  # 10 pontos de peso: fora (os 80 do campo vêm do recorte)
        {"k": "cd-7", "nome": "Sete", "exercicio": False, "campo": {"saldo": -90}, "peso": {"saldo": 90, "simples": -90}},  # fora do exercício
    ]
    lista = peso.muda_quando_pesa(fichas)
    assert [(i["k"], i["dif"], i["alta"], i["alta_n"], i["baixa"]) for i in lista] == [("cd-5", 100, 100, 6, None)]


def test_a_nota_do_senado_vale_para_a_materia_com_a_fase_ao_lado():
    """O Senado anota a fase ao lado do número ("PL 1/2026 (Substitutivo-CD)"): a nota é a da matéria."""
    r = _regras()
    ano = _ano([("PL 1/2026 (Substitutivo-CD)", base.MERITO, "SSNNS"), ("PEC 2/2026 (fase 2)", base.MERITO, "SSNNN")])
    pessoas, cobertura = peso.calcular([ano], r, 57, {"PL 1/2026": 10, "PEC 2/2026": 90})
    assert cobertura["com_nota"] == 2 and pessoas[5].saldo == 80.0
    assert peso.resumo(pessoas[5], minimo=2)["simples"] == 0
