"""A agenda da semana dos Tribunais (``data/tribunais/agenda.json``): o que está pautado no
Plenário presencial do STF e nos órgãos julgadores do STJ, no formato único de
``qijournal/agenda/contrato.py`` (o mesmo de ``data/congresso/agenda.json``).

Como ``achados.py``: funções puras, sem rede e sem disco. ``dados.py`` entrega o que a rodada leu
(a pauta, os temas e o cruzamento da pauta com o acervo) e grava o texto que sai daqui.

**STF** (só o Plenário presencial; do Plenário Virtual e das Turmas a fonte só dá a contagem):

- uma linha por sessão do Plenário, com a hora que a fonte informa; dia que o calendário marca
  como de sessão presencial e ainda não tem processos entra como ``pauta: nao_publicada``; dia
  cuja consulta falhou, como ``nao_coletada`` (a mesclagem repõe a pauta lida na véspera);
- cada processo com o estágio que a pauta traz: ``retorno_de_vista`` (com o ministro),
  ``referendo_de_cautelar`` (o incidente diz "referendo") e ``destaque`` (com o ministro);
- o tema de repercussão geral entra pelo processo-paradigma da lista de temas (RE 1326541 é o
  Tema 1218); o resumo oficial só nas ações de controle concentrado e, na repercussão geral, só
  quando o acervo não mostra pessoa física entre as partes;
- as contagens das sessões virtuais vão para ``estado.stf.virtuais``.

**STJ** (a seleção roda sobre o arquivo COMPLETO de pautas futuras, antes de qualquer corte):

- ficam fora, só em contagem por sessão: processo em segredo de justiça, agravo contra decisão
  da Vice-Presidência ou da Presidência, recurso interno em lista (AgInt, AgRg, EDcl), recurso
  de Turma sem tema e as demais petições;
- entram: o recurso ligado a tema afetado (repetitivo ou IAC pendente, pelo número de registro
  nos precedentes qualificados), a proposta de afetação ou de relevância e os recursos
  principais da Corte Especial e das Seções;
- a fonte não informa hora, assunto nem se a sessão é virtual: nada disso é afirmado (``hora``
  e ``modalidade`` nulas; nenhum resumo); a ordem na pauta também não vem (``ordem`` nula);
- ``estado.stj.proximo`` guarda a primeira sessão DEPOIS da janela de coleta com recurso
  repetitivo pautado (dentro da janela, a sessão já está em ``sessoes``).

**Privacidade** (travada aqui e, de novo, na validação do contrato): parte só ente público ou
pessoa jurídica do catálogo de litigantes (``config/tribunais_partes.yaml``); pessoa física e
advogado nunca; relator só ministro da composição; processo em segredo só em contagem.

**Semana acumulada**: as duas fontes só publicam pautas futuras. :func:`juntar` usa a mesclagem
do contrato (sessão com data anterior a hoje, dentro da semana, fica do arquivo anterior, marcada
``como_pautado_em``) e mantém também a sessão do próprio dia quando a fonte deixa de trazê-la
antes de o dia acabar (nenhuma das duas fontes diz a que horas a sessão do dia sai da lista).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from ..agenda import contrato
from . import stj as stj_mod
from .modelo import Regras
from .partes import Catalogo, nome_proprio

ARQUIVO = contrato.ARQUIVO  # data/tribunais/agenda.json
TETO_BYTES = 60_000  # padrão; config/agenda.yaml → tetos.arquivos.tribunais_bytes vence (quem chama passa)
PARTES_MAXIMO = 6  # partes públicas por item
FONTE_STF, FONTE_STJ, FONTE_TEMAS_STJ = "stf_pauta", "stj_pautas", "stj_precedentes"
NOME_FONTE = {FONTE_STF: "STF: pauta das sessões", FONTE_STJ: "STJ: pautas de julgamento", FONTE_TEMAS_STJ: "STJ: precedentes qualificados"}
# Plenário do STF: a sigla que casa com o arquivo de exemplo do contrato ("PLEN", não "PL").
PLENARIO_STF = ("Plenário", "PLEN")
ORGAOS_STJ = {
    "CE": "Corte Especial", "S1": "Primeira Seção", "S2": "Segunda Seção", "S3": "Terceira Seção", "T1": "Primeira Turma",
    "T2": "Segunda Turma", "T3": "Terceira Turma", "T4": "Quarta Turma", "T5": "Quinta Turma", "T6": "Sexta Turma",
}
RELATOR_FORA_DA_SELECAO = frozenset({"vice", "presidencia"})  # Vice-Presidência e Presidência do STJ: só contagem
_RE_AREA = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_RE_CLASSE = re.compile(r"^[A-Za-z]{1,16}$")
_RE_PROPOSTA = re.compile(r"PROPOSTA DE (?:AFETACAO|RELEVANCIA)")


class AcimaDoTeto(ValueError):
    """A agenda não cabe no teto de tamanho nem depois de emagrecer: fica o arquivo anterior."""


@dataclass(frozen=True)
class Entrada:
    """O que a rodada traz de um tribunal. ``pauta`` é ``None`` quando a fonte da pauta falhou:
    o tribunal não é refeito e a parte dele fica como estava no arquivo anterior (as ``fontes``
    vêm sempre, com ``ok`` falso e o aviso).

    - STF: ``pauta`` = ``stf.coletar_pauta``; ``temas`` = os temas de ``stf.temas_rg``;
    - STJ: ``pauta`` = os itens de ``stj.ler_pautas`` (o arquivo completo); ``temas`` =
      ``stj.montar_temas`` (com ``registros``);
    - ``acervo`` = ``contas.cruzamento_da_pauta`` (área e partes públicas de cada processo
      pautado, e os que estão em segredo); pode faltar."""

    fontes: tuple[contrato.FonteAgenda, ...] = ()
    pauta: Any = None
    temas: Any = None
    acervo: Mapping[str, Any] | None = None


# ── peças comuns ─────────────────────────────────────────────────────────────────────────────


def _cfg(regras: Regras) -> Mapping[str, Any]:
    cfg = regras.limiares.get("agenda")
    return cfg if isinstance(cfg, Mapping) else {}


def _lista(cfg: Mapping[str, Any], chave: str, trib: str | None = None) -> frozenset[str]:
    valor = cfg.get(chave) or ()
    if trib is not None:
        valor = valor.get(trib) or () if isinstance(valor, Mapping) else ()
    return frozenset(str(v) for v in valor) if isinstance(valor, (list, tuple, set, frozenset)) else frozenset()


def _area(valor: Any) -> str | None:
    """O id da macroárea, ou ``None`` quando não é um id (a tela só mostra palavra de área oficial)."""
    return valor if isinstance(valor, str) and _RE_AREA.match(valor) else None


def _cortar(texto: Any, maximo: int) -> str | None:
    """O texto oficial limpo; acima de ``maximo``, cortado no fim de uma palavra com reticências."""
    limpo = contrato.limpar_texto(texto)
    if limpo is None or len(limpo) <= maximo:
        return limpo
    return limpo[: maximo - 1].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def fonte(chave: str, instituicao: str, entrada: Mapping[str, Any] | None, *, coletado_em: str | None = None) -> contrato.FonteAgenda:
    """A entrada de uma fonte em ``meta.json`` (``nome``, ``url``, ``ok``, ``data_dados``,
    ``coletado_em``, ``aviso``, ``velho_dias``) → a :class:`~qijournal.agenda.contrato.FonteAgenda`
    do arquivo da agenda. Fonte que falhou sai sem data (a mesclagem repõe a do último dia bom) e
    sempre com aviso; fonte boa sem instante de coleta usa ``coletado_em``."""
    entrada = entrada or {}
    ok = entrada.get("ok") is True and bool(entrada.get("data_dados"))
    velho = entrada.get("velho_dias")
    aviso = _cortar(entrada.get("aviso"), contrato.TEXTO_MAXIMO["aviso"])
    return contrato.FonteAgenda(
        chave=chave,
        instituicao=instituicao,
        nome=NOME_FONTE.get(chave) or contrato.limpar_texto(str(entrada.get("nome") or chave).replace(" · ", ": ")) or chave,
        url=str(entrada.get("url") or ""),
        ok=ok,
        data_dados=str(entrada["data_dados"])[:10] if ok else None,
        coletado_em=(entrada.get("coletado_em") or coletado_em) if ok else None,
        aviso=aviso if ok else (aviso or "A coleta desta fonte falhou; ficam os dados do último dia bom."),
        velho_dias=velho if isinstance(velho, int) and not isinstance(velho, bool) and velho >= 1 else None,
    )


def _falhou(f: contrato.FonteAgenda, aviso: str, *, parcial: bool = False) -> contrato.FonteAgenda:
    """A fonte passa a constar como falha, com o aviso. Falha inteira (nada lido): sem data
    própria, e a mesclagem repõe a do último dia bom. Falha ``parcial`` (parte foi lida hoje): a
    data continua sendo a de hoje, que é a do que o arquivo mostra de novo; o que não foi lido
    fica do arquivo anterior, marcado com a data dele."""
    if parcial:
        return replace(f, ok=False, aviso=contrato.limpar_texto(aviso))
    return replace(f, ok=False, data_dados=None, coletado_em=None, aviso=contrato.limpar_texto(aviso))


# ── privacidade: as partes que podem aparecer ────────────────────────────────────────────────


def _nome_de_ente(nome: Any, catalogo: Catalogo) -> str | None:
    """O nome de um ente público fora do catálogo, só quando o próprio nome COMEÇA pela marca de
    ente (``"AGENCIA NACIONAL ... - ANPD"``); o que vem depois de ``" - "`` não é publicado. Nome
    de gente seguido de órgão (``"FULANO - DEFENSORIA PUBLICA"``) não passa."""
    cabeca = contrato.limpar_texto(str(nome or "").split(" - ", 1)[0])
    if not cabeca or catalogo.tipo_pelo_nome(cabeca) != "ente":
        return None
    return contrato.limpar_texto(nome_proprio(cabeca))


def _publicavel(parte: Mapping[str, Any], catalogo: Catalogo) -> tuple[str, str] | None:
    """``(tipo, nome)`` da parte que pode ser publicada, ou ``None``. Vale o litigante do catálogo
    (os grupos fixos e as famílias: cada Estado, Ministério Público, Defensoria, Assembleia e
    Município) e, fora dele, só o ente público reconhecido pelo começo do nome. Pessoa jurídica
    fora do catálogo (``cnpj-…``, ``nome-…``) e pessoa física nunca."""
    grupo, tipo, nome = parte.get("grupo"), parte.get("tipo"), parte.get("nome")
    if tipo not in contrato.TIPOS_PARTE or not isinstance(grupo, str) or not grupo:
        return None
    feito_na_hora = grupo.startswith(("cnpj-", "nome-"))
    if not feito_na_hora and grupo not in catalogo.grupos and nome:
        catalogo.reconhecer_nome(str(nome))  # a família dos municípios entra no catálogo quando o nome é reconhecido
    if not feito_na_hora and grupo in catalogo.grupos:
        lit = catalogo.litigante(grupo)
        publico = contrato.limpar_texto(lit.nome) if lit is not None else None
        return (lit.tipo, publico) if lit is not None and lit.tipo in contrato.TIPOS_PARTE and publico else None
    if tipo == "ente":
        publico = _nome_de_ente(nome, catalogo)
        return ("ente", publico) if publico else None
    return None


def partes_publicas(partes: Iterable[Mapping[str, Any]] | None, catalogo: Catalogo) -> tuple[contrato.Parte, ...]:
    """As partes de um processo que a agenda pode nomear: ente público ou pessoa jurídica do
    catálogo de litigantes, com polo. Pessoa física, advogado, autoridade e qualificador nunca;
    repetição some; no máximo :data:`PARTES_MAXIMO`, na ordem da fonte."""
    saida: list[contrato.Parte] = []
    for parte in partes or ():
        if not isinstance(parte, Mapping) or parte.get("polo") not in contrato.POLOS:
            continue
        achado = _publicavel(parte, catalogo)
        if achado is None:
            continue
        tipo, nome = achado
        nova = contrato.Parte(polo=str(parte["polo"]), tipo=tipo, nome=nome[: contrato.TEXTO_MAXIMO["nome"]])
        if nova not in saida:
            saida.append(nova)
    return tuple(saida[:PARTES_MAXIMO])


# ── STF: o Plenário presencial ───────────────────────────────────────────────────────────────


def temas_do_stf(temas: Iterable[Mapping[str, Any]] | None) -> dict[tuple[str, str], contrato.Tema]:
    """``(classe, número)`` do processo-paradigma → o tema de repercussão geral (número e título
    oficial): é o vínculo que os dossiês já publicam no link do tema."""
    saida: dict[tuple[str, str], contrato.Tema] = {}
    for tema in temas or ():
        caso = tema.get("leading_case") if isinstance(tema, Mapping) else None
        numero = tema.get("numero") if isinstance(tema, Mapping) else None
        titulo = _cortar(tema.get("titulo"), contrato.TEXTO_MAXIMO["titulo"]) if isinstance(tema, Mapping) else None
        if not isinstance(caso, Mapping) or isinstance(numero, bool) or not isinstance(numero, int) or numero < 1 or not titulo:
            continue
        chave = (str(caso.get("classe") or ""), re.sub(r"\D", "", str(caso.get("numero") or "")).lstrip("0"))
        saida.setdefault(chave, contrato.Tema(numero=numero, titulo=titulo, tipo="repercussao_geral"))
    return saida


def _ministro(regras: Regras, trib: str, ministro_id: Any) -> str | None:
    m = regras.composicao[trib].ministro(ministro_id) if isinstance(ministro_id, str) and ministro_id else None
    return contrato.limpar_texto(m.nome) if m is not None else None


def _estagios_stf(item: Mapping[str, Any], regras: Regras) -> tuple[str, str | None, tuple[str, ...]]:
    """O estágio principal, o detalhe dele (o ministro da vista ou do destaque) e os demais."""
    candidatos = ["pautado"]
    if item.get("vista"):
        candidatos.append("retorno_de_vista")
    if "REFERENDO" in stj_mod.chave_rapida(item.get("incidente")):
        candidatos.append("referendo_de_cautelar")
    if item.get("destaque"):
        candidatos.append("destaque")
    principal = contrato.estagio_principal(candidatos)
    detalhe = {"retorno_de_vista": item.get("vista_id"), "destaque": item.get("destaque_id")}.get(principal)
    return principal, _ministro(regras, "stf", detalhe), tuple(e for e in candidatos if e not in (principal, "pautado"))


def _alcance_stf(base: str, sufixos: Sequence[str], tem_tema: bool, item: Mapping[str, Any], publicas: Sequence[Mapping[str, Any]],
                 cfg: Mapping[str, Any]) -> str:
    if tem_tema or item.get("repercussao_geral"):
        return "repercussao_geral"
    if base in _lista(cfg, "classes_controle"):
        federais = _lista(cfg, "passivo_federal")
        return "controle_federal" if any(p.get("polo") == "passivo" and p.get("grupo") in federais for p in publicas) else "controle_estadual"
    divergencia = _lista(cfg, "classes_divergencia")
    if base in divergencia or divergencia.intersection(sufixos):
        return "embargos_de_divergencia"
    if base in _lista(cfg, "classes_originarias", "stf"):
        return "acao_originaria"
    return "individual"


def item_do_stf(item: Mapping[str, Any], regras: Regras, catalogo: Catalogo, *, temas: Mapping[tuple[str, str], contrato.Tema],
                acervo: Mapping[str, Any] | None) -> contrato.ItemAgenda | None:
    """Um processo da pauta do Plenário → o item da agenda; ``None`` quando a classe ou o número
    não dão um identificador. Processo que o acervo marca em segredo volta com ``segredo``
    verdadeiro e sem dado nenhum (a sessão só o conta)."""
    cfg = _cfg(regras)
    base, *sufixos = str(item.get("classe") or "").split("-")
    numero = re.sub(r"\D", "", str(item.get("numero") or "")).lstrip("0")
    if not _RE_CLASSE.match(base) or not numero:
        return None
    identificador = contrato.identificador_de(base, numero)
    link = str((regras.fontes.get("links_oficiais") or {}).get("stf") or "").format(classe=base, numero=numero)
    em_segredo = set((acervo or {}).get("segredo") or ())
    if item.get("id") in em_segredo:
        return contrato.ItemAgenda(identificador=identificador, tipo=base, alcance="individual", estagio="pautado", link=link, segredo=True)
    processo = ((acervo or {}).get("processos") or {}).get(item.get("id")) or {}
    publicas = [p for p in processo.get("partes") or () if isinstance(p, Mapping)]
    tema = temas.get((base, numero)) if not sufixos else None
    alcance = _alcance_stf(base, sufixos, tema is not None, item, publicas, cfg)
    principal, detalhe, outros = _estagios_stf(item, regras)
    # O resumo oficial: sempre no controle concentrado (as partes são institucionais); na
    # repercussão geral, só quando o acervo conhece o processo e não mostra pessoa física.
    com_resumo = base in _lista(cfg, "classes_controle") or (
        alcance == "repercussao_geral" and bool(processo) and not processo.get("pessoa_fisica"))
    ordem = item.get("ordem")
    return contrato.ItemAgenda(
        identificador=identificador,
        tipo=base,
        alcance=alcance,
        estagio=principal,
        estagio_detalhe=detalhe,
        outros_estagios=outros,
        link=link,
        ementa=_cortar(item.get("resumo"), contrato.TEXTO_MAXIMO["ementa"]) if com_resumo else None,
        tema=tema,
        area=_area(processo.get("area")),
        relator=contrato.Relator(nome=nome) if (nome := _ministro(regras, "stf", item.get("relator_id"))) else None,
        ordem=ordem if isinstance(ordem, int) and not isinstance(ordem, bool) and ordem >= 1 else None,
        partes=partes_publicas(publicas, catalogo),
    )


def _juntar_repetidos(itens: Iterable[contrato.ItemAgenda]) -> tuple[contrato.ItemAgenda, ...]:
    """Linhas repetidas da fonte (o mesmo processo com dois incidentes) viram um item: fica o
    primeiro, com os estágios dos dois."""
    por_id: dict[str, contrato.ItemAgenda] = {}
    for item in itens:
        antes = por_id.get(item.identificador)
        if antes is None:
            por_id[item.identificador] = item
        elif not antes.segredo and not item.segredo:
            todos = {*antes.estagios, *item.estagios} - {"pautado"}
            principal = contrato.estagio_principal(todos or {"pautado"})
            detalhe = antes.estagio_detalhe if antes.estagio == principal else item.estagio_detalhe if item.estagio == principal else None
            por_id[item.identificador] = replace(antes, estagio=principal, estagio_detalhe=detalhe, outros_estagios=tuple(todos - {principal}))
    return tuple(por_id.values())


def _dias_do_stf(pauta: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Os dias de sessão presencial da coleta (``dias``); numa coleta do formato antigo, sem a
    chave, os dias saem dos próprios itens."""
    dias = pauta.get("dias")
    if isinstance(dias, list):
        return [d for d in dias if isinstance(d, Mapping) and d.get("data")]
    por_dia: dict[str, dict[tuple[Any, Any], dict[str, Any]]] = defaultdict(dict)
    for i in pauta.get("itens") or ():
        if isinstance(i, Mapping) and i.get("data"):
            sessao = por_dia[str(i["data"])].setdefault((i.get("orgao"), i.get("sessao")), {
                "data": i["data"], "orgao": i.get("orgao"), "sessao": i.get("sessao"), "hora": i.get("hora"), "itens": 0})
            sessao["itens"] += 1
    return [{"data": dia, "lida": True, "sessoes": list(sessoes.values())} for dia, sessoes in sorted(por_dia.items())]


