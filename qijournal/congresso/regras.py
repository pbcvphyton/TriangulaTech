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
    projeto_de_pais: ProjetoDePais | None = None  # a régua "Projeto de país" (a visão por parlamentar); sem o bloco, não existe
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
        projeto_de_pais=_projeto_de_pais(bruto.get("projeto_de_pais")),
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


# ── a régua "Projeto de país" (``projeto_de_pais``) ──────────────────────────
#
# Quanto cada proposição mexe em regra de sistema nas alavancas do objetivo declarado (o
# peso, de 0 a 100) e três marcas do que a ementa enuncia (contas, isonomia e controle). Um
# leitor marca seis campos com códigos fechados; a nota sai da fórmula, com os valores deste
# bloco. Aqui só se lê o arquivo: as contas ficam em :mod:`qijournal.congresso.leitura`.
# Nenhum id ou rótulo de alavanca, faixa, marca ou código é escrito no Python: vêm todos do
# YAML. Sem o bloco no arquivo, ``Regras.projeto_de_pais`` é ``None`` e a visão não existe.

ND = "nd"  # "a ementa não permite dizer": aceito em todo campo da leitura, nunca é código do YAML
ARREDONDAMENTOS = ("meio_para_cima",)  # os modos de arredondar o peso que a conta sabe fazer


@dataclass(frozen=True)
class CodigoDaRegua:
    """Um código fechado de um campo da leitura. Conforme o campo, traz o ``nivel`` (na
    alavanca: núcleo, entorno ou fora), o ``peso`` (no escopo e no mecanismo: o fator que
    multiplica) ou o ``valor`` (no custo, na isonomia e no controle: −1, 0 ou +1)."""

    id: str
    rotulo: str
    definicao: str = ""
    nivel: str = ""
    peso: float | None = None
    valor: int | None = None


@dataclass(frozen=True)
class CampoDaRegua:
    """Um campo que o leitor marca (alavanca, escopo, mecanismo, custo, isonomia, controle).
    ``chave`` é a letra dele na saída do leitor e a posição dele na linha gravada."""

    id: str
    chave: str
    rotulo: str
    codigos: tuple[CodigoDaRegua, ...]
    marca: str = ""  # a marca a que os valores do campo dão origem (custo → contas)
    conjuntos: tuple[str, ...] = ()  # no controle: os conjuntos de regras em que o campo vale

    def codigo(self, id_do_codigo: str) -> CodigoDaRegua | None:
        return next((c for c in self.codigos if c.id == id_do_codigo), None)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.codigos)

    @property
    def de_nivel(self) -> bool:
        """O campo que dá o nível (a alavanca)."""
        return any(c.nivel for c in self.codigos)

    @property
    def de_peso(self) -> bool:
        """Um campo que multiplica o peso (escopo e mecanismo)."""
        return any(c.peso is not None for c in self.codigos)


@dataclass(frozen=True)
class FaixaDaRegua:
    """Uma faixa do peso já arredondado: vale de ``de`` até o piso da faixa seguinte.
    ``so_nivel``: só para alavanca desse nível (peso 60 no entorno não é estruturante)."""

    id: str
    rotulo: str
    de: int
    so_nivel: str = ""
    descricao: str = ""


@dataclass(frozen=True)
class ValorDaMarca:
    """Como a página escreve um valor de uma marca: ``valor`` +1, 0 ou −1; ``None`` é o
    "a ementa não permite dizer". ``forma``: a do ponto nos gráficos."""

    id: str
    rotulo: str
    valor: int | None = None
    forma: str = ""


@dataclass(frozen=True)
class MarcaDaRegua:
    """Uma das marcas do sinal (contas, isonomia, controle), com o campo de onde ela sai."""

    id: str
    rotulo: str
    campo: str
    valores: tuple[ValorDaMarca, ...]

    def de(self, valor: int | None) -> ValorDaMarca | None:
        """A entrada do valor ``valor`` (``None``: a ementa não permite dizer)."""
        return next((v for v in self.valores if v.valor == valor), None)


