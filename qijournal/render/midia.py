"""Como a mídia cobre um nome: os nomes em destaque de uma editoria.

É a parte calculada no servidor do analítico da busca (o resto roda no navegador,
na Pesquisa analítica, sobre os mesmos arquivos de ``data/busca/``). Serve a duas
páginas, sem mudar nada do que elas já mostravam:

- **Analítico Político** (``edicoes/analitico.html``): a tabela "Nomes em
  destaque", com os nomes mais citados nas manchetes de Política dos últimos 30
  dias (pessoas, partidos, instituições), quanto espaço cada um ocupa, se a
  atenção sobe ou cai, quantos veículos falam dele e quão dividida ficou a
  cobertura comparada sobre ele; cada linha abre o analítico completo do nome na
  Pesquisa;
- **Pesquisa analítica** (``edicoes/pesquisa.html``): os mesmos nomes como
  sugestões de busca.

Os nomes saem das próprias manchetes em português (sequências de palavras com
inicial maiúscula; a primeira palavra do título só conta se o mesmo nome aparece
com maiúscula no meio de outros títulos do dia). A contagem final usa a mesma
regra de busca da página (sem acento, começo de palavra; até 3 letras, só a
palavra inteira), para os números da tabela baterem com o analítico do nome.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

from .. import text
from ..config import Config

SECAO = "politica"  # editoria dos nomes em destaque
JANELA_DIAS = 30  # edições dos últimos 30 dias
SEMANA = 7  # tendência: últimas 7 edições contra as 7 anteriores
LIMITE = 12  # nomes na tabela
CANDIDATOS = 40  # nomes conferidos pela regra de busca antes do corte
MIN_MANCHETES = 5  # menos que isso não entra na tabela
MIN_BASE_SEMANA = 50  # manchetes da editoria por semana para dizer a tendência
ESTAVEL = 0.15  # variação de até 15% é "estável"

_MARCAS = re.compile("[\u0300-\u036f]")  # acentos (marcas combinantes, depois do NFD)
_PALAVRA = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+(?:['’\-][0-9A-Za-zÀ-ÖØ-öø-ÿ]+)*")
_PONTUACAO = re.compile(r"[:.!?|\u2013\u2014\"\u201c\u201d\u2018']")
_CONECTORES = frozenset({"de", "da", "do", "das", "dos", "del", "di", "van", "von"})
# Palavras que nunca fazem parte de um nome (abrem frase com maiúscula: artigos,
# verbos de chamada, meses, dias da semana).
FORA = frozenset(
    """
    a o as os um uma e em no na nos nas de da do das dos ao aos com sem por para pela pelo sob sobre apos ate
    que se como quando onde quem qual mais menos ja nao sim eis
    veja saiba entenda confira assista leia ouca acompanhe conheca descubra
    dia serie
    janeiro fevereiro marco abril maio junho julho agosto setembro outubro novembro dezembro
    segunda terca quarta quinta sexta sabado domingo
    """.split()
)
# Nomes inteiros genéricos demais para dizerem algo sobre a cobertura: o país, os
# EUA, siglas de estado, cidades e emissoras (como parte de um nome maior, ficam:
# "Banco do Brasil", "Prefeitura de SP"). A busca continua achando todos eles.
FORA_NOMES = frozenset(
    {"brasil", "brasilia", "eua", "us", "usa", "tv", "globo", "tv globo", "band", "record", "sbt", "rio"}
    | {"sao paulo", "rio de janeiro", "minas gerais"}
    | set("ac al ap am ba ce df es go ma mt ms mg pa pb pr pe pi rj rn rs ro rr sc sp se to".split())
)


def norm(valor: Any) -> str:
    """Como a página normaliza: sem acentos e em minúsculas (nada mais)."""
    return _MARCAS.sub("", unicodedata.normalize("NFD", str(valor or ""))).lower()


def padrao(nome: str) -> re.Pattern[str] | None:
    """A regra de busca da Pesquisa para um nome (``termos()`` em ``pesquisa.js.j2``):
    as palavras em sequência, no começo de uma palavra; até 3 letras, só inteira."""
    n = re.sub(r"[^a-z0-9]+", " ", norm(nome)).strip()
    if not n:
        return None
    corpo = n.replace(" ", "[^a-z0-9]+")
    fim = "(?![a-z0-9])" if len(n) <= 3 and " " not in n else ""
    return re.compile(f"(?:^|[^a-z0-9])({corpo}){fim}")


def consulta(nome: str) -> str:
    """O nome como termo de busca: com mais de uma palavra, entre aspas (frase exata)."""
    nome = " ".join(str(nome).replace('"', " ").split())
    return f'"{nome}"' if " " in nome else nome


def exterior(config: Config) -> list[str]:
    """Veículos de fora do Brasil (feeds em outra língua), pelo nome exibido."""
    nacionais = {s.name for s in config.sources if (s.lang or "pt") == "pt"}
    return sorted({s.name for s in config.sources if (s.lang or "pt") != "pt"} - nacionais)


# ── corpus de um dia ─────────────────────────────────────────────────────────


@dataclass
class CorpusDia:
    """O que um dia de ``data/busca/`` oferece aos nomes em destaque."""

    data: str
    titulos: list[tuple[str, str, str]] = field(default_factory=list)  # (link, título normalizado, veículo)
    candidatos: Counter[str] = field(default_factory=Counter)  # nome normalizado → manchetes
    formas: dict[str, Counter[str]] = field(default_factory=dict)  # grafias de cada nome
    comparadas: list[tuple[str, int, int]] = field(default_factory=list)  # texto normalizado, lado A, lado B


def _palavras(titulo: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0)) for m in _PALAVRA.finditer(titulo)]


def _inicio_de_frase(titulo: str, palavras: list[tuple[int, int, str]], k: int) -> bool:
    """A palavra abre o título ou vem depois de dois-pontos, ponto, travessão ou aspas."""
    return k == 0 or bool(_PONTUACAO.search(titulo[palavras[k - 1][1] : palavras[k][0]]))


def _nomes_proprios(titulos: Iterable[str]) -> set[str]:
    """Palavras que, no meio das frases dos títulos, aparecem em geral com maiúscula."""
    maiuscula: Counter[str] = Counter()
    minuscula: Counter[str] = Counter()
    for titulo in titulos:
        palavras = _palavras(titulo)
        for k, (_a, _b, palavra) in enumerate(palavras):
            if not _inicio_de_frase(titulo, palavras, k):
                (maiuscula if palavra[0].isupper() else minuscula)[norm(palavra)] += 1
    return {p for p, n in maiuscula.items() if n >= 2 and n >= 2 * minuscula[p]}


def nomes_do_titulo(titulo: str, proprios: set[str]) -> set[str]:
    """Sequências de palavras com maiúscula (com "de", "da"… no meio), sem atravessar
    pontuação. A palavra que abre uma frase só conta se for nome próprio no dia ou sigla."""
    palavras = _palavras(titulo)

    def nome(k: int) -> bool:
        _a, _b, p = palavras[k]
        n = norm(p)
        if len(p) < 2 or not p[0].isupper() or p[0].isdigit() or n in FORA:
            return False
        return not _inicio_de_frase(titulo, palavras, k) or n in proprios or p.isupper()

    def junto(k: int) -> bool:  # só um espaço entre a palavra k e a seguinte
        return titulo[palavras[k][1] : palavras[k + 1][0]] == " "

    saida: set[str] = set()
    i = 0
    while i < len(palavras):
        if not nome(i):
            i += 1
            continue
        j = i
        while True:
            if j + 1 < len(palavras) and junto(j) and nome(j + 1):
                j += 1
            elif (
                j + 2 < len(palavras)
                and norm(palavras[j + 1][2]) in _CONECTORES
                and junto(j)
                and junto(j + 1)
                and nome(j + 2)
            ):
                j += 2
            else:
                break
        achado = titulo[palavras[i][0] : palavras[j][1]]
        if norm(achado) not in FORA_NOMES:
            saida.add(achado)
        i = j + 1
    return saida


def corpus_do_dia(dados: Mapping[str, Any], estrangeiros: Iterable[str] = (), secao: str = SECAO) -> CorpusDia:
    """As manchetes em português de uma editoria (``secao``), os nomes delas e os
    assuntos da cobertura comparada da editoria (com os dois lados), de um arquivo
    de busca (:func:`qijournal.render.pesquisa.dados_do_dia`)."""
    fora = set(estrangeiros)
    veiculos = dados.get("veiculos") or []
    secoes = dados.get("secoes") or []

    def secao_de(i: Any) -> str:
        return secoes[i][0] if isinstance(i, int) and 0 <= i < len(secoes) else ""

    def veiculo_de(i: Any) -> str:
        return veiculos[i] if isinstance(i, int) and 0 <= i < len(veiculos) else ""

    corpus = CorpusDia(data=str(dados.get("data") or ""))
    nacionais: list[tuple[str, str, str]] = []  # (link, título, veículo), todas as editorias
    vistos: set[str] = set()
    for n in dados.get("noticias") or []:
        try:
            si, _assunto, vi, titulo, link = n[0], n[1], n[2], n[3], n[4]
        except (IndexError, TypeError):
            continue
        veiculo = veiculo_de(vi)
        if not veiculo or veiculo in fora or not titulo or link in vistos:
            continue
        vistos.add(link)
        nacionais.append((link, str(titulo), veiculo))
        if secao_de(si) == secao:
            corpus.titulos.append((str(link), norm(titulo), veiculo))
    proprios = _nomes_proprios(t for _l, t, _v in nacionais)
    na_secao = {link for link, _t, _v in corpus.titulos}
    for link, titulo, _v in nacionais:
        if link not in na_secao:
            continue
        for achado in nomes_do_titulo(titulo, proprios):
            chave = norm(achado)
            corpus.candidatos[chave] += 1
            corpus.formas.setdefault(chave, Counter())[achado] += 1
    for c in dados.get("comparadas") or []:
        if secao_de(c.get("s")) != secao or not (c.get("a") and c.get("b")):
            continue
        lados = Counter(o[1] for o in c.get("f") or [] if isinstance(o, list) and len(o) > 1)
        if not lados["a"] + lados["b"]:
            continue
        partes = [c.get("t"), c.get("c"), c.get("a"), c.get("b")] + [o[2] for o in c.get("f") or [] if len(o) > 2]
        corpus.comparadas.append(("\n".join(norm(p) for p in partes if p), lados["a"], lados["b"]))
    return corpus


# ── nomes em destaque ────────────────────────────────────────────────────────


@dataclass
class NomeView:
    nome: str
    tambem: str  # forma longa frequente do mesmo nome ("Flávio Bolsonaro" para "Flávio")
    consulta: str  # termo de busca (com aspas quando tem mais de uma palavra)
    href: str  # analítico do nome na Pesquisa (mesma editoria e período)
    manchetes: int
    participacao: str  # % das manchetes da editoria no período
    veiculos: int
    tendencia: str
    tendencia_classe: str  # sobe | cai | est | ins
    tendencia_titulo: str
    debates: int
    divergencia: str  # 0 a 100 (100 = racha meio a meio); "—" sem debate
    divergencia_valor: float | None
    barra: float  # % da maior contagem (barra da coluna de manchetes)


@dataclass
class DestaquesView:
    secao: str
    secao_titulo: str
    inicio: str  # AAAA-MM-DD
    fim: str
    periodo: str
    edicoes: int
    manchetes: int  # manchetes da editoria no período (base da participação)
    nomes: list[NomeView]
    pesquisa_href: str  # Pesquisa analítica (para buscar outro nome)


def _chave(nome: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", norm(nome)).split())


def _contido(curto: str, longo: str) -> bool:
    """As palavras de ``curto`` aparecem em sequência em ``longo``."""
    a, b = curto.split(), longo.split()
    return len(a) < len(b) and any(b[i : i + len(a)] == a for i in range(len(b) - len(a) + 1))


def _pct(parte: int, todo: int) -> str:
    if not todo:
        return "—"
    valor = 100 * parte / todo
    return f"{text.format_number_pt(valor, 1 if valor < 10 else 0)}%"


def _tendencia(por_dia: Mapping[str, int], base_dia: Mapping[str, int], dias: list[str]) -> tuple[str, str, str]:
    if len(dias) < 2 * SEMANA:
        return "—", "ins", f"A tendência compara {SEMANA} edições com as {SEMANA} anteriores."
    recente, anterior = dias[-SEMANA:], dias[-2 * SEMANA : -SEMANA]
    br, ba = sum(base_dia.get(d, 0) for d in recente), sum(base_dia.get(d, 0) for d in anterior)
    if br < MIN_BASE_SEMANA or ba < MIN_BASE_SEMANA:
        return "—", "ins", "Poucas manchetes na editoria para comparar as semanas."
    sr = sum(por_dia.get(d, 0) for d in recente) / br
    sa = sum(por_dia.get(d, 0) for d in anterior) / ba
    titulo = (
        f"Participação nas manchetes: {_pct(round(sr * br), br)} nas últimas {SEMANA} edições, "
        f"{_pct(round(sa * ba), ba)} nas {SEMANA} anteriores"
    )
    if not sa:
        return ("novo", "sobe", titulo) if sr else ("—", "ins", titulo)
    var = (sr - sa) / sa
    if abs(var) <= ESTAVEL:
        return "estável", "est", titulo
    if var >= 1:  # dobrou ou mais: "▲ 5,8×" se lê melhor que "▲ 482%"
        return f"▲ {text.format_number_pt(sr / sa, 1)}×", "sobe", titulo
    seta = "▲" if var > 0 else "▼"
    return f"{seta} {text.format_number_pt(abs(var) * 100, 0)}%", ("sobe" if var > 0 else "cai"), titulo


def destaques(
    corpora: Iterable[CorpusDia],
    *,
    fim: str | None = None,
    secao: str = SECAO,
    secao_titulo: str = "Política",
    pesquisa_href: str = "pesquisa.html",
    janela: int = JANELA_DIAS,
    limite: int = LIMITE,
) -> DestaquesView | None:
    """Os nomes mais citados nas manchetes da editoria nas edições dos últimos
    ``janela`` dias até ``fim`` (padrão: a edição mais recente); ``None`` sem manchetes."""
    todos = sorted((c for c in corpora if c.data), key=lambda c: c.data)
    if not todos:
        return None
    ultimo = (fim or todos[-1].data)[:10]
    try:
        inicio = (date.fromisoformat(ultimo) - timedelta(days=janela - 1)).isoformat()
    except ValueError:
        return None
    dias = [c for c in todos if inicio <= c.data <= ultimo]
    titulos: list[tuple[str, str, str]] = []  # (dia, título, veículo), a mesma notícia uma vez
    vistos: set[str] = set()
    candidatos: Counter[str] = Counter()
    formas: dict[str, Counter[str]] = {}
    for c in dias:
        for link, titulo, veiculo in c.titulos:
            if link not in vistos:
                vistos.add(link)
                titulos.append((c.data, titulo, veiculo))
        candidatos.update(c.candidatos)
        for chave, grafias in c.formas.items():
            formas.setdefault(chave, Counter()).update(grafias)
    if not titulos:
        return None
    base_dia = Counter(d for d, _t, _v in titulos)
    datas = [c.data for c in dias]

    contagens: list[tuple[str, int, list[tuple[str, str]]]] = []  # (nome, manchetes, [(dia, veículo)])
    for chave, _n in candidatos.most_common(CANDIDATOS):
        nome = formas[chave].most_common(1)[0][0]
        regra = padrao(nome)
        if regra is None:
            continue
        achados = [(d, v) for d, t, v in titulos if regra.search(t)]
        if len(achados) >= MIN_MANCHETES:
            contagens.append((nome, len(achados), achados))
    contagens.sort(key=lambda x: (-x[1], norm(x[0])))

    # Um nome que só aparece dentro de outro maior ("Inácio" em "Luiz Inácio Lula da
    # Silva") dá lugar a ele; a forma longa frequente de um nome mais citado vira o
    # "também" dele ("Flávio", também "Flávio Bolsonaro"), e a rara sai ("Exército do Lula").
    escolhidos: list[dict[str, Any]] = []
    for nome, n, achados in contagens:
        chave = _chave(nome)
        absorvido = False
        for e in escolhidos:
            if chave == e["chave"]:
                absorvido = True
            elif _contido(e["chave"], chave):
                absorvido = True
                if n >= 0.9 * e["n"]:
                    e.update(nome=nome, chave=chave, n=n, achados=achados)
                elif n >= 0.25 * e["n"] and not e["tambem"]:
                    e["tambem"] = nome
            if absorvido:
                break
        if not absorvido:
            escolhidos.append({"nome": nome, "chave": chave, "n": n, "achados": achados, "tambem": ""})
    escolhidos = escolhidos[:limite]
    if not escolhidos:
        return None

    comparadas = [x for c in dias for x in c.comparadas]
    maior = max(e["n"] for e in escolhidos)
    nomes = []
    for e in escolhidos:
        nome, n, achados = e["nome"], e["n"], e["achados"]
        regra = padrao(nome)
        por_dia = Counter(d for d, _v in achados)
        tendencia, classe, titulo = _tendencia(por_dia, base_dia, datas)
        splits = [2 * min(a, b) / (a + b) for texto, a, b in comparadas if regra is not None and regra.search(texto)]
        media = sum(splits) / len(splits) if splits else None
        # O analítico do nome abre com a mesma editoria e o mesmo período da tabela.
        href = f"{pesquisa_href}?" + urlencode({"q": consulta(nome), "de": inicio, "ed": secao}) + "#midia"
        nomes.append(
            NomeView(
                nome=nome,
                tambem=e["tambem"],
                consulta=consulta(nome),
                href=href,
                manchetes=n,
                participacao=_pct(n, len(titulos)),
                veiculos=len({v for _d, v in achados}),
                tendencia=tendencia,
                tendencia_classe=classe,
                tendencia_titulo=titulo,
                debates=len(splits),
                divergencia="—" if media is None else text.format_number_pt(media * 100, 0),
                divergencia_valor=media,
                barra=round(100 * n / maior, 1),
            )
        )
    primeira, ultima = (text.pt_short_date(date.fromisoformat(c.data)) for c in (dias[0], dias[-1]))
    periodo = primeira if primeira == ultima else f"{primeira} a {ultima}"
    return DestaquesView(
        secao=secao,
        secao_titulo=secao_titulo,
        inicio=inicio,
        fim=ultimo,
        periodo=periodo,
        edicoes=len(dias),
        manchetes=len(titulos),
        nomes=nomes,
        pesquisa_href=pesquisa_href,
    )
