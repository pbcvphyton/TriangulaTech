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
com maiúscula no meio de outros títulos do dia; palavra que no meio das frases
aparece mais em minúscula, como "eleições", não é nome). A contagem final é a do
analítico do nome na Pesquisa, para os números da tabela baterem com a página que
a linha abre: a mesma regra de busca (sem acento, começo de palavra; até 3 letras,
só a palavra inteira — "Senado" também conta "senador"), todos os veículos, e a
notícia coletada em mais de uma edição conta uma vez, se alguma versão do título
citar o nome, na primeira edição em que citou.
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
from . import tom as tom_mod

SECAO = "politica"  # editoria dos nomes em destaque
JANELA_DIAS = 30  # edições dos últimos 30 dias
SEMANA = 7  # tendência: últimas 7 edições contra as 7 anteriores
LIMITE = 12  # nomes na tabela
CANDIDATOS = 40  # nomes conferidos pela regra de busca antes do corte
MIN_MANCHETES = 5  # menos que isso não entra na tabela
MIN_BASE_SEMANA = 50  # manchetes da editoria por semana para dizer a tendência
MIN_TENDENCIA = 10  # manchetes do nome nas duas semanas para dizer a tendência
ESTAVEL = 0.15  # variação de até 15% é "estável"
# Tom das manchetes (qijournal/render/tom.py): só manchetes de veículos brasileiros.
MIN_TOM = 20  # manchetes do nome para dizer o tom
MIN_TOM_VEICULO = 10  # manchetes do veículo sobre o nome para comparar o tom dele com o dos demais
Z_VEICULO = 2.58  # veículo × demais: 99% (cada nome compara muitos veículos de uma vez)
Z_TENDENCIA = 1.96  # últimas 7 edições × 7 anteriores: 95%
MIN_TOM_SEMANA = 20  # manchetes do nome em cada janela para dizer se o tom mudou
SEMANA_VEICULO = 3  # manchetes do veículo numa semana para a semana contar no padrão
SEMANA_DEMAIS = 10  # manchetes dos demais veículos na mesma semana
PADRAO_SEMANAS = 3  # semanas comparáveis para falar em padrão
PADRAO_FRACAO = 0.75  # "padrão": no mesmo sentido em 3 de cada 4 semanas
VEICULOS_TOM = 10  # veículos na tabela de cada nome

