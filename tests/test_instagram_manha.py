"""Em 1 minuto 2.0 (§5.4), com as edições reais de data/."""

from __future__ import annotations

import functools
import json
import os
from datetime import date
from pathlib import Path

import pytest

from qijournal.config import load_config
from qijournal.instagram import estatistica as E
from qijournal.instagram import gerar, redacao
from qijournal.instagram.conteudo import Peca, is_electoral
from qijournal.instagram.formatos import manha as MH
from qijournal.models import Edition

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
real = pytest.mark.skipif(not (DATA / "2026-10-29.json").is_file(), reason="sem os JSONs reais em data/")
pytestmark = real

NOMES = ("Lula", "Flávio Bolsonaro", "Bolsonaro")
DIAS_IA = [*range(14, 32), "2026-11-01", "2026-11-02"]  # inteiro: outubro
ELEICAO_ATE = date(2026, 10, 27)  # election_until


def _data(day: int | str) -> date:
    """O dia do teste: inteiro é outubro (19 → 19/10); senão AAAA-MM-DD."""
    return date(2026, 10, day) if isinstance(day, int) else date.fromisoformat(day)


@functools.lru_cache(maxsize=None)
def historico(day: int | str) -> tuple[Edition, ...]:
    return tuple(E.load_history(DATA, _data(day)))


def edicao(day: int | str) -> Edition:
    return next(e for e in historico(day) if e.date == _data(day).isoformat())


def eleicao(day: int | str) -> bool:
    return _data(day) <= ELEICAO_ATE


@functools.lru_cache(maxsize=None)
def peca(day: int | str, numero_serie: int = 31) -> Peca | None:
    return MH.em_1_minuto(edicao(day), {}, list(historico(day)), election=eleicao(day), nomes=NOMES, numero_serie=numero_serie)


def fatos(p: Peca):
    return [s for s in p.slides if s.template == "m1_fato"]


def capa(p: Peca):
    return p.slides[0]


def materia(e: Edition, prefixo: str):
    return next(s for s in e.stories if s.id.startswith(prefixo))


def veiculos(e: Edition) -> list[str]:
    return sorted({o.name for s in e.stories if s.coverage for o in s.coverage.outlets})


# ── 21/10: a capa sem adjetivo, o qualificador, o slide 2 e o mercado ──────


def test_21_10_capa_sem_gigante_com_cerca_de():
    p = peca(21)
    c = capa(p).ctx
    assert c["manchete"] == "iFood\u00a0Benefícios compra a Caju por cerca de R$ 1 bi"  # o nome não quebra
    assert "gigante" not in c["manchete"].lower()
    assert (c["qual"], c["valor"]) == ("CERCA DE", "R$ 1 bi")
    assert c["selo"] == "EM 1 MINUTO Nº 031 · QUA 21 OUT"
    assert c["faixa"] == "1.640 notícias · 46 fontes · 1.159 assuntos"
    # o mais coberto (SpaceX e Tesla) é o fato do slide 4: a capa só aponta o slide, com a contagem
    assert c["mais_coberto"] == {"nome": "", "k": 12, "proprio": False, "slide": 4,
                                 "story_id": "spacex-e-tesla-avaliam-fusao-historica-apos-salt"}
    assert "O mais coberto do dia, com 12 veículos, está no slide 4." in capa(p).alt
    assert "dek" not in c and "linha_fina" not in c  # sem linha fina na capa


def test_21_10_slide_2_nao_repete_a_capa():
    p = peca(21)
    manchete = capa(p).ctx["manchete"]
    s2 = fatos(p)[0].ctx
    assert s2["frase"].rstrip(".") != manchete
    assert redacao.similaridade(s2["frase"], manchete) < MH.JACCARD_CAPA
    assert s2["valor"] != "R$ 1 bi"  # nem o número da capa
    assert s2["relogio"][:3] == [
        {"nome": "Folha de S.Paulo", "hora": "20/10 15h57"},
        {"nome": "g1", "hora": "16h00"},
        {"nome": "NeoFeed", "hora": "16h35"},
    ]
    assert s2["relogio_mais"] == 5


def test_21_10_nenhum_fato_repete_o_mercado_nem_esporte():
    p = peca(21)
    mercado = next(s for s in p.slides if s.template == "mercado_linha")
    paineis = mercado.ctx["paineis"]
    ibov = next(x for x in paineis if x["id"] == "ibov")
    assert ibov["change"] == "+1,57%"
    for s in fatos(p):
        assert not MH._repete_mercado([s.ctx["frase"]], paineis)
        assert "1,57" not in s.ctx["frase"] and "Ibovespa" not in s.ctx["frase"]
        assert "Vasco" not in s.ctx["frase"] and s.ctx["section"] != "esporte"
    assert all("Vasco" not in s.alt for s in p.slides)
    assert not any(sid.startswith("vasco") or sid.startswith("ibovespa") for sid in p.story_ids)


def test_21_10_estrutura_e_legenda():
    p = peca(21)
    assert (p.slot, p.kind, p.serie, p.numero_serie) == ("manha", "carousel", "em1min", 31)
    assert [s.template for s in p.slides] == ["m1_capa"] + ["m1_fato"] * 5 + ["mercado_linha", "m1_fechamento"]
    assert p.slides[6].ctx["selo"] == "EM 1 MINUTO Nº 031" and p.slides[6].ctx["contador"] is True
    assert p.slides[6].ctx["tema"] == "t-dark"
    linhas = p.caption.split("\n")
    assert linhas[0] == "iFood Benefícios compra a Caju por cerca de R$ 1 bi · e mais 4 fatos da quarta"
    assert sum(1 for x in linhas if x.startswith("▸ ")) == 5
    assert "▸ A operação combina plataformas que somam 3 milhões de usuários" in p.caption
    assert "(8 veículos · sem divisão)" in p.caption
    assert "Resumo por IA a partir de 1.640 notícias de 46 fontes." in p.caption
    assert MH.CTA in p.caption
    tags = linhas[-1].split()
    assert 3 <= len(tags) <= 4 and tags[:2] == ["#triangula", "#noticias"]
    fim = p.slides[-1].ctx
    assert (fim["valor"], fim["frase"]) == ("1.039", "de 1.159 assuntos de hoje (90%) saíram em uma fonte só.")
    assert fim["cta"] == MH.CTA and "link na bio" not in p.slides[-1].alt  # um CTA só


def test_21_10_raiox_da_noite_nao_vira_capa_e_ganha_o_selo():
    e = edicao(21)
    spacex = materia(e, "spacex")
    p = MH.em_1_minuto(e, {}, list(historico(21)), election=True, nomes=NOMES, numero_serie=31, raiox_story_id=spacex.id)
    assert capa(p).ctx["manchete"].startswith("iFood")
    s = next(s for s in fatos(p) if "SpaceX" in s.ctx["frase"])
    assert "Raio-X às 18h30" in s.ctx["cobertura"] and "Raio-X às 18h30" in s.alt
    assert "12 veículos · dividida 6 × 2 · Raio-X às 18h30" in p.caption


