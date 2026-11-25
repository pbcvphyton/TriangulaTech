"""Testes do retrato da Agenda da semana (``qijournal/agenda/retrato.py``): as classes que a
edição grava e a tela lê, a ida e volta para dicionário e a leitura tolerante."""

from __future__ import annotations

import copy
import dataclasses
import json

import pytest
from jinja2 import Environment, StrictUndefined

from qijournal.agenda import montar, regras, retrato
from qijournal.agenda.retrato import (
    Contagem,
    DiaTela,
    FonteTela,
    Frase,
    ItemTela,
    LinhaTela,
    MateriaNaAgenda,
    Pagina,
    Pe,
    Retrato,
    ler_retrato,
)
from tests.fixtures import agenda as fx

REGRAS = regras.carregar()
CLASSES = (ItemTela, Contagem, LinhaTela, DiaTela, Frase, Pe, MateriaNaAgenda, FonteTela, Pagina, Retrato)


def _real() -> Retrato:
    return montar.montar(fx.camara_encerrada(), fx.tribunais(), "2026-11-24", REGRAS)


def _pequeno() -> Retrato:
    item = ItemTela(identificador="PL 1/2026", identificador_tela="PL 1/2026", link="https://www.camara.leg.br/x", nome="Nome do item", nome_curto="Nome", nota=63, midia=2, midia_texto="na mídia: 2 veículos", materia="materia-1")
    linha = LinhaTela(id="camara-1", data="2026-11-24", dia="ter 24", hora="14h", instituicao="Câmara", orgao="Plenário", cabeca="Câmara · Plenário", itens=(item,), mais=3, mais_texto="mais 3", nota=63)
    return Retrato(
        dia="2026-11-24",
        janela=("2026-11-24", "2026-11-25"),
        lados=("congresso",),
        dias=(DiaTela(data="2026-11-24", rotulo="ter 24", rotulo_longo="terça-feira, 24 de novembro", linhas=(linha,), principal=True),),
        frases=(Frase(tipo="fonte", instituicao="STF e STJ", texto="STF e STJ: pauta indisponível nesta edição."),),
        pe=Pe(texto="Conferido em 24/11. A pauta pode mudar.", conferido="24/11", conferido_em="2026-11-24"),
        materias=(MateriaNaAgenda(materia="materia-1", rotulo="na agenda: ter 24", data="2026-11-24", identificador="PL 1/2026", linha="camara-1"),),
    )


# ── todo campo tem padrão (o ambiente Jinja do projeto é estrito) ────────────────────────────


@pytest.mark.parametrize("classe", CLASSES)
def test_todo_campo_tem_padrao(classe):
    objeto = classe()  # sem argumento nenhum
    for campo in dataclasses.fields(classe):
        valor = getattr(objeto, campo.name)
        assert valor is not None, f"{classe.__name__}.{campo.name}"
        assert isinstance(valor, (str, int, bool, tuple)) or dataclasses.is_dataclass(valor)


def test_retrato_vazio_pode_ser_lido_por_um_modelo_estrito():
    ambiente = Environment(undefined=StrictUndefined, autoescape=True)
    modelo = ambiente.from_string(
        "{{ r.pe.texto }}|{{ r.pe.fontes|length }}|{{ r.vazio }}|{{ r.fora_do_teto }}|{{ r.rotulo_da_materia('x') }}|"
        "{% for d in r.dias %}{{ d.rotulo }}{{ d.vazio }}{% for l in d.linhas %}{{ l.cabeca }}{{ l.hora }}{{ l.situacao }}{{ l.mais_texto }}"
        "{{ l.mais_celular_texto }}{% for i in l.itens %}{{ i.area }}{{ i.nome }}{{ i.nome_curto }}{{ i.identificador_tela }}{{ i.link }}"
        "{{ i.situacao }}{{ i.midia_texto }}{{ i.materia }}{% endfor %}{% endfor %}{% endfor %}"
        "{{ r.pagina.sem_pauta }}{{ r.pagina.fila_texto }}{{ r.pagina.dias|length }}{{ r.pagina.corte }}"
    )
    assert modelo.render(r=Retrato()) == "|0|True|0||00"
    saida = modelo.render(r=_real())
    assert "STF · Plenário" in saida and "Conferido em 23/11" in saida


