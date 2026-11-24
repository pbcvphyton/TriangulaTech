"""As réguas de config/tribunais.yaml (áreas, cores, composição) e a classificação de um processo
em macroárea, subárea e recortes (qijournal/tribunais/areas.py)."""

from __future__ import annotations

import copy
import json
from datetime import date

import pytest
import yaml

from qijournal.tribunais import modelo
from qijournal.tribunais.areas import Classificador
from qijournal.tribunais.modelo import ErroDeConfig, Regras, carregar_regras, de_dict
from qijournal.tribunais.tpu import TPU
from tests.fixtures import tribunais as fx

ORDEM = ["constitucional", "tributario", "administrativo", "saude", "trabalho", "previdenciario", "ambiental", "eleitoral",
         "civil", "familia", "empresarial", "processual", "consumidor", "penal", "outros", "sem_assunto"]
SUPERFICIES = {"claro": ("#ffffff", "#f8f9fb", "#fffdf8"), "escuro": ("#111827", "#131c2d", "#262522")}


@pytest.fixture(scope="module")
def regras() -> Regras:
    return carregar_regras()


@pytest.fixture(scope="module")
def tpu() -> TPU:
    return TPU.carregar()


@pytest.fixture(scope="module")
def k(regras: Regras, tpu: TPU) -> Classificador:
    return Classificador(regras, tpu)


# ── o config ─────────────────────────────────────────────────────────────────────────────────


def test_areas_na_ordem_da_paleta_com_nome_curto(regras: Regras) -> None:
    assert list(regras.ids_areas) == ORDEM  # a ordem validada pelo designer (vizinhas na matriz)
    for a in regras.areas:
        assert 0 < len(a.nome_curto) <= 14, a.id
    assert regras.area("sem_assunto").cor is None  # hachura


