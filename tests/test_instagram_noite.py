"""A publicação da noite: Raio-X 2.0 (§4.2) e Placar do dia (§4.3), com as edições reais de data/."""

from __future__ import annotations

import dataclasses
import functools
import json
import re
from datetime import date
from pathlib import Path

import pytest

from qijournal.config import LimiaresConfig, load_config
from qijournal.instagram import estatistica as E
from qijournal.instagram import gerar, redacao
from qijournal.instagram import selecao as S
from qijournal.instagram.formatos import noite as N
from qijournal.models import Edition

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
real = pytest.mark.skipif(not (DATA / "2026-10-26.json").is_file(), reason="sem os JSONs reais em data/")
pytestmark = real

DIAS_IA = range(14, 26)  # 14 a 25/10: edições com IA, todas no período eleitoral
ESCOLHAS = {  # §4.9, item 3 (prefixo do id da matéria)
    14: "restricoes-na-starlink",
    15: "papa-leao-xiv",
    16: "ucrania-retoma-ataques",
    17: "deficit-fiscal-dos-estados",
    18: "trump-suspende-sancoes",
    19: None,  # Placar do dia
    20: "governo-declara-emergencia",
    21: "spacex-e-tesla",
    22: "stf-decide-que-iss",
    23: "bradesco",
    24: "ipca-15",
    25: "musk-acusa-ucrania",
}


@functools.lru_cache(maxsize=None)
def edicao(day: int, month: int = 10) -> Edition:
    return Edition.from_dict(json.loads((DATA / f"2026-{month:02d}-{day:02d}.json").read_text(encoding="utf-8")))


@functools.lru_cache(maxsize=None)
def noites() -> dict[int, object]:
    """A peça da noite de 14 a 25/10 como o gerador faria: Raio-X (com as entidades dos 3 dias
    anteriores) ou Placar do dia; a série começa em 001 em 14/10."""
    out, recentes = {}, []
    for n, day in enumerate(DIAS_IA, 1):
        e = edicao(day)
        peca = N.raio_x(e, {}, election=True, numero_serie=n, entidades_recentes=recentes[-3:])
        peca = peca or N.placar_do_dia(e, {}, election=True, numero_serie=n)
        recentes.append(list(peca.entidades) if peca else [])
        out[day] = peca
    return out


def raiox(day: int, numero: int = 14, **kw):
    return N.raio_x(edicao(day), {}, election=True, numero_serie=numero, **kw)


def slide(peca, template: str, i: int = 0):
    return [s for s in peca.slides if s.template == template][i]


def sp(texto: str) -> str:
    """O texto da arte com o espaço inseparável (U+00A0) como espaço comum."""
    return texto.replace("\u00a0", " ")


def setas(caption: str) -> list[str]:
    return [linha for linha in caption.split("\n") if linha.startswith("▸")]


def veiculos(e: Edition) -> list[str]:
    return sorted({o.name for s in e.stories if s.coverage for o in s.coverage.outlets})


# ── Raio-X 2.0 ────────────────────────────────────────────────────────────


def test_escolha_da_noite_de_14_a_25_10():
    for day, prefixo in ESCOLHAS.items():
        peca = noites()[day]
        if prefixo is None:
            assert peca.title == "Placar do dia" and peca.formula == "consenso", day
            continue
        assert peca.title == "Raio-X da cobertura", day
        assert peca.story_ids[0].startswith(prefixo), (day, peca.story_ids)
        assert all("tse-intima" not in sid for sid in peca.story_ids)


def test_raiox_de_21_10_capa_compacta_conta_so_os_analisados():
    """12 veículos publicaram e 11 foram analisados (o WSJ ficou fora): o G2 "11 VEÍCULOS, MESMO FATO" diria
    menos do que o relógio do mesmo carrossel (dados#8); com "analisados" passa de 90 e vira o compacto, que
    guarda o "só 2"."""
    p = raiox(21)
    assert (p.slot, p.kind, p.serie, p.numero_serie, p.formula) == ("noite", "carousel", "raiox", 14, "compacto")
    assert p.story_ids == ["spacex-e-tesla-avaliam-fusao-historica-apos-salt"]
    assert p.entidades == S.entidades(edicao(21).story(p.story_ids[0]))
    assert [s.template for s in p.slides] == [
        "rx_capa", "rx_fato", "rx_lado", "rx_lado", "rx_relogio", "rx_significado", "rx_importa", "rx_placar",
    ]
    capa = p.slides[0].ctx
    assert capa["selo"] == "RAIO-X Nº 014 · QUA 21 OUT · TECNOLOGIA"
    assert sp(capa["gancho"]) == "6 DE 11 DESTACARAM SINERGIA EM IA. SÓ 2, COMPLEXIDADE DE GOVERNANÇA."
    # a quebra de linha nunca separa o número da palavra nem o "só" do número (arte#13)
    assert "6\u00a0DE\u00a011\u00a0DESTACARAM" in capa["gancho"] and "SÓ\u00a02," in capa["gancho"]
    assert capa["destaque"] == "COMPLEXIDADE DE GOVERNANÇA"
    assert 96 <= capa["gancho_px"] <= 150
    # o assunto tem "histórica" (1 de 12 títulos): a capa usa a manchete só sem o adjetivo, que diz qual é o
    # fato (a linha fina não cita SpaceX, Tesla nem Musk: dados#7)
    assert capa["subtitulo"] == "SpaceX e Tesla avaliam fusão após salto nos resultados operacionais"
    assert p.slides[0].alt == (
        "Raio-X da cobertura nº 14, 21 de outubro. 6 de 11 destacaram sinergia em IA; "
        "só 2, complexidade de governança. SpaceX e Tesla avaliam fusão após salto nos resultados operacionais. "
        "Gráfico: barra com 6 veículos na maioria, 3 sem lado e 2 na minoria."
    )
    assert "<svg" in capa["grafico"] and "graf-anb" in capa["grafico"]
    # nenhum nome de veículo no texto da capa
    for nome in veiculos(edicao(21)):
        assert nome not in capa["gancho"] + capa["subtitulo"]


