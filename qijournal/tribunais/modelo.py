"""O modelo da aba Tribunais: o registro de um processo, os arquivos de ``data/tribunais/`` e as
réguas de ``config/tribunais.yaml``.

Três partes:

1. **O registro normalizado** (:class:`Processo` e o que ele carrega): o mesmo para o STF e o
   STJ. Vive em memória e no cache da coleta, nunca no git. ``stj.py`` e ``stf.py`` o montam;
   ``areas.py`` preenche área, subárea e recortes; ``partes.py`` tipifica as partes; ``contas.py``
   soma.
2. **Os arquivos de saída** (:class:`Meta`, :class:`Achados`, :class:`Painel`,
   :class:`Dossie`, :class:`MiniFolha`): o que ``dados.py`` grava em ``data/tribunais/`` e a
   página lê. O contrato completo, campo a campo e com exemplos, está em
   ``scratchpad/plano/contrato.md`` (a cópia de trabalho desta fase); aqui ficam os tipos.
3. **As réguas** (:class:`Regras`, :func:`carregar_regras`): áreas, subáreas, recortes,
   composição dos dois tribunais, limiares, fontes e DataJud, lidos de
   ``config/tribunais.yaml``. Erro de configuração derruba a leitura com o caminho da chave.

Convenções de todos os arquivos: datas ``AAAA-MM-DD``; instantes ``AAAA-MM-DDTHH:MM:SSZ``
(UTC); partes de um todo como fração de 0 a 1 com 4 casas (``0.0871``); razões com 2 casas;
contagens inteiras; chaves e listas em ordem estável. Pessoa física e MEI nunca têm nome; um
processo em segredo de justiça conta nos números e nunca aparece em lista.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, NamedTuple, TypedDict

import yaml

from . import VERSAO

ARQUIVO = "tribunais.yaml"  # em <raiz>/config/

Trib = Literal["stf", "stj"]
Polo = Literal["ativo", "passivo", "outro"]
TipoParte = Literal["pj", "ente", "pf", "autoridade", "qualificador", "desconhecido"]
# Como a área foi decidida (areas.py): pela classe, pelo assunto principal, pelo primeiro assunto de
# mérito da lista (principal só processual), só processual, sem assunto, ou pelo ramo em texto (STF,
# quando o caminho não casa com a TPU).
AreaPor = Literal["classe", "principal", "lista", "so_processual", "sem_assunto", "ramo"]
# Onde o processo está (o "local" resumido): no gabinete do relator (= na mesa), em outro gabinete,
# no cartório (coordenadorias e gerências: prazo, intimação, publicação), com o MP para parecer, na
# Presidência ou na triagem, remetido ao STF, ou outro.
Onde = Literal["gabinete", "outro_gabinete", "cartorio", "mp", "presidencia", "stf", "outro"]


# ── texto ────────────────────────────────────────────────────────────────────────────────────

_ASPAS = str.maketrans({c: " " for c in "\"'“”‘’«»`´"})


def normalizar(texto: str | None) -> str:
    """Forma de comparação de nomes: entidades HTML desfeitas, sem acento, maiúsculas, sem aspas,
    espaços colapsados ("Crimes de &quot;Lavagem&quot;" → ``CRIMES DE LAVAGEM``)."""
    s = html.unescape(texto or "")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.translate(_ASPAS).upper()
    return re.sub(r"\s+", " ", s).strip()


def slug(texto: str) -> str:
    """``"Nancy Andrighi"`` → ``nancy-andrighi`` (o fim do id de um ministro)."""
    s = re.sub(r"[^a-z0-9]+", "-", normalizar(texto).lower())
    return s.strip("-")


# Prefixos que as fontes põem antes do nome: "MIN.", "MINISTRO", "GABINETE DO MINISTRO",
# "GABINETE DA DESEMBARGADORA CONVOCADA DO TJPB"; e o parêntese "(Desembargador Convocado do TJMG)".
_PREFIXO_NOME = re.compile(
    r"^(?:GABINETE\s+(?:D[OA]\s+)?)?"
    r"(?:(?:MINISTR[OA]|MIN\.?|DESEMBARGADOR[A]?\s+CONVOCAD[OA](?:\s+D[OA])?\s+[A-Z]{2,6})\s+)?"
)


def chave_nome(texto: str | None) -> str:
    """A chave de comparação de um nome de relator ou de gabinete: ``"GABINETE DO MINISTRO RAUL
    ARAÚJO FILHO"`` → ``RAUL ARAUJO FILHO``; ``"Luís Carlos Gambogi (Desembargador Convocado do
    TJMG)"`` → ``LUIS CARLOS GAMBOGI``; ``"MIN. ANDRÉ MENDONÇA"`` → ``ANDRE MENDONCA``."""
    s = normalizar(re.sub(r"\([^)]*\)", " ", texto or ""))
    s = _PREFIXO_NOME.sub("", s)
    s = re.sub(r"[^A-Z0-9|ª º]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ── 1. o registro normalizado (em memória e no cache; nunca no git) ──────────────────────────


class Assunto(TypedDict):
    """O assunto principal. STJ: ``codigoAssuntoCNJ`` (com o prefixo 9 tirado quando o código
    inteiro não existe). STF: o caminho em texto da primeira linha de "Assuntos", casado com a TPU
    (``tpu.casar_caminho``); ``codigo`` é o nó mais profundo casado."""

    codigo: int | None
    nome: str | None  # nome na TPU (não o da fonte)
    caminho: str | None  # "DIREITO CIVIL › Obrigações › Espécies de Contratos › Contratos Bancários"
    profundidade: int  # STJ: nível do código na TPU (1 = raiz); STF: níveis casados (0 = nada)
    texto: str | None  # STF: o caminho como a fonte escreveu, sem o "1 - "; STJ: None


class Parte(TypedDict):
    """Uma parte. ``nome`` é ``None`` para pessoa física, MEI (CNPJ com CPF no nome) e parte em
    segredo: o nome nunca é guardado. ``polo`` é ``None`` para autoridade e qualificador (não são
    litigantes). No STJ e no STF, "ativo" é quem recorre ou pede; "passivo", quem responde."""

    polo: Polo | None
    papel: str  # como a fonte escreve: "AGRAVANTE", "RECDO.(A/S)", "IMPETRADO"...
    nome: str | None
    tipo: TipoParte
    grupo: str | None  # id do grupo de litigante (config/tribunais_partes.yaml) ou "cnpj-<raiz>"; None para pf
    cnpj_raiz: str | None  # 8 dígitos (STJ); None no STF (a fonte não traz CNPJ)


class Datas(TypedDict):
    autuacao: str | None  # chegada ao tribunal (STJ: dataRecebimento); STF: None (só o ano)
    ano_autuacao: int | None  # STJ: o ano de autuacao; STF: "Ano de Data Autuação"
    distribuicao: str | None  # STJ: dataUltimaDistribuicao; STF: None
    ultima_decisao: str | None  # STJ: dataUltimaDecisao (nula em 55%); STF: None
    andamento: str | None  # STF: "Data Andamento Atual" (serial do Excel convertido); STJ: None
    localizacao: str | None  # STF: "Data Localização Atual" (dia); STJ: None


class Flags(TypedDict, total=False):
    """Marcas do processo. Ausente = a fonte não informa (o STJ não tem vista nem pauta aqui)."""

    liminar: bool  # STJ pedidoDeLiminar == SIM; STF "**Medida liminar/Cautelar pendente" == Sim
    tutela: bool  # STF: tutela antecipada ou provisória pendente
    sobrestado: bool  # STJ sobrestado != N; STF "Sobrestado" == Sim
    motivo_sobrestamento: str | None  # STJ: "T" repetitivo, "RG", "C" controvérsia, "I" IAC, "ST" sem motivo
    vista: bool  # STF "Com pedido de vista"
    pauta: bool  # STF "Em mesa/pauta"
    sem_decisao: bool  # STF "Sem nenhuma decisão"
    sem_decisao_final: bool  # STF "Sem decisão final"
    recurso_interno: bool  # STF "Recurso interno pendente"
    ultimo_recurso: str | None  # STF "Sigla do Último Recurso" ("ARE-AgR")
    acordao_pendente: bool  # STF "Acórdão pendente de publicação"
    preferencia_criminal: bool  # STF


class Origem(TypedDict):
    uf: str | None
    orgao: str | None  # STJ "origem"; STF "Órgão Origem"


class Processo(TypedDict):
    """Um processo em tramitação, como ``stj.py`` e ``stf.py`` o entregam (``area`` e afins
    preenchidos por ``areas.classificar``). Campo a campo em ``contrato.md`` §1."""

    trib: Trib
    id: str  # "stj-REsp-2123456", "stf-ARE-1633374": <trib>-<classe>-<número sem pontos>
    classe: str  # sigla usual do tribunal, sem espaços ("REsp", "AREsp", "ARE", "ADI")
    numero: str  # número na classe, só dígitos
    cnj: str | None  # número único, 20 dígitos
    registro: str | None  # STJ numeroRegistro (o link oficial usa); STF: None
    relator: str | None  # nome de exibição (o da composição; senão como a fonte escreve)
    relator_id: str | None  # id da composição ("stj-nancy-andrighi") ou None
    fora: str | None  # id da linha fora da grade (composicao.<trib>.fora_da_grade) quando relator_id é None
    orgao: str | None  # STJ: codigoOrgaoJulgador sem espaços (T1..T6, S1..S3, CE); STF: None (a planilha não traz)
    local: str | None  # texto oficial do local atual
    onde: Onde
    na_mesa: bool  # o local é o gabinete do relator (composicao.na_mesa)
    area: str  # id da macroárea (config: areas)
    area_por: AreaPor
    subarea: str | None  # "<área>.<id>" ou "<área>.outros"; None nas áreas sem subárea
    recortes: list[str]  # ids de config: recortes
    tambem_toca: list[str]  # outras áreas de mérito tocadas pela lista de assuntos (não somam)
    assunto: Assunto
    assuntos: list[int]  # todos os códigos TPU (principal primeiro; sem repetição)
    segredo: bool
    partes: list[Parte]
    datas: Datas
    andamento: str | None  # STF "Andamento Atual"; STJ: None
    sem_decisao_dias: int | None  # STJ: data do retrato − max(distribuição, última decisão); STF: None
    concluso_dias: int | None  # STF: dias desde "Data Andamento Atual" quando o andamento é "Conclusos ao(à) Relator(a)"
    local_dias: int | None  # STF: dias desde "Data Localização Atual"; STJ: None
    flags: Flags
    origem: Origem


class Classificacao(TypedDict):
    """O que ``areas.Classificador.classificar`` devolve para um processo."""

    area: str
    por: AreaPor
    codigo: int | None  # o código TPU que decidiu a área (None: pela classe ou sem assunto)
    subarea: str | None
    recortes: list[str]
    tambem_toca: list[str]


# ── 2. os arquivos de data/tribunais/ ────────────────────────────────────────────────────────


class Fonte(TypedDict, total=False):
    """Uma fonte em ``meta.json`` → ``fontes``. Muda só quando chega dado novo ou a fonte falha
    (o aviso tem a data, não a hora: falhar de novo no mesmo dia não regrava o arquivo)."""

    nome: str  # "STJ · acervo em tramitação"
    trib: str  # "stj", "stf" ou "cnj"
    url: str  # a página da fonte para o leitor
    data_dados: str | None  # a data que a própria fonte declara (campo Data, nome do arquivo, "Data da última atualização")
    coletado_em: str | None  # instante (UTC) da coleta que trouxe os dados em uso
    ok: bool | None  # a última tentativa deu certo; False = falhou e ficam os dados de data_dados; None = desligada
    aviso: str | None  # frase para o leitor quando ok é False ou a fonte está atrasada
    registros: int | None  # quantos registros os dados em uso têm
    velho_dias: int  # passou disto desde data_dados, a página acende o alerta
    atribuicao: str | None  # "Fonte: STJ, Portal de Dados Abertos (CC-BY)"
    atraso_dias: int | None  # íntegras do STJ: dias entre o último arquivo e o dia da coleta
    ativo: bool  # só no datajud


class Meta(TypedDict, total=False):
    v: int  # VERSAO
    hoje: str  # data local (America/Sao_Paulo) da execução que gravou
    atualizado_em: str  # instante da última mudança de número (= o maior coletado_em entre as fontes ok)
    proxima_execucao: str  # antes disto nada novo é esperado (o job "decidir" compara como texto)
    fontes: dict[str, Fonte]
    cobertura: dict[str, Any]
    reguas: dict[str, Any]
    avisos: list[str]
    correcoes: list[dict[str, str]]


class Achado(TypedDict, total=False):
    tipo: str  # heranca, mesa_antiga, fora_do_padrao, litigante, tema, tema_quente, vista, entrou
    trib: str
    peso: float
    selo: str  # MESA, ÁREA, FILA, VISTA, PARTE, TEMA
    titulo: str
    texto: str
    alvo: str  # endereço dentro da página: "#v=ministros&t=stj&min=stj-nancy-andrighi"
    ministro: str | None
    prova: dict[str, str]  # {fonte, data, url}


class Achados(TypedDict):
    v: int
    hoje: str
    achados: list[Achado]


class ParteFolha(TypedDict):
    polo: Polo
    tipo: TipoParte
    grupo: str | None  # só para pj e ente; nunca nome


class MiniFolha(TypedDict, total=False):
    """Um processo individual numa lista (mais antigos, em pauta, em vista). Nunca de processo em
    segredo; sem nome de parte (só polo, tipo e o grupo do litigante para PJ e ente)."""

    id: str
    classe: str
    numero: str
    cnj: str | None
    registro: str | None
    uf: str | None
    area: str
    subarea: str | None
    assunto: dict[str, Any]  # {codigo, nome, caminho}
    relator_id: str | None
    local: str | None
    na_mesa: bool
    datas: dict[str, Any]  # {autuacao, ano_autuacao, distribuicao, ultima_decisao, andamento}
    sem_decisao_dias: int | None
    concluso_dias: int | None
    flags: dict[str, Any]
    partes: list[ParteFolha]
    link: str  # página oficial do processo (para o leitor; não é coletada)


class AreaCelula(TypedDict):
    """Uma célula da matriz ministro × área."""

    n: int  # processos da área no acervo do ministro
    p: float  # n / acervo do ministro
    pares: float | None  # a parte da área nos pares (STJ: a Turma sem ele; STF: o tribunal sem ele e sem a Presidência)
    razao: float | None  # p / pares
    marca: int  # 1 = ▲ acima dos pares, −1 = ▽ abaixo, 0 = sem marca (não passou no teste ou no piso, ou não comparável)


class Idade(TypedDict, total=False):
    mediana: int | None
    p25: int | None
    p75: int | None
    p90: int | None
    max: int | None
    mais_180: int
    mais_365: int


class ResumoMinistro(TypedDict, total=False):
    """Um ministro no painel (``stj.json``/``stf.json`` → ``ministros``)."""

    id: str
    nome: str
    genero: str
    grupo: str  # id do grupo da matriz (s1, s2, s3, pr; t1, t2, pr)
    turma: str | None
    secao: str | None
    cargos: list[str]
    convocado: dict[str, Any] | None  # {tribunal, vaga_de, desde}
    selos: list[str]  # convocado, heranca, recem_chegado, sem_distribuicao, poucos_processos
    comparavel: bool
    processos: int
    na_mesa: int
    onde: dict[str, int]  # contagem por Onde
    segredo: int
    sobrestados: int
    liminar: int
    ente_publico: int
    sem_decisao: Idade  # STJ: dos processos na mesa; STF: concluso_dias dos conclusos
    no_tribunal: Idade  # dias desde a autuação (STF: anos × 365, só mediana e quartis)
    areas: dict[str, AreaCelula]
    subareas: list[list[Any]]  # [[id, n, p], ...] as N maiores
    partes: list[list[Any]]  # [[grupo, n], ...] os N litigantes que mais aparecem
    mais_antigos: list[MiniFolha]
    entradas: dict[str, int] | None
    pauta: dict[str, int] | None
    decisoes: dict[str, int] | None
    temas: int | None


class Painel(TypedDict, total=False):
    """``data/tribunais/stj.json`` e ``stf.json``: o que a página mostra sem script."""

    v: int
    trib: str
    nome: str
    sigla: str
    data_dados: str | None
    universo: dict[str, int]
    grupos: list[dict[str, Any]]
    ministros: list[ResumoMinistro]
    fora_da_grade: list[dict[str, Any]]
    areas: list[dict[str, Any]]
    recortes: list[dict[str, Any]]
    partes: dict[str, Any]
    fila: dict[str, Any]
    entradas: dict[str, Any] | None
    pautas: dict[str, Any] | None
    decisoes: dict[str, Any] | None
    temas: dict[str, Any] | None
    valor: dict[str, Any]


class Dossie(TypedDict, total=False):
    """``data/tribunais/ministros/<id>.json``: a mesa de um ministro, lida quando o cartão abre."""

    v: int
    id: str
    trib: str
    data_dados: str | None
    ministro: dict[str, Any]
    resumo: ResumoMinistro
    areas: list[dict[str, Any]]
    subareas: list[dict[str, Any]]
    recortes: list[dict[str, Any]]
    classes: list[list[Any]]
    onde: dict[str, int]
    idade: dict[str, Any]
    mais_antigos: list[MiniFolha]
    em_pauta: list[dict[str, Any]]
    em_vista: list[dict[str, Any]]
    partes: dict[str, Any]
    entradas: dict[str, Any] | None
    decisoes: dict[str, Any] | None
    temas: list[dict[str, Any]] | None


# ── 3. as réguas (config/tribunais.yaml) ─────────────────────────────────────────────────────


class ErroDeConfig(ValueError):
    """``config/tribunais.yaml`` com chave obrigatória ausente ou valor inválido."""


@dataclass(frozen=True)
class Area:
    id: str
    nome: str
    nome_curto: str
    cor: tuple[str, str] | None  # (claro, escuro); None = hachura (sem_assunto)
    texto: str | None  # cor do texto sobre a área
    raizes: tuple[int, ...] = ()
    nos: tuple[int, ...] = ()
    classes: tuple[str, ...] = ()
    residual: bool = False
    # A classe cede ao mérito: estas classes (HC, RHC) vão para a área da classe salvo quando o
    # assunto de mérito do processo é de uma destas áreas (o HC da prisão civil por alimentos é
    # Família, não Penal).
    cede_classes: tuple[str, ...] = ()
    cede_areas: tuple[str, ...] = ()


@dataclass(frozen=True)
class Subarea:
    id: str  # "<área>.<id>"
    area: str
    nome: str
    codigos: tuple[int, ...]


@dataclass(frozen=True)
class Recorte:
    id: str
    nome: str
    codigos: tuple[int, ...]
    porque: str = ""


@dataclass(frozen=True)
class Ministro:
    id: str
    trib: str
    nome: str
    nome_completo: str
    genero: str  # "f" ou "m"
    turma: str | None = None
    secao: str | None = None
    corte_especial: bool = False
    cargos: tuple[str, ...] = ()
    grade: bool = True  # False: fora da matriz e das médias (Presidência, Vice, Corregedoria)
    posse: str | None = None
    turma_desde: str | None = None
    convocado: Mapping[str, Any] | None = None  # {tribunal, vaga_de}
    gabinete: str | None = None
    aliases: tuple[str, ...] = ()
    codigo_ata: int | None = None

    def recem_chegado(self, hoje: date, meses: int) -> bool:
        """Na Turma (ou convocado) há menos de ``meses`` meses: aparece com selo e sem marca."""
        if not self.turma_desde:
            return False
        desde = date.fromisoformat(self.turma_desde)
        return (hoje - desde).days < meses * 30.44


@dataclass(frozen=True)
class Grupo:
    id: str
    nome: str
    descricao: str
    turmas: tuple[str, ...]


@dataclass(frozen=True)
class LinhaFora:
    """Uma linha fora da grade (Presidência, sem relator, resíduos...)."""

    id: str
    rotulo: str
    nota: str
    relatores: tuple[str, ...] = ()
    prefixos: tuple[str, ...] = ()


class Relator(NamedTuple):
    """O que :meth:`Composicao.relator` acha para um nome de relator."""

    ministro: Ministro | None
    fora: str | None  # id da linha fora da grade quando não é ministro
    conhecido: bool  # False: nome fora do mapa (vai para "residuos" e para a cobertura)


@dataclass(frozen=True)
class Composicao:
    trib: str
    data: str
    fonte: str
    grupos: tuple[Grupo, ...]
    orgaos: Mapping[str, str]
    ministros: tuple[Ministro, ...]
    fora_da_grade: tuple[LinhaFora, ...]
    saiu: tuple[Mapping[str, Any], ...] = ()
    cadeiras: int | None = None
    # Quem presidiu o tribunal e quando (``[{id, de, ate}]``, datas de publicação): o que a pessoa
    # assinou como Presidente é da Presidência, nunca do cartão dela (as íntegras do STJ dão o nome,
    # não o cargo).
    presidencias: tuple[Mapping[str, Any], ...] = ()
    _indice: dict[str, Ministro] = field(default_factory=dict, compare=False, repr=False)
    _saiu: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        for m in self.ministros:
            for nome in (m.nome, m.nome_completo, m.gabinete, *m.aliases):
                chave = chave_nome(nome)
                if not chave:
                    continue
                outro = self._indice.get(chave)
                if outro is not None and outro.id != m.id:
                    raise ErroDeConfig(f"composicao.{self.trib}: o nome {nome!r} casa com {outro.id} e {m.id}")
                self._indice[chave] = m
        for s in self.saiu:
            for nome in (s.get("nome"), *(s.get("aliases") or ())):
                if nome:
                    self._saiu[chave_nome(nome)] = str(s.get("nome"))

    def ministro(self, ministro_id: str) -> Ministro | None:
        return next((m for m in self.ministros if m.id == ministro_id), None)

    def saida(self, texto: str | None) -> tuple[str, str] | None:
        """Quem saiu (``saiu`` no config), pelo nome como a fonte o escreve: ``(nome, motivo)``
        com o nome canônico (``"Carlos Cini Marchionatti (DESEMBARGADOR Convocado Tjrs)"`` →
        ``("Carlos Cini Marchionatti", "convocação encerrada")``); ``None`` fora da lista."""
        nome = self._saiu.get(chave_nome(texto)) if texto else None
        if nome is None:
            return None
        motivo = next((str(s.get("motivo") or "") for s in self.saiu if str(s.get("nome")) == nome), "")
        return nome, motivo

    def por_nome(self, texto: str | None) -> Ministro | None:
        """O ministro pelo nome como qualquer fonte o escreve (``"MIN. ANDRÉ MENDONÇA"``,
        ``"NANCY ANDRIGHI"``, ``"Luís Carlos Gambogi (Desembargador Convocado do TJMG)"``)."""
        return self._indice.get(chave_nome(texto)) if texto else None

    def do_gabinete(self, local: str | None) -> Ministro | None:
        """O dono do gabinete, se ``local`` é um gabinete de ministro (``"GABINETE DO MINISTRO RAUL
        ARAÚJO FILHO"``, ``"GABINETE MINISTRO ANDRÉ MENDONÇA"``); senão ``None``."""
        if not local or not normalizar(local).startswith("GABINETE"):
            return None
        return self._indice.get(chave_nome(local))

    def na_mesa(self, relator_id: str | None, local: str | None) -> bool:
        """O processo está no gabinete do próprio relator."""
        dono = self.do_gabinete(local)
        return bool(relator_id and dono is not None and dono.id == relator_id)

    def relator(self, texto: str | None) -> Relator:
        """Ministro da composição, linha fora da grade ou resíduo (quem saiu; nome desconhecido)."""
        m = self.por_nome(texto)
        if m is not None:
            return Relator(m, None, True)
        alvo = normalizar(texto)
        for linha in self.fora_da_grade:
            if any(alvo == normalizar(r) for r in linha.relatores) or any(
                alvo.startswith(normalizar(p)) for p in linha.prefixos if p
            ):
                return Relator(None, linha.id, True)
        conhecido = chave_nome(texto) in self._saiu
        return Relator(None, "residuos", conhecido)

    def presidente_em(self, dia: str | None) -> str | None:
        """O id de quem presidia o tribunal em ``dia`` (``"AAAA-MM-DD"``), pelas ``presidencias``."""
        if not dia:
            return None
        for p in self.presidencias:
            if str(p.get("de") or "") <= dia and (not p.get("ate") or dia <= str(p["ate"])):
                return str(p.get("id"))
        return None

    def grupo_de(self, m: Ministro) -> str:
        """O grupo da matriz: o que contém a Turma do ministro; quem está fora da grade vai para o
        grupo sem Turma (o último da lista)."""
        if m.grade and m.turma:
            for g in self.grupos:
                if m.turma in g.turmas:
                    return g.id
        return self.grupos[-1].id

    def da_grade(self) -> tuple[Ministro, ...]:
        return tuple(m for m in self.ministros if m.grade)


@dataclass(frozen=True)
class Regras:
    """``config/tribunais.yaml`` lido. ``limiares``, ``fontes`` e ``datajud`` ficam como no YAML
    (dicionários), com os valores padrão abaixo preenchidos quando faltam."""

    versao: int
    areas: tuple[Area, ...]
    subareas: tuple[Subarea, ...]
    recortes: tuple[Recorte, ...]
    recortes_economicos: tuple[str, ...]
    composicao: Mapping[str, Composicao]
    limiares: Mapping[str, Any]
    fontes: Mapping[str, Any]
    datajud: Mapping[str, Any]

    def area(self, area_id: str) -> Area:
        for a in self.areas:
            if a.id == area_id:
                return a
        raise KeyError(area_id)

    @property
    def ids_areas(self) -> tuple[str, ...]:
        return tuple(a.id for a in self.areas)

    def subareas_de(self, area_id: str) -> tuple[Subarea, ...]:
        return tuple(s for s in self.subareas if s.area == area_id)

    def nome_subarea(self, subarea_id: str) -> str:
        """Nome de exibição; ``"<área>.outros"`` → ``"Outros de <área>"``."""
        for s in self.subareas:
            if s.id == subarea_id:
                return s.nome
        area_id = subarea_id.split(".", 1)[0]
        try:
            return f"Outros de {self.area(area_id).nome_curto}"
        except KeyError:
            return subarea_id

    def pausa_do_host(self, host: str) -> float:
        hosts = self.fontes.get("hosts") or {}
        return float((hosts.get(host) or {}).get("pausa_s", self.fontes.get("pausa_s", 1.0)))

    def reguas_para_meta(self) -> dict[str, Any]:
        """A cópia das réguas que vai em ``meta.json`` → ``reguas`` (o "Como ler" lê dali)."""
        return {
            "areas": [
                {
                    "id": a.id,
                    "nome": a.nome,
                    "nome_curto": a.nome_curto,
                    "cor": list(a.cor) if a.cor else None,
                    "texto": a.texto,
                    "regra": {"classes": list(a.classes), "nos": list(a.nos), "raizes": list(a.raizes), "residual": a.residual,
                              **({"cede": {"classes": list(a.cede_classes), "areas": list(a.cede_areas)}} if a.cede_classes else {})},
                }
                for a in self.areas
            ],
            "subareas": [{"id": s.id, "area": s.area, "nome": s.nome, "codigos": list(s.codigos)} for s in self.subareas],
            "recortes": [{"id": r.id, "nome": r.nome, "codigos": list(r.codigos), "porque": r.porque} for r in self.recortes],
            "recortes_economicos": list(self.recortes_economicos),
            "limiares": _congelado_para_json(self.limiares),
        }


LIMIARES_PADRAO: dict[str, Any] = {
    "mesa_dias": [180, 365],
    "idade_faixas_meses": [6, 12, 24, 36, 60],
    "comparacao": {"confianca": 0.99, "razao": [0.80, 1.25], "pontos": 2, "minimo_area": 30, "minimo_acervo": 1000,
                   "pares": {"stj": "turma", "stf": "tribunal"}},
    "nao_comparavel_meses": 6,
    "heranca_parte": 0.30,
    "distribuicao_reduzida": 0.5,
    "classes_recurso": {"stj": ["REsp", "AREsp", "RHC", "RMS", "EREsp", "EAREsp", "RO", "Ag", "EAg"],
                        "stf": ["RE", "ARE", "RHC", "RMS", "AI", "RO"]},
    "nome_parte_tribunal": 20,
    "nome_parte_ministro": 5,
    "achado_heranca": 300,
    "tema_quente_vezes": 2,
    "tema_quente_minimo": 50,
    "litigante_semana_vezes": 3,
    "litigante_semana_minimo": 100,
    "vista_dias": 90,
    "recusa_arquivo_menor_que": 0.85,
    "recusa_arquivo_maior_que": 1.15,
    "recortes_por": "principal",
    "rampa": [0.02, 0.05, 0.10, 0.25],
    "listas": {"painel_mais_antigos": 5, "fila_mais_antigos": 30, "dossie_mais_antigos": 15, "dossie_em_pauta": 20,
               "partes_tribunal": 30, "partes_ministro": 10, "partes_painel_ministro": 3, "subareas_painel": 5,
               "assuntos_por_area": 10, "partes_por_area": 5},
    "janelas": {"entradas_dias": 365, "entradas_recentes_dias": 30, "decisoes_dias": 365},
    "pesos_achados": {"heranca": 5, "mesa_antiga": 4, "fora_do_padrao": 3, "litigante": 3, "tema": 3, "tema_quente": 2,
                      "vista": 2, "entrou": 1},
}


def _mesclar(padrao: Mapping[str, Any], dado: Mapping[str, Any] | None) -> dict[str, Any]:
    saida = dict(padrao)
    for k, v in (dado or {}).items():
        if isinstance(v, Mapping) and isinstance(saida.get(k), Mapping):
            saida[k] = _mesclar(saida[k], v)
        else:
            saida[k] = v
    return saida


def _congelado_para_json(valor: Any) -> Any:
    if isinstance(valor, Mapping):
        return {str(k): _congelado_para_json(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_congelado_para_json(v) for v in valor]
    return valor


def _exigir(bruto: Mapping[str, Any], chave: str, onde: str) -> Any:
    if chave not in bruto or bruto[chave] in (None, ""):
        raise ErroDeConfig(f"config/{ARQUIVO}: falta {onde}.{chave}")
    return bruto[chave]


def _inteiros(valores: Iterable[Any] | None, onde: str) -> tuple[int, ...]:
    try:
        return tuple(int(v) for v in (valores or ()))
    except (TypeError, ValueError) as exc:
        raise ErroDeConfig(f"config/{ARQUIVO}: {onde} tem código que não é número ({exc})") from exc


def _data(valor: Any, onde: str) -> str | None:
    if valor in (None, ""):
        return None
    texto = valor.isoformat() if isinstance(valor, date) else str(valor)
    try:
        date.fromisoformat(texto)
    except ValueError as exc:
        raise ErroDeConfig(f"config/{ARQUIVO}: {onde} não é data AAAA-MM-DD ({texto!r})") from exc
    return texto


def _composicao(trib: str, bruto: Mapping[str, Any]) -> Composicao:
    onde = f"composicao.{trib}"
    grupos = tuple(
        Grupo(id=str(_exigir(g, "id", f"{onde}.grupos")), nome=str(g.get("nome") or g["id"]),
              descricao=str(g.get("descricao") or ""), turmas=tuple(g.get("turmas") or ()))
        for g in (_exigir(bruto, "grupos", onde) or ())
    )
    ministros = []
    for i, m in enumerate(_exigir(bruto, "ministros", onde)):
        local = f"{onde}.ministros[{i}]"
        mid = str(_exigir(m, "id", local))
        if not mid.startswith(f"{trib}-"):
            raise ErroDeConfig(f"config/{ARQUIVO}: {local}.id deve começar com {trib}-")
        genero = str(m.get("genero") or "m")
        if genero not in ("f", "m"):
            raise ErroDeConfig(f"config/{ARQUIVO}: {local}.genero deve ser f ou m")
        convocado = m.get("convocado")
        ministros.append(
            Ministro(
                id=mid, trib=trib, nome=str(_exigir(m, "nome", local)), nome_completo=str(m.get("nome_completo") or m["nome"]),
                genero=genero, turma=m.get("turma"), secao=m.get("secao"), corte_especial=bool(m.get("corte_especial")),
                cargos=tuple(m.get("cargos") or ()), grade=bool(m.get("grade", True)),
                posse=_data(m.get("posse"), f"{local}.posse"), turma_desde=_data(m.get("turma_desde"), f"{local}.turma_desde"),
                convocado=dict(convocado) if convocado else None, gabinete=m.get("gabinete"),
                aliases=tuple(str(a) for a in (m.get("aliases") or ())),
                codigo_ata=int(m["codigo_ata"]) if m.get("codigo_ata") is not None else None,
            )
        )
    ids = [m.id for m in ministros]
    if len(ids) != len(set(ids)):
        raise ErroDeConfig(f"config/{ARQUIVO}: {onde}.ministros tem id repetido")
    fora = tuple(
        LinhaFora(id=str(_exigir(f, "id", f"{onde}.fora_da_grade")), rotulo=str(f.get("rotulo") or f["id"]),
                  nota=str(f.get("nota") or ""), relatores=tuple(str(r) for r in (f.get("relatores") or ())),
                  prefixos=tuple(str(p) for p in (f.get("prefixos") or ())))
        for f in (bruto.get("fora_da_grade") or ())
    )
    if not any(f.id == "residuos" for f in fora):
        raise ErroDeConfig(f"config/{ARQUIVO}: {onde}.fora_da_grade precisa da linha residuos")
    return Composicao(
        trib=trib, data=str(bruto.get("data") or ""), fonte=str(bruto.get("fonte") or ""), grupos=grupos,
        orgaos=dict(bruto.get("orgaos") or {}), ministros=tuple(ministros), fora_da_grade=fora,
        saiu=tuple(dict(s) for s in (bruto.get("saiu") or ())), cadeiras=bruto.get("cadeiras"),
        presidencias=tuple(
            {"id": str(_exigir(x, "id", f"{onde}.presidencias")), "de": _data(x.get("de"), f"{onde}.presidencias.de"),
             "ate": _data(x.get("ate"), f"{onde}.presidencias.ate")}
            for x in (bruto.get("presidencias") or ())
        ),
    )


def de_dict(bruto: Mapping[str, Any]) -> Regras:
    """As réguas a partir do YAML já lido (os testes montam o dicionário à mão)."""
    areas = []
    for i, a in enumerate(_exigir(bruto, "areas", "")):
        onde = f"areas[{i}]"
        cor = a.get("cor")
        if cor is not None and (len(cor) != 2 or not all(re.fullmatch(r"#[0-9a-fA-F]{6}", str(c)) for c in cor)):
            raise ErroDeConfig(f"config/{ARQUIVO}: {onde}.cor deve ser [claro, escuro] em #rrggbb")
        nome_curto = str(a.get("nome_curto") or a.get("nome") or "")
        if len(nome_curto) > 14:
            raise ErroDeConfig(f"config/{ARQUIVO}: {onde}.nome_curto passa de 14 caracteres ({nome_curto!r})")
        areas.append(
            Area(id=str(_exigir(a, "id", onde)), nome=str(_exigir(a, "nome", onde)), nome_curto=nome_curto,
                 cor=(str(cor[0]), str(cor[1])) if cor else None, texto=a.get("texto"),
                 raizes=_inteiros(a.get("raizes"), f"{onde}.raizes"), nos=_inteiros(a.get("nos"), f"{onde}.nos"),
                 classes=tuple(str(c) for c in (a.get("classes") or ())), residual=bool(a.get("residual")),
                 cede_classes=tuple(str(c) for c in ((a.get("cede_ao_merito") or {}).get("classes") or ())),
                 cede_areas=tuple(str(c) for c in ((a.get("cede_ao_merito") or {}).get("areas") or ())))
        )
    ids = [a.id for a in areas]
    for obrigatoria in ("processual", "outros", "sem_assunto"):
        if obrigatoria not in ids:
            raise ErroDeConfig(f"config/{ARQUIVO}: areas precisa da área {obrigatoria}")
    if len(ids) != len(set(ids)):
        raise ErroDeConfig(f"config/{ARQUIVO}: areas tem id repetido")
    for a in areas:
        for alvo in a.cede_areas:
            if alvo not in ids:
                raise ErroDeConfig(f"config/{ARQUIVO}: areas.{a.id}.cede_ao_merito cita a área {alvo!r}, que não existe")
    vistos: dict[int, str] = {}
    for a in areas:
        for c in (*a.raizes, *a.nos):
            if c in vistos and vistos[c] != a.id:
                raise ErroDeConfig(f"config/{ARQUIVO}: o código {c} está em {vistos[c]} e em {a.id}")
            vistos[c] = a.id
    subareas = []
    for area_id, lista in (bruto.get("subareas") or {}).items():
        if area_id not in ids:
            raise ErroDeConfig(f"config/{ARQUIVO}: subareas.{area_id} não é uma área")
        for j, s in enumerate(lista or ()):
            sid = str(_exigir(s, "id", f"subareas.{area_id}[{j}]"))
            if not sid.startswith(f"{area_id}."):
                raise ErroDeConfig(f"config/{ARQUIVO}: subareas.{area_id}[{j}].id deve começar com {area_id}.")
            subareas.append(Subarea(id=sid, area=area_id, nome=str(_exigir(s, "nome", f"subareas.{sid}")),
                                    codigos=_inteiros(s.get("codigos"), f"subareas.{sid}.codigos")))
    recortes = tuple(
        Recorte(id=str(_exigir(r, "id", "recortes")), nome=str(_exigir(r, "nome", "recortes")),
                codigos=_inteiros(r.get("codigos"), f"recortes.{r.get('id')}.codigos"), porque=str(r.get("porque") or ""))
        for r in (bruto.get("recortes") or ())
    )
    economicos = tuple(str(x) for x in (bruto.get("recortes_economicos") or ()))
    for x in economicos:
        if x not in {r.id for r in recortes}:
            raise ErroDeConfig(f"config/{ARQUIVO}: recortes_economicos cita {x}, que não é recorte")
    comp_bruta = _exigir(bruto, "composicao", "")
    composicao = {trib: _composicao(trib, comp_bruta[trib]) for trib in ("stf", "stj") if trib in comp_bruta}
    fontes = dict(bruto.get("fontes") or {})
    fontes.setdefault("robots_validade_h", 24)
    fontes.setdefault("pausa_s", 1.0)
    fontes.setdefault("recusa_espera_s", 120)
    fontes.setdefault("hosts", {})
    fontes.setdefault("proibidos", [])
    return Regras(
        versao=int(bruto.get("versao") or VERSAO),
        areas=tuple(areas),
        subareas=tuple(subareas),
        recortes=recortes,
        recortes_economicos=economicos,
        composicao=composicao,
        limiares=_mesclar(LIMIARES_PADRAO, bruto.get("limiares")),
        fontes=fontes,
        datajud=dict(bruto.get("datajud") or {"ativo": False}),
    )


def carregar_regras(raiz: Path | str | None = None) -> Regras:
    """Lê ``<raiz>/config/tribunais.yaml`` (raiz padrão: a do repositório)."""
    raiz = Path(raiz) if raiz is not None else Path(__file__).resolve().parents[2]
    caminho = raiz / "config" / ARQUIVO
    bruto = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    return de_dict(bruto)
