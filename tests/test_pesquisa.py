"""Pesquisa analítica: arquivo de busca de cada edição, página e publicação
(``data/busca/``, ``edicoes/pesquisa.html``)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from qijournal import pipeline
from qijournal.config import load_config
from qijournal.models import Edition, IndexItem, IndexTopic
from qijournal.render import pesquisa
from tests.fixtures.pipeline.factory import make_edition

FIXTURE = Path(__file__).parent / "fixtures" / "edicao-2026-10-20.json"


@pytest.fixture
def config():
    return load_config(env={})


def edicao_com_indice() -> Edition:
    """A edição real de 20/10 (matérias e cobertura comparada) com uma lista do dia."""
    edition = Edition.from_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    story = edition.stories[0]
    edition.index = [
        IndexTopic(
            section=story.section,
            title=story.headline,
            story_id=story.id,
            items=[
                IndexItem(source="Folha de S.Paulo", title="Título da Folha", url="https://www1.folha.uol.com.br/a.shtml", published="2026-10-20T08:00:00+00:00"),
                IndexItem(source="Agência X", title="Título sem https", url="http://agencia.example/b", published="2026-10-19T23:30:00+00:00"),
            ],
        ),
        IndexTopic(
            section="esporte",
            title="Jogo da rodada",
            items=[IndexItem(source="ge", title="Jogo da rodada", url="https://ge.globo.com/c", published=None)],
        ),
        IndexTopic(section="mundo", title="Inválida", items=[IndexItem(source="X", title="Sem link", url="javascript:alert(1)")]),
    ]
    return edition


def _analise(i: str, **extra) -> dict:
    base = {
        "id": i, "url": f"https://exemplo.com/{i}", "titulo": f"Texto {i}", "veiculo": "Folha de S.Paulo",
        "editoria": "Política", "secao": "politica", "materia": "m", "data_publicacao": "2026-10-19",
        "analisado_em": "2026-10-20", "versao_calculo": "1.1.0", "selo": None, "estado": "ok", "T": -14,
        "rotulo": "Leve à esquerda", "resumo": "Resumo do cálculo.", "qualidade_apuracao": {"Q": 61, "rotulo": "Regular"},
    }
    return {**base, **extra}


# ── arquivo de busca ─────────────────────────────────────────────────────────


def test_arquivo_do_dia_tem_tudo_o_que_a_edicao_publicou(config):
    edition = edicao_com_indice()
    registro = {"analises": [_analise("a1"), _analise("a2", estado="nao_se_aplica", T=None, origem="cobertura comparada")]}
    dados = pesquisa.dados_do_dia(edition, config, registro)

    assert dados["v"] == pesquisa.VERSAO and dados["data"] == "2026-10-20" and dados["todas"] is True
    veiculos, secoes = dados["veiculos"], dados["secoes"]
    # matérias: título, linha fina, texto sem marcação, "por que importa", veículos e a manchete marcada
    assert len(dados["materias"]) == len(edition.stories)
    lead = next(m for m in dados["materias"] if m.get("m") == 1)
    assert lead["id"] == edition.lead
    primeira = dados["materias"][0]
    assert "**" not in primeira["c"] and primeira["t"] == edition.stories[0].headline
    assert [veiculos[i] for i in primeira["f"]] == list(dict.fromkeys(s.name for s in edition.stories[0].sources))
    assert secoes[primeira["s"]][0] == edition.stories[0].section
    # todas as notícias: link sem "https://" (http fica inteiro), minutos desde a meia-noite da edição
    # (no fuso do jornal; negativo no dia anterior) e só os links http(s)
    noticias = dados["noticias"]
    assert [n[3] for n in noticias] == ["Título da Folha", "Título sem https", "Jogo da rodada"]
    assert [n[4] for n in noticias] == ["www1.folha.uol.com.br/a.shtml", "http://agencia.example/b", "ge.globo.com/c"]
    assert [n[5] for n in noticias] == [5 * 60, -(24 * 60) + 20 * 60 + 30, None]
    assert veiculos[noticias[1][2]] == "Agência X"
    # assunto que virou matéria fica; assunto com uma notícia de mesmo título, não
    assert dados["assuntos"] == [[edition.stories[0].headline, edition.stories[0].id, 0]]
    assert [n[1] for n in noticias] == [0, 0, -1]
    # cobertura comparada: assunto, conclusão, rótulo do medidor e o foco de cada veículo
    assert len(dados["comparadas"]) == len(edition.compared)
    c = dados["comparadas"][0]
    assert c["t"] == edition.compared[0].topic and c["c"] == edition.compared[0].conclusion and c["p"]
    assert {veiculos[o[0]] for o in c["f"]} == {o.name for o in edition.compared[0].outlets}
    # análises do termômetro feitas no dia, com a origem
    assert [(a["id"], a["T"], a["o"]) for a in dados["analises"]] == [("a1", -14, "materia"), ("a2", None, "cobertura comparada")]
    assert dados["analises"][0]["u"] == "exemplo.com/a1" and veiculos[dados["analises"][0]["f"]] == "Folha de S.Paulo"
    # compacto e lido de volta
    texto = pesquisa.serializar(dados)
    assert "\n" not in texto.rstrip("\n") and json.loads(texto) == dados


def test_resumo_e_versao_do_arquivo(tmp_path, config):
    dados = pesquisa.dados_do_dia(edicao_com_indice(), config, None)
    resumo = pesquisa.resumo_do_dia(dados)
    assert resumo["d"] == "2026-10-20" and resumo["n"] == [24, 3, 3, 0] and resumo["t"] is True
    assert resumo["veiculos"]["Folha de S.Paulo"] >= 1
    caminho = pesquisa.caminho_do_dia(tmp_path, "2026-10-20")
    caminho.parent.mkdir(parents=True)
    caminho.write_text(pesquisa.serializar(dados), encoding="utf-8")
    assert pesquisa.ler_dia(caminho) == dados
    caminho.write_text(json.dumps({**dados, "v": pesquisa.VERSAO + 1}), encoding="utf-8")
    assert pesquisa.ler_dia(caminho) is None  # formato antigo: o publish refaz


def test_pagina_traz_indice_filtros_e_nao_deixa_texto_fechar_o_script(config):
    dados = pesquisa.dados_do_dia(edicao_com_indice(), config, None)
    dados["veiculos"].append("</script><script>alert(1)</script>")
    dados["noticias"].append([0, -1, len(dados["veiculos"]) - 1, "Notícia", "exemplo.com/x", 10])
    html = pesquisa.render_pesquisa_page([pesquisa.resumo_do_dia(dados)], config, home_href="../", archive_href="./", edition_date="2026-10-20")
    assert "<title>Triangula — Pesquisa analítica</title>" in html
    manifesto = re.search(r'<script type="application/json" id="pqDados">(.*?)</script>', html, re.S).group(1)
    indice = json.loads(manifesto)
    assert indice["base"] == "../data/busca/" and [d["d"] for d in indice["dias"]] == ["2026-10-20"]
    assert "</script><script>" not in manifesto
    assert '<option value="politica">' in html and "<option>Folha de S.Paulo</option>" in html
    assert 'name="tipo" value="a" checked' in html  # marca com termômetro: o tipo Termômetro aparece
    assert "Busca em 1 edição do arquivo, de 20/10/2026: 4 notícias coletadas, 24 matérias" in html


# ── publicação ───────────────────────────────────────────────────────────────


def _publicar(edition: Edition, config, out: Path) -> dict[str, Path]:
    return pipeline.publish(edition, config, out_dir=out, subject="s", email_html="<p>e</p>", email_text="e")


def _semear(out: Path, dia: str) -> None:
    (out / "edicoes").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "edicoes" / f"{dia}.html").write_text("<html></html>", encoding="utf-8")
    (out / "data" / f"{dia}.json").write_text(json.dumps(make_edition(dia).to_dict()), encoding="utf-8")


def test_publish_grava_a_busca_do_dia_cria_as_que_faltam_e_apaga_as_expiradas(tmp_path, config):
    config.site.archive_keep_days = 30  # corte: 2026-08-30
    for dia in ["2026-08-01", "2026-09-27", "2026-09-28"]:
        _semear(tmp_path, dia)
    (tmp_path / "edicoes" / "2026-09-26.html").write_text("<html></html>", encoding="utf-8")  # sem JSON: fica fora
    expirada = pesquisa.caminho_do_dia(tmp_path / "data", "2026-08-01")
    expirada.parent.mkdir(parents=True)
    expirada.write_text("{}", encoding="utf-8")

    paths = _publicar(make_edition("2026-09-29"), config, tmp_path)

    busca = sorted(p.name for p in (tmp_path / "data" / "busca").iterdir())
    assert busca == ["2026-09-27.json", "2026-09-28.json", "2026-09-29.json"]
    assert paths["busca"] == tmp_path / "data" / "busca" / "2026-09-29.json"
    pagina = paths["pesquisa"].read_text(encoding="utf-8")
    indice = json.loads(re.search(r'id="pqDados">(.*?)</script>', pagina, re.S).group(1))
    assert [d["d"] for d in indice["dias"]] == ["2026-09-29", "2026-09-28", "2026-09-27"]
    hoje = json.loads(paths["busca"].read_text(encoding="utf-8"))
    assert hoje["data"] == "2026-09-29" and len(hoje["materias"]) == len(make_edition("2026-09-29").stories)


def test_publish_nao_reescreve_a_busca_das_edicoes_anteriores(tmp_path, config):
    _publicar(make_edition("2026-09-28"), config, tmp_path)
    ontem = pesquisa.caminho_do_dia(tmp_path / "data", "2026-09-28")
    antes = ontem.read_bytes()
    ontem.write_bytes(antes.replace(b'"data":"2026-09-28"', b'"data":"2026-09-28","marca":1'))
    _publicar(make_edition("2026-09-29"), config, tmp_path)
    assert b'"marca":1' in ontem.read_bytes()  # arquivo válido do dia anterior fica como está


def test_capa_e_edicoes_levam_a_busca_para_a_pesquisa(tmp_path, config):
    paths = _publicar(make_edition("2026-09-29"), config, tmp_path)
    capa = paths["index"].read_text(encoding="utf-8")
    arquivada = paths["edition"].read_text(encoding="utf-8")
    assert 'href="edicoes/pesquisa.html" title="Pesquisa analítica' in capa
    assert 'action="edicoes/pesquisa.html"' in capa
    assert 'href="./pesquisa.html" title="Pesquisa analítica' in arquivada and 'action="./pesquisa.html"' in arquivada
    assert 'href="pesquisa.html" title="Pesquisa analítica' in paths["archive"].read_text(encoding="utf-8")
