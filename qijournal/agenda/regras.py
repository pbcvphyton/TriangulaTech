"""As escolhas da Agenda da semana (``config/agenda.yaml``) em objetos tipados: os pesos da
régua (foro, alcance e estágio; a mídia não pontua), os cortes, os tetos, as comissões que
decidem, o frescor, o dicionário de situações da home, as palavras de área, os nomes e os
apelidos aprovados por pessoa e os feeds que não contam como imprensa.

A leitura é tolerante de ponta a ponta: sem o arquivo, com YAML ilegível ou com um valor
inválido, vale o padrão seguro escrito aqui e o problema vai para ``Regras.avisos``. Nada neste
módulo levanta exceção para quem chama :func:`carregar`: a edição nunca cai por causa dele.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from . import contrato

log = logging.getLogger(__name__)

ARQUIVO = "agenda.yaml"  # em <raiz>/config/
ROOT = Path(__file__).resolve().parent.parent.parent

# A escala da régua: três partes que somam até 85. A mídia não é parte dela.
TETO_FORO, TETO_ALCANCE, TETO_ESTAGIO = 30, 30, 25
NOTA_MAXIMA = TETO_FORO + TETO_ALCANCE + TETO_ESTAGIO

PESOS_FORO: tuple[tuple[str, int], ...] = (
    ("plenario", 30), ("corte_especial", 25), ("secao", 20), ("comissao_decide", 15), ("prazo", 15), ("turma", 10), ("comissao", 5),
)
PESOS_ALCANCE: tuple[tuple[str, int], ...] = (
    ("repercussao_geral", 30), ("repetitivo", 30), ("controle_federal", 30), ("pec", 30), ("plp", 30), ("mpv", 30), ("veto", 30),
    ("orcamento", 30), ("indicacao", 30), ("pl", 18), ("controle_estadual", 15), ("acao_originaria", 15),
    ("embargos_de_divergencia", 15), ("afetacao", 15), ("pdl", 9), ("individual", 5), ("outro", 0),
)
PESOS_ESTAGIO: tuple[tuple[str, int], ...] = (
    ("em_votacao", 25), ("prazo_vence", 25), ("retorno_de_vista", 20), ("referendo_de_cautelar", 15), ("destaque", 15),
    ("urgencia", 15), ("pauta_confirmada", 15), ("pautado", 10), ("pauta_nao_publicada", 0),
)
COMISSOES_QUE_DECIDEM: tuple[tuple[str, tuple[str, ...]], ...] = (
    (contrato.CAMARA, ("CCJC", "CFT")), (contrato.SENADO, ("CCJ", "CAE")), (contrato.CONGRESSO, ("CMO",)),
)
# Os canais dos próprios tribunais e das próprias Casas em config/sources.yaml: não são imprensa.
FEEDS_OFICIAIS: tuple[str, ...] = ("stf", "stj", "tst", "tse", "cnj", "tjdft", "tjmg", "trf6", "camara", "senado")

# Chaves do dicionário de situações que não são palavra do vocabulário do contrato.
SITUACOES_ESPECIAIS = ("prazo_mpv", "prazo_veto", "pauta_camara_nao_publicada", "fila", "dia_vazio", "resultado_tribunal", "como_pautado")
# O que nenhum texto de tela da agenda pode dizer: previsão, adjetivo e travessão.
EXPRESSOES_PROIBIDAS = (
    "vai votar", "vão votar", "vai julgar", "vão julgar", "será votad", "serão votad", "será julgad", "serão julgad",
    "deve aprovar", "deve ser aprovad", "polêmic", "pauta-bomba", "pauta bomba", "—", "–",
)
APELIDO_MINIMO_DE_PALAVRAS = 2


@dataclass(frozen=True)
class Regua:
    """Os pesos das três partes da nota (0 a 85). A mídia não pontua."""

    foro: tuple[tuple[str, int], ...] = PESOS_FORO
    alcance: tuple[tuple[str, int], ...] = PESOS_ALCANCE
    estagio: tuple[tuple[str, int], ...] = PESOS_ESTAGIO

    def pontos_foro(self, foro: str) -> int:
        return dict(self.foro).get(foro, 0)

    def pontos_alcance(self, alcance: str) -> int:
        return dict(self.alcance).get(alcance, 0)

    def pontos_estagio(self, *estagios: str) -> int:
        """O maior peso entre os estágios que valem para o item (``item.estagios``)."""
        pesos = dict(self.estagio)
        return max((pesos.get(e, 0) for e in estagios), default=0)

    @property
    def maximo(self) -> int:
        """A maior nota possível com estes pesos (85 com os do arquivo)."""
        return sum(max((p for _nome, p in parte), default=0) for parte in (self.foro, self.alcance, self.estagio))


@dataclass(frozen=True)
class Cortes:
    """A nota mínima da linha na home e na página completa."""

    home: int = 60
    pagina: int = 40


@dataclass(frozen=True)
class TetosHome:
    linhas: int = 8  # linhas de pauta na janela
    itens_por_linha: int = 2  # itens nomeados por linha; o resto vira "mais N"
    nome_ate: int = 90  # caracteres do nome de um item
    resultados: int = 2  # "Já decidido esta semana"
    fila_nomeados: int = 2  # itens nomeados da fila do Plenário da Câmara
    altura_px: int = 520
    celular_linhas_hoje_amanha: int = 4
    celular_itens_por_linha: int = 1
    celular_nome_ate: int = 60
    celular_linhas_de_texto: int = 3
    celular_altura_px: int = 600


@dataclass(frozen=True)
class TetosPagina:
    linhas: int = 60
    bytes: int = 250_000  # edicoes/agenda.html


@dataclass(frozen=True)
class TetosArquivos:
    congresso_bytes: int = 150_000  # data/congresso/agenda.json
    tribunais_bytes: int = 60_000  # data/tribunais/agenda.json


@dataclass(frozen=True)
class Tetos:
    home: TetosHome = field(default_factory=TetosHome)
    pagina: TetosPagina = field(default_factory=TetosPagina)
    arquivos: TetosArquivos = field(default_factory=TetosArquivos)


@dataclass(frozen=True)
class Frescor:
    """Quando o dado de uma fonte ganha aviso e quando some (ver :meth:`Regras.frescor_de`)."""

    aviso_dias_uteis: int = 2  # a partir daqui: "dados de DD/MM, pode ter mudado"
    some_dias: int = 4  # acima disto, em dias corridos, os itens da fonte somem (vale o limite da fonte, quando ela traz)


@dataclass(frozen=True)
class Situacao:
    """Uma entrada do dicionário da home: a frase e, quando o registro traz o detalhe (o ministro
    da vista), a frase que o usa."""

    chave: str
    texto: str
    com_detalhe: str = ""


@dataclass(frozen=True)
class Apelido:
    """Um apelido aprovado por pessoa para ligar um item à mídia, com validade."""

    item: str  # o identificador: "PLP 299/2026"
    termos: tuple[str, ...]  # 2 palavras ou mais cada; o primeiro é o que a tela mostra
    de: str
    ate: str

    def vale_em(self, dia: str) -> bool:
        return self.de <= dia[:10] <= self.ate


@dataclass(frozen=True)
class Regras:
    """Tudo o que ``config/agenda.yaml`` decide. ``avisos`` diz o que caiu no padrão."""

    regua: Regua = field(default_factory=Regua)
    cortes: Cortes = field(default_factory=Cortes)
    tetos: Tetos = field(default_factory=Tetos)
    comissoes_que_decidem: tuple[tuple[str, tuple[str, ...]], ...] = COMISSOES_QUE_DECIDEM
    frescor: Frescor = field(default_factory=Frescor)
    situacoes: tuple[Situacao, ...] = ()
    palavras_de_area: tuple[tuple[str, str], ...] = ()
    nomes_aprovados: tuple[tuple[str, str], ...] = ()
    apelidos: tuple[Apelido, ...] = ()
    feeds_oficiais: tuple[str, ...] = FEEDS_OFICIAIS
    avisos: tuple[str, ...] = ()

    # ── régua ────────────────────────────────────────────────────────────────

    def comissao_decide(self, instituicao: str, sigla: str | None) -> bool:
        """A comissão de sigla ``sigla`` é das que decidem naquela Casa (vale "comissao_decide")."""
        siglas = dict(self.comissoes_que_decidem).get(instituicao, ())
        return bool(sigla) and str(sigla).strip().upper() in siglas

    def frescor_de(self, data_dados: str | None, hoje: str, velho_dias: int | None = None) -> str:
        """``"ok"``, ``"aviso"`` (o dado tem dias úteis demais: mostrar a data) ou ``"some"`` (velho
        demais, ou sem data: os itens da fonte saem e fica a frase com o link oficial). O limite
        de sumir é o da própria fonte (``velho_dias``) quando ela traz."""
        if not data_dados:
            return "some"
        try:
            corridos = (date.fromisoformat(hoje[:10]) - date.fromisoformat(data_dados[:10])).days
            uteis = contrato.dias_uteis_entre(data_dados, hoje)
        except ValueError:
            return "some"
        if corridos > (velho_dias if velho_dias else self.frescor.some_dias):
            return "some"
        return "aviso" if uteis >= self.frescor.aviso_dias_uteis else "ok"

    # ── textos ───────────────────────────────────────────────────────────────

    def situacao(self, chave: str, detalhe: str | None = None, data: str | None = None) -> str:
        """A frase da home para uma palavra de estágio, de resultado ou de situação (ou para uma
        das chaves de :data:`SITUACOES_ESPECIAIS`). Sem entrada no dicionário, o termo oficial."""
        entrada = next((s for s in self.situacoes if s.chave == chave), None)
        if entrada is None:
            return contrato.termo(chave)
        modelo = entrada.com_detalhe if detalhe and entrada.com_detalhe else entrada.texto
        return modelo.format(detalhe=detalhe or "", data=data or "").strip()

    def palavra_de_area(self, area: str | None) -> str | None:
        """A palavra que abre o item, ou ``None`` quando a área não tem palavra (ou não há área)."""
        return dict(self.palavras_de_area).get(area or "") or None

    def nome_aprovado(self, identificador: str) -> str | None:
        return dict(self.nomes_aprovados).get(_identificador(identificador)) or None

    # ── ligação com a mídia ──────────────────────────────────────────────────

    def apelidos_de(self, identificador: str, dia: str) -> tuple[str, ...]:
        """Os apelidos aprovados do item que valem no dia (o primeiro é o que a tela mostra)."""
        alvo = _identificador(identificador)
        return tuple(t for a in self.apelidos if a.item == alvo and a.vale_em(dia) for t in a.termos)

    def apelidos_em(self, dia: str) -> dict[str, str]:
        """Apelido → identificador, só os que valem no dia (para procurar nos textos)."""
        return {t: a.item for a in self.apelidos if a.vale_em(dia) for t in a.termos}

    def feed_oficial(self, feed_id: str | None) -> bool:
        """O feed é o canal de um tribunal ou de uma Casa: não conta como imprensa."""
        return str(feed_id or "").strip().lower() in self.feeds_oficiais


PADRAO = Regras()  # o que vale sem o arquivo: a régua, os cortes e os tetos decididos; dicionário e tabelas vazios


# ── leitura tolerante ────────────────────────────────────────────────────────────────────────


def _identificador(texto: Any) -> str:
    return re.sub(r"\s+", " ", str(texto or "")).strip()


def _objeto(bruto: Any, onde: str, avisos: list[str]) -> dict[str, Any]:
    if bruto is None:
        return {}
    if not isinstance(bruto, Mapping):
        avisos.append(f"{onde}: esperado um bloco de chaves; usando o padrão")
        return {}
    return dict(bruto)


def _inteiro(bruto: Any, padrao: int, onde: str, avisos: list[str], *, minimo: int = 0, maximo: int | None = None) -> int:
    if bruto is None:
        return padrao
    if isinstance(bruto, bool) or not isinstance(bruto, int) or bruto < minimo or (maximo is not None and bruto > maximo):
        limite = f" a {maximo}" if maximo is not None else " ou mais"
        avisos.append(f"{onde}: esperado um inteiro de {minimo}{limite}, veio {bruto!r}; usando {padrao}")
        return padrao
    return bruto


def _pesos(bruto: Any, padrao: tuple[tuple[str, int], ...], teto: int, onde: str, avisos: list[str]) -> tuple[tuple[str, int], ...]:
    """Os pesos de uma parte da régua, na ordem do padrão. Palavra fora do vocabulário é ignorada;
    palavra que falta ou peso inválido (não inteiro, negativo, acima do teto da parte) fica no padrão."""
    dado = _objeto(bruto, onde, avisos)
    for nome in dado:
        if nome not in dict(padrao):
            avisos.append(f"{onde}.{nome}: palavra fora do vocabulário; ignorada")
    return tuple((nome, _inteiro(dado.get(nome), peso, f"{onde}.{nome}", avisos, maximo=teto)) for nome, peso in padrao)


def _campos(classe: type, bruto: Any, onde: str, avisos: list[str], *, minimo: int = 1) -> dict[str, int]:
    """Os campos inteiros de um dataclass lidos de um bloco, cada um com o padrão do dataclass."""
    dado = _objeto(bruto, onde, avisos)
    padrao = classe()
    return {nome: _inteiro(dado.get(nome), getattr(padrao, nome), f"{onde}.{nome}", avisos, minimo=minimo) for nome in classe.__dataclass_fields__}


def _tetos(bruto: Any, avisos: list[str]) -> Tetos:
    dado = _objeto(bruto, "tetos", avisos)
    home = _objeto(dado.get("home"), "tetos.home", avisos)
    celular = _objeto(home.get("celular"), "tetos.home.celular", avisos)
    plano = {**{k: v for k, v in home.items() if k != "celular"}, **{f"celular_{k}": v for k, v in celular.items()}}
    return Tetos(
        home=TetosHome(**_campos(TetosHome, plano, "tetos.home", avisos)),
        pagina=TetosPagina(**_campos(TetosPagina, dado.get("pagina"), "tetos.pagina", avisos)),
        arquivos=TetosArquivos(**_campos(TetosArquivos, dado.get("arquivos"), "tetos.arquivos", avisos)),
    )


def _comissoes(bruto: Any, avisos: list[str]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if bruto is None:
        return COMISSOES_QUE_DECIDEM
    dado = _objeto(bruto, "comissoes_que_decidem", avisos)
    saida = []
    for casa, padrao in COMISSOES_QUE_DECIDEM:
        siglas = dado.get(casa, padrao)
        if not isinstance(siglas, (list, tuple)) or not all(isinstance(s, str) and s.strip() for s in siglas):
            avisos.append(f"comissoes_que_decidem.{casa}: esperada uma lista de siglas; usando o padrão")
            siglas = padrao
        saida.append((casa, tuple(dict.fromkeys(s.strip().upper() for s in siglas))))
    for casa in dado:
        if casa not in dict(COMISSOES_QUE_DECIDEM):
            avisos.append(f"comissoes_que_decidem.{casa}: Casa desconhecida; ignorada")
    return tuple(saida)


def _proibida(texto: str) -> str | None:
    """A expressão proibida que o texto de tela traz, se trouxer alguma."""
    baixo = texto.lower()
    return next((e for e in EXPRESSOES_PROIBIDAS if e in baixo), None)


def _frase(bruto: Any, onde: str, avisos: list[str]) -> str:
    """Uma frase de tela válida, ou vazio: texto limpo, sem expressão proibida, e que aceita os
    marcadores ``{detalhe}`` e ``{data}`` (qualquer outro marcador invalida a frase)."""
    frase = contrato.limpar_texto(bruto) if isinstance(bruto, str) else None
    if not frase:
        if bruto not in (None, ""):
            avisos.append(f"{onde}: esperado um texto; ignorado")
        return ""
    proibida = _proibida(frase)
    if proibida:
        avisos.append(f"{onde}: traz {proibida!r} (previsão, adjetivo ou travessão); ignorado")
        return ""
    try:
        frase.format(detalhe="x", data="x")
    except (KeyError, IndexError, ValueError):
        avisos.append(f"{onde}: marcador desconhecido (só {{detalhe}} e {{data}}); ignorado")
        return ""
    return frase


def _situacoes(bruto: Any, avisos: list[str]) -> tuple[Situacao, ...]:
    conhecidas = {*contrato.ESTAGIOS, *contrato.PALAVRAS_RESULTADO, *contrato.SITUACOES_SESSAO, *SITUACOES_ESPECIAIS}
    saida = []
    for chave, valor in _objeto(bruto, "situacoes", avisos).items():
        onde = f"situacoes.{chave}"
        if chave not in conhecidas:
            avisos.append(f"{onde}: situação desconhecida; ignorada")
            continue
        if isinstance(valor, str):
            entrada: Mapping[str, Any] = {"texto": valor}
        elif isinstance(valor, Mapping):
            entrada = valor
        else:
            avisos.append(f"{onde}: esperado um texto ou um bloco com \"texto\"; ignorada")
            continue
        texto = _frase(entrada.get("texto"), f"{onde}.texto", avisos)
        if not texto:
            if "texto" not in entrada:
                avisos.append(f"{onde}: falta o texto; ignorada")
            continue
        saida.append(Situacao(chave=str(chave), texto=texto, com_detalhe=_frase(entrada.get("com_detalhe"), f"{onde}.com_detalhe", avisos)))
    return tuple(saida)


def _tabela(bruto: Any, onde: str, avisos: list[str], *, chave_de_area: bool) -> tuple[tuple[str, str], ...]:
    """Uma tabela chave → texto de tela (palavras de área, nomes aprovados), sem as linhas inválidas."""
    saida = []
    for chave, valor in _objeto(bruto, onde, avisos).items():
        nome = str(chave).strip() if chave_de_area else _identificador(chave)
        texto = _frase(valor, f"{onde}.{chave}", avisos)
        if not nome or not texto or "{" in texto:
            if texto and "{" in texto:
                avisos.append(f"{onde}.{chave}: texto com marcador; ignorado")
            continue
        saida.append((nome, texto))
    return tuple(saida)


def _data(bruto: Any) -> str | None:
    """``AAAA-MM-DD`` de um texto ou de uma data do YAML (sem aspas o YAML já entrega a data)."""
    if isinstance(bruto, date):
        return bruto.isoformat()
    try:
        return date.fromisoformat(str(bruto)).isoformat() if isinstance(bruto, str) else None
    except ValueError:
        return None


def _apelidos(bruto: Any, avisos: list[str]) -> tuple[Apelido, ...]:
    if bruto is None:
        return ()
    if not isinstance(bruto, list):
        avisos.append("apelidos: esperada uma lista; ignorada")
        return ()
    saida = []
    for i, linha in enumerate(bruto):
        onde = f"apelidos[{i}]"
        dado = _objeto(linha, onde, avisos) if linha is not None else {}
        item, de, ate = _identificador(dado.get("item")), _data(dado.get("de")), _data(dado.get("ate"))
        if not item or not de or not ate or de > ate:
            avisos.append(f"{onde}: precisa de item e de validade (de e ate, AAAA-MM-DD, em ordem); ignorado")
            continue
        termos = []
        bruto_termos = dado.get("termos")
        for termo in bruto_termos if isinstance(bruto_termos, list) else []:
            limpo = contrato.limpar_texto(termo) if isinstance(termo, str) else None
            if not limpo or len(limpo.split()) < APELIDO_MINIMO_DE_PALAVRAS:
                avisos.append(f"{onde}.termos: {termo!r} tem menos de {APELIDO_MINIMO_DE_PALAVRAS} palavras; ignorado")
            elif _proibida(limpo):
                avisos.append(f"{onde}.termos: {termo!r} traz adjetivo, previsão ou travessão; ignorado")
            elif limpo not in termos:
                termos.append(limpo)
        if not termos:
            avisos.append(f"{onde}: nenhum termo válido; ignorado")
            continue
        saida.append(Apelido(item=item, termos=tuple(termos), de=de, ate=ate))
    return tuple(saida)


def _feeds(bruto: Any, avisos: list[str]) -> tuple[str, ...]:
    if bruto is None:
        return FEEDS_OFICIAIS
    if not isinstance(bruto, list) or not all(isinstance(f, str) and f.strip() for f in bruto):
        avisos.append("feeds_oficiais: esperada uma lista de ids de config/sources.yaml; usando o padrão")
        return FEEDS_OFICIAIS
    return tuple(dict.fromkeys(f.strip().lower() for f in bruto))


def de_dict(bruto: Any) -> Regras:
    """As regras de um YAML já lido. Nunca levanta exceção: o que não serve cai no padrão e vai
    para ``avisos``."""
    avisos: list[str] = []
    try:
        dado = _objeto(bruto, ARQUIVO, avisos)
        regua = _objeto(dado.get("regua"), "regua", avisos)
        cortes = Cortes(**_campos(Cortes, dado.get("cortes"), "cortes", avisos, minimo=0))
        if cortes.pagina > cortes.home:
            avisos.append("cortes: o corte da página completa não pode ser maior que o da home; usando o padrão")
            cortes = Cortes()
        return Regras(
            regua=Regua(
                foro=_pesos(regua.get("foro"), PESOS_FORO, TETO_FORO, "regua.foro", avisos),
                alcance=_pesos(regua.get("alcance"), PESOS_ALCANCE, TETO_ALCANCE, "regua.alcance", avisos),
                estagio=_pesos(regua.get("estagio"), PESOS_ESTAGIO, TETO_ESTAGIO, "regua.estagio", avisos),
            ),
            cortes=cortes,
            tetos=_tetos(dado.get("tetos"), avisos),
            comissoes_que_decidem=_comissoes(dado.get("comissoes_que_decidem"), avisos),
            frescor=Frescor(**_campos(Frescor, dado.get("frescor"), "frescor", avisos)),
            situacoes=_situacoes(dado.get("situacoes"), avisos),
            palavras_de_area=_tabela(dado.get("palavras_de_area"), "palavras_de_area", avisos, chave_de_area=True),
            nomes_aprovados=_tabela(dado.get("nomes_aprovados"), "nomes_aprovados", avisos, chave_de_area=False),
            apelidos=_apelidos(dado.get("apelidos"), avisos),
            feeds_oficiais=_feeds(dado.get("feeds_oficiais"), avisos),
            avisos=tuple(avisos),
        )
    except Exception as erro:  # defesa: um formato que nenhuma checagem previu
        log.warning("config/%s: formato inesperado; a agenda usa os padrões", ARQUIVO, exc_info=True)
        return Regras(avisos=(*avisos, f"{ARQUIVO}: formato inesperado ({type(erro).__name__}); usando os padrões"))


def carregar(root: Path | str | None = None) -> Regras:
    """Lê ``<root>/config/agenda.yaml``. Sem o arquivo, ou com ele ilegível, devolve os padrões
    (com o motivo em ``avisos``). Nunca levanta exceção."""
    caminho = Path(root) / "config" / ARQUIVO if root else ROOT / "config" / ARQUIVO
    try:
        texto = caminho.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Regras(avisos=(f"config/{ARQUIVO} não existe; a agenda usa os padrões",))
    except (OSError, ValueError):
        log.warning("%s: arquivo ilegível; a agenda usa os padrões", caminho)
        return Regras(avisos=(f"config/{ARQUIVO} ilegível; a agenda usa os padrões",))
    try:
        bruto = yaml.safe_load(texto)
    except yaml.YAMLError:
        log.warning("%s: YAML inválido; a agenda usa os padrões", caminho)
        return Regras(avisos=(f"config/{ARQUIVO} com YAML inválido; a agenda usa os padrões",))
    regras = de_dict(bruto)
    for aviso in regras.avisos:
        log.warning("config/%s: %s", ARQUIVO, aviso)
    return regras
