"""A pauta: por onde passou cada matéria que o Plenário aprovou, o que esperava enquanto
isso, para que temas o Plenário olhou e em que semanas votou.

Tudo aqui é regra sobre o registro oficial, sem julgar intenção. Para cada matéria aprovada
pelo Plenário na janela de :data:`relevancia.JANELA_APROVADAS`, sete **sinais**, todos sim
ou não (limiares e padrões em ``config/congresso.yaml``, bloco ``pauta``):

- ``urgencia``: o Plenário aprovou o requerimento de urgência (o histórico da matéria diz);
- ``plenario``: o parecer foi dado por um relator em Plenário, no lugar das comissões;
- ``relampago``: do protocolo ao voto em até ``relampago_dias``;
- ``no_dia``: a urgência e o texto foram votados no mesmo dia;
- ``sem_nominal``: nenhuma votação nominal do texto principal no Plenário (a aprovação foi
  simbólica: não há lista de quem votou o quê);
- ``proprio``: a ementa casa com um padrão de "regras do próprio jogo" (eleições, partidos,
  mandatos, emendas, cargos das Casas);
- ``fraca``: nota de relevância abaixo de ``relevancia.baixa``.

Nenhum sinal diz que a matéria é ruim nem prova pressa indevida: diz por onde ela passou.
No Senado o registro não traz o caminho (urgência, parecer): valem só ``relampago``,
``sem_nominal``, ``proprio`` e ``fraca``.

As funções são puras e leem as fichas das matérias como :mod:`.materias` as grava
(``materias-<casa>.json``): ``rel`` (a nota, a aprovação e o caminho), ``vot`` (as
votações nominais), ``e`` (a ementa), ``tm`` (os temas), ``o`` (a origem).
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from functools import cache
from typing import Any

from .. import text
from . import base, relevancia
from .regras import Marco, Pauta, Regras

SINAIS = ("urgencia", "plenario", "relampago", "no_dia", "sem_nominal", "proprio", "fraca")
DO_CAMINHO = frozenset({"urgencia", "plenario", "no_dia"})  # só o histórico da Câmara traz o caminho
FORA_DA_LISTA = frozenset({"urgencia"})  # a urgência sozinha (a mais comum) não conta para "vale olhar"
DAS_QUE_ESPERAM = ("proprio", "fraca")  # o que dá para dizer de uma matéria que ainda não foi votada
LISTA = 12  # matérias na lista "vale olhar"
EMENTA = 360  # caracteres da ementa no painel da Casa (a ficha tem a ementa inteira)

ROTULOS = {
    "urgencia": "Com urgência",
    "plenario": "Sem comissão",
    "relampago": "Relâmpago",
    "no_dia": "Urgência no dia",
    "sem_nominal": "Sem registro de voto",
    "proprio": "Regras do próprio jogo",
    "fraca": "Sinais fracos",
}
# A explicação de uma linha de cada sinal ({dias} e {baixa}: os limiares do arquivo de escolhas).
LINHAS = {
    "urgencia": "a urgência dispensa prazos e leva a matéria direto ao Plenário",
    "plenario": "o parecer foi dado no próprio Plenário: nenhuma comissão votou o texto",
    "relampago": "do protocolo ao voto em até {dias} dias",
    "no_dia": "a urgência e o texto foram votados no mesmo dia",
    "sem_nominal": "aprovada em votação simbólica: não há registro de quem votou o quê",
    "proprio": "trata de eleições, partidos, mandatos, emendas ou dos cargos das próprias Casas: quem vota decide sobre regras que valem para si",
    "fraca": "nota de relevância abaixo de {baixa}",
}
# A mesma explicação, curta, para o cartão de cada matéria (cabe numa linha).
CURTAS = {
    "urgencia": "dispensa prazos e leva direto ao Plenário",
    "plenario": "parecer dado no Plenário: nenhuma comissão votou o texto",
    "relampago": "do protocolo ao voto em até {dias} dias",
    "no_dia": "a urgência e o texto, votados no mesmo dia",
    "sem_nominal": "votação simbólica: não há registro de quem votou o quê",
    "proprio": "eleições, partidos, mandatos, emendas ou cargos das Casas",
    "fraca": "nota abaixo de {baixa}",
}
# O que o sinal não prova (dito junto com a explicação, nos filtros e no "Como ler").
RESSALVAS = {
    "urgencia": "Não prova pressa indevida.",
    "relampago": "Matéria que veio da outra Casa ou do Executivo chega com parte do caminho andado.",
    "sem_nominal": "É o caso mais comum.",
    "proprio": "Não diz que a matéria é ruim.",
    "fraca": "A nota não diz se a matéria é boa.",
}


def sinais_da_casa(casa: str) -> tuple[str, ...]:
    """Os sinais que o registro da Casa permite ler."""
    return SINAIS if casa == "cd" else tuple(s for s in SINAIS if s not in DO_CAMINHO)


def linha_do_sinal(sinal: str, regras: Regras, curta: bool = False) -> str:
    """A explicação do sinal, com os limiares do arquivo de escolhas."""
    return (CURTAS if curta else LINHAS)[sinal].format(dias=regras.pauta.relampago_dias, baixa=round(regras.relevancia.baixa))


# ── uma matéria ──────────────────────────────────────────────────────────────


@cache
def _padrao(padrao: str) -> re.Pattern[str]:
    return re.compile(padrao)


def proprio_jogo(ementa: str | None, pauta: Pauta) -> str:
    """O grupo de "regras do próprio jogo" em que a ementa cai (``""``: nenhum). O padrão vale
    sobre a ementa normalizada e tem de começar nos primeiros ``ementa_ate`` caracteres (o
    começo diz do que a matéria trata; o fim costuma citar outras leis)."""
    e = text.normalize(ementa or "")
    if not e:
        return ""
    for grupo in pauta.proprio_jogo:
        for padrao in grupo.padroes:
            achado = _padrao(padrao).search(e)
            if achado and achado.start() < pauta.ementa_ate:
                return grupo.id
    return ""


def tema_de(m: Mapping[str, Any]) -> str:
    """O 1º tema da matéria (``""``: sem tema)."""
    temas = m.get("tm") or []
    return str(temas[0]) if temas else ""


def passou(m: Mapping[str, Any]) -> bool:
    """Aprovada pelo Plenário na janela, com o tempo do protocolo ao voto conhecido."""
    rel = m.get("rel") or {}
    return rel.get("q") == "passou" and isinstance(rel.get("dias"), int) and rel["dias"] >= 0


def espera(m: Mapping[str, Any]) -> bool:
    return (m.get("rel") or {}).get("q") == "espera"


def sinais_da_materia(m: Mapping[str, Any], regras: Regras, casa: str = "cd", votos_desde: str = "", voto_fora: bool = False) -> list[str]:
    """Os sinais de uma matéria aprovada, na ordem de :data:`SINAIS`.

    ``votos_desde``: a data a partir da qual as votações nominais foram lidas (o começo da
    legislatura). De uma matéria aprovada antes disso não dá para dizer que não houve
    votação nominal: o sinal ``sem_nominal`` fica de fora. O mesmo vale com ``voto_fora``:
    a matéria teve uma votação nominal que saiu das contas (o registro um a um não bate com
    o placar anunciado), então houve votação nominal, só não dá para dizer de quê."""
    if not passou(m):
        return []
    rel = m["rel"]
    achados: set[str] = set()
    if casa == "cd":
        caminho = rel.get("cam") or {}
        urgencia = caminho.get("urgencia") or {}
        if urgencia:
            achados.add("urgencia")
            if urgencia.get("d") and urgencia["d"] == rel.get("apv"):
                achados.add("no_dia")
        if caminho.get("parecer_plenario"):
            achados.add("plenario")
    if rel["dias"] <= regras.pauta.relampago_dias:
        achados.add("relampago")
    if not voto_fora and (rel.get("apv") or "") >= votos_desde and not any(v.get("t") == base.MERITO for v in m.get("vot") or []):
        achados.add("sem_nominal")
    if proprio_jogo(m.get("e"), regras.pauta):
        achados.add("proprio")
    if (rel.get("n") or 0) < regras.relevancia.baixa:
        achados.add("fraca")
    return [s for s in SINAIS if s in achados]


def para_a_lista(sinais: Iterable[str]) -> int:
    """Quantos sinais contam para a lista "vale olhar" (a urgência sozinha não conta)."""
    return sum(1 for s in sinais if s not in FORA_DA_LISTA)


def enquanto_isso(m: Mapping[str, Any], esperando: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    """O que esperava pronto no mesmo 1º tema com nota maior que a da matéria aprovada:
    quantas são e a mais antiga pelo protocolo (``t`` e ``da``: guarda a data, não a conta
    de dias). ``None`` para a matéria sem tema; ``{"n": 0}`` quando nenhuma espera."""
    tema = tema_de(m)
    if not tema:
        return None
    nota = (m.get("rel") or {}).get("n") or 0
    acima = sorted(
        (e.get("da") or "9999-99-99", titulo)
        for titulo, e in esperando.items()
        if tema_de(e) == tema and ((e.get("rel") or {}).get("n") or 0) > nota
    )
    if not acima:
        return {"n": 0}
    desde, titulo = acima[0]
    saida: dict[str, Any] = {"n": len(acima), "t": titulo}
    if desde[:4] != "9999":
        saida["da"] = desde
    return saida


def anotar(
    materias: Mapping[str, dict[str, Any]], regras: Regras, casa: str = "cd", votos_desde: str = "", votos_fora: Iterable[str] = ()
) -> None:
    """Grava em cada aprovada ``rel.sin`` (os sinais) e ``rel.enq`` (o "enquanto isso").
    ``votos_fora``: as matérias com votação nominal fora das contas (ver :func:`sinais_da_materia`)."""
    esperando = {t: m for t, m in materias.items() if espera(m)}
    fora = set(votos_fora)
    for titulo, m in materias.items():
        if not passou(m):
            continue
        m["rel"]["sin"] = sinais_da_materia(m, regras, casa, votos_desde, voto_fora=titulo in fora)
        enq = enquanto_isso(m, esperando)
        if enq is not None:
            m["rel"]["enq"] = enq


# ── a Casa ───────────────────────────────────────────────────────────────────


def direcao(aprovadas: Iterable[Mapping[str, Any]], esperando: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Por tema (o 1º de cada matéria): quantas aprovadas (``a``) e quantas prontas esperando
    (``e``). Em cima, os temas com parte maior entre as aprovadas do que entre as que
    esperam; embaixo, o contrário. As matérias sem tema entram no total, não na lista."""
    a, e = Counter(tema_de(m) for m in aprovadas), Counter(tema_de(m) for m in esperando)
    na, ne = sum(a.values()) or 1, sum(e.values()) or 1
    temas = sorted((t for t in set(a) | set(e) if t), key=lambda t: (e[t] / ne - a[t] / na, t))
    return [{"t": t, "a": a[t], "e": e[t]} for t in temas]