def _luminancia(hexa: str) -> float:
    def canal(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hexa[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)


def _contraste(a: str, b: str) -> float:
    la, lb = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _hex6(cor: str) -> str:
    return "#ffffff" if cor == "#fff" else cor


def test_cada_area_tem_cor_com_contraste_no_claro_e_no_escuro(regras: Regras) -> None:
    for a in regras.areas:
        if a.cor is None:
            continue
        for modo, cor in zip(("claro", "escuro"), a.cor):
            for fundo in SUPERFICIES[modo]:
                assert _contraste(cor, fundo) >= 3.0, f"{a.id} {modo} {cor} contra {fundo}"
            assert _contraste(_hex6(a.texto), cor) >= 4.5, f"texto de {a.id} sobre {cor}"


def test_todo_codigo_do_config_existe_na_tpu_e_subarea_fica_na_sua_area(regras: Regras, tpu: TPU, k: Classificador) -> None:
    for a in regras.areas:
        for c in (*a.raizes, *a.nos):
            assert c in tpu, f"{a.id}: {c}"
            assert k.area_do_codigo(c) == a.id, f"{c} cai em {k.area_do_codigo(c)}, não em {a.id}"
    for s in regras.subareas:
        for c in s.codigos:
            assert k.area_do_codigo(c) == s.area, f"{s.id}: {c} está em {k.area_do_codigo(c)}"
    for r in regras.recortes:
        assert all(c in tpu for c in r.codigos), r.id
    assert set(regras.recortes_economicos) <= {r.id for r in regras.recortes}


def test_nome_da_subarea_e_dos_outros(regras: Regras) -> None:
    assert regras.nome_subarea("penal.drogas") == "Drogas"
    assert regras.nome_subarea("civil.outros") == "Outros de Civil"


def test_reguas_para_meta_levam_as_cores_e_os_limiares(regras: Regras) -> None:
    meta = regras.reguas_para_meta()
    assert [a["id"] for a in meta["areas"]] == ORDEM
    assert meta["areas"][5] == {"id": "previdenciario", "nome": "Previdência e assistência", "nome_curto": "Previdência",
                                "cor": ["#874e87", "#a84d95"], "texto": "#fff",
                                "regra": {"classes": [], "nos": [], "raizes": [195, 12734], "residual": False}}
    assert meta["limiares"]["comparacao"]["razao"] == [0.8, 1.25] and meta["limiares"]["mesa_dias"] == [180, 365]
    json.dumps(meta)  # serializável


@pytest.fixture()
def bruto() -> dict:
    return yaml.safe_load((modelo.Path(modelo.__file__).resolve().parents[2] / "config" / "tribunais.yaml").read_text(encoding="utf-8"))


def test_config_com_erro_derruba_com_o_caminho(bruto: dict) -> None:
    longo = copy.deepcopy(bruto)
    longo["areas"][2]["nome_curto"] = "Administrativo e afins"
    with pytest.raises(ErroDeConfig, match=r"areas\[2\]\.nome_curto"):
        de_dict(longo)
    repetido = copy.deepcopy(bruto)
    repetido["areas"][1]["raizes"] = [14, 899]
    with pytest.raises(ErroDeConfig, match="899"):
        de_dict(repetido)
    sem = copy.deepcopy(bruto)
    sem["areas"] = [a for a in sem["areas"] if a["id"] != "processual"]
    with pytest.raises(ErroDeConfig, match="processual"):
        de_dict(sem)
    mal = copy.deepcopy(bruto)
    mal["composicao"]["stf"]["ministros"][0]["id"] = "stj-flavio-dino"
    with pytest.raises(ErroDeConfig, match="stf-"):
        de_dict(mal)


def test_datajud_ligado_e_publicado_por_decisao_do_dono(regras: Regras) -> None:
    """O dono decidiu (23/11/2026) publicar a camada do CNJ: uso pessoal, repositório público."""
    assert regras.datajud["ativo"] is True and regras.datajud["publicar"] is True and regras.datajud["por_minuto"] == 120
    assert regras.pausa_do_host("dadosabertos.web.stj.jus.br") == 10.0
    assert regras.pausa_do_host("portal.stf.jus.br") == 1.0
    assert "https://dadosabertos.web.stj.jus.br/api/" in regras.fontes["proibidos"]


def test_site_yaml_liga_a_aba_e_qij_no_tribunais_desliga() -> None:
    from qijournal.config import load_config

    ligada = load_config(env={})
    assert ligada.tribunais.enabled and ligada.tribunais.marcas == ["tech"] and not ligada.tribunais.publicado
    assert ligada.tribunais_ativo
    assert not load_config(env={"QIJ_NO_TRIBUNAIS": "1"}).tribunais_ativo
    assert not load_config(env={"QIJ_BRAND": "pbcv"}).tribunais_ativo  # só a marca tech


# ── a composição ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("trib", "texto", "ministro", "fora"),
    [
        ("stj", "Nancy Andrighi", "stj-nancy-andrighi", None),
        ("stj", "NANCY ANDRIGHI", "stj-nancy-andrighi", None),  # atas e íntegras: caixa alta
        ("stj", "Luís Carlos Gambogi (Desembargador Convocado do TJMG)", "stj-luis-carlos-gambogi", None),
        ("stj", "FÁTIMA MARANHÃO (DESEMBARGADORA CONVOCADA DO TJPB)", "stj-fatima-maranhao", None),
        ("stj", "Sérgio Kukina", "stj-sergio-kukina", None),
        ("stj", "SEBASTIÃO REIS JÚNIOR", "stj-sebastiao-reis-junior", None),
        ("stj", "Presidente do STJ", None, "presidencia"),
        ("stj", "Vice-Presidente do STJ", None, "vice"),
        ("stj", "Não Informado", None, "sem_relator"),
        ("stj", "Presidente da Terceira Seção", None, "presidentes_orgao"),
        ("stj", "Comissão Gestora de Precedentes | 2ª Seção - integrante 1", None, "cogepac"),
        ("stj", "Og Fernandes", None, "residuos"),
        ("stj", None, None, "sem_relator_arquivo"),
        ("stf", "MIN. ANDRÉ MENDONÇA", "stf-andre-mendonca", None),
        ("stf", "MIN. CÁRMEN LÚCIA", "stf-carmen-lucia", None),
        ("stf", "GILMAR MENDES", "stf-gilmar-mendes", None),  # pesquisa de jurisprudência
        ("stf", "MINISTRO PRESIDENTE", None, "presidencia"),
        ("stf", "VICE-PRESIDENTE", None, "vice"),
        ("stf", "MIN. LUÍS ROBERTO BARROSO", None, "residuos"),
    ],
)
def test_relator_como_cada_fonte_escreve(regras: Regras, trib: str, texto: str | None, ministro: str | None, fora: str | None) -> None:
    achado = regras.composicao[trib].relator(texto)
    assert (achado.ministro.id if achado.ministro else None, achado.fora, achado.conhecido) == (ministro, fora, True)


