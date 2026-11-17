"""Pessoa ou partido? Quanto o partido explica de cada medida, e o que anda junto.

Para cada medida de cada parlamentar em exercício (as que a página já tem: de que lado vota,
governismo, fidelidade, presença, cota, número de propostas; e as da leitura dos projetos:
parte do topo (só o confirmado por duas leituras), parte da faixa mínima, mediana do peso,
saldo de contas, taxa de exceção e voto fiscal), três números:

- **quanto o partido explica** (``e2``, em %): da variação entre pessoas, a parte que fica
  entre os partidos, já descontado o que qualquer divisão em tantos grupos explicaria
  (:func:`pais_estat.eta2`). Entram as pessoas que alcançam o mínimo da medida, em partidos
  com duas ou mais delas;
- **o intervalo** (``ic``): 95%, reamostrando as pessoas ``cruzar.reamostras`` vezes;
- **o que daria por acaso** (``acaso``): o 95º percentil do mesmo número com os partidos
  embaralhados entre as pessoas, ``cruzar.permutacoes`` vezes.

Os sorteios saem de um gerador semeado com ``cruzar.semente``, a Casa e a medida: com os mesmos
dados, o resultado é o mesmo de um dia para o outro, e uma medida não muda porque outra entrou.

``zona``: ``quase_tudo`` (do corte alto de ``cruzar.zonas`` para cima), ``parte`` ou ``pouco``.
``distingue`` é falso quando o intervalo encosta no acaso. **Potência**: com menos de
``limiares.pessoas_minimo`` pessoas no mínimo da medida, não há número: a zona é
``sem_como_dizer`` ("não dá para responder por pessoa").

**O que anda junto** (:func:`pares`): correlação de postos (Spearman) entre duas medidas, na
Casa e dentro dos partidos; "passa" do acaso a 1%, contando todos os pares (Holm).

O que fica de fora: causa (andar junto não é causa); o bloco (base × oposição); e qualquer
correção pelo erro da leitura: as medidas da leitura herdam o ruído dela, o que **puxa para
baixo** tanto o que o partido explica quanto as correlações. Em "de que lado vota", parte do
número vem da própria construção (os polos são definidos por partidos).
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any

from . import pais_estat as estat
from .regras import ProjetoDePais

ALFA_PARES = 0.01  # "passa do acaso": 99%, contando todos os pares (Holm)
OUTROS = "Outros"
SEM_COMO_DIZER = "sem_como_dizer"
VOTO, PROJETOS, GABINETE = "voto", "projetos", "gabinete"
Linha = Mapping[str, Any]


def _parte(coluna: str, minimo: Callable[[ProjetoDePais], int]) -> Callable[[Linha, ProjetoDePais], float | None]:
    """A parte dos projetos lidos da pessoa que cai em ``coluna`` (%), com o mínimo de lidos."""

    def valor(linha: Linha, regua: ProjetoDePais) -> float | None:
        return 100.0 * linha[coluna] / linha["n"] if linha.get("n", 0) >= max(1, minimo(regua)) else None

    return valor


def _coluna(nome: str, padrao: float | None = None) -> Callable[[Linha, ProjetoDePais], float | None]:
    return lambda linha, regua: linha.get(nome) if linha.get(nome) is not None else padrao


def _voto_fiscal(linha: Linha, regua: ProjetoDePais) -> float | None:
    return 100.0 * linha["va"] / linha["vn"] if linha.get("vn", 0) >= max(1, regua.limiares.voto_minimo) else None


# (id, grupo, de onde sai o valor de cada pessoa; None = a pessoa não alcança o mínimo da medida)
MEDIDAS: tuple[tuple[str, str, Callable[[Linha, ProjetoDePais], float | None]], ...] = (
    ("saldo", VOTO, _coluna("saldo")),  # de que lado vota (quem tem saldo na página)
    ("gov", VOTO, _coluna("gov")),  # governismo
    ("vf", VOTO, _voto_fiscal),  # voto fiscal (limiares.voto_minimo votos ou mais)
    ("fid", VOTO, _coluna("fid")),  # fidelidade ao partido
    ("prop", PROJETOS, _coluna("prop", 0)),  # quantos projetos apresenta (o número que a página já tem)
    ("topo", PROJETOS, _parte("estr", lambda regua: regua.limiares.perfil_minimo)),  # parte do topo: só o confirmado ("estr"), como no funil
    ("sb", PROJETOS, _parte("sb", lambda regua: regua.limiares.perfil_minimo)),  # parte da faixa mínima
    ("tip", PROJETOS, _coluna("tip")),  # mediana do peso
    ("sc", PROJETOS, _coluna("sc")),  # saldo de contas
    ("tx", PROJETOS, _coluna("tx")),  # taxa de exceção
    ("pres", GABINETE, _coluna("pres")),  # presença
    ("ct", GABINETE, _coluna("ct")),  # cota (média por mês)
)
# Os pares de "o que anda junto": o da régua (saldo de contas × voto fiscal) e os da tela.
PARES = (("sc", "vf"), ("topo", "vf"), ("saldo", "topo"), ("saldo", "sb"), ("fid", "topo"), ("pres", "prop"), ("pres", "topo"), ("ct", "prop"), ("ct", "topo"))
VOTO_DA_FRASE, PROJETOS_DA_FRASE = "saldo", "topo"  # as duas medidas da frase-resposta


def valores(linhas: list[Linha], regua: ProjetoDePais) -> dict[str, list[float | None]]:
    """Medida → o valor de cada pessoa, na ordem de ``linhas`` (``None``: não alcança o mínimo)."""
    return {ident: [valor(linha, regua) for linha in linhas] for ident, _grupo, valor in MEDIDAS}


def zona(e2: float, zonas: tuple[float, float]) -> str:
    return "quase_tudo" if e2 >= zonas[1] else "parte" if e2 >= zonas[0] else "pouco"


def medida(ident: str, grupo: str, x: list[float | None], partidos: list[str], regua: ProjetoDePais, casa: str) -> dict[str, Any]:
    """Quanto o partido explica de uma medida: ``{id, grupo, n, k, e2, ic, acaso, zona,
    distingue}`` (``e2``, ``ic`` e ``acaso`` em %). ``n`` pessoas em ``k`` partidos de duas ou
    mais. Sem potência (``n`` abaixo de ``limiares.pessoas_minimo``) ou sem variação: os
    números saem nulos e a zona é ``sem_como_dizer``, com o motivo."""
    pares = [(v, p) for v, p in zip(x, partidos, strict=True) if v is not None and p]
    vals, grupos = [v for v, _p in pares], [p for _v, p in pares]
    e2, n, k = estat.eta2(vals, grupos)
    saida: dict[str, Any] = {"id": ident, "grupo": grupo, "n": n, "k": k, "e2": None, "ic": None, "acaso": None, "zona": SEM_COMO_DIZER, "distingue": False}
    if n < regua.limiares.pessoas_minimo:
        return {**saida, "motivo": "poucas_pessoas"}
    if e2 is None:
        return {**saida, "motivo": "sem_variacao"}
    cfg = regua.cruzar
    ic = estat.eta2_intervalo(vals, grupos, reamostras=cfg.reamostras, gerador=random.Random(f"{cfg.semente}:{casa}:{ident}:reamostra"), confianca=regua.limiares.confianca)
    acaso = estat.eta2_acaso(vals, grupos, permutacoes=cfg.permutacoes, gerador=random.Random(f"{cfg.semente}:{casa}:{ident}:permuta"))
    saida.update(
        e2=round(100.0 * e2, 1),
        ic=[round(100.0 * ic[0], 1), round(100.0 * ic[1], 1)] if ic else None,
        acaso=round(100.0 * acaso, 1) if acaso is not None else None,
        zona=zona(100.0 * e2, cfg.zonas),
        distingue=bool(ic and acaso is not None and ic[0] > acaso),
    )
    return saida


def frase(medidas: list[dict[str, Any]], zonas: tuple[float, float]) -> dict[str, Any]:
    """A frase-resposta, pelos casos do contrato. V = quanto o partido explica do lado em que
    cada um vota; P = da parte do topo no que cada um propõe.

    - ``sem_como_dizer``: V não existe (faltam pessoas) ou não se distingue do acaso;
    - ``nem_o_voto``: V abaixo do corte alto;
    - ``voto_sim_projetos_nao`` / ``voto_sim_projetos_parte`` / ``os_dois``: V no corte alto ou
      acima, e P abaixo do corte baixo / entre os dois / no alto ou acima.

    ``projetos_distingue`` falso (P não existe ou não se distingue do acaso): a segunda metade
    da frase tem de dizer que, no que cada um propõe, não dá para separar o partido do acaso,
    qualquer que seja o caso."""
    por_id = {m["id"]: m for m in medidas}
    v, p = por_id.get(VOTO_DA_FRASE) or {}, por_id.get(PROJETOS_DA_FRASE) or {}
    saida = {"voto": v.get("e2"), "projetos": p.get("e2"), "voto_distingue": bool(v.get("distingue")), "projetos_distingue": bool(p.get("distingue")), "n": v.get("n", 0), "k": v.get("k", 0)}
    if v.get("e2") is None or not v.get("distingue"):
        caso = SEM_COMO_DIZER
    elif v["e2"] < zonas[1]:
        caso = "nem_o_voto"
    elif p.get("e2") is None or p["e2"] < zonas[0]:
        caso = "voto_sim_projetos_nao"
    elif p["e2"] < zonas[1]:
        caso = "voto_sim_projetos_parte"
    else:
        caso = "os_dois"
    return {"caso": caso, **saida}


def pares(x: Mapping[str, list[float | None]], partidos: list[str], regua: ProjetoDePais, lista: tuple[tuple[str, str], ...] = PARES) -> list[dict[str, Any]]:
    """O que anda junto: para cada par de medidas, a correlação de postos entre as pessoas que
    alcançam o mínimo das duas (``rho``), a mesma conta dentro dos partidos (``dentro``), o
    ``n``, a chance sob o acaso (``p``) e ``passa`` (a 1%, Holm sobre os pares calculados). Com
    menos de ``limiares.pessoas_minimo`` pessoas, ou sem variação numa das medidas, o par sai
    sem número (``sem`` = 1)."""
    saida = []
    chances: dict[int, float] = {}
    for a, b in lista:
        juntos = [(va, vb, p) for va, vb, p in zip(x[a], x[b], partidos, strict=True) if va is not None and vb is not None]
        n = len(juntos)
        rho = estat.spearman([t[0] for t in juntos], [t[1] for t in juntos]) if n >= regua.limiares.pessoas_minimo else None
        if rho is None:
            saida.append({"a": a, "b": b, "n": n, "rho": None, "dentro": None, "p": None, "passa": False, "sem": 1})
            continue
        dentro = estat.spearman_dentro([t[0] for t in juntos], [t[1] for t in juntos], [t[2] for t in juntos])
        chances[len(saida)] = estat.p_da_correlacao(rho, n)
        saida.append({"a": a, "b": b, "n": n, "rho": round(rho, 2), "dentro": round(dentro, 2) if dentro is not None else None, "p": float(f"{chances[len(saida)]:.2g}"), "passa": False})
    for posicao in estat.holm(chances, ALFA_PARES):
        saida[posicao]["passa"] = True
    return saida


def por_partido(linhas: list[Linha], x: Mapping[str, list[float | None]], regua: ProjetoDePais) -> list[dict[str, Any]]:
    """A mediana de cada medida em cada partido com ``limiares.partido_minimo`` em exercício ou
    mais (os demais, juntos, por último). A mediana só sai com ``partido_minimo`` valores."""
    minimo = regua.limiares.partido_minimo
    membros: dict[str, list[int]] = defaultdict(list)
    for i, linha in enumerate(linhas):
        membros[linha["pt"]].append(i)
    grandes = sorted(sigla for sigla, lista in membros.items() if sigla and len(lista) >= minimo)
    outros = [i for sigla, lista in membros.items() if sigla not in grandes for i in lista]
    saida = []
    for sigla, indices in [*((s, membros[s]) for s in grandes), (OUTROS, outros)]:
        if not indices:
            continue
        medianas = {}
        for ident, serie in x.items():
            achados = [serie[i] for i in indices if serie[i] is not None]
            mediana = estat.mediana(achados) if len(achados) >= minimo else None
            medianas[ident] = round(mediana, 1) if mediana is not None else None
        saida.append({"pt": sigla, "n": len(indices), "med": medianas})
    return saida


def calcular(linhas: list[Linha], regua: ProjetoDePais, casa: str) -> dict[str, Any]:
    """O bloco "cruzar" do lp. ``linhas``: uma por parlamentar em exercício, com as colunas de
    ``autores-<casa>.json``."""
    x = valores(linhas, regua)
    partidos = [linha["pt"] for linha in linhas]
    medidas = [medida(ident, grupo, x[ident], partidos, regua, casa) for ident, grupo, _valor in MEDIDAS]
    tamanhos: dict[str, int] = defaultdict(int)
    for sigla in partidos:
        if sigla:
            tamanhos[sigla] += 1
    cfg = regua.cruzar
    return {
        "pessoas": len(linhas),
        "partidos": sum(1 for n in tamanhos.values() if n >= 2),
        "parametros": {"zonas": list(cfg.zonas), "permutacoes": cfg.permutacoes, "reamostras": cfg.reamostras, "semente": cfg.semente, "confianca": regua.limiares.confianca, "pessoas_minimo": regua.limiares.pessoas_minimo, "alfa_pares": ALFA_PARES},
        "medidas": medidas,
        "frase": frase(medidas, cfg.zonas),
        "pares": pares(x, partidos, regua),
        "por_partido": por_partido(linhas, x, regua),
    }
