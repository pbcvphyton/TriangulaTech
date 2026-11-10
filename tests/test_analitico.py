"""Aba Analítico Político: a leitura (``qijournal/render/achados.py``), o placar e a matriz
das manchetes, a lente do enquadramento (``qijournal/render/lados.py``), os desvios do
termômetro e a página inteira (ordem, âncoras, base recolhida)."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

import pytest

from qijournal import pipeline
from qijournal.config import load_config
from qijournal.models import (
    Coverage,
    CoverageOutlet,
    IndexItem,
    IndexTopic,
    SourceRef,
    Story,
)
from qijournal.render import achados, analitico, lados, midia, pesquisa
from qijournal.render.analitico import analitico_view, render_analitico_page
from qijournal.termometro import agregacao, analise
from tests.fixtures.pipeline.factory import make_edition
from tests.fixtures.render.helpers import parse, visible_text

FIM = "2026-10-30"


@pytest.fixture
def config():
    return load_config(env={})


def _dias(n: int, fim: str = FIM) -> list[str]:
    ultimo = date.fromisoformat(fim)
    return [(ultimo - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


# ── manchetes: fábrica ───────────────────────────────────────────────────────


def _dia_de_busca(dia: str, manchetes: list[tuple[str, str]]) -> dict[str, Any]:
    """Um arquivo de busca mínimo: ``manchetes`` = [(veículo, título)] de Política."""
    veiculos: list[str] = []

    def v(nome: str) -> int:
        if nome not in veiculos:
            veiculos.append(nome)
        return veiculos.index(nome)

    noticias = [[0, -1, v(o), t, f"x.com/{dia}/{i}", 0] for i, (o, t) in enumerate(manchetes)]
    return {
        "v": pesquisa.VERSAO, "data": dia, "secoes": [["politica", "Política", "#000"]], "veiculos": veiculos,
        "noticias": noticias, "comparadas": [], "assuntos": [],
    }


def _destaques(dias: int = 21) -> midia.DestaquesView:
    """Fulano de Tal some das manchetes e o tom dele vira; Beltrano Silva não muda; a Folha
    é a única que põe Fulano em contexto desfavorável desde o começo."""
    arquivos = []
    for i, d in enumerate(_dias(dias)):
        recente = i >= dias - 7
        manchetes = [("Folha", f"Escândalo atinge Fulano de Tal {i}")]
        if recente:  # nas últimas 7 edições, só 2 veículos falam dele, e mal
            manchetes += [(f"Veículo {k}", f"Fulano de Tal é alvo de investigação {i}-{k}") for k in range(2)]
        else:
            manchetes += [(f"Veículo {k}", f"Fulano de Tal vence e ganha apoio {i}-{k}") for k in range(7)]
        manchetes += [(f"Veículo {k}", f"Hoje Beltrano Silva fala {i}-{k}") for k in range(3)]
        manchetes += [("g1", f"Câmara vota o orçamento {i}-{k}") for k in range(8)]
        arquivos.append(_dia_de_busca(d, manchetes))
    view = midia.destaques([midia.corpus_do_dia(a) for a in arquivos], fim=FIM)
    assert view is not None
    return view


def _nome(view: midia.DestaquesView, nome: str) -> midia.NomeView:
    return next(n for n in view.nomes if n.nome == nome)


# ── manchetes: os números por trás do placar ─────────────────────────────────


def test_nomes_guardam_a_curva_e_o_antes_e_depois_em_numeros():
    view = _destaques()
    fulano, beltrano = _nome(view, "Fulano de Tal"), _nome(view, "Beltrano Silva")
    assert len(view.datas) == 21 and len(fulano.serie) == 21 and view.veiculos == 9
    # 8 de 19 manchetes por edição nas 14 primeiras; 3 de 14 nas últimas 7
    assert fulano.serie[0] == pytest.approx(8 / 19, abs=1e-3) and fulano.serie[-1] == pytest.approx(3 / 14, abs=1e-3)
    e = fulano.espaco
    assert e is not None and (e.manchetes_anterior, e.manchetes_recente) == (56, 21)
    assert e.anterior == pytest.approx(56 / 133) and e.recente == pytest.approx(21 / 98)
    assert e.z is not None and e.z < -midia.Z_ESPACO and e.alem_do_acaso  # caiu além do acaso
    # Beltrano mantém as mesmas 3 manchetes por edição: a parte dele até sobe um pouco, mas dentro do acaso
    b = beltrano.espaco
    assert b is not None and b.recente > b.anterior and not b.alem_do_acaso
    t = fulano.tom
    assert t is not None and t.tendencia == "piorou" and (t.n_anterior, t.n_recente) == (56, 21)
    assert t.saldo_anterior == pytest.approx(75.0) and t.saldo_recente == pytest.approx(-100.0)
    assert t.n_desfavoraveis + t.n_favoraveis <= t.manchetes and len(t.todos) == 8  # todos os veículos, não só os 10 da tabela
    assert all(v.demais_valor is not None for v in t.todos)


def test_espaco_sem_base_nao_e_dito():
    # menos de 14 edições: sem comparação (a tendência também não é dita)
    assert midia._espaco({}, {}, _dias(10)) is None
    dias = _dias(14)
    base = {d: 100 for d in dias}
    assert midia._espaco({d: 0 for d in dias}, base, dias) is None  # menos de 10 manchetes do nome
    e = midia._espaco({d: 10 for d in dias}, base, dias)  # igual nas duas semanas: z = 0
    assert e is not None and e.z == pytest.approx(0.0) and not e.alem_do_acaso


# ── placar e matriz ──────────────────────────────────────────────────────────


def test_placar_marca_so_o_que_passa_do_acaso():
    view = _destaques()
    pl = achados.placar(view)
    linhas = {l.n.nome: l for l in pl.linhas}
    fulano, beltrano = linhas["Fulano de Tal"], linhas["Beltrano Silva"]
    assert fulano.id == "qf-1" and fulano.barra == 100.0 and fulano.curva is not None and fulano.curva.recente
    assert fulano.esp_forte and fulano.tom_forte and (fulano.tom_antes, fulano.tom_agora) == ("+75", "−100")
    assert fulano.tom_antes_x == 100.0 and fulano.tom_agora_x == 0.0  # a escala vai de −50 a +50: os dois na ponta
    assert not beltrano.esp_forte and not beltrano.tom_forte and beltrano.esp_antes  # variação escrita, sem a marca
    assert pl.mudaram == 1
    # o que mudou: as mudanças além do acaso, a de maior peso primeiro (a virada de tom antes do espaço)
    assert [(m.nome, m.tipo, m.selo) for m in pl.mudancas] == [
        ("Fulano de Tal", "tom", "piorou"), ("Fulano de Tal", "espaço", fulano.n.tendencia),
    ]
    assert pl.mudancas[0].classe == "neg" and pl.mudancas[0].href == "#qf-1"


def test_matriz_traz_o_saldo_de_cada_veiculo_e_contorna_quem_destoa():
    view = _destaques()
    mz = achados.matriz(view)
    assert mz is not None and [n for n, _id in mz.nomes] == ["Fulano de Tal", "Beltrano Silva"]
    assert mz.geral[0].valor == _nome(view, "Fulano de Tal").tom.saldo and mz.geral[1].valor == "0"
    linhas = {l.veiculo: l for l in mz.linhas}
    folha = linhas["Folha"].celulas[0]
    assert folha.valor == "−100" and folha.nivel == "n3" and folha.destaque == "mais crítico"
    assert "Folha sobre Fulano de Tal: saldo −100 em 21 manchetes" in folha.title and "além do acaso" in folha.title
    assert linhas["Folha"].celulas[1].valor == "" and linhas["Folha"].celulas[1].title == ""  # sem manchete: vazio
    assert mz.destaques == 1 and any(f.startswith("Além do acaso: Folha mais crítico a Fulano de Tal") for f in mz.leitura)
    assert achados._nivel(4) == "z" and achados._nivel(-5) == "n1" and achados._nivel(10) == "p2" and achados._nivel(-33) == "n3"


def test_celula_com_poucas_manchetes_fica_sem_numero():
    v = midia.VeiculoTomView(
        nome="X", manchetes=3, esperado="—", esperado_classe="", esperado_valor=None, presenca="3 de 30", desfavoraveis="33%",
        favoraveis="0,0%", saldo="−33", saldo_valor=-33.0, demais="0", destaque="", classe="", alvo="0,0%", assunto="",
        padrao="", barra_left=17.0, barra_width=33.0, pequena=True,
    )
    c = achados._celula("Fulano", v)
    assert c.valor == "·" and c.nivel == "" and c.pequena and "Amostra pequena: menos de 10 manchetes" in c.title


# ── enquadramento ────────────────────────────────────────────────────────────


def _cobertura(titulo: str, a: list[str], b: list[str], neutros: list[str] = (), secao: str = "politica") -> Coverage:
    outlets = [CoverageOutlet(name=v, stance="a", framing="foco") for v in a]
    outlets += [CoverageOutlet(name=v, stance="b", framing="foco") for v in b]
    outlets += [CoverageOutlet(name=v, stance="neutro", framing="") for v in neutros]
    debate = bool(a and b)
    return Coverage(
        topic=titulo, conclusion="Conclusão.", outlets=outlets, side_a="Destaca o risco" if debate else "",
        side_b="Destaca o alívio" if debate else "", section=secao,
    )


def _edicao(dia: str, comparadas: list[Coverage], materias: list[Coverage] = ()) -> Any:
    edition = make_edition(dia)
    edition.compared = list(comparadas)
    for i, cobertura in enumerate(materias):
        edition.stories.append(
            Story(
                id=f"pol-{i}", section="politica", headline=f"Matéria de política {i}", dek="Linha fina.", body=["Texto."],
                sources=[SourceRef(name="Folha", url="https://x.com/1")], article_ids=[], coverage=cobertura,
            )
        )
    return edition


def test_do_dia_conta_os_comparados_e_guarda_os_que_racharam():
    edition = _edicao(
        FIM,
        [
            _cobertura("Racha 2 × 1", ["Folha", "g1"], ["Valor"], ["CNN"]),
            _cobertura("Sem debate", [], [], ["Folha", "g1", "Valor"]),
            _cobertura("Todos de um lado", ["Folha", "g1"], []),  # tem lados, mas ninguém no B: não rachou
            _cobertura("Outra editoria", ["Folha"], ["g1"], secao="esporte"),
            _cobertura("Um veículo só", [], [], ["Folha"]),  # menos de 2 veículos: não é comparação
        ],
        materias=[_cobertura("Tópico da matéria", ["Folha", "Folha"], ["g1"])],
    )
    dia = lados.do_dia(edition)
    assert dia.data == FIM and dia.comparados == 4  # a matéria, o racha, o sem debate e o de um lado só
    assert [(a.titulo, a.a, a.b, a.neutros, a.materia) for a in dia.divididos] == [
        ("Matéria de política 0", ["Folha"], ["g1"], 0, "pol-0"),  # o título é o da matéria; veículo repetido conta uma vez
        ("Racha 2 × 1", ["Folha", "g1"], ["Valor"], 1, ""),
    ]
    assert dia.divididos[1].divergencia == pytest.approx(2 / 3) and dia.divididos[1].lados == {"Folha": "a", "g1": "a", "Valor": "b"}
    nos_dois = lados.Assunto(FIM, "t", "A", "B", ["Folha", "g1"], ["Folha", "Valor"], 0)
    assert nos_dois.lados == {"g1": "a", "Valor": "b"}  # quem aparece nos dois lados não entra nas contas de par


def test_o_acaso_depende_do_placar_de_cada_assunto():
    assert lados._acaso_par(2, 2) == pytest.approx(1 / 3)  # num 2 × 2, dois veículos só caem juntos 1 vez em 3
    assert lados._acaso_par(3, 1) == pytest.approx(0.5) and lados._acaso_par(1, 1) == 0.0
    esperado, z = lados._z(9, [1 / 3] * 12)
    assert esperado == pytest.approx(4.0) and z == pytest.approx(5 / (12 * 2 / 9) ** 0.5)
    assert lados._z(1, [0.0, 0.0]) == (0.0, None)  # 1 × 1 sempre: sem variação para comparar
    # quem decide é a conta exata: 16 de 24 quando o acaso daria 8 passa de 99%; 11 de 24, não
    assert lados._p(16, [1 / 3] * 24) < lados.ALFA < lados._p(11, [1 / 3] * 24)
    assert lados._p(0, [0.5] * 6) == pytest.approx(2 / 64) and lados._p(3, [0.5] * 6) == 1.0
    assert lados._p(1, [0.0, 0.0]) == 0.0 and lados._p(0, [0.0, 0.0]) == 1.0  # o impossível e o certo


def _dias_de_lados(n: int = 14) -> list[lados.DiaLados]:
    """Folha e Estadão sempre juntos, contra Valor e g1 (2 × 2); um assunto 3 × 1 por dia em
    que a CNN fica sozinha."""
    dias = []
    for i, d in enumerate(_dias(n)):
        dias.append(
            lados.do_dia(
                _edicao(
                    d,
                    [
                        _cobertura(f"Assunto 2 × 2 do dia {i}", ["Folha", "Estadão"], ["Valor", "g1"]),
                        _cobertura(f"Assunto 3 × 1 do dia {i}", ["Folha", "Valor", "g1"], ["CNN"], ["Exame"]),
                        _cobertura(f"Consenso do dia {i}", [], [], ["Folha", "g1"]),
                    ],
                )
            )
        )
    return dias


def test_lados_aponta_os_pares_e_quem_destoa_alem_do_acaso():
    view = lados.lados(_dias_de_lados(), fim=FIM, edicoes=_dias(14)[-3:])
    assert view is not None and (view.comparados, view.divididos, view.empates, view.materias) == (42, 28, 14, 0)
    assert view.pct_divididos == "67%" and view.edicoes == 14
    pares = {(p.a, p.b): p for p in view.pares}
    juntos = pares[("Estadão", "Folha")]
    assert (juntos.juntos, juntos.total, juntos.classe) == (14, 14, "ju") and juntos.esperado == "4,7"  # 14 × 1/3
    opostos = pares[("Folha", "Valor")]  # opostos nos 2 × 2 e juntos nos 3 × 1: metade das vezes
    assert (opostos.juntos, opostos.total) == (14, 28) and opostos.classe == ""
    assert pares[("Estadão", "Valor")].classe == "op" and pares[("Estadão", "Valor")].juntos == 0
    assert view.par_destaque is not None and view.par_destaque.classe in ("ju", "op")
    cnn = next(d for d in view.destoam if d.nome == "CNN")
    assert (cnn.minoria, cnn.total, cnn.classe) == (14, 14, "mais") and cnn.esperado == "3,5"  # 14 × 1/4
    folha = next(d for d in view.destoam if d.nome == "Folha")
    assert folha.minoria == 0 and folha.total == 14  # só os assuntos com maioria contam (os 3 × 1)
    assert any("passa do acaso" in f for f in view.leitura) and any(f.startswith("Quem mais destoa: CNN") for f in view.leitura)
    # recentes: os das últimas 7 edições, os mais disputados primeiro (o 2 × 2 antes do 3 × 1)
    assert view.recentes_n == 14 and len(view.recentes) == lados.RECENTES
    assert all(a.titulo.startswith("Assunto 2 × 2") and a.divergencia == "100" for a in view.recentes)
    assert view.recentes[0].data == "30/10/2026" and view.recentes[0].href == f"{FIM}.html#cobertura"
    assert len(view.demais) == lados.PERIODO and not {a.titulo for a in view.demais} & {a.titulo for a in view.recentes}
    fora = lados.lados(_dias_de_lados(), fim=FIM)  # sem a edição no arquivo, o assunto fica sem link
    assert all(a.href == "" for a in fora.recentes)


def test_lados_sem_nada_alem_do_acaso_diz_isso():
    dias = [
        lados.do_dia(_edicao(d, [_cobertura(f"Assunto {i}", ["Folha", "g1"] if i % 2 else ["Folha", "Valor"], ["Estadão"])]))
        for i, d in enumerate(_dias(4))
    ]
    view = lados.lados(dias, fim=FIM)
    assert view is not None and view.divididos == 4 and view.pares == [] and view.par_destaque is None  # menos de 8 em comum
    assert view.destoam == [] and not any("passa do acaso" in f for f in view.leitura)
    assert lados.lados([], fim=FIM) is None
    assert lados.lados([lados.do_dia(_edicao(FIM, [_cobertura("Esporte", ["a"], ["b"], secao="esporte")]))], fim=FIM) is None
    link = lados.lados([lados.do_dia(_edicao(FIM, [], materias=[_cobertura("t", ["Folha"], ["g1"])]))], fim=FIM, edicoes=[FIM])
    assert link.materias == 1 and link.recentes[0].href == f"{FIM}.html#s-pol-0" and link.recentes[0].materia


def test_conta_do_nome_e_lente_veem_os_mesmos_assuntos(config):
    # A conta de assuntos divididos de cada nome (do arquivo de busca) e a lente do enquadramento
    # (de data/AAAA-MM-DD.json) leem a mesma lista de cada edição: as matérias com análise da
    # cobertura e os demais assuntos comparados, cada assunto uma vez.
    edition = _edicao(
        FIM,
        [_cobertura("Senado adia a votação", ["Folha", "Valor"], ["g1"]), _cobertura("Senado sem racha", [], [], ["Folha", "g1"])],
        materias=[_cobertura("Senado aprova a PEC", ["Folha"], ["g1", "Valor"])],
    )
    materia = edition.story("pol-0")
    materia.headline, materia.article_ids = "Senado aprova a PEC", ["n1"]
    repetido = _cobertura("Senado aprova a PEC, de novo", ["Folha"], ["g1"])
    repetido.article_ids = ["n1"]  # a mesma notícia da matéria: não entra em nenhuma das contas
    edition.compared.append(repetido)
    edition.index = [
        IndexTopic(section="politica", title="Senado", items=[
            IndexItem(source="Folha", title=f"Hoje o Senado vota a PEC {i}", url=f"https://x.com/{i}") for i in range(6)
        ])
    ]
    lente = lados.do_dia(edition)
    corpus = midia.corpus_do_dia(pesquisa.dados_do_dia(edition, config, None))
    assert [a.titulo for a in lente.divididos] == ["Senado aprova a PEC", "Senado adia a votação"] and lente.comparados == 3
    assert len(corpus.comparadas) == len(lente.divididos)  # antes, a busca só via o assunto que não virou matéria
    view = midia.destaques([corpus], fim=FIM)
    senado = _nome(view, "Senado")
    assert senado.debates == 2 and senado.divergencia == "67"  # 2 × 1 ÷ 3 nos dois
    # o dossiê diz de onde vem a conta: os mesmos assuntos da lente (matérias e demais assuntos)
    html = render_analitico_page([], config, home_href="../", archive_href="./", destaques=view)
    dossie = html[html.index('<div class="az-dossie">') :]
    assert "2 assuntos dividiram os veículos (divergência média 67)" in dossie and "assuntos comparados dividiram" not in dossie
    assert "nas matérias da edição e nos demais assuntos comparados: os mesmos da lente do enquadramento" in dossie


# ── termômetro: os desvios ───────────────────────────────────────────────────


def _analise(i: str, dia: str, T: int | None = 0, **extra: Any) -> dict[str, Any]:
    base = {
        "id": i, "url": f"https://x.com/{i}", "titulo": f"Texto {i}", "veiculo": "Folha", "editoria": "Política",
        "secao": "politica", "materia": "m", "data_publicacao": dia, "analisado_em": dia, "versao_calculo": "1.1.0",
        "selo": None, "genero": "noticia", "triagem": {"relevancia_politica": "alta", "eixos": ["economico"]},
        "estado": "ok" if T is not None else "nao_se_aplica", "T": T,
        "rotulo": agregacao.rotulo_T(T) if T is not None else "Não se aplica", "confianca": "media",
        "resumo": "Resumo.", "qualidade_apuracao": {"Q": 60, "rotulo": "Regular"}, "evidencias": [],
    }
    return {**base, **extra}


def test_teste_de_sinal_dos_desvios():
    assert analitico._passa_do_acaso(49, 73) and not analitico._passa_do_acaso(24, 73)  # só o lado maior é testado
    assert not analitico._passa_do_acaso(3, 4) and not analitico._passa_do_acaso(7, 8)  # 7 de 8: p = 0,07
    assert analitico._passa_do_acaso(8, 8) and not analitico._passa_do_acaso(5, 10) and not analitico._passa_do_acaso(0, 0)
    assert analitico._passa_do_acaso(560, 1001) and not analitico._passa_do_acaso(520, 1001)  # acima de mil, pela normal


def test_desvios_contam_os_t_fora_do_zero_e_para_que_lado():
    valores = [0] * 40 + [-3] * 12 + [-6] * 5 + [3] * 2 + [14]
    noticias = [_analise(f"a{i}", FIM, T=t) for i, t in enumerate(valores)]
    d = analitico._desvios(noticias)
    assert d is not None and (d.zeros, d.esq, d.dir, d.noticias, d.dentro) == (40, 17, 3, 60, 19)
    assert d.dominio == 25 and d.passo == 1 and d.f1 == 10 and d.faixa == 40.0 and d.alem_do_acaso
    assert d.titulo == "Quando o texto sai do zero, pende mais à esquerda: 17 notícias contra 3"
    assert len(d.esquerda) == 25 and len(d.direita) == 25 and d.esquerda[0].t == -25 and d.direita[-1].t == 25
    colunas = {c.t: c for c in d.esquerda + d.direita}
    assert (colunas[-3].n, colunas[-3].altura, colunas[-3].rotulo) == (12, 100.0, "12")  # a mais alta de cada lado leva o número
    assert (colunas[3].n, colunas[3].rotulo) == (2, "2") and colunas[14].n == 1 and colunas[14].rotulo == "" and colunas[-9].n == 0
    assert "a diferença passa do acaso" in d.leitura and "19 ficam dentro da faixa de equilíbrio (até ±10)" in d.leitura
    largo = analitico._desvios([_analise("x", FIM, T=-60), _analise("y", FIM, T=7)])
    assert largo.dominio == 100 and largo.passo == 4 and not largo.alem_do_acaso and "sem lado" in largo.titulo
    assert {c.t: c.n for c in largo.esquerda + largo.direita if c.n} == {-60: 1, 8: 1}  # colunas de 4 pontos
    assert analitico._desvios([_analise("z", FIM, T=0)]) is None


def test_textos_mais_inclinados_das_ultimas_edicoes_trazem_o_sinal_que_pesou():
    sinais = [
        {"id": "E1", "criterio": "FONTES", "citacao": "ouviu só um lado", "paragrafo": "P2", "direcao": "equilibrio", "contribuicao": 0.0},
        {"id": "E2", "criterio": "ROTULOS", "citacao": "o chamado pacote", "paragrafo": "P1", "direcao": "direita", "contribuicao": 9.0},
    ]
    antigas = [_analise(f"v{i}", "2026-10-01", T=-9 + i) for i in range(3)]  # −9, −8, −7 (há um mês)
    recentes = [_analise("r1", FIM, T=14, evidencias=sinais), _analise("r2", "2026-10-28", T=-4), _analise("r3", FIM, T=0)]
    opiniao = [_analise("o1", FIM, T=30, selo="Opinião")]
    view = analitico_view([{"data": FIM, "analises": antigas + recentes + opiniao}], edition_date=FIM)
    assert view.inclinados_titulo == "Os textos mais inclinados das últimas sete edições"
    assert [i.a.titulo for i in view.inclinados] == ["Texto r1", "Texto r2"]  # só as com T fora do zero, a mais longe primeiro
    primeiro = view.inclinados[0]
    assert primeiro.sinal is not None and primeiro.sinal.citacao == "o chamado pacote" and primeiro.outros == 1  # o sinal com lado
    assert [a.titulo for a in view.recordes] == ["Texto v0", "Texto v1", "Texto v2"]  # o resto do histórico, em uma linha
    assert [a.titulo for a in view.opiniao] == ["Texto o1"]  # à parte: não entra nos inclinados nem na média
    assert view.leitura is not None and any(a.tipo == "inclinado" and "T +14" in a.titulo for a in view.leitura.achados)
    so_antigas = analitico_view([{"data": "2026-10-01", "analises": antigas}], edition_date=FIM)
    assert so_antigas.inclinados_titulo == "Os textos mais inclinados do histórico" and so_antigas.recordes == []


# ── a leitura ────────────────────────────────────────────────────────────────


def _texto(**extra: Any) -> achados.TextoResumo:
    base = dict(
        textos=120, veiculos=8, noticias=100, equilibrado=98, desiguais=20, pct_equilibrado="98%", pct_desiguais="20%",
        esq=14, dir=6, media="−1", rotulo="Equilibrado", lado="à esquerda", q="84", q_rotulo="Boa",
    )
    return achados.TextoResumo(**{**base, **extra})


def test_leitura_poe_no_alto_o_achado_de_maior_peso_e_o_termometro_na_linha_fina():
    view = _destaques()
    ld = lados.lados(_dias_de_lados(), fim=FIM)
    l = achados.leitura(view, ld, _texto(), periodo="edições de teste")
    assert l is not None and l.chapeu == "A leitura · edições de teste"
    assert l.manchete == "O tom das manchetes sobre Fulano de Tal piorou: saldo de +75 para −100 em sete edições"
    assert l.principal.tipo == "tom" and l.principal.href == "#qf-1" and "A diferença passa do acaso" in l.principal.texto
    assert l.linha_fina.startswith("No texto das notícias, o termômetro segue em equilíbrio: 98 de 100")
    tipos = [a.tipo for a in l.achados]
    assert "texto" not in tipos and len(tipos) == len(set(tipos))  # um por tipo; o veredito do texto está na linha fina
    assert tipos[:2] == ["espaco", "lados"] and [a.peso for a in l.achados] == sorted((a.peso for a in l.achados), reverse=True)
    espaco = l.achados[0]
    assert espaco.titulo.startswith("Fulano de Tal perde espaço: de 42% para 21% das manchetes de Política") and espaco.numero == "42% → 21%"
    # o par mais longe do acaso: Valor e g1 ficam juntos nos 2 × 2 e nos 3 × 1
    assert l.achados[1].titulo == "Valor e g1 enquadram do mesmo jeito: mesmo lado em 28 de 28 assuntos divididos"
    veiculos = next(a for a in l.achados if a.tipo == "veiculos")
    assert veiculos.titulo == "Folha é o veículo mais crítico a Fulano de Tal nas manchetes" and veiculos.href == "#an-matriz"
    assert [(x.ordem, x.nome, x.href) for x in l.lentes] == [
        (1, "Manchetes", "#an-nomes"), (2, "Enquadramento", "#an-lados"), (3, "Texto", "#an-texto"),
    ]


def test_leitura_so_com_o_termometro_e_sem_nada_a_dizer():
    so_texto = achados.leitura(None, None, _texto())
    assert so_texto is not None and so_texto.principal.tipo == "texto" and so_texto.linha_fina == ""
    assert so_texto.manchete == "No texto das notícias, o termômetro não encontra viés editorial relevante"
    assert [a.tipo for a in so_texto.achados] == ["apuracao"] and [(x.ordem, x.nome) for x in so_texto.lentes] == [(1, "Texto")]
    com_vies = achados.leitura(None, None, _texto(equilibrado=40, media="−18", rotulo="Leve à esquerda"))
    assert com_vies.manchete == "O termômetro encontra viés editorial em parte das notícias, em média à esquerda"
    assimetria = achados.leitura(None, None, _texto(lado_esq=49, lado_dir=24, lado_dentro=71, lado_alem=True, concentracao="Amostra concentrada."))
    # os desvios com lado pesam mais que o veredito: viram a manchete, e o veredito desce para a linha fina
    assert assimetria.principal.tipo == "assimetria" and assimetria.principal.numero == "49 × 24"
    assert assimetria.manchete == "Quando o texto sai do zero, pende mais à esquerda: 49 notícias contra 24"
    assert "Amostra concentrada." in assimetria.principal.texto and assimetria.linha_fina.startswith("No texto das notícias")
    assert achados.leitura(None, None, None) is None and achados.leitura(None, None, _texto(textos=0, noticias=0)) is None
    # sem nada que passe do acaso, as manchetes ainda dizem de quem a imprensa fala
    calmo = midia.destaques(
        [midia.corpus_do_dia(_dia_de_busca(d, [("Folha", f"Hoje o Senado vota a PEC {i}"), ("g1", f"Mais tarde o Senado aprova a PEC {i}")])) for i, d in enumerate(_dias(3))],
        fim=FIM,
    )
    l = achados.leitura(calmo, None, None)
    assert l is not None and l.principal.tipo == "presenca" and l.principal.href == "#an-nomes"
    assert l.manchete == "PEC está em 100% das manchetes de Política; Senado, em 100%" and l.achados == []
    assert achados._aspas("texto") == "“texto”" and achados._aspas("“Tsunami” democrata") == "‘“Tsunami” democrata’"


def test_ordens_numeram_so_as_lentes_presentes():
    view = _destaques()
    ld = lados.lados(_dias_de_lados(), fim=FIM)
    assert achados.ordens(view, ld) == {"manchetes": 1, "lados": 2, "texto": 3}
    assert achados.ordens(None, ld) == {"lados": 1, "texto": 2} and achados.ordens(view, None) == {"manchetes": 1, "texto": 2}
    assert achados.ordens(None, None) == {"texto": 1}


# ── a página ─────────────────────────────────────────────────────────────────


def _registros() -> list[dict[str, Any]]:
    evid = [{"id": "E1", "criterio": "ROTULOS", "citacao": "o chamado pacote", "paragrafo": "P1", "direcao": "esquerda", "contribuicao": -5.0}]
    analises = [_analise(f"a{i}", FIM, T=0, veiculo="Valor" if i % 3 else "Folha") for i in range(40)]
    analises += [_analise(f"e{i}", FIM, T=-6, evidencias=evid) for i in range(12)]
    analises += [_analise("d1", FIM, T=14), _analise("o1", FIM, T=22, selo="Opinião"), _analise("n1", FIM, T=None)]
    return [{"data": FIM, "analises": analises}]


@pytest.mark.parametrize("com_nomes", [True, False])
@pytest.mark.parametrize("com_lados", [True, False])
@pytest.mark.parametrize("com_historico", [True, False])
def test_pagina_bem_formada_com_qualquer_combinacao_de_lentes(config, com_nomes, com_lados, com_historico):
    html = render_analitico_page(
        _registros() if com_historico else [],
        config,
        home_href="../",
        archive_href="./",
        edition_date=FIM,
        edicoes=[FIM],
        destaques=_destaques() if com_nomes else None,
        enquadramento=lados.lados(_dias_de_lados(), fim=FIM, edicoes=[FIM]) if com_lados else None,
    )
    estrutura = parse(html)
    assert estrutura.errors == [] and estrutura.stack == []
    assert all(n == 1 for n in estrutura.ids.values())
    visivel = visible_text(html).replace('pattern=".*\\S.*"', "")  # o padrão do campo "analisar outro nome" não é texto
    assert "*" not in visivel and html.count("<script") == 3  # os dois da página e o da aba
    # toda âncora da página leva a um bloco que existe (achados, lentes, matriz, "como ler")
    ancoras = {h[1:] for h in estrutura.hrefs if h.startswith("#") and len(h) > 1}
    assert ancoras and ancoras <= set(estrutura.ids), ancoras - set(estrutura.ids)
    assert ('id="an-leitura"' in html) == (com_nomes or com_lados or com_historico)
    assert ('id="an-nomes"' in html) == com_nomes and ('id="an-lados"' in html) == com_lados
    assert ('id="an-base"' in html) == com_historico and ("Nenhuma análise no histórico ainda." in html) == (not com_historico)
    # as lentes presentes são numeradas em sequência, na ordem manchetes, enquadramento, texto
    numeros = re.findall(r'<p class="az-ln"><b>(\d)</b>(\w+)</p>', html)
    esperadas = [n for n, tem in (("Manchetes", com_nomes), ("Enquadramento", com_lados), ("Texto", True)) if tem]
    assert numeros == [(str(i), nome) for i, nome in enumerate(esperadas, start=1)]


def test_pagina_abre_pela_leitura_e_deixa_a_base_recolhida(config):
    html = render_analitico_page(
        _registros(), config, home_href="../", archive_href="./", edition_date=FIM, edicoes=[FIM],
        destaques=_destaques(), enquadramento=lados.lados(_dias_de_lados(), fim=FIM, edicoes=[FIM]),
    )
    ordem = ["an-leitura", "an-nomes", "an-quem", "an-matriz", "an-lados", "an-racha", "an-pares", "an-texto", "an-resumo",
             "an-onde", "an-desvios", "an-sinais", "an-q", "an-base", "an-dist", "an-indo", "an-series", "an-rk-1", "an-rk-2",
             "an-eixos", "an-dias", "an-todas", "an-metodo"]
    posicoes = [html.index(f'id="{i}"') for i in ordem]
    assert posicoes == sorted(posicoes)
    # a manchete é o achado de maior peso; os demais vêm numerados e levam à prova
    assert '<h3 class="az-manchete">O tom das manchetes sobre Fulano de Tal piorou: saldo de +75 para −100 em sete edições</h3>' in html
    assert html.count('<li class="az-a">') >= 4 and 'href="#an-pares"' in html and 'href="#an-desvios"' in html
    assert "O que mudou em sete edições" in html and '<span class="az-chip on neg">piorou</span>' in html
    # o placar: uma linha por nome, fechada, com o dossiê dentro
    assert html.count('<details class="az-n" id="qf-') == 2 and '<details class="az-n" id="qf-1">' in html
    assert '<span class="az-dq neg"><b>Folha</b> mais crítico</span>' in html and 'class="az-c az-n3 az-on"' in html
    # os desvios e o texto mais inclinado, com a citação
    assert "Quando o texto sai do zero, pende mais à esquerda: 12 notícias contra 1" in html
    assert '<li class="az-inc">' in html and "o chamado pacote" in html and "Opinião, à parte" in html
    # a base completa fica fechada: nada mudou além do acaso neste histórico
    base = html[html.index('id="an-base"') : html.index('id="an-metodo"')]
    assert base.count('<details class="az-base"') == 8 and "<details class=\"az-base\" id=\"an-indo\" open" not in base
    assert 'id="azFil" hidden' in base and base.count('class="tl ') == 55
    # como ler: uma entrada por lente presente
    metodo = html[html.index('id="an-metodo"') :]
    for titulo in ("A leitura", "Manchetes · espaço", "Manchetes · tom", "Enquadramento", "Texto · os desvios", "Limites"):
        assert f"<dt>{titulo}</dt>" in metodo


def test_tendencia_que_passa_do_acaso_abre_o_bloco_e_sobe_para_a_leitura(config):
    # sete meses: equilíbrio no começo, textos à direita no fim (a tendência mensal passa do acaso)
    registros = []
    for m in range(7):
        dia = f"2026-{3 + m:02d}-15"
        registros.append({"data": dia, "analises": [_analise(f"{m}-{k}", dia, T=m * 6 + (k % 3)) for k in range(35)]})
    view = analitico_view(registros, edition_date="2026-09-15")
    assert view.indo.aberto and "rumo à direita" in view.indo.resumo
    assert view.leitura is not None and view.leitura.principal.tipo == "texto"  # 105 de 245 fora do equilíbrio: o texto é a manchete
    assert any(a.tipo == "tendencia" and a.href == "#an-indo" for a in view.leitura.achados)
    html = render_analitico_page(registros, config, home_href="../", archive_href="./", edition_date="2026-09-15")
    assert '<details class="az-base" id="an-indo" open>' in html


# ── publicação ───────────────────────────────────────────────────────────────


def _registro_do_dia(dia: str) -> dict[str, Any]:
    return analise.registro_do_dia(dia, [_analise("a", dia)], provedor="falsa")


def test_publish_leva_o_enquadramento_ao_analitico(tmp_path, config):
    # ontem: publicado antes (fica em data/ e entra na janela); hoje: a edição em memória
    ontem = _edicao("2026-09-28", [_cobertura("Racha de ontem", ["Folha"], ["g1"])])
    ontem.termometro = _registro_do_dia("2026-09-28")
    pipeline.publish(ontem, config, out_dir=tmp_path, subject="s", email_html="<p>e</p>", email_text="e")
    hoje = _edicao("2026-09-29", [_cobertura("Racha de hoje", ["Folha", "Valor"], ["g1"])], materias=[_cobertura("t", ["Folha"], ["g1"])])
    hoje.termometro = _registro_do_dia("2026-09-29")
    paths = pipeline.publish(hoje, config, out_dir=tmp_path, subject="s", email_html="<p>e</p>", email_text="e")
    pagina = paths["analitico"].read_text(encoding="utf-8")
    assert 'id="an-lados"' in pagina and "Racha de hoje" in pagina and "Racha de ontem" in pagina
    assert 'href="2026-09-29.html#s-pol-0"' in pagina and 'href="2026-09-28.html#cobertura"' in pagina
    assert "3 dos 3 assuntos de Política" in pagina  # dois de hoje (a matéria e o racha) e um de ontem
    # as outras páginas não recebem nada da lente
    for nome in ("pesquisa", "index", "edition", "archive"):
        assert "an-lados" not in paths[nome].read_text(encoding="utf-8")


def test_falha_no_enquadramento_nao_derruba_a_edicao(tmp_path, config, monkeypatch, caplog):
    ontem = _edicao("2026-09-28", [_cobertura("Racha de ontem", ["Folha"], ["g1"])])
    ontem.termometro = _registro_do_dia("2026-09-28")
    pipeline.publish(ontem, config, out_dir=tmp_path, subject="s", email_html="<p>e</p>", email_text="e")
    (tmp_path / "data" / "2026-09-28.json").write_text("{quebrado", encoding="utf-8")  # a edição de ontem fica ilegível
    hoje = _edicao("2026-09-29", [_cobertura("Racha de hoje", ["Folha"], ["g1"])])
    hoje.termometro = _registro_do_dia("2026-09-29")
    paths = pipeline.publish(hoje, config, out_dir=tmp_path, subject="s", email_html="<p>e</p>", email_text="e")
    pagina = paths["analitico"].read_text(encoding="utf-8")
    assert "Racha de hoje" in pagina and "Racha de ontem" not in pagina
    assert "Enquadramento: edição 2026-09-28 ignorada" in caplog.text

    def quebra(*_args, **_kwargs):
        raise ValueError("lados fora do formato")

    monkeypatch.setattr(lados, "lados", quebra)
    paths = pipeline.publish(hoje, config, out_dir=tmp_path, subject="s", email_html="<p>e</p>", email_text="e")
    pagina = paths["analitico"].read_text(encoding="utf-8")
    assert 'id="an-lados"' not in pagina and 'id="an-texto"' in pagina  # a página sai sem o bloco
    assert "Enquadramento: lados fora do formato — Analítico sem o bloco" in caplog.text


def test_o_arquivo_de_busca_nao_muda_com_os_campos_novos(config):
    # os números a mais dos nomes não vão para a Pesquisa: as sugestões usam só o nome, o link e a contagem
    dados = pesquisa.dados_do_dia(make_edition(FIM), config, None)
    com = pesquisa.render_pesquisa_page([pesquisa.resumo_do_dia(dados)], config, home_href="../", archive_href="./", destaques=_destaques())
    assert "az-" not in re.sub(r"<style>.*?</style>", "", com, flags=re.S) and "Fulano de Tal</a>" in com
    json.dumps(dados)  # serializável como estava
