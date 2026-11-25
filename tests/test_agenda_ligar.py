"""Testes da ligação da Agenda da semana com a mídia (``qijournal/agenda/ligar.py``): só por
identificador ou por apelido aprovado; sigla solta, nome de ministro e nome de parte não ligam."""

from __future__ import annotations

from dataclasses import replace

import pytest

from qijournal.agenda import contrato, ligar, regras
from qijournal.models import Coverage, CoverageOutlet, IndexItem, IndexTopic
from tests.fixtures import agenda as fx
from tests.fixtures.pipeline.factory import make_config, make_edition, make_story

REGRAS = regras.carregar()
DIA = "2026-11-24"
ALVOS = ligar.alvos_da_agenda(fx.congresso(), fx.tribunais())


def _texto(texto: str, *veiculos: str, materia: str = "") -> ligar.Texto:
    return ligar.Texto(texto, veiculos or ("Folha de S.Paulo",), materia=materia, data=DIA)


def _ligados(texto: str, dia: str = DIA) -> list[str]:
    return sorted(ligar.ligar(ALVOS, [_texto(texto)], REGRAS, dia))


# ── alvos ────────────────────────────────────────────────────────────────────────────────────


def test_alvos_saem_das_sessoes_da_fila_e_do_proximo_evento():
    por_id = {a.identificador: a for a in ALVOS}
    assert por_id["PLP 299/2026"].instituicao == contrato.CAMARA
    assert por_id["RE 1326541"] == ligar.Alvo("RE 1326541", contrato.STF, 1218)
    assert por_id["REsp 2034211"].tema == 1254 and por_id["REsp 2034211"].instituicao == contrato.STJ
    assert "MPV 1395/2026" in por_id  # o próximo evento do Congresso, fora da janela de coleta
    assert ligar.alvos_da_agenda(None, None) == ()
    assert len({(a.identificador, a.instituicao) for a in ALVOS}) == len(ALVOS)


# ── acertos ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Câmara aprova o PLP 299/2026 em sessão que entrou pela madrugada", ["PLP 299/2026"]),
        ("Senado pauta o PL 6.461/2019, o Estatuto do Aprendiz", ["PL 6461/2019"]),
        ("Senado pauta o PL nº 6461 de 2019", ["PL 6461/2019"]),
        ("MP 1.381/26 perde a validade nesta sexta", ["MPV 1381/2026"]),
        ("Governo quer votar a MPV 1381/2026", ["MPV 1381/2026"]),
        ("Relator apresenta parecer à PEC 1/2025", ["PEC 1/2025"]),
        # processo por classe e número, com ou sem ponto de milhar
        ("Plenário retoma o RE 1326541 na quarta", ["RE 1326541"]),
        ("Julgamento do RE 1.326.541 é retomado", ["RE 1326541"]),
        ("STF julga a ADI 6.002 nesta quarta", ["ADI 6002"]),
        ("Corte Especial analisa o REsp 2.034.211", ["REsp 2034211"]),
        ("Relator pede pauta para a ADI nº 7717", ["ADI 7717"]),
        # "Tema N" com o tribunal no mesmo texto (os dois recursos do mesmo tema ligam)
        ("Supremo retoma o Tema 1218 da repercussão geral", ["RE 1326541"]),
        ("STF deve concluir Tema 1.324 sobre salário de professores", ["ARE 1502069"]),
        ("STJ define o Tema 1254 dos repetitivos", ["REsp 2034211", "REsp 2034214"]),
        # apelido aprovado, com o órgão no mesmo texto
        ("Câmara aprova pacote fiscal do governo", ["PLP 299/2026"]),
        ("Deputados votam o pacote de ajuste fiscal", ["PLP 299/2026"]),
        ("Congresso discute o Pacote Fiscal", ["PLP 299/2026"]),
    ],
)
def test_liga_por_identificador_ou_apelido_aprovado(texto: str, esperado: list[str]):
    assert _ligados(texto) == esperado


