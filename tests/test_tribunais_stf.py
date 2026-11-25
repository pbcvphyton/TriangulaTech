"""As fontes do STF (qijournal/tribunais/stf.py): a planilha do acervo lida só com a stdlib, as atas
de distribuição, a agregação da jurisprudência, a repercussão geral e a pauta. Sem rede: as
fixtures são recortes reais (tests/fixtures/tribunais/stf/, origem no __init__.py), com pessoa
física e advogados mascarados; a coleta usa o coletor de verdade com ``fetch`` falso."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from qijournal import net
from qijournal.net import FetchError
from qijournal.tribunais import stf
from qijournal.tribunais.areas import Classificador
from qijournal.tribunais.fontes import ColetorTribunais, RecusaDoHost, RespostaParcial
from qijournal.tribunais.modelo import Regras, carregar_regras
from qijournal.tribunais.tpu import TPU
from tests.fixtures import tribunais as fx
from tests.fixtures.tribunais import stf as fx_stf

PLANILHA = fx_stf.PASTA / "acervo_amostra.xlsx"


@pytest.fixture(scope="module")
def regras() -> Regras:
    return carregar_regras()


@pytest.fixture(scope="module")
def tpu() -> TPU:
    return TPU.carregar()


@pytest.fixture(scope="module")
def acervo(regras: Regras, tpu: TPU) -> stf.Acervo:
    return stf.ler_acervo(PLANILHA, regras, tpu=tpu)


@pytest.fixture(scope="module")
def por_id(acervo: stf.Acervo) -> dict[str, Any]:
    return {p["id"]: p for p in acervo.processos}


# ── xlsx com a stdlib ────────────────────────────────────────────────────────────────────────


def test_coluna_da_referencia() -> None:
    assert [stf.coluna_da_referencia(r) for r in ("A1", "C5", "Z9", "AA1", "AG12", "ag3")] == [0, 2, 25, 26, 32, 32]
    with pytest.raises(stf.ErroDeFormato):
        stf.coluna_da_referencia("12")


def test_serial_do_excel() -> None:
    assert stf.serial_excel("46342") == "2026-11-16"
    assert stf.serial_excel(46315.6477893519) == "2026-10-20"  # a fração (a hora) é ignorada
    assert stf.serial_excel("46315.6477893519") == "2026-10-20"
    assert stf.serial_excel(1) == "1899-12-31"
    assert stf.serial_excel(0, base_1904=True) is None
    assert stf.serial_excel(1, base_1904=True) == "1904-01-02"
    assert stf.serial_excel(None) is None and stf.serial_excel("") is None
    assert stf.data_br("Data da última atualização: 22/11/26") == "2026-11-22"
    assert stf.data_br("25/11/2026 14:00") == "2026-11-25"
    assert stf.data_br("31/02/2026") is None


def _xlsx(folha: str, textos: str | None = None, livro_1904: bool = False) -> bytes:
    """Um xlsx mínimo, à mão, para os casos que a planilha do STF não tem (texto rico, inlineStr...)."""
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        pr = '<workbookPr date1904="1"/>' if livro_1904 else ""
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rel}>{pr}<sheets><sheet name="F" sheetId="1" r:id="rId9"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/folha_a.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="textos.xml"/>'
                   "</Relationships>")
        z.writestr("xl/worksheets/folha_a.xml", f"<worksheet {ns}><sheetData>{folha}</sheetData></worksheet>")
        if textos is not None:
            z.writestr("xl/textos.xml", f"<sst {ns}>{textos}</sst>")
    return buf.getvalue()


def test_linhas_xlsx_resolve_textos_e_pula_vazias() -> None:
    textos = ("<si><t>simples</t></si>"
              "<si><r><t>texto </t></r><r><rPr><b/></rPr><t>rico</t></r><rPh><t>LEITURA</t></rPh></si>"
              "<si><t>linha 1_x000D_linha 2</t></si>")
    folha = ('<row r="2"><c r="A2" t="s"><v>0</v></c><c r="D2" t="s"><v>1</v></c><c r="E2"/></row>'
             '<row r="5"><c r="B5" t="inlineStr"><is><t>em linha</t></is></c><c r="C5"><v>46342</v></c>'
             '<c r="F5" t="b"><v>1</v></c><c r="G5" t="e"><v>#N/A</v></c><c r="H5" t="s"><v>2</v></c></row>'
             '<row><c t="s"><v>0</v></c><c><v>7</v></c></row>')
    linhas = list(stf.linhas_xlsx(_xlsx(folha, textos)))
    assert linhas == [
        (2, {0: "simples", 3: "texto rico"}),  # a fonética (rPh) fica de fora; a célula vazia E2 também
        (5, {1: "em linha", 2: "46342", 5: "1", 7: "linha 1\rlinha 2"}),  # a de erro (G5) não vem
        (6, {0: "simples", 1: "7"}),  # sem referência: a linha seguinte e a coluna seguinte
    ]
    assert stf.base_1904(_xlsx("", livro_1904=True)) is True
    assert stf.base_1904(PLANILHA) is False


def test_xlsx_invalido_e_coluna_obrigatoria_ausente() -> None:
    with pytest.raises(stf.ErroDeFormato):
        list(stf.linhas_xlsx(b"isto nao e um zip"))
    cab = '<row r="4"><c r="A4" t="inlineStr"><is><t>Classe</t></is></c><c r="B4" t="inlineStr"><is><t>Número</t></is></c></row>'
    with pytest.raises(stf.ErroDeFormato, match="Relator Atual"):
        stf.ler_planilha(_xlsx(cab))
    with pytest.raises(stf.ErroDeFormato, match="cabeçalho"):
        stf.ler_planilha(_xlsx('<row r="1"><c r="A1" t="inlineStr"><is><t>outra coisa</t></is></c></row>'))


def test_planilha_real_data_cabecalho_e_soma(regras: Regras) -> None:
    planilha = stf.ler_planilha(PLANILHA)
    assert planilha.data_dados == "2026-11-22"  # "Data da última atualização: 22/11/26", na 2ª linha
    assert len(planilha.registros) == 23
    assert planilha.linhas_ignoradas == 1  # a linha "Soma:" do fim
    assert planilha.colunas_novas == []
    primeiro = planilha.registros[0]
    assert primeiro["classe"] == "AC" and primeiro["numero"] == "1014" and primeiro["link"] == "AC-1014"
    assert primeiro["data_andamento"] == "46342" and primeiro["local"] == "GABINETE MINISTRO ANDRÉ MENDONÇA"
    assert "tutela_antecipada" not in primeiro  # coluna vazia em toda a planilha: a célula não existe
    assert planilha.registros[-1]["assuntos"].count("\r") == 2  # &#x0D; entre as linhas de assunto


def test_numero_em_notacao_cientifica() -> None:
    # metade dos números da planilha vem como real: "1.34462e6" é o ARE 1344620 (Decimal, sem float)
    assert stf._so_digitos("1.34462e6") == "1344620"
    assert stf._so_digitos("1.017365e6") == "1017365"
    assert stf._so_digitos("1.384504E6") == "1384504"
    assert stf._so_digitos("2031") == "2031" and stf._so_digitos("2031.0") == "2031"
    assert stf._so_digitos("00052358320051000000") == "00052358320051000000"


# ── o Processo ───────────────────────────────────────────────────────────────────────────────


def test_acervo_ids_numeros_e_contrato(acervo: stf.Acervo, por_id: dict[str, Any]) -> None:
    assert acervo.data_dados == "2026-11-22" and acervo.avisos == []  # nenhum número diverge da coluna Link
    assert len(por_id) == 23
    for pid in ("stf-ARE-1344620", "stf-RE-1017365", "stf-ARE-1606302", "stf-AC-1014", "stf-RvC-6414"):
        assert pid in por_id
    campos = {"trib", "id", "classe", "numero", "cnj", "registro", "relator", "relator_id", "fora", "orgao", "local", "onde",
              "na_mesa", "area", "area_por", "subarea", "recortes", "tambem_toca", "assunto", "assuntos", "segredo", "partes",
              "datas", "andamento", "sem_decisao_dias", "concluso_dias", "local_dias", "flags", "origem", "polos"}
    for p in acervo.processos:
        assert set(p) == campos, p["id"]
        assert p["trib"] == "stf" and p["orgao"] is None and p["registro"] is None and p["sem_decisao_dias"] is None
        assert set(p["datas"]) == {"autuacao", "ano_autuacao", "distribuicao", "ultima_decisao", "andamento", "localizacao"}
    p = por_id["stf-AC-2031"]
    assert p["cnj"] == "00020260420081000000" and p["datas"]["ano_autuacao"] == 2008
    assert p["origem"] == {"uf": None, "orgao": "SUPREMO TRIBUNAL FEDERAL"}
    assert p["assunto"]["codigo"] == 10102 and p["assunto"]["profundidade"] == 3
    assert p["assunto"]["texto"] == "DIREITO ADMINISTRATIVO E OUTRAS MATÉRIAS DE DIREITO PÚBLICO || DOMÍNIO PÚBLICO || TERRAS INDÍGENAS"


def test_relator_mesa_e_onde(por_id: dict[str, Any]) -> None:
    casos = {
        # id: (relator_id, fora, onde, na_mesa)
        "stf-AC-1014": ("stf-andre-mendonca", None, "gabinete", True),
        "stf-AC-2031": ("stf-edson-fachin", None, "gabinete", True),  # o Presidente, relator do processo dele
        "stf-ACO-3673": ("stf-carmen-lucia", None, "outro_gabinete", False),  # no gabinete de Flávio Dino
        "stf-ACO-3735": ("stf-cristiano-zanin", None, "mp", False),  # PROCURADORIA-GERAL DA REPÚBLICA
        "stf-ACO-3751": ("stf-flavio-dino", None, "cartorio", False),  # GERÊNCIA ...
        "stf-ACO-3609": (None, "residuos", "outro_gabinete", False),  # Barroso, no gabinete de Moraes
        "stf-ARE-1606302": (None, "presidencia", "presidencia", False),  # ASSESSORIA DE ANÁLISE DE RECURSOS
        "stf-ARE-1421381": (None, "presidencia", "presidencia", False),  # "MINISTRA PRESIDENTE"
        "stf-ARE-1495001": (None, "vice", "presidencia", False),  # GABINETE DA VICE-PRESIDÊNCIA
        "stf-ARE-1565855": (None, "sem_relator", "cartorio", False),
        "stf-RvC-6414": (None, "sem_relator", "cartorio", False),
    }
    for pid, (relator_id, fora, onde, na_mesa) in casos.items():
        p = por_id[pid]
        assert (p["relator_id"], p["fora"], p["onde"], p["na_mesa"]) == (relator_id, fora, onde, na_mesa), pid
    assert por_id["stf-AC-1014"]["relator"] == "André Mendonça"  # o nome da composição
    assert por_id["stf-ADI-4245"]["relator"] == "MIN. LUÍS ROBERTO BARROSO"  # quem saiu: como veio
    assert por_id["stf-ARE-1606302"]["relator"] == "MINISTRO PRESIDENTE"


def test_onde_stf_tabela(regras: Regras) -> None:
    comp = regras.composicao["stf"]
    assert stf.onde_stf("GABINETE MINISTRO GILMAR MENDES", "stf-gilmar-mendes", True, comp) == "gabinete"
    assert stf.onde_stf("GABINETE MINISTRO GILMAR MENDES", "stf-luiz-fux", False, comp) == "outro_gabinete"
    assert stf.onde_stf("GABINETE MINISTRA ROSA WEBER", None, False, comp) == "outro_gabinete"  # gabinete de quem saiu
    assert stf.onde_stf("GABINETE DA VICE-PRESIDÊNCIA", None, False, comp) == "presidencia"
    assert stf.onde_stf("PRESIDÊNCIA", "stf-andre-mendonca", False, comp) == "presidencia"
    assert stf.onde_stf("PROCURADORIA-GERAL DA REPÚBLICA", None, False, comp) == "mp"
    assert stf.onde_stf("GERÊNCIA DE RECURSOS EXTRAORDINÁRIOS", None, False, comp) == "cartorio"
    assert stf.onde_stf("COORDENADORIA DE RECEBIMENTO E ADMISSIBILIDADE RECURSAL", None, False, comp) == "cartorio"
    assert stf.onde_stf("ADVOGADO-GERAL DA UNIÃO", None, False, comp) == "outro"
    assert stf.onde_stf(None, None, False, comp) == "outro"


def test_conclusao_ao_relator_e_dias(por_id: dict[str, Any]) -> None:
    p = por_id["stf-AC-2031"]  # "Conclusos ao(à) Relator(a)" em 24/09/2025; dados de 22/11/2026
    assert p["andamento"] == "Conclusos ao(à) Relator(a)"
    assert p["datas"]["andamento"] == "2025-09-24" and p["concluso_dias"] == 424
    assert p["datas"]["localizacao"] == "2025-09-24" and p["local_dias"] == 424
    assert por_id["stf-ARE-1344620"]["concluso_dias"] == 179  # "... para o acórdão" também conta
    assert por_id["stf-ACO-3609"]["andamento"] == "Conclusão (art. 38 do RISTF)"
    assert por_id["stf-ACO-3609"]["concluso_dias"] is None  # vai ao substituto: não é a mesa do relator
    assert por_id["stf-ARE-1421381"]["concluso_dias"] is None  # "Conclusos à Presidência"
    assert por_id["stf-AC-1014"]["concluso_dias"] is None and por_id["stf-AC-1014"]["local_dias"] == 33
    assert stf.e_conclusao_ao_relator("CONCLUSOS AO(À) RELATOR(A)")
    assert not stf.e_conclusao_ao_relator("Conclusos ao(à) Revisor(a)")


def test_flags_sim_nao(por_id: dict[str, Any]) -> None:
    f = por_id["stf-ACO-3735"]["flags"]
    assert f["liminar"] and f["tutela"] and f["sem_decisao"] and f["sem_decisao_final"] and not f["recurso_interno"]
    assert por_id["stf-ACO-3751"]["flags"]["sobrestado"] and por_id["stf-ACO-3751"]["flags"]["liminar"]
    assert por_id["stf-ADI-4151"]["flags"]["vista"] and por_id["stf-ADI-4151"]["flags"]["ultimo_recurso"] == "ADI-ED-ED-ED"
    assert por_id["stf-ADI-6198"]["flags"]["pauta"]
    assert por_id["stf-AC-1014"]["flags"]["preferencia_criminal"]
    assert "ultimo_recurso" not in por_id["stf-AC-1014"]["flags"]
    assert stf.flags_do_registro({}) == {}  # coluna que a planilha não preencheu: a flag não existe
    assert stf.flags_do_registro({"tutela_antecipada": "Não", "tutela_provisoria": "Sim"}) == {"tutela": True}


def test_areas_e_casamento(acervo: stf.Acervo, por_id: dict[str, Any]) -> None:
    assert por_id["stf-ADI-4245"]["area"] == "constitucional" and por_id["stf-ADI-4245"]["area_por"] == "classe"
    assert por_id["stf-HC-165704"]["area"] == "penal" and por_id["stf-HC-165704"]["area_por"] == "classe"
    assert por_id["stf-TPA-60"]["area"] == "eleitoral" and len(por_id["stf-TPA-60"]["assuntos"]) == 3
    assert por_id["stf-ACO-342"]["area"] == "sem_assunto"  # "ASSUNTO PARA PROCESSO ANTIGO": não casa nem pelo ramo
    assert por_id["stf-ACO-342"]["assunto"]["profundidade"] == 0
    c = acervo.cobertura
    assert c["processos"] == 23 and c["segredo"] == 1 and c["na_mesa"] == 8 and c["na_grade"] == 12
    assert sum(c["casamento"].values()) == c["linhas_de_assunto"] == 46
    assert c["casamento"] == {"folha": 44, "parcial": 1, "raiz": 0, "nada": 1}  # parcial: "PRISÃO PREVENTIVA || REVOGAÇÃO"
    assert c["fora_da_grade"] == {"presidencia": 3, "vice": 1, "residuos": 2, "sem_relator": 3}
    assert c["relatores_fora_do_mapa"] == []
    assert sum(c["area_por"].values()) == 23


def test_segredo_sem_partes(por_id: dict[str, Any]) -> None:
    p = por_id["stf-AC-1014"]
    assert p["segredo"] is True and p["partes"] == []
    assert p["polos"] == {"ativo": {"texto": None, "representantes": [], "advogados": 0},
                          "passivo": {"texto": None, "representantes": [], "advogados": 0}}


def test_partes_coladas_separadas_so_com_seguranca(por_id: dict[str, Any]) -> None:
    def partes(pid: str) -> list[tuple[str, str, bool]]:
        return [(x["polo"], x["nome"], x["colado"]) for x in por_id[pid]["partes"]]

    # "UNIÃOFUNDAÇÃO NACIONAL DOS POVOS INDÍGENAS, ": os dois nomes aparecem sozinhos em outros processos
    assert partes("stf-AC-2031")[:2] == [("ativo", "UNIÃO", False), ("ativo", "FUNDAÇÃO NACIONAL DOS POVOS INDÍGENAS", False)]
    # FATMA nunca aparece sozinha: fica colado, marcado
    assert partes("stf-AC-2031")[2] == ("passivo", "ESTADO DE SANTA CATARINAFUNDAÇÃO ESTADUAL DO MEIO AMBIENTE - FATMA", True)
    assert partes("stf-ADI-4245") == [("ativo", "DEMOCRATAS - DEM", False), ("passivo", "PRESIDENTE DA REPÚBLICA", False),
                                      ("passivo", "CONGRESSO NACIONAL", False)]
    assert partes("stf-ACO-2988")[1:] == [("passivo", "UNIÃO", False), ("passivo", "INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS", False)]
    # a vírgula solta no fim de uma parte só não é lista; o \xa0 vira espaço
    assert partes("stf-ADI-6198")[0] == ("ativo", "PROCURADORA-GERAL DA REPÚBLICA", False)
    assert partes("stf-ADI-6198")[2] == ("passivo", "ASSEMBLEIA LEGISLATIVA DO ESTADO DO MATO GROSSO", False)
    assert partes("stf-ARE-1566788") == [("ativo", "PESSOA FÍSICA FPESSOA FÍSICA G E OUTROS", True),
                                         ("ativo", "PESSOA FÍSICA H", False), ("passivo", "VIBRA ENERGIA S.A", False)]
    assert partes("stf-RvC-6414") == []
    parte = por_id["stf-ADC-96"]["partes"][0]
    assert parte == {"polo": "ativo", "papel": "POLO ATIVO", "nome": "PRESIDENTE DA REPÚBLICA", "tipo": "desconhecido",
                     "grupo": None, "cnpj_raiz": None, "colado": False}
    assert por_id["stf-AC-2031"]["polos"]["ativo"]["texto"] == "UNIÃOFUNDAÇÃO NACIONAL DOS POVOS INDÍGENAS, "  # o bruto, para partes.py


def test_separar_polo_regras() -> None:
    conhecidos = frozenset({"UNIÃO", "ESTADO DO PARÁ", "ESTADO DO PARANÁ", "MUNICIPIO DE JACAREACANGA"})
    assert stf.separar_polo("UNIÃO") == [("UNIÃO", False)]
    assert stf.separar_polo("AGENCIA NACIONAL DO PETROLEO, GAS NATURAL E BIOCOMBUSTIVEIS") == [
        ("AGENCIA NACIONAL DO PETROLEO, GAS NATURAL E BIOCOMBUSTIVEIS", False)]  # vírgula no nome, sem a do fim
    assert stf.separar_polo("ESTADO DO PARÁESTADO DO PARANÁ, ", conhecidos) == [("ESTADO DO PARÁ", False), ("ESTADO DO PARANÁ", False)]
    assert stf.separar_polo("ESTADO DO PARÁESTADO DO PARANÁ, ") == [("ESTADO DO PARÁESTADO DO PARANÁ", True)]
    assert stf.separar_polo("MUNICIPIO DE JACAREACANGAESTADO DO PARÁ, UNIÃO, ", conhecidos) == [
        ("MUNICIPIO DE JACAREACANGA", False), ("ESTADO DO PARÁ", False), ("UNIÃO", False)]
    assert stf.separar_polo("*SEGREDO DE JUSTIÇA*") == [] and stf.separar_polo(None) == [] and stf.separar_polo("  ") == []
    # dois cortes possíveis: não separa (nenhuma adivinhação)
    assert stf.descolar("ABC", {"A", "BC", "AB", "C"}) is None
    assert stf.descolar("ABC", {"A", "BC"}) == ("A", "BC")
    assert stf.descolar("A BC", {"A", "BC", "A ", " BC"}) is None  # emenda com espaço não é cola
    assert stf.nomes_conhecidos(["UNIÃO", "X, Y", "ABC, D, E, ", "*SEGREDO DE JUSTIÇA*", None, "F,"]) == {"UNIÃO", "D", "E", "F"}


def test_advogados_so_institucionais_com_nome(acervo: stf.Acervo, por_id: dict[str, Any]) -> None:
    assert por_id["stf-ACO-342"]["polos"]["passivo"] == {
        "texto": "ADVOCACIA GERAL DA UNIAO",
        "representantes": ["ADVOGADO GERAL DA UNIAO", "PROCURADOR GERAL DO ESTADO DO PARA", "PROCURADOR GERAL DO ESTADO DO PARANA"],
        "advogados": 2,
    }
    assert por_id["stf-ADC-96"]["polos"]["passivo"]["advogados"] == 7
    assert "ADVOGADO(A)" not in json.dumps(acervo.processos, ensure_ascii=False)  # nenhum advogado pessoa física
    assert stf.representantes("PROCURADOR GERAL FEDERAL, FULANO DE TAL, SEM REPRESENTACAO NOS AUTOS, PROCURADOR GERAL FEDERAL") == (
        ["PROCURADOR GERAL FEDERAL", "SEM REPRESENTACAO NOS AUTOS"], 1)
    for nome in ("ADVOGADO-GERAL DA UNIÃO", "DEFENSOR PÚBLICO-GERAL FEDERAL", "PROCURADORA-GERAL DA REPÚBLICA",
                 "SEM REPRESENTAÇÃO NOS AUTOS", "PROCURADORIA-GERAL DO DISTRITO FEDERAL"):
        assert stf.representacao_institucional(nome), nome
    assert not stf.representacao_institucional("RODOLFO PROCURADOR GERAL")
    assert not stf.representacao_institucional(None)


def test_relator_fora_do_mapa(regras: Regras, tpu: TPU) -> None:
    registro = {"classe": "RE", "numero": "1", "relator": "MIN. FULANO DE TAL", "local": "GABINETE MINISTRO FULANO DE TAL",
                "assuntos": "1 - DIREITO TRIBUTÁRIO"}
    p = stf.processo_do_registro(registro, data_dados="2026-11-22", regras=regras, classificador=Classificador(regras, tpu))
    assert p["relator_id"] is None and p["fora"] == "residuos" and p["relator"] == "MIN. FULANO DE TAL"
    assert p["onde"] == "outro_gabinete" and p["area"] == "tributario"
    assert not regras.composicao["stf"].relator("MIN. FULANO DE TAL").conhecido


def test_leitura_deterministica(regras: Regras, tpu: TPU, acervo: stf.Acervo) -> None:
    de_novo = stf.ler_acervo(PLANILHA.read_bytes(), regras, tpu=tpu)
    assert json.dumps(de_novo.processos, ensure_ascii=False) == json.dumps(acervo.processos, ensure_ascii=False)
    assert de_novo.cobertura == acervo.cobertura


def test_coletar_acervo_pelo_cache(tmp_path: Path, regras: Regras, tpu: TPU) -> None:
    class Falso:
        def __init__(self) -> None:
            self.pedidos: list[tuple[str, float]] = []

        def arquivo(self, url: str, *, validade_h: float) -> Path:
            self.pedidos.append((url, validade_h))
            return PLANILHA

    c = Falso()
    acervo = stf.coletar_acervo(c, regras, tpu=tpu)
    assert c.pedidos == [("https://www.stf.jus.br/arquivo/cms/publicacaoBOInternet/anexo/acervo/Acervo_STF_Lista_Geral.xlsx", 20.0)]
    assert acervo.cobertura["processos"] == 23


# ── atas de distribuição ─────────────────────────────────────────────────────────────────────


def test_indice_do_dia() -> None:
    assert stf.ler_indice_atas(fx_stf.ler("ata_recuperaata_2026-11-19.xml", "latin-1")) == {
        "vazio": False, "atas": [{"num": 252, "sessao": "O", "data": "2026-11-19", "modo": "AU"}], "presidencia": 235}
    assert stf.ler_indice_atas(fx_stf.ler("ata_recuperaata_2026-11-22_domingo.xml", "latin-1")) == {
        "vazio": True, "atas": [], "presidencia": 9}


def test_ata_inteira(regras: Regras) -> None:
    ata = stf.ler_ata(fx_stf.ler("ata_2026-11-20_253.html"), regras)
    assert (ata["data"], ata["num"], ata["sessao"], ata["modo"]) == ("2026-11-20", 253, "O", "AU")
    assert ata["presidente"] == {"nome": "MIN. EDSON FACHIN", "ministro_id": "stf-edson-fachin"}
    assert ata["total"] == {"sorteio": 3, "heranca": 0, "total": 3}
    assert [(p["id"], p["relator_id"], p["uf"]) for p in ata["processos"]] == [
        ("stf-Rcl-102719", "stf-carmen-lucia", "SC"), ("stf-Pet-17156", "stf-alexandre-de-moraes", "RJ"),
        ("stf-Rcl-102721", "stf-cristiano-zanin", "DF")]
    rcl = ata["processos"][0]
    assert [(x["papel"], x["polo"], x["nome"]) for x in rcl["partes"]] == [
        ("RECLTE.(S)", "ativo", "D.J."), ("RECLDO.(A/S)", "passivo", "JUIZ FEDERAL DE GARANTIAS DA 1ª VARA FEDERAL DE ITAJAÍ"),
        ("BENEF.(A/S)", "passivo", "MINISTÉRIO PÚBLICO FEDERAL")]
    assert rcl["representantes"] == [
        {"polo": "ativo", "papel": "ADV.(A/S)", "nome": None},  # advogado pessoa física: sem nome
        {"polo": "passivo", "papel": "ADV.(A/S)", "nome": "SEM REPRESENTAÇÃO NOS AUTOS"},
        {"polo": "passivo", "papel": "PROC.(A/S)(ES)", "nome": "PROCURADOR-GERAL DA REPÚBLICA"}]
    assert ata["processos"][2]["partes"][0]["e_outros"] is True  # tip_impressao="E OUTRO(A/S)"
    totais = {t["ministro_id"]: (t["sorteio"], t["heranca"]) for t in ata["totais"]}
    assert len(totais) == 9 and totais["stf-carmen-lucia"] == (1, 0) and totais["stf-gilmar-mendes"] == (0, 0)


def test_ata_categorias_e_totalizador_oficial(regras: Regras) -> None:
    ata = stf.ler_ata(fx_stf.ler("ata_2026-11-19_252_trecho.html"), regras)
    assert len(ata["processos"]) == 10
    assert ata["total"] == {"sorteio": 228, "heranca": 0, "total": 228}  # o totalizador oficial, não a conta do recorte
    assert sum(t["total"] for t in ata["totais"]) == 228 and all(t["ministro_id"] for t in ata["totais"])
    papeis = {(x["papel"], x["polo"], x["tipo"]) for p in ata["processos"] for x in p["partes"]}
    for esperado in [("PACTE.(S)", "ativo", "desconhecido"), ("IMPTE.(S)", "ativo", "desconhecido"),
                     ("COATOR(A/S)(ES)", None, "autoridade"), ("IMPDO.(A/S)", None, "autoridade"),
                     ("AUT. POL.", None, "autoridade"), ("LIT.PAS.", "passivo", "desconhecido"),
                     ("INTDO.(A/S)", "passivo", "desconhecido"), ("REQDO.(A/S)", "passivo", "desconhecido"),
                     ("RECTE.(S)", "ativo", "desconhecido"), ("RECDO.(A/S)", "passivo", "desconhecido")]:
        assert esperado in papeis, esperado
    reps = [r["nome"] for p in ata["processos"] for r in p["representantes"] if r["nome"]]
    assert "PROCURADOR-GERAL FEDERAL" in reps  # "(00000/DF)" tirado
    assert not any("ADVOGADO(A)" in (r or "") for r in reps)
    assert stf.chave_categoria("PROC.(A/S)(ES)") == "PROC" and stf.chave_categoria("AUT. POL.") == "AUT POL"


def test_ata_sem_xml_e_xml_com_e_comercial(regras: Regras) -> None:
    with pytest.raises(stf.ErroDeFormato):
        stf.ler_ata("<html><body>Erro</body></html>", regras)
    pagina = ('<div hidden><ata_distribuicao num_ata="1" tipo_sessao="O" dat_ata="02/01/2026"><processo num_processo="5" '
              'sig_classe_proces="RE" nom_min_relator="MIN. LUIZ FUX"><partes><parte categoria="RECTE.(S)" '
              'nome="BELMONTE & ADVOGADOS ASSOCIADOS"/></partes></processo></ata_distribuicao></div>')
    ata = stf.ler_ata(pagina, regras)
    assert ata["processos"][0]["partes"][0]["nome"] == "BELMONTE & ADVOGADOS ASSOCIADOS"
    assert ata["total"] == {"sorteio": 0, "heranca": 0, "total": 0}


def test_feitos_registrados_a_presidencia(regras: Regras) -> None:
    feitos = stf.ler_feitos_presidencia(fx_stf.ler("ata_feitos_2026-11-20.html"), regras)
    assert len(feitos) == 8 and {p["fora"] for p in feitos} == {"presidencia"}
    assert sorted({p["classe"] for p in feitos}) == ["ARE", "RE"]


def _resposta(url: str, corpo: bytes | str, status: int = 200, tipo: str = "text/html; charset=utf-8") -> net.Response:
    return net.Response(url=url, status=status, content=corpo.encode("utf-8") if isinstance(corpo, str) else corpo,
                        headers={"content-type": tipo})


def test_coletar_atas_pelo_coletor(tmp_path: Path, regras: Regras) -> None:
    pedidos: list[str] = []
    paginas = {
        "/robots.txt": fx.ler("robots_portal_stf.txt"),
        "recuperaata.asp?dia=19&mes=11&ano=2026": fx_stf.ler("ata_recuperaata_2026-11-19.xml", "latin-1"),
        "recuperaata.asp?dia=20&mes=11&ano=2026": fx_stf.ler("ata_recuperaata_2026-11-20.xml", "latin-1"),
        "recuperaata.asp?dia=22&mes=11&ano=2026": fx_stf.ler("ata_recuperaata_2026-11-22_domingo.xml", "latin-1"),
        "listaratas.asp?data=19/11/2026&num=252&sessao=O": fx_stf.ler("ata_2026-11-19_252_trecho.html"),
        "listaratas.asp?data=20/11/2026&num=253&sessao=O": fx_stf.ler("ata_2026-11-20_253.html"),
    }

    def fetch(url: str, **kw: Any) -> net.Response:
        pedidos.append(url)
        for fim, corpo in paginas.items():
            if url.endswith(fim):
                return _resposta(url, corpo)
        raise FetchError(url, "HTTP 500", status=500)

    c = ColetorTribunais(tmp_path, regras=regras, fetch=fetch, dormir=lambda s: None)
    saida = stf.coletar_atas(c, regras, ate=date(2026, 11, 22), dias=4)
    assert saida["janela"] == ["2026-11-19", "2026-11-22"]
    assert [d["data"] for d in saida["dias"]] == ["2026-11-19", "2026-11-20", "2026-11-22"]  # o dia 21 falhou
    assert len(saida["avisos"]) == 1 and "21/11/2026" in saida["avisos"][0]
    assert saida["dias"][0]["presidencia"] == 235 and saida["dias"][0]["atas"][0]["num"] == 252
    assert saida["dias"][2]["vazio"] is True and saida["dias"][2]["atas"] == []
    assert not any("/processos" in u for u in pedidos)
    assert pedidos[0] == "https://portal.stf.jus.br/robots.txt"
    # de novo: tudo do cache (o dia que falhou é pedido outra vez)
    pedidos.clear()
    stf.coletar_atas(c, regras, ate=date(2026, 11, 22), dias=4)
    assert pedidos == ["https://portal.stf.jus.br/atadistribuicao/recuperaata.asp?dia=21&mes=11&ano=2026"]


def test_coletar_atas_recusa_do_host_sobe(tmp_path: Path, regras: Regras) -> None:
    def fetch(url: str, **kw: Any) -> net.Response:
        if url.endswith("/robots.txt"):
            return _resposta(url, fx.ler("robots_portal_stf.txt"), tipo="text/plain")
        raise FetchError(url, "HTTP 403", status=403)

    c = ColetorTribunais(tmp_path, regras=regras, fetch=fetch, dormir=lambda s: None)
    with pytest.raises(RecusaDoHost):
        stf.coletar_atas(c, regras, ate=date(2026, 11, 22), dias=2)


# ── jurisprudência ───────────────────────────────────────────────────────────────────────────


def test_consulta_e_a_testada_ao_vivo() -> None:
    assert stf.consulta_decisoes(date(2025, 11, 23), date(2026, 11, 22)) == json.loads(fx_stf.ler("juris_consulta_12m.json"))
    corpo = stf.consulta_decisoes("2026-01-01", "2026-01-31")
    assert corpo["size"] == 0 and corpo["query"]["bool"]["filter"][1]["range"]["julgamento_data"]["gte"] == "2026-01-01"
    assert corpo["aggs"]["relator"]["terms"]["field"] == "relator_processo_nome.keyword"


def test_decisoes_por_ministro_e_as_etiquetas_do_presidente(regras: Regras) -> None:
    d = stf.ler_decisoes(json.loads(fx_stf.ler("juris_agregacao_12m.json")), regras, inicio="2025-11-23", fim="2026-11-22")
    assert d["janela"] == ["2025-11-23", "2026-11-22"] and d["data_dados"] == "2026-11-18" and d["fonte"] == "stf_jurisprudencia"
    assert (d["documentos"], d["monocraticas"], d["acordaos"], d["processos"]) == (72155, 59470, 12685, 58223)
    # o Presidente: "PRESIDENTE" (12.255 monocráticas) + "EDSON FACHIN (Presidente)" (2.504) + "MINISTRO PRESIDENTE" (57)
    pres = d["fora"]["presidencia"]
    assert (pres["documentos"], pres["monocraticas"], pres["acordaos"]) == (14816, 12255, 2561) and d["presidencia"] == 14816
    # o cartão de Fachin só tem o que ele decidiu como relator dos processos dele
    assert {k: d["ministros"]["stf-edson-fachin"][k] for k in ("documentos", "monocraticas", "acordaos")} == {
        "documentos": 47, "monocraticas": 18, "acordaos": 29}
    assert d["fora"]["vice"]["documentos"] == 87  # "VICE-PRESIDENTE" + "ALEXANDRE DE MORAES (Vice-Presidente)"
    assert d["ministros"]["stf-alexandre-de-moraes"]["documentos"] == 8634
    assert d["fora"]["residuos"]["documentos"] == 47
    # Sepúlveda Pertence (1 acórdão na janela) entrou em composicao.stf.saiu na integração: resíduo conhecido, fora do alarme
    assert d["relatores_fora_do_mapa"] == []
    soma = sum(v["documentos"] for v in d["ministros"].values()) + sum(v["documentos"] for v in d["fora"].values())
    assert soma + d["sem_relator"] == d["documentos"] and d["sem_relator"] == 1
    assert list(d["ministros"])[:3] == ["stf-flavio-dino", "stf-carmen-lucia", "stf-alexandre-de-moraes"]  # a ordem do config
    assert len(d["por_mes"]) == 13 and sum(m + a for _, m, a in d["por_mes"]) == d["documentos"]
    dino = d["ministros"]["stf-flavio-dino"]
    assert sum(m for _, m, _a in dino["por_mes"]) == dino["monocraticas"] and dino["processos"] == 5467
    assert d["acordaos_recurso_interno"] is None


def test_destino_do_relator(regras: Regras) -> None:
    comp = regras.composicao["stf"]
    assert stf.destino_do_relator("PRESIDENTE", comp) == (None, "presidencia")
    assert stf.destino_do_relator("MINISTRA PRESIDENTE", comp) == (None, "presidencia")
    assert stf.destino_do_relator("EDSON FACHIN (Presidente)", comp) == (None, "presidencia")
    assert stf.destino_do_relator("ALEXANDRE DE MORAES (Vice-Presidente)", comp) == (None, "vice")
    assert stf.destino_do_relator("VICE-PRESIDENTE", comp) == (None, "vice")
    assert stf.destino_do_relator("EDSON FACHIN", comp) == ("stf-edson-fachin", None)
    assert stf.destino_do_relator("MIN. CÁRMEN LÚCIA", comp) == ("stf-carmen-lucia", None)
    assert stf.destino_do_relator("ROSA WEBER", comp) == (None, "residuos")


def test_resposta_parcial_nao_vale(regras: Regras) -> None:
    resposta = json.loads(fx_stf.ler("juris_agregacao_12m.json"))
    resposta["result"]["_shards"]["failed"] = 1
    with pytest.raises(RespostaParcial):
        stf.ler_decisoes(resposta, regras, inicio="2025-11-23", fim="2026-11-22")


def test_coletar_decisoes_um_post(tmp_path: Path, regras: Regras) -> None:
    pedidos: list[tuple[str, dict[str, Any]]] = []
    resposta = fx_stf.ler("juris_agregacao_12m.json")

    def fetch(url: str, **kw: Any) -> net.Response:
        if url.endswith("/robots.txt"):  # o host responde a página da SPA no lugar do robots.txt
            return _resposta(url, "<!doctype html><html><head><title>Jurisprudência</title></head></html>")
        pedidos.append((url, kw))
        return _resposta(url, resposta, tipo="application/json")

    c = ColetorTribunais(tmp_path, regras=regras, fetch=fetch, dormir=lambda s: None)
    d = stf.coletar_decisoes(c, regras, hoje=date(2026, 11, 23))
    assert d["janela"] == ["2025-11-23", "2026-11-22"] and d["documentos"] == 72155
    (url, kw), = pedidos
    assert url == "https://jurisprudencia.stf.jus.br/api/search/search" and kw["method"] == "POST"
    assert json.loads(kw["data"]) == json.loads(fx_stf.ler("juris_consulta_12m.json"))
    stf.coletar_decisoes(c, regras, hoje=date(2026, 11, 23))
    assert len(pedidos) == 1  # a segunda vem do cache (validade do config)

    parcial = json.loads(resposta)
    parcial["result"]["_shards"]["failed"] = 2
    c2 = ColetorTribunais(tmp_path / "outro", regras=regras, dormir=lambda s: None,
                          fetch=lambda url, **kw: _resposta(url, json.dumps(parcial), tipo="application/json"))
    with pytest.raises(RespostaParcial):
        stf.coletar_decisoes(c2, regras, hoje=date(2026, 11, 23))


# ── repercussão geral ────────────────────────────────────────────────────────────────────────


def test_acentos_quebrados() -> None:
    assert stf.corrigir_acentos("HÃ¡") == "Há"
    assert stf.corrigir_acentos("NÃ£o hÃ¡ (questÃ£o infraconstitucional)") == "Não há (questão infraconstitucional)"
    assert stf.corrigir_acentos("Há (com reafirmação)") == "Há (com reafirmação)"
    assert stf.corrigir_acentos(None) is None
    assert [stf.classe_rg(t) for t in ("Há repercussão geral", "HÃ¡ (com reafirmaÃ§Ã£o de jurisprudÃªncia)", "NÃ£o hÃ¡",
                                       "Não há repercussão geral (questão infraconstitucional)", "", None)] == [
        "ha", "ha_reafirmacao", "nao_ha", "nao_ha_infra", None, None]


def test_todos_os_temas(regras: Regras) -> None:
    todos = stf.ler_todos_temas(fx_stf.ler("rg_todostemas_trecho.html"))
    assert len(todos) == 14
    t1 = todos[0]
    assert t1 == {
        "numero": 1, "titulo": "Base de cálculo do PIS e da COFINS sobre a importação.", "ramo": "DIREITO TRIBUTÁRIO",
        "leading_case": {"classe": "RE", "numero": "559937", "incidente": 2549049}, "relator": "MIN. ELLEN GRACIE",
        "plenario_virtual": False, "situacao_rg": "Há repercussão geral", "desde": "2013-03-21", "transito": "2014-10-24",
        "data_tese": "2013-03-21"}
    assert todos[1]["relator"] == "MIN. GILMAR MENDES" and todos[1]["plenario_virtual"] is True
    assert next(t for t in todos if t["numero"] == 1502)["titulo"] is None


def test_exportacao_e_cruzamento(regras: Regras) -> None:
    exportados = stf.ler_exportacao(fx_stf.ler("rg_exportar_trecho.xls.html"))
    assert len(exportados) == 14
    assert exportados[1]["ha_rg"] == "Há" and exportados[1]["rg"] == "ha" and exportados[1]["situacao"] == "Trânsito em Julgado"
    assert exportados[98]["rg"] == "ha_reafirmacao" and exportados[97]["situacao"] == "Cancelado"
    temas = stf.temas_rg(stf.ler_todos_temas(fx_stf.ler("rg_todostemas_trecho.html")), exportados, regras)
    por = {t["numero"]: t for t in temas}
    assert [t["numero"] for t in temas] == sorted(por)
    assert por[79]["pendente"] and por[79]["relator_id"] == "stf-nunes-marques"
    assert por[264]["pendente"]  # "Analisada Preliminar de Repercussão Geral" com RG reconhecida
    assert por[1472]["pendente"] and por[1472]["fora"] == "presidencia"
    assert not por[1281]["pendente"]  # RG negada (questão infraconstitucional): não há mérito a julgar
    assert not por[118]["pendente"] and not por[98]["pendente"] and por[97]["cancelado"]
    assert por[536]["fora"] == "residuos" and por[536]["relator"] == "MIN. LUÍS ROBERTO BARROSO"  # o relator do tema, não o sucessor
    assert por[79]["link"] == ("https://portal.stf.jus.br/jurisprudenciaRepercussao/verAndamentoProcesso.asp?incidente=2563583"
                               "&numeroProcesso=565886&classeProcesso=RE&numeroTema=79")
    assert stf.pendentes_por_relator(temas) == {
        "total": 6,
        "ministros": {"stf-carmen-lucia": 1, "stf-edson-fachin": 1, "stf-nunes-marques": 1},
        "fora": {"residuos": 2, "presidencia": 1},
        "relator_saiu": [["LUÍS ROBERTO BARROSO", 1], ["ROSA WEBER", 1]],
    }


def test_tema_so_numa_lista(regras: Regras) -> None:
    temas = stf.temas_rg([], {7: {"relator": "MIN. GILMAR MENDES", "rg": "ha", "situacao": "Acórdão de Repercussão Geral publicado",
                                  "julgamento": "2020-01-02"}}, regras)
    assert temas[0]["numero"] == 7 and temas[0]["pendente"] and temas[0]["relator_id"] == "stf-gilmar-mendes"
    assert temas[0]["desde"] == "2020-01-02" and temas[0]["link"] is None


def test_coletar_rg(regras: Regras) -> None:
    class Falso:
        def __init__(self) -> None:
            self.pedidos: list[tuple[str, float]] = []

        def texto(self, url: str, *, validade_h: float) -> str:
            self.pedidos.append((url, validade_h))
            return fx_stf.ler("rg_todostemas_trecho.html" if "todostemas" in url else "rg_exportar_trecho.xls.html")

    c = Falso()
    rg = stf.coletar_rg(c, regras)
    assert [u for u, _ in c.pedidos] == ["https://portal.stf.jus.br/jurisprudenciaRepercussao/todostemas.asp",
                                         "https://portal.stf.jus.br/jurisprudenciaRepercussao/exportarDados.asp"]
    assert {v for _, v in c.pedidos} == {160.0}  # semanal
    assert len(rg["temas"]) == 14 and rg["pendentes"]["total"] == 6


def test_exportacao_sem_colunas_obrigatorias() -> None:
    with pytest.raises(stf.ErroDeFormato):
        stf.ler_exportacao("<table><thead><tr><th>Outra</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>")
    with pytest.raises(stf.ErroDeFormato):
        stf.ler_todos_temas("<html>sem tabela</html>")


# ── pauta ────────────────────────────────────────────────────────────────────────────────────


def test_sessao_presencial(regras: Regras) -> None:
    itens = stf.ler_sessao_presencial(json.loads(fx_stf.ler("pauta_sessao-presencial_2026-11-25.json")), regras)
    assert len(itens) == 10 and {(i["data"], i["orgao"]) for i in itens} == {("2026-11-25", "PL")}
    are = next(i for i in itens if i["id"] == "stf-ARE-1502069")
    assert (are["relator_id"], are["vista"], are["vista_id"], are["repercussao_geral"]) == (
        "stf-dias-toffoli", "MIN. GILMAR MENDES", "stf-gilmar-mendes", True)
    adi = next(i for i in itens if i["id"] == "stf-ADI-6002")
    assert adi["destaque_id"] == "stf-flavio-dino" and adi["vista"] is None


def test_sessao_presencial_guarda_o_que_a_agenda_precisa(regras: Regras) -> None:
    """Incidente, resumo oficial, destaque por processo, hora, número da sessão e ordem na pauta:
    o que a leitura descartava (a amostra real traz o resumo cortado em 60 caracteres)."""
    itens = stf.ler_sessao_presencial(json.loads(fx_stf.ler("pauta_sessao-presencial_2026-11-25.json")), regras)
    assert [i["ordem"] for i in itens] == list(range(1, 11))
    assert {(i["sessao"], i["hora"], i["sessao_numero"], i["sessao_ano"]) for i in itens} == {("87282", "14:00", 35, 2026)}
    por_id = {i["id"]: i for i in itens}
    assert por_id["stf-ADI-7807"]["incidente"] == "REFERENDO NA MEDIDA CAUTELAR NA AÇÃO DIRETA DE INCONSTITUCIONALIDADE"
    assert por_id["stf-ADI-6002"]["incidente"] is None
    assert (por_id["stf-ADI-7807"]["destaque"], por_id["stf-ADI-7807"]["destaque_id"]) == ("MIN. EDSON FACHIN", "stf-edson-fachin")
    assert sum(1 for i in itens if i["destaque"]) == 6 and sum(1 for i in itens if i["vista"]) == 3
    # o resumo oficial: nas ações de controle concentrado e nos recursos com repercussão geral
    assert por_id["stf-ADI-6002"]["resumo"] == "1. Trata-se de Ação Direta de Inconstitucionalidade, com ped…"
    assert por_id["stf-RE-1326541"]["resumo"] == "1. Trata-se de Recurso Extraordinário interposto contra acór…"
    assert all(i["resumo"] for i in itens)


def test_resumo_so_em_controle_concentrado_e_repercussao_geral_com_corte(regras: Regras) -> None:
    def resposta(classe: str, rg: str | None, texto: str) -> dict[str, Any]:
        return {"data": "25/11/2026", "colegiados": [{"codigo": "TP", "descricao": "Plenário", "sessoes": [{
            "id": "1", "dataInicio": "25/11/2026 14:00", "numero": "35", "ano": "2026",
            "incidentes": [{"classe": classe, "numero": "123", "relator": "MIN. LUIZ FUX", "repercussaoGeral": rg, "resumoTema": texto}]}]}]}

    texto = "1. Trata-se de processo de exemplo   em que o autor, FULANO DE TAL, pede uma coisa. " * 40
    assert stf.ler_sessao_presencial(resposta("HC", None, texto), regras)[0]["resumo"] is None  # pode trazer nome de pessoa
    assert stf.ler_sessao_presencial(resposta("RE-AgR", "N", texto), regras)[0]["resumo"] is None
    longo = stf.ler_sessao_presencial(resposta("ADPF", None, texto), regras)[0]["resumo"]
    assert longo is not None and len(longo) <= stf.RESUMO_MAX and longo.endswith("…") and "  " not in longo
    assert not longo[:-1].endswith((" ", ",")) and texto.replace("   ", " ").startswith(longo[:-1])  # corta no fim de uma palavra
    assert stf.ler_sessao_presencial(resposta("RE", "S", "Resumo curto."), regras)[0]["resumo"] == "Resumo curto."
    assert stf.cortar_no_fim_de_palavra("  ", 10) is None and stf.cortar_no_fim_de_palavra(None, 10) is None


def test_sessao_presencial_tolera_campo_ausente(regras: Regras) -> None:
    """A fonte muda: campo que falta vira None e nada levanta."""
    minimo = {"colegiados": [{"descricao": "Plenário", "sessoes": [{"incidentes": [{"classe": "ADI", "numero": "7000"}, "lixo"]}]}, "lixo"]}
    (item,) = stf.ler_sessao_presencial(minimo, regras)
    assert (item["id"], item["orgao"], item["ordem"], item["data"]) == ("stf-ADI-7000", "PL", 1, None)
    assert all(item[c] is None for c in ("sessao", "hora", "sessao_numero", "sessao_ano", "incidente", "resumo", "vista", "vista_id",
                                         "destaque", "destaque_id"))
    assert item["repercussao_geral"] is False
    for vazio in ({}, {"colegiados": None}, {"colegiados": [{"sessoes": "x"}]}, {"colegiados": [{"sessoes": [{"incidentes": None}]}]}):
        assert stf.ler_sessao_presencial(vazio, regras) == []
    assert stf.ler_sessoes_do_dia({}) == [] and stf.ler_sessoes_do_dia({"colegiados": [{"codigo": "1T", "sessoes": [{}]}]}) == [
        {"data": None, "orgao": "T1", "sessao": None, "hora": None, "numero": None, "ano": None, "tipo": None, "itens": 0}]
    assert stf.hora_da_sessao("25/11/2026 14:00") == "14:00" and stf.hora_da_sessao("25/11/2026 9h30") == "09:30"
    assert stf.hora_da_sessao("25/11/2026") is None and stf.hora_da_sessao(None) is None  # a hora nunca é inventada


def test_sessoes_do_dia(regras: Regras) -> None:
    dia = json.loads(fx_stf.ler("pauta_sessao-presencial_2026-11-25.json"))
    assert stf.ler_sessoes_do_dia(dia) == [
        {"data": "2026-11-25", "orgao": "PL", "sessao": "87282", "hora": "14:00", "numero": 35, "ano": 2026, "tipo": "O", "itens": 10}]


def test_calendario(regras: Regras) -> None:
    cal = stf.ler_calendario(json.loads(fx_stf.ler("pauta_calendarios_2026-11.json")), 11, 2026)
    assert cal["presenciais"][:2] == ["2026-11-04", "2026-11-05"] and len(cal["presenciais"]) == 8
    assert cal["virtuais"][-1] == {"inicio": "2026-11-27", "fim": "2026-12-04", "PL": 165, "T1": 187, "T2": 194}


def test_coletar_pauta(regras: Regras) -> None:
    class Falso:
        def __init__(self) -> None:
            self.pedidos: list[str] = []

        def json(self, url: str, *, validade_h: float) -> Any:
            self.pedidos.append(url)
            if "calendarios&mes=11" in url:
                return json.loads(fx_stf.ler("pauta_calendarios_2026-11.json"))
            if "sessao-presencial&data=25/11/2026" in url:
                return json.loads(fx_stf.ler("pauta_sessao-presencial_2026-11-25.json"))
            raise FetchError(url, "HTTP 500", status=500)

    c = Falso()
    pauta = stf.coletar_pauta(c, regras, hoje=date(2026, 11, 23), dias=14)
    assert pauta["janela"] == ["2026-11-23", "2026-12-07"]
    assert pauta["sessoes"] == [["2026-11-25", "PL", 10]] and len(pauta["itens"]) == 10
    assert len(pauta["avisos"]) == 2  # o calendário de dezembro e a sessão de 26/11 falharam: avisos, não erro
    assert c.pedidos[0].endswith("calendario-service.asp?dados=calendarios&mes=11&ano=2026")
    # todo dia que o calendário marca é listado; o de 26/11 não foi lido nesta coleta (a consulta falhou)
    assert [(d["data"], d["lida"], len(d["sessoes"])) for d in pauta["dias"]] == [("2026-11-25", True, 1), ("2026-11-26", False, 0)]
    assert pauta["dias"][0]["sessoes"][0]["hora"] == "14:00" and pauta["calendarios"] == {"lidos": 1, "falhas": 1}
    # as contagens das sessões virtuais (a fonte só dá a contagem por colegiado)
    assert {"inicio": "2026-11-20", "fim": "2026-11-30", "PL": 179, "T1": 209, "T2": 274} in pauta["virtuais"]


def test_coletar_pauta_lista_o_dia_de_sessao_sem_processos(regras: Regras) -> None:
    """O calendário marca 26/11 como dia de sessão presencial e a fonte ainda não lista processos:
    o dia entra em ``dias`` (lido, sem sessões), em vez de sumir. A resposta do dia vazio e o
    calendário de dezembro são hipóteses do teste (não há resposta real guardada)."""
    class Falso:
        def json(self, url: str, *, validade_h: float) -> Any:
            if "calendarios&mes=11" in url:
                return json.loads(fx_stf.ler("pauta_calendarios_2026-11.json"))
            if "calendarios&mes=12" in url:
                return {"diasPresenciais": [2, "x", 99], "julgamentosVirtuais": None}
            if "sessao-presencial&data=25/11/2026" in url:
                return json.loads(fx_stf.ler("pauta_sessao-presencial_2026-11-25.json"))
            if "sessao-presencial&data=26/11/2026" in url:
                return {"data": "26/11/2026", "tipo": "PRESENCIAL", "colegiados": []}
            if "sessao-presencial&data=02/12/2026" in url:
                return ["resposta fora do formato"]
            raise FetchError(url, "HTTP 500", status=500)

    pauta = stf.coletar_pauta(Falso(), regras, hoje=date(2026, 11, 23), dias=14)
    assert [(d["data"], d["lida"], d["sessoes"] != []) for d in pauta["dias"]] == [
        ("2026-11-25", True, True), ("2026-11-26", True, False), ("2026-12-02", False, False)]
    assert pauta["calendarios"] == {"lidos": 2, "falhas": 0} and len(pauta["avisos"]) == 1 and "02/12/2026" in pauta["avisos"][0]
    assert pauta["sessoes"] == [["2026-11-25", "PL", 10]]  # o bloco do painel não muda: conta só o que tem processo


def test_planilha_sem_data_avisa(regras: Regras, tpu: TPU) -> None:
    def celula(ref: str, texto: str) -> str:
        return f'<c r="{ref}" t="inlineStr"><is><t>{texto}</t></is></c>'

    cab = "".join(celula(f"{c}4", t) for c, t in zip("ABCDEF", ("Classe", "Número", "Relator Atual", "Localização Atual",
                                                                  "Assuntos", "Andamento Atual")))
    linha = (celula("A5", "RE") + '<c r="B5"><v>1.2e3</v></c>' + celula("C5", "MIN. LUIZ FUX")
             + celula("D5", "GABINETE MINISTRO LUIZ FUX") + celula("E5", "1 - DIREITO TRIBUTÁRIO")
             + celula("F5", "Conclusos ao(à) Relator(a)"))
    acervo = stf.ler_acervo(_xlsx(f'<row r="4">{cab}</row><row r="5">{linha}</row>'), regras, tpu=tpu)
    (p,) = acervo.processos
    assert p["id"] == "stf-RE-1200" and p["na_mesa"] and p["concluso_dias"] is None
    assert acervo.data_dados is None and "data da atualização" in acervo.avisos[0]
