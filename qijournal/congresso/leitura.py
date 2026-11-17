"""A leitura dos projetos de lei pela régua "Projeto de país": a guarda, a conta e o que falta ler.

Cada proposição (PL, PLP, PEC) é lida pela ementa: um leitor marca seis campos com códigos
fechados, um alerta, a confiança e uma justificativa de uma linha. Os códigos, os pesos e os
cortes estão em ``config/congresso.yaml`` (``projeto_de_pais``). **Toda leitura vem da guarda**
e é de IA (``Leitura.fonte`` = ``ia``): um leitor de IA, com as instruções de
``config/leitura_projetos.txt``, lê todas as proposições do universo, e um leitor mais forte
relê as candidatas ao topo e as matérias votadas. A proposição que a guarda não tem fica sem
leitura até a próxima rodada.

O encaixe para um leitor de regras, sem IA (``fonte`` = ``regra``:
:mod:`qijournal.congresso.leitor_regras`), continua no código e hoje não lê nada. Se um dia
ler, a leitura dele não é gravada, nunca confirma a faixa do topo, e a de IA **vale sobre
ela** (:func:`leitura_de`).

Este módulo não lê nenhum projeto: guarda o que a IA leu, faz a conta de cada leitura e diz
o que falta mandar à IA.

- **A guarda** (``data/congresso/leitura/<casa>-<ano>.json``, um arquivo por Casa e ano da
  proposição, uma proposição por linha, em ordem de chave)::

      {"v":1,"regua":1,"casa":"cd","ano":2025,"itens":{
      "2491234":["fiscal","grupo","beneficio","gasto","profissao","neutro",1,3,"","Aposentadoria…",1]
      }}

  A linha: os códigos dos campos na ordem do YAML (``a, e, m, ct, is, co``), o alerta de
  iniciativa (0 ou 1), a confiança (1 a 3), os campos em dúvida, a justificativa e ``r``, o
  estado da leitura: 1 = uma leitura; 2 = duas leituras independentes na mesma faixa; 3 = as
  duas divergiram e uma terceira decidiu; 0 = leitura instável. A chave é o id da proposição
  na Câmara e ``SIGLA-NUMERO-ANO`` no Senado. ``regua`` é a versão das instruções ao leitor:
  quando ela sobe, os arquivos gravados com a antiga deixam de valer.

- **A conta** (:func:`avaliar`): o peso é 100 × nível da alavanca × escopo × mecanismo,
  arredondado meio para cima; a faixa sai do peso (a do topo só para alavanca do núcleo, e
  só conta depois de confirmada por duas leituras de IA: a leitura de regras, ou uma só de
  IA, fica na faixa de baixo, com o alerta); as três marcas (contas, isonomia, controle)
  levam o valor do seu código e nunca se somam nem multiplicam o peso.

- **A leitura de uma proposição** (:func:`leitura_de`; :class:`Leitor` para quem faz as
  contas): a de IA, da guarda, se houver; senão, a do leitor de regras (hoje, nenhuma).

- **O que falta mandar à IA** (:func:`universo`, :func:`faltam`, :func:`para_segunda`): das
  proposições de que as contas precisam (o que cada um propõe, o que tramita e as matérias
  com votação do texto principal: :func:`do_texto_principal`), as que a IA ainda não leu e
  as que esperam a segunda leitura; saem em lotes cegos (:func:`gravar_lotes`): o leitor não
  recebe número, autor, partido nem origem.

O que fica de fora: a leitura em si (a de IA: a carga, feita por fora e trazida por
:func:`importar`, e a de todo dia, das proposições novas, em :mod:`qijournal.congresso.ler`,
que grava na mesma guarda e deixa o registro de cada dia em ``diario.json``: :func:`ler_diario`;
a de regras, em ``leitor_regras.py``), as contas por parlamentar e por Casa, e tudo o que a
ementa não mostra (o texto integral, o custo em reais, o mérito). Sem leitura (algum "nd" em
campo de peso, leitura instável ou proposição que ninguém leu), a proposição fica fora de
toda conta e só aparece na cobertura (:func:`cobertura`).
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from .. import text
from . import base, materias, projetos, temas
from .base import AnoVotos
from .fontes import Coletor
from .projetos import Proposicao
from .regras import ND, FaixaDaRegua, ProjetoDePais, Regras

log = logging.getLogger(__name__)

VERSAO = 1  # o formato do arquivo da guarda ("v")
PASTA = "leitura"  # data/congresso/leitura/
AUDITORIA = "auditoria.json"  # a concordância medida entre leituras: escrita por fora, na mesma pasta
INSTRUCOES = "leitura_projetos.txt"  # config/: o que o leitor recebe
REFERENCIA = "leitura_referencia.jsonl"  # config/: as perguntas de controle da leitura diária (:mod:`qijournal.congresso.ler`)
DIARIO = "diario.json"  # o registro da leitura diária, na pasta da guarda: uma linha por dia
# De onde vem uma leitura (os rótulos ficam no YAML, em ``fontes``). Na coluna "fo" dos
# arquivos da tela: "regra", "ia" e, para a leitura de IA confirmada (r ≥ 2), "ia2".
REGRA, IA, IA2 = "regra", "ia", "ia2"
FONTES = (REGRA, IA)
# O estado de uma leitura de IA ("r"). A leitura de regras é sempre uma leitura só.
INSTAVEL, UMA, DUAS, TRES = 0, 1, 2, 3
ESTADOS = (INSTAVEL, UMA, DUAS, TRES)
CONFIANCAS = (1, 2, 3)
PORQUE_MAX = 160  # a justificativa gravada (as instruções pedem até 120)
# Os alertas de uma leitura (os rótulos ficam no YAML, em ``alertas``).
INICIATIVA, A_CONFIRMAR, DUVIDA = "iniciativa", "a_confirmar", "duvida"
# Por que uma proposição precisa de leitura.
PERFIL, ESPERA, VOTO = "perfil", "espera", "voto"
UNIVERSOS = (PERFIL, ESPERA, VOTO)
# Por que uma leitura de IA pede uma segunda.
PESO_ALTO, POUCA_CONFIANCA, VOTADA = "peso", "duvida", "votada"
# Por que uma proposição sem leitura de IA precisa de uma: é matéria votada (VOTADA) ou o
# leitor de regras a põe entre as candidatas ao topo.
CANDIDATA = "candidata"
# Os lotes cegos.
LOTE = 300
SEMENTE = 20261116  # a mesma lista sai sempre na mesma ordem
EMENTA_MAX, EXPLICACAO_MAX, PALAVRAS_MAX, PALAVRAS, TEMAS = 700, 300, 220, 14, 2  # o que o leitor recebe de cada proposição


# ── uma leitura e a conta dela ───────────────────────────────────────────────


@dataclass(frozen=True)
class Leitura:
    """O que um leitor marcou numa proposição. A de IA é uma linha da guarda; a de regras
    tem a mesma forma, com ``fonte`` "regra" e, em ``porque``, a regra que decidiu."""

    codigos: tuple[str, ...]  # um por campo da régua, na ordem do YAML (a, e, m, ct, is, co); "nd" onde a ementa não permite dizer
    iniciativa: int = 0  # "in": 1 se a ementa mexe em órgão, cargo ou servidor do Executivo
    confianca: int = 2  # "cf": 3 = decorre das palavras da ementa; 2 = houve desempate; 1 = admite outra leitura
    duvida: str = ""  # "du": as chaves dos campos em dúvida ("e", "a,m")
    porque: str = ""  # "pq": o fato da ementa que decidiu os códigos
    r: int = UMA  # o estado da leitura (ver ESTADOS)
    fonte: str = IA  # de onde vem: "ia" (a guarda) ou "regra" (o leitor de regras: não vai à guarda e não confirma)

    def linha(self) -> list[Any]:
        """A linha gravada: ``[a, e, m, ct, is, co, in, cf, du, pq, r]`` (a fonte não vai: só
        leitura de IA é gravada)."""
        return [*self.codigos, self.iniciativa, self.confianca, self.duvida, self.porque, self.r]

    @property
    def confirmada(self) -> bool:
        """Dois leitores de IA ou mais chegaram à mesma faixa (``r`` 2 ou 3). A leitura de
        regras nunca confirma."""
        return self.fonte == IA and self.r >= DUAS

    @property
    def fo(self) -> str:
        """A fonte como vai aos arquivos da tela: ``regra``, ``ia`` ou ``ia2`` (IA, confirmada)."""
        return REGRA if self.fonte != IA else IA2 if self.confirmada else IA


@dataclass(frozen=True)
class Avaliacao:
    """Da leitura aos números de uma proposição. Sem leitura (``lida`` falso), o resto fica
    vazio e a proposição só conta na cobertura."""

    lida: bool
    motivo: str = ""  # sem leitura: "nd" (a ementa não permite dizer) ou "instavel" (r = 0)
    peso: int | None = None  # 0 a 100
    faixa: str = ""  # o id da faixa que conta (a do topo, só depois de confirmada)
    faixa_da_ementa: str = ""  # o id da faixa que sai da fórmula, com uma leitura ou com duas
    alavanca: str = ""  # o código da alavanca (o "eixo" da tela)
    nivel: str = ""
    marcas: Mapping[str, int | None] = field(default_factory=dict)  # marca → +1, 0, −1 ou None (a ementa não permite dizer)
    sinais: Mapping[str, str] = field(default_factory=dict)  # marca → o id do valor no YAML (contas: poupa, neutro, onera, nd)
    alertas: tuple[str, ...] = ()  # iniciativa, a_confirmar, duvida
    conta: str = ""  # a conta do peso por extenso
    cod: tuple[tuple[str, str], ...] = ()  # (campo, código) dos demais campos, na ordem do YAML
    fonte: str = ""  # quem leu: "regra" ou "ia" (vazio: ninguém leu)
    fo: str = ""  # a fonte como vai à tela: "regra", "ia" ou "ia2" (IA, confirmada)


def _decimal(valor: float) -> Decimal:
    return Decimal(str(valor))


def _numero(valor: Decimal) -> str:
    """``1`` → "1,0"; ``0.15`` → "0,15"; ``9.6`` → "9,6"."""
    texto = format(valor.normalize(), "f")
    return (texto if "." in texto else texto + ".0").replace(".", ",")


def _minuscula(rotulo: str) -> str:
    """O rótulo dentro de uma frase ("Regra de sistema" → "regra de sistema"); a sigla fica."""
    return rotulo[:1].lower() + rotulo[1:] if len(rotulo) > 1 and not rotulo[1].isupper() else rotulo


def fatores(regua: ProjetoDePais, leitura: Leitura) -> list[tuple[Decimal, str]] | None:
    """Os fatores do peso, na ordem da conta: ``(valor, rótulo)`` do nível da alavanca e de
    cada campo de peso (escopo, mecanismo). ``None`` se algum deles é "nd"."""
    codigos = dict(zip(regua.chaves, leitura.codigos))
    alavanca = regua.alavanca.codigo(codigos.get(regua.alavanca.chave, ND))
    if alavanca is None:
        return None
    saida = [(_decimal(regua.peso_do_nivel(alavanca.nivel)), regua.rotulo_do_nivel(alavanca.nivel))]
    for campo in regua.campos_de_peso:
        codigo = campo.codigo(codigos.get(campo.chave, ND))
        if codigo is None or codigo.peso is None:
            return None
        saida.append((_decimal(codigo.peso), _minuscula(codigo.rotulo)))
    return saida


def _produto(termos: Iterable[tuple[Decimal, str]]) -> Decimal:
    total = Decimal(100)
    for valor, _rotulo in termos:
        total *= valor
    return total


def peso(regua: ProjetoDePais, leitura: Leitura) -> int | None:
    """100 × nível da alavanca × escopo × mecanismo, arredondado meio para cima (9,6 → 10;
    4,5 → 5). ``None`` quando algum dos três é "nd": sem leitura."""
    termos = fatores(regua, leitura)
    if termos is None:
        return None
    return int(_produto(termos).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def conta_por_extenso(regua: ProjetoDePais, leitura: Leitura) -> str:
    """A conta do peso, termo a termo: "100 × 1,0 (núcleo) × 0,6 (um segmento inteiro) ×
    1,0 (regra de sistema) = 60". Quando o produto não é inteiro, diz o arredondamento:
    "… = 9,6, arredondado para 10". Vazio sem leitura."""
    termos = fatores(regua, leitura)
    if termos is None:
        return ""
    exato = _produto(termos)
    inteiro = int(exato.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    partes = " × ".join(["100", *(f"{_numero(valor)} ({rotulo})" for valor, rotulo in termos)])
    return f"{partes} = {inteiro}" if exato == inteiro else f"{partes} = {_numero(exato)}, arredondado para {inteiro}"


def _faixas_do_peso(regua: ProjetoDePais, valor: int, nivel: str) -> list[FaixaDaRegua]:
    """As faixas cujo piso o peso alcança e que valem para o nível, da mais baixa à mais alta."""
    return [f for f in regua.faixas if f.de <= valor and (not f.so_nivel or f.so_nivel == nivel)]


def pede_confirmacao(regua: ProjetoDePais, faixa: FaixaDaRegua) -> bool:
    """Faixa que só conta depois da segunda leitura: a que começa no peso mínimo da segunda
    leitura (``segunda_leitura.peso_minimo``) ou acima dele."""
    return faixa.de >= regua.segunda_leitura.peso_minimo


def faixa_do_peso(regua: ProjetoDePais, valor: int, nivel: str) -> FaixaDaRegua:
    """A faixa que sai da fórmula: a mais alta cujo piso o peso alcança e que vale para o
    nível da alavanca (peso 60 fora do núcleo fica na faixa de baixo)."""
    return _faixas_do_peso(regua, valor, nivel)[-1]


def faixa_que_conta(regua: ProjetoDePais, valor: int, nivel: str, confirmada: bool) -> FaixaDaRegua:
    """A faixa em que a proposição entra nas contas: a da fórmula, salvo quando ela pede
    confirmação e a leitura não está confirmada (uma leitura só de IA, ou leitura de
    regras); aí vale a faixa logo abaixo."""
    validas = _faixas_do_peso(regua, valor, nivel)
    if confirmada or not pede_confirmacao(regua, validas[-1]):
        return validas[-1]
    return next((f for f in reversed(validas) if not pede_confirmacao(regua, f)), validas[0])


def marcas(regua: ProjetoDePais, leitura: Leitura) -> dict[str, int | None]:
    """As marcas do sinal (contas, isonomia, controle): o valor do código de cada campo
    (+1, 0 ou −1) ou ``None`` onde a ementa não permite dizer. Cada uma vale sozinha: não
    se somam entre si nem multiplicam o peso."""
    codigos = dict(zip(regua.chaves, leitura.codigos))
    saida: dict[str, int | None] = {}
    for campo in regua.campos_de_marca:
        codigo = campo.codigo(codigos.get(campo.chave, ND))
        saida[campo.marca] = codigo.valor if codigo is not None else None
    return saida


def alertas(regua: ProjetoDePais, leitura: Leitura, *, tipo: str = "", origem: str = "") -> tuple[str, ...]:
    """Os alertas de uma leitura com peso:

    - ``iniciativa``: o leitor marcou ``in`` = 1 e a proposição é de origem parlamentar e de
      um dos tipos do YAML (PL ou PLP); sem ``tipo`` e ``origem``, o alerta não acende;
    - ``a_confirmar``: a faixa da fórmula pede confirmação e a leitura não está confirmada
      (uma só de IA, ou de regras);
    - ``duvida``: confiança baixa (``leitura.reler_confianca``) em leitura não confirmada.
    """
    valor = peso(regua, leitura)
    if valor is None or (leitura.fonte == IA and leitura.r == INSTAVEL):
        return ()
    saida = []
    regra = regua.alerta_iniciativa
    if leitura.iniciativa and origem == regra.origem and tipo in regra.tipos:
        saida.append(INICIATIVA)
    alavanca = regua.alavanca.codigo(leitura.codigos[regua.chaves.index(regua.alavanca.chave)])
    if not leitura.confirmada and alavanca is not None and pede_confirmacao(regua, faixa_do_peso(regua, valor, alavanca.nivel)):
        saida.append(A_CONFIRMAR)
    if not leitura.confirmada and leitura.confianca <= regua.leitura.reler_confianca:
        saida.append(DUVIDA)
    return tuple(saida)


def avaliar(regua: ProjetoDePais, leitura: Leitura | None, *, tipo: str = "", origem: str = "") -> Avaliacao:
    """Da leitura aos números. ``tipo`` (a sigla: PL, PLP, PEC) e ``origem`` (a de
    ``Proposicao.origem``) só servem ao alerta de iniciativa.

    Sem leitura (``lida`` falso): ninguém leu (``leitura`` é ``None``), leitura de IA
    instável (``r`` = 0) ou "nd" na alavanca, no escopo ou no mecanismo.

    A leitura de regras (``fonte`` "regra") passa pela mesma conta e nunca confirma: no peso
    do topo, conta na faixa de baixo e leva o alerta ``a_confirmar``.
    """
    if leitura is None:
        return Avaliacao(lida=False, motivo="")
    if leitura.fonte == IA and leitura.r == INSTAVEL:
        return Avaliacao(lida=False, motivo="instavel", fonte=leitura.fonte, fo=leitura.fo)
    valor = peso(regua, leitura)
    if valor is None:
        return Avaliacao(lida=False, motivo=ND, fonte=leitura.fonte, fo=leitura.fo)
    codigos = dict(zip(regua.chaves, leitura.codigos))
    alavanca = regua.alavanca.codigo(codigos[regua.alavanca.chave])
    assert alavanca is not None  # peso() já garantiu
    sinal = marcas(regua, leitura)
    ids = {}
    for nome, v in sinal.items():
        marca = regua.marca(nome)
        entrada = marca.de(v) if marca else None
        ids[nome] = entrada.id if entrada else ND
    return Avaliacao(
        lida=True,
        peso=valor,
        faixa=faixa_que_conta(regua, valor, alavanca.nivel, leitura.confirmada).id,
        faixa_da_ementa=faixa_do_peso(regua, valor, alavanca.nivel).id,
        alavanca=alavanca.id,
        nivel=alavanca.nivel,
        marcas=sinal,
        sinais=ids,
        alertas=alertas(regua, leitura, tipo=tipo, origem=origem),
        conta=conta_por_extenso(regua, leitura),
        cod=tuple((campo.id, codigos[campo.chave]) for campo in regua.campos if campo.id != regua.alavanca.id),
        fonte=leitura.fonte,
        fo=leitura.fo,
    )


def pede_segunda(regua: ProjetoDePais, leitura: Leitura, *, votada: bool = False) -> tuple[str, ...]:
    """Por que uma leitura de IA pede uma segunda, independente (vazio se não pede). Só a
    leitura de um leitor de IA só (``r`` = 1) pede: ``peso`` (peso no mínimo da segunda
    leitura ou acima), ``duvida`` (confiança baixa) e ``votada`` (matéria com votação nominal
    do texto principal). A leitura de regras não pede segunda: pede a primeira de IA
    (:func:`pede_ia`)."""
    if leitura.fonte != IA or leitura.r != UMA:
        return ()
    motivos = []
    valor = peso(regua, leitura)
    if valor is not None and valor >= regua.segunda_leitura.peso_minimo:
        motivos.append(PESO_ALTO)
    if leitura.confianca <= regua.leitura.reler_confianca:
        motivos.append(POUCA_CONFIANCA)
    if votada:
        motivos.append(VOTADA)
    return tuple(motivos)


def _faixa_ou_nada(regua: ProjetoDePais, leitura: Leitura) -> str:
    """O id da faixa da fórmula; vazio para a leitura que deixa a proposição sem leitura."""
    return avaliar(regua, replace(leitura, r=DUAS)).faixa_da_ementa


def conciliar(regua: ProjetoDePais, primeira: Leitura, segunda: Leitura, terceira: Leitura | None = None) -> Leitura:
    """O que vale depois de uma segunda leitura independente (e de uma terceira, se houve).

    - as duas na mesma faixa (ou as duas sem leitura): valem os códigos da primeira, ``r`` = 2;
    - faixas diferentes e sem terceira: leitura instável, ``r`` = 0 (os códigos da primeira
      ficam guardados, mas a proposição fica sem leitura);
    - com terceira: vale a faixa em que dois dos três caíram, com os códigos do primeiro
      leitor que caiu nela, ``r`` = 3; três faixas diferentes: leitura instável, ``r`` = 0.
    """
    f1, f2 = _faixa_ou_nada(regua, primeira), _faixa_ou_nada(regua, segunda)
    if f1 == f2:
        return replace(primeira, r=DUAS)
    if terceira is None:
        return replace(primeira, r=INSTAVEL)
    f3 = _faixa_ou_nada(regua, terceira)
    if f3 == f1:
        return replace(primeira, r=TRES)
    if f3 == f2:
        return replace(segunda, r=TRES)
    return replace(primeira, r=INSTAVEL)


# ── a linha do leitor e a linha da guarda ────────────────────────────────────


def _inteiro(valor: Any) -> int | None:
    if isinstance(valor, bool):
        return int(valor)
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    if isinstance(valor, str) and re.fullmatch(r"-?\d+", valor.strip()):
        return int(valor.strip())
    return None


def validar(regua: ProjetoDePais, registro: Mapping[str, Any], *, fonte: str = IA) -> tuple[Leitura | None, str]:
    """Uma leitura como o leitor a entrega (``{"a":…,"e":…,"m":…,"ct":…,"is":…,"co":…,
    "in":0,"cf":3,"du":"","pq":"…","r":1}``) → ``(Leitura, "")`` ou ``(None, motivo)``.
    ``fonte``: quem leu ("ia", o padrão, ou "regra", que é sempre uma leitura só: ``r`` = 1).

    Recusa: código que não está no YAML (todo campo aceita "nd"), ``in`` fora de 0 e 1,
    ``cf`` fora de 1 a 3 e ``r`` fora de 0 a 3 (sem ``r``, é uma leitura: 1). ``du`` fica só
    com as chaves de campo que existem; ``pq`` perde os espaços a mais e é cortado em 160
    caracteres.
    """
    codigos = []
    for campo in regua.campos:
        valor = registro.get(campo.chave)
        if not isinstance(valor, str) or (valor != ND and campo.codigo(valor) is None):
            return None, f"código inválido em \"{campo.chave}\": {valor!r}"
        codigos.append(valor)
    chave_do_alerta = regua.alerta_iniciativa.chave
    alerta = _inteiro(registro.get(chave_do_alerta, 0))
    if alerta not in (0, 1):
        return None, f"\"{chave_do_alerta}\" tem de ser 0 ou 1: {registro.get(chave_do_alerta)!r}"
    confianca = _inteiro(registro.get("cf"))
    if confianca not in CONFIANCAS:
        return None, f"\"cf\" tem de ser 1, 2 ou 3: {registro.get('cf')!r}"
    r = _inteiro(registro.get("r", UMA))
    if r not in ESTADOS:
        return None, f"\"r\" tem de ser 0, 1, 2 ou 3: {registro.get('r')!r}"
    duvida = ",".join(k for k in dict.fromkeys(re.split(r"[,;\s]+", str(registro.get("du") or ""))) if k in regua.chaves)
    porque = text.truncate(" ".join(str(registro.get("pq") or "").split()), PORQUE_MAX)
    if fonte not in FONTES:
        return None, f"fonte desconhecida: {fonte!r}"
    return Leitura(codigos=tuple(codigos), iniciativa=alerta, confianca=confianca, duvida=duvida, porque=porque, r=r if fonte == IA else UMA, fonte=fonte), ""


def conferir(regua: ProjetoDePais, leitura: Leitura) -> str:
    """O que há de errado numa leitura já montada (vazio: nada): um código por campo, todos
    do YAML (ou "nd"), ``in`` 0 ou 1, confiança de 1 a 3, ``r`` de 0 a 3 e fonte conhecida."""
    if len(leitura.codigos) != len(regua.campos):
        return f"{len(leitura.codigos)} código(s) para {len(regua.campos)} campo(s)"
    for campo, valor in zip(regua.campos, leitura.codigos):
        if not isinstance(valor, str) or (valor != ND and campo.codigo(valor) is None):
            return f"código inválido em \"{campo.chave}\": {valor!r}"
    if leitura.iniciativa not in (0, 1) or leitura.confianca not in CONFIANCAS or leitura.r not in ESTADOS:
        return f"\"{regua.alerta_iniciativa.chave}\", \"cf\" ou \"r\" fora do formato"
    if leitura.fonte not in FONTES:
        return f"fonte desconhecida: {leitura.fonte!r}"
    return ""


def de_linha(regua: ProjetoDePais, linha: Any) -> Leitura | None:
    """A leitura de uma linha da guarda (``None`` se a linha não está no formato ou traz
    código que o YAML não tem mais)."""
    n = len(regua.campos)
    if not isinstance(linha, list) or len(linha) != n + 5:
        return None
    registro = dict(zip(regua.chaves, linha[:n]))
    registro.update({regua.alerta_iniciativa.chave: linha[n], "cf": linha[n + 1], "du": linha[n + 2], "pq": linha[n + 3], "r": linha[n + 4]})
    return validar(regua, registro)[0]


# ── a chave de cada proposição ───────────────────────────────────────────────

_TITULO = re.compile(r"^([A-Z]+) (\d+)/(\d{4})$")
_CHAVE_SENADO = re.compile(r"^([A-Z]+)-(\d+)-(\d{4})$")
_PARTES = re.compile(r"(\d+)")


def chave(p: Proposicao) -> str:
    """A chave da proposição na guarda: na Câmara, o id (como texto); no Senado,
    ``SIGLA-NUMERO-ANO`` (``PL-123-2025``)."""
    return str(p.id) if p.casa == "cd" else f"{p.sigla}-{p.numero}-{p.ano}"


def chave_da_materia(casa: str, titulo: str, prop_id: int | None = None) -> str:
    """A chave de uma matéria votada, pelo que a votação guarda: na Câmara, o id da
    proposição afetada; no Senado, o título (``"PEC 66/2023 (fase 2)"`` → ``PEC-66-2023``).
    Vazio quando não dá para saber."""
    if casa == "cd":
        return str(prop_id) if prop_id else ""
    achado = _TITULO.match(materias.titulo_da_materia(titulo))
    return f"{achado.group(1)}-{int(achado.group(2))}-{achado.group(3)}" if achado else ""


def ano_da_chave(casa: str, chave_: str) -> int | None:
    """O ano da proposição, quando a chave o traz (só a do Senado)."""
    achado = _CHAVE_SENADO.match(chave_) if casa == "sf" else None
    return int(achado.group(3)) if achado else None


def chave_valida(casa: str, chave_: str) -> bool:
    return bool(re.fullmatch(r"\d+", chave_)) if casa == "cd" else bool(_CHAVE_SENADO.match(chave_)) if casa == "sf" else False


def _ordem(chave_: str) -> list[Any]:
    """A ordem das chaves no arquivo: os números contam como números ("999" antes de
    "1000"; "PL-9-2025" antes de "PL-10-2025")."""
    return [int(parte) if k % 2 else parte for k, parte in enumerate(_PARTES.split(chave_))]


# ── a guarda ─────────────────────────────────────────────────────────────────

_ARQUIVO = re.compile(r"^(cd|sf)-(\d{4})\.json$")
NOVA, TROCADA, IGUAL = "nova", "trocada", "igual"


def pasta_da_guarda(pasta: Path) -> Path:
    """``data/congresso`` → ``data/congresso/leitura``."""
    return Path(pasta) / PASTA


def arquivo(pasta: Path, casa: str, ano: int) -> Path:
    return Path(pasta) / f"{casa}-{ano}.json"


def serializar(regua: ProjetoDePais, casa: str, ano: int, itens: Mapping[str, Leitura]) -> str:
    """O arquivo de uma Casa e um ano: uma proposição por linha, em ordem de chave (o git
    guarda só as linhas que mudaram)."""
    cabeca = json.dumps({"v": VERSAO, "regua": regua.leitura.versao, "casa": casa, "ano": ano}, ensure_ascii=False, separators=(",", ":"))[:-1]
    linhas = ",\n".join(
        f"{json.dumps(k)}:{json.dumps(leitura.linha(), ensure_ascii=False, separators=(',', ':'))}"
        for k, leitura in sorted(itens.items(), key=lambda par: _ordem(par[0]))
    )
    return f'{cabeca},"itens":{{\n{linhas}\n}}}}\n'


def _gravar(caminho: Path, conteudo: str) -> bool:
    """Grava se o conteúdo mudou (arquivo temporário + ``os.replace``). Devolve se gravou."""
    caminho = Path(caminho)
    try:
        if caminho.read_text(encoding="utf-8") == conteudo:
            return False
    except OSError:
        pass
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=caminho.parent, prefix=f".{caminho.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(conteudo)
        os.chmod(tmp, 0o644)
        os.replace(tmp, caminho)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


class Guarda:
    """As leituras de IA gravadas: ``(casa, chave)`` → :class:`Leitura`, repartidas por ano
    da proposição (um arquivo por Casa e ano). Uma proposição tem uma leitura só: registrar
    de novo substitui a anterior. A leitura de regras não entra: é refeita a cada execução."""

    def __init__(self, regua: ProjetoDePais) -> None:
        self.regua = regua
        self._anos: dict[tuple[str, int], dict[str, Leitura]] = {}
        self._onde: dict[tuple[str, str], int] = {}  # (casa, chave) → ano
        self.ignorados: list[str] = []  # arquivos de outra versão das instruções (ou do formato), ou ilegíveis
        self.invalidas = 0  # linhas fora do formato ou com código que o YAML não tem mais

    @classmethod
    def ler(cls, pasta: Path, regua: ProjetoDePais) -> Guarda:
        """Lê ``<pasta>/<casa>-<ano>.json``. O arquivo gravado com outra versão das
        instruções ao leitor não vale mais: fica de fora (e as proposições dele voltam a
        "falta ler"). Pasta ausente: guarda vazia."""
        guarda = cls(regua)
        pasta = Path(pasta)
        if not pasta.is_dir():
            return guarda
        for caminho in sorted(pasta.iterdir()):
            achado = _ARQUIVO.match(caminho.name)
            if not achado:
                continue
            casa, ano = achado.group(1), int(achado.group(2))
            try:
                dados = json.loads(caminho.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                dados = None
            if not isinstance(dados, dict) or dados.get("v") != VERSAO or dados.get("regua") != regua.leitura.versao or not isinstance(dados.get("itens"), dict):
                guarda.ignorados.append(caminho.name)
                continue
            for chave_, linha in dados["itens"].items():
                leitura = de_linha(regua, linha)
                if leitura is None or not chave_valida(casa, str(chave_)):
                    guarda.invalidas += 1
                    continue
                guarda.registrar(casa, ano, str(chave_), leitura)
        return guarda

    def __len__(self) -> int:
        return len(self._onde)

    def de(self, casa: str, chave_: str) -> Leitura | None:
        """A leitura da proposição de chave ``chave_`` (``None``: ainda não lida)."""
        ano = self._onde.get((casa, chave_))
        return self._anos[(casa, ano)].get(chave_) if ano is not None else None

    def da(self, p: Proposicao) -> Leitura | None:
        """A leitura de uma proposição (``None``: ainda não lida)."""
        return self.de(p.casa, chave(p))

    def ano_de(self, casa: str, chave_: str) -> int | None:
        return self._onde.get((casa, chave_))

    def da_casa(self, casa: str) -> dict[str, Leitura]:
        """Todas as leituras de uma Casa: chave → leitura."""
        return {k: leitura for (c, _ano), itens in self._anos.items() if c == casa for k, leitura in itens.items()}

    def registrar(self, casa: str, ano: int, chave_: str, leitura: Leitura) -> str:
        """Guarda a leitura (a nova substitui a antiga da mesma proposição). Devolve
        ``"nova"``, ``"trocada"`` ou ``"igual"``. Só leitura de IA: outra fonte é erro."""
        if leitura.fonte != IA:
            raise ValueError(f"só a leitura de IA vai à guarda (veio de \"{leitura.fonte}\": {casa} {chave_})")
        antes_em = self._onde.get((casa, chave_))
        antes = self._anos[(casa, antes_em)].get(chave_) if antes_em is not None else None
        if antes_em is not None and antes_em != ano:
            del self._anos[(casa, antes_em)][chave_]
        self._anos.setdefault((casa, ano), {})[chave_] = leitura
        self._onde[(casa, chave_)] = ano
        if antes is None:
            return NOVA
        return IGUAL if antes == leitura and antes_em == ano else TROCADA

    def gravar(self, pasta: Path) -> list[Path]:
        """Grava os arquivos por Casa e ano (só os que mudaram). Devolve os gravados."""
        gravados = []
        for (casa, ano), itens in sorted(self._anos.items()):
            caminho = arquivo(pasta, casa, ano)
            if not itens:
                if caminho.is_file() and caminho.name not in self.ignorados:  # as leituras do ano mudaram todas de arquivo
                    caminho.unlink()
                    gravados.append(caminho)
                continue
            if _gravar(caminho, serializar(self.regua, casa, ano, itens)):
                gravados.append(caminho)
        return gravados


# ── importar ─────────────────────────────────────────────────────────────────


@dataclass
class Importacao:
    """O que uma importação fez."""

    linhas: int = 0
    aceitas: int = 0
    novas: int = 0
    trocadas: int = 0
    iguais: int = 0
    recusadas: int = 0
    motivos: Counter[str] = field(default_factory=Counter)  # motivo da recusa → quantas linhas
    exemplos: list[str] = field(default_factory=list)  # as primeiras recusas: "arquivo:linha: motivo"
    gravados: list[Path] = field(default_factory=list)  # os arquivos da guarda que mudaram
    ignorados: list[str] = field(default_factory=list)  # arquivos da guarda de outra versão, que não valem mais


EXEMPLOS = 20  # recusas mostradas uma a uma
_ANO_NO_TITULO = re.compile(r"/(\d{4})\b")


def _ano_valido(valor: Any) -> int | None:
    ano = _inteiro(valor)
    return ano if ano is not None and 1900 < ano < 2200 else None


def anos_dos_mapas(caminhos: Iterable[Path]) -> dict[tuple[str, str], int]:
    """``(casa, chave)`` → ano da proposição, dos mapas dos lotes (``*.map.json``:
    ``[id opaco, casa, id, título]``; o ano vem do título, "PL 123/2025"). ``caminhos``:
    arquivos de mapa ou pastas (olha a pasta e as subpastas diretas). É por onde a
    importação descobre o ano das proposições da Câmara, cuja chave não o traz."""
    anos: dict[tuple[str, str], int] = {}
    arquivos: list[Path] = []
    for caminho in caminhos:
        caminho = Path(caminho)
        if caminho.is_dir():
            arquivos += sorted(caminho.glob("*.map.json")) + sorted(caminho.glob("*/*.map.json"))
        elif caminho.is_file():
            arquivos.append(caminho)
    for mapa in arquivos:
        try:
            linhas = json.loads(mapa.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("Leitura: mapa %s ilegível", mapa)
            continue
        for linha in linhas if isinstance(linhas, list) else []:
            if not isinstance(linha, list) or len(linha) < 4:
                continue
            achado = _ANO_NO_TITULO.search(str(linha[3]))
            if achado:
                anos[(str(linha[1]), str(linha[2]))] = int(achado.group(1))
    return anos


def anos_das_materias(pasta: Path) -> dict[tuple[str, str], int]:
    """``(casa, chave)`` → ano da proposição, das fichas das matérias votadas já publicadas
    (``pasta``: ``data/congresso``; ``materias-cd.json``: título → ficha com o ``id``). Só a
    Câmara precisa: a chave do Senado já traz o ano. Sem o arquivo, vazio."""
    try:
        fichas = json.loads((Path(pasta) / materias.ARQUIVO.format(casa="cd")).read_text(encoding="utf-8")).get("materias") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    anos: dict[tuple[str, str], int] = {}
    for titulo, ficha in fichas.items() if isinstance(fichas, dict) else ():
        achado = _TITULO.match(str(titulo))
        if achado and isinstance(ficha, dict) and _inteiro(ficha.get("id")):
            anos[("cd", str(_inteiro(ficha["id"])))] = int(achado.group(3))
    return anos


def importar(pasta: Path, regua: ProjetoDePais, arquivos: Iterable[Path], *, anos: Mapping[tuple[str, str], int] | None = None) -> Importacao:
    """Traz para a guarda (``pasta``: ``data/congresso/leitura``) as leituras de IA dos
    ``arquivos``, uma por linha::

        {"casa":"cd","id":"2491234","a":"fiscal","e":"grupo","m":"beneficio","ct":"gasto","is":"profissao","co":"neutro","in":1,"cf":3,"du":"","pq":"…","r":1}

    Cada linha é conferida contra os códigos do YAML (:func:`validar`); a inválida é recusada
    e contada, e as outras entram. A linha nova substitui a antiga da mesma proposição (no
    mesmo arquivo de entrada, vale a última). Os arquivos da guarda saem por Casa e ano, em
    ordem de chave, e só os que mudaram são regravados: importar duas vezes dá no mesmo.

    O ano da proposição decide o arquivo. No Senado está na chave. Na Câmara vem, nesta
    ordem: da própria linha (``"ano": 2025`` ou ``"t": "PL 123/2025"``, opcionais), de onde a
    proposição já está na guarda, ou de ``anos`` (:func:`anos_dos_mapas`, dos mapas dos
    lotes; :func:`anos_das_materias`, das matérias votadas já publicadas). Sem nenhum deles,
    a linha é recusada.
    """
    pasta = Path(pasta)
    guarda = Guarda.ler(pasta, regua)
    anos = anos or {}
    resultado = Importacao(ignorados=list(guarda.ignorados))
    feitas: dict[tuple[str, str], str] = {}  # o primeiro efeito de cada proposição nesta importação

    def recusar(onde: str, motivo: str, exemplo: str = "") -> None:
        resultado.recusadas += 1
        resultado.motivos[motivo] += 1
        if len(resultado.exemplos) < EXEMPLOS:
            resultado.exemplos.append(f"{onde}: {exemplo or motivo}")

    for caminho in arquivos:
        caminho = Path(caminho)
        with open(caminho, encoding="utf-8", errors="replace") as handle:
            for n, bruta in enumerate(handle, 1):
                bruta = bruta.strip()
                if not bruta:
                    continue
                resultado.linhas += 1
                onde = f"{caminho.name}:{n}"
                try:
                    registro = json.loads(bruta)
                except ValueError:
                    registro = None
                if not isinstance(registro, dict):
                    recusar(onde, "a linha não é um objeto JSON")
                    continue
                casa, chave_ = str(registro.get("casa") or ""), str(registro.get("id") if registro.get("id") is not None else "").strip()
                if not chave_valida(casa, chave_):
                    recusar(onde, "casa ou id fora do formato", f"casa ou id fora do formato: {casa!r}, {chave_!r}")
                    continue
                leitura, motivo = validar(regua, registro)
                if leitura is None:
                    recusar(onde, motivo.split(":")[0], motivo)
                    continue
                titulo = _ANO_NO_TITULO.search(str(registro.get("t") or ""))
                ano = (
                    ano_da_chave(casa, chave_)
                    or _ano_valido(registro.get("ano"))
                    or (int(titulo.group(1)) if titulo else None)
                    or guarda.ano_de(casa, chave_)
                    or anos.get((casa, chave_))
                )
                if not ano:
                    recusar(onde, "sem o ano da proposição (Câmara): falta \"ano\" ou \"t\" na linha, ou o mapa do lote")
                    continue
                efeito = guarda.registrar(casa, ano, chave_, leitura)
                resultado.aceitas += 1
                primeiro = feitas.setdefault((casa, chave_), efeito)
                if primeiro == IGUAL and efeito == TROCADA:  # repetida na entrada com outra leitura: no fim, trocou
                    feitas[(casa, chave_)] = TROCADA
    contagem = Counter(feitas.values())
    resultado.novas, resultado.trocadas, resultado.iguais = contagem[NOVA], contagem[TROCADA], contagem[IGUAL]
    resultado.gravados = guarda.gravar(pasta)
    return resultado


# ── o universo: o que precisa de leitura ─────────────────────────────────────


@dataclass
class Item:
    """Uma proposição de que as contas precisam, com o que o leitor recebe dela (e só isso:
    o lote não leva número, autor, partido nem origem)."""

    casa: str
    chave: str
    titulo: str  # "PL 123/2025": vai só no mapa do lote, nunca ao leitor
    tipo: str  # a sigla: é o que o leitor vê do título
    ano: int
    ementa: str
    explicacao: str = ""  # a ementa detalhada (Câmara) ou a explicação da ementa (Senado)
    palavras: str = ""  # as palavras-chave do registro
    temas: tuple[str, ...] = ()  # os macrotemas (ids de config/congresso.yaml)
    universos: set[str] = field(default_factory=set)  # por que precisa de leitura: perfil, espera, voto


def _enxuto(valor: str, limite: int) -> str:
    return " ".join((valor or "").split())[:limite]


def item_da_proposicao(p: Proposicao) -> Item:
    """O que o leitor recebe de uma proposição: a ementa inteira (até 700 caracteres), a
    ementa detalhada quando diz outra coisa (até 300), até 14 palavras-chave e até 2 temas."""
    ementa = _enxuto(p.inteira or p.ementa, EMENTA_MAX)
    explicacao = _enxuto(p.explicacao, EXPLICACAO_MAX)
    return Item(
        casa=p.casa,
        chave=chave(p),
        titulo=p.titulo,
        tipo=p.sigla,
        ano=p.ano,
        ementa=ementa,
        explicacao=explicacao if explicacao and explicacao != ementa else "",
        palavras=", ".join(projetos.termos(p.chaves)[:PALAVRAS])[:PALAVRAS_MAX],
        temas=tuple(p.temas[:TEMAS]),
    )


def _item_da_votada(casa: str, chave_: str, titulo: str, v: base.Votacao, ficha: Mapping[str, Any] | None) -> Item | None:
    """A matéria votada que não está entre as proposições carregadas: vale o que a ficha da
    matéria traz (``materias-<casa>.json``) ou, sem ela, a ementa do registro da votação."""
    achado = _TITULO.match(titulo)
    if not achado:
        return None
    ficha = ficha or {}
    palavras = ficha.get("kw") or []
    ementa = _enxuto(str(ficha.get("e") or v.ementa or ""), EMENTA_MAX)
    explicacao = _enxuto(str(ficha.get("ex") or ""), EXPLICACAO_MAX)
    return Item(
        casa=casa,
        chave=chave_,
        titulo=titulo,
        tipo=achado.group(1),
        ano=int(achado.group(3)),
        ementa=ementa,
        explicacao=explicacao if explicacao and explicacao != ementa else "",
        palavras=", ".join(str(t) for t in (palavras if isinstance(palavras, list) else [])[:PALAVRAS])[:PALAVRAS_MAX],
        temas=tuple((ficha.get("tm") or v.temas or [])[:TEMAS]),
    )


# A mudança que a outra Casa fez no texto ("Rejeitada a Emenda do Senado Federal nº 1", "Aprovado o
# Substitutivo do Senado Federal ao Projeto…", "Votação do Substitutivo da Câmara dos Deputados…"),
# no começo da descrição oficial; o Senado também a anota no título ("PLP 22/2025 (Substitutivo-CD)").
_DA_OUTRA_CASA = re.compile(r"\b(?:emendas?|substitutivo) d[oa] (?:senado federal|camara dos deputados)\b")
_ANOTADA_DA_OUTRA_CASA = re.compile(r"\((?:substitutivo|emendas?)-(?:cd|sf)\)\s*$", re.IGNORECASE)
# A descrição que não diz o que foi votado: "Votação nominal.", "Votação nominal, em 2º turno, ressalvado o destaque."
_SO_VOTACAO = re.compile(r"^(?:votacao(?: \w+)?)?(?: ?em (?:1o|2o|primeiro|segundo) turno)?(?: ?ressalvad[oa]s? (?:[oa]s? )?destaques?)?$")
COMECO_DA_DESCRICAO = 12  # palavras da descrição em que se procura o que foi votado
EMENTA_NA_DESCRICAO = 0.3  # parte das palavras da descrição que têm de estar na ementa para a descrição ser "a ementa da matéria"


def do_texto_principal(v: base.Votacao) -> bool:
    """Se a votação é a do **texto principal da própria matéria**: nominal, aberta, no
    Plenário, de mérito, e o Sim quer dizer aprovar a matéria (é o que dá sentido ao voto de
    cada um). Não é:

    - a da redação final (o texto já foi aprovado);
    - a da mudança feita pela **outra Casa** (as emendas ou o substitutivo do Senado, na
      Câmara; os da Câmara, no Senado): o Sim aceita a mudança e o Não fica com o texto da
      própria Casa; nenhum dos dois é voto contra a matéria;
    - no registro que não diz o que foi votado (parte das votações do Senado até 2021, que a
      base trata como de mérito por falta de outra pista), a que descreve **outra coisa** que
      não a matéria ("Idade mínima para mulheres.": é um destaque). Vale a que não descreve
      nada ("Votação nominal.", "Votação em 2º turno") e a que repete a ementa: ao menos
      ``EMENTA_NA_DESCRICAO`` das palavras da descrição estão na ementa da matéria.

    A última regra é uma aproximação: tira também o texto principal descrito com palavras
    que a ementa não usa. Erra para o lado de deixar a votação de fora."""
    if not (v.plenario and v.tipo == base.MERITO and v.prop) or materias.secreta(v) or materias.redacao_final(v.descricao, v.abertura):
        return False
    descricao = text.normalize(v.descricao)
    if _DA_OUTRA_CASA.search(" ".join(descricao.split()[:COMECO_DA_DESCRICAO])) or _ANOTADA_DA_OUTRA_CASA.search(v.prop):
        return False
    if base.tipo_da_votacao(v.descricao, padrao=base.OUTRO) == base.MERITO or _SO_VOTACAO.match(descricao):
        return True
    palavras = text.tokens(v.descricao)
    return bool(palavras) and len(palavras & text.tokens(v.ementa)) >= EMENTA_NA_DESCRICAO * len(palavras)


def votacoes_do_texto(anos: Iterable[AnoVotos]) -> Iterable[base.Votacao]:
    """As votações nominais abertas do texto principal no Plenário, nos anos dados
    (:func:`do_texto_principal`)."""
    for ano in anos:
        for v in ano.votacoes:
            if do_texto_principal(v):
                yield v


def universo(
    regua: ProjetoDePais,
    casa: str,
    *,
    tramitam: Iterable[Proposicao] = (),
    autorais: Iterable[Proposicao] = (),
    anos: Iterable[AnoVotos] = (),
    inicio: str = "",
    fichas: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Item]:
    """As proposições de uma Casa que precisam de leitura: chave → :class:`Item`, com os
    motivos em ``universos``. Só os tipos do YAML (``leitura.tipos``: PL, PLP, PEC e os
    nomes antigos PLS e PLC) e, nas votadas, também ``leitura.tipos_votadas`` (MPV, PLV).

    1. ``perfil``: de ``autorais``, as de autoria principal de um parlamentar (quem assina
       primeiro) apresentadas de ``inicio`` em diante (o começo da legislatura). Na Câmara,
       ``autorais`` é a lista de todas as proposições; no Senado, as de cada senador.
    2. ``espera``: de ``tramitam``, as que estão tramitando. A apensada segue a principal e
       não entra no estoque.
    3. ``voto``: as matérias com votação nominal do texto principal no Plenário em ``anos``
       (os anos da base: desde 2019). ``fichas``: título → ficha da matéria
       (``materias-<casa>.json``), para o texto das que não estão nas listas de cima.

    Fica de fora o que não é de nenhum dos três: a proposição encerrada de quem não é
    parlamentar, a de antes da legislatura que já saiu de tramitação, a votada só em
    destaque, requerimento ou votação secreta, e a votada sem id no registro (Câmara).
    """
    tipos = set(regua.leitura.tipos)
    itens: dict[str, Item] = {}

    def entra(p: Proposicao, motivo: str) -> None:
        if p.casa != casa or p.sigla not in tipos:
            return
        item = itens.get(chave(p))
        if item is None:
            item = itens[chave(p)] = item_da_proposicao(p)
        item.universos.add(motivo)

    conhecidas: dict[str, Proposicao] = {}
    for p in autorais:
        conhecidas.setdefault(chave(p), p)
        if p.primeiro is not None and inicio and p.apresentada >= inicio:
            entra(p, PERFIL)
    for p in tramitam:
        conhecidas.setdefault(chave(p), p)
        if p.estado == projetos.TRAMITANDO:
            entra(p, ESPERA)
    votadas = tipos | set(regua.leitura.tipos_votadas)
    for v in votacoes_do_texto(anos):
        titulo = materias.titulo_da_materia(v.prop)
        achado = _TITULO.match(titulo)
        chave_ = chave_da_materia(casa, titulo, v.prop_id)
        if not achado or achado.group(1) not in votadas or not chave_:
            continue
        item = itens.get(chave_)
        if item is None:
            p = conhecidas.get(chave_)
            item = item_da_proposicao(p) if p is not None and p.casa == casa else _item_da_votada(casa, chave_, titulo, v, (fichas or {}).get(titulo))
            if item is None:
                continue
            itens[chave_] = item
        item.universos.add(VOTO)
    return itens


def faltam(itens: Mapping[str, Item], guarda: Guarda) -> list[Item]:
    """Do universo, as proposições sem leitura de IA gravada (com a versão atual das
    instruções), em ordem de chave. A que a IA já leu e ficou sem leitura ("nd" ou instável)
    não volta. Nem todas precisam de IA: ver :func:`para_ia`."""
    return sorted((i for i in itens.values() if guarda.de(i.casa, i.chave) is None), key=lambda i: (i.casa, _ordem(i.chave)))


def para_segunda(regua: ProjetoDePais, itens: Mapping[str, Item], guarda: Guarda) -> list[tuple[Item, tuple[str, ...]]]:
    """Do universo, as proposições cuja leitura de IA espera confirmação: pede uma segunda,
    independente (:func:`pede_segunda`), com os motivos, em ordem de chave."""
    saida = []
    for item in sorted(itens.values(), key=lambda i: (i.casa, _ordem(i.chave))):
        leitura = guarda.de(item.casa, item.chave)
        motivos = pede_segunda(regua, leitura, votada=VOTO in item.universos) if leitura is not None else ()
        if motivos:
            saida.append((item, motivos))
    return saida


def contar(itens: Iterable[Item]) -> dict[str, dict[str, int]]:
    """Quantas proposições por Casa: o total e em cada universo (uma proposição pode estar em
    mais de um), e as sem ementa no registro."""
    saida: dict[str, dict[str, int]] = {}
    for item in itens:
        conta = saida.setdefault(item.casa, {"total": 0, **{u: 0 for u in UNIVERSOS}, "sem_ementa": 0})
        conta["total"] += 1
        for motivo in item.universos:
            conta[motivo] += 1
        if not item.ementa:
            conta["sem_ementa"] += 1
    return saida


# ── as duas fontes: a IA (a guarda) e o leitor de regras ─────────────────────

# O leitor de regras, como as contas o chamam: recebe o que o leitor de IA recebe de uma
# proposição (:class:`Item`: tipo, ementa, ementa detalhada, palavras-chave e temas; sem
# número, autor, partido nem origem) e devolve a :class:`Leitura` dele (um código por campo
# da régua, na ordem do YAML, "nd" onde as regras não decidem; ``iniciativa``; ``confianca``;
# em ``porque``, a regra que decidiu) ou ``None`` (não leu). Quem o constrói, já com a régua
# e as regras carregadas, é ``leitor_regras.carregar(regua, root)``.
LeitorDeRegras = Callable[["Item"], "Leitura | None"]


def sem_leitor(item: Item) -> Leitura | None:
    """O leitor de regras que não lê nada: toda proposição que a IA não leu fica sem leitura."""
    return None


def leitura_de(regua: ProjetoDePais, proposicao: Item | Proposicao, guarda: Guarda | None = None, leitor: LeitorDeRegras | None = None) -> Leitura | None:
    """A leitura que vale para uma proposição (um :class:`Item` ou uma ``Proposicao``):

    1. a de IA, da guarda, se houver. Vale sempre sobre a de regras, mesmo quando deixa a
       proposição sem leitura ("nd" em campo de peso, ou leitura instável): se a IA disse que
       a ementa não permite dizer, a regra não diz por ela;
    2. senão, a do leitor de regras (``leitor``), com ``fonte`` "regra" e ``r`` = 1 (a leitura
       de regras é uma leitura só e nunca confirma, diga o leitor o que disser);
    3. senão, ``None``: ninguém leu.

    Código que o YAML não tem, vindo do leitor de regras, é erro de quem escreveu as regras
    (``ValueError``), não uma proposição sem leitura.
    """
    chave_ = proposicao.chave if isinstance(proposicao, Item) else chave(proposicao)
    da_ia = guarda.de(proposicao.casa, chave_) if guarda is not None else None
    if da_ia is not None:
        return da_ia
    if leitor is None:
        return None
    por_regra = leitor(proposicao if isinstance(proposicao, Item) else item_da_proposicao(proposicao))
    if por_regra is None:
        return None
    if (por_regra.fonte, por_regra.r) != (REGRA, UMA):
        por_regra = replace(por_regra, fonte=REGRA, r=UMA)
    motivo = conferir(regua, por_regra)
    if motivo:
        raise ValueError(f"leitor de regras: {motivo} ({proposicao.casa} {chave_})")
    return por_regra


@dataclass
class Leitor:
    """As duas fontes juntas, como as contas as usam: a guarda (IA) e o leitor de regras."""

    regua: ProjetoDePais
    guarda: Guarda
    regras: LeitorDeRegras | None = None

    def de(self, proposicao: Item | Proposicao) -> Leitura | None:
        """A leitura que vale para a proposição (:func:`leitura_de`)."""
        return leitura_de(self.regua, proposicao, self.guarda, self.regras)

    def avaliar(self, proposicao: Item | Proposicao, *, origem: str = "") -> Avaliacao:
        """Da proposição aos números (:func:`avaliar`). O tipo e a origem, que só servem ao
        alerta de iniciativa, vêm da própria ``Proposicao``; de um :class:`Item`, a origem
        vem em ``origem`` (o lote cego não a leva)."""
        tipo = proposicao.tipo if isinstance(proposicao, Item) else proposicao.sigla
        return avaliar(self.regua, self.de(proposicao), tipo=tipo, origem=origem or getattr(proposicao, "origem", ""))


def abrir(root: Path, pasta: Path, regras: Regras, *, avisos: list[str] | None = None) -> Leitor | None:
    """O :class:`Leitor` de uma atualização: a guarda de ``<pasta>/leitura`` (``pasta``:
    ``data/congresso``) e o leitor de regras de ``config/`` (``root``: a raiz do
    repositório). ``None`` sem o bloco ``projeto_de_pais`` no config: a visão não existe.
    Se o leitor de regras não carrega, vira aviso e só a IA lê."""
    regua = regras.projeto_de_pais
    if regua is None:
        return None
    from . import leitor_regras  # import tardio: o leitor de regras usa este módulo

    try:
        por_regra: LeitorDeRegras | None = leitor_regras.carregar(regua, Path(root))
    except Exception as exc:  # noqa: BLE001 — acessório: sem as regras, fica a leitura de IA
        por_regra = None
        if avisos is not None:
            avisos.append(f"Leitura: leitor de regras indisponível ({exc})")
        log.warning("Leitura: leitor de regras indisponível (%s)", exc)
    return Leitor(regua=regua, guarda=Guarda.ler(pasta_da_guarda(pasta), regua), regras=por_regra)


def candidata(regua: ProjetoDePais, leitura: Leitura | None) -> bool:
    """A leitura põe a proposição entre as candidatas ao topo: peso no corte das candidatas
    (``leitura.candidatas_peso``; sem ele, o peso mínimo da segunda leitura) ou acima, em
    alavanca de nível em que existe faixa que pede confirmação (o núcleo)."""
    valor = peso(regua, leitura) if leitura is not None else None
    if valor is None or valor < regua.peso_das_candidatas:
        return False
    alavanca = regua.alavanca.codigo(leitura.codigos[regua.chaves.index(regua.alavanca.chave)])
    return alavanca is not None and any(pede_confirmacao(regua, f) and f.so_nivel in ("", alavanca.nivel) for f in regua.faixas)


def pede_ia(regua: ProjetoDePais, por_regra: Leitura | None, *, votada: bool = False) -> tuple[str, ...]:
    """Por que uma proposição que a IA ainda não leu precisa da leitura dela (vazio se não
    precisa): ``votada`` (matéria com votação nominal do texto principal: o voto fiscal só
    usa leitura de IA) e ``candidata`` (o leitor de regras a põe entre as candidatas ao
    topo: :func:`candidata`). O resto fica com a leitura de regras."""
    motivos = [VOTADA] if votada else []
    if candidata(regua, por_regra):
        motivos.append(CANDIDATA)
    return tuple(motivos)


def para_ia(regua: ProjetoDePais, itens: Mapping[str, Item], guarda: Guarda, leitor: LeitorDeRegras | None = None) -> list[tuple[Item, tuple[str, ...]]]:
    """Do universo, as proposições sem leitura de IA que precisam de uma (:func:`pede_ia`),
    com os motivos, em ordem de chave."""
    saida = []
    for item in faltam(itens, guarda):
        motivos = pede_ia(regua, leitura_de(regua, item, None, leitor), votada=VOTO in item.universos)
        if motivos:
            saida.append((item, motivos))
    return saida


def cobertura(regua: ProjetoDePais, leituras: Iterable[Leitura | None]) -> dict[str, int]:
    """Quantas proposições têm leitura, e de quem: ``total``; ``lidos`` (com peso: entram nas
    contas), dos quais ``regra`` (lidos pelo leitor de regras) e ``ia`` (lidos por IA), e,
    destes, ``confirmados`` (duas leituras de IA na mesma faixa); ``sem_leitura`` (ninguém
    leu, "nd" em campo de peso ou leitura instável)."""
    conta = {"total": 0, "lidos": 0, REGRA: 0, IA: 0, "confirmados": 0, "sem_leitura": 0}
    for leitura in leituras:
        conta["total"] += 1
        if leitura is None or peso(regua, leitura) is None or (leitura.fonte == IA and leitura.r == INSTAVEL):
            conta["sem_leitura"] += 1
            continue
        conta["lidos"] += 1
        conta[REGRA if leitura.fonte != IA else IA] += 1
        conta["confirmados"] += leitura.confirmada
    return conta


# ── os lotes cegos ───────────────────────────────────────────────────────────


def bloco(pid: str, item: Item, nomes_dos_temas: Mapping[str, str]) -> str:
    """O que o leitor recebe de uma proposição: o identificador opaco, o tipo, a ementa
    (``E:``) e, quando há, a ementa detalhada (``X:``), as palavras-chave (``K:``) e a área
    temática (``T:``). Sem número, autor, partido nem origem."""
    partes = [f"[{pid}] {item.tipo}", f"E: {' '.join(item.ementa.split())}"]
    if item.explicacao:
        partes.append(f"X: {' '.join(item.explicacao.split())}")
    if item.palavras:
        partes.append(f"K: {' '.join(item.palavras.split())}")
    tema = ", ".join(nomes_dos_temas.get(t, t) for t in item.temas)
    if tema:
        partes.append(f"T: {tema}")
    return "\n".join(partes)


def embaralhar(itens: Iterable[Item], semente: int = SEMENTE) -> list[Item]:
    """Os itens numa ordem sorteada com semente fixa (cada lote com um pouco de tudo; a
    mesma lista sai sempre na mesma ordem)."""
    lista = sorted(itens, key=lambda i: (i.casa, _ordem(i.chave)))
    random.Random(semente).shuffle(lista)
    return lista


def gravar_lotes(
    pasta: Path,
    itens: Iterable[Item],
    nomes_dos_temas: Mapping[str, str],
    *,
    tamanho: int = LOTE,
    nome: str = "lote",
    prefixo: str = "p",
    semente: int = SEMENTE,
    leva: Callable[[Item], int] | None = None,
) -> int:
    """Grava os lotes cegos em ``pasta`` e devolve quantos: ``<nome>-NNNN.txt`` (o que o
    leitor lê, um bloco por proposição: ver :func:`bloco`) e ``<nome>-NNNN.map.json`` (a
    volta: ``[id opaco, casa, id, título]`` por linha do lote; o id da Câmara como número).
    Os lotes ``<nome>-*`` de uma exportação anterior são apagados antes: o que está na pasta
    é sempre o que falta agora. A proposição sem ementa não entra (não há o que ler).
    ``leva``: a leva de cada proposição (0, 1, …); as levas saem em ordem, cada uma
    embaralhada por dentro, para que os primeiros lotes sejam os que mais importam."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    antigo = re.compile(rf"^{re.escape(nome)}-\d{{4}}\.(?:txt|map\.json)$")
    for caminho in pasta.iterdir():
        if antigo.match(caminho.name):
            caminho.unlink()
    lista = embaralhar((i for i in itens if i.ementa), semente)
    if leva is not None:
        lista.sort(key=leva)  # a ordenação é estável: dentro da leva, fica o sorteio
    tamanho = max(1, tamanho)
    lotes = [lista[k : k + tamanho] for k in range(0, len(lista), tamanho)]
    for n, lote in enumerate(lotes, 1):
        ids = [f"{prefixo}{k:04d}" for k in range(1, len(lote) + 1)]
        _gravar(pasta / f"{nome}-{n:04d}.txt", "\n\n".join(bloco(pid, item, nomes_dos_temas) for pid, item in zip(ids, lote)) + "\n")
        mapa = [[pid, item.casa, int(item.chave) if item.casa == "cd" else item.chave, item.titulo] for pid, item in zip(ids, lote)]
        _gravar(pasta / f"{nome}-{n:04d}.map.json", json.dumps(mapa, ensure_ascii=False))
    return len(lotes)


