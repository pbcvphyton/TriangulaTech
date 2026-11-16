"""As escolhas editoriais do analítico do Congresso (``config/congresso.yaml``):
legislaturas, governos, polos, limiares, faixas do campo, macrotemas e a apresentação
da cota parlamentar (os nomes curtos das despesas, os meses que ainda recebem notas e a
tabela do limite mensal por estado, que só vale depois de conferida na fonte oficial).

Nada disto é dado oficial: são as réguas com que os registros oficiais são lidos.
Por isso ficam num arquivo só, à vista, e vão escritas na seção "Como ler" da aba.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
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
    escolha_fidelidade: int = 80  # "vota diferente do partido": com a maioria do partido em menos que isto (%)
    escolha_presenca: int = 60  # "votou em menos de X% das votações"
    frente_poucas: int = 20  # votações em comum abaixo disto: o cartão da frente avisa que são poucas
    voto_apertado: int = 30  # "diferença pequena" entre o Sim e o Não, em votos da Câmara (no Senado, na mesma proporção)
    placar_tolerancia: int = 20  # diferença, em votos, entre o registro um a um e o placar anunciado para a votação sair das contas


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
    curto: str = ""  # o nome curto, para listas e filtros ("Economia")


@dataclass(frozen=True)
class FrenteDeCausa:
    """Uma frente parlamentar de causa (config/congresso.yaml, ``frentes``): o começo do
    título oficial, o nome curto e o tema da aba em que a causa se vota (``todas``: a frente
    não tem um tema só, e conta tudo)."""

    titulo: str  # normalizado (sem acento, minúsculas)
    nome: str
    tema: str


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


@dataclass(frozen=True)
class GrupoDoJogo:
    """Um grupo de "regras do próprio jogo" (eleições, partidos, mandato…): os padrões valem
    sobre o começo da ementa, normalizada (sem acento, minúsculas)."""

    id: str
    nome: str
    padroes: tuple[str, ...]


@dataclass(frozen=True)
class Marco:
    """Uma data do calendário oficial anotada no "quando vota" (recesso, eleição). ``de`` e
    ``ate`` em ``MM-DD`` valem para todo ano; em ``AAAA-MM-DD``, para aquele dia."""

    nome: str
    de: str
    ate: str = ""


@dataclass(frozen=True)
class Pauta:
    """Os sinais das matérias aprovadas pelo Plenário ("O que espera × o que passa"): os
    limiares, os padrões de "regras do próprio jogo" e o calendário. Tudo escolha editorial,
    em ``config/congresso.yaml`` (``pauta``). Sem padrões no arquivo, o sinal não existe."""

    relampago_dias: int = 30  # "relâmpago": do protocolo ao voto em até N dias
    sinais_vale_olhar: int = 4  # "vale olhar": tantos sinais ou mais (a urgência sozinha não conta)
    sinais_vale_olhar_senado: int = 3  # o mesmo no Senado, onde o registro só permite ler quatro sinais
    surto_vezes: float = 2.0  # "surto": semana com N vezes a mediana das semanas com aprovação
    ementa_ate: int = 400  # "regras do próprio jogo": o padrão tem de começar nos primeiros N caracteres
    tema_minimo: int = 50  # a direção por tema só aparece se ao menos N% das aprovadas e das que esperam têm tema no registro
    proprio_jogo: tuple[GrupoDoJogo, ...] = ()
    calendario: tuple[Marco, ...] = ()


# As 27 unidades da federação: a tabela do limite mensal da cota só vale com todas.
UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
)  # fmt: skip
MESES_RECENTES = 3  # sem o bloco "cota" no arquivo: os três últimos meses fechados ainda recebem notas


@dataclass(frozen=True)
class RotuloDeDespesa:
    """O nome de uma despesa da cota na página (config/congresso.yaml, ``cota.rotulos``)."""

    oficial: str  # o nome oficial normalizado (sem acento, minúsculas, sem pontuação): é por ele que se casa
    id: str  # estável: é o que vai no endereço da página e no arquivo de dados
    curto: str  # o que a página mostra ("Divulgação do mandato")


@dataclass(frozen=True)
class TetosDaCota:
    """A tabela do limite mensal da cota de uma Casa, por estado, em centavos. Só existe
    depois de conferida na fonte oficial (``fonte`` e ``url`` dizem qual); cada vigência
    traz as 27 UFs."""

    fonte: str
    url: str
    vigencias: tuple[tuple[str, Mapping[str, int]], ...]  # (desde AAAA-MM, {UF: centavos por mês}), da mais antiga à mais nova

    def em(self, uf: str, mes: str) -> int | None:
        """O limite do estado ``uf`` no mês ``AAAA-MM``, em centavos (``None`` antes da
        primeira vigência ou para uma UF que a tabela não conhece)."""
        valor = None
        for desde, valores in self.vigencias:
            if desde <= mes:
                valor = valores.get(uf)
        return valor


@dataclass(frozen=True)
class Cota:
    """A apresentação da cota parlamentar (config/congresso.yaml, ``cota``). Nada aqui muda
    uma soma: são os nomes com que as despesas aparecem, quantos meses do fim da série
    ainda recebem notas e, se conferida, a tabela do limite mensal por estado. Sem a
    tabela (o padrão), a página não mostra limite nenhum."""

    meses_recentes: int = MESES_RECENTES
    rotulos: Mapping[str, tuple[RotuloDeDespesa, ...]] = field(default_factory=dict)  # casa → rótulos
    tetos: Mapping[str, TetosDaCota] = field(default_factory=dict)  # casa → tabela (só as conferidas e completas)
    avisos: tuple[str, ...] = ()  # o que o arquivo trazia e não pôde valer (tabela incompleta)

    def rotulo(self, casa: str, oficial: str) -> tuple[str, str]:
        """``(id, nome curto)`` da despesa de nome ``oficial``. Sem rótulo no arquivo: o id é
        o nome normalizado com hífens e o nome curto, o oficial só com a inicial maiúscula."""
        chave = text.normalize(oficial)
        for r in self.rotulos.get(casa) or ():
            if r.oficial == chave:
                return r.id, r.curto
        limpo = re.sub(r"\s*([,;:])\s*", r"\1 ", re.sub(r"\s+", " ", oficial)).strip(" .")
        return chave.replace(" ", "-") or "despesa", (limpo[:1].upper() + limpo[1:].lower()) or oficial

    def teto(self, casa: str) -> TetosDaCota | None:
        return self.tetos.get(casa)


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
    frentes: tuple[FrenteDeCausa, ...] = ()  # as frentes parlamentares de causa (só a Câmara registra quem assina)
    fusoes: dict[str, str] = field(default_factory=dict)  # partido que deixou de existir → o sucessor (siglas normalizadas)
    pauta: Pauta = field(default_factory=Pauta)  # os sinais das aprovadas, o calendário e as "regras do próprio jogo"
    cota: Cota = field(default_factory=Cota)  # a apresentação da cota: nomes curtos, meses recentes e o limite por estado

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


def _pauta(bruto: Any) -> Pauta:
    if not isinstance(bruto, dict):
        return Pauta()
    campos: dict[str, Any] = {}
    for chave in ("relampago_dias", "sinais_vale_olhar", "sinais_vale_olhar_senado", "ementa_ate", "tema_minimo"):
        if bruto.get(chave) is not None:
            campos[chave] = int(bruto[chave])
    if bruto.get("surto_vezes") is not None:
        campos["surto_vezes"] = float(bruto["surto_vezes"])
    grupos = []
    for g in bruto.get("proprio_jogo") or []:
        padroes = tuple(str(p) for p in (g.get("padroes") or []) if str(p).strip()) if isinstance(g, dict) else ()
        if padroes and g.get("id"):
            for padrao in padroes:
                re.compile(padrao)  # padrão inválido no arquivo: erro na leitura, não uma regra muda
            grupos.append(GrupoDoJogo(id=str(g["id"]), nome=str(g.get("nome") or g["id"]).strip(), padroes=padroes))
    campos["proprio_jogo"] = tuple(grupos)
    campos["calendario"] = tuple(
        Marco(nome=str(m.get("nome") or "").strip(), de=str(m["de"]), ate=str(m.get("ate") or ""))
        for m in bruto.get("calendario") or []
        if isinstance(m, dict) and m.get("de")
    )
    return Pauta(**campos)


def _centavos(bruto: Any) -> int | None:
    """R$ do arquivo → centavos inteiros; vazio, negativo, zero ou ilegível não é valor."""
    if isinstance(bruto, bool) or bruto is None:
        return None
    try:
        valor = round(float(bruto) * 100)
    except (TypeError, ValueError):
        return None
    return valor if valor > 0 else None


def _tetos(bruto: Any, casa: str) -> tuple[TetosDaCota | None, str]:
    """A tabela do limite mensal de uma Casa e, se ela não puder valer, o aviso que diz por
    quê. Vale só inteira: fonte, endereço e as 27 UFs com valor em toda vigência. Tabela
    pela metade compararia uns estados e não outros; então fica desligada."""
    if bruto is None:
        return None, ""
    onde = f"config/{ARQUIVO}, cota.tetos.{casa}"
    if not isinstance(bruto, dict):
        return None, f"{onde}: fora do formato; a página sai sem o limite mensal"
    fonte, url = str(bruto.get("fonte") or "").strip(), str(bruto.get("url") or "").strip()
    vigencias: list[tuple[str, dict[str, int]]] = []
    for v in bruto.get("vigencias") or []:
        desde = str((v or {}).get("desde") or "")[:7] if isinstance(v, dict) else ""
        valores = v.get("valores") if isinstance(v, dict) else None
        if not re.fullmatch(r"\d{4}-\d{2}", desde) or not isinstance(valores, dict):
            return None, f"{onde}: vigência sem \"desde\" (AAAA-MM) ou sem \"valores\"; a página sai sem o limite mensal"
        centavos = {str(uf).strip().upper(): _centavos(valor) for uf, valor in valores.items()}
        faltam = [uf for uf in UFS if not centavos.get(uf)]
        if faltam:
            return None, f"{onde}: a vigência de {desde} não traz {', '.join(faltam)}; a página sai sem o limite mensal"
        vigencias.append((desde, {uf: int(centavos[uf] or 0) for uf in UFS}))
    if not vigencias or not fonte or not url:
        return None, f"{onde}: falta a fonte, o endereço ou a vigência; a página sai sem o limite mensal"
    return TetosDaCota(fonte=fonte, url=url, vigencias=tuple(sorted(vigencias, key=lambda par: par[0]))), ""


def _cota(bruto: Any) -> Cota:
    if not isinstance(bruto, dict):
        return Cota()
    campos: dict[str, Any] = {}
    if bruto.get("meses_recentes") is not None:
        campos["meses_recentes"] = max(0, int(bruto["meses_recentes"]))
    rotulos: dict[str, tuple[RotuloDeDespesa, ...]] = {}
    for casa, lista in (bruto.get("rotulos") or {}).items():
        itens = []
        for r in lista or []:
            if isinstance(r, dict) and r.get("oficial") and r.get("id"):
                itens.append(RotuloDeDespesa(oficial=text.normalize(str(r["oficial"])), id=str(r["id"]).strip(), curto=str(r.get("curto") or r["oficial"]).strip()))
        ids = [r.id for r in itens]
        repetidos = sorted({i for i in ids if ids.count(i) > 1})
        if repetidos:  # o id vai no endereço: repetido, dois filtros virariam um
            raise ValueError(f"config/{ARQUIVO}, cota.rotulos.{casa}: id repetido ({', '.join(repetidos)})")
        rotulos[str(casa)] = tuple(itens)
    campos["rotulos"] = rotulos
    tetos: dict[str, TetosDaCota] = {}
    avisos: list[str] = []
    for casa, tabela in (bruto.get("tetos") or {}).items():
        lido, aviso = _tetos(tabela, str(casa))
        if lido is not None:
            tetos[str(casa)] = lido
        elif aviso:
            avisos.append(aviso)
    campos["tetos"] = tetos
    campos["avisos"] = tuple(avisos)
    return Cota(**campos)


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
                curto=str(t.get("curto") or "").strip() or nome_curto(str(t.get("nome") or t["id"])),
            )
            for t in bruto.get("temas") or []
        ],
        relevancia=_relevancia(bruto.get("relevancia")),
        apelidos={
            re.sub(r"\s+", " ", str(titulo)).strip(): [str(n).strip() for n in (nomes if isinstance(nomes, list) else [nomes]) if str(n).strip()]
            for titulo, nomes in (bruto.get("apelidos") or {}).items()
        },
        frentes=tuple(
            FrenteDeCausa(titulo=text.normalize(str(f["titulo"])), nome=str(f.get("nome") or f["titulo"]).strip(), tema=str(f.get("tema") or "todas"))
            for f in bruto.get("frentes") or []
            if f.get("titulo")
        ),
        fusoes={
            text.normalize(str(de)): text.normalize(str(f["para"]))
            for f in bruto.get("fusoes") or []
            if f.get("para")
            for de in (f.get("de") if isinstance(f.get("de"), list) else [f.get("de")])
            if de
        },
        pauta=_pauta(bruto.get("pauta")),
        cota=_cota(bruto.get("cota")),
    )


def nome_curto(nome: str) -> str:
    """O começo do nome do tema, até a primeira vírgula ou " e " ("Economia, tributos e
    orçamento" → "Economia"), quando o config não dá o nome curto."""
    return re.split(r",\s*| e ", nome, maxsplit=1)[0].strip() or nome


def carregar(root: Path) -> Regras:
    """Lê ``<root>/config/congresso.yaml``."""
    bruto = yaml.safe_load((Path(root) / "config" / ARQUIVO).read_text(encoding="utf-8")) or {}
    return de_dict(bruto)
