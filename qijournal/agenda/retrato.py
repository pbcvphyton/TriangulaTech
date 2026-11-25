"""O retrato da Agenda da semana: o que a edição grava (``Edition.agenda``) e a tela mostra.

O retrato é montado uma vez por edição (:mod:`.montar`) e traz os TEXTOS DE TELA PRONTOS: quem
desenha a home e a página ``edicoes/agenda.html`` só lê campos, não decide nada. Todo campo tem
padrão (o ambiente Jinja do projeto é estrito: campo que a tela lê tem de existir no objeto).

Ida e volta: :meth:`Retrato.para_dict` devolve um dicionário só com tipos de JSON, em ordem
fixa e sem os campos que estão no padrão (o JSON da edição fica menor); :func:`ler_retrato` faz
o caminho de volta e devolve ``None`` para qualquer formato inesperado (não é dicionário, versão
desconhecida, campo de tipo errado), que quem lê trata como "sem agenda".

Forma (campo a campo, nos docstrings de cada classe): :class:`Retrato` tem os dias da janela
(:class:`DiaTela`, com as linhas :class:`LinhaTela` e os itens :class:`ItemTela`), as frases
(:class:`Frase`), a faixa "Já decidido esta semana", o pé (:class:`Pe`), o rótulo "na agenda" de
cada matéria ligada (:class:`MateriaNaAgenda`) e a visão completa da página (:class:`Pagina`).
"""

from __future__ import annotations

import dataclasses
import typing
from dataclasses import dataclass, field
from typing import Any, Mapping

VERSAO = 1  # outro número vale como "sem agenda" para quem lê


@dataclass(frozen=True)
class ItemTela:
    """Um item de pauta pronto para a tela.

    ``nome`` (até 90 caracteres) e ``nome_curto`` (até 60, celular) dizem o assunto e vêm antes
    do número; item sem nome não é nomeado na home. ``identificador_tela`` é o identificador com o
    apelido aprovado entre parênteses, quando há ("PLP 299/2026 (pacote fiscal)"), e ``link`` é a
    página oficial. ``area`` é a palavra de área ou vazio. ``situacao`` é a frase em português
    claro (home) e ``termo`` o termo oficial (página completa). ``resultado`` só existe em item já
    deliberado. ``nota`` e ``conta`` são a régua ("por que está aqui"). ``midia`` é o número de
    veículos que citam o item (0 = calado), com os textos prontos da home (``midia_texto``) e da
    página (``midia_pagina``); ``materia`` é o id da matéria da edição do dia ligada, ou vazio
    (aí o link da marca vai para a Pesquisa, com o identificador)."""

    identificador: str = ""
    identificador_tela: str = ""
    link: str = ""
    nome: str = ""
    nome_curto: str = ""
    area: str = ""
    situacao: str = ""
    termo: str = ""
    resultado: str = ""
    nota: int = 0
    conta: str = ""
    midia: int = 0
    midia_texto: str = ""
    midia_pagina: str = ""
    materia: str = ""
    relator: str = ""  # só na página completa: "Fulano (PT-SP)"; ministro vem só com o nome


@dataclass(frozen=True)
class Contagem:
    """Um número com o seu rótulo, só para a página completa ("recursos internos em lista: 185")."""

    rotulo: str = ""
    n: int = 0
    texto: str = ""


@dataclass(frozen=True)
class LinhaTela:
    """Uma linha de pauta: uma sessão, um julgamento, uma reunião de comissão ou um prazo.

    ``id`` é o da sessão (serve de âncora). ``data`` é ISO e ``dia`` o rótulo curto ("qua 25");
    ``hora`` é "14h", "13h55" ou vazio (a fonte não informa). ``instituicao`` e ``orgao`` vêm por
    extenso e ``cabeca`` já os junta ("Senado · Plenário"). ``situacao`` é o texto da linha
    inteira ("em andamento", "pauta ainda não publicada", "Medida provisória perde a validade",
    "como pautado em 23/11") ou vazio. ``itens`` são os nomeados (na home, até o teto por linha);
    ``mais`` e ``mais_texto`` ("mais 10", ou "10 itens pautados" quando nenhum foi nomeado) contam
    o resto; ``mais_celular*`` é a mesma conta com um só item nomeado. ``piso`` marca a linha que
    está na home pelo piso da instituição; ``aberta_no_celular`` as que abrem no celular (hoje e
    amanhã). ``fora`` e ``abaixo_do_corte`` só existem na página completa."""

    id: str = ""
    data: str = ""
    dia: str = ""
    hora: str = ""
    instituicao: str = ""
    orgao: str = ""
    cabeca: str = ""
    link: str = ""
    situacao: str = ""
    itens: tuple[ItemTela, ...] = ()
    mais: int = 0
    mais_texto: str = ""
    mais_celular: int = 0
    mais_celular_texto: str = ""
    nota: int = 0
    passado: bool = False
    piso: bool = False
    aberta_no_celular: bool = False
    fora: tuple[Contagem, ...] = ()
    abaixo_do_corte: int = 0


