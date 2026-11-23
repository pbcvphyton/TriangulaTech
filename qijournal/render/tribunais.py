"""Aba "Tribunais" (``edicoes/tribunais.html``): que ministro do STF e do STJ está com o quê.

A página responde, em ordem: que ministro está com o quê (o acervo de cada relator e quanto está
na mesa dele); em que ele está mais (a matriz ministro × área, comparada com os colegas só onde
passa do teste); quanto vale (as fontes abertas não publicam o valor da causa: a página diz isso e
mostra o que se mede); quem se repete (os litigantes por polo); e o que espera há mais tempo. Abre
com a leitura (os achados do dia, cada um com a prova) e fecha com o "Como ler", escrito a partir
das réguas de ``config/tribunais.yaml`` copiadas no ``meta.json``: nenhum nome de área nem limiar
mora no modelo.

Tudo o que é número sai pronto de ``data/tribunais/`` (``meta.json``, ``achados.json``,
``stj.json``, ``stf.json``; contrato em ``qijournal/tribunais/modelo.py``); aqui só se arruma para
mostrar. Sem script a página sai inteira: os dois tribunais em sequência, cada um com todas as
abas, e uma mesa compacta por ministro. Com script, um tribunal e uma aba por vez, o estado no
endereço (``#v=ministros&t=stj&o=s2&min=stj-<slug>&area=consumidor``), o dossiê de cada ministro
lido de ``data/tribunais/ministros/<id>.json`` e a folha de cada processo listado (``#pr=<id>``).

Quem grava: :func:`gravar_pagina` (a linha de comando e a integração chamam esta).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any

from .. import text
from ..config import Config
from ..tribunais import PAGINA, PASTA, TRIBUNAIS, VERSAO, modelo
from . import filters
from .web import brand_view

log = logging.getLogger(__name__)

TETO_BYTES = 1_500_000  # contrato §9: a página passa disto, não é gravada (o celular sente; o Congresso tem 4,3 MB)
COLUNA_MINIMA = 0.005  # área abaixo de 0,5% em todas as mesas da grade sai das colunas da matriz (vai escrita na nota)
# A largura de cada coluna da matriz sai da palavra mais longa do nome curto da área (o cabeçalho
# quebra entre palavras, nunca no meio de uma): 6 px por letra no corpo do cabeçalho (10,5 px,
# negrito; "Administrativo" mede 74 px) mais a folga, e nunca menos que o número da célula com a
# marca ▲/▽. A tabela rola dentro da moldura quando a soma não cabe; o nome nunca se cruza com o vizinho.
PX_POR_LETRA = 6.0
FOLGA_COLUNA = 8
COLUNA_PX_MINIMA = 44
AREAS_NO_CARTAO = 8  # barras ordenadas no cartão do ministro; as demais somam em "outras"
PARTES_DA_DIGITAL = 5  # a impressão digital da mesa: as 5 maiores áreas + "outras" (no máximo 6 partes: designer.md §2.2)
MINISTROS_POR_AREA = 12  # na aba Assuntos, quantos ministros cada área mostra (os demais, somados)
FILA_NA_PAGINA = 30  # a fila do tribunal inteiro (o painel traz até limiares.listas.fila_mais_antigos)
ARQUIVOS = {"meta": "meta.json", "achados": "achados.json"}  # e "<trib>.json", "ministros/<id>.json"
NOMES = {"stj": "Superior Tribunal de Justiça", "stf": "Supremo Tribunal Federal"}
ONDE = {
    "gabinete": "no gabinete (na mesa)",
    "outro_gabinete": "em outro gabinete",
    "cartorio": "no cartório (prazo, intimação, publicação)",
    "mp": "com o Ministério Público, para parecer",
    "presidencia": "na Presidência ou na triagem",
    "stf": "remetidos ao STF",
    "outro": "em outro lugar",
}
MOTIVOS = {"T": "tema repetitivo", "RG": "repercussão geral", "C": "controvérsia", "I": "incidente de assunção de competência", "ST": "sem motivo informado"}
SELOS_MINISTRO = {
    "convocado": "convocado",
    "heranca": "acervo herdado há pouco",
    "recem_chegado": "na Turma há pouco",
    "sem_distribuicao": "não recebe distribuição comum",
    "poucos_processos": "poucos processos para comparar",
}
CARGOS = {
    "presidente": "Presidente do tribunal",
    "vice_presidente": "Vice-Presidente",
    "corregedor_cnj": "Corregedor Nacional de Justiça",
    "presidente_turma": "preside a Turma",
    "presidente_secao": "preside a Seção",
    "decano": "decano",
}
# As dicas (i) dos números: o texto vai uma vez, no manifesto; o botão leva a chave.
GLOSSARIO = {
    "mesa": "Processos do ministro que estão no gabinete dele na data do arquivo. O resto do acervo está no cartório, com o Ministério Público ou em outro lugar.",
    "mesa_tribunal": "Processos dos ministros das Turmas que estão no gabinete do relator na data do arquivo.",
    "sem_decisao_stj": "Dias desde a última distribuição ou a última decisão, a mais recente das duas. Não é a data da conclusão: quando chega uma petição nova, a conta não vê.",
    "sem_decisao_stf": "Dias desde o andamento \"Conclusos ao Relator\"; só conta os processos cujo andamento atual é esse.",
    "idade": "Desde a chegada ao tribunal (a autuação), não desde o início da ação.",
    "valor": "Nenhuma fonte aberta do STF nem do STJ publica o valor da causa. A página não estima: mostra onde há dinheiro público no recurso e os recortes de conteúdo econômico.",
}
TIPOS_TEMA = {"repetitivo": "Repetitivo", "iac": "IAC", "controversia": "Controvérsia", "rg": "Repercussão geral"}
TIPOS_PARTE = {"pj": "empresa", "ente": "ente público", "pf": "pessoa física", "desconhecido": "parte sem tipo na fonte"}
ORDINAL = {"1": "Primeira", "2": "Segunda", "3": "Terceira", "4": "Quarta", "5": "Quinta", "6": "Sexta"}


# ── dados ────────────────────────────────────────────────────────────────────


@dataclass
class Dados:
    meta: dict[str, Any]
    paineis: dict[str, dict[str, Any]]  # "stf"/"stj" → o painel (só os que existem, na versão certa)
    achados: list[dict[str, Any]]


def _ler(caminho: Path) -> Any | None:
    try:
        return json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def carregar(data_dir: Path) -> Dados | None:
    """O que ``tribunais atualizar`` gravou em ``data/tribunais/`` (``None`` sem ``meta.json``, com
    formato de outra versão ou sem painel de nenhum tribunal)."""
    pasta = Path(data_dir) / PASTA
    meta = _ler(pasta / ARQUIVOS["meta"])
    if not isinstance(meta, dict) or meta.get("v") != VERSAO:
        return None
    paineis = {}
    for trib in TRIBUNAIS:
        painel = _ler(pasta / f"{trib}.json")
        if isinstance(painel, dict) and painel.get("v") == VERSAO and painel.get("trib") == trib:
            paineis[trib] = painel
    if not paineis:
        return None
    bruto = _ler(pasta / ARQUIVOS["achados"])
    achados = bruto.get("achados") if isinstance(bruto, dict) and bruto.get("v") == VERSAO else None
    return Dados(meta=meta, paineis=paineis, achados=[a for a in achados or [] if isinstance(a, dict)])


def publicada(out_dir: Path) -> bool:
    """A aba existe no site: ``edicoes/tribunais.html`` gravada e ``data/tribunais/meta.json`` ao
    lado. É o que acende o botão "Tribunais" nas outras páginas (``config.tribunais.publicado``)."""
    out_dir = Path(out_dir)
    return (out_dir / "edicoes" / PAGINA).is_file() and (out_dir / "data" / PASTA / ARQUIVOS["meta"]).is_file()


def marcar_publicada(config: Config, out_dir: Path) -> bool:
    """Marca ``config.tribunais.publicado`` pela existência da aba (o botão cruzado: a edição, o
    arquivo e a aba do Congresso chamam isto antes de renderizar). Devolve o valor marcado."""
    config.tribunais.publicado = bool(config.tribunais_ativo and publicada(out_dir))
    return config.tribunais.publicado


# ── formatação (pt-BR, sempre com unidade) ───────────────────────────────────


def _num(valor: Any) -> str:
    try:
        return text.format_number_pt(int(valor), 0)
    except (TypeError, ValueError):
        return "—"


def _pct(fracao: Any, fino: bool = False) -> str:
    """``0.0871`` → ``"9%"`` (``fino``: ``"8,7%"``); abaixo de 1% sempre com uma casa (``"0,6%"``)."""
    if fracao is None:
        return "—"
    valor = float(fracao) * 100
    if valor == 0:
        return "0%"
    if valor < 1 or (fino and valor < 10):
        casas = text.format_number_pt(valor, 1)
        return ("<0,1" if casas == "0,0" else casas) + "%"
    return f"{round(valor)}%"


def _celula(fracao: float) -> str:
    """O número da célula da matriz (em %, sem o sinal): inteiro de 1 para cima, uma casa abaixo."""
    return _pct(fracao).rstrip("%")


def _razao(valor: Any) -> str:
    if valor is None:
        return "—"
    return text.format_number_pt(float(valor), 1 if float(valor) < 10 else 0)


def _decimal(valor: Any) -> str:
    """Um número de régua como está no config: ``1.25`` → ``"1,25"``; ``0.8`` → ``"0,8"``."""
    try:
        texto = f"{float(valor):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "—"
    return texto.replace(".", ",")


def _data(iso: Any, ano: bool = True) -> str:
    try:
        dia = date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return ""
    return f"{dia.day:02d}/{dia.month:02d}/{dia.year}" if ano else f"{dia.day:02d}/{dia.month:02d}"


def _mes_ano(iso: Any) -> str:
    try:
        dia = date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return ""
    return f"{dia.month:02d}/{dia.year}"


def _dias(n: Any) -> str:
    """``1214`` → ``"1.214 dias (3 anos)"``; abaixo de 2 anos, só os dias."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "—"
    base = f"{_num(n)} {'dia' if n == 1 else 'dias'}"
    return f"{base} ({n // 365} anos)" if n >= 730 else base


def _anos(dias: Any) -> tuple[str, str]:
    """Para um número solto: ``(valor, unidade)``. ``438`` → ``("1,2", "ano")``; ``112`` → ``("112", "dias")``."""
    try:
        dias = int(dias)
    except (TypeError, ValueError):
        return "—", ""
    if dias < 365:
        return _num(dias), "dia" if dias == 1 else "dias"
    anos = dias / 365
    return text.format_number_pt(anos, 1), "ano" if anos < 2 else "anos"