def test_raiox_de_21_10_fato_nao_repete_a_capa():
    p = raiox(21)
    capa, fato = p.slides[0].ctx, p.slides[1].ctx
    assert fato["kicker"] == "O fato"
    # a linha fina não tem número nem nome e não cabe junto: o slide 2 fica com a 1ª frase do corpo
    assert fato["texto"].startswith("Os conselhos de administração da SpaceX e da Tesla iniciaram conversas formais")
    assert capa["subtitulo"] not in fato["texto"]  # nunca repete a capa (T3)
    assert len(fato["texto"]) <= N.FATO_MAX and N._tem_dado(fato["texto"])
    assert fato["cobertura"] == "11 veículos · 6 × 2 · 3 sem lado"
    assert (fato["a"], fato["n"], fato["b"]) == (6, 3, 2)
    assert fato["fontes"].startswith("Valor Econômico, Folha de S.Paulo")
    assert 46 <= fato["texto_px"] <= 76


def test_raiox_de_21_10_maioria_e_minoria_em_ordem_de_horario():
    p = raiox(21)
    maioria, minoria = (s.ctx for s in p.slides[2:4])
    assert (maioria["lado"], maioria["kicker"], maioria["titulo"]) == ("a", "A maioria", "6 DE 11 · SINERGIA EM IA")
    assert maioria["destaque"] == "6 DE 11"
    assert [v["name"] for v in maioria["veiculos"]] == ["The New York Times", "Valor Econômico", "g1"]
    assert maioria["veiculos"][0]["hora"] == "20/10 13h17"
    assert maioria["tambem"] == "Al Jazeera, CNBC, InfoMoney"
    assert all(len(v["framing"]) <= N.FRAMING_MAX and not v["framing"].endswith("…") for v in maioria["veiculos"])
    assert (minoria["lado"], minoria["titulo"]) == ("b", "2 DE 11: COMPLEXIDADE DE GOVERNANÇA")  # quebra: dois pontos
    assert [v["name"] for v in minoria["veiculos"]] == ["Financial Times", "BBC News"]
    # Uma hora com data na lista: todas com data ("BBC News 03h36" ao lado de "20/10 22h49", dados#12).
    assert [v["hora"] for v in minoria["veiculos"]] == ["20/10 22h49", "21/10 03h36"]
    assert minoria["neutros"] == "Sem tomar lado (3): TechCrunch, Folha de S.Paulo, MarketWatch"
    assert "Lado A" not in json.dumps([maioria, minoria], ensure_ascii=False)


def test_raiox_de_21_10_relogio_e_virada():
    s5 = raiox(21).slides[4]
    # a duração por extenso: "16H43" em caixa alta se lia como hora do dia (arte#14); o exato fica no gráfico
    assert sp(s5.ctx["titulo"]) == "12 VEÍCULOS EM MAIS DE 16 HORAS" and sp(s5.ctx["destaque"]) == "16 HORAS"
    assert ">16h43</tspan>" in s5.ctx["grafico"]
    assert s5.ctx["selo"] == "RAIO-X Nº 014 · RELÓGIO DA NOTÍCIA"
    assert s5.ctx["virada"] == (
        "Primeiro texto em inglês: The New York Times, 20/10 13h17. Primeiro em português: Valor Econômico, 20/10 18h17."
    )
    assert s5.ctx["contexto"] == []  # 12 linhas: não sobra espaço
    assert s5.ctx["rodape"] == "Horário informado por cada veículo, em Brasília. Não indica quem apurou primeiro."
    # a frase do §4.1, gerada pela mesma função que desenha o gráfico
    assert (
        "Gráfico: linha do tempo com 12 pontos, de 20/10 13h17 a 21/10 06h01; 6 do lado sinergia em IA, "
        "2 do lado complexidade de governança, 3 neutros, 1 fora da análise."
    ) in s5.alt