# ── as instruções ao leitor ──────────────────────────────────────────────────

_SECAO = re.compile(r"^([a-z]+) = ")
_CODIGO = re.compile(r"^  ([a-z_]+)(?: = |$)")


def codigos_das_instrucoes(texto: str, chaves: Iterable[str]) -> dict[str, list[str]]:
    """Os códigos que as instruções ao leitor (``config/leitura_projetos.txt``) listam em
    cada campo, na ordem em que aparecem (com o "nd"). A seção de um campo começa na linha
    ``<chave> = …`` e os códigos vêm um por linha (``  codigo = definição``), antes do
    "Desempate". Serve para conferir que as instruções e o YAML dizem o mesmo."""
    chaves = set(chaves)
    saida: dict[str, list[str]] = {}
    atual: str | None = None
    for linha in texto.splitlines():
        secao = _SECAO.match(linha)
        if secao:  # vale a primeira seção de cada campo (a legenda da saída repete "is = isonomia" no começo de uma linha)
            atual = secao.group(1) if secao.group(1) in chaves and secao.group(1) not in saida else None
            if atual:
                saida[atual] = []
            continue
        if linha and not linha.startswith(" "):
            atual = None
            continue
        if atual is None:
            continue
        if linha.strip().startswith("Desempate"):
            atual = None
            continue
        codigo = _CODIGO.match(linha)
        if codigo:
            saida[atual].append(codigo.group(1))
    return saida