def test_numero_do_dia_nao_vira_capa_nem_fato_e_tira_o_ativo_do_titulo():
    e = edicao(21)
    ifood = materia(e, "ifood")
    numero = Peca(slot="numero", kind="carousel", title="Número do dia", slides=[], caption="",
                  story_ids=[ifood.id], chave="acum:ibov")
    p = MH.em_1_minuto(e, {}, list(historico(21)), election=True, nomes=NOMES, numero_serie=31, numero=numero)
    assert ifood.id not in p.story_ids and not capa(p).ctx["manchete"].startswith("iFood")
    mercado = next(s for s in p.slides if s.template == "mercado_linha")
    assert not mercado.ctx["titulo"].startswith("Ibovespa")


# ── 26 e 27/10: período eleitoral ──────────────────────────────────────


def politica(section: str | None, texto: str) -> bool:
    """O item ocupa a vaga de política do período eleitoral (a seção, ou eleição de outro país)?"""
    return section == "politica" or bool(MH._ELEICAO.search(texto) or MH._URNAS.search(texto))


@pytest.mark.parametrize("dia", [26, 27])
def test_periodo_eleitoral_capa_nunca_eleitoral_e_politica_por_ultimo(dia):
    e = edicao(dia)
    sel = MH.selecionar(e, election=True, nomes=NOMES)
    assert sel is not None
    assert not MH.eleitoral_capa(sel.capa, [sel.manchete], NOMES)
    assert not is_electoral(sel.capa, sel.manchete, NOMES)
    pol = [politica(f.story.section if f.story else None, f.texto) for f in sel.fatos[1:]]
    assert sum(pol) <= 1
    if any(pol):
        assert pol[-1]
    for f in sel.fatos:
        assert not MH._eleitoral(f.story, [f.texto], NOMES)


def test_26_10_so_o_bola_de_ouro_escapa_e_o_07h_vira_terminal():
    sel = MH.selecionar(edicao(26), election=True, nomes=NOMES)
    assert sel.capa.section == "esporte" and len(sel.fatos) < MH.FATOS_MIN
    assert peca(26) is None


def test_27_10_capa_nao_e_a_eleicao_de_israel():
    e = edicao(27)
    israel = materia(e, "israel-vai-as-urnas")
    assert MH.eleitoral_capa(israel, [], NOMES)
    p = peca(27)
    c = capa(p).ctx
    assert c["manchete"] == "Itaú Unibanco anuncia a aquisição total da startup NeoSpace por R$ 2,925 bilhões"
    assert c["valor"] == "R$ 2,925 bi"
    assert c["mais_coberto"] is None  # o mais coberto do dia é a eleição de Israel: a linha sai
    for s in p.slides:
        assert not E.is_electoral(s.alt, s.ctx.get("section"), NOMES)
    # nada eleitoral até 27/10, nem a eleição de outro país (§2): Israel também sai dos fatos
    assert israel.id not in p.story_ids
    assert not any(E.eleicao_escrita(s.alt) for s in p.slides) and not E.eleicao_escrita(p.caption)
    assert "Ibovespa" not in " ".join(s.ctx["frase"] for s in fatos(p))  # o circuit breaker está no slide 7


def test_eleicao_de_outro_pais_so_na_linha_fina_nao_tira_a_capa():
    trump = materia(edicao(18), "trump-suspende")
    assert "eleições" in trump.dek and not MH.eleitoral_capa(trump, [], NOMES)
    assert capa(peca(18)).ctx["manchete"].startswith("Trump suspende sanções")


# ── 29/10: o mesmo assunto duas vezes no briefing ─────────────────────────


def test_29_10_sem_fato_duplicado():
    e = edicao(29)
    p = peca(29)
    flavio = [s.id for s in e.stories if s.id.startswith("flavio-bolsonaro-desembarca")]
    assert len(flavio) == 2 and sum(1 for b in e.briefing if "Flávio Bolsonaro desembarca" in b) == 2
    assert sum(1 for sid in p.story_ids if sid in flavio) == 1
    frases = [s.ctx["frase"] for s in fatos(p)]
    textos = [capa(p).ctx["manchete"], *frases]
    for i, a in enumerate(textos):
        for b in textos[i + 1:]:
            if a is textos[0] and b is frases[0]:
                continue  # o slide 2 é a matéria da capa, com outro texto
            assert not MH.mesmo_assunto(a, b), (a, b)
    assert len(set(p.story_ids)) == len(p.story_ids) == 5
    assert sum(1 for f in frases if f.startswith("Fed mantém")) <= 1
    assert sum(1 for f in frases if "trens" in f) <= 1


def test_29_10_verbo_neutro_e_ressalva_ao_menos():
    p = peca(29)
    alphabet = next(s.ctx for s in fatos(p) if "Alphabet" in s.ctx["frase"])
    assert alphabet["frase"].startswith("Ações da Alphabet caem até 10%")
    assert (alphabet["qual"], alphabet["valor"]) == ("ATÉ", "10%")
    # o briefing e a manchete são o título literal da Folha (dados#10): vai a frase do corpo que nomeia o fato
    trens = next(s.ctx for s in fatos(p) if "trens" in s.ctx["frase"])
    assert trens["frase"].startswith("Pelo menos 68 pessoas morreram e mais de cem ficaram feridas")
    assert "colisão entre dois trens de alta velocidade" in trens["frase"]
    assert (trens["qual"], trens["valor"]) == ("PELO MENOS", "68 pessoas")


# ── Sem IA e outras regras ──────────────────────────────────────────────


def test_edicao_sem_ia_devolve_none():
    e = Edition.from_dict(json.loads((DATA / "2026-10-13.json").read_text(encoding="utf-8")))
    assert e.mode == "heuristic"
    assert MH.em_1_minuto(e, {}, [e], election=True, nomes=NOMES, numero_serie=1) is None