@dataclass(frozen=True)
class LimiaresDaRegua:
    """Os mínimos e cortes da visão por parlamentar (``projeto_de_pais.limiares``)."""

    perfil_minimo: int = 10  # lidos para percentuais, mediana e rankings
    perfil_lista: int = 3  # de 3 a 9: a lista e a ponta
    ponta: int = 3  # média dos 3 maiores pesos; nunca em ranking
    saldo_minimo: int = 5  # projetos com contas +1 ou −1 para o saldo de contas
    partido_minimo: int = 5  # perfis para a mediana do partido
    andamento_peso: int = 20
    voto_peso: int = 20  # "nas matérias de peso"
    voto_minimo: int = 10
    voto_fechado: float = 0.80  # com orientação Sim ou Não
    voto_condicao: int = 5  # votações em cada condição e sob cada governo
    voto_quase_unanime: float = 0.90  # fora de todos os itens
    pessoas_minimo: int = 50  # abaixo disto: "não dá para responder por pessoa"
    confianca: float = 0.95  # intervalo por partido
    longe_do_partido: int = 20  # só apresentação: distância da mediana do partido para o destaque
    teto_pontos: int = 600  # só apresentação: máximo de pontos por Casa no gráfico dos projetos


@dataclass(frozen=True)
class LeituraDaRegua:
    """Como a leitura é feita e guardada (``projeto_de_pais.leitura``). ``versao`` é a das
    instruções ao leitor: quando sobe, as leituras gravadas com a antiga deixam de valer."""

    versao: int = 1
    tipos: tuple[str, ...] = ("PL", "PLP", "PEC")  # o que se lê
    tipos_votadas: tuple[str, ...] = ("MPV", "PLV")  # a mais, só nas matérias votadas
    estados: tuple[tuple[int, str], ...] = ()  # (r, como a página o diz)
    cega: bool = True
    id_opaco: bool = True
    piloto: int = 1000
    faixa_alerta: float = 0.5
    padrao_a_mao: int = 300
    fracao_plp_pec: float = 0.5
    concordancia_minima: float = 0.6
    reler_confianca: int = 1  # confiança até este valor pede uma segunda leitura
    candidatas_peso: int | None = None  # peso pela leitura de regras a partir do qual a proposição vai à IA; sem ele, vale ``segunda_leitura.peso_minimo``

    def estado(self, r: int) -> str:
        """Como a página diz o estado ``r`` de uma leitura (vazio se o arquivo não o nomeia)."""
        return dict(self.estados).get(r, "")


@dataclass(frozen=True)
class AlertaDeIniciativa:
    """Quando o ``in`` do leitor vira alerta: só com esta origem e nestes tipos."""

    chave: str = "in"
    origem: str = "parlamentar"
    tipos: tuple[str, ...] = ("PL", "PLP")


@dataclass(frozen=True)
class SegundaLeitura:
    peso_minimo: int = 60  # peso a partir do qual a faixa só conta depois de confirmada
    nd_tipos: tuple[str, ...] = ()
    nd_prontas_e_votadas: bool = False


@dataclass(frozen=True)
class AutoriaDaRegua:
    reapresentada_similaridade: float = 0.9
    bancada_coautores: int = 10
    bancada_fracao: float = 0.5
    onde_mira_sem: tuple[str, ...] = ()


@dataclass(frozen=True)
class Sensibilidade:
    """Uma escolha discutível da régua: o valor padrão de um código e a alternativa."""

    campo: str
    codigo: str
    padrao: int
    alternativa: int


@dataclass(frozen=True)
class CasoEspelho:
    caso: str
    codigos: tuple[str, ...]  # os códigos dos campos de peso, na ordem dos campos


@dataclass(frozen=True)
class ParEspelho:
    """Teste de aceite da régua: os dois lados têm de cair na mesma faixa."""

    id: str
    a: CasoEspelho
    b: CasoEspelho


@dataclass(frozen=True)
class Cruzar:
    """Os cortes e os sorteios de "Pessoa ou partido?" (``projeto_de_pais.cruzar``)."""

    zonas: tuple[float, float] = (30.0, 70.0)
    permutacoes: int = 1000
    reamostras: int = 2000
    semente: int = 57