def test_raiox_de_21_10_significado_importa_e_placar():
    p = raiox(21)
    s6, s7, s8 = p.slides[5:8]
    assert "fora da análise: The Wall Street Journal" in s6.alt
    # "veículos internacionais mantiveram atenção às exigências de governança": NYT, Al Jazeera e CNBC estão
    # na maioria; a frase da IA é desmentida pelo espectro e dá lugar ao molde com as contagens (dados#3)
    assert s6.ctx["conclusao"] == (
        "6 de 11 veículos destacaram sinergia em IA; 2, complexidade de governança; 3 não tomaram lado. "
        "Entre os veículos em inglês, 3 destacaram sinergia em IA e 2, complexidade de governança; em português, "
        "3 e nenhum."
    )
    # "unindo gigantes de Musk": adjetivo da casa fora, cortado na vírgula
    assert s7.ctx["texto"] == "A transação remodelaria as indústrias automotiva, espacial e de inteligência artificial."
    # A spec simulou 4 de 26 sem o filtro por conteúdo; o referendo de Alberta (eleição de outro país) também sai.
    assert sp(s8.ctx["titulo"]) == "3 DE 24 ASSUNTOS DIVIDIRAM A COBERTURA HOJE"
    assert sp(s8.ctx["destaque"]) == "3 DE 24" and "%" not in s8.ctx["titulo"]
    assert s8.ctx["cta"] == "Leu só uma versão? Mande para quem leu a outra."
    assert s8.ctx["serie"] == "Série completa no destaque Raio-X."
    assert "3 dividiram e 21 sem divisão; o contorno marca este Raio-X." in s8.alt


def test_raiox_de_21_10_legenda_no_molde():
    p = raiox(21)
    linhas = p.caption.split("\n")
    assert linhas[0] == "6 de 11 destacaram sinergia em IA; só 2, complexidade de governança."
    assert linhas[1] == ""
    # o fato com sujeito e o quê (dados#7)
    assert linhas[2] == "▸ O fato: SpaceX e Tesla avaliam fusão após salto nos resultados operacionais"
    assert "▸ Sinergia em IA (6): The New York Times, Valor Econômico, g1, Al Jazeera, CNBC, InfoMoney" in linhas
    # os sem lado vão na linha da minoria: 5 linhas "▸", nunca 6 (dados#16)
    assert ("▸ Complexidade de governança (2): Financial Times, BBC News. Sem tomar lado (3): TechCrunch, "
            "Folha de S.Paulo, MarketWatch") in linhas
    assert len(setas(p.caption)) == 5
    assert (
        "▸ Relógio: 12 veículos (11 analisados) em 16h43, de The New York Times (20/10 13h17) a The Wall Street "
        "Journal (21/10 06h01), horário informado por cada veículo."
    ) in linhas
    assert "▸ Placar do dia: 3 de 24 assuntos com 4 ou mais veículos dividiram a cobertura." in linhas
    assert linhas[-4].startswith("Por que importa: A transação remodelaria")
    assert linhas[-3] == N.METODO
    assert linhas[-2] == N.CTA_RAIOX
    assert linhas[-1] == "#triangula #jornalismo #tecnologia"


@pytest.mark.parametrize(
    "day, formula, gancho, destaque",
    [
        (20, "G3", "MESMO FATO, 4 VEÍCULOS: METADE DESTACOU IMPACTO COMERCIAL; METADE, CONTENÇÃO SANITÁRIA.",
         "CONTENÇÃO SANITÁRIA"),
        (24, "G3", "MESMO FATO, 6 VEÍCULOS: METADE DESTACOU ALÍVIO; METADE, PERSISTÊNCIA DE ALIMENTOS.",
         "PERSISTÊNCIA DE ALIMENTOS"),
        (23, "compacto", "4 DE 6 DESTACARAM PERDA DE FÔLEGO; 2, CONTINUIDADE DA VIRADA OPERACIONAL.",
         "CONTINUIDADE DA VIRADA OPERACIONAL"),
    ],
)
def test_ganchos_reais_da_spec(day, formula, gancho, destaque):
    p = noites()[day]
    assert p.formula == formula
    assert sp(p.slides[0].ctx["gancho"]) == gancho and p.slides[0].ctx["destaque"] == destaque
    assert len(gancho) <= S.GANCHO_MAX
    assert not re.search(r"\d ", p.slides[0].ctx["gancho"])  # número sempre preso à palavra seguinte (arte#13)


def test_empate_sem_maioria_nem_minoria():
    p = noites()[20]
    assert p.slides[0].alt.endswith("Gráfico: barra com 2 veículos do lado impacto comercial e 2 do lado contenção sanitária.")
    assert [s.ctx["kicker"] for s in p.slides[2:4]] == ["Metade", "A outra metade"]
    assert p.slides[0].ctx["subtitulo"] == "Governo declara emergência zoossanitária em SC após foco da doença de Newcastle"


def test_fato_pula_linha_fina_com_vicio_de_texto():
    p = noites()[23]  # a linha fina tem "em meio à desaceleração"
    texto = p.slides[1].ctx["texto"]
    assert "em meio" not in texto and texto.startswith("O Bradesco registrou lucro líquido recorrente de R$ 7,11 bilhões")


def test_conclusao_sem_lado_a_e_lado_b_entre_parenteses():
    cov = edicao(18).story(noites()[18].story_ids[0]).coverage
    concl = N._conclusao(cov)
    assert "lado" not in concl.lower() and concl.endswith("pressão eleitoral interna.")


