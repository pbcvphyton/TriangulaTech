"""Gera as publicações do dia (imagens JPEG + legendas) e devolve os itens da fila.

A grade do dia (docs/instagram-onda1.md §2) sai de :func:`build_pieces`, nesta ordem de
escolha: a noite (Raio-X, Placar do dia ou Mercado em linha), o Número do dia, a manhã
(Em 1 minuto ou Terminal) e os stories com a agenda do que de fato foi gerado. Cada peça
passa pelo lint (:func:`.redacao.lint`, com o que transbordou no render); reprovada, cai
para o substituto e o motivo vai para o log. Na fila, a ordem é manhã, stories, número, noite.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from qijournal.config import Config, InstagramSlot
from qijournal.models import Edition
from qijournal.render.filters import clean_svg, md_lite, plain

from . import conteudo, redacao, selecao
from . import estatistica as E
from .fila import Fila, Post
from .formatos import manha as MH
from .formatos import manifesto as MF
from .formatos import mercado as ME
from .formatos import noite as NO
from .formatos import numero as NU
from .formatos import stories as ST
from .render import Renderer, Transbordo, fonts_css

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
SIZES = {"carousel": (1080, 1350), "image": (1080, 1350), "story": (1080, 1920)}
STORY_STEP = timedelta(minutes=1)  # stories em sequência: um minuto de diferença no horário-alvo
LAUNCH_ID = "lancamento-comece-aqui"  # o manifesto: sai uma vez só, antes de tudo, quando a conta for conectada
LAUNCH_DAYS = 3  # cada dia renova o post (números do dia) e o prazo, até ele sair
HISTORICO_DIAS = 90  # edições de data/ que as contas leem (séries do mercado, véspera, manifesto)
TENTATIVAS_NUMERO = 8  # substitutos do Número do dia antes de desistir

# Lint de uma peça: devolve os problemas (lista vazia = aprovada).
Aprovar = Callable[[conteudo.Peca], list[str]]
# A imagem já renderizada do slide i (a partir de 0) de uma peça, como data: URI ("" se não houver).
Imagem = Callable[[conteudo.Peca, int], str]


def environment() -> Environment:
    # QIJ_IG_TEMPLATES: outra pasta de templates (para testar um visual novo sem mexer nestes).
    templates = os.environ.get("QIJ_IG_TEMPLATES") or str(TEMPLATES_DIR)
    env = Environment(
        loader=FileSystemLoader(templates),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    env.filters.update(md_lite=md_lite, plain=plain)
    return env


def brand_context(config: Config) -> dict:
    root = config.root
    logo = (root / "assets" / "triangula-marca-cabecalho.svg").read_text(encoding="utf-8")
    icon = (root / "assets" / "triangula-favicon.svg").read_text(encoding="utf-8")
    return {
        "name": config.brand.name,
        "tagline": config.brand.tagline,
        "colors": config.brand.colors,
        "logo": clean_svg(logo),
        "icon": clean_svg(icon),
        "fonts_css": Markup(fonts_css(root)),
        # @ do perfil sem o @ ("" enquanto a conta não existe: a assinatura sai "▲ triangula").
        "handle": re.sub(r"[^A-Za-z0-9._]", "", (config.instagram.handle or "").strip().lstrip("@")),
    }


def election_mode(config: Config, day: date) -> bool:
    until = config.instagram.election_until
    return bool(until) and day <= date.fromisoformat(str(until))


def slot_window(slot: InstagramSlot, day: date, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    """(horário-alvo, prazo) do slot no dia; None se não sai nesse dia da semana."""
    hhmm = slot.weekends if day.weekday() >= 5 else slot.weekdays
    if not hhmm:
        return None
    due = datetime.combine(day, time.fromisoformat(hhmm), tz)
    expires = datetime.combine(day, time.fromisoformat(slot.expires), tz)
    return due, max(expires, due + timedelta(minutes=30))


def horarios(config: Config, day: date) -> dict[str, str]:
    """{slot: "HH:MM"} do dia (com o horário de fim de semana), só dos slots que saem."""
    out = {}
    for name, slot in config.instagram.slots.items():
        hhmm = slot.weekends if day.weekday() >= 5 else slot.weekdays
        if hhmm:
            out[name] = hhmm
    return out


def post_id(day: str, slot: str) -> str:
    """``"2026-10-21-noite"``: um post de feed por slot e dia (os stories ganham ``-N``)."""
    return f"{day}-{slot}"


def veiculos(edition: Edition) -> list[str]:
    """Os veículos das coberturas comparadas: nenhum pode ir para o destaque em laranja (lint)."""
    nomes = {o.name for s in edition.stories if s.coverage for o in s.coverage.outlets}
    nomes |= {o.name for c in edition.compared for o in c.outlets}
    return sorted(n for n in nomes if n)


# A 1ª linha da legenda não repete a de um post do mesmo formato dos últimos 14 dias (``recentes`` do
# lint) só onde ela vem do fato do dia: o Em 1 minuto (a capa) e o Raio-X (o gancho). Fora, por desenho:
# o Número (a chave dele já não volta em 7 dias, e a linha do acumulado é molde: "−3,5%: Ouro acumula
# queda desde 29/09…"), o Placar ("Dia de consenso: 0 de 23 assuntos…"), o Mercado em linha e o Terminal,
# moldes com o número do dia, e os stories, sem legenda. Em 14/10 a 02/11, a maior similaridade foi 0,38
# no Em 1 minuto e 0,46 no Raio-X; no Número, 0,71, e no Placar, 0,88 (o limite é 0,6).
LINT_RECENTES = ("Em 1 minuto", "Raio-X da cobertura")


def linhas_recentes(fila: Fila, peca: conteudo.Peca, day: date) -> list[str]:
    """O ``recentes`` do lint para a peça: as 1ªs linhas do mesmo formato (:data:`LINT_RECENTES`)."""
    return fila.primeiras_linhas(peca.title, day) if peca.title in LINT_RECENTES else []


def assunto_principal(peca: conteudo.Peca | None) -> str | None:
    """A matéria que a peça estampa (a 1ª de ``story_ids``); None no Mercado, no Terminal e no Placar."""
    if peca is None or peca.title in ("Placar do dia", "Mercado em linha", "Terminal da manhã"):
        return None
    return peca.story_ids[0] if peca.story_ids else None


# ── Renderização ──────────────────────────────────────────────────────────


@dataclass
class Renderizada:
    """As imagens de uma peça (caminhos relativos à pasta de mídia) e o que transbordou em cada slide."""

    images: list[str]
    transbordos: dict[int, list[Transbordo]] = field(default_factory=dict)  # slide (a partir de 1) → cortes

    def overflow(self) -> list[str]:
        """Os cortes no formato do ``overflow`` de :func:`.redacao.lint`."""
        return [f"slide {i}: {t}" for i, ts in sorted(self.transbordos.items()) for t in ts]


def _render_piece(
    renderer: Renderer,
    env: Environment,
    piece: conteudo.Peca,
    brand: dict,
    out_dir: Path,
    rel_dir: str,
) -> Renderizada:
    """Renderiza os slides de ``piece`` (nome com hash do conteúdo) e guarda os transbordos por slide."""
    width, height = SIZES[piece.kind]
    out = Renderizada(images=[])
    total = len(piece.slides)
    for i, slide in enumerate(piece.slides, 1):
        html = env.get_template(f"{slide.template}.html.j2").render(
            brand=brand, s=conteudo.polish(slide.ctx), page=i, pages=total, kind=piece.kind, slot=piece.slot
        )
        tmp = out_dir / f".{piece.slot}-{i:02d}.jpg"
        result = renderer.render(html, tmp, width=width, height=height)
        cortes = list(getattr(result, "transbordos", None) or [])
        if cortes:
            out.transbordos[i] = cortes
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()[:10]
        final = out_dir / f"{piece.slot}-{i:02d}-{digest}.jpg"
        tmp.replace(final)
        out.images.append(f"{rel_dir}/{final.name}")
    return out


def data_uri(path: Path) -> Markup:
    """A imagem como ``data:`` URI (Markup: o polish e o escape não mexem nela); "" se o arquivo não existe."""
    path = Path(path)
    if not path.is_file():
        return Markup("")
    return Markup(f"data:image/jpeg;base64,{base64.b64encode(path.read_bytes()).decode('ascii')}")


# ── A grade do dia ────────────────────────────────────────────────────────


def _com_o_dia(edition: Edition, history: Iterable[Edition] | None) -> list[Edition]:
    """O histórico até o dia, com a própria edição no lugar da que estiver em data/."""
    outras = [e for e in history or () if e.date < edition.date]
    return [*sorted(outras, key=lambda e: e.date), edition]


def _passo(peca: conteudo.Peca) -> int:
    """O passo da cadeia (1 a 7) que achou o Número da peça (está no slide do método)."""
    return next((int(s.ctx["passo"]) for s in peca.slides if isinstance(s.ctx.get("passo"), int)), 0)


def _ativo(chave: str) -> str:
    """O ativo de um Número de mercado ("acum:ibov", "ibov:−13,88%" → "ibov"); "" nos outros."""
    if chave.startswith("acum:"):
        return chave[5:]
    head, sep, _ = chave.partition(":")
    return head if sep and head != "conc" else ""


def build_pieces(
    edition: Edition,
    config: Config,
    brand: dict,
    fila: Fila | None = None,
    history: Sequence[Edition] | None = None,
    *,
    aprovar: Aprovar | None = None,
    imagem: Imagem | None = None,
    avisos: list[str] | None = None,
) -> list[conteudo.Peca]:
    """As publicações do dia, na ordem da fila: manhã, stories, número, noite.

    Escolha (§2): 1) a noite: Raio-X, senão Placar do dia, senão (ou reprovada) Mercado em
    linha, montado depois do Número para não repetir o ativo dele no título; 2) o Número, sem
    a manchete nem a matéria da noite e sem as chaves publicadas na semana; 3) a manhã: Em 1
    minuto (sem a matéria da noite na capa nem a do Número), senão (ou reprovada) Terminal;
    4) os stories, com a agenda do que saiu. Os três posts de feed nunca estampam a mesma matéria.

    - ``fila``: numeração das séries (``numero_para``), entidades dos Raio-X recentes e chaves
      dos Números publicados (sem ela, uma fila vazia);
    - ``history``: :func:`.estatistica.load_history` até o dia (a edição entra de todo jeito);
    - ``aprovar``: o lint de cada peça; o gerador renderiza e passa o que transbordou. Sem ele,
      só o lint do texto.
    - ``imagem``: a imagem já renderizada de um slide de uma peça aprovada (``data:`` URI), para a
      miniatura da capa do Em 1 minuto na tela 4 dos stories. Sem ela, o desenho genérico.
    - ``avisos``: recebe uma linha por peça reprovada (o quê, por quê e o que entrou no lugar), para o
      alerta do dono (``instagram gerar --alert-out``) e o resumo da execução.

    Cada peça aprovada leva em ``redacao`` como chegou lá: ``{"modo": "molde"}`` ou, quando substitui
    outra do mesmo slot, ``{"modo": "substituto", "de": [{"peca": "Raio-X da cobertura", "falhas": [...]}]}``
    (vai para ``Post.redacao`` na fila).
    """
    day = date.fromisoformat(edition.date)
    ig = config.instagram
    election = election_mode(config, day)
    nomes, lim = tuple(ig.eleicao.nomes), ig.limiares
    fila = fila if fila is not None else Fila()
    history = _com_o_dia(edition, history)
    if aprovar is None:
        nomes_veiculos = veiculos(edition)

        def aprovar(peca: conteudo.Peca) -> list[str]:
            return redacao.lint(
                peca, election=election, nomes=nomes, veiculos=nomes_veiculos, recentes=linhas_recentes(fila, peca, day)
            )

    def numero_serie(serie: str, slot: str) -> int:
        return fila.numero_para(serie, post_id(edition.date, slot))

    caidas: dict[str, list[dict[str, Any]]] = {}  # slot → peças que caíram antes da aprovada

    def caiu(slot: str, rotulo: str, falhas: list[str], depois: str) -> None:
        caidas.setdefault(slot, []).append({"peca": rotulo, "falhas": falhas[:6]})
        if avisos is not None:
            avisos.append(f"{edition.date}: {rotulo} reprovado ({'; '.join(falhas[:3])}); {depois}.")

    def aprovada(peca: conteudo.Peca | None, depois: str) -> conteudo.Peca | None:
        if peca is None:
            return None
        problemas = aprovar(peca)
        rotulo = f"{peca.title} ({peca.chave})" if peca.chave else peca.title
        if problemas:
            log.warning("Instagram: %s reprovado no lint (%s); %s", rotulo, "; ".join(problemas[:6]), depois)
            caiu(peca.slot, rotulo, ["lint: " + p for p in problemas], depois)
            return None
        de = caidas.get(peca.slot)
        peca.redacao = {"modo": "substituto", "de": list(de)} if de else {"modo": "molde"}
        return peca

    # 1. Noite: a mais escassa primeiro.
    recentes = fila.entidades_raiox(day, lim.raiox_repeticao_dias)
    escolha = selecao.escolher_noite(edition, election=election, nomes=nomes, entidades_recentes=recentes, limiares=lim)
    noite: conteudo.Peca | None = None
    raiox_id: str | None = None
    serie_noite = numero_serie(NO.SERIE, NO.SLOT)
    if escolha.tipo == "raiox":
        noite = NO.raio_x(
            edition, brand, election=election, nomes=nomes, numero_serie=serie_noite,
            entidades_recentes=recentes, limiares=lim, story=escolha.story,
        )
        if noite is None:  # não montou: o Placar, se a base der
            escolha.tipo = "placar"
        else:
            noite = aprovada(noite, "a noite vira o Mercado em linha")
            raiox_id = noite.story_ids[0] if noite is not None else None
    if escolha.tipo == "placar":
        noite = NO.placar_do_dia(edition, brand, election=election, nomes=nomes, numero_serie=serie_noite, limiares=lim)
        noite = aprovada(noite, "a noite vira o Mercado em linha")

    # 2. Número do dia: o próximo passo da cadeia substitui o reprovado.
    exclude = {edition.lead, *(noite.story_ids if noite is not None else [])}
    usados = fila.chaves_numero(day, lim.numero_repeticao_dias)
    numero: conteudo.Peca | None = None
    rejeitadas: set[str] = set()
    apos = 0
    for _ in range(TENTATIVAS_NUMERO):
        candidato = NU.numero_do_dia(
            edition, brand, history, election=election, nomes=nomes, numero_serie=numero_serie(NU.SERIE, NU.SLOT),
            exclude=exclude, usados=usados, min_pontos=lim.grafico_min_pontos, apos=apos, rejeitadas=rejeitadas,
        )
        if candidato is None or candidato.chave in rejeitadas:
            break
        numero = aprovada(candidato, "vai o próximo passo da cadeia")
        if numero is not None:
            break
        rejeitadas.add(candidato.chave)
        apos = max(apos, min(_passo(candidato), 6))
    if numero is None and rejeitadas:
        log.warning("Instagram: sem Número do dia em %s (nenhum candidato passou no lint)", edition.date)
    elif numero is None:  # 03 e 04/10: sem IA, série com menos de 10 leituras e sem Selic/IPCA
        log.warning("Instagram: sem Número do dia em %s (a cadeia não teve candidato)", edition.date)
        if avisos is not None:
            avisos.append(f"{edition.date}: sem Número do dia (a cadeia não teve candidato).")
    if noite is None:  # o Mercado em linha, sem o ativo do Número no título
        evitar = {_ativo(numero.chave)} - {""} if numero is not None else set()
        noite = aprovada(
            ME.mercado_em_linha(
                edition, brand, history, min_pontos=lim.grafico_min_pontos, evitar=evitar, election=election, nomes=nomes
            ),
            "a noite fica sem post",
        )

    # 3. Manhã: Em 1 minuto ou, sem IA (ou reprovado), o Terminal.
    ontem = next((e for e in history if e.date == (day - timedelta(days=1)).isoformat()), None)
    # a capa não estampa o que a noite do dia estampa (o Raio-X ou os destaques do Placar) nem reabre, sem
    # número novo, o assunto do Raio-X ou do Placar da noite anterior, o post logo antes dela na grade
    ids_ontem = set(fila.materias_raiox(day, 1))
    noite_ontem = [s for s in (ontem.stories if ontem is not None else []) if s.id in ids_ontem]
    manha = MH.em_1_minuto(
        edition, brand, history, election=election, nomes=nomes, numero_serie=numero_serie(MH.SERIE, MH.SLOT),
        raiox_story_id=raiox_id, ontem=ontem, numero=numero, limiares=lim,
        evitar_capa=noite.story_ids if noite is not None else (), noite_anterior=noite_ontem,
    )
    manha = aprovada(manha, "a manhã vira o Terminal")
    # Os três posts de feed nunca estampam a mesma matéria (a manhã cede).
    principais = [assunto_principal(p) for p in (numero, noite)]
    if manha is not None and assunto_principal(manha) in [x for x in principais if x]:
        log.warning("Instagram: a capa do Em 1 minuto repete a matéria de outro post; a manhã vira o Terminal")
        caiu(manha.slot, manha.title, ["a capa repete a matéria de outro post do dia"], "a manhã vira o Terminal")
        manha = None
    if manha is None:  # o Terminal, sem o ativo do Número nem o do título do Mercado em linha
        ocupados = {_ativo(numero.chave) if numero is not None else ""}
        ocupados |= {str(s.ctx.get("ativo") or "") for s in (noite.slides if noite is not None else []) if s.template == "mercado_linha"}
        terminal = conteudo.terminal(
            edition, brand, evitar=ocupados - {""}, history=history, election=election, nomes=nomes
        )
        manha = aprovada(terminal, "a manhã fica sem post")

    # 4. Stories, com a agenda do que de fato foi gerado.
    feed = [p for p in (manha, numero, noite) if p is not None]
    agenda = ST.agenda_das_pecas(feed, horarios(config, day))
    capa = None
    capa_img = ""
    if manha is not None and manha.serie == MH.SERIE and manha.story_ids:
        capa = next((s for s in edition.stories if s.id == manha.story_ids[0]), None)
        capa_img = (imagem(manha, 0) if imagem is not None else "") or ""
    stories = ST.stories_manha(
        edition, brand, history, election=election, nomes=nomes, agenda=agenda, capa=capa, raiox_story_id=raiox_id,
        capa_img=capa_img,
    )
    stories = aprovada(stories, "os stories não saem")
    return [p for p in (manha, stories, numero, noite) if p is not None]


def _post(piece: conteudo.Peca, edition: Edition, pid: str, due: datetime, expires: datetime, images: list[str]) -> Post:
    return Post(
        id=pid,
        date=edition.date,
        kind=piece.kind,
        due=due.isoformat(),
        expires=expires.isoformat(),
        images=images,
        caption=piece.caption,
        alt_texts=[conteudo.alt(s.alt) for s in piece.slides],
        title=piece.title,
        serie=piece.serie,
        numero_serie=piece.numero_serie,
        story_ids=list(piece.story_ids),
        chave=piece.chave,
        entidades=list(piece.entidades),
        formula=piece.formula,
        redacao=dict(piece.redacao),
    )


def generate(
    edition: Edition,
    config: Config,
    *,
    root: Path,
    media_dir: Path,
    now: datetime,
    renderer_factory: Callable[[], Renderer] | None = None,
    launch: bool = True,
    fila: Fila | None = None,
    history: Sequence[Edition] | None = None,
    avisos: list[str] | None = None,
) -> list[Post]:
    """Renderiza as publicações do dia em ``media_dir/<data>/`` e devolve os itens da fila.

    Slots cujo prazo já passou não são gerados (a edição saiu tarde demais). Com ``launch``,
    inclui o manifesto (a fila só o aceita enquanto não saiu). ``fila`` dá a numeração das
    séries e o que saiu nos últimos dias; ``history`` (padrão: ``root/data`` dos últimos 90
    dias) alimenta as séries do mercado, a véspera e o manifesto. ``avisos`` recebe as peças
    reprovadas e o que entrou no lugar (:func:`build_pieces`).
    """
    if not config.instagram.enabled:
        log.info("Instagram desligado em config/site.yaml")
        return []
    tz = ZoneInfo(config.site.timezone)
    day = date.fromisoformat(edition.date)
    brand = brand_context(config)
    env = environment()
    if history is None:
        history = E.load_history(Path(root) / "data", day, HISTORICO_DIAS)
    history = _com_o_dia(edition, history)
    election = election_mode(config, day)
    nomes = tuple(config.instagram.eleicao.nomes)
    nomes_veiculos = veiculos(edition)
    rel_dir = edition.date
    out_dir = Path(media_dir) / rel_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    feitas: dict[int, Renderizada] = {}

    def janela(piece: conteudo.Peca) -> tuple[datetime, datetime] | None:
        slot = config.instagram.slots.get(piece.slot)
        return slot_window(slot, day, tz) if slot else None

    posts: list[Post] = []
    with (renderer_factory or Renderer)() as renderer:

        def aprovar(piece: conteudo.Peca) -> list[str]:
            w = janela(piece)
            antes = linhas_recentes(fila, piece, day) if fila is not None else []
            if w is None or now >= w[1]:  # não sai: só o lint do texto, sem renderizar
                return redacao.lint(piece, election=election, nomes=nomes, veiculos=nomes_veiculos, recentes=antes)
            feita = _render_piece(renderer, env, piece, brand, out_dir, rel_dir)
            problemas = redacao.lint(
                piece, election=election, nomes=nomes, veiculos=nomes_veiculos, overflow=feita.overflow(), recentes=antes
            )
            if problemas:
                for img in feita.images:  # imagens da peça reprovada não ficam na pasta
                    (Path(media_dir) / img).unlink(missing_ok=True)
            else:
                feitas[id(piece)] = feita
            return problemas

        def imagem(piece: conteudo.Peca, i: int) -> str:
            feita = feitas.get(id(piece))
            return data_uri(Path(media_dir) / feita.images[i]) if feita is not None and i < len(feita.images) else ""

        pieces = build_pieces(edition, config, brand, fila, history, aprovar=aprovar, imagem=imagem, avisos=avisos)
        for piece in pieces:
            w = janela(piece)
            if w is None:
                log.info("Instagram: %s não sai neste dia da semana", piece.title)
                continue
            due, expires = w
            if now >= expires:
                log.warning("Instagram: %s não gerado — o prazo (%s) já passou", piece.title, expires.strftime("%H:%M"))
                continue
            images = feitas[id(piece)].images
            if piece.kind == "story":
                for n, (image, slide) in enumerate(zip(images, piece.slides), 1):
                    post = _post(piece, edition, f"{post_id(edition.date, piece.slot)}-{n}", due + STORY_STEP * (n - 1), expires, [image])
                    post.caption, post.alt_texts, post.title = "", [], f"{piece.title} {n}/{len(images)}"
                    posts.append(post)
            else:
                posts.append(_post(piece, edition, post_id(edition.date, piece.slot), due, expires, images))
            log.info("Instagram: %s pronto (%d imagem(ns), sai a partir de %s)", piece.title, len(images), due.strftime("%d/%m %H:%M"))
        if launch and edition.mode == "ai":
            piece = MF.manifesto(edition, brand, history)
            feita = _render_piece(renderer, env, piece, brand, out_dir, rel_dir)
            problemas = redacao.lint(piece, election=election, nomes=nomes, overflow=feita.overflow())
            if problemas:
                log.warning("Instagram: manifesto reprovado no lint (%s); não entra na fila hoje", "; ".join(problemas[:6]))
                if avisos is not None:
                    avisos.append(f"{edition.date}: manifesto reprovado (lint: {'; '.join(problemas[:3])}); não entra na fila hoje.")
                for img in feita.images:
                    (Path(media_dir) / img).unlink(missing_ok=True)
            else:
                start = datetime.combine(day, time(0, 0), tz)
                post = _post(piece, edition, LAUNCH_ID, start, start + timedelta(days=LAUNCH_DAYS), feita.images)
                post.permanent = True
                posts.insert(0, post)
    return posts


def clean_media(fila: Fila, media_dir: Path, today: date, keep_days: int) -> list[Path]:
    """Apaga pastas de dias antigos e imagens que nenhuma publicação da fila usa mais."""
    media_dir = Path(media_dir)
    removed: list[Path] = []
    referenced = {img for p in fila.posts for img in p.images}
    cutoff = today - timedelta(days=keep_days)
    for folder in sorted(p for p in media_dir.iterdir() if p.is_dir()) if media_dir.is_dir() else []:
        try:
            folder_day = date.fromisoformat(folder.name)
        except ValueError:
            continue
        if folder_day < cutoff:
            shutil.rmtree(folder)
            removed.append(folder)
            continue
        for image in folder.glob("*.jpg"):
            if f"{folder.name}/{image.name}" not in referenced:
                image.unlink()
                removed.append(image)
    return removed
