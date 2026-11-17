"""A leitura diária dos projetos de lei novos (qijournal.congresso.ler): o que lê e em que
ordem, os lotes cegos com perguntas de controle, a segunda e a terceira leitura, os limites,
o registro do dia e o comando ``congresso ler``. Tudo com editores de mentira, sem rede."""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from qijournal import edit
from qijournal.__main__ import main
from qijournal.config import ROOT, load_config
from qijournal.congresso import cli, dados, fontes, leitura, ler, regras
from qijournal.edit.chatapi import ChatBackend
from qijournal.edit.llm import LLMUnavailable, _Usage
from tests.fixtures.congresso import fabrica
from tests.fixtures.editor import chat_fakes

HOJE = "2026-11-15"
AGORA = "2026-11-15T23:20:00+00:00"
# Códigos (a, e, m, ct, is, co) e a faixa em que caem pela fórmula.
MINIMO = "outro grupo conduta neutro neutro neutro"  # 100 × 0,3 × 0,4 × 0,4 = 5
PONTUAL = "justica grupo pena prisional neutro neutro"  # 100 × 0,6 × 0,4 × 0,4 = 10
MEDIO = "fiscal grupo ajuste neutro neutro neutro"  # 100 × 1 × 0,4 × 0,6 = 24
TOPO = "fiscal geral ajuste corta neutro neutro"  # 100 × 1 × 1 × 0,6 = 60, no núcleo
TOPO_TAMBEM = "infra setor sistema neutro neutro neutro"  # 100 × 1 × 0,6 × 1 = 60: outros códigos, a mesma faixa
BLOCO = re.compile(r"^\[(p\d{4})\] (\S+)\nE: (.*)$", re.M)


@pytest.fixture(scope="module")
def plano() -> ler.Plano:
    """O plano de verdade: a régua, os limites, as instruções e os controles do repositório."""
    return ler.preparar(ROOT)


@pytest.fixture(scope="module")
def gabarito() -> dict[str, dict[str, Any]]:
    """As perguntas de controle: a ementa como vai no lote → a leitura de referência."""
    linhas = (ROOT / "config" / leitura.REFERENCIA).read_text(encoding="utf-8").splitlines()
    return {" ".join(d["ementa"].split())[: leitura.EMENTA_MAX]: d["leitura"] for d in map(json.loads, linhas)}


def _codigos(codigos: str | dict[str, Any], **extra: Any) -> dict[str, Any]:
    """``"fiscal geral ajuste corta neutro neutro"`` → a resposta do leitor (sem o id)."""
    if isinstance(codigos, dict):
        return {"cf": 3, "du": "", "pq": "Controle.", **codigos, **extra}
    return {**dict(zip(("a", "e", "m", "ct", "is", "co"), codigos.split())), "in": 0, "cf": 3, "du": "", "pq": "Lido no teste.", **extra}


def _lida(codigos: str, **campos: Any) -> leitura.Leitura:
    return leitura.Leitura(codigos=tuple(codigos.split()), **campos)


REGUA = regras.carregar(ROOT).projeto_de_pais
SIMBOLICO = "outro local simbolico neutro neutro neutro"  # peso 0


def _noutra_faixa(certa: dict[str, Any]) -> str:
    """Códigos que caem numa faixa diferente da da leitura de referência de um controle."""
    lida, _motivo = leitura.validar(REGUA, _codigos(certa))
    return TOPO if ler.faixa_da_formula(REGUA, lida) != ler.faixa_da_formula(REGUA, _lida(TOPO)) else SIMBOLICO


class LeitorFalso:
    """Um editor de mentira, com a interface dos editores do jornal, que lê lotes cegos.

    ``respostas``: ementa → códigos (ou uma lista deles: a k-ésima vez que a ementa aparece
    recebe o k-ésimo); o resto recebe ``padrao``. Os controles recebem a leitura de referência,
    a não ser com ``erra_controles`` (aí caem sempre em outra faixa). ``omitir``: ementas sem
    resposta; ``invalidas``: ementas com código que não existe; ``falha``: a exceção de cada
    chamada; ``ao_chamar``: chamado a cada chamada (para andar o relógio do teste)."""

    parallel = 1
    max_tokens_write = 32000
    max_tokens_select = 32000
    context_tokens = 128000

    def __init__(self, gabarito: dict[str, dict[str, Any]], nome: str = "falso", *, respostas=None, padrao: str = MINIMO, erra_controles: bool = False, omitir=(), invalidas=(), falha=None, ao_chamar=None):  # fmt: skip
        self.provider, self.model = nome, f"modelo-{nome}"
        self.gabarito, self.respostas, self.padrao = gabarito, dict(respostas or {}), padrao
        self.erra_controles, self.omitir, self.invalidas, self.falha, self.ao_chamar = erra_controles, set(omitir), set(invalidas), falha, ao_chamar
        self.usage = _Usage()
        self.exhausted = False
        self.calls: list[dict[str, Any]] = []
        self.vistas: dict[str, int] = {}

    def lote(self, n: int) -> list[tuple[str, str, str]]:
        """Os blocos da chamada ``n``: (id opaco, tipo, ementa)."""
        return BLOCO.findall(self.calls[n]["user_text"])

    def ementas(self, n: int) -> list[str]:
        """As ementas das proposições de verdade da chamada ``n`` (sem os controles)."""
        return [e for _pid, _tipo, e in self.lote(n) if e not in self.gabarito]

    def call(self, *, label, system, user_text, schema, max_tokens, deadline=None):
        self.calls.append({"label": label, "system": system, "user_text": user_text, "schema": schema, "deadline": deadline})
        if self.ao_chamar is not None:
            self.ao_chamar()
        if self.falha is not None:
            raise self.falha
        saida = []
        for pid, _tipo, ementa in BLOCO.findall(user_text):
            if ementa in self.gabarito:
                certa = self.gabarito[ementa]
                saida.append({"i": pid, **_codigos(_noutra_faixa(certa) if self.erra_controles else certa)})
                continue
            if ementa in self.omitir:
                continue
            vez = self.vistas[ementa] = self.vistas.get(ementa, 0) + 1
            resposta = self.respostas.get(ementa, self.padrao)
            if isinstance(resposta, list):
                resposta = resposta[min(vez, len(resposta)) - 1]
            item = {"i": pid, **_codigos(resposta)}
            if ementa in self.invalidas:
                item["a"] = "saude"  # código que o YAML não tem
            saida.append(item)
        self.usage.record(self.model, 1000, 200)
        return {"leituras": saida}


def _item(n: int, *, casa: str = "cd", ano: int = 2026, universos=(leitura.PERFIL,), tipo: str = "PL") -> leitura.Item:
    chave = str(n) if casa == "cd" else f"{tipo}-{n}-{ano}"
    return leitura.Item(casa, chave, f"{tipo} {n}/{ano}", tipo, ano, f"Ementa {casa} {n} de {ano}.", universos=set(universos))


def _universo(*itens: leitura.Item) -> dict[str, dict[str, leitura.Item]]:
    saida: dict[str, dict[str, leitura.Item]] = {}
    for item in itens:
        saida.setdefault(item.casa, {})[item.chave] = item
    return saida


def _com(plano: ler.Plano, **limites: Any) -> ler.Plano:
    return replace(plano, limites=replace(plano.limites, **limites))


def _guarda(pasta: Path, plano: ler.Plano) -> leitura.Guarda:
    return leitura.Guarda.ler(pasta / "leitura", plano.regua)


def _semear(pasta: Path, plano: ler.Plano, leituras: dict[tuple[str, int, str], leitura.Leitura]) -> None:
    guarda = _guarda(pasta, plano)
    for (casa, ano, chave), lida in leituras.items():
        guarda.registrar(casa, ano, chave, lida)
    guarda.gravar(pasta / "leitura")


def _dias(pasta: Path) -> list[dict[str, Any]]:
    return leitura.ler_diario(pasta / "leitura")


# ── o config ─────────────────────────────────────────────────────────────────