@dataclass(frozen=True)
class PapeisDaRegua:
    """Quem é quem nas contas da visão por parlamentar (``projeto_de_pais.papeis``): os ids,
    do próprio arquivo, da marca do caixa (a "direção" da tela; dá o saldo de contas e o voto
    fiscal), da marca da exceção (a taxa de exceção), da marca de controle e da alavanca em que
    o controle também conta como voto fiscal; essa alavanca entra no voto fiscal com qualquer
    peso, menos nos escopos de ``voto_fiscal_sem_escopo``. Vazio: a conta que depende dele não
    é feita."""

    caixa: str = ""
    excecao: str = ""
    controle: str = ""
    alavanca_do_caixa: str = ""
    voto_fiscal_sem_escopo: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjetoDePais:
    """A régua "Projeto de país", como está em ``config/congresso.yaml``."""

    objetivo: str
    niveis: tuple[tuple[str, float, str], ...]  # (id, peso, rótulo), do maior peso para o menor
    campos: tuple[CampoDaRegua, ...]  # na ordem do arquivo, que é a da linha gravada
    faixas: tuple[FaixaDaRegua, ...]  # do piso menor para o maior
    marcas: tuple[MarcaDaRegua, ...]
    nd: str = "a ementa não permite dizer"
    arredonda: str = ARREDONDAMENTOS[0]
    alertas: dict[str, str] = field(default_factory=dict)  # id do alerta → como a página o escreve
    alerta_iniciativa: AlertaDeIniciativa = field(default_factory=AlertaDeIniciativa)
    sensibilidade: tuple[Sensibilidade, ...] = ()
    cft: tuple[str, ...] = ()
    segunda_leitura: SegundaLeitura = field(default_factory=SegundaLeitura)
    autoria: AutoriaDaRegua = field(default_factory=AutoriaDaRegua)
    limiares: LimiaresDaRegua = field(default_factory=LimiaresDaRegua)
    leitura: LeituraDaRegua = field(default_factory=LeituraDaRegua)
    pares_espelho: tuple[ParEspelho, ...] = ()
    cruzar: Cruzar = field(default_factory=Cruzar)
    fontes: dict[str, str] = field(default_factory=dict)  # de onde vem a leitura (regra, ia, ia2) → como a página o escreve
    selo: str = ""  # o selo da tela ("Leitura por IA, com régua pública")
    papeis: PapeisDaRegua = field(default_factory=PapeisDaRegua)  # quem é quem nas contas da visão por parlamentar

    def campo(self, nome: str) -> CampoDaRegua | None:
        """O campo de id (``"escopo"``) ou de chave (``"e"``) ``nome``."""
        return next((c for c in self.campos if nome in (c.id, c.chave)), None)

    @property
    def chaves(self) -> tuple[str, ...]:
        """As chaves dos campos, na ordem da linha gravada (``a, e, m, ct, is, co``)."""
        return tuple(c.chave for c in self.campos)

    @property
    def alavanca(self) -> CampoDaRegua:
        """O campo que dá o nível (o "eixo" da tela)."""
        return next(c for c in self.campos if c.de_nivel)

    @property
    def campos_de_peso(self) -> tuple[CampoDaRegua, ...]:
        """Os campos que multiplicam o peso, na ordem do arquivo (escopo e mecanismo)."""
        return tuple(c for c in self.campos if c.de_peso)

    @property
    def campos_de_marca(self) -> tuple[CampoDaRegua, ...]:
        return tuple(c for c in self.campos if c.marca)

    def peso_do_nivel(self, nivel: str) -> float:
        return next((peso for nome, peso, _rotulo in self.niveis if nome == nivel), 0.0)

    def rotulo_do_nivel(self, nivel: str) -> str:
        return next((rotulo for nome, _peso, rotulo in self.niveis if nome == nivel), nivel)

    def faixa(self, id_da_faixa: str) -> FaixaDaRegua | None:
        return next((f for f in self.faixas if f.id == id_da_faixa), None)

    def marca(self, id_da_marca: str) -> MarcaDaRegua | None:
        return next((m for m in self.marcas if m.id == id_da_marca), None)

    def rotulo_da_fonte(self, fonte: str) -> str:
        """Como a página escreve a fonte de uma leitura (sem rótulo no arquivo, o próprio id)."""
        return self.fontes.get(fonte, fonte)

    @property
    def peso_das_candidatas(self) -> int:
        """O peso, pela leitura de regras, a partir do qual a proposição vai à IA."""
        return self.leitura.candidatas_peso if self.leitura.candidatas_peso is not None else self.segunda_leitura.peso_minimo


def _conhecidos(classe: type, bruto: Any) -> dict[str, Any]:
    """Do dicionário do arquivo, só as chaves que a dataclass tem (listas viram tuplas)."""
    if not isinstance(bruto, dict):
        return {}
    return {k: (tuple(v) if isinstance(v, list) else v) for k, v in bruto.items() if k in classe.__dataclass_fields__ and v is not None}


