"""Mercado em linha (§4.8): o slide do Em 1 minuto e o substituto da noite (sem Chromium)."""

from __future__ import annotations

import functools
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from qijournal.config import load_config
from qijournal.instagram import conteudo, estatistica as E, gerar, redacao
from qijournal.instagram.formatos import mercado as M
from qijournal.models import Edition

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
real = pytest.mark.skipif(not (DATA / "2026-10-26.json").is_file(), reason="sem os JSONs reais em data/")


@functools.lru_cache(maxsize=None)
def edicao(day: str) -> Edition:
    return Edition.from_dict(json.loads((DATA / f"2026-10-{day}.json").read_text(encoding="utf-8")))


@functools.lru_cache(maxsize=None)
def historia(day: str) -> list[Edition]:
    return E.load_history(DATA, date(2026, 10, int(day)), 90)


def _sintetica(day: date, quotes: list[dict]) -> Edition:
    return Edition.from_dict(
        {
            "date": day.isoformat(), "date_label": day.isoformat(), "generated_at": f"{day}T09:00:00+00:00",
            "mode": "ai", "model": None, "editorial": "", "briefing": [], "lead": "", "secondary": [],
            "highlights": [], "sections": [], "stories": [], "quotes": quotes, "weather": [], "compared": [], "index": [],
        }
    )


def _quote(qid: str, value: float, change: float | None, as_of: datetime) -> dict:
    return {"id": qid, "label": {"ibov": "Ibovespa", "usd": "Dólar"}.get(qid, qid), "value": value, "display": "",
            "change_pct": change, "kind": "index", "as_of": as_of.isoformat(), "source": "Yahoo Finance"}


def _render(slide, kind: str = "image") -> str:
    env = gerar.environment()
    brand = gerar.brand_context(load_config(ROOT))
    return env.get_template(f"{slide.template}.html.j2").render(
        brand=brand, s=conteudo.polish(slide.ctx), page=7, pages=8, kind=kind, slot="manha"
    )


@real
def test_slide_de_21_10_titulo_paineis_e_faixa():
    s = M.mercado_slide(edicao("21"), historia("21"))
    c = s.ctx
    assert s.template == "mercado_linha"
    assert (c["tema"], c["cor"], c["contador"], c["selo"]) == ("t-dark", "em1min", False, "MERCADO EM LINHA · QUA 21 OUT")
    assert (c["titulo"], c["destaque"]) == ("Ibovespa +13,8% desde 28/09", "+13,8%")
    assert [p["id"] for p in c["paineis"]] == ["ibov", "usd", "brent", "btc"]
    p = {x["id"]: x for x in c["paineis"]}
    assert (p["ibov"]["valor"], p["ibov"]["selo"], p["ibov"]["change"], p["ibov"]["trend"]) == ("208.296", "fech. 20/10", "+1,57%", "up")
    assert (p["usd"]["valor"], p["usd"]["selo"], p["usd"]["acumulado"]) == ("R$ 5,01", "leitura das 06h00", "−4,1% desde 28/09")
    assert (p["brent"]["valor"], p["brent"]["acumulado"]) == ("US$ 110,14", "+10,1% desde 29/09")
    assert p["btc"]["valor"] == "US$ 83.366"
    assert all(str(x["grafico"]).startswith("<svg class='graf graf-linha'") for x in c["paineis"])
    assert c["faixa"] == "Selic 13,75% · IPCA 12m 4,58% (set) · juro real ex-post 8,77%"
    assert c["pe"] == "Uma leitura por dia · fech. = fechamento"  # a fonte só na assinatura
    assert c["tracejado"] and p["ibov"]["faltantes"] == 2 and p["usd"]["faltantes"] == 0
    assert c["nota"] == "fonte: Yahoo Finance e BCB"
    # Acumulado ponta a ponta, com as duas datas (§3): a ponta final vai embaixo do título.
    assert c["selo_titulo"] == "até o fechamento de 20/10"


@real
def test_alt_tem_os_valores_e_a_frase_de_cada_grafico():
    alt = M.mercado_slide(edicao("21"), historia("21")).alt
    assert alt.startswith("Mercado em linha, 21/10: Ibovespa +13,8% desde 28/09, até o fechamento de 20/10. ")
    assert "Ibovespa 208.296 (fech. 20/10, +1,57% no dia, +13,8% desde 28/09)" in alt
    assert "Gráficos: Ibovespa: linha com 14 leituras" in alt and "2 pregões sem leitura, tracejado" in alt
    assert len(alt) <= redacao.ALT_MAX


@real
def test_evitar_tira_o_ativo_do_titulo():
    c = M.mercado_slide(edicao("21"), historia("21"), evitar={"ibov"}).ctx
    assert (c["titulo"], c["destaque"]) == ("Brent +10,1% desde 29/09", "+10,1%")