@pytest.mark.parametrize("dia", [d for d in DIAS_IA if d != 26])
def test_lint_e_regras_em_todos_os_dias(dia):
    e = edicao(dia)
    p = peca(dia)
    assert p is not None and len(p.slides) == 8
    assert redacao.lint(p, election=eleicao(dia), nomes=NOMES, veiculos=veiculos(e)) == []
    assert redacao.lint(p, election=False, nomes=NOMES, veiculos=veiculos(e)) == []
    c = capa(p).ctx
    assert len(c["manchete"]) <= MH.MANCHETE_MAX and not MH._adj_sobrando(c["manchete"], MH._titulos(e, materia(e, p.story_ids[0])))
    if eleicao(dia):
        assert not MH.eleitoral_capa(materia(e, p.story_ids[0]), [c["manchete"]], NOMES)
        pol = [politica(s.ctx["section"], s.ctx["frase"]) for s in fatos(p)[1:]]
        assert sum(pol) <= 1 and (not any(pol) or pol[-1])
    if c["mais_coberto"]:
        assert c["mais_coberto"]["k"] >= MH.MAIS_COBERTO_MIN
    for s in fatos(p):
        x = s.ctx
        assert x["frase"].endswith((".", "!", "?"))
        if x["heroi"] == "cobertura":
            assert x["qual"] == MH.NOTICIADO and int(x["valor"].split()[0]) >= MH.COBERTURA_MIN
        if x["valor"]:
            assert x["tam"] >= MH.NUM_FATO_MIN and MH.px_cabe(x["valor"]) >= MH.NUM_FATO_MIN
        else:
            assert x["solo"] and x["tam_frase"] >= 60
    linha1 = p.caption.split("\n", 1)[0]
    assert len(linha1) <= redacao.LINHA1_MAX and len(p.caption) <= redacao.LEGENDA_MAX


def test_selos_e_fatos_de_varios_dias():
    # 19/10: o slide 2 não repete o número da capa nem por outra palavra ("2.400 presos" / "2.400 detidos")
    p = peca(19)
    assert capa(p).ctx["valor"] == "2.400 presos"
    assert "2.400" not in fatos(p)[0].ctx["valor"]
    # 17/10: contagem que não cabe numa linha perde o substantivo, que está na frase
    p = peca(17)
    americanas = next(s.ctx for s in fatos(p) if "ex-executivos" in s.ctx["frase"])
    assert americanas["valor"] == "12"
    # 16/10: no período eleitoral, a política (não eleitoral) entra uma vez e por último
    p = peca(16)
    assert fatos(p)[-1].ctx["section"] == "politica"


# ── Peças de texto ──────────────────────────────────────────────────────


def test_numero_fato_contagem_qualificador_e_nomes():
    assert MH.numero_fato("Colisão de trens mata ao menos 68 pessoas na Espanha") == ("AO MENOS", "68 pessoas")
    assert MH.numero_fato("Mais de 7 milhões de israelenses votam") == ("MAIS DE", "7 milhões")
    assert MH.numero_fato("Emenda Constitucional 141 reduz jornada para 40 horas") is None
    assert MH.numero_fato("Abstenção registra menor patamar em 20 anos") is None
    assert MH.numero_fato("França mobiliza 70 mil policiais") == ("", "70 mil")  # não cabe a 180 px
    assert MH.numero_fato("Protestos deixam 2.400 presos", minimo_px=MH.NUM_CAPA_MIN) == ("", "2.400 presos")
    assert MH.numero_fato("iFood compra a Caju por cerca de R$ 1 bilhão", evitar=["R$ 1 bi"]) is None
    assert MH.numero_capa("iFood Benefícios compra a Caju por cerca de R$ 1 bi e cria gigante") == ("CERCA DE", "R$ 1 bi")
    assert MH.numero_capa("Azul fecha financiamento de pelo menos R$ 2 bilhões") == ("PELO MENOS", "R$ 2 bi")
    assert MH.numero_fato("o planeta caminha para um aquecimento de 2,8°C") == ("", "2,8°C")


def test_verbo_neutro_so_antes_de_numero():
    assert MH.neutro("Ações da Alphabet desabam até 10% com queima de caixa") == "Ações da Alphabet caem até 10% com queima de caixa"
    assert MH.neutro("Ibovespa despenca 1,15% com cautela") == "Ibovespa cai 1,15% com cautela"
    assert MH.neutro("Ações da Jio disparam mais de 21% em estreia") == "Ações da Jio sobem mais de 21% em estreia"
    assert MH.neutro("Neymar é expulso e dispara contra Nestor") == "Neymar é expulso e dispara contra Nestor"


def test_mesmo_assunto():
    assert MH.mesmo_assunto(
        "Fed mantém taxa de juros inalterada e projeta retomada de altas em dezembro",
        "Fed mantém juros na faixa de 3,75% a 4% ao ano em votação com duas dissidências",
    )
    assert MH.mesmo_assunto(
        "Colisão entre trens de alta velocidade mata ao menos 68 pessoas na Espanha",
        "Colisão de trens de alta velocidade na Espanha deixa vítimas e abre crise sobre segurança",
    )
    assert not MH.mesmo_assunto(
        "DeepSeek levanta US$ 15 bilhões em rodada de financiamento e dobra meta de captação",
        "OpenAI negocia rodada de até US$ 30 bilhões a valuation de US$ 1,25 trilhão antes de IPO",
    )


def test_linha1_corta_a_manchete_e_mantem_os_fatos():
    linha = MH.linha1("Mercados financeiros devolvem parte do estresse com ajuda do Tesouro e falas de Galípolo", 4,
                      date(2026, 10, 28))
    assert linha == "Mercados financeiros devolvem parte do estresse com ajuda do Tesouro · e mais 4 fatos da quarta"
    assert MH.linha1("Curta", 4, date(2026, 10, 24)) == "Curta · e mais 4 fatos do sábado"


def test_tamanhos_enchem_o_quadro_sem_palavra_solta():
    area = MH.area_fato(relogio=False, selo=True, fontes=True)
    n, f = MH.tamanhos_fato("0,32%", "", "IPCA-15 sobe 0,32% em outubro com alívio tarifário, mas alimentos seguem pressionados.", area)
    assert n == MH.NUM_FATO_MAX and f == MH.FRASE_TAMS[0]
    n, f = MH.tamanhos_fato("", "", "STJ fixa novos critérios temporais para indenizações em desapropriações.", area)
    assert n == 0 and f < MH.SOLO_TAMS[0]
    assert not MH._palavra_solta("STJ fixa novos critérios temporais para indenizações em desapropriações.", f, MH.SERIF_800)


# ── Render (só com o Chromium do Playwright: QIJ_CHROMIUM) ────────────────


@pytest.mark.skipif(not os.environ.get("QIJ_CHROMIUM"), reason="sem Chromium (QIJ_CHROMIUM)")
@pytest.mark.parametrize("dia", [14, 15, 19, 21, 24, 27, 29, 31, "2026-11-01"])
def test_render_sem_texto_cortado(dia, tmp_path):
    from qijournal.instagram import conteudo
    from qijournal.instagram.render import Renderer

    config = load_config(ROOT)
    env = gerar.environment()
    brand = gerar.brand_context(config)
    p = MH.em_1_minuto(edicao(dia), brand, list(historico(dia)), election=eleicao(dia), nomes=NOMES, numero_serie=31)
    with Renderer() as r:
        for i, s in enumerate(p.slides, 1):
            html = env.get_template(f"{s.template}.html.j2").render(
                brand=brand, s=conteudo.polish(s.ctx), page=i, pages=len(p.slides), kind=p.kind, slot=p.slot
            )
            res = r.render(html, tmp_path / f"{i:02d}.jpg", width=1080, height=1350)
            assert res.transbordos == [], (i, [str(t) for t in res.transbordos])