def test_adjetivo_da_casa_sai_do_texto_corrido():
    assert N._sem_adj("A maior parte deu destaque ao risco fiscal bilionário da derrota da União, enquanto X.", []) == (
        "A maior parte deu destaque ao risco fiscal da derrota da União, enquanto X."
    )
    assert N._sem_adj("Sem adjetivo nenhum.", []) == "Sem adjetivo nenhum."
    assert N._sem_adj("Fusão histórica na bolsa", ["fusão histórica", "a fusão histórica"]) == "Fusão histórica na bolsa"


def test_titulo_do_lado_usa_dois_pontos_quando_quebra():
    assert N._titulo_lado("6 de 11", "sinergia em IA") == "6 de 11 · sinergia em IA"
    assert N._titulo_lado("4 de 6", "restabelecimento") == "4 de 6: restabelecimento"


def test_sem_por_que_importa_o_carrossel_fica_com_7():
    e = edicao(21)
    stories = [dataclasses.replace(s, why_it_matters=None) if s.id.startswith("spacex") else s for s in e.stories]
    p = N.raio_x(dataclasses.replace(e, stories=stories), {}, election=True, numero_serie=14)
    assert len(p.slides) == 7 and "rx_importa" not in [s.template for s in p.slides]
    assert not any(linha.startswith("Por que importa") for linha in p.caption.split("\n"))


def test_relogio_invalido_vira_a_atencao():
    p = raiox(21, limiares=LimiaresConfig(relogio_min_span_min=100_000))
    s5 = p.slides[4]
    assert s5.template == "rx_atencao"
    assert s5.ctx["titulo"] == "12 VEÍCULOS, 19 TEXTOS" and s5.ctx["destaque"] == "12 VEÍCULOS"
    assert s5.ctx["linhas"] == ["1º entre 1.159 assuntos do dia; 1.039 saíram em uma fonte só", "14 dos 19 textos em inglês"]
    assert "Gráfico: matriz com 1.159 pontos" in s5.alt
    assert "▸ A atenção: 12 veículos, 19 textos; 1º entre 1.159 assuntos do dia" in p.caption


def test_relogio_com_poucos_veiculos_ganha_as_linhas_da_atencao():
    s5 = noites()[20].slides[4]
    assert sp(s5.ctx["titulo"]) == "4 VEÍCULOS EM 17 HORAS"
    assert s5.ctx["virada"] == "Os 2 primeiros a publicar destacaram contenção sanitária."
    assert s5.ctx["contexto"][0] == "7 textos de 4 veículos: o 29º assunto mais coberto entre os 1.071 do dia."


def test_sem_rotulo_de_lado():
    e = edicao(21)
    stories = []
    for s in e.stories:
        if s.id.startswith("spacex"):
            s = dataclasses.replace(s, coverage=dataclasses.replace(s.coverage, side_b="Afasta a governança"))
        stories.append(s)
    p = N.raio_x(dataclasses.replace(e, stories=stories), {}, election=True, numero_serie=14)
    assert p.formula == "sem_rotulo"
    assert sp(p.slides[0].ctx["gancho"]) == "11 VEÍCULOS ANALISADOS, MESMO FATO: 6 CONTARAM DE UM JEITO, 2 DE OUTRO."
    assert p.slides[0].ctx["destaque"] == ""
    assert p.slides[3].ctx["titulo"] == "2 DE 11 · A MINORIA"
    assert "▸ Minoria (2): Financial Times, BBC News" in p.caption


def test_sem_numero_de_serie():
    p = raiox(21, numero=0)
    assert p.slides[0].ctx["selo"] == "RAIO-X · QUA 21 OUT · TECNOLOGIA"
    assert p.slides[0].alt.startswith("Raio-X da cobertura, 21 de outubro.")


def test_sem_ia_nao_ha_noite_por_aqui():
    e = edicao(10)
    assert e.mode != "ai"
    assert N.raio_x(e, {}, election=True, numero_serie=1) is None
    assert N.placar_do_dia(e, {}, election=True, numero_serie=1) is None


# ── Placar do dia ─────────────────────────────────────────────────────────


