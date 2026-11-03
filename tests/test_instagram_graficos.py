"""Gráficos do Instagram (graficos.py), macros do sistema visual 2 e relatório de transbordo do render."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from markupsafe import Markup

from qijournal.instagram import graficos as G
from qijournal.instagram.render import RenderResult, Transbordo

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "qijournal" / "instagram" / "templates"
UTC = timezone.utc
NS = "{http://www.w3.org/2000/svg}"


def conta(svg: str, tag: str) -> int:
    return len(re.findall(rf"<{tag}[\s>/]", svg))


def textos(svg: str) -> list[str]:
    """Texto de cada <text>, juntando os <tspan>."""
    raiz = ET.fromstring(svg)
    return ["".join(t.itertext()) for t in raiz.iter(f"{NS}text")]


def caixas_texto(svg: str) -> list[tuple[float, float, float, float, str]]:
    """Caixa aproximada de cada <text> simples (sem tspan), pela tabela de larguras."""
    out = []
    for t in ET.fromstring(svg).iter(f"{NS}text"):
        if len(t):
            continue
        estilo = t.get("style", "")
        tam = float(re.search(r"font-size:([\d.]+)px", estilo).group(1))
        fonte = "mono" if "Plex Mono" in estilo else ("hook" if "62%" in estilo else "label")
        w = G.largura_texto(t.text or "", fonte, tam)
        x, y = float(t.get("x")), float(t.get("y"))
        anc = t.get("text-anchor", "start")
        x0 = x if anc == "start" else (x - w if anc == "end" else x - w / 2)
        out.append((x0, y - tam * 0.75, x0 + w, y + tam * 0.2, t.text or ""))
    return out


def sem_sobreposicao(svg: str) -> None:
    cx = caixas_texto(svg)
    for i, a in enumerate(cx):
        for b in cx[i + 1 :]:
            separados = a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
            assert separados, f"rótulos sobrepostos: {a[4]!r} e {b[4]!r}"


def valido(svg: str) -> ET.Element:
    """SVG bem formado, com viewBox, só tokens de cor e sem aspas duplas (atravessa o polish)."""
    assert isinstance(svg, Markup)
    raiz = ET.fromstring(svg)
    assert raiz.tag == f"{NS}svg" and raiz.get("viewBox", "").startswith("0 0 ")
    assert '"' not in svg
    cores = set(re.findall(r"(?:fill|stroke)='([^']+)'", svg))
    assert all(c.startswith("var(--") or c == "none" for c in cores), cores
    return raiz


# ── Dados reais de 21/10 (SpaceX e Tesla), copiados do JSON ───────────────

BATIDAS = [
    ("The New York Times", "2026-10-20T16:17:54+00:00", "a"),
    ("TechCrunch", "2026-10-20T20:40:41+00:00", "neutro"),
    ("Valor Econômico", "2026-10-20T21:17:24+00:00", "a"),
    ("Folha de S.Paulo", "2026-10-20T21:31:00+00:00", "neutro"),
    ("MarketWatch", "2026-10-21T00:14:00+00:00", "neutro"),
    ("g1", "2026-10-21T00:44:56+00:00", "a"),
    ("Financial Times", "2026-10-21T01:49:39+00:00", "b"),
    ("Al Jazeera", "2026-10-21T03:51:39+00:00", "a"),
    ("BBC News", "2026-10-21T06:36:39+00:00", "b"),
    ("CNBC", "2026-10-21T08:28:13+00:00", "a"),
    ("InfoMoney", "2026-10-21T08:45:50+00:00", "a"),
    ("The Wall Street Journal", "2026-10-21T09:01:41+00:00", None),
]
ROT = {"a": "sinergia em IA", "b": "complexidade de governança"}
IBOV = [  # fechamentos de 28/09 a 20/10 (14 leituras)
    ("2026-09-28", 182991.12), ("2026-09-29", 183827.6), ("2026-10-01", 184924.62), ("2026-10-02", 192114.55),
    ("2026-10-05", 207949.73), ("2026-10-06", 204961.38), ("2026-10-07", 204990.47), ("2026-10-08", 206220.23),
    ("2026-10-09", 209066.9), ("2026-10-14", 206505.98), ("2026-10-15", 204324.27), ("2026-10-16", 205430.33),
    ("2026-10-19", 205079.25), ("2026-10-20", 208295.98),
]
BTC = [  # variação diária do bitcoin, 29/09 a 21/10 (23 leituras)
    -0.08, -1.1, 0.1, 2.36, -2.04, 0.47, -0.06, 1.59, -3.07, -2.52, -0.09, 0.21, 0.2, 0.62, -0.07, -1.98, -2.36,
    2.23, -0.4, 0.57, 1.04, -1.6, 2.44,
]


def batidas():
    return [(n, datetime.fromisoformat(t)) for n, t, _ in BATIDAS]


def stance():
    return {n: s for n, _, s in BATIDAS if s}


# ── Medida de texto ──────────────────────────────────────────────────────


def test_largura_texto_usa_a_tabela_da_fonte():
    assert G.largura_texto("0000", "mono", 20) == pytest.approx(48)
    assert G.largura_texto("É", "label", 100) == G.largura_texto("E", "label", 100)
    assert G.largura_texto("M", "hook", 100) < G.largura_texto("M", "label", 100)


def test_tamanho_gancho_cabe_nas_linhas_pedidas():
    assert G.tamanho_gancho("Mesmo fato, várias versões.") == 150
    gancho = "6 de 11 destacaram sinergia em IA; 2, complexidade de governança."
    tam = G.tamanho_gancho(gancho)
    assert 112 <= tam < 150
    assert len(G.linhas_texto(gancho.upper(), "hook", tam, G.LARGURA * 0.985, -0.01)) <= 4
    # o G2 de 21/10 (87 caracteres) não cabe em 4 linhas nem no mínimo: devolve o mínimo
    g2 = "11 veículos, mesmo fato. 6 destacaram sinergia em IA. Só 2, complexidade de governança."
    assert G.tamanho_gancho(g2, tam_min=96) == 96


# ── barra_anb ────────────────────────────────────────────────────────────


def test_barra_anb_capa_do_raiox():
    svg, desc = G.barra_anb(6, 3, 2, {"a": "maioria", "n": "sem lado", "b": "minoria"})
    valido(svg)
    assert conta(svg, "rect") == 3
    assert {"6", "3", "2", "maioria", "sem lado", "minoria", "11 veículos analisados"} <= set(textos(svg))
    assert desc == "Gráfico: barra com 6 veículos na maioria, 3 sem lado e 2 na minoria."
    assert not any("%" in t for t in textos(svg))  # sem percentual


def test_barra_anb_mini_selo_sem_texto():
    svg, desc = G.barra_anb(6, 3, 2, mini=True)
    raiz = valido(svg)
    assert raiz.get("viewBox") == "0 0 240 16"
    assert conta(svg, "rect") == 3 and conta(svg, "text") == 0
    assert desc == "Gráfico: barra com 6 veículos de um lado, 3 sem lado e 2 do outro."


def test_barra_anb_casos_de_borda():
    vazio, desc = G.barra_anb(0, 0, 0)
    assert conta(vazio, "rect") == 1 and "vazia" in desc
    assert conta(G.barra_anb(0, 0, 0, mini=True)[0], "rect") == 1
    um, desc = G.barra_anb(5, 0, 0, {"a": "maioria"})
    assert conta(um, "rect") == 1 and desc == "Gráfico: barra com 5 veículos na maioria."
    # segmento mínimo de 64 px: a contagem de 1 dígito cabe dentro
    estreito, _ = G.barra_anb(40, 0, 1, {"a": "maioria", "b": "minoria"})
    assert {"40", "1", "minoria"} <= set(textos(estreito))
    larguras = [float(w) for w in re.findall(r"width='([\d.]+)'", estreito)[1:]]
    assert min(larguras) >= 64
    # quando nem assim cabe, a contagem vai para o rótulo
    fino, _ = G.barra_anb(5000, 0, 10, {"a": "maioria", "b": "minoria"})
    assert "minoria · 10" in textos(fino) and "10" not in textos(fino)


# ── relogio ──────────────────────────────────────────────────────────────


def test_relogio_spacex_21_10():
    svg, desc = G.relogio(batidas(), stance(), date(2026, 10, 21), rotulos=ROT)
    valido(svg)
    assert desc == (
        "Gráfico: linha do tempo com 12 pontos, de 20/10 13h17 a 21/10 06h01; 6 do lado sinergia em IA, "
        "2 do lado complexidade de governança, 3 neutros, 1 fora da análise."
    )
    assert conta(svg, "circle") == 12 + 4  # + as 4 marcas da legenda
    assert svg.count("fill='var(--bg)' stroke='var(--ink2)'") == 2  # The Wall Street Journal e a marca da legenda
    ts = textos(svg)
    assert "00h 21/10" in ts and "16h43 do primeiro ao último horário" in ts
    # a data vai no eixo, uma vez: na 1ª marca e na virada; as linhas só com HHhMM e o veículo
    assert "12h 20/10" in ts
    linhas = [t for t in ts if any(n in t for n, _, _ in BATIDAS)]
    assert linhas[0] == "13h17The New York Times"
    assert linhas[-1] == "06h01The Wall Street Journal"
    assert not any("/10" in t for t in linhas)
    # a legenda sob o eixo nomeia cada cor (nada de rótulo por linha nem nome cortado)
    assert {"sinergia em IA", "sem lado", "complexidade de governança", "fora da análise"} <= set(ts)
    assert not any("…" in t for t in ts)
    assert any(t.startswith("12 veículos · horário informado") for t in ts)
    # marcas de 3 em 3 h (a das 15h cede lugar à 1ª marca, mais larga com a data)
    assert {"18h", "21h", "03h", "06h"} <= set(ts)
    # a altura da legenda que o formato desconta do slide
    assert G.altura_legenda_relogio(batidas(), stance(), ROT) == G.LEGENDA_LINHA + 14
    assert G.altura_legenda_relogio(batidas(), None) == 0


def _caixas_svg(svg: str) -> list[tuple[float, float, str]]:
    """(x0, x1, texto) de cada <text>, juntando os <tspan> (com o dx) pela tabela de larguras."""
    out = []
    for t in ET.fromstring(svg).iter(f"{NS}text"):
        partes = [t] if not len(t) else list(t)
        w = 0.0
        for el in partes:
            estilo = el.get("style", "") or t.get("style", "")
            m = re.search(r"font-size:([\d.]+)px", estilo)
            tam = float(m.group(1)) if m else 22
            fonte = "mono" if "Plex Mono" in estilo else ("hook" if "62%" in estilo else "label")
            w += float(el.get("dx", 0)) + G.largura_texto(el.text or "", fonte, tam)
        x = float(t.get("x", 0))
        anc = t.get("text-anchor", "start")
        x0 = x if anc == "start" else (x - w if anc == "end" else x - w / 2)
        out.append((x0, x0 + w, "".join(t.itertext())))
    return out


def dentro_do_quadro(svg: str) -> None:
    largura = float(ET.fromstring(svg).get("width"))
    for x0, x1, txt in _caixas_svg(svg):
        assert x0 >= -1 and x1 <= largura + 1, f"texto fora do quadro [{x0:.0f}, {x1:.0f}] de {largura:.0f}: {txt!r}"


def test_relogio_28_10_linha_do_meio_nao_sai_do_quadro():
    """28/10: 15 veículos, os do meio entre 21h04 e 23h58 viravam "+4 veículos entre 27/10 21h04 e 27/10
    23h58", cortado na borda esquerda (arte#0)."""
    base = datetime(2026, 10, 27, 9, 13, tzinfo=G.TZ)
    minutos = [0, 54, 134, 185, 219, 379, 384, 581, 646, 683, 711, 760, 790, 885, 1103]
    bs = [(f"Veículo {i}", base + timedelta(minutes=m)) for i, m in enumerate(minutos)]
    st = {n: ("a" if i < 6 else "b") for i, (n, _) in enumerate(bs[:-2])}
    svg, _ = G.relogio(bs, st, date(2026, 10, 28), rotulos={"a": "fuga ilícita", "b": "asilo político"},
                       linha=44, max_linhas=12)
    valido(svg)
    dentro_do_quadro(svg)
    assert any(t.startswith("+4 veículos") for t in textos(svg))


