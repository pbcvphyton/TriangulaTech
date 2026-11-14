"""Votações nominais nas comissões da Câmara e do Senado.

Só o registro oficial: quem votou, como, o placar e o que a Casa escreveu sobre o que se
votava. Ninguém tem a intenção interpretada.

- **Câmara**: a base (:mod:`.base`) já traz as votações nominais de comissão (órgão
  diferente de ``PLEN``), dos mesmos arquivos em lote do Plenário. Só aparecem os que
  votaram (Sim, Não, Abstenção): a comissão não registra obstrução por deputado, nem quem
  presidia, nem quem faltou. O voto nominal de comissão é sobre o **parecer** do relator:
  Sim é aprovar o parecer, que pode concluir pela rejeição do projeto. A conclusão vem de
  ``votacoes-AAAA.csv`` (``ultimaApresentacaoProposicao_descricao``), que é o **último**
  parecer apresentado, nem sempre o votado, e às vezes vem sem conclusão; a regra que a
  lê é estreita (:func:`conclusao`) e, na dúvida, deixa vazio.
- **Senado**: ``/votacaoComissao/comissao/{sigla}``, uma chamada por comissão permanente
  com o período da legislatura. A fonte não traz o resultado, nem se o item era
  terminativo, nem a UF: o Sim não é interpretado; a página mostra a descrição oficial e
  o placar. A CEsp volta vazia pela sigla (falha da API, registrada no log).

**Mudou de voto** (só Câmara): quando o Plenário vota o parecer de uma comissão sobre uma
matéria (a descrição oficial diz "Aprovado/Rejeitado o parecer da Comissão…"), a votação
de comissão comparável é a **última** da mesma matéria, até a data do Plenário, que
aprovou um parecer ("Aprovado o Parecer…"): é esse parecer que o Plenário vota. Quem
votou Sim ou Não nas duas (obstrução conta como Não) e trocou de letra "mudou". Quando a
comissão votou dois pareceres de conclusão oposta (um rejeitado, o vencedor aprovado), só
o aprovado conta. O parecer da Comissão Mista (medida provisória) fica de fora: o voto
dela não está nos arquivos da Câmara.

Sem taxa: na mediana, um deputado vota poucas vezes por ano em comissão, e a conta
mediria mais a sorte de ser membro da CCJC do que a posição política.
"""

from __future__ import annotations

import csv
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .. import text
from . import base, fontes
from .base import AnoVotos, Votacao
from .fontes import Coletor
from .perfil import CONTEUDO
from .regras import Regras

log = logging.getLogger(__name__)

# As comissões permanentes do Senado (a CEsp volta vazia pela sigla: falha conhecida da API).
SENADO_COMISSOES = ("CAE", "CAS", "CCDD", "CCJ", "CCT", "CDD", "CDH", "CDR", "CE", "CEsp", "CI", "CMA", "CRA", "CRE", "CSP", "CTFC")
VALIDADE_H = 20.0  # o ano corrente (Câmara) e a consulta do Senado: refeitos todo dia
VALIDADE_ANTIGOS_H = 24 * 30  # Câmara, anos fechados: o arquivo quase não muda
ULTIMAS = 15  # votações divididas de conteúdo no painel
VOTOS_PESSOA = 12  # votos de cada pessoa em votações divididas de conteúdo
DESCRICAO_MAX = 220
PARECER_MAX = 200

SIM, NAO, ABSTENCAO = base.SIM, base.NAO, base.ABSTENCAO
_VOTO_CAMARA = {base.SIM: SIM, base.NAO: NAO, base.OBSTRUCAO: NAO, base.ABSTENCAO: ABSTENCAO}  # a obstrução conta como Não
_VOTO_SENADO = {"S": SIM, "N": NAO, "A": ABSTENCAO}


class ComissoesIndisponiveis(RuntimeError):
    """Uma fonte das votações de comissão não veio (fora do ar ou fora do formato)."""


# ── a conclusão do parecer (Câmara) ──────────────────────────────────────────