def test_nome_fora_do_mapa_e_residuo_desconhecido(regras: Regras) -> None:
    achado = regras.composicao["stj"].relator("Fulano de Tal (Desembargador Convocado do TJXX)")
    assert (achado.ministro, achado.fora, achado.conhecido) == (None, "residuos", False)


@pytest.mark.parametrize(
    ("trib", "local", "dono"),
    [
        ("stj", "GABINETE DO MINISTRO RAUL ARAÚJO FILHO", "stj-raul-araujo"),
        ("stj", "GABINETE DO MINISTRO HUMBERTO EUSTÁQUIO SOARES MARTINS", "stj-humberto-martins"),
        ("stj", "GABINETE DO MINISTRO PAULO SERGIO DOMINGUES", "stj-paulo-sergio-domingues"),  # sem acento
        ("stj", "GABINETE DO MINISTRO SÉRGIO LUIZ KUKINA", "stj-sergio-kukina"),
        ("stj", "GABINETE DO DESEMBARGADOR CONVOCADO DO TJMG LUÍS CARLOS GAMBOGI", "stj-luis-carlos-gambogi"),
        ("stj", "GABINETE DA DESEMBARGADORA CONVOCADA DO TJDFT NILSONI DE FREITAS", "stj-nilsoni-de-freitas"),
        ("stj", "COORDENADORIA DE PROCESSAMENTO DE FEITOS DE DIREITO PRIVADO", None),
        ("stj", "MINISTÉRIO PÚBLICO FEDERAL", None),
        ("stf", "GABINETE MINISTRO ANDRÉ MENDONÇA", "stf-andre-mendonca"),
        ("stf", "GABINETE MINISTRA CÁRMEN LÚCIA", "stf-carmen-lucia"),
        ("stf", "GABINETE DA VICE-PRESIDÊNCIA", None),
        ("stf", "GERÊNCIA DE RECURSOS EXTRAORDINÁRIOS", None),
    ],
)
def test_gabinete_e_na_mesa(regras: Regras, trib: str, local: str, dono: str | None) -> None:
    comp = regras.composicao[trib]
    achado = comp.do_gabinete(local)
    assert (achado.id if achado else None) == dono
    if dono:
        assert comp.na_mesa(dono, local) and not comp.na_mesa(f"{trib}-outro", local)


def test_grupos_e_grade(regras: Regras) -> None:
    stj, stf = regras.composicao["stj"], regras.composicao["stf"]
    assert len(stj.da_grade()) == 30 and len(stf.da_grade()) == 9  # 27 ministros + 3 convocadas; o Presidente do STF fora
    assert [g.id for g in stj.grupos] == ["s1", "s2", "s3", "pr"] and [g.id for g in stf.grupos] == ["t1", "t2", "pr"]
    assert stj.grupo_de(stj.ministro("stj-nancy-andrighi")) == "s2"
    assert stj.grupo_de(stj.ministro("stj-luis-felipe-salomao")) == "pr"
    assert stf.grupo_de(stf.ministro("stf-alexandre-de-moraes")) == "t1"
    assert stf.grupo_de(stf.ministro("stf-edson-fachin")) == "pr"
    assert sum(1 for m in stj.ministros if "presidente_turma" in m.cargos) == 6
    fatima = stj.ministro("stj-fatima-maranhao")
    assert fatima.recem_chegado(date(2026, 11, 23), 6) and not stj.ministro("stj-nancy-andrighi").recem_chegado(date(2026, 11, 23), 6)


def test_os_relatores_dos_casos_reais_estao_no_mapa(regras: Regras) -> None:
    casos = json.loads(fx.ler("stj_acervo_casos.json"))["casos"]
    for nome, p in casos.items():
        assert regras.composicao["stj"].relator(p.get("nomeMinistroRelator")).conhecido, nome


