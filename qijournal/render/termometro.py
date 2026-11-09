"""Views do Termômetro de Viés Editorial: o painel do dia (fim da edição) e as
peças reaproveitadas pela aba "Analítico Político" (:mod:`qijournal.render.analitico`).

Tudo aqui sai pronto para o template: textos limpos, números já formatados em
pt-BR (sinal de menos tipográfico) e posições em % na escala de −100 (esquerda)
a +100 (direita). Os números vêm só do cálculo de referência
(:mod:`qijournal.termometro.referencia`) e da agregação
(:mod:`qijournal.termometro.agregacao`); nada é recalculado aqui — as médias (do
dia, de cada veículo e de cada editoria) saem de ``agregacao.resumo``, e o resto
são contagens das análises guardadas (rótulos, sinais por critério, itens da
qualidade da apuração).

O painel do dia tem, de cima para baixo: o placar (T médio, rótulo e intervalo de
95%, com a escala, as faixas, o intervalo e um traço por notícia), os indicadores,
a distribuição por rótulo, os sinais por critério, o checklist da qualidade da
apuração, os recortes por veículo e por editoria e a lista de cada texto analisado
(com filtros, ordem e o detalhe de cada um: sinais com a citação, enquadramento,
fontes, checklist e teste de inversão).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .. import text
from ..termometro import agregacao, historico
from ..termometro.referencia import NOMES, PARAMETROS, arredondar
from . import filters

ANALITICO_PAGINA = "analitico.html"

# Faixas do termômetro (PARAMETROS["faixas"] = 10, 25, 50), da esquerda para a direita.
_F1, _F2, _F3 = PARAMETROS["faixas"]
FAIXAS = (
    (-100, -_F3, "ef", "Esquerda forte"),
    (-_F3, -_F2, "em", "Esquerda moderada"),
    (-_F2, -_F1, "el", "Leve à esquerda"),
    (-_F1, _F1, "eq", "Equilibrado"),
    (_F1, _F2, "dl", "Leve à direita"),
    (_F2, _F3, "dm", "Direita moderada"),
    (_F3, 100, "df", "Direita forte"),
)
CLASSE = {rotulo: classe for _, _, classe, rotulo in FAIXAS} | {"Misto": "mx"}
ORDEM_ROTULOS = [rotulo for *_, rotulo in FAIXAS] + ["Misto"]
# Nome da faixa na escala (o lado vem da posição: "← Esquerda" e "Direita →" nas pontas).
ZONA = {"ef": "Forte", "em": "Moderada", "el": "Leve", "eq": "Equilibrado", "dl": "Leve", "dm": "Moderada", "df": "Forte"}
# Marcas da escala: as fronteiras das faixas (as de ±10 e ±25 somem no celular).
MARCAS = (-100, -_F3, -_F2, -_F1, 0, _F1, _F2, _F3, 100)
CONFIANCA = {"alta": "alta", "media": "média", "baixa": "baixa"}
DIRECAO = {"esquerda": "esquerda", "direita": "direita", "equilibrio": "equilíbrio"}
SETA = {"esquerda": "esq", "direita": "dir", "equilibrio": "eq"}
EIXOS = {
    "economico": "Econômico",
    "costumes": "Costumes",
    "seguranca": "Segurança",
    "instituicoes": "Instituições",
    "geral": "Geral",
}
CAMPOS = {
    "esquerda": "esquerda",
    "centro_esquerda": "centro-esquerda",
    "centro": "centro",
    "centro_direita": "centro-direita",
    "direita": "direita",
    "indefinido": "sem campo",
}
# Itens da qualidade da apuração (PARAMETROS["qualidade_pesos"]), como o leitor os lê.
QUALIDADE_ITENS = {
    "titulo_sustentado": "Título sustentado pelo texto",
    "contraponto": "Contraponto ouvido ou procurado",
    "fontes_identificadas": "Fontes identificadas",
    "opiniao_separada": "Opinião separada do fato",
    "numeros_contextualizados": "Números com fonte, base e período",
    "documento_primario": "Documento ou dado de origem citado",
    "incerteza_declarada": "Diz o que não se sabe",
    "autoria_data": "Autoria e data informadas",
}
ENQUADRAMENTO = {
    "problema": "Problema",
    "causa": "Causa",
    "vitima": "Quem sofre",
    "culpado": "Responsável",
    "solucao": "Solução",
    "motor_moral": "Valor moral",
}
GENERO = {"noticia": "notícia", "entrevista": "entrevista", "analise": "análise", "opiniao": "opinião", "editorial": "editorial"}
RELEVANCIA = {"alta": "alta", "media": "média", "baixa": "baixa", "nenhuma": "nenhuma"}
ESTADO = {"ok": "Com T", "inconclusivo": "Inconclusivo", "nao_se_aplica": "Sem dimensão política"}
DOMINIOS = (25, 50, 100)
RECORTE_MAX = 10  # veículos visíveis no recorte do dia (os demais abrem num "ver todos")
LISTA_VISIVEL = 12  # textos visíveis antes do "mostrar todos" (com o script; sem ele, todos)
MENOS = "−"


# ── números ──────────────────────────────────────────────────────────────────


def fmt_t(valor: float | None) -> str:
    """T arredondado como no cálculo, com sinal: "−14", "+8", "0"."""
    if valor is None:
        return "—"
    t = arredondar(valor)
    return f"{MENOS}{abs(t)}" if t < 0 else f"+{t}" if t > 0 else "0"


def fmt_dec(valor: float | None, casas: int = 1, sinal: bool = True) -> str:
    """Decimal em pt-BR com sinal tipográfico: "−1,2", "+0,4"."""
    if valor is None:
        return "—"
    numero = text.format_number_pt(abs(valor), casas)
    if round(valor, casas) == 0:
        return numero
    if valor < 0:
        return f"{MENOS}{numero}"
    return f"+{numero}" if sinal else numero


def fmt_q(valor: float | None) -> str:
    return "—" if valor is None else str(arredondar(valor))


def fmt_ic(ic: list[float] | None, fmt=fmt_t) -> str:
    return f"{fmt(ic[0])} a {fmt(ic[1])}" if ic else ""


def pct(valor: float, dominio: float = 100.0) -> float:
    """Posição (0 a 100%) de um valor numa escala simétrica de −dominio a +dominio."""
    valor = max(-dominio, min(dominio, valor))
    return round((valor + dominio) / (2 * dominio) * 100, 2)


def contagem(n: int, singular: str, plural: str) -> str:
    return f"{text.format_number_pt(n, 0)} {singular if n == 1 else plural}"


def _porcento(parte: int, total: int) -> int:
    return arredondar(100 * parte / total) if total else 0


def porcento_txt(parte: int, total: int) -> str:
    """A parcela escrita sem esconder o que sobra: 452 de 453 é "99,8%", não "100%",
    e 1 de 453 é "0,2%", não "0%" (só 0 e o total inteiro saem como 0% e 100%)."""
    if not total or not parte:
        return "0%"
    if parte >= total:
        return "100%"
    valor = 100 * parte / total
    if valor < 0.05:
        return "<0,1%"
    if valor < 1 or valor > 99:
        return f"{text.format_number_pt(min(valor, 99.9), 1)}%"
    return f"{arredondar(valor)}%"


def _marca(valor: int) -> str:
    return f"{MENOS}{abs(valor)}" if valor < 0 else f"+{valor}" if valor > 0 else "0"


# ── escala, medidor e distribuição ───────────────────────────────────────────


@dataclass
class FaixaView:
    classe: str
    rotulo: str
    left: float
    width: float
    zona: str = ""  # nome curto na escala ("Leve", "Equilibrado"…)
    ativa: bool = False  # a faixa em que cai a média (em destaque)


@dataclass
class MarcaView:
    left: float
    rotulo: str
    menor: bool = False  # ±10 e ±25: some no celular


@dataclass
class GaugeView:
    """Escala de −100 a +100 com as faixas, o ponto da média, o intervalo de 95% e,
    no painel do dia, um traço por notícia (``rug``)."""

    pct: float
    valor: str  # "−14"
    rotulo: str
    classe: str
    ic: str  # "−20 a −8" (vazio sem intervalo)
    ic_left: float | None
    ic_width: float | None
    aria: str
    faixas: list[FaixaView]
    marcas: list[MarcaView] = field(default_factory=list)
    rug: list[float] = field(default_factory=list)  # posição (%) de cada notícia com T


def faixas_view(ativa: str = "") -> list[FaixaView]:
    return [FaixaView(c, r, pct(a), round(pct(b) - pct(a), 2), ZONA[c], c == ativa) for a, b, c, r in FAIXAS]


def marcas_view() -> list[MarcaView]:
    return [MarcaView(pct(v), _marca(v), abs(v) in (_F1, _F2)) for v in MARCAS]


def gauge(
    media: float | None,
    ic: list[float] | None,
    rotulo: str | None,
    *,
    descricao: str,
    valores: Iterable[float] = (),
) -> GaugeView | None:
    if media is None:
        return None
    rotulo = rotulo or agregacao.rotulo_T(media)
    classe = CLASSE.get(rotulo, "eq")
    ic_left = ic_width = None
    if ic:
        ic_left = pct(ic[0])
        ic_width = round(max(0.4, pct(ic[1]) - ic_left), 2)
    texto_ic = fmt_ic(ic)
    aria = f"{descricao}: T {fmt_t(media)} ({rotulo})" + (f", intervalo de 95% de {texto_ic}" if texto_ic else "")
    return GaugeView(
        pct=pct(media),
        valor=fmt_t(media),
        rotulo=rotulo,
        classe=classe,
        ic=texto_ic,
        ic_left=ic_left,
        ic_width=ic_width,
        aria=aria + "; escala de −100 (esquerda) a +100 (direita)",
        faixas=faixas_view(classe),
        marcas=marcas_view(),
        rug=sorted(pct(v) for v in valores),
    )


@dataclass
class BarraView:
    rotulo: str
    classe: str
    n: int
    largura: float  # % da barra maior
    share: str  # parcela do total, escrita ("99,8%")


@dataclass
class DistribuicaoView:
    """Quantos textos em cada rótulo, da esquerda forte à direita forte (Misto à parte)."""

    barras: list[BarraView]
    total: int
    aria: str
    fora: str = ""  # notícias que não entram no T (sem dimensão política, inconclusivas)


def distribuicao(rotulos: Mapping[str, int], *, fora: str = "") -> DistribuicaoView | None:
    total = sum(int(rotulos.get(r, 0) or 0) for r in ORDEM_ROTULOS)
    if not total:
        return None
    maior = max(int(rotulos.get(r, 0) or 0) for r in ORDEM_ROTULOS)
    barras = []
    for r in ORDEM_ROTULOS:
        n = int(rotulos.get(r, 0) or 0)
        if r == "Misto" and not n:
            continue  # Misto só aparece quando há
        barras.append(BarraView(r, CLASSE[r], n, round(n * 100 / maior, 2) if n else 0.0, porcento_txt(n, total)))
    aria = "Rótulos das notícias: " + "; ".join(f"{b.rotulo}: {b.n}" for b in barras if b.n)
    return DistribuicaoView(barras=barras, total=total, aria=aria, fora=fora)


# ── análises ─────────────────────────────────────────────────────────────────


@dataclass
class EvidenciaView:
    id: str
    criterio: str
    citacao: str
    direcao: str
    contribuicao: str
    explicacao: str
    seta: str = "eq"  # esq | dir | eq (cor e seta do sinal)
    paragrafo: str = ""
    intensidade: str = ""  # "leve", "claro" ou "forte"
    favorece: str = ""
    desfavorece: str = ""
    ressalva: str = ""


@dataclass
class ChecagemView:
    nome: str
    valor: str  # sim | nao | nao_se_aplica
    texto: str  # "sim", "não", "não se aplica"


@dataclass
class FonteView:
    rotulo: str
    campo: str
    anonima: bool
    palavras: int
    ultima: bool


@dataclass
class ContribuicaoView:
    nome: str
    valor: str  # "−2,4"
    lado: str  # esq | dir | eq
    width: float  # % da maior contribuição do texto


@dataclass
class AnaliseView:
    id: str
    veiculo: str
    editoria: str
    titulo: str
    url: str | None
    data: str  # "29/09"
    estado: str
    valor: str  # "−14" ou "—"
    pct: float | None
    rotulo: str
    classe: str
    confianca: str
    q: str
    q_rotulo: str
    selo: str
    resumo: str
    eixos: str
    evidencias: list[EvidenciaView] = field(default_factory=list)
    dominio: str = ""  # site do texto original ("valor.globo.com"), na dica do link
    # detalhe (análises feitas a partir de nov/2026; as anteriores não têm)
    t_num: float | None = None
    q_num: int | None = None
    origem: str = ""
    genero: str = ""
    relevancia: str = ""
    justificativa: str = ""
    motivos: str = ""
    palavras: str = ""
    cortado: bool = False
    enquadramento: list[tuple[str, str]] = field(default_factory=list)
    checklist: list[ChecagemView] = field(default_factory=list)
    fontes: list[FonteView] = field(default_factory=list)
    fala: str = ""  # "Fala direta: 18 palavras de fontes de esquerda · 22 de direita"
    inversao: str = ""
    contribuicoes: list[ContribuicaoView] = field(default_factory=list)
    atores: str = ""  # "Lula (esquerda) · Tarcísio (direita)"
    descartadas: int = 0
    opiniao: bool = False

    @property
    def sinais(self) -> int:
        return len(self.evidencias)

    @property
    def detalhe(self) -> bool:
        return bool(
            self.resumo or self.evidencias or self.enquadramento or self.checklist or self.fontes or self.inversao
        )


def nome_criterio(chave: Any) -> str:
    """Nome do critério para o leitor, com inicial maiúscula ("Verbos de atribuição")."""
    nome = NOMES.get(str(chave), filters.plain(str(chave or "")))
    return nome[:1].upper() + nome[1:]


def _dominio(url: str | None) -> str:
    try:
        host = urlsplit(url or "").hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _data_curta(iso: str | None) -> str:
    iso = str(iso or "")
    return f"{iso[8:10]}/{iso[5:7]}" if len(iso) >= 10 else iso


def _texto(valor: Any) -> str:
    return filters.plain(str(valor)) if isinstance(valor, str) else ""


def _peso(e: Any) -> float:
    return abs(e["contribuicao"]) if isinstance(e, dict) and isinstance(e.get("contribuicao"), (int, float)) else 0.0


def _evidencias(a: Mapping[str, Any]) -> list[EvidenciaView]:
    """Os sinais que pontuaram, do maior peso no T para o menor (a ordem do texto desempata)."""
    lista = []
    for e in sorted(a.get("evidencias") or [], key=lambda e: -_peso(e)):
        if not isinstance(e, dict):
            continue
        direcao = str(e.get("direcao") or "")
        intensidade = e.get("intensidade")
        lista.append(
            EvidenciaView(
                id=_texto(e.get("id")),
                criterio=nome_criterio(e.get("criterio")),
                citacao=_texto(e.get("citacao")),
                direcao=DIRECAO.get(direcao, ""),
                contribuicao=(
                    fmt_dec(e["contribuicao"])
                    if isinstance(e.get("contribuicao"), (int, float)) and round(e["contribuicao"], 1)
                    else ""
                ),
                explicacao=_texto(e.get("explicacao")),
                seta=SETA.get(direcao, "eq"),
                paragrafo=_texto(e.get("paragrafo")),
                intensidade={1: "leve", 2: "claro", 3: "forte"}.get(intensidade, "") if isinstance(intensidade, int) else "",
                favorece=_texto(e.get("favorece")),
                desfavorece=_texto(e.get("desfavorece")),
                ressalva=_texto(e.get("ressalva")),
            )
        )
    return lista


def _valor_moral(valor: str) -> str:
    """O motor moral vem como código ("cuidado_igualdade", "ordem_autoridade"): vira "cuidado e igualdade"."""
    return valor.replace("_", " e ") if " " not in valor else valor


def _checklist(qualidade: Mapping[str, Any]) -> list[ChecagemView]:
    itens = qualidade.get("itens")
    if not isinstance(itens, dict):
        return []
    textos = {"sim": "sim", "nao": "não", "nao_se_aplica": "não se aplica"}
    return [
        ChecagemView(nome, str(itens[k]), textos.get(str(itens[k]), ""))
        for k, nome in QUALIDADE_ITENS.items()
        if str(itens.get(k)) in textos
    ]


def _fontes(a: Mapping[str, Any]) -> list[FonteView]:
    fontes = []
    for f in a.get("fontes") or []:
        if isinstance(f, dict) and _texto(f.get("rotulo")):
            fontes.append(
                FonteView(
                    rotulo=_texto(f.get("rotulo")),
                    campo=CAMPOS.get(str(f.get("campo")), "sem campo"),
                    anonima=bool(f.get("anonima")),
                    palavras=int(f.get("palavras") or 0),
                    ultima=bool(f.get("ultima")),
                )
            )
    return fontes


def _contribuicoes(a: Mapping[str, Any]) -> list[ContribuicaoView]:
    criterios = a.get("criterios")
    if not isinstance(criterios, dict):
        return []
    valores = {k: v for k, v in criterios.items() if isinstance(v, (int, float)) and k in NOMES}
    maior = max((abs(v) for v in valores.values()), default=0)
    return [
        ContribuicaoView(
            nome_criterio(k),
            fmt_dec(v),
            "esq" if v < 0 else "dir" if v > 0 else "eq",
            round(abs(v) * 100 / maior, 2) if maior else 0.0,
        )
        for k, v in sorted(valores.items(), key=lambda kv: -abs(kv[1]))
    ]


def analise_view(a: Mapping[str, Any], *, evidencias: bool = True) -> AnaliseView:
    estado = str(a.get("estado") or "")
    valor = a.get("T") if estado == "ok" else None
    rotulo = filters.plain(str(a.get("rotulo") or ""))
    qualidade = a.get("qualidade_apuracao") or {}
    q_num = qualidade.get("Q") if isinstance(qualidade.get("Q"), (int, float)) else None
    eixos = [EIXOS.get(str(x), str(x)) for x in (a.get("triagem") or {}).get("eixos") or []]
    url = filters.safe_url(a.get("url"))
    view = AnaliseView(
        id=str(a.get("id") or ""),
        veiculo=filters.plain(str(a.get("veiculo") or "")),
        editoria=filters.plain(str(a.get("editoria") or "")),
        titulo=filters.plain(str(a.get("titulo") or "")),
        url=url,
        dominio=_dominio(url),
        data=_data_curta(a.get("data_publicacao")),
        estado=estado,
        valor=fmt_t(valor),
        pct=pct(valor) if isinstance(valor, (int, float)) else None,
        rotulo=rotulo,
        classe=CLASSE.get(rotulo, "na"),
        confianca=CONFIANCA.get(str(a.get("confianca")), ""),
        q=fmt_q(q_num),
        q_rotulo=filters.plain(str(qualidade.get("rotulo") or "")),
        selo=filters.plain(str(a.get("selo") or "")),
        resumo=filters.plain(str(a.get("resumo") or "")),
        eixos=", ".join(eixos),
        evidencias=_evidencias(a) if evidencias else [],  # do maior peso para o menor
        t_num=float(valor) if isinstance(valor, (int, float)) else None,
        q_num=arredondar(q_num) if q_num is not None else None,
        opiniao=a.get("selo") == "Opinião",
    )
    if not evidencias:  # lista de uma linha (aba analítica): sem o detalhe
        return view
    triagem = a.get("triagem") or {}
    origem = str(a.get("origem") or "")
    view.origem = "cobertura comparada" if origem == "cobertura comparada" else "matéria" if origem else ""
    view.genero = GENERO.get(str(a.get("genero")), "")
    view.relevancia = RELEVANCIA.get(str(triagem.get("relevancia_politica")), "")
    view.justificativa = _texto(triagem.get("justificativa"))
    view.motivos = "; ".join(_texto(m) for m in a.get("motivos_confianca") or [] if _texto(m))
    if isinstance(a.get("palavras"), int):
        view.palavras = contagem(a["palavras"], "palavra", "palavras")
    view.cortado = bool(a.get("cortado"))
    enquadramento = a.get("enquadramento") if isinstance(a.get("enquadramento"), dict) else {}
    view.enquadramento = [
        (nome, _valor_moral(_texto(enquadramento.get(k))) if k == "motor_moral" else _texto(enquadramento.get(k)))
        for k, nome in ENQUADRAMENTO.items()
        if _texto(enquadramento.get(k))
    ]
    view.checklist = _checklist(qualidade)
    view.fontes = _fontes(a)
    saldo = a.get("saldo_fontes") if isinstance(a.get("saldo_fontes"), dict) else {}
    esq, dir_ = int(saldo.get("esquerda") or 0), int(saldo.get("direita") or 0)
    if esq or dir_:
        view.fala = f"Fala direta de fontes de esquerda: {contagem(esq, 'palavra', 'palavras')} · de direita: {contagem(dir_, 'palavra', 'palavras')}"
    view.inversao = _texto(a.get("teste_inversao"))
    view.contribuicoes = _contribuicoes(a)
    atores = [x for x in a.get("atores") or [] if isinstance(x, dict) and _texto(x.get("nome"))]
    view.atores = " · ".join(f"{_texto(x['nome'])} ({CAMPOS.get(str(x.get('campo')), 'sem campo')})" for x in atores)
    view.descartadas = int(a.get("descartadas") or 0) if isinstance(a.get("descartadas"), int) else 0
    return view


# ── painéis do dia ───────────────────────────────────────────────────────────


@dataclass
class IndicadorView:
    rotulo: str
    valor: str
    detalhe: str = ""
    unidade: str = ""  # "/100"
    meter: float | None = None  # % de um medidor de 0 a 100 (qualidade)


@dataclass
class CriterioView:
    nome: str
    esq: int
    dir: int
    eq: int
    w_esq: float  # % da maior contagem de um lado
    w_dir: float

    @property
    def aria(self) -> str:
        partes = [f"{self.esq} à esquerda", f"{self.dir} à direita"]
        if self.eq:
            partes.append(f"{self.eq} de equilíbrio")
        return f"{self.nome}: " + ", ".join(partes)


@dataclass
class SinaisView:
    criterios: list[CriterioView]
    total: int
    textos: int  # textos com ao menos um sinal
    esq: int
    dir: int
    eq: int


@dataclass
class ItemQualidadeView:
    nome: str
    pct: int  # % de "sim" entre os textos em que o item se aplica (largura da barra)
    sim: int
    base: int

    @property
    def pct_txt(self) -> str:
        return porcento_txt(self.sim, self.base)


@dataclass
class ChecklistView:
    itens: list[ItemQualidadeView]
    textos: int  # notícias com o checklist guardado


@dataclass
class GrupoView:
    nome: str
    n: int
    valor: str
    pct: float | None
    ic_left: float | None
    ic_width: float | None
    rotulo: str
    classe: str
    q: str
    title: str


@dataclass
class RecorteView:
    titulo: str
    itens: list[GrupoView]
    mais: list[GrupoView]
    escala: list[tuple[float, str]]
    zero: float
    dominio: int


@dataclass
class OpcaoView:
    valor: str
    rotulo: str
    n: int


@dataclass
class TermometroDiaView:
    """Painel "Termômetro do dia", no fim da edição."""

    vazio: bool
    motivo: str
    gauge: GaugeView | None
    distribuicao: DistribuicaoView | None
    resumo: str  # "Leve à esquerda · T −14 na média de 11 notícias com dimensão política"
    analises: list[AnaliseView]
    analitico_href: str = ""
    base: str = ""  # "média de 52 notícias com dimensão política"
    indicadores: list[IndicadorView] = field(default_factory=list)
    sinais: SinaisView | None = None
    checklist: ChecklistView | None = None
    recortes: list[RecorteView] = field(default_factory=list)
    execucao: str = ""  # quem analisou, quantas chamadas, candidatos
    veiculos: list[OpcaoView] = field(default_factory=list)  # filtros da lista
    editorias: list[OpcaoView] = field(default_factory=list)
    com_sinais: int = 0
    inclinados: int = 0
    opiniao: int = 0
    visiveis: int = LISTA_VISIVEL


def valida(a: Any) -> bool:
    """Análise no formato da especificação, com o que a agregação lê."""
    return isinstance(a, dict) and all(
        isinstance(a.get(k), str) and a.get(k) for k in ("id", "veiculo", "editoria", "data_publicacao", "versao_calculo")
    )


def analises_validas(registro: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Análises do registro no formato da especificação (as malformadas ficam de fora)."""
    return [a for a in (registro or {}).get("analises") or [] if valida(a)]