# ── o registro da leitura diária ─────────────────────────────────────────────
#
# ``data/congresso/leitura/diario.json``: ``{"v":1,"dias":[…]}``, uma linha por dia em que a
# leitura diária rodou (``python -m qijournal congresso ler``: quem escreve é
# :mod:`qijournal.congresso.ler`), da mais antiga à mais nova. Aqui só se lê e se resume.

DIARIO_VERSAO = 1
# O que se soma de um dia para o outro (e entre as rodadas do mesmo dia).
SOMAS_DO_DIARIO = (
    "rodadas", "primeiras", "segundas", "terceiras", "lotes", "descartados", "falhas", "confirmadas", "decididas", "instaveis", "invalidas", "mesmo_editor", "chamadas",
)  # fmt: skip


def ler_diario(pasta: Path) -> list[dict[str, Any]]:
    """Os dias do registro da leitura diária (``<pasta>/diario.json``, a pasta da guarda), do
    mais antigo ao mais novo. Arquivo ausente, ilegível ou de outro formato: lista vazia."""
    try:
        dados = json.loads((Path(pasta) / DIARIO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(dados, dict) or dados.get("v") != DIARIO_VERSAO or not isinstance(dados.get("dias"), list):
        return []
    dias = [dict(d) for d in dados["dias"] if isinstance(d, dict) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d.get("data") or ""))]
    return sorted(dias, key=lambda d: d["data"])