_ROTULOS = {
    "aprovacao": "aprovação",
    "rejeicao": "rejeição",
    "admissibilidade": "admissibilidade",
    "inadmissibilidade": "inadmissibilidade",
    "procedencia": "procedência",
    "improcedencia": "improcedência",
    "sustacao": "sustação",
    "arquivamento": "arquivamento",
    "provimento": "provimento",
    "conhecimento": "conhecimento",
    "prejudicialidade": "prejudicialidade",
    "constitucionalidade": "constitucionalidade",
    "inconstitucionalidade": "inconstitucionalidade",
}
_MASCULINOS = frozenset(("arquivamento", "provimento", "conhecimento"))
_CONCLUSAO = re.compile(r"\b(?:pel[ao]s?|por sua|por seu) (nao )?(" + "|".join(_ROTULOS) + r")\b")
_MERITO = " no merito "
# O que a Câmara registra como apresentado: só um parecer tem conclusão ("Parecer do Relator…",
# "Lido o Parecer Vencedor…", "Pela aprovação…"); a apresentação de um requerimento não tem.
_E_PARECER = re.compile(r"^(?:parecer|lido o parecer|pel[ao]s? )")


@dataclass(frozen=True)
class Parecer:
    """O último parecer apresentado antes de uma votação de comissão da Câmara."""

    texto: str  # o registro, encurtado (até PARECER_MAX caracteres)
    conclusao: str  # "pela rejeição", "pela admissibilidade"… ou "" (ver conclusao)


def _primeira_conclusao(normalizado: str) -> str:
    """A primeira conclusão do texto (sem acento, minúsculo); depois de "no mérito", se houver."""
    trecho = normalizado.split(_MERITO, 1)[1] if _MERITO in normalizado else normalizado
    for achado in _CONCLUSAO.finditer(trecho):
        negacao, palavra = bool(achado.group(1)), achado.group(2)
        if palavra == "conhecimento" and not negacao:
            continue  # "pelo conhecimento do recurso" só abre o mérito; "pelo não conhecimento" é conclusão
        artigo = "pelo" if palavra in _MASCULINOS else "pela"
        return f"{artigo} {'não ' if negacao else ''}{_ROTULOS[palavra]}"
    return ""


def conclusao(registro: str | None) -> str:
    """A conclusão de um parecer, pelo que a Câmara registrou (``"Parecer do Relator, Dep. X
    (PL-SP), pela rejeição."`` → ``"pela rejeição"``).

    Regra estreita: o texto precisa ser de um parecer (não a apresentação de um
    requerimento); vale a primeira conclusão ("pela aprovação deste e pela rejeição do PL
    apensado" → aprovação), e, quando o parecer separa a admissibilidade do mérito ("pela
    constitucionalidade… e, no mérito, pela aprovação"), a do mérito. Sem uma das palavras
    de conclusão (aprovação, rejeição, admissibilidade, procedência, sustação, arquivamento,
    provimento, constitucionalidade e as negativas), fica vazio."""
    normalizado = text.normalize(registro or "")
    if not _E_PARECER.match(normalizado):
        return ""
    return _primeira_conclusao(normalizado)


def _curto(valor: str | None, limite: int) -> str:
    """O texto em uma linha, com até ``limite`` caracteres contando a reticência do corte."""
    return text.truncate(re.sub(r"\s+", " ", (valor or "").strip()), limite - 1)


def validade_camara(ano: int, hoje: str) -> float:
    """O ano corrente vale :data:`VALIDADE_H`; os fechados, :data:`VALIDADE_ANTIGOS_H`."""
    return VALIDADE_H if ano >= int(hoje[:4]) else VALIDADE_ANTIGOS_H


def pareceres(coletor: Coletor, ids_por_ano: Mapping[int, Iterable[str]], *, hoje: str) -> dict[str, Parecer]:
    """Votação → o último parecer apresentado antes dela, de ``votacoes-AAAA.csv``, só para as
    votações pedidas (``{ano: ids}``). Um arquivo que não vem levanta :class:`ComissoesIndisponiveis`."""
    saida: dict[str, Parecer] = {}
    for ano in sorted(ids_por_ano):
        ids = set(ids_por_ano[ano])
        if not ids:
            continue
        try:
            caminho = fontes.camara_arquivo(coletor, "votacoes", ano, validade_h=validade_camara(ano, hoje))
        except Exception as exc:  # a rede fora do ar (ou a resposta ilegível): quem chama decide
            raise ComissoesIndisponiveis(f"Câmara: votacoes-{ano}.csv indisponível ({exc})") from exc
        try:
            for r in fontes.csv_linhas(caminho):
                vid = (r.get("id") or "").strip()
                if vid in ids:
                    registro = r.get("ultimaApresentacaoProposicao_descricao") or ""
                    saida[vid] = Parecer(texto=_curto(registro, PARECER_MAX), conclusao=conclusao(registro))
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise ComissoesIndisponiveis(f"Câmara: votacoes-{ano}.csv ilegível ({exc})") from exc
        faltam = len(ids - saida.keys())
        if faltam:
            log.warning("Comissões: %d votações da Câmara de %d sem linha em votacoes-%d.csv", faltam, ano, ano)
    return saida