def semana_de(dia: str) -> str:
    """A segunda-feira da semana (segunda a domingo) em que o dia cai."""
    d = date.fromisoformat(dia[:10])
    return (d - timedelta(days=d.weekday())).isoformat()


def semanas(aprovadas: Iterable[tuple[str, Mapping[str, Any]]], vezes: float) -> list[dict[str, Any]]:
    """As semanas com aprovação, em ordem: ``d`` (a segunda-feira), ``n``, ``m`` (as matérias,
    pela data do voto) e ``s`` nas semanas cheias: ``n`` ≥ ``vezes`` × a mediana das semanas
    em que houve aprovação."""
    por: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for titulo, m in aprovadas:
        dia = (m.get("rel") or {}).get("apv")
        if dia:
            por[semana_de(dia)].append((dia, titulo))
    if not por:
        return []
    corte = vezes * statistics.median(len(v) for v in por.values())
    return [{"d": d, "n": len(v), "m": [t for _dia, t in sorted(v)], **({"s": 1} if len(v) >= corte else {})} for d, v in sorted(por.items())]


def calendario(marcos: Iterable[Marco], desde: str, ate: str) -> list[dict[str, str]]:
    """As datas do calendário oficial que caem na janela: ``[{nome, de, ate}]``, em ordem."""
    saida = []
    for m in marcos:
        if len(m.de) == 10:
            pares = [(m.de, m.ate if len(m.ate) == 10 else m.de)]
        else:  # MM-DD: todo ano (o período pode virar o ano)
            fim = m.ate or m.de
            pares = [(f"{ano}-{m.de}", f"{ano + (1 if fim < m.de else 0)}-{fim}") for ano in range(int(desde[:4]) - 1, int(ate[:4]) + 1)]
        for de, fim in pares:
            try:
                date.fromisoformat(de), date.fromisoformat(fim)
            except ValueError:
                continue
            if fim >= desde and de <= ate:
                saida.append({"nome": m.nome, "de": de, "ate": fim})
    return sorted(saida, key=lambda x: (x["de"], x["nome"]))


