"""O saldo pesado pela relevância da matéria.

O saldo de :mod:`.perfil` conta cada votação divisiva igual: um destaque de uma matéria
de homenagem pesa o mesmo que o texto principal de uma PEC. Aqui, a mesma conta com
cada voto pesando a **nota de relevância** da matéria votada (0 a 100, a de
:mod:`.relevancia`, que a ficha da matéria mostra sinal a sinal):

    saldo pesado = 100 × (Σ nota dos votos com a direita − Σ nota dos votos com a esquerda)
                         ÷ Σ nota de todos esses votos

Só entram as votações cuja matéria tem nota (a régua mede PL, PLP, PEC, PLS e PLC;
medida provisória, decreto legislativo e requerimento ficam de fora). Ao lado, o mesmo
saldo sem peso em duas metades: as votações de nota igual ou acima da mediana da Casa e
as de nota abaixo dela (a mediana, não as faixas fixas de "sinais fortes" e "fracos":
na Câmara, quase toda matéria votada tem nota baixa, e a faixa de sinais fortes ficaria
quase vazia). É o que mostra quem vota de um lado nas matérias pequenas e do outro nas
grandes.

Não muda o saldo de ninguém: é um segundo número, ao lado do primeiro.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import base, materias
from .base import AnoVotos
from .perfil import CONTEUDO, Eixo, ler_votacao
from .regras import Regras

FAIXAS = ("alta", "baixa")
DIFERENCA = 20  # pontos entre o saldo pesado e o simples para alguém entrar na lista "muda quando pesa"
MINIMO_FAIXA = 5  # votações numa metade (nota alta ou baixa) para o saldo dela aparecer


@dataclass
class Pesado:
    """Os votos de uma pessoa nas votações divisivas de conteúdo da legislatura."""

    todas: int = 0  # votações divisivas em que tomou lado (com ou sem nota)
    eixo: Eixo = field(default_factory=Eixo)  # só as que têm nota, sem peso
    peso_dir: float = 0.0
    peso_esq: float = 0.0
    faixas: dict[str, Eixo] = field(default_factory=lambda: {f: Eixo() for f in FAIXAS})

    @property
    def saldo(self) -> float | None:
        total = self.peso_dir + self.peso_esq
        return 100.0 * (self.peso_dir - self.peso_esq) / total if total else None


def mediana(valores: list[float]) -> float | None:
    if not valores:
        return None
    ordem = sorted(valores)
    meio = len(ordem) // 2
    return ordem[meio] if len(ordem) % 2 else (ordem[meio - 1] + ordem[meio]) / 2


def calcular(
    anos: Iterable[AnoVotos],
    regras: Regras,
    legislatura: int,
    notas: Mapping[str, float],
) -> tuple[dict[int, Pesado], dict[str, Any]]:
    """Pessoa → :class:`Pesado`, nas votações de conteúdo divisivas, abertas e de Plenário
    da ``legislatura``, e a cobertura: quantas são, quantas têm nota, a mediana da nota (o
    corte entre a metade "alta" e a "baixa") e quantas ficam em cada metade.
    ``notas``: matéria ("PEC 3/2021") → nota de relevância."""
    divisivas: list[tuple[AnoVotos, Any, str, float | None]] = []
    for ano in anos:
        for k, v in enumerate(ano.votacoes):
            if not v.plenario or v.secreta or v.tipo not in CONTEUDO:
                continue
            leg = regras.legislatura_em(v.data)
            if leg is None or leg.numero != legislatura:
                continue
            leitura = ler_votacao(v, [p.partido_em(k) for p in ano.pessoas], regras)
            if leitura.divisiva:
                # A nota é a da matéria: o Senado anota a fase ao lado do número ("PEC 66/2023 (fase 2)").
                divisivas.append((ano, v, leitura.direita, notas.get(materias.titulo_da_materia(v.prop)) if v.prop else None))
    com_nota = [nota for _a, _v, _d, nota in divisivas if nota is not None]
    corte = mediana(com_nota)
    cobertura: dict[str, Any] = {"divisivas": len(divisivas), "com_nota": len(com_nota)}
    if corte is not None:
        cobertura["corte"] = round(corte, 1)
        cobertura["alta"] = sum(1 for nota in com_nota if nota >= corte)
        cobertura["baixa"] = len(com_nota) - cobertura["alta"]
    pessoas: dict[int, Pesado] = {}
    for ano, v, direita_lado, nota in divisivas:
        for pessoa, voto in zip(ano.pessoas, v.votos, strict=False):
            lado = base.POSICAO.get(voto)
            if not lado:
                continue
            p = pessoas.setdefault(pessoa.id, Pesado())
            p.todas += 1
            if nota is None or corte is None:
                continue
            fx = p.faixas["alta" if nota >= corte else "baixa"]
            if lado == direita_lado:
                p.eixo.dir += 1
                fx.dir += 1
                p.peso_dir += nota
            else:
                p.eixo.esq += 1
                fx.esq += 1
                p.peso_esq += nota
    return pessoas, cobertura


def notas_das_materias(materias: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    """Matéria → nota, das fichas das matérias (``materias-<casa>.json``)."""
    return {titulo: float(m["rel"]["n"]) for titulo, m in materias.items() if isinstance(m.get("rel"), Mapping) and m["rel"].get("n") is not None}


def _eixo(e: Eixo) -> dict[str, Any]:
    d: dict[str, Any] = {"esq": e.esq, "dir": e.dir, "n": e.n}
    if e.saldo is not None:
        d["saldo"] = round(e.saldo)
    return d


def resumo(p: Pesado, minimo: int) -> dict[str, Any] | None:
    """O que vai para a ficha: o saldo pesado (com ``minimo`` votações com nota ou mais),
    quantas votações têm nota e o saldo sem peso de cada metade (com :data:`MINIMO_FAIXA`
    votações ou mais nela)."""
    if not p.todas:
        return None
    d: dict[str, Any] = {"n": p.eixo.n, "de": p.todas}
    if p.eixo.n >= minimo and p.saldo is not None:
        d["saldo"] = round(p.saldo)
        d["simples"] = round(p.eixo.saldo)  # o saldo sem peso nas mesmas votações (as que têm nota)
    faixas = {f: _eixo(e) for f, e in p.faixas.items() if e.n >= MINIMO_FAIXA}
    if faixas:
        d["faixas"] = faixas
    return d


def muda_quando_pesa(fichas: Iterable[Mapping[str, Any]], diferenca: int = DIFERENCA) -> list[dict[str, Any]]:
    """Quem está em exercício e tem o saldo pesado a ``diferenca`` pontos ou mais do saldo
    sem peso nas mesmas votações (as que têm nota), da maior diferença para a menor: a
    diferença é só o efeito do peso."""
    saida = []
    for f in fichas:
        peso = f.get("peso") or {}
        if not f.get("exercicio") or peso.get("saldo") is None or peso.get("simples") is None:
            continue
        dif = peso["saldo"] - peso["simples"]
        if abs(dif) >= diferenca:
            faixas = peso.get("faixas") or {}
            saida.append(
                {
                    "k": f["k"],
                    "nome": f["nome"],
                    "partido": f.get("partido") or "",
                    "uf": f.get("uf") or "",
                    "simples": peso["simples"],
                    "pesado": peso["saldo"],
                    "dif": dif,
                    "alta": (faixas.get("alta") or {}).get("saldo"),
                    "alta_n": (faixas.get("alta") or {}).get("n") or 0,
                    "baixa": (faixas.get("baixa") or {}).get("saldo"),
                    "baixa_n": (faixas.get("baixa") or {}).get("n") or 0,
                }
            )
    saida.sort(key=lambda i: (-abs(i["dif"]), i["nome"]))
    return saida
