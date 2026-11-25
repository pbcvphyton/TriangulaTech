"""Agenda da semana na tela: o bloco da home e a página ``edicoes/agenda.html``.

O retrato gravado na edição (``Edition.agenda``, lido por
:func:`qijournal.agenda.retrato.ler_retrato`) já traz os textos prontos. Aqui ele vira
objetos de apresentação validados (texto sem espaço sobrando e com teto de tamanho, link
só ``http(s)``, data ISO conferida, tetos da home reaplicados), e é só isso que os modelos
leem: nenhuma frase é montada na tela, os campos são apenas juntados.

Duas saídas:

- :func:`bloco_da_home` devolve o :class:`AgendaView` do bloco "Agenda da semana" da home
  (``edition.html.j2``): os dias em duas colunas ("hoje e amanhã" e o resto da semana), a
  faixa "Já decidido esta semana", até duas frases e o pé. ``web.build_view`` chama dentro
  de ``try/except``; sem retrato, ou com qualquer erro, a home sai sem o bloco.
- :func:`render_agenda_page` devolve o HTML de ``edicoes/agenda.html`` (``None`` sem
  retrato ou com a agenda desligada para a marca). ``pipeline.publish`` chama dentro de
  ``try/except`` próprio: se falhar, a página não é gravada e a edição sai igual.

"HOJE", "AMANHÃ" e o cinza dos dias passados não moram aqui: são marcados no navegador,
pela data de Brasília (``page.js.j2``), a partir da data de cada dia.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Collection
from urllib.parse import quote

from ..agenda import contrato, regras as regras_mod
from ..agenda.retrato import Contagem, DiaTela, Frase, ItemTela, LinhaTela, Retrato, ler_retrato
from ..config import Config
from ..models import Edition
from . import filters

log = logging.getLogger(__name__)

PAGINA = "agenda.html"  # dentro de edicoes/
ANCORA = "agenda"  # id do bloco na home (alvo de "na agenda: qua 25")
TETO_LINHAS = 8  # linhas de pauta na janela da home
TETO_ABERTAS_NO_CELULAR = 4  # linhas de hoje e amanhã que abrem no celular
TETO_ITENS_POR_LINHA = 2  # itens nomeados por linha na home (no celular, só o primeiro)
TETO_FRASES = 2
TETO_DECIDIDO = 2
TEXTO_MAX = 400  # nenhum texto de tela da agenda passa disto (os do retrato já vêm curtos)
NOME_MAX = 90
NOME_CURTO_MAX = 60
PAGINA_BYTES_MAX = 250_000  # meta de peso da página completa (acima disto, só um aviso no registro)


# ── objetos de tela ──────────────────────────────────────────────────────────


@dataclass
class ItemView:
    """Um item de pauta: o assunto, o identificador (link oficial) e a situação."""

    nome: str = ""  # o assunto, sempre antes do número ("" = item sem nome: só o identificador)
    nome_curto: str = ""  # a versão do celular, só quando é diferente do nome
    nome_comum: str = ""  # com nome_curto: o começo que as duas versões têm em comum...
    nome_resto: str = ""  # ...e o resto, que só a tela larga mostra (o celular fecha com reticências)
    area: str = ""
    identificador: str = ""
    link: str = ""  # página oficial ("" = sem link)
    situacao: str = ""  # português claro; vazio quando a linha diz uma vez só (situacao_comum)
    midia: str = ""  # "na mídia: 3 veículos" (home) ou "menção direta encontrada em 3 veículos" (página)
    midia_href: str = ""  # a janela da matéria da edição ou a Pesquisa
    so_tela_larga: bool = False  # do segundo item em diante: some no celular
    # só na página completa
    termo: str = ""  # termo oficial da situação
    resultado: str = ""
    relator: str = ""
    conta: str = ""  # "Plenário (30) + projeto de lei (18) + urgência (15) = 63"


@dataclass
class LinhaView:
    """Uma linha de pauta: sessão, julgamento, reunião de comissão ou prazo."""

    ancora: str = ""  # id na página completa
    dia: str = ""  # "seg 23" (faixa "Já decidido")
    hora: str = ""
    cabeca: str = ""  # "Senado · Plenário": instituição e órgão por extenso
    link: str = ""  # página oficial da sessão
    situacao: str = ""  # texto da linha inteira ("em andamento", "Medida provisória perde a validade")
    itens: list[ItemView] = field(default_factory=list)
    situacao_comum: str = ""  # a situação dos itens, quando é a mesma em todos
    mais: str = ""  # "mais 10" ou "10 itens pautados"
    mais_celular: str = ""  # a mesma conta com um item só ("mais 11")
    aberta: bool = False  # abre no celular (as outras ficam em uma linha)
    fora: list[str] = field(default_factory=list)  # página: o que ficou fora nesta sessão
    abaixo_do_corte: int = 0


@dataclass
class DiaView:
    data: str = ""  # ISO: é por ela que o navegador marca HOJE, AMANHÃ e o que passou
    rotulo: str = ""  # "qua 25"
    rotulo_longo: str = ""  # "quarta-feira, 25 de novembro"
    linhas: list[LinhaView] = field(default_factory=list)
    vazio: str = ""  # "nada de peso pautado"


@dataclass
class FraseView:
    texto: str = ""
    link: str = ""
    link_rotulo: str = ""


@dataclass
class AgendaView:
    """O bloco "Agenda da semana" da home."""

    principais: list[DiaView] = field(default_factory=list)  # coluna larga: "hoje e amanhã"
    resto: list[DiaView] = field(default_factory=list)  # coluna estreita: o resto da semana
    decidido: list[LinhaView] = field(default_factory=list)  # "Já decidido esta semana"
    frases: list[FraseView] = field(default_factory=list)
    pe: str = ""  # "Conferido em 24/11. A pauta pode mudar."
    pe_fontes: list[str] = field(default_factory=list)  # só com fonte velha: "STF: dados de 20/11"
    pagina_href: str = ""  # a agenda completa ("" = a página não existe)
    pagina_rotulo: str = "Agenda completa"
    congresso_href: str = ""  # as abas, só quando publicadas
    tribunais_href: str = ""

    @property
    def lateral(self) -> bool:
        """Há o que pôr na coluna estreita (o resto da semana ou o já decidido)."""
        return bool(self.resto or self.decidido)


@dataclass
class FonteView:
    instituicao: str = ""
    nome: str = ""
    url: str = ""
    texto: str = ""  # "dados de 24/11"
    estado: str = ""  # "ok" | "aviso" | "some" | "ausente"


@dataclass
class ParteDaRegua:
    """Uma das três partes da nota (foro, alcance, estágio) com os pesos de cada caso."""

    nome: str
    pergunta: str
    teto: int
    pesos: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class PaginaView:
    """A página completa ``edicoes/agenda.html``."""

    dias: list[DiaView] = field(default_factory=list)
    decidido: list[DiaView] = field(default_factory=list)
    radar: list[DiaView] = field(default_factory=list)
    fila: list[ItemView] = field(default_factory=list)
    fila_texto: str = ""
    frases: list[FraseView] = field(default_factory=list)
    fontes: list[FonteView] = field(default_factory=list)
    limites: list[str] = field(default_factory=list)
    fora: list[str] = field(default_factory=list)
    contagens: list[str] = field(default_factory=list)
    abaixo_do_corte: int = 0
    fora_do_teto: int = 0
    corte: int = 40
    corte_home: int = 60
    sem_pauta: str = ""
    pe: str = ""
    pe_fontes: list[str] = field(default_factory=list)
    conferido: str = ""  # "24/11"
    periodo: str = ""  # "de 24/11 a 30/11"
    regua: list[ParteDaRegua] = field(default_factory=list)
    nota_maxima: int = 85

    @property
    def tem_fora(self) -> bool:
        return bool(self.fora or self.contagens or self.abaixo_do_corte or self.fora_do_teto)


# ── validação ────────────────────────────────────────────────────────────────


def _txt(valor: Any, teto: int = TEXTO_MAX) -> str:
    """Texto de tela: só texto, sem espaço sobrando e dentro do teto (o corte é defesa)."""
    if not isinstance(valor, str):
        return ""
    limpo = " ".join(valor.split())
    return limpo if len(limpo) <= teto else limpo[: teto - 1].rstrip() + "…"


def _url(valor: Any) -> str:
    return filters.safe_url(valor if isinstance(valor, str) else None) or ""


def _data(valor: Any) -> str:
    """A data ISO conferida ("" quando não é uma data: o navegador não marca nada)."""
    try:
        return date.fromisoformat(valor).isoformat() if isinstance(valor, str) else ""
    except ValueError:
        return ""


def _dia_mes(iso: str) -> str:
    dia = _data(iso)
    return f"{dia[8:10]}/{dia[5:7]}" if dia else ""


def _ancora(texto: str, usadas: set[str]) -> str:
    """Id de uma linha na página completa, único no documento."""
    base = "l-" + (re.sub(r"[^A-Za-z0-9]+", "-", texto).strip("-") or "linha")
    ancora, n = base, 2
    while ancora in usadas:
        ancora, n = f"{base}-{n}", n + 1
    usadas.add(ancora)
    return ancora


@dataclass
class _Destinos:
    """Para onde vão os links internos da agenda, vistos da página que está sendo montada."""

    materias: Collection[str]  # ids das matérias da edição (só elas ganham link de janela)
    materia_href: str  # prefixo do link da janela: "" na página da edição, "AAAA-MM-DD.html" fora dela
    pasta: str  # a pasta edicoes/ vista da página: "edicoes/" na home, "" dentro dela


def _midia_href(item: ItemTela, destinos: _Destinos) -> str:
    """O link da marca de mídia: a janela da matéria da edição ligada; sem ela, a Pesquisa
    com o identificador do item."""
    if item.materia and item.materia in destinos.materias:
        return f"{destinos.materia_href}#s-{item.materia}"
    termo = _txt(item.identificador, 80)
    return f"{destinos.pasta}pesquisa.html?q={quote(termo)}" if termo else ""


def _item(item: ItemTela, destinos: _Destinos, *, pagina: bool = False) -> ItemView:
    nome = _txt(item.nome, NOME_MAX)
    curto = _txt(item.nome_curto, NOME_CURTO_MAX)
    midia = _txt(item.midia_pagina if pagina else item.midia_texto, 80) if item.midia > 0 else ""
    curto = curto if curto and curto != nome and not pagina else ""
    # O nome curto costuma ser o começo do nome com reticências: a tela escreve o começo uma vez só.
    comum = curto.rstrip("…").rstrip()
    partido = bool(comum) and curto.endswith("…") and nome.startswith(comum) and len(nome) > len(comum)
    return ItemView(
        nome=nome,
        nome_curto=curto,
        nome_comum=comum if partido else "",
        nome_resto=nome[len(comum) :] if partido else "",
        area=_txt(item.area, 40),
        identificador=_txt(item.identificador_tela or item.identificador, 80),
        link=_url(item.link),
        situacao=_txt(item.situacao, 160),
        midia=midia,
        midia_href=_midia_href(item, destinos) if midia else "",
        termo=_txt(item.termo, 160) if pagina else "",
        resultado=_txt(item.resultado, 160) if pagina else "",
        relator=_txt(item.relator, 120) if pagina else "",
        conta=_txt(item.conta, 200) if pagina else "",
    )


def _linha(
    linha: LinhaTela, destinos: _Destinos, *, pagina: bool = False, compacta: bool = False, usadas: set[str] | None = None
) -> LinhaView:
    """A linha validada. Na home, até ``TETO_ITENS_POR_LINHA`` itens com nome; quando todos
    dizem a mesma situação, ela sai uma vez só, na linha. ``compacta`` (o resto da semana e o
    já decidido): um item nomeado só e o resto em "mais N"."""
    brutos = linha.itens if pagina else tuple(i for i in linha.itens if _txt(i.nome))[:TETO_ITENS_POR_LINHA]
    itens = [_item(i, destinos, pagina=pagina) for i in brutos]
    itens = [i for i in itens if i.nome or i.identificador]
    comum = ""
    if not pagina:
        for n, item in enumerate(itens):
            item.so_tela_larga = n > 0
        situacoes = {i.situacao for i in itens}
        if len(itens) > 1 and len(situacoes) == 1 and "" not in situacoes:
            comum = itens[0].situacao
            for item in itens:
                item.situacao = ""
    mais = _txt(linha.mais_texto, 60)
    mais_celular = _txt(linha.mais_celular_texto, 60) or mais
    if compacta:  # coluna estreita e "Já decidido": um item só, com a conta do celular
        itens, mais = itens[:1], mais_celular
        for item in itens:
            item.nome_curto = item.nome_comum = item.nome_resto = ""
    return LinhaView(
        ancora=_ancora(linha.id, usadas) if usadas is not None else "",
        dia=_txt(linha.dia, 20),
        hora=_txt(linha.hora, 10),
        cabeca=_txt(linha.cabeca or linha.instituicao, 120),
        link=_url(linha.link),
        situacao=_txt(linha.situacao, 160),
        itens=itens,
        situacao_comum=comum,
        mais=mais,
        mais_celular=mais_celular,
        aberta=bool(linha.aberta_no_celular),
        fora=[t for t in (_txt(c.texto, 120) for c in linha.fora) if t] if pagina else [],
        abaixo_do_corte=max(0, linha.abaixo_do_corte) if pagina else 0,
    )


def _dia(dia: DiaTela, linhas: list[LinhaView]) -> DiaView:
    return DiaView(
        data=_data(dia.data),
        rotulo=_txt(dia.rotulo, 20),
        rotulo_longo=_txt(dia.rotulo_longo, 60),
        linhas=linhas,
        vazio="" if linhas else _txt(dia.vazio, 80),
    )


def _frases(frases: tuple[Frase, ...], teto: int | None = None) -> list[FraseView]:
    saida = [
        FraseView(texto=_txt(f.texto), link=_url(f.link), link_rotulo=_txt(f.link_rotulo, 40) or "fonte oficial")
        for f in frases
        if _txt(f.texto)
    ]
    return saida if teto is None else saida[:teto]


def _contagens(contagens: tuple[Contagem, ...]) -> list[str]:
    return [t for t in (_txt(c.texto, 160) for c in contagens) if t]


# ── home ─────────────────────────────────────────────────────────────────────


def bloco_da_home(
    retrato: Retrato | None,
    *,
    materias: Collection[str],
    pasta: str,
    pagina: bool = True,
    congresso: bool = False,
    tribunais: bool = False,
) -> AgendaView | None:
    """O bloco da home a partir do retrato (``None`` = não desenhar).

    ``materias``: ids das matérias da edição (a marca "na mídia" só abre janela de matéria
    que existe; as outras vão para a Pesquisa). ``pasta``: a pasta ``edicoes/`` vista da
    página ("edicoes/" na home, "" na cópia arquivada). ``pagina``: a página completa existe
    (sem ela, o pé não leva o link). ``congresso`` e ``tribunais``: as abas estão publicadas.
    """
    if retrato is None or retrato.vazio:
        return None
    destinos = _Destinos(materias=materias, materia_href="", pasta=pasta)
    principais: list[DiaView] = []
    resto: list[DiaView] = []
    restam, abertas = TETO_LINHAS, TETO_ABERTAS_NO_CELULAR
    for dia in retrato.dias:
        linhas = [_linha(linha, destinos, compacta=not dia.principal) for linha in dia.linhas[: max(0, restam)]]
        restam -= len(linhas)
        for linha in linhas:  # só linha de hoje e amanhã abre no celular, até o teto
            linha.aberta = linha.aberta and dia.principal and abertas > 0
            abertas -= 1 if linha.aberta else 0
        tela = _dia(dia, linhas)
        if not (tela.rotulo and (tela.linhas or tela.vazio)):
            continue
        (principais if dia.principal else resto).append(tela)
    decidido = [_linha(linha, destinos, compacta=True) for linha in retrato.decidido[:TETO_DECIDIDO]]
    for linha in decidido:
        linha.aberta = True  # no celular, o resultado não pode ficar cortado numa linha só
    fora = max(0, retrato.fora_do_teto)
    return AgendaView(
        principais=principais,
        resto=resto,
        decidido=[linha for linha in decidido if linha.itens],
        frases=_frases(retrato.frases, TETO_FRASES),
        pe=_txt(retrato.pe.texto, 120),
        pe_fontes=[t for t in (_txt(f, 80) for f in retrato.pe.fontes) if t],
        pagina_href=f"{pasta}{PAGINA}" if pagina else "",
        pagina_rotulo=f"Mais {fora} na agenda completa" if fora else "Agenda completa",
        congresso_href=f"{pasta}congresso.html" if congresso else "",
        tribunais_href=f"{pasta}tribunais.html" if tribunais else "",
    )


# ── página completa ──────────────────────────────────────────────────────────

_PARTES = (
    ("Foro", "onde se decide", "foro", contrato.TERMO_FORO),
    ("Alcance", "quanta gente a decisão alcança", "alcance", contrato.TERMO_ALCANCE),
    ("Estágio", "em que ponto está", "estagio", contrato.TERMO_ESTAGIO),
)


def _regua(regras: regras_mod.Regras) -> list[ParteDaRegua]:
    """As três partes da nota com os pesos de ``config/agenda.yaml``, do maior para o menor."""
    partes: list[ParteDaRegua] = []
    for nome, pergunta, campo, termos in _PARTES:
        pesos = [(_txt(str(termos.get(chave, chave)).replace("_", " "), 60), int(pontos)) for chave, pontos in getattr(regras.regua, campo)]
        pesos = sorted((p for p in pesos if p[0]), key=lambda par: -par[1])
        partes.append(ParteDaRegua(nome=nome, pergunta=pergunta, teto=max((p[1] for p in pesos), default=0), pesos=pesos))
    return partes


def pagina_view(retrato: Retrato, config: Config, *, materias: Collection[str], edicao: str) -> PaginaView:
    """A página completa a partir de ``retrato.pagina`` (os links de matéria apontam para a
    cópia arquivada da edição, que mora na mesma pasta)."""
    destinos = _Destinos(materias=materias, materia_href=f"{edicao}.html" if _data(edicao) else "", pasta="")
    usadas: set[str] = set()
    p = retrato.pagina

    def dias(origem: tuple[DiaTela, ...]) -> list[DiaView]:
        telas = [_dia(d, [_linha(linha, destinos, pagina=True, usadas=usadas) for linha in d.linhas]) for d in origem]
        return [d for d in telas if d.rotulo and (d.linhas or d.vazio)]

    regras = regras_mod.carregar(getattr(config, "root", None))
    janela = [d for d in (_dia_mes(dia) for dia in retrato.janela) if d]
    return PaginaView(
        dias=dias(p.dias),
        decidido=dias(p.decidido),
        radar=dias(p.radar),
        fila=[i for i in (_item(item, destinos, pagina=True) for item in p.fila) if i.nome or i.identificador],
        fila_texto=_txt(p.fila_texto, 200),
        frases=_frases(p.frases),
        fontes=[
            FonteView(instituicao=_txt(f.instituicao, 40), nome=_txt(f.nome, 120), url=_url(f.url), texto=_txt(f.texto, 160), estado=_txt(f.estado, 10))
            for f in p.fontes
            if _txt(f.nome) or _txt(f.instituicao)
        ],
        limites=[t for t in (_txt(limite) for limite in p.limites) if t],
        fora=_contagens(p.fora),
        contagens=_contagens(p.contagens),
        abaixo_do_corte=max(0, p.abaixo_do_corte),
        fora_do_teto=max(0, p.fora_do_teto),
        corte=p.corte or regras.cortes.pagina,
        corte_home=p.corte_home or regras.cortes.home,
        sem_pauta=_txt(p.sem_pauta, 120),
        pe=_txt(retrato.pe.texto, 120),
        pe_fontes=[t for t in (_txt(f, 80) for f in retrato.pe.fontes) if t],
        conferido=_txt(retrato.pe.conferido, 10),
        periodo=f"de {janela[0]} a {janela[-1]}" if len(janela) > 1 else "",
        regua=_regua(regras),
        nota_maxima=regras.regua.maximo,
    )


def render_agenda_page(edition: Edition, config: Config, *, home_href: str, archive_href: str) -> str | None:
    """Página ``edicoes/agenda.html`` (``None`` com a agenda desligada para a marca ou sem
    retrato na edição). Pode levantar: quem chama (``pipeline.publish``) protege."""
    from .web import brand_view  # import tardio: web.py importa este módulo

    if not config.agenda_ativo:
        return None
    retrato = ler_retrato(edition.agenda)
    if retrato is None:
        return None
    view = pagina_view(retrato, config, materias={s.id for s in edition.stories}, edicao=edition.date)
    template = filters.environment().get_template("agenda.html.j2")
    html = template.render(
        a=view,
        brand=brand_view(config),
        home_href=filters.safe_href(home_href) or "../",
        archive_href=filters.safe_href(archive_href) or "./",
        base_url=config.site.base_url,
        repo_url=filters.safe_url(config.site.repo_url) or "",
        analitico=config.termometro_ativo,
        pagina_completa=True,  # agenda.css.j2: os estilos da página, além dos do bloco
        agenda_script=True,  # page.js.j2: HOJE, AMANHÃ e os dias passados
    )
    tamanho = len(html.encode("utf-8"))
    if tamanho > PAGINA_BYTES_MAX:
        log.warning("Agenda: a página completa tem %d KB, acima da meta de %d KB", tamanho // 1024, PAGINA_BYTES_MAX // 1024)
    log.info("Agenda da semana renderizada: %d dia(s) na janela, %d KB", len(view.dias), tamanho // 1024)
    return html