@real
@pytest.mark.parametrize("dia", ["21", "26"])
def test_substituto_da_noite_em_papel_sem_numero(dia):
    p = M.mercado_em_linha(edicao(dia), {}, historia(dia))
    assert (p.slot, p.kind, p.serie, p.numero_serie, p.chave) == ("noite", "image", "mercado", 0, "")
    assert len(p.slides) == 1 and p.slides[0].ctx["tema"] == "t-paper" and p.slides[0].ctx["cor"] == "mercado"
    linhas = p.caption.split("\n")
    ctx = p.slides[0].ctx
    assert linhas[0] == f"Mercado em linha · {dia}/10: {ctx['titulo']} ({ctx['selo_titulo']})" and linhas[1] == ""
    assert len(linhas[0]) <= redacao.LINHA1_MAX and len(p.caption) <= redacao.LEGENDA_MAX
    assert 3 <= sum(1 for x in linhas if x.startswith("▸ ")) <= 5
    assert linhas[-2] == M.CTA and linhas[-1] == "#triangula #dados #mercadofinanceiro #economia"
    assert redacao.lint(p, election=True) == []


@real
def test_noite_de_26_10_nao_titula_com_o_fechamento_de_sexta():
    # 18h30 de segunda: o Ibovespa da edição é o fechamento de sexta (23/10); o título vai para um ativo
    # lido no dia, sempre com a ponta final (dados#2, arte#11).
    p = M.mercado_em_linha(edicao("26"), {}, historia("26"))
    c = p.slides[0].ctx
    assert c["ativo"] != "ibov" and not c["titulo"].startswith("Ibovespa")
    assert c["selo_titulo"].startswith("até a leitura das ")
    assert p.caption.split("\n")[0].endswith(f"({c['selo_titulo']})")
    # O slide 7 do Em 1 minuto (07h00) continua com o maior acumulado, com a data do fechamento.
    manha = M.mercado_slide(edicao("26"), historia("26")).ctx
    assert (manha["ativo"], manha["selo_titulo"]) == ("ibov", "até o fechamento de 23/10")


@real
def test_legenda_do_substituto_em_21_10():
    p = M.mercado_em_linha(edicao("21"), {}, historia("21"))
    assert "▸ Ibovespa: 208.296 pontos (fech. 20/10, +1,57% no dia, +13,8% desde 28/09)." in p.caption
    assert "▸ Dólar: R$ 5,01 (leitura das 06h00, −0,35% no dia, −4,1% desde 28/09)." in p.caption
    assert "▸ Selic 13,75% · IPCA 12m 4,58% (set) · juro real ex-post 8,77%." in p.caption


@real
def test_dia_sem_selic_nem_ipca_fica_sem_faixa_de_juros():
    s = M.mercado_slide(edicao("18"), historia("18"), tema="paper")
    assert s.ctx["faixa"] == "" and "Selic" not in s.alt
    assert 'class="faixa"' not in _render(s)


@real
def test_serie_curta_mostra_so_o_valor_com_o_acumulado():
    e = Edition.from_dict(json.loads((DATA / "2026-10-05.json").read_text(encoding="utf-8")))
    s = M.mercado_slide(e, E.load_history(DATA, date(2026, 10, 5), 90), tema="paper")
    assert not s.ctx["graficos"] and all(p["grafico"] == "" for p in s.ctx["paineis"])
    assert s.ctx["titulo"].endswith("desde 28/09") and "Gráficos" not in s.alt
    html = _render(s)
    assert 'class="frame curta"' in html and "na série: curta para o gráfico" in html


def test_leitura_sem_variacao_nao_mostra_variacao():
    dia = date(2026, 10, 21)
    cedo = datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc)  # 05h00 em Brasília, 0,00%
    e = _sintetica(dia, [_quote("usd", 5.0, 0.0, cedo)])
    p = M.mercado_slide(e, [e]).ctx["paineis"][0]
    assert (p["selo"], p["change"], p["trend"], p["acumulado"], p["grafico"]) == ("", "", "", "", "")


def test_linha_mostra_no_maximo_60_dias():
    fim = date(2026, 10, 21)
    hist = []
    for k in range(80, -1, -1):
        d = fim - timedelta(days=k)
        at = datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)
        hist.append(_sintetica(d, [_quote("usd", 5 + k / 100, 0.1, at)]))
    p = M.mercado_slide(hist[-1], hist).ctx["paineis"][0]
    assert p["leituras"] == M.JANELA_DIAS + 1
    assert p["acumulado"] == f"{E.sinal((5.0 / 5.6 - 1) * 100)} desde {fim - timedelta(days=60):%d/%m}"


def test_unidades_no_texto():
    assert M.valor_fmt("ibov", 208295.98) == "208.296 pontos"
    assert M.valor_fmt("ibov", 208295.98, unidade=False) == "208.296"
    assert M.valor_fmt("usd", 5.0093) == "R$ 5,01" and M.valor_fmt("btc", 83365.79) == "US$ 83.366"
    assert M.nome_corrido("usd", "Dólar") == "dólar" and M.nome_corrido("brent", "Brent") == "Brent"


