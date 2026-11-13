"""Projetos e filas, o cruzamento com o jornal, a montagem de data/congresso/, a aba e
as fichas nas matérias (qijournal.congresso.projetos, jornal, dados; qijournal.render.congresso).

Tudo contra o Congresso de mentira de ``tests/fixtures/congresso/fabrica.py``, sem rede.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qijournal import pipeline
from qijournal.__main__ import main
from qijournal.config import Config, load_config
from qijournal.congresso import dados, ficha, fontes, jornal, perfil, projetos, regras, temas
from qijournal.render import congresso as pagina
from qijournal.render.web import render_edition_page
from tests.fixtures.congresso import fabrica
from tests.fixtures.pipeline.factory import make_edition

HOJE = fabrica.HOJE


@pytest.fixture
def r() -> regras.Regras:
    return fabrica.regras_de_teste()


@pytest.fixture
def coletor(tmp_path: Path) -> fontes.Coletor:
    return fontes.Coletor(tmp_path / "cache", fetch=fabrica.FetchFalso(), pausa=0.0)


# ── situação → estado e fila ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("situacao", "esperado"),
    [
        ("Aguardando Designação de Relator(a)", (projetos.TRAMITANDO, projetos.F_RELATOR)),
        ("Aguardando Parecer", (projetos.TRAMITANDO, projetos.F_PARECER)),
        ("MATÉRIA COM A RELATORIA", (projetos.TRAMITANDO, projetos.F_PARECER)),
        ("Pronta para Pauta", (projetos.TRAMITANDO, projetos.F_PAUTA)),
        ("PRONTO PARA DELIBERAÇÃO DO PLENÁRIO", (projetos.TRAMITANDO, projetos.F_PAUTA)),
        ("Aguardando Despacho do Presidente da Câmara dos Deputados (Chancela)", (projetos.TRAMITANDO, projetos.F_MESA)),
        ("Aguardando Criação de Comissão Temporária", (projetos.TRAMITANDO, projetos.F_COMISSAO)),
        ("Aguardando Recurso", (projetos.TRAMITANDO, projetos.F_RECURSO)),
        ("", (projetos.TRAMITANDO, projetos.F_MESA)),  # recém-apresentada: ainda sem despacho
        ("Tramitando em Conjunto", (projetos.APENSADA, "")),
        ("Aguardando Apreciação pelo Senado Federal", (projetos.APROVADA, "")),
        ("Aguardando Apreciação do Veto", (projetos.APROVADA, "")),
        ("Transformado em Norma Jurídica", (projetos.NORMA, "")),
        ("Arquivada", (projetos.ENCERRADA, "")),
        ("Retirado pelo(a) Autor(a)", (projetos.ENCERRADA, "")),
        ("Devolvida ao(à) Autor(a)", (projetos.ENCERRADA, "")),
        ("Perdeu a Eficácia", (projetos.ENCERRADA, "")),
    ],
)
def test_classificar_situacao(situacao: str, esperado: tuple[str, str]):
    assert projetos.classificar(situacao) == esperado


def test_classificar_no_senado_vale_o_que_ele_diz_que_tramita():
    assert projetos.classificar("QUALQUER COISA", tramitando=False) == (projetos.ENCERRADA, "")
    assert projetos.classificar("AGUARDANDO DESIGNAÇÃO DO RELATOR", tramitando=True) == (projetos.TRAMITANDO, projetos.F_RELATOR)


# ── Câmara: proposições, autoria e fila ──────────────────────────────────────


@pytest.fixture
def props(coletor: fontes.Coletor, r: regras.Regras) -> list[projetos.Proposicao]:
    return projetos.camara_proposicoes(coletor, [2026], temas=temas.TemasCamara(coletor, r))


def test_camara_proposicoes_so_as_que_podem_virar_norma(props: list[projetos.Proposicao]):
    por = {p.id: p for p in props}
    assert sorted(por) == [1010, 1011, 1012, 1013, 1014, 1015]  # o requerimento (1016) fica de fora
    pl10 = por[1010]
    assert (pl10.titulo, pl10.estado, pl10.fila, pl10.orgao, pl10.movimento, pl10.temas) == ("PL 10/2026", projetos.TRAMITANDO, projetos.F_RELATOR, "CCJC", "2026-01-10", ["economia"])
    assert (por[1011].fila, por[1011].relator) == (projetos.F_PARECER, 7)
    assert (por[1012].estado, por[1012].autores, por[1012].autoria) == (projetos.NORMA, [], "Poder Executivo")
    assert (por[1013].autores, por[1013].primeiro) == ([4], 4)  # quem só apoia a PEC não é autor
    assert (por[1014].autores, por[1014].primeiro) == ([1, 2], 1)
    assert pl10.dias_sem_andar(HOJE) == 307


def test_resumo_de_autoria_separa_autoria_principal_de_coautoria(props: list[projetos.Proposicao], r: regras.Regras):
    de_ana = [p for p in props if 1 in p.autores]
    resumo = projetos.resumo_de_autoria(de_ana, HOJE, r.limiares, pessoa=1)
    assert (resumo["total"], resumo["coautorias"], resumo["paradas_n"]) == (3, 0, 2)
    assert resumo["estados"] == {projetos.TRAMITANDO: 2, projetos.ENCERRADA: 1}
    assert [i["t"] for i in resumo["paradas"]] == ["PL 10/2026", "PL 14/2026"]  # a parada há mais tempo primeiro
    assert all("dias" not in i for i in resumo["paradas"])  # a ficha guarda a data, não a contagem (que muda todo dia)
    de_bia = projetos.resumo_de_autoria([p for p in props if 2 in p.autores], HOJE, r.limiares, pessoa=2)
    assert (de_bia["total"], de_bia["coautorias"]) == (0, 1)


def test_relatorias_e_fila_da_camara(props: list[projetos.Proposicao], coletor: fontes.Coletor, r: regras.Regras):
    relatorias = projetos.camara_relatorias(props)
    assert [x.prop for x in relatorias[7]] == ["PL 11/2026"]
    resumo = projetos.resumo_de_relatorias(relatorias[7], HOJE, r.limiares)
    assert (resumo["total"], resumo["antigas_n"], resumo["mais_antiga"]) == (1, 1, "2026-02-01")
    presidentes = projetos.camara_presidentes(coletor, 57, HOJE)
    assert presidentes == {"CCJC": (8, "Hugo Leal")}
    fila = projetos.fila_da_casa(props, HOJE, r.limiares, presidentes=presidentes, nomes={7: "Gil Castro"})
    assert fila["tramitando"] == 4
    assert {f: b["n"] for f, b in fila["filas"].items()} == {projetos.F_RELATOR: 1, projetos.F_PARECER: 1, projetos.F_COMISSAO: 1, projetos.F_PAUTA: 1}
    assert fila["orgaos"][0]["orgao"] == "CCJC" and fila["orgaos"][0]["presidente"] == {"id": 8, "nome": "Hugo Leal"}
    assert fila["sentados"] == [{"id": 7, "nome": "Gil Castro", "n": 1, "antigas": 1, "mais_antiga_dias": 285}]
    assert (fila["prontas_plenario_n"], fila["prontas_plenario"][0]["t"], fila["prontas_plenario"][0]["dias"]) == (1, "PL 14/2026", 257)


def test_senado_autoria_relatorias_e_o_que_tramita(coletor: fontes.Coletor):
    tramitando = projetos.senado_tramitando(coletor)
    assert [(p.titulo, p.fila) for p in tramitando] == [("PL 21/2026", projetos.F_RELATOR)]  # o requerimento (RQS) fica de fora
    autoria = projetos.senado_de_autoria(coletor, {101: "Rita Alves", 103: "Túlio Braga"})
    assert sorted(p.titulo for p in autoria[101]) == ["PL 20/2026", "PL 21/2026"]
    assert {p.titulo: p.estado for p in autoria[101]} == {"PL 20/2026": projetos.APROVADA, "PL 21/2026": projetos.TRAMITANDO}
    assert all(p.primeiro == 101 for p in autoria[101])
    assert [(p.titulo, p.primeiro) for p in autoria[103]] == [("PL 21/2026", None)]  # coautor: não abre a autoria
    relatorias = projetos.senado_relatorias(coletor)
    assert [(x.prop, x.desde, x.orgao) for x in relatorias[103]] == [("PL 21/2026", "2026-02-01", "CI")]


# ── o cruzamento com o jornal ────────────────────────────────────────────────


def _nomes(*pessoas: tuple[str, str, str]) -> jornal.Nomes:
    return jornal.Nomes(jornal.Pessoa(*p) for p in pessoas)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Kim Kataguiri rebate Lula", ["cd-3"]),
        ("KIM KATAGUIRI em maiúsculas não é o registro", []),
        ("Bastidores da vitória do Atlético-MG", []),  # "Da Vitoria" só com as maiúsculas do registro
        ("Deputado Da Vitoria (PP-ES) assume relatoria", ["cd-1"]),
        ("Júlio César deve assumir o futebol do Corinthians", []),  # só prenomes: exige o título
        ("Deputado Júlio César critica a PEC", ["cd-2"]),
        ("Júlio Cesar (PSD-PI) relata o projeto", ["cd-2"]),
        ("Paulo Bilynskyj e Tiririca votam contra", ["cd-4"]),  # o tratamento ("Delegado") pode faltar; nome de uma palavra, não
        ("O deputado Tiririca anuncia a saída", ["cd-5"]),
        ("Rogério Marinho é internado", ["sf-6"]),  # sem diferenciar acentos
        ("Eduardo Braga apresenta relatório", []),  # homônimos nas duas Casas: só com o título
        ("Senador Eduardo Braga apresenta relatório", ["sf-8"]),
        ("Dr. Luizinho (PP-RJ) e o Luizinho do futebol", ["cd-9"]),
    ],
)
def test_nomes_regra_conservadora(texto: str, esperado: list[str]):
    nomes = _nomes(
        ("cd-1", "cd", "Da Vitoria"), ("cd-2", "cd", "Júlio Cesar"), ("cd-3", "cd", "Kim Kataguiri"), ("cd-4", "cd", "Delegado Paulo Bilynskyj"),
        ("cd-5", "cd", "Tiririca"), ("sf-6", "sf", "Rogerio Marinho"), ("cd-7", "cd", "Eduardo Braga"), ("sf-8", "sf", "Eduardo Braga"), ("cd-9", "cd", "Dr. Luizinho"),
    )
    assert [p.chave for p in nomes.achar(texto)] == esperado


def test_proposicoes_citadas_pelo_numero_ou_pelo_apelido():
    texto = "O PL 2.475/2025 e a MP 1.394/26; a PEC nº 18 de 2025, a PEC da Segurança Pública"
    assert jornal.proposicoes_citadas(texto, {"pec da seguranca publica": "PEC 18/2025"}) == ["PL 2475/2025", "MPV 1394/2026", "PEC 18/2025"]
    assert jornal.proposicoes_citadas("Nada aqui") == []


def test_mencoes_do_dia_so_nas_editorias_em_que_politico_e_noticia(tmp_path: Path):
    nomes = _nomes(*[(f"cd-{i}", "cd", nome) for i, nome, _p, _u in fabrica.DEPUTADOS])
    lidas, por = jornal.mencoes_do_dia(fabrica.busca_do_dia(), nomes)
    assert lidas == 3  # a manchete de esporte não é lida
    assert [(m.tipo, m.veiculo, m.url) for m in por["cd-4"]] == [("n", "Folha", "https://folha.example/a"), ("n", "Valor", "https://valor.example/d")]
    assert [m.titulo for m in por["cd-8"]] == ["Deputado Hugo Leal defende a reforma"]
    assert [(m.tipo, m.materia) for m in por["cd-7"]] == [("m", "pacote-fiscal")]
    (tmp_path / "2026-11-12.json").write_text(json.dumps(fabrica.busca_do_dia()), encoding="utf-8")
    (tmp_path / "2026-09-01.json").write_text(json.dumps({**fabrica.busca_do_dia(), "data": "2026-09-01"}), encoding="utf-8")
    cruzamento = jornal.cruzar(tmp_path, nomes, desde="2026-10-01")
    assert cruzamento.edicoes == ["2026-11-12"] and cruzamento.manchetes == 3
    resumo = jornal.resumo_de_mencoes(cruzamento.de("cd-4"), HOJE)
    assert (resumo["n"], resumo["n7"], resumo["n30"], resumo["veiculos"]) == (2, 2, 2, {"Folha": 1, "Valor": 1})
    assert jornal.resumo_de_mencoes([], HOJE) == {"n": 0}


# ── a montagem de data/congresso/ ────────────────────────────────────────────


@pytest.fixture
def publicado(tmp_path: Path, r: regras.Regras) -> tuple[Path, fabrica.FetchFalso, dados.Resultado]:
    """``data/`` com o arquivo de busca do dia e o Congresso de mentira já montado."""
    data_dir = tmp_path / "data"
    (data_dir / "busca").mkdir(parents=True)
    (data_dir / "busca" / "2026-11-12.json").write_text(json.dumps(fabrica.busca_do_dia()), encoding="utf-8")
    fetch = fabrica.FetchFalso()
    resultado = dados.atualizar(tmp_path, data_dir, tmp_path / "cache", dados.Opcoes(hoje=HOJE, projetos_desde=2026), fetch=fetch, regras=r)
    return data_dir, fetch, resultado


def _ler(data_dir: Path, nome: str) -> dict:
    return json.loads((data_dir / "congresso" / nome).read_text(encoding="utf-8"))


def test_atualizar_grava_base_fichas_paineis_e_leitura(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado]):
    data_dir, _fetch, resultado = publicado
    pasta = data_dir / "congresso"
    assert resultado.avisos == []
    assert resultado.pessoas == {"cd": 11, "sf": 4} and resultado.votacoes == {"cd": 6, "sf": 2}
    for nome in ("meta.json", "resumo.json", "achados.json", "casa-cd.json", "casa-sf.json", "base/cd-2026.json", "base/sf-2026.json", "base/cd-exercicio.json"):
        assert (pasta / nome).is_file(), nome
    assert len(list((pasta / "fichas").glob("*.json"))) == 15
    meta = _ler(data_dir, "meta.json")
    assert (meta["hoje"], meta["governo"], meta["polos"]) == (HOJE, "Lula", {"esquerda": ["PT"], "direita": ["PL"]})
    assert meta["jornal"] == {"edicoes": 1, "de": "2026-11-12", "ate": "2026-11-12", "manchetes": 3}


def test_quem_trocou_de_partido_e_comparado_pelo_que_votou_no_partido_atual(r: regras.Regras):
    """A distância da bancada usa só os votos dados no partido atual; com poucos, não compara."""
    p = perfil.Perfil(id=5, casa="cd")
    p.legislaturas[57] = perfil.Recorte(eixo=perfil.Eixo(esq=3, dir=3))
    p.eixos_por_partido[57] = {"PP": perfil.Eixo(esq=3, dir=0), "PL": perfil.Eixo(esq=0, dir=3)}
    cadastro = ficha.Cadastro(id=5, casa="cd", nome="Deputado 5", partido="PL", uf="SP", exercicio=True)
    f = ficha.montar(cadastro, p, r, legislatura=57, governo_atual="Lula")
    assert (f["campo"]["saldo"], f["campo"]["no_partido"]) == (0, {"saldo": 100, "n": 3})
    assert ficha.saldo_no_partido(f, 2) == 100
    assert ficha.saldo_no_partido(f, 5) is None  # poucas votações no partido atual
    # acabou de trocar e ainda não votou no partido novo
    p.eixos_por_partido[57] = {"PP": perfil.Eixo(esq=3, dir=3)}
    f = ficha.montar(cadastro, p, r, legislatura=57, governo_atual="Lula")
    assert f["campo"]["no_partido"] == {"saldo": None, "n": 0} and ficha.saldo_no_partido(f, 2) is None
    # quem nunca trocou: vale o saldo da legislatura
    p.eixos_por_partido[57] = {"PL": perfil.Eixo(esq=3, dir=3)}
    f = ficha.montar(cadastro, p, r, legislatura=57, governo_atual="Lula")
    assert "no_partido" not in f["campo"] and ficha.saldo_no_partido(f, 2) == 0


def test_a_ficha_junta_votos_projetos_relatorias_e_jornal(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado]):
    data_dir, _fetch, _resultado = publicado
    gil = _ler(data_dir, "fichas/cd-7.json")
    assert (gil["nome"], gil["partido"], gil["uf"], gil["exercicio"]) == ("Gil Castro", "PP", "BA", True)
    assert gil["campo"] == {"id": "centro_esquerda", "nome": "mais com a esquerda", "saldo": -33, "n": 3}
    assert gil["gov"] == {"Lula": {"k": 4, "n": 5, "pct": 80.0}}
    assert gil["leg"]["57"]["pres"]["pct"] == 100.0
    assert gil["relatorias"] == {"total": 1, "antigas_n": 1, "mais_antiga": "2026-02-01", "lista": [
        {"t": "PL 11/2026", "e": "Aumenta penas para o crime organizado.", "u": "https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao=1011", "desde": "2026-02-01", "o": "CSPCCO", "a": ""}
    ]}
    assert gil["jornal"]["materias"] == [{"d": "2026-11-12", "t": "Pacote fiscal avança", "id": "pacote-fiscal"}]
    assert gil["frases"][0].startswith("Vota mais com a esquerda: ficou desse lado em 67% das 3 votações de conteúdo")
    assert "Tem 1 matéria esperando o seu parecer, 1 há mais de 3 meses." in gil["frases"]
    ana = _ler(data_dir, "fichas/cd-1.json")
    assert (ana["autoria"]["total"], ana["autoria"]["paradas_n"], ana["autoria"]["temas"]) == (3, 2, {"economia": 1, "homenagens": 1, "trabalho": 1})
    joao = _ler(data_dir, "fichas/cd-10.json")
    assert [m["id"] for m in joao["dissidencias"]] == ["1010-1"] and joao["dissidencias"][0]["pm"] == "S"
    iris = _ler(data_dir, "fichas/cd-9.json")
    assert iris["presidiu"] == [["2025-02-01", ""]]
    rita = _ler(data_dir, "fichas/sf-101.json")
    assert (rita["casa"], rita["autoria"]["total"], rita["leg"]["57"]["pres"]["votou"]) == ("sf", 2, 2)
    sergio = _ler(data_dir, "fichas/sf-102.json")
    assert (sergio["leg"]["57"]["pres"]["faltas"], sergio["gov"]["Lula"]["pct"]) == (1, 0.0)


def test_o_resumo_e_os_paineis(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado]):
    data_dir, _fetch, _resultado = publicado
    resumo = {p["k"]: p for p in _ler(data_dir, "resumo.json")["pessoas"]}
    assert len(resumo) == 15
    assert (resumo["cd-7"]["saldo"], resumo["cd-7"]["campo"], resumo["cd-7"]["gov"], resumo["cd-7"]["rel_ant"]) == (-33, "centro_esquerda", 80.0, 1)
    assert resumo["cd-4"]["jor"] == 2 and resumo["cd-9"]["preside"] == 1
    cd = _ler(data_dir, "casa-cd.json")
    assert cd["votacoes"]["total"] == 6 and cd["votacoes"]["divisivas"] == 3 and cd["em_exercicio"] == 11
    assert [(b["sigla"], b["membros"], b["saldo"]) for b in cd["bancadas"]] == [("PP", 5, -33), ("PL", 3, 100), ("PT", 3, -100)]
    assert cd["temas"]["partidos"] == {}  # menos de 5 votações divisivas por tema: nada a dizer
    assert cd["ultimas"][0]["id"] == "1012-2" and cd["ultimas"][0]["sim"] == 10
    assert [x["k"] for x in cd["listas"]["citados"]] == ["cd-4", "cd-8", "cd-10"]
    assert "cd-9" not in [x["k"] for x in cd["listas"]["ausentes"]]  # quem preside a Casa fica fora de "quem menos vota"
    assert cd["fila"]["tramitando"] == 4
    furaram = cd["furaram"]
    assert (furaram["aprovadas"], furaram["rapidas_n"], furaram["rapidas"][0]["p"], furaram["rapidas"][0]["dias"], furaram["rapidas"][0]["urg"]) == (1, 1, "PL 12/2026", 15, 1)
    achados = _ler(data_dir, "achados.json")["achados"]
    assert {a["tipo"] for a in achados} >= {"fila", "furaram", "relatoria"}
    assert all(a["titulo"] and a["texto"] and a["alvo"].startswith("#cg-") for a in achados)


def test_rodar_de_novo_sem_mudanca_nao_regrava_nada(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado], tmp_path: Path, r: regras.Regras):
    data_dir, fetch, _resultado = publicado
    de_novo = dados.atualizar(tmp_path, data_dir, tmp_path / "cache", dados.Opcoes(hoje=HOJE, projetos_desde=2026), fetch=fetch, regras=r)
    assert de_novo.gravados == 0  # o commit diário só leva o que mudou


def test_fonte_fora_do_ar_mantem_o_que_ja_existe(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado], tmp_path: Path, r: regras.Regras):
    data_dir, _fetch, _resultado = publicado
    antes = (data_dir / "congresso" / "base" / "cd-2026.json").read_text(encoding="utf-8")
    fora = fabrica.FetchFalso()
    fora.fora_do_ar |= {"votacoesVotos", "/dadosabertos/votacao?"}
    resultado = dados.atualizar(tmp_path, data_dir, tmp_path / "cache2", dados.Opcoes(hoje=HOJE, projetos_desde=2026), fetch=fora, regras=r)
    assert (data_dir / "congresso" / "base" / "cd-2026.json").read_text(encoding="utf-8") == antes
    assert any("Câmara dos Deputados 2026" in a for a in resultado.avisos) and any("Senado Federal 2026" in a for a in resultado.avisos)
    assert resultado.pessoas == {"cd": 11, "sf": 4}  # as contas saem da base que já existia


# ── a aba e as fichas nas matérias ───────────────────────────────────────────


@pytest.fixture
def config() -> Config:
    return load_config(env={})


def test_a_aba_sai_inteira_sem_script(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado], config: Config):
    data_dir, _fetch, _resultado = publicado
    html = pagina.render_congresso_page(config, data_dir, home_href="../", archive_href="./")
    assert html is not None
    for trecho in (
        "<title>Triangula — Congresso</title>",
        'id="cg-leitura"', 'id="cg-polos"', 'id="cg-fila"', 'id="cg-plenario"', 'id="cg-quem"', 'id="cg-metodo"',
        "Câmara dos Deputados", "Senado Federal",
        'href="#p=cd-7" data-k="cd-7">Gil Castro</a>',  # o nome abre o dossiê
        "PL 12/2026",  # passou na frente
        "Hugo Leal",  # quem preside a comissão que segura a fila
        "Como ler",
    ):
        assert trecho in html, trecho
    manifesto = json.loads(html.split('id="cgManifesto">', 1)[1].split("</script>", 1)[0])
    assert (manifesto["hoje"], manifesto["base"], manifesto["governo"]) == (HOJE, "../data/congresso/fichas/", "Lula")
    assert html.count('data-casa="cd" data-b=') == 11 and html.count('data-casa="sf" data-b=') == 4


def test_sem_dados_nao_ha_aba(tmp_path: Path, config: Config):
    assert pagina.render_congresso_page(config, tmp_path, home_href="../", archive_href="./") is None
    assert pagina.carregar_indice(tmp_path) is None
    assert main(["congresso", "pagina", "--dir", str(tmp_path)]) == 1


def test_fichas_da_edicao_so_quem_esta_em_exercicio_e_e_citado(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado], config: Config):
    data_dir, _fetch, _resultado = publicado
    indice = pagina.carregar_indice(data_dir)
    assert indice is not None and len(indice.pessoas) == 15
    edition = make_edition()
    edition.stories[0].section = "politica"
    edition.stories[0].body = ["O deputado Gil Castro (PP-BA) promete o parecer; Davi Rocha critica o texto."]
    edition.stories[1].section = "esporte"
    edition.stories[1].body = ["Davi Rocha marcou dois gols."]
    fichas = pagina.fichas_da_edicao(edition, indice, limite=4)
    assert fichas is not None and list(fichas["materias"]) == [edition.stories[0].id]  # esporte fica de fora
    gil, davi = fichas["materias"][edition.stories[0].id]
    assert (gil["k"], gil["saldo"], gil["campo"], gil["gov"], gil["rel"]) == ("cd-7", -33, "mais com a esquerda", 80.0, 1)
    assert (davi["k"], davi["saldo"], davi["campo"]) == ("cd-4", 100, "com a direita")
    edition.stories[0].body = ["Nenhum parlamentar aqui."]
    assert pagina.fichas_da_edicao(edition, indice) is None


def test_a_edicao_mostra_as_fichas_e_o_botao_so_com_a_aba_publicada(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado], config: Config):
    data_dir, _fetch, _resultado = publicado
    out_dir = data_dir.parent
    edition = make_edition()
    edition.stories[0].section = "politica"
    edition.stories[0].body = ["O deputado Gil Castro (PP-BA) promete o parecer."]
    pipeline.anexar_congresso(edition, config, out_dir)
    assert edition.congresso is not None and not config.congresso.publicado  # há dados, mas a aba ainda não foi gravada
    sem_aba = render_edition_page(edition, config, home_href="./", archive_href="edicoes/")
    assert 'class="mcg"' not in sem_aba and "tool-cg" not in sem_aba

    (out_dir / "edicoes").mkdir()
    (out_dir / "edicoes" / pagina.PAGINA).write_text("<html></html>", encoding="utf-8")
    pipeline.anexar_congresso(edition, config, out_dir)
    assert config.congresso.publicado
    capa = render_edition_page(edition, config, home_href="./", archive_href="edicoes/")
    assert 'href="edicoes/congresso.html#p=cd-7">Gil Castro</a>' in capa
    assert "PP-BA · Câmara" in capa and "mais com a esquerda" in capa and "Votou com o governo em 80% das vezes" in capa
    assert 'class="tool tool-cg" href="edicoes/congresso.html"' in capa
    arquivada = render_edition_page(edition, config, home_href="../", archive_href="./", is_archive=True)
    assert 'href="./congresso.html#p=cd-7"' in arquivada
    # a edição guarda o retrato do dia: ida e volta pelo JSON
    assert type(edition).from_dict(edition.to_dict()).congresso == edition.congresso


def test_anexar_congresso_nunca_derruba_a_edicao(tmp_path: Path, config: Config):
    edition = make_edition()
    pipeline.anexar_congresso(edition, config, tmp_path)  # sem dados
    assert edition.congresso is None and not config.congresso.publicado
    pasta = tmp_path / "data" / "congresso"
    pasta.mkdir(parents=True)
    (pasta / "resumo.json").write_text("{isto não é json", encoding="utf-8")
    pipeline.anexar_congresso(edition, config, tmp_path)  # dados ilegíveis
    assert edition.congresso is None
    desligado = load_config(env={"QIJ_NO_CONGRESSO": "1"})
    assert not desligado.congresso_ativo
    pipeline.anexar_congresso(edition, desligado, tmp_path)
    assert edition.congresso is None