def test_placar_de_19_10_dia_de_consenso():
    p = noites()[19]
    assert (p.slot, p.serie, p.numero_serie, p.formula, p.title) == ("noite", "raiox", 6, "consenso", "Placar do dia")
    assert [s.template for s in p.slides] == ["placar_capa", "placar_lista", "rx_relogio", "placar_metodo"]
    capa = p.slides[0].ctx
    assert capa["selo"] == "RAIO-X Nº 006 · DIA DE CONSENSO"
    assert sp(capa["gancho"]) == "0 DE 13 ASSUNTOS DIVIDIRAM A COBERTURA HOJE"  # a spec simulou 0 de 16
    assert capa["subtitulo"] == "assuntos com 4 ou mais veículos comparados; dividir = pelo menos 2 veículos de cada lado."
    assert "grade de 13 quadrados" in p.slides[0].alt and "0 dividiram e 13 sem divisão" in p.slides[0].alt
    consenso = p.slides[1].ctx
    assert consenso["titulo"] == "8 VEÍCULOS, NENHUM LADO"
    assert consenso["assunto"].startswith("Protestos violentos na França")
    assert consenso["texto"].startswith("A cobertura convergiu")
    coberto = p.slides[2].ctx
    assert coberto["titulo"] == "13 VEÍCULOS, 29 TEXTOS"
    assert coberto["assunto"] == "O mesmo assunto foi o mais coberto do dia, entre 680."
    assert p.slides[3].ctx["cta"] == "Salve: o placar sai todo dia."
    linhas = p.caption.split("\n")
    assert linhas[0] == "Dia de consenso: 0 de 13 assuntos com 4 ou mais veículos dividiram a cobertura hoje."
    assert any(linha.startswith("▸ O mais coberto: o mesmo assunto, 13 veículos e 29 textos") for linha in linhas)
    assert "▸ Por editoria: Mundo 0 de 9 · Esporte 0 de 3 · Tecnologia 0 de 1." in linhas  # sem Trump × Netanyahu
    assert linhas[-2:] == ["Salve: o placar sai todo dia.", "#triangula #jornalismo #geopolitica"]


def test_placar_com_rachas_fora_do_periodo_eleitoral():
    p = N.placar_do_dia(edicao(19), {}, election=False, numero_serie=15)
    assert p.formula == "placar"
    assert p.slides[0].ctx["selo"] == "RAIO-X Nº 015 · PLACAR DO DIA"
    assert sp(p.slides[0].ctx["gancho"]) == "1 DE 21 ASSUNTOS DIVIDIU A COBERTURA HOJE"  # pelo numerador (arte#21)
    lista = p.slides[1].ctx
    assert lista["kicker"] == "1 de 21 assuntos" and len(lista["itens"]) == 1
    assert lista["itens"][0]["linha"] == "2 × 2 · cobrança da campanha × esclarecimentos operacionais"
    assert p.caption.startswith("Placar do dia: 1 de 21 assuntos")
    # o destaque e o gancho nunca levam nome de político, nem fora do período eleitoral
    for s in p.slides:
        for chave in ("destaque", "gancho", "titulo"):
            assert not E.cita_nome(s.ctx.get(chave) or "", ignora_caixa=True)


def test_placar_sai_so_com_base_10_ou_mais():
    e = edicao(26)
    assert E.placar(e, election=True).base == 2
    assert N.placar_do_dia(e, {}, election=True, numero_serie=19) is None
    assert N.placar_do_dia(e, {}, election=True, numero_serie=19, limiares=LimiaresConfig(placar_min_base=2)) is not None


def test_26_10_no_periodo_eleitoral_nada_eleitoral_entra():
    e = edicao(26)  # a edição do 2º turno: quase tudo é política
    assert N.raio_x(e, {}, election=True, numero_serie=19) is None
    assert N.placar_do_dia(e, {}, election=True, numero_serie=19) is None  # vai para o Mercado em linha
    for cov, story in E.placar(e, election=True).itens:
        assert not E.is_political_coverage(cov, story)
    p = N.placar_do_dia(e, {}, election=True, numero_serie=19, limiares=LimiaresConfig(placar_min_base=2))
    assert redacao.lint(p, election=True) == []


# ── Regras comuns (§4.1) em todas as peças de 14 a 25/10 ──────────────────


def test_lint_e_limites_em_todas_as_noites():
    for day, p in noites().items():
        e = edicao(day)
        assert redacao.lint(p, election=True, veiculos=veiculos(e)) == [], day
        primeira = p.caption.split("\n", 1)[0]
        assert len(primeira) <= redacao.LINHA1_MAX and len(p.caption) <= redacao.LEGENDA_MAX, day
        assert all(len(s.alt) <= redacao.ALT_MAX and s.alt for s in p.slides), day
        assert len(p.slides) in ((7, 8) if p.title.startswith("Raio-X") else (4,)), day
        assert not re.search(r"\d+ ?%", " ".join(s.ctx.get("titulo", "") for s in p.slides if s.template.endswith("placar")))


def test_destaque_gancho_e_rotulo_sem_politico_nem_partido_tambem_fora_da_eleicao():
    for day in DIAS_IA:
        p = N.raio_x(edicao(day), {}, election=False, numero_serie=1)
        if p is None:
            continue
        for s in p.slides:
            for chave in ("destaque", "gancho"):
                valor = s.ctx.get(chave) or ""
                assert not E.cita_nome(valor, ignora_caixa=True) and not E.cita_partido(valor), (day, valor)
            assert not E.ADJ_CASA.search(s.ctx.get("destaque") or "")


def test_templates_renderizam_com_e_sem_handle():
    cfg = load_config(ROOT)
    env = gerar.environment()
    for handle, assinatura in (("", ">triangula<"), ("triangula", ">@triangula<")):
        brand = {**gerar.brand_context(cfg), "handle": handle}
        for p in (noites()[21], noites()[19], raiox(21, limiares=LimiaresConfig(relogio_min_span_min=100_000))):
            for i, s in enumerate(p.slides, 1):
                html = env.get_template(f"{s.template}.html.j2").render(
                    brand=brand, s=gerar.conteudo.polish(s.ctx), page=i, pages=len(p.slides), kind=p.kind, slot=p.slot
                )
                assert assinatura in html, (s.template, handle)
                corpo = html.split("</style>", 1)[1]
                assert 'class="t-' in corpo and " v2" in corpo.split(">", 2)[1]
                assert "Lado A" not in corpo and "Noto Sans" not in corpo
                if s.ctx.get("grafico"):
                    assert "<svg class='graf" in html