def test_graficos_de_texto_nunca_saem_do_quadro():
    for svg, _ in (
        G.relogio(batidas(), stance(), date(2026, 10, 21), rotulos=ROT),
        G.relogio(batidas(), stance(), date(2026, 10, 21), rotulos=ROT, linha=44, max_linhas=6),
        G.espectro(["The New York Times", "Valor Econômico"], ["CNBC"], ["Financial Times", "BBC News"],
                   fora=["NeoFeed", "The New York Times", "Financial Times", "The Wall Street Journal", "Bloomberg Línea"]),
        G.linha(ibov(), [date(2026, 9, 30)], prefixo="R$ "),
        G.barras_variacao(btc(), 1.5375),
    ):
        dentro_do_quadro(svg)


def test_espectro_fora_da_analise_com_nomes_inteiros():
    fora = ["NeoFeed", "The New York Times", "Financial Times", "The Wall Street Journal", "Bloomberg Línea"]
    svg, desc = G.espectro(["g1", "Estadão"], [], ["Folha de S.Paulo", "Valor Econômico"], fora=fora)
    linha = next(t for t in textos(svg) if t.startswith("fora da análise:"))
    assert "…" not in linha and re.search(r" \+\d$", linha)
    mostrados = linha.removeprefix("fora da análise: ").rsplit(" +", 1)[0].split(", ")
    assert set(mostrados) <= set(fora) and len(mostrados) + int(linha.rsplit("+", 1)[1]) == len(fora)
    assert "fora da análise: " + ", ".join(fora) in desc  # o texto alternativo leva todos