def _janela(janela: Any) -> tuple[str, bool]:
    """A janela de uma série (``[ini, fim]``) dita como ela é: "em 12 meses" quando cobre o ano
    (360 dias ou mais), senão "entre DD/MM e DD/MM" (a série ainda cresce no cache). O segundo
    valor diz se a série tem 30 dias ou menos (aí "nos últimos 30 dias" não acrescenta nada)."""
    try:
        ini, fim = (date.fromisoformat(str(x)[:10]) for x in janela)
    except (TypeError, ValueError):
        return "em 12 meses", False
    dias = (fim - ini).days + 1
    if dias >= 360:
        return "em 12 meses", False
    return f"entre {_data(ini.isoformat(), ano=False)} e {_data(fim.isoformat(), ano=False)}", dias <= 30


def _plural(n: int, um: str, varios: str) -> str:
    return f"{_num(n)} {um if n == 1 else varios}"


def _numero_processo(folha: dict[str, Any]) -> str:
    """``REsp 1.712.345/SP`` (classe, número com milhar, UF quando há)."""
    numero = str(folha.get("numero") or "")
    rotulo = f"{folha.get('classe') or ''} {_num(numero) if numero.isdigit() else numero}".strip()
    return f"{rotulo}/{folha['uf']}" if folha.get("uf") else rotulo


def _cnj(numero: Any) -> str:
    """O número único no formato do CNJ: ``NNNNNNN-DD.AAAA.J.TR.OOOO``."""
    s = "".join(c for c in str(numero or "") if c.isdigit())
    if len(s) != 20:
        return str(numero or "")
    return f"{s[:7]}-{s[7:9]}.{s[9:13]}.{s[13]}.{s[14:16]}.{s[16:]}"


def _turma_curta(turma: str | None) -> str:
    if not turma:
        return ""
    if turma.startswith("T") and turma[1:].isdigit():
        return f"{turma[1:]}ªT"
    return turma


def _iniciais(nome: str) -> str:
    partes = [p for p in nome.split() if p[:1].isupper() and p.lower() not in {"de", "da", "do", "dos", "das"}]
    return (partes[0][0] + (partes[-1][0] if len(partes) > 1 else "")).upper() if partes else "?"


def _humano(grupo: str) -> str:
    """Nome de reserva para um litigante sem nome no painel: o id do catálogo por extenso
    (``banco-do-brasil`` → ``Banco do Brasil``; ``nome-cooperativa-x`` → ``Cooperativa X``) ou a raiz do CNPJ
    (``cnpj-12345678`` → ``CNPJ 12.345.678``)."""
    if grupo.startswith("cnpj-") and grupo[5:].isdigit() and len(grupo) == 13:
        r = grupo[5:]
        return f"CNPJ {r[:2]}.{r[2:5]}.{r[5:]}"
    miudas = {"de", "da", "do", "dos", "das", "e"}
    grupo = grupo.removeprefix("nome-")  # PJ sem CNPJ: o id é o nome normalizado
    return " ".join(p if p in miudas else p[:1].upper() + p[1:] for p in grupo.split("-"))


# ── réguas (meta.json → reguas, com o config de reserva) ────────────────────


@dataclass
class Reguas:
    areas: list[dict[str, Any]]  # na ordem do config: {id, nome, curto, cor: [claro, escuro] | None, texto, regra}
    subareas: dict[str, str]
    recortes: dict[str, dict[str, Any]]
    recortes_economicos: list[str]
    limiares: dict[str, Any]
    orgaos: dict[str, dict[str, str]]  # trib → {T1: "Primeira Turma"}
    grupos: dict[str, dict[str, str]]  # trib → {s1: "Primeira Seção"}
    links: dict[str, str]

    def area(self, area_id: str) -> dict[str, Any]:
        for a in self.areas:
            if a["id"] == area_id:
                return a
        return {"id": area_id, "nome": area_id, "curto": area_id, "cor": None, "texto": None, "regra": {}}

    def nome_subarea(self, sub_id: str) -> str:
        if sub_id in self.subareas:
            return self.subareas[sub_id]
        area_id = sub_id.split(".", 1)[0]
        return f"Outros de {self.area(area_id)['curto']}"


def _reguas(meta: dict[str, Any], config: Config) -> Reguas:
    try:
        regras: modelo.Regras | None = modelo.carregar_regras(config.root)
    except (OSError, modelo.ErroDeConfig) as exc:  # a página sai com as réguas do meta.json
        log.warning("Tribunais: config/tribunais.yaml ilegível (%s); a página usa as réguas do meta.json", exc)
        regras = None
    bruto = meta.get("reguas") if isinstance(meta.get("reguas"), dict) else {}
    if bruto.get("areas"):
        areas = [{"id": a["id"], "nome": a.get("nome") or a["id"], "curto": a.get("nome_curto") or a.get("nome") or a["id"],
                  "cor": a.get("cor"), "texto": a.get("texto"), "regra": a.get("regra") or {}} for a in bruto["areas"]]
    else:
        areas = [{"id": a.id, "nome": a.nome, "curto": a.nome_curto, "cor": list(a.cor) if a.cor else None, "texto": a.texto,
                  "regra": {"classes": list(a.classes), "nos": list(a.nos), "raizes": list(a.raizes), "residual": a.residual}}
                 for a in (regras.areas if regras else ())]
    subareas = {s["id"]: s.get("nome") or s["id"] for s in bruto.get("subareas") or []}
    recortes = {r["id"]: r for r in bruto.get("recortes") or []}
    if regras and not subareas:
        subareas = {s.id: s.nome for s in regras.subareas}
    if regras and not recortes:
        recortes = {r.id: {"id": r.id, "nome": r.nome, "codigos": list(r.codigos), "porque": r.porque} for r in regras.recortes}
    limiares = dict(modelo.LIMIARES_PADRAO)
    limiares.update(bruto.get("limiares") or (regras.limiares if regras else {}))
    orgaos = {t: dict(c.orgaos) for t, c in (regras.composicao.items() if regras else ())}
    grupos = {t: {g.id: g.nome for g in c.grupos} for t, c in (regras.composicao.items() if regras else ())}
    links = dict((regras.fontes.get("links_oficiais") or {}) if regras else {})
    economicos = list(bruto.get("recortes_economicos") or (regras.recortes_economicos if regras else ()))
    return Reguas(areas=areas, subareas=subareas, recortes=recortes, recortes_economicos=economicos, limiares=limiares,
                  orgaos=orgaos, grupos=grupos, links=links)


def _faixas_rampa(rampa: list[float]) -> list[dict[str, str]]:
    """A legenda da cor da célula, a partir de ``limiares.rampa`` (``[0.02, 0.05, 0.10, 0.25]``)."""
    cortes = [float(x) for x in rampa][:4]
    rotulos = [f"menos de {_pct(cortes[0])}"]
    rotulos += [f"{_pct(a)} a {_pct(b)}" for a, b in pairwise(cortes)]
    rotulos.append(f"{_pct(cortes[-1])} ou mais")
    return [{"classe": f"cg-j{i}", "rotulo": r} for i, r in enumerate(rotulos)]


def _classe_rampa(fracao: float, rampa: list[float]) -> str:
    nivel = sum(1 for corte in rampa[:4] if fracao >= float(corte))
    return f"cg-j{nivel}"


def _texto_pares(trib: str, limiares: dict[str, Any]) -> tuple[str, str]:
    """Com quem se compara (frase longa, rótulo curto), de ``limiares.comparacao.pares``."""
    pares = ((limiares.get("comparacao") or {}).get("pares") or {}).get(trib, "turma")
    if pares == "turma":
        return "os colegas da mesma Turma, sem ele", "a Turma"
    return "o tribunal sem ele e sem a Presidência", "o tribunal"


def _de(rotulo: str) -> str:
    """``"a Turma"`` → ``"da Turma"``; ``"o tribunal"`` → ``"do tribunal"`` (a contração)."""
    for artigo, contraido in (("a ", "da "), ("o ", "do "), ("as ", "das "), ("os ", "dos ")):
        if rotulo.startswith(artigo):
            return contraido + rotulo[len(artigo):]
    return "de " + rotulo


def _regua_marca(limiares: dict[str, Any]) -> str:
    """A regra do ▲/▽ escrita com os números do config."""
    c = limiares.get("comparacao") or {}
    razao = c.get("razao") or [0.8, 1.25]
    return (f"passa do teste (binomial exata, {_pct(c.get('confianca', 0.99))}, com Holm) e a diferença é grande: "
            f"{_decimal(razao[1])} vez ou mais (▲) ou {_decimal(razao[0])} ou menos (▽), com {c.get('pontos', 2)} pontos ou mais "
            f"e {c.get('minimo_area', 30)} processos ou mais")


# ── as partes de uma mini-folha ──────────────────────────────────────────────


def _parte_curta(parte: dict[str, Any], nomes: dict[str, str]) -> str:
    tipo = parte.get("tipo") or "desconhecido"
    rotulo = TIPOS_PARTE.get(tipo, "parte")
    grupo = parte.get("grupo")
    if grupo and tipo in ("pj", "ente"):
        return f"{rotulo} ({nomes.get(grupo) or _humano(grupo)})"
    return rotulo


def _partes_folha(folha: dict[str, Any], nomes: dict[str, str]) -> str:
    """"Recorre: ente público (INSS) · Responde: pessoa física" (nunca nome de pessoa física)."""
    ativos = [_parte_curta(p, nomes) for p in folha.get("partes") or [] if p.get("polo") == "ativo"]
    passivos = [_parte_curta(p, nomes) for p in folha.get("partes") or [] if p.get("polo") == "passivo"]
    trechos = []
    if ativos:
        trechos.append("Recorre: " + ", ".join(dict.fromkeys(ativos)))
    if passivos:
        trechos.append("Responde: " + ", ".join(dict.fromkeys(passivos)))
    return " · ".join(trechos)


