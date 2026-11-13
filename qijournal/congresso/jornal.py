"""O cruzamento com o banco do jornal: quem do Congresso aparece nas notícias.

O banco são os arquivos de busca de cada edição (``data/busca/AAAA-MM-DD.json``,
:mod:`qijournal.render.pesquisa`): todas as manchetes coletadas e o texto das
matérias. Aqui eles são lidos atrás dos **nomes parlamentares** de quem está (ou
esteve, nesta legislatura) na Câmara e no Senado.

A regra de casamento é conservadora, porque um nome errado numa ficha é pior que
um nome a menos:

- o nome inteiro, com as maiúsculas do registro oficial e sem diferenciar
  acentos ("Rogerio Marinho" acha "Rogério Marinho"; "Da Vitoria" não acha "da
  vitória");
- nomes de uma palavra só ("Tiririca", "Zucco") e nomes feitos só de prenomes
  comuns ("Júlio Cesar", "Pedro Paulo") só valem com um título antes
  ("deputado", "senadora", "dep.") ou a legenda depois ("(PL-RS)");
- tratamentos do nome parlamentar ("Delegado", "Dr.", "Pastor") podem faltar na
  notícia quando sobram duas palavras ("Paulo Bilynskyj");
- um nome que serve a duas pessoas só vale com o título da Casa ("senador…");
- só as editorias em que político é notícia (esporte, cultura e natureza ficam de
  fora: é onde moram os homônimos).
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import text
from .projetos import dias_entre

log = logging.getLogger(__name__)

SECOES = frozenset(("politica", "brasil", "juridico", "mercados", "mundo", "tecnologia", "imobiliario"))
TRATAMENTOS = frozenset(
    """delegado delegada dr dra doutor doutora pastor pastora professor professora prof profa coronel cel capitao
    capitão cap general gen sargento sgt cabo major tenente subtenente soldado policial pr padre missionario
    missionário missionaria missionária bispo bispa apostolo apóstolo enfermeira enfermeiro medico médico
    medica médica juiz juiza juíza comandante irmao irmão irma irmã tio tia vovo""".split()
)
PRENOMES = frozenset(
    """jose joao pedro paulo julio cesar luiz luis carlos antonio marcos lucas andre fernando eduardo ricardo roberto
    daniel rafael felipe bruno marcelo rodrigo gabriel mario sergio maria ana jorge francisco manoel manuel
    alexandre gustavo leonardo guilherme vinicius diego tiago thiago mateus matheus miguel augusto henrique
    victor vitor igor caio renato flavio marcio mauricio claudio fabio juliana fernanda patricia adriana
    marcia aline camila luciana claudia sandra renata vera lucia helena""".split()
)
_TITULO = r"(?:deputad[oa]s?|senador(?:a|es|as)?|dep\.|sen\.|parlamentar|l[ií]der|relator(?:a)?|presidente)"
_LEGENDA = r"\s*\((?:[A-Z][A-Za-zÃÕÇãõç]{1,14})\s*[-/]\s*[A-Z]{2}\)"
_TITULO_CASA = {"cd": re.compile(r"deputad|dep\.", re.I), "sf": re.compile(r"senador|sen\.", re.I)}
JANELA_TITULO = 28  # caracteres antes do nome em que o título pode estar


@dataclass(frozen=True)
class Pessoa:
    chave: str  # "cd-204536"
    casa: str
    nome: str


@dataclass
class Mencao:
    data: str  # AAAA-MM-DD da edição
    tipo: str  # "n" (manchete coletada) ou "m" (matéria da edição)
    titulo: str
    veiculo: str = ""
    url: str = ""  # manchete: link original
    materia: str = ""  # matéria: id (âncora #s-<id> na edição)
    secao: str = ""


def _sem_acento(valor: str) -> str:
    return text.strip_accents(valor or "")


class Nomes:
    """Índice dos nomes parlamentares para achar quem é citado num texto."""

    def __init__(self, pessoas: Iterable[Pessoa]) -> None:
        self._formas: dict[str, list[tuple[Pessoa, bool]]] = defaultdict(list)  # forma → [(pessoa, exige título)]
        for pessoa in pessoas:
            for forma, exige in self._formas_de(pessoa.nome):
                if not any(p.chave == pessoa.chave for p, _ in self._formas[forma]):
                    self._formas[forma].append((pessoa, exige))
        formas = sorted(self._formas, key=len, reverse=True)
        self._padrao = (
            re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(f) for f in formas) + r")(?![A-Za-z0-9])") if formas else None
        )

    @staticmethod
    def _formas_de(nome: str) -> list[tuple[str, bool]]:
        limpo = re.sub(r"\s+", " ", _sem_acento(nome).strip())
        if not limpo:
            return []
        partes = limpo.split(" ")
        chaves = [p.lower().strip(".") for p in partes]
        comuns = all(c in PRENOMES for c in chaves)
        formas = [(limpo, len(partes) == 1 or comuns)]
        if len(partes) >= 3 and chaves[0] in TRATAMENTOS:  # "Delegado Paulo Bilynskyj" → "Paulo Bilynskyj"
            resto = partes[1:]
            formas.append((" ".join(resto), all(p.lower() in PRENOMES for p in resto)))
        return formas

    def achar(self, texto: str) -> list[Pessoa]:
        """As pessoas citadas em ``texto``, na ordem em que aparecem, sem repetir."""
        if self._padrao is None or not texto:
            return []
        plano = _sem_acento(texto)
        achadas: dict[str, Pessoa] = {}
        for match in self._padrao.finditer(plano):
            candidatas = self._formas[match.group(1)]
            antes = plano[max(0, match.start() - JANELA_TITULO) : match.start()]
            depois = plano[match.end() : match.end() + 24]
            titulo = re.search(_TITULO + r"\s+(?:federal\s+|estadual\s+)?$", antes, re.I)
            legenda = re.match(_LEGENDA, depois)
            if len(candidatas) > 1:  # homônimos: só com o título da Casa
                candidatas = [(p, e) for p, e in candidatas if titulo and _TITULO_CASA[p.casa].search(titulo.group(0))]
                if len(candidatas) != 1:
                    continue
            pessoa, exige = candidatas[0]
            if exige and not (titulo or legenda):
                continue
            achadas.setdefault(pessoa.chave, pessoa)
        return list(achadas.values())


# ── proposições citadas ──────────────────────────────────────────────────────

_PROPOSICAO = re.compile(
    r"\b(PEC|PLP|PL|PDL|MPV|MP)\s*(?:n[ºo°.]{0,2}\s*)?(\d{1,2}\.?\d{0,3})\s*(?:/|\s+de\s+)\s*(\d{4}|\d{2})\b"
)


def proposicoes_citadas(texto: str, apelidos: Mapping[str, str] | None = None) -> list[str]:
    """As proposições que o texto cita pelo número ("PL 2.475/2025", "MP 1.394/26") ou
    por um apelido da tabela (``config/congresso.yaml → apelidos``), como títulos
    ("PL 2475/2025"), sem repetir."""
    achadas: list[str] = []
    for sigla, numero, ano in _PROPOSICAO.findall(texto or ""):
        ano = f"20{ano}" if len(ano) == 2 else ano
        titulo = f"{'MPV' if sigla == 'MP' else sigla} {int(numero.replace('.', ''))}/{ano}"
        if titulo not in achadas:
            achadas.append(titulo)
    if apelidos:
        plano = text.normalize(texto or "")
        for apelido, titulo in apelidos.items():
            if apelido and re.search(rf"\b{re.escape(apelido)}\b", plano) and titulo not in achadas:
                achadas.append(titulo)
    return achadas


# ── leitura do banco ─────────────────────────────────────────────────────────


@dataclass
class Cruzamento:
    """As menções de cada pessoa nas edições lidas."""

    edicoes: list[str] = field(default_factory=list)  # datas lidas, em ordem
    manchetes: int = 0  # manchetes lidas (nas editorias que contam)
    por_pessoa: dict[str, list[Mencao]] = field(default_factory=dict)

    def de(self, chave: str) -> list[Mencao]:
        return self.por_pessoa.get(chave, [])


def mencoes_do_dia(dados: Mapping[str, Any], nomes: Nomes) -> tuple[int, dict[str, list[Mencao]]]:
    """As menções num arquivo de busca: ``(manchetes lidas, {chave: [menções]})``."""
    data = str(dados.get("data") or "")
    secoes = [str(s[0]) for s in dados.get("secoes") or []]
    veiculos = [str(v) for v in dados.get("veiculos") or []]
    por: dict[str, list[Mencao]] = defaultdict(list)
    lidas = 0
    vistos: set[tuple[str, str]] = set()
    for item in dados.get("noticias") or []:
        try:
            secao = secoes[item[0]]
            veiculo = veiculos[item[2]]
            titulo, url = str(item[3]), str(item[4])
        except (IndexError, TypeError):
            continue
        if secao not in SECOES:
            continue
        lidas += 1
        for pessoa in nomes.achar(titulo):
            if (pessoa.chave, url) in vistos:
                continue
            vistos.add((pessoa.chave, url))
            por[pessoa.chave].append(
                Mencao(data=data, tipo="n", titulo=titulo, veiculo=veiculo, url=f"https://{url}" if "://" not in url else url, secao=secao)
            )
    for materia in dados.get("materias") or []:
        try:
            secao = secoes[materia["s"]]
        except (IndexError, KeyError, TypeError):
            secao = ""
        corpo = " ".join(str(materia.get(k) or "") for k in ("t", "l", "c"))
        for pessoa in nomes.achar(corpo):
            por[pessoa.chave].append(
                Mencao(data=data, tipo="m", titulo=str(materia.get("t") or ""), materia=str(materia.get("id") or ""), secao=secao)
            )
    return lidas, dict(por)


def cruzar(pasta_busca: Path, nomes: Nomes, *, desde: str = "") -> Cruzamento:
    """Lê os arquivos de busca de ``pasta_busca`` (``data/busca``), das edições a partir
    de ``desde``, e junta as menções de cada pessoa (da mais nova para a mais antiga)."""
    cruzamento = Cruzamento()
    por: dict[str, list[Mencao]] = defaultdict(list)
    for arquivo in sorted(Path(pasta_busca).glob("????-??-??.json")):
        if arquivo.stem < desde:
            continue
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Congresso × jornal: %s ilegível (%s)", arquivo.name, exc)
            continue
        if not isinstance(dados, dict):
            continue
        lidas, do_dia = mencoes_do_dia(dados, nomes)
        cruzamento.edicoes.append(arquivo.stem)
        cruzamento.manchetes += lidas
        for chave, mencoes in do_dia.items():
            por[chave] += mencoes
    for chave, mencoes in por.items():
        mencoes.sort(key=lambda m: (m.data, m.tipo), reverse=True)
        cruzamento.por_pessoa[chave] = mencoes
    return cruzamento


def resumo_de_mencoes(mencoes: list[Mencao], hoje: str, *, lista: int = 10) -> dict[str, Any]:
    """O que o jornal publicou de alguém: quantas manchetes (nos últimos 7 e 30 dias e
    em tudo o que foi lido), por veículo, as mais recentes e as matérias da edição."""
    manchetes = [m for m in mencoes if m.tipo == "n"]
    materias = [m for m in mencoes if m.tipo == "m"]
    if not manchetes and not materias:
        return {"n": 0}

    def desde(dias: int) -> int:
        return sum(1 for m in manchetes if (dias_entre(m.data, hoje) or 0) < dias)

    veiculos: dict[str, int] = defaultdict(int)
    for m in manchetes:
        veiculos[m.veiculo] += 1
    return {
        "n": len(manchetes),
        "n7": desde(7),
        "n30": desde(30),
        "veiculos": dict(sorted(veiculos.items(), key=lambda kv: -kv[1])[:8]),
        "manchetes": [{"d": m.data, "t": m.titulo, "v": m.veiculo, "u": m.url} for m in manchetes[:lista]],
        "materias": [{"d": m.data, "t": m.titulo, "id": m.materia} for m in materias[:lista]],
    }
