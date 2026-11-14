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
from ..congresso import VERSAO, base, dados, ficha, jornal, materias, peso, projetos, relevancia
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
    "juntos": "Partidos",
    "fila": "Fila",
    "pauta": "Pauta",
    "furaram": "Passou na frente",
    "relatoria": "Relatoria",
    "relevancia": "Espera × passa",
    "presenca": "Presença",
    "mudanca": "Mudança",
    "destoa": "Fora da curva",
    "tema_pessoa": "Muda de lado",
    "frente": "Frentes",
    "cota": "Cota",
    "peso": "Relevância",
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
    polo: str = ""  # "esquerda"/"direita": o partido define um polo (fica na ponta da régua por construção)


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
    juntos: dict[str, Any] | None  # quanto os partidos votam juntos (todas as votações de conteúdo)
    cruzada: list[dict[str, Any]]  # matérias de autores de um polo em que o outro votou a favor
    relevancia: dict[str, Any] | None
    jornal_voto: list[dict[str, Any]]
    fila: dict[str, Any] | None
    furaram: dict[str, Any] | None
    ultimas: list[dict[str, Any]]
    listas: list[dict[str, Any]]
    escolha: dict[str, Any] = field(default_factory=dict)  # "Escolha o seu candidato": quem está no mandato, com os traços
    frentes: dict[str, Any] | None = None  # frentes de causa × voto (só a Câmara registra quem assina)
    cota: dict[str, Any] | None = None  # a cota parlamentar: quem gasta quanto, com o quê e com quem
    comissoes: dict[str, Any] | None = None  # as votações nominais nas comissões


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
    temas: list[dict[str, Any]]  # o que entra em cada tema: [{nome, camara, senado}]
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
        "obstrucao": v.get("obstrucao") or 0,
        "quorum": v.get("quorum") or 0,
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
    ("muda_peso", "Quem muda quando pesa a relevância", "Nas votações cujas matérias têm nota, o saldo sem peso contra o saldo com cada voto pesando a nota de relevância, e o saldo sem peso nas de nota alta e nas de nota baixa (da mediana da Casa para cima e abaixo dela): quem vota de um lado nas matérias pequenas e do outro nas grandes.", "peso", ""),
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
            elif tipo == "peso":
                valor = f"{_saldo(i.get('simples'))} → {_saldo(i.get('pesado'))}"
                partes = []
                if i.get("alta") is not None:
                    partes.append(f"nota alta {_saldo(i.get('alta'))} ({_num(i.get('alta_n'))})")
                if i.get("baixa") is not None:
                    partes.append(f"baixa {_saldo(i.get('baixa'))} ({_num(i.get('baixa_n'))})")
                extra = " · ".join(partes)
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


def _definicao_do_tema(areas: list[str], casa: str) -> str:
    """O que entra num tema, numa frase: as áreas oficiais da Casa."""
    if not areas:
        return ""
    lista = (", " if len(areas) > 2 else " e ").join(f"“{a}”" for a in areas)
    return f"Matérias que {'a Câmara' if casa == 'cd' else 'o Senado'} classifica em {lista}."


JUNTOS_FAIXAS = (40, 55, 70, 85)  # cortes das cinco faixas de cor de "quanto votam juntos"


def _juntos(cv: Mapping[str, Any], polos: Mapping[str, list[str]], membros: Mapping[str, int]) -> dict[str, Any] | None:
    """Quanto os partidos votam juntos: as contas do consenso e a tabela partido × partido."""
    total = int(cv.get("conteudo") or 0) if cv else 0
    if not total:
        return None
    partidos = list(cv.get("partidos") or [])
    de_polo = {s: "esquerda" for s in polos.get("esquerda") or []} | {s: "direita" for s in polos.get("direita") or []}
    linhas = []
    for a, linha in zip(partidos, cv.get("matriz") or [], strict=False):
        celulas = []
        for b, celula in zip(partidos, linha, strict=False):
            if a == b:
                celulas.append({"valor": "", "classe": "x", "titulo": ""})
            elif not celula:
                celulas.append({"valor": "·", "classe": "na", "titulo": f"{a} × {b}: poucas votações em que os dois tiveram lado"})
            else:
                pct, n = celula
                faixa = sum(1 for corte in JUNTOS_FAIXAS if pct >= corte)
                celulas.append({"valor": str(pct), "classe": f"j{faixa}", "titulo": f"{a} × {b}: do mesmo lado em {pct}% de {_num(n)} votações de conteúdo"})
        linhas.append({"sigla": a, "polo": de_polo.get(a, ""), "celulas": celulas})
    # O exemplo que a página escreve por extenso: o maior partido de cada polo.
    exemplo = None
    maiores = [max((s for s in partidos if de_polo.get(s) == polo), key=lambda s: membros.get(s, 0), default="") for polo in ("esquerda", "direita")]
    if all(maiores):
        celula = (cv.get("matriz") or [])[partidos.index(maiores[0])][partidos.index(maiores[1])]
        if celula:
            exemplo = {"a": maiores[0], "b": maiores[1], "pct": celula[0], "n": _num(celula[1])}

    def parte(chave: str) -> dict[str, Any]:
        n = int(cv.get(chave) or 0)
        return {"n": _num(n), "pct": _pct(100 * n / total), "w": round(100 * n / total, 1)}

    return {
        "conteudo": _num(total),
        "juntos": parte("juntos"),
        "divisivas": parte("divisivas"),
        "rachas": parte("rachas"),
        "sem_lado": parte("sem_lado"),
        "partidos": partidos,
        "linhas": linhas,
        "exemplo": exemplo,
    }


def _cruzada(cz: Mapping[str, Any], casa: str, polos: Mapping[str, list[str]]) -> list[dict[str, Any]]:
    """As matérias de autores de um polo e como o outro polo votou o texto principal."""
    saida = []
    for autor, outro in (("direita", "esquerda"), ("esquerda", "direita")):
        x = (cz or {}).get(autor) or {}
        if not x.get("n"):
            continue
        saida.append(
            {
                "autor": autor,
                "outro": outro,
                "siglas": " e ".join(polos.get(autor) or []) if len(polos.get(autor) or []) <= 2 else ", ".join(polos.get(autor) or []),
                "n": x["n"],
                "favor": x.get("favor") or 0,
                "contra": x.get("contra") or 0,
                "dividido": x.get("dividido") or 0,
                "itens": [
                    {
                        "t": i["t"], "m": materias.chave(casa, i["t"]), "apel": i.get("ap") or "", "e": i.get("e") or "",
                        "data": _data(i.get("d"), ano=True), "pa": ", ".join(i.get("pa") or []),
                        "resultado": "" if i.get("apv") is None else ("aprovada" if i["apv"] else "rejeitada"),
                    }
                    for i in x.get("itens") or []
                ],
            }
        )
    return saida