# ── a classificação ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("classe", "principal", "assuntos", "area", "por", "subarea", "recortes"),
    [
        ("HC", 3608, [3608], "penal", "classe", "penal.drogas", ["drogas"]),  # a classe vale antes; a subárea sai do assunto
        ("AREsp", 5779, [5779], "familia", "principal", "familia.alimentos", []),  # nó Família vence a raiz Civil
        ("REsp", 9607, [9607], "civil", "principal", "civil.bancario", ["bancario"]),  # Bancário vence Contratos
        ("REsp", 4805, [4805], "civil", "principal", "civil.contratos", []),
        ("AREsp", 7779, [7779], "consumidor", "principal", "consumidor.responsabilidade", ["dano_moral"]),
        ("REsp", 4993, [4993], "empresarial", "principal", "empresarial.recuperacao_falencia", ["recuperacao_falencia"]),
        ("REsp", 6017, [6017], "tributario", "principal", "tributario.execucao_fiscal", ["execucao_fiscal"]),
        ("REsp", 9964, [9964], "familia", "principal", "familia.outros", []),  # Infância, Seção Cível (sob 9633)
        ("REsp", 9634, [9634], "penal", "principal", "penal.outros", []),  # Ato infracional (sob 9633)
        ("REsp", 6216, [6216], "familia", "principal", "familia.outros", []),  # alimentos internacionais
        ("REsp", 6197, [6197], "administrativo", "principal", "administrativo.outros", []),  # Estrangeiro
        ("REsp", 6191, [6191], "civil", "principal", "civil.outros", []),  # o resto de Internacional
        ("REsp", 3618, [3618], "penal", "principal", "penal.outros", []),  # crime ambiental fica em Penal
        ("REsp", 12467, [12467], "outros", "principal", None, []),
        ("ADI", 10288, [10288], "constitucional", "classe", None, []),
        ("REsp", 8961, [8961, 10433], "civil", "lista", "civil.responsabilidade", ["dano_moral"]),  # principal só processual
        ("REsp", 8961, [8961], "processual", "so_processual", None, []),
        ("REsp", None, [], "sem_assunto", "sem_assunto", None, []),
        ("REsp", 11828, [11828], "ambiental", "principal", None, []),  # área sem subáreas no config
    ],
)
def test_classificar(k: Classificador, classe: str, principal: int | None, assuntos: list[int], area: str, por: str,
                     subarea: str | None, recortes: list[str]) -> None:
    c = k.classificar(classe=classe, principal=principal, assuntos=assuntos)
    assert (c["area"], c["por"], c["subarea"], c["recortes"]) == (area, por, subarea, recortes)


def test_tambem_toca_e_codigo_que_decidiu(k: Classificador) -> None:
    c = k.classificar(classe="SE", principal=7664, assuntos=[7664, 6191, 10433, 8961])
    assert c["area"] == "familia" and c["codigo"] == 7664 and c["tambem_toca"] == ["civil"]
    assert k.classificar(classe="REsp", principal=8961, assuntos=[8961, 5946])["codigo"] == 5946


# Os 18 casos difíceis do acervo real do STJ (20/11/2026), conferidos à mão contra a TPU.
CASOS = {
    "codigo_com_prefixo_9_na_lista": ("administrativo", "principal", "administrativo.servidores", [10288]),
    "precatorio_stj_requisitante": ("administrativo", "principal", "administrativo.servidores", [10288]),
    "sobrestado_repercussao_geral": ("consumidor", "principal", "consumidor.contratos", [6233]),
    "qualificador_representado_por": ("familia", "principal", "familia.inventario", [7687]),
    "familia_em_segredo": ("familia", "principal", "familia.casamento", [7664, 6191]),
    "banco_do_brasil_raiz_00000000": ("civil", "principal", "civil.contratos", [4805]),
    "carta_rogatoria_autoridade_central": ("penal", "principal", "penal.outros", [287]),
    "ramo_fora_do_padrao": ("sem_assunto", "sem_assunto", None, []),  # só "210303 - COFINS", código fora da TPU
    "hc_tribunal_como_impetrado": ("penal", "classe", "penal.outros", [3603, 4272]),
    "no_mpf_vista": ("administrativo", "principal", "administrativo.outros", [10069, 8961]),
    "presidencia_na_arp": ("ambiental", "principal", None, [11828, 12755]),
    "pj_com_cpf_no_nome_mei": ("civil", "principal", "civil.responsabilidade", [10433]),
    "ex_ministro_em_redistribuicao_og": ("penal", "principal", "penal.drogas", [3608, 12333, 10926, 10621, 5897, 3521, 3633]),
    "heranca_convocada_fatima_maranhao": ("penal", "classe", "penal.dignidade_sexual", [12397, 10949]),
    "relator_nao_informado": ("administrativo", "principal", "administrativo.militares", [10330]),
    "principal_processual_com_material_na_lista": ("processual", "so_processual", None, [14912, 8961]),
}