def _valor_de_marca(bruto: Any, onde: str) -> int:
    if isinstance(bruto, bool) or bruto not in (-1, 0, 1):
        raise ValueError(f"{onde}: o valor tem de ser -1, 0 ou 1 (veio {bruto!r})")
    return int(bruto)


def _campos_da_regua(bruto: Any, niveis: set[str], onde: str) -> tuple[CampoDaRegua, ...]:
    if not isinstance(bruto, dict) or not bruto:
        raise ValueError(f"{onde}.campos: falta a lista de campos")
    campos: list[CampoDaRegua] = []
    for nome, c in bruto.items():
        aqui = f"{onde}.campos.{nome}"
        if not isinstance(c, dict) or not c.get("chave") or not c.get("codigos"):
            raise ValueError(f"{aqui}: falta \"chave\" ou \"codigos\"")
        marca = str(c.get("marca") or "").strip()
        codigos: list[CodigoDaRegua] = []
        for cod in c["codigos"]:
            if not isinstance(cod, dict) or not str(cod.get("id") or "").strip():
                raise ValueError(f"{aqui}: código sem \"id\"")
            cid = str(cod["id"]).strip()
            nivel = str(cod.get("nivel") or "").strip()
            if nivel and nivel not in niveis:
                raise ValueError(f"{aqui}.{cid}: nível desconhecido ({nivel})")
            if marca and cod.get("valor") is None:
                raise ValueError(f"{aqui}.{cid}: falta o \"valor\" (o campo dá a marca {marca})")
            peso = cod.get("peso")
            if peso is not None and (isinstance(peso, bool) or not isinstance(peso, (int, float)) or peso < 0):
                raise ValueError(f"{aqui}.{cid}: o peso tem de ser um número de 0 para cima (veio {peso!r})")
            codigos.append(
                CodigoDaRegua(
                    id=cid,
                    rotulo=str(cod.get("rotulo") or cid).strip(),
                    definicao=str(cod.get("def") or "").strip(),
                    nivel=nivel,
                    peso=float(peso) if peso is not None else None,
                    valor=_valor_de_marca(cod["valor"], f"{aqui}.{cid}") if cod.get("valor") is not None else None,
                )
            )
        ids = [cod.id for cod in codigos]
        if ND in ids or len(set(ids)) != len(ids):
            raise ValueError(f"{aqui}: código repetido ou chamado \"{ND}\" (que é reservado)")
        tipos = [bool(all(cod.nivel for cod in codigos)), bool(all(cod.peso is not None for cod in codigos)), bool(marca)]
        if sum(tipos) != 1:  # cada campo tem um papel só: dá o nível, multiplica o peso ou dá uma marca
            raise ValueError(f"{aqui}: todos os códigos têm de trazer \"nivel\", ou todos \"peso\", ou o campo traz \"marca\" e os códigos, \"valor\"")
        campos.append(
            CampoDaRegua(
                id=str(nome),
                chave=str(c["chave"]).strip(),
                rotulo=str(c.get("rotulo") or nome).strip(),
                codigos=tuple(codigos),
                marca=marca,
                conjuntos=tuple(str(x) for x in c.get("conjuntos") or []),
            )
        )
    chaves = [c.chave for c in campos]
    if len(set(chaves)) != len(chaves):
        raise ValueError(f"{onde}.campos: \"chave\" repetida")
    if sum(1 for c in campos if c.de_nivel) != 1 or not any(c.de_peso for c in campos):
        raise ValueError(f"{onde}.campos: é preciso um campo com \"nivel\" (a alavanca) e ao menos um com \"peso\"")
    return tuple(campos)


