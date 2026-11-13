"""Aba "Congresso" (``edicoes/congresso.html``) e as fichas dentro das matérias.

A página responde a uma pergunta: **o que faz quem está sentado lá**. Segue o
desenho da aba Analítico: abre com a leitura (os achados, cada um com a prova),
desce por quatro lentes e fecha com o método.

1. **Com quem vota**: cada parlamentar entre os dois polos, partido a partido, e a
   matriz partido × tema (onde o rótulo e o voto se separam);
2. **A fila**: o que tramita, esperando o quê e com quem, e o que passou na frente;
3. **O Plenário**: as últimas votações nominais, com quem votou contra o próprio partido;
4. **Quem é quem**: as listas (quem falta, quem diverge, quem mudou) e a tabela de
   todos; clicar num nome abre o **dossiê**, carregado de
   ``data/congresso/fichas/<casa>-<id>.json``.

Tudo o que é número sai pronto de :mod:`qijournal.congresso.dados`; aqui só se
arruma para mostrar. Sem script a página continua inteira (o dossiê é o que
depende dele).

As **fichas nas matérias** (:func:`fichas_da_edicao`) são um retrato do dia: os
parlamentares que a matéria cita, com o que os registros dizem de cada um, gravado
na própria edição (``Edition.congresso``).
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .. import text
from ..config import Config
from ..congresso import VERSAO, base, dados, ficha, jornal, projetos, relevancia
from ..models import Edition
from . import filters
from .web import brand_view

log = logging.getLogger(__name__)

PAGINA = "congresso.html"
FAIXA_MIN = {"cd": 8, "sf": 3}  # parlamentares em exercício para o partido ganhar uma faixa
PILHA_PX = 7  # distância vertical entre pontos empilhados na faixa
PILHA_MAX = 9
SELOS = {
    "tema": "Por tema",
    "racha": "Racha",
    "fila": "Fila",
    "pauta": "Pauta",
    "furaram": "Passou na frente",
    "relatoria": "Relatoria",
    "relevancia": "Espera × passa",
    "presenca": "Presença",
    "mudanca": "Mudança",
    "destoa": "Fora da curva",
    "tema_pessoa": "Muda de lado",
}
ORDEM_FILAS = (
    projetos.F_RELATOR,
    projetos.F_PARECER,
    projetos.F_MESA,
    projetos.F_PAUTA,
    projetos.F_COMISSAO,
    projetos.F_RECURSO,
    projetos.F_OUTRA,
)
LADO = {"S": "Sim", "N": "Não", "L": "Liberado", "O": "Obstrução", "": "—"}
VOTO_NOME = {"S": "Sim", "N": "Não", "A": "Abstenção", "O": "Obstrução", "P": "Presidia", "E": "Presente, sem voto", "V": "Votou (secreta)", "J": "Ausência justificada", "F": "Faltou"}


# ── dados ────────────────────────────────────────────────────────────────────


@dataclass
class Dados:
    meta: dict[str, Any]
    resumo: dict[str, Any]
    casas: dict[str, dict[str, Any]]
    achados: list[dict[str, Any]]


def carregar(data_dir: Path) -> Dados | None:
    """O que ``congresso atualizar`` gravou em ``data/congresso/`` (``None`` se faltar
    algum arquivo ou se o formato for de outra versão)."""
    pasta = dados.pasta_de(data_dir)
    meta = dados.ler_json(pasta / dados.META)
    resumo = dados.ler_json(pasta / dados.RESUMO)
    if not isinstance(meta, dict) or not isinstance(resumo, dict) or meta.get("v") != VERSAO or resumo.get("v") != VERSAO:
        return None
    casas = {}
    for casa in ("cd", "sf"):
        painel = dados.ler_json(pasta / f"casa-{casa}.json")
        if isinstance(painel, dict):
            casas[casa] = painel
    if not casas:
        return None
    bruto = dados.ler_json(pasta / dados.ACHADOS)
    achados = (bruto.get("achados") if isinstance(bruto, dict) else None) or []
    return Dados(meta=meta, resumo=resumo, casas=casas, achados=[a for a in achados if isinstance(a, dict)])


# ── utilitários de apresentação ──────────────────────────────────────────────


def _num(valor: Any) -> str:
    try:
        return text.format_number_pt(int(valor), 0)
    except (TypeError, ValueError):
        return "—"


def _pct(valor: Any) -> str:
    if valor is None:
        return "—"
    valor = float(valor)
    if 99.5 <= valor < 100 or 0 < valor < 0.5:
        return f"{text.format_number_pt(valor, 1)}%"
    return f"{round(valor)}%"


def _saldo(valor: Any) -> str:
    return "—" if valor is None else f"{int(round(float(valor))):+d}".replace("-", "−")


def classe_do_saldo(valor: Any, campos: list[dict[str, Any]] | None = None) -> str:
    """A faixa do saldo como classe de cor (``e3 e2 m d2 d3``; ``na`` sem saldo)."""
    if valor is None:
        return "na"
    valor = float(valor)
    limites = [c.get("ate", 0) for c in campos] if campos else [-60, -20, 20, 60, 100]
    for limite, classe in zip(limites, ("e3", "e2", "m", "d2", "d3"), strict=False):
        if valor <= limite:
            return classe
    return "d3"


def _barra(valor: Any) -> tuple[float, float]:
    """``(left %, width %)`` da barra do saldo, que parte do centro (0) para o lado do sinal."""
    if valor is None:
        return 50.0, 0.0
    valor = max(-100.0, min(100.0, float(valor)))
    return (50.0 + valor / 2, -valor / 2) if valor < 0 else (50.0, valor / 2)


def _data(iso: str | None, ano: bool = False) -> str:
    try:
        dia = date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return ""
    return f"{dia.day:02d}/{dia.month:02d}/{dia.year}" if ano else f"{dia.day:02d}/{dia.month:02d}"


def _tempo(dias: Any) -> str:
    """Dias por extenso: "23 dias", "7 meses", "3 anos e 2 meses"."""
    try:
        dias = int(dias)
    except (TypeError, ValueError):
        return "—"
    if dias < 60:
        return f"{dias} {'dia' if dias == 1 else 'dias'}"
    meses = dias // 30
    if meses < 24:
        return f"{meses} meses"
    anos, resto = divmod(meses, 12)
    return f"{anos} anos" + (f" e {resto} {'mês' if resto == 1 else 'meses'}" if resto else "")


# ── views ────────────────────────────────────────────────────────────────────


@dataclass
class Ponto:
    k: str
    nome: str
    left: float
    top: int
    classe: str
    titulo: str


@dataclass
class Faixa:
    sigla: str
    membros: int
    media: str
    altura: int
    pontos: list[Ponto] = field(default_factory=list)


@dataclass
class Celula:
    valor: str
    classe: str
    titulo: str


@dataclass
class LinhaMatriz:
    sigla: str
    membros: int
    geral: Celula
    celulas: list[Celula]


@dataclass
class CasaView:
    id: str
    nome: str
    curto: str
    periodo: str
    em_exercicio: int
    estat: dict[str, Any]
    resumo: str
    faixas: list[Faixa]
    bancadas: list[dict[str, Any]]
    matriz_temas: list[dict[str, Any]]
    temas_faixa: list[dict[str, Any]]  # os temas do seletor de "Onde cada um está"
    matriz: list[LinhaMatriz]
    matriz_base: int
    rachas: dict[str, Any] | None
    relevancia: dict[str, Any] | None
    jornal_voto: list[dict[str, Any]]
    fila: dict[str, Any] | None
    furaram: dict[str, Any] | None
    ultimas: list[dict[str, Any]]
    listas: list[dict[str, Any]]


@dataclass
class CongressoView:
    atualizado: str
    periodo: str
    governo: str
    polos: dict[str, str]
    leitura: dict[str, Any] | None
    casas: list[CasaView]
    pessoas: list[dict[str, Any]]
    fora: int  # linhas de quem não está em exercício (escondidas até pedir)
    metodo: list[tuple[str, str]]
    manifesto: str
    totais: dict[str, Any]


def _faixas(casa: str, pessoas: list[dict[str, Any]], campos: list[dict[str, Any]]) -> list[Faixa]:
    """Uma faixa por partido: cada ponto é um parlamentar em exercício, na posição do saldo dele."""
    por: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in pessoas:
        if p["k"].startswith(casa) and p.get("ex") and p.get("saldo") is not None and p.get("partido"):
            por[p["partido"]].append(p)
    faixas = []
    for sigla, lista in por.items():
        if len(lista) < FAIXA_MIN[casa]:
            continue
        pilha: dict[int, int] = defaultdict(int)
        pontos = []
        for p in sorted(lista, key=lambda p: p["saldo"]):
            left = 50 + p["saldo"] / 2
            casa_pilha = round(left / 1.25)
            nivel = min(pilha[casa_pilha], PILHA_MAX)
            pilha[casa_pilha] += 1
            pontos.append(
                Ponto(
                    k=p["k"],
                    nome=p["nome"],
                    left=round(left, 2),
                    top=nivel * PILHA_PX,
                    classe=classe_do_saldo(p["saldo"], campos),
                    titulo=f"{p['nome']} ({sigla}-{p.get('uf', '')}): saldo {_saldo(p['saldo'])} em {p.get('div', 0)} votações",
                )
            )
        media = sum(p["saldo"] for p in lista) / len(lista)
        faixas.append(
            Faixa(
                sigla=sigla,
                membros=len(lista),
                media=_saldo(media),
                altura=(min(max(pilha.values()), PILHA_MAX + 1)) * PILHA_PX + 8,
                pontos=pontos,
            )
        )
    faixas.sort(key=lambda f: float(f.media.replace("−", "-")))
    return faixas


def _celula(par: list[int] | None, campos: list[dict[str, Any]], sigla: str, tema: str) -> Celula:
    if not par:
        return Celula("·", "na", f"{sigla} · {tema}: menos de 5 votações divisivas")
    saldo, n = par
    lado = "com a direita" if saldo > 0 else "com a esquerda" if saldo < 0 else "dividido"
    return Celula(_saldo(saldo), classe_do_saldo(saldo, campos), f"{sigla} · {tema}: saldo {_saldo(saldo)} ({lado}) em {n} votações divisivas")


def _votacao(v: Mapping[str, Any], casa: str) -> dict[str, Any]:
    partidos = []
    dissidentes = []
    for p in v.get("partidos") or []:
        partidos.append({"sigla": p["sigla"], "lado": LADO.get(p["lado"], p["lado"]), "classe": "s" if p["lado"] == "S" else "n", "coesao": p["coesao"], "votos": p["votos"]})
        contra = p.get("contra")
        if isinstance(contra, list) and contra:
            dissidentes.append({"sigla": p["sigla"], "lado": LADO.get(p["lado"], ""), "nomes": [{"k": f"{casa}-{c['id']}", "nome": c["nome"]} for c in contra if c.get("nome")]})
    aprovada = v.get("aprovada")
    return {
        "id": v.get("id"),
        "data": _data(v.get("data")),
        "hora": v.get("hora") or "",
        "tipo": base.TIPOS.get(v.get("tipo") or "", "votação"),
        "prop": v.get("prop") or "",
        "apelido": v.get("apelido") or "",
        "descricao": v.get("descricao") or "",
        "ementa": v.get("ementa") or "",
        "resultado": "" if aprovada is None else ("Aprovada" if aprovada else "Rejeitada"),
        "classe": "" if aprovada is None else ("ok" if aprovada else "no"),
        "secreta": bool(v.get("secreta")),
        "sim": v.get("sim") or 0,
        "nao": v.get("nao") or 0,
        "abstencao": v.get("abstencao") or 0,
        "governo": LADO.get(v.get("governo") or "", ""),
        "esquerda": LADO.get(v.get("esquerda") or "", ""),
        "direita": LADO.get(v.get("direita") or "", ""),
        "divisiva": bool(v.get("divisiva")),
        "racha": [f"{sigla} com a {com}" for sigla, com in sorted((v.get("racha") or {}).items())],
        "partidos": partidos,
        "dissidentes": dissidentes,
    }


# O gráfico "o que espera × o que passou": nota (0 a 100) × tempo desde o protocolo, em escala log.
GR_W, GR_H = 800, 330
GR_M = {"l": 46, "r": 18, "t": 14, "b": 44}
GR_DIAS = (7, 7300)  # de uma semana a vinte anos
GR_TICKS = ((7, "1 sem."), (30, "1 mês"), (90, "3 meses"), (365, "1 ano"), (730, "2 anos"), (1825, "5 anos"), (3650, "10 anos"), (7300, "20 anos"))


def _gx(dias: float) -> float:
    d = min(max(float(dias), GR_DIAS[0]), GR_DIAS[1])
    largura = GR_W - GR_M["l"] - GR_M["r"]
    return round(GR_M["l"] + largura * (math.log10(d) - math.log10(GR_DIAS[0])) / (math.log10(GR_DIAS[1]) - math.log10(GR_DIAS[0])), 1)


def _gy(nota: float) -> float:
    altura = GR_H - GR_M["t"] - GR_M["b"]
    return round(GR_M["t"] + altura * (1 - max(0.0, min(100.0, float(nota))) / 100), 1)


def _sinais(i: Mapping[str, Any]) -> list[str]:
    """A nota decomposta, em palavras: de onde vêm os pontos."""
    pontos = i.get("pt") or {}
    saida = []
    if i.get("ap"):
        saida.append(f"{_num(i['ap'])} {'projeto parecido anexado' if i['ap'] == 1 else 'projetos parecidos anexados'} (+{pontos.get('demanda', 0)})")
    if pontos.get("origem"):
        saida.append(f"{relevancia.ORIGENS.get(i.get('o') or '', '')} (+{pontos['origem']})")
    if pontos.get("alcance"):
        saida.append(f"{(i.get('t') or '').split(' ')[0]} (+{pontos['alcance']})")
    if pontos.get("apoio"):
        saida.append(f"autores de mais de um partido (+{pontos['apoio']})")
    if i.get("simb"):
        saida.append("simbólica: nota limitada")
    return saida


def _relevancia(r: Mapping[str, Any], casa: str, alta: float, baixa: float) -> dict[str, Any] | None:
    if not r or not (r.get("esperam_n") or r.get("passaram_n")):
        return None
    pontos = []
    for dias, nota, grupo, titulo in r.get("pontos") or []:
        pontos.append(
            {
                "x": _gx(dias), "y": _gy(nota), "g": "a" if grupo else "e", "t": titulo, "n": nota,
                "tempo": _tempo(dias), "situacao": "aprovada" if grupo else "esperando",
            }
        )
    esperam = [
        {
            "t": i["t"], "u": filters.safe_url(i.get("u")) or "", "e": i.get("e") or "", "n": i["n"], "sinais": _sinais(i),
            "pronta": _data(i.get("pronta"), ano=True), "idade": _tempo(i.get("idade")), "urg": bool(i.get("urg")),
            "autoria": i.get("autoria") or "", "apel": i.get("apel") or "", "jor": i.get("jor") or 0,
        }
        for i in r.get("esperam") or []
    ]
    passaram = []
    for i in r.get("passaram") or []:
        caminho = i.get("caminho") or {}
        passos = []
        if caminho.get("urgencia"):
            u = caminho["urgencia"]
            passos.append(f"urgência aprovada em {_data(u.get('d'), ano=True)}" + (f", a pedido de {u['por']}" if u.get("por") else ""))
        if caminho.get("parecer_plenario"):
            p = caminho["parecer_plenario"]
            passos.append("parecer dado direto no Plenário, no lugar das comissões" + (f", pelo relator {p['relator']}" if p.get("relator") else ""))
        passaram.append(
            {
                "t": i["t"], "u": filters.safe_url(i.get("u")) or "", "e": i.get("e") or "", "n": i["n"], "sinais": _sinais(i),
                "apel": i.get("apel") or "", "jor": i.get("jor") or 0,
                "aprovada": _data(i.get("aprovada"), ano=True), "tempo": _tempo(i.get("dias")), "dias": i.get("dias"),
                "rapida": (i.get("dias") or 0) < relevancia.RAPIDA, "passos": passos, "autoria": i.get("autoria") or "",
            }
        )
    largura = GR_W - GR_M["l"] - GR_M["r"]
    return {
        "esperam_n": _num(r.get("esperam_n") or 0),
        "passaram_n": _num(r.get("passaram_n") or 0),
        "fortes_paradas": _num(r.get("fortes_paradas") or 0),
        "fracas_rapidas": _num(r.get("fracas_rapidas") or 0),
        "rapidas": _num(r.get("rapidas") or 0),
        "mediana_esperam": r.get("mediana_esperam"),
        "mediana_passaram": r.get("mediana_passaram"),
        "sem_demanda": bool(r.get("sem_demanda")),
        "esperam": esperam,
        "passaram": passaram,
        "grafico": {
            "w": GR_W, "h": GR_H, "l": GR_M["l"], "r": GR_W - GR_M["r"], "t": GR_M["t"], "b": GR_H - GR_M["b"], "largura": largura,
            "xticks": [{"x": _gx(d), "rotulo": rotulo, "fim": d == GR_TICKS[-1][0]} for d, rotulo in GR_TICKS],
            "yticks": [{"y": _gy(v), "rotulo": str(v)} for v in (0, 25, 50, 75, 100)],
            "alta": _gy(alta), "baixa": _gy(baixa), "x_rapida": _gx(relevancia.RAPIDA), "x_ano": _gx(365),
            "pontos": pontos,
        },
        "tabela": sorted(
            ({"t": p["t"], "situacao": p["situacao"], "n": p["n"], "tempo": p["tempo"]} for p in pontos),
            key=lambda p: (-p["n"], p["situacao"], p["t"]),
        ),
        "alta": round(alta),
        "baixa": round(baixa),
    }


def _jornal_voto(itens: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """No jornal × no voto, na Casa: a manchete que cita alguém e uma proposição, e como ele votou nela."""
    saida = []
    for i in itens:
        votos = [
            {"data": _data(v.get("d"), ano=True),
             "tipo": base.TIPOS.get(v.get("t") or "", "votação") + (f", {v['tu']}º turno," if v.get("tu") else ""),
             "voto": VOTO_NOME.get(v.get("x") or "", v.get("x") or ""),
             "resultado": "" if v.get("ap") is None else ("aprovada" if v.get("ap") else "rejeitada")}
            for v in i.get("votos") or []
        ]
        saida.append(
            {
                "k": i["k"], "nome": i["nome"], "legenda": f"{i.get('partido', '')}-{i.get('uf', '')}".strip("-"),
                "data": _data(i.get("d"), ano=True), "titulo": i.get("t") or "", "veiculo": i.get("v") or "",
                "u": filters.safe_url(i.get("u")) or "", "materia": i.get("id") or "",
                "prop": i.get("p") or "", "apel": i.get("apel") or "", "votos": votos, "mais": max(0, (i.get("n_votos") or 0) - len(votos)),
            }
        )
    return saida


def _rachas(r: Mapping[str, Any], temas_nomes: Mapping[str, str]) -> dict[str, Any] | None:
    """Onde cada polo racha: por partido, as votações em que ele ficou com o outro polo."""
    if not r or not r.get("partidos"):
        return None
    partidos = []
    for p in r["partidos"]:
        itens = []
        for i in p.get("itens") or []:
            itens.append(
                {
                    "data": _data(i.get("d"), ano=True),
                    "tipo": base.TIPOS.get(i.get("t") or "", "votação"),
                    "prop": i.get("p") or "",
                    "ementa": i.get("e") or "",
                    "temas": [temas_nomes.get(t, t) for t in i.get("tm") or []][:2],
                    "lado": LADO.get(i.get("lado") or "", ""),
                    "parte": i.get("parte"),
                    "polo": LADO.get(i.get("polo") or "", ""),
                    "gov": LADO.get(i.get("gov") or "", ""),
                    "resultado": "" if i.get("ap") is None else ("Aprovada" if i.get("ap") else "Rejeitada"),
                }
            )
        partidos.append(
            {
                "sigla": p["sigla"],
                "polo": p["polo"],
                "com": p["com"],
                "classe": "e3" if p["com"] == "esquerda" else "d3",
                "n": _num(p["n"]),
                "n_int": p["n"],
                "contra_governo": _num(p.get("contra_governo") or 0),
                "com_governo": _num(p.get("com_governo") or 0),
                "temas": [{"nome": t["nome"], "n": _num(t["n"])} for t in (p.get("temas") or [])[:5]],
                "itens": itens,
                "mais": max(0, p["n"] - len(itens)),
            }
        )
    return {"opostas": _num(r.get("opostas") or 0), "partidos": partidos}


def _fila(fila: Mapping[str, Any], casa: str) -> dict[str, Any] | None:
    if not fila or not fila.get("tramitando"):
        return None
    total = fila["tramitando"]
    blocos = []
    for chave in ORDEM_FILAS:
        b = (fila.get("filas") or {}).get(chave)
        if not b or not b.get("n"):
            continue
        blocos.append(
            {
                "id": chave,
                "nome": projetos.FILAS[chave],
                "quem": projetos.QUEM_SEGURA.get(chave, ""),
                "n": _num(b["n"]),
                "largura": round(100 * b["n"] / total, 1),
                "parte": _pct(100 * b["n"] / total) if b["n"] / total >= 0.005 else "menos de 1%",
                "mediana": _tempo(b.get("mediana_dias")),
                "parados": _num(b.get("parados") or 0),
                "parados_parte": _pct(100 * (b.get("parados") or 0) / b["n"]),
            }
        )
    return {
        "tramitando": _num(total),
        "apensadas": _num(fila.get("apensadas") or 0),
        "blocos": blocos,
        "orgaos": [
            {
                "orgao": o["orgao"],
                "sem_relator": _num(o["sem_relator"]),
                "pauta": _num(o["pauta"]),
                "com_relator": _num(o.get("com_relator") or 0),
                "mais_antiga": _tempo(o.get("mais_antiga_dias")),
                "presidente": (o.get("presidente") or {}).get("nome") or "",
                "presidente_k": f"{casa}-{o['presidente']['id']}" if o.get("presidente") else "",
            }
            for o in fila.get("orgaos") or []
        ],
        "sentados": [
            {"k": f"{casa}-{s['id']}", "nome": s.get("nome") or "", "n": _num(s["n"]), "antigas": _num(s.get("antigas") or 0), "mais_antiga": _tempo(s.get("mais_antiga_dias"))}
            for s in fila.get("sentados") or []
            if s.get("nome")
        ],
        "prontas_n": _num(fila.get("prontas_plenario_n") or 0),
        "prontas": [
            {"t": i["t"], "apel": i.get("ap") or "", "e": i.get("e") or "", "u": filters.safe_url(i.get("u")) or "", "tempo": _tempo(i.get("dias")), "desde": _data(i.get("m") or i.get("d"), ano=True)}
            for i in fila.get("prontas_plenario") or []
        ],
    }


def _furaram(f: Mapping[str, Any]) -> dict[str, Any] | None:
    if not f or not f.get("aprovadas"):
        return None
    return {
        "desde": _data(f.get("desde"), ano=True),
        "aprovadas": _num(f["aprovadas"]),
        "rapidas_n": _num(f.get("rapidas_n") or 0),
        "mediana": _tempo(f.get("mediana_dias")),
        "com_urgencia": _num(f.get("com_urgencia") or 0),
        "rapidas": [
            {"p": i["p"], "e": i.get("e") or "", "tempo": _tempo(i["dias"]), "data": _data(i.get("d"), ano=True), "urg": bool(i.get("urg")), "autoria": i.get("autoria") or ""}
            for i in f.get("rapidas") or []
        ],
    }


_LISTAS = (
    ("ausentes", "Quem menos vota", "A parte das votações nominais em que cada um podia votar e votou. É voto registrado, não comparecimento: quem estava na Casa e não votou também conta. Licenças do mandato e, no Senado, ausências justificadas ficam fora da conta.", "pct", "votou em"),
    ("dissidentes", "Quem mais vota contra o próprio partido", "A parte das votações em que cada um votou com a maioria do partido do dia.", "pct", "com o partido em"),
    ("mudam_tema", "Quem muda de lado conforme o assunto", "Quem vota de um lado em quase tudo e do outro num tema: o saldo daquele tema contra o geral. Só entra o que passa do acaso, contando todas as pessoas e temas comparados.", "tema", ""),
    ("destoam", "Quem vota longe da própria bancada", "O saldo de cada um contra a média do partido (−100 = sempre com a esquerda; +100 = sempre com a direita). De quem trocou de partido, só contam os votos dados no partido atual.", "saldo", ""),
    ("mudaram", "Quem mudou em relação ao governo", "O último semestre contra os anteriores do mesmo governo: só entra quem mudou mais de 15 pontos, com 99% de confiança.", "mud", ""),
    ("parados", "Quem tem mais propostas paradas", "Proposições de autoria principal que tramitam e não têm andamento há mais de um ano.", "n", ""),
    ("citados", "Os mais citados no jornal", "Manchetes dos últimos 30 dias que citam o nome, e o que os registros dizem dele.", "n", ""),
)


def _listas(listas: Mapping[str, Any]) -> list[dict[str, Any]]:
    saida = []
    for chave, titulo, dek, tipo, prefixo in _LISTAS:
        itens = listas.get(chave) or []
        if not itens:
            continue
        linhas = []
        for i in itens[:10]:
            if tipo == "pct":
                valor, extra = _pct(i.get("pct")), f"{_num(i.get('n'))} votações"
            elif tipo == "tema":
                valor, extra = f"{_saldo(i.get('tema_saldo'))} em {i.get('tema_nome') or ''}", f"geral {_saldo(i.get('saldo'))} · {_num(i.get('n'))} votações"
            elif tipo == "saldo":
                valor, extra = _saldo(i.get("saldo")), f"partido: {_saldo(i.get('partido_saldo'))}" + (" · desde que entrou" if i.get("no_partido") else "")
            elif tipo == "mud":
                antes, agora = (i.get("antes") or {}).get("pct"), (i.get("agora") or {}).get("pct")
                valor, extra = f"{_pct(antes)} → {_pct(agora)}", f"semestre {str(i.get('semestre') or '').replace('-', '/')}"
            elif chave == "parados":
                valor, extra = _num(i.get("n")), f"de {_num(i.get('total'))} propostas"
            else:
                valor, extra = _num(i.get("n")), i.get("frase") or ""
            linhas.append({"k": i["k"], "nome": i["nome"], "legenda": f"{i.get('partido', '')}-{i.get('uf', '')}".strip("-"), "valor": valor, "extra": extra})
        saida.append({"id": chave, "titulo": titulo, "dek": dek, "prefixo": prefixo, "linhas": linhas})
    return saida


def _casa(
    casa: str,
    painel: Mapping[str, Any],
    pessoas: list[dict[str, Any]],
    campos: list[dict[str, Any]],
    temas_nomes: Mapping[str, str] | None = None,
    relevancia_limites: tuple[float, float] | None = None,
) -> CasaView:
    temas_nomes = temas_nomes or {}
    relev_alta, relev_baixa = relevancia_limites or (60.0, 30.0)
    estat = painel.get("votacoes") or {}
    matriz = painel.get("temas") or {}
    temas_cols = (matriz.get("temas") or [])[:11]
    membros = {b["sigla"]: b["membros"] for b in painel.get("bancadas") or []}
    linhas = []
    for sigla, por_tema in (matriz.get("partidos") or {}).items():
        linhas.append(
            LinhaMatriz(
                sigla=sigla,
                membros=membros.get(sigla, 0),
                geral=_celula(por_tema.get("todas"), campos, sigla, "todas as votações de conteúdo"),
                celulas=[_celula(por_tema.get(t["id"]), campos, sigla, t["nome"]) for t in temas_cols],
            )
        )
    linhas.sort(key=lambda linha: float(linha.geral.valor.replace("−", "-")) if linha.geral.valor not in ("·", "—") else 0.0)
    bancadas = []
    for b in painel.get("bancadas") or []:
        left, width = _barra(b.get("saldo"))
        bancadas.append(
            {
                "sigla": b["sigla"],
                "membros": b["membros"],
                "saldo": _saldo(b.get("saldo")),
                "classe": classe_do_saldo(b.get("saldo"), campos),
                "left": round(left, 1),
                "width": round(width, 1),
                "gov": _pct(b.get("gov")),
                "gov_w": round(float(b.get("gov") or 0), 1),
                "coesao": _pct(b.get("coesao")),
                "n": b.get("n") or 0,
            }
        )
    curto = "Câmara" if casa == "cd" else "Senado"
    resumo = (
        f"{_num(painel.get('em_exercicio'))} {'deputados' if casa == 'cd' else 'senadores'} em exercício; "
        f"{_num(estat.get('total'))} votações nominais de Plenário desde {_data(estat.get('primeira'), ano=True)}"
        + (f" ({_num(estat.get('secretas'))} secretas)" if estat.get("secretas") else "")
        + f"; {_num(estat.get('divisivas'))} votações de conteúdo dividiram os polos."
    )
    return CasaView(
        id=casa,
        nome=str(painel.get("nome") or curto),
        curto=curto,
        periodo=str(painel.get("periodo") or ""),
        em_exercicio=int(painel.get("em_exercicio") or 0),
        estat=dict(estat),
        resumo=resumo,
        faixas=_faixas(casa, pessoas, campos),
        bancadas=bancadas,
        matriz_temas=temas_cols,
        temas_faixa=_temas_faixa(matriz),
        matriz=linhas,
        matriz_base=int(matriz.get("divisivas") or 0),
        rachas=_rachas(painel.get("rachas") or {}, temas_nomes),
        relevancia=_relevancia(painel.get("relevancia") or {}, casa, relev_alta, relev_baixa),
        jornal_voto=_jornal_voto((painel.get("listas") or {}).get("jornal_voto") or []),
        fila=_fila(painel.get("fila") or {}, casa),
        furaram=_furaram(painel.get("furaram") or {}),
        ultimas=[_votacao(v, casa) for v in painel.get("ultimas") or []],
        listas=_listas(painel.get("listas") or {}),
    )


def _temas_faixa(matriz: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Os temas do seletor de "Onde cada um está". Tema com menos votações esquerda × direita
    que o mínimo da faixa não põe ninguém nela: fica à vista, desabilitado, com o motivo. Sem
    nenhum tema que passe do mínimo, não há seletor."""
    temas = [
        {"id": t["id"], "nome": t["nome"], "n": t["n"], "poucas": t["n"] < ficha.MINIMO_TEMA_FAIXA}
        for t in matriz.get("temas") or []
        if t.get("n")
    ]
    return temas if any(not t["poucas"] for t in temas) else []


