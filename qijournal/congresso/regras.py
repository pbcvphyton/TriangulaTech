"""As escolhas editoriais do analítico do Congresso (``config/congresso.yaml``):
legislaturas, governos, polos, limiares, faixas do campo e macrotemas.

Nada disto é dado oficial: são as réguas com que os registros oficiais são lidos.
Por isso ficam num arquivo só, à vista, e vão escritas na seção "Como ler" da aba.
"""

from __future__ import annotations

import re
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
    senado_nomes: tuple[str, ...] = ()  # os mesmos, como estão escritos (para mostrar a definição do tema)


@dataclass(frozen=True)
class Relevancia:
    """O índice de relevância das matérias (0 a 100): sinais que dá para medir nos
    registros oficiais. Não diz se a matéria é boa; diz quantos sinais de relevância
    pública ela tem. Pesos e critérios ficam em ``config/congresso.yaml``."""

    pesos: tuple[tuple[str, float], ...] = (("demanda", 40.0), ("origem", 25.0), ("alcance", 20.0), ("apoio", 15.0))
    janela_demanda_anos: int = 0  # só os projetos parecidos apresentados nos N anos antes da referência (0: todos)
    demanda_cheia: int = 15  # tantos projetos parecidos na janela dão a nota cheia (escala logarítmica)
    origem: tuple[tuple[str, float], ...] = (
        ("outra_casa", 1.0),
        ("popular", 1.0),
        ("executivo", 0.8),
        ("comissao", 0.7),
        ("judiciario", 0.6),
        ("parlamentar", 0.0),
    )
    alcance: tuple[tuple[str, float], ...] = (("PEC", 1.0), ("PLP", 0.8), ("PL", 0.6), ("PLS", 0.6), ("PLC", 0.6), ("PDL", 0.3))
    simbolicas: tuple[str, ...] = (
        r"^(institui|cria|estabelece|dispoe sobre) (o|a) (dia|semana|mes|ano|jornada) ",
        r"\bdia nacional\b",
        r"\bsemana nacional\b",
        r"^denomina\b",
        r"^confere\b",
        r"^inscreve\b",
        r"^declara\b",
        r"\bcapital nacional d",
        r"\blivro dos herois\b",
        r"\btitulo de cidada",
        r"\bpatrimonio (cultural|historico|imaterial)\b",
        r"\bcalendario oficial\b",
        r"\bmanifestacao da cultura nacional\b",
        r"\b(rota|regiao|circuito|polo) turistic",
        r"\bcapital (nacional|brasileira) d",
    )
    simbolica_teto: float = 10.0  # matéria simbólica não passa desta nota
    alta: float = 60.0  # nota a partir da qual os sinais são fortes
    baixa: float = 30.0  # nota abaixo da qual os sinais são fracos

    def peso(self, componente: str) -> float:
        return dict(self.pesos).get(componente, 0.0)


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
    relevancia: Relevancia = field(default_factory=Relevancia)
    apelidos: dict[str, list[str]] = field(default_factory=dict)  # "PL 2630/2020" → ["PL das Fake News", …]

    def __post_init__(self) -> None:
        self._tema_camara = {cod: t.id for t in self.temas for cod in t.camara}
        prefixos = [(prefixo, t.id) for t in self.temas for prefixo in t.senado]
        self._tema_senado = sorted(prefixos, key=lambda par: len(par[0]), reverse=True)
        self._nomes = {t.id: t.nome for t in self.temas}
        self._busca_apelidos = {text.normalize(nome): titulo for titulo, nomes in self.apelidos.items() for nome in nomes if text.normalize(nome)}

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

    # ── apelidos ─────────────────────────────────────────────────────────────

    def apelidos_para_busca(self) -> dict[str, str]:
        """Apelido (sem acento, minúsculo) → proposição, para achar no texto do jornal."""
        return dict(self._busca_apelidos)

    def apelido_de(self, titulo: str) -> str:
        """O apelido que a página mostra ao lado do número (o primeiro da lista)."""
        nomes = self.apelidos.get(titulo) or []
        return nomes[0] if nomes else ""


def _partidos(valores: Any) -> frozenset[str]:
    return frozenset(base.partido(str(v)) for v in (valores or []) if str(v).strip())


def _relevancia(bruto: Any) -> Relevancia:
    if not isinstance(bruto, dict):
        return Relevancia()
    padrao = Relevancia()
    campos: dict[str, Any] = {}
    for chave in ("pesos", "origem", "alcance"):
        if isinstance(bruto.get(chave), dict):
            campos[chave] = tuple((str(k), float(v)) for k, v in bruto[chave].items())
    if isinstance(bruto.get("simbolicas"), list):
        campos["simbolicas"] = tuple(str(p) for p in bruto["simbolicas"])
    for chave in ("janela_demanda_anos", "demanda_cheia"):
        if bruto.get(chave) is not None:
            campos[chave] = int(bruto[chave])
    for chave in ("simbolica_teto", "alta", "baixa"):
        if bruto.get(chave) is not None:
            campos[chave] = float(bruto[chave])
    return Relevancia(**{**padrao.__dict__, **campos})


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
                senado_nomes=tuple(str(s).strip() for s in t.get("senado") or []),
            )
            for t in bruto.get("temas") or []
        ],
        relevancia=_relevancia(bruto.get("relevancia")),
        apelidos={
            re.sub(r"\s+", " ", str(titulo)).strip(): [str(n).strip() for n in (nomes if isinstance(nomes, list) else [nomes]) if str(n).strip()]
            for titulo, nomes in (bruto.get("apelidos") or {}).items()
        },
    )


def carregar(root: Path) -> Regras:
    """Lê ``<root>/config/congresso.yaml``."""
    bruto = yaml.safe_load((Path(root) / "config" / ARQUIVO).read_text(encoding="utf-8")) or {}
    return de_dict(bruto)