# ── mudou de voto (Câmara) ───────────────────────────────────────────────────

# O Plenário vota o parecer de uma comissão: "Aprovado o parecer da Comissão de Constituição e
# Justiça e de Cidadania à Sustação…", "Rejeitado o Parecer da Comissão… que conclui pela…".
_PLENARIO_PARECER = re.compile(
    r"^(?:aprovad|rejeitad)[oa]s?,? (?:em apreciacao preliminar,? )?(?:o )?parecer d[aoe]s? (?:comiss|ccjc|relator)"
)
_COMISSAO_MISTA = re.compile(r"\bcomissao mista\b")
_PARECER_APROVADO = "aprovado o parecer"


def vota_parecer_de_comissao(v: Votacao) -> bool:
    """Se a votação de Plenário é a do parecer de uma comissão da Câmara (pela descrição oficial).
    O parecer da Comissão Mista (medida provisória) não conta: o voto dela não está na Câmara."""
    d = text.normalize(v.descricao)
    return v.plenario and bool(_PLENARIO_PARECER.match(d)) and not _COMISSAO_MISTA.search(d)


def aprovou_parecer(v: Votacao) -> bool:
    """Se a votação de comissão aprovou um parecer ("Aprovado o Parecer…", com ``aprovada``)."""
    return not v.plenario and v.aprovada is True and text.normalize(v.descricao).startswith(_PARECER_APROVADO)


# ── as contas, iguais nas duas Casas ─────────────────────────────────────────


@dataclass(frozen=True)
class _Nominal:
    """Uma votação nominal de comissão, já lida da fonte de cada Casa."""

    id: str
    data: str  # AAAA-MM-DD
    hora: str  # HH:MM ou ""
    comissao: str
    prop: str
    descricao: str
    parecer: str  # a conclusão (Câmara) ou ""
    conteudo: bool
    aprovada: bool | None
    votos: dict[int, str]  # pessoa → S/N/A

    @property
    def placar(self) -> tuple[int, int, int]:
        conta = Counter(self.votos.values())
        return conta.get(SIM, 0), conta.get(NAO, 0), conta.get(ABSTENCAO, 0)

    @property
    def dividida(self) -> bool:
        sim, nao, _abst = self.placar
        return sim > 0 and nao > 0

    def venceu(self, voto: str) -> bool | None:
        """Se o voto ficou com a maioria (Sim × Não) da votação; ``None`` em abstenção ou empate."""
        sim, nao, _abst = self.placar
        if voto not in (SIM, NAO) or sim == nao:
            return None
        return voto == (SIM if sim > nao else NAO)


def _ordem_id(ident: str) -> tuple[int, ...]:
    """``"2494565-41"`` → ``(2494565, 41)``: o desempate numérico entre votações da mesma hora."""
    return tuple(int(n) for n in re.findall(r"\d+", ident))


def _recente(n: _Nominal) -> tuple[str, str, tuple[int, ...], str]:
    return (n.data, n.hora, _ordem_id(n.id), n.id)


def _decrescente(contagem: Counter[str]) -> list[list[Any]]:
    return [[sigla, k] for sigla, k in sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0]))]