# ── Revisão da Onda 1: dividida, selo, capa e slide 2, títulos literais ─────


@pytest.mark.parametrize("dia", [d for d in DIAS_IA if d != 26])
def test_dividida_so_pelo_criterio_do_placar_em_todo_o_perfil(dia):
    """dados#9 / arte#20: uma definição só de "dividida", a do Placar (4+ comparados, 2+ de cada lado)."""
    e = edicao(dia)
    p = E.placar(e, election=eleicao(dia), nomes=NOMES)
    assert p.rachas == sum(1 for c, _ in p.itens if E.dividida(c))
    for s in e.stories:
        if s.coverage is None:
            continue
        selo = E.selo_cobertura(e, s)
        assert ("dividida" in selo) == E.dividida(s.coverage), (s.id, selo)
        a, n, b = E.lados(s.coverage)
        if not E.dividida(s.coverage) and a and b:
            assert f"· {max(a, b)} × {min(a, b)}" in selo  # abaixo do critério, só a contagem


def test_selos_de_21_10_19_10_e_29_10():
    e = edicao(21)
    deepseek = next(s for s in e.stories if "DeepSeek" in s.headline)
    assert E.selo_cobertura(e, deepseek) == "6 veículos · 4 × 1"  # antes: "dividida 4 × 1"
    emenda = materia(e, "promulgacao-da-emenda")
    assert E.selo_cobertura(e, emenda) == "4 fontes · 2 × 1"  # a Secovi-SP está na comparação: "fontes" (§3)
    assert E.selo_cobertura(e, materia(e, "spacex"), materia(e, "spacex").id) == "12 veículos · dividida 6 × 2 · Raio-X às 18h30"
    e = edicao(19)
    agro = materia(e, "pedidos-de-recuperacao")
    assert E.selo_cobertura(e, agro) == "3 veículos · 2 × 1"  # o Placar de 19/10 é "0 de 13": nada dividiu
    assert E.placar(e, election=True, nomes=NOMES).rachas == 0
    e = edicao(29)
    fed = materia(e, "fed-mantem")
    assert E.selo_cobertura(e, fed) == "4 veículos · dividida 2 × 2"


@pytest.mark.parametrize("dia", [d for d in DIAS_IA if d != 26])
def test_barra_e_fontes_fecham_com_o_selo(dia):
    """dados#18 / arte#12: a mini barra nunca soma mais que o selo e a lista de fontes nunca tem menos nomes."""
    p = peca(dia)
    for s in fatos(p):
        x = s.ctx
        if not x["cob_kt"]:
            continue
        soma = x["cob_a"] + x["cob_n"] + x["cob_b"]
        assert soma <= x["cob_k"], (x["frase"], soma, x["cob_kt"])
        assert soma == 0 or soma >= 2  # com uma fonte comparada, sem barra
        nomes = s.alt.split("Fontes: ", 1)[1].rstrip(".").split(", ") if "Fontes: " in s.alt else []
        assert len(nomes) >= x["cob_k"], (x["frase"], nomes, x["cob_kt"])


def test_27_10_stj_e_19_10_escala_5x2():
    e = edicao(27)
    stj = materia(e, "stj-fixa-novos")
    assert E.selo_cobertura(e, stj) == "2 fontes · sem divisão"  # Valor e STJ: antes "1 veículo" com 2 na barra
    s = next(s.ctx for s in fatos(peca(27)) if s.ctx["frase"].startswith("STJ fixa"))
    assert (s["cob_a"], s["cob_n"], s["cob_b"]) == (0, 2, 0) and s["cob_k"] == 2
    escala = next(s for s in fatos(peca(19)) if s.ctx["frase"].startswith("Empresas correm"))
    assert escala.ctx["cobertura"] == "2 veículos · sem divisão"
    assert escala.ctx["fontes"] == "Valor Econômico, Estadão"  # antes só "Valor Econômico"


@pytest.mark.parametrize("dia", [d for d in DIAS_IA if d != 26])
def test_slide_2_nunca_repete_o_dado_da_capa(dia):
    """arte#7 / dados#11 / arte#8: o laranja da capa é o dado dela; o slide 2 não repete esse dado; a contagem do
    mais coberto vai em tinta e, quando ele é a própria capa, fica para o slide 2."""
    p = peca(dia)
    c, s2 = capa(p).ctx, fatos(p)[0].ctx
    assert c["destaque"] == c["valor"]  # nunca a contagem do mais coberto de outra matéria
    if c["valor"]:
        assert s2["valor"] != c["valor"]
    # nem um número da manchete da capa, nem o texto dela (31/10: a COP17 de novo, com 2 palavras a mais)
    assert not (s2["valor"] and s2["valor"] in MH._todos_numeros(c["manchete"])), (c["manchete"], s2["valor"])
    assert not MH.repete_capa(s2["frase"], c["manchete"]), (c["manchete"], s2["frase"])
    # o número da capa é o da manchete que sai nela, quando ela tem um
    if MH.numero_capa(c["manchete"]) and c["heroi"] == "numero":
        assert c["valor"] in MH._todos_numeros(c["manchete"]), (c["manchete"], c["valor"])
    if c["heroi"] == "cobertura":
        assert c["qual"] == MH.NOTICIADO and int(c["valor"].split()[0]) >= MH.COBERTURA_CAPA_MIN
        assert s2["heroi"] != "cobertura"
    mc = c["mais_coberto"]
    if mc and mc["proprio"]:
        assert "veículos" not in capa(p).alt.split("É o assunto mais coberto do dia", 1)[1][:3]


@functools.lru_cache(maxsize=None)
def peca_22() -> Peca:
    """22/10 como no gerador: o Raio-X da noite (STF/ISS) não vira capa."""
    stf = materia(edicao(22), "stf-decide-que-iss")
    return MH.em_1_minuto(edicao(22), {}, list(historico(22)), election=True, nomes=NOMES, numero_serie=31,
                          raiox_story_id=stf.id)


def test_capa_sem_numero_com_5_veiculos_estampa_a_cobertura():
    p = peca_22()
    c = capa(p).ctx
    assert c["manchete"].startswith("Rússia aceita retomar negociações")
    assert (c["heroi"], c["qual"], c["valor"]) == ("cobertura", MH.NOTICIADO, "9 veículos")
    assert c["destaque"] == "9 veículos" and c["mais_coberto"]["k"] == 12  # o mais coberto é a Meta, em tinta
    assert fatos(p)[0].ctx["heroi"] != "cobertura"
    assert "Noticiado por 9 veículos." in capa(p).alt