def _leitura(achados: list[dict[str, Any]], d: Dados) -> dict[str, Any] | None:
    if not achados:
        return None
    principal, *demais = achados
    casas = d.meta.get("casas") or {}
    total = sum((casas.get(c) or {}).get("em_exercicio") or 0 for c in ("cd", "sf"))
    votacoes = sum(((casas.get(c) or {}).get("votacoes") or {}).get("total") or 0 for c in ("cd", "sf"))
    leg = d.meta.get("legislatura") or {}
    return {
        "chapeu": "A leitura",
        "manchete": principal["titulo"],
        "principal": {"selo": SELOS.get(principal.get("tipo"), "Achado"), "texto": principal.get("texto") or "", "href": principal.get("alvo") or "#cg-polos", "ficha": principal.get("ficha") or ""},
        "linha_fina": (
            f"Do registro oficial de {_num(total)} parlamentares em exercício e {_num(votacoes)} votações nominais da legislatura "
            f"{leg.get('rotulo', '')}: o que cada um vota, propõe e deixa parado."
        ),
        "achados": [
            {
                "selo": SELOS.get(a.get("tipo"), "Achado"),
                "numero": i,
                "titulo": a["titulo"],
                "texto": a.get("texto") or "",
                "href": a.get("alvo") or "#cg-polos",
                "ficha": a.get("ficha") or "",
            }
            for i, a in enumerate(demais[:9], 2)
        ],
    }