@dataclass(frozen=True)
class DiaTela:
    """Um dia da agenda. ``rotulo`` é "qua 25" e ``rotulo_longo`` "quarta-feira, 25 de novembro".
    Dia da janela sem linha traz ``vazio`` ("nada de peso pautado"). ``principal`` marca os dois
    primeiros dias da janela (a coluna larga "hoje e amanhã"; "HOJE" e "AMANHÃ" são postos pelo
    navegador, a partir de ``data``)."""

    data: str = ""
    rotulo: str = ""
    rotulo_longo: str = ""
    linhas: tuple[LinhaTela, ...] = ()
    vazio: str = ""
    principal: bool = False
    passado: bool = False


@dataclass(frozen=True)
class Frase:
    """Uma frase da agenda, por instituição: a fonte velha ou ausente (``tipo`` "fonte"), o piso
    sem item na janela com o próximo evento ("proximo"), a pauta do Plenário da Câmara ainda não
    publicada e a fila ("camara"), o recesso ("recesso"). ``texto`` é a frase inteira; ``itens``
    são os itens que ela nomeia (a fila da Câmara, o próximo evento), para a tela pôr os links;
    ``link`` é o endereço oficial e ``link_rotulo`` o texto do link."""

    tipo: str = ""
    instituicao: str = ""
    texto: str = ""
    itens: tuple[ItemTela, ...] = ()
    link: str = ""
    link_rotulo: str = ""


@dataclass(frozen=True)
class Pe:
    """O pé do bloco. ``texto`` é a frase pronta ("Conferido em 24/11. A pauta pode mudar.");
    ``conferido`` é a data curta e ``conferido_em`` a data ISO. ``fontes`` só vem preenchido
    quando alguma fonte está velha: uma frase por fonte ("STF: dados de 20/11")."""

    texto: str = ""
    conferido: str = ""
    conferido_em: str = ""
    fontes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MateriaNaAgenda:
    """O rótulo que a chamada de uma matéria ligada ganha na linha de serviço ("na agenda: qua
    25"), com o dia, o item e a linha (sessão) a que ele leva."""

    materia: str = ""
    rotulo: str = ""
    data: str = ""
    identificador: str = ""
    linha: str = ""


@dataclass(frozen=True)
class FonteTela:
    """A situação de uma fonte, para a página completa: o nome, o endereço oficial, o dia do dado
    ("24/11"), o estado ("ok", "aviso", "some" ou "ausente") e a frase pronta."""

    chave: str = ""
    instituicao: str = ""
    nome: str = ""
    url: str = ""
    data: str = ""
    estado: str = ""
    texto: str = ""


@dataclass(frozen=True)
class Pagina:
    """A visão completa (``edicoes/agenda.html``): os dias da janela com todas as linhas de nota
    40 ou mais e todos os itens delas (com a conta da nota e o termo oficial); os dias passados da
    semana (``decidido``); "No radar" (``radar``: o que tem data depois da janela); a fila do
    Plenário da Câmara; as frases; as fontes e os limites de cada uma; e as contagens do que ficou
    fora (``fora``: por motivo; ``abaixo_do_corte``: itens com nota menor que o corte;
    ``fora_do_teto``: linhas além do teto da página; ``contagens``: os números das fontes)."""

    dias: tuple[DiaTela, ...] = ()
    decidido: tuple[DiaTela, ...] = ()
    radar: tuple[DiaTela, ...] = ()
    fila: tuple[ItemTela, ...] = ()
    fila_texto: str = ""
    frases: tuple[Frase, ...] = ()
    fontes: tuple[FonteTela, ...] = ()
    limites: tuple[str, ...] = ()
    fora: tuple[Contagem, ...] = ()
    contagens: tuple[Contagem, ...] = ()
    abaixo_do_corte: int = 0
    fora_do_teto: int = 0
    corte: int = 0
    corte_home: int = 0
    sem_pauta: str = ""