def test_capa_mais_coberta_entrega_a_contagem_no_slide_2():
    p = peca(14)
    c, s2 = capa(p).ctx, fatos(p)[0].ctx
    assert c["valor"] == "3,7%" and (c["mais_coberto"]["nome"], c["mais_coberto"]["k"], c["mais_coberto"]["proprio"]) == (
        "", 11, True)
    assert "É o assunto mais coberto do dia." in capa(p).alt and "do dia: 11" not in capa(p).alt
    assert (s2["heroi"], s2["valor"]) == ("cobertura", "11 veículos")


def test_template_da_capa_sem_contagem_quando_proprio_e_em_tinta_quando_outro():
    env = gerar.environment()
    from qijournal.instagram import conteudo

    def html(dia):
        p = peca_22() if dia == 22 else peca(dia)
        return env.get_template("m1_capa.html.j2").render(brand={"logo": "", "handle": "", "fonts_css": ""},
                                                           s=conteudo.polish(p.slides[0].ctx), page=1, pages=8,
                                                           kind="carousel", slot="manha")

    h14 = html(14)
    assert "É o assunto mais coberto do dia</div>" in h14 and "11 veículos" not in h14
    h22 = html(22)
    assert 'class="miolo cob"' in h22 and "class=hl" not in h22
    assert "O mais coberto do dia está no slide 4 · <b>12 veículos</b>" in h22  # a Meta é o fato do slide 4
    h24 = html(24)  # a França não é fato: a linha leva o nome
    assert "O mais coberto · <b>12 veículos</b>" in h24 and "conter nova onda de protestos" in h24


@pytest.mark.parametrize("dia", [d for d in DIAS_IA if d != 26])
def test_nenhum_titulo_literal_de_veiculo(dia):
    """dados#10: nem a capa, nem os fatos, nem a legenda trazem o título literal de um veículo."""
    e = edicao(dia)
    literais = E.titulos_literais(e)
    p = peca(dia)
    assert not E.titulo_literal(capa(p).ctx["manchete"], literais)
    for s in fatos(p):
        assert not E.titulo_literal(s.ctx["frase"], literais), s.ctx["frase"]
    for linha in p.caption.split("\n"):
        if linha.startswith("▸ "):
            assert not E.titulo_literal(linha[2:].rsplit(" (", 1)[0], literais), linha


def test_16_10_credipaiva_sem_o_titulo_da_cnn():
    e = edicao(16)
    st = next(s for s in e.stories if "Credipaiva" in s.headline)
    b = MH._briefing(e)[0].get(st.id, "")
    literais = E.titulos_literais(e)
    assert E.titulo_literal("Banco Central decreta liquidação extrajudicial da cooperativa Credipaiva", literais)
    f = MH._texto(e, st, b, literais=literais)
    assert f.texto != "Banco Central decreta liquidação extrajudicial da cooperativa Credipaiva."
    assert not E.titulo_literal(f.texto, literais) and "Credipaiva" in f.texto
    # sem a lista de literais, o texto antigo (o da CNN Brasil) voltaria
    assert MH._texto(e, st, b).texto == "Banco Central decreta liquidação extrajudicial da cooperativa Credipaiva."


def test_capa_nao_repete_a_noite():
    """Pendência da integração (19/10) e §2: a capa não estampa o que a noite do mesmo dia estampa (o Placar
    de 19/10 destaca os protestos na França) nem reabre, sem número novo, o Raio-X da noite anterior (o de
    28/10 é a fuga de Flávio Bolsonaro; em 29/10 ele desembarca nos EUA). O assunto ainda pode ser fato."""
    e19 = edicao(19)
    franca = materia(e19, "protestos-violentos-na-franca")
    assert capa(peca(19)).ctx["manchete"].startswith("Protestos violentos na França")  # sem a noite
    p = MH.em_1_minuto(e19, {}, list(historico(19)), election=True, nomes=NOMES, numero_serie=31, evitar_capa=[franca.id])
    assert p.story_ids[0] != franca.id and franca.id in p.story_ids
    e28, e29 = edicao(28), edicao(29)
    fuga = materia(e28, "flavio-bolsonaro-descumpre")
    assert capa(peca(29)).ctx["manchete"].startswith("Flávio Bolsonaro desembarca")  # sem a noite anterior
    p = MH.em_1_minuto(e29, {}, list(historico(29)), election=False, nomes=NOMES, numero_serie=31, noite_anterior=[fuga])
    assert not p.story_ids[0].startswith("flavio-bolsonaro")
    assert any(sid.startswith("flavio-bolsonaro-desembarca") for sid in p.story_ids[1:])
    # com número novo, a continuação pode ser capa (a regra da véspera, §5.4)
    marca = MH._marca(MH.Fato(fuga.headline, fuga, "manchete"))
    assert MH._repete_vespera(materia(e29, "flavio-bolsonaro-desembarca"), [materia(e29, "flavio-bolsonaro-desembarca").headline], [marca])
    assert not MH._repete_vespera(materia(e29, "flavio-bolsonaro-desembarca"), ["Flávio Bolsonaro paga R$ 2 milhões"], [marca])