@real
def test_painel_legivel_no_celular():
    """A 375 px, os 4 painéis tinham 5 linhas de texto (o selo e as datas do gráfico em 17 a 20 px) e o gráfico,
    ~110 px de linha (arte#10). Agora: três linhas ("Ibovespa · fech. 20/10", o valor com a variação, o
    acumulado), a linha compacta com um rótulo só e o eixo, e nenhum texto com dado abaixo de 22 px."""
    s = M.mercado_slide(edicao("21"), historia("21"), tema="paper")
    html = _render(s)
    fonte = (ROOT / "qijournal" / "instagram" / "templates" / "mercado_linha.html.j2").read_text(encoding="utf-8")
    css = fonte.split("{% block style %}")[1].split("{% endblock %}")[0]
    assert min(int(x) for x in re.findall(r"font-size:(\d+)px", css)) >= 22
    assert 'data-min="20"' in html.split('class="pe"')[1].split(">")[0]  # o rodapé encolhe no máximo até 20
    assert html.count('<div class="hd"><span class="nm">') == 4 and '<span class="sl">fech. 20/10</span>' in html
    assert '<div class="vr"><span class="v">208.296</span>' in html
    for p in s.ctx["paineis"]:
        svg = str(p["grafico"])
        assert ET.fromstring(svg).get("viewBox") == f"0 0 {M.PAINEL_LARGURA} {M.PAINEL_ALTURA}"
        assert min(float(x) for x in re.findall(r"font-size:([\d.]+)px", svg)) >= 22
        textos = ["".join(t.itertext()) for t in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}text")]
        assert len(textos) <= 4 and textos[-1].endswith(" leituras"), textos  # 1 rótulo + o eixo
    # o tracejado (Ibovespa com 2 pregões sem leitura) se explica no rodapé, uma vez
    assert html.count("pregão sem leitura") == 1 and "stroke-dasharray=\"3 9\"" in html


@real
@pytest.mark.parametrize("tema, classe", [("dark", "t-dark v2"), ("paper", "t-paper v2")])
def test_template_renderiza_nos_dois_temas(tema, classe):
    s = M.mercado_slide(edicao("21"), historia("21"), tema=tema)
    html = _render(s, "carousel")
    assert classe in html and '<em class="hl">+13,8%</em>' in html
    assert html.count("graf graf-linha") == 4 and "triangula" in html
    s.ctx.update(selo="EM 1 MINUTO Nº 031", contador=True)  # como slide 7 do Em 1 minuto
    assert '<span class="cnt">07/08</span>' in _render(s, "carousel")


def _luminancia(rgb: tuple[int, ...]) -> float:
    c = [v / 255 for v in rgb[:3]]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


@real
@pytest.mark.skipif(not os.environ.get("QIJ_CHROMIUM"), reason="sem Chromium (QIJ_CHROMIUM)")
@pytest.mark.parametrize("dia", ["20", "26", "27"])
def test_render_sem_corte_e_com_a_faixa_visivel(dia, tmp_path):
    """O slide 7 do Em 1 minuto e a noite em papel cabem sem texto cortado, e a faixa de série (y = 3) passa de
    3:1 contra o fundo logo abaixo (y = 20): era #1B2745 sobre #0F1730 e papel sobre papel (arte#4)."""
    from PIL import Image

    from qijournal.instagram.render import Renderer

    manha = M.mercado_slide(edicao(dia), historia(dia))
    manha.ctx.update(selo="EM 1 MINUTO Nº 031", contador=True)
    noite = M.mercado_em_linha(edicao(dia), {}, historia(dia)).slides[0]
    with Renderer() as r:
        for nome, s in (("manha", manha), ("noite", noite)):
            res = r.render(_render(s, "carousel"), tmp_path / f"{nome}.jpg", width=1080, height=1350)
            assert res.transbordos == [], (nome, [str(t) for t in res.transbordos])
            im = Image.open(res.path).convert("RGB")
            a, b = sorted((_luminancia(im.getpixel((540, 3))), _luminancia(im.getpixel((540, 20)))), reverse=True)
            assert (a + 0.05) / (b + 0.05) >= 3.0, nome


@real
def test_mercado_em_linha_evitar_e_o_ativo_no_ctx():
    """Pendência da integração: ``evitar`` do substituto da noite tira o ativo do título (o do Número do dia), e o
    ``ativo`` do ctx diz qual titulou (o Terminal evita esse também)."""
    livre = M.mercado_em_linha(edicao("21"), {}, historia("21")).slides[0].ctx
    assert livre["ativo"] == "brent" and livre["titulo"].startswith("Brent")  # o Ibovespa é o fechamento de ontem
    sem = M.mercado_em_linha(edicao("21"), {}, historia("21"), evitar={"brent"}).slides[0].ctx
    assert sem["ativo"] not in ("brent", "") and not sem["titulo"].startswith("Brent")
    assert sem["titulo"].startswith(next(p["nome"] for p in sem["paineis"] if p["id"] == sem["ativo"]))
    # com todos os ativos evitados, o título fica no maior |acumulado| (o Mercado nunca fica sem título)
    todos = M.mercado_em_linha(edicao("21"), {}, historia("21"), evitar=set(M.ATIVOS)).slides[0].ctx
    assert todos["titulo"] and todos["ativo"] in M.ATIVOS