def _montar(nominais: list[_Nominal]) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """O painel (sem os campos de "mudou") e, por pessoa, o que ela votou."""
    ordem = sorted(nominais, key=_recente, reverse=True)
    mostrar = [n for n in ordem if n.dividida and n.conteudo]
    painel: dict[str, Any] = {
        "votacoes": len(nominais),
        "conteudo": sum(1 for n in nominais if n.conteudo),
        "divididas": sum(1 for n in nominais if n.dividida),
        "por_comissao": _decrescente(Counter(n.comissao for n in nominais)),
        "ultimas": [],
        "mudaram": [],
        "mudaram_casos": [],
    }
    for n in mostrar[:ULTIMAS]:
        sim, nao, abst = n.placar
        painel["ultimas"].append(
            {
                "id": n.id,
                "d": n.data,
                "comissao": n.comissao,
                "prop": n.prop,
                "x": _curto(n.descricao, DESCRICAO_MAX),
                "parecer": n.parecer,
                "sim": sim,
                "nao": nao,
                "abst": abst,
                "aprovada": n.aprovada,
            }
        )

    dados: dict[int, int] = Counter()
    divididas: dict[int, int] = Counter()
    comissoes: dict[int, Counter[str]] = defaultdict(Counter)
    votos: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for n in ordem:  # do mais recente: os primeiros VOTOS_PESSOA de cada um são os que ficam
        sim, nao, abst = n.placar
        for pid in sorted(n.votos):
            voto = n.votos[pid]
            dados[pid] += 1
            comissoes[pid][n.comissao] += 1
            if not n.dividida:
                continue
            divididas[pid] += 1
            if n.conteudo and len(votos[pid]) < VOTOS_PESSOA:
                votos[pid].append(
                    {
                        "d": n.data,
                        "comissao": n.comissao,
                        "prop": n.prop,
                        "x": _curto(n.descricao, DESCRICAO_MAX),
                        "parecer": n.parecer,
                        "v": voto,
                        "sim": sim,
                        "nao": nao,
                        "abst": abst,
                        "venceu": n.venceu(voto),
                    }
                )
    por_pessoa = {
        pid: {
            "n": dados[pid],
            "divididas": divididas.get(pid, 0),
            "comissoes": _decrescente(comissoes[pid]),
            "votos": votos.get(pid, []),
            "mudou": [],
        }
        for pid in sorted(dados)
    }
    return painel, por_pessoa


def _da_legislatura(data: str, regras: Regras, legislatura: int) -> bool:
    leg = regras.legislatura_em(data)
    return leg is not None and leg.numero == legislatura


# ── Câmara ───────────────────────────────────────────────────────────────────


def _votos_camara(v: Votacao, ano: AnoVotos) -> dict[int, str]:
    return {p.id: _VOTO_CAMARA[c] for p, c in zip(ano.pessoas, v.votos, strict=False) if c in _VOTO_CAMARA}


def camara(anos: list[AnoVotos], coletor: Coletor, regras: Regras, legislatura: int, *, hoje: str) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """O painel das votações nominais de comissão da Câmara na ``legislatura`` e, por
    deputado (id), o que ele votou em comissão. ``anos``: a base da Câmara; a conclusão de
    cada parecer vem de ``votacoes-AAAA.csv`` (:func:`pareceres`), que, fora do ar, levanta
    :class:`ComissoesIndisponiveis`."""
    anos = sorted(anos, key=lambda a: a.ano)
    da_comissao: list[tuple[AnoVotos, int]] = []
    do_plenario: list[tuple[AnoVotos, int]] = []
    for ano in anos:
        for k, v in enumerate(ano.votacoes):
            if v.data > hoje or not _da_legislatura(v.data, regras, legislatura):
                continue
            if not v.plenario:
                da_comissao.append((ano, k))
            elif v.prop_id is not None and vota_parecer_de_comissao(v):
                do_plenario.append((ano, k))

    ids_por_ano: dict[int, list[str]] = defaultdict(list)
    for ano, k in da_comissao:
        ids_por_ano[ano.ano].append(ano.votacoes[k].id)
    lidos = pareceres(coletor, ids_por_ano, hoje=hoje)

    # O parecer de cada votação de comissão: só quando a votação é a de um parecer (a descrição
    # diz "parecer"); um destaque ou um requerimento não vota a conclusão do relator.
    conclusoes: dict[str, str] = {}
    for ano, k in da_comissao:
        v = ano.votacoes[k]
        lido = lidos.get(v.id)
        conclusoes[v.id] = lido.conclusao if lido and "parecer" in text.normalize(v.descricao) else ""

    casos, mudaram = _mudancas(da_comissao, do_plenario, conclusoes)

    nominais = []
    for ano, k in da_comissao:
        v = ano.votacoes[k]
        votos = _votos_camara(v, ano)
        if not votos:
            continue
        nominais.append(
            _Nominal(
                id=v.id,
                data=v.data,
                hora=v.hora,
                comissao=v.orgao,
                prop=v.prop,
                descricao=v.descricao,
                parecer=conclusoes.get(v.id, ""),
                conteudo=v.tipo in CONTEUDO,
                aprovada=v.aprovada,
                votos=votos,
            )
        )
    painel, por_pessoa = _montar(nominais)
    painel["mudaram"] = mudaram
    painel["mudaram_casos"] = casos
    for item in mudaram:
        pid = int(item["k"].split("-", 1)[1])
        if pid in por_pessoa:
            por_pessoa[pid]["mudou"].append(item)
    log.info(
        "Comissões, Câmara: %d votações nominais (%d de conteúdo, %d divididas), %d deputados; %d casos comissão × Plenário, %d mudaram",
        painel["votacoes"],
        painel["conteudo"],
        painel["divididas"],
        len(por_pessoa),
        len(casos),
        len(mudaram),
    )
    return painel, por_pessoa


