"""As escolhas editoriais do analítico do Congresso (``config/congresso.yaml``):
legislaturas, governos, polos, limiares, faixas do campo e macrotemas.

Nada disto é dado oficial: são as réguas com que os registros oficiais são lidos.
Por isso ficam num arquivo só, à vista, e vão escritas na seção "Como ler" da aba.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .. import text
from . import base

ARQUIVO = "congresso.yaml"
SEM_FIM = "9999-12-31"


@dataclass(frozen=True)
class Legislatura:
    numero: int
    inicio: str
    fim: str

    @property
    def rotulo(self) -> str:
        return f"{self.inicio[:4]}–{int(self.fim[:4]) - (1 if self.fim[5:] < '02-01' else 0)}"


@dataclass(frozen=True)
class Governo:
    nome: str
    inicio: str
    fim: str = SEM_FIM


@dataclass(frozen=True)
class Polos:
    inicio: str
    fim: str
    esquerda: frozenset[str]
    direita: frozenset[str]


@dataclass(frozen=True)
class Limiares:
    coesao_polo: float = 0.75
    minimo_polo: int = 3
    minimo_divisivas: int = 10
    coesao_partido: float = 0.8
    minimo_partido: int = 4
    minimo_presenca: int = 20
    dias_parado: int = 365
    dias_relatoria: int = 180


@dataclass(frozen=True)
class Campo:
    id: str
    nome: str
    ate: float  # saldo máximo da faixa (−100 a +100)


@dataclass(frozen=True)
class Tema:
    id: str
    nome: str
    camara: frozenset[int] = frozenset()
    senado: tuple[str, ...] = ()  # prefixos de "Raiz / Filho", normalizados


CAMPOS_PADRAO = (
    Campo("esquerda", "com a esquerda", -60),
    Campo("centro_esquerda", "mais com a esquerda", -20),
    Campo("centro", "dividido", 20),
    Campo("centro_direita", "mais com a direita", 60),
    Campo("direita", "com a direita", 100),
)


@dataclass
class Regras:
    legislaturas: list[Legislatura] = field(default_factory=list)
    governos: list[Governo] = field(default_factory=list)
    polos: list[Polos] = field(default_factory=list)
    limiares: Limiares = field(default_factory=Limiares)
    campos: tuple[Campo, ...] = CAMPOS_PADRAO
    temas: list[Tema] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._tema_camara = {cod: t.id for t in self.temas for cod in t.camara}
        prefixos = [(prefixo, t.id) for t in self.temas for prefixo in t.senado]
        self._tema_senado = sorted(prefixos, key=lambda par: len(par[0]), reverse=True)
        self._nomes = {t.id: t.nome for t in self.temas}

    # ── períodos ─────────────────────────────────────────────────────────────

    def legislatura_em(self, data: str) -> Legislatura | None:
        return next((leg for leg in self.legislaturas if leg.inicio <= data <= leg.fim), None)

    def legislatura_atual(self, hoje: str) -> Legislatura | None:
        atual = self.legislatura_em(hoje)
        if atual is None and self.legislaturas:
            atual = max((leg for leg in self.legislaturas if leg.inicio <= hoje), key=lambda leg: leg.inicio, default=None)
        return atual

    def governo_em(self, data: str) -> Governo | None:
        return next((g for g in self.governos if g.inicio <= data <= g.fim), None)

    def polos_em(self, data: str) -> Polos | None:
        return next((p for p in self.polos if p.inicio <= data <= p.fim), None)

    def anos(self) -> list[int]:
        """Os anos cobertos pelas legislaturas configuradas."""
        if not self.legislaturas:
            return []
        primeiro = min(int(leg.inicio[:4]) for leg in self.legislaturas)
        ultimo = max(int(leg.fim[:4]) - (1 if leg.fim[5:] < "02-01" else 0) for leg in self.legislaturas)
        return list(range(primeiro, ultimo + 1))

    # ── campo e temas ────────────────────────────────────────────────────────

    def campo(self, saldo: float) -> Campo:
        """A faixa do saldo (−100 = sempre com a esquerda; +100 = sempre com a direita)."""
        for faixa in self.campos:
            if saldo <= faixa.ate:
                return faixa
        return self.campos[-1]

    def tema_camara(self, codigo: int | None) -> str | None:
        return self._tema_camara.get(codigo) if codigo is not None else None

    def tema_senado(self, hierarquia: str | None) -> str | None:
        chave = text.normalize(hierarquia or "")
        if not chave:
            return None
        return next((tema for prefixo, tema in self._tema_senado if chave.startswith(prefixo)), None)

    def nome_do_tema(self, tema: str) -> str:
        return self._nomes.get(tema, tema)


def _partidos(valores: Any) -> frozenset[str]:
    return frozenset(base.partido(str(v)) for v in (valores or []) if str(v).strip())


def de_dict(bruto: dict[str, Any]) -> Regras:
    limiares = Limiares(**{k: v for k, v in (bruto.get("limiares") or {}).items() if k in Limiares.__dataclass_fields__})
    campos = tuple(
        Campo(str(c["id"]), str(c.get("nome") or c["id"]), float(c["ate"])) for c in bruto.get("campos") or []
    ) or CAMPOS_PADRAO
    return Regras(
        legislaturas=sorted(
            (Legislatura(int(leg["numero"]), str(leg["inicio"]), str(leg["fim"])) for leg in bruto.get("legislaturas") or []),
            key=lambda leg: leg.inicio,
        ),
        governos=sorted(
            (Governo(str(g["nome"]), str(g["inicio"]), str(g.get("fim") or SEM_FIM)) for g in bruto.get("governos") or []),
            key=lambda g: g.inicio,
        ),
        polos=sorted(
            (
                Polos(str(p["inicio"]), str(p.get("fim") or SEM_FIM), _partidos(p.get("esquerda")), _partidos(p.get("direita")))
                for p in bruto.get("polos") or []
            ),
            key=lambda p: p.inicio,
        ),
        limiares=limiares,
        campos=tuple(sorted(campos, key=lambda c: c.ate)),
        temas=[
            Tema(
                id=str(t["id"]),
                nome=str(t.get("nome") or t["id"]),
                camara=frozenset(int(c) for c in t.get("camara") or []),
                senado=tuple(text.normalize(str(s)) for s in t.get("senado") or []),
            )
            for t in bruto.get("temas") or []
        ],
    )


def carregar(root: Path) -> Regras:
    """Lê ``<root>/config/congresso.yaml``."""
    bruto = yaml.safe_load((Path(root) / "config" / ARQUIVO).read_text(encoding="utf-8")) or {}
    return de_dict(bruto)
