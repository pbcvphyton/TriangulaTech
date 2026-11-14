"""Frentes parlamentares: quem assina cada uma e como vota no tema dela.

A Câmara publica, em arquivos em lote, as frentes parlamentares registradas
(``frentes.csv``: título, data de criação, legislatura, requerimento de registro) e
quem assina cada uma (``frentesDeputados.csv``: um deputado por linha, com o
coordenador marcado). O Senado não publica as frentes; das mistas, só se sabe quem
assinou pela Câmara.

Assinar é barato: na legislatura 57, um deputado em exercício assina, na mediana, mais
de cem frentes. Por isso a assinatura não é lida como posição. O que se compara é o
**voto**: o de cada membro com o do **coordenador** (quem registra a frente), nas
votações em que a causa se vota, que são as de conteúdo, abertas, de Plenário e
divisivas (esquerda × direita, a régua de :mod:`.perfil`) do tema da frente
(``config/congresso.yaml``, ``frentes``). Uma frente de tema ``todas`` conta todas as
votações divisivas.

- **com o coordenador**: nas votações em que os dois tomaram posição (Sim; Não ou
  obstrução = Não), em quantas ficaram do mesmo lado;
- **lados**: os membros em exercício pelo saldo no tema (ou o geral, nas frentes de
  ``todas``), cortados nas faixas do campo: até o começo do "dividido", com a esquerda;
  acima do fim dele, com a direita; no meio, dividido.

A frente não toma posição em votação nenhuma: "com o coordenador" não quer dizer "com a
frente". E a lista não tem data de adesão (``dataInicio`` e ``dataFim`` vêm vazios):
quem assinou em 2025 conta também nas votações de 2023.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .. import text
from . import base, fontes
from .base import AnoVotos
from .fontes import Coletor
from .perfil import CONTEUDO, ler_votacao
from .peso import mediana
from .regras import FrenteDeCausa, Regras

log = logging.getLogger(__name__)

COORDENADOR = 14  # deputado_.codTitulo do coordenador ("Coordenador"; nas linhas sem id, "Coordernador")
MEMBRO = 1004  # deputado_.codTitulo de quem só assina
TODAS = "todas"  # tema de uma frente sem tema só: contam todas as votações divisivas
CORTE_PADRAO = (-20.0, 20.0)  # o "dividido" das faixas padrão do campo
MAIS_ASSINAM = 15  # quantos aparecem na lista de quem mais assina
CONTRA = 10  # quantos aparecem na lista de quem menos vota com o coordenador
MINIMO_VOTACOES = 5  # votações com o coordenador para alguém ter a porcentagem e entrar nessa lista

_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Frente:
    """Uma frente parlamentar registrada numa legislatura, com quem a assina."""

    id: int
    titulo: str
    legislatura: int
    criada: str  # AAAA-MM-DD ou ""
    coordenador: int | None  # o deputado marcado como coordenador
    membros: frozenset[int]  # todos os que assinam, coordenador incluído
    documento: str  # o requerimento de registro (PDF na Câmara)


# ── coleta ───────────────────────────────────────────────────────────────────


def _data(valor: str | None) -> str:
    bruto = (valor or "").strip()[:10]
    return bruto if _DATA.fullmatch(bruto) else ""


def coletar(
    coletor: Coletor, legislatura: int, *, validade_h: float = 20.0, validade_membros_h: float = 168.0
) -> dict[int, Frente]:
    """As frentes registradas na ``legislatura``, com os membros (id → :class:`Frente`).

    O coordenador é o deputado marcado como tal na lista de membros (o primeiro, se a
    lista marcar mais de um); quando a marca vem sem o id do deputado (há linhas
    "Coordernador" só com o nome), a frente fica sem coordenador: o coordenador_id do
    cadastro não é confiável. As linhas sem id de deputado ficam de fora. ``validade_membros_h``: a lista de membros tem
    perto de 100 MB e muda pouco; é baixada de novo uma vez por semana."""
    cadastro: dict[int, dict[str, str]] = {}
    for r in fontes.csv_linhas(fontes.camara_arquivo(coletor, "frentes", validade_h=validade_h)):
        fid = fontes.inteiro(r.get("id"))
        if fid is not None and fontes.inteiro(r.get("idLegislatura")) == legislatura:
            cadastro[fid] = r

    membros: dict[int, set[int]] = defaultdict(set)
    coordenadores: dict[int, int] = {}
    linhas = 0
    for r in fontes.csv_linhas(fontes.camara_arquivo(coletor, "frentesDeputados", validade_h=validade_membros_h)):
        fid = fontes.inteiro(r.get("id"))
        pid = fontes.inteiro(r.get("deputado_.id"))
        if fid not in cadastro or pid is None:
            continue
        leg = fontes.inteiro(r.get("deputado_.idLegislatura"))
        if leg is not None and leg != legislatura:
            continue
        linhas += 1
        membros[fid].add(pid)
        if fontes.inteiro(r.get("deputado_.codTitulo")) == COORDENADOR:
            coordenadores.setdefault(fid, pid)

    frentes: dict[int, Frente] = {}
    for fid in sorted(cadastro):
        r = cadastro[fid]
        # Só vale o coordenador marcado na lista de membros: o coordenador_id do cadastro não é
        # confiável (na PROSUL, 54531, aponta para um simples membro com o nome de outro).
        coordenador = coordenadores.get(fid)
        assinam = membros.get(fid, set())
        frentes[fid] = Frente(
            id=fid,
            titulo=re.sub(r"\s+", " ", r.get("titulo") or "").strip(),
            legislatura=legislatura,
            criada=_data(r.get("dataCriacao")),
            coordenador=coordenador,
            membros=frozenset(assinam),
            documento=(r.get("urlDocumento") or "").strip().rstrip('"').strip(),  # vem com uma aspa sobrando no fim
        )
    log.info("Frentes: %d na legislatura %d, %d assinaturas", len(frentes), legislatura, linhas)
    return frentes


# ── as frentes de causa ──────────────────────────────────────────────────────


def de_causa(frentes: Mapping[int, Frente], regras: Regras) -> list[tuple[FrenteDeCausa, Frente]]:
    """As frentes de causa do config (``regras.frentes``), na ordem dele, cada uma com o
    registro cujo título começa pelo título dado (sem acento, minúsculo). Se mais de um
    registro casar, vale o de mais membros (no empate, o de id menor). A entrada sem
    registro que case fica de fora, e vai para o log. ``frentes``: as de uma legislatura."""
    titulos = sorted(((text.normalize(f.titulo), f) for f in frentes.values()), key=lambda par: (par[0], par[1].id))
    pares: list[tuple[FrenteDeCausa, Frente]] = []
    sem: list[str] = []
    for causa in regras.frentes:
        candidatas = [f for titulo, f in titulos if causa.titulo and titulo.startswith(causa.titulo)]
        if not candidatas:
            sem.append(causa.nome)
            continue
        escolhida = min(candidatas, key=lambda f: (-len(f.membros), f.id))
        if len(candidatas) > 1:
            log.info(
                "Frentes: %s casa com %d registros; vale %d (%s)",
                causa.nome,
                len(candidatas),
                escolhida.id,
                ", ".join(f"{f.id}: {len(f.membros)} membros" for f in sorted(candidatas, key=lambda f: f.id)),
            )
        pares.append((causa, escolhida))
    if sem:
        log.warning("Frentes: sem registro que case com %s", ", ".join(sem))
    return pares


# ── lados ────────────────────────────────────────────────────────────────────


def corte(regras: Regras) -> tuple[float, float]:
    """Os limites do "dividido": do ``ate`` da faixa que vem antes da de id ``centro`` até
    o ``ate`` dela (nas faixas padrão, −20 e +20). Sem essa faixa, :data:`CORTE_PADRAO`."""
    campos = sorted(regras.campos, key=lambda c: c.ate)
    for i, campo in enumerate(campos):
        if campo.id == "centro" and i > 0:
            return (float(campos[i - 1].ate), float(campo.ate))
    return CORTE_PADRAO


def lado(saldo: float, limites: tuple[float, float]) -> str:
    """``e`` (com a esquerda: até o começo do "dividido"), ``d`` (com a direita: acima do
    fim dele) ou ``c`` (dividido), como nas faixas de :meth:`Regras.campo`."""
    if saldo <= limites[0]:
        return "e"
    if saldo > limites[1]:
        return "d"
    return "c"


# ── as contas ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Divisiva:
    """Uma votação divisiva da legislatura: os temas e a posição (S/N) de quem tomou uma."""

    temas: frozenset[str]
    posicoes: dict[int, str]


def _divisivas(anos: Iterable[AnoVotos], regras: Regras, legislatura: int) -> list[_Divisiva]:
    """As votações de conteúdo, abertas, de Plenário e divisivas da ``legislatura``, lidas uma vez só."""
    saida = []
    for ano in anos:
        for k, v in enumerate(ano.votacoes):
            if not v.plenario or v.secreta or v.tipo not in CONTEUDO:
                continue
            leg = regras.legislatura_em(v.data)
            if leg is None or leg.numero != legislatura:
                continue
            if not ler_votacao(v, [p.partido_em(k) for p in ano.pessoas], regras).divisiva:
                continue
            posicoes = {}
            for pessoa, voto in zip(ano.pessoas, v.votos, strict=False):
                posicao = base.POSICAO.get(voto)
                if posicao:
                    posicoes[pessoa.id] = posicao
            saida.append(_Divisiva(frozenset(v.temas), posicoes))
    return saida


def _pct(k: int, n: int) -> float | None:
    return round(100.0 * k / n, 1) if n else None


def _numero(valor: float | None) -> float | int | None:
    """Um número para o JSON: inteiro quando é inteiro, senão com uma casa."""
    if valor is None:
        return None
    return int(valor) if float(valor).is_integer() else round(float(valor), 1)


def analisar(
    frentes: Mapping[int, Frente],
    causas: list[tuple[FrenteDeCausa, Frente]],
    anos: Iterable[AnoVotos],
    regras: Regras,
    legislatura: int,
    *,
    pessoas: Mapping[int, Mapping[str, Any]],
    saldos_tema: Mapping[int, Mapping[str, tuple[float, int]]],
    saldo_geral: Mapping[int, tuple[float, int]],
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """O painel das frentes da ``legislatura`` e, por deputado, as frentes que ele assina.

    ``anos``: a base de votações da Câmara; ``pessoas``: id → ``{"k", "nome", "partido"
    (o atual), "uf", "ex" (em exercício hoje)}``; ``saldos_tema``: id → tema → (saldo,
    votações esquerda × direita no tema), só de quem tem :data:`MINIMO_VOTACOES` ou mais
    no tema; ``saldo_geral``: id → (saldo, votações divisivas), só de quem tem lado.

    O painel traz quantas frentes há, quantas um deputado em exercício assina (mediana e
    média), quantos não assinam nenhuma, quem mais assina e, para cada frente de causa
    (``causas``, de :func:`de_causa`), o coordenador, os membros por partido e por lado, e
    quanto eles votam com o coordenador nas votações do tema."""
    limites = corte(regras)
    da_legislatura = sorted((f for f in frentes.values() if f.legislatura == legislatura), key=lambda f: f.id)
    total = len(da_legislatura)

    assinadas: Counter[int] = Counter()
    coordena: dict[int, list[Frente]] = defaultdict(list)
    for f in da_legislatura:
        assinadas.update(f.membros)
        if f.coordenador is not None:
            coordena[f.coordenador].append(f)

    def info(pid: int) -> Mapping[str, Any]:
        return pessoas.get(pid) or {}

    def chave_nome(pid: int) -> tuple[str, int]:
        return (text.normalize(str(info(pid).get("nome") or "")), pid)

    def quem(pid: int) -> dict[str, Any]:
        p = info(pid)
        return {"k": p.get("k") or f"cd-{pid}", "nome": p.get("nome") or "", "partido": p.get("partido") or "", "uf": p.get("uf") or ""}

    def saldo(pid: int, tema: str) -> tuple[float, int] | None:
        return saldo_geral.get(pid) if tema == TODAS else (saldos_tema.get(pid) or {}).get(tema)

    em_exercicio = sorted((pid for pid, p in pessoas.items() if p.get("ex")), key=chave_nome)
    exercendo = set(em_exercicio)
    contagens = [assinadas.get(pid, 0) for pid in em_exercicio]
    meio = mediana([float(n) for n in contagens])
    painel: dict[str, Any] = {
        "legislatura": legislatura,
        "total": total,
        "mediana": _numero(meio),
        "media": round(sum(contagens) / len(contagens), 1) if contagens else None,
        "nenhuma": sum(1 for n in contagens if n == 0),
        "mais_assinam": [
            {**quem(pid), "n": assinadas[pid]}
            for pid in sorted((pid for pid in em_exercicio if assinadas.get(pid)), key=lambda pid: (-assinadas[pid], chave_nome(pid)))[:MAIS_ASSINAM]
        ],
        "causas": [],
    }
    bancadas = Counter(str(pessoas[pid].get("partido") or "") for pid in em_exercicio)

    por_pessoa_causas: dict[int, list[dict[str, Any]]] = defaultdict(list)
    divisivas = _divisivas(anos, regras, legislatura)
    for causa, frente in causas:
        votacoes = divisivas if causa.tema == TODAS else [d for d in divisivas if causa.tema in d.temas]
        coord = frente.coordenador
        pares: dict[int, list[int]] = {}  # membro → [votações do mesmo lado do coordenador, votações]
        com_posicao = 0  # votações em que o coordenador tomou posição
        if coord is not None:
            outros = sorted(frente.membros - {coord})
            for d in votacoes:
                do_coord = d.posicoes.get(coord)
                if do_coord is None:
                    continue
                com_posicao += 1
                for m in outros:
                    do_membro = d.posicoes.get(m)
                    if do_membro is None:
                        continue
                    par = pares.setdefault(m, [0, 0])
                    par[0] += do_membro == do_coord
                    par[1] += 1

        membros_ex = sorted((m for m in frente.membros if m in exercendo), key=chave_nome)
        lados = {"e": 0, "c": 0, "d": 0, "sem": 0}
        for m in membros_ex:
            s = saldo(m, causa.tema)
            lados["sem" if s is None else lado(s[0], limites)] += 1
        por_partido = Counter(str(pessoas[m].get("partido") or "") for m in membros_ex)
        soma_k = sum(pares[m][0] for m in membros_ex if m in pares)
        soma_n = sum(pares[m][1] for m in membros_ex if m in pares)
        contra = sorted(
            (m for m in membros_ex if m in pares and pares[m][1] >= MINIMO_VOTACOES),
            key=lambda m: (pares[m][0] / pares[m][1], -pares[m][1], chave_nome(m)),
        )[:CONTRA]

        coordenador: dict[str, Any] | None = None
        if coord is not None:
            s = saldo(coord, causa.tema)
            coordenador = {
                **quem(coord),
                "ex": 1 if coord in exercendo else 0,
                "saldo": _numero(s[0]) if s else None,
                "n": s[1] if s else com_posicao,
            }
        painel["causas"].append(
            {
                "id": frente.id,
                "nome": causa.nome,
                "titulo": frente.titulo,
                "tema": causa.tema,
                "criada": frente.criada,
                "doc": frente.documento,
                "membros": len(frente.membros),
                "em_exercicio": len(membros_ex),
                "coord": coordenador,
                "partidos": [
                    [sigla, n, bancadas.get(sigla, 0)] for sigla, n in sorted(por_partido.items(), key=lambda kv: (-kv[1], kv[0]))
                ],
                "lados": lados,
                "votacoes": com_posicao,
                "com_coord": _pct(soma_k, soma_n),
                "contra": [
                    {**quem(m), "pct": _pct(*pares[m]), "k_n": pares[m][0], "n": pares[m][1]} for m in contra
                ],
            }
        )
        for m in sorted(frente.membros):
            if m not in pessoas:
                continue
            k, n = (0, com_posicao) if m == coord else pares.get(m, [0, 0])
            por_pessoa_causas[m].append(
                {
                    "id": frente.id,
                    "nome": causa.nome,
                    "tema": causa.tema,
                    "coord": 1 if m == coord else 0,
                    "com": _pct(k, n) if m != coord and n >= MINIMO_VOTACOES else None,
                    "n": n,
                }
            )

    por_pessoa: dict[int, dict[str, Any]] = {}
    for pid in sorted(pessoas):
        n = assinadas.get(pid, 0)
        if not n:
            por_pessoa[pid] = {"n": 0, "de": total}
            continue
        por_pessoa[pid] = {
            "n": n,
            "de": total,
            "coordena": [
                {"id": f.id, "titulo": f.titulo} for f in sorted(coordena.get(pid, []), key=lambda f: (text.normalize(f.titulo), f.id))
            ],
            "causas": por_pessoa_causas.get(pid, []),
        }
    return painel, por_pessoa