def _marcas_da_regua(bruto: Any, campos: tuple[CampoDaRegua, ...], nd: str, onde: str) -> tuple[MarcaDaRegua, ...]:
    """As marcas, uma por campo que tem ``marca``, na ordem dos campos. Sem rótulos no
    arquivo, cada valor aparece como número ("+1", "0", "−1")."""
    bruto = bruto if isinstance(bruto, dict) else {}
    marcas: list[MarcaDaRegua] = []
    for campo in campos:
        if not campo.marca:
            continue
        m = bruto.get(campo.marca) if isinstance(bruto.get(campo.marca), dict) else {}
        valores: list[ValorDaMarca] = []
        for v in m.get("valores") or []:
            if not isinstance(v, dict) or not str(v.get("id") or "").strip():
                raise ValueError(f"{onde}.marcas.{campo.marca}: valor sem \"id\"")
            valor = _valor_de_marca(v["valor"], f"{onde}.marcas.{campo.marca}.{v['id']}") if v.get("valor") is not None else None
            valores.append(ValorDaMarca(id=str(v["id"]).strip(), rotulo=str(v.get("rotulo") or v["id"]).strip(), valor=valor, forma=str(v.get("forma") or "").strip()))
        for valor, ident, rotulo in ((1, "mais", "+1"), (0, "zero", "0"), (-1, "menos", "−1"), (None, ND, nd)):
            if not any(v.valor == valor for v in valores):
                valores.append(ValorDaMarca(id=ident, rotulo=rotulo, valor=valor))
        if len({v.id for v in valores}) != len(valores) or len({v.valor for v in valores}) != len(valores):
            raise ValueError(f"{onde}.marcas.{campo.marca}: id ou valor repetido")
        marcas.append(MarcaDaRegua(id=campo.marca, rotulo=str(m.get("rotulo") or campo.rotulo).strip(), campo=campo.id, valores=tuple(valores)))
    if len({m.id for m in marcas}) != len(marcas):
        raise ValueError(f"{onde}.campos: dois campos dão a mesma marca")
    return tuple(marcas)


def _papeis_da_regua(bruto: Any, campos: tuple[CampoDaRegua, ...], marcas: tuple[MarcaDaRegua, ...], onde: str) -> PapeisDaRegua:
    """O bloco ``papeis``: cada id tem de existir no próprio arquivo (uma marca, um código da
    alavanca, códigos de um campo de peso). Sem o bloco, nenhum papel: as contas que dependem
    deles (saldo de contas, taxa de exceção, voto fiscal) não são feitas."""
    papeis = _conhecidos(PapeisDaRegua, bruto)
    ids_das_marcas = {m.id for m in marcas}
    for papel in ("caixa", "excecao", "controle"):
        if papeis.get(papel) and str(papeis[papel]) not in ids_das_marcas:
            raise ValueError(f"{onde}.papeis.{papel}: marca desconhecida ({papeis[papel]})")
    alavanca = next(c for c in campos if c.de_nivel)
    if papeis.get("alavanca_do_caixa") and str(papeis["alavanca_do_caixa"]) not in alavanca.ids:
        raise ValueError(f"{onde}.papeis.alavanca_do_caixa: alavanca desconhecida ({papeis['alavanca_do_caixa']})")
    de_peso = {cid for c in campos if c.de_peso for cid in c.ids}
    fora = [str(e) for e in papeis.get("voto_fiscal_sem_escopo") or () if str(e) not in de_peso]
    if fora:
        raise ValueError(f"{onde}.papeis.voto_fiscal_sem_escopo: código desconhecido ({', '.join(fora)})")
    return PapeisDaRegua(**{k: (tuple(str(x) for x in v) if isinstance(v, tuple) else str(v)) for k, v in papeis.items()})