def _noticia_com_t(a: Mapping[str, Any]) -> bool:
    return agregacao.tipo_de(a) == "noticia" and a.get("estado") == "ok" and isinstance(a.get("T"), (int, float))


def _sinais(analises: list[dict[str, Any]]) -> SinaisView:
    """Sinais que pontuaram, por critério e direção (contagem das evidências guardadas)."""
    por = {k: Counter() for k in PARAMETROS["pesos"]}
    textos = 0
    for a in analises:
        evs = [e for e in a.get("evidencias") or [] if isinstance(e, dict) and e.get("criterio") in por]
        textos += bool(evs)
        for e in evs:
            por[e["criterio"]][str(e.get("direcao") or "")] += 1
    linhas = [(k, c["esquerda"], c["direita"], c["equilibrio"]) for k, c in por.items() if sum(c.values())]
    maior = max((max(esq, dir_) for _, esq, dir_, _ in linhas), default=0)
    criterios = [
        CriterioView(
            nome_criterio(k),
            esq,
            dir_,
            eq,
            round(esq * 100 / maior, 2) if maior else 0.0,
            round(dir_ * 100 / maior, 2) if maior else 0.0,
        )
        for k, esq, dir_, eq in sorted(linhas, key=lambda l: (-(l[1] + l[2] + l[3]), list(por).index(l[0])))
    ]
    return SinaisView(
        criterios=criterios,
        total=sum(c.esq + c.dir + c.eq for c in criterios),
        textos=textos,
        esq=sum(c.esq for c in criterios),
        dir=sum(c.dir for c in criterios),
        eq=sum(c.eq for c in criterios),
    )