def _casa(
    casa: str,
    painel: Mapping[str, Any],
    pessoas: list[dict[str, Any]],
    campos: list[dict[str, Any]],
    temas_nomes: Mapping[str, str] | None = None,
    relevancia_limites: tuple[float, float] | None = None,
    polos: Mapping[str, list[str]] | None = None,
    temas_def: Mapping[str, Mapping[str, list[str]]] | None = None,
    temas_meta: list[dict[str, Any]] | None = None,
    governo: str = "",
    limiares: Mapping[str, Any] | None = None,
) -> CasaView:
    temas_nomes = temas_nomes or {}
    polos = polos or {}
    temas_def = temas_def or {}
    de_polo = {s: "esquerda" for s in polos.get("esquerda") or []} | {s: "direita" for s in polos.get("direita") or []}
    relev_alta, relev_baixa = relevancia_limites or (60.0, 30.0)
    estat = painel.get("votacoes") or {}
    matriz = painel.get("temas") or {}
    temas_cols = [
        {**t, "dica": _definicao_do_tema((temas_def.get(t["id"]) or {}).get("camara" if casa == "cd" else "senado") or [], casa)}
        for t in (matriz.get("temas") or [])[:11]
    ]
    membros = {b["sigla"]: b["membros"] for b in painel.get("bancadas") or []}
    linhas = []
    for sigla, por_tema in (matriz.get("partidos") or {}).items():
        linhas.append(
            LinhaMatriz(
                sigla=sigla,
                membros=membros.get(sigla, 0),
                geral=_celula(por_tema.get("todas"), campos, sigla, "todas as votações de conteúdo"),
                celulas=[_celula(por_tema.get(t["id"]), campos, sigla, t["nome"]) for t in temas_cols],
                polo=de_polo.get(sigla, ""),
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
        juntos=_juntos(painel.get("convivencia") or {}, polos, membros),
        cruzada=_cruzada(painel.get("cruzada") or {}, casa, polos),
        relevancia=_relevancia(painel.get("relevancia") or {}, casa, relev_alta, relev_baixa),
        jornal_voto=_jornal_voto((painel.get("listas") or {}).get("jornal_voto") or []),
        fila=_fila(painel.get("fila") or {}, casa),
        furaram=_furaram(painel.get("furaram") or {}),
        ultimas=[_votacao(v, casa) for v in painel.get("ultimas") or []],
        listas=_listas(painel.get("listas") or {}),
        escolha=_escolha(casa, pessoas, temas_meta or [], campos, governo),
        frentes=_frentes(painel.get("frentes") or {}, temas_meta or [], campos, int(painel.get("em_exercicio") or 0), int((limiares or {}).get("frente_poucas") or FRENTE_POUCAS)),
        cota=_cota(painel.get("cota") or {}, casa),
        comissoes=_comissoes(painel.get("comissoes") or {}, casa),
    )


ESCOLHA_TEMAS = 3  # temas de cada lado na linha de cada um
ESCOLHA_PROPOE = 3  # temas em que mais propõe, na linha de cada um


def iniciais(nome: str) -> str:
    """As iniciais do primeiro e do último nome ("Maria do Rosário" → "MR"): o que o cartão
    mostra enquanto a foto não vem (ou se ela não vier)."""
    partes = [parte for parte in nome.replace(".", " ").split() if parte[:1].isalpha() and parte.lower() not in ("de", "da", "do", "das", "dos", "e")]
    if not partes:
        return ""
    return (partes[0][0] + (partes[-1][0] if len(partes) > 1 else "")).upper()


def corte_do_centro(campos: list[dict[str, Any]]) -> tuple[float, float]:
    """Os limites da faixa "dividido" (o campo ``centro`` de config/congresso.yaml): de
    −20 a +20 no padrão. Abaixo, "com a esquerda"; acima, "com a direita"."""
    ordem = sorted(campos, key=lambda c: float(c.get("ate") or 0))
    for i, c in enumerate(ordem):
        if c.get("id") == "centro":
            return (float(ordem[i - 1]["ate"]) if i else -100.0, float(c["ate"]))
    return (-20.0, 20.0)


def lado_no_tema(saldo: float, corte: tuple[float, float]) -> str:
    """``e`` (com a esquerda), ``c`` (dividido) ou ``d`` (com a direita)."""
    if saldo <= corte[0]:
        return "e"
    return "d" if saldo > corte[1] else "c"


MESES_CURTOS = ("jan.", "fev.", "mar.", "abr.", "maio", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez.")


def _mediana(valores: list[float]) -> float | None:
    ordem = sorted(valores)
    if not ordem:
        return None
    meio = len(ordem) // 2
    return ordem[meio] if len(ordem) % 2 else (ordem[meio - 1] + ordem[meio]) / 2


def excecao_de_tema(p: Mapping[str, Any], corte: tuple[float, float]) -> tuple[str, float, int] | None:
    """O tema em que a pessoa vota do lado oposto ao do seu padrão geral (``(tema, saldo,
    votações)``; ``None`` se não houver): ela tem lado no geral (fora da faixa "dividido") e, num
    tema com 5 votações ou mais, fica do outro lado. Havendo mais de um, o de mais votações."""
    if p.get("saldo") is None:
        return None
    geral = lado_no_tema(float(p["saldo"]), corte)
    if geral == "c":
        return None
    opostos = [
        (tema, float(valores[0]), int(valores[1]))
        for tema, valores in (p.get("tm") or {}).items()
        if lado_no_tema(float(valores[0]), corte) not in ("c", geral)
    ]
    return max(opostos, key=lambda x: (x[2], abs(x[1]), x[0])) if opostos else None


def _escolha(casa: str, pessoas: list[dict[str, Any]], temas: list[dict[str, Any]], campos: list[dict[str, Any]], governo: str) -> dict[str, Any]:
    """"Escolha o seu candidato": quem está em exercício na Casa, do estado A ao Z, um cartão
    cada: a foto, de que lado vota, se aparece para votar, se acompanha o partido, onde foge do
    próprio padrão e o que propôs; o resto atrás do "ver mais". O script filtra e ordena esta
    mesma lista; sem ele, ela sai inteira."""
    curtos = {t["id"]: t.get("curto") or t["nome"] for t in temas}
    corte = corte_do_centro(campos)
    da_casa = [p for p in pessoas if p["k"].startswith(casa + "-") and p.get("ex")]
    mediana_pres = _mediana([float(p["pres"]) for p in da_casa if p.get("pres") is not None])
    mediana_fid = _mediana([float(p["fid"]) for p in da_casa if p.get("fid") is not None])
    # A cota se compara dentro do estado (o gasto e o teto mudam com a distância de Brasília); no
    # Senado, com três por estado, a comparação é com a Casa.
    por_uf: dict[str, list[float]] = defaultdict(list)
    for p in da_casa:
        if p.get("ct") is not None:
            por_uf[p.get("uf") or "" if casa == "cd" else ""].append(float(p["ct"]))
    medianas_ct = {uf: _mediana(v) for uf, v in por_uf.items()}
    onde_casa = "da Câmara" if casa == "cd" else "do Senado"
    itens = []
    for p in da_casa:
        lados: dict[str, list[tuple[str, int, int]]] = {"e": [], "c": [], "d": []}
        for tema, valores in (p.get("tm") or {}).items():
            if tema in curtos:
                lados[lado_no_tema(float(valores[0]), corte)].append((curtos[tema], int(valores[0]), int(valores[1])))
        for lista in lados.values():
            lista.sort(key=lambda x: (-x[2], x[0]))
        propoe = sorted(((curtos[t], n) for t, n in (p.get("pt") or {}).items() if t in curtos), key=lambda x: (-x[1], x[0]))
        exc = excecao_de_tema(p, corte)
        troca = p.get("troca") or None
        desde = ""
        if troca and troca[0]:
            desde = f"{MESES_CURTOS[int(troca[0][5:7]) - 1]} {troca[0][:4]}"
        uf_ct = (p.get("uf") or "") if casa == "cd" else ""
        mediana_ct = medianas_ct.get(uf_ct)
        left, _width = _barra(p.get("saldo"))
        mais = [
            x
            for x in (
                (f"Com o governo {governo}".strip(), f"votou com a orientação em {_pct(p.get('gov'))} das vezes") if p.get("gov") is not None else None,
                (
                    "Cota parlamentar",
                    f"{reais(p.get('ct'))} por mês"
                    + (f" · mediana {'de ' + uf_ct if uf_ct else onde_casa}: {reais(mediana_ct)}" if mediana_ct is not None else ""),
                )
                if p.get("ct") is not None
                else None,
                ("Com a esquerda em", ", ".join(f"{nome} {_saldo(v)}" for nome, v, _n in lados["e"])) if lados["e"] else None,
                ("Com a direita em", ", ".join(f"{nome} {_saldo(v)}" for nome, v, _n in lados["d"])) if lados["d"] else None,
                ("Propõe sobre", ", ".join(f"{nome} ({n})" for nome, n in propoe[:ESCOLHA_PROPOE])) if propoe else None,
                ("Frentes", f"assina {_num(p.get('frn'))}") if p.get("frn") else None,
            )
            if x
        ]
        itens.append(
            {
                "k": p["k"],
                "nome": p["nome"],
                "partido": p.get("partido") or "",
                "uf": p.get("uf") or "",
                "saldo": p.get("saldo"),
                "saldo_txt": _saldo(p.get("saldo")),
                "classe": classe_do_saldo(p.get("saldo"), campos),
                "pres": p.get("pres"),
                "gov": p.get("gov"),
                "fid": p.get("fid"),
                "norma": p.get("norma") or 0,
                "prop": p.get("prop") or 0,
                "ct": p.get("ct"),
                "foto": filters.safe_url(p.get("foto")) or "",
                "iniciais": iniciais(p["nome"]),
                # De que lado vota, em palavras (o começo da frase da ficha), com o número e a base.
                "vota": str(p.get("frase") or "").split(":", 1)[0] if p.get("saldo") is not None else "",
                "regua": round(50 + float(p["saldo"]) / 2, 1) if p.get("saldo") is not None else None,
                "div": _num(p.get("div")) if p.get("div") else "",
                "troca": {"desde": desde, "antes": troca[1]} if troca else None,
                "pres_txt": _pct(p.get("pres")) if p.get("pres") is not None else "",
                "fid_txt": _pct(p.get("fid")) if p.get("fid") is not None else "",
                "excecao": {"tema": curtos.get(exc[0], exc[0]), "lado": "com a direita" if exc[1] > 0 else "com a esquerda", "saldo": _saldo(exc[1]), "n": _num(exc[2])} if exc else None,
                "propostas": (
                    f"{_num(p.get('prop'))} {'proposta' if p.get('prop') == 1 else 'propostas'}"
                    + (f" · {_num(p.get('norma'))} {'virou norma' if p.get('norma') == 1 else 'viraram norma'}" if p.get("norma") else "")
                )
                if p.get("prop")
                else "Nenhuma proposta de autoria principal",
                "mais": mais,
            }
        )
    itens.sort(key=lambda i: (i["uf"], text.normalize(i["nome"])))
    ufs = sorted({i["uf"] for i in itens if i["uf"]})
    partidos = sorted({i["partido"] for i in itens if i["partido"]}, key=text.normalize)
    return {
        "itens": itens,
        "ufs": ufs,
        "partidos": partidos,
        "corte": [corte[0], corte[1]],
        "corte_txt": [_saldo(corte[0]), _saldo(corte[1])],
        "mediana_pres": _pct(mediana_pres) if mediana_pres is not None else "",
        "mediana_fid": _pct(mediana_fid) if mediana_fid is not None else "",
        "casa_txt": onde_casa,
    }


MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def reais(valor: Any) -> str:
    """R$ em reais inteiros ("R$ 42.638"); abaixo de mil, com centavos."""
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return "—"
    return "R$ " + (text.format_number_pt(v, 2) if abs(v) < 1000 else text.format_number_pt(round(v), 0))


def _mes(am: str | None) -> str:
    """"2026-10" → "out/2026"."""
    if not am or len(am) < 7:
        return ""
    try:
        return f"{MESES[int(am[5:7]) - 1]}/{am[:4]}"
    except (ValueError, IndexError):
        return am


def _cnpj(cnpj: str | None) -> str:
    d = "".join(ch for ch in (cnpj or "") if ch.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return d


def _extra_cota(x: Mapping[str, Any]) -> str:
    """O total e os meses; na Câmara, as passagens da agência em 2024 (fora da conta), para
    quem parece gastar pouco só porque viaja pela agência."""
    texto = f"{reais(x.get('total'))} em {_num(x.get('meses'))} meses"
    if float(x.get("sigepa_2024") or 0) > 0:
        texto += f"; fora da conta, {reais(x.get('sigepa_2024'))} em passagens da agência em 2024"
    return texto


def _cota(ct: Mapping[str, Any], casa: str) -> dict[str, Any] | None:
    """A cota parlamentar da Casa, pronta para mostrar."""
    if not ct or not ct.get("total"):
        return None

    def pessoa_(x: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
        return {"k": x.get("k") or "", "nome": x.get("nome") or "", "legenda": f"{x.get('partido', '')}-{x.get('uf', '')}".strip("-"), **extra}

    total = float(ct.get("total") or 0) or 1.0
    maior_cat = max((float(c.get("total") or 0) for c in ct.get("categorias") or []), default=0) or 1.0
    return {
        "desde": _mes(ct.get("desde")),
        "ate": _mes(ct.get("ate")),
        "sem": ct.get("sem") or "",
        "total": reais(ct.get("total")),
        "pessoas": _num(ct.get("pessoas")),
        "mediana": reais(ct.get("mediana")),
        "quem": "deputados" if casa == "cd" else "senadores",
        "mais": [pessoa_(x, valor=reais(x.get("media")), extra=_extra_cota(x)) for x in (ct.get("mais_gastam") or [])[:10]],
        "menos": [pessoa_(x, valor=reais(x.get("media")), extra=_extra_cota(x)) for x in (ct.get("menos_gastam") or [])[:10]],
        "categorias": [
            {"nome": c.get("nome") or "", "valor": reais(c.get("total")), "pct": _pct(c.get("pct")), "w": round(100 * float(c.get("total") or 0) / maior_cat, 1)}
            for c in (ct.get("categorias") or [])[:12]
        ],
        "fornecedores": [
            {"nome": f.get("nome") or "", "cnpj": _cnpj(f.get("cnpj")), "valor": reais(f.get("total")), "gabinetes": _num(f.get("gabinetes")), "n": _num(f.get("n"))}
            for f in (ct.get("fornecedores") or [])[:15]
        ],
        "concentrados": [
            pessoa_(x, fornecedor=x.get("fornecedor") or "", cnpj=_cnpj(x.get("cnpj")), valor=reais(x.get("valor")), pct=_pct(x.get("pct")), total=reais(x.get("total")))
            for x in (ct.get("concentrados") or [])[:15]
        ],
        "repetidas": [
            {
                "fornecedor": r.get("fornecedor") or "",
                "cnpj": _cnpj(r.get("cnpj")),
                "numero": r.get("numero") or "",
                "data": _data(r.get("data"), ano=True),
                "valor": reais(r.get("valor")),
                "docs": [pessoa_(x, url=filters.safe_url(x.get("url")) or "") for x in r.get("docs") or []],
            }
            for r in (ct.get("repetidas") or [])[:20]
        ],
        "glosas": [pessoa_(x, valor=reais(x.get("valor")), extra=f"{_num(x.get('n'))} notas") for x in (ct.get("glosas") or [])[:10]],
        "sem_despesa": [pessoa_(x) for x in ct.get("sem_despesa") or []],
        "parte_maior": _pct(100 * float(((ct.get("categorias") or [{}])[0]).get("total") or 0) / total) if ct.get("categorias") else "—",
    }


VOTO_LETRA = {"S": "Sim", "N": "Não", "A": "Abstenção", "O": "Obstrução"}


def _comissoes(cm: Mapping[str, Any], casa: str) -> dict[str, Any] | None:
    """As votações nominais nas comissões da Casa, prontas para mostrar."""
    if not cm or not cm.get("votacoes"):
        return None
    casos = []
    por_caso: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for m in cm.get("mudaram") or []:
        por_caso[(m.get("prop") or "", m.get("dp") or "")].append(m)
    for c in cm.get("mudaram_casos") or []:
        quem = por_caso.get((c.get("prop") or "", c.get("dp") or ""), [])
        casos.append(
            {
                "prop": c.get("prop") or "",
                "comissao": c.get("comissao") or "",
                "dc": _data(c.get("dc"), ano=True),
                "dp": _data(c.get("dp"), ano=True),
                "mudaram": int(c.get("mudaram") or 0),
                "ambas": int(c.get("ambas") or 0),
                "quem": [
                    {
                        "k": m["k"],
                        "nome": m.get("nome") or "",
                        "legenda": f"{m.get('partido', '')}-{m.get('uf', '')}".strip("-"),
                        "vc": VOTO_LETRA.get(m.get("vc") or "", m.get("vc") or ""),
                        "vp": VOTO_LETRA.get(m.get("vp") or "", m.get("vp") or ""),
                    }
                    for m in quem
                ],
                "parecer": (quem[0].get("parecer") if quem else "") or "",
            }
        )
    return {
        "votacoes": _num(cm.get("votacoes")),
        "conteudo": _num(cm.get("conteudo")),
        "divididas": _num(cm.get("divididas")),
        "por_comissao": [f"{sigla} {_num(n)}" for sigla, n in (cm.get("por_comissao") or [])[:12]],
        "ultimas": [
            {
                "data": _data(v.get("d"), ano=True),
                "comissao": v.get("comissao") or "",
                "prop": v.get("prop") or "",
                "x": v.get("x") or "",
                "parecer": v.get("parecer") or "",
                "sim": _num(v.get("sim")),
                "nao": _num(v.get("nao")),
                "abst": int(v.get("abst") or 0),
                "resultado": {True: "aprovado", False: "rejeitado"}.get(v.get("aprovada"), ""),
                "classe": {True: "ok", False: "no"}.get(v.get("aprovada"), ""),
            }
            for v in (cm.get("ultimas") or [])[:15]
        ],
        "casos": [c for c in casos if c["mudaram"]],
        "casos_sem": sum(1 for c in casos if not c["mudaram"]),
    }


FRENTE_LINHAS = 8  # deputados por coluna no desenho de cada frente (um ponto por deputado que assina)
FRENTE_CONTRA = 5  # membros que menos votam igual ao coordenador, na lista de cada cartão


FRENTE_POUCAS = 20  # votações abaixo disto: o cartão avisa que são poucas (o valor vem de config/congresso.yaml, limiares.frente_poucas)


def lado_firme(saldo: Any, campos: list[dict[str, Any]]) -> str:
    """``e`` ou ``d`` quando o saldo está numa das pontas da régua (a primeira e a última faixa
    de ``campos``: até −60 e acima de +60 no padrão); senão, vazio. Só com o coordenador numa
    ponta a página fala em "lado oposto ao do coordenador"."""
    if saldo is None:
        return ""
    ordem = sorted(campos, key=lambda c: float(c.get("ate") or 0))
    if len(ordem) < 2:
        return ""
    if float(saldo) <= float(ordem[0]["ate"]):
        return "e"
    return "d" if float(saldo) > float(ordem[-2]["ate"]) else ""


def manchete_da_frente(c: Mapping[str, Any], firme: str) -> tuple[dict[str, str], str]:
    """A conclusão do cartão da frente (o número e a frase) e de que tipo ela é.

    ``oposto``: o coordenador vota numa das pontas (``firme``) e há membros com votações no
    tema: quantos deles votam do lado oposto ao dele. ``igual``: sem isso, mas com votações em
    comum: em quanto das vezes quem assina votou igual a ele. ``assinam``: nada a comparar."""
    lados = c.get("lados") or {}
    com_lado = sum(int(lados.get(x) or 0) for x in ("e", "c", "d"))
    if firme and com_lado:
        n = int(lados.get("d" if firme == "e" else "e") or 0)
        return ({"numero": f"{_num(n)} de {_num(com_lado)}", "texto": "que assinam votam do lado oposto ao do coordenador."}, "oposto")
    if c.get("com_coord") is not None:
        return (
            {"numero": _pct(c.get("com_coord")), "texto": "das vezes, quem assina votou igual ao coordenador, que não vota de um lado só neste tema."},
            "igual",
        )
    return ({"numero": _num(c.get("em_exercicio")), "texto": "deputados assinam. Não há votações bastantes para comparar com o coordenador."}, "assinam")


def _frentes(fr: Mapping[str, Any], temas: list[dict[str, Any]], campos: list[dict[str, Any]], em_exercicio: int = 0, poucas: int = 0) -> dict[str, Any] | None:
    """As frentes de causa (config/congresso.yaml) e o voto de quem assina: um cartão cada, a
    começar por onde menos se vota igual ao coordenador."""
    if not fr or not fr.get("causas"):
        return None
    curtos = {t["id"]: t.get("curto") or t["nome"] for t in temas}
    corte = corte_do_centro(campos)
    poucas = poucas or FRENTE_POUCAS
    cartoes = []
    for c in fr["causas"]:
        lados = c.get("lados") or {}
        coord = c.get("coord") or None
        tema = c.get("tema") or "todas"
        onde = f"em {curtos.get(tema, tema)}" if tema != "todas" else "nas votações que opõem esquerda e direita"
        firme = lado_firme(coord.get("saldo") if coord else None, campos)
        manchete, tipo = manchete_da_frente(c, firme)
        lado_coord = lado_no_tema(float(coord["saldo"]), corte) if coord and coord.get("saldo") is not None else ""
        # Com o coordenador numa ponta, os grupos se chamam pelo lado dele; senão, pelo lado da régua.
        nomes = {"e": "com a esquerda", "c": "divididos", "d": "com a direita", "sem": "sem votações bastantes"}
        if firme:
            nomes["e" if firme == "e" else "d"] = "do mesmo lado"
            nomes["d" if firme == "e" else "e"] = "do lado oposto"
        votacoes = int(c.get("votacoes") or 0)
        cartoes.append(
            {
                "id": c["id"],
                "nome": c.get("nome") or c.get("titulo") or "",
                "titulo": c.get("titulo") or "",
                "doc": filters.safe_url(c.get("doc")) or "",
                "criada": _data(c.get("criada"), ano=True),
                "tema": tema,
                "tema_nome": curtos.get(tema, "Sem um tema só"),
                "onde": onde,
                "membros": _num(c.get("membros")),
                "em_exercicio": int(c.get("em_exercicio") or 0),
                "em_exercicio_txt": _num(c.get("em_exercicio")),
                "coord": {
                    **coord,
                    "saldo_txt": _saldo(coord.get("saldo")),
                    "classe": classe_do_saldo(coord.get("saldo"), campos),
                    "vota": next((str(x.get("nome")) for x in sorted(campos, key=lambda x: float(x.get("ate") or 0)) if float(coord["saldo"]) <= float(x.get("ate") or 0)), "")
                    if coord.get("saldo") is not None
                    else "",
                }
                if coord
                else None,
                # Um ponto por deputado: colunas cheias de FRENTE_LINHAS e o resto da última coluna.
                "grupos": [
                    {"id": x, "n": int(lados.get(x) or 0), "nome": nomes[x], "cols": int(lados.get(x) or 0) // FRENTE_LINHAS, "resto": int(lados.get(x) or 0) % FRENTE_LINHAS, "coord": x == lado_coord}
                    for x in ("e", "c", "d", "sem")
                    if lados.get(x)
                ],
                "manchete": manchete,
                "tipo": tipo,
                "oposto": int(lados.get("d" if firme == "e" else "e") or 0) if firme else "",
                "votacoes": _num(votacoes),
                "poucas": 0 < votacoes < poucas,
                "com_coord": _pct(c.get("com_coord")),
                "com_coord_n": c.get("com_coord") if c.get("com_coord") is not None else "",
                "sem_assinar": _pct(c.get("sem_assinar")) if c.get("sem_assinar") is not None else "",
                "contra": [
                    {**x, "legenda": f"{x.get('partido', '')}-{x.get('uf', '')}".strip("-"), "pct_txt": _pct(x.get("pct"))}
                    for x in (c.get("contra") or [])[:FRENTE_CONTRA]
                ],
            }
        )
    # Primeiro, onde menos se vota igual ao coordenador; sem a conta, no fim, pelo nome.
    cartoes.sort(key=lambda f: (f["com_coord_n"] == "", f["com_coord_n"] or 0, text.normalize(f["nome"])))
    return {
        "total": _num(fr.get("total")),
        "causas": _num(len(cartoes)),
        "da_casa": _num(em_exercicio) if em_exercicio else "",
        "mediana": _num(fr.get("mediana")),
        "media": str(fr.get("media") or "").replace(".", ","),
        "nenhuma": int(fr.get("nenhuma") or 0),
        "legislatura": fr.get("legislatura"),
        "mais_assinam": [
            {**x, "legenda": f"{x.get('partido', '')}-{x.get('uf', '')}".strip("-"), "n_txt": _num(x.get("n"))} for x in (fr.get("mais_assinam") or [])[:10]
        ],
        "cartoes": cartoes,
        "linhas": FRENTE_LINHAS,
        "poucas": poucas,
    }


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
                "sp": p.get("sp"),
                "sp_txt": _saldo(p.get("sp")),
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
    corte = corte_do_centro([c for c in m.get("campos") or [] if isinstance(c, dict)])
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
            "O placar",
            "O placar sai como a Mesa anuncia: Sim, Não, abstenção e, à parte, a obstrução (a bancada que se declara em "
            "obstrução não dá número à votação). Na conta de que lado cada um ficou, a obstrução vale como Não, porque é de "
            "quem não quer a matéria votada; no placar, ela não é voto Não. Proposta de emenda à Constituição precisa do Sim de "
            f"três quintos da Casa ({base.quorum('cd', 'PEC')} deputados; {base.quorum('sf', 'PEC')} senadores) e projeto de lei complementar, da maioria absoluta "
            f"({base.quorum('cd', 'PLP')}; {base.quorum('sf', 'PLP')}), no texto e em cada destaque: a página mostra esse número ao lado do placar, porque um texto pode ter "
            "mais Sim do que Não e ser rejeitado. Nas demais matérias decide a maioria dos presentes.",
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
            "Por que um voto é “de esquerda” ou “de direita”",
            f"Não é o assunto que define o lado. Em cada votação, a esquerda é o lado em que votou a maioria ({round(100 * float(lim.get('coesao_polo', 0.75)))}% ou mais) "
            f"dos parlamentares de {esquerda}; a direita, o da maioria de {direita}. Votar “com a esquerda” é votar igual a essa maioria "
            "naquela votação: numa, isso é votar Sim; em outra, votar Não. Por isso os partidos que definem os polos ficam nas pontas "
            "(−100 e +100) quase por definição: eles são a régua. O que a régua informa é onde ficam os outros partidos e cada pessoa. "
            "Para saber por que um voto caiu de um lado, a página mostra, em cada votação, o que estava em jogo e o Sim–Não de cada "
            "partido dos polos (na ficha da matéria, no detalhe de cada tema e nos votos de cada um por tema).",
        ),
        (
            "Quanto os partidos votam juntos",
            "Na aba Partidos, uma conta que não depende da régua dos polos: em todas as votações de conteúdo do Plenário, dois "
            "partidos votaram juntos quando a maioria de cada um ficou do mesmo lado. A tabela dá a porcentagem para cada par "
            f"(par com menos de {dados.MINIMO_PAR} votações fica vazio). Ao lado, quantas votações foram de consenso (os dois polos do "
            "mesmo lado), quantas opuseram os polos (as únicas que entram no saldo), quantas tiveram racha e em quantas um polo "
            "não teve maioria clara.",
        ),
        (
            "Quando um lado vota a proposta do outro",
            "As matérias de parlamentares de partidos de um polo, sem coautor do outro, que tiveram o texto principal votado "
            "nominalmente no Plenário, e o lado do outro polo na última dessas votações. O autor é o do registro original: o texto "
            "votado pode ter mudado no caminho (a ficha mostra a ementa original e a que vale).",
        ),
        (
            "Os rachas",
            "Se um partido de um polo vota fechado com o outro (o PSOL com o PL contra o PT, por exemplo), a votação sai do saldo. "
            "Em geral são os dois extremos votando contra o governo por razões opostas: o PSOL contra uma regra fiscal por achá-la "
            "dura demais, o PL por ser oposição. Contada no saldo, ela faria o PSOL parecer menos de esquerda que o PT. Essas "
            "votações aparecem à parte, em \"Onde cada polo racha\", com o tema e a orientação do governo de cada uma.",
        ),
        (
            "Saldo pesado pela relevância",
            "O mesmo saldo, com cada voto pesando a nota de relevância da matéria votada (0 a 100, a de “o que espera × o que "
            "passa”): um voto no texto de uma PEC com muitos sinais pesa mais que um voto num projeto de sinais fracos. Só entram "
            "as votações cuja matéria tem nota (projetos de lei, de lei complementar e PECs; medidas provisórias e decretos "
            f"legislativos ficam de fora) e o número aparece com {lim.get('minimo_divisivas', 10)} delas ou mais. Ao lado, o saldo sem peso "
            "em duas metades: as votações de nota igual ou acima da mediana da Casa e as de nota abaixo dela (cada metade com "
            f"{peso.MINIMO_FAIXA} votações ou mais). “Quem muda quando pesa” é quem tem o saldo pesado a {peso.DIFERENCA} pontos ou mais do saldo sem peso "
            "nas mesmas votações. A nota mede sinais do registro (projetos parecidos anexados, origem, tipo de norma, apoio), não a "
            "repercussão: um projeto que voltou do Senado com o número de lá, como o do licenciamento ambiental, pode ter nota "
            "baixa. O saldo simples continua sendo o da página.",
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
            "desabilitado no seletor. Abaixo das faixas vêm as votações do tema. A fita tem um ponto por votação que opôs "
            "esquerda e direita, na ordem do tempo, com as da mesma matéria empilhadas: quadrado é o texto principal, círculo é "
            "destaque ou emenda, e a cor diz de que polo era o lado que prevaleceu. Os realces: texto principal; governo "
            f"derrotado (a liderança do Governo orientou o lado que perdeu); diferença pequena (menos de {lim.get('voto_apertado', 30)} votos entre o Sim e "
            "o Não na Câmara; no Senado, a mesma proporção das cadeiras); com obstrução. A matéria é representada pela votação "
            "do texto principal com mais votos (não pela redação final). Em “Quem votou”, cada linha é um partido (o do dia da "
            "votação) e cada ponto, um parlamentar: Sim à esquerda do eixo, Não à direita; ponto vazado é obstrução ou "
            "abstenção; o aro marca quem votou contra o próprio partido fechado; e a cor do ponto é o saldo da pessoa nas "
            f"votações do tema (com {ficha.MINIMO_TEMA_FAIXA} ou mais), não o voto daquele dia. O traço na barra do placar marca os votos Sim que a "
            "Constituição exige (veja “O placar”). Quando os votos um a um não batem com o placar que a Mesa anunciou, o cartão avisa.",
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
            "classe do processo. As duas classificações foram levadas a uma lista comum de macrotemas (config/congresso.yaml); a "
            "tabela “O que entra em cada tema”, abaixo, diz quais áreas oficiais caem em cada um.",
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
            "A ficha da matéria",
            "Para cada matéria votada nominalmente no Plenário nesta legislatura e para cada ponto de “o que espera × o que passa”: "
            "a ementa inteira (a que vale, quando a Câmara anotou uma nova), a explicação que a Casa escreveu (a ementa detalhada da "
            "Câmara; a explicação da ementa do Senado), as palavras-chave do registro e o endereço do texto integral. A página não "
            "resume o projeto: mostra o que a Casa registrou. Em cada votação: o que a Mesa pôs em votação e quem pediu o destaque "
            "(a Câmara registra; o Senado, não), o placar, o lado de cada polo com o Sim–Não de cada partido dele e a orientação do "
            "Governo. “O que o Sim queria dizer” sai da descrição oficial, pela regra da Casa: no texto principal, aprovar o texto; "
            "no destaque de emenda, aprovar a emenda; no destaque para votação em separado, manter o trecho no texto (o Não o retira).",
        ),
        (
            "O que cada um propõe",
            "As contas são das proposições em que a pessoa assina primeiro (autoria principal). “Os termos que mais marcam as "
            "propostas” são palavras-chave da indexação oficial da Câmara: as que estão em mais propostas da pessoa e em poucas do "
            f"conjunto (termo presente em mais de {round(100 * projetos.Vocabulario.COMUM)}% de todas as proposições, como “alteração”, fica de fora). "
            "O Senado não traz as palavras-chave nas listas de matérias.",
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
            "Frentes",
            "Uma frente parlamentar é um grupo registrado na Câmara em torno de uma causa; para registrar, basta reunir as "
            "assinaturas de um terço dos deputados e senadores (Ato da Mesa nº 69, de 2005), e o registro diz quem assinou e quem "
            "coordena (os dados abertos da Câmara, em lote). O Senado não publica quem assina. A página não lista todas: as "
            "frentes de causa, com o tema da aba em que cada causa se vota, ficam em config/congresso.yaml, com as duas pontas de "
            "cada tema. Cada cartão responde a uma pergunta: quem assina vota junto com quem coordena? A conta usa as votações "
            "esquerda × direita do tema da frente (nas frentes sem um tema só, todas): a parte das vezes em que cada membro em "
            "exercício votou igual ao coordenador (Sim ou Não; obstrução conta como Não). Ao lado vai a mesma conta para os "
            "deputados em exercício que não assinam: é a base de comparação, porque assinar só diz alguma coisa se quem assina "
            "vota diferente de quem não assina. Cada ponto do cartão é um deputado que assina, do lado em que ele vota no tema "
            f"(o saldo dele, com {ficha.MINIMO_TEMA_FAIXA} ou mais votações). “Do lado oposto ao do coordenador” só aparece quando o coordenador vota numa das pontas "
            "da régua (saldo até −60 ou acima de +60): são os que assinam e votam do outro lado, fora da faixa “dividido”. Com "
            f"menos de {lim.get('frente_poucas', FRENTE_POUCAS)} votações no tema, o cartão avisa que são poucas. Coordenador é só quem a lista de membros marca "
            "assim. O cartão abre a ficha da frente, com todos os que assinam, um a um. A frente não registra posição: o "
            "coordenador é quem a registra e responde por ela. A Câmara não registra quando cada um assinou.",
        ),
        (
            "Nas comissões",
            "As votações nominais das comissões (na Câmara, dos arquivos em lote de votações; no Senado, da API de votações em "
            "comissão das 16 permanentes). Ficam fora das contas do Plenário (saldo, presença, governismo): a maioria das decisões "
            "das comissões é simbólica, a maioria das nominais é de requerimento de pauta, e só os membros votam, então uma taxa "
            "mediria mais a sorte de ser membro do que a posição. A página lista os votos. Na Câmara, o voto é no parecer do relator "
            "(Sim é aprovar o parecer, que pode concluir pela rejeição; a conclusão vem do último parecer registrado) e só aparece "
            "quem votou. “Mudou de voto entre a comissão e o Plenário”: o Plenário votou o parecer de uma comissão; vale a última "
            "votação da comissão que aprovou esse parecer, e entra quem votou Sim ou Não nas duas. No Senado, o registro não diz se "
            "o item era terminativo nem o resultado: a página mostra a descrição oficial e o placar.",
        ),
        (
            "Cota parlamentar",
            "Os reembolsos da cota para o exercício da atividade parlamentar, nota a nota: na Câmara, os arquivos anuais oficiais "
            "(com o endereço de cada nota); no Senado, os dados abertos administrativos. O valor é o reembolsado: a nota menos a glosa "
            "(a parte que a própria Casa recusou) e menos o que o parlamentar devolveu. A média por mês é o total da legislatura "
            "dividido pelos meses fechados com despesa (o mês corrente, ainda incompleto, fica fora da média): a cota acumula "
            "dentro do ano, então um mês alto não diz nada sozinho; as listas só têm quem tem 6 meses ou mais. O fornecedor é "
            "contado pelo CNPJ; pagamento a pessoa física não tem o nome mostrado. “Um fornecedor só”: 25% ou mais do próprio total para uma empresa, com R$ 50 mil ou mais. “A mesma "
            "nota em mais de um gabinete”: mesmo CNPJ, número, data e valor reembolsados a pessoas diferentes (números que são só "
            "mês/ano, como “9/2026”, ficam de fora); o registro não diz o motivo. Na Câmara, ficam fora de todos os anos as passagens "
            "aéreas compradas pela agência da Casa (SIGEPA), que o arquivo oficial deixou de trazer em agosto de 2025: com elas em "
            "uns anos e não em outros, a comparação seria injusta; ao lado de quem parece gastar pouco, a página mostra quanto ele "
            "teve em passagens da agência em 2024, o último ano completo. No Senado, fica fora o reembolso de moradia, que o "
            "Senado lança no mesmo arquivo mas não conta como cota. O teto da cota muda por estado e não está nos dados abertos.",
        ),
        (
            "Escolha o seu candidato",
            "Não é lista de candidatos: é quem está no mandato hoje, um cartão cada, com os mesmos números do resto da página: o "
            "estado, o partido, de que lado vota (a régua), a presença e quanto vota com o próprio partido (com a mediana da Casa "
            f"ao lado) e o que propôs. “Exceção” é o tema em que a pessoa vota do lado oposto ao do seu padrão geral: ela tem lado no geral e, num tema com {ficha.MINIMO_TEMA_FAIXA} "
            "votações ou mais, fica do outro (havendo mais de um, o de mais votações). “Mudou de partido” traz o partido "
            "anterior e o mês do primeiro voto registrado pelo partido atual (a data da filiação não está nos dados de votação). "
            f"Os atalhos: mudou de partido na legislatura; vota diferente do partido (com a maioria dele em menos de {lim.get('escolha_fidelidade', 80)}% das "
            f"vezes); votou em menos de {lim.get('escolha_presenca', 60)}% das votações; muda de lado em algum tema (a exceção). Os filtros: o lado em que "
            f"vota em cada tema (com a esquerda: saldo do tema até {_saldo(corte[0])}; dividido: acima disso e até {_saldo(corte[1])}; com a direita: acima de "
            f"{_saldo(corte[1])}; só quem tem {ficha.MINIMO_TEMA_FAIXA} ou mais votações esquerda × direita no tema), o tema sobre o qual propôs (autoria principal), "
            "a frente que assina, a presença, o governismo, as propostas que viraram norma e a cota (na Câmara, comparada dentro "
            "do estado, porque o teto muda por estado; no Senado, com a Casa inteira). Escolhidos os temas, a lista se ordena por "
            "quanto cada um está do lado escolhido (a média, nos temas escolhidos, do saldo no sentido pedido). As fotos são as "
            "oficiais de cada Casa. Não é recomendação: é o registro de cada um diante do que você escolher.",
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
    polos = d.meta.get("polos") or {}
    temas_def = {t["id"]: {"camara": t.get("camara") or [], "senado": t.get("senado") or []} for t in d.meta.get("temas") or []}
    casas = [
        _casa(casa, d.casas[casa], d.resumo.get("pessoas") or [], campos, temas_nomes, limites, polos, temas_def, d.meta.get("temas") or [], str(d.meta.get("governo") or ""), d.meta.get("limiares") or {})
        for casa in ("cd", "sf")
        if casa in d.casas
    ]
    leg = d.meta.get("legislatura") or {}
    manifesto = {
        "v": VERSAO,
        "hoje": str(d.meta.get("hoje") or ""),
        "base": f"../data/{dados.PASTA}/{dados.FICHAS}/",
        "votosArq": f"../data/{dados.PASTA}/" + dados.VOTOS.replace("{casa}.json", ""),
        "materiasArq": f"../data/{dados.PASTA}/" + materias.ARQUIVO.replace("{casa}.json", ""),
        "frentesArq": f"../data/{dados.PASTA}/{dados.FRENTES}",
        # O endereço da foto oficial de cada um, pela chave ({id}): para os cartões e as dicas montados pelo script.
        "fotos": {"cd": ficha.FOTO_CAMARA, "sf": ficha.FOTO_SENADO},
        "polos": {"esquerda": list(polos.get("esquerda") or []), "direita": list(polos.get("direita") or [])},
        "cadeiras": dict(base.CADEIRAS),
        # O que entra em cada tema: as áreas temáticas da Câmara e as classes do Senado.
        "temasDef": {t["id"]: {"cd": t.get("camara") or [], "sf": t.get("senado") or []} for t in d.meta.get("temas") or []},
        "rel": {"alta": rel_meta.get("alta") or 60, "baixa": rel_meta.get("baixa") or 30, "rapida": relevancia.RAPIDA},
        "relNomes": relevancia.NOMES,
        "origens": relevancia.ORIGENS,
        "siglas": projetos.NOMES_SIGLA,
        "pesquisa": "pesquisa.html",
        "edicoes": "./",
        "legislatura": leg.get("numero"),
        "periodo": leg.get("rotulo") or "",
        "governo": d.meta.get("governo") or "",
        "campos": campos,
        "temas": {t["id"]: t["nome"] for t in d.meta.get("temas") or []},
        # Com quem cada um vota em cada tema, para as faixas filtradas: pessoa → {tema: [saldo, n, vira]}
        "tm": {p["k"]: p["tm"] for p in d.resumo.get("pessoas") or [] if p.get("ex") and p.get("tm")},
        # Sobre o que cada um propõe: pessoa → {tema: propostas de autoria principal} (para "Escolha o seu candidato").
        "pt": {p["k"]: p["pt"] for p in d.resumo.get("pessoas") or [] if p.get("ex") and p.get("pt")},
        "temasCurtos": {t["id"]: t.get("curto") or t["nome"] for t in d.meta.get("temas") or []},
        # As frentes de causa (Câmara): a mediana de frentes por deputado, o nome de cada uma e quem assina quais.
        "frentes": {
            "mediana": ((d.casas.get("cd") or {}).get("frentes") or {}).get("mediana"),
            "nomes": {str(c["id"]): c.get("nome") or c.get("titulo") or "" for c in ((d.casas.get("cd") or {}).get("frentes") or {}).get("causas") or []},
            "temas": {str(c["id"]): c.get("tema") or "todas" for c in ((d.casas.get("cd") or {}).get("frentes") or {}).get("causas") or []},
        },
        "fr": {p["k"]: p["fr"] for p in d.resumo.get("pessoas") or [] if p.get("ex") and p.get("fr")},
        # A cota de cada Casa: a mediana da média mensal e o que fica fora da conta (para o dossiê).
        "cota": {
            casa: {"mediana": (d.casas[casa].get("cota") or {}).get("mediana"), "sem": (d.casas[casa].get("cota") or {}).get("sem") or ""}
            for casa in ("cd", "sf")
            if casa in d.casas and d.casas[casa].get("cota")
        },
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
        temas=[
            {"nome": t["nome"], "camara": "; ".join(t.get("camara") or []) or "—", "senado": "; ".join(t.get("senado") or []) or "—"}
            for t in d.meta.get("temas") or []
        ],
        metodo=_metodo(d),
        manifesto=json.dumps(manifesto, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c"),
        totais={
            "pessoas": sum(1 for p in pessoas if p["ex"]),
            "votacoes": sum(c.estat.get("total") or 0 for c in casas),
            "divisivas": sum(c.estat.get("divisivas") or 0 for c in casas),
            "tramitando": sum((d.casas[c.id].get("fila") or {}).get("tramitando") or 0 for c in casas),
            # Os cortes dos atalhos de "Escolha o seu candidato" (config/congresso.yaml, limiares).
            "escolha_fidelidade": int((d.meta.get("limiares") or {}).get("escolha_fidelidade") or 80),
            "escolha_presenca": int((d.meta.get("limiares") or {}).get("escolha_presenca") or 60),
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