# ── armadilhas ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "texto",
    [
        # "PL" de Partido Liberal
        "PL decide apoiar o governo na votação do orçamento",
        "Deputados do PL deixam a sessão; PL tem 99 deputados",
        "PL 22 lança candidato em 2026",
        # "MP" de Ministério Público
        "MP denuncia ex-prefeito por desvio de verbas",
        "MP de São Paulo abre inquérito em 2026",
        # nome de ministro sozinho
        "Gilmar Mendes pede vista em julgamento sobre professores",
        "Flávio Dino leva ação ao Plenário",
        # nome de parte
        "União recorre de decisão sobre piso do magistério",
        "Estado de São Paulo e Ministério Público Federal travam disputa",
        # palavras da ementa ou do tema, sem identificador
        "Piso nacional do magistério volta ao debate entre prefeitos",
        "Estatuto do Aprendiz divide empresários",
        # "Tema N" sem o tribunal, ou com o tribunal errado
        "Tema 1218 volta à pauta nesta semana",
        "STJ julga o Tema 1218",
        "Supremo analisa o Tema 1254",
        # apelido sem o órgão no mesmo texto
        "Governo defende pacote fiscal em entrevista",
        "Mercado reage ao pacote de ajuste fiscal",
        # número de outro processo, de outra classe ou de outro ano
        "STF julga a ADI 60020",
        "STF julga a ADPF 6002",
        "Câmara vota o PLP 299/2025",
        "Câmara vota o PL 299/2026",
    ],
)
def test_nao_liga(texto: str):
    assert _ligados(texto) == []


def test_apelido_so_vale_dentro_da_validade_e_com_duas_palavras():
    assert _ligados("Câmara aprova pacote fiscal", "2026-12-31") == ["PLP 299/2026"]
    assert _ligados("Câmara aprova pacote fiscal", "2027-01-02") == []
    assert _ligados("Câmara aprova pacote fiscal", "2026-10-31") == []
    # Apelido de uma palavra só, mesmo que alguém o ponha na tabela, não liga.
    uma_palavra = replace(REGRAS, apelidos=(regras.Apelido(item="PLP 299/2026", termos=("pacote",), de="2026-11-01", ate="2026-12-31"),))
    assert ligar.ligar(ALVOS, [_texto("Câmara aprova pacote")], uma_palavra, DIA) == {}


def test_os_apelidos_da_aba_congresso_nao_sao_lidos():
    # config/congresso.yaml tem a sua tabela; a agenda só usa config/agenda.yaml.
    sem_apelidos = replace(REGRAS, apelidos=())
    assert ligar.ligar(ALVOS, [_texto("Câmara aprova pacote fiscal")], sem_apelidos, DIA) == {}


# ── veículos e matérias ──────────────────────────────────────────────────────────────────────


def test_conta_veiculos_distintos_e_guarda_as_materias_da_edicao():
    textos = [
        _texto("Câmara aprova o PLP 299/2026", "Folha de S.Paulo", "Valor Econômico", materia="materia-1"),
        _texto("PLP 299/2026 passa na Câmara", "Valor Econômico"),
        _texto("Deputados aprovam pacote fiscal", "O Globo", materia="materia-2"),
        _texto("Câmara vota PLP 299/2026", "Folha de S.Paulo", materia="materia-1"),
        _texto("Tempo seco no Centro-Oeste", "G1"),
    ]
    ligacao = ligar.ligar(ALVOS, textos, REGRAS, DIA)["PLP 299/2026"]
    assert ligacao.veiculos == ("Folha de S.Paulo", "O Globo", "Valor Econômico")
    assert ligacao.n_veiculos == 3
    assert ligacao.materias == ("materia-1", "materia-2")


def test_feed_do_proprio_orgao_nao_conta_como_imprensa():
    config = make_config()
    oficiais = ligar.veiculos_oficiais_de(config.sources, REGRAS)
    assert {"STF", "STJ", "Agência Câmara", "Agência Senado"} <= set(oficiais)
    assert "Agência Brasil" not in oficiais and "Folha de S.Paulo" not in oficiais
    textos = [
        _texto("STF julga o RE 1326541", "STF", materia="materia-1"),
        _texto("Plenário retoma o RE 1326541", "Agência Câmara", "JOTA"),
    ]
    ligacao = ligar.ligar(ALVOS, textos, REGRAS, DIA, veiculos_oficiais=oficiais)["RE 1326541"]
    assert ligacao.veiculos == ("JOTA",)
    assert ligacao.materias == ("materia-1",)  # a matéria da edição continua ligada
    so_oficial = ligar.ligar(ALVOS, textos[:1], REGRAS, DIA, veiculos_oficiais=oficiais)["RE 1326541"]
    assert so_oficial.n_veiculos == 0 and so_oficial.materias == ("materia-1",)