def _mediana(valores: Iterable[int]) -> int | None:
    lista = list(valores)
    return round(statistics.median(lista)) if lista else None


def _cartao(titulo: str, m: Mapping[str, Any]) -> dict[str, Any]:
    """O que a página precisa para escrever uma aprovada sem abrir a ficha."""
    rel = m["rel"]
    d: dict[str, Any] = {
        "t": titulo,
        "e": text.truncate(m.get("e") or "", EMENTA),
        "u": m.get("u") or "",
        "da": m.get("da") or "",
        "apv": rel.get("apv") or "",
        "dias": rel["dias"],
        "n": rel.get("n") or 0,
        "o": m.get("o") or "parlamentar",
        "sin": list(rel.get("sin") or []),
    }
    if m.get("ap"):
        d["ap"] = m["ap"]
    if tema_de(m):
        d["tm"] = tema_de(m)
    if rel.get("cam"):
        d["cam"] = rel["cam"]
    if rel.get("enq"):
        d["enq"] = rel["enq"]
    return d


def painel(materias: Mapping[str, Mapping[str, Any]], regras: Regras, hoje: str, casa: str = "cd") -> dict[str, Any]:
    """O painel da pauta de uma Casa (``casa-<casa>.json``, chave ``pauta``), das fichas já
    anotadas por :func:`anotar`: as contas dos sinais, a lista "vale olhar", a direção por
    tema, a origem, as semanas e o calendário."""
    pauta = regras.pauta
    aprovadas = [(t, m) for t, m in materias.items() if passou(m)]
    esperando = [(t, m) for t, m in materias.items() if espera(m)]
    if not aprovadas and not esperando:
        return {}
    validos = sinais_da_casa(casa)
    sinais = {t: [s for s in m["rel"].get("sin") or [] if s in validos] for t, m in aprovadas}
    conta = {s: sum(1 for lista in sinais.values() if s in lista) for s in validos}
    limiar = pauta.sinais_vale_olhar if casa == "cd" else pauta.sinais_vale_olhar_senado
    vale = sorted(
        ((t, m) for t, m in aprovadas if para_a_lista(sinais[t]) >= limiar),
        key=lambda par: (-para_a_lista(sinais[par[0]]), par[1]["rel"]["dias"], par[0]),
    )
    foco = min(aprovadas, key=lambda par: (-len(sinais[par[0]]), par[1]["rel"]["dias"], par[0]), default=None)
    do_jogo = [(t, m) for t, m in aprovadas if "proprio" in sinais[t]]
    demais = [m["rel"]["dias"] for t, m in aprovadas if "proprio" not in sinais[t]]
    origem = Counter(m.get("o") or "parlamentar" for _t, m in aprovadas)
    lista = semanas(aprovadas, pauta.surto_vezes)
    mediana_semana = statistics.median(s["n"] for s in lista) if lista else 0
    desde = (date.fromisoformat(hoje) - timedelta(days=relevancia.JANELA_APROVADAS)).isoformat()
    saida: dict[str, Any] = {
        "n": len(aprovadas),
        "esperam": len(esperando),
        "hoje": hoje,
        "desde": desde,
        "conta": conta,
        "altas": sum(1 for _t, m in aprovadas if (m["rel"].get("n") or 0) >= regras.relevancia.alta),
        "mediana_dias": _mediana(m["rel"]["dias"] for _t, m in aprovadas),
        "proprio": {
            "n": len(do_jogo),
            "esperam": sum(1 for _t, m in esperando if proprio_jogo(m.get("e"), pauta)),
            "mediana_dias": _mediana(m["rel"]["dias"] for _t, m in do_jogo),
            "mediana_demais": _mediana(demais),
        },
        "vale_n": len(vale),
        "vale": [_cartao(t, m) for t, m in vale[:LISTA]],
        "foco": _cartao(*foco) if foco else None,
        "direcao": direcao((m for _t, m in aprovadas), (m for _t, m in esperando)),
        "sem_tema": {"a": sum(1 for _t, m in aprovadas if not tema_de(m)), "e": sum(1 for _t, m in esperando if not tema_de(m))},
        "origem": dict(sorted(origem.items(), key=lambda par: (-par[1], par[0]))),
        "semanas": lista,
        "mediana_semana": int(mediana_semana) if float(mediana_semana).is_integer() else mediana_semana,
        "surto_de": math.ceil(pauta.surto_vezes * mediana_semana) if lista else 0,
        "calendario": calendario(pauta.calendario, desde, hoje),
        "regras": {"relampago_dias": pauta.relampago_dias, "vale_olhar": limiar, "surto_vezes": pauta.surto_vezes, "tema_minimo": pauta.tema_minimo},
        "linhas": {s: linha_do_sinal(s, regras) for s in validos},
        "curtas": {s: linha_do_sinal(s, regras, curta=True) for s in validos},
    }
    if casa == "cd":
        saida["caminho"] = {
            "os_dois": sum(1 for lista_ in sinais.values() if "urgencia" in lista_ and "plenario" in lista_),
            "nenhum": sum(1 for lista_ in sinais.values() if "urgencia" not in lista_ and "plenario" not in lista_),
        }
    return saida


def pontos(pontos_: Iterable[list[Any]], materias: Mapping[str, Mapping[str, Any]], regras: Regras, casa: str = "cd") -> list[list[Any]]:
    """Os pontos do gráfico (``[dias, nota, grupo, título]``) com o que os filtros precisam:
    o 1º tema e os sinais (das aprovadas, ``rel.sin``; das que esperam, só o que dá para
    dizer antes do voto: "regras do próprio jogo" e "sinais fracos"), separados por espaço."""
    validos = sinais_da_casa(casa)
    saida = []
    for p in pontos_:
        dias, nota, grupo, titulo = p[:4]
        m = materias.get(titulo) or {}
        if grupo:
            sinais = [s for s in (m.get("rel") or {}).get("sin") or [] if s in validos]
        else:
            achados = {"proprio": bool(proprio_jogo(m.get("e"), regras.pauta)), "fraca": nota < regras.relevancia.baixa}
            sinais = [s for s in DAS_QUE_ESPERAM if achados[s]]
        saida.append([dias, nota, grupo, titulo, tema_de(m), " ".join(sinais)])
    return saida