def _item_fila(folha: dict[str, Any], trib: str, r: Reguas, nomes: dict[str, str], relatores: dict[str, str], com_relator: bool) -> dict[str, Any]:
    """Um processo numa lista "o que espera há mais tempo" (a mini-folha arrumada)."""
    dias = folha.get("sem_decisao_dias") if trib == "stj" else folha.get("concluso_dias")
    datas = folha.get("datas") or {}
    if trib == "stj":
        desde = datas.get("ultima_decisao") or datas.get("distribuicao")
        quando = f"sem decisão desde {_mes_ano(desde)}" if desde else ""
    else:
        rotulo = "concluso desde" if folha.get("concluso_dias") is not None else "último andamento em"  # concluso só com a conclusão ao relator
        quando = f"{rotulo} {_mes_ano(datas.get('andamento'))}" if datas.get("andamento") else ""
    area = r.area(folha.get("area") or "outros")
    extras = [x for x in (_partes_folha(folha, nomes), quando) if x]
    if (folha.get("flags") or {}).get("liminar"):
        extras.append("liminar pendente")
    rel = relatores.get(folha.get("relator_id") or "", "") if com_relator else ""
    return {
        "id": str(folha.get("id") or ""), "rotulo": _numero_processo(folha), "link": filters.safe_url(str(folha.get("link") or "")) or "",
        "dias": _num(dias) if dias is not None else "—", "area": area["id"], "area_nome": area["curto"], "relator": rel,
        "texto": " · ".join(extras),
    }


# ── a leitura ────────────────────────────────────────────────────────────────


def _href_de(alvo: str, trib: str) -> str:
    """O destino sem script de um endereço de estado (``#v=ministros&t=stj&min=stj-x`` → a mesa
    compacta dele; ``#v=fila&t=stf`` → o bloco da fila do STF)."""
    campos = dict(p.split("=", 1) for p in str(alvo or "").lstrip("#").split("&") if "=" in p)
    t = campos.get("t") or trib
    if campos.get("min"):
        return f"#tb-mesa-{campos['min']}"
    if campos.get("area") and campos.get("v") == "assuntos":
        return f"#tb-area-{t}-{campos['area']}"
    return f"#tb-{campos.get('v') or 'resumo'}-{t}"


def _leitura(trib: str, p: dict[str, Any], achados: list[dict[str, Any]], r: Reguas) -> dict[str, Any]:
    """A manchete do tribunal (o achado de maior peso dele) e os demais, numerados. Sem achado, a
    manchete sai dos números do dia, escrita por regra."""
    do_trib = [a for a in achados if a.get("trib") == trib and a.get("titulo")]
    u = p.get("universo") or {}
    n_min = sum(1 for m in p.get("ministros") or [] if m.get("grupo") != "pr")
    sem = "sem decisão registrada" if trib == "stj" else "conclusos ao relator"
    fina = (f"Do registro público de {_num(u.get('processos'))} processos em tramitação no {p.get('sigla', trib.upper())} em "
            f"{_data(p.get('data_dados'))}: {_num(u.get('na_grade'))} com os {n_min} ministros das Turmas, {_num(u.get('na_mesa'))} "
            f"na mesa deles; de que é a mesa de cada um, há quanto tempo e quem aparece.")
    if do_trib:
        topo, resto = do_trib[0], do_trib[1:]
        principal = {"selo": topo.get("selo") or "", "texto": topo.get("texto") or "", "href": _href_de(topo.get("alvo", ""), trib),
                     "alvo": topo.get("alvo") or "", "fonte": _prova(topo)}
        manchete = topo["titulo"]
    else:
        resto = []
        manchete = (f"No {p.get('sigla', trib.upper())}, {_num(u.get('na_mesa'))} processos estão na mesa dos ministros; "
                    f"{_num(u.get('sem_decisao_365'))} {sem} há mais de um ano")
        principal = {"selo": "FILA", "texto": f"{_num(u.get('sem_decisao_365'))} de {_num(u.get('na_mesa'))} na mesa, em {_data(p.get('data_dados'))}.",
                     "href": f"#tb-fila-{trib}", "alvo": f"#v=fila&t={trib}", "fonte": ""}
    return {
        "manchete": manchete, "principal": principal, "fina": fina,
        "achados": [{"selo": a.get("selo") or "", "titulo": a["titulo"], "texto": a.get("texto") or "", "href": _href_de(a.get("alvo", ""), trib),
                     "alvo": a.get("alvo") or "", "fonte": _prova(a)} for a in resto],
    }


def _prova(achado: dict[str, Any]) -> str:
    prova = achado.get("prova") or {}
    return f"dado de {_data(prova.get('data'))}" if prova.get("data") else ""


# ── as fontes de hoje e os estados ───────────────────────────────────────────