def _checklist_do_dia(analises: list[dict[str, Any]]) -> ChecklistView | None:
    """Em quantas notícias cada item da qualidade da apuração foi cumprido (entre as que o item se aplica)."""
    noticias = [
        a for a in analises
        if agregacao.tipo_de(a) == "noticia" and isinstance((a.get("qualidade_apuracao") or {}).get("itens"), dict)
    ]
    if not noticias:
        return None
    itens = []
    for k, nome in QUALIDADE_ITENS.items():
        respostas = [str(a["qualidade_apuracao"]["itens"].get(k)) for a in noticias]
        base = sum(r in ("sim", "nao") for r in respostas)
        if base:
            sim = sum(r == "sim" for r in respostas)
            itens.append(ItemQualidadeView(nome, _porcento(sim, base), sim, base))
    return ChecklistView(itens=itens, textos=len(noticias)) if itens else None


def _dominio_de(valores: Iterable[float | None]) -> int:
    maior = max((abs(v) for v in valores if v is not None), default=0)
    return next((d for d in DOMINIOS if maior <= d), 100)


def _recorte(titulo: str, linhas: list[dict[str, Any]], por: str, maximo: int | None = None) -> RecorteView | None:
    """T médio de cada veículo (ou editoria) nas notícias do dia (``agregacao.resumo`` de cada grupo)."""
    grupos = []
    for nome in sorted({l[por] for l in linhas}):
        s = agregacao.resumo([l for l in linhas if l[por] == nome])
        if s["n"]:
            grupos.append((nome, s))
    if not grupos:
        return None
    dominio = _dominio_de([v for _, s in grupos for v in [s["media_T"], *(s["ic95_T"] or [])]])
    grupos.sort(key=lambda g: (-g[1]["n"], g[1]["media_T"], g[0]))
    itens = []
    for nome, s in grupos:
        rotulo = s["rotulo"] or agregacao.rotulo_T(s["media_T"])
        ic = s["ic95_T"]
        ic_left = pct(ic[0], dominio) if ic else None
        q = f"qualidade {fmt_q(s['media_Q'])}" if s["media_Q"] is not None else "sem qualidade"
        itens.append(
            GrupoView(
                nome=filters.plain(nome),
                n=s["n"],
                valor=fmt_t(s["media_T"]),
                pct=pct(s["media_T"], dominio),
                ic_left=ic_left,
                ic_width=round(max(0.6, pct(ic[1], dominio) - ic_left), 2) if ic else None,
                rotulo=rotulo,
                classe=CLASSE.get(rotulo, "eq"),
                q=fmt_q(s["media_Q"]),
                title=(
                    f"{filters.plain(nome)}: T {fmt_t(s['media_T'])} ({rotulo}) em "
                    f"{contagem(s['n'], 'notícia', 'notícias')}" + (f", intervalo de 95% de {fmt_ic(ic)}" if ic else "") + f"; {q}"
                ),
            )
        )
    corte = maximo if maximo is not None else len(itens)
    meio = {25: 10, 50: 25, 100: 50}[dominio]
    escala = [(pct(v, dominio), _marca(v)) for v in (-dominio, -meio, 0, meio, dominio)]
    return RecorteView(titulo=titulo, itens=itens[:corte], mais=itens[corte:], escala=escala, zero=50.0, dominio=dominio)