def test_relogio_muitas_linhas_e_virada_de_dois_dias():
    base = datetime(2026, 10, 19, 22, 0, tzinfo=G.TZ)
    bs = [(f"Veículo {i}", base + timedelta(hours=2 * i)) for i in range(20)]
    svg, desc = G.relogio(bs, None, date(2026, 10, 21), max_linhas=12)
    valido(svg)
    assert desc.startswith("Gráfico: linha do tempo com 20 pontos, de 19/10 22h00 a 21/10 12h00")
    assert conta(svg, "circle") == 20  # 11 linhas + 9 pontos pequenos na linha "+9"
    ts = textos(svg)
    assert any(t.startswith("+9 veículos entre") for t in ts)
    assert {"00h 20/10", "00h 21/10"} <= set(ts)
    assert "fora da análise" not in svg  # sem cobertura comparada, nenhum rótulo de lado
    altura = float(ET.fromstring(svg).get("height"))
    assert altura < 44 + 52 + 12 * 60 + 60


def test_relogio_bordas():
    svg, desc = G.relogio([], None)
    assert conta(svg, "circle") == 0 and "sem horários" in desc
    um, desc = G.relogio([("g1", datetime(2026, 10, 21, 9, 0, tzinfo=UTC))], None, date(2026, 10, 21))
    assert conta(um, "circle") == 1 and desc == "Gráfico: linha do tempo com 1 ponto, em 21/10 06h00."
    # datetime ingênuo é Brasília; objeto com .source/.at também serve
    class Batida:
        def __init__(self, source, at):
            self.source, self.at = source, at
    svg, desc = G.relogio([Batida("A", datetime(2026, 10, 21, 8, 0)), Batida("B", datetime(2026, 10, 21, 9, 30))])
    assert "de 21/10 08h00 a 21/10 09h30" in desc
    # nome longo em gráfico estreito: cortado com reticências, nunca para fora
    estreito, _ = G.relogio(batidas()[:3], stance(), date(2026, 10, 21), rotulos=ROT, largura=420)
    assert any("…" in t for t in textos(estreito))


# ── espectro ─────────────────────────────────────────────────────────────


def test_espectro_raiox():
    maj = ["The New York Times", "Valor Econômico", "g1", "Al Jazeera", "CNBC", "InfoMoney"]
    neu = ["TechCrunch", "Folha de S.Paulo", "MarketWatch"]
    mino = ["Financial Times", "BBC News"]
    svg, desc = G.espectro(maj, neu, mino, rotulos=(ROT["a"], ROT["b"]), fora=["The Wall Street Journal"])
    valido(svg)
    assert conta(svg, "circle") == 6 + 3 + 2 + 1
    ts = textos(svg)
    assert {"6", "3", "2", "sem lado", "11 veículos analisados"} <= set(ts)
    assert set(maj + neu + mino) <= set(ts)
    assert "fora da análise: The Wall Street Journal" in ts
    assert "complexidade de governança" in " ".join(ts) and not any("…" in t for t in ts)
    assert desc.startswith("Gráfico: espectro com 11 veículos analisados; à esquerda, 6 do lado sinergia em IA (")
    assert "no meio, 3 sem lado" in desc and "à direita, 2 do lado complexidade de governança" in desc
    assert not any("%" in t for t in textos(svg))  # sem percentual


def test_espectro_sem_neutros_e_com_muitos_nomes():
    nomes = [f"Veículo com nome comprido {i}" for i in range(12)]
    svg, desc = G.espectro(nomes, [], ["BBC News"], lado_maioria="b")
    valido(svg)
    ts = textos(svg)
    assert "+6" in ts and "sem lado" not in ts
    assert svg.count("fill='var(--g-b)'") == 12 and svg.count("fill='var(--g-a)'") == 1
    assert desc.startswith("Gráfico: espectro com 13 veículos analisados; à esquerda, 12 na maioria (")
    vazio, _ = G.espectro(["g1", "CNN Brasil"], [], [])
    assert "stroke-dasharray" in vazio and "0" in textos(vazio)


# ── waffle ───────────────────────────────────────────────────────────────