def test_item_sem_ligacao_nao_entra_no_resultado():
    assert ligar.ligar(ALVOS, [_texto("Chuva forte no Sul")], REGRAS, DIA) == {}
    assert ligar.ligar(ALVOS, [], REGRAS, DIA) == {}
    assert ligar.ligar((), [_texto("Câmara aprova o PLP 299/2026")], REGRAS, DIA) == {}
    assert not ligar.SEM_LIGACAO and ligar.SEM_LIGACAO.n_veiculos == 0


# ── corpus ───────────────────────────────────────────────────────────────────────────────────


def test_textos_da_edicao_do_dia():
    materia = replace(make_story(1, headline="Câmara aprova o PLP 299/2026"), id="plp")
    comparada = Coverage(
        topic="Supremo retoma o Tema 1218",
        conclusion="Veículos divergem sobre o alcance.",
        outlets=[CoverageOutlet(name="JOTA", stance="a", framing="Destaca o piso."), CoverageOutlet(name="Conjur", stance="b", framing="Destaca o RE 1326541.")],
    )
    indice = [
        IndexTopic(section="politica", title="Pacote", story_id="plp", items=[IndexItem(source="O Globo", title="Deputados aprovam pacote fiscal", url="https://x/1")]),
        IndexTopic(section="juridico", title="STJ", items=[IndexItem(source="STJ", title="Corte Especial julga o REsp 2034211", url="https://x/2")]),
    ]
    edicao = replace(
        make_edition(date=DIA),
        stories=[materia, make_story(2)],
        compared=[comparada],
        index=indice,
        wire=[{"title": "MP 1.381/26 caduca na sexta", "source": "Poder360"}],
    )
    textos = ligar.textos_da_edicao(edicao)
    assert [t.materia for t in textos if t.materia] == ["plp", "materia-2", "plp"]
    ligacoes = ligar.ligar(ALVOS, textos, REGRAS, DIA, veiculos_oficiais=("STJ",))
    # a matéria da edição liga a matéria, mas não credita as fontes dela; O Globo citou no título
    assert ligacoes["PLP 299/2026"] == ligar.Ligacao(veiculos=("O Globo",), materias=("plp",))
    # no assunto comparado, só o veículo cujo enquadramento cita o item (a síntese não credita ninguém)
    assert ligacoes["RE 1326541"].veiculos == ("Conjur",) and ligacoes["RE 1326541"].materias == ()
    assert ligacoes["MPV 1381/2026"].veiculos == ("Poder360",)
    assert "REsp 2034211" not in ligacoes  # só o canal do próprio STJ citou: não é imprensa e não há matéria


def test_textos_de_um_arquivo_de_busca():
    dados = {
        "data": "2026-11-23",
        "veiculos": ["Valor Econômico", "Folha de S.Paulo", "STF"],
        "materias": [{"id": "m", "s": 0, "t": "Câmara vota o PLP 299/2026", "l": "", "c": "Texto.", "p": "", "f": [0, 1]}],
        "comparadas": [{"t": "Supremo e o Tema 1218", "c": "", "a": "", "b": "", "f": [[1, "a", "Destaca o RE 1326541", "x"], [0, "b", "foco", "y"]]}],
        "noticias": [[0, -1, 1, "MP 1.381/26 perde a validade", "x", 10], [0, -1, 9, "PL 6461/2019 no Senado", "y", None], "lixo", [1]],
    }
    textos = ligar.textos_da_busca(dados)
    assert len(textos) == 6 and all(t.materia == "" and t.data == "2026-11-23" for t in textos)
    ligacoes = ligar.ligar(ALVOS, textos, REGRAS, DIA, veiculos_oficiais=("STF",))
    assert "PLP 299/2026" not in ligacoes  # só a matéria da edição citou: as fontes dela não contam
    assert ligacoes["RE 1326541"].veiculos == ("Folha de S.Paulo",)  # o enquadramento da Folha cita o RE
    assert ligacoes["MPV 1381/2026"].veiculos == ("Folha de S.Paulo",)
    assert "PL 6461/2019" not in ligacoes  # veículo fora da tabela: o texto não tem a quem ser atribuído


@pytest.mark.parametrize("lixo", [None, [], "texto", 7, {"materias": "x", "noticias": 3, "comparadas": None, "veiculos": "y"}, {"materias": [1, None], "noticias": [[1, 2]]}])
def test_arquivo_de_busca_fora_do_formato_nao_derruba(lixo):
    assert ligar.textos_da_busca(lixo) == []
