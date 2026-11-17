"""A leitura diária dos projetos de lei novos (qijournal.congresso.ler): o que lê e em que
ordem, os lotes cegos com perguntas de controle, a segunda e a terceira leitura, os limites,
o registro do dia e o comando ``congresso ler``. Tudo com editores de mentira, sem rede. No
fim, o fluxo do GitHub que a roda (``leitura.yml``): o passo que decide e o que publica são
rodados de verdade, em repositórios git locais."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from qijournal import edit
from qijournal.__main__ import main
from qijournal.config import ROOT, _truthy, load_config
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

    # --max 3: as três primeiras seguintes e nenhuma segunda.
    registro = ler.ler_universo(_com(plano, lote=2), tmp_path, itens, HOJE, agora=AGORA, editores=[ia], maximo=3)
    assert (registro.primeiras, registro.segundas, registro.pendentes_primeiras, registro.pendentes_segundas) == (3, 0, 2, 2)
    # --max 0 só diz o que leria: não chama a IA e não grava nada (nem a guarda, nem o registro
    # do dia, que continua com as duas rodadas que leram e o motivo da última).
    def no_disco() -> dict[str, tuple[bytes, int]]:
        return {a.name: (a.read_bytes(), a.stat().st_mtime_ns) for a in (tmp_path / "leitura").iterdir()}

    antes, chamadas = no_disco(), len(ia.calls)
    registro = ler.ler_universo(plano, tmp_path, itens, HOJE, agora="2026-11-15T23:59:00+00:00", editores=[ia], maximo=0)
    assert len(ia.calls) == chamadas and registro.primeiras == 0 and "--max 0" in registro.motivo
    assert (registro.faltavam, registro.esperavam_segunda, registro.escolhidas) == (2, 2, [])
    assert (registro.pendentes_primeiras, registro.pendentes_segundas, registro.gravados) == (2, 2, [])
    assert no_disco() == antes and sorted(antes) == ["cd-2020.json", "cd-2026.json", "diario.json"]
    (dia,) = _dias(tmp_path)
    assert (dia["rodadas"], dia["primeiras"], dia["quando"], dia["motivo"]) == (2, 8, AGORA, "")
    # Sem guarda nenhuma, nem a pasta é criada; e os editores nem são montados.
    registro = ler.ler_universo(plano, tmp_path / "vazia", itens, HOJE, editores=lambda: pytest.fail("--max 0 não monta a cadeia de IAs"), maximo=0)
    assert not (tmp_path / "vazia").exists() and (registro.faltavam, registro.pendentes_primeiras) == (10, 10)


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


def test_os_controles_tem_teto_por_faixa_e_nao_se_repetem_nos_lotes_que_o_teto_de_chamadas_permite(plano):
    """Com os limites de verdade (10 controles por lote, mínimo de 7 certos, até 12 chamadas, 185
    perguntas), em 200 datas: nenhum lote leva mais de 6 perguntas da mesma faixa (o mínimo
    menos um) nem repete pergunta, e os 12 lotes de uma rodada levam 120 perguntas diferentes.
    A pergunta que estouraria o teto da faixa não se perde: fica na fila e sai no lote seguinte.
    A lista do dia só dá a volta depois do teto de chamadas; daí em diante as perguntas voltam,
    nunca duas vezes no mesmo lote, e o teto continua valendo. O lote de um número é sempre o
    mesmo, seja qual for a ordem das chamadas."""
    limites = plano.limites
    teto = limites.controles_minimo - 1
    assert (limites.controles, limites.controles_minimo, limites.chamadas, teto) == (10, 7, 12, 6)
    faixa_maior, quantas = Counter(c.faixa for c in plano.controles).most_common(1)[0]
    assert quantas * 2 > len(plano.controles) == 185  # mais da metade das perguntas cai numa faixa só

    def chaves(controles) -> set[tuple[str, str]]:
        return {(c.item.casa, c.item.chave) for c in controles}

    def da_faixa_maior(controles) -> list[ler.Controle]:
        return [c for c in controles if c.faixa == faixa_maior]

    deu_a_volta, estourariam = [], 0
    for k in range(200):
        data = (date(2026, 11, 15) + timedelta(days=k)).isoformat()
        rodada = ler.Rodada(plano=plano, data=data, editores=[], registro=ler.Registro(data=data), prazo=0.0)
        lotes = [rodada.controles_do_lote(n, []) for n in range(1, 23)]
        assert all(len(lote) == len(chaves(lote)) == 10 and max(Counter(c.faixa for c in lote).values()) <= teto for lote in lotes), data
        assert len(chaves(c for lote in lotes[: limites.chamadas] for c in lote)) == limites.chamadas * limites.controles == 120, data
        deu_a_volta.append(next(n for n, lote in enumerate(lotes, 1) if chaves(lote) & chaves(c for antes in lotes[: n - 1] for c in antes)))
        # As dez primeiras da lista do dia: as que passam do teto da faixa ficam para o lote 2, e entram as seguintes das outras faixas.
        ordem = rodada._ordem_dos_controles
        sobram = da_faixa_maior(ordem[:10])[teto:]
        assert [c for c in lotes[0] if c in ordem[:10]] == [c for c in ordem[:10] if c not in sobram] and len(da_faixa_maior(lotes[0])) == min(teto, len(da_faixa_maior(ordem[:10])))
        assert lotes[1][: len(sobram)] == sobram and not [c for c in lotes[0][10 - len(sobram) :] if c.faixa == faixa_maior]
        estourariam += bool(sobram)
        if k == 0:
            outra = ler.Rodada(plano=plano, data=data, editores=[], registro=ler.Registro(data=data), prazo=0.0)
            assert [outra.controles_do_lote(n, []) for n in (12, 3, 19, 1)] == [lotes[11], lotes[2], lotes[18], lotes[0]]
    assert estourariam > 20  # sem o teto, mais de um em cada dez primeiros lotes levaria 7 ou mais perguntas da mesma faixa
    assert limites.chamadas < min(deu_a_volta) and max(deu_a_volta) <= len(plano.controles) // limites.controles + 1  # a volta vem entre o 13º e o 19º lote
    # Sem mínimo de acertos não há teto por faixa; e o config que não dá para respeitar (teto de 1 por faixa, 5 faixas, 10 por lote) completa o lote assim mesmo.
    sem_minimo = ler.Rodada(plano=_com(plano, controles_minimo=0), data=HOJE, editores=[], registro=ler.Registro(data=HOJE), prazo=0.0)
    assert sem_minimo.controles_do_lote(1, []) == sem_minimo._ordem_dos_controles[:10]
    apertado = ler.Rodada(plano=_com(plano, controles_minimo=2), data=HOJE, editores=[], registro=ler.Registro(data=HOJE), prazo=0.0)
    assert len(chaves(apertado.controles_do_lote(1, []))) == 10 and len({c.faixa for c in apertado.controles_do_lote(1, [])[:5]}) == 5


def test_quem_responde_a_mesma_coisa_para_tudo_nao_passa_nos_controles(plano, gabarito, tmp_path: Path):
    """Mais da metade das perguntas de controle cai numa faixa só. Sem o teto por faixa, o
    leitor que marca tudo com os mesmos códigos dessa faixa acertava 7 de 10 em mais de um lote
    a cada cinco, e cada lote aceito punha 30 leituras iguais na guarda. Com o teto, ele nunca
    chega ao mínimo, em faixa nenhuma: todo lote é descartado e nada vai à guarda."""

    class Constante(LeitorFalso):
        def call(self, *, label, system, user_text, schema, max_tokens, deadline=None):
            self.calls.append({"label": label, "user_text": user_text})
            self.usage.record(self.model, 1000, 200)
            return {"leituras": [{"i": pid, **_codigos(self.padrao)} for pid, _tipo, _ementa in BLOCO.findall(user_text)]}

    itens = _universo(*[_item(n) for n in range(1, 61)])  # dois lotes de 30 por dia
    for codigos in (PONTUAL, MINIMO, MEDIO, TOPO, SIMBOLICO):
        for k in range(15):
            data = (date(2026, 11, 1) + timedelta(days=k)).isoformat()
            pasta = tmp_path / f"{codigos.split()[2]}-{k}"
            registro = ler.ler_universo(_com(plano, segundas=0), pasta, itens, data, editores=[Constante(gabarito, padrao=codigos)])
            assert (registro.lotes, registro.descartados, registro.primeiras, registro.controles_total) == (2, 2, 0, 20), (codigos, data)
            assert registro.controles_certos <= 2 * (plano.limites.controles_minimo - 1) and len(_guarda(pasta, plano)) == 0


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
    assert (registro.mesmo_editor, registro.de_outra_rodada) == (0, 2)  # as primeiras são de outra rodada: não se sabe de que editor
    assert leitura.avaliar(plano.regua, guarda.de("cd", "1")).faixa == "estruturante"  # agora conta como topo


def test_na_materia_votada_a_marca_do_caixa_em_que_os_leitores_divergem_fica_nd(plano, gabarito, tmp_path: Path):
    """Dois leitores na mesma faixa com marcas do caixa opostas: na matéria votada, a leitura
    é confirmada (r = 2) com o custo "nd" e a anotação que a tira do voto fiscal (a regra da
    leitura inicial), e não com a marca do primeiro; na proposição que não foi a voto, valem
    os códigos do primeiro. Com a terceira leitura, a mesma regra entre os dois que ficaram
    na faixa que vale."""
    gasta, corta = "fiscal geral ajuste gasto neutro neutro", TOPO  # as duas de peso 60; caixa −1 e +1
    itens = _universo(_item(1, universos={leitura.VOTO}), _item(2), _item(3, universos={leitura.VOTO, leitura.PERFIL}))
    _semear(tmp_path, plano, {("cd", 2026, str(n)): _lida(gasta, porque="Cria despesa.") for n in (1, 2, 3)})
    ia = LeitorFalso(gabarito, respostas={"Ementa cd 1 de 2026.": corta, "Ementa cd 2 de 2026.": corta, "Ementa cd 3 de 2026.": [MEDIO, corta]})
    registro = ler.ler_universo(plano, tmp_path, itens, HOJE, agora=AGORA, editores=[ia])
    guarda = _guarda(tmp_path, plano)
    em_disputa = _lida("fiscal geral ajuste nd neutro neutro", porque=f"Cria despesa. {leitura.DISPUTA_DE_CONTAS}")
    assert guarda.de("cd", "1") == replace(em_disputa, r=leitura.DUAS) and leitura.contas_em_disputa(guarda.de("cd", "1"))
    assert guarda.de("cd", "2") == _lida(gasta, porque="Cria despesa.", r=leitura.DUAS)  # não foi a voto: os códigos do primeiro
    assert guarda.de("cd", "3") == replace(em_disputa, r=leitura.TRES)  # a terceira ficou com a primeira na faixa, com a marca oposta
    assert (registro.segundas, registro.confirmadas, registro.terceiras, registro.decididas) == (3, 2, 1, 1)
    assert leitura.avaliar(plano.regua, guarda.de("cd", "1")).marcas["contas"] is None


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


def test_sem_terceira_possivel_hoje_a_segunda_fica_guardada_e_a_terceira_sai_na_rodada_seguinte(plano, gabarito, tmp_path: Path):
    """As duas leituras divergem e a terceira não cabe (o teto de chamadas acabou; ou o lote
    da terceira foi descartado, ou veio sem a resposta): a leitura fica com r = 1, igual, e a
    segunda fica guardada (``a_decidir.json``). Na rodada seguinte não se pede outra segunda:
    a leitura nova é a terceira, e vale a regra dos três com as três leituras feitas."""
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
        # A segunda leitura, que divergiu, fica guardada, com quem a fez; e o registro do dia diz que uma espera a terceira.
        em_espera = leitura.ler_a_decidir(pasta / "leitura", plano.regua)
        assert em_espera == {("cd", "1"): leitura.ADecidir(_lida(MEDIO, porque="Lido no teste.", confianca=3), ("falso",))}, pasta.name
        assert pasta / "leitura" / leitura.A_DECIDIR in registro.gravados and _dias(pasta)[-1]["pendentes"] == {"primeiras": 0, "segundas": 1, "terceiras": 1}
        # No dia seguinte, uma leitura só, que é a terceira: fica com a primeira, e a leitura é decidida por dois de três (não "confirmada por dois").
        amanha = LeitorFalso(gabarito, respostas={"Ementa cd 1 de 2026.": TOPO_TAMBEM})
        registro = ler.ler_universo(plano, pasta, itens, "2026-11-16", editores=[amanha])
        assert registro.passos == [(1, ler.TERCEIRA, "falso")] and len(amanha.calls) == 1
        assert _guarda(pasta, plano).de("cd", "1") == replace(primeira, r=leitura.TRES)
        assert (registro.segundas, registro.confirmadas, registro.terceiras, registro.decididas, registro.instaveis, registro.pendentes_segundas) == (0, 0, 1, 1, 0, 0)
        assert leitura.ler_a_decidir(pasta / "leitura", plano.regua) == {} and (pasta / "leitura" / leitura.A_DECIDIR).is_file()  # o arquivo fica, vazio: quem publica não leva remoções
        assert [d["data"] for d in _dias(pasta)] == [HOJE, "2026-11-16"] and _dias(pasta)[-1]["pendentes"] == {"primeiras": 0, "segundas": 0, "terceiras": 0}
        # O dia em que só houve a terceira leitura é dia em que algo foi lido (a página diz qual foi o último).
        assert leitura.resumo_do_diario(pasta / "leitura")["periodo"]["leu_em"] == "2026-11-16"


def test_leituras_que_divergem_em_dias_seguidos_nao_saem_confirmadas(plano, gabarito, tmp_path: Path):
    """A primeira leitura (de outro dia) cai no topo. No dia 1 a segunda cai em outra faixa e a
    terceira não cabe; no dia 2 a leitura nova cai numa terceira faixa: são três leituras em
    três faixas, e a proposição fica instável (r = 0). Antes, a segunda que divergia era
    esquecida e pedia-se outra a cada dia, até uma concordar: no dia 3 a proposição saía
    "confirmada por dois leitores" (r = 2) com quatro leituras em três faixas."""
    itens = _universo(_item(1), _item(2))
    _semear(tmp_path, plano, {("cd", 2026, "1"): _lida(TOPO), ("cd", 2026, "2"): _lida(TOPO)})
    um = _com(plano, chamadas=1)
    dia1 = ler.ler_universo(um, tmp_path, itens, "2026-11-15", editores=[LeitorFalso(gabarito, "a", respostas={"Ementa cd 1 de 2026.": MEDIO, "Ementa cd 2 de 2026.": MEDIO})])
    assert (dia1.segundas, dia1.confirmadas, dia1.terceiras) == (2, 0, 0) and sorted(leitura.ler_a_decidir(tmp_path / "leitura", plano.regua)) == [("cd", "1"), ("cd", "2")]
    # Dia 2: a leitura nova da 1 cai numa terceira faixa (instável); a da 2 volta ao topo (duas de três: decidida, com os códigos da primeira).
    b = LeitorFalso(gabarito, "b", respostas={"Ementa cd 1 de 2026.": PONTUAL, "Ementa cd 2 de 2026.": TOPO_TAMBEM})
    dia2 = ler.ler_universo(um, tmp_path, itens, "2026-11-16", editores=[LeitorFalso(gabarito, "a", falha=LLMUnavailable("fora do ar")), b])
    guarda = _guarda(tmp_path, plano)
    assert (guarda.de("cd", "1").r, guarda.de("cd", "2")) == (leitura.INSTAVEL, _lida(TOPO, r=leitura.TRES))
    assert (dia2.segundas, dia2.confirmadas, dia2.terceiras, dia2.decididas, dia2.instaveis) == (0, 0, 2, 1, 1) and dia2.passos == [(1, ler.TERCEIRA, "b")]
    assert leitura.ler_a_decidir(tmp_path / "leitura", plano.regua) == {}
    # Dia 3: não há mais o que reler (a instável não volta à fila).
    c = LeitorFalso(gabarito, "c", respostas={"Ementa cd 1 de 2026.": TOPO})
    dia3 = ler.ler_universo(um, tmp_path, itens, "2026-11-17", editores=[c])
    assert c.calls == [] and (dia3.segundas, dia3.confirmadas) == (0, 0) and _guarda(tmp_path, plano).de("cd", "1").r == leitura.INSTAVEL


def test_a_terceira_de_outro_dia_evita_quem_fez_a_segunda(plano, gabarito, tmp_path: Path):
    """A segunda que espera a terceira guarda quem já leu a proposição na rodada dela: a
    terceira, em outro dia, vai a quem ainda não leu, se houver (a guarda das leituras não
    diz quem leu; o arquivo das que esperam diz)."""
    itens = _universo(_item(1))
    a, b, c = (LeitorFalso(gabarito, nome, respostas={"Ementa cd 1 de 2026.": codigos}) for nome, codigos in (("a", TOPO), ("b", MEDIO), ("c", MEDIO)))
    dia1 = ler.ler_universo(_com(plano, chamadas=2), tmp_path, itens, "2026-11-15", editores=[a, b, c])  # a primeira (a) e a segunda (b); a terceira não cabe
    assert dia1.passos == [(1, ler.PRIMEIRA, "a"), (2, ler.SEGUNDA, "b")]
    assert leitura.ler_a_decidir(tmp_path / "leitura", plano.regua)[("cd", "1")].editores == ("a", "b")
    dia2 = ler.ler_universo(plano, tmp_path, itens, "2026-11-16", editores=[a, b, c])
    assert dia2.passos == [(1, ler.TERCEIRA, "c")] and _guarda(tmp_path, plano).de("cd", "1") == _lida(MEDIO, porque="Lido no teste.", confianca=3, r=leitura.TRES)
    assert (dia2.terceiras, dia2.decididas, dia2.mesmo_editor, dia2.terceira_de_quem_leu) == (1, 1, 0, 0)
    # Com as duas IAs de ontem só, a terceira volta a uma delas, e o registro conta.
    _semear(tmp_path / "dois", plano, {("cd", 2026, "1"): _lida(TOPO)})
    leitura.gravar_a_decidir(tmp_path / "dois" / "leitura", plano.regua, {("cd", "1"): leitura.ADecidir(_lida(MEDIO), ("a", "b"))})
    dia2 = ler.ler_universo(plano, tmp_path / "dois", itens, "2026-11-16", editores=[a, b])
    assert [etapa for _n, etapa, _quem in dia2.passos] == [ler.TERCEIRA] and (dia2.terceiras, dia2.mesmo_editor, dia2.terceira_de_quem_leu) == (1, 0, 1)


def test_a_segunda_guardada_de_proposicao_fora_do_universo_de_hoje_fica_e_nao_entra_nas_pendentes(plano, gabarito, tmp_path: Path):
    """A proposição com a segunda guardada pode não estar no universo de hoje (saiu das contas,
    ou a fonte dela caiu). A segunda fica guardada (a proposição pode voltar), mas o registro
    não a conta: ``pendentes.terceiras`` é parte de ``pendentes.segundas`` ("dessas, as que
    esperam a terceira"), e contava todas as do arquivo (0 à espera da segunda, 1 delas à
    espera da terceira). Quando a proposição volta, a leitura dela é a terceira."""
    _semear(tmp_path, plano, {("cd", 2026, "1"): _lida(TOPO), ("cd", 2026, "2"): _lida(TOPO), ("cd", 2026, "3"): _lida(MINIMO)})
    guardadas = {("cd", n): leitura.ADecidir(_lida(MEDIO), ("a",)) for n in ("1", "2")}
    leitura.gravar_a_decidir(tmp_path / "leitura", plano.regua, guardadas)
    um = _com(plano, chamadas=0)  # nenhuma chamada cabe: só o que fica na fila
    sem_a_1 = ler.ler_universo(um, tmp_path, _universo(_item(2), _item(3)), HOJE, agora=AGORA, editores=[LeitorFalso(gabarito)])
    assert (sem_a_1.pendentes_primeiras, sem_a_1.pendentes_segundas, sem_a_1.pendentes_terceiras) == (0, 1, 1)  # só a 2: a 1 não está no universo de hoje
    sem_nenhuma = ler.ler_universo(plano, tmp_path, _universo(_item(3)), HOJE, agora=AGORA, editores=[LeitorFalso(gabarito)])
    assert sem_nenhuma.motivo == "nada a ler: a guarda está em dia" and _dias(tmp_path)[-1]["pendentes"] == {"primeiras": 0, "segundas": 0, "terceiras": 0}
    assert leitura.ler_a_decidir(tmp_path / "leitura", plano.regua) == guardadas  # nada foi esquecido
    volta = ler.ler_universo(plano, tmp_path, _universo(_item(1), _item(2), _item(3)), "2026-11-16", editores=[LeitorFalso(gabarito, "b", padrao=TOPO_TAMBEM)])
    assert [etapa for _n, etapa, _quem in volta.passos] == [ler.TERCEIRA] and (volta.terceiras, volta.decididas, volta.pendentes_segundas, volta.pendentes_terceiras) == (2, 2, 0, 0)


@pytest.mark.parametrize("estrago", ["ilegível", "de outra versão", "com linha ruim"])
def test_sem_ler_inteiro_o_arquivo_das_segundas_guardadas_a_rodada_nao_faz_segundas_nem_o_regrava(plano, gabarito, tmp_path: Path, estrago: str):
    """``a_decidir.json`` ilegível, de outra versão das instruções ou com uma linha fora do
    formato: a rodada avisa, faz as primeiras leituras, não faz segundas nem terceiras (a
    segunda que divergisse não teria onde ficar guardada, e a guardada que não foi lida seria
    pedida de novo) e deixa o arquivo como está. Consertado o arquivo, as segundas voltam."""
    _semear(tmp_path, plano, {("cd", 2026, "1"): _lida(TOPO), ("cd", 2026, "2"): _lida(TOPO)})
    arquivo = tmp_path / "leitura" / leitura.A_DECIDIR
    leitura.gravar_a_decidir(tmp_path / "leitura", plano.regua, {("cd", "1"): leitura.ADecidir(_lida(MEDIO), ("a",))})
    bom = json.loads(arquivo.read_text(encoding="utf-8"))
    texto = {
        "ilegível": "{isto não é json",
        "de outra versão": json.dumps({**bom, "regua": plano.regua.leitura.versao + 1}),
        "com linha ruim": json.dumps({**bom, "itens": {**bom["itens"], "cd|77": {"s": ["saude"], "ed": []}}}),
    }[estrago]
    arquivo.write_text(texto, encoding="utf-8")
    itens = _universo(_item(1), _item(2), _item(3))
    ia = LeitorFalso(gabarito, padrao=TOPO_TAMBEM)
    registro = ler.ler_universo(plano, tmp_path, itens, HOJE, agora=AGORA, editores=[ia])
    assert registro.passos == [(1, ler.PRIMEIRA, "falso")] and (registro.primeiras, registro.segundas, registro.terceiras, registro.pendentes_segundas) == (1, 0, 0, 3)
    assert arquivo.read_text(encoding="utf-8") == texto and arquivo not in registro.gravados
    assert [_guarda(tmp_path, plano).de("cd", n).r for n in ("1", "2", "3")] == [leitura.UMA] * 3
    assert [aviso for aviso in registro.avisos if leitura.A_DECIDIR in aviso and "não faz segundas leituras" in aviso]
    arquivo.unlink()
    depois = ler.ler_universo(plano, tmp_path, itens, "2026-11-16", editores=[ia])
    assert (depois.segundas, depois.confirmadas, depois.avisos) == (3, 3, [])


def test_a_segunda_guardada_sai_do_arquivo_quando_a_leitura_deixa_de_ser_de_um_leitor_so(plano, gabarito, tmp_path: Path):
    """A leitura da guarda foi confirmada ou ficou instável por outro caminho (importada por
    fora): a segunda guardada já não espera nada e sai do arquivo, mesmo numa rodada que não
    lê; a que ainda espera fica."""
    _semear(tmp_path, plano, {("cd", 2026, "1"): _lida(TOPO, r=leitura.DUAS), ("cd", 2026, "2"): _lida(TOPO, r=leitura.INSTAVEL), ("cd", 2026, "3"): _lida(TOPO)})
    leitura.gravar_a_decidir(tmp_path / "leitura", plano.regua, {("cd", n): leitura.ADecidir(_lida(MEDIO), ("a",)) for n in ("1", "2", "3")})
    registro = ler.ler_universo(plano, tmp_path, _universo(_item(1), _item(2), _item(3)), HOJE, agora=AGORA, editores=[])
    assert registro.motivo.startswith("sem IA") and tmp_path / "leitura" / leitura.A_DECIDIR in registro.gravados
    assert sorted(leitura.ler_a_decidir(tmp_path / "leitura", plano.regua)) == [("cd", "3")]
    assert (registro.pendentes_segundas, registro.pendentes_terceiras) == (1, 1)


def test_a_primeira_que_mudou_e_cai_na_faixa_da_segunda_guardada_sai_confirmada(plano, gabarito, tmp_path: Path):
    """A primeira leitura mudou depois de a segunda ser guardada (importada por fora) e as duas
    agora caem na mesma faixa: a leitura do dia não muda nada, a proposição sai confirmada por
    dois (r = 2) com os códigos da primeira, e o registro conta uma confirmada (não uma
    instável nem uma decidida pela terceira)."""
    _semear(tmp_path, plano, {("cd", 2026, "1"): _lida(MEDIO, porque="Importada.")})
    leitura.gravar_a_decidir(tmp_path / "leitura", plano.regua, {("cd", "1"): leitura.ADecidir(_lida(MEDIO, porque="A segunda."), ("a",))})
    registro = ler.ler_universo(plano, tmp_path, _universo(_item(1, universos={leitura.VOTO})), HOJE, agora=AGORA, editores=[LeitorFalso(gabarito, "b", padrao=MINIMO)])
    assert _guarda(tmp_path, plano).de("cd", "1") == _lida(MEDIO, porque="Importada.", r=leitura.DUAS)
    assert (registro.segundas, registro.terceiras, registro.confirmadas, registro.decididas, registro.instaveis) == (0, 1, 1, 0, 0)
    assert leitura.ler_a_decidir(tmp_path / "leitura", plano.regua) == {}


def test_o_arquivo_das_segundas_que_esperam_a_terceira(plano, tmp_path: Path):
    """``a_decidir.json``: uma proposição por linha; ausente, ilegível, de outra versão das
    instruções ou com linha fora do formato não derruba nada (a linha ruim fica de fora); e
    só é gravado quando muda."""
    pasta = tmp_path / "leitura"
    assert leitura.ler_a_decidir(pasta, plano.regua) == {} and not leitura.gravar_a_decidir(pasta, plano.regua, {}) and not pasta.exists()
    espera = {("sf", "PL-12-2025"): leitura.ADecidir(_lida(MEDIO, porque="Segunda."), ("b",)), ("cd", "30"): leitura.ADecidir(_lida(TOPO), ()), ("cd", "4"): leitura.ADecidir(_lida(MINIMO), ("a", "b"))}
    assert leitura.gravar_a_decidir(pasta, plano.regua, espera) and not leitura.gravar_a_decidir(pasta, plano.regua, espera)
    texto = (pasta / leitura.A_DECIDIR).read_text(encoding="utf-8")
    assert texto.splitlines()[0] == f'{{"v":1,"regua":{plano.regua.leitura.versao},"itens":{{' and [linha.split(":")[0] for linha in texto.splitlines()[1:-1]] == ['"cd|4"', '"cd|30"', '"sf|PL-12-2025"']
    assert leitura.ler_a_decidir(pasta, plano.regua) == espera
    assert _guarda(tmp_path, plano).ignorados == [] and len(_guarda(tmp_path, plano)) == 0  # não é arquivo da guarda
    dados_ = json.loads(texto)
    dados_["itens"].update({"cd|9": {"s": ["saude"], "ed": []}, "xx|1": dados_["itens"]["cd|4"], "cd|abc": dados_["itens"]["cd|4"], "cd|7": "isto não é uma leitura"})
    (pasta / leitura.A_DECIDIR).write_text(json.dumps(dados_), encoding="utf-8")
    assert leitura.ler_a_decidir(pasta, plano.regua) == espera
    (pasta / leitura.A_DECIDIR).write_text(json.dumps({**dados_, "regua": plano.regua.leitura.versao + 1}), encoding="utf-8")
    assert leitura.ler_a_decidir(pasta, plano.regua) == {}
    (pasta / leitura.A_DECIDIR).write_text("{isto não é json", encoding="utf-8")
    assert leitura.ler_a_decidir(pasta, plano.regua) == {}
    assert leitura.gravar_a_decidir(pasta, plano.regua, {}) and json.loads((pasta / leitura.A_DECIDIR).read_text(encoding="utf-8"))["itens"] == {}


def test_a_segunda_leitura_prefere_outro_editor_da_cadeia(plano, gabarito, tmp_path: Path):
    """A proposição lida pela primeira vez nesta rodada (pelo primeiro editor) tem a segunda
    leitura feita pelo editor seguinte; a terceira, por quem ainda não a leu, se houver. Da
    que já estava na guarda não se sabe quem fez a primeira (a guarda não diz): supõe-se o
    primeiro editor da cadeia, que a segunda evita do mesmo jeito, e o registro conta essas
    segundas à parte. Com um editor só, ele faz tudo, e o registro diz quantas foram."""
    itens = _universo(_item(1, universos={leitura.VOTO}), _item(2, universos={leitura.VOTO}), _item(3, universos={leitura.VOTO}))
    _semear(tmp_path / "tres", plano, {("cd", 2026, "3"): _lida(MINIMO)})
    a = LeitorFalso(gabarito, "a", respostas={"Ementa cd 1 de 2026.": TOPO})
    b = LeitorFalso(gabarito, "b", respostas={"Ementa cd 1 de 2026.": MEDIO})
    c = LeitorFalso(gabarito, "c", respostas={"Ementa cd 1 de 2026.": MEDIO})
    registro = ler.ler_universo(plano, tmp_path / "tres", itens, HOJE, agora=AGORA, editores=[a, b, c])
    assert registro.passos == [(1, ler.PRIMEIRA, "a"), (2, ler.SEGUNDA, "b"), (3, ler.TERCEIRA, "c")]
    assert [x["label"] for x in a.calls] == ["leitura 1"] and sorted(a.ementas(0)) == ["Ementa cd 1 de 2026.", "Ementa cd 2 de 2026."]  # só as primeiras
    # As segundas num lote só, para o "b": as duas que o "a" acabou de ler e a que já estava na guarda.
    assert [x["label"] for x in b.calls] == ["leitura 2"] and sorted(b.ementas(0)) == ["Ementa cd 1 de 2026.", "Ementa cd 2 de 2026.", "Ementa cd 3 de 2026."]
    assert [x["label"] for x in c.calls] == ["leitura 3"] and c.ementas(0) == ["Ementa cd 1 de 2026."]
    guarda = _guarda(tmp_path / "tres", plano)
    assert (guarda.de("cd", "1").r, guarda.de("cd", "1").codigos) == (leitura.TRES, tuple(MEDIO.split()))  # a e b divergiram; c ficou com b
    assert guarda.de("cd", "2").r == guarda.de("cd", "3").r == leitura.DUAS
    assert (registro.primeiras, registro.segundas, registro.terceiras, registro.mesmo_editor, registro.de_outra_rodada, registro.provedor) == (2, 3, 1, 0, 1, "a → b → c")

    so = LeitorFalso(gabarito, "so")
    registro = ler.ler_universo(plano, tmp_path / "um", itens, HOJE, agora=AGORA, editores=[so])
    assert registro.passos == [(1, ler.PRIMEIRA, "so"), (2, ler.SEGUNDA, "so")]
    assert (registro.primeiras, registro.segundas, registro.confirmadas, registro.mesmo_editor, registro.de_outra_rodada, registro.provedor) == (3, 3, 3, 3, 0, "so")
    assert (_dias(tmp_path / "um")[0]["mesmo_editor"], _dias(tmp_path / "um")[0]["de_outra_rodada"]) == (3, 0)

    # O editor que estoura o limite sai da cadeia: quem sobra faz as duas leituras.
    esgotado = LeitorFalso(gabarito, "esgotado", falha=LLMUnavailable("cota do dia", exhausted=True))
    reserva = LeitorFalso(gabarito, "reserva")
    registro = ler.ler_universo(plano, tmp_path / "esgotado", itens, HOJE, agora=AGORA, editores=[esgotado, reserva])
    assert len(esgotado.calls) == 1 and esgotado.exhausted and len(reserva.calls) == 2 and registro.passos == [(1, ler.PRIMEIRA, "reserva"), (2, ler.SEGUNDA, "reserva")]
    assert (registro.primeiras, registro.segundas, registro.mesmo_editor, registro.falhas, registro.provedor) == (3, 3, 3, 0, "reserva")


def test_a_segunda_leitura_de_primeira_de_outro_dia_evita_o_primeiro_editor_da_cadeia(plano, gabarito, tmp_path: Path):
    """No primeiro dia só cabe a primeira leitura (teto de uma chamada), feita pelo primeiro
    editor. No dia seguinte a rodada não tem como saber quem a fez (a guarda não guarda o
    editor): a segunda leitura evita o primeiro da cadeia e vai ao seguinte, em vez de voltar
    a quem leu; o registro não as conta como "mesmo editor" (não sabe) e as conta à parte."""
    itens = _universo(*[_item(n, universos={leitura.VOTO}) for n in (1, 2, 3)])
    a, b = LeitorFalso(gabarito, "a", padrao=TOPO), LeitorFalso(gabarito, "b", padrao=TOPO_TAMBEM)
    registro = ler.ler_universo(_com(plano, chamadas=1), tmp_path, itens, HOJE, agora=AGORA, editores=[a, b])
    assert registro.passos == [(1, ler.PRIMEIRA, "a")] and (registro.primeiras, registro.segundas, registro.pendentes_segundas) == (3, 0, 3)
    registro = ler.ler_universo(plano, tmp_path, itens, "2026-11-16", editores=[a, b])
    assert registro.passos == [(1, ler.SEGUNDA, "b")] and (len(a.calls), len(b.calls)) == (1, 1)  # nenhuma chamada a mais a quem fez a primeira
    assert (registro.segundas, registro.confirmadas, registro.mesmo_editor, registro.de_outra_rodada, registro.provedor) == (3, 3, 0, 3, "b")
    assert all(_guarda(tmp_path, plano).de("cd", str(n)).r == leitura.DUAS for n in (1, 2, 3))
    assert [(d["data"], d["segundas"], d["mesmo_editor"], d["de_outra_rodada"]) for d in _dias(tmp_path)] == [(HOJE, 0, 0, 0), ("2026-11-16", 3, 0, 3)]
    assert leitura.resumo_do_diario(tmp_path / "leitura")["periodo"]["de_outra_rodada"] == 3  # e vai somado ao resumo que a atualização leva a meta.json
    # Com um editor só na cadeia não há quem evitar: ele relê, e o registro continua sem saber dizer se foi o mesmo.
    _semear(tmp_path / "um", plano, {("cd", 2026, "1"): _lida(TOPO)})
    so = LeitorFalso(gabarito, "so", padrao=TOPO)
    registro = ler.ler_universo(plano, tmp_path / "um", _universo(_item(1)), HOJE, editores=[so])
    assert registro.passos == [(1, ler.SEGUNDA, "so")] and (registro.segundas, registro.mesmo_editor, registro.de_outra_rodada) == (1, 0, 1)
    # Quando a segunda diverge, a terceira evita os dois: o suposto da primeira e o da segunda.
    _semear(tmp_path / "tres", plano, {("cd", 2026, "1"): _lida(TOPO)})
    a, b, c = LeitorFalso(gabarito, "a", padrao=TOPO), LeitorFalso(gabarito, "b", padrao=MEDIO), LeitorFalso(gabarito, "c", padrao=MEDIO)
    registro = ler.ler_universo(plano, tmp_path / "tres", _universo(_item(1)), HOJE, editores=[a, b, c])
    assert registro.passos == [(1, ler.SEGUNDA, "b"), (2, ler.TERCEIRA, "c")] and len(a.calls) == 0
    assert (registro.terceiras, registro.decididas, registro.mesmo_editor, registro.terceira_de_quem_leu, registro.de_outra_rodada) == (1, 1, 0, 0, 1)


def test_com_dois_editores_a_terceira_leitura_volta_a_um_deles_e_o_registro_conta(plano, gabarito, tmp_path: Path):
    """Cadeia de dois editores: um lê, o outro diverge, e não há um terceiro para decidir. A
    terceira leitura volta a quem já leu (se ele repetir a própria resposta, "duas de três" é
    ele duas vezes): o registro conta essas terceiras à parte, em ``terceira_de_quem_leu``.
    ``mesmo_editor`` continua contando só as segundas feitas por quem fez a primeira: é o que
    a página escreve ("N segundas, M delas pelo mesmo editor que fez a primeira"), e aqui as
    segundas foram do outro editor."""
    itens = _universo(*[_item(n, universos={leitura.VOTO}) for n in (1, 2, 3)])
    a, b = LeitorFalso(gabarito, "a", padrao=TOPO), LeitorFalso(gabarito, "b", padrao=MEDIO)
    registro = ler.ler_universo(plano, tmp_path, itens, HOJE, agora=AGORA, editores=[a, b])
    assert registro.passos == [(1, ler.PRIMEIRA, "a"), (2, ler.SEGUNDA, "b"), (3, ler.TERCEIRA, "a")]
    assert (registro.segundas, registro.confirmadas, registro.terceiras, registro.decididas, registro.mesmo_editor, registro.terceira_de_quem_leu, registro.de_outra_rodada) == (3, 0, 3, 3, 0, 3, 0)
    guarda = _guarda(tmp_path, plano)
    assert all((guarda.de("cd", str(n)).r, guarda.de("cd", str(n)).codigos) == (leitura.TRES, tuple(TOPO.split())) for n in (1, 2, 3))
    assert (_dias(tmp_path)[0]["mesmo_editor"], _dias(tmp_path)[0]["terceira_de_quem_leu"]) == (0, 3)
    # Com um editor só, ele faz as três: as segundas contam em mesmo_editor (nunca mais que as segundas) e as terceiras, à parte.
    so = LeitorFalso(gabarito, "so", respostas={f"Ementa cd {n} de 2026.": [TOPO, MEDIO, TOPO] for n in (1, 2, 3)})
    registro = ler.ler_universo(plano, tmp_path / "um", itens, HOJE, agora=AGORA, editores=[so])
    assert (registro.segundas, registro.terceiras, registro.mesmo_editor, registro.terceira_de_quem_leu) == (3, 3, 3, 3)
    periodo = leitura.resumo_do_diario(tmp_path / "um" / "leitura")["periodo"]  # o que a atualização leva a meta.json
    assert (periodo["segundas"], periodo["mesmo_editor"], periodo["terceiras"], periodo["terceira_de_quem_leu"]) == (3, 3, 3, 3) and periodo["mesmo_editor"] <= periodo["segundas"]


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
    assert _dias(tmp_path)[0]["motivo"] == registro.motivo and _dias(tmp_path)[0]["pendentes"] == {"primeiras": 4, "segundas": 0, "terceiras": 0}


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
    assert (dia["primeiras"], dia["lotes"], dia["pendentes"]) == (2, 1, {"primeiras": 2, "segundas": 0, "terceiras": 0})


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
    assert _dias(tmp_path)[0]["motivo"].startswith("sem IA: nada lido") and _dias(tmp_path)[0]["pendentes"] == {"primeiras": 2, "segundas": 0, "terceiras": 0}
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
        "controles": [30, 30], "confirmadas": 2, "decididas": 0, "instaveis": 0, "invalidas": 0, "mesmo_editor": 2, "terceira_de_quem_leu": 0, "de_outra_rodada": 0, "chamadas": 3,
        "tokens": {"entrada": 3000, "saida": 600}, "pendentes": {"primeiras": 0, "segundas": 0, "terceiras": 0}, "provedor": "falso", "modelo": "modelo-falso", "motivo": "",
    }  # fmt: skip
    assert dias[-1] == ler.linha(registro) and json.loads(linhas[-2]) == dias[-1]

    resumo = leitura.resumo_do_diario(pasta)
    assert {k: v for k, v in resumo.items() if k != "periodo"} == dias[-1]
    assert resumo["periodo"] == {
        "dias": 60, "desde": antigos[10]["data"], "leu_em": HOJE, "rodadas": 60, "primeiras": 59 + 5, "segundas": 2, "terceiras": 0, "lotes": 59 + 3, "descartados": 0,
        "falhas": 0, "confirmadas": 2, "decididas": 0, "instaveis": 0, "invalidas": 0, "mesmo_editor": 2, "terceira_de_quem_leu": 0, "de_outra_rodada": 0, "chamadas": 3, "controles": [59 * 10 + 30, 59 * 10 + 30],
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
    assert (junta["quando"], junta["motivo"], junta["pendentes"]) == ("t2", "", {"primeiras": 0, "segundas": 0, "terceiras": 0}) and "avisos" not in junta
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


def test_o_arquivo_da_guarda_com_linha_que_nao_valida_nao_e_regravado(plano, gabarito, tmp_path: Path):
    """Um código renomeado ou retirado da régua sem subir a versão das instruções (ou uma
    linha editada à mão) deixa na guarda linhas que não validam. Regravar o arquivo as
    apagaria, e com elas leituras confirmadas: a rodada não o regrava, leia ou não leia
    alguma coisa, deixa as proposições daquele ano e Casa de fora e avisa no registro. Os
    outros arquivos seguem como sempre, e a atualização continua usando as linhas válidas."""
    pasta = tmp_path / "leitura"
    pasta.mkdir()
    da_camara = leitura.serializar(plano.regua, "cd", 2025, {"1": _lida(TOPO, r=2), "2": _lida(TOPO, r=2), "3": _lida(PONTUAL, r=3), "4": _lida(TOPO)})
    da_camara = da_camara.replace('"2":["fiscal","geral","ajuste","corta"', '"2":["fiscal","geral","ajuste","codigo_antigo"')  # um código que a régua não tem mais
    do_senado = leitura.serializar(plano.regua, "sf", 2025, {"PL-7-2025": _lida(TOPO, r=2), "PL-8-2025": _lida(MINIMO, r=2)}).replace('"PL-8-2025":', '"oito":')  # uma chave fora do formato
    em_ordem = leitura.serializar(plano.regua, "cd", 2024, {"90": _lida(MINIMO, r=2)})
    textos = {"cd-2025.json": da_camara, "sf-2025.json": do_senado, "cd-2024.json": em_ordem}
    assert "codigo_antigo" in da_camara and '"oito":' in do_senado
    for nome, texto in textos.items():
        (pasta / nome).write_text(texto, encoding="utf-8")
    idades = {nome: (pasta / nome).stat().st_mtime_ns for nome in textos}

    def intactos() -> bool:
        return all((pasta / nome).read_text(encoding="utf-8") == texto and (pasta / nome).stat().st_mtime_ns == idades[nome] for nome, texto in textos.items())

    # A 4 espera a segunda leitura (peso 60) e a 5 não tem leitura, as duas no arquivo que não se regrava; a 6 e a 91 são de outros arquivos.
    itens = _universo(*[_item(n, ano=2025) for n in range(1, 6)], _item(7, casa="sf", ano=2025), _item(9, casa="sf", ano=2025), _item(6), _item(91, ano=2024))
    registro = ler.ler_universo(plano, tmp_path, itens, HOJE, agora=AGORA, editores=[])  # uma rodada que não lê nada
    assert registro.motivo == "sem IA: nada lido" and registro.gravados == [] and intactos()
    (aviso,) = [a for a in registro.avisos if "não valida" in a]
    assert aviso.endswith("ficam de fora: cd-2025.json (1 linha), sf-2025.json (1 linha)") and "não os regrava" in aviso
    assert (registro.faltavam, registro.esperavam_segunda, registro.pendentes_primeiras, registro.pendentes_segundas) == (2, 0, 2, 0)  # só a 6 e a 91
    assert _dias(tmp_path)[0]["avisos"] == [aviso]  # o aviso vai ao registro do dia
    # Com IA: lê só o que é dos outros arquivos (nem a primeira leitura da 5, nem a segunda da 4), e os dois arquivos ficam como estavam.
    ia = LeitorFalso(gabarito)
    registro = ler.ler_universo(plano, tmp_path, itens, HOJE, agora=AGORA, editores=[ia])
    assert len(ia.calls) == 1 and sorted(ia.ementas(0)) == ["Ementa cd 6 de 2026.", "Ementa cd 91 de 2024."]
    assert (registro.primeiras, registro.segundas, registro.pendentes_primeiras, registro.pendentes_segundas) == (2, 0, 0, 0) and aviso in registro.avisos
    assert sorted(p.name for p in registro.gravados) == ["cd-2024.json", "cd-2026.json"]
    assert (pasta / "cd-2025.json").read_text(encoding="utf-8") == da_camara and (pasta / "sf-2025.json").read_text(encoding="utf-8") == do_senado
    assert all((pasta / nome).stat().st_mtime_ns == idades[nome] for nome in ("cd-2025.json", "sf-2025.json"))
    assert (pasta / "cd-2024.json").read_text(encoding="utf-8").splitlines()[1:3] == [em_ordem.splitlines()[1] + ",", '"91":["outro","grupo","conduta","neutro","neutro","neutro",0,3,"","Lido no teste.",1]']
    # Quem lê a guarda para as contas continua com as linhas válidas desses arquivos, e sabe quais têm linha inválida.
    guarda = _guarda(tmp_path, plano)
    assert (guarda.invalidas, guarda.com_invalidas, guarda.ignorados) == (2, {"cd-2025.json": 1, "sf-2025.json": 1}, [])
    assert guarda.de("cd", "1").r == leitura.DUAS and guarda.de("cd", "2") is None and guarda.de("sf", "PL-7-2025").r == leitura.DUAS and len(guarda) == 7
    guarda.deixar_de_fora("cd-2025.json")
    assert guarda.de("cd", "1") is None and guarda.ano_de("cd", "3") is None and len(guarda) == 4
    guarda.deixar_de_fora("sf-2025.json")
    guarda.deixar_de_fora("não é arquivo da guarda")
    assert len(guarda) == 3 and guarda.gravar(pasta) == []  # sem os dois na memória, gravar não toca neles


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
    pasta = publicado / "data" / "congresso" / "leitura"
    with caplog.at_level("INFO"):
        assert main(_comando(publicado, "--max", "0")) == 0 and not pasta.exists()  # --max 0 só diz o que leria: não grava nada
        assert "9 proposição(ões) sem leitura" in caplog.text and "--max 0" in caplog.text
        caplog.clear()
        assert main(_comando(publicado)) == 0
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
    assert dia["pendentes"] == {"primeiras": 0, "segundas": 0, "terceiras": 0} and dia["motivo"] == ""

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
FLUXOS = ROOT / ".github" / "workflows"
SE_LER = "steps.decidir.outputs.ler == 'true'"  # a condição dos passos que vêm depois da decisão
A_GUARDA = "data/congresso/leitura"
HOJE_NO_FLUXO, ONTEM_NO_FLUXO = "2026-11-17", "2026-11-16"  # o `date` de mentira dos passos responde sempre este dia
COMMIT_DA_LEITURA = "📚 Leitura dos projetos de lei em 17/11/2026"
precisa_de_git = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="sem git ou sem bash")


def _fluxo(nome: str) -> dict[str, Any]:
    yml = yaml.safe_load((FLUXOS / nome).read_text(encoding="utf-8"))
    if True in yml:  # o YAML 1.1 lê a chave "on" como verdadeiro
        yml["on"] = yml.pop(True)
    return yml


def _passo(job: dict[str, Any], nome: str) -> dict[str, Any]:
    (passo,) = [p for p in job["steps"] if p.get("name") == nome]
    return passo


def _da_leitura() -> dict[str, Any]:
    """O único job de leitura.yml."""
    (job,) = _fluxo("leitura.yml")["jobs"].values()
    return job


@pytest.mark.parametrize("fluxo", ["daily.yml", "congresso.yml"])
def test_a_edicao_diaria_e_o_fluxo_manual_do_congresso_nao_leem_os_projetos(fluxo: str):
    """A leitura não mora na "Edição diária" nem no fluxo manual do Congresso: o Instagram
    dispara quando a "Edição diária" termina e não pode esperar a leitura, e o passo que
    publica a leitura não pode conviver com os que refazem o commit com a cópia local de
    ``data/``. Nenhum passo deles roda ``congresso ler`` nem publica a guarda à parte."""
    texto = (FLUXOS / fluxo).read_text(encoding="utf-8")
    assert "congresso ler" not in texto and "QIJ_NO_LEITURA" not in texto and "Leitura dos projetos" not in texto
    nomes = [str(p.get("name") or "") for job in _fluxo(fluxo)["jobs"].values() for p in job["steps"]]
    assert "Atualizar o Congresso" in nomes and "Publicar (commit e push)" in nomes
    assert not [n for n in nomes if "projetos" in n.lower() or "leitura" in n.lower()]


def test_a_leitura_tem_um_fluxo_proprio_que_roda_depois_da_edicao_diaria():
    """``leitura.yml``: dispara quando a "Edição diária" termina (como o Instagram) ou à mão,
    com um teto opcional; só segue se a execução que disparou deu certo; tem fila própria
    (não a da edição) e prazo curto; baixa a ponta da branch e, antes de qualquer outra coisa,
    decide se há o que ler: todo passo seguinte depende dessa decisão. Instala como a edição,
    só restaura o cache do Congresso (com a chave da edição) e lê com os mesmos segredos de
    IA e as mesmas variáveis de modelo do passo "Gerar a edição"."""
    yml, diario = _fluxo("leitura.yml"), _fluxo("daily.yml")
    assert yml["name"] == "Leitura dos projetos" and diario["name"] == "Edição diária"
    assert yml["on"]["workflow_run"] == {"workflows": ["Edição diária"], "types": ["completed"]} == _fluxo("instagram.yml")["on"]["workflow_run"]
    assert set(yml["on"]) == {"workflow_run", "workflow_dispatch"}  # sem agendamento próprio nem push
    assert list(yml["on"]["workflow_dispatch"]["inputs"]) == ["max"] and yml["on"]["workflow_dispatch"]["inputs"]["max"]["default"] == ""
    assert yml["permissions"] == {"contents": "write"}
    job = _da_leitura()
    assert job["if"] == "github.event_name != 'workflow_run' || github.event.workflow_run.conclusion == 'success'"
    # A fila é só deste fluxo, e fica no job: a execução pulada pelo "if" não entra nela.
    assert "concurrency" not in yml and job["concurrency"]["cancel-in-progress"] is False
    assert job["concurrency"]["group"].startswith("leitura-") and "daily-edition" not in job["concurrency"]["group"]
    assert job["concurrency"]["group"] not in {diario["concurrency"]["group"], _fluxo("congresso.yml")["concurrency"]["group"], "instagram"}
    passos = job["steps"]
    nomes = [p.get("name") for p in passos]
    assert nomes == [
        "Baixar o repositório", "Verificar se há o que ler", "Configurar Python", "Instalar dependências", "Dia do cache do Congresso",
        "Restaurar o cache do que foi baixado do Congresso", "Ler os projetos novos", "Publicar a leitura (commit e push)",
    ]  # fmt: skip
    da_edicao = diario["jobs"]["edition"]
    baixar, decidir = passos[:2]
    assert baixar == {"name": "Baixar o repositório", **{k: _passo(da_edicao, "Baixar o repositório")[k] for k in ("uses", "with")}} and baixar["with"] == {"ref": "${{ github.ref_name }}"}
    assert decidir["id"] == "decidir" and "if" not in decidir and "continue-on-error" not in decidir
    assert decidir["env"] == {"EVENT": "${{ github.event_name }}", "QIJ_NO_LEITURA": "${{ vars.QIJ_NO_LEITURA }}", "QIJ_NO_CONGRESSO": "${{ vars.QIJ_NO_CONGRESSO }}", "INPUT_MAX": "${{ inputs.max }}"}
    assert all(p.get("if") == SE_LER for p in passos[2:])  # nada roda sem a decisão
    assert all("${{" not in p.get("run", "") for p in passos)  # nenhuma entrada é interpolada direto num script
    # Python e dependências como na edição.
    for nome in ("Configurar Python", "Instalar dependências", "Dia do cache do Congresso"):
        assert {k: v for k, v in _passo(job, nome).items() if k != "if"} == _passo(da_edicao, nome), nome
    # O cache da edição, só restaurado: a mesma versão da ação, a mesma chave, e nada grava de volta.
    cache, da_edicao_cache = _passo(job, "Restaurar o cache do que foi baixado do Congresso"), _passo(da_edicao, "Cache do que foi baixado do Congresso")
    assert cache["uses"] == da_edicao_cache["uses"].replace("actions/cache@", "actions/cache/restore@") != da_edicao_cache["uses"]
    assert cache["with"] == da_edicao_cache["with"] and cache["with"]["path"] == "${{ runner.temp }}/congresso" and cache["continue-on-error"] is True
    assert [p["uses"] for p in passos if "actions/cache" in p.get("uses", "")] == [cache["uses"]]
    # A leitura: acessória, com prazo próprio dentro do prazo do job, e com as IAs da edição.
    ler_ = _passo(job, "Ler os projetos novos")
    assert ler_["continue-on-error"] is True and 5 <= ler_["timeout-minutes"] < job["timeout-minutes"] <= 30
    assert 'args=(--dir . --cache "$RUNNER_TEMP/congresso")' in ler_["run"] and 'python -m qijournal congresso ler "${args[@]}"' in ler_["run"]
    # Sem o cache restaurado, não lê (ver test_sem_o_cache_do_congresso_o_passo_nao_le): a pasta conferida é a que a edição guarda.
    assert ler_["run"].index('find "$RUNNER_TEMP/congresso" -type f') < ler_["run"].index("python -m qijournal congresso ler") and cache["with"]["path"].endswith("/congresso")
    assert ler_["env"]["INPUT_MAX"] == "${{ inputs.max }}" and 'if [ -n "$INPUT_MAX" ]; then args+=(--max "$INPUT_MAX"); fi' in ler_["run"]
    edicao = _passo(da_edicao, "Gerar a edição")
    da_ia = {k: v for k, v in edicao["env"].items() if k in SEGREDOS_DE_IA or (k.startswith("QIJ_") and k.endswith("MODEL"))}
    assert set(SEGREDOS_DE_IA) < set(da_ia) and len(da_ia) > len(SEGREDOS_DE_IA) and {k: ler_["env"].get(k) for k in da_ia} == da_ia
    for segredo in SEGREDOS_DE_IA:
        assert ler_["env"][segredo] == f"${{{{ secrets.{segredo} }}}}"
    assert {k: ler_["env"][k] for k in ("QIJ_BRAND", "TZ", "PYTHONUNBUFFERED")} == {k: edicao["env"][k] for k in ("QIJ_BRAND", "TZ", "PYTHONUNBUFFERED")}
    assert ler_["env"]["QIJ_NO_LEITURA"] == "${{ vars.QIJ_NO_LEITURA }}" and ler_["env"]["QIJ_NO_CONGRESSO"] == "${{ vars.QIJ_NO_CONGRESSO }}"


def test_o_passo_que_publica_a_leitura_so_leva_a_guarda_e_nunca_refaz_o_commit_com_a_copia_local():
    """O texto do passo "Publicar a leitura": o único ``git add`` é o da pasta da guarda, sem
    remoções; o commit leva só o que ele pôs; não há ``git reset`` (nem outro comando que
    troque o que o remoto tem pelo que está na cópia local); push recusado: o commit é
    reaplicado sobre o remoto e, se não der, o passo desiste e avisa."""
    publicar = _passo(_da_leitura(), "Publicar a leitura (commit e push)")
    script = publicar["run"]
    assert "continue-on-error" not in publicar and f"pasta={A_GUARDA}\n" in script
    assert re.findall(r"git add\b[^\n]*", script) == ['git add --ignore-removal -- "$pasta"']
    assert re.findall(r"git commit\b[^\n]*", script) == ['git commit --quiet -m "📚 Leitura dos projetos de lei em $(date +%d/%m/%Y)"']
    for proibido in ("git reset", "git checkout", "git restore", "git rm", "git clean", "git stash", "git merge", "git pull", "--force", "-f origin", "commit -a", "--all", "add -A", "add -u", "add ."):
        assert proibido not in script, proibido
    assert re.findall(r"git push\b[^\n;]*", script) == ['git push origin "HEAD:${GITHUB_REF_NAME}"']
    assert re.findall(r"git (?:fetch|rebase)\b[^\n;]*", script) == ['git fetch --quiet origin "${GITHUB_REF_NAME}"', "git rebase --quiet --autostash FETCH_HEAD", "git rebase --abort || true"]
    assert script.count("::warning::") == 2 and "exit 1" not in script


def _ambiente_do_passo(tmp: Path, **extra: str) -> dict[str, str]:
    """O ambiente em que os passos rodam nos testes: só o que o passo recebe, um git sem a
    configuração da máquina e ``date`` e ``sleep`` de mentira (hoje é sempre 17/11/2026, e
    ninguém espera)."""
    comandos = tmp / "bin"
    if not comandos.is_dir():
        comandos.mkdir(parents=True)
        (comandos / "date").write_text(f'#!/bin/sh\ncase "$1" in +%F) echo {HOJE_NO_FLUXO} ;; +%d/%m/%Y) echo 17/11/2026 ;; *) exit 9 ;; esac\n', encoding="utf-8")
        (comandos / "sleep").write_text("#!/bin/sh\n", encoding="utf-8")
        for comando in comandos.iterdir():
            comando.chmod(0o755)
        (tmp / "gitconfig").write_text("[user]\n\tname = Outro\n\temail = outro@example.invalid\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    return {
        "PATH": f"{comandos}{os.pathsep}{os.environ['PATH']}", "HOME": str(tmp), "GIT_CONFIG_GLOBAL": str(tmp / "gitconfig"), "GIT_CONFIG_SYSTEM": os.devnull,
        "GITHUB_OUTPUT": str(tmp / "saida"), "GITHUB_REF_NAME": "main", "TZ": "America/Sao_Paulo", **extra,
    }  # fmt: skip


def _rodar(script: str, onde: Path, tmp: Path, **ambiente: str) -> subprocess.CompletedProcess[str]:
    """Roda o script de um passo como o GitHub o roda (``bash -e``), em ``onde``."""
    (tmp / "passo.sh").write_text(script, encoding="utf-8")
    (tmp / "saida").write_text("", encoding="utf-8")
    return subprocess.run(["bash", "-e", str(tmp / "passo.sh")], cwd=onde, env=_ambiente_do_passo(tmp, **ambiente), capture_output=True, text=True, timeout=120)


@precisa_de_git
@pytest.mark.parametrize(
    ("evento", "edicao", "dias", "ambiente", "ler_"),
    [
        ("workflow_run", HOJE_NO_FLUXO, [ONTEM_NO_FLUXO], {}, "true"),  # a edição de hoje saiu e o registro não tem hoje
        ("workflow_run", HOJE_NO_FLUXO, None, {}, "true"),  # ainda sem registro (a primeira leitura)
        ("workflow_run", HOJE_NO_FLUXO, "{isto não é json", {}, "true"),  # registro ilegível: a rodada começa um novo
        ("workflow_run", HOJE_NO_FLUXO, [ONTEM_NO_FLUXO, HOJE_NO_FLUXO], {}, "false"),  # a leitura de hoje já está no registro
        ("workflow_run", ONTEM_NO_FLUXO, [], {}, "false"),  # a edição de hoje ainda não saiu
        ("workflow_run", None, [], {}, "false"),  # sem manifesto da edição
        ("workflow_dispatch", ONTEM_NO_FLUXO, [HOJE_NO_FLUXO], {}, "true"),  # à mão, sempre lê
        ("workflow_dispatch", HOJE_NO_FLUXO, [HOJE_NO_FLUXO], {"INPUT_MAX": "0"}, "true"),
        ("workflow_dispatch", HOJE_NO_FLUXO, [], {"INPUT_MAX": "40"}, "true"),
        ("workflow_dispatch", HOJE_NO_FLUXO, [], {"INPUT_MAX": "muitas"}, None),  # teto que não é número: erro
        ("workflow_dispatch", HOJE_NO_FLUXO, [], {"INPUT_MAX": "-3"}, None),
        ("workflow_dispatch", HOJE_NO_FLUXO, [], {"QIJ_NO_LEITURA": "1"}, "false"),  # desligada: nem à mão
        ("workflow_run", HOJE_NO_FLUXO, [], {"QIJ_NO_LEITURA": " Sim "}, "false"),
        ("workflow_run", HOJE_NO_FLUXO, [], {"QIJ_NO_CONGRESSO": "true"}, "false"),
        ("workflow_run", HOJE_NO_FLUXO, [], {"QIJ_NO_LEITURA": "0", "QIJ_NO_CONGRESSO": ""}, "true"),  # "0" não desliga
    ],
)
def test_o_primeiro_passo_decide_se_ha_o_que_ler(tmp_path: Path, evento: str, edicao: str | None, dias: list[str] | str | None, ambiente: dict[str, str], ler_: str | None):
    """O passo "Verificar se há o que ler", rodado de verdade: depois da "Edição diária", só
    manda ler se a edição de hoje (``edicoes/latest.json``, no fuso de Brasília) já saiu e o
    registro ainda não tem a linha de hoje; à mão, sempre; com a leitura ou o Congresso
    desligados, nunca. As variáveis que desligam valem como o Python as lê."""
    repo = tmp_path / "repo"
    (repo / "edicoes").mkdir(parents=True)
    if edicao is not None:
        (repo / "edicoes" / "latest.json").write_text(json.dumps({"date": edicao}), encoding="utf-8")
    if dias is not None:
        (repo / A_GUARDA).mkdir(parents=True)
        (repo / A_GUARDA / leitura.DIARIO).write_text(dias if isinstance(dias, str) else ler.serializar_diario([{"data": d, "primeiras": 1} for d in dias]), encoding="utf-8")
    if any(_truthy(v) for k, v in ambiente.items() if k.startswith("QIJ_NO_")):  # como o Python lê as variáveis que desligam
        assert ler_ == "false"
    feito = _rodar(_passo(_da_leitura(), "Verificar se há o que ler")["run"], repo, tmp_path, EVENT=evento, **ambiente)
    saida = (tmp_path / "saida").read_text(encoding="utf-8")
    if ler_ is None:
        assert feito.returncode == 1 and "::error::" in feito.stdout and saida == ""
    else:
        assert (feito.returncode, saida) == (0, f"ler={ler_}\n"), feito.stdout + feito.stderr


@precisa_de_git
@pytest.mark.parametrize(("cache", "le"), [(None, False), ("vazio", False), ("com arquivos", True)])
def test_sem_o_cache_do_congresso_o_passo_nao_le(tmp_path: Path, cache: str | None, le: bool):
    """O passo "Ler os projetos novos", rodado de verdade com um ``python`` de mentira: sem o
    cache do Congresso restaurado (a pasta não existe, ou não tem nenhum arquivo), a leitura
    baixaria tudo de novo, estouraria o prazo do passo sem deixar a linha do dia no registro
    e repetiria isso a cada hora. O passo avisa e termina sem ler; com o cache, lê."""
    comandos = Path(_ambiente_do_passo(tmp_path)["PATH"].split(os.pathsep)[0])
    (comandos / "python").write_text('#!/bin/sh\necho "python $*"\n', encoding="utf-8")
    (comandos / "python").chmod(0o755)
    temporaria = tmp_path / "runner"
    if cache is not None:
        (temporaria / "congresso" / "api").mkdir(parents=True)
    if cache == "com arquivos":
        (temporaria / "congresso" / "api" / "abc.json").write_text("{}", encoding="utf-8")
    feito = _rodar(_passo(_da_leitura(), "Ler os projetos novos")["run"], tmp_path, tmp_path, RUNNER_TEMP=str(temporaria), INPUT_MAX="7")
    assert feito.returncode == 0, feito.stdout + feito.stderr
    if le:
        assert feito.stdout == f"python -m qijournal congresso ler --dir . --cache {temporaria}/congresso --max 7\n"
    else:
        assert "python" not in feito.stdout and feito.stdout.startswith("::warning::Sem o cache do Congresso") and "não leu" in feito.stdout


def _texto_da_guarda(ano: int, estados: dict[str, int]) -> str:
    """Um arquivo da guarda como a rodada o grava: ``{chave: r}`` → uma proposição por linha."""
    return leitura.serializar(REGUA, "cd", ano, {chave: _lida(TOPO, r=r, porque=f"Proposição {chave}.") for chave, r in estados.items()})


def _registro_com(*dias: str) -> str:
    return ler.serializar_diario([{"data": d, "primeiras": 1} for d in dias])


CONFIRMADAS = {str(n): 2 for n in range(110, 180, 10)}  # sete proposições que ninguém toca, entre a 100 e o fim do arquivo
NO_REPOSITORIO = {  # a guarda antes da leitura do dia
    "cd-2026.json": _texto_da_guarda(2026, {"100": 1, **CONFIRMADAS}),
    "cd-2025.json": _texto_da_guarda(2025, {"50": 2}),
    leitura.DIARIO: _registro_com(ONTEM_NO_FLUXO),
    leitura.AUDITORIA: '{"data":"2026-11-16"}\n',
}
LIDO_HOJE = {  # o que a leitura do dia grava: a 180, nova, no fim de cd-2026.json, e a linha de hoje no registro
    "cd-2026.json": _texto_da_guarda(2026, {"100": 1, **CONFIRMADAS, "180": 1}),
    leitura.DIARIO: _registro_com(ONTEM_NO_FLUXO, HOJE_NO_FLUXO),
}
IMPORTADAS = {  # o que outro envia em arquivos da guarda que a leitura do dia não tocou
    "cd-2027.json": _texto_da_guarda(2027, {"900": 1}),
    "cd-2025.json": _texto_da_guarda(2025, {"50": 3, "60": 2}),
    leitura.AUDITORIA: '{"data":"2026-11-17"}\n',
}
NO_FIM_DO_ARQUIVO = _texto_da_guarda(2026, {"100": 2, **CONFIRMADAS, "190": 2})  # outro confirmou a 100 e importou a 190 para o fim, onde a leitura pôs a 180
NO_COMECO_DO_ARQUIVO = _texto_da_guarda(2026, {"100": 2, **CONFIRMADAS})  # outro só confirmou a 100, longe da linha nova
CASOS_DE_PUBLICAR = {
    # caso: (o que a leitura gravou na guarda; o que outro enviou enquanto ela lia, {caminho: texto}; a guarda do repositório no fim)
    "normal": (LIDO_HOJE, {}, {**NO_REPOSITORIO, **LIDO_HOJE}),
    "o remoto andou fora da guarda": (LIDO_HOJE, {"index.html": "refeita\n", "data/congresso/meta.json": '{"v":2}\n'}, {**NO_REPOSITORIO, **LIDO_HOJE}),
    "o remoto andou em outros arquivos da guarda": (LIDO_HOJE, {f"{A_GUARDA}/{nome}": texto for nome, texto in IMPORTADAS.items()}, {**NO_REPOSITORIO, **LIDO_HOJE, **IMPORTADAS}),
    # Não dá para juntar: fica o que o outro enviou, e a leitura do dia não é publicada.
    "o remoto mexeu nas mesmas linhas da guarda": (
        LIDO_HOJE,
        {f"{A_GUARDA}/cd-2026.json": NO_FIM_DO_ARQUIVO, f"{A_GUARDA}/cd-2027.json": IMPORTADAS["cd-2027.json"]},
        {**NO_REPOSITORIO, "cd-2026.json": NO_FIM_DO_ARQUIVO, "cd-2027.json": IMPORTADAS["cd-2027.json"]},
    ),
    # As duas mudanças ficam: a 100 confirmada pelo outro e a 180 lida hoje.
    "o remoto mexeu no mesmo arquivo, em outras linhas": (
        LIDO_HOJE,
        {f"{A_GUARDA}/cd-2026.json": NO_COMECO_DO_ARQUIVO},
        {**NO_REPOSITORIO, **LIDO_HOJE, "cd-2026.json": _texto_da_guarda(2026, {"100": 2, **CONFIRMADAS, "180": 1})},
    ),
    "nada a publicar": ({}, {"index.html": "refeita\n"}, NO_REPOSITORIO),
    # Um arquivo que sumiu da cópia local e a sobra de uma gravação interrompida não vão ao repositório.
    "um arquivo sumiu da cópia local": ({**LIDO_HOJE, "cd-2025.json": None, ".cd-2026.json.abc123.tmp": "pela metade"}, {"index.html": "refeita\n"}, {**NO_REPOSITORIO, **LIDO_HOJE}),
}


@precisa_de_git
@pytest.mark.parametrize("caso", list(CASOS_DE_PUBLICAR))
def test_publicar_a_leitura_nao_desfaz_nem_apaga_o_que_outro_enviou(plano, tmp_path: Path, caso: str):
    """O passo "Publicar a leitura", rodado de verdade em repositórios locais: o do GitHub,
    a cópia rasa da execução e a de outro, que envia enquanto a leitura lê. O commit da
    leitura só toca a pasta da guarda; o que o outro enviou (dentro ou fora dela) chega ao
    fim igual, sempre; e quando os dois mexeram nas mesmas linhas, a leitura não é publicada
    (o passo avisa e termina sem erro)."""
    gravado, enviado, no_fim = CASOS_DE_PUBLICAR[caso]
    conflito = caso == "o remoto mexeu nas mesmas linhas da guarda"
    ambiente = _ambiente_do_passo(tmp_path)

    def git(onde: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=onde, env=ambiente, check=True, capture_output=True, text=True, timeout=60).stdout

    def gravar(repo: Path, arquivos: dict[str, str | None]) -> None:
        for caminho, texto in arquivos.items():
            destino = repo / caminho
            destino.parent.mkdir(parents=True, exist_ok=True)
            if texto is None:
                destino.unlink()
            else:
                destino.write_text(texto, encoding="utf-8")

    def enviar(mensagem: str) -> None:
        git(outro, "add", "--all")
        git(outro, "commit", "--quiet", "-m", mensagem)
        git(outro, "push", "--quiet", "origin", "main")

    origem, runner, outro = tmp_path / "origin.git", tmp_path / "runner", tmp_path / "outro"
    git(tmp_path, "init", "--quiet", "--bare", str(origem))
    git(tmp_path, "clone", "--quiet", str(origem), str(outro))
    gravar(outro, {"index.html": "hoje\n", "edicoes/latest.json": '{"date":"2026-11-17"}\n', "data/congresso/meta.json": '{"v":1}\n', **{f"{A_GUARDA}/{nome}": texto for nome, texto in NO_REPOSITORIO.items()}})
    enviar("inicial")
    git(tmp_path, "clone", "--quiet", "--depth", "1", origem.as_uri(), str(runner))  # raso, como o checkout do Actions
    gravar(runner, {f"{A_GUARDA}/{nome}": texto for nome, texto in gravado.items()})
    if enviado:
        gravar(outro, enviado)
        enviar("enviado por outro")
    do_outro = git(origem, "rev-parse", "main").strip()

    feito = _rodar(_passo(_da_leitura(), "Publicar a leitura (commit e push)")["run"], runner, tmp_path)
    assert feito.returncode == 0, feito.stdout + feito.stderr
    assert ("::warning::" in feito.stdout) is conflito
    assert not (runner / ".git" / "rebase-merge").exists() and not (runner / ".git" / "rebase-apply").exists()
    # A guarda no repositório, arquivo por arquivo; e o que o outro enviou, igual.
    guarda_final = {nome: git(origem, "show", f"main:{A_GUARDA}/{nome}") for nome in git(origem, "ls-tree", "--name-only", f"main:{A_GUARDA}/").split()}
    assert guarda_final == no_fim
    for caminho, texto in enviado.items():
        if conflito or not caminho.endswith("cd-2026.json"):  # no mesmo arquivo, em outras linhas, as duas mudanças se juntam
            assert git(origem, "show", f"main:{caminho}") == texto, caminho
    # A guarda que ficou é legível, linha por linha.
    (tmp_path / "conferir" / "leitura").mkdir(parents=True)
    gravar(tmp_path / "conferir" / "leitura", guarda_final)
    conferida = _guarda(tmp_path / "conferir", plano)
    assert (len(conferida), conferida.invalidas, conferida.ignorados) == (sum(len(json.loads(texto)["itens"]) for nome, texto in no_fim.items() if nome.startswith("cd-")), 0, [])
    assert [d["data"] for d in _dias(tmp_path / "conferir")] == [ONTEM_NO_FLUXO, *([HOJE_NO_FLUXO] if gravado and not conflito else [])]
    historico = git(origem, "log", "--format=%s", "main").splitlines()
    if not gravado or conflito:
        assert git(origem, "rev-parse", "main").strip() == do_outro and COMMIT_DA_LEITURA not in historico  # nada foi publicado
    else:
        assert historico == [COMMIT_DA_LEITURA, *(["enviado por outro"] if enviado else []), "inicial"]
        assert git(origem, "rev-parse", "main~1").strip() == do_outro  # o commit da leitura vem por cima do que o outro enviou
        mudou = git(origem, "show", "--format=", "--name-status", "main").split()
        assert mudou == ["M", f"{A_GUARDA}/cd-2026.json", "M", f"{A_GUARDA}/{leitura.DIARIO}"]  # só a guarda; nenhuma remoção, nenhum arquivo temporário


@precisa_de_git
def test_publicar_a_leitura_sem_a_pasta_das_leituras_nao_e_erro(tmp_path: Path):
    """Leitura desligada e pasta ainda fora do repositório: o passo termina sem erro e sem commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    feito = _rodar("git init --quiet .\n" + _passo(_da_leitura(), "Publicar a leitura (commit e push)")["run"], repo, tmp_path)
    assert feito.returncode == 0 and "nada a publicar" in feito.stdout, feito.stdout + feito.stderr
    assert _rodar("git log --oneline\n", repo, tmp_path).returncode != 0  # nenhum commit