def test_election_mode_do_gerador_cobre_os_dias_da_previa():
    cfg = load_config(ROOT)
    assert all(gerar.election_mode(cfg, date(2026, 10, d)) for d in (19, 20, 21, 23, 24, 26))
    assert not gerar.election_mode(cfg, date(2026, 10, 28))


# ── Revisão da Onda 1: noite (Raio-X e Placar), com os JSONs reais ────────


@functools.lru_cache(maxsize=None)
def raiox_pos(day: int):
    """O Raio-X de um dia depois da eleição (28 e 29/10), fora do período eleitoral."""
    return N.raio_x(edicao(day), {}, election=False, numero_serie=15)


def test_29_10_empate_com_sem_lado_nao_e_metade():
    """5 × 5 com 1 sem lado (CNBC): nenhum dos lados é metade dos 11 (arte#2, dados#4)."""
    p = raiox_pos(29)
    assert p.story_ids[0].startswith("colisao-entre-trens")
    assert [s.ctx["kicker"] for s in p.slides[2:4]] == ["Empate · um lado", "Empate · o outro lado"]
    assert all("etade" not in s.alt for s in p.slides[2:4])
    assert sp(p.slides[0].ctx["gancho"]) == "11 VEÍCULOS, MESMO FATO: 5 DESTACARAM APURAÇÃO DE FALHA HUMANA; 5, VÍTIMAS."


def test_29_10_historico_de_e_substantivo():
    """'luto e histórico de segurança na malha': substantivo, não adjetivo da casa (dados#5)."""
    assert raiox_pos(29).slides[3].ctx["descricao"] == "Foco nas vítimas, luto e histórico de segurança na malha"
    # tirar a palavra deixando "e de" (ou preposição no fim) apaga a descrição em vez de publicar texto quebrado
    assert N._sem_adj("Foco nas vítimas, luto e gigante de segurança na malha", []) == ""
    assert N._sem_adj("Destaca o custo gigante", []) == "Destaca o custo"
    assert N._sem_adj("Destaca o salto e o bilionário de vendas", []) == ""  # sobraria "e o de vendas"


def test_29_10_capa_e_legenda_dizem_qual_e_o_fato():
    """A manchete e o assunto são o título da Folha e a linha fina não diz 'trens' nem '68' (dados#7)."""
    p = raiox_pos(29)
    sub = p.slides[0].ctx["subtitulo"]
    assert "trens" in sub and "68" in sub and len(sub) <= N.SUBTITULO_MAX_CHARS
    assert N._norm(sub) not in N._titulos_literais(edicao(29))
    fato = p.slides[1].ctx["texto"]
    assert not N._norm(fato).startswith(N._norm(sub)) and N._norm(sub) not in N._norm(fato)  # o slide 2 não repete
    linha = next(x for x in setas(p.caption) if x.startswith("▸ O fato:"))
    assert "trens" in linha and "68" in linha


def test_29_10_conclusao_desmentida_pelo_espectro_vira_molde():
    """'veículos espanhóis e agências priorizaram a admissão de erro': nenhum veículo espanhol, e a única agência
    está no outro lado; 'a maior parte da imprensa internacional': 3 de 6 (dados#3)."""
    s6 = raiox_pos(29).slides[5]
    assert s6.ctx["conclusao"] == (
        "5 de 11 veículos destacaram apuração de falha humana; 5, vítimas; 1 não tomou lado. Entre os veículos em "
        "inglês, 2 destacaram apuração de falha humana e 3, vítimas; em português, 3 e 2."
    )
    assert s6.alt.startswith("O que isso significa: 5 de 11 veículos destacaram")


def test_conclusao_so_com_frases_que_a_contagem_sustenta():
    ld = S.Lados("a", "b", 6, 2, 3, 11, "sinergia em IA", "complexidade de governança")
    empate = S.Lados("a", "b", 5, 5, 1, 11, "x", "y")
    ok = N._frase_sustentada
    assert not ok("A cobertura concentrou-se majoritariamente na SpaceX, enquanto veículos internacionais miraram a "
                  "governança.", ld)
    assert not ok("A mídia brasileira e agências focaram o aparato operacional.", ld)
    assert not ok("Jornais generalistas e internacionais exploraram as dúvidas.", ld)
    assert ok("A cobertura concentrou-se majoritariamente nos resultados da SpaceX.", ld)  # 6 de 11 é maioria
    assert not ok("A maior parte dos veículos focou o drama humano.", empate)  # 5 × 5 não tem maior parte
    assert ok("A maioria dos veículos (6 a 2) tratou o caso como prejuízo.", ld)
    assert not ok("A maioria dos veículos (7 a 2) tratou o caso como prejuízo.", ld)
    assert not ok("Quatro veículos enfatizaram a desaceleração.", ld)
    assert ok("Houve divisão entre veículos que enfatizaram a surpresa e aqueles que frisaram os núcleos.", ld)
    assert ok("A cobertura convergiu, com alguns veículos adicionando repercussões econômicas e políticas.", ld)


