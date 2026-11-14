"""Base de votações nominais: um arquivo por Casa e ano em ``data/congresso/base/``.

Só entram votações **nominais** (as que registram o voto de cada parlamentar);
as simbólicas não dizem quem votou o quê. Cada arquivo (``cd-2026.json``,
``sf-2026.json``) traz as pessoas que votaram no ano e, para cada votação, um
texto com **um caractere por pessoa**, na ordem da lista de pessoas:

====  =======================================================================
``S``  Sim
``N``  Não
``A``  Abstenção
``O``  Obstrução (a bancada se retira para não dar quórum)
``P``  presidia a sessão (art. 17 do regimento da Câmara, art. 51 do Senado)
``E``  presente, sem registrar voto
``V``  votou em votação secreta (não se sabe como)
``J``  ausência justificada (Senado: licença, missão, atividade parlamentar)
``F``  falta registrada (Senado: não compareceu)
``-``  sem registro (Câmara: ausente ou fora do exercício; Senado: fora do mandato)
====  =======================================================================

O ano corrente é refeito a cada atualização; os anos fechados nunca mudam. O
arquivo é escrito com uma votação por linha, para o git guardar só a diferença.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import text
from . import VERSAO, fontes
from .fontes import Coletor

log = logging.getLogger(__name__)

CASAS = {"cd": "Câmara dos Deputados", "sf": "Senado Federal"}
PLENARIO = "PLEN"

SIM, NAO, ABSTENCAO, OBSTRUCAO = "S", "N", "A", "O"
PRESIDIU, SEM_VOTO, SECRETO, JUSTIFICADA, FALTA, SEM_REGISTRO = "P", "E", "V", "J", "F", "-"
POSICAO = {SIM: SIM, NAO: NAO, OBSTRUCAO: NAO}  # a obstrução é de quem não quer a matéria votada
VOTOU = frozenset((SIM, NAO, ABSTENCAO, OBSTRUCAO, SECRETO))
PRESENTE = VOTOU | {PRESIDIU, SEM_VOTO}

# Orientação de bancada: Sim, Não, Liberado, Obstrução.
ORIENTACAO = {"sim": "S", "nao": "N", "liberado": "L", "livre": "L", "obstrucao": "O"}
GOVERNO, OPOSICAO, MAIORIA, MINORIA = "Governo", "Oposição", "Maioria", "Minoria"
_LIDERANCAS = {"governo": GOVERNO, "gov": GOVERNO, "oposicao": OPOSICAO, "maioria": MAIORIA, "minoria": MINORIA}

# Tipos de votação (pela descrição oficial): o mérito é o texto principal.
MERITO, URGENCIA, DESTAQUE, PROCEDIMENTO, RECURSO, AUTORIDADE, OUTRO = (
    "merito",
    "urgencia",
    "destaque",
    "procedimento",
    "recurso",
    "autoridade",
    "outro",
)
TIPOS = {
    MERITO: "texto principal",
    URGENCIA: "urgência",
    DESTAQUE: "destaque ou emenda",
    PROCEDIMENTO: "requerimento de procedimento",
    RECURSO: "recurso",
    AUTORIDADE: "escolha de autoridade",
    OUTRO: "outra",
}
EMENTA_MAX = 240
DESCRICAO_MAX = 320
# Os campos de cada votação gravada. 2: "o que se votou" (``abertura``). Um ano fechado gravado
# num formato mais antigo é refeito uma vez (:func:`qijournal.congresso.dados.atualizar_base`).
FORMATO = 2

# Siglas de proposição que são a matéria em si (e não o requerimento sobre ela).
_PRINCIPAIS = ("PEC", "PLP", "PL", "MPV", "PLV", "PDL", "PRC", "PLN", "PDC", "PRS", "PLS", "PLC", "PDS", "VET", "MSF", "MSC")
_ORDEM_PRINCIPAL = {sigla: i for i, sigla in enumerate(_PRINCIPAIS)}


@dataclass
class Pessoa:
    """Quem votou no ano. ``partidos``: ``(índice da 1ª votação, sigla)``, em ordem."""

    id: int
    nome: str
    uf: str = ""
    partidos: list[tuple[int, str]] = field(default_factory=list)

    def partido_em(self, indice: int) -> str:
        """O partido na votação de índice ``indice`` (o do registro mais próximo antes dela)."""
        atual = self.partidos[0][1] if self.partidos else ""
        for inicio, sigla in self.partidos:
            if inicio > indice:
                break
            atual = sigla
        return atual


@dataclass
class Votacao:
    id: str
    data: str  # AAAA-MM-DD
    orgao: str  # sigla; "PLEN" = Plenário
    descricao: str
    tipo: str
    votos: str  # um caractere por pessoa
    hora: str = ""  # HH:MM
    # Câmara: o que foi posto em votação, nas palavras da Mesa ("Votação do DTQ 5: PL: Emenda de
    # Plenário nº 4", "Destaque para Votação em Separado do art. 9º"): diz quem pediu o destaque e qual trecho.
    abertura: str = ""
    aprovada: bool | None = None
    secreta: bool = False
    prop: str = ""  # "PL 1234/2023": a matéria afetada
    prop_id: int | None = None
    ementa: str = ""
    apresentada: str = ""  # data de apresentação da matéria, quando a fonte a traz junto
    temas: list[str] = field(default_factory=list)  # macrotemas (config/congresso.yaml)
    ori: dict[str, str] = field(default_factory=dict)  # bancada → S/N/L/O

    @property
    def plenario(self) -> bool:
        return self.orgao == PLENARIO

    def conta(self) -> Counter[str]:
        return Counter(self.votos)


@dataclass
class AnoVotos:
    """As votações nominais de uma Casa num ano."""

    casa: str
    ano: int
    pessoas: list[Pessoa] = field(default_factory=list)
    votacoes: list[Votacao] = field(default_factory=list)
    atualizado: str = ""  # AAAA-MM-DD
    formato: int = FORMATO  # o formato em que o ano foi gravado (ver FORMATO)

    def indice(self) -> dict[int, int]:
        return {p.id: i for i, p in enumerate(self.pessoas)}


# ── normalização ─────────────────────────────────────────────────────────────

_PARTIDO = {
    "PODEMOS": "PODE",
    "PROGRESSISTAS": "PP",
    "PROGRES": "PP",
    "REPUBLICAN": "REPUBLICANOS",
    "REPUBLICA": "REPUBLICANOS",
    "SOLIDARIED": "SOLIDARIEDADE",
    "SD": "SOLIDARIEDADE",
    "CIDADAN": "CIDADANIA",
    "PCDOB": "PCdoB",
    "PC DO B": "PCdoB",
    "S.PART.": "S/PARTIDO",
    "S/PARTIDO": "S/PARTIDO",
    "SEM PARTIDO": "S/PARTIDO",
    "SEM REGISTRO": "S/PARTIDO",  # Senado: entre um partido e outro
    "UNIAO": "UNIÃO",
    "MISSAO": "MISSÃO",
}


def partido(sigla: str | None) -> str:
    """Sigla do partido num formato só (``Podemos`` → ``PODE``, ``Novo`` → ``NOVO``)."""
    bruto = re.sub(r"\s+", " ", (sigla or "").strip())
    if not bruto:
        return ""
    chave = text.strip_accents(bruto).upper()
    if chave in _PARTIDO:
        return _PARTIDO[chave]
    return "PCdoB" if chave == "PCDOB" else bruto.upper()


# Bloco escrito com as siglas coladas ("PpMdbPtb", "PslPpPsd...").
_BLOCO = re.compile(r"(?:[A-Z][a-z]+){2,}\.*")


def bancada(rotulo: str | None) -> str:
    """Rótulo de quem orienta: as lideranças (Governo, Oposição, Maioria, Minoria)
    num nome só; partidos pela sigla; blocos e federações como vêm."""
    bruto = re.sub(r"\s+", " ", (rotulo or "").strip())
    chave = text.normalize(bruto)
    if chave in _LIDERANCAS:
        return _LIDERANCAS[chave]
    if re.match(r"^(bl|bloco|fdr|fed|federacao|b)\b", chave) or "." in bruto or _BLOCO.fullmatch(bruto):
        return bruto  # bloco ou federação ("Bl UniPpPsd...", "PpMdbPtb", "Fdr PT-PCdoB-PV")
    return partido(bruto)


def orientacao(valor: str | None) -> str:
    return ORIENTACAO.get(text.normalize(valor or ""), "")


# A descrição oficial começa pelo resultado e pelo objeto ("Aprovado o Substitutivo…",
# "Mantido o texto", "Rejeitado o Requerimento"; no Senado, "Votação nominal do
# Projeto…"): tirado o resultado, a primeira palavra do objeto diz o tipo.
_RESULTADO = re.compile(
    r"^(?:votacao nominal |votacao |em votacao )?"
    r"(?:(?:aprovad|rejeitad|mantid|suprimid|prejudicad|adotad|acolhid|retirad|restabelecid)[oa]s?,? )?"
    r"(?:em (?:primeiro|segundo|1o|2o) turno,? )?(?:em globo,? )?(?:d?[oa]s? )?"
)
_SUBSTITUTIVO = re.compile(r"\bsubstitutiv[oa]\b")
_OBJETO_MERITO = re.compile(
    r"^(?:substitutivo|subemenda substitutiva|projeto|medida provisoria|proposta de emenda|redacao final|texto base"
    r"|texto principal|parecer|veto|materia|proposicao|mensagem|pec\b|pl\b|plp\b|pdl\b|mpv\b|plv\b)"
)
_OBJETO_DESTAQUE = re.compile(
    r"^(?:texto|expressao|expressoes|dispositivo|dispositivos|artigo|art\b|inciso|paragrafo|alinea|destaque|destaques"
    r"|emenda|emendas|subemenda|dvs|dtq|trecho|parte|caput|item|anexo|capitulo|admissibilidade d[oa]s? destaque"
    r"|supressao|suprimir)"
)
_PROCEDIMENTO = re.compile(
    r"\b(requerimento|requerimentos|retirada de pauta|adiamento|encerramento d[ae] discussao|inversao de pauta"
    r"|preferencia|quebra de intersticio|dispensa de intersticio|sessao permanente|questao de ordem)\b"
)
_MERITO = re.compile(
    r"\b(substitutivo|projeto de |medida provisoria|proposta de emenda|redacao final|texto base"
    r"|subemenda substitutiva|parecer|projeto de lei de conversao|veto)\b"
)


def tipo_da_votacao(
    descricao: str, *, sigla: str = "", secreta: bool = False, contexto: str = "", padrao: str = OUTRO
) -> str:
    """Classifica a votação pela descrição oficial (``contexto``: texto extra da fonte,
    como o resumo que o Senado dá ao requerimento votado; ``padrao``: o tipo quando a
    descrição não diz nada, como nas votações antigas do Senado, descritas pela ementa).

    Urgência vem primeiro ("requerimento de urgência" é urgência); depois vale o
    objeto da frase: "Aprovado o Substitutivo…, ressalvados os destaques" é texto
    principal; "Mantido o texto" e "Rejeitada a Emenda nº 3" são destaques; emenda
    substitutiva é texto principal.
    """
    d = text.normalize(descricao)
    if secreta or sigla in ("MSF", "OFS"):
        return AUTORIDADE
    if "urgencia" in d or "urgencia" in text.normalize(contexto):
        return URGENCIA
    objeto = _RESULTADO.sub("", d, count=1)
    inicio = " ".join(objeto.split(" ")[:8])
    if objeto.startswith(("requerimento", "requerimentos")):
        return PROCEDIMENTO
    if objeto.startswith(("recurso", "recursos")):
        return RECURSO
    if objeto.startswith(("emenda", "emendas", "subemenda")):
        if _SUBSTITUTIVO.search(inicio) or objeto.startswith(("emendas do senado", "emenda do senado")):
            return MERITO
        return DESTAQUE
    if _OBJETO_MERITO.search(objeto) and not objeto.startswith(("texto do", "texto da", "texto destacad")):
        return MERITO
    if _OBJETO_DESTAQUE.search(objeto):
        return DESTAQUE
    if _PROCEDIMENTO.search(d):
        return PROCEDIMENTO
    if _MERITO.search(d):
        return MERITO
    return padrao


# "primeiro", "2º", "1°"... seguido de "turno" na mesma oração ("entre o primeiro e o segundo turno" dá os dois)
_TURNO = re.compile(r"(?:\b(primeiro|segundo)\b|\b([12])\s*[º°o])(?=[^.;]{0,25}?\bturno\b)", re.IGNORECASE)


def turno(descricao: str) -> int | None:
    """O turno de uma votação de PEC, quando a descrição oficial diz um só ("Aprovada, em
    primeiro turno, ...", "(2º turno)"). Quem cita os dois (a quebra de interstício "entre o
    primeiro e o segundo turno") fica sem."""
    achados = {1 if (m.group(1) or "").lower() == "primeiro" or m.group(2) == "1" else 2 for m in _TURNO.finditer(descricao or "")}
    return achados.pop() if len(achados) == 1 else None


# O que a Constituição exige para o Sim vencer: três quintos da Casa na proposta de emenda à
# Constituição (art. 60, § 2º) e a maioria absoluta no projeto de lei complementar (art. 69).
# Vale para o texto e para os destaques (quem quer manter o trecho ou aprovar a emenda precisa
# dos mesmos votos). Nas demais matérias decide a maioria dos presentes: não há número fixo.
CADEIRAS = {"cd": 513, "sf": 81}
_SIGLA = re.compile(r"^\s*([A-Z]+)\b")


def quorum(casa: str, prop: str | None) -> int | None:
    """Os votos Sim que a Constituição exige nas votações de conteúdo de uma PEC (três
    quintos: 308 na Câmara, 49 no Senado) ou de um PLP (maioria absoluta: 257 e 41). Sem
    número fixo (``None``) nas demais."""
    cadeiras = CADEIRAS.get(casa)
    achado = _SIGLA.match(prop or "")
    if not cadeiras or not achado:
        return None
    if achado.group(1) == "PEC":
        return -(-cadeiras * 3 // 5)
    if achado.group(1) == "PLP":
        return cadeiras // 2 + 1
    return None


_NOVA_EMENTA = re.compile(r"\s*NOVA EMENTA:\s*", re.IGNORECASE)


def ementa_valida(texto: str | None) -> tuple[str, str]:
    """``(a ementa que vale, a original)``: quando o texto muda no caminho, a Câmara anota a
    "NOVA EMENTA" depois da original. Sem nova ementa, a original fica vazia."""
    limpa = re.sub(r"\s+", " ", (texto or "").strip())
    partes = _NOVA_EMENTA.split(limpa, maxsplit=1)
    if len(partes) == 2 and partes[1].strip():
        return partes[1].strip(), partes[0].strip()
    return limpa, ""


def _curto(valor: str | None, limite: int) -> str:
    return text.truncate(re.sub(r"\s+", " ", (valor or "").strip()), limite)


def _segmentos(registros: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """``[(índice da votação, partido)…]`` em ordem → os trechos em que o partido muda."""
    trechos: list[tuple[int, str]] = []
    for indice, sigla in registros:
        if sigla and (not trechos or trechos[-1][1] != sigla):
            trechos.append((indice, sigla))
    return trechos


def _fechar(
    casa: str,
    ano: int,
    brutas: list[dict[str, Any]],
    nomes: Mapping[int, tuple[str, str]],
    atualizado: str,
) -> AnoVotos:
    """Monta o :class:`AnoVotos` a partir das votações com o voto por id de pessoa
    (``votos``: ``{id: caractere}``; ``partidos``: ``{id: sigla}``)."""
    brutas.sort(key=lambda v: (v["data"], v.get("hora") or "", v["id"]))
    ids = sorted({pid for v in brutas for pid in v["votos"]}, key=lambda pid: (text.normalize(nomes.get(pid, ("", ""))[0]), pid))
    posicao = {pid: i for i, pid in enumerate(ids)}
    registros: dict[int, list[tuple[int, str]]] = defaultdict(list)
    votacoes = []
    for k, v in enumerate(brutas):
        linha = [SEM_REGISTRO] * len(ids)
        for pid, voto in v["votos"].items():
            linha[posicao[pid]] = voto
            sigla = v["partidos"].get(pid) or ""
            if sigla:
                registros[pid].append((k, sigla))
        votacoes.append(
            Votacao(
                id=v["id"],
                data=v["data"],
                hora=v.get("hora") or "",
                orgao=v.get("orgao") or PLENARIO,
                descricao=_curto(v.get("descricao"), DESCRICAO_MAX),
                abertura=_curto(v.get("abertura"), DESCRICAO_MAX),
                tipo=v["tipo"],
                votos="".join(linha),
                aprovada=v.get("aprovada"),
                secreta=bool(v.get("secreta")),
                prop=v.get("prop") or "",
                prop_id=v.get("prop_id"),
                ementa=_curto(v.get("ementa"), EMENTA_MAX),
                apresentada=str(v.get("apresentada") or "")[:10],
                temas=list(v.get("temas") or []),
                ori=dict(sorted((v.get("ori") or {}).items())),
            )
        )
    pessoas = [
        Pessoa(id=pid, nome=nomes.get(pid, ("", ""))[0], uf=nomes.get(pid, ("", ""))[1], partidos=_segmentos(registros[pid]))
        for pid in ids
    ]
    return AnoVotos(casa=casa, ano=ano, pessoas=pessoas, votacoes=votacoes, atualizado=atualizado)


# ── Câmara ───────────────────────────────────────────────────────────────────

_VOTO_CAMARA = {"sim": SIM, "nao": NAO, "abstencao": ABSTENCAO, "obstrucao": OBSTRUCAO, "artigo 17": PRESIDIU, "": SEM_VOTO}

Temas = Callable[[int | None, int | None], list[str]]


def _principal(afetadas: list[dict[str, str]]) -> dict[str, str] | None:
    """A matéria em si entre as proposições afetadas (PEC, PLP, PL, MPV… antes de
    requerimentos); na falta, a primeira."""
    if not afetadas:
        return None
    return min(afetadas, key=lambda r: _ORDEM_PRINCIPAL.get(r.get("proposicao_siglaTipo") or "", len(_PRINCIPAIS)))


def _titulo(sigla: str | None, numero: Any, ano: Any) -> str:
    sigla = (sigla or "").strip()
    numero = str(numero or "").strip().lstrip("0")
    ano = str(ano or "").strip()
    if not sigla or not numero:
        return ""
    return f"{sigla} {numero}/{ano}" if ano and ano != "0" else f"{sigla} {numero}"


def camara_ano(coletor: Coletor, ano: int, *, temas: Temas | None = None, atualizado: str = "", validade_h: float = 20.0) -> AnoVotos:
    """As votações nominais da Câmara em ``ano``, dos arquivos em lote (Plenário e comissões)."""
    votos: dict[str, dict[int, str]] = defaultdict(dict)
    partidos: dict[str, dict[int, str]] = defaultdict(dict)
    nomes: dict[int, tuple[str, str]] = {}
    for r in fontes.csv_linhas(fontes.camara_arquivo(coletor, "votacoesVotos", ano, validade_h=validade_h)):
        pid = fontes.inteiro(r.get("deputado_id"))
        vid = (r.get("idVotacao") or "").strip()
        if pid is None or not vid:
            continue
        votos[vid][pid] = _VOTO_CAMARA.get(text.normalize(r.get("voto") or ""), SEM_VOTO)
        partidos[vid][pid] = partido(r.get("deputado_siglaPartido"))
        nomes[pid] = ((r.get("deputado_nome") or "").strip(), (r.get("deputado_siglaUf") or "").strip())

    orientacoes: dict[str, dict[str, str]] = defaultdict(dict)
    for r in fontes.csv_linhas(fontes.camara_arquivo(coletor, "votacoesOrientacoes", ano, validade_h=validade_h)):
        vid = (r.get("idVotacao") or "").strip()
        valor = orientacao(r.get("orientacao"))
        if vid in votos and valor:
            orientacoes[vid][bancada(r.get("siglaBancada"))] = valor

    afetadas: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in fontes.csv_linhas(fontes.camara_arquivo(coletor, "votacoesProposicoes", ano, validade_h=validade_h)):
        vid = (r.get("idVotacao") or "").strip()
        if vid in votos:
            afetadas[vid].append(r)

    brutas = []
    for r in fontes.csv_linhas(fontes.camara_arquivo(coletor, "votacoes", ano, validade_h=validade_h)):
        vid = (r.get("id") or "").strip()
        if vid not in votos:
            continue
        registro = (r.get("dataHoraRegistro") or "").strip()
        principal = _principal(afetadas.get(vid, []))
        prop_id = fontes.inteiro(principal.get("proposicao_id")) if principal else None
        prop_ano = fontes.inteiro(principal.get("proposicao_ano")) if principal else None
        aprovacao = (r.get("aprovacao") or "").strip()
        brutas.append(
            {
                "id": vid,
                "data": (r.get("data") or registro[:10]).strip(),
                "hora": registro[11:16],
                "orgao": (r.get("siglaOrgao") or "").strip() or PLENARIO,
                "descricao": r.get("descricao") or "",
                "abertura": r.get("ultimaAberturaVotacao_descricao") or "",
                "tipo": tipo_da_votacao(r.get("descricao") or ""),
                "aprovada": {"1": True, "0": False}.get(aprovacao),
                "prop": _titulo(principal.get("proposicao_siglaTipo"), principal.get("proposicao_numero"), principal.get("proposicao_ano")) if principal else "",
                "prop_id": prop_id,
                "ementa": ementa_valida(principal.get("proposicao_ementa") if principal else "")[0],
                "temas": temas(prop_id, prop_ano) if temas and prop_id else [],
                "ori": orientacoes.get(vid, {}),
                "votos": votos[vid],
                "partidos": partidos[vid],
            }
        )
    resultado = _fechar("cd", ano, brutas, nomes, atualizado)
    log.info("Câmara %d: %d votações nominais, %d deputados", ano, len(resultado.votacoes), len(resultado.pessoas))
    return resultado


# ── Senado ───────────────────────────────────────────────────────────────────

_VOTO_SENADO = {
    "sim": SIM,
    "nao": NAO,
    "abstencao": ABSTENCAO,
    "obstrucao": OBSTRUCAO,
    "votou": SECRETO,
    "p nrv": SEM_VOTO,  # presente, não registrou voto
    "ncom": FALTA,  # não compareceu
}


def _voto_senado(sigla: str | None) -> str:
    chave = text.normalize(sigla or "")
    if chave in _VOTO_SENADO:
        return _VOTO_SENADO[chave]
    if chave.startswith("presidente"):
        return PRESIDIU
    if chave.startswith("p ") or chave == "p":  # P-OD (obstrução declarada) e variações de presente
        return OBSTRUCAO if "od" in chave.split() else SEM_VOTO
    return JUSTIFICADA  # AP, MIS, LS, LP, LAP, LC, NA…: fora do plenário por motivo registrado


def senado_ano(
    coletor: Coletor,
    ano: int,
    *,
    temas: Callable[[int | None], list[str]] | None = None,
    atualizado: str = "",
    validade_h: float = 20.0,
    fim: str | None = None,
) -> AnoVotos:
    """As votações nominais do Plenário do Senado em ``ano`` (API ``/votacao``), com a
    orientação das lideranças (``/plenario/votacao/orientacaoBancada``)."""
    ultimo = fim or f"{ano}-12-31"
    lista = fontes.senado_api(coletor, "votacao", validade_h=validade_h, dataInicio=f"{ano}-01-01", dataFim=ultimo)
    orientacoes: dict[int, dict[str, Any]] = {}
    try:
        bruto = fontes.senado_api(
            coletor,
            f"plenario/votacao/orientacaoBancada/{ano}0101/{ultimo.replace('-', '')}",
            validade_h=validade_h,
        )
        for item in (bruto.get("votacoes") if isinstance(bruto, dict) else None) or []:
            seq = fontes.inteiro(item.get("sequencialVotacao"))
            if seq is not None:
                orientacoes[seq] = item
    except Exception as exc:  # noqa: BLE001 — a orientação é acessória: sem ela, o ano sai sem governismo
        log.warning("Senado %d: orientações de bancada indisponíveis (%s)", ano, exc)

    brutas = []
    nomes: dict[int, tuple[str, str]] = {}
    for item in lista if isinstance(lista, list) else []:
        if not isinstance(item, dict):
            continue
        seq = fontes.inteiro(item.get("sequencialVotacao"))
        chave = seq if seq is not None else fontes.inteiro(item.get("codigoSessaoVotacao"))
        if chave is None:
            continue
        secreta = str(item.get("votacaoSecreta") or "").upper().startswith("S")
        votos: dict[int, str] = {}
        partidos: dict[int, str] = {}
        for voto in item.get("votos") or []:
            pid = fontes.inteiro(voto.get("codigoParlamentar"))
            if pid is None:
                continue
            votos[pid] = _voto_senado(voto.get("siglaVotoParlamentar"))
            partidos[pid] = partido(voto.get("siglaPartidoParlamentar"))
            nomes[pid] = ((voto.get("nomeParlamentar") or "").strip(), (voto.get("siglaUFParlamentar") or "").strip())
        if not votos:
            continue
        guia = orientacoes.get(seq) if seq is not None else None
        ori = {}
        hora = ""
        if guia:
            for o in guia.get("orientacoesLideranca") or []:
                valor = orientacao(o.get("voto"))
                if valor:
                    ori[bancada(o.get("partido"))] = valor
            hora = str(guia.get("dataInicioVotacao") or "")[11:16]
        sigla = (item.get("sigla") or "").strip()
        id_processo = fontes.inteiro(item.get("idProcesso"))
        descricao = item.get("descricaoVotacao") or ""
        resultado = str(item.get("resultadoVotacao") or "").upper()
        brutas.append(
            {
                "id": f"sf{chave}",
                "data": str(item.get("dataSessao") or "")[:10],
                "hora": hora,
                "orgao": PLENARIO,
                "descricao": descricao,
                "tipo": tipo_da_votacao(
                    descricao,
                    sigla=sigla,
                    secreta=secreta,
                    contexto=str((guia or {}).get("descricaoVotacao") or ""),
                    padrao=MERITO if sigla in _PRINCIPAIS else OUTRO,
                ),
                "aprovada": {"A": True, "R": False}.get(resultado),
                "secreta": secreta,
                "prop": (item.get("identificacao") or "").strip(),
                "prop_id": id_processo,
                "ementa": item.get("ementa") or "",
                "apresentada": item.get("dataApresentacao") or "",
                "temas": temas(id_processo) if temas and id_processo else [],
                "ori": ori,
                "votos": votos,
                "partidos": partidos,
            }
        )
    resultado_ano = _fechar("sf", ano, brutas, nomes, atualizado)
    log.info("Senado %d: %d votações nominais, %d senadores", ano, len(resultado_ano.votacoes), len(resultado_ano.pessoas))
    return resultado_ano


# ── arquivo ──────────────────────────────────────────────────────────────────


def caminho(pasta: Path, casa: str, ano: int) -> Path:
    return Path(pasta) / f"{casa}-{ano}.json"


def _compacto(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, separators=(",", ":"))


def _votacao_dict(v: Votacao) -> dict[str, Any]:
    d: dict[str, Any] = {"id": v.id, "d": v.data, "o": v.orgao, "t": v.tipo, "x": v.descricao, "v": v.votos}
    if v.hora:
        d["h"] = v.hora
    if v.abertura:
        d["ab"] = v.abertura
    if v.aprovada is not None:
        d["ap"] = 1 if v.aprovada else 0
    if v.secreta:
        d["sec"] = 1
    if v.prop:
        d["p"] = v.prop
    if v.prop_id is not None:
        d["pi"] = v.prop_id
    if v.ementa:
        d["e"] = v.ementa
    if v.apresentada:
        d["da"] = v.apresentada
    if v.temas:
        d["tm"] = v.temas
    if v.ori:
        d["or"] = v.ori
    return d


def serializar(ano: AnoVotos) -> str:
    """JSON com uma pessoa e uma votação por linha (o git guarda só o que mudou)."""
    pessoas = ",\n".join(_compacto([p.id, p.nome, p.uf, [list(t) for t in p.partidos]]) for p in ano.pessoas)
    votacoes = ",\n".join(_compacto(_votacao_dict(v)) for v in ano.votacoes)
    cabeca = _compacto({"v": VERSAO, "f": FORMATO, "casa": ano.casa, "ano": ano.ano, "atualizado": ano.atualizado})[:-1]
    return f'{cabeca},\n"pessoas":[\n{pessoas}\n],\n"votacoes":[\n{votacoes}\n]}}\n'


def desserializar(dados: Mapping[str, Any]) -> AnoVotos:
    pessoas = [
        Pessoa(id=int(p[0]), nome=str(p[1]), uf=str(p[2]), partidos=[(int(i), partido(str(s))) for i, s in p[3]])
        for p in dados.get("pessoas") or []
    ]
    votacoes = [
        Votacao(
            id=str(v["id"]),
            data=str(v["d"]),
            orgao=str(v.get("o") or PLENARIO),
            descricao=str(v.get("x") or ""),
            tipo=str(v.get("t") or OUTRO),
            votos=str(v.get("v") or ""),
            hora=str(v.get("h") or ""),
            abertura=str(v.get("ab") or ""),
            aprovada=None if v.get("ap") is None else bool(v["ap"]),
            secreta=bool(v.get("sec")),
            prop=str(v.get("p") or ""),
            prop_id=v.get("pi"),
            ementa=str(v.get("e") or ""),
            apresentada=str(v.get("da") or ""),
            temas=list(v.get("tm") or []),
            ori=dict(v.get("or") or {}),
        )
        for v in dados.get("votacoes") or []
    ]
    return AnoVotos(
        casa=str(dados.get("casa") or ""),
        ano=int(dados.get("ano") or 0),
        pessoas=pessoas,
        votacoes=votacoes,
        atualizado=str(dados.get("atualizado") or ""),
        formato=int(dados.get("f") or 1),
    )


def ler(pasta: Path, casa: str, ano: int) -> AnoVotos | None:
    """O ano gravado (``None`` se ausente, ilegível ou de outra versão do formato)."""
    try:
        dados = json.loads(caminho(pasta, casa, ano).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(dados, dict) or dados.get("v") != VERSAO:
        return None
    try:
        return desserializar(dados)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        log.warning("Congresso: base %s-%d fora do formato (%s)", casa, ano, exc)
        return None


def anos_gravados(pasta: Path, casa: str) -> list[int]:
    anos = []
    for arquivo in Path(pasta).glob(f"{casa}-*.json"):
        match = re.fullmatch(rf"{casa}-(\d{{4}})\.json", arquivo.name)
        if match:
            anos.append(int(match.group(1)))
    return sorted(anos)


def carregar(pasta: Path, casa: str, anos: Iterable[int]) -> list[AnoVotos]:
    """Os anos pedidos que existem na base, em ordem."""
    return [ano for ano in (ler(pasta, casa, a) for a in sorted(set(anos))) if ano is not None]