def _par(valor: Any) -> list[int]:
    """``[certos, de quantos]`` dos controles (``[0, 0]`` se a linha não o traz)."""
    if isinstance(valor, list) and len(valor) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in valor):
        return [valor[0], valor[1]]
    return [0, 0]


def resumo_do_diario(pasta: Path) -> dict[str, Any] | None:
    """O resumo do registro da leitura diária, como vai a ``meta.json`` →
    ``leitura_projetos.diaria`` (``None`` sem registro): o último dia em que a leitura rodou
    (``data``, ``quando``, quantas primeiras e segundas leu, os lotes e os descartados, os
    controles certos de quantos, as confirmadas, decididas e instáveis, o que ficou pendente
    e os editores que responderam) e, em ``periodo``, a soma dos dias guardados (``dias``,
    ``desde``, ``leu_em``: o último dia em que alguma proposição foi lida)."""
    dias = ler_diario(pasta)
    if not dias:
        return None
    ultimo = {k: v for k, v in dias[-1].items() if k != "avisos"}
    periodo: dict[str, Any] = {"dias": len(dias), "desde": dias[0]["data"], "leu_em": None}
    for chave_ in SOMAS_DO_DIARIO:
        periodo[chave_] = sum(v for d in dias if isinstance(v := d.get(chave_), int) and not isinstance(v, bool))
    periodo["controles"] = [sum(_par(d.get("controles"))[k] for d in dias) for k in (0, 1)]
    for dia in dias:
        if any(isinstance(dia.get(k), int) and dia[k] > 0 for k in ("primeiras", "segundas")):
            periodo["leu_em"] = dia["data"]
    return {**ultimo, "controles": _par(ultimo.get("controles")), "periodo": periodo}