def test_waffle_placar_4_de_26():
    svg, desc = G.waffle(26, 4, 0)
    valido(svg)
    assert svg.count("fill='var(--g-hi)'") == 4
    assert svg.count("fill='var(--g-cinza)'") == 22
    assert conta(svg, "rect") == 26 + 2  # + contorno + a amostra do contorno no rótulo
    assert {"4", "22", "dividiram", "sem divisão", "este Raio-X"} <= set(textos(svg))
    assert "base: 26 assuntos com 4 ou mais veículos comparados" in textos(svg)
    # o laranja fica no quadrado e no gancho; a contagem da legenda vai em tinta (um destaque por slide: arte#17)
    assert "var(--accent)" not in svg
    assert desc == (
        "Gráfico: grade de 26 quadrados, um por assunto com 4 ou mais veículos comparados; "
        "4 dividiram e 22 sem divisão; o contorno marca este Raio-X."
    )
    # 7 por linha, quadrados de 64 px
    xs = sorted({float(x) for x in re.findall(r"<rect x='([\d.]+)' y='[\d.]+' width='64'", svg)})
    assert len(xs) == 7


def test_waffle_dia_de_consenso_e_bordas():
    svg, desc = G.waffle(16, 0)
    assert svg.count("fill='var(--g-hi)'") == 0 and "0 dividiram e 16 sem divisão" in desc
    assert "contorno" not in desc
    vazio, desc = G.waffle(0, 0)
    assert conta(vazio, "rect") == 0 and "grade vazia" in desc
    grande, _ = G.waffle(300, [0, 5, 299], unidade="assunto da semana")
    valido(grande)
    assert grande.count("fill='var(--g-hi)'") == 3
    ys = {float(y) for y in re.findall(r"<rect x='[\d.]+' y='([\d.]+)'", grande)}
    assert len([y for y in ys if y < 1000]) <= 11  # até 10 linhas (+ a amostra)
    um, desc = G.waffle(1, 1)
    assert "grade de 1 quadrado" in desc


# ── matriz_pontos ────────────────────────────────────────────────────────

# 21/10: 1.159 assuntos, 1.039 com 1 fonte, 60 com 3+, 22 com 5+, 1 com 10+ (o de 12 fontes)
CONTAGENS = [1] * 1039 + [2] * 60 + [3] * 38 + [5] * 21 + [12]


def test_matriz_pontos_concentracao_21_10():
    svg, desc = G.matriz_pontos(CONTAGENS, len(CONTAGENS) - 1, rotulo_destaque="SpaceX e Tesla: 12 veículos")
    valido(svg)
    assert conta(svg, "circle") == 1159 + 3  # + as amostras das 3 faixas
    assert svg.count("fill='var(--g-hi)'") == 1
    ts = textos(svg)
    assert "1.159 assuntos · 60 com 3+ · 22 com 5+ · 1 com 10+" in ts
    assert {"22", "98", "1.039", "SpaceX e Tesla: 12 veículos"} <= set(ts)
    assert desc == (
        "Gráfico: matriz com 1.159 pontos, um por assunto; 1.039 com 1 fonte, 98 com 2 a 4 fontes e "
        "22 com 5 ou mais fontes; em destaque, SpaceX e Tesla: 12 veículos."
    )
    # 34 colunas e o ponto ampliado inteiro dentro do desenho
    raiz = ET.fromstring(svg)
    cxs = sorted({c.get("cx") for c in raiz.iter(f"{NS}circle") if c.get("fill") != "var(--g-hi)"})
    assert len(cxs) == 34 + 1  # + a coluna das amostras
    d = next(c for c in raiz.iter(f"{NS}circle") if c.get("fill") == "var(--g-hi)")
    assert float(d.get("cx")) - float(d.get("r")) >= 0


def test_matriz_pontos_1200_sem_rotulos_e_bordas():
    svg, desc = G.matriz_pontos([1] * 1200, None, rotulos=False, altura_max=500)
    valido(svg)
    assert conta(svg, "circle") == 1200
    assert float(ET.fromstring(svg).get("height")) <= 500 + 60
    assert "em destaque" not in desc
    vazio, desc = G.matriz_pontos([], None)
    assert conta(vazio, "circle") == 0 and "matriz vazia" in desc
    um, desc = G.matriz_pontos([3], 0)
    assert conta(um, "circle") == 2 and "em destaque, 3 fontes" in desc


# ── linha ────────────────────────────────────────────────────────────────


def ibov():
    return [(date.fromisoformat(d), v) for d, v in IBOV]


def test_linha_ibovespa_desde_28_09():
    faltantes = [date(2026, 9, 30), date(2026, 10, 10), date(2026, 10, 13)]
    svg, desc = G.linha(ibov(), faltantes, altura=560)
    valido(svg)
    assert conta(svg, "circle") == 14
    assert svg.count("fill='var(--g-hi)'") == 1
    assert "stroke-dasharray='3 9'" in svg  # pregões sem leitura
    ts = textos(svg)
    assert {"182.991", "208.296", "20/10", "máx. 209.067 · 09/10"} <= set(ts)
    assert "28/09 · mín." in ts
    assert ts[-1] == "14 leituras, de 28/09 a 20/10 · 3 pregões sem leitura (tracejado)"
    assert desc == (
        "Gráfico: linha com 14 leituras, de 182.991 (28/09) a 208.296 (20/10); máxima de 209.067 em 09/10; "
        "mínima de 182.991 em 28/09; 3 pregões sem leitura, tracejado."
    )
    sem_sobreposicao(svg)


def test_linha_compacta_e_bordas():
    svg, _ = G.linha(ibov(), [], largura=440, altura=300, prefixo="US$ ", casas=2, compacto=True)
    valido(svg)
    assert ET.fromstring(svg).get("viewBox") == "0 0 440 300"
    sem_sobreposicao(svg)
    um, desc = G.linha([(date(2026, 10, 21), 5.01)], casas=2, prefixo="R$ ")
    assert conta(um, "circle") == 1 and desc == "Gráfico: uma leitura, R$ 5,01 em 21/10."
    vazio, desc = G.linha([])
    assert conta(vazio, "circle") == 0 and desc == "Gráfico: linha sem leituras."
    plano, _ = G.linha([(date(2026, 10, d), 100.0) for d in range(1, 8)])
    valido(plano)
    # datetimes com fuso entram pelo dia de Brasília
    dts = [(datetime(2026, 10, 20, 2, 0, tzinfo=UTC), 1.0), (datetime(2026, 10, 21, 2, 0, tzinfo=UTC), 2.0)]
    assert "de 1 (19/10) a 2 (20/10)" in G.linha(dts)[1]