def _pessoas(resumo: Mapping[str, Any], campos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    linhas = []
    for p in resumo.get("pessoas") or []:
        left, width = _barra(p.get("saldo"))
        casa = p["k"].split("-", 1)[0]
        linhas.append(
            {
                "k": p["k"],
                "nome": p["nome"],
                "casa": casa,
                "casa_nome": "Câmara" if casa == "cd" else "Senado",
                "partido": p.get("partido") or "",
                "uf": p.get("uf") or "",
                "ex": bool(p.get("ex")),
                "saldo": p.get("saldo"),
                "saldo_txt": _saldo(p.get("saldo")),
                "classe": classe_do_saldo(p.get("saldo"), campos),
                "left": round(left, 1),
                "width": round(width, 1),
                "gov": p.get("gov"),
                "gov_txt": _pct(p.get("gov")),
                "pres": p.get("pres"),
                "pres_txt": _pct(p.get("pres")),
                "fid": p.get("fid"),
                "fid_txt": _pct(p.get("fid")),
                "prop": p.get("prop") or 0,
                "par": p.get("par") or 0,
                "norma": p.get("norma") or 0,
                "rel": p.get("rel_ant") or 0,
                "jor": p.get("jor") or 0,
                "busca": text.normalize(f"{p['nome']} {p.get('partido', '')} {p.get('uf', '')}"),
            }
        )
    return linhas


def _metodo_relevancia(rel: Mapping[str, Any]) -> str:
    pesos = rel.get("pesos") or {}
    origem = rel.get("origem") or {}
    alcance = rel.get("alcance") or {}

    def pts(chave: str, fator: Any) -> str:
        return _num(round(float(pesos.get(chave) or 0) * float(fator or 0)))

    return (
        "As matérias prontas para o Plenário e as aprovadas pelo Plenário nos últimos 12 meses ganham a mesma nota, de 0 a 100, "
        "feita de sinais que dá para medir nos registros. "
        f"Projetos parecidos: até {_num(pesos.get('demanda') or 0)} pontos, pelos projetos anexados a ela"
        + (f" nos {_num(rel['janela_demanda_anos'])} anos antes" if rel.get("janela_demanda_anos") else "")
        + f" (outros parlamentares pedindo o mesmo; {_num(rel.get('demanda_cheia') or 15)} "
        "ou mais dão a nota cheia, em escala logarítmica); só na Câmara, porque o Senado não informa. Uma matéria mais "
        "antiga teve mais tempo para juntar projetos parecidos; quando o Plenário vota uma matéria, os parecidos mais antigos "
        "costumam ser anexados a ela. "
        f"Origem: até {_num(pesos.get('origem') or 0)} pontos (já aprovada pela outra Casa, {pts('origem', origem.get('outra_casa'))}; do "
        f"Executivo, {pts('origem', origem.get('executivo'))}; de comissão, {pts('origem', origem.get('comissao'))}; do Judiciário ou órgão "
        f"de controle, {pts('origem', origem.get('judiciario'))}; de um parlamentar, {pts('origem', origem.get('parlamentar'))}). "
        f"Alcance: até {_num(pesos.get('alcance') or 0)} pontos (PEC, {pts('alcance', alcance.get('PEC'))}; lei complementar, "
        f"{pts('alcance', alcance.get('PLP'))}; lei, {pts('alcance', alcance.get('PL'))}). "
        f"Apoio: até {_num(pesos.get('apoio') or 0)} pontos, se os autores são de mais de um partido (a nota cheia, se dos dois polos). "
        "No Senado, sem os projetos parecidos, os outros pesos valem o todo. Matéria simbólica (dia nacional, denominação, título, "
        f"patrimônio cultural) não passa de {_num(rel.get('simbolica_teto') or 10)}. A nota não diz se a matéria é boa: diz quantos "
        "sinais de relevância pública ela tem. Na Câmara, a data da aprovação e o caminho (urgência aprovada e a pedido de quem, "
        "parecer dado direto no Plenário) vêm do histórico oficial de cada matéria; no Senado, da lista de deliberações do Plenário. "
        "Medidas provisórias ficam de fora: têm prazo na Constituição."
    )


def _metodo(d: Dados) -> list[tuple[str, str]]:
    m = d.meta
    lim = m.get("limiares") or {}
    polos = m.get("polos") or {}
    esquerda, direita = ", ".join(polos.get("esquerda") or []), ", ".join(polos.get("direita") or [])
    governos = "; ".join(
        f"{g['nome']} ({g['inicio'][:4]}–{g['fim'][:4] if g.get('fim') else 'hoje'})" for g in m.get("governos") or []
    )
    jor = m.get("jornal") or {}
    return [
        (
            "De onde vem",
            "Dos dados abertos da Câmara dos Deputados (arquivos em lote e API) e do Senado Federal (API legislativa), "
            "baixados todo dia antes da edição. São registros oficiais; nenhuma IA entra nas contas.",
        ),
        (
            "Só votação nominal",
            "Só as votações que registram o voto de cada um, no Plenário. A votação simbólica, que é a maioria, não diz quem "
            "votou o quê. No Senado, as votações secretas (escolha de autoridades) contam para a presença, não para a posição.",
        ),
        (
            "Com quem vota",
            f"Entram só as votações de conteúdo (texto principal, destaques e emendas) em que a maioria do polo de esquerda "
            f"({esquerda}) votou de um jeito e a do polo de direita ({direita}), de outro, com pelo menos "
            f"{round(100 * float(lim.get('coesao_polo', 0.75)))}% de cada polo do mesmo lado. O saldo vai de −100 (sempre com a esquerda) a "
            f"+100 (sempre com a direita) e só aparece com {lim.get('minimo_divisivas', 10)} votações ou mais. É comportamento, "
            "não rótulo: diz com quem a pessoa vota, não o que ela diz ser.",
        ),
        (
            "Os rachas",
            "Se um partido de um polo vota fechado com o outro (o PSOL com o PL contra o PT, por exemplo), a votação sai do saldo. "
            "Em geral são os dois extremos votando contra o governo por razões opostas: o PSOL contra uma regra fiscal por achá-la "
            "dura demais, o PL por ser oposição. Contada no saldo, ela faria o PSOL parecer menos de esquerda que o PT. Essas "
            "votações aparecem à parte, em \"Onde cada polo racha\", com o tema e a orientação do governo de cada uma.",
        ),
        (
            "Por que só conteúdo",
            "Requerimentos de retirada de pauta, adiamento e urgência são manobra de plenário: medem quem obstrui e quem toca a "
            "pauta. Entram no governismo e na presença, mas não no saldo entre os polos.",
        ),
        (
            "Governismo",
            f"Nas votações em que a liderança do Governo orientou Sim ou Não, a parte em que a pessoa votou igual. Contado à parte "
            f"em cada governo ({governos}): quem acompanha qualquer governo aparece assim. Obstrução conta como Não.",
        ),
        (
            "Presença",
            "A parte das votações nominais em que a pessoa podia votar e votou. Na Câmara, só contam os dias em que o deputado "
            "estava em exercício (o histórico do mandato diz quando ele se licenciou ou quando o suplente assumiu); os dados "
            "abertos da Câmara não dizem se uma ausência foi justificada. No Senado, a própria votação registra quem faltou e quem "
            "tinha ausência justificada (licença, missão, atividade parlamentar): a justificada fica fora da conta e aparece à "
            "parte na ficha. Quem preside a Casa só vota em caso de empate e não entra na lista de quem menos vota.",
        ),
        (
            "Partido e dissidência",
            f"A fidelidade é a parte dos votos iguais ao da maioria do partido do dia. Dissidência é votar diferente quando "
            f"{round(100 * float(lim.get('coesao_partido', 0.8)))}% ou mais do partido votou junto. Surpresa é quem tem lado definido "
            "(saldo além de ±60) e, numa votação divisiva, ficou com o outro polo.",
        ),
        (
            "Fora da linha, por assunto",
            "No dossiê, cada voto contra o próprio partido fechado e cada voto com o outro polo é contado no tema da matéria. "
            "\"Muda de lado conforme o assunto\" é quem tem lado definido (saldo além de ±60) e, num tema com 10 votações ou "
            "mais, fica do lado oposto: o voto naquele tema é comparado com o resto dos votos da própria pessoa (teste binomial "
            "exato). Na lista da Casa, a diferença precisa passar do acaso contando todas as pessoas e temas comparados (Holm, "
            "99%); no dossiê, contando os temas da pessoa.",
        ),
        (
            "Onde cada um está, por tema",
            f"Escolhido um tema, cada ponto vai para o saldo da pessoa nas votações esquerda × direita daquele tema; quem tem "
            f"menos de {ficha.MINIMO_TEMA_FAIXA} no tema fica de fora, e os partidos se reordenam pela média. O contorno marca "
            "quem vira de lado no tema, pelo mesmo teste do dossiê. Tema com menos votações do que isso na Casa aparece "
            "desabilitado no seletor.",
        ),
        (
            "Na Casa",
            "O dossiê mostra a posição da pessoa em cada número (presença, governismo, fidelidade, votos contra o partido, "
            "propostas, paradas, normas, relatorias, jornal), entre os que estão em exercício na mesma Casa. O 1º é quem tem o "
            "maior número; empate divide a posição. Quem preside a Casa fica fora do ranking de presença.",
        ),
        (
            "Temas",
            "Cada votação herda o tema da matéria votada: na Câmara, a área temática dada pelo Centro de Documentação; no Senado, a "
            "classe do processo. As duas classificações foram levadas a uma lista comum de macrotemas (config/congresso.yaml).",
        ),
        (
            "A fila",
            f"A situação oficial de cada proposição diz de quem ela está esperando: a designação de um relator (presidente da "
            f"comissão), o parecer (relator), a pauta ou um despacho da Mesa. Parada é a que tramita sem andamento há mais de "
            f"{lim.get('dias_parado', 365)} dias. Na Câmara entram as proposições apresentadas desde {m.get('projetos_desde', '')}; no "
            "Senado, tudo o que tramita.",
        ),
        (
            "Passaram na frente",
            f"Das matérias aprovadas no Plenário nos últimos {dados.JANELA_FURARAM} dias, as que foram do protocolo ao voto em menos de "
            f"{dados.RAPIDO} dias, com a urgência quando houve. A fila ao lado dá a medida do que ficou para trás.",
        ),
        (
            "O que espera × o que passa",
            _metodo_relevancia(m.get("relevancia") or {}),
        ),
        (
            "O jornal",
            f"Os nomes parlamentares são procurados nas manchetes e nas matérias das últimas {_num(jor.get('edicoes'))} edições "
            f"({_num(jor.get('manchetes'))} manchetes lidas). A regra é conservadora: nome inteiro, com as maiúsculas do registro; "
            "nomes de uma palavra e homônimos só valem com o título (“deputado”, “senadora”) ou a legenda ao lado.",
        ),
        (
            "No jornal × no voto",
            "O texto do jornal que cita a pessoa e uma proposição, pelo número (\"PEC 3/2021\") ou por um apelido conferido "
            "contra a ementa oficial (a lista fica em config/congresso.yaml; apelido que serve a mais de um projeto fica de fora). "
            "Numa matéria, a pessoa e a proposição precisam estar na mesma frase. Ao lado, os votos dela nessa proposição (o "
            "texto principal primeiro; na PEC, com o turno que a descrição oficial da votação dá). A "
            "página não interpreta a fala: põe as duas coisas lado a lado.",
        ),
        (
            "O comparativo",
            "Compara quaisquer dois parlamentares, das duas Casas: pelos nomes em \"Comparar dois\", marcando dois na tabela, "
            "pelo dossiê ou trocando um dos lados dentro do comparativo. Votam juntos: nas votações de conteúdo da legislatura em que os dois votaram Sim ou Não (obstrução conta como Não), "
            "a parte em que votaram igual; ao lado, a mesma conta só nas que dividiram esquerda e direita. Num tema, os dois ficam "
            "de lados opostos quando cada um tem saldo de 20 ou mais, um para cada lado. Deputado e senador não têm votações em "
            "comum: as Casas votam em separado.",
        ),
        (
            "O que isto não diz",
            "Voto não é discurso, e a página não lê discurso: ela mostra o registro para ser posto ao lado do que cada um diz. "
            "Voto com o governo não é ideologia; votação simbólica não aparece; ausência pode ter motivo que o registro não traz. "
            "Os polos e os temas são escolhas editoriais, escritas aqui para poderem ser contestadas.",
        ),
    ]


def congresso_view(d: Dados, config: Config) -> CongressoView:
    campos = [c for c in d.meta.get("campos") or [] if isinstance(c, dict)]
    pessoas = _pessoas(d.resumo, campos)
    temas_nomes = {t["id"]: t["nome"] for t in d.meta.get("temas") or []}
    rel_meta = d.meta.get("relevancia") or {}
    limites = (float(rel_meta.get("alta") or 60), float(rel_meta.get("baixa") or 30))
    casas = [_casa(casa, d.casas[casa], d.resumo.get("pessoas") or [], campos, temas_nomes, limites) for casa in ("cd", "sf") if casa in d.casas]
    polos = d.meta.get("polos") or {}
    leg = d.meta.get("legislatura") or {}
    manifesto = {
        "v": VERSAO,
        "hoje": str(d.meta.get("hoje") or ""),
        "base": f"../data/{dados.PASTA}/{dados.FICHAS}/",
        "votosArq": f"../data/{dados.PASTA}/" + dados.VOTOS.replace("{casa}.json", ""),
        "pesquisa": "pesquisa.html",
        "edicoes": "./",
        "legislatura": leg.get("numero"),
        "periodo": leg.get("rotulo") or "",
        "governo": d.meta.get("governo") or "",
        "campos": campos,
        "temas": {t["id"]: t["nome"] for t in d.meta.get("temas") or []},
        # Com quem cada um vota em cada tema, para as faixas filtradas: pessoa → {tema: [saldo, n, vira]}
        "tm": {p["k"]: p["tm"] for p in d.resumo.get("pessoas") or [] if p.get("ex") and p.get("tm")},
        "tipos": base.TIPOS,
        "votos": {
            "S": "Sim",
            "N": "Não",
            "A": "Abstenção",
            "O": "Obstrução",
            "P": "Presidia",
            "E": "Presente, sem voto",
            "V": "Votou (secreta)",
            "J": "Ausência justificada",
            "F": "Faltou",
            "-": "Sem registro",
        },
        "filas": projetos.FILAS,
        "estados": projetos.ESTADOS,
        "limiares": d.meta.get("limiares") or {},
    }
    hoje = str(d.meta.get("hoje") or "")
    return CongressoView(
        atualizado=_data(hoje, ano=True),
        periodo=str(leg.get("rotulo") or ""),
        governo=str(d.meta.get("governo") or ""),
        polos={"esquerda": ", ".join(polos.get("esquerda") or []), "direita": ", ".join(polos.get("direita") or [])},
        leitura=_leitura(d.achados, d),
        casas=casas,
        pessoas=pessoas,
        fora=sum(1 for p in pessoas if not p["ex"]),
        metodo=_metodo(d),
        manifesto=json.dumps(manifesto, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c"),
        totais={
            "pessoas": sum(1 for p in pessoas if p["ex"]),
            "votacoes": sum(c.estat.get("total") or 0 for c in casas),
            "divisivas": sum(c.estat.get("divisivas") or 0 for c in casas),
            "tramitando": sum((d.casas[c.id].get("fila") or {}).get("tramitando") or 0 for c in casas),
        },
    )


def render_congresso_page(config: Config, data_dir: Path, *, home_href: str, archive_href: str) -> str | None:
    """Página ``edicoes/congresso.html`` (``None`` sem dados em ``data/congresso/``)."""
    d = carregar(data_dir)
    if d is None:
        return None
    view = congresso_view(d, config)
    config.congresso.publicado = True
    template = filters.environment().get_template("congresso.html.j2")
    html = template.render(
        c=view,
        brand=brand_view(config),
        home_href=filters.safe_href(home_href) or "../",
        archive_href=filters.safe_href(archive_href) or "./",
        base_url=config.site.base_url,
        repo_url=filters.safe_url(config.site.repo_url) or "",
        analitico=config.termometro_ativo,
    )
    log.info("Aba Congresso renderizada: %d parlamentares, %d KB", len(view.pessoas), len(html.encode("utf-8")) // 1024)
    return html


# ── fichas nas matérias da edição ────────────────────────────────────────────


@dataclass
class Indice:
    """O índice das pessoas (``resumo.json``) pronto para achar quem uma matéria cita."""

    hoje: str
    pessoas: dict[str, dict[str, Any]]
    nomes: jornal.Nomes
    campos: list[dict[str, Any]]


def carregar_indice(data_dir: Path) -> Indice | None:
    pasta = dados.pasta_de(data_dir)
    resumo = dados.ler_json(pasta / dados.RESUMO)
    meta = dados.ler_json(pasta / dados.META)
    if not isinstance(resumo, dict) or resumo.get("v") != VERSAO or not isinstance(meta, dict):
        return None
    # Só quem está em exercício: um ex-senador que virou ministro do STF não é notícia "do Congresso".
    pessoas = {
        p["k"]: p for p in resumo.get("pessoas") or [] if isinstance(p, dict) and p.get("k") and p.get("nome") and p.get("ex")
    }
    if not pessoas:
        return None
    nomes = jornal.Nomes(jornal.Pessoa(k, k.split("-", 1)[0], p["nome"]) for k, p in pessoas.items())
    return Indice(hoje=str(resumo.get("hoje") or ""), pessoas=pessoas, nomes=nomes, campos=[c for c in meta.get("campos") or [] if isinstance(c, dict)])


def _nome_do_campo(campo_id: str | None, campos: list[dict[str, Any]]) -> str:
    return next((str(c.get("nome")) for c in campos if c.get("id") == campo_id), "")


def ficha_curta(p: Mapping[str, Any], campos: list[dict[str, Any]]) -> dict[str, Any]:
    """O que a matéria mostra de um parlamentar citado (um retrato do dia, gravado na edição)."""
    d: dict[str, Any] = {
        "k": p["k"],
        "nome": p["nome"],
        "partido": p.get("partido") or "",
        "uf": p.get("uf") or "",
        "casa": "Câmara" if str(p["k"]).startswith("cd") else "Senado",
        "ex": bool(p.get("ex")),
    }
    for origem, destino in (("saldo", "saldo"), ("gov", "gov"), ("pres", "pres"), ("prop", "prop"), ("par", "par"), ("rel_ant", "rel"), ("div", "div")):
        if p.get(origem) is not None:
            d[destino] = p[origem]
    if p.get("campo"):
        d["campo"] = _nome_do_campo(p["campo"], campos)
    if p.get("preside"):
        d["preside"] = True
        d.pop("pres", None)  # quem preside só vota em caso de empate: a presença não diz nada
    return d


def fichas_da_edicao(edition: Edition, indice: Indice, limite: int = 4) -> dict[str, Any] | None:
    """Os parlamentares citados em cada matéria da edição: ``{"hoje": …, "materias":
    {id da matéria: [fichas curtas]}}`` (``None`` se nenhuma matéria cita alguém)."""
    materias: dict[str, list[dict[str, Any]]] = {}
    for story in edition.stories:
        if story.section not in jornal.SECOES:
            continue
        corpo = " ".join([story.headline or "", story.dek or "", *[p or "" for p in story.body]])
        achadas = indice.nomes.achar(filters.plain(corpo))
        if achadas:
            materias[story.id] = [ficha_curta(indice.pessoas[p.chave], indice.campos) for p in achadas[:limite]]
    if not materias:
        return None
    return {"hoje": indice.hoje, "materias": materias}