# ── carregar o universo fora da atualização ──────────────────────────────────


def coletar(
    regras: Regras, pasta: Path, coletor: Coletor, hoje: str, *, projetos_desde: int, validade_h: float = 20.0, avisos: list[str] | None = None
) -> dict[str, dict[str, Item]]:
    """O universo das duas Casas (``casa`` → chave → :class:`Item`), com as proposições
    carregadas como a atualização as carrega (as mesmas funções e o mesmo cache), sem
    refazer o resto das contas. ``pasta``: ``data/congresso``, de onde vêm a base de votos
    (as matérias votadas), os senadores em exercício (``resumo.json``) e as fichas das
    matérias. Uma fonte fora do ar vira aviso e a parte dela fica de fora."""
    from . import dados  # import tardio: dados.py é quem monta tudo e passa a usar este módulo

    regua = regras.projeto_de_pais
    if regua is None:
        return {}
    avisos = avisos if avisos is not None else []
    pasta = Path(pasta)
    legislatura = regras.legislatura_atual(hoje)
    inicio = legislatura.inicio if legislatura else ""
    corrente = int(hoje[:4])
    props_cd: list[Proposicao] = []
    try:
        props_cd = projetos.camara_proposicoes(
            coletor, range(projetos_desde, corrente + 1), temas=temas.TemasCamara(coletor, regras), validade_h=validade_h, ano_corrente=corrente
        )
    except Exception as exc:  # noqa: BLE001 — sem a fonte, a Casa sai sem esta parte
        avisos.append(f"Câmara: proposições indisponíveis ({exc})")
    props_sf: list[Proposicao] = []
    try:
        props_sf = projetos.senado_tramitando(coletor, validade_h=validade_h)
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"Senado: matérias em tramitação indisponíveis ({exc})")
    autorais_sf: list[Proposicao] = []
    resumo = dados.ler_json(pasta / dados.RESUMO) or {}
    ativos = {
        int(p["k"][3:]): str(p.get("nome") or "")
        for p in resumo.get("pessoas") or []
        if isinstance(p, dict) and str(p.get("k") or "").startswith("sf-") and p.get("ex") and str(p["k"])[3:].isdigit()
    }
    if not ativos:
        avisos.append("Senado: sem a lista de senadores em exercício (resumo.json); as proposições de autoria ficam de fora")
    else:
        try:
            autorais_sf = [p for lista in projetos.senado_de_autoria(coletor, ativos, validade_h=validade_h).values() for p in lista]
        except Exception as exc:  # noqa: BLE001
            avisos.append(f"Senado: autoria indisponível ({exc})")
    saida = {}
    for casa, tramitam, autorais in (("cd", props_cd, props_cd), ("sf", props_sf, autorais_sf)):
        fichas = (dados.ler_json(pasta / materias.ARQUIVO.format(casa=casa)) or {}).get("materias") or {}
        votos = base.carregar(pasta / dados.BASE, casa, regras.anos())
        saida[casa] = universo(regua, casa, tramitam=tramitam, autorais=autorais, anos=votos, inicio=inicio, fichas=fichas)
    return saida