def test_gerador_capa_sem_a_noite_do_dia_nem_a_da_vespera():
    """No gerador (build_pieces, com a fila avançando): em 19/10 a capa não é a França do Placar; em 29/10 não
    reabre a fuga de Flávio Bolsonaro, o Raio-X de 28/10. A tela 2 dos stories conta a mesma capa."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    from qijournal.instagram.fila import PUBLISHED, Fila

    config = load_config(ROOT)
    tz = ZoneInfo("America/Sao_Paulo")
    fila = Fila()
    pecas = {}
    for dia in (18, 19, 28, 29):
        e = edicao(dia)
        ps = gerar.build_pieces(e, config, {}, fila, list(historico(dia)))
        dia_d = date.fromisoformat(e.date)
        inicio, fim = datetime.combine(dia_d, time(7), tz), datetime.combine(dia_d, time(23), tz)
        posts = [gerar._post(p, e, gerar.post_id(e.date, p.slot), inicio, fim, []) for p in ps]
        fila.replace_day(e.date, posts)
        for p in posts:
            atual = fila.get(p.id)
            atual.status, atual.published_at = PUBLISHED, atual.due
            fila.registrar_publicacao(atual)
        pecas[dia] = {p.slot: p for p in ps}
    noite19, manha19 = pecas[19]["noite"], pecas[19]["manha"]
    assert noite19.title == "Placar do dia" and noite19.story_ids
    assert manha19.story_ids[0] not in noite19.story_ids
    assert pecas[28]["noite"].story_ids[0].startswith("flavio-bolsonaro-descumpre")
    manha29 = pecas[29]["manha"]
    assert manha29.title == "Em 1 minuto" and not manha29.story_ids[0].startswith("flavio-bolsonaro")
    assert pecas[29]["stories"].story_ids == manha29.story_ids[:1]


def test_repete_capa_jaccard_ou_a_capa_contida():
    capa02 = "Fazenda descarta teto para juros após proposta de André Lara Resende agitar mercado"
    assert MH.repete_capa("O Ministério da Fazenda descartou fixar teto para a Selic após a tese de André Lara Resende "
                          "gerar turbulência no mercado financeiro.", capa02)  # a capa dita de novo
    assert not MH.repete_capa("Ministro Dario Durigan nega debate sobre limitação da Selic e desmente reunião de Lula "
                              "com economista; PT apoia críticas ao Banco Central.", capa02)
    capa31 = "COP17 de Biodiversidade é encerrada na Armênia com críticas e definição de nova sede"
    assert MH.repete_capa(
        "COP17 de Biodiversidade é encerrada na Armênia com críticas às metas globais e definição do Equador como "
        "sede da COP18.", capa31)  # Jaccard 0,55, mas a capa inteira está dentro
    assert not MH.repete_capa(
        "Conferência da ONU sobre biodiversidade termina em Yerevan com avaliação negativa sobre metas globais, e "
        "Equador é escolhido para sediar a COP18.", capa31)
    capa30 = "TCU define alíquota de 11,3% para a CBS"
    assert MH.repete_capa("O TCU estimou em 11,3% a alíquota da CBS, elevando o alerta entre empresas e tributaristas.", capa30)


def test_31_10_e_20_10_slide_2_com_outro_texto():
    s2 = fatos(peca(31))[0].ctx
    assert capa(peca(31)).ctx["manchete"].startswith("COP17 de Biodiversidade é encerrada")
    assert s2["frase"].startswith("Conferência da ONU sobre biodiversidade termina em Yerevan")
    s2 = fatos(peca(20))[0].ctx
    assert capa(peca(20)).ctx["valor"] == "R$ 42,5 bi" and s2["valor"] != "R$ 42,5 bi"
    assert not s2["frase"].startswith("A Justiça do Rio encerrou")  # a capa de novo


def test_18_10_slide_2_cai_para_a_frase_do_corpo():
    """Sem a noite (no gerador, o Trump de 18/10 é o Raio-X): o item do briefing é a própria capa e a linha fina
    cita a eleição dos EUA; o slide 2 usa a frase do corpo em vez de a capa cair."""
    p = peca(18)
    assert capa(p).ctx["manchete"].startswith("Trump suspende sanções")
    s2 = fatos(p)[0].ctx
    # a 1ª frase do corpo ("Donald Trump assinou um decreto… suspendendo… sanções e tarifas… contra a Rússia") ainda
    # é a capa dita de novo; vai a 2ª, com o número novo
    assert s2["frase"].startswith("A legislação sancionada no mês passado previa a cobrança de tarifas de até 100%")
    assert (s2["qual"], s2["valor"]) == ("ATÉ", "100%")
    assert not MH.repete_capa(s2["frase"], capa(p).ctx["manchete"])


def test_01_11_capa_com_sujeito_e_o_numero_novo():
    """A manchete cai pelo "recorde" e a linha fina não diz de quem é o fato: o sujeito vai na frente. O número
    da capa é o da manchete que sai nela (90%), não os US$ 6,3 bi do Placar de 31/10; o slide 2 não repete."""
    e = edicao("2026-11-01")
    binance = materia(e, "binance-retoma")
    assert MH.texto_capa(e, binance).startswith("Binance: corretora informou ter coberto 90%")
    p = peca("2026-11-01")
    c, s2 = capa(p).ctx, fatos(p)[0].ctx
    assert p.story_ids[0] == binance.id
    assert c["manchete"] == "Binance: corretora informou ter coberto 90% dos valores levados"
    assert (c["heroi"], c["valor"]) == ("numero", "90%")
    assert s2["valor"] != "90%" and "90%" not in s2["frase"]
    assert p.caption.startswith("Binance: corretora informou")
    # a noite anterior (o Placar de 31/10 destaca o roubo de US$ 6,3 bi): o mesmo assunto, com número novo
    hackers = materia(edicao(31), "hackers-norte-coreanos")
    noite = [(*MH._marca(MH.Fato(hackers.headline, hackers, "manchete")), hackers.headline)]
    b = MH._briefing(e)[0].get(binance.id, "")
    repete, velhos = MH._da_noite(binance, [b, binance.headline, binance.dek], noite)
    assert not repete and velhos == {"6,3"}
    assert MH.numero_da_capa(binance, binance.headline) == ("", "US$ 6,3 bi")
    assert MH.numero_da_capa(binance, binance.headline, velhos) == ("", "90%")
    p = MH.em_1_minuto(e, {}, list(historico("2026-11-01")), election=False, nomes=NOMES, numero_serie=31,
                       noite_anterior=[hackers])
    assert p.story_ids[0] == binance.id and capa(p).ctx["valor"] == "90%"
    # sem número novo, a continuação não é capa
    assert MH._da_noite(binance, [binance.headline], noite) == (True, set())


def test_gerador_capa_de_01_11_depois_do_placar_de_31_10():
    """No gerador, com a fila avançando: o Placar de 31/10 destaca a Binance e conta como noite anterior."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    from qijournal.instagram.fila import PUBLISHED, Fila

    config = load_config(ROOT)
    tz = ZoneInfo("America/Sao_Paulo")
    fila = Fila()
    pecas = {}
    for dia in (30, 31, "2026-11-01"):
        e = edicao(dia)
        ps = gerar.build_pieces(e, config, {}, fila, list(historico(dia)))
        dia_d = date.fromisoformat(e.date)
        inicio, fim = datetime.combine(dia_d, time(7), tz), datetime.combine(dia_d, time(23), tz)
        posts = [gerar._post(p, e, gerar.post_id(e.date, p.slot), inicio, fim, []) for p in ps]
        fila.replace_day(e.date, posts)
        for p in posts:
            atual = fila.get(p.id)
            atual.status, atual.published_at = PUBLISHED, atual.due
            fila.registrar_publicacao(atual)
        pecas[dia] = {p.slot: p for p in ps}
    noite31 = pecas[31]["noite"]
    assert noite31.title == "Placar do dia" and any(s.startswith("hackers-norte-coreanos") for s in noite31.story_ids)
    assert set(fila.materias_raiox("2026-11-01")) == set(noite31.story_ids)
    manha = pecas["2026-11-01"]["manha"]
    assert manha.story_ids[0].startswith("binance-retoma") and manha.slides[0].ctx["valor"] == "90%"
    assert pecas["2026-11-01"]["stories"].story_ids == manha.story_ids[:1]


