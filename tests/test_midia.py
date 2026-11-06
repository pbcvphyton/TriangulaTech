"""Como a mídia cobre: nomes em destaque (``qijournal/render/midia.py``), o bloco do
Analítico Político e o analítico da busca na Pesquisa analítica."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from qijournal import pipeline
from qijournal.config import load_config
from qijournal.models import IndexItem, IndexTopic
from qijournal.render import midia, pesquisa
from qijournal.render.analitico import render_analitico_page
from qijournal.termometro import analise
from tests.fixtures.pipeline.factory import make_edition

TEMPLATES = Path(__file__).resolve().parents[1] / "qijournal" / "render" / "templates"


@pytest.fixture
def config():
    return load_config(env={})


# ── regra de busca e nomes de um título ──────────────────────────────────────


def test_padrao_e_a_regra_da_busca_da_pagina():
    lula = midia.padrao("Lula")
    assert lula.search(midia.norm("Lula diz que…")) and lula.search(midia.norm("onda pró-Lula"))
    assert not lula.search(midia.norm("célula"))  # começo de palavra, como na página
    pt = midia.padrao("PT")
    assert pt.search("pt e pl") and not pt.search("ptax sobe")  # até 3 letras: só a palavra inteira
    frase = midia.padrao("Flávio Bolsonaro")
    assert frase.search(midia.norm("Flávio  Bolsonaro vota")) and not frase.search("flavio dino")
    assert midia.padrao("  ") is None
    assert midia.consulta("Flávio Bolsonaro") == '"Flávio Bolsonaro"' and midia.consulta("Lula") == "Lula"


def test_js_usa_a_mesma_regra_de_busca_e_de_nomes():
    js = (TEMPLATES / "pesquisa.js.j2").read_text(encoding="utf-8")
    assert "var fim = n.length <= 3 && n.indexOf(' ') < 0 ? '(?![a-z0-9])' : '';" in js
    assert "var fonte = '(?:^|[^a-z0-9])(' + corpo + ')' + fim;" in js
    md = (TEMPLATES / "midia.js.j2").read_text(encoding="utf-8")
    palavra = re.compile(re.search(r"var PALAVRA = /(.+?)/g;", md).group(1))
    amostra = "D'Ávila, pró-Lula e Trump’s: R$ 5,4 bi — “PEC” (6x1)"
    assert palavra.findall(amostra) == midia._PALAVRA.findall(amostra)
    pontuacao = re.compile(re.search(r"var PONTUACAO = /(.+?)/;", md).group(1))
    for c in ':.!?|\u2013\u2014"\u201c\u201d\u2018\',;()- ':
        assert bool(pontuacao.search(c)) == bool(midia._PONTUACAO.search(c)), c
    conectores = set(re.findall(r"'?([a-z]+)'?: 1", re.search(r"var CONECTORES = \{(.+?)\};", md).group(1)))
    assert conectores == set(midia._CONECTORES)
    # meses e dias só abrindo um nome; "AO VIVO" não é nome (as mesmas regras de nomes_do_titulo)
    assert "if (CALENDARIO[p.n]) return k + 1 < ps.length && junto(k) && nome(k + 1);" in md
    assert "(p.n === 'vivo' && k > 0 && ps[k - 1].n === 'ao')" in md


@pytest.mark.parametrize(
    ("titulo", "proprios", "esperado"),
    [
        ("Lula diz que Flávio Bolsonaro mente", {"lula"}, {"Lula", "Flávio Bolsonaro"}),
        ("Lula diz que Flávio Bolsonaro mente", set(), {"Flávio Bolsonaro"}),  # 1ª palavra sem prova de nome
        ("Eleição: Veja como votou o Senado", set(), {"Senado"}),  # depois de ":" abre frase
        ("Pesquisa mostra Lula à frente", {"lula"}, {"Lula"}),
        ("Luiz Inácio Lula da Silva viaja", {"luiz"}, {"Luiz Inácio Lula da Silva"}),
        ("SP: Tarcísio anuncia pacote", {"tarcisio"}, {"Tarcísio"}),  # sigla de estado sai; não atravessa ":"
        ("SP: Tarcísio anuncia pacote", set(), set()),  # depois de ":" só com prova de nome próprio
        ("Banco do Brasil lucra mais", {"banco"}, {"Banco do Brasil"}),
        ("Gonet e STF discutem; Paulo Guedes fala", set(), {"STF", "Paulo Guedes"}),
        ("TV Globo faz debate no Rio", set(), set()),
        ("Lula recebe Marco Rubio no Planalto", set(), {"Marco Rubio", "Planalto"}),  # mês abrindo nome
        ("Segunda Turma do STF absolve deputado", set(), {"Segunda Turma do STF"}),
        ("Março tem inflação menor", set(), set()),  # mês sozinho não é nome
        ("VÍDEO: Lula discursa em ato", {"lula"}, {"Lula"}),  # selo editorial em caixa alta
        ("É FAKE que o TSE mudou a urna", set(), {"TSE"}),
        ("AO VIVO: Senado vota a PEC", set(), {"PEC"}),
        ("TSE: prazo termina hoje", set(), {"TSE"}),  # sigla que abre o título continua
    ],
)
def test_nomes_do_titulo(titulo, proprios, esperado):
    assert midia.nomes_do_titulo(titulo, proprios) == esperado


# ── nomes em destaque ────────────────────────────────────────────────────────

SECOES = [["politica", "Política", "#C90055"], ["esporte", "Esporte", "#111111"]]


def _dia(dia: str, manchetes: list[tuple[str, str]], comparadas: list[dict[str, Any]] = (), esporte: int = 0) -> dict[str, Any]:
    """Um arquivo de busca mínimo: ``manchetes`` = [(veículo, título)] de Política."""
    veiculos: list[str] = []

    def v(nome: str) -> int:
        if nome not in veiculos:
            veiculos.append(nome)
        return veiculos.index(nome)

    noticias = [[0, -1, v(o), t, f"exemplo.com/{dia}/{i}", 0] for i, (o, t) in enumerate(manchetes)]
    noticias += [[1, -1, v("ge"), f"Flamengo vence o jogo {i}", f"ge.com/{dia}/{i}", 0] for i in range(esporte)]
    saida = []
    for c in comparadas:
        saida.append({**c, "f": [[v(o), lado, "foco", ""] for o, lado in c["f"]]})
    return {"v": pesquisa.VERSAO, "data": dia, "secoes": SECOES, "veiculos": veiculos, "noticias": noticias, "comparadas": saida}


def _dias(n: int, fim: str = "2026-10-30") -> list[str]:
    ultimo = date.fromisoformat(fim)
    return [(ultimo - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def test_corpus_tem_a_base_da_pesquisa_e_os_nomes_das_manchetes_em_portugues():
    dados = _dia(
        "2026-10-30",
        [("Folha", "Lula sanciona lei; Senado reage"), ("The Guardian", "Lula Says Brazil Will Act"), ("Folha", "Lula sanciona lei; Senado reage")],
        comparadas=[{"s": 0, "t": "Lula e o Senado", "c": "", "a": "A", "b": "B", "f": [("Folha", "a"), ("g1", "b"), ("Estadão", "a")]},
                    {"s": 0, "t": "Um lado só", "c": "", "a": "A", "b": "B", "f": [("Folha", "a"), ("g1", "a"), ("Estadão", "neutro")]},
                    {"s": 0, "t": "Sem lados", "c": "", "a": "", "b": "", "f": [("Folha", "neutro")]}],
        esporte=3,
    )
    dados["noticias"][2][4] = dados["noticias"][0][4]  # mesmo link: conta uma vez
    corpus = midia.corpus_do_dia(dados, ["The Guardian"])
    # A base é a da Pesquisa (todos os veículos da editoria); os nomes, só das manchetes em português.
    assert [v for _l, _t, v in corpus.titulos] == ["Folha", "The Guardian"]
    assert corpus.nomes == {"exemplo.com/2026-10-30/0": ["Senado"]}  # "Lula" abre o título e não aparece no meio de outro
    assert corpus.caixa == {"senado": (1, 0)}
    assert corpus.comparadas == [("lula e o senado\na\nb\nfoco\nfoco\nfoco", 2, 1)]  # só a com veículos dos dois lados


def test_destaques_contam_com_a_regra_da_busca_e_juntam_as_formas_do_nome():
    dias = _dias(14)
    arquivos = []
    for i, dia in enumerate(dias):
        recente = i >= 7
        manchetes = [
            ("Folha", f"Aliados dizem que Lula e Flávio Bolsonaro negociam {i}"),
            ("g1", f"Entre jovens, Flávio lidera {i}"),
            ("Estadão", f"Governo de Lula anuncia pacote {i}"),
            ("Valor", f"Hoje o Senado aprova projeto {i}") if recente else ("Valor", f"Mercado reage ao Copom {i}"),
            ("Valor", f"Bolsa sobe com o Senado {i}") if recente else ("Valor", f"Bolsa fecha em alta no pregão {i}"),
        ] + [("Valor", f"Bolsa fecha em alta no pregão {i}-{k}") for k in range(3)]
        comparadas = [{"s": 0, "t": "Senado e o projeto", "c": "", "a": "A", "b": "B", "f": [("Folha", "a"), ("g1", "b")]}] if recente else []
        arquivos.append(_dia(dia, manchetes, comparadas))
    view = midia.destaques([midia.corpus_do_dia(d) for d in arquivos], fim=dias[-1])
    assert view is not None and view.edicoes == 14 and view.manchetes == 14 * 8 and view.inicio == "2026-10-01"
    por_nome = {n.nome: n for n in view.nomes}
    # "Flávio" (28: duas manchetes por dia) leva "Flávio Bolsonaro" (14) como "também"
    assert por_nome["Flávio"].manchetes == 28 and por_nome["Flávio"].tambem == "Flávio Bolsonaro"
    assert "Flávio Bolsonaro" not in por_nome
    lula = por_nome["Lula"]
    assert lula.manchetes == 28 and lula.veiculos == 2 and lula.participacao == "25%"
    assert lula.tendencia == "estável" and lula.debates == 0 and lula.divergencia == "—"
    senado = por_nome["Senado"]  # só nas 7 últimas edições: tendência "novo"; debate 1 × 1 = divergência 100
    assert senado.manchetes == 14 and senado.tendencia == "novo" and senado.tendencia_classe == "sobe"
    assert senado.debates == 7 and senado.divergencia == "100"
    assert lula.href == "pesquisa.html?q=Lula&de=2026-10-01&ed=politica#midia"
    assert por_nome["Flávio"].consulta == "Flávio"
    assert [n.nome for n in view.nomes][:2] == ["Flávio", "Lula"]  # mais citados primeiro (empate: ordem alfabética)


def test_tendencia_em_vezes_quando_mais_que_dobra_e_sem_base_suficiente():
    base = {d: 100 for d in _dias(14)}
    dias = sorted(base)
    por_dia = {d: (30 if i >= 7 else 10) for i, d in enumerate(dias)}
    assert midia._tendencia(por_dia, base, dias)[:2] == ("▲ 3,0×", "sobe")
    por_dia = {d: (5 if i >= 7 else 10) for i, d in enumerate(dias)}
    assert midia._tendencia(por_dia, base, dias)[:2] == ("▼ 50%", "cai")
    assert midia._tendencia(por_dia, base, dias[:10])[:2] == ("—", "ins")  # menos de 14 edições
    assert midia._tendencia(por_dia, {d: 5 for d in dias}, dias)[:2] == ("—", "ins")  # semana com < 50 manchetes
    # Os mesmos rótulos do analítico do nome na Pesquisa: "zerou" e "novo"…
    assert midia._tendencia({d: (0 if i >= 7 else 2) for i, d in enumerate(dias)}, base, dias)[:2] == ("zerou", "cai")
    assert midia._tendencia({d: (2 if i >= 7 else 0) for i, d in enumerate(dias)}, base, dias)[:2] == ("novo", "sobe")
    # …e nada de tendência com poucas manchetes do nome (3 contra 4)
    poucas = {dias[0]: 3, dias[-1]: 4}
    assert midia._tendencia(poucas, base, dias)[:2] == ("—", "ins")


def _destaques_de(arquivos: list[dict[str, Any]], **kw: Any) -> list[tuple[str, int]]:
    view = midia.destaques([midia.corpus_do_dia(d) for d in arquivos], fim=arquivos[-1]["data"], **kw)
    return [(n.nome, n.manchetes) for n in view.nomes] if view else []


def test_destaques_contam_a_noticia_se_alguma_versao_do_titulo_cita_o_nome():
    dias = _dias(6)
    arquivos = [_dia(d, [("g1", f"Hoje o Senado vota a PEC {i}")]) for i, d in enumerate(dias)]
    # um blog ao vivo: a mesma notícia (link) muda de título; só a versão de depois cita o Senado
    for i, titulo in ((0, "Acompanhe a apuração ao vivo"), (5, "Apuração: Senado confirma resultado")):
        arquivos[i]["noticias"].append([0, -1, 0, titulo, "g1.com/ao-vivo", 0])
    assert ("Senado", 7) in _destaques_de(arquivos)
    view = midia.destaques([midia.corpus_do_dia(d) for d in arquivos], fim=dias[-1])
    assert view.manchetes == 7  # a notícia ao vivo entra uma vez na base


def test_candidatos_contam_cada_noticia_uma_vez(monkeypatch):
    # Uma notícia repetida em 20 edições não vale mais que um nome citado em 5 notícias.
    dias = _dias(20)
    arquivos = []
    for i, d in enumerate(dias):
        manchetes = [("Folha", "Hoje Fulano fala")] + ([("g1", f"Hoje Beltrano fala {i}")] if i < 5 else [])
        dados = _dia(d, manchetes)
        dados["noticias"][0][4] = "folha.com/fulano"  # o mesmo link todo dia
        arquivos.append(dados)
    monkeypatch.setattr(midia, "CANDIDATOS", 1)
    assert _destaques_de(arquivos) == [("Beltrano", 5)]


def test_palavra_comum_com_maiuscula_nao_vira_nome():
    # "Eleições" sai com maiúscula às vezes, mas no meio das frases é mais "eleições": não é nome.
    manchetes = [("Folha", f"Hoje o TSE divulga Eleições {i}") for i in range(6)]
    manchetes += [("g1", f"Prazo das eleições termina {i}") for i in range(7)]
    assert _destaques_de([_dia("2026-10-30", manchetes)]) == [("TSE", 6)]


def test_nome_contido_em_outro_empatado_nao_vira_outra_linha():
    # "Alexandre de Moraes" (empate com "Moraes", que vem depois na ordem alfabética) fica uma linha só.
    arquivos = [
        _dia(d, [("Folha", f"Alexandre de Moraes nega pedido {i}"), ("g1", f"Defesa recorre a Alexandre de Moraes {i}")])
        for i, d in enumerate(_dias(6))
    ]
    assert _destaques_de(arquivos) == [("Alexandre de Moraes", 12)]


def test_sem_manchetes_sem_destaques():
    assert midia.destaques([]) is None
    assert midia.destaques([midia.corpus_do_dia(_dia("2026-10-30", []))]) is None


def test_exterior_sao_os_feeds_em_outra_lingua(config):
    fora = midia.exterior(config)
    assert "The Guardian" in fora and "Folha de S.Paulo" not in fora and "Valor Econômico" not in fora


# ── páginas ──────────────────────────────────────────────────────────────────


def _destaques() -> midia.DestaquesView:
    dias = _dias(3)
    arquivos = [_dia(d, [("Folha", f"Senado vota a PEC {i}"), ("g1", f"Hoje o Senado aprova a PEC {i}")]) for i, d in enumerate(dias)]
    view = midia.destaques([midia.corpus_do_dia(d) for d in arquivos], fim=dias[-1])
    assert view is not None
    return view


def test_pesquisa_traz_o_analitico_e_as_sugestoes(config):
    dados = pesquisa.dados_do_dia(make_edition("2026-10-30"), config, None)
    html = pesquisa.render_pesquisa_page([pesquisa.resumo_do_dia(dados)], config, home_href="../", archive_href="./", destaques=_destaques())
    assert '<section class="md" id="midia" aria-labelledby="mdH" hidden></section>' in html and 'id="mdIr" hidden' in html
    assert "Como a mídia cobre " in html and "function leituraMidia(e, ts)" in html and "midia(e);" in html
    # as sugestões abrem a busca com a editoria e o período dos números (como no Analítico)
    assert 'class="md-sug"' in html and 'href="pesquisa.html?q=Senado&amp;de=2026-' in html and "&amp;ed=politica#midia" in html
    # o analítico vem depois da lista (os resultados ficam onde estavam), com um atalho acima
    assert html.index('id="pqMore"') < html.index('id="midia"') and html.index('id="mdIr"') < html.index('id="pqList"')
    manifesto = json.loads(re.search(r'id="pqDados">(.*?)</script>', html, re.S).group(1))
    assert "The Guardian" in manifesto["exterior"] and "veja" in manifesto["fora"] and "sp" in manifesto["foraNomes"]
    assert "marco" in manifesto["calendario"] and "marco" in manifesto["fora"]
    sem = pesquisa.render_pesquisa_page([pesquisa.resumo_do_dia(dados)], config, home_href="../", archive_href="./")
    assert 'class="md-sug"' not in sem and 'id="midia"' in sem  # sem nomes, o analítico da busca continua


def test_analitico_traz_os_nomes_em_destaque(config):
    view = _destaques()
    html = render_analitico_page([], config, home_href="../", archive_href="./", destaques=view)
    assert 'id="an-nomes"' in html and "Nomes em destaque · como a mídia cobre cada um" in html
    assert 'href="pesquisa.html?q=Senado&amp;de=' in html and "&amp;ed=politica#midia" in html
    assert 'action="pesquisa.html#midia"' in html and ".nm-tbl" in html and 'required pattern=".*\\S.*"' in html
    sem = render_analitico_page([], config, home_href="../", archive_href="./")
    assert 'id="an-nomes"' not in sem and ".nm-tbl" not in sem  # sem nomes, a página fica como era


def test_publish_leva_os_nomes_ao_analitico_e_a_pesquisa(tmp_path, config):
    edition = make_edition("2026-09-29")
    edition.index = [
        IndexTopic(
            section="politica",
            title="Senado aprova a PEC",
            items=[
                IndexItem(source="Folha de S.Paulo", title=f"Hoje o Senado aprova a PEC {i}", url=f"https://www1.folha.uol.com.br/{i}.shtml")
                for i in range(6)
            ],
        )
    ]
    analises = [{
        "id": "a", "url": "https://x.com/a", "titulo": "Texto a", "veiculo": "Folha", "editoria": "Política", "secao": "politica",
        "materia": "m", "data_publicacao": "2026-09-29", "analisado_em": "2026-09-29", "versao_calculo": "1.1.0", "selo": None,
        "genero": "noticia", "triagem": {"relevancia_politica": "alta", "eixos": ["economico"]}, "estado": "ok", "T": 0,
        "rotulo": "Equilibrado", "confianca": "media", "resumo": "Resumo.", "qualidade_apuracao": {"Q": 60, "rotulo": "Regular"},
        "evidencias": [],
    }]
    edition.termometro = analise.registro_do_dia("2026-09-29", analises, provedor="falsa")
    paths = pipeline.publish(edition, config, out_dir=tmp_path, subject="s", email_html="<p>e</p>", email_text="e")
    analitico = paths["analitico"].read_text(encoding="utf-8")
    assert 'id="an-nomes"' in analitico and ">Senado</a>" in analitico and "Texto a" in analitico
    busca = paths["pesquisa"].read_text(encoding="utf-8")
    assert 'href="pesquisa.html?q=Senado&amp;de=' in busca and 'id="midia"' in busca


def test_falha_nos_nomes_nao_derruba_a_edicao(tmp_path, config, monkeypatch, caplog):
    def quebra(*_args, **_kwargs):
        raise ValueError("arquivo estranho")

    monkeypatch.setattr(midia, "corpus_do_dia", quebra)
    paths = pipeline.publish(make_edition("2026-09-29"), config, out_dir=tmp_path, subject="s", email_html="<p>e</p>", email_text="e")
    busca = paths["pesquisa"].read_text(encoding="utf-8")
    assert 'id="midia"' in busca and 'class="md-sug"' not in busca  # o analítico da busca fica; as sugestões, não
    assert "Nomes em destaque: dia 2026-09-29 ignorado (arquivo estranho)" in caplog.text
    monkeypatch.undo()  # agora o corpus sai e o cálculo dos nomes é que falha
    monkeypatch.setattr(midia, "destaques", quebra)
    paths = pipeline.publish(make_edition("2026-09-29"), config, out_dir=tmp_path, subject="s", email_html="<p>e</p>", email_text="e")
    assert paths["pesquisa"].is_file() and "páginas sem o bloco" in caplog.text