def _tamanhos(svg: str) -> list[float]:
    return [float(x) for x in re.findall(r"font-size:([\d.]+)px", svg)]


def test_linha_compacta_legivel_a_375_px():
    """No Mercado em linha, o compacto tinha valor em 20 px e data em 17 px (5,9 px no celular), três rótulos de
    duas linhas e a base em duas linhas: o texto dominava os ~110 px da linha (arte#10). Agora: um rótulo só
    (o extremo do meio), o eixo com as duas datas e a base, tudo com 22 px ou mais, e a linha ocupa o quadro."""
    faltantes = [date(2026, 10, 10), date(2026, 10, 13)]
    svg, desc = G.linha(ibov(), faltantes, largura=440, altura=270, compacto=True)
    valido(svg)
    dentro_do_quadro(svg)
    sem_sobreposicao(svg)
    assert textos(svg) == ["máx. 209.067 · 09/10", "28/09", "20/10", "14 leituras"]
    assert min(_tamanhos(svg)) >= 22
    assert "stroke-dasharray='3 9'" in svg and svg.count("fill='var(--g-hi)'") == 1  # o tracejado e o ponto de hoje
    ys = [float(y) for y in re.findall(r"<circle cx='[\d.]+' cy='([\d.]+)'", svg)]
    assert max(ys) - min(ys) >= 170  # a linha tinha ~110 px de altura
    assert "2 pregões sem leitura, tracejado" in desc  # o texto alternativo continua com tudo
    # extremo na ponta (série que só sobe): o rótulo é o valor da 1ª leitura, embaixo dela
    sobe = [(date(2026, 10, 1) + timedelta(days=k), 100.0 + k) for k in range(10)]
    svg, _ = G.linha(sobe, [], largura=440, altura=270, prefixo="US$ ", casas=2, compacto=True)
    dentro_do_quadro(svg)
    sem_sobreposicao(svg)
    assert textos(svg) == ["US$ 100,00", "01/10", "10/10", "10 leituras"] and min(_tamanhos(svg)) >= 22


def test_linha_grande_com_pregao_faltante_na_base_sem_cortar():
    faltantes = [date(2026, 10, 10), date(2026, 10, 13)]
    svg, _ = G.linha(ibov(), faltantes, altura=560)
    assert textos(svg)[-1] == "14 leituras, de 28/09 a 20/10 · 2 pregões sem leitura (tracejado)"
    assert not any(t.endswith("…") for t in textos(svg))


# ── barras_juros ─────────────────────────────────────────────────────────


def test_barras_juros_selic_ipca_e_juro_real():
    svg, desc = G.barras_juros(13.75, 4.58, 8.77, "(1 + 13,75%) ÷ (1 + 4,58%) − 1 = 8,77%")
    raiz = valido(svg)
    assert conta(svg, "rect") == 3
    assert svg.count("fill='var(--g-hi)'") == 1  # só o juro real em laranja
    ts = textos(svg)
    assert ["Selic", "13,75%", "IPCA de 12 meses", "4,58%", "Juro real ex-post", "8,77%"] == ts[:6]
    assert ts[-1] == "(1 + 13,75%) ÷ (1 + 4,58%) − 1 = 8,77%"
    larguras = [float(r.get("width")) for r in raiz.iter(f"{NS}rect")]
    assert larguras[0] > larguras[2] > larguras[1]  # proporcionais aos valores
    assert desc == (
        "Gráfico: barras com a Selic de 13,75%, o IPCA de 12 meses de 4,58% e o juro real ex-post de 8,77%, "
        "pela conta (1 + 13,75%) ÷ (1 + 4,58%) − 1 = 8,77%."
    )
    negativo, desc = G.barras_juros(10.0, 11.0, -0.9, "conta")
    assert "−0,9%" in desc or "−0,90%" in desc


# ── barras_variacao ──────────────────────────────────────────────────────


def btc():
    inicio = date(2026, 9, 29)
    return [(inicio + timedelta(days=i), c) for i, c in enumerate(BTC)]


def test_barras_variacao_bitcoin():
    svg, desc = G.barras_variacao(btc(), 1.5375)
    valido(svg)
    assert conta(svg, "rect") == 23
    assert svg.count("fill='var(--g-hi)'") == 1
    assert svg.count("stroke-dasharray='8 8'") == 4  # ±2 e ±3 vezes a escala
    ts = textos(svg)
    assert {"hoje: +2,44%", "1,6 vez a variação típica", "29/09", "21/10", "+3×", "−2×"} <= set(ts)
    assert desc == (
        "Gráfico: barras com 23 variações diárias, de 29/09 a 21/10; hoje, +2,44%, 1,6 vez a variação típica "
        "de 1,54%; linhas tracejadas em 2 e 3 vezes essa variação."
    )


def test_barras_variacao_sem_escala_e_limite_de_30():
    longa = [(date(2026, 9, 1) + timedelta(days=i), (-1) ** i * 0.5) for i in range(40)]
    longa[-1] = (longa[-1][0], -2.36)
    svg, desc = G.barras_variacao(longa, 0.9)
    assert conta(svg, "rect") == 30 and "hoje, −2,36%, 2,6 vezes" in desc
    sem, desc = G.barras_variacao(btc()[:5])
    assert "stroke-dasharray" not in sem and "típica" not in desc
    assert conta(G.barras_variacao([])[0], "rect") == 0
    um, desc = G.barras_variacao([(date(2026, 10, 21), 0.0)])
    assert conta(um, "rect") == 1 and "em 21/10; hoje, 0,00%" in desc