def _execucao(registro: Mapping[str, Any], analises: list[dict[str, Any]]) -> str:
    """Como o termômetro rodou hoje: textos novos e reaproveitados, candidatos e quem analisou."""
    partes = []
    novas = registro.get("novas")
    if isinstance(novas, int) and novas:
        reaproveitadas = len(analises) - novas
        partes.append(
            f"{contagem(novas, 'análise nova', 'análises novas')}"
            + (f" e {contagem(reaproveitadas, 'reaproveitada', 'reaproveitadas')}" if reaproveitadas > 0 else "")
        )
    candidatos = registro.get("candidatos")
    if isinstance(candidatos, int) and candidatos:
        curtos = registro.get("textos_curtos") if isinstance(registro.get("textos_curtos"), int) else 0
        partes.append(
            f"{contagem(candidatos, 'texto candidato visto', 'textos candidatos vistos')}"
            + (f" ({contagem(curtos, 'curto demais', 'curtos demais')})" if curtos else "")
        )
    chamadas = registro.get("chamadas")
    modelo = filters.plain(str(registro.get("modelo") or ""))
    if isinstance(chamadas, int) and chamadas and modelo:
        partes.append(f"{contagem(chamadas, 'chamada', 'chamadas')} à IA ({modelo})")
    return " · ".join(partes)