def test_nome_do_mais_coberto_para_antes_do_lugar():
    """30 e 31/10: o título passa de 90 caracteres sem junção; o nome para antes de " no maior ataque…", em vez de
    cair na tese ("Corretora congela ativos…", sem dizer quem) ou na linha fina."""
    def nome(dia):
        e = edicao(dia)
        return MH.nome_do_assunto(e, E.concentracao(e, election=eleicao(dia), nomes=NOMES).topo)

    assert nome(30) == "Binance suspende saques após roubo de US$ 6,3 bilhões"
    assert nome(31) == "Hackers norte-coreanos roubam US$ 6,3 bilhões da Binance"
    # arte (rodada 2): a manchete sem o adjetivo da casa, e não o rótulo sem verbo ("SpaceX e Tesla")
    assert nome(21) == "SpaceX e Tesla avaliam fusão após salto nos resultados operacionais"


# ── Ajuste final, rodada 1 ────────────────────────────────────────────────


@functools.lru_cache(maxsize=None)
def _grade(dias: tuple = (*range(14, 32), "2026-11-01", "2026-11-02")) -> dict:
    """As peças de cada dia pelo gerador, com UMA fila que avança dia a dia (marca como publicado)."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    from qijournal.instagram.fila import PUBLISHED, Fila

    config = load_config(ROOT)
    tz = ZoneInfo("America/Sao_Paulo")
    fila = Fila()
    pecas = {}
    for dia in dias:
        e = edicao(dia)
        ps = gerar.build_pieces(e, config, {}, fila, list(historico(dia)))
        dia_d = date.fromisoformat(e.date)
        inicio, fim = datetime.combine(dia_d, time(7), tz), datetime.combine(dia_d, time(23), tz)
        posts = [gerar._post(p, e, gerar.post_id(e.date, p.slot), inicio, fim, []) for p in ps]
        fila.replace_day(e.date, posts)
        for p in posts:
            atual = fila.get(p.id)
            atual.status, atual.published_at = PUBLISHED, atual.due
            fila.registrar_publicacao(atual)
        pecas[dia] = {p.slot: p for p in ps}
    return pecas


def manha(dia):
    """O Em 1 minuto do dia como o gerador o monta (sem a noite do dia, o Número e a noite anterior)."""
    return _grade()[dia]["manha"]


@pytest.mark.parametrize("dia, valor", [(15, "4 veículos"), (16, "4 veículos"), (24, "4 veículos"), (31, "3 veículos")])
def test_capa_sem_numero_com_3_veiculos_ou_mais_estampa_a_cobertura(dia, valor):
    """arte#8: sem número na manchete, a capa das 07:00 não fica só serif no marinho com 3+ veículos."""
    c = capa(manha(dia)).ctx
    assert (c["heroi"], c["qual"], c["valor"], c["destaque"]) == ("cobertura", "NOTICIADO POR", valor, valor)


@pytest.mark.parametrize("dia", [22, 24, 28])
def test_slide_2_sem_numero_estampa_a_duracao_do_relogio(dia):
    """O slide 2 sem número nem cobertura (a da capa não volta) leva a duração do relógio, sempre nova, quando o
    texto traz dado novo e a duração passa de 3 horas."""
    s2 = fatos(manha(dia))[0].ctx
    assert s2["heroi"] == "relogio" and s2["qual"].startswith("DO PRIMEIRO AO ÚLTIMO VEÍCULO") and s2["relogio"]
    assert s2["valor"].endswith(("horas", "minutos")) and s2["destaque"] == s2["valor"]
    assert s2["valor"] != capa(manha(dia)).ctx["valor"]


def test_22_10_duracao_por_extenso_com_a_ressalva():
    s2 = fatos(manha(22))[0]
    assert (s2.ctx["qual"], s2.ctx["valor"]) == ("DO PRIMEIRO AO ÚLTIMO VEÍCULO, MAIS DE", "25 horas")
    assert "Do primeiro ao último veículo, mais de 25 horas." in s2.alt


def test_19_10_contagem_fora_da_lista_vira_o_numero_do_slide_2():
    texto = "Levantamento da Serasa Experian registra 448 requerimentos e aponta queda"
    assert MH.numero_fato(texto, minimo_px=100) == ("", "448 requerimentos")
    assert MH.numero_fato(texto) == ("", "448")  # não cabe numa linha a 180 px: o substantivo fica na frase
    assert MH.numero_fato("O prazo é de 20 anos e 48 horas para 3 vezes mais, com 6 votos") is None  # tempo, "vezes", "votos"
    assert fatos(manha(19))[0].ctx["valor"] == "448"


def test_28_10_slide_2_traz_dado_novo_e_nao_a_capa_dita_de_outro_jeito():
    capa28 = capa(manha(28)).ctx["manchete"]
    assert not MH.dado_novo("O mercado financeiro arrefeceu o estresse pós-eleitoral com queda dos juros futuros e do "
                            "dólar, auxiliado por recompras de títulos do Tesouro.", capa28)
    s2 = fatos(manha(28))[0].ctx["frase"]
    assert s2.startswith("Juros futuros e dólar registram queda") and MH.dado_novo(s2, capa28)


def test_numero_sem_unidade_em_evitar_pula_o_mesmo_numero_com_unidade():
    texto = "Hackers norte-coreanos roubam US$ 6,3 bilhões da Binance e 40% dos fundos"
    assert MH.numero_fato(texto, ["6,3"]) == ("", "40%")
    assert MH.numero_fato(texto, ["US$ 6,3 bi"]) == ("", "40%")


def test_nome_proprio_nao_quebra_na_capa():
    """02/11: a quebra equilibrada partia "André / Lara Resende"; só na arte (a legenda fica com espaço comum)."""
    p = manha("2026-11-02")
    c = capa(p).ctx
    assert "André Lara Resende" in c["manchete"] and "André Lara Resende" in p.slides[0].alt
    assert MH.nomes_juntos("A Binance começou a liberar") == "A Binance começou a liberar"  # artigo não junta
    juntos = MH.nomes_juntos("ao lado de Luiz Inácio Lula da Silva hoje").split(" ")
    assert all(len(w) <= MH.NOME_JUNTO_MAX for w in juntos) and "Luiz" in juntos  # o nome longo só em parte


def test_grade_de_29_10_a_01_11_sem_o_mesmo_numero_em_posts_seguidos():
    """arte/dados: o "11,3%" do TCU (Número de 29/10) não volta como capa em 30/10; o "US$ 6,3 BI" da Binance não
    é destaque de fato em 30 e 31/10; o Brent do slide de mercado de 31/10 não vira o Número de 01/11, nem o
    Ibovespa do Número de 31/10 ("18%")."""
    g = _grade()
    assert g[29]["numero"].chave == "11,3%"
    capa30 = g[30]["manha"]
    assert not capa30.story_ids[0].startswith("tcu-") and capa30.slides[0].ctx["valor"] != "11,3%"
    herois31 = [s.ctx["valor"] for s in g[31]["manha"].slides if s.template == "m1_fato"]
    herois30 = [s.ctx["valor"] for s in g[30]["manha"].slides if s.template == "m1_fato"]
    assert "US$ 6,3 bi" in herois30 and "US$ 6,3 bi" not in herois31
    assert g[31]["manha"].chave == "mercado:brent"
    assert g["2026-11-01"]["numero"].chave not in ("acum:brent", "acum:ibov")


