"""As escolhas da Agenda da semana (config/agenda.yaml, lidas por qijournal/agenda/regras.py): a
régua em três partes, os cortes, os tetos, o dicionário de situações, os apelidos e a leitura
tolerante; e o liga/desliga da agenda por marca e por variável (config/site.yaml, QIJ_NO_AGENDA)."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from qijournal.agenda import contrato, regras
from qijournal.agenda.regras import Regras
from qijournal.config import ROOT, AgendaConfig, load_config

ARQUIVO = ROOT / "config" / "agenda.yaml"


@pytest.fixture(scope="module")
def bruto() -> dict[str, Any]:
    return yaml.safe_load(ARQUIVO.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def do_arquivo() -> Regras:
    return regras.carregar()


def _com(bruto: dict[str, Any], **mudancas: Any) -> Regras:
    dado = copy.deepcopy(bruto)
    dado.update(mudancas)
    return regras.de_dict(dado)


# ── o arquivo de verdade ─────────────────────────────────────────────────────────────────────


def test_o_arquivo_carrega_sem_aviso_e_bate_com_os_padroes(do_arquivo: Regras) -> None:
    """A régua, os cortes, os tetos, as comissões, o frescor e os feeds do arquivo são os mesmos
    do código: sem o arquivo, a agenda decide igual."""
    assert do_arquivo.avisos == ()
    padrao = regras.PADRAO
    assert do_arquivo.regua == padrao.regua and do_arquivo.cortes == padrao.cortes and do_arquivo.tetos == padrao.tetos
    assert do_arquivo.comissoes_que_decidem == padrao.comissoes_que_decidem
    assert do_arquivo.frescor == padrao.frescor and do_arquivo.feeds_oficiais == padrao.feeds_oficiais


def test_a_regua_tem_tres_partes_e_soma_ate_85(do_arquivo: Regras, bruto: dict[str, Any]) -> None:
    regua = do_arquivo.regua
    assert set(bruto["regua"]) == {"foro", "alcance", "estagio"}  # a mídia não pontua: não há quarta parte
    assert [nome for nome, _ in regua.foro] == list(contrato.FOROS)
    assert [nome for nome, _ in regua.alcance] == list(contrato.ALCANCES)
    assert [nome for nome, _ in regua.estagio] == list(contrato.ESTAGIOS)
    assert max(p for _, p in regua.foro) == regras.TETO_FORO == 30
    assert max(p for _, p in regua.alcance) == regras.TETO_ALCANCE == 30
    assert max(p for _, p in regua.estagio) == regras.TETO_ESTAGIO == 25
    assert regua.maximo == regras.NOTA_MAXIMA == 85
    assert all(isinstance(p, int) and p >= 0 for parte in (regua.foro, regua.alcance, regua.estagio) for _, p in parte)
    assert (do_arquivo.cortes.home, do_arquivo.cortes.pagina) == (60, 40)


def test_os_pesos_sao_os_da_regra_de_selecao(do_arquivo: Regras) -> None:
    regua = do_arquivo.regua
    assert dict(regua.foro) == {"plenario": 30, "corte_especial": 25, "secao": 20, "comissao_decide": 15, "prazo": 15, "turma": 10, "comissao": 5}
    alcance = dict(regua.alcance)
    cheios = ("repercussao_geral", "repetitivo", "controle_federal", "pec", "plp", "mpv", "veto", "orcamento", "indicacao")
    assert all(alcance[a] == 30 for a in cheios)
    assert alcance["pl"] == 18 and alcance["pdl"] == 9 and alcance["individual"] == 5 and alcance["outro"] == 0
    assert all(alcance[a] == 15 for a in ("controle_estadual", "acao_originaria", "embargos_de_divergencia", "afetacao"))
    assert dict(regua.estagio) == {"em_votacao": 25, "prazo_vence": 25, "retorno_de_vista": 20, "referendo_de_cautelar": 15, "destaque": 15,
                                   "urgencia": 15, "pauta_confirmada": 15, "pautado": 10, "pauta_nao_publicada": 0}


@pytest.mark.parametrize(("foro", "alcance", "estagios", "nota"), [
    ("plenario", "repercussao_geral", ("retorno_de_vista",), 80),  # Temas 1218 e 1324 no Plenário do STF
    ("plenario", "controle_federal", ("destaque",), 75),  # ADI 6002
    ("plenario", "controle_federal", ("retorno_de_vista",), 80),  # ADI 7717
    ("plenario", "controle_estadual", ("referendo_de_cautelar", "destaque"), 60),  # ADI 7807
    ("prazo", "mpv", ("prazo_vence",), 70),  # MPV 1381/2026
    ("plenario", "pl", ("pauta_confirmada",), 63),  # projeto de lei no Plenário do Senado
    ("comissao_decide", "pec", ("pautado",), 55),  # PEC na CCJ do Senado: só na página completa
    ("corte_especial", "afetacao", ("pautado",), 50),  # o melhor caso do STJ na semana
    ("corte_especial", "repetitivo", ("pautado",), 65),  # recurso repetitivo na Corte Especial
    ("plenario", "plp", ("em_votacao", "urgencia"), 85),  # PLP 299/2026 em votação: o teto
    ("comissao", "outro", ("pauta_nao_publicada",), 5),
    ("foro_novo", "alcance_novo", ("estagio_novo",), 0),  # palavra desconhecida não soma
])
def test_a_conta_da_nota_fecha_nos_casos_conferidos(do_arquivo: Regras, foro: str, alcance: str, estagios: tuple[str, ...], nota: int) -> None:
    regua = do_arquivo.regua
    assert regua.pontos_foro(foro) + regua.pontos_alcance(alcance) + regua.pontos_estagio(*estagios) == nota


def test_tetos_comissoes_e_frescor_do_arquivo(do_arquivo: Regras) -> None:
    home = do_arquivo.tetos.home
    assert (home.linhas, home.itens_por_linha, home.nome_ate, home.resultados, home.fila_nomeados, home.altura_px) == (8, 2, 90, 2, 2, 520)
    assert (home.celular_linhas_hoje_amanha, home.celular_itens_por_linha, home.celular_nome_ate) == (4, 1, 60)
    assert (home.celular_linhas_de_texto, home.celular_altura_px) == (3, 600)
    assert (do_arquivo.tetos.pagina.linhas, do_arquivo.tetos.pagina.bytes) == (60, 250_000)
    assert (do_arquivo.tetos.arquivos.congresso_bytes, do_arquivo.tetos.arquivos.tribunais_bytes) == (150_000, 60_000)
    assert dict(do_arquivo.comissoes_que_decidem) == {"camara": ("CCJC", "CFT"), "senado": ("CCJ", "CAE"), "congresso": ("CMO",)}
    assert do_arquivo.comissao_decide("camara", "CCJC") and do_arquivo.comissao_decide("senado", " ccj ")
    assert do_arquivo.comissao_decide("congresso", "CMO")
    assert not do_arquivo.comissao_decide("senado", "CAS") and not do_arquivo.comissao_decide("camara", "CCJ")
    assert not do_arquivo.comissao_decide("camara", None) and not do_arquivo.comissao_decide("stf", "CCJC")
    assert (do_arquivo.frescor.aviso_dias_uteis, do_arquivo.frescor.some_dias) == (2, 4)


@pytest.mark.parametrize(("data_dados", "hoje", "velho_dias", "esperado"), [
    ("2026-11-23", "2026-11-23", None, "ok"),
    ("2026-11-23", "2026-11-24", None, "ok"),  # um dia útil
    ("2026-11-20", "2026-11-23", None, "ok"),  # sexta lida na segunda: o STJ não publica no fim de semana
    ("2026-11-20", "2026-11-24", None, "aviso"),  # dois dias úteis: "dados de 20/11, pode ter mudado"
    ("2026-11-23", "2026-11-25", None, "aviso"),
    ("2026-11-23", "2026-11-27", None, "aviso"),  # 4 dias corridos: ainda aparece
    ("2026-11-23", "2026-11-28", None, "some"),  # 5 dias corridos: os itens da fonte somem
    ("2026-11-19", "2026-11-26", 8, "aviso"),  # a fonte traz o seu limite (velho_dias de data/tribunais/meta.json)
    ("2026-11-19", "2026-11-28", 8, "some"),
    (None, "2026-11-23", None, "some"),  # sem data não dá para dizer que está em dia
    ("ontem", "2026-11-23", None, "some"),
])
def test_frescor_de(do_arquivo: Regras, data_dados: str | None, hoje: str, velho_dias: int | None, esperado: str) -> None:
    assert do_arquivo.frescor_de(data_dados, hoje, velho_dias) == esperado


# ── dicionário de situações ──────────────────────────────────────────────────────────────────


def test_o_dicionario_cobre_o_que_precisa_de_traducao(do_arquivo: Regras) -> None:
    chaves = {s.chave for s in do_arquivo.situacoes}
    assert 12 <= len(chaves) <= 18
    assert {"retorno_de_vista", "destaque", "referendo_de_cautelar", "aprovado_ressalvados_destaques", "prazo_mpv", "dia_vazio",
            "resultado_tribunal", "como_pautado", "pauta_camara_nao_publicada", "fila"} <= chaves
    conhecidas = {*contrato.ESTAGIOS, *contrato.PALAVRAS_RESULTADO, *contrato.SITUACOES_SESSAO, *regras.SITUACOES_ESPECIAIS}
    assert chaves <= conhecidas
    assert do_arquivo.situacao("retorno_de_vista", "Gilmar Mendes") == "volta ao julgamento depois do pedido de vista de Gilmar Mendes"
    assert do_arquivo.situacao("retorno_de_vista") == "volta ao julgamento depois de um pedido de vista"
    assert do_arquivo.situacao("destaque", "Edson Fachin").endswith("a pedido de Edson Fachin")
    assert do_arquivo.situacao("aprovado_ressalvados_destaques") == "texto principal aprovado; faltam os trechos votados em separado"
    assert do_arquivo.situacao("referendo_de_cautelar", "qualquer") == "o Plenário confirma ou derruba a decisão provisória"
    assert do_arquivo.situacao("prazo_mpv") == "Medida provisória perde a validade"
    assert do_arquivo.situacao("dia_vazio") == "nada de peso pautado"
    assert do_arquivo.situacao("resultado_tribunal") == "resultado no andamento oficial"
    assert do_arquivo.situacao("como_pautado", data="23/11") == "como pautado em 23/11"
    assert do_arquivo.situacao("pauta_camara_nao_publicada") == "pauta do Plenário ainda não publicada pela Câmara"


def test_sem_entrada_no_dicionario_vale_o_termo_oficial(do_arquivo: Regras) -> None:
    assert do_arquivo.situacao("pautado") == "pautado"
    assert do_arquivo.situacao("pauta_confirmada") == "pauta confirmada"
    assert do_arquivo.situacao("em_andamento") == "em andamento"
    assert do_arquivo.situacao("rejeitado") == "rejeitado"
    assert regras.PADRAO.situacao("retorno_de_vista", "Gilmar Mendes") == "retorno de vista"  # sem o arquivo: o termo oficial
    for palavra in (*contrato.ESTAGIOS, *contrato.PALAVRAS_RESULTADO, *contrato.SITUACOES_SESSAO):
        assert do_arquivo.situacao(palavra) and "_" not in do_arquivo.situacao(palavra)


def test_nenhum_texto_de_tela_do_arquivo_preve_adjetiva_ou_usa_travessao(do_arquivo: Regras) -> None:
    textos = [t for s in do_arquivo.situacoes for t in (s.texto, s.com_detalhe) if t]
    textos += [palavra for _, palavra in do_arquivo.palavras_de_area] + [nome for _, nome in do_arquivo.nomes_aprovados]
    textos += [t for a in do_arquivo.apelidos for t in a.termos]
    assert len(textos) > 30
    for texto in textos:
        baixo = texto.lower()
        assert not any(proibida in baixo for proibida in regras.EXPRESSOES_PROIBIDAS), texto
        assert not any(palavra in baixo for palavra in ("chance", "provável", "provavel", "deve ser", "tende a", "prioridade")), texto
        assert texto == texto.strip() and "  " not in texto and len(texto) <= 90, texto


def test_entrada_ruim_do_dicionario_e_ignorada_com_aviso(bruto: dict[str, Any]) -> None:
    lidas = _com(bruto, situacoes={
        "retorno_de_vista": {"texto": "será julgado de novo"},  # previsão
        "destaque": {"texto": "pauta-bomba no Plenário"},  # adjetivo
        "urgencia": {"texto": "pautado com urgência — sem comissão"},  # travessão
        "prejudicado": {"texto": "não foi a voto {motivo}"},  # marcador desconhecido
        "pedido_de_vista": {"com_detalhe": "pedido de {detalhe}"},  # falta o texto
        "apelido_novo": {"texto": "qualquer coisa"},  # situação que não existe
        "retirado_de_pauta": "saiu da pauta",  # a forma curta vale
        "nao_apreciado": {"texto": "não chegou a ser votado", "com_detalhe": "vai votar depois de {detalhe}"},  # só o detalhe cai
        "adiado": 5,
    })
    assert {s.chave: (s.texto, s.com_detalhe) for s in lidas.situacoes} == {
        "retirado_de_pauta": ("saiu da pauta", ""), "nao_apreciado": ("não chegou a ser votado", "")}
    assert len(lidas.avisos) == 8 and all(a.startswith("situacoes.") for a in lidas.avisos)
    assert lidas.situacao("retorno_de_vista", "Gilmar Mendes") == "retorno de vista"  # cai no termo oficial
    assert lidas.situacao("nao_apreciado", "alguém") == "não chegou a ser votado"


# ── palavras de área, nomes e apelidos ───────────────────────────────────────────────────────


def test_palavras_de_area_so_para_classificacao_oficial(do_arquivo: Regras) -> None:
    assert do_arquivo.palavra_de_area("economia") == "Economia" and do_arquivo.palavra_de_area("tributario") == "Tributos"
    assert do_arquivo.palavra_de_area("saude") == "Saúde" and do_arquivo.palavra_de_area("administrativo") == "Setor público"
    for sem_palavra in ("constitucional", "processual", "outros", "sem_assunto", "inexistente", "", None):
        assert do_arquivo.palavra_de_area(sem_palavra) is None
    temas_do_congresso = {t["id"] for t in yaml.safe_load((ROOT / "config" / "congresso.yaml").read_text(encoding="utf-8"))["temas"]}
    areas_dos_tribunais = {a["id"] for a in yaml.safe_load((ROOT / "config" / "tribunais.yaml").read_text(encoding="utf-8"))["areas"]}
    assert {chave for chave, _ in do_arquivo.palavras_de_area} <= temas_do_congresso | areas_dos_tribunais  # só ids que existem
    assert regras.PADRAO.palavra_de_area("economia") is None  # sem o arquivo, sem palavra


def test_nomes_aprovados_comecam_vazios_e_so_entram_por_pessoa(do_arquivo: Regras, bruto: dict[str, Any]) -> None:
    assert do_arquivo.nomes_aprovados == () and do_arquivo.nome_aprovado("PL 5807/2025") is None
    com_nome = _com(bruto, nomes_aprovados={
        "PL  5807/2025": "Falsificação de alimentos, bebidas e suplementos", "PL 1/2026": "projeto polêmico", "PL 2/2026": ""})
    assert com_nome.nome_aprovado("PL 5807/2025") == "Falsificação de alimentos, bebidas e suplementos"
    assert com_nome.nome_aprovado("PL 1/2026") is None and com_nome.nome_aprovado("PL 2/2026") is None
    assert len(com_nome.avisos) == 1 and "nomes_aprovados.PL 1/2026" in com_nome.avisos[0]


def test_o_apelido_inicial_e_o_do_plp_299_com_validade(do_arquivo: Regras) -> None:
    assert len(do_arquivo.apelidos) == 1
    apelido = do_arquivo.apelidos[0]
    assert (apelido.item, apelido.termos) == ("PLP 299/2026", ("pacote fiscal", "pacote de ajuste fiscal"))
    assert (apelido.de, apelido.ate) == ("2026-11-01", "2026-12-31")
    assert do_arquivo.apelidos_de("PLP 299/2026", "2026-11-24") == ("pacote fiscal", "pacote de ajuste fiscal")
    assert do_arquivo.apelidos_de("PLP 299/2026", "2026-11-01") and do_arquivo.apelidos_de("PLP 299/2026", "2026-12-31")  # as pontas valem
    assert do_arquivo.apelidos_de("PLP 299/2026", "2026-10-31") == () and do_arquivo.apelidos_de("PLP 299/2026", "2027-01-01") == ()
    assert do_arquivo.apelidos_de("PL 299/2026", "2026-11-24") == ()
    assert do_arquivo.apelidos_em("2026-11-24") == {"pacote fiscal": "PLP 299/2026", "pacote de ajuste fiscal": "PLP 299/2026"}
    assert do_arquivo.apelidos_em("2027-02-01") == {}
    comentario = ARQUIVO.read_text(encoding="utf-8").split("apelidos:")[-1].split("- item:")[0]
    assert "SUGESTÃO A SER CONFIRMADA PELO DONO" in comentario
    assert all(len(t.split()) >= regras.APELIDO_MINIMO_DE_PALAVRAS for a in do_arquivo.apelidos for t in a.termos)


def test_apelido_invalido_e_ignorado_com_aviso(bruto: dict[str, Any]) -> None:
    import datetime

    lidas = _com(bruto, apelidos=[
        # "pacote" sozinho não liga; o termo repetido entra uma vez; a data sem aspas chega do YAML como data
        {"item": "PLP 299/2026", "termos": ["pacote", "pacote fiscal", "pacote fiscal"], "de": datetime.date(2026, 11, 1), "ate": "2026-12-31"},
        {"item": "PEC 1/2025", "termos": ["fundo do DF"], "de": "2026-12-31", "ate": "2026-11-01"},  # validade invertida
        {"item": "PL 5/2026", "termos": ["PL"], "de": "2026-11-01", "ate": "2026-12-31"},  # sigla solta
        {"item": "", "termos": ["sem item nenhum"], "de": "2026-11-01", "ate": "2026-12-31"},
        {"item": "PL 6/2026", "termos": ["projeto do fim"], "de": "novembro", "ate": "2026-12-31"},
        {"item": "PL 7/2026", "termos": ["pauta-bomba fiscal"], "de": "2026-11-01", "ate": "2026-12-31"},
        "PL 8/2026",
        None,
    ])
    assert [(a.item, a.termos) for a in lidas.apelidos] == [("PLP 299/2026", ("pacote fiscal",))]
    assert lidas.apelidos[0].de == "2026-11-01"  # data sem aspas no YAML também vale
    assert len(lidas.avisos) >= 7
    assert _com(bruto, apelidos={"PLP 299/2026": ["pacote fiscal"]}).apelidos == ()  # precisa ser uma lista


def test_feeds_oficiais_existem_em_sources_e_nao_contam_como_imprensa(do_arquivo: Regras) -> None:
    feeds = {f["id"] for f in yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))["feeds"]}
    assert set(do_arquivo.feeds_oficiais) <= feeds
    assert {"stf", "stj", "camara", "senado"} <= set(do_arquivo.feeds_oficiais)
    assert do_arquivo.feed_oficial("stj") and do_arquivo.feed_oficial(" STF ") and do_arquivo.feed_oficial("camara")
    for imprensa in ("folha", "valor", "jota", "conjur", "agenciabrasil", "", None):
        assert not do_arquivo.feed_oficial(imprensa)


# ── leitura tolerante ────────────────────────────────────────────────────────────────────────


def test_arquivo_ausente_usa_os_padroes(tmp_path: Path) -> None:
    lidas = regras.carregar(tmp_path)
    assert lidas.regua == regras.PADRAO.regua and lidas.cortes == regras.PADRAO.cortes and lidas.tetos == regras.PADRAO.tetos
    assert lidas.regua.maximo == 85 and (lidas.cortes.home, lidas.cortes.pagina) == (60, 40)
    assert lidas.situacoes == () and lidas.apelidos == () and lidas.nomes_aprovados == () and lidas.palavras_de_area == ()
    assert lidas.feeds_oficiais == regras.FEEDS_OFICIAIS and lidas.comissao_decide("camara", "CCJC")
    assert len(lidas.avisos) == 1 and "não existe" in lidas.avisos[0]
    assert lidas.situacao("retorno_de_vista") == "retorno de vista"  # a agenda segue de pé, com o termo oficial


@pytest.mark.parametrize("conteudo", [
    "regua: [1, 2", "- uma\n- lista\n", "só um texto", "", "\x00\x01",
    "regua: 5\ncortes: [60, 40]\ntetos: nada\nsituacoes: 3\napelidos: sim\n",
    "regua:\n  foro: {plenario: {a: 1}}\n  alcance: [1]\nfrescor: {aviso_dias_uteis: dois}\nfeeds_oficiais: stf\n",
])
def test_arquivo_ilegivel_ou_de_formato_inesperado_nunca_derruba(tmp_path: Path, conteudo: str) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "agenda.yaml").write_text(conteudo, encoding="utf-8")
    lidas = regras.carregar(tmp_path)
    assert lidas.regua == regras.PADRAO.regua and lidas.cortes == regras.PADRAO.cortes and lidas.tetos == regras.PADRAO.tetos
    assert lidas.frescor == regras.PADRAO.frescor and lidas.feeds_oficiais == regras.FEEDS_OFICIAIS
    if conteudo.strip():
        assert lidas.avisos


def test_arquivo_que_e_pasta_ou_binario_usa_os_padroes(tmp_path: Path) -> None:
    (tmp_path / "config" / "agenda.yaml").mkdir(parents=True)
    assert regras.carregar(tmp_path).regua == regras.PADRAO.regua
    outra = tmp_path / "outra"
    (outra / "config").mkdir(parents=True)
    (outra / "config" / "agenda.yaml").write_bytes(b"\xff\xfe\xfa regua")
    lidas = regras.carregar(outra)
    assert lidas.regua == regras.PADRAO.regua and "ilegível" in lidas.avisos[0]


def test_valor_invalido_cai_no_padrao_e_o_resto_do_arquivo_vale(bruto: dict[str, Any]) -> None:
    dado = copy.deepcopy(bruto)
    dado["regua"]["foro"]["plenario"] = 45  # acima do teto da parte
    dado["regua"]["foro"]["turma"] = -3
    dado["regua"]["alcance"]["pl"] = "dezoito"
    dado["regua"]["alcance"]["midia"] = 15  # a mídia não é parte da régua
    dado["regua"]["estagio"]["pautado"] = 12  # mudança válida
    del dado["regua"]["estagio"]["urgencia"]  # faltou: fica o padrão
    dado["regua"]["midia"] = {"seis_ou_mais": 15}
    dado["tetos"]["home"]["linhas"] = 0
    dado["tetos"]["home"]["celular"]["nome_ate"] = 48
    dado["tetos"]["pagina"] = None
    dado["comissoes_que_decidem"]["camara"] = "CCJC"
    dado["comissoes_que_decidem"]["senado"] = ["ccj", "CAE", "CAS"]
    dado["comissoes_que_decidem"]["stf"] = ["Plenário"]
    dado["frescor"]["some_dias"] = True
    lidas = regras.de_dict(dado)
    regua = lidas.regua
    assert regua.pontos_foro("plenario") == 30 and regua.pontos_foro("turma") == 10 and regua.pontos_alcance("pl") == 18
    assert regua.pontos_alcance("midia") == 0 and "midia" not in dict(regua.alcance)
    assert regua.pontos_estagio("pautado") == 12 and regua.pontos_estagio("urgencia") == 15
    assert regua.maximo == 85
    assert lidas.tetos.home.linhas == 8 and lidas.tetos.home.celular_nome_ate == 48 and lidas.tetos.pagina == regras.TetosPagina()
    assert dict(lidas.comissoes_que_decidem) == {"camara": ("CCJC", "CFT"), "senado": ("CCJ", "CAE", "CAS"), "congresso": ("CMO",)}
    assert lidas.frescor.some_dias == 4
    assert lidas.cortes == regras.Cortes() and len(lidas.situacoes) >= 12  # o que estava certo continua valendo
    avisos = " | ".join(lidas.avisos)
    for trecho in ("regua.foro.plenario", "regua.foro.turma", "regua.alcance.pl", "regua.alcance.midia", "tetos.home.linhas",
                   "comissoes_que_decidem.camara", "comissoes_que_decidem.stf", "frescor.some_dias"):
        assert trecho in avisos


def test_corte_invertido_volta_ao_padrao(bruto: dict[str, Any]) -> None:
    lidas = _com(bruto, cortes={"home": 40, "pagina": 60})
    assert (lidas.cortes.home, lidas.cortes.pagina) == (60, 40) and any("cortes" in a for a in lidas.avisos)
    mais_rigorosa = _com(bruto, cortes={"home": 70, "pagina": 50})
    assert (mais_rigorosa.cortes.home, mais_rigorosa.cortes.pagina) == (70, 50) and mais_rigorosa.avisos == ()


@pytest.mark.parametrize("coisa", [
    None, [], "texto", 7, {"regua": "x"}, {"situacoes": [1, 2]}, {"apelidos": [[1]]}, {"palavras_de_area": {"economia": 3, 5: "Cinco"}},
])
def test_de_dict_nunca_levanta_excecao(coisa: Any) -> None:
    lidas = regras.de_dict(coisa)
    assert isinstance(lidas, Regras) and lidas.regua.maximo == 85


# ── liga e desliga (config/site.yaml, QIJ_NO_AGENDA) ─────────────────────────────────────────


def test_site_yaml_liga_a_agenda_para_a_marca_tech_e_qij_no_agenda_desliga() -> None:
    ligada = load_config(env={})
    assert isinstance(ligada.agenda, AgendaConfig) and ligada.agenda.enabled and ligada.agenda.marcas == ["tech"]
    assert ligada.brand.key == "tech" and ligada.agenda_ativo
    for valor in ("1", "true", " Sim ", "on", "YES"):
        assert not load_config(env={"QIJ_NO_AGENDA": valor}).agenda_ativo, valor
    for valor in ("", "0", "false", "nao"):  # "0" não desliga
        assert load_config(env={"QIJ_NO_AGENDA": valor}).agenda_ativo, valor
    assert not load_config(env={"QIJ_BRAND": "pbcv"}).agenda_ativo  # só a marca tech
    assert load_config(env={"QIJ_BRAND": "pbcv"}).agenda.enabled  # ligada no arquivo; a marca é que não está na lista


def test_a_agenda_e_os_tribunais_desligam_cada_um_por_si() -> None:
    sem_agenda = load_config(env={"QIJ_NO_AGENDA": "1"})
    assert not sem_agenda.agenda_ativo and sem_agenda.tribunais_ativo and sem_agenda.congresso_ativo
    sem_tribunais = load_config(env={"QIJ_NO_TRIBUNAIS": "1", "QIJ_NO_CONGRESSO": "1"})
    assert sem_tribunais.agenda_ativo and not sem_tribunais.tribunais_ativo and not sem_tribunais.congresso_ativo


def _raiz_com(tmp_path: Path, agenda: Any) -> Path:
    """Uma cópia de config/ com o bloco ``agenda`` do site.yaml trocado (``...`` tira o bloco)."""
    destino = tmp_path / "config"
    destino.mkdir(parents=True)
    for nome in ("site.yaml", "sources.yaml"):
        shutil.copy(ROOT / "config" / nome, destino / nome)
    site = yaml.safe_load((destino / "site.yaml").read_text(encoding="utf-8"))
    if agenda is ...:
        site.pop("agenda", None)
    else:
        site["agenda"] = agenda
    (destino / "site.yaml").write_text(yaml.safe_dump(site, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return tmp_path


def test_o_bloco_agenda_do_site_yaml_manda(tmp_path: Path) -> None:
    sem_bloco = load_config(root=_raiz_com(tmp_path / "a", ...), env={})
    assert sem_bloco.agenda == AgendaConfig() and sem_bloco.agenda_ativo  # padrão do código: ligada para a marca tech
    desligada = load_config(root=_raiz_com(tmp_path / "b", {"enabled": False, "marcas": ["tech"]}), env={})
    assert not desligada.agenda_ativo
    outra_marca = load_config(root=_raiz_com(tmp_path / "c", {"enabled": True, "marcas": ["pbcv"]}), env={})
    assert not outra_marca.agenda_ativo
    assert load_config(root=_raiz_com(tmp_path / "d", {"enabled": True, "marcas": ["pbcv"]}), env={"QIJ_BRAND": "pbcv"}).agenda_ativo
    chave_nova = load_config(root=_raiz_com(tmp_path / "e", {"enabled": True, "marcas": ["tech"], "chave_do_futuro": 3}), env={})
    assert chave_nova.agenda_ativo  # chave desconhecida no YAML não derruba a leitura
