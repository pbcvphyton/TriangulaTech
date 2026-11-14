"""As folhas da aba "Votações e matérias": cada matéria como documento, com o carimbo do
resultado, a pilha das votações, a cor do tema e o placar ao lado
(qijournal.congresso.materias: folhas, capa; qijournal.render.congresso: _folha, _placar, carimbo).

Dados fabricados, sem rede.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from qijournal.config import Config, load_config
from qijournal.congresso import base, dados, materias, regras
from qijournal.render import congresso as pagina
from tests.fixtures.congresso import fabrica

MODELOS = Path(pagina.__file__).parent / "templates"


@pytest.fixture
def r() -> regras.Regras:
    return fabrica.regras_de_teste()


# ── a votação da capa ────────────────────────────────────────────────────────


def _v(vid: str, tipo: str, rf: bool = False) -> dict:
    return {"id": vid, "t": tipo, **({"rf": 1} if rf else {})}


@pytest.mark.parametrize(
    ("votos", "esperado"),
    [
        ([_v("a", base.MERITO)], "a"),
        # o texto principal vale mais que a urgência antes dele e que o destaque depois
        ([_v("u", base.URGENCIA), _v("m", base.MERITO), _v("d", base.DESTAQUE)], "m"),
        # na PEC, o último turno
        ([_v("t1", base.MERITO), _v("d", base.DESTAQUE), _v("t2", base.MERITO)], "t2"),
        # a redação final só confirma o texto: perde para o texto principal, ganha do destaque
        ([_v("m", base.MERITO), _v("rf", base.MERITO, rf=True)], "m"),
        ([_v("d", base.DESTAQUE), _v("rf", base.MERITO, rf=True)], "rf"),
        # sem texto principal: o destaque; sem ele, o requerimento mais recente
        ([_v("u", base.URGENCIA), _v("d", base.DESTAQUE), _v("p", base.PROCEDIMENTO)], "d"),
        ([_v("u", base.URGENCIA), _v("p", base.PROCEDIMENTO)], "p"),
        # a escolha de autoridade é o texto principal dela
        ([_v("p", base.PROCEDIMENTO), _v("au", base.AUTORIDADE)], "au"),
        ([], ""),
    ],
)
def test_a_capa_e_a_votacao_do_texto_principal(votos: list[dict], esperado: str):
    assert materias.capa(votos) == esperado


@pytest.mark.parametrize(
    ("descricao", "abertura", "esperado"),
    [
        ("Aprovada a Redação Final assinada pelo relator.", "", True),
        ("Aprovada, a Redação Final.", "", True),
        ("Aprovado o Projeto.", "Votação da Redação Final.", True),
        ("Aprovado o Substitutivo, ressalvados os destaques.", "", False),
        ("Aprovado o Projeto. Vai à redação final.", "", False),
    ],
)
def test_a_redacao_final_sai_da_descricao_oficial(descricao: str, abertura: str, esperado: bool):
    assert materias.redacao_final(descricao, abertura) is esperado


# ── o carimbo ────────────────────────────────────────────────────────────────


def test_o_carimbo_diz_a_palavra_e_sobre_o_que():
    texto = pagina.objeto_do_voto(base.MERITO)
    assert pagina.carimbo(texto, True) == {"palavra": "Aprovado", "que": "o texto principal", "classe": "ok"}
    assert pagina.carimbo(texto, False, turno=2) == {"palavra": "Rejeitado", "que": "o texto principal, em 2º turno", "classe": "no"}
    # no destaque de um trecho, o Sim é manter o trecho: perder é o trecho sair do texto
    trecho = pagina.objeto_do_voto(base.DESTAQUE, "trecho")
    assert (pagina.carimbo(trecho, True)["palavra"], pagina.carimbo(trecho, False)["palavra"]) == ("Mantido", "Retirado")
    assert pagina.carimbo(pagina.objeto_do_voto(base.DESTAQUE, "emenda"), False)["que"] == "a emenda"
    assert pagina.objeto_do_voto(base.MERITO, redacao=True) == "redacao"
    assert pagina.objeto_do_voto(base.PROCEDIMENTO) == "requerimento" and pagina.objeto_do_voto(base.URGENCIA) == "urgencia"
    assert pagina.objeto_do_voto("coisa nova") == "outro"
    # sem resultado no registro, não há o que carimbar
    assert pagina.carimbo(texto, None) is None


def test_o_quadro_de_carimbos_do_script_e_o_mesmo_da_pagina():
    """As folhas pequenas da busca são montadas pelo script, que tem o seu quadro de carimbos:
    tem de dizer o mesmo que o da página."""
    script = (MODELOS / "congresso_materias.js.j2").read_text(encoding="utf-8")
    quadro = script.split("var CARIMBOS = {", 1)[1].split("};", 1)[0]
    achados = {chave: tuple(re.findall(r"'([^']*)'", valores)) for chave, valores in re.findall(r"(\w+): \[([^\]]*)\]", quadro)}
    assert achados == pagina.CARIMBOS


# ── o placar ─────────────────────────────────────────────────────────────────


def test_o_placar_da_lei_complementar_diz_quantos_votos_faltaram():
    """O PLP 281/2019 teve 252 Sim e 176 Não e foi rejeitado: precisava de 257."""
    p = pagina._placar({"sim": 252, "nao": 176, "quorum": 257, "tipo": base.MERITO}, "cd", "PLP")
    assert (p["sim"], p["nao"], p["quorum"], p["faltaram"], p["passou_do_quorum"]) == (252, 176, 257, 5, False)
    assert p["exigencia"] == "mais da metade dos 513 deputados, exigida para lei complementar"
    assert not p["sem_numero"]
    # a barra são as 513 cadeiras: a marca do quórum fica logo depois do fim do Sim
    assert p["w_sim"] == round(100 * 252 / 513, 2) and p["q_left"] == round(100 * 257 / 513, 2)
    passou = pagina._placar({"sim": 318, "nao": 113, "quorum": 257, "tipo": base.MERITO}, "cd", "PLP")
    assert (passou["faltaram"], passou["passou_do_quorum"]) == (0, True)


def test_o_placar_da_pec_no_senado_e_da_lei_comum():
    pec = pagina._placar({"sim": 45, "nao": 30, "quorum": 49, "tipo": base.MERITO}, "sf", "PEC")
    assert pec["faltaram"] == 4 and pec["exigencia"] == "três quintos dos 81 senadores, exigidos para mudar a Constituição"
    # lei comum: não há número fixo, decide a maioria de quem votou
    comum = pagina._placar({"sim": 200, "nao": 150, "tipo": base.MERITO}, "cd", "PL")
    assert comum["sem_numero"] and not comum["quorum"] and comum["exigencia"] == ""
    # requerimento não é votação de conteúdo: a página não fala de quórum
    assert not pagina._placar({"sim": 200, "nao": 150, "tipo": base.URGENCIA}, "cd", "PL")["sem_numero"]


def test_o_placar_deixa_a_obstrucao_e_a_abstencao_fora_do_sim_e_do_nao():
    p = pagina._placar({"sim": 300, "nao": 100, "obstrucao": 60, "abstencao": 3, "tipo": base.MERITO}, "cd", "PL")
    assert (p["sim"], p["nao"], p["obstrucao"], p["abstencao"]) == (300, 100, 60, 3)
    assert p["w_obs"] == round(100 * 60 / 513, 2) and p["w_nao"] == round(100 * 100 / 513, 2)
    assert round(p["w_sim"] + p["w_nao"] + p["w_obs"], 1) <= 100


def test_o_placar_anunciado_vale_quando_difere_da_contagem_um_a_um():
    """A Mesa anunciou 252 × 176; no registro do voto de cada um aparecem 250 × 176. Vale o
    anunciado (é ele que decide), e a página mostra também a contagem do registro."""
    p = pagina._placar({"sim": 250, "nao": 176, "an": [252, 176], "quorum": 257, "tipo": base.MERITO}, "cd", "PLP")
    assert (p["sim"], p["nao"], p["faltaram"]) == (252, 176, 5)
    assert p["registrado"] == {"sim": 250, "nao": 176}
    assert pagina._placar({"sim": 252, "nao": 176, "tipo": base.MERITO}, "cd", "PLP")["registrado"] is None
    # votação secreta sem placar no registro: não há números a mostrar
    secreta = pagina._placar({"sim": 0, "nao": 0, "sec": 1, "votaram": 70, "tipo": base.AUTORIDADE}, "sf", "MSF")
    assert secreta["secreta"] and not secreta["tem_placar"] and secreta["votaram"] == 70


# ── as folhas (os dados) ─────────────────────────────────────────────────────


def _congresso_de_papel() -> base.AnoVotos:
    """Dez deputados (PT 1 a 4, PL 5 a 8, PP 9 e 10) e seis votações."""
    pessoas = [base.Pessoa(i, f"Deputado {i}", "SP", [(0, "PT" if i <= 4 else "PL" if i <= 8 else "PP")]) for i in range(1, 11)]

    def v(vid: str, data: str, hora: str, prop: str, tipo: str, descricao: str, votos: str, **mais) -> base.Votacao:
        return base.Votacao(id=vid, data=data, hora=hora, orgao=mais.pop("orgao", base.PLENARIO), descricao=descricao, tipo=tipo, votos=votos, prop=prop, **mais)

    votacoes = [
        v("velha", "2025-12-01", "10:00", "PL 1/2025", base.MERITO, "Aprovado o Projeto.", "SSSSNNNNSS", aprovada=True),  # outra legislatura
        v("lei", "2026-03-01", "15:00", "PL 7/2026", base.MERITO, "Aprovado o Projeto. Sim: 6; Não: 4; Total: 10.", "SSSSNNNNSS", aprovada=True,
          ementa="Cria o programa de teste.", temas=["cidades"]),
        v("comissao", "2026-03-02", "09:00", "PL 7/2026", base.MERITO, "Aprovado o Parecer.", "SSSSNNNNSS", aprovada=True, orgao="CCJC"),
        v("req", "2026-03-05", "16:00", "PLP 5/2026", base.PROCEDIMENTO, "Rejeitado o Requerimento. Sim: 4; Não: 6; Total: 10.", "NNNNSSSSNN", aprovada=False),
        # o PT fecha no Sim com um voto contra (75% ≥ 70%); o PL fica no Não, com um em obstrução
        v("plp", "2026-03-05", "18:30", "PLP 5/2026", base.MERITO, "Rejeitado o Projeto de Lei Complementar. Sim: 5; Não: 4; Total: 9.", "SSSNNNNOSS", aprovada=False),
        v("nome", "2026-03-09", "11:00", "MSF 3/2026", base.AUTORIDADE, "Votação da indicação.", "VVVVVVVV--", aprovada=True, secreta=True),
    ]
    return base.AnoVotos(casa="cd", ano=2026, pessoas=pessoas, votacoes=votacoes)


@pytest.fixture
def folhas(r: regras.Regras) -> list[dict]:
    ano = _congresso_de_papel()
    fichas = {"PLP 5/2026": {"e": "Dispõe sobre o teste das folhas.", "au": "Poder Executivo", "da": "2026-01-10", "tm": ["economia", "politica"], "u": "https://www.camara.leg.br/x"}}
    return materias.folhas([ano], r, 57, fichas, {p.id: p.nome for p in ano.pessoas})


def test_as_folhas_sao_as_materias_votadas_por_ultimo_no_plenario(folhas: list[dict], r: regras.Regras):
    # da mais recente; só o Plenário e só a legislatura pedida (a de 2025 e a da comissão ficam fora)
    assert [(f["t"], f["n"], f["cp"]) for f in folhas] == [("MSF 3/2026", 1, "nome"), ("PLP 5/2026", 2, "plp"), ("PL 7/2026", 1, "lei")]
    ano = _congresso_de_papel()
    so_duas = materias.folhas([ano], r, 57, {}, {}, quantas=2)
    assert [f["t"] for f in so_duas] == ["MSF 3/2026", "PLP 5/2026"]
    # com lugar para uma votação só por folha, fica a da capa (a pilha continua dizendo quantas houve)
    uma = materias.folhas([ano], r, 57, {}, {}, por_folha=1)[1]
    assert (uma["n"], [v["id"] for v in uma["vot"]]) == (2, ["plp"])


def test_a_folha_traz_o_documento_e_o_placar_com_a_obstrucao_a_parte(folhas: list[dict]):
    plp = folhas[1]
    # o documento vem da ficha da matéria; o que a ficha não tem, da própria votação
    assert (plp["e"], plp["au"], plp["da"], plp["tm"]) == ("Dispõe sobre o teste das folhas.", "Poder Executivo", "2026-01-10", ["economia", "politica"])
    assert (folhas[2]["e"], folhas[2]["tm"]) == ("Cria o programa de teste.", ["cidades"])
    assert [v["id"] for v in plp["vot"]] == ["req", "plp"]  # em ordem
    voto = plp["vot"][1]
    # a obstrução fica fora do Sim e do Não; o quórum da lei complementar vai junto
    assert (voto["sim"], voto["nao"], voto["obstrucao"], voto["quorum"], voto["aprovada"]) == (5, 4, 1, 257, False)
    assert plp["vot"][0]["quorum"] == 0  # o requerimento não tem número fixo
    assert "an" not in voto  # o placar anunciado é igual à contagem: não há o que avisar
    # quem saiu da linha: só onde o partido votou fechado (aqui, 70% ou mais do mesmo lado)
    assert plp["fechado"] == 70
    partidos = {p["sigla"]: p for p in voto["partidos"]}
    assert (partidos["PT"]["lado"], partidos["PT"]["coesao"], partidos["PT"]["contra"]) == ("S", 75, [{"id": 4, "nome": "Deputado 4"}])
    assert (partidos["PL"]["lado"], partidos["PL"]["contra"]) == ("N", [])  # a obstrução acompanha o Não do partido
    # a votação secreta entra, sem placar e sem o voto de ninguém
    secreta = folhas[0]["vot"][0]
    assert secreta["sec"] == 1 and (secreta["sim"], secreta["nao"], secreta["votaram"]) == (0, 0, 8) and secreta["partidos"] == []


def test_a_votacao_guarda_o_placar_anunciado_so_quando_ele_difere(r: regras.Regras):
    ano = _congresso_de_papel()
    k = next(i for i, v in enumerate(ano.votacoes) if v.id == "plp")
    assert "an" not in materias.votacao(ano.votacoes[k], ano, k, r)
    ano.votacoes[k].descricao = "Rejeitado o Projeto de Lei Complementar. Sim: 6; Não: 4; Total: 10."
    curta = materias.votacao(ano.votacoes[k], ano, k, r)
    assert (curta["s"], curta["n"], curta["an"]) == (5, 4, [6, 4])


# ── as folhas (a página) ─────────────────────────────────────────────────────

TEMAS = {"economia": {"id": "economia", "nome": "Economia e finanças", "curto": "Economia"}, "politica": {"id": "politica", "nome": "Política"}}


def test_a_folha_da_pagina_tem_o_carimbo_da_capa_a_pilha_e_o_tema(folhas: list[dict]):
    f = pagina._folha(folhas[1], "cd", TEMAS, {"economia"})
    assert (f["id"], f["chave"], f["casa"], f["tipo"]) == ("cg-fo-cd-PLP-5-2026", "cd-PLP-5-2026", "Câmara dos Deputados", "projeto de lei complementar")
    assert (f["sigla"], f["numero"], f["ementa"]) == ("PLP", "5/2026", "Dispõe sobre o teste das folhas.")
    # a capa é a do texto principal; ela vem primeiro, e depois as outras, da mais recente
    assert f["capa"]["id"] == "plp" and [v["id"] for v in f["votos"]] == ["plp", "req"]
    assert f["capa"]["carimbo"] == {"palavra": "Rejeitado", "que": "o texto principal", "classe": "no"}
    assert f["votos"][1]["carimbo"] == {"palavra": "Rejeitado", "que": "o requerimento", "classe": "no"}
    # a pilha: uma folha atrás por votação além da capa (no máximo três); quantas, vai escrito
    assert (f["n"], f["n_txt"], f["pilha"]) == (2, "2 votações nominais", 1)
    assert pagina._folha({**folhas[1], "n": 9}, "cd", TEMAS, set())["pilha"] == pagina.PILHA
    # o tema: a cor é a do primeiro, e o nome vai sempre escrito (o curto, quando há)
    assert f["tema"] == "economia" and [t["nome"] for t in f["temas"]] == ["Economia", "Política"]
    # o placar da capa: teve mais Sim que Não, mas precisava de 257
    p = f["capa"]["placar"]
    assert (p["sim"], p["nao"], p["obstrucao"], p["quorum"], p["faltaram"]) == (5, 4, 1, 257, 252)
    # as duas votações são do mesmo dia: a aba de cada uma mostra a hora
    assert [v["quando"] for v in f["votos"]] == ["18:30", "16:00"]
    # os lados: a esquerda (PT) no Sim, a direita (PL) no Não; o Governo não orientou
    assert (f["capa"]["governo"], f["capa"]["esquerda"], f["capa"]["direita"], f["capa"]["divisiva"]) == ("", "Sim", "Não", True)
    # quem saiu da linha, com o endereço do dossiê
    assert f["capa"]["dissidentes"] == [{"sigla": "PT", "lado": "Sim", "nomes": [{"k": "cd-4", "nome": "Deputado 4"}], "mais": 0}]
    assert f["capa"]["fora_da_linha"] == 1
    # "ver quem votou, um a um": só a votação de conteúdo que opôs os polos, num tema que a outra aba oferece
    assert f["capa"]["um_a_um"] == "#v=posicoes&c=cd&t=economia&m=cd-PLP-5-2026&vt=plp"
    assert f["votos"][1]["um_a_um"] == ""
    assert pagina._folha(folhas[1], "cd", TEMAS, set())["capa"]["um_a_um"] == ""


def test_a_folha_sem_tema_e_a_votacao_secreta(folhas: list[dict]):
    f = pagina._folha(folhas[0], "sf", TEMAS, set())
    assert (f["tema"], f["temas"], f["pilha"], f["n_txt"]) == ("", [], 0, "1 votação nominal")
    assert f["capa"]["carimbo"] == {"palavra": "Aprovada", "que": "a indicação", "classe": "ok"}
    assert f["capa"]["placar"]["secreta"] and not f["capa"]["placar"]["tem_placar"]
    assert f["capa"]["partidos"] == [] and f["capa"]["um_a_um"] == ""
    # na votação secreta não há lado de ninguém a mostrar (nem um traço no lugar do Governo)
    assert (f["capa"]["governo"], f["capa"]["esquerda"], f["capa"]["direita"]) == ("", "", "")


def test_dados_gravados_antes_das_folhas_ainda_viram_folhas():
    """Sem ``folhas`` no painel (dados antigos), a página monta as folhas das últimas votações."""
    ultimas = [
        {"id": "b", "data": "2026-03-05", "tipo": base.MERITO, "prop": "PL 7/2026", "ementa": "Cria o programa.", "temas": ["economia"], "aprovada": True, "sim": 6, "nao": 4},
        {"id": "a", "data": "2026-03-04", "tipo": base.URGENCIA, "prop": "PL 7/2026", "ementa": "Cria o programa.", "temas": ["economia"], "aprovada": True, "sim": 7, "nao": 3},
    ]
    saida = pagina._folhas({"ultimas": ultimas}, "cd", [TEMAS["economia"]])
    assert len(saida) == 1 and (saida[0]["titulo"], saida[0]["n"], saida[0]["capa"]["id"]) == ("PL 7/2026", 2, "b")
    assert [v["id"] for v in saida[0]["votos"]] == ["b", "a"]


# ── a aba sem script ─────────────────────────────────────────────────────────


@pytest.fixture
def html(tmp_path: Path, r: regras.Regras) -> str:
    data_dir = tmp_path / "data"
    (data_dir / "busca").mkdir(parents=True)
    (data_dir / "busca" / "2026-11-12.json").write_text(json.dumps(fabrica.busca_do_dia()), encoding="utf-8")
    dados.atualizar(tmp_path, data_dir, tmp_path / "cache", dados.Opcoes(hoje=fabrica.HOJE, projetos_desde=2026), fetch=fabrica.FetchFalso(), regras=r)
    config: Config = load_config(env={})
    pagina_ = pagina.render_congresso_page(config, data_dir, home_href="../", archive_href="./")
    assert pagina_ is not None
    return pagina_


def test_as_folhas_saem_inteiras_sem_script(html: str):
    secao = html.split('data-vista="votacoes"', 2)[2].split('data-vista="pessoas"', 1)[0]
    mesa = secao.split('id="cg-mesa-cd"', 1)[1].split('id="cg-materias-cd"', 1)[0]
    folhas = re.findall(r'<li class="cg-me-i" id="(cg-fo-[^"]+)"([^>]*)>', mesa)
    assert folhas and all("hidden" not in resto for _id, resto in folhas)  # todas à vista, uma depois da outra
    assert 'id="cg-fo-cd-PL-12-2026"' in mesa
    folha = mesa.split('id="cg-fo-cd-PL-12-2026"', 1)[1].split('<li class="cg-me-i"', 1)[0]
    for trecho in (
        '<p class="cg-fo-casa">Câmara dos Deputados</p>',  # o cabeçalho de documento oficial
        '<p class="cg-fo-tipo">projeto de lei</p>',  # o tipo por extenso (o estilo põe em maiúsculas)
        'data-m="cd-PL-12-2026"',  # o número abre a ficha (sem script, a página oficial da matéria)
        ">PL 12/2026</a></h5>",
        'class="cg-fo-et"',  # a ementa oficial, como texto do documento
        'class="cg-cb cg-cb-',  # o carimbo, em texto de verdade
        'class="cg-pc-n"',  # o placar ao lado
        "O que se votou", "De que lado ficaram", "Os partidos", "Quem saiu da linha",
    ):
        assert trecho in folha, trecho
    # o tema vai sempre escrito ao lado da cor
    assert re.search(r'<span class="cg-tr cg-t-\w+ cg-tr-1"[^>]*><i aria-hidden="true"></i>[^<]+</span>', folha)
    # um carimbo à vista por folha (o da capa); os das outras votações esperam a troca de votação
    carimbos = re.findall(r'<p class="cg-cb [^"]*" data-vt="[^"]*"( hidden)?>', folha)
    assert carimbos.count("") == 1
    # cada miniatura leva à sua folha
    assert all(f'href="#{fid}" data-folha="{fid}"' in mesa for fid, _resto in folhas)
    # a busca e os atalhos que dependem do script saem escondidos
    assert re.search(r'id="cg-materias-cd" data-materias="cd" hidden', secao)
    assert "data-com-script hidden" in mesa
    # os dois estilos estão na página, e o script do foco também
    assert ".cg-fo{" in html and "--cg-t-economia:" in html and "aoEndereco.votacoes" in html


def test_o_script_das_folhas_nao_monta_html():
    for nome in ("congresso_papel.js.j2", "congresso_materias.js.j2"):
        assert "innerHTML" not in (MODELOS / nome).read_text(encoding="utf-8"), nome


# ── as cores dos temas ───────────────────────────────────────────────────────


def _variaveis(css: str, escuro: bool) -> dict[str, str]:
    """As variáveis de cor de um bloco ``:root{…}`` (claro) ou ``:root[data-theme="dark"]{…}``."""
    seletor = r':root\[data-theme="dark"\]\{([^}]*)\}' if escuro else r":root\{([^}]*)\}"
    css = re.sub(r"\{\{.*?\}\}", "x", css)  # as expressões do modelo têm chaves: saem antes de procurar o bloco
    return {nome: cor.lower() for bloco in re.findall(seletor, css) for nome, cor in re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{3,6})\b", bloco)}


def _luminancia(cor: str) -> float:
    cor = cor.lstrip("#")
    if len(cor) == 3:
        cor = "".join(c * 2 for c in cor)
    canais = [int(cor[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canais]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contraste(a: str, b: str) -> float:
    la, lb = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("escuro", [False, True])
def test_cada_tema_tem_cor_com_contraste_no_claro_e_no_escuro(escuro: bool):
    """Uma cor por tema de config/congresso.yaml, com 3:1 ou mais contra o papel da folha, a
    página e o fundo suave (a cor pinta faixas e quadradinhos; o nome do tema vai sempre escrito)."""
    raiz = Path(__file__).resolve().parents[1]
    temas = [t.id for t in regras.carregar(raiz).temas]
    cores = _variaveis((MODELOS / "congresso_temas.css.j2").read_text(encoding="utf-8"), escuro)
    papel = _variaveis((MODELOS / "congresso_papel.css.j2").read_text(encoding="utf-8"), escuro)["--cg-fo-papel"]
    base_css = _variaveis((MODELOS / "styles.css.j2").read_text(encoding="utf-8"), escuro)
    fundos = {"papel": papel, "página": base_css["--paper"], "fundo suave": base_css["--soft"]}
    assert len(temas) == 13
    for tema in [*temas, "0"]:  # "0": sem tema
        cor = cores[f"--cg-t-{tema}"]
        for nome, fundo in fundos.items():
            assert _contraste(cor, fundo) >= 3, (tema, cor, nome, fundo, round(_contraste(cor, fundo), 2))
    # e a classe que põe a cor: cg-t-<id>
    css = (MODELOS / "congresso_temas.css.j2").read_text(encoding="utf-8")
    assert all(f".cg-t-{tema}{{--cg-t:var(--cg-t-{tema})}}" in css for tema in temas)
