"""Base de votações e perfil de cada parlamentar (qijournal.congresso.base, perfil, exercicio).

As contas são conferidas contra o Congresso de mentira de
``tests/fixtures/congresso/fabrica.py``, feito para caber numa tabela.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from qijournal.congresso import base, exercicio, fontes, materias, perfil, regras, temas
from tests.fixtures.congresso import fabrica


@pytest.fixture
def coletor(tmp_path: Path) -> fontes.Coletor:
    return fontes.Coletor(tmp_path / "cache", fetch=fabrica.FetchFalso(), pausa=0.0)


@pytest.fixture
def r() -> regras.Regras:
    return fabrica.regras_de_teste()


@pytest.fixture
def camara(coletor: fontes.Coletor, r: regras.Regras) -> base.AnoVotos:
    return base.camara_ano(coletor, 2026, temas=temas.TemasCamara(coletor, r))


def _votacao(ano: base.AnoVotos, vid: str) -> base.Votacao:
    return next(v for v in ano.votacoes if v.id == vid)


def _voto(ano: base.AnoVotos, vid: str, pessoa: int) -> str:
    return _votacao(ano, vid).votos[ano.indice()[pessoa]]


# ── cache ────────────────────────────────────────────────────────────────────


def test_limpar_apaga_do_cache_so_o_que_e_velho_e_nao_foi_usado(tmp_path: Path):
    agora = [1_800_000_000.0]
    coletor = fontes.Coletor(tmp_path / "cache", fetch=fabrica.FetchFalso(), pausa=0.0, relogio=lambda: agora[0])
    usado = coletor.arquivo(f"{fabrica.CAMARA}/arquivos/votacoes/csv/votacoes-2026.csv")
    velho = tmp_path / "cache" / "api" / "consulta-de-outro-dia.json"
    novo = tmp_path / "cache" / "api" / "consulta-de-ontem.json"
    for caminho, idade_dias in ((velho, 30), (novo, 1), (usado, 30)):
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(caminho.read_bytes() if caminho.exists() else b"{}")
        os.utime(caminho, (agora[0] - idade_dias * 86400,) * 2)
    assert coletor.limpar(dias=10) == 1
    assert not velho.exists() and novo.exists() and usado.exists()  # o usado fica, mesmo velho


# ── classificação e normalização ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("descricao", "tipo"),
    [
        ("Aprovado o Substitutivo ao Projeto de Lei nº 1, de 2026, ressalvados os destaques. Sim: 300; Não: 100.", base.MERITO),
        ("Aprovada, em primeiro turno, a Proposta de Emenda à Constituição nº 45, de 2019.", base.MERITO),
        ("Aprovada a Medida Provisória nº 1.313 de 2025, na forma do Projeto de Lei de Conversão.", base.MERITO),
        ("Aprovada a Redação Final assinada pelo relator.", base.MERITO),
        ("Aprovada a Subemenda Substitutiva Global ao Projeto de Lei Complementar nº 186.", base.MERITO),
        ("Votação nominal da Emenda nº 5 (Substitutivo) à Proposta de Emenda à Constituição nº 22.", base.MERITO),
        ("Mantido o texto. Sim: 401; Não: 34.", base.DESTAQUE),
        ("Rejeitada a Emenda de Plenário nº 68.", base.DESTAQUE),
        ("Rejeitadas as Emendas de Plenário.", base.DESTAQUE),
        ("Aprovado o DTQ 12. Sim: 241; não: 18.", base.DESTAQUE),
        ("Rejeitada a admissibilidade dos destaques simples.", base.DESTAQUE),
        ("Votação nominal da Emenda nº 2 ao Projeto de Lei Complementar nº 18, de 2021, destacada.", base.DESTAQUE),
        ("Aprovado o Requerimento de Urgência (Art. 155 do RICD).", base.URGENCIA),
        ("Rejeitado o Requerimento. Sim: 22; Não: 362.", base.PROCEDIMENTO),
        ("Rejeitado o Recurso nº 2, de 2026, contra parecer terminativo.", base.RECURSO),
        ("Resultado. Sim: 182; Não: 182.", base.OUTRO),
    ],
)
def test_tipo_da_votacao_pela_descricao_oficial(descricao: str, tipo: str):
    assert base.tipo_da_votacao(descricao) == tipo


def test_tipo_da_votacao_no_senado_usa_o_contexto_a_sigla_e_o_padrao():
    assert base.tipo_da_votacao("Votação nominal do Requerimento n° 770, de Líderes.", contexto="Solicita urgência para o PLP 299") == base.URGENCIA
    assert base.tipo_da_votacao("Votação nominal da Mensagem nº 3.", sigla="MSF") == base.AUTORIDADE
    assert base.tipo_da_votacao("Qualquer coisa", secreta=True) == base.AUTORIDADE
    # votações antigas do Senado são descritas pela ementa: vale o padrão dado pela sigla
    assert base.tipo_da_votacao("Altera os arts. 7º e 23 da Constituição.", padrao=base.MERITO) == base.MERITO


@pytest.mark.parametrize(
    ("rotulo", "esperado"),
    [
        ("GOV.", base.GOVERNO),
        ("Governo", base.GOVERNO),
        ("Oposição", base.OPOSICAO),
        ("Novo", "NOVO"),
        ("Podemos", "PODE"),
        ("SEM REGISTRO", "S/PARTIDO"),
        ("Republican", "REPUBLICANOS"),
        ("Solidaried", "SOLIDARIEDADE"),
        ("PCdoB", "PCdoB"),
        ("União", "UNIÃO"),
        ("PROGRES", "PP"),
        ("Bl UniPpPsd...", "Bl UniPpPsd..."),
        ("PpMdbPtb", "PpMdbPtb"),
        ("Fdr PT-PCdoB-PV", "Fdr PT-PCdoB-PV"),
    ],
)
def test_bancada_tem_um_nome_so(rotulo: str, esperado: str):
    assert base.bancada(rotulo) == esperado


def test_orientacao():
    assert [base.orientacao(v) for v in ("Sim", "NÃO", "Liberado", "LIVRE", "Obstrução", "")] == ["S", "N", "L", "L", "O", ""]


# ── Câmara ───────────────────────────────────────────────────────────────────


def test_camara_ano_le_so_as_votacoes_nominais_com_votos_e_materia(camara: base.AnoVotos):
    assert [v.id for v in camara.votacoes] == ["1010-1", "1011-1", "1011-2", "1010-2", "1010-3", "1012-1", "1012-2"]
    assert len(camara.pessoas) == 11
    v1 = _votacao(camara, "1010-1")
    assert (v1.data, v1.hora, v1.orgao, v1.tipo, v1.aprovada) == ("2026-03-03", "15:00", "PLEN", base.MERITO, True)
    assert (v1.prop, v1.prop_id, v1.temas) == ("PL 10/2026", 1010, ["economia"])
    assert v1.ori == {base.GOVERNO: "S", "PT": "S"}
    assert _votacao(camara, "1011-2").tipo == base.PROCEDIMENTO and _votacao(camara, "1011-2").aprovada is False
    assert _votacao(camara, "1010-2").tipo == base.DESTAQUE
    # o que a Mesa pôs em votação: quem pediu o destaque e qual trecho
    assert _votacao(camara, "1010-2").abertura.startswith("Votação do DTQ 1: PL: Destaque para Votação em Separado do art. 3º")
    assert _votacao(camara, "1010-1").abertura == ""
    assert _votacao(camara, "1012-1").tipo == base.URGENCIA
    assert not _votacao(camara, "1010-3").plenario


def test_camara_ano_um_caractere_por_pessoa(camara: base.AnoVotos):
    assert _voto(camara, "1010-1", 1) == base.SIM
    assert _voto(camara, "1010-1", 4) == base.NAO
    assert _voto(camara, "1010-2", 9) == base.PRESIDIU  # "Artigo 17"
    assert _voto(camara, "1012-1", 2) == base.SEM_REGISTRO  # não aparece no arquivo
    assert _voto(camara, "1010-1", 11) == base.SEM_REGISTRO
    assert all(len(v.votos) == len(camara.pessoas) for v in camara.votacoes)


def test_partido_do_dia_segue_a_troca(coletor: fontes.Coletor):
    """Quem troca de partido no meio do ano tem o partido de cada votação."""
    fetch = coletor.fetch
    chave = "/arquivos/votacoesVotos/csv/votacoesVotos-2026.csv"
    fetch.mapa[chave] = fetch.mapa[chave].replace('"1012-2";"Sim";"10";"João Paz";"PP";"GO"'.encode(), '"1012-2";"Sim";"10";"João Paz";"PL";"GO"'.encode())
    ano = base.camara_ano(coletor, 2026)
    joao = next(p for p in ano.pessoas if p.id == 10)
    assert [sigla for _i, sigla in joao.partidos] == ["PP", "PL"]
    ultimo = len(ano.votacoes) - 1
    assert joao.partido_em(0) == "PP" and joao.partido_em(ultimo) == "PL"


def test_serializar_e_ler_sao_ida_e_volta(camara: base.AnoVotos, tmp_path: Path):
    camara.atualizado = "2026-06-16"
    texto = base.serializar(camara)
    assert texto.count("\n") >= len(camara.votacoes) + len(camara.pessoas)  # uma por linha
    base.caminho(tmp_path, "cd", 2026).write_text(texto, encoding="utf-8")
    assert base.ler(tmp_path, "cd", 2026) == camara
    assert base.anos_gravados(tmp_path, "cd") == [2026]
    assert base.ler(tmp_path, "sf", 2026) is None
    base.caminho(tmp_path, "cd", 2025).write_text(texto.replace('"v":1', '"v":99'), encoding="utf-8")
    assert base.ler(tmp_path, "cd", 2025) is None  # outra versão do formato: refaz
    # um ano gravado antes de um campo novo é lido, mas diz o formato em que está (para ser refeito uma vez)
    assert '"f":%d' % base.FORMATO in texto and base.ler(tmp_path, "cd", 2026).formato == base.FORMATO
    base.caminho(tmp_path, "cd", 2024).write_text(texto.replace('"f":%d,' % base.FORMATO, ""), encoding="utf-8")
    assert base.ler(tmp_path, "cd", 2024).formato == 1


# ── Senado ───────────────────────────────────────────────────────────────────


def test_senado_ano_traduz_os_codigos_de_voto_e_junta_a_orientacao(coletor: fontes.Coletor, r: regras.Regras):
    ano = base.senado_ano(coletor, 2026, temas=temas.TemasSenado(coletor, r), fim=fabrica.HOJE)
    aberta, secreta = ano.votacoes
    assert (aberta.id, aberta.data, aberta.hora, aberta.tipo, aberta.prop) == ("sf501", "2026-05-12", "17:10", base.MERITO, "PL 20/2026")
    assert aberta.ori == {base.GOVERNO: "S", "PL": "N"}
    assert aberta.temas == ["economia"] and aberta.apresentada == "2026-04-30"
    assert [aberta.votos[ano.indice()[c]] for c in (101, 102, 103, 104)] == [base.SIM, base.NAO, base.SIM, base.JUSTIFICADA]
    assert secreta.secreta and secreta.tipo == base.AUTORIDADE
    assert [secreta.votos[ano.indice()[c]] for c in (101, 102, 103, 104)] == [base.SECRETO, base.FALTA, base.PRESIDIU, base.SEM_VOTO]


def test_senado_ano_sem_as_orientacoes_ainda_sai(coletor: fontes.Coletor):
    coletor.fetch.fora_do_ar.add("orientacaoBancada")
    ano = base.senado_ano(coletor, 2026, fim=fabrica.HOJE)
    assert len(ano.votacoes) == 2 and ano.votacoes[0].ori == {}


# ── perfil ───────────────────────────────────────────────────────────────────


@pytest.fixture
def perfis(camara: base.AnoVotos, r: regras.Regras) -> dict[int, perfil.Perfil]:
    em_exercicio = {d: [("2023-02-01", fabrica.HOJE)] for d in range(1, 11)} | {11: [("2026-06-01", fabrica.HOJE)]}
    return perfil.calcular([camara], r, exercicio=em_exercicio, presidencia={9: [("2025-02-01", fabrica.HOJE)]})


def test_o_campo_so_conta_votacoes_de_conteudo_que_dividem_os_polos(perfis: dict[int, perfil.Perfil]):
    gil, joao, ana, davi = (perfis[i].legislaturas[57] for i in (7, 10, 1, 4))
    assert (gil.eixo.esq, gil.eixo.dir) == (2, 1)  # V1 e V4 com a esquerda, V2 com a direita; o requerimento (V3) não conta
    assert round(gil.eixo.saldo) == -33
    assert (joao.eixo.esq, joao.eixo.dir, joao.eixo.saldo) == (0, 3, 100.0)
    assert ana.eixo.saldo == -100.0 and davi.eixo.saldo == 100.0


def test_governismo_conta_onde_o_governo_orientou_sim_ou_nao(perfis: dict[int, perfil.Perfil]):
    def governo(dep: int) -> tuple[int, int]:
        f = perfis[dep].governos["Lula"].governo
        return f.k, f.n

    assert governo(7) == (4, 5)  # V5 foi "Liberado": fora da conta
    assert governo(4) == (1, 5)
    assert governo(1) == (5, 5)


def test_presenca_so_conta_os_dias_em_exercicio(perfis: dict[int, perfil.Perfil]):
    bia, lia, iris = (perfis[i].legislaturas[57] for i in (2, 11, 9))
    assert (bia.possiveis, bia.votou, bia.faltas) == (6, 5, 1)  # faltou à urgência (V5)
    assert (lia.possiveis, lia.votou, lia.faltas) == (2, 2, 0)  # suplente: só as votações depois da posse
    assert (iris.possiveis, iris.votou) == (5, 5)  # presidia em V4: fora da conta


def test_no_senado_a_ausencia_justificada_fica_fora_da_presenca(coletor: fontes.Coletor, r: regras.Regras):
    """Licença, missão e atividade parlamentar não são falta: a votação sai da conta de
    presença e é contada à parte."""
    ano = base.senado_ano(coletor, 2026, fim=fabrica.HOJE)
    perfis = perfil.calcular([ano], r)
    rita, sergio, vera = (perfis[c].legislaturas[57] for c in (101, 102, 104))
    assert (vera.possiveis, vera.votou, vera.justificadas, vera.sem_voto) == (1, 0, 1, 1)  # "AP" na aberta; presente sem votar na secreta
    assert (sergio.possiveis, sergio.votou, sergio.faltas) == (2, 1, 1)  # "NCom": falta registrada
    assert (rita.possiveis, rita.votou, rita.presenca) == (2, 2, 100.0)  # o voto secreto conta como presença


def test_sem_os_periodos_de_exercicio_a_presenca_nao_e_contada(camara: base.AnoVotos, r: regras.Regras):
    sem = perfil.calcular([camara], r)
    assert sem[2].legislaturas[57].possiveis == 0 and sem[2].legislaturas[57].presenca is None
    assert (sem[2].legislaturas[57].sim, sem[2].legislaturas[57].nao) == (3, 2)  # os votos continuam contados


def test_dissidencia_e_votar_contra_o_partido_fechado(perfis: dict[int, perfil.Perfil]):
    joao = perfis[10]
    assert [m.votacao.id for m in joao.dissidencias] == ["1010-1"]  # em V4 o PP rachou (2 × 1): abaixo da coesão pedida
    marca = joao.dissidencias[0]
    assert (marca.voto, marca.partido, marca.lado_partido, round(marca.coesao, 2)) == (base.NAO, "PP", base.SIM, 0.75)
    fidelidade = joao.legislaturas[57].partido
    assert (fidelidade.k, fidelidade.n) == (4, 6)
    assert perfis[7].dissidencias == []


def test_surpresa_e_ficar_com_o_outro_polo(r: regras.Regras):
    """Quem tem lado definido (saldo além de ±60) e, numa votação divisiva de conteúdo,
    ficou com o outro polo. Seis votações: o PT vota Sim, o PL vota Não, e o deputado 5
    acompanha o PL em cinco delas."""
    pessoas = [base.Pessoa(i, f"Deputado {i}", "SP", [(0, sigla)]) for i, sigla in ((1, "PT"), (2, "PT"), (3, "PL"), (4, "PL"), (5, "PP"), (6, "PP"))]
    votacoes = [
        base.Votacao(id=f"v{k}", data=f"2026-03-{k + 1:02d}", orgao=base.PLENARIO, descricao="Aprovado o Projeto.", tipo=base.MERITO,
                     votos="SSNN" + ("S" if k == 3 else "N") + "N")
        for k in range(6)
    ]
    ano = base.AnoVotos(casa="cd", ano=2026, pessoas=pessoas, votacoes=votacoes)
    perfis = perfil.calcular([ano], r)
    assert round(perfis[5].legislaturas[57].eixo.saldo) == 67
    assert [(m.votacao.id, m.voto, m.com) for m in perfis[5].surpresas] == [("v3", base.SIM, "esquerda")]
    assert perfis[6].surpresas == [] and perfis[1].surpresas == []


def test_quem_troca_de_partido_tem_o_saldo_de_cada_partido(r: regras.Regras):
    """O deputado 5 vota com o PT enquanto está no PP (três votações) e com o PL depois
    que entra no PL (três votações): o saldo da legislatura é 0, e o de cada partido é o
    que ele fez enquanto estava nele."""
    pessoas = [base.Pessoa(i, f"Deputado {i}", "SP", [(0, sigla)]) for i, sigla in ((1, "PT"), (2, "PT"), (3, "PL"), (4, "PL"))]
    pessoas.append(base.Pessoa(5, "Deputado 5", "SP", [(0, "PP"), (3, "PL")]))
    votacoes = [
        base.Votacao(id=f"v{k}", data=f"2026-03-{k + 1:02d}", orgao=base.PLENARIO, descricao="Aprovado o Projeto.", tipo=base.MERITO,
                     votos="SSNN" + ("S" if k < 3 else "N"))
        for k in range(6)
    ]
    perfis = perfil.calcular([base.AnoVotos(casa="cd", ano=2026, pessoas=pessoas, votacoes=votacoes)], r)
    cinco = perfis[5]
    assert cinco.legislaturas[57].eixo.saldo == 0.0
    por_partido = cinco.eixos_por_partido[57]
    assert (por_partido["PP"].saldo, por_partido["PP"].n) == (-100.0, 3)
    assert (por_partido["PL"].saldo, por_partido["PL"].n) == (100.0, 3)
    assert set(perfis[1].eixos_por_partido[57]) == {"PT"}


def test_o_psol_com_o_pl_contra_o_pt_vira_racha():
    r = regras.de_dict(
        {
            "legislaturas": [{"numero": 57, "inicio": "2026-01-01", "fim": "2027-01-31"}],
            "polos": [{"inicio": "2019-01-01", "esquerda": ["PT", "PSOL"], "direita": ["PL"]}],
            "limiares": {"coesao_polo": 0.75, "minimo_polo": 2, "minimo_partido": 2},
        }
    )
    # PT 8 Sim; PSOL 2 Não; PL 4 Não → esquerda 8 × 2 (80% Sim), direita Não: polos opostos, e o PSOL ficou com a direita
    siglas = ["PT"] * 8 + ["PSOL"] * 2 + ["PL"] * 4
    v = base.Votacao(id="x", data="2026-03-01", orgao=base.PLENARIO, descricao="Aprovado o Projeto.", tipo=base.MERITO, votos="S" * 8 + "NN" + "NNNN")
    leitura = perfil.ler_votacao(v, siglas, r)
    assert leitura.opostos and leitura.racha == {"PSOL": "direita"} and not leitura.divisiva
    # o mesmo placar com o PSOL junto do PT: esquerda × direita de verdade
    v.votos = "S" * 10 + "NNNN"
    leitura = perfil.ler_votacao(v, siglas, r)
    assert leitura.divisiva and leitura.racha == {}
    # partido de um polo dividido (1 × 1) não racha o polo
    v.votos = "S" * 8 + "SN" + "NNNN"
    assert perfil.ler_votacao(v, siglas, r).divisiva


def test_o_saldo_de_quem_racha_conta_so_esquerda_contra_direita():
    """O PSOL vota com o PL em duas votações (rachas) e com o PT em duas: o saldo do
    PSOL só vê as duas em que esquerda e direita se opuseram de verdade, e fica −100."""
    r = regras.de_dict(
        {
            "legislaturas": [{"numero": 57, "inicio": "2026-01-01", "fim": "2027-01-31"}],
            "polos": [{"inicio": "2019-01-01", "esquerda": ["PT", "PSOL"], "direita": ["PL"]}],
            "limiares": {"coesao_polo": 0.75, "minimo_polo": 2, "minimo_divisivas": 2, "minimo_partido": 2},
        }
    )
    pessoas = [base.Pessoa(i, f"Deputado {i}", "SP", [(0, "PT" if i <= 8 else "PSOL" if i <= 10 else "PL")]) for i in range(1, 15)]
    racha, junto = "S" * 8 + "NN" + "NNNN", "S" * 10 + "NNNN"
    votacoes = [
        base.Votacao(id=f"v{k}", data=f"2026-03-{k + 1:02d}", orgao=base.PLENARIO, descricao="Aprovado o Projeto.", tipo=base.MERITO, votos=votos)
        for k, votos in enumerate([racha, racha, junto, junto])
    ]
    perfis = perfil.calcular([base.AnoVotos(casa="cd", ano=2026, pessoas=pessoas, votacoes=votacoes)], r)
    psol = perfis[9].legislaturas[57].eixo
    assert (psol.esq, psol.dir, psol.saldo) == (2, 0, -100.0)
    assert perfis[1].legislaturas[57].eixo.saldo == -100.0 and perfis[11].legislaturas[57].eixo.saldo == 100.0


def test_fora_da_linha_por_tema(perfis: dict[int, perfil.Perfil], r: regras.Regras):
    """João (PP) votou contra o PP fechado em V1 (economia): uma dissidência, no tema da
    matéria. Com saldo +100, nunca ficou com a esquerda."""
    assert perfis[10].fora[57] == [1, 0] and perfis[10].fora_tema[57] == {"economia": [1, 0]}
    assert 57 not in perfis[1].fora  # Ana nunca saiu da linha
    # quem tem lado definido e vota com o outro polo num tema: conta no tema
    pessoas = [base.Pessoa(i, f"Deputado {i}", "SP", [(0, sigla)]) for i, sigla in ((1, "PT"), (2, "PT"), (3, "PL"), (4, "PL"), (5, "PP"))]
    votacoes = [
        base.Votacao(id=f"v{k}", data=f"2026-03-{k + 1:02d}", orgao=base.PLENARIO, descricao="Aprovado o Projeto.", tipo=base.MERITO,
                     votos="SSNN" + ("S" if k == 3 else "N"), temas=["trabalho" if k == 3 else "economia"])
        for k in range(6)
    ]
    cinco = perfil.calcular([base.AnoVotos(casa="cd", ano=2026, pessoas=pessoas, votacoes=votacoes)], r)[5]
    assert cinco.fora[57] == [0, 1] and cinco.fora_tema[57] == {"trabalho": [0, 1]}


def test_temas_e_tipos_ficam_por_legislatura(perfis: dict[int, perfil.Perfil]):
    gil = perfis[7]
    assert set(gil.temas[57]) == {"economia", "seguranca", "cidades"}
    assert (gil.temas[57]["economia"].eixo.esq, gil.temas[57]["seguranca"].eixo.dir) == (2, 1)
    assert set(gil.tipos[57]) == {base.MERITO, base.DESTAQUE, base.PROCEDIMENTO, base.URGENCIA}
    assert [m.votacao.id for m in gil.recentes] == ["1012-2", "1012-1", "1011-1", "1010-1"]  # texto principal e urgência, do mais novo


def test_bancadas_e_resumo_da_votacao(camara: base.AnoVotos, r: regras.Regras):
    por = {b.sigla: b for b in perfil.bancadas([camara], r, 57)}
    assert (por["PT"].eixo.saldo, por["PL"].eixo.saldo) == (-100.0, 100.0)
    assert round(por["PP"].eixo.saldo) == -33 and por["PP"].membros == 5
    assert por["PT"].governo.pct == 100.0
    indice = next(i for i, v in enumerate(camara.votacoes) if v.id == "1010-1")
    resumo = perfil.resumo_da_votacao(camara.votacoes[indice], camara, indice, r)
    assert (resumo["sim"], resumo["nao"], resumo["governo"], resumo["divisiva"]) == (6, 4, "S", True)
    pp = next(p for p in resumo["partidos"] if p["sigla"] == "PP")
    assert (pp["lado"], pp["coesao"], pp["contra"]) == ("S", 75, [10])


def test_mudanca_recente_exige_15_pontos_e_99_por_cento(r: regras.Regras):
    p = perfil.Perfil(id=1, casa="cd")
    p.semestres["2025-1"] = perfil.Recorte(governo=perfil.Fracao(k=80, n=100))
    p.semestres["2025-2"] = perfil.Recorte(governo=perfil.Fracao(k=40, n=100))
    mudanca = perfil.mudanca_recente(p, r)
    assert mudanca is not None and (mudanca.semestre, round(mudanca.pontos)) == ("2025-2", -40)
    p.semestres["2025-2"] = perfil.Recorte(governo=perfil.Fracao(k=72, n=100))  # 8 pontos: nada a dizer
    assert perfil.mudanca_recente(p, r) is None
    p.semestres["2025-2"] = perfil.Recorte(governo=perfil.Fracao(k=4, n=10))  # poucas votações
    assert perfil.mudanca_recente(p, r) is None


# ── exercício ────────────────────────────────────────────────────────────────


def test_intervalos_do_historico():
    historico = [
        {"dataHora": "2023-02-01T00:00", "situacao": None},
        {"dataHora": "2023-02-01T12:05", "situacao": "Exercício"},
        {"dataHora": "2023-06-01T10:00", "situacao": "Exercício"},  # alteração de partido: não quebra
        {"dataHora": "2023-09-13T14:53", "situacao": "Licença"},
        {"dataHora": "2024-12-03T10:11", "situacao": "Exercício"},
        {"dataHora": "2024-12-06T17:00", "situacao": "Licença"},
        {"dataHora": "2026-03-31T15:28", "situacao": "Exercício"},
    ]
    assert exercicio.do_historico(historico) == [("2023-02-01", "2023-09-13"), ("2024-12-03", "2024-12-06"), ("2026-03-31", "")]
    assert exercicio.do_historico([{"dataHora": "2023-02-01T12:05", "situacao": "Exercício"}, {"dataHora": "2023-06-06T16:54", "situacao": "VACANCIA"}]) == [
        ("2023-02-01", "2023-06-06")
    ]
    assert exercicio.fechados({1: [("2026-03-31", "")]}, "2026-11-13") == {1: [("2026-03-31", "2026-11-13")]}


def test_atualizar_so_consulta_quem_falta_ou_mudou_de_estado(coletor: fontes.Coletor, tmp_path: Path):
    fetch = coletor.fetch
    primeiro = exercicio.atualizar(coletor, {}, [1, 11], {1, 11})
    assert primeiro == {1: [("2023-02-01", "")], 11: [("2026-06-01", "")]}
    assert sum("/historico" in u for u in fetch.pedidos) == 2
    fetch.pedidos.clear()
    assert exercicio.atualizar(coletor, primeiro, [1, 11], {1, 11}) == primeiro
    assert not any("/historico" in u for u in fetch.pedidos)  # nada mudou: nenhuma consulta
    exercicio.atualizar(coletor, primeiro, [1, 11], {1})  # o 11 saiu da lista dos que estão em exercício
    assert [u for u in fetch.pedidos if "/historico" in u] == [f"{fabrica.CAMARA}/api/v2/deputados/11/historico"]
    arquivo = tmp_path / exercicio.ARQUIVO
    arquivo.write_text(exercicio.serializar(primeiro, {9: [("2025-02-01", "")]}), encoding="utf-8")
    assert exercicio.ler(tmp_path) == primeiro and exercicio.ler_presidencia(tmp_path) == {9: [("2025-02-01", "")]}


def test_presidencia_da_camara(coletor: fontes.Coletor):
    assert exercicio.presidencia(coletor, [57]) == {9: [("2025-02-01", "")]}


def test_o_placar_separa_a_obstrucao_do_nao_e_diz_o_quorum(r: regras.Regras):
    """O placar sai como a Mesa anuncia: a obstrução fica fora do Sim e do Não (na conta de
    que lado cada um ficou ela vale como Não). E a proposta de emenda à Constituição e o
    projeto de lei complementar dizem quantos votos Sim a Constituição exige."""
    assert (base.quorum("cd", "PEC 3/2021"), base.quorum("sf", "PEC 66/2023 (fase 2)")) == (308, 49)  # três quintos
    assert (base.quorum("cd", "PLP 281/2019"), base.quorum("sf", "PLP 68/2024")) == (257, 41)  # maioria absoluta
    assert base.quorum("cd", "PL 10/2026") is None and base.quorum("cd", "") is None and base.quorum("xx", "PEC 1/2026") is None
    pessoas = [base.Pessoa(i, f"Deputado {i}", "SP", [(0, sigla)]) for i, sigla in ((1, "PT"), (2, "PT"), (3, "PT"), (4, "PL"), (5, "PL"), (6, "PL"), (7, "PP"))]
    v = base.Votacao(id="v1", data="2026-03-02", orgao=base.PLENARIO, descricao="Rejeitada a Subemenda Substitutiva. Sim: 3; Não: 2; Total: 5.",
                     tipo=base.MERITO, prop="PLP 281/2019", aprovada=False, votos="SSSNNOA")
    ano = base.AnoVotos(casa="cd", ano=2026, pessoas=pessoas, votacoes=[v])
    na_ficha = materias.votacao(v, ano, 0, r)
    assert (na_ficha["s"], na_ficha["n"], na_ficha["o"], na_ficha["a"], na_ficha["q"]) == (3, 2, 1, 1, 257)
    assert na_ficha["pp"] == [["PT", 3, 0, "e"], ["PL", 0, 3, "d"]]  # no lado de cada polo, a obstrução conta como Não
    na_lista = perfil.resumo_da_votacao(v, ano, 0, r)
    assert (na_lista["sim"], na_lista["nao"], na_lista["obstrucao"], na_lista["abstencao"], na_lista["quorum"]) == (3, 2, 1, 1, 257)
    # o requerimento (urgência, retirada de pauta) decide por maioria dos presentes, mesmo numa PEC
    urgencia = base.Votacao(id="v2", data="2026-03-03", orgao=base.PLENARIO, descricao="Aprovado o Requerimento de Urgência.", tipo=base.URGENCIA, prop="PEC 3/2021", votos="SSSNNNS")
    ano.votacoes.append(urgencia)
    assert "q" not in materias.votacao(urgencia, ano, 1, r) and perfil.resumo_da_votacao(urgencia, ano, 1, r)["quorum"] == 0


def test_a_votacao_que_nao_bate_com_o_placar_anunciado_sai_das_contas():
    """A Câmara publica o placar na descrição e, à parte, o voto de cada um. Quando os dois
    não batem (ou não há voto de ninguém), a votação sai; os índices dos partidos recuam."""
    assert base.placar_anunciado("Rejeitada a Emenda nº 40. Sim: 35; não: 375; total: 410.") == (35, 375)
    assert base.placar_anunciado("Aprovado o Requerimento de Urgência.") is None
    pessoas = [base.Pessoa(1, "Ana", "SP", [(0, "PT")]), base.Pessoa(2, "Bia", "SP", [(0, "PL"), (2, "PP"), (3, "MDB")]), base.Pessoa(3, "Caio", "RJ", [(0, "PL")])]

    def votacao(k: int, descricao: str, votos: str) -> base.Votacao:
        return base.Votacao(id=f"v{k}", data=f"2026-03-{k + 1:02d}", orgao=base.PLENARIO, descricao=descricao, tipo=base.MERITO, votos=votos)

    ano = base.AnoVotos(casa="cd", ano=2026, pessoas=pessoas, votacoes=[
        votacao(0, "Aprovado o Projeto. Sim: 2; Não: 1; Total: 3.", "SSN"),  # bate
        votacao(1, "Rejeitada a Emenda. Sim: 35; Não: 375; Total: 410.", "SSN"),  # os votos são de outra votação
        votacao(2, "Aprovado o Projeto. Sim: 3; Não: 2; Total: 5.", "SSN"),  # diferença dentro da tolerância
        votacao(3, "Escolhido o ministro. Sim: 404; Não: 61; Total: 465.", "---"),  # secreta: sem o voto de ninguém
        votacao(4, "Aprovado o Projeto.", "SNN"),  # sem placar na descrição: nada a conferir
    ])
    assert [base.fora_do_placar(v, 2) for v in ano.votacoes] == [False, True, False, True, False]
    retiradas = base.sem_as_que_nao_batem(ano, 2)
    assert [v.id for v in retiradas] == ["v1", "v3"] and [v.id for v in ano.votacoes] == ["v0", "v2", "v4"]
    # Bia entrou no PP na votação v2 (que agora é a de índice 1) e no MDB na v3, que saiu: o MDB passa a valer a partir da v4 (índice 2)
    assert pessoas[1].partidos == [(0, "PL"), (1, "PP"), (2, "MDB")]
    assert [pessoas[1].partido_em(k) for k in range(3)] == ["PL", "PP", "MDB"]
    assert base.sem_as_que_nao_batem(ano, 2) == []  # nada mais a tirar