def test_barras_variacao_dia_fora_do_comum_junta_os_rotulos_dos_limiares():
    # 27/10: Ibovespa −13,88% com variação típica de 1,06% (z = 13,2): os tracejados de 2× e 3× ficam a
    # ~12 px um do outro, e "+3×" caía em cima de "+2×" (arte#1). Um rótulo só por sinal.
    serie = [(date(2026, 9, 28) + timedelta(days=i), c) for i, c in enumerate([0.3, -0.5, 1.2, 8.25, -0.6, 0.2, -13.88])]
    svg, desc = G.barras_variacao(serie, 1.06, altura=520)
    valido(svg)
    sem_sobreposicao(svg)
    ts = textos(svg)
    assert "+2–3×" in ts and "−2–3×" in ts and "+3×" not in ts and "+2×" not in ts
    assert svg.count("stroke-dasharray='8 8'") == 4  # as 4 linhas continuam
    # Escala normal: os 4 rótulos separados, sem encostar.
    svg, _ = G.barras_variacao(btc(), 1.5375)
    sem_sobreposicao(svg)


def test_barras_variacao_marca_as_leituras_do_meio_do_pregao():
    # Ibovespa: 05/10 só tem a leitura das 13h51 (+8,25%); não se compara com um fechamento sem aviso.
    serie = [(date(2026, 10, 1) + timedelta(days=i), c) for i, c in enumerate([0.3, -0.5, 1.2, 8.25, -0.6, -13.88])]
    svg, desc = G.barras_variacao(serie, 1.06, parciais=[date(2026, 10, 4), date(2026, 10, 2)], rotulo_hoje="fech. 06/10")
    valido(svg)
    sem_sobreposicao(svg)
    assert svg.count("fill='none'") == 2  # vazadas
    assert "vazadas: 2 leituras do meio do pregão, sem o fechamento" in textos(svg)
    assert desc.endswith("; vazadas: 2 leituras do meio do pregão, sem o fechamento.")
    assert "fech. 06/10: −13,88%" in textos(svg)


# ── checklist ────────────────────────────────────────────────────────────


def test_checklist_com_uma_hora_de_outro_dia_poe_a_data_em_todas():
    # 29/10: "Estadão 28/10 19h15" e, embaixo, "CNN Brasil 04h47" parecia voltar no tempo (dados#12).
    horas = {"Estadão": datetime(2026, 10, 28, 22, 15, tzinfo=UTC), "CNN Brasil": datetime(2026, 10, 29, 7, 47, tzinfo=UTC)}
    svg, _ = G.checklist(["Estadão", "CNN Brasil"], [], horas, ref_date=date(2026, 10, 29))
    assert {"28/10 19h15", "29/10 04h47"} <= set(textos(svg)) and "04h47" not in textos(svg)
    so_hoje = {"CNN Brasil": datetime(2026, 10, 29, 7, 47, tzinfo=UTC)}
    assert "04h47" in textos(G.checklist(["CNN Brasil"], [], so_hoje, ref_date=date(2026, 10, 29))[0])


def test_checklist_numero_triangulado():
    nomes = ["Valor Econômico", "g1", "Folha de S.Paulo", "Agência Brasil", "Estadão", "Money Times"]
    horas = {n: datetime(2026, 10, 21, 20, i, tzinfo=UTC) for i, n in enumerate(nomes)}
    horas["Money Times"] = datetime(2026, 10, 22, 8, 59, tzinfo=UTC)
    ok = set(nomes) - {"g1"}
    svg, desc = G.checklist(nomes, ok, horas, ref_date=date(2026, 10, 22))
    valido(svg)
    assert conta(svg, "path") == 5  # ✓ desenhado, não glifo
    ts = textos(svg)
    assert "5 de 6 veículos com o número no título" in ts
    assert "21/10 17h00" in ts and "22/10 05h59" in ts and "05h59" not in ts  # data em todas (dados#12)
    assert desc.startswith("Gráfico: lista de 6 veículos; 5 de 6 com o número no título (Valor Econômico, 21/10 17h00;")
    assert desc.endswith("; sem o número: g1; títulos não reproduzidos.")


def test_checklist_muitos_e_bordas():
    nomes = [f"Veículo {i}" for i in range(15)]
    svg, _ = G.checklist(nomes, nomes[::2], [None] * 15, max_linhas=12)
    assert any(t.startswith("+4 veículos (") for t in textos(svg))
    assert conta(svg, "path") == 6  # os confirmados entre os 11 mostrados
    vazio, desc = G.checklist([], [])
    assert "lista vazia" in desc
    longo, _ = G.checklist(["The Wall Street Journal"], [], largura=300)
    assert "…" in textos(longo)[0]


def test_svg_atravessa_o_polish_dos_slides():
    from qijournal.instagram.conteudo import polish

    for svg, _ in (
        G.relogio(batidas(), stance(), date(2026, 10, 21), rotulos=ROT),
        G.linha(ibov(), [date(2026, 9, 30)], prefixo="R$ "),
        G.waffle(26, 4, 0),
    ):
        depois = polish({"grafico": str(svg)})["grafico"]
        assert "“" not in depois and "”" not in depois
        assert conta(depois, "text") == conta(svg, "text") and conta(depois, "circle") == conta(svg, "circle")
        ET.fromstring(depois)


# ── Macros e tokens do sistema 2 ─────────────────────────────────────────


@pytest.fixture
def env():
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    return Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True, trim_blocks=True,
                       lstrip_blocks=True, undefined=StrictUndefined)


def macro(env, nome: str, *args, ctx: dict | None = None, **kwargs) -> str:
    contexto = {"brand": {"name": "Triangula"}, "page": 3, "pages": 8, "kind": "carousel"} | (ctx or {})
    m = env.get_template("_macros.html.j2").make_module(contexto)
    return str(getattr(m, nome)(*args, **kwargs))


def test_assinatura_sem_handle_e_com_handle(env):
    sem = macro(env, "assinatura", "análise por IA de 11 veículos")
    assert ">triangula<" in sem and "@" not in sem
    assert "análise por IA de 11 veículos" in sem and "var(--tri)" in sem
    vazio = macro(env, "assinatura", ctx={"brand": {"handle": ""}})
    assert ">triangula<" in vazio and "note" not in vazio
    com = macro(env, "assinatura", "fonte: Yahoo Finance", ctx={"brand": {"handle": "triangula"}})
    assert ">@triangula<" in com
    assert ">@outro<" in macro(env, "assinatura", handle="@outro")