def _opcoes(valores: Iterable[str]) -> list[OpcaoView]:
    contagens = Counter(v for v in valores if v)
    return [OpcaoView(v, v, n) for v, n in sorted(contagens.items(), key=lambda kv: (-kv[1], text.normalize(kv[0])))]


def termometro_dia_view(registro: Mapping[str, Any] | None) -> TermometroDiaView | None:
    """O painel do termômetro das notícias compiladas na edição (notícias; opinião à parte)."""
    if registro is None:
        return None
    analises = [a for a in analises_validas(registro) if a["versao_calculo"] == PARAMETROS["versao"]]
    if not analises:
        motivo = filters.plain(str(registro.get("motivo") or "nenhum texto analisado"))
        return TermometroDiaView(True, motivo, None, None, "", [])
    linhas = agregacao.acumular(analises)
    noticias = agregacao.resumo(linhas)
    opiniao = agregacao.resumo([l for l in linhas if l["tipo"] == "opiniao"], incluir_opiniao=True)
    veiculos = len({a["veiculo"] for a in analises})
    editorias = len({a["editoria"] for a in analises})
    texto_opiniao = ""
    if opiniao["n_total"]:
        texto_opiniao = contagem(opiniao["n_total"], "texto de opinião", "textos de opinião")
        if opiniao["media_T"] is not None:
            texto_opiniao += f" (T {fmt_t(opiniao['media_T'])}, à parte)"
        else:
            texto_opiniao += " à parte"
    fora = " · ".join(
        p
        for p in (
            f"{noticias['n_nao_se_aplica']} sem dimensão política" if noticias["n_nao_se_aplica"] else "",
            contagem(noticias["n_inconclusivo"], "inconclusiva", "inconclusivas") if noticias["n_inconclusivo"] else "",
            texto_opiniao,
        )
        if p
    )
    com_t = [a for a in analises if _noticia_com_t(a)]
    medidor = gauge(
        noticias["media_T"],
        noticias["ic95_T"],
        noticias["rotulo"],
        descricao="Média das notícias do dia",
        valores=[a["T"] for a in com_t],
    )
    base = contagem(noticias["n"], "notícia com dimensão política", "notícias com dimensão política")
    if medidor is not None:
        resumo = f"{medidor.rotulo} · T {medidor.valor} na média de {base}"
    else:
        resumo = "Nenhuma notícia com dimensão política e texto suficiente para o termômetro"

    sinais = _sinais([a for a in analises if agregacao.tipo_de(a) == "noticia"])
    indicadores = [
        IndicadorView(
            "Textos analisados",
            text.format_number_pt(len(analises), 0),
            f"{contagem(veiculos, 'veículo', 'veículos')} · {contagem(editorias, 'editoria', 'editorias')}",
        ),
        IndicadorView(
            "Notícias com T",
            text.format_number_pt(noticias["n"], 0),
            fora or "todos os textos analisados",
        ),
        IndicadorView(
            "Qualidade da apuração",
            fmt_q(noticias["media_Q"]),
            f"{noticias['rotulo_Q']} · média das notícias" if noticias["rotulo_Q"] else "sem avaliação",
            unidade="/100" if noticias["media_Q"] is not None else "",
            meter=float(arredondar(noticias["media_Q"])) if noticias["media_Q"] is not None else None,
        ),
        IndicadorView(
            "Sinais que pontuaram",
            text.format_number_pt(sinais.total, 0),
            (
                f"em {contagem(sinais.textos, 'notícia', 'notícias')}: {sinais.esq} à esquerda · {sinais.dir} à direita"
                + (f" · {sinais.eq} de equilíbrio" if sinais.eq else "")
            )
            if sinais.total
            else "nenhum tratamento desigual confirmado",
        ),
    ]
    recortes = [
        r
        for r in (
            _recorte("Por veículo", linhas, "veiculo", RECORTE_MAX),
            _recorte("Por editoria", linhas, "editoria"),
        )
        if r is not None
    ]
    ordem = {"ok": 0, "inconclusivo": 1, "nao_se_aplica": 2}
    views = sorted(
        (analise_view(a) for a in analises),
        key=lambda v: (ordem.get(v.estado, 3), -abs(v.t_num) if v.t_num is not None else 0.0, -v.sinais, v.veiculo),
    )
    return TermometroDiaView(
        vazio=False,
        motivo="",
        gauge=medidor,
        distribuicao=distribuicao(noticias["rotulos"], fora=fora),
        resumo=resumo,
        analises=views,
        base=f"média de {base}",
        indicadores=indicadores,
        sinais=sinais,
        checklist=_checklist_do_dia(analises),
        recortes=recortes,
        execucao=_execucao(registro, analises),
        veiculos=_opcoes(v.veiculo for v in views),
        editorias=_opcoes(v.editoria for v in views),
        com_sinais=sum(1 for v in views if v.sinais),
        inclinados=sum(1 for v in views if v.estado == "ok" and v.classe not in ("eq", "na")),
        opiniao=sum(1 for v in views if v.opiniao),
    )


def todas_validas(registros: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Todas as análises do histórico (uma por texto), só as bem formadas."""
    return [a for a in historico.todas_as_analises(registros) if valida(a)]
