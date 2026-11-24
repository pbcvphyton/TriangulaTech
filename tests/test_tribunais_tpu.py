"""A TPU compacta (data/tribunais/tpu/), o casamento de caminho em texto do STF e a atualização a
partir do dump do SGT."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qijournal.tribunais import tpu
from qijournal.tribunais.tpu import TPU
from tests.fixtures import tribunais as fx

TETO_JSON = 300 * 1024  # bytes por arquivo de data/tribunais/ (brief, §4)


@pytest.fixture(scope="module")
def t() -> TPU:
    return TPU.carregar()


# ── a cópia versionada ───────────────────────────────────────────────────────────────────────


def test_a_copia_versionada_e_a_versao_85_inteira(t: TPU) -> None:
    assert t.versao["versao"] == 85 and t.versao["data"] == "2026-11-05"
    assert len(t) == 5650 and t.versao["assuntos_ativos"] == 4986 and t.versao["classes"] == 849
    assert sorted(t.raizes())[:4] == [14, 195, 287, 864] and len(t.raizes()) == 22


def test_os_arquivos_cabem_no_teto_e_tem_um_item_por_linha() -> None:
    for nome in ("assuntos.json", "classes.json", "versao.json"):
        caminho = tpu.PASTA / nome
        assert caminho.stat().st_size <= TETO_JSON, f"{nome}: {caminho.stat().st_size} bytes"
    linhas = (tpu.PASTA / "assuntos.json").read_text(encoding="utf-8").splitlines()
    assert linhas[0] == "[" and linhas[-1] == "]" and len(linhas) == 5650 + 2
    assert linhas[1] == '[14,null,"DIREITO TRIBUTÁRIO"],'


def test_cadeia_nome_e_caminho(t: TPU) -> None:
    assert t.cadeia(5779) == (899, 5626, 5779)
    assert t.caminho(5779) == "DIREITO CIVIL › Família › Alimentos"
    assert t.raiz(3608) == 287 and t.nivel(3608) == 4  # Penal › Legislação Extravagante › Drogas › Tráfico
    assert t.nome(14) == "DIREITO TRIBUTÁRIO" and t.cadeia(99999999) == () and t.caminho(None) is None


def test_inativos_ficam_e_entidades_html_sao_desfeitas(t: TPU) -> None:
    assert t.nome(2663) == "Abono Pecuniário" and not t.ativo(2663) and t.ativo(13810)
    assert t.nome(3628) == 'Crimes de "Lavagem" ou Ocultação de Bens, Direitos ou Valores'


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("10288", 10288),
        (" 910288 ", 10288),  # o STJ repete o código com o prefixo 9 na lista
        ("9607", 9607),  # começa com 9 e existe: fica como está
        (5779, 5779),
        ("Nao Informado", None),
        ("", None),
        (None, None),
        ("210303", None),  # não existe nem sem o primeiro dígito (só o 9 é prefixo)
        (True, None),
    ],
)
def test_normalizar_codigo(t: TPU, valor: object, esperado: int | None) -> None:
    assert t.normalizar_codigo(valor) == esperado


# ── o caminho em texto do STF ────────────────────────────────────────────────────────────────


def test_o_exemplo_do_brief_casa_ate_a_folha(t: TPU) -> None:
    c = t.casar_caminho("1 - DIREITO PROCESSUAL PENAL || MEDIDAS ASSECURATÓRIAS || BUSCA E APREENSÃO DE BENS")
    assert (c.profundidade, c.niveis, c.folha) == (3, 3, True)
    assert t.cadeia(c.codigo)[0] == 1209 and t.nome(c.codigo) == "Busca e Apreensão de Bens"
    assert c.texto == "DIREITO PROCESSUAL PENAL || MEDIDAS ASSECURATÓRIAS || BUSCA E APREENSÃO DE BENS"


def test_aspas_acento_caixa_e_espacos_nao_atrapalham(t: TPU) -> None:
    aspas = t.casar_caminho('2 - DIREITO PENAL || CRIMES PREVISTOS NA LEGISLAÇÃO EXTRAVAGANTE || CRIMES DE "LAVAGEM" OU OCULTAÇÃO DE BENS, DIREITOS OU VALORES')
    assert aspas.codigo == 3628 and aspas.folha  # na TPU o nome vem com &quot;
    solto = t.casar_caminho("direito  tributario|| impostos ||ICMS/IMPOSTO SOBRE CIRCULACAO DE MERCADORIAS")
    assert solto.codigo == 5946 and solto.folha


def test_caminho_que_a_tpu_nao_tem_devolve_o_no_mais_fundo(t: TPU) -> None:
    c = t.casar_caminho("1 - DIREITO PROCESSUAL PENAL || PRISÃO PREVENTIVA || REVOGAÇÃO")
    assert (c.codigo, c.profundidade, c.niveis, c.folha) == (4355, 2, 3, False)
    nada = t.casar_caminho("1 - ASSUNTOS DIVERSOS")
    assert (nada.codigo, nada.profundidade) == (None, 0)
    assert t.casar_caminho("").niveis == 0 and t.casar_caminho(None).codigo is None


def test_homonimo_inativo_nao_impede_o_ativo_de_descer() -> None:
    # Duas "Férias" sob o mesmo pai: a inativa sem filhos e a ativa com o filho pedido.
    arvore = TPU({1: (None, "RAIZ", True), 2: (1, "Férias", False), 3: (1, "Férias", True), 4: (3, "Abono", True),
                  5: (2, "Outro", False)}, {})
    assert t_codigo(arvore, "RAIZ || FÉRIAS || ABONO") == (4, 3)
    assert t_codigo(arvore, "RAIZ || FÉRIAS || OUTRO") == (5, 3)  # o inativo vale quando é o único caminho
    assert t_codigo(arvore, "RAIZ || FÉRIAS") == (3, 2)  # empate: o ativo


def t_codigo(arvore: TPU, texto: str) -> tuple[int | None, int]:
    c = arvore.casar_caminho(texto)
    return c.codigo, c.profundidade


def test_linhas_do_stf_separadas_por_retorno_de_carro(t: TPU) -> None:
    celula = "1 - DIREITO TRIBUTÁRIO || IMPOSTOS || ICMS/ IMPOSTO SOBRE CIRCULAÇÃO DE MERCADORIAS\r2 - DIREITO CIVIL"
    assert list(tpu.iterar_linhas_stf(celula)) == [
        "DIREITO TRIBUTÁRIO || IMPOSTOS || ICMS/ IMPOSTO SOBRE CIRCULAÇÃO DE MERCADORIAS", "DIREITO CIVIL"]
    assert t.linhas_de_assunto("a\r\nb\nc\r") == ["a", "b", "c"]


def test_amostra_real_do_stf_casa_quase_tudo(t: TPU) -> None:
    """106 processos reais (22/11/2026), escolhidos para ter de tudo, inclusive os que não casam.
    No acervo inteiro (22.211 processos, 39.310 linhas), 98,98% das linhas casaram até a folha,
    1,00% até o nível 2 ou mais e 0,01% (4 linhas) não casou nada."""
    processos = json.loads(fx.ler("stf_assuntos_amostra.json"))["processos"]
    linhas = [linha for p in processos for linha in tpu.iterar_linhas_stf(p["assuntos"])]
    casamentos = [t.casar_caminho(linha) for linha in linhas]
    folhas = sum(c.folha for c in casamentos)
    assert len(linhas) >= 150 and folhas / len(linhas) >= 0.85
    nada = {c.texto for c in casamentos if c.profundidade == 0}
    assert nada <= {"ASSUNTOS DIVERSOS", "ASSUNTO PARA PROCESSO ANTIGO |||| PROCESSO ANTIGO"}
    assert all(c.profundidade >= 2 for c in casamentos if c.profundidade and not c.folha)


def test_raiz_pelo_nome_do_ramo(t: TPU) -> None:
    assert t.raiz_por_nome("DIREITO TRIBUTÁRIO") == 14
    assert t.raiz_por_nome("Direito administrativo e outras matérias de direito público") == 9985
    assert t.raiz_por_nome("Nao Informado") is None and t.raiz_por_nome(None) is None


# ── classes ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("trib", "sigla", "codigo", "nome"),
    [
        ("stj", "AREsp", 11881, "Agravo em Recurso Especial"),  # "AgREsp" na TPU
        ("stj", "RHC", 1722, "Recurso Ordinário em Habeas Corpus"),
        ("stj", "REsp", 1032, "Recurso Especial"),  # a classe sob a raiz do STJ, não a genérica 213
        ("stj", "APn", 1033, "Ação Penal"),
        ("stj", "CR", 1047, "Carta Rogatória"),
        ("stf", "ACO", 1315, "Ação Cível Originária"),  # "AOC" na TPU
        ("stf", "RE", 1348, "Recurso Extraordinário"),  # a do STF, não a 212
        ("stf", "ARE", 11880, "Recurso Extraordinário com Agravo"),
        ("stf", "ADI", 1314, "Ação Direta de Inconstitucionalidade"),
    ],
)
def test_classe_pela_sigla_do_tribunal(t: TPU, trib: str, sigla: str, codigo: int, nome: str) -> None:
    achada = t.classe_por_sigla(trib, sigla)
    assert achada is not None and (achada["codigo"], achada["nome"]) == (codigo, nome)
    assert t.nome_classe(trib, f" {sigla} ") == nome
    assert t.classe_por_sigla(trib, "XYZ") is None and t.nome_classe(trib, None) is None


# ── o dump do SGT ────────────────────────────────────────────────────────────────────────────


def test_ler_dump_trata_os_vicios_do_arquivo() -> None:
    tabelas = tpu.ler_dump(fx.PASTA / "sgt_dump_mini.sql")
    assert {k: len(v) for k, v in tabelas.items()} == {"ITENS": 11, "ASSUNTOS": 3, "CLASSES": 2}
    hc_stf = next(r for r in tabelas["CLASSES"] if r["cod_classe"] == "1331")
    assert '"d" e "i\' (CF)' in hc_stf["artigo"]  # \" e \' desfeitos
    assert hc_stf["glossario"].startswith("<p>")  # o textoGrande da instrução anterior
    nomes = {r["cod_item"]: r["nome"] for r in tabelas["ITENS"]}
    assert nomes["14"] == "DIREITO TRIBUTÁRIO" and nomes["26"] == "Distribuição"  # latin-1


def test_atualizar_de_dump_refaz_e_e_idempotente(tmp_path: Path) -> None:
    versao = tpu.atualizar_de_dump(fx.PASTA / "sgt_dump_mini.sql", tmp_path)
    assert (versao["versao"], versao["data"], versao["assuntos"], versao["classes"], versao["gravados"]) == (68, "2025-09-11", 6, 3, 3)
    mini = TPU.carregar(tmp_path)
    assert mini.cadeia(5779) == (899, 5626, 5779) and mini.caminho(5946).startswith("DIREITO TRIBUTÁRIO › Impostos")
    assert mini.classe_por_sigla("stf", "HC")["codigo"] == 1331 and mini.classe_por_sigla("stj", "HC")["codigo"] == 1720
    de_novo = tpu.atualizar_de_dump(fx.PASTA / "sgt_dump_mini.sql", tmp_path, data_versao="2026-11-05")
    assert de_novo["gravados"] == 1 and de_novo["data"] == "2026-11-05"  # só o versao.json mudou
    assert tpu.atualizar_de_dump(fx.PASTA / "sgt_dump_mini.sql", tmp_path, data_versao="2026-11-05")["gravados"] == 0


def test_o_mini_dump_bate_com_a_copia_versionada(t: TPU) -> None:
    assuntos, classes = tpu.compactar(tpu.ler_dump(fx.PASTA / "sgt_dump_mini.sql"))
    for codigo, pai, nome, *_ in assuntos:
        assert (t.pai(codigo), t.nome(codigo)) == (pai, nome)
    assert {c[0] for c in classes["itens"]} == {1310, 1331, 1720}


def test_versao_publicada_e_link_do_dump() -> None:
    assert tpu.data_da_resposta_soap(fx.ler("sgt_getDataUltimaVersao.xml")) == "2026-11-05"
    assert tpu.data_da_resposta_soap("<return>31/02/2026</return>") is None
    assert tpu.dump_da_pagina(fx.ler("sgt_versoes_trecho.html")) == (85, "2026-11-05")
    assert tpu.dump_da_pagina("<html></html>") is None


class ColetorFalso:
    """Responde à versão (SOAP), à página de versões e ao dump, e anota o que foi pedido."""

    def __init__(self, data_soap: str) -> None:
        self.data_soap = data_soap
        self.pedidos: list[str] = []

    def consulta(self, url: str, corpo: bytes, **kw: object) -> str:
        self.pedidos.append("soap")
        assert b"getDataUltimaVersao" in corpo and kw.get("formato") == "texto"
        return f"<return>{self.data_soap}</return>"

    def texto(self, url: str, **kw: object) -> str:
        self.pedidos.append("versoes")
        return fx.ler("sgt_versoes_trecho.html")

    def arquivo_grande(self, url: str, **kw: object) -> Path:
        self.pedidos.append(url)
        return fx.PASTA / "sgt_dump_mini.sql"


def test_atualizar_so_baixa_o_dump_quando_a_versao_muda(tmp_path: Path) -> None:
    (tmp_path / "versao.json").write_text(json.dumps({"versao": 85, "data": "2026-11-05"}), encoding="utf-8")
    igual = ColetorFalso("05/11/2026")
    assert tpu.atualizar(igual, None, tmp_path) == {"mudou": False, "versao": {"versao": 85, "data": "2026-11-05"}}
    assert igual.pedidos == ["soap"]
    nova = ColetorFalso("20/12/2026")
    resultado = tpu.atualizar(nova, None, tmp_path)
    assert resultado["mudou"] and resultado["versao"]["data"] == "2026-12-20"
    assert nova.pedidos[:2] == ["soap", "versoes"] and "url=85_dump_dados_oracle_postgres.sql" in nova.pedidos[2]
    assert resultado["versao"]["gerado_de"] == "85_dump_dados_oracle_postgres.sql"