def _mudancas(
    da_comissao: list[tuple[AnoVotos, int]], do_plenario: list[tuple[AnoVotos, int]], conclusoes: dict[str, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Os casos comissão × Plenário sobre o mesmo parecer e quem mudou de letra entre os dois.
    Quando o último parecer apresentado não traz conclusão e a descrição do Plenário traz (é o
    mesmo parecer), vale a do Plenário (e ela passa a valer para a votação de comissão)."""
    aprovados: dict[int, list[tuple[AnoVotos, int]]] = defaultdict(list)
    for ano, k in da_comissao:
        v = ano.votacoes[k]
        if v.prop_id is not None and aprovou_parecer(v):
            aprovados[v.prop_id].append((ano, k))
    for lista in aprovados.values():
        lista.sort(key=lambda par: (par[0].votacoes[par[1]].data, par[0].votacoes[par[1]].hora, _ordem_id(par[0].votacoes[par[1]].id)))

    casos: list[tuple[Votacao, Votacao, dict[str, Any], list[dict[str, Any]]]] = []  # (Plenário, comissão, caso, quem mudou)
    for ano_p, kp in do_plenario:
        p = ano_p.votacoes[kp]
        # A última aprovação de parecer até a data do Plenário (só a data: o registro do Plenário
        # passa da meia-noite e a hora dele pode ser do dia seguinte).
        anteriores = [par for par in aprovados.get(p.prop_id or -1, []) if par[0].votacoes[par[1]].data <= p.data]
        if not anteriores:
            continue
        ano_c, kc = anteriores[-1]
        c = ano_c.votacoes[kc]
        if not conclusoes.get(c.id):
            conclusoes[c.id] = _primeira_conclusao(text.normalize(p.descricao))
        na_comissao = {x.id: base.POSICAO[voto] for x, voto in zip(ano_c.pessoas, c.votos, strict=False) if voto in base.POSICAO}
        ambas = 0
        trocaram = []
        for pessoa, voto in zip(ano_p.pessoas, p.votos, strict=False):
            vp = base.POSICAO.get(voto)
            vc = na_comissao.get(pessoa.id)
            if not vp or not vc:
                continue
            ambas += 1
            if vp != vc:
                trocaram.append(
                    {
                        "k": f"cd-{pessoa.id}",
                        "nome": pessoa.nome,
                        "partido": pessoa.partido_em(kp),
                        "uf": pessoa.uf,
                        "prop": p.prop,
                        "comissao": c.orgao,
                        "dc": c.data,
                        "vc": vc,
                        "dp": p.data,
                        "vp": vp,
                        "parecer": conclusoes.get(c.id, ""),
                    }
                )
        trocaram.sort(key=lambda i: (text.normalize(i["nome"]), i["k"]))
        caso = {"prop": p.prop, "comissao": c.orgao, "dc": c.data, "dp": p.data, "mudaram": len(trocaram), "ambas": ambas}
        casos.append((p, c, caso, trocaram))

    casos.sort(key=lambda t: (t[0].data, t[0].hora, t[1].data, _ordem_id(t[0].id)), reverse=True)  # do mais recente
    return [caso for _p, _c, caso, _t in casos], [item for _p, _c, _caso, trocaram in casos for item in trocaram]


# ── Senado ───────────────────────────────────────────────────────────────────

_REQUERIMENTO = re.compile(r"\b(?:requerimentos?|req|rqs)\b")


def coletar_senado(
    coletor: Coletor, *, inicio: str, hoje: str, siglas: Iterable[str] = SENADO_COMISSOES, validade_h: float = VALIDADE_H
) -> list[dict[str, Any]]:
    """As votações nominais das comissões ``siglas`` de ``inicio`` a ``hoje`` (AAAA-MM-DD),
    uma chamada por comissão, cada ``CodigoVotacao`` uma vez só (a mesma votação vem
    repetida entre comissões e, às vezes, dentro da mesma: fica a primeira, na ordem das
    siglas). Uma comissão que não vem levanta :class:`ComissoesIndisponiveis`; a que vem
    vazia (a CEsp, pela sigla) vai para o log."""
    vistas: dict[str, dict[str, Any]] = {}
    repetidas = 0
    vazias = []
    for sigla in siglas:
        try:
            bruto = fontes.senado_api(
                coletor,
                f"votacaoComissao/comissao/{sigla}",
                validade_h=validade_h,
                dataInicio=inicio.replace("-", ""),
                dataFim=hoje.replace("-", ""),
            )
        except Exception as exc:  # a rede fora do ar (ou a resposta ilegível): quem chama decide
            raise ComissoesIndisponiveis(f"Senado: votações nominais da {sigla} indisponíveis ({exc})") from exc
        raiz = bruto.get("VotacoesComissao") if isinstance(bruto, dict) else None
        if not isinstance(raiz, dict):
            raise ComissoesIndisponiveis(f"Senado: votações nominais da {sigla} fora do formato (sem VotacoesComissao)")
        lista = fontes.lista((raiz.get("Votacoes") or {}).get("Votacao") if isinstance(raiz.get("Votacoes"), dict) else None)
        if not lista:
            vazias.append(sigla)
        for item in lista:
            codigo = str(item.get("CodigoVotacao") or "").strip() if isinstance(item, dict) else ""
            if not codigo:
                continue
            if codigo in vistas:
                repetidas += 1
                continue
            vistas[codigo] = {**item, "_sigla": sigla}
    if vazias:
        log.info("Comissões, Senado: nenhuma votação nominal de %s no período (a CEsp volta vazia pela sigla)", ", ".join(vazias))
    if repetidas:
        log.info("Comissões, Senado: %d votações repetidas (mesmo CodigoVotacao) ficaram de fora", repetidas)
    return list(vistas.values())


def _nominal_senado(item: Mapping[str, Any]) -> _Nominal | None:
    votos: dict[int, str] = {}
    for voto in fontes.lista((item.get("Votos") or {}).get("Voto") if isinstance(item.get("Votos"), dict) else None):
        if not isinstance(voto, dict):
            continue
        pid = fontes.inteiro(voto.get("CodigoParlamentar"))
        valor = _VOTO_SENADO.get(str(voto.get("QualidadeVoto") or "").strip().upper())
        casa = str(voto.get("SiglaCasaParlamentar") or "SF").strip().upper()
        if pid is None or not valor or casa != "SF":
            continue
        votos.setdefault(pid, valor)  # o mesmo senador às vezes vem duas vezes na mesma votação
    if not votos:
        return None
    quando = str(item.get("DataHoraInicioReuniao") or "")
    prop = re.sub(r"\s+", " ", str(item.get("IdentificacaoMateria") or "")).strip()
    descricao = str(item.get("DescricaoVotacao") or "")
    return _Nominal(
        id=str(item.get("CodigoVotacao")).strip(),
        data=quando[:10],
        hora=quando[11:16],
        comissao=str(item.get("SiglaColegiado") or item.get("_sigla") or "").strip(),
        prop=prop,
        descricao=descricao,
        parecer="",
        conteudo=bool(prop) and not _REQUERIMENTO.search(text.normalize(descricao)),
        aprovada=None,  # a fonte não traz o resultado
        votos=votos,
    )


def senado(coletor: Coletor, regras: Regras, legislatura: int, *, inicio: str, hoje: str) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """O painel das votações nominais das comissões permanentes do Senado na ``legislatura``
    (de ``inicio``, o começo dela, a ``hoje``) e, por senador (código), o que ele votou. Sem
    resultado nem conclusão do relatório: o Sim não é interpretado. Sem "mudou" (o
    cruzamento com o Plenário é só da Câmara). A fonte fora do ar levanta
    :class:`ComissoesIndisponiveis`."""
    nominais = []
    for item in coletar_senado(coletor, inicio=inicio, hoje=hoje):
        n = _nominal_senado(item)
        if n is not None and n.data <= hoje and _da_legislatura(n.data, regras, legislatura):
            nominais.append(n)
    painel, por_pessoa = _montar(nominais)
    log.info(
        "Comissões, Senado: %d votações nominais (%d de conteúdo, %d divididas), %d senadores",
        painel["votacoes"],
        painel["conteudo"],
        painel["divididas"],
        len(por_pessoa),
    )
    return painel, por_pessoa