def test_28_10_capa_conta_os_analisados_e_o_relogio_todos():
    """15 veículos publicaram, 11 foram analisados: a capa não diz '11 veículos, mesmo fato' (dados#8)."""
    p = raiox_pos(28)
    assert sp(p.slides[0].ctx["gancho"]).startswith("11 VEÍCULOS ANALISADOS, MESMO FATO: 6 DESTACARAM FUGA ILÍCITA")
    assert p.caption.startswith("11 veículos analisados, mesmo fato:")
    rel = p.slides[4]
    assert sp(rel.ctx["titulo"]) == "15 VEÍCULOS EM MAIS DE 18 HORAS"
    assert "4 fora da análise" in rel.alt
    assert any(x.startswith("▸ Relógio: 15 veículos (11 analisados) em 18h22") for x in setas(p.caption))
    # a legenda do relógio nomeia o vazado (antes, cada linha levava o rótulo e alguns sumiam: arte#6)
    assert ">fora da análise</text>" in rel.ctx["grafico"] and ">asilo político</text>" in rel.ctx["grafico"]


def test_21_10_enquadramento_sem_erro_de_digitacao():
    maioria = raiox(21).slides[2].ctx
    assert maioria["veiculos"][1]["framing"].startswith("Detalhou a reversão do prejuízo")  # o JSON diz "Detralhou"


def test_20_10_placar_concorda_pelo_numerador():
    """'1 de 24 assuntos dividiu', como a legenda do waffle (arte#21)."""
    p = noites()[20]
    s8 = p.slides[-1]
    assert sp(s8.ctx["titulo"]) == "1 DE 24 ASSUNTOS DIVIDIU A COBERTURA HOJE"
    assert "1 dividiu e 23 sem divisão" in s8.alt
    assert "▸ Placar do dia: 1 de 24 assuntos com 4 ou mais veículos dividiu a cobertura." in p.caption
    assert ">1</text>" in s8.ctx["grafico"] and "var(--accent)" not in s8.ctx["grafico"]  # um laranja só (arte#17)


@functools.lru_cache(maxsize=None)
def placar_27():
    return N.placar_do_dia(edicao(27), {}, election=True, numero_serie=13)


def test_27_10_placar_empate_sem_maioria_nem_rotulo_hiperbolico():
    """2 × 2 com 7 sem lado: nem 'maioria' nem 'riscos astronômicos' (arte#3, dados#6)."""
    item = placar_27().slides[1].ctx["itens"][0]
    assert item["linha"] == "2 × 2" and item["contagem"] == "2 × 2 · 7 sem lado"
    assert "maioria" not in item["grafico"] and "astronômic" not in item["grafico"]
    assert ">de um lado</text>" in item["grafico"] and ">do outro</text>" in item["grafico"]
    assert placar_27().slides[1].alt.endswith("Gráfico: barra com 2 veículos de um lado, 7 sem lado e 2 do outro.")
    assert S.rotulo_lado("Destaca os riscos astronômicos e os custos elevados") is None


def test_27_10_placar_tem_4_slides_e_legenda_de_3_a_5_linhas():
    """A eleição de Israel sai do 'mais coberto' e entra o seguinte do ranking, a Anthropic (dados#19a); a Bola
    de Ouro conta em Esporte, não em Mercados (dados#19b); 'dividiu' (arte#21); 3 a 5 linhas (dados#16)."""
    p = placar_27()
    assert [s.template for s in p.slides] == ["placar_capa", "placar_lista", "rx_relogio", "placar_metodo"]
    assert sp(p.slides[0].ctx["gancho"]) == "1 DE 17 ASSUNTOS DIVIDIU A COBERTURA HOJE"
    coberto = p.slides[2].ctx
    assert coberto["assunto"] == "O mesmo assunto foi o mais coberto do dia, entre 1.116."
    assert "Israel" not in json.dumps([s.ctx for s in p.slides], ensure_ascii=False, default=str)
    assert coberto["virada"].startswith("Primeiro horário: Folha de S.Paulo, 26/10 10h58. Último: InfoMoney, 27/10 05h00.")
    linhas = setas(p.caption)
    assert 3 <= len(linhas) <= 5
    assert "▸ Por editoria: Mundo 0 de 7 · Esporte 0 de 5 · Tecnologia 1 de 4 · Brasil 0 de 1." in linhas
    assert p.caption.startswith("Placar do dia: 1 de 17 assuntos com 4 ou mais veículos dividiu a cobertura hoje.")
    assert redacao.lint(p, election=True, veiculos=veiculos(edicao(27))) == []