@dataclass(frozen=True)
class Retrato:
    """O retrato do dia.

    ``dia`` é a data da edição e ``janela`` os dias que a agenda mostra (o dia da edição e os 4
    dias úteis seguintes). ``dias`` é a home: os dias da janela, cada um com as suas linhas.
    ``frases`` são as frases por instituição. ``decidido`` é a faixa "Já decidido esta semana"
    (linhas de um item, dos dias já passados da semana). ``pe`` é o pé. ``fora_do_teto`` conta as
    linhas que passaram do corte e não couberam no teto da home ("mais N na agenda completa").
    ``lados`` diz de que arquivos de coleta o retrato saiu ("congresso", "tribunais").
    ``materias`` guarda o rótulo "na agenda" de cada matéria ligada. ``pagina`` é a visão
    completa."""

    versao: int = VERSAO
    dia: str = ""
    janela: tuple[str, ...] = ()
    lados: tuple[str, ...] = ()
    dias: tuple[DiaTela, ...] = ()
    frases: tuple[Frase, ...] = ()
    decidido: tuple[LinhaTela, ...] = ()
    pe: Pe = field(default_factory=Pe)
    fora_do_teto: int = 0
    materias: tuple[MateriaNaAgenda, ...] = ()
    pagina: Pagina = field(default_factory=Pagina)

    # ── consultas para a tela ────────────────────────────────────────────────

    @property
    def linhas(self) -> tuple[LinhaTela, ...]:
        """Todas as linhas da home, na ordem em que aparecem."""
        return tuple(linha for dia in self.dias for linha in dia.linhas)

    @property
    def vazio(self) -> bool:
        """Não há o que mostrar na home: nenhuma linha, nenhuma frase e nada já decidido."""
        return not (self.linhas or self.frases or self.decidido)

    def materia(self, story_id: str) -> MateriaNaAgenda | None:
        """O registro "na agenda" de uma matéria da edição, ou ``None`` quando ela não é ligada."""
        return next((m for m in self.materias if m.materia == story_id), None)

    def rotulo_da_materia(self, story_id: str) -> str:
        """O rótulo "na agenda: qua 25" de uma matéria da edição, ou vazio."""
        registro = self.materia(story_id)
        return registro.rotulo if registro else ""

    # ── ida e volta ──────────────────────────────────────────────────────────

    def para_dict(self) -> dict[str, Any]:
        """Dicionário só com tipos de JSON, em ordem fixa, sem os campos que estão no padrão."""
        return _para_dict(self)

    @classmethod
    def de_dict(cls, dados: Mapping[str, Any]) -> Retrato:
        """Monta o objeto de um dicionário; levanta ``ValueError`` se algum campo tem o tipo
        errado ou a versão é desconhecida (:func:`ler_retrato` é a versão que não levanta)."""
        retrato = _de_dict(cls, dados)
        if retrato.versao != VERSAO:
            raise ValueError(f"versão {retrato.versao!r} do retrato da agenda é desconhecida")
        return retrato


# ── ida e volta genérica (pelos tipos declarados nos campos) ─────────────────────────────────

_TIPOS: dict[type, dict[str, Any]] = {}


def _tipos(classe: type) -> dict[str, Any]:
    if classe not in _TIPOS:
        _TIPOS[classe] = typing.get_type_hints(classe)
    return _TIPOS[classe]


def _padrao(campo: dataclasses.Field[Any]) -> Any:
    if campo.default is not dataclasses.MISSING:
        return campo.default
    if campo.default_factory is not dataclasses.MISSING:
        return campo.default_factory()
    return dataclasses.MISSING


def _para_json(valor: Any) -> Any:
    if dataclasses.is_dataclass(valor) and not isinstance(valor, type):
        return _para_dict(valor)
    if isinstance(valor, tuple):
        return [_para_json(v) for v in valor]
    return valor


def _para_dict(objeto: Any) -> dict[str, Any]:
    saida: dict[str, Any] = {}
    for campo in dataclasses.fields(objeto):
        valor = getattr(objeto, campo.name)
        if campo.name != "versao" and valor == _padrao(campo):
            continue
        saida[campo.name] = _para_json(valor)
    return saida


def _converter(tipo: Any, bruto: Any, onde: str) -> Any:
    if tipo is str:
        if not isinstance(bruto, str):
            raise ValueError(f"{onde}: texto esperado")
        return bruto
    if tipo is bool:
        if not isinstance(bruto, bool):
            raise ValueError(f"{onde}: verdadeiro ou falso esperado")
        return bruto
    if tipo is int:
        if isinstance(bruto, bool) or not isinstance(bruto, int):
            raise ValueError(f"{onde}: número inteiro esperado")
        return bruto
    if typing.get_origin(tipo) is tuple:
        if not isinstance(bruto, (list, tuple)):
            raise ValueError(f"{onde}: lista esperada")
        interno = typing.get_args(tipo)[0]
        return tuple(_converter(interno, valor, f"{onde}[{i}]") for i, valor in enumerate(bruto))
    if dataclasses.is_dataclass(tipo):
        return _de_dict(tipo, bruto, onde)
    raise ValueError(f"{onde}: tipo de campo sem conversão")


def _de_dict(classe: Any, dados: Any, onde: str = "") -> Any:
    if not isinstance(dados, Mapping):
        raise ValueError(f"{onde or classe.__name__}: objeto esperado")
    tipos = _tipos(classe)
    valores = {
        campo.name: _converter(tipos[campo.name], dados[campo.name], f"{onde}.{campo.name}" if onde else campo.name)
        for campo in dataclasses.fields(classe)
        if campo.name in dados
    }
    return classe(**valores)


def ler_retrato(dados: Any) -> Retrato | None:
    """O retrato gravado na edição (``Edition.agenda``) como objeto, ou ``None`` quando não há
    retrato ou ele veio em formato inesperado. Nunca levanta."""
    if not isinstance(dados, Mapping) or dados.get("versao") != VERSAO or isinstance(dados.get("versao"), bool):
        return None
    try:
        return Retrato.de_dict(dados)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return None