def test_os_limites_do_config_sao_os_padroes_prudentes(plano):
    """O bloco ``projeto_de_pais.leitura.diaria`` de config/congresso.yaml: 150 primeiras, 60
    segundas, lotes de 30 com 10 controles (mínimo de 7 certos), 8 minutos e 12 chamadas."""
    bruto = yaml.safe_load((ROOT / "config" / "congresso.yaml").read_text(encoding="utf-8"))["projeto_de_pais"]["leitura"]["diaria"]
    assert ler.limites_de(bruto) == plano.limites == ler.Limites()
    assert (plano.limites.primeiras, plano.limites.segundas, plano.limites.lote, plano.limites.controles, plano.limites.controles_minimo) == (150, 60, 30, 10, 7)
    assert (plano.limites.prazo_segundos, plano.limites.chamadas, plano.limites.dias, plano.limites.editores, plano.limites.ligada) == (480, 12, 60, (), True)
    assert ler.limites_de(None) == ler.Limites()  # sem o bloco, valem os padrões
    assert ler.limites_de({"lote": 10, "editores": ["gemini", " claude "], "ligada": False}) == ler.Limites(lote=10, editores=("gemini", "claude"), ligada=False)
    # Os tetos cabem no teto de chamadas: 150 primeiras em 5 lotes, 60 segundas em 2.
    assert -(-plano.limites.primeiras // plano.limites.lote) + -(-plano.limites.segundas // plano.limites.lote) < plano.limites.chamadas


@pytest.mark.parametrize(
    ("bruto", "erro"),
    [
        ({"lotes": 10}, "desconhecida"),
        ({"lote": 0}, "1 ou mais"),
        ({"primeiras": -1}, "zero ou mais"),
        ({"segundas": "muitas"}, "inteiro"),
        ({"chamadas": True}, "inteiro"),
        ({"ligada": "sim"}, "true ou false"),
        ({"editores": "gemini"}, "lista"),
        ({"controles": 2, "controles_minimo": 3}, "maior que controles"),
        (["lote", 30], "bloco de chaves"),
    ],
)
def test_limite_fora_do_formato_e_configuracao_quebrada(bruto, erro: str):
    with pytest.raises(ler.ErroDeConfiguracao, match=erro):
        ler.limites_de(bruto)


def test_as_perguntas_de_controle_sao_185_e_a_faixa_do_arquivo_e_a_da_formula(plano):
    """config/leitura_referencia.jsonl: 185 proposições reais com a leitura em que dois
    leitores concordaram. A faixa que se confere sai da fórmula de hoje e é a que o arquivo
    anota ("sem": algum "nd" em campo de peso). O controle leva só o que o leitor pode ver."""
    linhas = [json.loads(linha) for linha in (ROOT / "config" / leitura.REFERENCIA).read_text(encoding="utf-8").splitlines()]
    assert len(plano.controles) == len(linhas) == 185
    assert [c.faixa or "sem" for c in plano.controles] == [d["faixa"] for d in linhas]
    assert {c.faixa for c in plano.controles} == {f.id for f in plano.regua.faixas} | {""}  # todas as faixas têm controle
    assert all(c.item.ementa and len(c.item.ementa) <= leitura.EMENTA_MAX and c.item.tipo for c in plano.controles)
    assert len({(c.item.casa, c.item.chave) for c in plano.controles}) == 185


def _raiz_com(tmp_path: Path, **trocas: str | None) -> Path:
    """Uma cópia do config do repositório, com alguns arquivos trocados (``None``: sem ele)."""
    destino = tmp_path / "config"
    destino.mkdir(parents=True)
    for nome in ("congresso.yaml", leitura.INSTRUCOES, leitura.REFERENCIA):
        conteudo = trocas[nome] if nome in trocas else (ROOT / "config" / nome).read_text(encoding="utf-8")
        if conteudo is not None:
            (destino / nome).write_text(conteudo, encoding="utf-8")
    return tmp_path


def test_preparar_recusa_o_config_quebrado(tmp_path: Path):
    yaml_ = (ROOT / "config" / "congresso.yaml").read_text(encoding="utf-8")
    instrucoes = (ROOT / "config" / leitura.INSTRUCOES).read_text(encoding="utf-8")
    referencia = (ROOT / "config" / leitura.REFERENCIA).read_text(encoding="utf-8")
    casos = {
        "sem-yaml": ({"congresso.yaml": None}, "congresso.yaml"),
        "yaml-invalido": ({"congresso.yaml": "projeto_de_pais: [\n"}, "congresso.yaml"),
        "sem-regua": ({"congresso.yaml": "legislaturas: []\n"}, "projeto_de_pais"),
        "limite": ({"congresso.yaml": yaml_.replace("      lote: 30 ", "      lote: 0 ")}, "1 ou mais"),
        "sem-instrucoes": ({leitura.INSTRUCOES: None}, "instruções"),
        "instrucoes-de-outra-regua": ({leitura.INSTRUCOES: instrucoes.replace("  infra = ", "  obras = ")}, 'códigos de "a"'),
        "sem-controles": ({leitura.REFERENCIA: None}, "perguntas de controle"),
        "poucos-controles": ({leitura.REFERENCIA: "\n".join(referencia.splitlines()[:9]) + "\n"}, r"9 pergunta\(s\) de controle para 10 por lote"),  # uma a menos que as de um lote
        "controle-nao-json": ({leitura.REFERENCIA: referencia + "{isto não é json\n"}, ":186: a linha não é JSON"),
        "controle-sem-ementa": ({leitura.REFERENCIA: '{"casa":"cd","id":"1","tipo":"PL","leitura":{}}\n' + referencia}, ":1: faltam"),
        "controle-com-codigo-de-fora": ({leitura.REFERENCIA: referencia.replace('"a": "fiscal"', '"a": "fisco"', 1)}, 'código inválido em "a"'),
        "controle-repetido": ({leitura.REFERENCIA: referencia + referencia.splitlines()[0] + "\n"}, "duas vezes"),
    }
    assert "      lote: 30 " in yaml_ and "  infra = " in instrucoes and '"a": "fiscal"' in referencia
    for nome, (trocas, erro) in casos.items():
        with pytest.raises(ler.ErroDeConfiguracao, match=erro):
            ler.preparar(_raiz_com(tmp_path / nome, **trocas))
    # Sem controles no config, o arquivo de referência não é exigido.
    assert "      controles: 10 " in yaml_ and "      controles_minimo: 7 " in yaml_
    sem = ler.preparar(_raiz_com(tmp_path / "zero", **{"congresso.yaml": yaml_.replace("      controles: 10 ", "      controles: 0 ").replace("      controles_minimo: 7 ", "      controles_minimo: 0 "), leitura.REFERENCIA: None}))
    assert sem.controles == () and sem.limites.controles == 0 and sem.limites.controles_minimo == 0


# ── o que ler e em que ordem ─────────────────────────────────────────────────


def test_a_ordem_das_primeiras_leituras_e_das_segundas(plano, tmp_path: Path):
    """Primeiro as matérias votadas, depois as do perfil de quem propõe, depois as que só
    esperam; em cada grupo, das mais novas para as mais antigas, as duas Casas intercaladas.
    A proposição sem ementa não tem o que ler. Nas segundas: só o que muda a conta (o peso
    do topo e as votadas), as votadas primeiro; a de pouca confiança fica de fora."""
    itens = _universo(
        _item(900, ano=2010, universos={leitura.ESPERA}),
        _item(2001), _item(2002), _item(1500, ano=2025), _item(7, casa="sf"), _item(9, casa="sf"),
        _item(300, ano=2019, universos={leitura.VOTO}), _item(2000, universos={leitura.VOTO, leitura.PERFIL}), _item(5, casa="sf", ano=2019, universos={leitura.VOTO}),
        replace(_item(2003), ementa=""),
    )  # fmt: skip
    vazia = leitura.Guarda(plano.regua)
    faltam, sem_ementa = ler.faltam_ler(itens, vazia)
    assert sem_ementa == 1
    assert [i.titulo for i in faltam] == [
        "PL 2000/2026", "PL 300/2019", "PL 5/2019",  # votadas: 2026 antes de 2019; em 2019, Câmara e Senado
        "PL 2002/2026", "PL 9/2026", "PL 2001/2026", "PL 7/2026", "PL 1500/2025",  # o perfil: 2026 (as Casas intercaladas), depois 2025
        "PL 900/2010",  # o que só espera
    ]  # fmt: skip
    assert [ler.classe_da_primeira(i) for i in faltam] == [0, 0, 0, 1, 1, 1, 1, 1, 2]

    _semear(tmp_path, plano, {
        ("cd", 2026, "2001"): _lida(TOPO),  # peso 60: pede a segunda
        ("cd", 2026, "2002"): _lida(MINIMO, confianca=1),  # só pouca confiança: fora da leitura diária
        ("cd", 2019, "300"): _lida(MINIMO),  # votada: pede a segunda, e vem antes
        ("cd", 2026, "2000"): _lida(TOPO, r=2),  # já confirmada
        ("sf", 2026, "PL-9-2026"): _lida(TOPO_TAMBEM),
        ("cd", 2010, "900"): _lida(MINIMO),
    })  # fmt: skip
    guarda = _guarda(tmp_path, plano)
    assert [i.titulo for i in ler.pedem_segunda(plano.regua, itens, guarda)] == ["PL 300/2019", "PL 2001/2026", "PL 9/2026"]
    assert sorted(i.titulo for i, _m in leitura.para_segunda(plano.regua, itens["cd"], guarda)) == ["PL 2001/2026", "PL 2002/2026", "PL 300/2019"]  # a exportação segue levando a de pouca confiança
    assert [i.titulo for i in ler.faltam_ler(itens, guarda)[0]] == ["PL 5/2019", "PL 7/2026", "PL 1500/2025"]


def test_le_na_ordem_e_respeita_os_tetos(plano, gabarito, tmp_path: Path):
    """Com teto de 5 primeiras e lotes de 2: três chamadas, na ordem de prioridade, e o resto
    fica para amanhã. O teto de segundas vale à parte, e --max limita a soma das duas."""
    itens = _universo(*[_item(n) for n in range(1, 9)], _item(50, ano=2020, universos={leitura.VOTO}), _item(60, ano=2021, universos={leitura.ESPERA}))
    ia = LeitorFalso(gabarito, respostas={"Ementa cd 8 de 2026.": TOPO, "Ementa cd 7 de 2026.": TOPO})
    registro = ler.ler_universo(_com(plano, primeiras=5, segundas=1, lote=2), tmp_path, itens, HOJE, agora=AGORA, editores=[ia])
    assert [sorted(ia.ementas(n)) for n in range(3)] == [
        ["Ementa cd 50 de 2020.", "Ementa cd 8 de 2026."], ["Ementa cd 6 de 2026.", "Ementa cd 7 de 2026."], ["Ementa cd 5 de 2026."],
    ]  # fmt: skip
    assert [i.titulo for i in registro.escolhidas] == ["PL 50/2020", "PL 8/2026", "PL 7/2026", "PL 6/2026", "PL 5/2026"]
    assert (registro.faltavam, registro.primeiras, registro.pendentes_primeiras) == (10, 5, 5)
    guarda = _guarda(tmp_path, plano)
    assert sorted(guarda.da_casa("cd"), key=int) == ["5", "6", "7", "8", "50"] and guarda.ano_de("cd", "50") == 2020
    # Três pedem a segunda (a votada e as duas de peso 60); o teto é 1: vai a votada.
    assert len(ia.calls) == 4 and ia.ementas(3) == ["Ementa cd 50 de 2020."]
    assert (registro.segundas, registro.confirmadas, registro.pendentes_segundas) == (1, 1, 2)
    assert guarda.de("cd", "50").r == leitura.DUAS and guarda.de("cd", "8").r == leitura.UMA

    # --max 3: as três primeiras seguintes e nenhuma segunda; --max 0 só diz o que leria.
    registro = ler.ler_universo(_com(plano, lote=2), tmp_path, itens, HOJE, agora=AGORA, editores=[ia], maximo=3)
    assert (registro.primeiras, registro.segundas, registro.pendentes_primeiras, registro.pendentes_segundas) == (3, 0, 2, 2)
    chamadas = len(ia.calls)
    registro = ler.ler_universo(plano, tmp_path, itens, HOJE, agora=AGORA, editores=[ia], maximo=0)
    assert len(ia.calls) == chamadas and registro.primeiras == 0 and "--max 0" in registro.motivo
    assert (registro.faltavam, registro.esperavam_segunda, registro.escolhidas) == (2, 2, [])


# ── o lote cego e os controles ───────────────────────────────────────────────


def test_o_pedido_a_ia_as_instrucoes_o_lote_cego_e_o_esquema(plano, gabarito, tmp_path: Path):
    """O "system" são as instruções inteiras (mais a nota de entrega); o texto do usuário é o
    lote no formato dos lotes exportados, sem número, autor nem Casa; a saída vem num esquema
    JSON com os códigos do YAML."""
    item = replace(_item(4321), explicacao="Explica a ementa.", palavras="tributo, alíquota", temas=("economia",))
    ia = LeitorFalso(gabarito)
    ler.ler_universo(plano, tmp_path, _universo(item, _item(4322)), HOJE, editores=[ia])
    chamada = ia.calls[0]
    instrucoes = (ROOT / "config" / leitura.INSTRUCOES).read_text(encoding="utf-8")
    assert chamada["system"].startswith(instrucoes.rstrip("\n")) and chamada["system"].endswith(ler.ENTREGA) and '{"leituras": [' in ler.ENTREGA
    assert chamada["label"] == "leitura 1"  # o pedido não diz se é a primeira, a segunda ou a terceira leitura
    texto = chamada["user_text"]
    nomes = {t.id: t.nome for t in plano.regras.temas}
    assert "[p0001] " in texto and "[p0012] " in texto and "[p0013]" not in texto  # 2 proposições e 10 controles
    bloco = next(b for b in texto.split("\n\n") if "Ementa cd 4321" in b)
    pid = bloco[1:6]
    assert bloco == leitura.bloco(pid, item, nomes) == f"[{pid}] PL\nE: Ementa cd 4321 de 2026.\nX: Explica a ementa.\nK: tributo, alíquota\nT: {nomes['economia']}\n".rstrip("\n")
    assert "4321/2026" not in texto and "PL 4322" not in texto and "cd" not in re.sub(r"Ementa cd", "", texto).split()
    for controle in plano.controles:  # nem o número dos controles vai ao leitor
        assert controle.item.titulo not in texto
    esquema = chamada["schema"]
    assert esquema == ler.esquema(plano.regua, [f"p{k:04d}" for k in range(1, 13)])
    linha = esquema["properties"]["leituras"]["items"]
    assert esquema["required"] == ["leituras"] and linha["required"] == ["i", "a", "e", "m", "ct", "is", "co", "in", "cf", "du", "pq"]
    ids = linha["properties"]["i"]["enum"]
    assert linha["additionalProperties"] is False and (ids[0], ids[-1], len(ids)) == ("p0001", "p0012", 12) and ids == sorted(set(ids))
    for campo in plano.regua.campos:
        assert linha["properties"][campo.chave]["enum"] == [*campo.ids, regras.ND]


def test_os_controles_vao_misturados_e_saem_da_data_e_do_numero_do_lote(plano, gabarito, tmp_path: Path):
    """Cada lote leva 10 perguntas de controle, com id opaco como as demais e fora de posição
    fixa. A mesma data e o mesmo número de lote dão as mesmas perguntas, na mesma ordem; outro
    dia, outras. No dia, os lotes não repetem pergunta."""
    itens = _universo(*[_item(n) for n in range(1, 13)])
    feitas = []
    for k, data in enumerate((HOJE, HOJE, "2026-11-16")):
        ia = LeitorFalso(gabarito)
        registro = ler.ler_universo(_com(plano, lote=4), tmp_path / str(k), itens, data, editores=[ia])
        assert (registro.lotes, registro.controles_certos, registro.controles_total, registro.primeiras) == (3, 30, 30, 12)
        feitas.append(ia)
    mesmo_dia, de_novo, outro_dia = feitas
    assert [c["user_text"] for c in mesmo_dia.calls] == [c["user_text"] for c in de_novo.calls]
    assert [c["user_text"] for c in mesmo_dia.calls] != [c["user_text"] for c in outro_dia.calls]
    do_dia: list[str] = []
    posicoes = set()
    for n in range(3):
        lote = mesmo_dia.lote(n)
        assert [pid for pid, _t, _e in lote] == [f"p{k:04d}" for k in range(1, 15)]  # 4 proposições e 10 controles, numerados em sequência
        controles = [(k, e) for k, (_pid, _t, e) in enumerate(lote) if e in gabarito]
        assert len(controles) == 10
        do_dia += [e for _k, e in controles]
        posicoes |= {k for k, _e in controles}
    assert len(set(do_dia)) == 30 and len(posicoes) > 10  # nenhuma repetida no dia; não ficam sempre nos mesmos lugares
    de_outro_dia = [e for n in range(3) for _pid, _t, e in outro_dia.lote(n) if e in gabarito]
    assert set(de_outro_dia) != set(do_dia)
    # A proposição que é, ela mesma, uma pergunta de controle não vai duas vezes no mesmo lote.
    rodada = ler.Rodada(plano=plano, data=HOJE, editores=[mesmo_dia], registro=ler.Registro(data=HOJE), prazo=time.monotonic() + 60)
    primeiros = rodada.controles_do_lote(1, [])
    assert [c.item.ementa for c in primeiros] == do_dia[:10] or sorted(c.item.ementa for c in primeiros) == sorted(do_dia[:10])
    sem_o_primeiro = rodada.controles_do_lote(1, [primeiros[0].item])
    assert primeiros[0] not in sem_o_primeiro and len(sem_o_primeiro) == 10 and sem_o_primeiro[:9] == primeiros[1:]


def test_os_controles_nao_se_repetem_nos_lotes_que_o_teto_de_chamadas_permite(plano):
    """Com os limites de verdade (10 controles por lote, até 12 chamadas, 185 perguntas): nenhum
    lote repete pergunta, e os 12 lotes de uma rodada levam 120 perguntas diferentes. A lista do
    dia só dá a volta no 19º lote (os 18 primeiros gastam 180): ele leva as 5 últimas e as 5
    primeiras, ou seja, repete perguntas do 1º lote, nunca duas vezes no mesmo lote."""
    limites = plano.limites
    rodada = ler.Rodada(plano=plano, data=HOJE, editores=[], registro=ler.Registro(data=HOJE), prazo=time.monotonic() + 60)

    def chaves(controles) -> set[tuple[str, str]]:
        return {(c.item.casa, c.item.chave) for c in controles}

    do_dia = [rodada.controles_do_lote(n, []) for n in range(1, limites.chamadas + 1)]
    assert all(len(lote) == len(chaves(lote)) == limites.controles == 10 for lote in do_dia)
    assert len(chaves(c for lote in do_dia for c in lote)) == limites.chamadas * limites.controles == 120 <= len(plano.controles)
    inteiros = len(plano.controles) // limites.controles  # lotes que cabem na lista antes de ela dar a volta
    assert inteiros == 18 > limites.chamadas
    da_volta = rodada.controles_do_lote(inteiros + 1, [])
    assert len(da_volta) == len(chaves(da_volta)) == 10 and da_volta[5:] == do_dia[0][:5] and not chaves(da_volta[:5]) & chaves(c for lote in do_dia for c in lote)
    assert rodada.controles_do_lote(inteiros + 2, []) == do_dia[0][5:] + do_dia[1][:5]  # depois da volta, as fatias seguem de onde pararam


def test_o_lote_que_erra_nos_controles_e_descartado_inteiro(plano, gabarito, tmp_path: Path):
    """O editor que põe os controles na faixa errada: nada do lote vai à guarda, o lote é
    contado e as proposições ficam para a próxima rodada. O lote seguinte vai ao editor
    seguinte da cadeia, que acerta."""
    itens = _universo(*[_item(n) for n in range(1, 7)])
    ruim, bom = LeitorFalso(gabarito, "ruim", erra_controles=True, padrao=TOPO), LeitorFalso(gabarito, "bom")
    registro = ler.ler_universo(_com(plano, lote=3, segundas=0), tmp_path, itens, HOJE, agora=AGORA, editores=[ruim, bom])
    assert (len(ruim.calls), len(bom.calls)) == (1, 1)
    assert (registro.lotes, registro.descartados, registro.primeiras, registro.invalidas) == (2, 1, 3, 0)
    assert (registro.controles_certos, registro.controles_total) == (10, 20)
    guarda = _guarda(tmp_path, plano)
    assert sorted(guarda.da_casa("cd")) == ["1", "2", "3"]  # só o segundo lote (as mais antigas do dia)
    assert all(guarda.de("cd", k).codigos == tuple(MINIMO.split()) for k in ("1", "2", "3"))  # nenhuma leitura do editor reprovado
    assert registro.pendentes_primeiras == 3 and registro.provedor == "ruim → bom" and registro.modelo == "modelo-ruim + modelo-bom"
    assert any("lote descartado (0 de 10 controles certos; editor: ruim)" in aviso for aviso in registro.avisos) and registro.passos == [(1, ler.PRIMEIRA, "ruim"), (2, ler.PRIMEIRA, "bom")]
    # Com 7 certos de 10 o lote passa; com 6, não (o mínimo do config é 7).
    do_lote = ler.Rodada(plano=plano, data=HOJE, editores=[bom], registro=ler.Registro(data=HOJE), prazo=time.monotonic() + 600)
    blocos, _texto = do_lote.montar(1, [_item(99)])
    controles = [e for _pid, i, c in blocos if c is not None for e in [i.ementa]]
    assert len(controles) == 10 and plano.limites.controles_minimo == 7
    erra = dict(gabarito)
    for quantos, aceito in ((3, True), (4, False)):
        for ementa in controles[:quantos]:
            erra[ementa] = _codigos(_noutra_faixa(gabarito[ementa]))
        rodada = ler.Rodada(plano=plano, data=HOJE, editores=[LeitorFalso(erra, "meio")], registro=ler.Registro(data=HOJE), prazo=time.monotonic() + 600)
        resultado = rodada.lote([_item(99)], ler.PRIMEIRA)
        assert (resultado is not None) is aceito and (rodada.registro.controles_certos, rodada.registro.controles_total, rodada.registro.descartados) == (10 - quantos, 10, int(not aceito))


def test_guarda_so_o_que_veio_valido(plano, gabarito, tmp_path: Path):
    """Lote aceito com uma proposição sem resposta e outra com código de fora do YAML: as
    válidas são gravadas, as outras duas são contadas e voltam na rodada seguinte."""
    itens = _universo(*[_item(n) for n in range(1, 6)])
    ia = LeitorFalso(gabarito, omitir={"Ementa cd 2 de 2026."}, invalidas={"Ementa cd 4 de 2026."}, respostas={"Ementa cd 5 de 2026.": PONTUAL})
    registro = ler.ler_universo(plano, tmp_path, itens, HOJE, agora=AGORA, editores=[ia])
    guarda = _guarda(tmp_path, plano)
    assert sorted(guarda.da_casa("cd")) == ["1", "3", "5"]
    assert (registro.primeiras, registro.invalidas, registro.descartados, registro.pendentes_primeiras) == (3, 2, 0, 2)
    assert guarda.de("cd", "5") == leitura.Leitura(tuple(PONTUAL.split()), 0, 3, "", "Lido no teste.", leitura.UMA)
    # A resposta que traz "r" não confirma nada sozinha, e a que vem repetida vale uma vez.
    rodada = ler.Rodada(plano=plano, data=HOJE, editores=[ia], registro=ler.Registro(data=HOJE), prazo=time.monotonic() + 600)
    assert ler.respostas_do_lote({"leituras": [{"i": "p0001", "a": "x"}, {"i": "p0001", "a": "y"}, {"i": "p0009"}, "lixo", {"a": "sem id"}]}, {"p0001", "p0002"}) == {"p0001": {"i": "p0001", "a": "x"}}
    assert ler.respostas_do_lote([{"i": "p0002"}], {"p0002"}) == {"p0002": {"i": "p0002"}} and ler.respostas_do_lote("texto solto", {"p0001"}) == {}

    class ComR(LeitorFalso):
        def call(self, **kwargs):
            dados_ = super().call(**kwargs)
            return {"leituras": [{**item, "r": 2} for item in dados_["leituras"]]}

    rodada.editores = [ComR(gabarito)]
    validas, _quem = rodada.lote([_item(77)], ler.PRIMEIRA)
    assert validas[("cd", "77")].r == leitura.UMA
    # Na rodada seguinte, as duas que ficaram são lidas; o dia soma as duas rodadas.
    registro = ler.ler_universo(plano, tmp_path, itens, HOJE, agora="2026-11-15T23:50:00+00:00", editores=[LeitorFalso(gabarito)])
    assert (registro.faltavam, registro.primeiras, registro.pendentes_primeiras) == (2, 2, 0)
    assert len(_guarda(tmp_path, plano)) == 5
    (dia,) = _dias(tmp_path)
    assert (dia["rodadas"], dia["primeiras"], dia["invalidas"], dia["lotes"], dia["controles"], dia["quando"]) == (2, 5, 2, 2, [20, 20], "2026-11-15T23:50:00+00:00")


# ── a segunda e a terceira leitura ───────────────────────────────────────────


def test_a_segunda_leitura_confirma_na_mesma_faixa(plano, gabarito, tmp_path: Path):
    """A segunda leitura é uma chamada nova, que não vê a primeira. Com outros códigos na
    mesma faixa, a leitura fica confirmada (r = 2) com os códigos da primeira."""
    itens = _universo(_item(1), _item(2, universos={leitura.VOTO}), _item(3))
    primeira = _lida(TOPO, porque="A primeira leitura.", confianca=2, duvida="e")
    _semear(tmp_path, plano, {("cd", 2026, "1"): primeira, ("cd", 2026, "2"): _lida(MINIMO), ("cd", 2026, "3"): _lida(MINIMO)})
    ia = LeitorFalso(gabarito, respostas={"Ementa cd 1 de 2026.": TOPO_TAMBEM})
    registro = ler.ler_universo(plano, tmp_path, itens, HOJE, agora=AGORA, editores=[ia])
    assert len(ia.calls) == 1 and ia.calls[0]["label"] == "leitura 1" and registro.passos == [(1, ler.SEGUNDA, "falso")]
    assert sorted(ia.ementas(0)) == ["Ementa cd 1 de 2026.", "Ementa cd 2 de 2026."]  # a de peso 60 e a votada; a terceira não pede
    assert "A primeira leitura" not in ia.calls[0]["user_text"] and TOPO.split()[0] not in ia.calls[0]["user_text"].split()
    guarda = _guarda(tmp_path, plano)
    assert guarda.de("cd", "1") == replace(primeira, r=leitura.DUAS)  # os códigos, a confiança e o porquê da primeira
    assert guarda.de("cd", "2").r == leitura.DUAS and guarda.de("cd", "3").r == leitura.UMA
    assert (registro.primeiras, registro.segundas, registro.confirmadas, registro.terceiras, registro.pendentes_segundas) == (0, 2, 2, 0, 0)
    assert leitura.avaliar(plano.regua, guarda.de("cd", "1")).faixa == "estruturante"  # agora conta como topo


@pytest.mark.parametrize(
    ("terceira", "r", "codigos", "conta"),
    [
        (TOPO_TAMBEM, leitura.TRES, TOPO, "decididas"),  # a terceira fica com a primeira: valem os códigos da primeira
        (MEDIO, leitura.TRES, MEDIO, "decididas"),  # fica com a segunda: valem os da segunda
        (MINIMO, leitura.INSTAVEL, TOPO, "instaveis"),  # três faixas: leitura instável, com os códigos da primeira guardados
    ],
)
def test_faixas_diferentes_pedem_a_terceira_e_ela_decide(plano, gabarito, tmp_path: Path, terceira: str, r: int, codigos: str, conta: str):
    itens = _universo(_item(1), _item(2))
    _semear(tmp_path, plano, {("cd", 2026, "1"): _lida(TOPO), ("cd", 2026, "2"): _lida(TOPO)})
    ia = LeitorFalso(gabarito, respostas={"Ementa cd 1 de 2026.": [MEDIO, terceira], "Ementa cd 2 de 2026.": TOPO})
    registro = ler.ler_universo(plano, tmp_path, itens, HOJE, agora=AGORA, editores=[ia])
    assert [c["label"] for c in ia.calls] == ["leitura 1", "leitura 2"] and registro.passos == [(1, ler.SEGUNDA, "falso"), (2, ler.TERCEIRA, "falso")]
    assert ia.ementas(1) == ["Ementa cd 1 de 2026."] and len(ia.lote(1)) == 11  # só a divergente, com os seus 10 controles
    guarda = _guarda(tmp_path, plano)
    assert (guarda.de("cd", "1").r, guarda.de("cd", "1").codigos) == (r, tuple(codigos.split()))
    assert guarda.de("cd", "2").r == leitura.DUAS
    assert (registro.segundas, registro.confirmadas, registro.terceiras, getattr(registro, conta), registro.decididas + registro.instaveis) == (2, 1, 1, 1, 1)
    assert registro.pendentes_segundas == 0
    assert leitura.avaliar(plano.regua, guarda.de("cd", "1")).lida is (r != leitura.INSTAVEL)


def test_sem_terceira_possivel_hoje_fica_como_esta_e_volta_amanha(plano, gabarito, tmp_path: Path):
    """As duas leituras divergem e a terceira não cabe (o teto de chamadas acabou; ou o lote
    da terceira foi descartado, ou veio sem a resposta): a leitura fica com r = 1, igual, e
    no dia seguinte a segunda leitura é refeita do começo."""
    itens = _universo(_item(1))
    primeira = _lida(TOPO, porque="A primeira.")
    for pasta, limites, extra in (
        (tmp_path / "teto", {"chamadas": 1}, {}),
        (tmp_path / "descartado", {}, {"erra_na_terceira": True}),
        (tmp_path / "omitida", {}, {"omite_na_terceira": True}),
    ):
        _semear(pasta, plano, {("cd", 2026, "1"): primeira})
        ia = LeitorFalso(gabarito, respostas={"Ementa cd 1 de 2026.": [MEDIO, MEDIO]})

        def na_terceira(ia=ia, extra=extra):
            if len(ia.calls) == 2:
                ia.erra_controles = bool(extra.get("erra_na_terceira"))
                ia.omitir = {"Ementa cd 1 de 2026."} if extra.get("omite_na_terceira") else set()

        ia.ao_chamar = na_terceira
        registro = ler.ler_universo(_com(plano, **limites), pasta, itens, HOJE, agora=AGORA, editores=[ia])
        assert _guarda(pasta, plano).de("cd", "1") == primeira, pasta.name
        assert (registro.segundas, registro.confirmadas, registro.terceiras, registro.decididas, registro.instaveis, registro.pendentes_segundas) == (1, 0, 0, 0, 0, 1), pasta.name
        assert len(ia.calls) == (1 if limites else 2)
        assert ("teto de 1 chamadas" in registro.motivo) is bool(limites)
        assert registro.descartados == (1 if extra.get("erra_na_terceira") else 0) and registro.invalidas == (1 if extra.get("omite_na_terceira") else 0)
        # No dia seguinte, outra segunda leitura, que agora concorda: confirmada.
        amanha = LeitorFalso(gabarito, respostas={"Ementa cd 1 de 2026.": TOPO_TAMBEM})
        registro = ler.ler_universo(plano, pasta, itens, "2026-11-16", editores=[amanha])
        assert _guarda(pasta, plano).de("cd", "1") == replace(primeira, r=leitura.DUAS) and registro.confirmadas == 1
        assert [d["data"] for d in _dias(pasta)] == [HOJE, "2026-11-16"]


def test_a_segunda_leitura_prefere_outro_editor_da_cadeia(plano, gabarito, tmp_path: Path):
    """A proposição lida pela primeira vez nesta rodada (pelo primeiro editor) tem a segunda
    leitura feita pelo editor seguinte; a terceira, por quem ainda não a leu, se houver. A
    que já estava na guarda segue a ordem da cadeia. Com um editor só, vale o mesmo, e o
    registro diz quantas foram."""
    itens = _universo(_item(1, universos={leitura.VOTO}), _item(2, universos={leitura.VOTO}), _item(3, universos={leitura.VOTO}))
    _semear(tmp_path / "tres", plano, {("cd", 2026, "3"): _lida(MINIMO)})
    a = LeitorFalso(gabarito, "a", respostas={"Ementa cd 1 de 2026.": TOPO})
    b = LeitorFalso(gabarito, "b", respostas={"Ementa cd 1 de 2026.": MEDIO})
    c = LeitorFalso(gabarito, "c", respostas={"Ementa cd 1 de 2026.": MEDIO})
    registro = ler.ler_universo(plano, tmp_path / "tres", itens, HOJE, agora=AGORA, editores=[a, b, c])
    assert registro.passos == [(1, ler.PRIMEIRA, "a"), (2, ler.SEGUNDA, "a"), (3, ler.SEGUNDA, "b"), (4, ler.TERCEIRA, "c")]
    assert [x["label"] for x in a.calls] == ["leitura 1", "leitura 2"]  # as primeiras; e a segunda da que já estava na guarda
    assert sorted(a.ementas(0)) == ["Ementa cd 1 de 2026.", "Ementa cd 2 de 2026."] and a.ementas(1) == ["Ementa cd 3 de 2026."]
    assert [x["label"] for x in b.calls] == ["leitura 3"] and sorted(b.ementas(0)) == ["Ementa cd 1 de 2026.", "Ementa cd 2 de 2026."]
    assert [x["label"] for x in c.calls] == ["leitura 4"] and c.ementas(0) == ["Ementa cd 1 de 2026."]
    guarda = _guarda(tmp_path / "tres", plano)
    assert (guarda.de("cd", "1").r, guarda.de("cd", "1").codigos) == (leitura.TRES, tuple(MEDIO.split()))  # a e b divergiram; c ficou com b
    assert guarda.de("cd", "2").r == guarda.de("cd", "3").r == leitura.DUAS
    assert (registro.primeiras, registro.segundas, registro.terceiras, registro.mesmo_editor, registro.provedor) == (2, 3, 1, 0, "a → b → c")

    so = LeitorFalso(gabarito, "so")
    registro = ler.ler_universo(plano, tmp_path / "um", itens, HOJE, agora=AGORA, editores=[so])
    assert registro.passos == [(1, ler.PRIMEIRA, "so"), (2, ler.SEGUNDA, "so")]
    assert (registro.primeiras, registro.segundas, registro.confirmadas, registro.mesmo_editor, registro.provedor) == (3, 3, 3, 3, "so")
    assert _dias(tmp_path / "um")[0]["mesmo_editor"] == 3

    # O editor que estoura o limite sai da cadeia: quem sobra faz as duas leituras.
    esgotado = LeitorFalso(gabarito, "esgotado", falha=LLMUnavailable("cota do dia", exhausted=True))
    reserva = LeitorFalso(gabarito, "reserva")
    registro = ler.ler_universo(plano, tmp_path / "esgotado", itens, HOJE, agora=AGORA, editores=[esgotado, reserva])
    assert len(esgotado.calls) == 1 and esgotado.exhausted and len(reserva.calls) == 2 and registro.passos == [(1, ler.PRIMEIRA, "reserva"), (2, ler.SEGUNDA, "reserva")]
    assert (registro.primeiras, registro.segundas, registro.mesmo_editor, registro.falhas, registro.provedor) == (3, 3, 3, 0, "reserva")


# ── os limites: prazo, chamadas, IA fora do ar ───────────────────────────────


def test_prazo_estourado_para_sem_perder_o_que_ja_leu(plano, gabarito, tmp_path: Path):
    """Cada chamada leva 200 s de um prazo de 480 (com 30 de reserva): saem três lotes e o
    quarto não sai. O que os três leram está gravado, e o registro diz por que parou."""
    comeco = time.monotonic() + 1000.0  # o relógio do teste anda na escala do relógio dos editores, só que aos saltos
    relogio = {"t": comeco}
    ia = LeitorFalso(gabarito, ao_chamar=lambda: relogio.__setitem__("t", relogio["t"] + 200))
    itens = _universo(*[_item(n) for n in range(1, 11)])
    registro = ler.ler_universo(_com(plano, lote=2), tmp_path, itens, HOJE, agora=AGORA, editores=[ia], relogio=lambda: relogio["t"])
    assert len(ia.calls) == 3 and all(c["deadline"] == comeco + 480 for c in ia.calls)  # o prazo vai junto a cada chamada
    assert registro.motivo == "parou antes do fim: prazo de 480 s"
    assert (registro.primeiras, registro.lotes, registro.pendentes_primeiras) == (6, 3, 4)
    assert sorted(_guarda(tmp_path, plano).da_casa("cd"), key=int) == ["5", "6", "7", "8", "9", "10"]
    assert _dias(tmp_path)[0]["motivo"] == registro.motivo and _dias(tmp_path)[0]["pendentes"] == {"primeiras": 4, "segundas": 0}


def test_rodada_interrompida_no_meio_nao_perde_o_lote_ja_lido(plano, gabarito, tmp_path: Path):
    """A guarda e o registro são gravados a cada lote: se o passo for morto na segunda
    chamada, a primeira já está no disco."""

    def morre():
        if len(ia.calls) == 2:
            raise KeyboardInterrupt

    ia = LeitorFalso(gabarito, ao_chamar=morre)
    itens = _universo(*[_item(n) for n in range(1, 5)])
    with pytest.raises(KeyboardInterrupt):
        ler.ler_universo(_com(plano, lote=2), tmp_path, itens, HOJE, agora=AGORA, editores=[ia])
    assert sorted(_guarda(tmp_path, plano).da_casa("cd")) == ["3", "4"]
    (dia,) = _dias(tmp_path)
    assert (dia["primeiras"], dia["lotes"], dia["pendentes"]) == (2, 1, {"primeiras": 2, "segundas": 0})


def test_teto_de_chamadas_e_ia_que_falha(plano, gabarito, tmp_path: Path):
    """O teto de chamadas vale para a rodada inteira. A chamada que falha (sem derrubar o
    editor) conta como lote sem resposta e a rodada segue; a cadeia toda esgotada encerra a
    rodada. Nada disso é erro: o que não foi lido fica para amanhã."""
    itens = _universo(*[_item(n) for n in range(1, 9)])
    ia = LeitorFalso(gabarito)
    registro = ler.ler_universo(_com(plano, lote=2, chamadas=2), tmp_path / "teto", itens, HOJE, editores=[ia])
    assert (len(ia.calls), registro.chamadas, registro.primeiras, registro.motivo) == (2, 2, 4, "parou antes do fim: teto de 2 chamadas")

    instavel = LeitorFalso(gabarito, falha=LLMUnavailable("resposta cortada"))
    registro = ler.ler_universo(_com(plano, lote=4), tmp_path / "falha", itens, HOJE, editores=[instavel])
    assert (len(instavel.calls), registro.falhas, registro.lotes, registro.primeiras, registro.pendentes_primeiras, registro.motivo) == (2, 2, 0, 0, 8, "")
    assert len(registro.avisos) == 2 and "a IA falhou" in registro.avisos[0] and registro.provedor == "" and registro.passos == [(1, ler.PRIMEIRA, ""), (2, ler.PRIMEIRA, "")]
    assert not list((tmp_path / "falha" / "leitura").glob("cd-*.json"))

    esgotada = LeitorFalso(gabarito, falha=LLMUnavailable("sem saldo", exhausted=True))
    registro = ler.ler_universo(_com(plano, lote=4), tmp_path / "esgotada", itens, HOJE, editores=[esgotada])
    assert (len(esgotada.calls), registro.falhas, registro.primeiras) == (1, 1, 0)
    assert registro.motivo == "parou antes do fim: os editores por IA estouraram o limite"

    quebrada = LeitorFalso(gabarito, falha=RuntimeError("erro que ninguém previu"))
    registro = ler.ler_universo(_com(plano, lote=8), tmp_path / "quebrada", itens, HOJE, editores=[quebrada])
    assert (registro.falhas, registro.primeiras, registro.pendentes_primeiras) == (1, 0, 8)


def test_o_lote_cabe_na_saida_do_editor_de_menor_limite(plano, gabarito):
    pequeno = LeitorFalso(gabarito)
    pequeno.max_tokens_write = 4000  # metade para a resposta: 2.000 tokens = 12 leituras, 10 delas controles
    for editores, tamanho in (([LeitorFalso(gabarito)], 30), ([LeitorFalso(gabarito), pequeno], 2)):
        rodada = ler.Rodada(plano=plano, data=HOJE, editores=editores, registro=ler.Registro(data=HOJE), prazo=0.0)
        assert rodada.tamanho == tamanho


def test_sem_editor_nao_le_e_registra_o_motivo(plano, gabarito, tmp_path: Path):
    """Sem nenhuma IA: a rodada diz o que leria, registra "sem IA: nada lido" e não grava
    leitura nenhuma."""
    itens = _universo(_item(1), _item(2))
    registro = ler.ler_universo(plano, tmp_path, itens, HOJE, agora=AGORA, editores=lambda: ([], "GEMINI_API_KEY não definida; ANTHROPIC_API_KEY não definida"))
    assert registro.motivo == "sem IA: nada lido (GEMINI_API_KEY não definida; ANTHROPIC_API_KEY não definida)"
    assert [i.titulo for i in registro.escolhidas] == ["PL 2/2026", "PL 1/2026"] and (registro.faltavam, registro.primeiras, registro.pendentes_primeiras) == (2, 0, 2)
    assert sorted(a.name for a in (tmp_path / "leitura").iterdir()) == ["diario.json"]
    assert _dias(tmp_path)[0]["motivo"].startswith("sem IA: nada lido") and _dias(tmp_path)[0]["pendentes"] == {"primeiras": 2, "segundas": 0}
    assert ler.ler_universo(plano, tmp_path, itens, HOJE, editores=[]).motivo == "sem IA: nada lido"

    # Com a guarda em dia: com IA, "nada a ler" (e nenhuma chamada); sem IA, o registro diz que não há IA.
    _semear(tmp_path / "em-dia", plano, {("cd", 2026, "1"): _lida(MINIMO), ("cd", 2026, "2"): _lida(TOPO, r=2)})
    ia = LeitorFalso(gabarito)
    registro = ler.ler_universo(plano, tmp_path / "em-dia", itens, HOJE, editores=[ia])
    assert registro.motivo == "nada a ler: a guarda está em dia" and (registro.faltavam, registro.esperavam_segunda, len(ia.calls)) == (0, 0, 0)
    registro = ler.ler_universo(plano, tmp_path / "em-dia", itens, HOJE, editores=lambda: ([], "nenhuma chave"))
    assert registro.motivo == "sem IA: nada lido (nenhuma chave)" and (registro.pendentes_primeiras, registro.pendentes_segundas) == (0, 0)


# ── o registro do dia ────────────────────────────────────────────────────────


def test_o_registro_do_dia(plano, gabarito, tmp_path: Path):
    """``diario.json``: uma linha por dia, com o que a rodada fez e os editores que
    responderam; ficam os últimos 60 dias; o resumo vai a meta.json."""
    pasta = tmp_path / "leitura"
    pasta.mkdir(parents=True)
    antigos = [{"data": f"2026-{mes:02d}-{dia:02d}", "rodadas": 1, "primeiras": 1, "segundas": 0, "lotes": 1, "descartados": 0, "controles": [10, 10]} for mes in (8, 9, 10) for dia in range(1, 24)]
    (pasta / leitura.DIARIO).write_text(ler.serializar_diario(antigos), encoding="utf-8")
    assert len(antigos) == 69 and leitura.ler_diario(pasta) == antigos

    itens = _universo(*[_item(n) for n in range(1, 5)], _item(9, universos={leitura.VOTO}))
    ia = LeitorFalso(gabarito, respostas={"Ementa cd 4 de 2026.": TOPO})
    registro = ler.ler_universo(_com(plano, lote=3), tmp_path, itens, HOJE, agora=AGORA, editores=[ia])
    texto = (pasta / leitura.DIARIO).read_text(encoding="utf-8")
    linhas = texto.splitlines()
    assert linhas[0] == '{"v":1,"dias":[' and linhas[-1] == "]}" and len(linhas) == 62  # um dia por linha: os últimos 60
    dias = json.loads(texto)["dias"]
    assert [d["data"] for d in dias] == [d["data"] for d in antigos[10:]] + [HOJE]
    assert dias[-1] == {
        "data": HOJE, "quando": AGORA, "rodadas": 1, "primeiras": 5, "segundas": 2, "terceiras": 0, "lotes": 3, "descartados": 0, "falhas": 0,
        "controles": [30, 30], "confirmadas": 2, "decididas": 0, "instaveis": 0, "invalidas": 0, "mesmo_editor": 2, "chamadas": 3,
        "tokens": {"entrada": 3000, "saida": 600}, "pendentes": {"primeiras": 0, "segundas": 0}, "provedor": "falso", "modelo": "modelo-falso", "motivo": "",
    }  # fmt: skip
    assert dias[-1] == ler.linha(registro) and json.loads(linhas[-2]) == dias[-1]

    resumo = leitura.resumo_do_diario(pasta)
    assert {k: v for k, v in resumo.items() if k != "periodo"} == dias[-1]
    assert resumo["periodo"] == {
        "dias": 60, "desde": antigos[10]["data"], "leu_em": HOJE, "rodadas": 60, "primeiras": 59 + 5, "segundas": 2, "terceiras": 0, "lotes": 59 + 3, "descartados": 0,
        "falhas": 0, "confirmadas": 2, "decididas": 0, "instaveis": 0, "invalidas": 0, "mesmo_editor": 2, "chamadas": 3, "controles": [59 * 10 + 30, 59 * 10 + 30],
    }  # fmt: skip
    # No dia seguinte, sem nada a ler: a linha do dia diz isso, e "leu_em" continua sendo o último dia com leitura.
    ler.ler_universo(plano, tmp_path, itens, "2026-11-16", agora="2026-11-16T09:00:00+00:00", editores=[ia])
    resumo = leitura.resumo_do_diario(pasta)
    assert (resumo["data"], resumo["primeiras"], resumo["motivo"], resumo["controles"]) == ("2026-11-16", 0, "nada a ler: a guarda está em dia", [0, 0])
    assert (resumo["periodo"]["leu_em"], resumo["periodo"]["dias"], resumo["periodo"]["desde"]) == (HOJE, 60, antigos[11]["data"])
    # Arquivo ausente, ilegível ou de outro formato: sem registro, e a rodada começa um novo.
    assert leitura.resumo_do_diario(tmp_path / "nada") is None and leitura.ler_diario(tmp_path / "nada") == []
    for ruim in ("{não é json", '{"v":2,"dias":[]}', '{"v":1,"dias":{}}', "[]"):
        (pasta / leitura.DIARIO).write_text(ruim, encoding="utf-8")
        assert leitura.ler_diario(pasta) == [] and leitura.resumo_do_diario(pasta) is None
    (pasta / leitura.DIARIO).write_text('{"v":1,"dias":[{"data":"ontem"},"lixo",{"data":"2026-11-02","primeiras":"duas","controles":[1]}]}', encoding="utf-8")
    assert leitura.resumo_do_diario(pasta)["periodo"] == {**{k: 0 for k in leitura.SOMAS_DO_DIARIO}, "dias": 1, "desde": "2026-11-02", "leu_em": None, "controles": [0, 0]}


def test_juntar_soma_as_rodadas_do_mesmo_dia():
    primeira = ler.linha(ler.Registro(data=HOJE, quando="t1", primeiras=3, lotes=1, controles_certos=3, controles_total=3, chamadas=1, tokens_entrada=10, tokens_saida=2, provedor="a", modelo="m-a", motivo="parou antes do fim: prazo de 480 s", pendentes_primeiras=7, avisos=["um"]))
    segunda = ler.linha(ler.Registro(data=HOJE, quando="t2", primeiras=7, segundas=2, lotes=3, descartados=1, controles_certos=5, controles_total=9, confirmadas=2, chamadas=3, tokens_entrada=30, tokens_saida=5, provedor="b → a", modelo="m-b + m-a"))
    junta = ler.juntar(primeira, segunda)
    assert (junta["rodadas"], junta["primeiras"], junta["segundas"], junta["lotes"], junta["descartados"], junta["chamadas"], junta["confirmadas"]) == (2, 10, 2, 4, 1, 4, 2)
    assert (junta["controles"], junta["tokens"], junta["provedor"], junta["modelo"]) == ([8, 12], {"entrada": 40, "saida": 7}, "a → b", "m-a + m-b")
    assert (junta["quando"], junta["motivo"], junta["pendentes"]) == ("t2", "", {"primeiras": 0, "segundas": 0}) and "avisos" not in junta
    assert ler.juntar(None, segunda) == segunda and list(junta) == list(segunda)


# ── nada é apagado ───────────────────────────────────────────────────────────


def test_nenhuma_leitura_existente_e_apagada(plano, gabarito, tmp_path: Path):
    """A rodada só acrescenta primeiras leituras e muda o ``r`` das que relê. O que já estava
    na guarda (confirmada, instável, de pouca confiança, fora do universo de hoje, de outro
    ano) fica igual, linha por linha; arquivo em que nada mudou nem é regravado; o arquivo de
    outra versão das instruções não é sobrescrito."""
    antes = {
        ("cd", 2026, "1"): _lida(TOPO, r=leitura.DUAS, porque="Confirmada."),
        ("cd", 2026, "2"): _lida(MEDIO, r=leitura.INSTAVEL, porque="Instável."),
        ("cd", 2026, "3"): _lida(PONTUAL, confianca=1, duvida="m", porque="Pouca confiança."),
        ("cd", 2026, "4"): _lida(TOPO, porque="Espera a segunda."),
        ("cd", 2024, "77"): _lida(MINIMO, porque="Fora do universo de hoje."),
        ("sf", 2025, "PL-8-2025"): _lida(MINIMO, r=leitura.TRES, porque="Do Senado."),
    }
    _semear(tmp_path, plano, antes)
    pasta = tmp_path / "leitura"
    de_outra_versao = '{"v":1,"regua":99,"casa":"cd","ano":2023,"itens":{\n"500":["fiscal","geral","sistema","nd","neutro","neutro",0,3,"","De outra versão.",2]\n}}\n'
    (pasta / "cd-2023.json").write_text(de_outra_versao, encoding="utf-8")
    (pasta / leitura.AUDITORIA).write_text('{"modelo":"x"}', encoding="utf-8")
    textos = {a.name: a.read_text(encoding="utf-8") for a in pasta.iterdir()}
    idades = {a.name: a.stat().st_mtime_ns for a in pasta.iterdir()}

    itens = _universo(*[_item(n) for n in range(1, 7)], _item(8, casa="sf", ano=2025), _item(9, casa="sf", ano=2025), _item(500, ano=2023), _item(501, ano=2023))
    ia = LeitorFalso(gabarito, respostas={"Ementa cd 4 de 2026.": TOPO_TAMBEM})
    registro = ler.ler_universo(plano, tmp_path, itens, HOJE, agora=AGORA, editores=[ia])
    guarda = _guarda(tmp_path, plano)
    for (casa, ano, chave), lida in antes.items():
        esperada = replace(lida, r=leitura.DUAS) if chave == "4" else lida
        assert guarda.de(casa, chave) == esperada and guarda.ano_de(casa, chave) == ano
    assert sorted(ia.ementas(0)) == ["Ementa cd 5 de 2026.", "Ementa cd 6 de 2026.", "Ementa sf 9 de 2025."]  # só o que não tinha leitura
    assert ia.ementas(1) == ["Ementa cd 4 de 2026."]  # e, na segunda, só a de peso 60 com um leitor
    assert (registro.primeiras, registro.segundas, len(guarda)) == (3, 1, len(antes) + 3)
    # As linhas antigas continuam nos arquivos, iguais; as novas entraram na ordem da chave.
    for nome in ("cd-2026.json", "sf-2025.json"):
        novas = (pasta / nome).read_text(encoding="utf-8").splitlines()
        velhas = [linha.rstrip(",") for linha in textos[nome].splitlines()[1:-1]]
        mantidas = [linha for linha in velhas if not linha.startswith('"4":')]
        assert all(linha in [n.rstrip(",") for n in novas] for linha in mantidas) and len(novas) > len(textos[nome].splitlines())
    assert '"4":["fiscal","geral","ajuste","corta","neutro","neutro",0,2,"","Espera a segunda.",2]' in (pasta / "cd-2026.json").read_text(encoding="utf-8")
    # O que não mudou não foi regravado; o arquivo de outra versão ficou como estava, e as
    # proposições daquele ano não foram lidas (a rodada não grava por cima dele).
    for nome in ("cd-2024.json", "cd-2023.json", leitura.AUDITORIA):
        assert (pasta / nome).read_text(encoding="utf-8") == textos[nome] and (pasta / nome).stat().st_mtime_ns == idades[nome]
    assert sorted(p.name for p in registro.gravados) == ["cd-2026.json", "sf-2025.json"]
    assert any("cd-2023.json" in aviso for aviso in registro.avisos) and registro.pendentes_primeiras == 0
    assert sorted(a.name for a in pasta.iterdir()) == sorted([*textos, leitura.DIARIO])


# ── o comando, com o Congresso de mentira ────────────────────────────────────


@pytest.fixture
def publicado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A pasta publicada com o Congresso de mentira já montado (tests/fixtures/congresso), a
    régua de verdade nas contas e nenhuma chave de IA no ambiente."""
    com_regua = fabrica.regras_de_teste()
    com_regua.projeto_de_pais = regras.carregar(ROOT).projeto_de_pais
    dados.atualizar(tmp_path, tmp_path / "data", tmp_path / "cache", dados.Opcoes(hoje=fabrica.HOJE, projetos_desde=2026), fetch=fabrica.FetchFalso(), regras=com_regua)
    monkeypatch.setattr(cli, "Coletor", lambda cache: fontes.Coletor(cache, fetch=fabrica.FetchFalso(), pausa=0.0))
    for nome in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY", "AIMLAPI_KEY", "SENSENOVA_API_KEY", "MOONSHOT_API_KEY", "QIJ_NO_LEITURA", "QIJ_NO_LLM", "QIJ_NO_CONGRESSO", "QIJ_BRAND", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(nome, raising=False)
    return tmp_path


def _comando(publicado: Path, *extra: str) -> list[str]:
    return ["congresso", "ler", "--dir", str(publicado), "--cache", str(publicado / "cache"), "--now", f"{fabrica.HOJE}T12:00:00", *extra]


def test_sem_chave_de_ia_o_comando_nao_le_e_nao_falha(publicado: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """Sem nenhuma chave no ambiente: o comando diz o que leria, registra "sem IA: nada lido"
    e sai com 0. Com a leitura desligada (QIJ_NO_LEITURA, ou no config), nem carrega nada."""
    resumo = tmp_path / "resumo.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(resumo))
    with caplog.at_level("INFO"):
        assert main(_comando(publicado)) == 0
    pasta = publicado / "data" / "congresso" / "leitura"
    assert sorted(a.name for a in pasta.iterdir()) == ["diario.json"]
    (dia,) = leitura.ler_diario(pasta)
    assert dia["data"] == fabrica.HOJE and dia["quando"] == f"{fabrica.HOJE}T12:00:00+00:00" and dia["primeiras"] == 0
    assert dia["motivo"].startswith("sem IA: nada lido (") and "GEMINI_API_KEY não definida" in dia["motivo"] and "ANTHROPIC_API_KEY não definida" in dia["motivo"]
    assert dia["pendentes"]["primeiras"] > 0
    assert "a ler hoje: " in caplog.text and "PL 10/2026 (cd)" in caplog.text and "sem IA: nada lido" in caplog.text
    texto = resumo.read_text(encoding="utf-8")
    assert "### 📖 Leitura dos projetos de lei" in texto and "| Primeiras leituras | 0 |" in texto and "Sem IA: nada lido (GEMINI_API_KEY não definida; AIMLAPI_KEY não definida" in texto

    antes = (pasta / "diario.json").read_text(encoding="utf-8")
    monkeypatch.setenv("QIJ_NO_LEITURA", "1")
    assert main(_comando(publicado)) == 0 and (pasta / "diario.json").read_text(encoding="utf-8") == antes
    monkeypatch.delenv("QIJ_NO_LEITURA")
    monkeypatch.setenv("QIJ_NO_CONGRESSO", "1")
    assert main(_comando(publicado)) == 0 and (pasta / "diario.json").read_text(encoding="utf-8") == antes
    monkeypatch.delenv("QIJ_NO_CONGRESSO")
    monkeypatch.setattr(ler, "preparar", lambda root: replace(ler.Plano(None, None, ler.Limites(ligada=False), "", ())))
    assert main(_comando(publicado)) == 0 and (pasta / "diario.json").read_text(encoding="utf-8") == antes
    assert "Desligada" in resumo.read_text(encoding="utf-8")


def test_o_comando_le_com_a_cadeia_do_jornal_e_a_atualizacao_leva_o_resumo(publicado: Path, gabarito, monkeypatch: pytest.MonkeyPatch):
    """De ponta a ponta: ``congresso ler`` monta a cadeia como a edição (``llm.providers``),
    lê todo o universo do Congresso de mentira (as votadas primeiro), confirma as votadas com
    a segunda leitura e grava a guarda e o registro. A atualização seguinte usa as leituras e
    leva o resumo do registro a ``meta.json`` → ``leitura_projetos.diaria``."""
    ia = LeitorFalso(gabarito, respostas={"Altera a tabela do imposto de renda.": TOPO})
    monkeypatch.setattr(edit, "_backends", lambda config, client: ([("falso", lambda: ia)], []))
    assert main(_comando(publicado)) == 0
    pasta = publicado / "data" / "congresso"
    plano = ler.preparar(ROOT)
    guarda = _guarda(pasta, plano)
    coletor = fontes.Coletor(publicado / "cache", fetch=fabrica.FetchFalso(), pausa=0.0)
    itens = leitura.coletar(plano.regras, pasta, coletor, fabrica.HOJE, projetos_desde=2026)
    com_ementa = [i for casa in itens for i in itens[casa].values() if i.ementa]
    votadas = [i for i in com_ementa if leitura.VOTO in i.universos]
    assert len(com_ementa) >= 7 and len(votadas) == 4  # PL 10, PL 11 e PL 12 na Câmara; PL 20 no Senado
    assert len(guarda) == len(com_ementa) and ler.faltam_ler(itens, guarda)[0] == [] and ler.pedem_segunda(plano.regua, itens, guarda) == []
    assert all(guarda.de(i.casa, i.chave).r == leitura.DUAS for i in votadas)
    assert guarda.de("cd", "1010").codigos == tuple(TOPO.split()) and guarda.ano_de("cd", "1010") == 2026 and guarda.de("sf", "PL-20-2026") is not None
    assert [c["label"] for c in ia.calls] == ["leitura 1", "leitura 2"]
    assert "Ana Lima" not in ia.calls[0]["user_text"] and "PL 10" not in ia.calls[0]["user_text"] and "1010" not in ia.calls[0]["user_text"]
    (dia,) = leitura.ler_diario(pasta / "leitura")
    assert (dia["primeiras"], dia["segundas"], dia["confirmadas"], dia["lotes"], dia["controles"], dia["provedor"], dia["mesmo_editor"]) == (len(com_ementa), 4, 4, 2, [20, 20], "falso", 4)
    assert dia["pendentes"] == {"primeiras": 0, "segundas": 0} and dia["motivo"] == ""

    # No dia seguinte, a guarda está em dia: nenhuma chamada, e o registro ganha a linha do dia.
    assert main(["congresso", "ler", "--dir", str(publicado), "--cache", str(publicado / "cache"), "--now", "2026-11-14T12:00:00"]) == 0
    assert len(ia.calls) == 2 and [d["data"] for d in leitura.ler_diario(pasta / "leitura")] == [fabrica.HOJE, "2026-11-14"]

    com_regua = fabrica.regras_de_teste()
    com_regua.projeto_de_pais = plano.regua
    resultado = dados.atualizar(publicado, publicado / "data", publicado / "cache", dados.Opcoes(hoje=fabrica.HOJE, projetos_desde=2026), fetch=fabrica.FetchFalso(), regras=com_regua)
    assert resultado.avisos == []
    diaria = json.loads((pasta / "meta.json").read_text(encoding="utf-8"))["leitura_projetos"]["diaria"]
    assert diaria == leitura.resumo_do_diario(pasta / "leitura")
    assert (diaria["data"], diaria["primeiras"], diaria["motivo"]) == ("2026-11-14", 0, "nada a ler: a guarda está em dia")
    assert (diaria["periodo"]["leu_em"], diaria["periodo"]["primeiras"], diaria["periodo"]["controles"], diaria["periodo"]["dias"]) == (fabrica.HOJE, len(com_ementa), [20, 20], 2)
    assert json.loads((pasta / "casa-cd.json").read_text(encoding="utf-8"))["lp"]["cobertura"]["confirmados"] == 3  # as três votadas da Câmara
    assert (pasta / "leitura" / "diario.json").is_file() and len(_guarda(pasta, plano)) == len(com_ementa)  # a atualização não mexe na guarda


def test_a_cadeia_e_montada_como_a_da_edicao(gabarito, monkeypatch: pytest.MonkeyPatch):
    """Os editores saem de ``llm.providers``, cada um com a sua chave no ambiente, na ordem
    do config; ``editores`` no bloco da leitura restringe e reordena; ``QIJ_NO_LLM`` desliga."""
    for nome in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY", "AIMLAPI_KEY", "SENSENOVA_API_KEY", "MOONSHOT_API_KEY"):
        monkeypatch.delenv(nome, raising=False)
    config = load_config(env={})
    editores, motivo = ler.montar_editores(config, ler.Limites())
    assert editores == [] and motivo.count("GEMINI_API_KEY não definida") == 1 and motivo.endswith("ANTHROPIC_API_KEY não definida")
    monkeypatch.setenv("GEMINI_API_KEY", chat_fakes.KEY)
    monkeypatch.setenv("MISTRAL_API_KEY", chat_fakes.KEY)
    editores, motivo = ler.montar_editores(config, ler.Limites())
    assert [e.provider for e in editores] == ["gemini", "gemini_lite", "mistral"] and motivo == "" and all(isinstance(e, ChatBackend) for e in editores)
    editores, _motivo = ler.montar_editores(config, ler.Limites(editores=("mistral", "gemini", "kimi")))
    assert [e.provider for e in editores] == ["mistral", "gemini"]
    editores, motivo = ler.montar_editores(config, ler.Limites(editores=("kimi",)))
    assert editores == [] and "MOONSHOT_API_KEY não definida" in motivo
    assert ler.montar_editores(load_config(env={"QIJ_NO_LLM": "1"}), ler.Limites()) == ([], "IA desligada na configuração")


def test_um_editor_de_verdade_com_a_api_de_mentira_le_um_lote(plano, gabarito, tmp_path: Path):
    """O caminho inteiro de uma chamada com o editor do jornal (``ChatBackend``) e uma API de
    chat de mentira: o pedido leva as instruções e o esquema, e a resposta vira leitura."""
    pedidos = []

    def post(url, headers, body, timeout):
        pedido = json.loads(body)
        pedidos.append(pedido)
        blocos = BLOCO.findall(pedido["messages"][1]["content"])
        return chat_fakes.ok({"leituras": [{"i": pid, **_codigos(gabarito.get(ementa, MEDIO))} for pid, _tipo, ementa in blocos]}, usage=(21000, 900), model="gemini-de-mentira")

    editor = ChatBackend("gemini", load_config(env={}), api_key=chat_fakes.KEY, post=post)
    registro = ler.ler_universo(plano, tmp_path, _universo(_item(1), _item(2)), HOJE, agora=AGORA, editores=[editor])
    assert (registro.primeiras, registro.lotes, registro.controles_certos, registro.controles_total, registro.chamadas, registro.provedor) == (2, 1, 10, 10, 1, "gemini")
    assert (registro.tokens_entrada, registro.tokens_saida, registro.modelo) == (21000, 900, editor.model)
    (pedido,) = pedidos
    assert pedido["messages"][0]["content"].startswith("Você classifica proposições legislativas brasileiras") and pedido["messages"][0]["content"].endswith(ler.ENTREGA)
    assert pedido["response_format"]["json_schema"]["schema"] == ler.esquema(plano.regua, [f"p{k:04d}" for k in range(1, 13)])  # 2 proposições e 10 controles
    assert pedido["response_format"]["json_schema"]["name"] == "leitura_1" and registro.passos == [(1, ler.PRIMEIRA, "gemini")]
    assert _guarda(tmp_path, plano).de("cd", "1").codigos == tuple(MEDIO.split())


def test_configuracao_quebrada_e_o_unico_erro_do_comando(publicado: Path, gabarito, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """Código 1 só com o config quebrado. Erro inesperado no meio da rodada vira aviso, e o
    comando sai com 0 sem mexer no que estava gravado."""
    resumo = tmp_path / "resumo.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(resumo))
    args = argparse.Namespace(dir=str(publicado), cache=str(publicado / "cache"), now=None, max=None)
    quebrado = load_config(env={})
    quebrado.root = _raiz_com(tmp_path / "quebrado", **{leitura.REFERENCIA: None})
    assert cli.cmd_ler(args, quebrado) == cli.EXIT_ERROR
    assert "configuração quebrada" in caplog.text and "Configuração quebrada" in resumo.read_text(encoding="utf-8")
    assert not (publicado / "data" / "congresso" / "leitura").exists()

    def explode(*_a, **_k):
        raise RuntimeError("fonte fora do ar de um jeito novo")

    monkeypatch.setattr(ler, "executar", explode)
    assert main(_comando(publicado)) == 0
    assert "erro inesperado" in caplog.text and "Erro inesperado" in resumo.read_text(encoding="utf-8")


# ── os fluxos do GitHub ──────────────────────────────────────────────────────

SEGREDOS_DE_IA = ("GEMINI_API_KEY", "AIMLAPI_KEY", "SENSENOVA_API_KEY", "MISTRAL_API_KEY", "MOONSHOT_API_KEY", "ANTHROPIC_API_KEY")


@pytest.mark.parametrize(("fluxo", "job"), [("daily.yml", "edition"), ("congresso.yml", "congresso")])
def test_os_fluxos_leem_os_projetos_no_fim_da_edicao_diaria_e_antes_de_atualizar_no_fluxo_manual(fluxo: str, job: str):
    """Nos dois fluxos, o passo "Ler os projetos novos" é acessório (continue-on-error, com
    prazo próprio), usa o mesmo cache da atualização e recebe os mesmos segredos de IA e as
    mesmas variáveis de modelo que o passo da edição. No fluxo manual do Congresso, vem logo
    antes de "Atualizar o Congresso", e o que lê entra no commit do fluxo. Na edição diária,
    vem por último, depois de gerar e publicar a edição e de enviar o e-mail (a edição usa as
    IAs primeiro e não espera a leitura), e é seguido de "Publicar a leitura", também
    acessório, que leva só data/congresso/leitura num commit próprio."""
    yml = yaml.safe_load((ROOT / ".github" / "workflows" / fluxo).read_text(encoding="utf-8"))
    passos = yml["jobs"][job]["steps"]
    nomes = [p.get("name") for p in passos]
    assert nomes.count("Ler os projetos novos") == 1
    onde = nomes.index("Ler os projetos novos")
    assert nomes.index("Cache do que foi baixado do Congresso") < onde
    ler_, atualizar = passos[onde], passos[nomes.index("Atualizar o Congresso")]
    assert ler_["continue-on-error"] is True and 5 <= ler_["timeout-minutes"] <= 20
    assert 'python -m qijournal congresso ler --dir . --cache "$RUNNER_TEMP/congresso"' in ler_["run"]
    assert '--cache "$RUNNER_TEMP/congresso"' in atualizar["run"] or "$RUNNER_TEMP/congresso" in atualizar["run"]
    for segredo in SEGREDOS_DE_IA:
        assert ler_["env"][segredo] == f"${{{{ secrets.{segredo} }}}}"
    edicao = next(p for p in yaml.safe_load((ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8"))["jobs"]["edition"]["steps"] if p.get("name") == "Gerar a edição")
    da_ia = {k: v for k, v in edicao["env"].items() if k in SEGREDOS_DE_IA or (k.startswith("QIJ_") and k.endswith("MODEL"))}
    assert {k: ler_["env"].get(k) for k in da_ia} == da_ia
    assert ler_["env"]["QIJ_NO_LEITURA"] == "${{ vars.QIJ_NO_LEITURA }}" and ler_["env"]["QIJ_NO_CONGRESSO"] == "${{ vars.QIJ_NO_CONGRESSO }}"
    publicar = next(p for p in passos if p.get("name") == "Publicar (commit e push)")
    caminhos = re.search(r'git add "\$@" -- (.+)', publicar["run"]).group(1).split()
    if fluxo == "congresso.yml":
        # Logo antes da atualização, que já usa as leituras; a guarda e o registro
        # (data/congresso/leitura/) entram no commit que o fluxo já faz.
        assert onde + 1 == nomes.index("Atualizar o Congresso")
        assert any(c in ("data", "data/congresso") for c in caminhos)
    else:
        # A edição primeiro: a leitura vem depois de gerar e publicar a edição e de enviar o e-mail.
        for antes in ("Gerar a edição", "Publicar (commit e push)", "Enviar e-mail (SMTP)"):
            assert nomes.index(antes) < onde, antes
        # O disparo manual "Gerar sem IA" vale também para a leitura.
        assert ler_["env"]["INPUT_NO_LLM"] == "${{ inputs.no_llm }}" and 'if [ "$INPUT_NO_LLM" = "true" ]; then export QIJ_NO_LLM=1; fi' in ler_["run"]
        # O que foi lido vai num commit próprio, logo em seguida: só a guarda e o registro do dia.
        assert nomes.count("Publicar a leitura (commit e push)") == 1 and nomes[onde + 1] == "Publicar a leitura (commit e push)"
        da_leitura = passos[onde + 1]
        assert da_leitura["continue-on-error"] is True
        adicoes = re.findall(r"git add\b[^\n]*", da_leitura["run"])
        assert adicoes and set(adicoes) == {"git add -- data/congresso/leitura"}
        assert not re.search(r"git commit\b[^\n]*(?:\s-a\b|--all\b)", da_leitura["run"])  # o commit leva só o que o "git add" pôs
        assert "git commit" in da_leitura["run"] and 'git push origin "HEAD:${GITHUB_REF_NAME}"' in da_leitura["run"]
    assert yml["jobs"][job]["timeout-minutes"] >= {"daily.yml": 95, "congresso.yml": 55}[fluxo]
