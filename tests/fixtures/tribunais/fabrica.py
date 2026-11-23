"""Uma aba Tribunais de mentira, no formato do contrato (``data/tribunais/``: ``meta.json``,
``achados.json``, ``stj.json``, ``stf.json`` e ``ministros/<id>.json``), para os testes da página
e as fotos de tela.

Tudo aqui é INVENTADO: os nomes dos ministros ("Ana Exemplar", "Bento Modelo"), das partes ("Banco
Exemplo S.A.") e todos os números. As réguas são as de verdade (``config/tribunais.yaml``: as 16
áreas, as subáreas, os recortes, os grupos da matriz e as linhas fora da grade), para a página
ser testada com os ids que os dados reais vão trazer.

As contas fecham entre si, como no arquivo de verdade: o acervo de um ministro é a soma das áreas
dele; a linha "média" de um grupo é a soma dos ministros da grade do grupo; o tribunal soma os
grupos e as linhas fora da grade; ``pares`` é a parte da área nos colegas da mesma Turma sem ele
(STJ) ou no tribunal sem ele e sem a Presidência (STF). A marca (▲/▽) segue o piso de efeito do
config (razão fora de [0,80; 1,25], 2 pontos ou mais, 30 processos ou mais) sem o teste de acaso.

Uso: ``gravar(tmp_path / "data")`` grava a pasta inteira e devolve o caminho de
``data/tribunais``; ``gravar(..., estado="fora_do_ar")`` simula a fonte do STJ que falhou (fica o
dado da véspera); ``tribunais=("stj",)`` simula o STF sem dados.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from qijournal.tribunais import VERSAO, modelo
from qijournal.tribunais.tpu import TPU

REGRAS = modelo.carregar_regras()
_TPU = TPU.carregar()
AREAS = REGRAS.ids_areas
HOJE = "2026-11-23"
DATA_DADOS = {"stj": "2026-11-20", "stf": "2026-11-22"}
NOMES = {"stj": "Superior Tribunal de Justiça", "stf": "Supremo Tribunal Federal"}

# Prenomes e sobrenomes que não são de ninguém: a página de teste nunca põe número inventado ao
# lado do nome de um ministro de verdade.
_PRE = ["Ana", "Bento", "Carla", "Davi", "Eva", "Fábio", "Gil", "Helena", "Ivo", "Júlia", "Lia", "Mário", "Nina", "Otto",
        "Paula", "Rui", "Sara", "Teo", "Vera", "Xavier"]
_SOB = ["Exemplar", "Modelo Lima", "Fictício", "Amostra Reis", "Protótipo", "Rascunho Dias", "Ensaio Prado", "Teste da Costa",
        "Maquete", "Esboço Neves", "Hipótese", "Simulado", "Provisório Leal", "Figurante", "Suposto Brandão", "Tentativa",
        "Ilustração", "Molde Farias", "Padrão", "Genérico Lopes", "Anônimo", "Qualquer", "Exemplo Bento", "Inventado",
        "Imaginário Sá", "Fulano", "Beltrano Cruz", "Sicrano", "Exemplo Neto", "Rascunho Filho", "Modelo Júnior",
        "Teste Mendes", "Amostra Vidal", "Ensaio Matos", "Esboço Pires"]

# A mistura de áreas de cada grupo (pesos relativos): no STJ a Seção decide o ramo.
_MISTURA = {
    ("stj", "s1"): {"tributario": 28, "administrativo": 30, "previdenciario": 9, "saude": 5, "ambiental": 3, "processual": 1,
                    "penal": 1, "civil": 2, "trabalho": 0.2, "outros": 0.1, "sem_assunto": 0.05, "eleitoral": 0.1},
    ("stj", "s2"): {"civil": 32, "consumidor": 24, "familia": 9, "empresarial": 8, "saude": 7, "processual": 0.4,
                    "administrativo": 0.6, "ambiental": 0.3, "outros": 0.1},
    ("stj", "s3"): {"penal": 93, "administrativo": 0.5, "processual": 0.2, "civil": 0.2, "sem_assunto": 0.05},
    ("stj", "pr"): {"administrativo": 30, "penal": 25, "civil": 15, "tributario": 10, "processual": 5},
    ("stf", "t1"): {"penal": 34, "administrativo": 26, "tributario": 10, "trabalho": 6, "saude": 5, "processual": 5,
                    "constitucional": 4, "civil": 4, "previdenciario": 3, "consumidor": 1, "empresarial": 1, "ambiental": 1,
                    "eleitoral": 1, "familia": 0.5, "outros": 0.1},
    ("stf", "t2"): {"penal": 30, "administrativo": 28, "tributario": 11, "trabalho": 5, "saude": 6, "processual": 4,
                    "constitucional": 4, "civil": 4, "previdenciario": 3, "consumidor": 1, "empresarial": 1, "ambiental": 1,
                    "eleitoral": 1, "familia": 0.5, "outros": 0.1},
    ("stf", "pr"): {"constitucional": 30, "administrativo": 25, "penal": 20, "tributario": 15, "processual": 10},
}
# Quantos ministros por grupo (como os tribunais de verdade).
_TAMANHO = {"stj": {"s1": 10, "s2": 10, "s3": 10, "pr": 3}, "stf": {"t1": 4, "t2": 5, "pr": 1}}
_TURMAS = {"s1": ("T1", "T2"), "s2": ("T3", "T4"), "s3": ("T5", "T6"), "t1": ("T1",), "t2": ("T2",), "pr": (None,)}
_SECAO = {"s1": "S1", "s2": "S2", "s3": "S3"}
_CLASSES = {"stj": ["AREsp", "REsp", "HC", "RHC", "RMS", "CC", "MS"], "stf": ["ARE", "RE", "HC", "Rcl", "ADI", "MS", "RHC"]}
# Litigantes inventados (id, nome, tipo). Os ids "cnpj-…" imitam a PJ fora do catálogo.
_PARTES = [
    ("ente-federal-a", "Ente Público Federal A", "ente"), ("instituto-previdenciario-b", "Instituto Previdenciário B", "ente"),
    ("estado-c", "Estado C", "ente"), ("municipio-d", "Município D", "ente"), ("banco-exemplo", "Banco Exemplo S.A.", "pj"),
    ("operadora-e", "Operadora de Saúde E", "pj"), ("seguradora-f", "Seguradora F S.A.", "pj"),
    ("concessionaria-g", "Concessionária G", "pj"), ("cnpj-12345678", "Construtora H Ltda.", "pj"),
    ("telefonica-i", "Telefônica I S.A.", "pj"), ("ministerio-publico-j", "Ministério Público J", "ente"),
]
# Litigante que só tem nome no mapa ``partes.nomes`` (o contas.py publica ali o nome de todo grupo
# citado nas listas): aparece nas mini-folhas, fora do topo do tribunal. Id "nome-…" = PJ sem CNPJ.
_PARTES_SO_NOMES = [("nome-cooperativa-k", "Cooperativa K", "pj")]
_NOMES = {pid: {"nome": nome, "tipo": tipo} for pid, nome, tipo in _PARTES + _PARTES_SO_NOMES}
_ORGAO_NOME = {"T1": "Primeira Turma", "T2": "Segunda Turma", "T3": "Terceira Turma", "T4": "Quarta Turma",
               "T5": "Quinta Turma", "T6": "Sexta Turma"}


def _r4(x: float) -> float:
    return round(x, 4)


def _virgula(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def _n(x: int) -> str:
    """``24341`` → ``"24.341"`` (os títulos dos achados vêm escritos, como os de verdade)."""
    return f"{int(x):,}".replace(",", ".")


def _repartir(total: int, pesos: dict[str, float]) -> dict[str, int]:
    """``total`` repartido pelos pesos, em inteiros que somam exatamente ``total``."""
    soma = sum(pesos.values()) or 1
    brutos = {k: total * v / soma for k, v in pesos.items()}
    saida = {k: math.floor(v) for k, v in brutos.items()}
    falta = total - sum(saida.values())
    for k in sorted(brutos, key=lambda k: (-(brutos[k] - saida[k]), k))[:falta]:
        saida[k] += 1
    return saida


def _idade(rnd: random.Random, base: int, n: int) -> dict[str, Any]:
    mediana = int(base * rnd.uniform(0.8, 1.25))
    return {"mediana": mediana, "p25": int(mediana * 0.38), "p75": int(mediana * 2.4), "p90": int(mediana * 5.2),
            "max": int(mediana * 30), "mais_180": int(n * 0.3), "mais_365": int(n * 0.12)}


def _ministros(trib: str, rnd: random.Random) -> list[dict[str, Any]]:
    """Os ministros inventados, grupo a grupo, com os atributos da composição."""
    saida, k = [], 0 if trib == "stj" else 13
    for g in REGRAS.composicao[trib].grupos:
        for i in range(_TAMANHO[trib][g.id]):
            nome = f"{_PRE[k % len(_PRE)]} {_SOB[k % len(_SOB)]}"
            k += 1
            turma = _TURMAS[g.id][i % len(_TURMAS[g.id])]
            saida.append({"id": f"{trib}-{modelo.slug(nome)}", "nome": nome, "genero": "f" if _PRE.index(nome.split()[0]) % 2 == 0 else "m",
                          "grupo": g.id, "turma": turma, "secao": _SECAO.get(g.id) if trib == "stj" else None,
                          "grade": g.id != "pr"})
    return saida


def _assunto(area: str, sub: str | None) -> dict[str, Any]:
    """O assunto de uma mini-folha: um código de verdade da TPU, o da subárea ou o da área (a raiz antes do nó) (o nome e o
    caminho saem da TPU versionada, como o ``contas.py`` faz); sem código na régua, nada."""
    codigos = [s.codigos for s in REGRAS.subareas if s.id == sub and s.codigos] + [REGRAS.area(area).raizes, REGRAS.area(area).nos]
    codigo = next((c[0] for c in codigos if c), None)
    if codigo is None:
        return {"codigo": None, "nome": None, "caminho": None}
    return {"codigo": codigo, "nome": _TPU.nome(codigo), "caminho": _TPU.caminho(codigo)}


def _folha(trib: str, rnd: random.Random, ministro: dict[str, Any] | None, area: str, dias: int, hoje: date, segredo: bool = False) -> dict[str, Any]:
    """Uma mini-folha (contrato §7): sem nome de parte, sem processo em segredo."""
    classe = rnd.choice(_CLASSES[trib])
    numero = str(rnd.randint(1_000_000, 2_999_999) if trib == "stj" else rnd.randint(100_000, 1_599_999))
    distribuicao = hoje - timedelta(days=dias)  # "sem decisão há" conta da distribuição (sem decisão depois)
    autuacao = distribuicao - timedelta(days=rnd.randint(3, 20))
    registro = f"{autuacao.year}0{numero[:7]}" if trib == "stj" else None
    subareas = REGRAS.subareas_de(area)
    sub = subareas[rnd.randrange(len(subareas))].id if subareas else None
    ativo = rnd.choice([("ente", "ente-federal-a"), ("pj", "banco-exemplo"), ("pf", None), ("ente", "instituto-previdenciario-b")])
    passivo = rnd.choice([("pf", None), ("pj", "seguradora-f"), ("ente", "estado-c"), ("pj", "nome-cooperativa-k")])
    link = REGRAS.fontes["links_oficiais"][trib].format(registro=registro or "", classe=classe, numero=numero)
    flags = {"liminar": True} if rnd.random() < 0.2 else {}
    return {
        "id": f"{trib}-{classe}-{numero}", "classe": classe, "numero": numero,
        "cnj": f"{rnd.randint(0, 9999999):07d}{rnd.randint(10, 99)}{autuacao.year}8{rnd.randint(10, 26):02d}{rnd.randint(1, 9999):04d}",
        "registro": registro, "uf": rnd.choice(["SP", "RJ", "MG", "RS", "PR", "BA", "DF"]), "area": area, "subarea": sub,
        "assunto": _assunto(area, sub),
        "relator_id": ministro["id"] if ministro else None,
        "local": f"GABINETE DO MINISTRO {ministro['nome'].upper()}" if ministro else "COORDENADORIA DA SEGUNDA TURMA",
        "na_mesa": ministro is not None,
        "datas": {"autuacao": autuacao.isoformat() if trib == "stj" else None, "ano_autuacao": autuacao.year,
                  "distribuicao": distribuicao.isoformat() if trib == "stj" else None, "ultima_decisao": None,
                  "andamento": (hoje - timedelta(days=dias)).isoformat() if trib == "stf" else None},
        "sem_decisao_dias": dias if trib == "stj" else None, "concluso_dias": dias if trib == "stf" else None,
        "flags": flags,
        "partes": [{"polo": "ativo", "tipo": ativo[0], "grupo": ativo[1]}, {"polo": "passivo", "tipo": passivo[0], "grupo": passivo[1]}],
        "link": link,
    }


def painel(trib: str, *, semente: int = 7) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """O painel de um tribunal (``stj.json``/``stf.json``) e os dossiês dos ministros dele."""
    rnd = random.Random(f"{trib}-{semente}")
    hoje = date.fromisoformat(DATA_DADOS[trib])
    comp = REGRAS.composicao[trib]
    lim = REGRAS.limiares
    ministros = _ministros(trib, rnd)
    # o acervo de cada um, por área
    for i, m in enumerate(ministros):
        pesos = {a: v * rnd.uniform(0.6, 1.5) for a, v in _MISTURA[(trib, m["grupo"])].items()}
        if m["grade"] and i % 4 == 1:  # um em cada quatro com uma área bem acima dos colegas
            alvo = sorted(pesos, key=lambda a: -pesos[a])[1]
            pesos[alvo] *= 2.4
        if m["grade"] and i % 4 == 3:  # e um com uma área bem abaixo
            alvo = sorted(pesos, key=lambda a: -pesos[a])[1]
            pesos[alvo] *= 0.35
        total = (rnd.randint(5200, 14800) if trib == "stj" else rnd.randint(600, 1900)) if m["grade"] else rnd.randint(250, 900)
        m["n"] = _repartir(total, pesos)
        m["processos"] = total
    # pares e marca: a Turma sem ele (STJ) ou o tribunal da grade sem ele (STF)
    razao_lim, pontos, minimo = lim["comparacao"]["razao"], lim["comparacao"]["pontos"] / 100, lim["comparacao"]["minimo_area"]
    grade = [m for m in ministros if m["grade"]]
    for m in ministros:
        pares = [o for o in grade if o is not m and (o["turma"] == m["turma"] if trib == "stj" else True)] if m["grade"] else []
        base = sum(o["processos"] for o in pares)
        m["areas"] = {}
        for a in AREAS:
            n = m["n"].get(a, 0)
            p = n / m["processos"]
            pp = (sum(o["n"].get(a, 0) for o in pares) / base) if base else None
            razao = round(p / pp, 2) if pp else None
            marca = 0
            if razao is not None and m["grade"] and (n >= minimo or pp * m["processos"] >= minimo) and abs(p - pp) >= pontos:
                marca = 1 if razao >= razao_lim[1] else -1 if razao <= razao_lim[0] else 0
            m["areas"][a] = {"n": n, "p": _r4(p), "pares": _r4(pp) if pp is not None else None, "razao": razao, "marca": marca}
    # selos: uma convocada recém-chegada, um ministro com herança, um com poucos processos (STF: a Presidência)
    for m in ministros:
        m["selos"], m["convocado"] = [], None
    if trib == "stj":
        conv = next(m for m in ministros[20:30] if m["genero"] == "f")  # uma convocada na Terceira Seção
        conv["selos"] = ["convocado", "recem_chegado", "heranca"]
        conv["convocado"] = {"tribunal": "TJXX", "vaga_de": None, "desde": "2026-11-17"}
    ministros[-1]["selos"] = ["sem_distribuicao"]

    resumo: list[dict[str, Any]] = []
    dossies: dict[str, dict[str, Any]] = {}
    folhas_fila: list[dict[str, Any]] = []
    for m in ministros:
        total = m["processos"]
        na_mesa = int(total * rnd.uniform(0.55, 0.86))
        onde = {"gabinete": na_mesa, "outro_gabinete": int(total * 0.004), "cartorio": 0, "mp": int(total * 0.02), "presidencia": 0, "stf": int(total * 0.003), "outro": 0}
        onde["cartorio"] = total - sum(onde.values())
        maiores = sorted((a for a in AREAS if m["n"].get(a)), key=lambda a: (-m["n"][a], a))
        subareas = []
        for a in maiores[:3]:
            for s in REGRAS.subareas_de(a)[:2]:
                n = int(m["n"][a] * rnd.uniform(0.2, 0.45))
                subareas.append([s.id, n, _r4(n / total)])
        subareas.sort(key=lambda s: (-s[1], s[0]))
        partes = sorted(([p[0], int(total * rnd.uniform(0.01, 0.06))] for p in rnd.sample(_PARTES, 4)), key=lambda x: (-x[1], x[0]))[:3]
        antigos = [_folha(trib, rnd, m, maiores[0], dias, hoje) for dias in sorted((rnd.randint(500, 3200) for _ in range(15)), reverse=True)]
        folhas_fila += antigos[:3] if m["grade"] else []
        sem_decisao = _idade(rnd, 110 if trib == "stj" else 70, na_mesa)
        no_tribunal = _idade(rnd, 300, total)
        no_tribunal.update({"mais_180": 0, "mais_365": 0})
        entradas = {"sorteio_12m": int(total * 0.45), "heranca_12m": 1014 if "heranca" in m["selos"] else 0,
                    "sorteio_30d": int(total * 0.04), "heranca_30d": 1014 if "heranca" in m["selos"] else 0} if m["grade"] else None
        item = {
            "id": m["id"], "nome": m["nome"], "genero": m["genero"], "grupo": m["grupo"], "turma": m["turma"], "secao": m["secao"],
            "cargos": ["presidente_turma"] if m is grade[0] else (["presidente"] if m["grupo"] == "pr" and m is ministros[-1] else []),
            "convocado": m["convocado"], "selos": m["selos"],
            "comparavel": m["grade"] and not set(m["selos"]) & {"heranca", "recem_chegado"},
            "processos": total, "na_mesa": na_mesa, "onde": onde, "segredo": int(total * (0.3 if m["grupo"] == "s2" else 0.05)),
            "sobrestados": int(total * 0.01), "liminar": int(total * 0.08), "ente_publico": int(total * (0.6 if m["grupo"] in ("s1", "t1", "t2") else 0.02)),
            "sem_decisao": sem_decisao, "no_tribunal": no_tribunal, "areas": m["areas"],
            "subareas": subareas[: lim["listas"]["subareas_painel"]], "partes": partes,
            "mais_antigos": antigos[: lim["listas"]["painel_mais_antigos"]],
            "entradas": entradas,
            "pauta": {"itens": int(total * 0.04), "principal": int(total * 0.006), "interno": int(total * 0.034)} if m["grade"] else None,
            "decisoes": {"processos": int(total * 0.4), "documentos": int(total * 0.45), "monocraticas": int(total * 0.32), "acordaos": int(total * 0.13)} if m["grade"] else None,
            "temas": rnd.randint(0, 9) if m["grade"] else None,
        }
        resumo.append(item)
        dossies[m["id"]] = _dossie(trib, item, m, antigos, rnd, hoje)

    # os grupos (a linha "média" de cada um) e o tribunal
    grupos = []
    for g in comp.grupos:
        membros = [m for m in ministros if m["grupo"] == g.id]
        da_grade = [m for m in membros if m["grade"]]
        soma = sum(m["processos"] for m in da_grade)
        grupos.append({
            "id": g.id, "nome": g.nome, "descricao": g.descricao, "turmas": list(g.turmas), "ministros": [m["id"] for m in membros],
            "processos": soma, "na_mesa": sum(r["na_mesa"] for r in resumo if r["grupo"] == g.id and r["id"] in {m["id"] for m in da_grade}),
            "areas": {a: [n, _r4(n / soma)] for a in AREAS if (n := sum(m["n"].get(a, 0) for m in da_grade))} if soma else {},
        })
    soma_grade = sum(m["processos"] for m in grade)
    media = {"processos": soma_grade, "areas": {a: [n, _r4(n / soma_grade)] for a in AREAS if (n := sum(m["n"].get(a, 0) for m in grade))}}
    fora = []
    for i, linha in enumerate(comp.fora_da_grade):
        n = [104808, 9479, 19246, 3840, 376, 3414, 261][i] if trib == "stj" else [4155, 78, 697, 1581][i]
        if linha.id == "cogepac" and trib == "stj":
            n = 0  # linha vazia: some da página
        if n:
            pesos = _MISTURA[(trib, "pr")]
            fora.append({"id": linha.id, "rotulo": linha.rotulo, "nota": linha.nota, "n": n, "na_mesa": None,
                         "areas": {a: v for a, v in _repartir(n, pesos).items() if v},
                         "relatores": [[linha.relatores[0] if linha.relatores else "MIN. FULANO DE TAL", n]]})
    total_trib = sum(m["processos"] for m in ministros) + sum(f["n"] for f in fora)
    area_trib = {a: sum(m["n"].get(a, 0) for m in ministros) + sum(f["areas"].get(a, 0) for f in fora) for a in AREAS}
    areas = []
    for a in AREAS:
        n = area_trib[a]
        if not n and a not in ("sem_assunto",):
            n = 0
        subs = REGRAS.subareas_de(a)
        areas.append({
            "id": a, "n": n, "p": _r4(n / total_trib), "na_grade": sum(m["n"].get(a, 0) for m in grade),
            "por_grupo": {g["id"]: g["areas"].get(a, [0])[0] for g in grupos if g["id"] != "pr"},
            "subareas": sorted(([s.id, int(n * f)] for s, f in zip(subs, (0.3, 0.22, 0.14, 0.09, 0.05), strict=False)), key=lambda x: (-x[1], x[0])) + ([[f"{a}.outros", int(n * 0.2)]] if subs else []),
            "assuntos": [[7681, int(n * 0.3)], [10433, int(n * 0.12)]] if a in ("civil", "consumidor") else [[3607, int(n * 0.31)], [3415, int(n * 0.2)]] if a == "penal" else [],
            "partes": [[p[0], int(n * 0.05)] for p in _PARTES[:3]] if n else [],
            "tambem_toca": [["processual", int(n * 0.04)]] if n and a != "processual" else [],
        })
    recortes = [{"id": r.id, "n": int(total_trib * f), "p": _r4(f), "na_grade": int(soma_grade * f)}
                for r, f in zip(REGRAS.recortes, (0.088, 0.027, 0.04, 0.02, 0.014, 0.06, 0.012), strict=False)]
    top = []
    for i, (pid, nome, tipo) in enumerate(_PARTES):
        n = int(total_trib * (0.054 - i * 0.004))
        top.append({"id": pid, "nome": nome, "tipo": tipo, "n": n, "p": _r4(n / total_trib), "recorre": int(n * (0.25 + 0.05 * (i % 5))),
                    "responde": int(n * (0.7 - 0.05 * (i % 5))), "outro": 0, "mesas": len(grade) - i, "area": ["administrativo", "previdenciario", "tributario", "tributario", "civil", "saude", "civil", "consumidor", "civil", "consumidor", "penal"][i]})
        top[-1]["outro"] = n - top[-1]["recorre"] - top[-1]["responde"]
    ente_n = sum(r["ente_publico"] for r in resumo)
    universo = {"processos": total_trib, "na_grade": soma_grade, "na_mesa": sum(r["na_mesa"] for r in resumo if r["grupo"] != "pr"),
                "segredo": sum(r["segredo"] for r in resumo), "sobrestados": sum(r["sobrestados"] for r in resumo),
                "liminar": sum(r["liminar"] for r in resumo), "ente_publico": ente_n,
                "sem_decisao_180": sum(r["sem_decisao"]["mais_180"] for r in resumo if r["grupo"] != "pr"),
                "sem_decisao_365": sum(r["sem_decisao"]["mais_365"] for r in resumo if r["grupo"] != "pr"),
                "pessoas_fisicas": int(total_trib * 0.43)}
    folhas_fila.sort(key=lambda f: (-(f["sem_decisao_dias"] or f["concluso_dias"] or 0), f["id"]))
    vistas = []
    if trib == "stf":
        vistas = [{"processo": folhas_fila[1], "ministro_vista": ministros[2]["id"], "relator_id": folhas_fila[1]["relator_id"], "sessao": "2026-11-25"}]
    p = {
        "v": VERSAO, "trib": trib, "nome": NOMES[trib], "sigla": trib.upper(), "data_dados": DATA_DADOS[trib],
        "universo": universo,
        "idade": {"sem_decisao": _idade(rnd, 112, universo["na_mesa"]), "no_tribunal": {**_idade(rnd, 301, soma_grade), "mais_180": 0, "mais_365": 0}},
        "grupos": grupos, "media": media, "ministros": resumo, "fora_da_grade": fora, "areas": areas, "recortes": recortes,
        "partes": {"top": top, "pessoas_fisicas": universo["pessoas_fisicas"],
                   "ente_publico": {"n": ente_n, "p": _r4(ente_n / total_trib), "por_grupo": {g["id"]: [sum(r["ente_publico"] for r in resumo if r["grupo"] == g["id"]), g["processos"]] for g in grupos if g["id"] != "pr"}},
                   "concentracao_top20": 0.41, "nomes": _NOMES},
        "fila": {"mais_antigos": folhas_fila[: lim["listas"]["fila_mais_antigos"]],
                 "por_grupo": {g["id"]: {"na_mesa": g["na_mesa"], "mais_180": int(g["na_mesa"] * 0.21), "mais_365": int(g["na_mesa"] * 0.09)} for g in grupos if g["id"] != "pr"},
                 "sobrestados": {"total": universo["sobrestados"], "por_motivo": {"ST": int(universo["sobrestados"] * 0.54), "RG": int(universo["sobrestados"] * 0.42), "T": int(universo["sobrestados"] * 0.04)}},
                 "vistas": vistas},
        "entradas": {"fonte": f"{trib}_atas", "janela": ["2025-11-21", DATA_DADOS[trib]], "dias_com_ata": 240,
                     "total": {"sorteio": int(soma_grade * 1.9), "heranca": 3100, "registro": int(soma_grade * 2.4) if trib == "stj" else 0},
                     "ultimos_30d": {"sorteio": int(soma_grade * 0.16), "heranca": 1014},
                     "por_mes": [[f"{2025 + (11 + i) // 12}-{(11 + i) % 12 + 1:02d}", int(soma_grade * rnd.uniform(0.13, 0.18)), 1014 if i == 11 else rnd.choice([0, 0, 120])] for i in range(12)]},
        "pautas": {"fonte": f"{trib}_pautas" if trib == "stj" else "stf_pauta", "data_dados": "2026-11-22", "sessoes": [["2026-11-24", "T3" if trib == "stj" else "PL", 120], ["2026-11-25", "T5" if trib == "stj" else "T1", 310]],
                   "itens": 8545, "principal": 1370, "interno": 7175, "por_tipo": {"AgInt": 3458, "AgRg": 2900, "EDcl": 791},
                   "vistas": 3 if trib == "stf" else None, "destaques": 2 if trib == "stf" else None},
        "decisoes": {"fonte": "stj_integras" if trib == "stj" else "stf_jurisprudencia", "janela": ["2025-10-16", "2026-10-15"] if trib == "stj" else ["2025-11-23", "2026-11-22"],
                     "ultimo_arquivo": "2026-10-15" if trib == "stj" else None, "atraso_dias": 39 if trib == "stj" else None,
                     "processos": int(soma_grade * 0.85), "documentos": int(soma_grade), "monocraticas": int(soma_grade * 0.76), "acordaos": int(soma_grade * 0.24),
                     "acordaos_recurso_interno": int(soma_grade * 0.14), "presidencia": int(soma_grade * 0.19)},
        "temas": {"fonte": "stj_precedentes" if trib == "stj" else "stf_rg", "pendentes": 130 if trib == "stj" else 151,
                  "por_tipo": {"repetitivo": 120, "iac": 10, "rg": 0} if trib == "stj" else {"repetitivo": 0, "iac": 0, "rg": 151},
                  "relator_saiu": [["Relator Que Saiu", 2]]},
        "valor": {"publicado": False, "texto": "Nenhuma fonte aberta do STF nem do STJ publica o valor da causa. A página não estima.",
                  "medidas": {"ente_publico": {"n": ente_n, "p": _r4(ente_n / total_trib)}, "liminar": {"n": universo["liminar"], "p": _r4(universo["liminar"] / total_trib)},
                              "recortes": {r["id"]: r["n"] for r in recortes if r["id"] in REGRAS.recortes_economicos}}},
    }
    return p, dossies


def _dossie(trib: str, item: dict[str, Any], m: dict[str, Any], antigos: list[dict[str, Any]], rnd: random.Random, hoje: date) -> dict[str, Any]:
    """O dossiê de um ministro (contrato §6)."""
    total = item["processos"]
    faixas = ["ate_6m", "6_12m", "1_2a", "2_3a", "3_5a", "mais_5a"]
    no_trib = _repartir(total, dict(zip(faixas, (30, 22, 24, 12, 8, 4), strict=True)))
    sem_dec = _repartir(item["na_mesa"], dict(zip(faixas, (55, 20, 13, 6, 4, 2), strict=True)))
    areas = [{"id": a, **c, "tambem_toca": int(c["n"] * 0.05)} for a, c in m["areas"].items() if c["n"]]
    subareas = [{"id": s[0], "area": s[0].split(".")[0], "n": s[1], "p": s[2], "pares": _r4(s[2] * rnd.uniform(0.8, 1.2))} for s in item["subareas"]]
    tipos = ["principal", "AgInt", "AgRg", "EDcl"]
    em_pauta = [{"processo": f, "sessao": (hoje + timedelta(days=2 + i)).isoformat(), "orgao": item["turma"] or "PL", "tipo": tipos[i % 4]} for i, f in enumerate(antigos[5:8])]
    em_vista = [{"processo": antigos[9], "ministro_vista": None, "dias": None}] if trib == "stf" else []
    meses = [f"{2025 + (11 + i) // 12}-{(11 + i) % 12 + 1:02d}" for i in range(12)]
    return {
        "v": VERSAO, "id": item["id"], "trib": trib, "data_dados": DATA_DADOS[trib],
        "ministro": {"id": item["id"], "nome": item["nome"], "nome_completo": item["nome"] + " de Exemplo", "genero": item["genero"],
                     "turma": item["turma"], "secao": item["secao"], "orgao_nome": _ORGAO_NOME.get(item["turma"] or "", None) if trib == "stj" else ({"T1": "Primeira Turma", "T2": "Segunda Turma"}.get(item["turma"] or "")),
                     "corte_especial": trib == "stj" and m["grupo"] == "s2", "cargos": item["cargos"], "grade": m["grade"], "posse": "2011-06-13",
                     "turma_desde": "2026-11-17" if "recem_chegado" in item["selos"] else "2016-08-25", "convocado": item["convocado"],
                     "gabinete": f"GABINETE DO MINISTRO {item['nome'].upper()}", "grupo": item["grupo"], "link_oficial": None},
        "resumo": {k: v for k, v in item.items() if k != "mais_antigos"},
        "areas": areas,
        "subareas": subareas,
        "recortes": [{"id": "bancario", "n": int(total * 0.12), "p": 0.12, "pares": 0.14}, {"id": "dano_moral", "n": int(total * 0.04), "p": 0.04, "pares": 0.035}],
        "classes": sorted(([c, int(total * f)] for c, f in zip(_CLASSES[trib], (0.48, 0.31, 0.09, 0.05, 0.03, 0.02, 0.02), strict=True)), key=lambda x: (-x[1], x[0])),
        "onde": item["onde"],
        "idade": {"no_tribunal": {"faixas": [[f, no_trib[f]] for f in faixas], "mediana": item["no_tribunal"]["mediana"], "p90": item["no_tribunal"]["p90"]},
                  "sem_decisao": {"faixas": [[f, sem_dec[f]] for f in faixas], "mediana": item["sem_decisao"]["mediana"], "p90": item["sem_decisao"]["p90"],
                                  "mais_180": item["sem_decisao"]["mais_180"], "mais_365": item["sem_decisao"]["mais_365"]}},
        "mais_antigos": antigos,
        "em_pauta": em_pauta,
        "em_vista": em_vista,
        "partes": {"top": [{"id": pid, "nome": nome, "tipo": tipo, "n": n, "recorre": int(n * 0.4), "responde": n - int(n * 0.4), "outro": 0,
                            "pares": 0.041, "razao": 1.26} for (pid, nome, tipo), n in zip(_PARTES[:6], (321, 290, 180, 120, 90, 60), strict=True)],
                   "pessoas_fisicas": int(total * 0.43), "ente_publico": {"n": item["ente_publico"], "p": _r4(item["ente_publico"] / total), "pares": 0.2},
                   "nomes": _NOMES},
        "entradas": {"janela": ["2025-11-21", DATA_DADOS[trib]], "sorteio": (item["entradas"] or {}).get("sorteio_12m", 0), "heranca": (item["entradas"] or {}).get("heranca_12m", 0),
                     "ultimos_30d": {"sorteio": (item["entradas"] or {}).get("sorteio_30d", 0), "heranca": (item["entradas"] or {}).get("heranca_30d", 0)},
                     "por_mes": [[mes, int(total * rnd.uniform(0.03, 0.05)), 1014 if ("heranca" in item["selos"] and i == 11) else 0] for i, mes in enumerate(meses)],
                     "heranca_dias": [["2026-11-19", 1014]] if "heranca" in item["selos"] else []} if item["entradas"] else None,
        "decisoes": {"janela": ["2025-10-16", "2026-10-15"], "ultimo_arquivo": "2026-10-15", **(item["decisoes"] or {}), "acordaos_recurso_interno": int(total * 0.07)} if item["decisoes"] else None,
        "temas": [{"numero": 1234 + i, "tipo": "repetitivo" if trib == "stj" else "rg", "titulo": f"Tema de exemplo número {i + 1}, sobre uma questão inventada.",
                   "situacao": "afetado" if trib == "stj" else "mérito pendente", "desde": "2025-03-01", "link": "https://www.stj.jus.br/" if trib == "stj" else "https://portal.stf.jus.br/"}
                  for i in range(item["temas"] or 0)] if item["temas"] is not None else None,
    }


def meta(paineis: dict[str, dict[str, Any]], *, estado: str = "ok") -> dict[str, Any]:
    """``meta.json`` (contrato §3). ``estado``: ``ok``; ``fora_do_ar`` (o acervo do STJ falhou hoje
    e ficam os dados de 20/11); ``parcial`` (as atas do STJ vieram até 21/11, com aviso)."""
    stj_ok = estado != "fora_do_ar"
    fontes = {
        "stj_acervo": {"nome": "STJ · acervo em tramitação", "trib": "stj", "url": "https://dadosabertos.web.stj.jus.br/dataset/acervo-em-tramitacao",
                       "data_dados": DATA_DADOS["stj"], "coletado_em": "2026-11-23T09:41:12Z", "ok": stj_ok,
                       "aviso": None if stj_ok else "O portal do STJ recusou o acesso (403) duas vezes em 23/11; os números do acervo são de 20/11.",
                       "registros": paineis.get("stj", {}).get("universo", {}).get("processos"), "velho_dias": 3, "atribuicao": "Fonte: STJ, Portal de Dados Abertos (CC-BY)"},
        "stj_atas": {"nome": "STJ · atas de distribuição", "trib": "stj", "url": "https://dadosabertos.web.stj.jus.br/dataset/atas-de-distribuicao",
                     "data_dados": "2026-11-21", "coletado_em": "2026-11-23T09:43:02Z", "ok": True,
                     "aviso": "A ata de 22/11 ainda não foi publicada; as entradas são até 21/11." if estado == "parcial" else None,
                     "registros": 5466, "velho_dias": 4, "atribuicao": "Fonte: STJ, Portal de Dados Abertos (CC-BY)"},
        "stj_pautas": {"nome": "STJ · pautas futuras", "trib": "stj", "url": "https://dadosabertos.web.stj.jus.br/dataset/pautas-futuras", "data_dados": "2026-11-22",
                       "coletado_em": "2026-11-23T09:43:30Z", "ok": True, "aviso": None, "registros": 8545, "velho_dias": 4, "atribuicao": "Fonte: STJ, Portal de Dados Abertos (CC-BY)"},
        "stj_precedentes": {"nome": "STJ · precedentes qualificados", "trib": "stj", "url": "https://dadosabertos.web.stj.jus.br/dataset/precedentes-qualificados", "data_dados": "2026-11-20",
                            "coletado_em": "2026-11-23T09:43:50Z", "ok": True, "aviso": None, "registros": 1310, "velho_dias": 8, "atribuicao": "Fonte: STJ, Portal de Dados Abertos (CC-BY)"},
        "stj_integras": {"nome": "STJ · decisões publicadas (íntegras)", "trib": "stj", "url": "https://dadosabertos.web.stj.jus.br/dataset/integras-de-decisoes-terminativas-e-acordaos-do-diario-da-justica",
                         "data_dados": "2026-10-15", "coletado_em": "2026-11-23T09:44:10Z", "ok": True, "aviso": "O último arquivo publicado pelo STJ é de 15/10/2026 (39 dias).",
                         "registros": 1275, "velho_dias": 45, "atraso_dias": 39, "atribuicao": "Fonte: STJ, Portal de Dados Abertos (CC-BY)"},
        "stf_acervo": {"nome": "STF · acervo (lista geral)", "trib": "stf", "url": "https://www.stf.jus.br/arquivo/cms/publicacaoBOInternet/anexo/acervo/Acervo_STF_Lista_Geral.xlsx",
                       "data_dados": DATA_DADOS["stf"], "coletado_em": "2026-11-23T09:45:30Z", "ok": True, "aviso": None,
                       "registros": paineis.get("stf", {}).get("universo", {}).get("processos"), "velho_dias": 3, "atribuicao": "Fonte: STF"},
        "stf_atas": {"nome": "STF · atas de distribuição", "trib": "stf", "url": "https://portal.stf.jus.br/atadistribuicao/", "data_dados": "2026-11-20",
                     "coletado_em": "2026-11-23T09:46:00Z", "ok": True, "aviso": None, "registros": 228, "velho_dias": 4, "atribuicao": "Fonte: STF"},
        "stf_jurisprudencia": {"nome": "STF · pesquisa de jurisprudência", "trib": "stf", "url": "https://jurisprudencia.stf.jus.br/", "data_dados": "2026-11-22",
                               "coletado_em": "2026-11-23T09:46:30Z", "ok": True, "aviso": None, "registros": 61000, "velho_dias": 4, "atribuicao": "Fonte: STF"},
        "stf_rg": {"nome": "STF · repercussão geral", "trib": "stf", "url": "https://portal.stf.jus.br/jurisprudenciaRepercussao/", "data_dados": "2026-11-21",
                   "coletado_em": "2026-11-23T09:47:00Z", "ok": True, "aviso": None, "registros": 1380, "velho_dias": 10, "atribuicao": "Fonte: STF"},
        "stf_pauta": {"nome": "STF · pauta", "trib": "stf", "url": "https://portal.stf.jus.br/pauta/", "data_dados": "2026-11-22",
                      "coletado_em": "2026-11-23T09:47:20Z", "ok": True, "aviso": None, "registros": 310, "velho_dias": 4, "atribuicao": "Fonte: STF"},
        "tpu": {"nome": "CNJ · Tabelas Processuais Unificadas", "trib": "cnj", "url": "https://www.cnj.jus.br/sgt/versoes.php", "data_dados": "2026-11-05",
                "coletado_em": "2026-11-23T09:40:00Z", "ok": True, "aviso": None, "registros": 5650, "velho_dias": 120},
        "datajud": {"nome": "CNJ · DataJud (API Pública)", "trib": "cnj", "url": "https://datajud-wiki.cnj.jus.br/api-publica/", "data_dados": None, "coletado_em": None,
                    "ok": None, "ativo": False, "aviso": "Desligado: o termo de uso (itens 3.3, 3.8, 3.9 e 3.13) e a instabilidade do serviço; a página não usa número do DataJud.",
                    "registros": None, "velho_dias": 15},
    }
    for t in ("stf", "stj"):
        if t not in paineis:
            fontes[f"{t}_acervo"].update(ok=False, data_dados=None, coletado_em=None, registros=None,
                                         aviso=f"A coleta do acervo do {t.upper()} não deu certo nenhuma vez (o portal recusou o acesso).")
    cobertura = {
        t: {"processos": p["universo"]["processos"], "na_grade": p["universo"]["na_grade"], "segredo": p["universo"]["segredo"],
            "area_por": {"principal": int(p["universo"]["processos"] * 0.9), "classe": int(p["universo"]["processos"] * 0.1)},
            "relatores_fora_do_mapa": [], "fora_da_grade": {f["id"]: f["n"] for f in p["fora_da_grade"]}}
        for t, p in paineis.items()
    }
    if "stf" in cobertura:
        cobertura["stf"].update(linhas_de_assunto=39311, casamento={"folha": 39206, "parcial": 96, "raiz": 3, "nada": 6})
    avisos = [f["aviso"] for f in fontes.values() if f.get("aviso") and f.get("ok") is False]
    return {"v": VERSAO, "hoje": HOJE, "atualizado_em": "2026-11-23T09:47:20Z", "proxima_execucao": "2026-11-23T22:40:00Z",
            "fontes": fontes, "cobertura": cobertura, "reguas": REGRAS.reguas_para_meta(), "avisos": avisos, "correcoes": []}


def achados(paineis: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """``achados.json`` (contrato §4): um de cada tipo que a página sabe mostrar."""
    lista = []
    if "stj" in paineis:
        stj = paineis["stj"]
        conv = next((m for m in stj["ministros"] if "heranca" in m["selos"]), stj["ministros"][0])
        marcado = next(((m, a) for m in stj["ministros"] for a, c in m["areas"].items() if c["marca"] == 1), (stj["ministros"][0], "civil"))
        m, a = marcado
        c = m["areas"][a]
        lista += [
            {"tipo": "heranca", "trib": "stj", "peso": 15.03, "selo": "MESA",
             "titulo": f"{conv['nome']}, convocada do TJXX desde 17/11, recebeu 1.014 processos por atribuição em 19/11/2026",
             "texto": "A atribuição é herança de acervo, não sorteio: aparece à parte e não soma no que ela recebeu por distribuição.",
             "alvo": f"#v=ministros&t=stj&min={conv['id']}", "ministro": conv["id"],
             "prova": {"fonte": "stj_atas", "data": "2026-11-19", "url": "https://dadosabertos.web.stj.jus.br/dataset/atas-de-distribuicao"}},
            {"tipo": "fora_do_padrao", "trib": "stj", "peso": 8.79, "selo": "ÁREA",
             "titulo": f"{REGRAS.area(a).nome_curto} é {round(c['p'] * 100)}% do acervo de {m['nome']}, {_virgula(c['razao'])} vez a parte dos colegas da Turma",
             "texto": f"{_n(c['n'])} de {_n(m['processos'])} processos em 20/11/2026. Diferença que passa do acaso (binomial exata, 99%, Holm).",
             "alvo": f"#v=ministros&t=stj&min={m['id']}&area={a}", "ministro": m["id"],
             "prova": {"fonte": "stj_acervo", "data": "2026-11-20", "url": "https://dadosabertos.web.stj.jus.br/dataset/acervo-em-tramitacao"}},
            {"tipo": "litigante", "trib": "stj", "peso": 6.2, "selo": "PARTE",
             "titulo": f"{stj['partes']['top'][0]['nome']} aparece em {_n(stj['partes']['top'][0]['n'])} processos do STJ, em {stj['partes']['top'][0]['mesas']} mesas",
             "texto": "Contado pelo grupo do litigante, em processos distintos; sem os processos em segredo para o nome.",
             "alvo": "#v=partes&t=stj", "ministro": None,
             "prova": {"fonte": "stj_acervo", "data": "2026-11-20", "url": "https://dadosabertos.web.stj.jus.br/dataset/acervo-em-tramitacao"}},
        ]
    if "stf" in paineis:
        stf = paineis["stf"]
        lista.append({"tipo": "mesa_antiga", "trib": "stf", "peso": 7.1, "selo": "FILA",
                      "titulo": f"No STF, {_n(stf['universo']['sem_decisao_365'])} processos estão conclusos ao relator há mais de um ano",
                      "texto": f"De {_n(stf['universo']['na_mesa'])} na mesa dos ministros da grade, em 22/11/2026.",
                      "alvo": "#v=fila&t=stf", "ministro": None,
                      "prova": {"fonte": "stf_acervo", "data": "2026-11-22", "url": "https://www.stf.jus.br/arquivo/cms/publicacaoBOInternet/anexo/acervo/Acervo_STF_Lista_Geral.xlsx"}})
    lista.sort(key=lambda x: (-x["peso"], x["trib"], x["tipo"], x["alvo"]))
    return {"v": VERSAO, "hoje": HOJE, "achados": lista}


def _json(dado: Any) -> str:
    return json.dumps(dado, ensure_ascii=False, separators=(",", ":")) + "\n"


def gravar(data_dir: Path, *, tribunais: tuple[str, ...] = ("stf", "stj"), estado: str = "ok", semente: int = 7) -> Path:
    """Grava ``<data_dir>/tribunais/`` inteiro e devolve a pasta."""
    pasta = Path(data_dir) / "tribunais"
    (pasta / "ministros").mkdir(parents=True, exist_ok=True)
    paineis, dossies = {}, {}
    for t in tribunais:
        paineis[t], d = painel(t, semente=semente)
        dossies.update(d)
    for t, p in paineis.items():
        (pasta / f"{t}.json").write_text(_json(p), encoding="utf-8")
    for mid, d in dossies.items():
        (pasta / "ministros" / f"{mid}.json").write_text(_json(d), encoding="utf-8")
    (pasta / "meta.json").write_text(_json(meta(paineis, estado=estado)), encoding="utf-8")
    (pasta / "achados.json").write_text(_json(achados(paineis)), encoding="utf-8")
    return pasta