def _sessao_do_plenario(data: str, link: str, *, pauta: str, sufixo: str | None = None, hora: str | None = None,
                        itens: tuple[contrato.ItemAgenda, ...] = (), fora: tuple[tuple[str, int], ...] = ()) -> contrato.SessaoAgenda:
    """Uma sessão presencial do Plenário do STF (o id é ``stf-plenario-<data>``; a segunda sessão do
    mesmo dia ganha a hora no id)."""
    partes_do_id = ("plenario", data) if sufixo is None else ("plenario", data, sufixo)
    return contrato.SessaoAgenda(
        id=contrato.id_de_sessao(contrato.STF, *partes_do_id), instituicao=contrato.STF, orgao=PLENARIO_STF[0], orgao_sigla=PLENARIO_STF[1],
        foro="plenario", tipo="julgamento", data=data, situacao="agendada", modalidade="presencial", link=link, hora=hora, pauta=pauta,
        itens=itens, fora=fora)


def sessoes_do_stf(pauta: Mapping[str, Any], regras: Regras, catalogo: Catalogo, *, janela: tuple[str, str], link: str,
                   temas: Iterable[Mapping[str, Any]] | None = None, acervo: Mapping[str, Any] | None = None,
                   ) -> tuple[tuple[contrato.SessaoAgenda, ...], contrato.EstadoInstituicao]:
    """A coleta da pauta do STF (``stf.coletar_pauta``) → as sessões do Plenário presencial na
    janela e o estado do STF (até onde a pauta está publicada, as contagens das sessões virtuais
    que tocam a janela e, se houver, a primeira sessão depois dela)."""
    de, ate = janela
    por_tema = temas_do_stf(temas)
    itens_por: dict[tuple[str, Any], list[Mapping[str, Any]]] = defaultdict(list)
    turmas: Counter[str] = Counter()
    for i in pauta.get("itens") or ():
        if not isinstance(i, Mapping) or not i.get("data"):
            continue
        if i.get("orgao") == "PL":
            itens_por[(str(i["data"]), i.get("sessao"))].append(i)
        elif de <= str(i["data"]) <= ate:
            turmas["itens_de_turma"] += 1

    todas: list[contrato.SessaoAgenda] = []
    for dia in _dias_do_stf(pauta):
        data = str(dia["data"])[:10]
        if not dia.get("lida"):  # a consulta do dia falhou: a agenda não afirma o que não leu
            todas.append(_sessao_do_plenario(data, link, pauta="nao_coletada"))
            continue
        sessoes = [s for s in dia.get("sessoes") or () if isinstance(s, Mapping)]
        plenario = sorted((s for s in sessoes if s.get("orgao") == "PL"), key=lambda s: (s.get("hora") or "99:99", str(s.get("sessao") or "")))
        if de <= data <= ate:
            turmas["sessoes_de_turma"] += sum(1 for s in sessoes if s.get("orgao") in ("T1", "T2"))
        if not plenario:
            if not sessoes:  # o calendário marca o dia e a fonte ainda não lista processos
                todas.append(_sessao_do_plenario(data, link, pauta="nao_publicada"))
            continue
        for n, sessao in enumerate(plenario):
            bruta = sessao.get("hora")
            hora = bruta if isinstance(bruta, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", bruta) else None
            brutos = itens_por.get((data, sessao.get("sessao")), [])
            itens = _juntar_repetidos(x for x in (item_do_stf(i, regras, catalogo, temas=por_tema, acervo=acervo) for i in brutos) if x)
            todas.append(_sessao_do_plenario(
                data, link, pauta="publicada" if brutos else "nao_publicada", hora=hora, itens=itens,
                sufixo=None if n == 0 else (hora or str(n + 1)).replace(":", ""),
                fora=(("outros", len(brutos) - len(itens)),) if len(brutos) > len(itens) else ()))

    na_janela = tuple(s for s in todas if de <= s.data <= ate)
    depois = sorted((s for s in todas if s.data > ate), key=lambda s: (not s.itens, s.data, s.hora or "99:99"))
    com_pauta = [s.data for s in todas if s.pauta == "publicada"]
    virtuais = []
    for v in pauta.get("virtuais") or ():
        inicio, fim = (v.get("inicio"), v.get("fim")) if isinstance(v, Mapping) else (None, None)
        if not (isinstance(inicio, str) and isinstance(fim, str) and inicio <= fim and fim >= de and inicio <= ate):
            continue
        quantos = [n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 0 for n in (v.get("PL"), v.get("T1"), v.get("T2"))]
        virtuais.append(contrato.JulgamentoVirtual(de=inicio[:10], ate=fim[:10], plenario=quantos[0], turma1=quantos[1], turma2=quantos[2]))
    estado = contrato.EstadoInstituicao(
        instituicao=contrato.STF,
        pauta_publicada_ate=max(com_pauta) if com_pauta else None,
        proximo=depois[0] if depois else None,
        virtuais=tuple(virtuais),
        contagens=(("sessoes", len(na_janela)), ("itens", sum(s.total_na_fonte for s in na_janela)), *turmas.items()),
    )
    return na_janela, estado


# ── STJ: a seleção sobre o arquivo completo ──────────────────────────────────────────────────


def foro_do_stj(orgao: str) -> str | None:
    """``CE`` → ``corte_especial``; ``S1``… → ``secao``; ``T1``… → ``turma``; outro → ``None``."""
    if orgao == "CE":
        return "corte_especial"
    return {"S": "secao", "T": "turma"}.get(orgao[:1]) if re.fullmatch(r"[ST]\d", orgao or "") else None


def _e_proposta(item: Mapping[str, Any], cfg: Mapping[str, Any]) -> bool:
    """Proposta de afetação (repetitivo) ou de relevância: pela sigla da petição ou pelo nome dela."""
    return str(item.get("tipo") or "") in _lista(cfg, "propostas") or bool(_RE_PROPOSTA.search(stj_mod.chave_rapida(item.get("peticao"))))


def motivo_fora_do_stj(item: Mapping[str, Any], foro: str, com_tema: bool, cfg: Mapping[str, Any], em_segredo: frozenset[str] = frozenset()) -> str | None:
    """Por que um item da pauta do STJ fica fora da seleção (só em contagem), ou ``None`` quando
    entra. Cada item conta uma vez, pelo primeiro motivo: segredo de justiça; agravo contra
    decisão da Vice-Presidência ou da Presidência (são todos recursos internos: contados antes
    deles, senão a conta da Vice sumiria); recurso interno em lista; recurso de Turma sem tema; o
    resto das petições."""
    if item.get("segredo") or (item.get("registro") and item.get("registro") in em_segredo):
        return "segredo"
    if item.get("fora") in RELATOR_FORA_DA_SELECAO:
        return "vice_presidencia"
    if item.get("interno"):
        return "recurso_interno"
    principal = str(item.get("tipo") or "principal") == "principal"
    if (principal and com_tema) or _e_proposta(item, cfg):
        return None
    if principal:
        return None if foro in ("corte_especial", "secao") else "sem_tema"
    return "outros"


def _tema_do_stj(temas: Sequence[Mapping[str, Any]]) -> contrato.Tema | None:
    for tema in temas:
        numero, titulo = tema.get("numero"), _cortar(tema.get("titulo"), contrato.TEXTO_MAXIMO["titulo"])
        tipo = tema.get("tipo") if tema.get("tipo") in contrato.TIPOS_TEMA else "outro"
        if isinstance(numero, int) and not isinstance(numero, bool) and numero >= 1 and titulo:
            return contrato.Tema(numero=numero, titulo=titulo, tipo=str(tipo))
    return None


def item_do_stj(item: Mapping[str, Any], regras: Regras, catalogo: Catalogo, *, temas: Sequence[Mapping[str, Any]], link: str,
                acervo: Mapping[str, Any] | None = None) -> contrato.ItemAgenda | None:
    """Um item selecionado da pauta do STJ → o item da agenda (``None`` sem identificador). A
    fonte não informa assunto: a área vem do tema ou, sem tema, do processo no acervo; não há
    resumo nem ordem."""
    cfg = _cfg(regras)
    classe = str(item.get("classe") or "")
    numero = re.sub(r"\D", "", str(item.get("numero") or "")).lstrip("0")
    if not _RE_CLASSE.match(classe) or not numero:
        return None
    registro = str(item.get("registro") or "")
    principal = str(item.get("tipo") or "principal") == "principal"
    do_tema = temas if principal else ()
    if do_tema:
        alcance = "repetitivo"
    elif _e_proposta(item, cfg):
        alcance = "afetacao"
    elif classe in _lista(cfg, "classes_divergencia"):
        alcance = "embargos_de_divergencia"
    elif classe in _lista(cfg, "classes_originarias", "stj"):
        alcance = "acao_originaria"
    else:
        alcance = "individual"
    area = (do_tema[0].get("area") if do_tema else None) or (((acervo or {}).get("processos") or {}).get(registro) or {}).get("area")
    modelo_do_link = str((regras.fontes.get("links_oficiais") or {}).get("stj") or "")
    partes = [catalogo.completar_stj(dict(p)) for p in item.get("partes") or () if isinstance(p, Mapping)]  # type: ignore[arg-type]
    return contrato.ItemAgenda(
        identificador=contrato.identificador_de(classe, numero),
        tipo=classe,
        alcance=alcance,
        estagio="pautado",
        link=modelo_do_link.format(registro=registro) if registro and modelo_do_link else link,
        tema=_tema_do_stj(do_tema),
        area=_area(area),
        relator=contrato.Relator(nome=nome) if (nome := _ministro(regras, "stj", item.get("relator_id"))) else None,
        partes=partes_publicas(partes, catalogo),
    )


def _mais_forte(a: contrato.ItemAgenda, b: contrato.ItemAgenda) -> contrato.ItemAgenda:
    """Entre duas linhas do mesmo processo na mesma sessão, a de maior alcance na régua."""
    return a if contrato.ALCANCES.index(a.alcance) <= contrato.ALCANCES.index(b.alcance) else b


def sessoes_do_stj(itens: Iterable[Mapping[str, Any]], regras: Regras, catalogo: Catalogo, *, janela: tuple[str, str], link: str,
                   temas: Iterable[Mapping[str, Any]] | None = None, acervo: Mapping[str, Any] | None = None,
                   ) -> tuple[tuple[contrato.SessaoAgenda, ...], contrato.EstadoInstituicao]:
    """O arquivo completo de pautas futuras (``stj.ler_pautas``) → as sessões da janela, cada uma
    com os itens selecionados e a contagem do que ficou fora, e o estado do STJ: até onde há
    pauta publicada, as contagens da janela e a primeira sessão DEPOIS da janela com recurso
    repetitivo pautado."""
    de, ate = janela
    cfg = _cfg(regras)
    nomes = {**ORGAOS_STJ, **{str(k): str(v) for k, v in (regras.composicao["stj"].orgaos or {}).items()}}
    por_registro = stj_mod.temas_por_registro(temas, tipos=_lista(cfg, "tipos_de_tema") or stj_mod.TIPOS_DE_TEMA_AFETADO)
    em_segredo = frozenset(str(x) for x in (acervo or {}).get("segredo") or ())
    grupos: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for i in itens:
        if isinstance(i, Mapping) and i.get("sessao") and foro_do_stj(str(i.get("orgao") or "")):
            grupos[(str(i["sessao"])[:10], str(i["orgao"]))].append(i)

    todas: list[contrato.SessaoAgenda] = []
    for (data, orgao), linhas in sorted(grupos.items()):
        foro = foro_do_stj(orgao) or "turma"
        fora: Counter[str] = Counter()
        escolhidos: dict[str, contrato.ItemAgenda] = {}
        for i in linhas:
            do_tema = por_registro.get(str(i.get("registro") or ""), [])
            motivo = motivo_fora_do_stj(i, foro, bool(do_tema), cfg, em_segredo)
            novo = None if motivo else item_do_stj(i, regras, catalogo, temas=do_tema, link=link, acervo=acervo)
            if novo is None:
                fora[motivo or "outros"] += 1
            elif novo.identificador in escolhidos:  # o mesmo processo em duas linhas da sessão: um item só
                escolhidos[novo.identificador] = _mais_forte(escolhidos[novo.identificador], novo)
            else:
                escolhidos[novo.identificador] = novo
        todas.append(contrato.SessaoAgenda(
            id=contrato.id_de_sessao(contrato.STJ, orgao, data), instituicao=contrato.STJ, orgao=nomes.get(orgao, orgao), orgao_sigla=orgao,
            foro=foro, tipo="julgamento", data=data, situacao="agendada", link=link, itens=tuple(escolhidos.values()), fora=tuple(fora.items())))

    na_janela = tuple(s for s in todas if de <= s.data <= ate)
    ordem_do_foro = {"corte_especial": 0, "secao": 1, "turma": 2}
    depois = sorted((s for s in todas if s.data > ate and any(i.alcance == "repetitivo" for i in s.itens)),
                    key=lambda s: (s.data, ordem_do_foro.get(s.foro, 9), s.orgao_sigla or ""))
    estado = contrato.EstadoInstituicao(
        instituicao=contrato.STJ,
        pauta_publicada_ate=max((s.data for s in todas), default=None),
        proximo=depois[0] if depois else None,
        contagens=(
            ("sessoes", len(na_janela)),
            ("itens", sum(s.total_na_fonte for s in na_janela)),
            ("repetitivos", sum(1 for s in na_janela for i in s.itens if i.alcance == "repetitivo")),
            ("propostas", sum(1 for s in na_janela for i in s.itens if i.alcance == "afetacao")),
        ),
    )
    return na_janela, estado


# ── o arquivo ────────────────────────────────────────────────────────────────────────────────


def montar(regras: Regras, *, hoje: str, gerado_em: str, catalogo: Catalogo, stf: Entrada | None = None,
           stj: Entrada | None = None) -> contrato.Agenda | None:
    """A agenda desta rodada, só com o que ela leu (a junção com o arquivo anterior é de
    :func:`juntar`). ``None`` quando nenhum dos dois tribunais veio.

    O tribunal cuja pauta falhou (``pauta`` nula, calendário do STF sem resposta, arquivo do STJ
    vazio ou sem os precedentes) não ganha sessões nem estado: a fonte dele sai com ``ok`` falso e
    a parte dele fica como estava. Quando só parte do calendário do STF respondeu, entram as
    sessões lidas e a fonte fica marcada como falha (com a data de hoje), para as sessões do mês
    que não respondeu não virarem zero."""
    hoje = hoje[:10]
    janela = contrato.janela_de_coleta(hoje)
    fontes: list[contrato.FonteAgenda] = []
    sessoes: list[contrato.SessaoAgenda] = []
    estado: list[contrato.EstadoInstituicao] = []

    if stf is not None:
        do_stf = {f.chave: f for f in stf.fontes if f.instituicao == contrato.STF}
        principal = do_stf.get(FONTE_STF)
        pauta = stf.pauta if isinstance(stf.pauta, Mapping) and principal is not None and principal.ok else None
        if pauta is not None and principal is not None:
            bruto = pauta.get("calendarios")
            calendarios: Mapping[str, Any] = bruto if isinstance(bruto, Mapping) else {}
            if calendarios.get("lidos") == 0:  # nenhum calendário respondeu: nada foi lido
                do_stf[FONTE_STF] = _falhou(principal, "O calendário da pauta do STF não respondeu; ficam as sessões do último dia bom.")
                pauta = None
            elif calendarios.get("falhas"):
                do_stf[FONTE_STF] = _falhou(principal, "Parte do calendário da pauta do STF não respondeu; as sessões não lidas ficam como estavam.",
                                            parcial=True)
        if pauta is not None and principal is not None:
            novas, do_estado = sessoes_do_stf(pauta, regras, catalogo, janela=janela, link=principal.url, temas=stf.temas, acervo=stf.acervo)
            sessoes += novas
            estado.append(do_estado)
        fontes += do_stf.values()

    if stj is not None:
        do_stj = {f.chave: f for f in stj.fontes if f.instituicao == contrato.STJ}
        principal = do_stj.get(FONTE_STJ)
        itens = list(stj.pauta) if isinstance(stj.pauta, (list, tuple)) and principal is not None and principal.ok else None
        if itens is not None and principal is not None and not itens:
            do_stj[FONTE_STJ] = _falhou(principal, "O arquivo de pautas do STJ veio vazio; ficam as sessões do último dia bom.")
            itens = None
        if itens is not None and stj.temas is None:  # sem os precedentes, um repetitivo sairia como caso individual
            itens = None
        if itens is not None and principal is not None:
            novas, do_estado = sessoes_do_stj(itens, regras, catalogo, janela=janela, link=principal.url, temas=stj.temas, acervo=stj.acervo)
            sessoes += novas
            estado.append(do_estado)
        fontes += do_stj.values()

    if not fontes:
        return None
    return contrato.Agenda(lado=contrato.LADO_TRIBUNAIS, gerado_em=gerado_em, hoje=hoje, fontes=tuple(fontes), sessoes=tuple(sessoes),
                           estado=tuple(estado), janela=janela)


def juntar(novo: contrato.Agenda, anterior: contrato.Agenda | None, hoje: str) -> contrato.Agenda:
    """A agenda da rodada junto com o arquivo anterior: a mesclagem do contrato (fonte que falhou
    não vira zero; sessão já passada da semana corrente fica, marcada ``como_pautado_em``; a poda é
    na virada da semana) e mais uma regra dos tribunais: a sessão de HOJE que a fonte deixou de
    trazer também fica do arquivo anterior. As fontes são de pautas futuras e nenhuma diz a que
    horas a sessão do dia sai da lista (o arquivo do STJ de 24/11/2026, publicado de manhã, ainda
    trazia as sessões daquele dia); se sair antes da coleta da noite, sem isto a sessão sumiria e
    não chegaria ao "já decidido" de amanhã."""
    hoje = hoje[:10]
    junto = contrato.mesclar_com_anterior(novo, anterior, hoje)
    if anterior is None or anterior.lado != novo.lado:
        return junto
    presentes = {s.id for s in junto.sessoes}
    de_hoje = []
    for antiga in anterior.sessoes:
        if antiga.data == hoje and antiga.id not in presentes:
            pautado_em = antiga.como_pautado_em or anterior.data_dados_de(antiga.instituicao) or anterior.hoje
            de_hoje.append(replace(antiga, como_pautado_em=min(pautado_em, hoje)))
    return replace(junto, sessoes=(*junto.sessoes, *de_hoje)) if de_hoje else junto


def _sem(agenda: contrato.Agenda, campo: str) -> contrato.Agenda:
    """A mesma agenda sem um campo dos itens (``ementa`` ou ``partes``), nas sessões e no próximo."""
    vazio: Any = None if campo == "ementa" else ()

    def sessao_sem(s: contrato.SessaoAgenda) -> contrato.SessaoAgenda:
        return replace(s, itens=tuple(replace(i, **{campo: vazio}) for i in s.itens))

    estado = tuple(replace(e, proximo=sessao_sem(e.proximo)) if e.proximo is not None else e for e in agenda.estado)
    return replace(agenda, sessoes=tuple(sessao_sem(s) for s in agenda.sessoes), estado=estado)


def texto(agenda: contrato.Agenda, teto: int = TETO_BYTES) -> tuple[str, list[str]]:
    """O texto do arquivo (validado pelo contrato: levanta ``contrato.ErroDeContrato`` se a agenda
    não cumprir) dentro do teto de tamanho, e o que foi cortado para caber. Acima do teto a agenda
    emagrece por etapas, sempre igual: primeiro saem os resumos oficiais, depois as partes; se
    ainda assim não couber, levanta :class:`AcimaDoTeto` (quem chama mantém o arquivo anterior)."""
    cortes: list[str] = []
    atual = agenda
    saida = contrato.para_json(atual)
    for campo, nome in (("ementa", "os resumos oficiais"), ("partes", "as partes")):
        if len(saida.encode("utf-8")) <= teto:
            break
        atual = _sem(atual, campo)
        cortes.append(nome)
        saida = contrato.para_json(atual)
    if len(saida.encode("utf-8")) > teto:
        raise AcimaDoTeto(f"a agenda tem {len(saida.encode('utf-8'))} bytes, acima do teto de {teto}, mesmo sem resumos e sem partes")
    return saida, cortes


__all__ = [
    "ARQUIVO", "AcimaDoTeto", "Entrada", "TETO_BYTES", "fonte", "foro_do_stj", "item_do_stf", "item_do_stj", "juntar", "montar",
    "motivo_fora_do_stj", "partes_publicas", "sessoes_do_stf", "sessoes_do_stj", "temas_do_stf", "texto",
]