def test_serie_band_selo_e_contador(env):
    capa = macro(env, "serie_band", "Raio-X Nº 014 · TER 21 OUT", None, "raiox")
    assert "Raio-X Nº 014 · TER 21 OUT" in capa and "cnt" not in capa
    assert 'class="sb-band"' in capa and "style=" not in capa.split('class="sb-row"')[0]  # a cor vem do tema
    interno = macro(env, "serie_band", "Número do dia Nº 003", True, "numero")
    assert ">03/08<" in interno and 'class="sb-band sb-hi"' in interno
    assert ">05/08<" in macro(env, "serie_band", "Em 1 minuto Nº 031", "05/08", "em1min")


# temas e séries das faixas que dependem do contexto (mercado.py e o lado do Raio-X)
_FAIXAS_DINAMICAS = {"mercado_linha": [("t-paper", "mercado"), ("t-dark", "em1min")],
                     "rx_lado": [("t-side-a", "raiox"), ("t-side-b", "raiox")]}


def test_faixa_de_serie_aparece_em_todo_template_e_tema(env):
    """A faixa de 8 px tinha a cor da série, que é a do próprio fundo: papel sobre papel no Raio-X, no Placar e
    no Mercado (1:1), #1B2745 sobre #0F1730 no Em 1 minuto (1,2:1), laranja sobre laranja na capa do Número e
    uma risca creme só nos lados do Raio-X (arte#4). Agora ela passa de 3:1 em todo template, em todo tema."""
    css = (TEMPLATES / "_base.html.j2").read_text(encoding="utf-8")
    regra = dict(re.findall(r"\.(sb-band(?:\.sb-hi)?)\{[^}]*background:var\(--([\w-]+)\)", css))
    assert regra == {"sb-band": "ink", "sb-band.sb-hi": "g-hi"}
    vistos = 0
    for p in sorted(TEMPLATES.glob("[!_]*.html.j2")):
        nome = p.name.removesuffix(".html.j2")
        fonte = p.read_text(encoding="utf-8")
        chamada = re.search(r"m\.serie_band\(([^)]*)\)", fonte)
        assert chamada, nome
        if nome in _FAIXAS_DINAMICAS:
            casos = _FAIXAS_DINAMICAS[nome]
        else:
            tema = re.search(r"\{% block theme %\}(t-[\w-]+) v2\{% endblock %\}", fonte).group(1)
            casos = [(tema, re.search(r"'(\w+)'\s*$", chamada.group(1)).group(1))]
        for tema, serie in casos:
            html = macro(env, "serie_band", "SELO", None, serie)
            classe = re.search(r'class="(sb-band[^"]*)"', html).group(1).replace(" ", ".")
            t = _tema(css, tema)
            cor = t[regra[classe]]
            assert _contraste(cor, t["bg"]) >= 3.0, (nome, tema, serie, cor, t["bg"])
            vistos += 1
    assert vistos >= 30
    # o laranja da faixa fica só com o Número (e o manifesto, que usa a série dele)
    assert "sb-hi" not in macro(env, "serie_band", "S", None, "em1min") + macro(env, "serie_band", "S", None, "raiox")


def test_selo_cobertura_realce_e_numero(env):
    selo = macro(env, "selo_cobertura", "12 veículos · dividida 6 × 2", 6, 3, 2)
    assert selo.count("<i ") == 3 and "flex:6 1 0" in selo and "var(--g-b)" in selo
    so_texto = macro(env, "selo_cobertura", "8 veículos · sem divisão")
    assert "anb" not in so_texto and "8 veículos · sem divisão" in so_texto
    realce = macro(env, "realce", "Só 2, complexidade de governança.", "Complexidade de governança")
    assert realce == 'Só 2, <em class="hl">complexidade de governança</em>.'
    assert macro(env, "realce", "<b>", "x") == "&lt;b&gt;"
    numero = macro(env, "num_hero", "R$ 1 bi", "cerca de")
    assert 'class="qual"' in numero and "font-size:320px" in numero and 'data-min="200"' in numero
    gancho = macro(env, "hook", "Mesmo fato", "fato", 150, 96)
    assert 'data-fit="gancho"' in gancho and '<em class="hl">fato</em>' in gancho


def test_macros_do_sistema_2_e_o_legado_fora(env):
    m = env.get_template("_macros.html.j2").make_module({"brand": {"logo": ""}, "page": 1, "pages": 1, "kind": "carousel"})
    for nome in ("logo", "tri", "change", "out_arrow", "go_arrow", "rich", "tx", "sources", "serie_band", "assinatura"):
        assert hasattr(m, nome), nome
    for nome in ("header", "footer", "spectrum", "rx_band", "datebox", "names_line", "weather_icon", "section_chip"):
        assert not hasattr(m, nome), nome
    base = (TEMPLATES / "_base.html.j2").read_text(encoding="utf-8")
    assert "--sans" not in base and ".datebox" not in base and 'class="grid"' not in base


def test_todo_template_e_do_sistema_2():
    usados = {p.stem.removesuffix(".html") for p in TEMPLATES.glob("[!_]*.html.j2")}
    assert {"capa", "fato", "fechamento", "mercado", "raiox_capa", "story_bomdia"}.isdisjoint(usados)
    for p in TEMPLATES.glob("[!_]*.html.j2"):
        assert " v2{% endblock %}" in p.read_text(encoding="utf-8"), p.name


def _tema(css: str, classe: str) -> dict[str, str]:
    bloco = re.search(rf"\.{classe}\{{([^}}]*)\}}", css).group(1)
    return dict(re.findall(r"--([\w-]+):(#[0-9A-Fa-f]{6})", bloco))


