"""A ligação entre os itens da Agenda da semana e a mídia, sem IA.

Um item só é ligado a um texto por identificador ou por apelido aprovado:

- proposição com sigla, número e ano ("PLP 299/2026", "MP 1.381/26"), com o mesmo detector da
  aba Congresso (:func:`qijournal.congresso.jornal.proposicoes_citadas`);
- processo por classe e número ("RE 1326541", "ADI 6.002");
- "Tema N", só com o tribunal no mesmo texto ("Tema 1218 do STF");
- apelido aprovado em ``config/agenda.yaml`` (2 palavras ou mais, dentro da validade), só com o
  órgão no mesmo texto ("pacote fiscal" e "Câmara").

Sigla solta ("PL" de Partido Liberal, "MP" de Ministério Público), nome de ministro, nome de
parte e palavras da ementa nunca ligam. O feed do próprio órgão não conta como imprensa: uma
notícia do STJ sobre um julgamento do STJ é comunicação do órgão.

O corpus são as matérias, os assuntos comparados e as notícias da edição do dia e os arquivos de
``data/busca`` das últimas edições. Por item a ligação devolve quantos veículos distintos o
citam e os ids das matérias da edição do dia ligadas. A mídia não pontua: a ligação só acrescenta
a marca "na mídia" ao item que já entrou pela régua. Tudo aqui é função pura.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from qijournal import text
from qijournal.agenda import contrato
from qijournal.agenda.regras import APELIDO_MINIMO_DE_PALAVRAS, Regras
from qijournal.congresso.jornal import proposicoes_citadas

EDICOES_ANTERIORES = 7  # quantos arquivos de data/busca entram no corpus, além da edição do dia

# Como o órgão aparece num texto (já sem acento e em minúsculas): o apelido e o "Tema N" só
# ligam com uma destas palavras no mesmo texto.
PALAVRAS_DO_ORGAO: dict[str, tuple[str, ...]] = {
    contrato.CAMARA: ("camara", "deputados", "congresso"),
    contrato.SENADO: ("senado", "senadores", "congresso"),
    contrato.CONGRESSO: ("congresso", "camara", "senado"),
    contrato.STF: ("stf", "supremo"),
    contrato.STJ: ("stj", "superior tribunal de justica"),
}

_PROPOSICAO_COM_ANO = re.compile(r"^([A-Za-z]{2,10}) (\d{1,6})/(\d{4})$")
_PROCESSO = re.compile(r"^([A-Za-z]{2,16}) (\d{1,9})$")
_PONTO_DE_MILHAR = re.compile(r"(?<=\d)\.(?=\d{3}(?!\d))")
_TEMA = re.compile(r"\btema\s+(?:n[ºo°.]{0,2}\s*)?(\d{1,4})\b(?![./]\d)", re.IGNORECASE)
_SIGLAS_DO_DETECTOR = frozenset(("PEC", "PLP", "PL", "PDL", "MPV"))


@dataclass(frozen=True)
class Texto:
    """Um texto do corpus: o que foi publicado, por quais veículos e, quando é uma matéria da
    edição do dia (ou uma notícia do assunto dela), o id da matéria."""

    texto: str
    veiculos: tuple[str, ...] = ()
    materia: str = ""
    data: str = ""


@dataclass(frozen=True)
class Alvo:
    """O que identifica um item nos textos: o identificador, a instituição em que ele está
    pautado e, quando há, o número do tema."""

    identificador: str
    instituicao: str
    tema: int | None = None


@dataclass(frozen=True)
class Ligacao:
    """A ligação de um item: os veículos de imprensa que o citam (nomes em ordem alfabética) e
    as matérias da edição do dia ligadas (na ordem da edição)."""

    veiculos: tuple[str, ...] = ()
    materias: tuple[str, ...] = ()

    @property
    def n_veiculos(self) -> int:
        return len(self.veiculos)

    def __bool__(self) -> bool:
        return bool(self.veiculos or self.materias)


SEM_LIGACAO = Ligacao()


# ── alvos ────────────────────────────────────────────────────────────────────────────────────


def alvos_da_agenda(*agendas: contrato.Agenda | None) -> tuple[Alvo, ...]:
    """Os itens de um ou dos dois arquivos de coleta (sessões, fila e próximo evento) como alvos
    da ligação, sem repetir (o mesmo item em duas instituições vira dois alvos)."""
    vistos: dict[tuple[str, str], Alvo] = {}
    for agenda in agendas:
        if agenda is None:
            continue
        sessoes = list(agenda.sessoes) + [e.proximo for e in agenda.estado if e.proximo is not None]
        pares = [(s.instituicao, item) for s in sessoes for item in s.itens]
        pares += [(e.instituicao, item) for e in agenda.estado for item in e.fila]
        for instituicao, item in pares:
            vistos.setdefault((item.identificador, instituicao), Alvo(item.identificador, instituicao, item.tema.numero if item.tema else None))
    return tuple(vistos.values())


# ── corpus ───────────────────────────────────────────────────────────────────────────────────


def _nomes(valores: Iterable[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(v).strip() for v in valores if str(v or "").strip()))


def textos_da_edicao(edition: Any) -> list[Texto]:
    """Os textos da edição do dia. Só credita um veículo o texto que é dele: o título de cada
    notícia da lista completa e o enquadramento de cada veículo num assunto comparado. A matéria
    escrita pela própria edição e a síntese do assunto comparado ligam a matéria (para "na
    agenda" e para o link), mas não creditam as fontes: um veículo citado como fonte de uma
    matéria que fala do projeto não citou o projeto por isso (revisão de 24/11/2026: 8 dos 15
    veículos contados para o PLP 299/2026 entravam só assim). A notícia de um assunto que virou
    matéria leva o id da matéria."""
    textos: list[Texto] = []
    dia = str(getattr(edition, "date", "") or "")
    comparadas = list(getattr(edition, "compared", None) or [])
    for story in getattr(edition, "stories", None) or []:
        corpo = " \n".join([story.headline or "", story.dek or "", *(story.body or []), story.why_it_matters or ""])
        textos.append(Texto(corpo, (), materia=story.id, data=dia))
        if story.coverage is not None:
            comparadas.append(story.coverage)
    for cobertura in comparadas:
        partes = [cobertura.topic, cobertura.conclusion, cobertura.side_a, cobertura.side_b]
        textos.append(Texto(" \n".join(p or "" for p in partes), (), data=dia))
        for o in cobertura.outlets:
            textos.append(Texto(o.framing or "", _nomes([o.name]), data=dia))
    for assunto in getattr(edition, "index", None) or []:
        for noticia in assunto.items:
            textos.append(Texto(noticia.title or "", _nomes([noticia.source]), materia=assunto.story_id or "", data=dia))
    for noticia in getattr(edition, "wire", None) or []:
        if isinstance(noticia, Mapping):
            textos.append(Texto(str(noticia.get("title") or ""), _nomes([noticia.get("source")]), data=dia))
    return textos


def textos_da_busca(dados: Any) -> list[Texto]:
    """Os textos de um arquivo de ``data/busca`` (uma edição anterior), com a mesma regra de
    ``textos_da_edicao``: só o título da notícia e o enquadramento de cada veículo creditam o
    veículo; a matéria e a síntese do assunto comparado não creditam ninguém. Formato
    inesperado devolve o que der para ler (uma linha fora do formato é pulada)."""
    if not isinstance(dados, Mapping):
        return []
    dia = str(dados.get("data") or "")
    veiculos = [str(v) for v in dados.get("veiculos") or []] if isinstance(dados.get("veiculos"), list) else []

    def nome(indice: Any) -> str:
        return veiculos[indice] if isinstance(indice, int) and not isinstance(indice, bool) and 0 <= indice < len(veiculos) else ""

    def lista(chave: str) -> list[Any]:
        valor = dados.get(chave)
        return valor if isinstance(valor, list) else []

    textos: list[Texto] = []
    for materia in lista("materias"):
        if isinstance(materia, Mapping):
            corpo = " \n".join(str(materia.get(k) or "") for k in ("t", "l", "c", "p"))
            textos.append(Texto(corpo, (), data=dia))
    for comparada in lista("comparadas"):
        if isinstance(comparada, Mapping):
            focos = [f for f in comparada.get("f") or [] if isinstance(f, list) and len(f) >= 3] if isinstance(comparada.get("f"), list) else []
            textos.append(Texto(" \n".join(str(comparada.get(k) or "") for k in ("t", "c", "a", "b")), (), data=dia))
            for f in focos:
                textos.append(Texto(str(f[2] or ""), _nomes([nome(f[0])]), data=dia))
    for noticia in lista("noticias"):
        if isinstance(noticia, list) and len(noticia) >= 4:
            textos.append(Texto(str(noticia[3] or ""), _nomes([nome(noticia[2])]), data=dia))
    return textos


# ── detecção ─────────────────────────────────────────────────────────────────────────────────

# "RE 1326541", "ADI nº 6002" (o ponto de milhar já foi tirado): classe e número, sem barra depois.
_PROCESSO_NO_TEXTO = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,16})\s*(?:n[ºo°.]{0,2}\s*)?(\d{1,9})(?![\d/])")
# "PRC 7/2025", "PLN 5/26": sigla em maiúsculas, número e ano (as siglas que o detector do
# Congresso não conhece).
_PROPOSICAO_NO_TEXTO = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,10})\s*(?:n[ºo°.]{0,2}\s*)?(\d{1,6})\s*/\s*(\d{4}|\d{2})(?!\d)")


@dataclass
class _Indice:
    """Os alvos arrumados pelo que os identifica num texto (montado uma vez por chamada)."""

    proposicoes: dict[str, list[Alvo]]  # "PL 6461/2019" (o título que o detector do Congresso devolve)
    outras: dict[tuple[str, int, str], list[Alvo]]  # ("PRC", 7, "2025")
    processos: dict[tuple[str, int], list[Alvo]]  # ("re", 1326541)
    temas: dict[int, list[Alvo]]  # só de item de tribunal
    apelidos: dict[str, list[Alvo]]  # apelido aprovado, já sem acento e em minúsculas


def _indice(alvos: Iterable[Alvo], regras: Regras, dia: str) -> _Indice:
    indice = _Indice({}, {}, {}, {}, {})
    for alvo in alvos:
        identificador = alvo.identificador.strip()
        com_ano = _PROPOSICAO_COM_ANO.match(identificador)
        processo = _PROCESSO.match(identificador)
        if com_ano:
            sigla, numero, ano = com_ano.group(1).upper(), int(com_ano.group(2)), com_ano.group(3)
            if sigla in _SIGLAS_DO_DETECTOR:
                indice.proposicoes.setdefault(f"{sigla} {numero}/{ano}", []).append(alvo)
            else:
                indice.outras.setdefault((sigla, numero, ano), []).append(alvo)
        elif processo:
            indice.processos.setdefault((processo.group(1).lower(), int(processo.group(2))), []).append(alvo)
        if alvo.tema is not None and alvo.instituicao in (contrato.STF, contrato.STJ):
            indice.temas.setdefault(alvo.tema, []).append(alvo)
        for apelido in regras.apelidos_de(identificador, dia):
            plano = text.normalize(apelido)
            if len(plano.split()) >= APELIDO_MINIMO_DE_PALAVRAS:
                indice.apelidos.setdefault(plano, []).append(alvo)
    return indice


def _com_orgao(plano: str, alvo: Alvo) -> bool:
    return any(re.search(rf"\b{re.escape(t)}\b", plano) for t in PALAVRAS_DO_ORGAO.get(alvo.instituicao, ()))


def citados(bruto: str, indice: _Indice) -> list[Alvo]:
    """Os alvos que o texto cita, por identificador ou por apelido aprovado."""
    achados: list[Alvo] = []
    if indice.proposicoes:
        for titulo in proposicoes_citadas(bruto):
            achados += indice.proposicoes.get(titulo, [])
    if indice.outras:
        for sigla, numero, ano in _PROPOSICAO_NO_TEXTO.findall(bruto):
            achados += indice.outras.get((sigla, int(numero), f"20{ano}" if len(ano) == 2 else ano), [])
    sem_milhar = _PONTO_DE_MILHAR.sub("", bruto)
    if indice.processos:
        for classe, numero in _PROCESSO_NO_TEXTO.findall(sem_milhar):
            achados += indice.processos.get((classe.lower(), int(numero)), [])
    temas = [int(n) for n in _TEMA.findall(sem_milhar) if int(n) in indice.temas] if indice.temas else []
    if temas or indice.apelidos:
        plano = text.normalize(bruto)
        for numero in temas:  # "Tema N" só liga com o tribunal no mesmo texto
            achados += [alvo for alvo in indice.temas[numero] if _com_orgao(plano, alvo)]
        for apelido, alvos in indice.apelidos.items():  # o apelido só liga com o órgão no mesmo texto
            if re.search(rf"\b{re.escape(apelido)}\b", plano):
                achados += [alvo for alvo in alvos if _com_orgao(plano, alvo)]
    return achados


def ligar(
    alvos: Iterable[Alvo],
    textos: Iterable[Texto],
    regras: Regras,
    dia: str,
    *,
    veiculos_oficiais: Iterable[str] = (),
) -> dict[str, Ligacao]:
    """A ligação de cada item com a mídia, por identificador (``{identificador: Ligacao}``; item
    sem ligação não entra). ``dia`` é o da edição (a validade dos apelidos); ``veiculos_oficiais``
    são os nomes dos canais dos próprios órgãos, que não contam como imprensa."""
    oficiais = {text.normalize(v) for v in veiculos_oficiais}
    indice = _indice(alvos, regras, dia)
    veiculos: dict[str, set[str]] = {}
    materias: dict[str, list[str]] = {}
    for item in textos:
        bruto = item.texto or ""
        if not bruto.strip():
            continue
        for alvo in citados(bruto, indice):
            chave = alvo.identificador
            veiculos.setdefault(chave, set()).update(v for v in item.veiculos if text.normalize(v) not in oficiais)
            lista = materias.setdefault(chave, [])
            if item.materia and item.materia not in lista:
                lista.append(item.materia)
    return {
        chave: Ligacao(veiculos=tuple(sorted(veiculos.get(chave, ()), key=text.normalize)), materias=tuple(materias.get(chave, ())))
        for chave in dict.fromkeys([*veiculos, *materias])
        if veiculos.get(chave) or materias.get(chave)
    }


def veiculos_oficiais_de(fontes: Iterable[Any], regras: Regras) -> tuple[str, ...]:
    """Os nomes de veículo dos feeds que são canais dos próprios órgãos (``config/sources.yaml``
    cruzado com ``feeds_oficiais`` de ``config/agenda.yaml``)."""
    return _nomes(getattr(f, "name", "") for f in fontes if regras.feed_oficial(getattr(f, "id", "")))