# ── ida e volta ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fabrica", [_pequeno, _real, Retrato])
def test_ida_e_volta_deterministica(fabrica):
    original = fabrica()
    dados = original.para_dict()
    texto = json.dumps(dados, ensure_ascii=False, sort_keys=False)
    de_volta = ler_retrato(json.loads(texto))
    assert de_volta == original
    assert json.dumps(de_volta.para_dict(), ensure_ascii=False) == texto  # a mesma saída, byte a byte
    assert json.dumps(fabrica().para_dict(), ensure_ascii=False) == texto  # e a mesma a cada montagem


def test_dicionario_so_tem_tipos_de_json_e_omite_o_que_esta_no_padrao():
    dados = _pequeno().para_dict()
    assert list(dados) == ["versao", "dia", "janela", "lados", "dias", "frases", "pe", "materias"]
    assert dados["versao"] == retrato.VERSAO
    linha = dados["dias"][0]["linhas"][0]
    assert isinstance(dados["janela"], list) and isinstance(linha["itens"], list)
    assert "piso" not in linha and "fora" not in linha and "situacao" not in linha  # padrões não são gravados
    assert "termo" not in linha["itens"][0] and linha["itens"][0]["midia"] == 2
    assert Retrato().para_dict() == {"versao": retrato.VERSAO}

    def confere(valor):
        if isinstance(valor, dict):
            assert all(isinstance(k, str) for k in valor)
            for v in valor.values():
                confere(v)
        elif isinstance(valor, list):
            for v in valor:
                confere(v)
        else:
            assert isinstance(valor, (str, int, bool))

    confere(_real().para_dict())


def test_consultas_para_a_tela():
    r = _pequeno()
    assert not r.vazio and Retrato().vazio
    assert [l.id for l in r.linhas] == ["camara-1"]
    assert r.rotulo_da_materia("materia-1") == "na agenda: ter 24"
    assert r.materia("materia-1").linha == "camara-1"
    assert r.rotulo_da_materia("outra") == "" and r.materia("outra") is None


# ── leitura tolerante ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "lixo",
    [
        None, "", "agenda", 7, 3.5, True, [], [1, 2], {},
        {"versao": 2}, {"versao": "1"}, {"versao": None}, {"versao": True}, {"dias": []},
        {"versao": 1, "dias": "hoje"},
        {"versao": 1, "dias": [1]},
        {"versao": 1, "dias": [{"linhas": [{"itens": [{"nota": "alta"}]}]}]},
        {"versao": 1, "dias": [{"linhas": [{"itens": "x"}]}]},
        {"versao": 1, "dias": [{"principal": "sim"}]},
        {"versao": 1, "dia": 20261124},
        {"versao": 1, "janela": ["2026-11-24", 5]},
        {"versao": 1, "pe": "Conferido"},
        {"versao": 1, "pe": {"fontes": "STF"}},
        {"versao": 1, "fora_do_teto": True},
        {"versao": 1, "fora_do_teto": 1.5},
        {"versao": 1, "pagina": []},
        {"versao": 1, "pagina": {"fora": [{"n": "muitos"}]}},
        {"versao": 1, "materias": [{"materia": None}]},
        {"versao": 1, "frases": [{"itens": [None]}]},
    ],
)
def test_formato_inesperado_vale_como_sem_agenda(lixo):
    assert ler_retrato(lixo) is None


def test_um_campo_estragado_em_qualquer_ponto_derruba_o_retrato_inteiro():
    bom = _real().para_dict()
    assert ler_retrato(bom) is not None
    estragado = copy.deepcopy(bom)
    estragado["pagina"]["dias"][0]["linhas"][0]["itens"][0]["nota"] = "63"
    assert ler_retrato(estragado) is None
    estragado = copy.deepcopy(bom)
    estragado["decidido"][0]["itens"] = {"0": {}}
    assert ler_retrato(estragado) is None


def test_campo_a_mais_e_ignorado_e_campo_a_menos_cai_no_padrao():
    dados = _pequeno().para_dict()
    dados["campo_do_futuro"] = {"x": 1}
    dados["dias"][0]["linhas"][0]["outro"] = [1, 2]
    del dados["pe"]
    lido = ler_retrato(dados)
    assert lido is not None and lido.pe == Pe() and lido.dias[0].linhas[0].cabeca == "Câmara · Plenário"


def test_de_dict_levanta_e_ler_retrato_nao():
    with pytest.raises(ValueError):
        Retrato.de_dict({"versao": 9})
    with pytest.raises(ValueError):
        Retrato.de_dict({"versao": 1, "dias": 3})
    assert ler_retrato({"versao": 9}) is None