def _contraste(a: str, b: str) -> float:
    def lum(h: str) -> float:
        r, g, b_ = (int(h[i : i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b_)

    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_temas_passam_de_4_5_em_todo_texto():
    css = (TEMPLATES / "_base.html.j2").read_text(encoding="utf-8")
    laranja = _tema(css, "t-orange")
    assert laranja["bg"] == "#FF5A1F" and laranja["ink"] == laranja["ink2"] == laranja["ink3"] == "#0F1730"
    for classe in ("t-dark", "t-paper", "t-orange"):
        t = _tema(css, classe)
        for token in ("ink", "ink2", "ink3", "accent"):
            assert _contraste(t[token], t["bg"]) >= 4.5, (classe, token)
        for token in ("g-a", "g-b", "g-cinza", "g-hi"):  # marcas de gráfico: 3:1
            assert _contraste(t[token], t["bg"]) >= 3.0, (classe, token)
        for lado in ("a", "b", "n"):  # contagem dentro do segmento
            assert _contraste(t[f"g-{lado}-ink"], t[f"g-{lado}"]) >= 4.5, (classe, lado)
    for classe in ("t-dark", "t-paper"):  # onde há barra A/N/B e relógio (arte#19)
        t = _tema(css, classe)
        # o sem lado é o cinza recessivo: não se confunde com o lado A (eram 1,07:1 e 1,14:1) e ainda aparece
        assert _contraste(t["g-a"], t["g-n"]) >= 3.0, classe
        assert _contraste(t["g-b"], t["g-n"]) >= 2.0 and _contraste(t["g-n"], t["bg"]) >= 2.0, classe
    assert "font-stretch:62%" in css and "--label:'Archivo'" in css
    assert re.search(r"\.hook\{[^}]*line-height:\.92[^}]*letter-spacing:-\.01em[^}]*text-transform:uppercase", css)
    assert re.search(r"\.num-hero\{[^}]*tabular-nums", css)


# ── render: transbordo para o lint ───────────────────────────────────────


def test_render_result_transbordos_tipados():
    r = RenderResult(Path("x.jpg"), [{"fit": "gancho", "size": 96, "min": 96, "altura_px": 40, "largura_px": 0,
                                      "texto": "11 VEÍCULOS"}, {"fit": "antigo", "size": 20}])
    assert not r.ok
    t = r.transbordos
    assert t[0] == Transbordo("gancho", 96.0, 96.0, 40, 0, "11 VEÍCULOS")
    assert "40 px na altura" in str(t[0])
    assert t[1].fit == "antigo" and t[1].min == 0
    assert RenderResult(Path("y.jpg")).ok


def _renderer():
    pytest.importorskip("playwright.sync_api")
    from qijournal.instagram.render import Renderer

    r = Renderer()
    try:
        r.__enter__()
    except Exception as exc:  # sem Chromium nesta máquina
        r._tmp.cleanup()
        pytest.skip(f"Chromium indisponível: {exc}")
    return r


def test_render_informa_quem_transbordou_no_minimo(tmp_path):
    from jinja2 import Environment, FileSystemLoader

    from qijournal.instagram.render import fonts_css

    (tmp_path / "t.html.j2").write_text(
        '{% extends "_base.html.j2" %}{% import "_macros.html.j2" as m with context %}'
        "{% block theme %}t-paper v2{% endblock %}"
        "{% block body %}{{ m.serie_band('Raio-X Nº 014') }}<div class='frame'>"
        "{{ m.hook(s.gancho, none, 150, 140, 300, 'gancho') }}"
        "{{ m.hook('Cabe', none, 150, 96, 400, 'cabe') }}</div>{{ m.assinatura('nota') }}{% endblock %}",
        encoding="utf-8",
    )
    env = Environment(loader=FileSystemLoader([str(tmp_path), str(TEMPLATES)]), autoescape=True)
    brand = {"fonts_css": Markup(fonts_css(ROOT)), "handle": None}
    html = env.get_template("t.html.j2").render(brand=brand, kind="carousel", s={"gancho": "palavra " * 40})
    r = _renderer()
    try:
        res = r.render(html, tmp_path / "x.jpg", width=1080, height=1350)
    finally:
        r.__exit__(None, None, None)
    assert res.path.exists()
    nomes = [t.fit for t in res.transbordos]
    assert "gancho" in nomes and "cabe" not in nomes
    t = next(t for t in res.transbordos if t.fit == "gancho")
    assert t.size == 140 and t.min == 140 and t.altura_px > 0 and t.texto.startswith("palavra")


def test_hora_no_gancho_fica_com_h_minusculo(env):
    """"LEITURA DAS 04H56" em caixa alta se lê como outra coisa (arte#14): o "h" entre algarismos vai num span
    sem text-transform; o resto do gancho continua em caixa alta e escapado."""
    html = macro(env, "hook", "Brent +1,95% na leitura das 06h45", "+1,95%", 96, 60)
    assert 'das 06<span class="hm">h</span>45' in html and '<em class="hl">+1,95%</em>' in html
    assert macro(env, "hook", "<b>ahh</b> 12h") == (
        '<h1 class="hook" data-fit="gancho" data-min="96" style="font-size:128px">&lt;b&gt;ahh&lt;/b&gt; 12h</h1>'
    )
    assert ".hm{text-transform:none}" in (TEMPLATES / "_base.html.j2").read_text(encoding="utf-8")


@pytest.mark.parametrize("nome", ["rx_placar", "placar_metodo"])
def test_cta_da_noite_no_gancho_condensado_sem_seta(nome):
    """O CTA de envio/salvamento fecha como o Número e o Em 1 minuto: gancho condensado, sem a seta → (arte#9)."""
    fonte = (TEMPLATES / f"{nome}.html.j2").read_text(encoding="utf-8")
    assert '<div class="cta"><h2 class="hook" data-fit="cta"' in fonte
    assert "<b>→</b>" not in fonte


def test_espectro_coluna_estreita_nao_corta_nome():
    """18/10: 3 colunas, "The New York Times" na de 2 veículos saía "The New York T…"."""
    svg, _ = G.espectro(["Folha de S.Paulo", "Estadão", "CNBC", "The Wall Street Journal"], ["g1", "BBC News"],
                        ["Valor Econômico", "The New York Times"], rotulos=("alívio a Moscou", "pausa por custos"))
    ts = textos(svg)
    assert "The New York Times" in ts and "The Wall Street Journal" in ts and not any("…" in t for t in ts)
    dentro_do_quadro(svg)