# ── Ajuste final, rodada 2 ────────────────────────────────────────────────


def test_dado_novo_sem_o_dia_do_mes_nem_substantivo_com_maiuscula():
    """codigo#2: "(26)" de "nesta segunda-feira (26)" não é número novo; "Justiça" não é nome próprio da matéria."""
    e27, e15 = edicao(27), edicao(15)
    itau = materia(e27, "itau-unibanco-anuncia")
    capa27 = "Itaú Unibanco anuncia a aquisição total da startup NeoSpace por R$ 2,925 bilhões"
    s27 = "O Itaú Unibanco assinou um acordo nesta segunda-feira (26) para adquirir a totalidade das ações da NeoSpace."
    assert MH._numeros_de_dado(s27) == set() and not MH.dado_novo(s27, capa27, itau)
    stf = materia(e15, "stf-decide-que-bancos")
    capa15 = "STF decide que bancos não devem pagar expurgos inflacionários em depósitos judiciais"
    s15 = ("STF formou maioria para livrar bancos do pagamento de expurgos inflacionários de planos econômicos em "
           "recursos sob custódia da Justiça.")
    assert MH.dado_novo(s15, capa15) and not MH.dado_novo(s15, capa15, stf)  # sem a matéria, a regra antiga
    assert MH.dado_novo("Por 7 votos a 2, corte veda incidência de índices dos planos Bresser, Verão e Collor.", capa15, stf)
    assert not MH.dado_novo("Prazo vale desde 2022 e até 15/10.", capa15, stf)  # ano e data não são dado


@pytest.mark.parametrize("dia, comeco", [
    (15, "Por 7 votos a 2, corte veda"),
    (16, "O ceticismo financeiro sobre o retorno da infraestrutura de IA"),  # o por que importa
    (27, "A transação consolida a corrida dos grandes conglomerados"),
])
def test_slide_2_nao_e_a_capa_dita_de_outro_jeito(dia, comeco):
    """codigo#2 (15, 16 e 27/10): sem texto com dado novo, o slide 2 vai para o "por que importa"; a duração só
    vira o número com dado novo e 3 horas ou mais (o "101 MINUTOS" de 15/10 sai)."""
    p = manha(dia)
    s2 = fatos(p)[0].ctx
    assert s2["frase"].startswith(comeco), s2["frase"]
    assert s2["heroi"] != "relogio"
    assert not MH.repete_capa(s2["frase"], capa(p).ctx["manchete"])


def test_duracao_do_relogio_como_numero_no_maximo_em_3_dias():
    """arte (rodada 2): a duração era o número do slide 2 em 6 de 19 dias; agora só com dado novo e 3+ horas."""
    g = _grade()
    dias = [d for d, ps in g.items() if ps["manha"].serie == MH.SERIE and fatos(ps["manha"])[0].ctx["heroi"] == "relogio"]
    assert dias == [22, 24, 28]


def test_capa_da_vespera_nao_volta_como_capa():
    """arte (rodada 2): a Alphabet foi capa em 29/10; em 30/10, com número novo (US$ 500 bi), ela fica como fato."""
    g = _grade()
    assert g[29]["manha"].story_ids[0].startswith("acoes-da-alphabet")
    assert not g[30]["manha"].story_ids[0].startswith("alphabet-")
    assert any(sid.startswith("alphabet-perde") for sid in g[30]["manha"].story_ids[1:])
    e29, e30 = edicao(29), edicao(30)
    assert MH.mesma_capa(materia(e29, "acoes-da-alphabet"), materia(e30, "alphabet-perde"))
    assert not MH.mesma_capa(materia(e29, "acoes-da-alphabet"), materia(e30, "petrobras-confirma"))
    for dia in [*range(15, 26), *range(28, 32), "2026-11-01", "2026-11-02"]:  # a grade inteira
        ontem = g[dia - 1 if isinstance(dia, int) and dia > 14 else 31 if dia == "2026-11-01" else "2026-11-01"]
        if ontem.get("manha") is None or ontem["manha"].serie != MH.SERIE or g[dia]["manha"].serie != MH.SERIE:
            continue
        e, e_ontem = edicao(dia), edicao(dia - 1 if isinstance(dia, int) else 31 if dia == "2026-11-01" else "2026-11-01")
        a = next(s for s in e_ontem.stories if s.id == ontem["manha"].story_ids[0])
        b = next(s for s in e.stories if s.id == g[dia]["manha"].story_ids[0])
        assert not MH.mesma_capa(a, b), (dia, a.headline, b.headline)


def test_30_10_numero_da_vespera_nao_volta_como_destaque_de_fato():
    """arte (rodada 2): o "11,3%" do Número de 29/10 (TCU/CBS) não é o número do fato de 30/10."""
    g = _grade()
    tcu = [s for s in fatos(g[30]["manha"]) if "TCU" in s.ctx["frase"]]
    assert tcu and all(s.ctx["valor"] != "11,3%" for s in tcu)


@pytest.mark.parametrize("dia, slide", [(21, 4), (29, 6), (31, 5), ("2026-11-02", 4)])
def test_mais_coberto_que_e_fato_so_aponta_o_slide(dia, slide):
    """arte (rodada 2): o mais coberto que também é fato não se diz duas vezes; a capa aponta o slide."""
    p = manha(dia)
    mc = capa(p).ctx["mais_coberto"]
    assert mc["slide"] == slide and mc["nome"] == ""
    fato = p.slides[slide - 1]
    assert fato.template == "m1_fato" and str(mc["k"]) in fato.ctx["cobertura"] + fato.ctx["valor"]


def test_23_10_mais_coberto_de_mercado_aponta_a_cotacao():
    mc = capa(manha(23)).ctx["mais_coberto"]
    assert mc["nome"] == "Petróleo Brent · cotação no slide 7" and not mc.get("slide")


@pytest.mark.parametrize("dia", [18, 24, 30, 27])
def test_tela_2_dos_stories_com_o_destaque_da_capa(dia):
    """arte (rodada 2): a tela 2 reproduz a capa letra por letra e também o destaque dela, a cobertura também
    ("NOTICIADO POR / 8 VEÍCULOS", 18/10), e não só o número da manchete."""
    g = _grade()[dia]
    c = capa(g["manha"]).ctx
    tela2 = next(s for s in g["stories"].slides if s.template == "st_manchete").ctx
    assert (tela2["heroi"], tela2["qual"], tela2["numero"]) == (c["heroi"], c["qual"], c["valor"])
    assert tela2["numero"], dia