def _projeto_de_pais(bruto: Any) -> ProjetoDePais | None:
    """O bloco ``projeto_de_pais``. Sem o bloco: ``None`` (a visão não existe). Bloco fora
    do formato: erro na leitura do arquivo, não uma régua pela metade."""
    if bruto is None:
        return None
    onde = f"config/{ARQUIVO}, projeto_de_pais"
    if not isinstance(bruto, dict):
        raise ValueError(f"{onde}: fora do formato")
    pesos = bruto.get("nivel")
    if not isinstance(pesos, dict) or not pesos:
        raise ValueError(f"{onde}.nivel: falta o peso de cada nível")
    rotulos = bruto.get("nivel_rotulo") if isinstance(bruto.get("nivel_rotulo"), dict) else {}
    niveis = tuple(sorted(((str(n), float(p), str(rotulos.get(n) or n)) for n, p in pesos.items()), key=lambda t: -t[1]))
    campos = _campos_da_regua(bruto.get("campos"), {n for n, _p, _r in niveis}, onde)
    faixas = tuple(
        sorted(
            (
                FaixaDaRegua(id=str(f["id"]), rotulo=str(f.get("rotulo") or f["id"]), de=int(f.get("de") or 0), so_nivel=str(f.get("so_nivel") or ""), descricao=str(f.get("descricao") or ""))
                for f in bruto.get("faixas") or []
            ),
            key=lambda f: f.de,
        )
    )
    if not faixas or faixas[0].de != 0 or faixas[0].so_nivel or len({f.id for f in faixas}) != len(faixas):
        raise ValueError(f"{onde}.faixas: a primeira faixa começa em 0 e vale para todo nível; os ids não se repetem")
    if any(f.so_nivel and f.so_nivel not in pesos for f in faixas):
        raise ValueError(f"{onde}.faixas: \"so_nivel\" com nível desconhecido")
    arredonda = str(bruto.get("arredonda") or ARREDONDAMENTOS[0])
    if arredonda not in ARREDONDAMENTOS:
        raise ValueError(f"{onde}.arredonda: só se sabe arredondar por {', '.join(ARREDONDAMENTOS)} (veio {arredonda})")
    nd = str(bruto.get("nd") or "a ementa não permite dizer").strip()
    leitura = _conhecidos(LeituraDaRegua, bruto.get("leitura"))
    if "estados" in leitura:
        leitura["estados"] = tuple((int(e["r"]), str(e.get("rotulo") or "").strip()) for e in leitura["estados"] if isinstance(e, dict) and e.get("r") is not None)
    for chave in ("tipos", "tipos_votadas"):
        if chave in leitura:
            leitura[chave] = tuple(str(t).strip().upper() for t in leitura[chave])
    iniciativa = _conhecidos(AlertaDeIniciativa, bruto.get("alerta_iniciativa"))
    if iniciativa.get("chave") in {c.chave for c in campos}:
        raise ValueError(f"{onde}.alerta_iniciativa: a chave repete a de um campo")
    cruzar = _conhecidos(Cruzar, bruto.get("cruzar"))
    if "zonas" in cruzar and (len(cruzar["zonas"]) != 2 or not cruzar["zonas"][0] < cruzar["zonas"][1]):
        raise ValueError(f"{onde}.cruzar.zonas: dois cortes, em ordem crescente")
    espelhos = []
    for par in bruto.get("pares_espelho") or []:
        lados = []
        for lado in ("a", "b"):
            caso = par.get(lado) if isinstance(par, dict) else None
            if not isinstance(caso, dict) or len(caso.get("codigos") or []) != 1 + sum(1 for c in campos if c.de_peso):
                raise ValueError(f"{onde}.pares_espelho: cada lado traz o caso e os códigos da alavanca e dos campos de peso")
            lados.append(CasoEspelho(caso=str(caso.get("caso") or ""), codigos=tuple(str(c) for c in caso["codigos"])))
        espelhos.append(ParEspelho(id=str(par.get("id") or len(espelhos) + 1), a=lados[0], b=lados[1]))
    marcas = _marcas_da_regua(bruto.get("marcas"), campos, nd, onde)
    return ProjetoDePais(
        objetivo=str(bruto.get("objetivo") or "").strip(),
        niveis=niveis,
        campos=campos,
        faixas=faixas,
        marcas=marcas,
        nd=nd,
        arredonda=arredonda,
        alertas={str(k): str(v).strip() for k, v in (bruto.get("alertas") or {}).items()},
        alerta_iniciativa=AlertaDeIniciativa(**iniciativa),
        sensibilidade=tuple(
            Sensibilidade(campo=str(s["campo"]), codigo=str(s["codigo"]), padrao=int(s["padrao"]), alternativa=int(s["alternativa"]))
            for s in bruto.get("sensibilidade") or []
        ),
        cft=tuple(str(c) for c in bruto.get("cft") or []),
        segunda_leitura=SegundaLeitura(**_conhecidos(SegundaLeitura, bruto.get("segunda_leitura"))),
        autoria=AutoriaDaRegua(**_conhecidos(AutoriaDaRegua, bruto.get("autoria"))),
        limiares=LimiaresDaRegua(**_conhecidos(LimiaresDaRegua, bruto.get("limiares"))),
        leitura=LeituraDaRegua(**leitura),
        pares_espelho=tuple(espelhos),
        cruzar=Cruzar(**cruzar),
        fontes={str(k): str(v).strip() for k, v in (bruto.get("fontes") or {}).items()},
        selo=str(bruto.get("selo") or "").strip(),
        papeis=_papeis_da_regua(bruto.get("papeis"), campos, marcas, onde),
    )