def _fontes(trib: str, meta: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A linha "as fontes de hoje" (estado escrito + forma: ● completo, ◐ com aviso, ■ falhou,
    ○ desligada) e os avisos do tribunal."""
    hoje = meta.get("hoje")
    linha, avisos = [], []
    for fid, f in (meta.get("fontes") or {}).items():
        if not isinstance(f, dict) or f.get("trib") not in (trib, "cnj"):
            continue
        nome = str(f.get("nome") or fid)
        data = _data(f.get("data_dados"), ano=False)
        velho = False
        try:
            velho = (date.fromisoformat(str(hoje)) - date.fromisoformat(str(f.get("data_dados")))).days > int(f.get("velho_dias") or 9999)
        except (TypeError, ValueError):
            velho = False
        if f.get("ok") is None:
            estado, forma, rotulo = "off", "○", "desligada"
        elif f.get("ok") is False:
            estado, forma, rotulo = "erro", "■", (f"falhou hoje; dados de {data}" if data else "falhou; sem dados")
        elif f.get("aviso") or velho:
            estado, forma, rotulo = "parc", "◐", f"dados de {data}" if data else "com aviso"
        else:
            estado, forma, rotulo = "ok", "●", f"dados de {data}" if data else "completa"
        linha.append({"id": fid, "nome": nome, "estado": estado, "forma": forma, "rotulo": rotulo,
                      "url": filters.safe_url(str(f.get("url") or "")) or "", "aviso": str(f.get("aviso") or "")})
        if f.get("trib") == trib and f.get("aviso") and f.get("ok") is not None:
            avisos.append({"classe": "tb-av-erro" if f.get("ok") is False else "tb-av-parcial",
                           "selo": f"Dado de {data}" if data else ("Fora do ar" if f.get("ok") is False else "Parcial"),
                           "nome": nome, "texto": str(f["aviso"])})
    return linha, avisos


# ── a matriz ministro × área ────────────────────────────────────────────────


def _colunas(p: dict[str, Any], r: Reguas) -> tuple[list[dict[str, Any]], list[str]]:
    """As áreas que viram coluna (alguma mesa da grade com ``COLUNA_MINIMA`` ou mais) e as que
    ficam só na nota (com o total do tribunal)."""
    grade = [m for m in p.get("ministros") or [] if m.get("grupo") != "pr"]
    por_area = {a["id"]: a for a in p.get("areas") or []}
    colunas, fora = [], []
    for a in r.areas:
        maior = max(((m.get("areas") or {}).get(a["id"], {}).get("p") or 0 for m in grade), default=0)
        total = (por_area.get(a["id"]) or {}).get("n") or 0
        if maior >= COLUNA_MINIMA:
            n_txt = f"{_num(round(total / 1000))} mil" if total >= 10_000 else _num(total)
            colunas.append({"id": a["id"], "curto": a["curto"], "nome": a["nome"], "n_txt": n_txt, "n": total, "w": _largura_coluna(a["curto"])})
        elif total:
            fora.append(f"{a['curto']} ({_num(total)})")
    return colunas, fora


def _largura_coluna(curto: str) -> int:
    """A largura mínima da coluna (px) para o nome curto da área caber sem cruzar o vizinho."""
    maior = max((len(p) for p in str(curto).split()), default=0)
    return max(COLUNA_PX_MINIMA, math.ceil(maior * PX_POR_LETRA) + FOLGA_COLUNA)


def _nota_colunas(fora: list[str]) -> str:
    """A frase das áreas que ficaram fora das colunas da matriz (com o corte, que mora aqui)."""
    if not fora:
        return ""
    return f"Áreas com menos de {_pct(COLUNA_MINIMA, fino=True)} em todas as mesas ficam fora das colunas: {', '.join(fora)} processos no tribunal."


def _matriz(trib: str, p: dict[str, Any], r: Reguas, colunas: list[dict[str, Any]]) -> dict[str, Any]:
    rampa = [float(x) for x in r.limiares.get("rampa") or [0.02, 0.05, 0.10, 0.25]]
    pares_longo, pares_curto = _texto_pares(trib, r.limiares)
    por_id = {m["id"]: m for m in p.get("ministros") or []}
    grupos = []
    for g in p.get("grupos") or []:
        linhas = []
        for mid in g.get("ministros") or []:
            m = por_id.get(mid)
            if not m:
                continue
            cels = []
            for c in colunas:
                cel = (m.get("areas") or {}).get(c["id"]) or {"n": 0, "p": 0, "pares": None, "razao": None, "marca": 0}
                n, frac = int(cel.get("n") or 0), float(cel.get("p") or 0)
                if not n:
                    cels.append({"a": c["id"], "classe": "tb-0", "v": "·", "titulo": f"{m['nome']}: nenhum processo de {c['curto']}", "sr": ""})
                    continue
                marca = int(cel.get("marca") or 0)
                titulo = f"{m['nome']}: {_pct(frac, fino=True)} do acervo em {c['curto']} ({_num(n)} processos)"
                if cel.get("pares") is not None:
                    titulo += f"; {pares_curto}: {_pct(cel['pares'], fino=True)}"
                    if cel.get("razao") is not None:
                        titulo += f" ({_razao(cel['razao'])} vez)"
                classe = _classe_rampa(frac, rampa) + (" tb-up" if marca > 0 else " tb-dn" if marca < 0 else "")
                sr = f" (bem acima {_de(pares_curto)})" if marca > 0 else f" (bem abaixo {_de(pares_curto)})" if marca < 0 else ""
                cels.append({"a": c["id"], "classe": classe, "v": _celula(frac), "titulo": titulo, "sr": sr})
            extra = [SELOS_MINISTRO[s] for s in m.get("selos") or [] if s in SELOS_MINISTRO]
            linhas.append({"id": mid, "nome": m.get("nome") or mid, "curta": _turma_curta(m.get("turma")) or ("Pres." if g["id"] == "pr" else ""),
                           "processos": _num(m.get("processos")), "na_mesa": _num(m.get("na_mesa")), "celulas": cels,
                           "selos": ", ".join(extra), "conv": "convocado" in (m.get("selos") or [])})
        media = None
        if g.get("id") != "pr" and g.get("processos"):
            cels = []
            for c in colunas:
                n, frac = (g.get("areas") or {}).get(c["id"], [0, 0])
                cels.append({"a": c["id"], "v": _celula(frac) if n else "·", "titulo": f"{g.get('nome')}: {_pct(frac, fino=True)} em {c['curto']} ({_num(n)} processos)"})
            media = {"rotulo": "média da Seção" if trib == "stj" else f"média da {g.get('nome', '')}", "processos": _num(g.get("processos")), "celulas": cels}
        turmas = ", ".join(r.orgaos.get(trib, {}).get(t, t) for t in g.get("turmas") or [])
        desc = " · ".join(x for x in (g.get("descricao") or "", turmas, f"{_num(g.get('processos'))} processos" if g.get("processos") else "") if x)
        grupos.append({"id": g.get("id"), "nome": g.get("nome") or g.get("id"), "desc": desc, "linhas": linhas, "media": media,
                       "n": len(linhas)})
    # Fora da grade: a Presidência (triagem), sem relator, quem saiu... cada linha com a parte de cada área.
    fora = []
    for f in p.get("fora_da_grade") or []:
        n = int(f.get("n") or 0)
        if not n:
            continue
        cels = []
        for c in colunas:
            k = int((f.get("areas") or {}).get(c["id"]) or 0)
            frac = k / n
            cels.append({"a": c["id"], "classe": _classe_rampa(frac, rampa) if k else "tb-0", "v": _celula(frac) if k else "·",
                         "titulo": f"{f.get('rotulo')}: {_pct(frac, fino=True)} em {c['curto']} ({_num(k)} processos)"})
        fora.append({"id": f.get("id"), "rotulo": f.get("rotulo") or f.get("id"), "nota": f.get("nota") or "", "processos": _num(n), "celulas": cels})
    media = p.get("media") or {}
    linha_tribunal = None
    if trib == "stf" and media.get("processos"):
        linha_tribunal = {"rotulo": "média do tribunal, sem a Presidência", "processos": _num(media.get("processos")),
                          "celulas": [{"a": c["id"], "v": _celula((media.get("areas") or {}).get(c["id"], [0, 0])[1]) if (media.get("areas") or {}).get(c["id"], [0])[0] else "·",
                                       "titulo": f"Média do tribunal sem a Presidência: {_pct((media.get('areas') or {}).get(c['id'], [0, 0])[1], fino=True)} em {c['curto']}"} for c in colunas]}
    caption = (f"A parte do acervo de cada ministro em cada área, em %. ▲ bem acima {_de(pares_longo)}; ▽ bem abaixo "
               f"(só onde {_regua_marca(r.limiares)}).")
    return {"caption": caption, "grupos": grupos, "fora": fora, "linha_tribunal": linha_tribunal,
            "legenda": _faixas_rampa(rampa), "pares": pares_curto, "pares_de": _de(pares_curto), "colspan": len(colunas) + 2,
            "largura": sum(c["w"] + 2 for c in colunas)}  # as colunas de área com o espaçamento (border-spacing: 2px)


def _digital(m: dict[str, Any], r: Reguas) -> tuple[list[dict[str, Any]], str]:
    """A impressão digital: as ``PARTES_DA_DIGITAL`` maiores áreas na ordem da legenda + "outras"."""
    areas = m.get("areas") or {}
    maiores = sorted((a for a in areas if (areas[a].get("n") or 0) > 0), key=lambda a: (-(areas[a].get("n") or 0), a))[:PARTES_DA_DIGITAL]
    ordem = [a["id"] for a in r.areas if a["id"] in maiores]
    partes = [{"area": a, "flex": f"{float(areas[a].get('p') or 0):.4f}"} for a in ordem]
    resto = max(0.0, 1 - sum(float(areas[a].get("p") or 0) for a in ordem))
    if resto >= 0.005:
        partes.append({"area": "outros-fp", "flex": f"{resto:.4f}"})
    aria = "; ".join(f"{r.area(a)['curto']} {_pct(areas[a].get('p'), fino=True)}" for a in sorted(ordem, key=lambda a: -(areas[a].get("n") or 0)))
    if resto >= 0.005:
        aria += f"; outras {_pct(resto, fino=True)}"
    return partes, aria


# ── o cartão (a mesa compacta de um ministro) ────────────────────────────────


def _cartao(trib: str, p: dict[str, Any], m: dict[str, Any], r: Reguas, nomes: dict[str, str], relatores: dict[str, str]) -> dict[str, Any]:
    ela = m.get("genero") == "f"
    pares_longo, pares_curto = _texto_pares(trib, r.limiares)
    pares_longo = pares_longo.replace("sem ele", "sem ela") if ela else pares_longo
    orgao = r.orgaos.get(trib, {}).get(m.get("turma") or "", "") or (f"{ORDINAL.get((m.get('turma') or ' ')[1:], '')} Turma".strip() if m.get("turma") else "")
    secao = r.orgaos.get(trib, {}).get(m.get("secao") or "", "") if m.get("secao") else ""
    grupo_nome = r.grupos.get(trib, {}).get(m.get("grupo") or "", "")
    kicker = " · ".join(x for x in (NOMES.get(trib, p.get("nome", "")), orgao, secao) if x) or NOMES.get(trib, "")
    if not orgao and grupo_nome:
        kicker += f" · {grupo_nome}"
    areas = m.get("areas") or {}
    total = int(m.get("processos") or 0)
    selos = []
    conv = m.get("convocado") or None
    if conv:
        conv_txt = f"{'convocada' if ela else 'convocado'} do {conv.get('tribunal')}"
        if conv.get("vaga_de"):
            conv_txt += f", na vaga de {conv['vaga_de']}"
        if conv.get("desde"):
            conv_txt += f", desde {_data(conv['desde'])}"
        selos.append(conv_txt)
    selos += [CARGOS[c] for c in m.get("cargos") or [] if c in CARGOS]
    selos += [SELOS_MINISTRO[s] for s in m.get("selos") or [] if s in SELOS_MINISTRO and s != "convocado"]
    if not m.get("comparavel") and m.get("grupo") != "pr":
        selos.append("sem marca de comparação")
    # as frases (por regra)
    frases = []
    maiores = sorted((a for a in areas if (areas[a].get("n") or 0) > 0), key=lambda a: (-(areas[a].get("n") or 0), a))
    if maiores:
        a = maiores[0]
        cel = areas[a]
        frase = f"{_num(total)} processos no acervo em {_data(p.get('data_dados'))}; {_pct(cel.get('p'), fino=True)} são de {r.area(a)['curto']}"
        if cel.get("pares") is not None and cel.get("razao") is not None:
            frase += f" ({pares_curto}: {_pct(cel.get('pares'), fino=True)})"
        frases.append(frase + ".")
    marcados = [a for a in maiores if int(areas[a].get("marca") or 0) != 0]
    if marcados:
        textos = [f"{r.area(a)['curto']} {_pct(areas[a].get('p'), fino=True)}, {_razao(areas[a].get('razao'))} vez {pares_curto}" for a in marcados[:2]]
        frases.append("Fora do padrão dos colegas, com teste: " + "; ".join(textos) + ".")
    sd = m.get("sem_decisao") or {}
    if sd.get("mais_365") is not None:
        rot = "sem decisão registrada" if trib == "stj" else "conclusos ao relator"
        antigo = (m.get("mais_antigos") or [{}])[0]
        dias_antigo = antigo.get("sem_decisao_dias") if trib == "stj" else antigo.get("concluso_dias")
        frase = f"{_num(sd.get('mais_365'))} na mesa {rot} há mais de um ano"
        if dias_antigo:
            frase += f"; o mais antigo, há {_dias(dias_antigo)}"
        frases.append(frase + ".")
    if m.get("partes"):
        g, n = m["partes"][0][0], m["partes"][0][1]
        frases.append(f"Quem mais aparece: {nomes.get(g) or _humano(g)}, em {_plural(int(n), 'processo', 'processos')}.")
    # os quatro números
    na_mesa = int(m.get("na_mesa") or 0)
    tiles = [
        {"k": "Na mesa", "dica": "mesa",
         "v": _num(na_mesa), "u": "", "x": f"de {_num(total)} no acervo ({_pct(na_mesa / total if total else 0)})"},
        {"k": "Sem decisão há + de 1 ano" if trib == "stj" else "Concluso há + de 1 ano",
         "dica": f"sem_decisao_{trib}",
         "v": _num(sd.get("mais_365")), "u": "", "x": f"mediana: {_dias(sd.get('mediana'))}" if sd.get("mediana") is not None else "a fonte não informa"},
    ]
    nt = m.get("no_tribunal") or {}
    v, u = _anos(nt.get("mediana"))
    tiles.append({"k": "Idade mediana", "dica": "idade",
                  "v": v, "u": u, "x": f"desde a chegada ao {p.get('sigla', trib.upper())}" + (" (pelo ano de autuação)" if trib == "stf" else "")})
    tiles.append({"k": "Valor da causa", "dica": "valor",
                  "v": "", "u": "", "x": f"as fontes abertas não publicam. Ente público num polo: {_num(m.get('ente_publico'))} ({_pct((m.get('ente_publico') or 0) / total if total else 0)}); liminar pedida: {_num(m.get('liminar'))}",
                  "sem_numero": True})
    for k in tiles:
        k.setdefault("sem_numero", False)
    # do que é a mesa: barras ordenadas com o traço da média
    barras = []
    escala = max([float(areas[a].get("p") or 0) for a in maiores[:AREAS_NO_CARTAO]] + [float(areas[a].get("pares") or 0) for a in maiores[:AREAS_NO_CARTAO]] + [0.4])
    for a in maiores[:AREAS_NO_CARTAO]:
        cel = areas[a]
        marca = int(cel.get("marca") or 0)
        barras.append({"area": a, "nome": r.area(a)["curto"], "pct": _pct(cel.get("p"), fino=True), "n": _num(cel.get("n")),
                       "w": f"{100 * float(cel.get('p') or 0) / escala:.1f}", "base": f"{100 * float(cel['pares']) / escala:.1f}" if cel.get("pares") is not None else "",
                       "razao": f"{_razao(cel.get('razao'))}× {pares_curto}" if cel.get("razao") is not None else "",
                       "marca": "▲" if marca > 0 else "▽" if marca < 0 else "", "marca_sr": f"bem acima {_de(pares_curto)}" if marca > 0 else f"bem abaixo {_de(pares_curto)}" if marca < 0 else "",
                       "aria": f"{_pct(cel.get('p'), fino=True)} do acervo" + (f"; {pares_curto} {_pct(cel.get('pares'), fino=True)}" if cel.get("pares") is not None else "")})
    outras = max(0, total - sum(int(areas[a].get("n") or 0) for a in maiores[:AREAS_NO_CARTAO]))
    digital, digital_aria = _digital(m, r)
    antigos = [_item_fila(f, trib, r, nomes, relatores, False) for f in m.get("mais_antigos") or []]
    sobrestados = int(m.get("sobrestados") or 0)
    fluxo = []
    e = m.get("entradas")
    if isinstance(e, dict):
        janela, curta = _janela((p.get("entradas") or {}).get("janela"))
        frase = f"Entraram {_num(e.get('sorteio_12m'))} por sorteio {janela}" + ("" if curta else f" ({_num(e.get('sorteio_30d'))} nos últimos 30 dias)")
        if e.get("heranca_12m"):
            frase += f"; {_num(e.get('heranca_12m'))} por atribuição (herança de acervo), à parte"
        fluxo.append(frase + ".")
    pa = m.get("pauta")
    if isinstance(pa, dict) and pa.get("itens"):
        fluxo.append(f"Em pauta: {_plural(int(pa['itens']), 'item', 'itens')} ({_num(pa.get('principal'))} recursos principais, {_num(pa.get('interno'))} recursos internos).")
    de = m.get("decisoes")
    if isinstance(de, dict) and de.get("processos") is not None:
        janela, _curta = _janela((p.get("decisoes") or {}).get("janela"))
        fluxo.append(f"Decidiu {_num(de.get('processos'))} processos {janela} ({_num(de.get('monocraticas'))} decisões monocráticas, {_num(de.get('acordaos'))} acórdãos).")
    if isinstance(m.get("temas"), int):
        fluxo.append(f"Temas sob relatoria, sem julgamento: {_num(m['temas'])}.")
    return {
        "id": m["id"], "nome": m.get("nome") or m["id"], "iniciais": _iniciais(m.get("nome") or "?"), "kicker": kicker,
        "sub": f"{'Relatora' if ela else 'Relator'} de {_num(total)} processos em tramitação em {_data(p.get('data_dados'))}",
        "grupo": m.get("grupo") or "", "selos": selos, "frases": frases, "tiles": tiles, "barras": barras,
        "outras": _num(outras) if outras else "", "digital": digital, "digital_aria": digital_aria,
        "pares": pares_curto, "pares_longo": pares_longo,
        "subareas": [{"nome": r.nome_subarea(s[0]), "n": _num(s[1]), "pct": _pct(s[2], fino=True)} for s in m.get("subareas") or []],
        "antigos": antigos, "rot_dias": "sem decisão há" if trib == "stj" else "concluso há",
        "sobrestados": (f"Fora desta lista: {_plural(sobrestados, 'processo suspenso', 'processos suspensos')} à espera de um tema repetitivo ou de repercussão geral (não estão parados por escolha do gabinete)."
                        if sobrestados else ""),
        "partes": [{"nome": nomes.get(g) or _humano(g), "n": _num(n)} for g, n in (m.get("partes") or [])],
        "fluxo": fluxo, "ela": ela, "processos": _num(total),
    }


# ── as outras abas ───────────────────────────────────────────────────────────


def _assuntos(trib: str, p: dict[str, Any], r: Reguas, nomes: dict[str, str], tpu: Any) -> dict[str, Any]:
    areas = [a for a in p.get("areas") or [] if (a.get("n") or 0) > 0]
    areas.sort(key=lambda a: (-(a.get("n") or 0), a["id"]))
    maior = max((a.get("n") or 0 for a in areas), default=1) or 1
    barras = [{"area": a["id"], "nome": r.area(a["id"])["curto"], "n": _num(a.get("n")), "pct": _pct(a.get("p"), fino=True),
               "w": f"{100 * (a.get('n') or 0) / maior:.1f}"} for a in areas]
    rec_maior = max((x.get("n") or 0 for x in p.get("recortes") or []), default=1) or 1
    recortes = [{"id": x["id"], "nome": (r.recortes.get(x["id"]) or {}).get("nome") or x["id"], "porque": (r.recortes.get(x["id"]) or {}).get("porque") or "",
                 "n": _num(x.get("n")), "pct": _pct(x.get("p"), fino=True), "w": f"{100 * (x.get('n') or 0) / rec_maior:.1f}"}
                for x in sorted(p.get("recortes") or [], key=lambda x: (-(x.get("n") or 0), x["id"]))]
    ministros = [m for m in p.get("ministros") or [] if m.get("grupo") != "pr"]
    focos = []
    for a in areas:
        aid = a["id"]
        nome = r.area(aid)
        lista = sorted(((m, (m.get("areas") or {}).get(aid) or {}) for m in ministros), key=lambda x: (-(x[1].get("n") or 0), x[0]["id"]))
        lista = [x for x in lista if (x[1].get("n") or 0) > 0]
        topo = max((c.get("n") or 0 for _, c in lista), default=1) or 1
        mesas = [{"id": m["id"], "nome": m.get("nome"), "n": _num(c.get("n")), "pct": _pct(c.get("p"), fino=True), "w": f"{100 * (c.get('n') or 0) / topo:.1f}",
                  "marca": "▲" if (c.get("marca") or 0) > 0 else "▽" if (c.get("marca") or 0) < 0 else ""} for m, c in lista[:MINISTROS_POR_AREA]]
        resto = lista[MINISTROS_POR_AREA:]
        dentro = []
        sub_maior = max((s[1] for s in a.get("subareas") or []), default=1) or 1
        for sid, n in a.get("subareas") or []:
            dentro.append({"nome": r.nome_subarea(sid), "n": _num(n), "w": f"{100 * n / sub_maior:.1f}", "codigo": ""})
        assuntos = []
        ass_maior = max((s[1] for s in a.get("assuntos") or []), default=1) or 1
        for codigo, n in a.get("assuntos") or []:
            assuntos.append({"nome": (tpu.nome(codigo) if tpu else None) or f"Código {codigo}", "codigo": str(codigo), "n": _num(n), "w": f"{100 * n / ass_maior:.1f}"})
        focos.append({
            "id": aid, "nome": nome["nome"], "curto": nome["curto"], "n": _num(a.get("n")), "pct": _pct(a.get("p"), fino=True),
            "na_grade": _num(a.get("na_grade")), "mesas": mesas,
            "resto": f"e mais {_plural(len(resto), 'ministro', 'ministros')}, com {_num(sum(c.get('n') or 0 for _, c in resto))} processos" if resto else "",
            "dentro": dentro, "assuntos": assuntos,
            "partes": [{"nome": nomes.get(g) or _humano(g), "n": _num(n)} for g, n in a.get("partes") or []],
            "toca": [{"area": t, "nome": r.area(t)["curto"], "n": _num(n)} for t, n in a.get("tambem_toca") or []],
        })
    return {"barras": barras, "recortes": recortes, "focos": focos}


def _partes(trib: str, p: dict[str, Any], r: Reguas) -> dict[str, Any]:
    partes = p.get("partes") or {}
    top = partes.get("top") or []
    maior = max((max(x.get("recorre") or 0, x.get("responde") or 0) for x in top), default=1) or 1
    n_grade = sum(1 for m in p.get("ministros") or [] if m.get("grupo") != "pr")
    linhas = [{"nome": x.get("nome") or _humano(x.get("id") or ""), "tipo": TIPOS_PARTE.get(x.get("tipo") or "", ""), "n": _num(x.get("n")), "pct": _pct(x.get("p"), fino=True),
               "recorre": _num(x.get("recorre")), "responde": _num(x.get("responde")),
               "wl": f"{80 * (x.get('recorre') or 0) / maior:.1f}", "wr": f"{80 * (x.get('responde') or 0) / maior:.1f}",
               "mesas": f"em {x.get('mesas')} de {n_grade} mesas" if x.get("mesas") is not None else "",
               "area": x.get("area") or "", "area_nome": r.area(x.get("area"))["curto"] if x.get("area") else ""} for x in top]
    ente = partes.get("ente_publico") or {}
    grupos_nome = r.grupos.get(trib, {})
    por_grupo = []
    for g, (n, base) in (ente.get("por_grupo") or {}).items():
        por_grupo.append(f"{grupos_nome.get(g, g)}: {_pct(n / base if base else 0, fino=True)} ({_num(n)} de {_num(base)})")
    return {
        "linhas": linhas, "pf": _num(partes.get("pessoas_fisicas")),
        "ente": f"{_pct(ente.get('p'), fino=True)} do acervo ({_num(ente.get('n'))} processos) tem um ente público num dos polos" if ente else "",
        "ente_grupos": por_grupo,
        "concentracao": f"Os 20 litigantes que mais aparecem estão em {_pct(partes.get('concentracao_top20'))} do acervo." if partes.get("concentracao_top20") is not None else "",
        "minimo": int(r.limiares.get("nome_parte_tribunal") or 20),
    }


def _fila(trib: str, p: dict[str, Any], r: Reguas, nomes: dict[str, str], relatores: dict[str, str]) -> dict[str, Any]:
    fila = p.get("fila") or {}
    grupos = {g["id"]: g for g in p.get("grupos") or []}
    # Idade de cada mesa: do 1º ao 3º quartil, o traço na mediana e o ponto no p90
    linhas = []
    escala = max([int((m.get("sem_decisao") or {}).get("p90") or 0) for m in p.get("ministros") or []] + [365])
    for g in p.get("grupos") or []:
        if g.get("id") == "pr":
            continue
        itens = []
        for mid in g.get("ministros") or []:
            m = next((x for x in p.get("ministros") or [] if x["id"] == mid), None)
            sd = (m or {}).get("sem_decisao") or {}
            if not m or sd.get("mediana") is None:
                continue
            itens.append({"id": mid, "nome": m.get("nome"), "med": int(sd["mediana"]),
                          "l": f"{100 * (sd.get('p25') or 0) / escala:.1f}", "w": f"{100 * max(0, (sd.get('p75') or 0) - (sd.get('p25') or 0)) / escala:.1f}",
                          "m": f"{100 * sd['mediana'] / escala:.1f}", "p90": f"{min(100.0, 100 * (sd.get('p90') or 0) / escala):.1f}",
                          "txt": f"mediana {_dias(sd['mediana'])} · 90% até {_dias(sd.get('p90'))}", "mais_365": _num(sd.get("mais_365"))})
        itens.sort(key=lambda x: (-x["med"], x["id"]))
        if itens:
            linhas.append({"grupo": g.get("nome"), "itens": itens})
    eixo = [{"l": f"{100 * d / escala:.1f}", "t": f"{d // 365} {'ano' if d // 365 == 1 else 'anos'}" if d >= 365 else f"{d} dias"}
            for d in (0, 180, 365, 730, 1095, 1825, 3650) if d <= escala]
    por_grupo = [{"nome": (grupos.get(g) or {}).get("nome") or r.grupos.get(trib, {}).get(g, g), "na_mesa": _num(v.get("na_mesa")),
                  "mais_180": _num(v.get("mais_180")), "mais_365": _num(v.get("mais_365"))} for g, v in (fila.get("por_grupo") or {}).items()]
    sob = fila.get("sobrestados") or {}
    motivos = [f"{_num(n)} por {MOTIVOS.get(k, k)}" for k, n in sorted((sob.get("por_motivo") or {}).items(), key=lambda kv: (-kv[1], kv[0]))]
    vistas = []
    for v in fila.get("vistas") or []:
        proc = v.get("processo") or {}
        item = _item_fila(proc, trib, r, nomes, relatores, True)
        item["vista"] = relatores.get(v.get("ministro_vista") or "", "") or "—"
        item["sessao"] = _data(v.get("sessao"))
        vistas.append(item)
    mesa_dias = r.limiares.get("mesa_dias") or [180, 365]
    return {
        "linhas": linhas, "eixo": eixo,
        "antigos": [_item_fila(f, trib, r, nomes, relatores, True) for f in (fila.get("mais_antigos") or [])[:FILA_NA_PAGINA]],
        "por_grupo": por_grupo, "mesa_dias": [_num(d) for d in mesa_dias],
        "sobrestados": f"{_num(sob.get('total'))} processos suspensos" + (f": {', '.join(motivos)}" if motivos else "") + "." if sob.get("total") else "",
        "vistas": vistas, "rot": "sem decisão registrada" if trib == "stj" else "conclusos ao relator",
    }


def _fluxo(trib: str, p: dict[str, Any], meta: dict[str, Any]) -> list[dict[str, str]]:
    """O que entrou, o que vai a julgamento, o que foi decidido e os temas, do tribunal (cada um com
    a fonte e a data; o que a fonte não trouxe sai com a frase)."""
    fontes = meta.get("fontes") or {}
    blocos = []
    e = p.get("entradas")
    if isinstance(e, dict):
        jan = e.get("janela") or [None, None]
        tot, u30 = e.get("total") or {}, e.get("ultimos_30d") or {}
        texto = (f"{_num(tot.get('sorteio'))} processos distribuídos por sorteio entre {_data(jan[0])} e {_data(jan[1])} ({_num(e.get('dias_com_ata'))} dias com ata); "
                 f"{_num(tot.get('heranca'))} por atribuição (herança de acervo), contados à parte.")
        if not _janela(jan)[1]:  # com 30 dias ou menos de série, "nos últimos 30 dias" repetiria o total
            texto += f" Nos últimos 30 dias: {_num(u30.get('sorteio'))} por sorteio e {_num(u30.get('heranca'))} por herança."
        if tot.get("registro"):
            texto += f" Outros {_num(tot.get('registro'))} foram registrados à Presidência ou à Vice, que não são entrada de ministro."
        blocos.append({"titulo": "O que entrou", "texto": texto, "fonte": _selo_fonte(fontes.get(e.get("fonte") or ""))})
    pa = p.get("pautas")
    if isinstance(pa, dict):
        tipos = ", ".join(f"{k} {_num(v)}" for k, v in sorted((pa.get("por_tipo") or {}).items(), key=lambda kv: (-kv[1], kv[0])))
        texto = (f"{_plural(int(pa.get('itens') or 0), 'item', 'itens')} nas sessões publicadas até agora: {_num(pa.get('principal'))} recursos principais e "
                 f"{_num(pa.get('interno'))} recursos internos" + (f" ({tipos})" if tipos else "") + ".")
        if pa.get("vistas") is not None:
            texto += f" Com pedido de vista: {_num(pa.get('vistas'))}; destaques: {_num(pa.get('destaques'))}."
        blocos.append({"titulo": "O que vai a julgamento", "texto": texto, "fonte": _selo_fonte(fontes.get(pa.get("fonte") or ""))})
    de = p.get("decisoes")
    if isinstance(de, dict):
        jan = de.get("janela") or [None, None]
        texto = (f"{_num(de.get('processos'))} processos com decisão publicada entre {_data(jan[0])} e {_data(jan[1])}: {_num(de.get('monocraticas'))} decisões monocráticas "
                 f"e {_num(de.get('acordaos'))} acórdãos"
                 + (f" ({_num(de.get('acordaos_recurso_interno'))} em recurso interno)" if de.get("acordaos_recurso_interno") is not None else "")
                 + f". Da Presidência: {_num(de.get('presidencia'))}.")
        if de.get("atraso_dias"):
            texto += f" O último arquivo publicado pelo tribunal é de {_data(de.get('ultimo_arquivo'))}: {_dias(de.get('atraso_dias'))} de atraso."
        blocos.append({"titulo": "O que foi decidido", "texto": texto, "fonte": _selo_fonte(fontes.get(de.get("fonte") or ""))})
    te = p.get("temas")
    if isinstance(te, dict):
        tipos = ", ".join(f"{TIPOS_TEMA.get(k, k).lower()} {_num(v)}" for k, v in (te.get("por_tipo") or {}).items() if v)
        texto = f"{_num(te.get('pendentes'))} temas sem julgamento sob relatoria" + (f" ({tipos})" if tipos else "") + "."
        saiu = te.get("relator_saiu") or []
        if saiu:
            texto += " Ainda em nome de quem deixou o tribunal: " + ", ".join(f"{n} ({_num(k)})" for n, k in saiu) + "."
        blocos.append({"titulo": "Temas que pesam para o país", "texto": texto, "fonte": _selo_fonte(fontes.get(te.get("fonte") or ""))})
    return blocos


def _selo_fonte(f: Any) -> str:
    if not isinstance(f, dict):
        return ""
    data = _data(f.get("data_dados"))
    return f"{f.get('nome')}, dados de {data}" if data else str(f.get("nome") or "")


# ── o "Como ler" ─────────────────────────────────────────────────────────────


def _metodo(d: Dados, r: Reguas, tpu: Any) -> dict[str, Any]:
    """O "Como ler": as definições com os números das réguas (``meta.json → reguas``), a tabela do
    que entra em cada área, a de onde vem cada número e o que a página não diz."""
    lim = r.limiares
    c = lim.get("comparacao") or {}
    mesa = lim.get("mesa_dias") or [180, 365]
    faixas = lim.get("idade_faixas_meses") or [6, 12, 24, 36, 60]
    rampa = [float(x) for x in lim.get("rampa") or [0.02, 0.05, 0.10, 0.25]]
    por_classe = [a for a in r.areas if (a.get("regra") or {}).get("classes")]
    classes_txt = "; ".join(f"{', '.join(a['regra']['classes'])} → {a['curto']}" for a in por_classe)
    fontes = d.meta.get("fontes") or {}
    datajud = fontes.get("datajud") or {}
    textos = [
        ("De onde vem", "O retrato de cada tribunal é o que ele mesmo publica em dados abertos. No STJ, o arquivo diário \"Acervo em tramitação\" do Portal de "
         "Dados Abertos (licença CC-BY): um registro por processo, com o relator, a Turma, onde o processo está, os assuntos da Tabela Processual "
         "Unificada do CNJ e as partes; também as atas de distribuição, as pautas, os precedentes qualificados e as íntegras publicadas. No STF, a "
         "planilha do acervo, as atas de distribuição, a pesquisa de jurisprudência e a lista de temas de repercussão geral. O robots.txt de cada "
         "portal é lido e respeitado; a consulta processual dos dois tribunais não é coletada (o link para o processo leva você até lá). "
         "Nenhuma IA entra nas contas."),
        ("A foto do dia", "O acervo é o retrato da data do arquivo, não a soma do ano. \"Com o ministro\" quer dizer: o processo está em tramitação e ele é o "
         "relator hoje. A triagem da Presidência, os processos sem relator e os de quem deixou a Turma aparecem em linhas próprias, fora da conta dos ministros."),
        ("Uma área por processo", f"Cada processo cai numa área pelo código do assunto principal na Tabela Processual Unificada do CNJ, nunca pelo texto do ramo. "
         f"A classe vale antes do assunto ({classes_txt}); depois, o nó mais específico listado na árvore; depois, a raiz. Quando o assunto principal é só de "
         "rito, vale o primeiro assunto de mérito da lista; sem nenhum, o processo fica em \"Só processual\", à mostra. Os outros assuntos aparecem em "
         "\"também toca\" e não somam. No STF os assuntos vêm em texto: o caminho é casado com a árvore pelo nome, nível a nível."),
        ("No STJ, o que entra é sorteado", "Dentro de cada Turma os ministros recebem processos parecidos em número e em assunto. Quando o acervo de um tem mais "
         "de uma área que o dos colegas, a diferença está no que ficou: o que ainda não foi decidido, o que veio por prevenção ou o que foi herdado. Não é "
         "escolha nem especialidade."),
        ("Só com os pares, e só com teste", f"No STJ cada ministro é comparado com os colegas da mesma Turma, sem ele; no STF, com o tribunal sem ele e sem a "
         f"Presidência. A marca ▲/▽ aparece quando a diferença passa do acaso (binomial exata, {_pct(c.get('confianca', 0.99))}, contando todas as comparações "
         f"do tribunal pelo método de Holm) e é grande o bastante: {_decimal((c.get('razao') or [0.8, 1.25])[1])} vez ou mais, ou {_decimal((c.get('razao') or [0.8, 1.25])[0])} "
         f"ou menos, e {c.get('pontos', 2)} pontos ou mais. Só entram áreas com {c.get('minimo_area', 30)} processos ou mais e ministros com "
         f"{_num(c.get('minimo_acervo', 1000))} ou mais no acervo. Quem herdou acervo ou chegou há menos de {lim.get('nao_comparavel_meses', 6)} meses aparece "
         "sem marca, com o selo. A linha \"média\" de cada grupo, em itálico, é contexto: soma os ministros da grade do grupo."),
        ("A cor da tabela", "A célula é a parte do acervo do ministro naquela área, com o número sempre escrito. A cor tem cinco degraus: "
         + ", ".join(f["rotulo"] for f in _faixas_rampa(rampa)) + ". A cor da área só aparece na faixa do alto de cada coluna e nos quadradinhos, sempre com o nome."),
        ("Na mesa", f"É o processo do ministro que está no gabinete dele. O que está no cartório (prazo, intimação, publicação), com o Ministério Público para "
         f"parecer ou suspenso à espera de um precedente está no acervo, mas não na mesa. No STJ, \"sem decisão há N dias\" conta da última distribuição "
         f"ou da última decisão, a mais recente; não é a data em que o processo foi concluso. No STF, conta desde o andamento \"Conclusos ao Relator\". "
         f"Os cortes da página: {_num(mesa[0])} e {_num(mesa[-1])} dias. A idade do processo conta da chegada ao tribunal, em faixas de "
         + ", ".join(f"{m} meses" for m in faixas) + "."),
        ("Quanto vale", "Nenhuma fonte aberta do STF nem do STJ publica o valor da causa. A página não estima. O valor citado no texto das decisões não serve: "
         "em geral é o de um pedido, de uma lei ou de uma multa, não o que está em jogo. No lugar, a página mostra onde há dinheiro público no recurso "
         "(um ente público num dos polos), quantos pedem liminar e quantos processos são de "
         + ", ".join(((r.recortes.get(x) or {}).get("nome") or x).lower() for x in r.recortes_economicos) + "."),
        ("Quem se repete", f"Contamos pessoas jurídicas e entes públicos, agrupados (a União e a Fazenda Nacional, por exemplo), em quantos processos distintos "
         f"aparecem, como quem recorre ou como quem responde. Um litigante só ganha nome com {lim.get('nome_parte_tribunal', 20)} processos ou mais no "
         f"tribunal, ou {lim.get('nome_parte_ministro', 5)} ou mais na mesa de um ministro. A autoridade coatora de um habeas corpus não é parte e fica fora. "
         "Pessoa física e MEI nunca têm o nome na página. Processo em segredo de justiça conta nos números e não aparece em lista nenhuma."),
        ("Recursos internos", "Agravo interno, agravo regimental e embargos de declaração não são processos novos: o processo continua um só. Nas pautas e nas "
         "decisões eles contam como itens."),
    ]
    if datajud:
        textos.append(("O DataJud", str(datajud.get("aviso") or "O DataJud, a base do CNJ, fica desligado: a página não usa número dele.")))
    textos.append(("O que fica de fora", "O Diário de Justiça Eletrônico Nacional (DJEN) recusa o acesso de fora do Brasil e não é lido nesta fase. "
                   "O valor da causa, a data exata da conclusão no STJ e o andamento processo a processo não estão nas fontes abertas lidas aqui."))
    textos.append(("O que isto não diz", "Número de processos não é produtividade nem orientação de quem julga. Acervo grande pode ser herança, prevenção ou "
                   "uma Turma com mais entrada. A área do processo é a que o tribunal registrou, não uma leitura do caso. A página não diz quem tem razão, "
                   "nem como um ministro vai votar. As escolhas editoriais estão escritas aqui para poderem ser contestadas."))
    # o que entra em cada área
    def _codigos(lista: list[int]) -> str:
        return "; ".join(f"{(tpu.nome(cod) if tpu else None) or 'código'} ({cod})" for cod in lista)

    tabela = []
    for a in r.areas:
        regra = a.get("regra") or {}
        partes = []
        if regra.get("classes"):
            partes.append("pela classe: " + ", ".join(regra["classes"]))
        if regra.get("nos"):
            partes.append("nós: " + _codigos(regra["nos"]))
        if regra.get("raizes"):
            partes.append("raízes: " + _codigos(regra["raizes"]))
        if a["id"] == "outros":
            partes.append("e todo código fora das outras regras")
        if a["id"] == "sem_assunto":
            partes.append("a fonte não informou código válido")
        if a["id"] == "processual" and regra.get("residual"):
            partes.append("só quando nenhum assunto do processo é de mérito")
        tabela.append({"id": a["id"], "nome": a["nome"], "regra": "; ".join(partes) or "—"})
    linhas_fontes = []
    for fid, f in fontes.items():
        if not isinstance(f, dict):
            continue
        estado = "desligada" if f.get("ok") is None else "deu certo" if f.get("ok") else "falhou na última coleta"
        linhas_fontes.append({"nome": str(f.get("nome") or fid), "url": filters.safe_url(str(f.get("url") or "")) or "", "data": _data(f.get("data_dados")) or "—",
                              "estado": estado, "registros": _num(f.get("registros")) if f.get("registros") is not None else "—",
                              "atribuicao": str(f.get("atribuicao") or "")})
    cobertura = []
    for trib, cob in (d.meta.get("cobertura") or {}).items():
        if not isinstance(cob, dict):
            continue
        sigla = trib.upper()
        frase = f"{sigla}: {_num(cob.get('processos'))} processos lidos; {_num(cob.get('segredo'))} em segredo de justiça (contam nos números, nunca em lista)."
        cas = cob.get("casamento")
        if isinstance(cas, dict) and cob.get("linhas_de_assunto"):
            total = cob["linhas_de_assunto"]
            frase += (f" Das {_num(total)} linhas de assunto em texto, {_pct((cas.get('folha') or 0) / total, fino=True)} casaram com a TPU até o fim do caminho; "
                      f"{_num(cas.get('parcial'))} em parte; {_num(cas.get('raiz'))} só no ramo; {_num(cas.get('nada'))} não casaram (vale o ramo em texto).")
        fora = cob.get("relatores_fora_do_mapa") or []
        if fora:
            frase += " Relatores fora do mapa da composição (contados como resíduo): " + ", ".join(f"{n} ({_num(k)})" for n, k in fora) + "."
        cobertura.append(frase)
    avisos = [str(a) for a in d.meta.get("avisos") or [] if a]
    correcoes = [{"data": _data(c.get("data")), "texto": str(c.get("texto") or "")} for c in d.meta.get("correcoes") or [] if isinstance(c, dict)]
    return {"textos": textos, "tabela": tabela, "fontes": linhas_fontes, "cobertura": cobertura, "avisos": avisos, "correcoes": correcoes}


# ── a página ─────────────────────────────────────────────────────────────────


def _nomes_partes(d: Dados, config: Config) -> dict[str, str]:
    """Nome de cada litigante citado (só PJ e ente público): o do painel (``partes.top`` e o mapa
    ``partes.nomes``) e, de reserva, o do catálogo ``config/tribunais_partes.yaml``."""
    nomes: dict[str, str] = {}
    try:
        import yaml  # o catálogo é do módulo de partes: aqui só os nomes

        bruto = yaml.safe_load((Path(config.root) / "config" / "tribunais_partes.yaml").read_text(encoding="utf-8")) or {}
        for item in bruto.get("grupos") or []:
            if isinstance(item, dict) and item.get("id") and item.get("nome"):
                nomes[str(item["id"])] = str(item["nome"])
    except (OSError, ValueError, AttributeError):
        pass
    for p in d.paineis.values():
        for gid, v in ((p.get("partes") or {}).get("nomes") or {}).items():  # {grupo: {nome, tipo}} de todo grupo citado
            nome = v.get("nome") if isinstance(v, dict) else v
            if gid and nome:
                nomes[str(gid)] = str(nome)
        for x in (p.get("partes") or {}).get("top") or []:
            if x.get("id") and x.get("nome"):
                nomes[str(x["id"])] = str(x["nome"])
    return nomes


def _classes(tpu: Any) -> dict[str, dict[str, str]]:
    """Sigla usual → nome por extenso da classe, para a folha e as dicas (do STF e do STJ)."""
    saida: dict[str, dict[str, str]] = {}
    if tpu is None:
        return saida
    for trib in TRIBUNAIS:
        nomes = {}
        for sigla in sorted(getattr(tpu, "_siglas", {}).get(trib, {})):
            nome = tpu.nome_classe(trib, sigla)
            if nome:
                nomes[sigla] = nome
        saida[trib] = nomes
    return saida


def _tribunal(trib: str, d: Dados, r: Reguas, nomes: dict[str, str], tpu: Any, padrao: str) -> dict[str, Any]:
    p = d.paineis.get(trib)
    linha_fontes, avisos = _fontes(trib, d.meta)
    base = {"id": trib, "sigla": trib.upper(), "nome": NOMES[trib], "padrao": trib == padrao, "fontes": linha_fontes, "avisos": avisos}
    if p is None:
        acervo = (d.meta.get("fontes") or {}).get(f"{trib}_acervo") or {}
        motivo = str(acervo.get("aviso") or "A coleta ainda não trouxe o acervo deste tribunal.")
        return {**base, "tem": False, "n_ministros": "—",
                "vazio": f"Sem dados do {trib.upper()} nesta página. {motivo} A página volta a mostrar o {trib.upper()} na próxima coleta que der certo."}
    relatores = {m["id"]: m.get("nome") or m["id"] for m in p.get("ministros") or []}
    u = p.get("universo") or {}
    idade = p.get("idade") or {}
    colunas, fora_colunas = _colunas(p, r)
    pares_longo, _ = _texto_pares(trib, r.limiares)
    n_grade = sum(1 for m in p.get("ministros") or [] if m.get("grupo") != "pr")
    v_idade, u_idade = _anos((idade.get("no_tribunal") or {}).get("mediana"))
    valor = p.get("valor") or {}
    medidas = valor.get("medidas") or {}
    ente, liminar = medidas.get("ente_publico") or {}, medidas.get("liminar") or {}
    recortes_valor = [{"nome": (r.recortes.get(k) or {}).get("nome") or k, "n": _num(n)} for k, n in (medidas.get("recortes") or {}).items()]
    sd_rot = "sem decisão registrada" if trib == "stj" else "conclusos ao relator"
    tiles = [
        {"k": f"Na mesa dos {n_grade}", "dica": "mesa_tribunal",
         "v": _num(u.get("na_mesa")), "u": "", "x": f"de {_num(u.get('na_grade'))} com eles; {_num(u.get('processos'))} no tribunal, em {_data(p.get('data_dados'))}"},
        {"k": "Sem decisão há + de 1 ano" if trib == "stj" else "Concluso há + de 1 ano",
         "dica": f"sem_decisao_{trib}",
         "v": _num(u.get("sem_decisao_365")), "u": "", "x": f"{_pct((u.get('sem_decisao_365') or 0) / u['na_mesa'] if u.get('na_mesa') else 0, fino=True)} da mesa; {_num(u.get('sem_decisao_180'))} há mais de {_num((r.limiares.get('mesa_dias') or [180])[0])} dias"},
        {"k": "Idade mediana", "dica": "idade", "v": v_idade, "u": u_idade,
         "x": f"desde a chegada ao {trib.upper()}"},
        {"k": "Valor da causa", "dica": "valor", "v": "", "u": "", "sem_numero": True,
         "x": f"as fontes abertas não publicam. Ente público num polo: {_pct(ente.get('p'), fino=True)} do acervo ({_num(ente.get('n'))})"},
    ]
    for k in tiles:
        k.setdefault("sem_numero", False)
    mapa = [
        {"vista": "ministros", "nome": "Ministros", "texto": "Quem está com o quê: a matriz ministro × área e a mesa de cada um.", "n": _num(n_grade), "n_txt": "ministros nas Turmas"},
        {"vista": "assuntos", "nome": "Assuntos", "texto": f"De que se ocupa o {trib.upper()}, e quem cuida de cada assunto.", "n": _num(sum(1 for a in p.get("areas") or [] if a.get("n"))), "n_txt": "áreas com processo"},
        {"vista": "partes", "nome": "Partes", "texto": "Quem mais aparece, de que lado e em quantas mesas.", "n": _num(len((p.get("partes") or {}).get("top") or [])), "n_txt": "litigantes com nome"},
        {"vista": "fila", "nome": "A fila", "texto": "O que espera há mais tempo, e na mesa de quem.", "n": _num(u.get("sem_decisao_365")), "n_txt": f"{sd_rot} há mais de um ano"},
    ]
    lista_mesa = []
    por_id = {m["id"]: m for m in p.get("ministros") or []}
    for g in p.get("grupos") or []:
        itens = []
        for mid in g.get("ministros") or []:
            m = por_id.get(mid)
            if m:
                dig, aria = _digital(m, r)
                itens.append({"id": mid, "nome": m.get("nome") or mid, "processos": _num(m.get("processos")), "digital": dig, "aria": aria})
        if itens:
            lista_mesa.append({"id": g.get("id"), "nome": g.get("nome"), "itens": itens})
    chips = [{"id": "", "nome": "Todos", "n": len(p.get("ministros") or [])}]
    chips += [{"id": g.get("id"), "nome": g.get("nome"), "n": len(g.get("ministros") or [])} for g in p.get("grupos") or []]
    return {
        **base, "tem": True, "n_ministros": len(p.get("ministros") or []), "data": _data(p.get("data_dados")), "data_curta": _data(p.get("data_dados"), ano=False),
        "leitura": _leitura(trib, p, d.achados, r), "tiles": tiles, "mapa": mapa,
        "valor": {"texto": str(valor.get("texto") or ""), "ente": f"{_pct(ente.get('p'), fino=True)} ({_num(ente.get('n'))} processos)",
                  "liminar": f"{_pct(liminar.get('p'), fino=True)} ({_num(liminar.get('n'))} processos)", "recortes": recortes_valor},
        "pares": pares_longo, "colunas": colunas, "nota_colunas": _nota_colunas(fora_colunas),
        "matriz": _matriz(trib, p, r, colunas), "chips": chips, "lista_mesa": lista_mesa,
        "cartoes": [_cartao(trib, p, m, r, nomes, relatores) for m in p.get("ministros") or []],
        "assuntos": _assuntos(trib, p, r, nomes, tpu), "partes": _partes(trib, p, r), "fila": _fila(trib, p, r, nomes, relatores),
        "fluxo": _fluxo(trib, p, d.meta),
        "colunas_grupo": {g.get("id"): sorted(a for a, (n, frac) in (g.get("areas") or {}).items() if frac < 0.01) for g in p.get("grupos") or [] if g.get("id") != "pr"},
    }


def _padrao(d: Dados) -> str:
    """O tribunal que abre: o primeiro (STF, STJ) com o acervo do dia coletado; senão o primeiro com dados."""
    fontes = d.meta.get("fontes") or {}
    for trib in TRIBUNAIS:
        if trib in d.paineis and ((fontes.get(f"{trib}_acervo") or {}).get("ok") is True):
            return trib
    return next(t for t in TRIBUNAIS if t in d.paineis)


def _carregar_tpu() -> Any:
    try:
        from ..tribunais import tpu

        return tpu.TPU.carregar()
    except (OSError, ValueError, KeyError) as exc:  # sem a TPU, os códigos saem sem nome
        log.warning("Tribunais: TPU ilegível (%s); os assuntos saem pelo código", exc)
        return None


def tribunais_view(d: Dados, config: Config) -> dict[str, Any]:
    """Tudo o que o modelo ``tribunais.html.j2`` usa (as chaves existem todas: o ambiente é estrito)."""
    r = _reguas(d.meta, config)
    tpu = _carregar_tpu()
    nomes = _nomes_partes(d, config)
    padrao = _padrao(d)
    tribunais = [_tribunal(t, d, r, nomes, tpu, padrao) for t in TRIBUNAIS]
    # As cores vêm do meta.json (dado, não código): só passam as que são cor de verdade.
    areas_css = [{"id": a["id"], "claro": filters.safe_color(str(a["cor"][0]), "#7b8494"), "escuro": filters.safe_color(str(a["cor"][1]), "#8d95a3"),
                  "texto": filters.safe_color(str(a.get("texto") or ""), "#0b0f17")}
                 for a in r.areas if isinstance(a.get("cor"), list) and len(a["cor"]) == 2 and _id_seguro(a["id"])]
    hoje = str(d.meta.get("hoje") or "")
    ministros = {m["id"]: m.get("nome") or m["id"] for p in d.paineis.values() for m in p.get("ministros") or []}
    manifesto = {
        "v": VERSAO, "hoje": hoje, "padrao": padrao, "tribs": list(TRIBUNAIS),  # o tribunal sem dados também abre: mostra o porquê
        "comDados": [t for t in TRIBUNAIS if t in d.paineis],
        "base": f"../data/{PASTA}/ministros/", "dados": f"../data/{PASTA}/",
        "areas": {a["id"]: [a["curto"], a["nome"]] for a in r.areas}, "ordem": [a["id"] for a in r.areas],
        "subareas": r.subareas, "recortes": {k: (v.get("nome") or k) for k, v in r.recortes.items()},
        "partes": nomes, "classes": _classes(tpu), "onde": ONDE, "motivos": MOTIVOS, "tiposTema": TIPOS_TEMA, "tiposParte": TIPOS_PARTE,
        "faixas": _rotulos_faixas(r.limiares.get("idade_faixas_meses") or [6, 12, 24, 36, 60]),
        "gloss": GLOSSARIO,
        "ministros": ministros,
        "generos": {m["id"]: m.get("genero") or "m" for p in d.paineis.values() for m in p.get("ministros") or []},
        "t": {t["id"]: {"sigla": t["sigla"], "nome": t["nome"], "pares": _texto_pares(t["id"], r.limiares)[1],
                        "grupos": r.grupos.get(t["id"], {}), "orgaos": r.orgaos.get(t["id"], {}),
                        "colunasGrupo": t.get("colunas_grupo", {}), "rotDias": "sem decisão há" if t["id"] == "stj" else "concluso há",
                        "data": (d.paineis.get(t["id"]) or {}).get("data_dados") or hoje}
              for t in tribunais if t["tem"]},
    }
    return {
        "atualizado": _data(hoje), "tribunais": tribunais, "padrao": padrao, "areas_css": areas_css,
        "areas": [{"id": a["id"], "curto": a["curto"], "nome": a["nome"]} for a in r.areas],
        "metodo": _metodo(d, r, tpu), "regua_marca": _regua_marca(r.limiares),
        "manifesto": json.dumps(manifesto, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c"),
    }


def _id_seguro(valor: Any) -> bool:
    """Um id que pode virar nome de classe CSS (as áreas vêm do meta.json)."""
    return isinstance(valor, str) and bool(valor) and all(c.isascii() and (c.isalnum() or c in "_-") for c in valor)


def _rotulos_faixas(meses: list[int]) -> list[str]:
    """``[6, 12, 24, 36, 60]`` → ``["até 6 meses", "6 a 12 meses", "1 a 2 anos", "2 a 3 anos", "3 a 5 anos", "mais de 5 anos"]``:
    em anos quando as duas pontas são anos inteiros; senão, em meses."""
    def em_anos(x: int) -> bool:
        return x >= 12 and x % 12 == 0

    rotulos = [f"até {meses[0]} meses"]
    for a, b in pairwise(meses):
        rotulos.append(f"{a // 12} a {b // 12} anos" if em_anos(a) and em_anos(b) else f"{a} a {b} meses")
    rotulos.append(f"mais de {meses[-1] // 12} anos" if em_anos(meses[-1]) else f"mais de {meses[-1]} meses")
    return rotulos


def render_tribunais_page(config: Config, data_dir: Path, *, home_href: str, archive_href: str) -> str | None:
    """Página ``edicoes/tribunais.html`` (``None`` sem dados em ``data/tribunais/``)."""
    d = carregar(data_dir)
    if d is None:
        return None
    view = tribunais_view(d, config)
    config.tribunais.publicado = True  # a própria aba mostra o botão dela (com aria-current)
    template = filters.environment().get_template("tribunais.html.j2")
    html = template.render(
        t=view,
        brand=brand_view(config),
        home_href=filters.safe_href(home_href) or "../",
        archive_href=filters.safe_href(archive_href) or "./",
        base_url=config.site.base_url,
        repo_url=filters.safe_url(config.site.repo_url) or "",
        analitico=config.termometro_ativo,
    )
    log.info("Aba Tribunais renderizada: %s, %d KB", ", ".join(sorted(d.paineis)), len(html.encode("utf-8")) // 1024)
    return html


def gravar_pagina(dir_saida: Path, config: Config) -> Path | None:
    """Lê ``<dir_saida>/data/tribunais/`` e grava ``<dir_saida>/edicoes/tribunais.html``.

    Devolve o caminho gravado, ou ``None`` quando a marca não usa a aba, quando não há dados (ou são
    de outra versão) ou quando a página passaria de ``TETO_BYTES`` (aí ela não é gravada: fica a
    anterior, e o log diz o tamanho). Acende o botão cruzado do Congresso quando a aba dele existe."""
    from .. import pipeline  # import tardio: o pipeline importa as páginas

    if not config.tribunais_ativo:
        log.info("Tribunais desligado para a marca %s; a página não é gravada", config.brand.key)
        return None
    dir_saida = Path(dir_saida)
    if config.congresso_ativo and not config.congresso.publicado:
        config.congresso.publicado = (dir_saida / pipeline.ARCHIVE_DIR / "congresso.html").is_file()
    html = render_tribunais_page(config, dir_saida / pipeline.DATA_DIR, home_href="../", archive_href="./")
    if html is None:
        return None
    tamanho = len(html.encode("utf-8"))
    if tamanho > TETO_BYTES:
        log.error("Tribunais: a página teria %d KB, acima do teto de %d KB; não foi gravada (fica a anterior)", tamanho // 1024, TETO_BYTES // 1024)
        return None
    return pipeline.write_atomic(dir_saida / pipeline.ARCHIVE_DIR / PAGINA, html)