def test_legendas_da_noite_com_3_a_5_linhas_de_14_a_29_10():
    for day in range(14, 30):
        e = edicao(day)
        eleicao = day <= 27
        p = N.raio_x(e, {}, election=eleicao, numero_serie=1) or N.placar_do_dia(e, {}, election=eleicao, numero_serie=1)
        if p is None:
            continue
        assert 3 <= len(setas(p.caption)) <= 5, (day, setas(p.caption))
        assert redacao.lint(p, election=eleicao, veiculos=veiculos(e)) == [], day


def test_duracao_do_titulo_do_relogio():
    from datetime import timedelta as td

    assert N._duracao_titulo(td(minutes=45)) == ("45 minutos", "45 minutos")
    assert N._duracao_titulo(td(minutes=95)) == ("95 minutos", "95 minutos")
    assert N._duracao_titulo(td(hours=17)) == ("17 horas", "17 horas")
    assert N._duracao_titulo(td(hours=18, minutes=22)) == ("mais de 18 horas", "18 horas")
    assert N._duracao_titulo(td(hours=22, minutes=51)) == ("quase 23 horas", "23 horas")


def test_relogio_nunca_esconde_veiculo_ate_12():
    """As linhas de contexto saem antes de um veículo virar "+k" (15/10: 8 veículos, antes 2 sumiam)."""
    for day in (*range(14, 26), 28, 29):
        p = N.raio_x(edicao(day), {}, election=day <= 27, numero_serie=1)
        if p is None or p.slides[4].template != "rx_relogio":
            continue
        k = int(sp(p.slides[4].ctx["titulo"]).split()[0])
        assert (">+" in p.slides[4].ctx["grafico"]) == (k > 12), day
    assert noites()[15].slides[4].ctx["contexto"] == []


def test_28_10_molde_com_a_janela_de_cada_lado():
    """Os 6 de 'fuga ilícita' publicaram de manhã; os 5 de 'asilo político', a partir das 15h37: o molde diz."""
    concl = raiox_pos(28).slides[5].ctx["conclusao"]
    assert concl.startswith("6 de 11 veículos destacaram fuga ilícita; 5, asilo político. Pelo horário informado, os 6 "
                            "de fuga ilícita publicaram entre 27/10 09h13 e 27/10 15h32; os 5 de asilo político, entre "
                            "27/10 15h37 e ")


def test_maioria_so_com_mais_da_metade_dos_analisados():
    """codigo#3: 6 × 4 com 4 sem lado (30/10, de 14): o lado maior não é "maioria" na capa, nos slides 3 e 4,
    no relógio, no espectro nem na legenda; 4 de 8 (18/10) também não; 6 de 11 (21/10) é."""
    p = raiox_pos(30)
    assert p.story_ids[0].startswith("binance")
    assert [s.ctx["kicker"] for s in p.slides[2:4]] == ["O lado maior", "O lado menor"]
    assert sp(p.slides[2].ctx["titulo"]) == "6 DE 14 · O LADO MAIOR"
    assert p.slides[0].alt.endswith("Gráfico: barra com 6 veículos no lado maior, 4 sem lado e 4 no lado menor.")
    for s in p.slides:
        assert "maioria" not in s.alt and "minoria" not in s.alt, (s.template, s.alt)
        assert "maioria" not in str(s.ctx.get("grafico", "")), s.template
    assert "▸ Lado maior (6): " in p.caption and "aioria" not in p.caption
    assert [s.ctx["kicker"] for s in noites()[18].slides[2:4]] == ["O lado maior", "O lado menor"]
    assert [s.ctx["kicker"] for s in noites()[21].slides[2:4]] == ["A maioria", "A minoria"]


def test_papeis_dos_lados():
    lados = lambda a, b, n: S.Lados("a", "b", a, b, n, a + b + n, None, None)  # noqa: E731
    assert lados(6, 4, 4).papeis == ("lado maior", "lado menor") and not lados(6, 4, 4).tem_maioria
    assert lados(6, 2, 3).papeis == ("maioria", "minoria") and lados(6, 2, 3).tem_maioria
    assert lados(4, 2, 2).papeis == ("lado maior", "lado menor")  # 4 de 8 é metade, não maioria
    assert lados(2, 2, 7).papeis == ("de um lado", "do outro lado")
    assert N._rotulos_barra(lados(6, 4, 4)) == {"a": "lado maior", "n": "sem lado", "b": "lado menor"}
    assert N._rotulos_barra(lados(5, 1, 0)) == {"a": "maioria", "n": "sem lado", "b": "minoria"}


def test_02_11_conclusao_majoritaria_sem_maioria_vira_molde():
    """4 de 9 não é maioria: "A cobertura majoritária enfatizou…" sai (como "a maioria dos veículos"), e entra o
    molde com as contagens (prévia final)."""
    e = edicao(2, 11)
    s = next(s for s in e.stories if s.id.startswith("eleicoes-de-meio-de-mandato"))
    p = N.raio_x(e, {}, election=False, numero_serie=19, story=s)
    concl = p.slides[5].ctx["conclusao"]
    assert "majorit" not in concl.lower()
    assert concl.startswith("4 de 9 veículos destacaram favoritismo democrata; 3, ofensiva de campanha; 2 não tomaram lado.")
    assert [x.ctx["kicker"] for x in p.slides[2:4]] == ["O lado maior", "O lado menor"]
