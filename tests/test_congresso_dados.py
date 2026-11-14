"""Projetos e filas, o cruzamento com o jornal, a montagem de data/congresso/, a aba e
as fichas nas matérias (qijournal.congresso.projetos, jornal, dados; qijournal.render.congresso).

Tudo contra o Congresso de mentira de ``tests/fixtures/congresso/fabrica.py``, sem rede.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
import json
from pathlib import Path

import pytest

from qijournal import pipeline
from qijournal.__main__ import main
from qijournal.config import Config, load_config
from qijournal.congresso import base, dados, ficha, fontes, jornal, materias, perfil, projetos, regras, temas
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
    assert sorted(por) == [1010, 1011, 1012, 1013, 1014, 1015, 1017]  # o requerimento (1016) fica de fora
    pl10 = por[1010]
    assert (pl10.titulo, pl10.estado, pl10.fila, pl10.orgao, pl10.movimento, pl10.temas) == ("PL 10/2026", projetos.TRAMITANDO, projetos.F_RELATOR, "CCJC", "2026-01-10", ["economia"])
    assert (por[1011].fila, por[1011].relator) == (projetos.F_PARECER, 7)
    assert (por[1012].estado, por[1012].autores, por[1012].autoria) == (projetos.NORMA, [], "Poder Executivo")
    assert (por[1013].autores, por[1013].primeiro) == ([4], 4)  # quem só apoia a PEC não é autor
    assert (por[1014].autores, por[1014].primeiro) == ([1, 2], 1)
    assert pl10.dias_sem_andar(HOJE) == 307
    # origem, partidos dos autores, a matéria principal das apensadas e a apreciação
    assert (por[1012].origem, por[1012].apreciacao) == ("executivo", "plenario")
    assert (pl10.origem, pl10.partidos, pl10.apreciacao) == ("parlamentar", ["PT"], "")
    assert (por[1013].partidos, por[1014].partidos) == (["PL"], ["PT"])  # na PEC, só o proponente
    assert (por[1017].estado, por[1017].principal, por[1017].origem) == (projetos.APENSADA, 1015, "outra_casa")


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
    # o requerimento (RQS) fica de fora; "pronto para deliberação do Plenário" é do Plenário
    assert [(p.titulo, p.fila, p.orgao) for p in tramitando] == [("PL 21/2026", projetos.F_RELATOR, ""), ("PL 22/2026", projetos.F_PAUTA, "PLEN")]
    assert [(p.origem, p.partidos) for p in tramitando] == [("parlamentar", ["PT", "MDB"]), ("outra_casa", [])]
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


@pytest.mark.parametrize(
    ("esq", "dir_", "comeco"),
    [
        (0, 20, "Vota quase sempre com a direita:"),  # +100
        (3, 17, "Vota com a direita:"),  # +70: a faixa "com a direita"
        (6, 14, "Vota mais com a direita:"),  # +40
        (8, 12, "Divide-se entre os polos:"),  # +20: ainda "dividido"
        (17, 3, "Vota com a esquerda:"),  # −70
        (20, 0, "Vota quase sempre com a esquerda:"),  # −100
    ],
)
def test_a_frase_do_campo_diz_o_mesmo_que_a_faixa(r: regras.Regras, esq: int, dir_: int, comeco: str):
    atual = perfil.Recorte(eixo=perfil.Eixo(esq=esq, dir=dir_))
    assert ficha.frase("cd", atual, {}, "Lula", None, None, r)[0].startswith(comeco)


def test_quem_muda_de_lado_conforme_o_tema_so_com_o_que_passa_do_acaso():
    def pessoa(k: str, dir_total: int, n_total: int, temas: list[tuple[str, int, int]], saldo: int) -> dict:
        return {
            "k": k, "nome": k, "partido": "PL", "uf": "SP", "exercicio": True,
            "campo": {"saldo": saldo, "n": n_total},
            "leg": {"57": {"eixo": {"esq": n_total - dir_total, "dir": dir_total, "n": n_total, "saldo": saldo}}},
            "temas": [{"t": t, "nome": t.capitalize(), "eixo": {"esq": n - d, "dir": d, "n": n, "saldo": round(100 * (2 * d - n) / n)}} for t, d, n in temas],
        }

    fichas = [
        pessoa("cd-1", 90, 100, [("trabalho", 2, 12), ("economia", 40, 44)], 80),  # vira de lado em trabalho
        pessoa("cd-2", 90, 100, [("trabalho", 5, 10)], 80),  # 50%: do lado oposto? saldo 0, não chega a −20
        pessoa("cd-3", 95, 100, [("saude", 4, 9)], 90),  # poucas votações no tema
        pessoa("cd-4", 10, 100, [("seguranca", 10, 12)], -80),  # esquerda que vira em segurança
    ]
    saida = dados.mudam_conforme_tema(fichas, 57)
    assert [(c["k"], c["tema"], c["com"]) for c in saida] == [("cd-4", "seguranca", "direita"), ("cd-1", "trabalho", "esquerda")]
    assert saida[1]["tema_saldo"] == -67 and saida[1]["n"] == 12 and saida[1]["p"] < 1e-5
    assert dados._binomial_bicaudal(5, 10, 0.5) == pytest.approx(1.0)
    dados.marcar_temas_que_viram(fichas, 57)
    assert [t["t"] for f in fichas for t in f["temas"] if t.get("vira")] == ["trabalho", "seguranca"]
    assert dados._holm({"a": 0.001, "b": 0.02, "c": 0.5}, 0.05) == {"a", "b"}  # 0,02 ≤ 0,05/2
    assert dados._holm({"a": 0.001, "b": 0.03, "c": 0.5}, 0.05) == {"a"}


def test_o_caminho_de_uma_aprovada_sai_do_historico_oficial():
    from qijournal.congresso import relevancia

    data, caminho = relevancia.caminho_camara(fabrica.tramitacoes_do_pl_12(), "2025-11-13")
    assert data == "2026-06-16"
    assert caminho == {
        "urgencia": {"d": "2026-06-09", "por": "Ana Lima e Davi Rocha"},
        "parecer_plenario": {"d": "2026-06-16", "relator": "Hugo Leal (PP-PE)"},
    }
    assert relevancia.caminho_camara(fabrica.tramitacoes_do_pl_12(), "2026-07-01") is None  # aprovada antes da janela


def test_a_nota_de_relevancia_soma_sinais_que_dao_para_medir(r: regras.Regras):
    from qijournal.congresso import relevancia

    def prop(pid: int, sigla: str, ementa: str, origem: str, partidos: list[str]) -> projetos.Proposicao:
        return projetos.Proposicao(id=pid, casa="cd", sigla=sigla, numero=pid, ano=2020, ementa=ementa, apresentada="2020-01-01", origem=origem, partidos=partidos)

    filhos = {1: ["2019-05-01", "2023-01-10", "2024-03-01", "2025-06-01", "2027-01-01"]}  # quatro até hoje; um depois
    regua = relevancia.Regua(r, filhos)
    nota = regua.nota(prop(1, "PL", "Altera o Código Penal.", "parlamentar", ["PT"]), HOJE)
    assert (nota.demanda, nota.pontos, nota.valor) == (4, {"demanda": 23, "origem": 0, "alcance": 12, "apoio": 0}, 35)
    # com a janela de quatro anos, o de 2019 sai
    com_janela = dataclasses.replace(r, relevancia=regras.Relevancia(janela_demanda_anos=4))
    assert relevancia.Regua(com_janela, filhos).nota(prop(1, "PL", "Altera o Código Penal.", "parlamentar", ["PT"]), HOJE).demanda == 3
    pec = regua.nota(prop(2, "PEC", "Altera o art. 5º.", "outra_casa", ["PT", "PL"]), HOJE)
    assert (pec.pontos, pec.valor) == ({"demanda": 0, "origem": 25, "alcance": 20, "apoio": 15}, 60)
    simbolica = regua.nota(prop(3, "PL", "Institui o Dia Nacional do Ciclista.", "outra_casa", []), HOJE)
    assert (simbolica.simbolica, simbolica.valor) == (True, 10)  # 37 pelos sinais, mas é simbólica: teto
    # no Senado, sem a demanda: os outros pesos valem o todo
    senado = relevancia.Regua(r, None).nota(prop(4, "PL", "Altera o imposto de renda.", "executivo", []), HOJE)
    assert (senado.demanda, senado.valor) == (None, round(100 * (0.8 * 25 + 0.6 * 20) / 60))


def test_o_que_espera_e_o_que_passou_no_painel(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado]):
    data_dir, _fetch, _resultado = publicado
    cd = json.loads((data_dir / "congresso" / "casa-cd.json").read_text(encoding="utf-8"))["relevancia"]
    assert (cd["esperam_n"], cd["passaram_n"], cd["sem_demanda"]) == (1, 1, False)
    (espera,) = cd["esperam"]
    assert (espera["t"], espera["ap"], espera["n"], espera["pt"]) == ("PL 14/2026", 1, 22, {"demanda": 10, "origem": 0, "alcance": 12, "apoio": 0})
    (passou,) = cd["passaram"]
    assert (passou["t"], passou["dias"], passou["aprovada"], passou["o"], passou["n"]) == ("PL 12/2026", 15, "2026-06-16", "executivo", 32)
    assert passou["caminho"]["urgencia"]["por"] == "Ana Lima e Davi Rocha"
    assert sorted(ponto[2] for ponto in cd["pontos"]) == [0, 1]
    sf = json.loads((data_dir / "congresso" / "casa-sf.json").read_text(encoding="utf-8"))["relevancia"]
    assert (sf["sem_demanda"], [e["t"] for e in sf["esperam"]], [e.get("simb") for e in sf["esperam"]]) == (True, ["PL 22/2026"], [1])
    assert [(x["t"], x["dias"], x["n"]) for x in sf["passaram"]] == [("PL 20/2026", 12, 20)]


def test_os_votos_da_legislatura_para_o_comparativo(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado]):
    """Só as votações de conteúdo abertas de Plenário, em ordem; um caractere por votação."""
    data_dir, _fetch, _resultado = publicado
    texto = (data_dir / "congresso" / "votos-cd.json").read_text(encoding="utf-8")
    votos = json.loads(texto)
    assert [v[0] for v in votos["votacoes"]] == ["1010-1", "1011-1", "1010-2", "1012-2"]  # sem o requerimento, a urgência e a CCJC
    assert [v[6] for v in votos["votacoes"]] == [1, 1, 1, 0]  # as que dividiram esquerda e direita
    assert all(len(v) == 8 and v[7] in (0, 1, 2) for v in votos["votacoes"])  # o turno, quando a descrição diz
    assert (votos["votos"]["cd-1"], votos["votos"]["cd-9"], votos["votos"]["cd-11"]) == ("SNSS", "SSPS", "---S")
    assert texto.count("\n") >= len(votos["votacoes"]) + len(votos["votos"])  # uma linha por votação e por pessoa
    sf = json.loads((data_dir / "congresso" / "votos-sf.json").read_text(encoding="utf-8"))
    assert [v[0] for v in sf["votacoes"]] == ["sf501"] and sf["votos"]["sf-104"] == "J"  # a secreta fica de fora


def test_no_jornal_e_no_voto(coletor: fontes.Coletor, r: regras.Regras):
    """A manchete que cita alguém e um projeto (pelo número ou pelo apelido) vai ao lado dos
    votos dele nesse projeto; numa matéria, só vale o projeto citado na mesma frase."""
    from qijournal.congresso import base

    r = dataclasses.replace(r, apelidos={"PL 10/2026": ["PL do Imposto"]})
    assert r.apelido_de("PL 10/2026") == "PL do Imposto" and r.apelidos_para_busca() == {"pl do imposto": "PL 10/2026"}
    nomes = jornal.Nomes([jornal.Pessoa("cd-4", "cd", "Davi Rocha"), jornal.Pessoa("cd-7", "cd", "Gil Castro")])
    busca = {
        "data": "2026-11-12", "secoes": [["politica", "Política", "#C90055"]], "veiculos": ["Folha"],
        "noticias": [[0, -1, 0, "Davi Rocha vota contra o PL do Imposto", "folha.example/x", 10]],
        "materias": [{"id": "m1", "s": 0, "t": "Votações da semana", "l": "",
                      "c": "O deputado Gil Castro (PP-BA) elogiou o PL 11/2026. A PEC 1/2026 segue parada na Mesa."}],
    }
    citadas: Counter[str] = Counter()
    _lidas, por = jornal.mencoes_do_dia(busca, nomes, r.apelidos_para_busca(), citadas)
    assert citadas == Counter({"PL 10/2026": 1, "PL 11/2026": 1, "PEC 1/2026": 1})
    assert por["cd-4"][0].props == ("PL 10/2026",) and por["cd-7"][0].props == ("PL 11/2026",)  # a PEC está em outra frase
    ano = base.camara_ano(coletor, 2026)
    por_prop = dados.votacoes_por_proposicao([ano], r, 57)
    (davi,) = dados.jornal_e_voto(por["cd-4"], 4, por_prop, r)
    assert (davi["p"], davi["apel"], davi["v"], davi["u"]) == ("PL 10/2026", "PL do Imposto", "Folha", "https://folha.example/x")
    assert [(v["t"], v["x"]) for v in davi["votos"]] == [("merito", "N"), ("destaque", "N")]  # o texto principal primeiro
    (gil,) = dados.jornal_e_voto(por["cd-7"], 7, por_prop, r)
    assert (gil["p"], gil["id"], [(v["t"], v["x"]) for v in gil["votos"]]) == ("PL 11/2026", "m1", [("merito", "S"), ("procedimento", "N")])
    assert dados.jornal_e_voto(por["cd-7"], 999, por_prop, r) == []  # quem não votou no projeto não aparece


@pytest.mark.parametrize(
    ("descricao", "esperado"),
    [
        ("Aprovada, em primeiro turno, a Proposta de Emenda à Constituição nº 221, de 2019. Sim: 472; Não: 22; Total: 494.", 1),
        ("Aprovada, em segundo turno, a Proposta de Emenda à Constituição n° 221, de 2019. . Sim: 461; Não: 19; Total: 480.", 2),
        ("PEC 66/2023, nos termos do parecer (2º turno)", 2),
        ("Votação nominal do 1° turno da Proposta de Emenda à Constituição", 1),
        ("Requerimento de quebra de interstício entre o primeiro e o segundo turno de deliberação da PEC", None),
        ("Aprovado o Requerimento. Sim: 372; Não: 101; Total: 473.", None),
    ],
)
def test_o_turno_da_pec_sai_da_descricao_oficial(descricao: str, esperado: int | None):
    assert dados.turno(descricao) == esperado


def test_rankings_de_cada_um_na_casa():
    def f(k: str, pres: float | None, prop: int | None, *, exercicio: bool = True, presidiu: list | None = None) -> dict:
        ficha_ = {"k": k, "exercicio": exercicio, "leg": {"57": {"pres": {"pct": pres}} if pres is not None else {}}}
        if prop is not None:
            ficha_["autoria"] = {"total": prop}
        if presidiu:
            ficha_["presidiu"] = presidiu
        return ficha_

    fichas = [
        f("a", 90.0, 10), f("b", 80.0, None), f("c", 80.0, 3),
        f("d", 50.0, 99, exercicio=False),  # fora do exercício: fora dos rankings
        f("e", 20.0, 1, presidiu=[["2025-02-01", ""]]),  # preside a Casa: fora do ranking de presença
    ]
    dados.rankings(fichas, 57, "Lula")
    por = {x["k"]: x.get("ranks", {}) for x in fichas}
    assert (por["a"]["pres"], por["b"]["pres"], por["c"]["pres"]) == ([1, 3, 3], [2, 3, 1], [2, 3, 1])  # empate: mesma posição
    assert "pres" not in por["e"] and por["d"] == {}
    assert (por["a"]["prop"], por["b"]["prop"], por["c"]["prop"], por["e"]["prop"]) == ([1, 4, 4], [4, 4, 1], [2, 4, 3], [3, 4, 2])
    assert "gov" not in por["a"]  # sem votos com orientação do governo: sem posição
    assert all("_preside" not in x for x in fichas)


def test_rachas_por_partido_com_tema_e_governo():
    """O PSOL vota com o PL contra o PT e o governo em cinco votações de economia, e com o
    PT em uma: cinco rachas, todas contra o governo; viram achado da Casa."""
    from qijournal.congresso import base, perfil as perfil_mod

    r = regras.de_dict(
        {
            "legislaturas": [{"numero": 57, "inicio": "2026-01-01", "fim": "2027-01-31"}],
            "polos": [{"inicio": "2019-01-01", "esquerda": ["PT", "PSOL"], "direita": ["PL"]}],
            "limiares": {"coesao_polo": 0.75, "minimo_polo": 2, "minimo_partido": 2},
            "temas": [{"id": "economia", "nome": "Economia", "camara": [40]}],
        }
    )
    pessoas = [base.Pessoa(i, f"Deputado {i}", "SP", [(0, "PT" if i <= 8 else "PSOL" if i <= 10 else "PL")]) for i in range(1, 15)]
    racha, junto = "S" * 8 + "NN" + "NNNN", "S" * 10 + "NNNN"
    votacoes = [
        base.Votacao(id=f"v{k}", data=f"2026-03-{k + 1:02d}", orgao=base.PLENARIO, descricao="Aprovado o Projeto.", tipo=base.MERITO,
                     votos=votos, prop=f"PL {k}/2026", ementa="Regra fiscal.", temas=["economia"], ori={base.GOVERNO: "S"}, aprovada=True)
        for k, votos in enumerate([racha] * 5 + [junto])
    ]
    ano = base.AnoVotos(casa="cd", ano=2026, pessoas=pessoas, votacoes=votacoes)
    rachas = dados._rachas([ano], r, 57)
    assert rachas["opostas"] == 6
    (psol,) = rachas["partidos"]
    assert (psol["sigla"], psol["polo"], psol["com"], psol["n"], psol["contra_governo"]) == ("PSOL", "esquerda", "direita", 5, 5)
    assert psol["temas"] == [{"id": "economia", "nome": "Economia", "n": 5}]
    assert [i["d"] for i in psol["itens"]][:2] == ["2026-03-05", "2026-03-04"]  # do mais novo
    assert (psol["itens"][0]["lado"], psol["itens"][0]["polo"], psol["itens"][0]["gov"]) == ("N", "S", "S")
    achado = next(a for a in dados.achados({"cd": {"rachas": rachas}}, r) if a["tipo"] == "racha")
    assert achado["titulo"] == "Na Câmara, o PSOL votou com a direita, contra o resto do polo, em 5 votações de conteúdo"
    assert "contra a orientação do governo" in achado["texto"] and achado["alvo"] == "#cg-rachas-cd"
    assert perfil_mod.ler_votacao(votacoes[5], [p.partido_em(5) for p in pessoas], r).divisiva


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
    assert "juntos" not in {a["tipo"] for a in achados}  # sem a tabela partido × partido (poucas votações), nada a dizer
    assert all(a["titulo"] and a["texto"] and a["alvo"].startswith("#cg-") for a in achados)


# ── a ficha de cada matéria ──────────────────────────────────────────────────


def test_a_proposicao_guarda_o_que_a_casa_diz_alem_da_ementa(props: list[projetos.Proposicao], coletor: fontes.Coletor):
    pl10 = next(p for p in props if p.id == 1010)
    assert pl10.explicacao.startswith("Corrige a tabela do imposto de renda") and pl10.teor == "https://www.camara.example/teor/1010"
    assert projetos.termos(pl10.chaves) == ["Alteração", "Lei do Imposto de Renda", "tabela progressiva", "isenção", "pessoa física"]
    assert projetos.termos("ALTERAÇÃO, CÓDIGO PENAL. ALTERAÇÃO; pena", 2) == ["Alteração", "Código penal"]  # o Senado grita; sem repetir
    assert base.ementa_valida("Dispõe sobre X.\n\nNOVA EMENTA: Dispõe sobre Y; e Z.") == ("Dispõe sobre Y; e Z.", "Dispõe sobre X.")
    senado = projetos.senado_do_detalhe(fabrica.detalhe_do_processo_7001())
    assert (senado.titulo, senado.id, senado.processo, senado.explicacao) == (
        "PL 20/2026", 160001, 7001, "Eleva a faixa de isenção do imposto de renda da pessoa física.")
    assert (senado.origem, senado.partidos, senado.teor) == ("parlamentar", ["PT"], "https://legis.senado.example/documento/7001")
    antiga = projetos.camara_do_detalhe(
        {"id": 99, "siglaTipo": "PL", "numero": 7, "ano": 2007, "ementa": "Trata de terras. NOVA EMENTA: Regula a demarcação de terras.", "keywords": "terra, demarcação",
         "urlInteiroTeor": "https://www.camara.example/teor/99", "dataApresentacao": "2007-03-01T10:00",
         "statusProposicao": {"descricaoSituacao": "Transformado em Norma Jurídica", "siglaOrgao": "MESA", "dataHora": "2023-10-20T10:00", "regime": "Urgência"}},
        [{"uri": f"{fabrica.CAMARA}/api/v2/deputados/4", "nome": "Davi Rocha", "tipo": "Deputado(a)", "ordemAssinatura": 1}],
    )
    assert (antiga.titulo, antiga.ementa, antiga.original, antiga.estado, antiga.urgencia) == (
        "PL 7/2007", "Regula a demarcação de terras.", "Trata de terras.", projetos.NORMA, True)
    assert (antiga.primeiro, antiga.autores, antiga.origem) == (4, [4], "parlamentar")
    assert projetos.camara_detalhes(coletor, [424242]) == {}  # fora do ar: só fica de fora


@pytest.mark.parametrize(
    ("tipo", "descricao", "abertura", "esperado"),
    [
        (base.MERITO, "Aprovado o Substitutivo ao Projeto de Lei nº 10, de 2026.", "", materias.TEXTO),
        (base.DESTAQUE, "Mantido o texto. Sim: 282; não: 115.", "Votação do DTQ 16: PL: Destaque para Votação em Separado do Artigo 9º (art. 161, I).", materias.TRECHO),
        (base.DESTAQUE, "Suprimido o texto. Sim: 173; Não: 238.", "Votação do DTQ 8 (NOVO): Destaque para Votação em Separado do Art. 27.", materias.TRECHO),
        (base.DESTAQUE, "Rejeitada a Emenda de Comissão nº 15. Sim: 143; não: 195.", "Votação do DTQ 3: PL: Emenda de Comissão Nº 15 (art. 161, II).", materias.EMENDA),
        (base.DESTAQUE, "Votação nominal da Emenda nº 83 ao Projeto de Lei Complementar nº 93, de 2023, destacada.", "", materias.EMENDA),
        (base.DESTAQUE, "Votação nominal do Art. 28 do Projeto de Lei nº 2.903, de 2023, destacado.", "", materias.TRECHO),
        (base.URGENCIA, "Aprovado o Requerimento de Urgência.", "", materias.REQUERIMENTO),
        (base.DESTAQUE, "Resultado sem objeto claro.", "", ""),
    ],
)
def test_o_que_o_sim_queria_dizer(tipo: str, descricao: str, abertura: str, esperado: str):
    v = base.Votacao(id="x", data="2026-03-03", orgao="PLEN", descricao=descricao, tipo=tipo, votos="", abertura=abertura)
    assert materias.sentido_do_sim(v) == esperado


@pytest.mark.parametrize(
    ("abertura", "esperado"),
    [
        ("Votação do DTQ 5: PL: Emenda de Plenário Nº 4 (art. 161, II).", "PL"),
        ("Votação do DTQ 3 (PL, UNIÃO, PP): Destaque da Emenda de Plenário n. 6. (161, II)", "PL, UNIÃO, PP"),
        ("Votação do DTQ 10: Bloco Fdr PT-PCdoB-PV: Destaque para Votação em Separado do §13", "Bloco Fdr PT-PCdoB-PV"),
        ("Votação da EMA 1 (Mauro Benevides Filho): Emenda Aglutinativa nº 1.", "Mauro Benevides Filho"),
        ("Votação em turno único.", ""),
    ],
)
def test_quem_pediu_o_destaque(abertura: str, esperado: str):
    assert materias.quem_pediu(abertura) == esperado


def test_a_ficha_da_materia_diz_o_que_e_e_como_cada_polo_votou(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado]):
    data_dir, _fetch, _resultado = publicado
    texto = (data_dir / "congresso" / "materias-cd.json").read_text(encoding="utf-8")
    cd = json.loads(texto)
    assert (cd["polos"], cd["pesos"]) == ({"esquerda": ["PT"], "direita": ["PL"]}, {"demanda": 40, "origem": 25, "alcance": 20, "apoio": 15})
    assert texto.count("\n") >= len(cd["materias"])  # uma matéria por linha
    # as votadas no Plenário (PL 10, 11 e 12) e as do painel (a PEC 1 não é votada nem está pronta: fica de fora)
    assert sorted(cd["materias"]) == ["PL 10/2026", "PL 11/2026", "PL 12/2026", "PL 14/2026"]
    pl10 = cd["materias"]["PL 10/2026"]
    assert (pl10["e"], pl10["ex"][:30], pl10["kw"][1], pl10["ut"]) == (
        "Altera a tabela do imposto de renda.", "Corrige a tabela do imposto de", "Lei do Imposto de Renda", "https://www.camara.example/teor/1010")
    assert (pl10["au"], pl10["auk"], pl10["o"], pl10["pa"], pl10["tm"], pl10["sit"]) == (
        "Ana Lima", "cd-1", "parlamentar", ["PT"], ["economia"], "Aguardando Designação de Relator(a)")
    assert [v["id"] for v in pl10["vot"]] == ["1010-1", "1010-2"]  # só o Plenário; a CCJC fica de fora
    principal, destaque = pl10["vot"]
    assert (principal["t"], principal["ss"], principal["e"], principal["dr"], principal["g"], principal["dv"], principal["apv"]) == (
        "merito", "texto", "S", "N", "S", 1, 1)
    assert (principal["s"], principal["n"]) == (6, 4)
    assert principal["pp"] == [["PT", 3, 0, "e"], ["PL", 0, 3, "d"]]  # a contagem de cada partido dos polos
    assert principal["pt"] == [["PP", "S", 75]]  # os demais partidos, pelo lado da maioria
    assert (destaque["t"], destaque["ss"], destaque["dq"], destaque["apv"]) == ("destaque", "trecho", "PL", 1)  # "Mantido o texto": o Sim venceu
    assert destaque["ab"].startswith("Votação do DTQ 1: PL: Destaque para Votação em Separado")
    # a nota do painel: a que espera, a que passou (com o caminho) e, para as só votadas, a nota na data do voto
    assert cd["materias"]["PL 14/2026"]["rel"]["q"] == "espera" and "vot" not in cd["materias"]["PL 14/2026"]
    passou = cd["materias"]["PL 12/2026"]["rel"]
    assert (passou["q"], passou["apv"], passou["dias"], passou["cam"]["urgencia"]["por"]) == ("passou", "2026-06-16", 15, "Ana Lima e Davi Rocha")
    assert [v["t"] for v in cd["materias"]["PL 12/2026"]["vot"]] == ["urgencia", "merito"]
    assert (pl10["rel"]["q"], pl10["rel"]["ref"]) == ("votada", "2026-04-07")
    assert cd["materias"]["PL 11/2026"]["rl"] == "Gil Castro" and cd["materias"]["PL 11/2026"]["rlk"] == "cd-7"
    sf = json.loads((data_dir / "congresso" / "materias-sf.json").read_text(encoding="utf-8"))
    pl20 = sf["materias"]["PL 20/2026"]
    assert (pl20["ex"], pl20["kw"][:2], pl20["u"]) == (
        "Eleva a faixa de isenção do imposto de renda da pessoa física.", ["Alteração", "Imposto de renda"],
        "https://www25.senado.leg.br/web/atividade/materias/-/materia/160001")
    assert [v["id"] for v in pl20["vot"]] == ["sf501"] and "demanda" not in sf["pesos"]  # o Senado não informa os apensados
    assert materias.chave("cd", "PL 10/2026") == "cd-PL-10-2026" and materias.chave("cd", "REQ 5") == ""
    assert materias.titulo_da_materia("PEC 66/2023 (fase 2)") == "PEC 66/2023"  # o Senado anota a fase ao lado do número


def test_a_materia_de_um_polo_que_o_outro_votou_a_favor(r: regras.Regras):
    def voto(esquerda: str, direita: str) -> dict[str, str]:
        return {"t": base.MERITO, "d": "2026-03-03", "e": esquerda, "dr": direita, "apv": 1}

    fichas = {
        "PL 1/2026": {"e": "Da direita, com a esquerda a favor.", "vot": [voto("N", "S"), voto("S", "S")]},  # vale a última
        "PL 2/2026": {"e": "Da direita, com a esquerda contra.", "vot": [voto("N", "S")]},
        "PL 3/2026": {"e": "Da esquerda, com a direita dividida.", "vot": [voto("S", "")]},
        "PL 4/2026": {"e": "Dos dois polos: não entra.", "vot": [voto("S", "S")]},
        "PL 5/2026": {"e": "Da direita, só com requerimento votado: não entra.", "vot": [{"t": base.URGENCIA, "d": "2026-03-03"}]},
    }
    props = {
        t: projetos.Proposicao(id=i, casa="cd", sigla="PL", numero=i, ano=2026, partidos=partidos)
        for i, (t, partidos) in enumerate(zip(fichas, (["PL", "PP"], ["PL"], ["PT"], ["PT", "PL"], ["PL"]), strict=True), start=1)
    }
    cruzada = materias.autoria_cruzada(fichas, props, r, HOJE)
    assert {k: v for k, v in cruzada["direita"].items() if k != "itens"} == {"n": 2, "favor": 1, "contra": 1, "dividido": 0}
    assert [(i["t"], i["pa"]) for i in cruzada["direita"]["itens"]] == [("PL 1/2026", ["PL"])]
    assert {k: v for k, v in cruzada["esquerda"].items() if k != "itens"} == {"n": 1, "favor": 0, "contra": 0, "dividido": 1}


def test_quanto_os_partidos_votam_juntos_sem_a_regua_dos_polos(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado], coletor: fontes.Coletor, r: regras.Regras):
    """Em todas as votações de conteúdo, não só nas que dividem os polos: é o que mostra que
    PT e PL também votam do mesmo lado."""
    ano = base.camara_ano(coletor, 2026)
    bruto = perfil.convivencia([ano], r, 57)
    assert {k: v for k, v in bruto.items() if k != "pares"} == {"conteudo": 4, "divisivas": 3, "rachas": 0, "juntos": 1, "sem_lado": 0}
    assert bruto["pares"][("PL", "PT")] == [1, 4]  # só o PL 12/2026, que todos aprovaram
    assert bruto["pares"][("PP", "PT")] == [3, 4] and bruto["pares"][("PL", "PP")] == [2, 4]
    data_dir, _fetch, _resultado = publicado
    cd = _ler(data_dir, "casa-cd.json")
    assert cd["convivencia"]["juntos"] == 1 and cd["convivencia"]["partidos"] == []  # menos de 20 votações por par: nada a dizer
    assert {k: v for k, v in cd["cruzada"]["esquerda"].items() if k != "itens"} == {"n": 1, "favor": 0, "contra": 1, "dividido": 0}
    meta = _ler(data_dir, "meta.json")
    economia = next(t for t in meta["temas"] if t["id"] == "economia")
    assert (economia["camara"], economia["senado"]) == (["Economia"], ["Economia e Desenvolvimento"])  # o que entra no tema


def test_sobre_o_que_cada_um_mais_propoe():
    def prop(i: int, chaves: str, primeiro: int = 1) -> projetos.Proposicao:
        return projetos.Proposicao(id=i, casa="cd", sigla="PL", numero=i, ano=2026, chaves=chaves, primeiro=primeiro)

    geral = [prop(i, "Alteração, saúde") for i in range(1, 101)]
    meus = [prop(200, "Alteração, Código Penal, aumento da pena"), prop(201, "ALTERAÇÃO, Código penal, crime"), prop(202, "Alteração, aumento da pena, Código Penal")]
    vocabulario = projetos.Vocabulario([*geral, *meus])
    # "alteração" está em todas (não diz o assunto); "crime" só numa proposta dele (pouco para dizer que é assunto dele)
    assert vocabulario.caracteristicos(meus) == [["Código Penal", 3], ["aumento da pena", 2]]
    resumo = projetos.resumo_de_autoria(meus, HOJE, fabrica.regras_de_teste().limiares, pessoa=1, vocabulario=vocabulario)
    assert resumo["termos"] == [["Código Penal", 3], ["aumento da pena", 2]] and resumo["siglas"] == {"PL": 3}
    assert "termos" not in projetos.resumo_de_autoria(meus, HOJE, fabrica.regras_de_teste().limiares, pessoa=1)


def test_um_ano_gravado_no_formato_antigo_e_refeito_uma_vez(tmp_path: Path, coletor: fontes.Coletor, r: regras.Regras):
    pasta = tmp_path / "base"
    pasta.mkdir()
    antigo = base.serializar(base.camara_ano(coletor, 2026)).replace('"f":%d,' % base.FORMATO, "")
    base.caminho(pasta, "cd", 2026).write_text(antigo, encoding="utf-8")
    avisos: list[str] = []
    votos, gravados = dados.atualizar_base(coletor, r, pasta, dados.Opcoes(hoje="2027-01-15", projetos_desde=2026), avisos)
    refeito = base.ler(pasta, "cd", 2026)  # 2026 é ano fechado em 2027: só é refeito porque estava no formato antigo
    assert refeito.formato == base.FORMATO and gravados >= 1 and votos["cd"][0].formato == base.FORMATO
    pedidos = len(coletor.fetch.pedidos)
    dados.atualizar_base(coletor, r, pasta, dados.Opcoes(hoje="2027-01-15", projetos_desde=2026), [])
    assert not [u for u in coletor.fetch.pedidos[pedidos:] if "votacoes-2026" in u]  # já está no formato novo: não baixa de novo


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
        'id="cgCmpF"', 'list="cgNomes"', '<datalist id="cgNomes">',  # comparar quaisquer dois, escolhidos pelo nome
        # as vistas (uma aba cada; sem script, todas em sequência) e os blocos novos
        'id="cgBarra"', 'href="#cg-partidos" data-vista="partidos"', 'id="cg-partidos"', 'id="cg-espera"', 'data-vista="votacoes"',
        'id="cg-juntos-cd"', 'id="cg-cruzada-cd"', 'id="cg-materias-cd"', 'id="cg-tema-det-cd"', 'id="cg-temas-def"',
        'href="#m=cd-PL-12-2026" data-m="cd-PL-12-2026">PL 12/2026</a>',  # o número da matéria abre a ficha dela
        "Por que um voto é “de esquerda” ou “de direita”",
    ):
        assert trecho in html, trecho
    assert html.count('class="az-sec cg-vista"') + html.count('class="cg-vista"') + html.count('class="az-sec az-met cg-vista"') == 11
    for vista in ("frentes", "cota", "escolha"):  # as abas da leva 4
        assert f'data-vista="{vista}"' in html and f'id="cg-{vista}"' in html, vista
    manifesto = json.loads(html.split('id="cgManifesto">', 1)[1].split("</script>", 1)[0])
    assert (manifesto["hoje"], manifesto["base"], manifesto["governo"]) == (HOJE, "../data/congresso/fichas/", "Lula")
    assert (manifesto["materiasArq"], manifesto["polos"]) == ("../data/congresso/materias-", {"esquerda": ["PT"], "direita": ["PL"]})
    assert manifesto["temasDef"]["economia"] == {"cd": ["Economia"], "sf": ["Economia e Desenvolvimento"]}  # a definição de cada tema
    assert html.count('data-casa="cd" data-b=') == 11 and html.count('data-casa="sf" data-b=') == 4


def test_o_seletor_de_tema_so_oferece_o_que_poe_alguem_na_faixa(
    publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado], config: Config, monkeypatch: pytest.MonkeyPatch
):
    data_dir, _fetch, _resultado = publicado
    monkeypatch.setitem(pagina.FAIXA_MIN, "cd", 1)  # a fixture tem poucos deputados por partido
    # Na fixture, os temas da Câmara têm 2 e 1 votações esquerda × direita: nenhum chega ao mínimo, não há seletor.
    html = pagina.render_congresso_page(config, data_dir, home_href="../", archive_href="./")
    assert 'id="cg-faixas-cd"' in html and 'id="cgFxTema-cd"' not in html
    arquivo = data_dir / dados.PASTA / "casa-cd.json"
    painel = json.loads(arquivo.read_text(encoding="utf-8"))
    economia = next(t for t in painel["temas"]["temas"] if t["id"] == "economia")
    economia["n"] = ficha.MINIMO_TEMA_FAIXA
    arquivo.write_text(json.dumps(painel), encoding="utf-8")
    html = pagina.render_congresso_page(config, data_dir, home_href="../", archive_href="./")
    seletor = html.split('id="cgFxTema-cd"', 1)[1].split("</select>", 1)[0]
    assert '<option value="economia">Economia (5 votações)</option>' in seletor
    assert '<option value="seguranca" disabled>Segurança (1 votação: poucas para o saldo)</option>' in seletor


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
    # sem fichas, o JSON da edição não ganha a chave (fica como antes, byte a byte)
    edition.congresso = None
    assert "congresso" not in edition.to_dict() and type(edition).from_dict(edition.to_dict()).congresso is None


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


# ── leva 4: saldo pesado, frentes, escolha ───────────────────────────────────


def test_o_nome_curto_do_tema_e_as_frentes_de_causa_da_config():
    assert regras.nome_curto("Economia, tributos e orçamento") == "Economia"
    assert regras.nome_curto("Agro e terra") == "Agro" and regras.nome_curto("Saúde") == "Saúde"
    r = regras.de_dict({"temas": [{"id": "x", "nome": "Meio ambiente e energia", "curto": "Ambiente"}, {"id": "y", "nome": "Cidades, transporte"}],
                        "frentes": [{"titulo": "Frente Parlamentar Mista Ambientalista", "nome": "Ambientalista", "tema": "x"}, {"titulo": "Frente Evangélica"}]})
    assert [t.curto for t in r.temas] == ["Ambiente", "Cidades"]
    assert r.frentes[0] == regras.FrenteDeCausa(titulo="frente parlamentar mista ambientalista", nome="Ambientalista", tema="x")
    assert (r.frentes[1].nome, r.frentes[1].tema) == ("Frente Evangélica", "todas")  # sem tema: todas as votações divisivas


def test_frentes_no_painel_na_ficha_no_resumo_e_na_aba(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado], config: Config):
    data_dir, _fetch, resultado = publicado
    assert resultado.avisos == []
    painel = _ler(data_dir, "casa-cd.json")["frentes"]
    assert (painel["total"], [c["id"] for c in painel["causas"]]) == (2, [9001, 9002])  # a da outra legislatura e a que não casa ficam de fora
    economia = painel["causas"][0]
    assert (economia["nome"], economia["coord"]["k"], economia["membros"]) == ("Economia Livre", "cd-4", 6)
    assert economia["doc"] == "https://www.camara.leg.br/frente/9001.pdf"  # sem a aspa que o arquivo traz sobrando
    assert "frentes" not in _ler(data_dir, "casa-sf.json")  # o Senado não publica as frentes
    gil = _ler(data_dir, "fichas/cd-7.json")["frentes"]
    assert (gil["n"], gil["de"], [c["id"] for c in gil["causas"]]) == (2, 2, [9001, 9002])
    linha = next(p for p in _ler(data_dir, "resumo.json")["pessoas"] if p["k"] == "cd-7")
    assert (linha["frn"], linha["fr"]) == (2, [9001, 9002])
    html = pagina.render_congresso_page(config, data_dir, home_href="../", archive_href="./")
    for trecho in ('id="cg-frentes"', 'id="cg-frente-9001"', 'href="#cg-frentes" data-vista="frentes"', "Economia Livre", "O Senado não publica quem assina"):
        assert trecho in html, trecho
    manifesto = json.loads(html.split('id="cgManifesto">', 1)[1].split("</script>", 1)[0])
    assert manifesto["fr"]["cd-7"] == [9001, 9002] and manifesto["frentes"]["nomes"] == {"9001": "Economia Livre", "9002": "Trabalho Decente"}


def test_escolha_o_seu_candidato_sai_inteira_sem_script(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado], config: Config):
    data_dir, _fetch, _resultado = publicado
    html = pagina.render_congresso_page(config, data_dir, home_href="../", archive_href="./")
    resumo = _ler(data_dir, "resumo.json")["pessoas"]
    em_exercicio = {casa: sum(1 for p in resumo if p["ex"] and p["k"].startswith(casa)) for casa in ("cd", "sf")}
    escolha = html.split('id="cg-escolha"', 1)[1].split('data-vista="metodo"', 1)[0]
    assert escolha.count('<li data-pk="cd-') == em_exercicio["cd"] and escolha.count('<li data-pk="sf-') == em_exercicio["sf"]
    assert 'id="cgEscF" hidden' in escolha  # os filtros são reforço: sem script, a lista inteira
    assert 'href="#p=cd-7" data-k="cd-7">Gil Castro</a>' in escolha  # o nome abre o dossiê
    assert "data-k=" not in escolha.split('<li data-pk="cd-7"', 1)[1].split("<a ", 1)[0]  # o item não abre o dossiê inteiro: só o nome
    manifesto = json.loads(html.split('id="cgManifesto">', 1)[1].split("</script>", 1)[0])
    assert manifesto["temasCurtos"]["economia"] == "Economia"
    propoe = {p["k"]: p["pt"] for p in resumo if p.get("pt") and p["ex"]}
    assert propoe and manifesto["pt"] == propoe  # sobre o que cada um propõe, por tema


def test_o_saldo_pesado_chega_a_ficha_ao_resumo_e_a_pagina(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado], config: Config):
    data_dir, _fetch, _resultado = publicado
    cobertura = _ler(data_dir, "casa-cd.json")["peso"]
    assert cobertura["divisivas"] >= cobertura["com_nota"] >= 0
    for p in _ler(data_dir, "resumo.json")["pessoas"]:
        f = _ler(data_dir, f"fichas/{p['k']}.json")
        assert p.get("sp") == (f.get("peso") or {}).get("saldo")
    html = pagina.render_congresso_page(config, data_dir, home_href="../", archive_href="./")
    assert 'data-ord="sp"' in html and "Saldo pesado pela relevância" in html


def test_a_cota_no_painel_na_ficha_e_na_aba(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado], config: Config):
    data_dir, _fetch, resultado = publicado
    assert resultado.avisos == []
    camara, senado = _ler(data_dir, "casa-cd.json")["cota"], _ler(data_dir, "casa-sf.json")["cota"]
    assert "SIGEPA" in camara["sem"] and "moradia" in senado["sem"]
    assert all("SIGEPA" not in c["nome"] for c in camara["categorias"])  # as passagens da agência ficam fora de todos os anos
    repetida = next(r for r in camara["repetidas"] if r["fornecedor"] == "LOCADORA SUL")
    assert sorted(d["k"] for d in repetida["docs"]) == ["cd-1", "cd-2"]  # a mesma nota em dois gabinetes
    assert senado["total"] == 12000.0  # o reembolso de moradia (tipo nulo) fica fora
    davi = _ler(data_dir, "fichas/cd-4.json")["cota"]
    assert davi["fornecedores"][0]["cnpj"] == "11111111000111" and davi["maiores"][0]["url"].startswith("https://www.camara.leg.br/")
    linha = next(p for p in _ler(data_dir, "resumo.json")["pessoas"] if p["k"] == "cd-4")
    assert linha["ct"] == round(davi["leg"]["media"])
    html = pagina.render_congresso_page(config, data_dir, home_href="../", archive_href="./")
    for trecho in ('id="cg-cota-cd"', "A mesma nota em mais de um gabinete", "LOCADORA SUL", "Fora da conta:", 'id="cgEscCt"'):
        assert trecho in html, trecho


def test_as_comissoes_no_painel_na_ficha_e_na_aba(publicado: tuple[Path, fabrica.FetchFalso, dados.Resultado], config: Config):
    data_dir, _fetch, _resultado = publicado
    camara, senado = _ler(data_dir, "casa-cd.json")["comissoes"], _ler(data_dir, "casa-sf.json")["comissoes"]
    assert (camara["votacoes"], camara["por_comissao"]) == (1, [["CCJC", 1]])  # a votação nominal da CCJC, fora das contas do Plenário
    assert (senado["votacoes"], senado["ultimas"][0]["prop"]) == (1, "PL 20/2026")
    assert _ler(data_dir, "fichas/sf-102.json")["comissoes"]["votos"][0]["v"] == "N"
    assert _ler(data_dir, "fichas/cd-4.json")["comissoes"]["n"] == 1
    html = pagina.render_congresso_page(config, data_dir, home_href="../", archive_href="./")
    assert 'id="cg-comissoes-cd"' in html and 'id="cg-comissoes-sf"' in html and "Nas comissões" in html