_MARCAS = re.compile("[\u0300-\u036f]")  # acentos (marcas combinantes, depois do NFD)
_PALAVRA = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+(?:['’\-][0-9A-Za-zÀ-ÖØ-öø-ÿ]+)*")
_PONTUACAO = re.compile(r"[:.!?|\u2013\u2014\"\u201c\u201d\u2018']")
_CONECTORES = frozenset({"de", "da", "do", "das", "dos", "del", "di", "van", "von"})
# Meses e dias da semana: sozinhos não são nome ("Março tem inflação"), mas abrem
# nomes ("Marco Rubio", "Segunda Turma do STF", "Outubro Rosa").
CALENDARIO = frozenset(
    """
    janeiro fevereiro marco abril maio junho julho agosto setembro outubro novembro dezembro
    segunda terca quarta quinta sexta sabado domingo
    """.split()
)
# Palavras que nunca fazem parte de um nome (abrem frase com maiúscula: artigos,
# verbos de chamada; selos editoriais em caixa alta, "VÍDEO:", "FATO ou FAKE").
FORA = (
    frozenset(
        """
        a o as os um uma e em no na nos nas de da do das dos ao aos com sem por para pela pelo sob sobre apos ate
        que se como quando onde quem qual mais menos ja nao sim eis
        veja saiba entenda confira assista leia ouca acompanhe conheca descubra
        dia serie
        video videos fato fake exclusivo urgente opiniao editorial analise infografico
        """.split()
    )
    | CALENDARIO
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
    # (link, título normalizado, veículo): todas as manchetes da editoria, de todos os
    # veículos (a base da Pesquisa); a mesma notícia uma vez no dia.
    titulos: list[tuple[str, str, str]] = field(default_factory=list)
    nomes: dict[str, list[str]] = field(default_factory=dict)  # link → nomes do título (veículos brasileiros)
    # nome de uma palavra → (vezes com maiúscula, vezes em minúscula) no meio das frases do dia
    caixa: dict[str, tuple[int, int]] = field(default_factory=dict)
    comparadas: list[tuple[str, int, int]] = field(default_factory=list)  # texto normalizado, lado A, lado B
    originais: dict[str, str] = field(default_factory=dict)  # link → título como publicado (manchetes da editoria)
    assuntos: dict[str, str] = field(default_factory=dict)  # link → assunto da edição (agrupamento), se houver
    exterior: set[str] = field(default_factory=set)  # links de veículos de fora do Brasil


def _palavras(titulo: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0)) for m in _PALAVRA.finditer(titulo)]


def _inicio_de_frase(titulo: str, palavras: list[tuple[int, int, str]], k: int) -> bool:
    """A palavra abre o título ou vem depois de dois-pontos, ponto, travessão ou aspas."""
    return k == 0 or bool(_PONTUACAO.search(titulo[palavras[k - 1][1] : palavras[k][0]]))


def _caixa(titulos: Iterable[str]) -> tuple[Counter[str], Counter[str]]:
    """Quantas vezes cada palavra aparece com maiúscula e em minúscula no meio das frases."""
    maiuscula: Counter[str] = Counter()
    minuscula: Counter[str] = Counter()
    for titulo in titulos:
        palavras = _palavras(titulo)
        for k, (_a, _b, palavra) in enumerate(palavras):
            if not _inicio_de_frase(titulo, palavras, k):
                (maiuscula if palavra[0].isupper() else minuscula)[norm(palavra)] += 1
    return maiuscula, minuscula


def nomes_do_titulo(titulo: str, proprios: set[str]) -> set[str]:
    """Sequências de palavras com maiúscula (com "de", "da"… no meio), sem atravessar
    pontuação. A palavra que abre uma frase só conta se for nome próprio no dia ou sigla."""
    palavras = _palavras(titulo)

    def nome(k: int) -> bool:
        _a, _b, p = palavras[k]
        n = norm(p)
        if len(p) < 2 or not p[0].isupper() or p[0].isdigit():
            return False
        if n in CALENDARIO:  # só abrindo um nome: "Marco Rubio", "Segunda Turma"
            return k + 1 < len(palavras) and junto(k) and nome(k + 1)
        if n in FORA or (n == "vivo" and k > 0 and norm(palavras[k - 1][2]) == "ao"):  # "AO VIVO"
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
    """As manchetes de uma editoria (``secao``), os nomes das manchetes em português
    e os assuntos da cobertura comparada da editoria com os dois lados, de um arquivo
    de busca (:func:`qijournal.render.pesquisa.dados_do_dia`)."""
    fora = set(estrangeiros)
    veiculos = dados.get("veiculos") or []
    secoes = dados.get("secoes") or []

    def secao_de(i: Any) -> str:
        return secoes[i][0] if isinstance(i, int) and 0 <= i < len(secoes) else ""

    def veiculo_de(i: Any) -> str:
        return veiculos[i] if isinstance(i, int) and 0 <= i < len(veiculos) else ""

    assuntos = dados.get("assuntos") or []

    def assunto_de(i: Any) -> str:
        if isinstance(i, int) and 0 <= i < len(assuntos) and isinstance(assuntos[i], list) and assuntos[i]:
            return str(assuntos[i][0] or "")
        return ""

    corpus = CorpusDia(data=str(dados.get("data") or ""))
    nacionais: list[tuple[str, str, str]] = []  # (link, título, veículo), todas as editorias
    vistos: set[str] = set()
    for n in dados.get("noticias") or []:
        try:
            si, ai, vi, titulo, link = n[0], n[1], n[2], n[3], n[4]
        except (IndexError, TypeError):
            continue
        veiculo = veiculo_de(vi)
        if not veiculo or not titulo or link in vistos:
            continue
        vistos.add(link)
        if veiculo not in fora:
            nacionais.append((str(link), str(titulo), veiculo))
        if secao_de(si) == secao:
            corpus.titulos.append((str(link), norm(titulo), veiculo))
            corpus.originais[str(link)] = str(titulo)
            if assunto_de(ai):
                corpus.assuntos[str(link)] = assunto_de(ai)
            if veiculo in fora:
                corpus.exterior.add(str(link))
    maiuscula, minuscula = _caixa(t for _l, t, _v in nacionais)
    # Nome próprio no dia: no meio das frases, em geral com maiúscula.
    proprios = {p for p, n in maiuscula.items() if n >= 2 and n >= 2 * minuscula[p]}
    na_secao = {link for link, _t, _v in corpus.titulos}
    for link, titulo, _v in nacionais:
        if link not in na_secao:
            continue
        achados = sorted(nomes_do_titulo(titulo, proprios))
        if achados:
            corpus.nomes[link] = achados
        for achado in achados:
            chave = norm(achado)
            if " " not in chave:
                corpus.caixa[chave] = (maiuscula[chave], minuscula[chave])
    for c in dados.get("comparadas") or []:
        if secao_de(c.get("s")) != secao or not (c.get("a") and c.get("b")):
            continue
        lados = Counter(o[1] for o in c.get("f") or [] if isinstance(o, list) and len(o) > 1)
        if not (lados["a"] and lados["b"]):  # todos do mesmo lado (ou neutros): não dividiu
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
    tom: TomView | None = None  # tom das manchetes brasileiras e comparação entre veículos


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
    """Últimas ``SEMANA`` edições contra as ``SEMANA`` anteriores, pela participação
    (os mesmos rótulos do analítico do nome na Pesquisa)."""
    if len(dias) < 2 * SEMANA:
        return "—", "ins", f"A tendência compara {SEMANA} edições com as {SEMANA} anteriores."
    recente, anterior = dias[-SEMANA:], dias[-2 * SEMANA : -SEMANA]
    br, ba = sum(base_dia.get(d, 0) for d in recente), sum(base_dia.get(d, 0) for d in anterior)
    if br < MIN_BASE_SEMANA or ba < MIN_BASE_SEMANA:
        return "—", "ins", "Poucas manchetes na editoria para comparar as semanas."
    kr, ka = sum(por_dia.get(d, 0) for d in recente), sum(por_dia.get(d, 0) for d in anterior)
    if kr + ka < MIN_TENDENCIA:
        return "—", "ins", f"Menos de {MIN_TENDENCIA} manchetes do nome nas duas semanas para comparar."
    sr, sa = kr / br, ka / ba
    titulo = (
        f"{kr} manchetes ({_pct(kr, br)}) nas últimas {SEMANA} edições, "
        f"{ka} ({_pct(ka, ba)}) nas {SEMANA} anteriores"
    )
    if not sa:
        return "novo", "sobe", titulo
    if not sr:
        return "zerou", "cai", titulo
    var = (sr - sa) / sa
    if abs(var) <= ESTAVEL:
        return "estável", "est", titulo
    if var >= 1:  # dobrou ou mais: "▲ 5,8×" se lê melhor que "▲ 482%"
        return f"▲ {text.format_number_pt(sr / sa, 1)}×", "sobe", titulo
    seta = "▲" if var > 0 else "▼"
    return f"{seta} {text.format_number_pt(abs(var) * 100, 0)}%", ("sobe" if var > 0 else "cai"), titulo



# ── tom das manchetes por nome e por veículo ────────────────────────────────


def _saldo(valor: float | None) -> str:
    """Saldo do tom (favoráveis − desfavoráveis, em pontos percentuais), com sinal."""
    if valor is None:
        return "—"
    v = round(valor)
    return f"−{abs(v)}" if v < 0 else f"+{v}" if v > 0 else "0"


def comparar(a: list[int], b: list[int]) -> float | None:
    """z entre as médias das notas (−1 desfavorável, +1 favorável, 0 as demais) de
    dois grupos de manchetes, com a variância das duas juntas (um grupo todo neutro
    não vira "diferente" só por não variar); ``None`` sem variação para comparar."""
    if not a or not b:
        return None
    todas = a + b
    media = sum(todas) / len(todas)
    var = sum((x - media) ** 2 for x in todas) / (len(todas) - 1) if len(todas) > 1 else 0.0
    erro = (var * (1 / len(a) + 1 / len(b))) ** 0.5
    return None if erro == 0 else (sum(a) / len(a) - sum(b) / len(b)) / erro


def _nota(t: str) -> int:
    return -1 if t == "neg" else 1 if t == "pos" else 0


def _saldo_de(notas: list[int]) -> float | None:
    return 100 * sum(notas) / len(notas) if notas else None


@dataclass
class ManchetesTom:
    """Uma manchete com o tom dela para o nome."""

    dia: str
    veiculo: str
    titulo: str
    link: str
    assunto: str
    tom: tom_mod.Tom


@dataclass
class VeiculoTomView:
    nome: str
    manchetes: int
    esperado: str  # "1,4×" (manchetes ÷ esperadas pela editoria) ou "—"
    esperado_classe: str  # acima | abaixo | ""
    esperado_valor: float | None  # o mesmo índice, em número
    presenca: str  # "28 de 30 edições"
    desfavoraveis: str
    favoraveis: str
    saldo: str
    saldo_valor: float | None
    demais: str  # saldo dos demais veículos (mesmo nome, mesmo período)
    destaque: str  # "mais crítico" | "mais favorável" | ""
    classe: str  # neg | pos | "" (o destaque)
    alvo: str
    assunto: str  # o assunto que mais rendeu ao veículo sobre o nome
    padrao: str  # "mais crítico em 4 de 5 semanas"
    barra_left: float
    barra_width: float
    pequena: bool  # menos de MIN_TOM_VEICULO manchetes: tom sem comparação
    excesso: bool = False  # fala mais do nome do que o esperado, além do acaso


@dataclass
class ExemploView:
    tipo: str  # neg | pos
    titulo: str
    veiculo: str
    data: str
    href: str
    gatilhos: str


@dataclass
class TomView:
    manchetes: int  # manchetes brasileiras com o nome
    desfavoraveis: str
    favoraveis: str
    neutras: str
    saldo: str
    saldo_valor: float
    classe: str  # neg | pos | est (saldo perto de zero)
    alvo: str
    ataca: str
    barra_left: float
    barra_width: float
    tendencia: str  # "piorou" | "melhorou" | "estável" | ""
    tendencia_classe: str  # cai | sobe | est | ins
    tendencia_titulo: str
    leitura: list[str]
    veiculos: list[VeiculoTomView]
    exemplos: list[ExemploView]
    criticos: int  # veículos mais críticos que os demais (com significância)
    favoraveis_n: int
    veiculos_fora: int = 0  # veículos com menos manchetes, fora da tabela


BARRA_TOM = 50  # a barra do saldo vai de −50 a +50 (saldos de manchetes raramente passam disso)


def _barra(saldo: float | None) -> tuple[float, float]:
    """Barra divergente de −BARRA_TOM a +BARRA_TOM (meio = 50%), em % da largura."""
    if saldo is None:
        return 50.0, 0.0
    v = max(-BARRA_TOM, min(BARRA_TOM, saldo)) * 50 / BARRA_TOM
    return (round(50 + v, 2), round(-v, 2)) if v < 0 else (50.0, round(v, 2))


def _href(link: str) -> str:
    return link if "://" in link else f"https://{link}"


def tom_do_nome(
    itens: list[ManchetesTom],
    *,
    dias: list[str],
    base_veiculo: Mapping[str, int],
    participacao: float,
    fim: str,
) -> TomView | None:
    """O tom das manchetes brasileiras sobre um nome e a comparação entre veículos
    (``base_veiculo``: manchetes de cada veículo na editoria no período;
    ``participacao``: a parte das manchetes da editoria que cita o nome)."""
    if len(itens) < MIN_TOM:
        return None
    n = len(itens)
    notas = [_nota(i.tom.tom) for i in itens]
    neg = sum(1 for i in itens if i.tom.tom == "neg")
    pos = sum(1 for i in itens if i.tom.tom == "pos")
    saldo = _saldo_de(notas) or 0.0
    try:
        ultimo = date.fromisoformat(fim[:10])
    except ValueError:
        ultimo = date.fromisoformat(max(i.dia for i in itens))

    def semana(dia: str) -> int:
        return (ultimo - date.fromisoformat(dia)).days // 7

    # Tendência do tom: as últimas SEMANA edições contra as SEMANA anteriores.
    tendencia, t_classe, t_titulo = "", "ins", f"O tom muda quando as últimas {SEMANA} edições diferem das {SEMANA} anteriores."
    if len(dias) >= 2 * SEMANA:
        recente, anterior = set(dias[-SEMANA:]), set(dias[-2 * SEMANA : -SEMANA])
        nr = [_nota(i.tom.tom) for i in itens if i.dia in recente]
        na = [_nota(i.tom.tom) for i in itens if i.dia in anterior]
        if len(nr) >= MIN_TOM_SEMANA and len(na) >= MIN_TOM_SEMANA:
            z = comparar(nr, na)
            t_titulo = (
                f"Saldo {_saldo(_saldo_de(nr))} em {len(nr)} manchetes nas últimas {SEMANA} edições, "
                f"{_saldo(_saldo_de(na))} em {len(na)} nas {SEMANA} anteriores"
            )
            if z is not None and z <= -Z_TENDENCIA:
                tendencia, t_classe = "piorou", "cai"
            elif z is not None and z >= Z_TENDENCIA:
                tendencia, t_classe = "melhorou", "sobe"
            else:
                tendencia, t_classe = "estável", "est"

    por_veiculo: dict[str, list[ManchetesTom]] = {}
    for i in itens:
        por_veiculo.setdefault(i.veiculo, []).append(i)
    veiculos: list[VeiculoTomView] = []
    criticos = favoraveis = 0
    ordem = sorted(por_veiculo, key=lambda v: (-len(por_veiculo[v]), norm(v)))
    for v in ordem:
        meus = por_veiculo[v]
        k = len(meus)
        minhas = [_nota(i.tom.tom) for i in meus]
        outras = [_nota(i.tom.tom) for i in itens if i.veiculo != v]
        s_v, s_o = _saldo_de(minhas), _saldo_de(outras)
        pequena = k < MIN_TOM_VEICULO
        destaque = classe = padrao = ""
        if not pequena and outras:
            z = comparar(minhas, outras)
            if z is not None and z <= -Z_VEICULO:
                destaque, classe = "mais crítico", "neg"
            elif z is not None and z >= Z_VEICULO:
                destaque, classe = "mais favorável", "pos"
            # Padrão: semana a semana, de que lado dos demais o veículo fica.
            sem_v: dict[int, list[int]] = {}
            sem_o: dict[int, list[int]] = {}
            for i in itens:
                (sem_v if i.veiculo == v else sem_o).setdefault(semana(i.dia), []).append(_nota(i.tom.tom))
            comparaveis = [
                w for w in sem_v if len(sem_v[w]) >= SEMANA_VEICULO and len(sem_o.get(w, [])) >= SEMANA_DEMAIS
            ]
            if len(comparaveis) >= PADRAO_SEMANAS:
                abaixo = sum(1 for w in comparaveis if sum(sem_v[w]) / len(sem_v[w]) < sum(sem_o[w]) / len(sem_o[w]))
                acima = sum(1 for w in comparaveis if sum(sem_v[w]) / len(sem_v[w]) > sum(sem_o[w]) / len(sem_o[w]))
                total = len(comparaveis)
                if abaixo >= PADRAO_FRACAO * total:
                    padrao = f"mais negativo que os demais em {abaixo} de {total} semanas"
                elif acima >= PADRAO_FRACAO * total:
                    padrao = f"mais positivo que os demais em {acima} de {total} semanas"
                else:
                    padrao = f"oscila: mais negativo em {abaixo} e mais positivo em {acima} de {total} semanas"
        criticos += classe == "neg"
        favoraveis += classe == "pos"
        base = base_veiculo.get(v, 0)
        esperado_n = base * participacao
        indice = k / esperado_n if base >= 30 and esperado_n > 0 else None
        # Excesso de atenção que o acaso não explica (contagem de Poisson): z das manchetes contra as esperadas.
        excesso = indice is not None and (k - esperado_n) / esperado_n**0.5 >= Z_VEICULO
        assuntos = Counter(i.assunto for i in meus if i.assunto)
        topo = assuntos.most_common(1)[0] if assuntos else None
        left, width = _barra(s_v)
        veiculos.append(
            VeiculoTomView(
                nome=v,
                manchetes=k,
                esperado="—" if indice is None else f"{text.format_number_pt(indice, 1)}×",
                esperado_classe="" if indice is None else "acima" if indice >= 1.5 else "abaixo" if indice <= 0.67 else "",
                esperado_valor=indice,
                presenca=f"{len({i.dia for i in meus})} de {len(dias)}",
                desfavoraveis=_pct(sum(1 for x in minhas if x < 0), k),
                favoraveis=_pct(sum(1 for x in minhas if x > 0), k),
                saldo=_saldo(s_v),
                saldo_valor=s_v,
                demais=_saldo(s_o),
                destaque=destaque,
                classe=classe,
                alvo=_pct(sum(1 for i in meus if i.tom.alvo), k),
                assunto=f"{topo[0]} ({topo[1]})" if topo and topo[1] >= 2 else "",
                padrao=padrao,
                barra_left=left,
                barra_width=width,
                pequena=pequena,
                excesso=excesso,
            )
        )

    def forca(i: ManchetesTom) -> int:
        return i.tom.neg - i.tom.pos

    exemplos = []
    for tipo, chave in (("neg", lambda i: (forca(i), i.dia)), ("pos", lambda i: (-forca(i), i.dia))):
        candidatos = [i for i in itens if i.tom.tom == tipo]
        if candidatos:
            i = max(candidatos, key=chave)
            exemplos.append(
                ExemploView(tipo, i.titulo, i.veiculo, _data_curta(i.dia), _href(i.link), ", ".join(i.tom.gatilhos))
            )

    left, width = _barra(saldo)
    view = TomView(
        manchetes=n,
        desfavoraveis=_pct(neg, n),
        favoraveis=_pct(pos, n),
        neutras=_pct(n - neg - pos, n),
        saldo=_saldo(saldo),
        saldo_valor=saldo,
        classe="neg" if saldo <= -10 else "pos" if saldo >= 10 else "est",
        alvo=_pct(sum(1 for i in itens if i.tom.alvo), n),
        ataca=_pct(sum(1 for i in itens if i.tom.ataca), n),
        barra_left=left,
        barra_width=width,
        tendencia=tendencia,
        tendencia_classe=t_classe,
        tendencia_titulo=t_titulo,
        leitura=[],
        veiculos=veiculos[:VEICULOS_TOM],
        exemplos=exemplos,
        criticos=criticos,
        favoraveis_n=favoraveis,
        veiculos_fora=max(0, len(veiculos) - VEICULOS_TOM),
    )
    view.leitura = _leitura_tom(view, veiculos, tendencia, t_titulo)
    return view


def _data_curta(iso: str) -> str:
    try:
        return text.pt_short_date(date.fromisoformat(iso[:10]))
    except ValueError:
        return iso


def _leitura_tom(t: TomView, veiculos: list[VeiculoTomView], tendencia: str, t_titulo: str) -> list[str]:
    """Frases automáticas: quem fala mais e por quê, como fala, se há padrão."""
    frases = [
        f"Tom: {t.desfavoraveis} das {text.format_number_pt(t.manchetes, 0)} manchetes brasileiras põem o nome em "
        f"contexto desfavorável e {t.favoraveis} em favorável (saldo {t.saldo}); é alvo de crítica, acusação ou "
        f"punição em {t.alvo} e age contra alguém em {t.ataca}."
    ]
    acima = [v for v in veiculos if v.excesso and not v.pequena]
    if acima:
        mais = max(acima, key=lambda v: (v.esperado_valor or 0, v.manchetes))
        frase = (
            f"Quem mais fala, proporcionalmente: {mais.nome} ({mais.manchetes} manchetes, {mais.esperado} o esperado "
            "pelo que publica na editoria)"
        )
        frases.append(frase + (f", puxado por “{mais.assunto.rsplit(' (', 1)[0]}”." if mais.assunto else "."))
    else:
        frases.append("Nenhum veículo fala do nome bem acima do esperado pelo que publica na editoria.")
    criticos = sorted((v for v in veiculos if v.classe == "neg"), key=lambda v: (v.saldo_valor or 0, -v.manchetes))
    favoraveis = sorted((v for v in veiculos if v.classe == "pos"), key=lambda v: (-(v.saldo_valor or 0), -v.manchetes))
    if criticos:
        v = criticos[0]
        frases.append(
            f"Mais crítico: {v.nome} (saldo {v.saldo}, contra {v.demais} nos demais veículos)"
            + (f"; {v.padrao}." if v.padrao else ".")
        )
    if favoraveis:
        v = favoraveis[0]
        frases.append(
            f"Mais favorável: {v.nome} (saldo {v.saldo}, contra {v.demais} nos demais)"
            + (f"; {v.padrao}." if v.padrao else ".")
        )
    if not criticos and not favoraveis and any(not v.pequena for v in veiculos):
        frases.append("Nenhum veículo destoa dos demais no tom: as diferenças estão dentro do que o acaso explica.")
    if tendencia in ("piorou", "melhorou"):
        frases.append(f"O tom {tendencia} nas últimas {SEMANA} edições ({t_titulo[0].lower() + t_titulo[1:]}).")
    return frases


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
    # Cada notícia (link) com as versões do título, da edição mais antiga à mais recente.
    versoes: dict[str, list[tuple[str, str, str]]] = {}  # link → [(dia, título, veículo)]
    candidatos: Counter[str] = Counter()  # nome → notícias em que aparece (uma vez cada)
    formas: dict[str, Counter[str]] = {}
    caixa: dict[str, list[int]] = {}
    ja: dict[str, set[str]] = {}
    for c in dias:
        for link, titulo, veiculo in c.titulos:
            versoes.setdefault(link, []).append((c.data, titulo, veiculo))
        for link, achados in c.nomes.items():
            vistos = ja.setdefault(link, set())
            for achado in achados:
                chave = norm(achado)
                if chave not in vistos:
                    vistos.add(chave)
                    candidatos[chave] += 1
                    formas.setdefault(chave, Counter())[achado] += 1
        for chave, (cima, baixo) in c.caixa.items():
            soma = caixa.setdefault(chave, [0, 0])
            soma[0] += cima
            soma[1] += baixo
    if not versoes:
        return None
    entrada = {link: vs[0][0] for link, vs in versoes.items()}  # edição em que a notícia apareceu
    base_dia = Counter(entrada.values())
    base_veiculo = Counter(vs[0][2] for vs in versoes.values())  # manchetes de cada veículo na editoria
    datas = [c.data for c in dias]
    corpus_de = {c.data: c for c in dias}

    # (nome, manchetes, [(dia, veículo, link)]): a notícia conta se alguma versão do
    # título cita o nome, na primeira edição em que citou (como nos resultados da busca).
    contagens: list[tuple[str, int, list[tuple[str, str, str]]]] = []
    for chave, _n in candidatos.most_common(CANDIDATOS):
        nome = formas[chave].most_common(1)[0][0]
        cima, baixo = caixa.get(chave, (0, 0))
        if baixo > cima:  # no meio das frases, sai mais em minúscula: palavra comum ("Eleições")
            continue
        regra = padrao(nome)
        if regra is None:
            continue
        achados = []
        for link, vs in versoes.items():
            dia = next((d for d, t, _v in vs if regra.search(t)), None)
            if dia is not None:
                achados.append((dia, vs[0][2], link))
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
            elif _contido(chave, e["chave"]):  # parte de um nome maior já escolhido (empate)
                absorvido = True
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
        por_dia = Counter(d for d, _v, _l in achados)
        # Na base, a notícia que cita o nome fica na edição em que entrou nos resultados.
        base_nome = base_dia.copy()
        for dia, _v, link in achados:
            if dia != entrada[link]:
                base_nome[entrada[link]] -= 1
                base_nome[dia] += 1
        tendencia, classe, titulo = _tendencia(por_dia, base_nome, datas)
        splits = [2 * min(a, b) / (a + b) for texto, a, b in comparadas if regra is not None and regra.search(texto)]
        media = sum(splits) / len(splits) if splits else None
        # O analítico do nome abre com a mesma editoria e o mesmo período da tabela.
        href = f"{pesquisa_href}?" + urlencode({"q": consulta(nome), "de": inicio, "ed": secao}) + "#midia"
        # Tom: as manchetes brasileiras, na versão do título que entrou nos resultados.
        itens_tom = []
        for dia, veiculo, link in sorted(achados):
            c = corpus_de[dia]
            manchete = c.originais.get(link)
            if manchete is None or link in c.exterior:
                continue
            itens_tom.append(
                ManchetesTom(
                    dia, veiculo, manchete, link, c.assuntos.get(link, ""),
                    tom_mod.classificar(manchete, nome, c.nomes.get(link, [])),
                )
            )
        tom_view = tom_do_nome(
            itens_tom, dias=datas, base_veiculo=base_veiculo, participacao=n / len(versoes), fim=ultimo
        )
        nomes.append(
            NomeView(
                nome=nome,
                tambem=e["tambem"],
                consulta=consulta(nome),
                href=href,
                manchetes=n,
                participacao=_pct(n, len(versoes)),
                veiculos=len({v for _d, v, _l in achados}),
                tendencia=tendencia,
                tendencia_classe=classe,
                tendencia_titulo=titulo,
                debates=len(splits),
                divergencia="—" if media is None else text.format_number_pt(media * 100, 0),
                divergencia_valor=media,
                barra=round(100 * n / maior, 1),
                tom=tom_view,
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
        manchetes=len(versoes),
        nomes=nomes,
        pesquisa_href=pesquisa_href,
    )