def test_casos_reais_do_stj(k: Classificador) -> None:
    casos = json.loads(fx.ler("stj_acervo_casos.json"))["casos"]
    for nome, (area, por, subarea, codigos) in CASOS.items():
        p = casos[nome]
        c, assunto, todos = k.classificar_stj(classe=p["siglaClasse"], codigo_principal=p["codigoAssuntoCNJ"], lista_assuntos=p["listaAssuntos"])
        assert (c["area"], c["por"], c["subarea"], todos) == (area, por, subarea, codigos), nome
        if codigos:
            assert assunto["codigo"] == codigos[0] and assunto["caminho"].startswith(k.tpu.nome(k.tpu.raiz(codigos[0])))


def test_stf_pelo_caminho_em_texto(k: Classificador) -> None:
    c, assunto, codigos = k.classificar_stf(
        classe="ARE", ramo="DIREITO TRIBUTÁRIO",
        assuntos_texto="1 - DIREITO TRIBUTÁRIO || IMPOSTOS || ICMS/ IMPOSTO SOBRE CIRCULAÇÃO DE MERCADORIAS\r2 - DIREITO CIVIL",
    )
    assert (c["area"], c["por"], c["subarea"], codigos) == ("tributario", "principal", "tributario.icms", [5946, 899])
    assert c["tambem_toca"] == ["civil"]
    assert (assunto["codigo"], assunto["profundidade"], assunto["texto"]) == (
        5946, 3, "DIREITO TRIBUTÁRIO || IMPOSTOS || ICMS/ IMPOSTO SOBRE CIRCULAÇÃO DE MERCADORIAS")
    adi, _, _ = k.classificar_stf(classe="ADI", assuntos_texto="1 - DIREITO ADMINISTRATIVO E OUTRAS MATÉRIAS DE DIREITO PÚBLICO")
    assert adi["area"] == "constitucional"


def test_stf_sem_casamento_cai_no_ramo(k: Classificador) -> None:
    c, assunto, codigos = k.classificar_stf(classe="RE", assuntos_texto="1 - ASSUNTOS DIVERSOS", ramo="DIREITO TRIBUTÁRIO")
    assert (c["area"], c["por"], c["codigo"], codigos) == ("tributario", "ramo", 14, [])
    assert (assunto["codigo"], assunto["profundidade"]) == (None, 0)
    nada, _, _ = k.classificar_stf(classe="RE", assuntos_texto=None, ramo="ASSUNTOS DIVERSOS")
    assert nada["area"] == "sem_assunto"


def test_amostra_real_do_stf_sai_inteira_classificada(k: Classificador, regras: Regras) -> None:
    processos = json.loads(fx.ler("stf_assuntos_amostra.json"))["processos"]
    areas = {k.classificar_stf(classe=p["classe"], assuntos_texto=p["assuntos"], ramo=p["ramo"])[0]["area"] for p in processos}
    assert areas <= set(regras.ids_areas) and {"constitucional", "penal", "tributario", "trabalho", "sem_assunto"} <= areas


def test_hc_de_alimentos_e_familia_nao_penal(k: Classificador) -> None:
    """J6: o HC da Terceira Turma contra a prisão civil do devedor de alimentos (assunto 5779,
    Família > Alimentos) é Família; o de acolhimento (12002) e o de internação compulsória (12508)
    seguem o mérito; o HC penal continua Penal (a classe vale antes do assunto)."""
    assert k.classificar(classe="HC", principal=5779, assuntos=[5779])["area"] == "familia"
    assert k.classificar(classe="RHC", principal=5779, assuntos=[5779])["area"] == "familia"
    assert k.classificar(classe="HC", principal=12508, assuntos=[12508])["area"] == "saude"
    assert k.classificar(classe="HC", principal=3607, assuntos=[3607])["area"] == "penal"
    assert k.classificar(classe="HC", principal=None, assuntos=[])["area"] == "penal"
    # rito no principal, mérito de família na lista: o mérito decide
    assert k.classificar(classe="HC", principal=None, assuntos=[5779])["area"] == "familia"
