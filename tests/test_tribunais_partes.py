"""Partes da aba Tribunais: polo, tipo, grupo de litigante, MEI, autoridade, nomes colados do STF."""

from __future__ import annotations

import json

import pytest

from qijournal.tribunais import partes, stj
from qijournal.tribunais.areas import Classificador
from qijournal.tribunais.modelo import carregar_regras
from qijournal.tribunais.tpu import TPU
from tests.fixtures.tribunais import contas as fx_contas


@pytest.fixture(scope="module")
def cat() -> partes.Catalogo:
    return partes.carregar_catalogo()


@pytest.fixture(scope="module")
def casos() -> dict:
    return json.loads((fx_contas.PASTA / "casos_dificeis_stj.json").read_text(encoding="utf-8"))["casos"]


@pytest.fixture(scope="module")
def leitor() -> stj.LeitorSTJ:
    regras = carregar_regras()
    return stj.LeitorSTJ(regras, Classificador(regras, TPU.carregar()))


def _parte_stj(papel: str, nome: str, cnpj: str | None = None) -> dict:
    return {"descTipoParte": papel, "nomeParte": nome, "numeroCNPJ": cnpj}


def _cnpj(base12: str) -> str:
    """Os 12 primeiros dígitos → o CNPJ com os dígitos verificadores certos."""
    d = [int(c) for c in base12]
    for n in (12, 13):
        pesos = list(range(n - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(a * b for a, b in zip(d, pesos)) % 11
        d.append(0 if resto < 2 else 11 - resto)
    return "".join(map(str, d))


# ── o catálogo ───────────────────────────────────────────────────────────────────────────────


def test_catalogo_carrega_com_familias_por_uf(cat: partes.Catalogo) -> None:
    for gid in ("uniao", "fazenda-nacional", "inss", "estado-sp", "estado-df", "mp-sp", "dpe-rj", "banco-do-brasil", "unimed"):
        assert gid in cat.grupos, gid
    assert cat.litigante("estado-ba").nome == "Estado da Bahia"
    assert cat.litigante("mp-pr").nome == "Ministério Público do Paraná"
    assert cat.litigante("estado-df").nome == "Distrito Federal"
    assert cat.litigante("uniao").dinheiro_publico and not cat.litigante("mp-sp").dinheiro_publico
    assert cat.classes_fora == {"Prc", "RPV"}


def test_raiz_nunca_por_prefixo_solto(cat: partes.Catalogo) -> None:
    # 00394 começa a Fazenda Nacional, o Distrito Federal e o Ministério da Justiça
    assert cat.grupo_da_raiz("00394460") == "fazenda-nacional"
    assert cat.grupo_da_raiz("00394601") == "estado-df"
    assert cat.grupo_da_raiz("00394494") == "uniao"
    assert cat.grupo_da_raiz("00394999") is None
    p = cat.completar_stj(stj.parte_preliminar(_parte_stj("AGRAVADO", "EMPRESA QUALQUER LTDA", _cnpj("003949990001"))))
    assert p["grupo"] == "cnpj-00394999"


def test_catalogo_recusa_raiz_em_dois_grupos() -> None:
    bruto = {"categorias": {"outro_ente": {"tipo": "ente"}, "outra_pj": {"tipo": "pj"}, "municipio": {"tipo": "ente"},
                            "banco": {"tipo": "pj"}},
             "grupos": [{"id": "a", "categoria": "banco", "raizes": ["12345678"]}, {"id": "b", "categoria": "banco", "raizes": ["12345678"]}]}
    with pytest.raises(partes.ErroDoCatalogo):
        partes.Catalogo(bruto)


@pytest.mark.parametrize(("nome", "grupo"), [
    ("UNIÃO", "uniao"),
    ("UNIÃO FEDERAL", "uniao"),
    ("UNIÃO DOS ESCOTEIROS DO BRASIL", None),
    ("INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS", "inss"),
    ("ESTADO DO PARANÁ", "estado-pr"),
    ("ESTADO DO PARÁ", "estado-pa"),
    ("ESTADO DE MATO GROSSO DO SUL", "estado-ms"),
    ("ESTADO DE MATO GROSSO", "estado-mt"),
    ("FAZENDA DO ESTADO DE SÃO PAULO", "estado-sp"),
    ("MINISTÉRIO PÚBLICO DO ESTADO DA PARAÍBA - PGJ", "mp-pb"),
    ("SINDICATO DOS SERVIDORES DO MINISTÉRIO PÚBLICO DO ESTADO DE SÃO PAULO", None),
    ("MUNICÍPIO DE SÃO PAULO", "municipio-sao-paulo"),
    ("BANCO DO BRASIL S/A", "banco-do-brasil"),
    ("BANCO DO BRASIL ADMINISTRADORA DE CONSORCIOS S A", None),
    ("UNIMED DE BELO HORIZONTE COOPERATIVA DE TRABALHO MÉDICO", "unimed"),
    ("CONGRESSO NACIONAL", "congresso-nacional"),
    ("FUNDACAO DOS ECONOMIARIOS FEDERAIS FUNCEF", "funcef"),
    ("UNIÃO DOS ESCOTEIROS", None),
    ("MESA DA ASSEMBLEIA LEGISLATIVA DO ESTADO DE SÃO PAULO", "assembleia-sp"),
])
def test_reconhecer_nome(cat: partes.Catalogo, nome: str, grupo: str | None) -> None:
    assert cat.reconhecer_nome(nome) == grupo


def test_nomes_colados_do_stf(cat: partes.Catalogo) -> None:
    assert [g for g, *_ in cat.reconhecer_texto("UNIÃOFUNDAÇÃO NACIONAL DOS POVOS INDÍGENAS")] == ["uniao"]
    assert [g for g, *_ in cat.reconhecer_texto("ESTADO DE MATO GROSSOMATO GROSSO PREVIDENCIA - MTPREV")] == ["estado-mt"]
    assert [g for g, *_ in cat.reconhecer_texto("PARANAPREVIDENCIAESTADO DO PARANÁ")] == ["estado-pr"]
    assert [g for g, *_ in cat.reconhecer_texto("ESTADO DO PARANÁ, MUNICIPIO DE CURITIBA, ")] == ["estado-pr", "municipio-curitiba"]
    # no meio de um nome (depois de espaço) não vale
    assert cat.reconhecer_texto("SINDICATO DOS SERVIDORES DO MINISTÉRIO PÚBLICO DO ESTADO DE SÃO PAULO") == []


def test_ancora_do_padrao() -> None:
    assert partes.ancora("(?:FAZENDA (?:PUBLICA )?(?:D[OAE] )?)?ESTADO D[OAE] SAO\\ PAULO") == "SAO PAULO"
    assert partes.ancora("BANCO DO BRASIL(?: S ?[/.]? ?A\\.?)?") == "BANCO DO BRASIL"
    assert partes.ancora("INSS") == "INSS"


def test_nome_proprio() -> None:
    assert partes.nome_proprio("BANCO DO ESTADO DO RIO GRANDE DO SUL SA") == "Banco do Estado do Rio Grande do Sul SA"
    assert partes.nome_proprio("BRB BANCO DE BRASILIA SA") == "BRB Banco de Brasilia SA"
    assert partes.nome_proprio("PARTIDO SOCIALISMO E LIBERDADE (P-SOL)") == "Partido Socialismo e Liberdade (P-SOL)"


# ── o STJ ────────────────────────────────────────────────────────────────────────────────────


def test_stj_raiz_e_tipo_do_catalogo(cat: partes.Catalogo) -> None:
    bb = partes.tipificar(_parte_stj("AGRAVANTE", "BANCO DO BRASIL SA", "00000000000191"), "stj", cat)
    assert (bb["polo"], bb["tipo"], bb["grupo"], bb["cnpj_raiz"]) == ("ativo", "pj", "banco-do-brasil", "00000000")
    uniao = partes.tipificar(_parte_stj("RECORRIDO", "UNIÃO", "26994558000123"), "stj", cat)
    assert (uniao["polo"], uniao["tipo"], uniao["grupo"]) == ("passivo", "ente", "uniao")
    mp = partes.tipificar(_parte_stj("INTERESSADO", "MINISTÉRIO PÚBLICO DO ESTADO DO PARANÁ", "78206307000130"), "stj", cat)
    assert (mp["polo"], mp["tipo"], mp["grupo"]) == ("outro", "ente", "mp-pr")


def test_stj_autoridade_e_qualificador_fora(cat: partes.Catalogo) -> None:
    tj = partes.tipificar(_parte_stj("IMPETRADO", "TRIBUNAL DE JUSTIÇA DO ESTADO DO CEARÁ", "09444530000101"), "stj", cat)
    assert (tj["polo"], tj["tipo"], tj["grupo"]) == (None, "autoridade", None)
    mjsp = partes.tipificar(_parte_stj("AUTORIDADE CENTRAL", "MINISTÉRIO DA JUSTIÇA E SEGURANÇA PÚBLICA", "00394494000136"), "stj", cat)
    assert (mjsp["tipo"], mjsp["grupo"]) == ("autoridade", None)
    rep = partes.tipificar(_parte_stj("REPRESENTADO POR", "FULANO DE TAL"), "stj", cat)
    assert (rep["tipo"], rep["nome"], rep["grupo"]) == ("qualificador", None, None)


def test_stj_pessoa_fisica_e_mei_sem_nome(cat: partes.Catalogo) -> None:
    pf = partes.tipificar(_parte_stj("AGRAVANTE", "FULANA DE TAL"), "stj", cat)
    assert (pf["tipo"], pf["nome"], pf["grupo"]) == ("pf", None, None)
    mei = partes.tipificar(_parte_stj("RECORRENTE", "FULANO DE TAL 12345678909", "27548667000180"), "stj", cat)
    assert (mei["tipo"], mei["nome"], mei["grupo"], mei["cnpj_raiz"]) == ("pf", None, None, None)
    paciente = partes.tipificar(_parte_stj("PACIENTE", "BELTRANO"), "stj", cat)
    assert (paciente["polo"], paciente["tipo"], paciente["nome"]) == ("ativo", "pf", None)


def test_stj_casos_dificeis_do_analista(cat: partes.Catalogo, casos: dict, leitor: stj.LeitorSTJ) -> None:
    def proc(caso: str) -> dict:
        return cat.completar(stj.processo_stj(casos[caso], leitor, data_dados="2026-11-20"))

    hc = proc("hc_tribunal_como_impetrado")
    assert partes.litigantes(hc) == {}  # o tribunal impetrado não é litigante; pacientes são gente
    assert all(p["nome"] is None for p in hc["partes"] if p["tipo"] == "pf")
    bb = proc("banco_do_brasil_raiz_00000000")
    assert partes.litigantes(bb)["banco-do-brasil"] == {"ativo"}
    mei = proc("pj_com_cpf_no_nome_mei")
    assert not any(g.startswith("cnpj-27548667") for g in partes.litigantes(mei))
    assert all(p["nome"] is None for p in mei["partes"] if p["tipo"] == "pf")
    prc = proc("precatorio_stj_requisitante")
    stj_req = next(p for p in prc["partes"] if p["papel"] == "REQUISITANTE")
    assert (stj_req["tipo"], stj_req["grupo"]) == ("autoridade", None)
    fam = proc("familia_em_segredo")
    assert fam["segredo"] and partes.litigantes(fam) == {}
    cr = proc("carta_rogatoria_autoridade_central")
    rogante = next(p for p in cr["partes"] if p["papel"] == "JUSTIÇA ROGANTE")
    assert (rogante["polo"], rogante["tipo"], rogante["grupo"]) == (None, "autoridade", None)
    assert partes.litigantes(cr) == {"cnpj-05114682": {"outro"}}  # a única PJ (interessada); o resto é gente
    mpf = proc("no_mpf_vista")
    lits = partes.litigantes(mpf)
    assert lits["uniao"] == {"outro"} and lits["estado-ce"] == {"outro"}
    assert partes.dinheiro_publico(mpf, cat) is False  # interessado não é polo de recurso
    fn = proc("ramo_fora_do_padrao")
    assert partes.dinheiro_publico(fn, cat) is True


def test_tipificar_como_gancho_do_leitor(cat: partes.Catalogo, casos: dict) -> None:
    regras = carregar_regras()
    leitor = stj.LeitorSTJ(regras, Classificador(regras, TPU.carregar()), tipificar=partes.tipificar)
    p = stj.processo_stj(casos["banco_do_brasil_raiz_00000000"], leitor, data_dados="2026-11-20")
    assert any(x["grupo"] == "banco-do-brasil" for x in p["partes"])


# ── o STF ────────────────────────────────────────────────────────────────────────────────────


def _parte_stf(polo: str, nome: str, colado: bool = False) -> dict:
    return {"polo": polo, "papel": f"POLO {polo.upper()}", "nome": nome, "tipo": "desconhecido", "grupo": None,
            "cnpj_raiz": None, "colado": colado}


def test_stf_nome_inteiro(cat: partes.Catalogo) -> None:
    [inss] = cat.partes_stf(_parte_stf("passivo", "INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS"))
    assert (inss["tipo"], inss["grupo"]) == ("ente", "inss")
    [gente] = cat.partes_stf(_parte_stf("ativo", "FULANO BELTRANO DE TAL"))
    assert (gente["tipo"], gente["nome"], gente["grupo"]) == ("pf", None, None)
    [partido] = cat.partes_stf(_parte_stf("ativo", "PARTIDO VERDE"))
    assert (partido["tipo"], partido["grupo"]) == ("pj", "partido-pv")  # partido: grupo do catálogo
    [autoridade] = cat.partes_stf(_parte_stf("passivo", "PRESIDENTE DA REPÚBLICA"))
    assert (autoridade["polo"], autoridade["tipo"], autoridade["grupo"]) == (None, "autoridade", None)


def test_stf_pedaco_colado_vira_varias_partes(cat: partes.Catalogo) -> None:
    saida = cat.partes_stf(_parte_stf("passivo", "UNIÃOFUNDAÇÃO NACIONAL DOS POVOS INDÍGENAS", colado=True))
    assert [(p["tipo"], p["grupo"]) for p in saida] == [("ente", "uniao"), ("desconhecido", None)]
    assert all(p["nome"] is None or p["grupo"] for p in saida)  # o resto colado (pode ser gente) fica sem nome
    gente = cat.partes_stf(_parte_stf("ativo", "FULANO DE TALBELTRANO DE TAL", colado=True))
    assert [(p["tipo"], p["nome"]) for p in gente] == [("desconhecido", None)]


def test_stf_autoridade_da_ata_fica_fora(cat: partes.Catalogo) -> None:
    [coator] = cat.partes_stf({"polo": None, "papel": "COATOR(A/S)(ES)", "nome": "SUPERIOR TRIBUNAL DE JUSTIÇA", "tipo": "autoridade"})
    assert (coator["polo"], coator["tipo"], coator["grupo"]) == (None, "autoridade", None)


def test_cobertura_stf(cat: partes.Catalogo) -> None:
    proc = {"trib": "stf", "segredo": False,
            "partes": [_parte_stf("ativo", "FULANO"), _parte_stf("passivo", "UNIÃOFUNDAÇÃO NACIONAL DOS POVOS INDÍGENAS", True)],
            "polos": {"ativo": {"texto": "FULANO"}, "passivo": {"texto": "UNIÃOFUNDAÇÃO NACIONAL DOS POVOS INDÍGENAS, "}}}
    cob = partes.CoberturaSTF()
    cob.somar_colados(proc["partes"], cat)
    cat.completar(proc)
    cob.somar(proc, cat)
    cob.somar({"trib": "stf", "segredo": True, "partes": []}, cat)
    m = cob.para_meta()
    assert m["processos_sem_segredo"] == 1
    assert m["polos"]["passivo"] == {"com_partes": 1, "reconhecido": 1, "no_texto_bruto": 1, "agrupado": 1}
    assert m["polos"]["ativo"]["reconhecido"] == 0
    assert m["colados"] == {"pedacos": 1, "com_entidade": 1}


def test_nome_de_grupo_feito_na_hora(cat: partes.Catalogo) -> None:
    c = partes.carregar_catalogo()
    for _ in range(3):
        c.completar_stj(stj.parte_preliminar(_parte_stj("AGRAVADO", "GALERA MARI E ADVOGADOS ASSOCIADOS", _cnpj("002905720001"))))
    lit = c.litigante("cnpj-00290572")
    assert lit is not None and lit.nome == "Galera Mari e Advogados Associados" and lit.tipo == "pj" and not lit.catalogo


# ── a revisão de privacidade e de tipificação (23/11/2026) ──────────────────────────────────


@pytest.mark.parametrize("nome", ["FULANA DE JESUS CAMARA", "FULANO DE SA", "FULANO DE TAL SA", "FULANO BRASILEIRO SANTOS",
                                  "FULANO DE TAL - ME", "W. DE A. CÂMARA"])
def test_stf_sobrenome_nao_e_instituicao(nome: str) -> None:
    """PRIV-1: Câmara, Sá e Brasileiro são sobrenomes; "- ME" é a firma individual (a própria pessoa):
    pessoa física, sem nome, nem em memória."""
    c = partes.carregar_catalogo()
    assert c._resolver_stf(nome, False) == [("pf", None, None)]
    c.partes_stf(_parte_stf("ativo", nome))
    assert c.nomes_vistos == {}


@pytest.mark.parametrize(("nome", "tipo"), [("CAMARA MUNICIPAL DE SAO PAULO", "ente"), ("USINA ACUCAREIRA X S A", "pj"), ("X S.A.", "pj"),
                                            ("ASSOC BRASILEIRA DE X", "pj"), ("FULANO CAMARA DE VEREADORES DE Y", "ente")])
def test_stf_instituicao_continua_instituicao(nome: str, tipo: str) -> None:
    """PRIV-1, os contra-testes: as marcas de instituição que ficaram não regridem."""
    assert partes.carregar_catalogo().tipo_pelo_nome(nome) == tipo


@pytest.mark.parametrize("nome", ["11.222.333 FULANA DE TAL", "11222333 FULANA DE TAL", "FULANO DE TAL - ME", "FULANO DE TAL ME", "FULANO DE TAL MEI"])
def test_stj_mei_no_formato_atual_e_empresario_individual(nome: str) -> None:
    """PRIV-2: o MEI de hoje traz a raiz do CNPJ antes do nome; o empresário individual, o sufixo
    ME: é gente, pessoa física, sem nome e sem raiz."""
    parte = stj.parte_preliminar(_parte_stj("RECORRENTE", nome, "11222333000181"))
    assert (parte["tipo"], parte["nome"], parte["cnpj_raiz"]) == ("pf", None, None)


@pytest.mark.parametrize("nome", ["11.222.333 LTDA", "X COMERCIO LTDA - ME"])
def test_stj_empresa_com_raiz_ou_me_continua_empresa(nome: str) -> None:
    parte = stj.parte_preliminar(_parte_stj("RECORRENTE", nome, "11222333000181"))
    assert parte["tipo"] == "pj" and parte["cnpj_raiz"] == "11222333"


def test_stj_leitor_nunca_guarda_nome_de_mei(leitor: stj.LeitorSTJ) -> None:
    """PRIV-2: o cache por trio do leitor nunca tem nome de MEI como chave."""
    brutas = [_parte_stj("RECORRENTE", "11.222.333 FULANA DE TAL", "11222333000181"), _parte_stj("RECORRIDO", "EMPRESA X LTDA", _cnpj("112223330001"))]
    leitor.partes(brutas)
    assert not any("FULANA" in str(k) for k in leitor._pj)


@pytest.mark.parametrize(("nome", "tipo", "grupo"), [
    ("SOLIDARIEDADE", "pj", "partido-solidariedade"), ("REPUBLICANOS - NACIONAL", "pj", "partido-republicanos"),
    ("PODEMOS - SAO PAULO - SP - MUNICIPAL", "pj", "partido-podemos"), ("UNIÃO BRASIL", "pj", "partido-uniao-brasil"),
])
def test_stf_partido_de_uma_palavra(cat: partes.Catalogo, nome: str, tipo: str, grupo: str) -> None:
    """PRIV-4: partido de uma palavra não é pessoa física (a ADI do Solidariedade saía "pessoa
    física"); "União Brasil" nunca é a União."""
    [p] = cat.partes_stf(_parte_stf("ativo", nome))
    assert (p["tipo"], p["grupo"]) == (tipo, grupo)


def test_juizados_sao_autoridade_e_sindicato_e_privado(cat: partes.Catalogo) -> None:
    """PRIV-4 e J9: o juizado é órgão julgador; o sindicato com "Ministério Público" no nome é pessoa
    jurídica de direito privado; a OAB é a mesma coisa no Conselho Federal e nas seccionais."""
    assert cat.tipo_pelo_nome("JUIZADOS ESPECIAIS FEDERAIS DA SEÇÃO JUDICIÁRIA DO RIO DE JANEIRO") == "autoridade"
    assert cat.tipo_pelo_nome("TURMAS RECURSAIS DO ESTADO X") == "autoridade"
    sind = stj.parte_preliminar(_parte_stj("RECORRENTE", "SINDICATO DOS SERVIDORES DO PODER JUDICIARIO E DO MINISTERIO PUBLICO DA UNIAO NO DISTRITO FEDERAL - SINDJUS/DF",
                                           _cnpj("264467810001")))
    assert sind["tipo"] == "pj"
    assert cat.tipo_pelo_nome("SINDICATO DOS SERVIDORES DO MINISTERIO PUBLICO") == "pj"
    oab_cf = stj.parte_preliminar(_parte_stj("RECORRENTE", "ORDEM DOS ADVOGADOS DO BRASIL CONSELHO FEDERAL", _cnpj("330002640001")))
    oab_sc = stj.parte_preliminar(_parte_stj("RECORRENTE", "ORDEM DOS ADVOGADOS DO BRASIL SANTA CATARINA", _cnpj("829141200001")))
    assert oab_cf["tipo"] == oab_sc["tipo"] == "pj"
    # o MP no começo continua ente; depois de " - " também
    assert stj.parte_preliminar(_parte_stj("RECORRIDO", "MINISTERIO PUBLICO DO ESTADO DE SAO PAULO", _cnpj("012235010001")))["tipo"] == "ente"
    assert stj.parte_preliminar(_parte_stj("RECORRIDO", "ESTADO X - PROCURADORIA GERAL", None))["tipo"] == "ente"


def test_saida_sem_nome_de_gente() -> None:
    """PRIV-9: pessoa física (sobrenome Câmara, Sá), MEI com a raiz no começo e empresário individual,
    todos acima dos limiares (5 no ministro, 20 no tribunal): nenhum nome sai no painel, nos dossiês
    nem fica no catálogo."""
    from qijournal.tribunais import contas
    from tests.test_tribunais_contas import DATA, HOJE, T4, proc

    gente = ["FULANA DE JESUS CAMARA", "FULANO DE SA", "11.222.333 FULANA DE TAL", "FULANO DE TAL - ME"]
    lote, i = [], 0
    regras = carregar_regras()
    for nome in gente:
        for j in range(25):
            i += 1
            bruta = stj.parte_preliminar(_parte_stj("RECORRENTE", nome, "11222333000181" if nome[0].isdigit() else None))
            lote.append(proc(i, T4[j % 4], partes_=(bruta,)))
    c = partes.carregar_catalogo()
    r = contas.montar("stj", lote, regras, data_dados=DATA, hoje=HOJE, catalogo=c)
    texto = json.dumps(r.painel, ensure_ascii=False) + json.dumps(r.dossies, ensure_ascii=False)
    for nome in ("CAMARA", "Camara", "Câmara", "FULANO", "Fulano", "FULANA", "Fulana", "DE SA", "De Sa"):
        assert nome not in texto, nome
    assert c.nomes_vistos == {}


@pytest.mark.parametrize(("colado", "esperado"), [
    ("MUNICÍPIO DE MINEIROSMUNICÍPIO DE TAUBATÉ", [("ente", "municipio-mineiros"), ("ente", "municipio-taubate")]),
    ("MUNICIPIO DE MAGEMUNICIPIO DE GUAPIMIRIM", [("ente", "municipio-mage"), ("ente", "municipio-guapimirim")]),
    ("MUNICIPIO DE JACAREACANGAESTADO DO PARÁ", [("ente", "municipio-jacareacanga"), ("ente", "estado-pa")]),
    ("MUNICIPIO DE CRUZ ALTAUNIÃO", [("ente", "municipio-cruz-alta"), ("ente", "uniao")]),
    ("MUNICIPIO DE SAO JOAO DE MERITICAIXA ECONOMICA FEDERAL", [("ente", "municipio-sao-joao-de-meriti"), ("pj", "caixa")]),
    ("MUNICIPIO DE IBIPORAINSTITUTO DE PREVIDENCIA DE IBIPORA", [("ente", "municipio-ibipora"), ("desconhecido", None)]),
    ("MUNICIPIO DE SAO BERNARDO DO CAMPOPREFEITO DO MUNICÍPIO DE SÃO BERNARDO DO CAMPO",
     [("ente", "municipio-sao-bernardo-do-campo"), ("desconhecido", None)]),
    ("UNIÃOMUNICÍPIO DE TAUBATÉ", [("ente", "uniao"), ("ente", "municipio-taubate")]),  # o município no fim vai até o fim
    ("FULANO DE TALMUNICIPIO DE CANOAS", [("ente", "municipio-canoas"), ("desconhecido", None)]),
    # sem emenda à vista, o nome do município levaria a pessoa colada atrás dele: sem grupo, sem nome
    ("MUNICIPIO DE EUNAPOLISFULANA BELTRANA DE TAL", [("desconhecido", None)]),
    ("MUNICÍPIO DE ARACAJUFULANO DOS SANTOS", [("desconhecido", None)]),
])
def test_stf_municipio_colado_corta_na_emenda(colado: str, esperado: list) -> None:
    """PRIV-9: o STF cola a parte seguinte na última letra do município (``P1P2``). O nome do
    município acaba na emenda com outra entidade ou instituição; sem emenda, o município do começo
    não ganha grupo (o nome levaria a parte colada, às vezes uma pessoa)."""
    c = partes.carregar_catalogo()
    saida = c.partes_stf(_parte_stf("passivo", colado, colado=True))
    assert [(p["tipo"], p["grupo"]) for p in saida] == esperado
    assert all(p["nome"] is None or p["grupo"] for p in saida)
    nomes = " ".join(c.litigante(p["grupo"]).nome for p in saida if p["grupo"]) + " " + " ".join(c.grupos)
    for pedaco in ("FULAN", "Fulan", "BELTRAN", "Beltran", "Santos", "SANTOS", "municipio-de", "Municipio de", "Prefeito", "Instituto"):
        assert pedaco not in nomes, pedaco


def test_municipio_sozinho_continua_municipio(cat: partes.Catalogo) -> None:
    """Fora da cabeça colada, o município vai até o fim, como sempre (inclusive "União da Vitória")."""
    assert cat.reconhecer_nome("MUNICIPIO DE UNIAO DA VITORIA") == "municipio-uniao-da-vitoria"
    assert [g for g, *_ in cat.reconhecer_texto("MUNICÍPIO DE SÃO PAULO")] == ["municipio-sao-paulo"]
    assert [p["grupo"] for p in cat.partes_stf(_parte_stf("ativo", "MUNICÍPIO DE SÃO PAULO"))] == ["municipio-sao-paulo"]
    assert [g for g, *_ in cat.reconhecer_texto("MUNICIPIO DE SAO PAULO - SPFULANO", colado=True)] == ["municipio-sao-paulo"]


def _nomes_publicados(dado, chave: str | None = None):
    """Cada litigante com nome num JSON publicado: ``(id, nome, tipo)``, das listas (``{"id", "nome",
    "tipo"}``) e dos mapas (``{id: {"nome", "tipo"}}``)."""
    if isinstance(dado, dict):
        if "nome" in dado and "tipo" in dado:
            yield str(dado.get("id") or chave), dado["nome"], dado["tipo"]
        for k, v in dado.items():
            yield from _nomes_publicados(v, k)
    elif isinstance(dado, list):
        for v in dado:
            yield from _nomes_publicados(v, chave)


def test_a_pasta_publicada_so_tem_nome_de_instituicao() -> None:
    """PRIV-9 na saída real (``data/tribunais/*.json`` e ``ministros/*.json``): todo litigante com nome
    é um grupo do catálogo, ``cnpj-<raiz>``, ``nome-<slug>`` ou ``municipio-<slug>``, do tipo PJ ou
    ente; o de CNPJ não é firma individual nem traz CPF; o feito só do nome (``nome-``,
    ``municipio-``) tem marca de instituição (``tipo_pelo_nome`` diferente de ``pf``); e o nome de
    município não traz outra parte colada (relido, dá ele mesmo e só ele)."""
    import re
    from pathlib import Path

    pasta = Path(__file__).resolve().parents[1] / "data" / "tribunais"
    arquivos = sorted(pasta.glob("*.json")) + sorted((pasta / "ministros").glob("*.json"))
    if not arquivos:
        pytest.skip("sem a pasta publicada")
    c = partes.carregar_catalogo()
    fixos = set(c.grupos)
    vistos: dict[str, str] = {}
    for arq in arquivos:
        for gid, nome, tipo in _nomes_publicados(json.loads(arq.read_text(encoding="utf-8"))):
            if tipo not in ("pj", "ente", "pf", "autoridade", "desconhecido"):
                continue  # outro dicionário com "nome" e "tipo" (não é litigante)
            onde = f"{arq.name}: {gid} {nome!r}"
            assert tipo in ("pj", "ente"), onde
            assert gid in fixos or re.fullmatch(r"cnpj-\d{8}|nome-[a-z0-9-]+|municipio-[a-z0-9-]+", gid), onde
            if gid in fixos:
                continue  # nome escrito à mão no catálogo ("Hapvida", "SPPrev")
            if gid.startswith("cnpj-"):
                # CNPJ válido é pessoa jurídica, salvo a firma individual com nome de gente (MEI,
                # empresário individual) e o CPF no fim do nome: a mesma régua de stj.parte_preliminar.
                # O nome sozinho não basta aqui ("Escritório Central de Arrecadação e Distribuição Ecad").
                chave = stj.chave_rapida(nome)
                assert not stj.firma_individual(chave, marca=partes._marca_instituicao), onde
                assert not stj._CPF_NO_FIM.search(nome), onde
                continue
            assert c.tipo_pelo_nome(nome) != "pf", onde  # sem CNPJ: só com marca de instituição
            if gid.startswith("municipio-"):
                assert [g for g, *_ in c.reconhecer_texto(nome)] == [gid], onde
            vistos[gid] = nome
    assert vistos  # a varredura leu alguma coisa
