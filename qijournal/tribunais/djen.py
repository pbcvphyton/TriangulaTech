"""DJEN: o Diário de Justiça Eletrônico Nacional (CNJ) do STJ e do STF, por ministro.

O que é. Desde 29/11/2024 os atos judiciais do STJ (despachos, decisões, ementas de acórdãos,
intimações, distribuição) saem no DJEN, a plataforma do CNJ disciplinada pela Res. CNJ 455/2022
(Res. STJ/GP 19/2024; a página "Publicações Oficiais do STJ" aponta para
``comunica.pje.jus.br/consulta?siglaTribunal=STJ&meio=D``). O STF mantém DJe próprio (Res. STF
700/2020 e 725/2021) e, até onde se sabe, **não** publica no DJEN: a sonda confere.

Onde roda. A API pública de consulta (``GET https://comunicaapi.pje.jus.br/api/v1/comunicacao``)
recusa com 403 geográfico (CloudFront) quem está fora do Brasil, e os executores do GitHub Actions
estão fora. Por isso este módulo roda **numa máquina do dono, no Brasil**, por uma tarefa agendada
(``scripts/djen/``), e envia só ``data/tribunais/djen.json``. Nada aqui contorna a recusa: o 403 vira
:class:`~.fontes.RecusaDoHost` no primeiro pedido (sem nova tentativa) e a coleta para. A edição e a
aba nunca dependem disto: sem ``djen.json`` novo, a aba sai sem a camada.

Dois modos (``python -m qijournal tribunais djen``):

- ``--sondar``: poucos pedidos (o robots.txt e uma página por tribunal, num dia útil recente) para
  responder, para o STJ e o STF: a API responde daqui? o tribunal publica no DJEN? quantos itens
  num dia? que tipos de documento? de que órgãos? o relator aparece no texto? Grava a resposta
  **mascarada** (``<cache>/djen/sonda/``) para virar amostra de teste.
- coleta (padrão): os dias úteis fechados da janela (30 dias), aos poucos (``dias_por_execucao``),
  página a página (``itensPorPagina=100``), com o ritmo do config; guarda no cache um registro
  **enxuto** de cada publicação (nada do texto, nenhum nome de pessoa física, nenhum advogado) e
  grava ``data/tribunais/djen.json`` com o agregado do último dia e da janela.

O que sai de cada publicação (``Leitor.ler``):

- **o relator**: o gabinete no ``nomeOrgao`` ("GABINETE DO MINISTRO ..."); senão a linha
  "RELATOR: MINISTRO ..." (ou a assinatura "Ministro ... / Relator") do texto; senão, na hora de
  somar, o relator do processo no acervo do dia (casamento pelo número único). "Presidente do STJ"
  vai para a Presidência, nunca para o cartão de quem preside. Sem nada disso, conta só no órgão;
- **o tipo**: decisão, acórdão, despacho, pauta, distribuição, edital ou outros, pelo
  ``tipoDocumento`` (e, quando ele é genérico, pelo título do texto), pelas réguas do config;
- **a área**: a do processo no acervo (casado pelo número único); senão a da classe
  (``codigoClasse`` da TPU → sigla do tribunal → ``areas.py``), quando a classe decide a área;
- **as partes**: os ``destinatarios`` com o polo (A/P), tipificados pelo catálogo
  (``partes.py``): só o **grupo** de litigante do catálogo (União, INSS, Estado de São Paulo, Banco
  do Brasil...); pessoa física e MEI (CPF no nome) ficam só na contagem, sem nome; advogados viram um
  número (nunca nome nem OAB);
- **segredo**: marcado pelo acervo ou pelo texto ("segredo de justiça"); conta nos números e
  nunca é listado nem entra nas partes.

O casamento com o acervo usa o **retrato** compacto que ``dados.py`` grava no cache
(``<cache>/retratos/<trib>-AAAA-MM-DD.tsv.gz``). Na máquina do dono, sem o cache do fluxo dos
Tribunais, o retrato é refeito aqui uma vez por dia (``acervo.baixar``): o arquivo aberto do acervo
do STJ (Portal de Dados Abertos, CC-BY) e a planilha do STF, pelo mesmo coletor (robots.txt, 10 s
entre pedidos ao portal do STJ). Sem retrato, sai tudo menos a "mesa".

O contrato de ``djen.json`` (para quem integra a camada na página) está no fim deste módulo
(:func:`agregar`) e em ``scratchpad/rodada3/djen-contrato.md``.

Não confirmado daqui (a primeira coleta no Brasil diz): se o filtro ``siglaTribunal`` da API é
respeitado (a sonda confere e a coleta para se não for); se o STF aparece; o formato do
``nomeOrgao`` e do ``tipoDocumento`` do STJ; se a API limita a paginação (``count`` grande demais);
o robots.txt de ``comunicaapi.pje.jus.br`` (lido e respeitado na hora, pelo coletor).
"""

from __future__ import annotations

import gzip
import html
import json
import logging
import os
import re
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import yaml

from .. import net
from ..congresso.dados import _gravar, _json
from ..net import FetchError
from . import PASTA
from .areas import Classificador
from .fontes import CaminhoVedado, ColetorTribunais, RecusaDoHost
from .modelo import ARQUIVO as ARQUIVO_CONFIG
from .modelo import Composicao, Regras, carregar_regras, normalizar
from .partes import Catalogo, carregar_catalogo
from .tpu import SIGLAS_USUAIS, TPU

log = logging.getLogger(__name__)

VERSAO_DJEN = 1
ARQUIVO = "djen.json"  # em data/tribunais/
API = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
HOST = "comunicaapi.pje.jus.br"
CONSULTA = "https://comunica.pje.jus.br/consulta"  # a consulta para pessoas (não é coletada)
SIGLAS = {"stj": "STJ", "stf": "STF"}
FUSO = "America/Sao_Paulo"
# A pasta data/tribunais/ inteira tem teto de 3 MB (dados.TETO_PASTA) e já usa ~2,6 MB: o djen.json
# fica bem abaixo dos 300 KB de cada JSON, para nunca empurrar os painéis para fora do teto.
TETO_JSON = 150_000
ALVO_JSON = 100_000  # acima disto as listas encolhem
PASTA_CACHE = "djen"

# As réguas padrão (o bloco ``djen:`` de config/tribunais.yaml as substitui chave a chave).
PADRAO: dict[str, Any] = {
    "ativo": True,
    "api": API,
    "consulta": CONSULTA,
    "tribunais": ["stj", "stf"],
    "itens_por_pagina": 100,
    "pausa_s": 2.0,
    "max_paginas_dia": 150,
    "janela_dias": 30,
    "dias_por_execucao": 5,
    "incluir_hoje": False,
    "prazo_s": 2400,
    "guardar_dias": 40,
    "sem_publicacao_dias": 3,
    "origem": "coletado no Brasil, fora do GitHub Actions",
    "atribuicao": "Fonte: CNJ, Diário de Justiça Eletrônico Nacional (DJEN)",
    "acervo": {"baixar": True, "retrato_validade_h": 30},
    "listas": {"mesa_por_ministro": 5, "partes": 10, "partes_ministro": 3, "orgaos": 40, "tipos_brutos": 30},
    "sonda": {"itens": 100, "tentativas_dias": 3, "amostra": 12},
    "tipos": [
        {"id": "pauta", "nome": "Pautas de julgamento", "padroes": ["PAUTA", "SESSAO DE JULGAMENTO", "SESSAO VIRTUAL"]},
        {"id": "distribuicao", "nome": "Distribuição", "padroes": ["DISTRIBUICAO"]},
        {"id": "acordao", "nome": "Acórdãos", "padroes": ["ACORDAO", "EMENTA"]},
        {"id": "decisao", "nome": "Decisões", "padroes": ["DECISAO", "SENTENCA", "MONOCRATICA"]},
        {"id": "despacho", "nome": "Despachos", "padroes": ["DESPACHO", "ATO ORDINATORIO"]},
        {"id": "edital", "nome": "Editais", "padroes": ["EDITAL"]},
    ],
}
OUTROS = "outros"
SEM_AREA = "sem_area"
# tipoDocumento que não diz nada: o título do texto decide.
_GENERICOS = frozenset({"", "INTIMACAO", "COMUNICACAO", "OUTROS", "OUTRO", "EXPEDIENTE", "PUBLICACAO", "DOCUMENTO", "NOTIFICACAO"})
_POLOS = {"A": "ativo", "P": "passivo"}


class ErroDoDJEN(FetchError):
    """Resposta da API fora do formato documentado (sem ``items``/``itens``, ``status`` de erro)."""


class FiltroIgnorado(ErroDoDJEN):
    """A API devolveu publicação de outro tribunal ou de outro dia: o filtro não vale, e paginar o
    país inteiro não é gentil. A coleta daquele tribunal para."""


# ── configuração ─────────────────────────────────────────────────────────────────────────────


def _mesclar(padrao: Mapping[str, Any], dado: Mapping[str, Any] | None) -> dict[str, Any]:
    saida = dict(padrao)
    for k, v in (dado or {}).items():
        saida[k] = _mesclar(saida[k], v) if isinstance(v, Mapping) and isinstance(saida.get(k), Mapping) else v
    return saida


def carregar_config(raiz: Path | str | None = None, bruto: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """O bloco ``djen:`` de ``config/tribunais.yaml`` (o ``modelo.carregar_regras`` ignora chave
    que não conhece) sobre :data:`PADRAO`. ``bruto`` (testes): o bloco já lido."""
    if bruto is None:
        raiz = Path(raiz) if raiz is not None else Path(__file__).resolve().parents[2]
        caminho = raiz / "config" / ARQUIVO_CONFIG
        try:
            bruto = (yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}).get("djen") or {}
        except OSError:
            bruto = {}
    cfg = _mesclar(PADRAO, bruto)
    cfg["itens_por_pagina"] = max(1, min(100, int(cfg["itens_por_pagina"])))
    cfg["tribunais"] = [t for t in (cfg.get("tribunais") or ()) if t in SIGLAS]
    return cfg


def regras_do_coletor(regras: Regras, cfg: Mapping[str, Any]) -> SimpleNamespace:
    """As regras de fontes com o host do DJEN: a pausa do config e **nenhuma** nova tentativa no
    403 (a recusa geográfica não passa com espera; o primeiro 403 encerra)."""
    fontes = dict(regras.fontes)
    hosts = dict(fontes.get("hosts") or {})
    hosts[HOST] = {"pausa_s": float(cfg.get("pausa_s", 2.0)), "recusa_espera_s": None}
    fontes["hosts"] = hosts
    return SimpleNamespace(fontes=fontes)


# ── datas ────────────────────────────────────────────────────────────────────────────────────


def dias_uteis(ate: date, quantos: int) -> list[date]:
    """Os ``quantos`` dias de segunda a sexta até ``ate`` (inclusive), do mais novo ao mais velho.
    Feriado não é conhecido aqui: vira um dia com 0 publicações."""
    dias: list[date] = []
    d = ate
    while len(dias) < quantos:
        if d.weekday() < 5:
            dias.append(d)
        d -= timedelta(days=1)
    return dias


def janela(hoje: date, cfg: Mapping[str, Any]) -> tuple[date, date]:
    """(primeiro dia, último dia) da janela: ``janela_dias`` dias corridos até ontem (ou até hoje,
    com ``incluir_hoje``)."""
    ate = hoje if cfg.get("incluir_hoje") else hoje - timedelta(days=1)
    return ate - timedelta(days=int(cfg["janela_dias"]) - 1), ate


def _instante(momento: datetime) -> str:
    return momento.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dia_local(instante: str | None) -> str | None:
    if not instante:
        return None
    try:
        return datetime.strptime(instante, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).astimezone(ZoneInfo(FUSO)).date().isoformat()
    except ValueError:
        return None


# ── a API ────────────────────────────────────────────────────────────────────────────────────


def url_da_consulta(sigla: str, dia: str, pagina: int = 1, por_pagina: int = 100, api: str = API) -> str:
    """O pedido de um dia de um tribunal. Os nomes dos parâmetros são os da documentação do CNJ e
    dos clientes públicos da API (``pagina``, ``itensPorPagina`` de 1 a 100, ``siglaTribunal``,
    ``dataDisponibilizacaoInicio``/``Fim`` em AAAA-MM-DD); a ordem é fixa (o cache usa a URL)."""
    return api + "?" + urlencode({
        "pagina": pagina, "itensPorPagina": por_pagina, "siglaTribunal": sigla,
        "dataDisponibilizacaoInicio": dia, "dataDisponibilizacaoFim": dia,
    })


def url_para_navegador(trib: str, dia: str, por_pagina: int = 5) -> str:
    """O endereço que o dono abre no navegador, no Brasil, para ver se o tribunal aparece."""
    return url_da_consulta(SIGLAS[trib], dia, 1, por_pagina)


def envelope(dados: Any, url: str = "") -> tuple[list[Mapping[str, Any]], int | None]:
    """``{status, message, count, items}`` → (itens, count). A resposta antiga usava ``itens``."""
    if not isinstance(dados, Mapping):
        raise ErroDoDJEN(url, "resposta do DJEN não é um objeto JSON")
    status = str(dados.get("status") or "success").lower()
    if status not in ("success", "sucesso", "ok"):
        raise ErroDoDJEN(url, f"o DJEN respondeu status {dados.get('status')!r}: {str(dados.get('message') or '')[:120]}")
    lista = dados.get("items", dados.get("itens"))
    if lista is None:
        raise ErroDoDJEN(url, "resposta do DJEN sem items/itens")
    if not isinstance(lista, list):
        raise ErroDoDJEN(url, "items do DJEN não é uma lista")
    try:
        total = int(dados["count"]) if dados.get("count") is not None else None
    except (TypeError, ValueError):
        total = None
    return [x for x in lista if isinstance(x, Mapping)], total


# ── o texto (lido e jogado fora) ─────────────────────────────────────────────────────────────

_QUEBRA = re.compile(r"<\s*(?:br|/p|/div|/tr|/li|/h\d|/table|p|div|tr|li|h\d)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def texto_simples(conteudo: str | None) -> str:
    """O HTML da publicação em linhas de texto (tags fora, entidades desfeitas, espaços colapsados)."""
    s = _QUEBRA.sub("\n", conteudo or "")
    s = html.unescape(_TAG.sub(" ", s))
    linhas = (" ".join(linha.split()) for linha in s.splitlines())
    return "\n".join(linha for linha in linhas if linha)


_RELATOR = re.compile(r"^RELATOR(?:A|\s*\(A\))?\s*(P/\s*(?:O\s+)?ACORDAO|PARA\s+(?:O\s+)?ACORDAO|DESIGNAD[OA])?\s*:\s*(.+)$")
_ASSINA = re.compile(r"^(?:MINISTR[OA]|MIN\.)\s+(.+)$")
_SO_RELATOR = re.compile(r"^RELATOR(?:A|\s*\(A\))?\.?$")
_TRATAMENTO = re.compile(r"^(?:(?:O|A)\s+)?(?:EXM[OA]\.?\s*)?(?:SR[A]?\.?\s*|SENHOR[A]?\s+)?")
_FIM_DO_NOME = re.compile(
    r"\s+(?:-|–|—|\||,)\s.*$|\s{2,}.*$|\s+(?:AGRAVANTE|AGRAVADO|RECORRENTE|RECORRIDO|IMPETRANTE|IMPETRADO|PACIENTE|ADVOGAD|"
    r"INTERESSAD|REQUERENTE|REQUERIDO|EMBARGANTE|EMBARGADO|AUTOR|REU|RECLAMANTE|RECLAMADO|PROCURAD|SUSCITANTE|SUSCITADO|"
    r"EMENTA|DECISAO|DESPACHO|ACORDAO|CERTIDAO)\b.*$"
)


def _limpar_nome(valor: str) -> str:
    v = _TRATAMENTO.sub("", valor.strip())
    v = _FIM_DO_NOME.sub("", v)
    return v.strip(" .;:-")


def nome_do_relator(texto: str) -> str | None:
    """O relator como o texto escreve, em forma de comparação: a linha "RELATOR: MINISTRO X"
    (a do processo vence a "RELATOR P/ ACÓRDÃO"), senão a assinatura "MINISTRO X" seguida da
    linha "RELATOR". ``None`` sem nenhuma das duas. Citação de precedente ("Rel. Ministro X,
    Terceira Turma") não conta: só linha que começa com a palavra."""
    linhas = [normalizar(linha) for linha in (texto or "").splitlines()]
    designado: str | None = None
    for linha in linhas:
        m = _RELATOR.match(linha)
        if not m:
            continue
        nome = _limpar_nome(m.group(2))
        if not nome:
            continue
        if m.group(1):
            designado = designado or nome
            continue
        return nome
    if designado:
        return designado
    for atual, seguinte in zip(linhas, linhas[1:], strict=False):
        if _SO_RELATOR.match(seguinte):
            m = _ASSINA.match(atual)
            if m:
                return _limpar_nome(m.group(1))
    return None


_VICE = re.compile(r"^VICE[- ]PRESIDEN(?:TE|CIA)\b")
_PRESIDENTE = re.compile(r"^PRESIDEN(?:TE|CIA)\b(?! D[AO] (?:PRIMEIRA|SEGUNDA|TERCEIRA|QUARTA|QUINTA|SEXTA|CORTE|COMISSAO|SECAO|TURMA))")


def _cargo(nome: str, trib: str) -> str:
    """"MINISTRO PRESIDENTE DO STJ" ou o órgão "PRESIDÊNCIA" → "Presidente do STJ" (a linha fora da
    grade; STF: "PRESIDENTE"); o mesmo para a Vice. Presidente de Turma ou de Seção não muda."""
    chave = re.sub(r"^(?:MINISTR[OA]|MIN\.)\s+", "", normalizar(nome))
    if _VICE.match(chave):
        return "Vice-Presidente do STJ" if trib == "stj" else "VICE-PRESIDENTE"
    if _PRESIDENTE.match(chave):
        return "Presidente do STJ" if trib == "stj" else "PRESIDENTE"
    return nome


_TITULO = re.compile(r"^(?:DECISAO|DESPACHO|ACORDAO|EMENTA|PAUTA|INTIMACAO|CERTIDAO|EDITAL|ATA|SENTENCA)\b[A-Z ]{0,40}$")
_SEGREDO = re.compile(r"SEGREDO DE JUSTICA|SEGREDO JUSTICA|\bSIGILOS[OA]\b")


def em_segredo(texto: str, item: Mapping[str, Any]) -> bool:
    """Marca de segredo no texto ou no tipo do documento (na dúvida, não lista)."""
    alvo = normalizar(" ".join(str(item.get(k) or "") for k in ("tipoDocumento", "nomeClasse"))) + "\n" + normalizar(texto[:4000])
    return _SEGREDO.search(alvo) is not None


def tipo_do_item(item: Mapping[str, Any], texto: str, tipos: Sequence[Mapping[str, Any]]) -> str:
    """O tipo pelas réguas (``djen.tipos``): o ``tipoDocumento``; se ele é genérico ou não casa,
    as três primeiras linhas do texto; edital pelo ``tipoComunicacao``; senão ``outros``."""
    td = normalizar(str(item.get("tipoDocumento") or ""))
    candidatos = [td] if td and td not in _GENERICOS else []
    candidatos.append(" | ".join(normalizar(linha) for linha in texto.splitlines()[:3]))
    for alvo in candidatos:
        for t in tipos:
            if any(p in alvo for p in t.get("padroes") or ()):
                return str(t["id"])
    if normalizar(str(item.get("tipoComunicacao") or "")).startswith("EDITAL") or str(item.get("meio") or "").upper() == "E":
        return "edital"
    return OUTROS


# ── uma publicação ───────────────────────────────────────────────────────────────────────────

_CPF = re.compile(r"(?<!\d)\d{11}(?!\d)|(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")


def _data_iso(item: Mapping[str, Any]) -> str | None:
    bruto = str(item.get("data_disponibilizacao") or item.get("dataDisponibilizacao") or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", bruto):
        return bruto[:10]
    br = str(item.get("datadisponibilizacao") or bruto)
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})", br.strip())
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _cnj(item: Mapping[str, Any]) -> str | None:
    for chave in ("numero_processo", "numeroProcesso", "numeroprocessocommascara"):
        digitos = re.sub(r"\D", "", str(item.get(chave) or ""))
        if len(digitos) == 20:
            return digitos
    return None


def _inteiro(valor: Any) -> int | None:
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


class Leitor:
    """Lê uma publicação do DJEN e devolve o registro enxuto que vai para o cache (nada do texto,
    nenhum nome de pessoa, nenhum advogado). Monta-se uma vez por execução."""

    def __init__(self, regras: Regras, tpu: TPU, classificador: Classificador, catalogo: Catalogo, cfg: Mapping[str, Any]) -> None:
        self.regras = regras
        self.tpu = tpu
        self.classificador = classificador
        self.catalogo = catalogo
        self.tipos = list(cfg.get("tipos") or PADRAO["tipos"])
        # código TPU da classe → sigla usual do tribunal ("1722" → "RHC" no STJ): as exceções de
        # tpu.SIGLAS_USUAIS vencem a sigla da TPU ("AgREsp", "ROHC")
        self._sigla: dict[str, dict[int, str]] = {}
        for trib, tabela in tpu.siglas.items():
            inversa: dict[int, str] = {}
            for sigla, cod in sorted(tabela.items()):
                inversa.setdefault(cod, sigla)
            inversa.update({cod: sigla for sigla, cod in SIGLAS_USUAIS.get(trib, {}).items() if sigla not in ("SE",)})
            self._sigla[trib] = inversa
        self._orgaos = {
            trib: sorted(((normalizar(nome), cod) for cod, nome in comp.orgaos.items()), key=lambda x: -len(x[0]))
            for trib, comp in regras.composicao.items()
        }

    def composicao(self, trib: str) -> Composicao | None:
        return self.regras.composicao.get(trib)

    def sigla_da_classe(self, trib: str, codigo: int | None) -> str | None:
        if codigo is None:
            return None
        sigla = self._sigla.get(trib, {}).get(codigo)
        if sigla:
            return sigla
        classe = self.tpu.classe(codigo)
        return classe.get("sigla") if classe else None

    def orgao_colegiado(self, trib: str, nome_orgao: str) -> str | None:
        """"COORDENADORIA DA TERCEIRA TURMA" → ``T3`` (o código da composição); senão ``None``."""
        chave = normalizar(nome_orgao)
        return next((cod for nome, cod in self._orgaos.get(trib, ()) if nome and nome in chave), None)

    def relator(self, trib: str, nome_orgao: str, texto: str) -> tuple[str | None, str | None, str | None, bool]:
        """(relator_id, fora, por, achou_nome_desconhecido): pelo gabinete do ``nomeOrgao``, senão
        pelo texto. Nome que não casa com a composição nem com quem saiu: nenhum (e não é guardado)."""
        comp = self.composicao(trib)
        if comp is None:
            return None, None, None, False
        dono = comp.do_gabinete(nome_orgao)
        if dono is not None:
            return dono.id, None, "orgao", False
        chave_orgao = _cargo(nome_orgao, trib)
        if chave_orgao != nome_orgao:  # "PRESIDÊNCIA"? só se o órgão for o próprio cargo
            r = comp.relator(chave_orgao)
            if r.ministro is None and r.fora not in (None, "residuos"):
                return None, r.fora, "orgao", False
        nome = nome_do_relator(texto)
        if not nome:
            return None, None, None, False
        r = comp.relator(_cargo(nome, trib))
        if r.ministro is not None:
            return r.ministro.id, None, "texto", False
        if r.fora is not None and (r.fora != "residuos" or r.conhecido):
            return None, r.fora, "texto", False
        return None, None, None, True

    def parte(self, destinatario: Mapping[str, Any]) -> list[Any] | None:
        """``[polo, tipo, grupo]`` de um destinatário; o nome nunca sai daqui. Grupo só do
        catálogo (ente, PJ conhecida, família de Estado/MP/Município); PF e MEI: ``[polo, "pf", None]``."""
        nome = str(destinatario.get("nome") or "").strip()
        polo = _POLOS.get(str(destinatario.get("polo") or "").strip().upper(), "outro")
        if not nome:
            return [polo, "desconhecido", None]
        if _CPF.search(nome):
            return [polo, "pf", None]
        gid = self.catalogo.reconhecer_nome(nome)
        lit = self.catalogo.litigante(gid) if gid else None
        if lit is not None and lit.catalogo:
            return [polo, lit.tipo, lit.id]
        tipo = self.catalogo.tipo_pelo_nome(nome)
        if tipo == "autoridade":
            return [None, "autoridade", None]
        if tipo not in ("pj", "ente"):
            tipo = "pf" if tipo == "pf" else "desconhecido"
        return [polo, tipo, None]

    def ler(self, item: Mapping[str, Any], trib: str | None = None) -> dict[str, Any] | None:
        """O registro enxuto (chaves curtas: vai para o cache, um por publicação). ``None`` para
        item sem tribunal conhecido ou sem data."""
        sigla = str(item.get("siglaTribunal") or "").strip().upper()
        t = trib or sigla.lower()
        if t not in SIGLAS or sigla != SIGLAS[t]:
            return None
        dia = _data_iso(item)
        if not dia:
            return None
        texto = texto_simples(str(item.get("texto") or ""))
        nome_orgao = " ".join(str(item.get("nomeOrgao") or "").split())
        relator_id, fora, por, desconhecido = self.relator(t, nome_orgao, texto)
        classe = _inteiro(item.get("codigoClasse"))
        cancelada = bool(item.get("data_cancelamento") or item.get("motivo_cancelamento")) or item.get("ativo") is False
        partes = [p for p in (self.parte(d) for d in (item.get("destinatarios") or ()) if isinstance(d, Mapping)) if p]
        return {
            "id": _inteiro(item.get("id")),
            "dia": dia,
            "cnj": _cnj(item),
            "classe": classe,
            "sigla": self.sigla_da_classe(t, classe),
            "tipo": tipo_do_item(item, texto, self.tipos),
            "td": " ".join(str(item.get("tipoDocumento") or "").split())[:60] or None,
            "tc": " ".join(str(item.get("tipoComunicacao") or "").split())[:40] or None,
            "orgao": nome_orgao[:120] or None,
            "orgao_cod": self.orgao_colegiado(t, nome_orgao),
            "relator_id": relator_id,
            "fora": fora,
            "relator_por": por,
            "relator_desconhecido": desconhecido,
            "segredo": em_segredo(texto, item),
            "partes": partes,
            "adv": len([a for a in (item.get("destinatarioadvogados") or ()) if isinstance(a, Mapping)]),
            "cancelada": cancelada,
        }


# ── o cache: um arquivo por tribunal e dia ───────────────────────────────────────────────────


@dataclass
class Dia:
    """As publicações de um tribunal num dia, como ficam no cache (``<cache>/djen/<trib>/<dia>.json.gz``)."""

    trib: str
    dia: str
    itens: list[dict[str, Any]] = field(default_factory=list)
    count: int | None = None  # o total que a API declarou
    paginas: int = 0
    completo: bool = False
    coletado_em: str | None = None
    aviso: str | None = None

    def para_json(self) -> dict[str, Any]:
        return {"v": VERSAO_DJEN, "trib": self.trib, "dia": self.dia, "count": self.count, "paginas": self.paginas,
                "completo": self.completo, "coletado_em": self.coletado_em, "aviso": self.aviso, "itens": self.itens}

    @property
    def fechado(self) -> bool:
        """Completo e coletado depois que o dia acabou (no fuso de Brasília): não volta a ser pedido."""
        local = _dia_local(self.coletado_em)
        return self.completo and local is not None and local > self.dia


def _arquivo_do_dia(cache: Path, trib: str, dia: str) -> Path:
    return Path(cache) / PASTA_CACHE / trib / f"{dia}.json.gz"


def gravar_dia(cache: Path, d: Dia) -> Path:
    destino = _arquivo_do_dia(cache, d.trib, d.dia)
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=destino.parent, prefix=f".{d.dia}.", suffix=".tmp")
    os.close(fd)
    try:
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as saida:
            json.dump(d.para_json(), saida, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, destino)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return destino


def ler_dia(cache: Path, trib: str, dia: str) -> Dia | None:
    caminho = _arquivo_do_dia(cache, trib, dia)
    try:
        with gzip.open(caminho, "rt", encoding="utf-8") as entrada:
            bruto = json.load(entrada)
    except (OSError, ValueError, EOFError):
        return None
    if not isinstance(bruto, Mapping) or bruto.get("v") != VERSAO_DJEN:
        return None
    return Dia(trib=trib, dia=dia, itens=list(bruto.get("itens") or []), count=bruto.get("count"), paginas=int(bruto.get("paginas") or 0),
               completo=bool(bruto.get("completo")), coletado_em=bruto.get("coletado_em"), aviso=bruto.get("aviso"))


def dias_no_cache(cache: Path, trib: str, de: date, ate: date) -> list[Dia]:
    pasta = Path(cache) / PASTA_CACHE / trib
    saida = []
    for caminho in sorted(pasta.glob("????-??-??.json.gz")) if pasta.is_dir() else ():
        dia = caminho.name[:10]
        if de.isoformat() <= dia <= ate.isoformat():
            d = ler_dia(cache, trib, dia)
            if d is not None:
                saida.append(d)
    return saida


def limpar_cache(cache: Path, hoje: date, guardar_dias: int) -> int:
    """Apaga os dias mais velhos que ``guardar_dias`` (o cache é da máquina do dono, não do git)."""
    limite = (hoje - timedelta(days=guardar_dias)).isoformat()
    apagados = 0
    for caminho in (Path(cache) / PASTA_CACHE).glob("*/????-??-??.json.gz"):
        if caminho.name[:10] < limite:
            caminho.unlink(missing_ok=True)
            apagados += 1
    return apagados


def planejar(cache: Path, trib: str, hoje: date, cfg: Mapping[str, Any], *, completo: bool = False) -> list[str]:
    """Os dias úteis da janela que faltam (ou ficaram pela metade), do mais novo ao mais velho, até
    ``dias_por_execucao`` (sem teto com ``completo``)."""
    de, ate = janela(hoje, cfg)
    quantos = sum(1 for i in range((ate - de).days + 1) if (de + timedelta(days=i)).weekday() < 5)
    faltam = []
    for d in dias_uteis(ate, quantos):
        existente = ler_dia(cache, trib, d.isoformat())
        if existente is None or not existente.fechado:
            faltam.append(d.isoformat())
    teto = None if completo else int(cfg.get("dias_por_execucao", 5))
    return faltam if teto is None else faltam[:teto]


# ── a coleta ─────────────────────────────────────────────────────────────────────────────────


def coletar_dia(coletor: ColetorTribunais, leitor: Leitor, trib: str, dia: str, cfg: Mapping[str, Any], *,
                agora: Callable[[], datetime], prazo: float | None = None, relogio: Callable[[], float] = time.time) -> Dia:
    """Todas as páginas de um tribunal num dia. ``FiltroIgnorado`` se a API devolver outro
    tribunal ou outro dia (a coleta daquele tribunal para); ``RecusaDoHost``/``CaminhoVedado`` sobem
    (quem chama para tudo); outra falha de rede sobe também (o dia fica para a próxima execução)."""
    sigla = SIGLAS[trib]
    por_pagina = int(cfg["itens_por_pagina"])
    d = Dia(trib=trib, dia=dia)
    vistos: set[Any] = set()
    pagina = 1
    while True:
        url = url_da_consulta(sigla, dia, pagina, por_pagina, str(cfg.get("api") or API))
        lista, total = envelope(coletor.json(url), url)
        d.paginas = pagina
        if d.count is None:
            d.count = total
        for bruto in lista:
            if str(bruto.get("siglaTribunal") or "").strip().upper() != sigla or _data_iso(bruto) != dia:
                raise FiltroIgnorado(url, f"a API devolveu publicação de {bruto.get('siglaTribunal')!r} em {_data_iso(bruto)!r} "
                                          f"num pedido de {sigla} em {dia}: o filtro não vale e a coleta de {sigla} parou")
            chave = bruto.get("id") if bruto.get("id") is not None else (bruto.get("hash") or json.dumps(bruto, sort_keys=True)[:200])
            if chave in vistos:
                continue
            vistos.add(chave)
            registro = leitor.ler(bruto, trib)
            if registro is not None:
                d.itens.append(registro)
        acabou = len(lista) < por_pagina or (d.count is not None and pagina * por_pagina >= d.count)
        if acabou:
            d.completo = d.count is None or len(vistos) >= d.count
            if not d.completo:
                d.aviso = f"a API declarou {d.count} publicações e entregou {len(vistos)}"
            break
        if pagina >= int(cfg["max_paginas_dia"]):
            d.aviso = f"parou no teto de {pagina} páginas ({len(vistos)} de {d.count} publicações)"
            break
        if prazo is not None and relogio() > prazo:
            d.aviso = f"parou no prazo da execução ({len(vistos)} de {d.count} publicações)"
            break
        pagina += 1
    d.coletado_em = _instante(agora())
    return d


# ── o acervo do dia (o retrato de dados.py) ──────────────────────────────────────────────────


@dataclass
class IndiceAcervo:
    """Os processos do acervo que tiveram publicação (pelo número único) e o tamanho da mesa de
    cada relator (todos os processos no gabinete do relator)."""

    trib: str
    data: str | None
    arquivo: str | None
    processos: dict[str, dict[str, Any]] = field(default_factory=dict)
    na_mesa: Counter[str] = field(default_factory=Counter)
    linhas: int = 0


def localizar_retrato(pastas: Iterable[Path], trib: str, *, validade_h: float | None, agora: float) -> Path | None:
    """O retrato mais novo (pela data no nome) em ``<pasta>/retratos/`` ou na própria pasta;
    com ``validade_h``, só se o arquivo foi gravado há menos que isso."""
    candidatos: list[Path] = []
    for pasta in pastas:
        for base in (Path(pasta) / "retratos", Path(pasta)):
            if base.is_dir():
                candidatos += [c for c in base.glob(f"{trib}-????-??-??.tsv.gz") if c.is_file()]
    candidatos.sort(key=lambda c: (c.name, c.stat().st_mtime), reverse=True)
    for c in candidatos:
        if validade_h is None or agora - c.stat().st_mtime <= validade_h * 3600:
            return c
    return None


def ler_indice(caminho: Path, trib: str, cnjs: set[str]) -> IndiceAcervo:
    """Lê o retrato em fluxo: guarda só os processos com publicação e conta a mesa de todos."""
    indice = IndiceAcervo(trib=trib, data=caminho.name[len(trib) + 1 : len(trib) + 11], arquivo=caminho.name)
    with gzip.open(caminho, "rt", encoding="utf-8") as entrada:
        cabeca = entrada.readline().rstrip("\n").split("\t")
        pos = {nome: i for i, nome in enumerate(cabeca)}
        for linha in entrada:
            campos = linha.rstrip("\n").split("\t")
            if len(campos) < len(cabeca):
                continue
            indice.linhas += 1
            relator = campos[pos["relator"]]
            onde = campos[pos["onde"]]
            if onde == "gabinete" and relator:
                indice.na_mesa[relator] += 1
            cnj = campos[pos["cnj"]]
            if cnj in cnjs:
                dias = campos[pos["sem_decisao_dias" if trib == "stj" else "concluso_dias"]]
                indice.processos[cnj] = {
                    "id": campos[pos["id"]], "registro": campos[pos["registro"]] or None, "relator": relator or None, "onde": onde or None,
                    "classe": campos[pos["classe"]] or None, "area": campos[pos["area"]] or None, "segredo": campos[pos["segredo"]] == "1",
                    "dias": int(dias) if dias.lstrip("-").isdigit() else None,
                }
    return indice


def construir_retrato(coletor: ColetorTribunais, regras: Regras, classificador: Classificador, tpu: TPU, cache: Path, trib: str) -> Path | None:
    """Refaz o retrato do acervo do dia na máquina do dono (sem o cache do fluxo dos Tribunais):
    o mesmo download e a mesma leitura de ``dados.py``, pelo mesmo coletor (robots.txt, ritmo)."""
    from . import stf, stj  # import tardio: só quando não há retrato
    from .dados import Retrato

    if trib == "stj":
        baixado = stj.baixar_acervo(coletor, regras)
        processos: Iterable[Any] = stj.ler_acervo(baixado.caminho, regras, classificador, data_dados=baixado.data)
        retrato = Retrato(cache, "stj", baixado.data)
    else:
        acervo = stf.coletar_acervo(coletor, regras, tpu=tpu, classificador=classificador)
        processos, retrato = acervo.processos, Retrato(cache, "stf", acervo.data_dados)
    ok = False
    try:
        for _ in retrato.passar(processos):
            pass
        ok = True
    finally:
        caminho = retrato.fechar(ok)
    return caminho


# ── o agregado (djen.json) ───────────────────────────────────────────────────────────────────


class _Bloco:
    """Um período (o último dia ou a janela) de um tribunal, de um ministro ou de uma linha fora da grade."""

    def __init__(self) -> None:
        self.itens = 0
        self.processos: set[str] = set()
        self.tipos: Counter[str] = Counter()
        self.areas: dict[str, set[str]] = defaultdict(set)
        self.mesa: set[str] = set()
        self.ativo: dict[str, set[str]] = defaultdict(set)
        self.passivo: dict[str, set[str]] = defaultdict(set)
        self.com_pf: set[str] = set()
        self.com_pj: set[str] = set()
        self.com_ente: set[str] = set()

    def somar(self, r: Mapping[str, Any], area: str, na_mesa: bool, segredo: bool) -> None:
        chave = r.get("cnj") or f"#{r.get('id')}"
        self.itens += 1
        self.processos.add(chave)
        self.tipos[r.get("tipo") or OUTROS] += 1
        self.areas[area].add(chave)
        if na_mesa:
            self.mesa.add(chave)
        if segredo:
            return
        for polo, tipo, grupo in r.get("partes") or ():
            if tipo == "pf":
                self.com_pf.add(chave)
            elif tipo == "pj":
                self.com_pj.add(chave)
            elif tipo == "ente":
                self.com_ente.add(chave)
            if grupo and polo in ("ativo", "passivo"):
                (self.ativo if polo == "ativo" else self.passivo)[grupo].add(chave)

    def para_json(self, ordem_tipos: Sequence[str], ordem_areas: Sequence[str], *, partes: int = 0, mesa: bool = True) -> dict[str, Any]:
        saida: dict[str, Any] = {
            "itens": self.itens,
            "processos": len(self.processos),
            "tipos": {t: self.tipos[t] for t in ordem_tipos if self.tipos.get(t)},
            "areas": {a: len(self.areas[a]) for a in ordem_areas if self.areas.get(a)},
        }
        if mesa:
            saida["mesa"] = len(self.mesa)
        if partes:
            def top(tabela: Mapping[str, set[str]]) -> list[list[Any]]:
                return [[g, len(s)] for g, s in sorted(tabela.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:partes]]
            saida["partes"] = {"ativo": top(self.ativo), "passivo": top(self.passivo),
                               "com_pf": len(self.com_pf), "com_pj": len(self.com_pj), "com_ente": len(self.com_ente)}
        return saida

    def grupos(self, n: int) -> list[str]:
        alvo = list(self.ativo.items()) + list(self.passivo.items())
        return [g for g, _ in sorted(alvo, key=lambda kv: (-len(kv[1]), kv[0]))[:n]]


def _area(r: Mapping[str, Any], proc: Mapping[str, Any] | None, classificador: Classificador) -> tuple[str, str]:
    """(área, por): a do acervo; senão a da classe, se a classe decide; senão ``sem_area``."""
    if proc and proc.get("area"):
        return str(proc["area"]), "acervo"
    if r.get("sigla"):
        c = classificador.classificar(classe=r["sigla"], principal=None, assuntos=())
        if c["por"] == "classe":
            return c["area"], "classe"
    return SEM_AREA, "nenhuma"


def agregar_tribunal(trib: str, dias: Sequence[Dia], indice: IndiceAcervo | None, regras: Regras, classificador: Classificador,
                     cfg: Mapping[str, Any], de: date, ate: date, nomes_grupos: dict[str, str], catalogo: Catalogo) -> dict[str, Any]:
    """O bloco de um tribunal em ``djen.json`` (ver o contrato em :func:`agregar`)."""
    listas = cfg.get("listas") or PADRAO["listas"]
    ordem_tipos = [str(t["id"]) for t in cfg.get("tipos") or PADRAO["tipos"]] + [OUTROS]
    ordem_areas = [*regras.ids_areas, SEM_AREA]
    comp = regras.composicao.get(trib)
    por_dia = {d.dia: d for d in dias}
    com_itens = [d.dia for d in dias if any(not r.get("cancelada") for r in d.itens)]
    ultimo = max(com_itens) if com_itens else None

    total, dia_b = _Bloco(), _Bloco()
    ministros: dict[str, dict[str, _Bloco]] = defaultdict(lambda: {"dia": _Bloco(), "mes": _Bloco()})
    fora: dict[str, dict[str, _Bloco]] = defaultdict(lambda: {"dia": _Bloco(), "mes": _Bloco()})
    orgaos_cod: dict[str, dict[str, _Bloco]] = defaultdict(lambda: {"dia": _Bloco(), "mes": _Bloco()})
    mesa_lista: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    relator_por: Counter[str] = Counter()
    area_por: Counter[str] = Counter()
    tipos_brutos: Counter[str] = Counter()
    orgaos_brutos: Counter[str] = Counter()
    canceladas = segredos = casados = desconhecidos = 0
    processos_casados: set[str] = set()

    for d in sorted(dias, key=lambda x: x.dia):
        for r in d.itens:
            if r.get("cancelada"):
                canceladas += 1
                continue
            proc = indice.processos.get(r.get("cnj") or "") if indice else None
            if proc is not None:
                casados += 1
                processos_casados.add(r["cnj"])
            relator_id, linha_fora, por = r.get("relator_id"), r.get("fora"), r.get("relator_por")
            if relator_id is None and linha_fora is None and proc and proc.get("relator"):
                rel = str(proc["relator"])
                relator_id, linha_fora = (rel, None) if rel.startswith(f"{trib}-") else (None, rel)
                por = "acervo"
            relator_por[por or "nenhum"] += 1
            desconhecidos += int(bool(r.get("relator_desconhecido")))
            area, apor = _area(r, proc, classificador)
            area_por[apor] += 1
            segredo = bool(r.get("segredo")) or bool(proc and proc.get("segredo"))
            segredos += int(segredo)
            # "na mesa": o processo estava no gabinete do próprio relator no retrato do acervo
            na_mesa = bool(proc and proc.get("onde") == "gabinete" and proc.get("relator") and proc.get("relator") == relator_id)
            tipos_brutos[r.get("td") or "(vazio)"] += 1
            if r.get("orgao"):
                orgaos_brutos[r["orgao"]] += 1
            alvos = [total]
            if relator_id:
                alvos.append(ministros[relator_id]["mes"])
            elif linha_fora:
                alvos.append(fora[linha_fora]["mes"])
            if r.get("orgao_cod"):
                alvos.append(orgaos_cod[r["orgao_cod"]]["mes"])
            if d.dia == ultimo:
                alvos.append(dia_b)
                if relator_id:
                    alvos.append(ministros[relator_id]["dia"])
                elif linha_fora:
                    alvos.append(fora[linha_fora]["dia"])
                if r.get("orgao_cod"):
                    alvos.append(orgaos_cod[r["orgao_cod"]]["dia"])
            for b in alvos:
                b.somar(r, area, na_mesa, segredo)
            if na_mesa and not segredo and relator_id and proc:
                anterior = mesa_lista[relator_id].get(r["cnj"])
                if anterior is None or (d.dia, r.get("tipo") or "") >= (anterior["dia"], anterior["tipo"] or ""):
                    classe, numero = (str(proc.get("id") or "").split("-", 2) + ["", ""])[1:3]
                    mesa_lista[relator_id][r["cnj"]] = {
                        "id": proc.get("id"), "classe": classe or proc.get("classe"), "numero": numero or None, "cnj": r["cnj"],
                        "registro": proc.get("registro"), "area": area, "tipo": r.get("tipo"), "dia": d.dia,
                        "dias_sem_decisao": proc.get("dias"),
                    }

    com_mesa = indice is not None

    def bloco_de(par: Mapping[str, _Bloco]) -> dict[str, Any]:
        return {"dia": par["dia"].para_json(ordem_tipos, ordem_areas, mesa=com_mesa),
                "mes": par["mes"].para_json(ordem_tipos, ordem_areas, mesa=com_mesa)}

    n_lista = int(listas.get("mesa_por_ministro", 5))
    saida_min: dict[str, Any] = {}
    ordem_min = [m.id for m in comp.ministros] if comp else []
    for mid in sorted(ministros, key=lambda x: (ordem_min.index(x) if x in ordem_min else 999, x)):
        par = ministros[mid]
        bloco = bloco_de(par)
        mesa_total = indice.na_mesa.get(mid) if indice else None
        lista = sorted(mesa_lista.get(mid, {}).values(),
                       key=lambda x: (-(x["dias_sem_decisao"] if x["dias_sem_decisao"] is not None else -1), x["dia"], x["cnj"]))
        bloco["mesa"] = None if indice is None else {
            "na_mesa": mesa_total or 0,
            "publicados": len(par["mes"].mesa),
            "parte": round(len(par["mes"].mesa) / mesa_total, 4) if mesa_total else None,
            "lista": lista[:n_lista],
        }
        grupos = par["mes"].grupos(int(listas.get("partes_ministro", 3)))
        bloco["partes"] = grupos
        for g in grupos:
            nomes_grupos.setdefault(g, _nome_do_grupo(catalogo, g))
        saida_min[mid] = bloco

    n_partes = int(listas.get("partes", 10))
    mes = total.para_json(ordem_tipos, ordem_areas, partes=n_partes, mesa=com_mesa)
    dia_json = dia_b.para_json(ordem_tipos, ordem_areas, partes=n_partes, mesa=com_mesa)
    for bloco in (mes, dia_json):
        for polo in ("ativo", "passivo"):
            for g, _n in bloco["partes"][polo]:
                nomes_grupos.setdefault(g, _nome_do_grupo(catalogo, g))

    serie = []
    for i in range((ate - de).days + 1):
        dd = de + timedelta(days=i)
        if dd.weekday() >= 5:
            continue
        x = por_dia.get(dd.isoformat())
        serie.append([dd.isoformat(), None if x is None else len([r for r in x.itens if not r.get("cancelada")]),
                      bool(x and x.completo)])
    fechados_vazios = [d for d in dias if d.fechado and not d.itens and d.count in (0, None)]
    publica: bool | None = True if com_itens else (False if len(fechados_vazios) >= int(cfg.get("sem_publicacao_dias", 3)) else None)
    itens_validos = sum(len([r for r in d.itens if not r.get("cancelada")]) for d in dias)
    avisos = [f"{_br(d.dia)}: {d.aviso}" for d in dias if d.aviso]
    return {
        "sigla": SIGLAS[trib],
        "publica": publica,
        "ultimo_dia": ultimo,
        "coletado_em": max((d.coletado_em for d in dias if d.coletado_em), default=None),
        "dias": serie,
        "dia": dia_json,
        "mes": mes,
        "ministros": saida_min,
        "fora": {fid: bloco_de(par) for fid, par in sorted(fora.items())},
        "orgaos": {cod: bloco_de(par) for cod, par in sorted(orgaos_cod.items())},
        "acervo": None if indice is None else {"data": indice.data, "processos": indice.linhas,
                                               "casados": casados, "processos_casados": len(processos_casados)},
        "cobertura": {
            "itens": itens_validos,
            "canceladas": canceladas,
            "segredo": segredos,
            "relator_por": dict(sorted(relator_por.items())),
            "relator_desconhecido": desconhecidos,
            "area_por": dict(sorted(area_por.items())),
            "tipos_brutos": [[k, v] for k, v in sorted(tipos_brutos.items(), key=lambda kv: (-kv[1], kv[0]))[: int(listas.get("tipos_brutos", 30))]],
            "orgaos_brutos": [[k, v] for k, v in sorted(orgaos_brutos.items(), key=lambda kv: (-kv[1], kv[0]))[: int(listas.get("orgaos", 40))]],
        },
        "avisos": avisos,
    }


def _nome_do_grupo(catalogo: Catalogo, gid: str) -> str:
    lit = catalogo.litigante(gid)
    return lit.nome if lit is not None else gid


def _br(iso: str | None) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else "—"


def agregar(dias_por_trib: Mapping[str, Sequence[Dia]], indices: Mapping[str, IndiceAcervo | None], regras: Regras,
            classificador: Classificador, catalogo: Catalogo, cfg: Mapping[str, Any], hoje: date,
            avisos: Sequence[str] = ()) -> dict[str, Any]:
    """``data/tribunais/djen.json`` (contrato completo em ``scratchpad/rodada3/djen-contrato.md``)::

        {v, gerado_em, hoje, origem, fonte{nome, url, api, atribuicao}, janela{de, ate, dias_uteis},
         reguas{tipos[{id, nome}], relator, mesa, area, partes, segredo},
         tribunais{stj|stf: {sigla, publica, ultimo_dia, coletado_em, dias[[dia, itens|null, completo]],
                             dia{itens, processos, tipos{}, areas{}, mesa?, partes{ativo[[g,n]], passivo, com_pf, com_pj, com_ente}},
                             mes{idem}, ministros{id: {dia, mes, mesa{na_mesa, publicados, parte, lista[...]}|null, partes[g]}},
                             fora{linha: {dia, mes}}, orgaos{T3: {dia, mes}}, acervo{data, processos, casados, processos_casados}|null,
                             cobertura{...}, avisos[]}},
         grupos{gid: nome}, avisos[]}

    ``gerado_em`` é o instante da coleta mais nova usada (mesmos dados, mesmos bytes)."""
    de, ate = janela(hoje, cfg)
    nomes_grupos: dict[str, str] = {}
    tribunais: dict[str, Any] = {}
    for trib in cfg.get("tribunais") or ():
        dias = dias_por_trib.get(trib) or []
        if not dias:
            continue
        tribunais[trib] = agregar_tribunal(trib, dias, indices.get(trib), regras, classificador, cfg, de, ate, nomes_grupos, catalogo)
    coletas = [t["coletado_em"] for t in tribunais.values() if t.get("coletado_em")]
    return {
        "v": VERSAO_DJEN,
        "gerado_em": max(coletas) if coletas else None,
        "hoje": hoje.isoformat(),
        "origem": cfg.get("origem"),
        "fonte": {"nome": "CNJ · Diário de Justiça Eletrônico Nacional (DJEN)", "url": cfg.get("consulta") or CONSULTA,
                  "api": cfg.get("api") or API, "atribuicao": cfg.get("atribuicao")},
        "janela": {"de": de.isoformat(), "ate": ate.isoformat(),
                   "dias_uteis": sum(1 for i in range((ate - de).days + 1) if (de + timedelta(days=i)).weekday() < 5)},
        "reguas": {
            "tipos": [{"id": str(t["id"]), "nome": str(t.get("nome") or t["id"])} for t in cfg.get("tipos") or PADRAO["tipos"]]
            + [{"id": OUTROS, "nome": "Outros"}],
            "relator": "O gabinete no nome do órgão; senão a linha \"Relator:\" do texto; senão o relator do processo no acervo "
                       "do dia (pelo número único). O que o Presidente assina como Presidente conta na Presidência.",
            "mesa": "Processo que estava no gabinete do próprio relator no retrato do acervo e teve publicação na janela.",
            "area": "A área do processo no acervo do dia; senão a da classe, quando a classe decide (HC, ADI...); senão sem área.",
            "partes": "Só grupos de litigante do catálogo (entes públicos e grandes litigantes); pessoa física e MEI só na "
                      "contagem, sem nome; advogados nunca.",
            "segredo": "Processo em segredo de justiça conta nos números e nunca é listado nem entra nas partes.",
        },
        "tribunais": tribunais,
        "grupos": dict(sorted(nomes_grupos.items())),
        "avisos": list(avisos),
    }


def serializar(dados: dict[str, Any]) -> str:
    """O JSON compacto; acima de :data:`ALVO_JSON`, as listas encolhem (e acima de
    :data:`TETO_JSON` saem); nunca passa do teto."""
    texto = _json(dados)
    if len(texto.encode("utf-8")) <= ALVO_JSON:
        return texto
    for t in dados.get("tribunais", {}).values():
        t["cobertura"]["orgaos_brutos"] = t["cobertura"]["orgaos_brutos"][:15]
        t["cobertura"]["tipos_brutos"] = t["cobertura"]["tipos_brutos"][:15]
        for m in t.get("ministros", {}).values():
            if m.get("mesa"):
                m["mesa"]["lista"] = m["mesa"]["lista"][:3]
    texto = _json(dados)
    if len(texto.encode("utf-8")) <= TETO_JSON:
        return texto
    for t in dados.get("tribunais", {}).values():
        t["orgaos"] = {}
        for m in t.get("ministros", {}).values():
            if m.get("mesa"):
                m["mesa"]["lista"] = []
    return _json(dados)


# ── a execução ───────────────────────────────────────────────────────────────────────────────


@dataclass
class Execucao:
    """O resumo de uma execução (log da tarefa agendada e testes)."""

    ativo: bool = True
    gravado: bool = False
    caminho: Path | None = None
    avisos: list[str] = field(default_factory=list)
    coletados: dict[str, list[str]] = field(default_factory=dict)  # trib → dias coletados nesta execução
    itens: dict[str, int] = field(default_factory=dict)  # trib → publicações na janela
    recusa: str | None = None
    requisicoes: int = 0
    segundos: float = 0.0


def _contexto(raiz: Path, pasta_dados: Path) -> tuple[Regras, TPU, Classificador, Catalogo]:
    regras = carregar_regras(raiz)
    pasta_tpu = pasta_dados / "tpu"
    tpu = TPU.carregar(pasta_tpu) if (pasta_tpu / "versao.json").is_file() else TPU.carregar()
    return regras, tpu, Classificador(regras, tpu), carregar_catalogo(raiz)


def executar(
    raiz: Path,
    saida: Path,
    cache: Path,
    *,
    agora: datetime | None = None,
    completo: bool = False,
    so: str | None = None,
    acervo: Sequence[Path] = (),
    sem_acervo: bool = False,
    fetch: net.Fetcher | None = None,
    relogio: Callable[[], float] = time.time,
    dormir: Callable[[float], None] = time.sleep,
    cfg: Mapping[str, Any] | None = None,
) -> Execucao:
    """A coleta: os dias que faltam de cada tribunal, o retrato do acervo e ``djen.json``.

    ``saida``: a pasta publicada (``djen.json`` vai para ``<saida>/data/tribunais/``). ``acervo``:
    pastas extras onde procurar o retrato (além de ``<cache>/retratos``). Fonte que falha vira aviso;
    a recusa do host (403) ou o robots.txt param tudo e nada é contornado."""
    inicio = relogio()
    cfg = dict(cfg) if cfg is not None else carregar_config(raiz)
    resultado = Execucao(ativo=bool(cfg.get("ativo")))
    if not resultado.ativo:
        return resultado
    agora = agora or datetime.now(UTC)
    hoje = agora.astimezone(ZoneInfo(FUSO)).date()
    pasta_dados = Path(saida) / "data" / PASTA
    regras, tpu, classificador, catalogo = _contexto(raiz, pasta_dados)
    coletor = ColetorTribunais(Path(cache), regras=regras_do_coletor(regras, cfg), fetch=fetch or net.fetch, relogio=relogio, dormir=dormir)
    leitor = Leitor(regras, tpu, classificador, catalogo, cfg)
    prazo = inicio + float(cfg.get("prazo_s") or PADRAO["prazo_s"])
    tribunais = [t for t in cfg["tribunais"] if so in (None, t)]

    def relogio_utc() -> datetime:
        return datetime.fromtimestamp(relogio(), UTC)

    parar = False
    for trib in tribunais:
        if parar:
            break
        resultado.coletados[trib] = []
        for dia in planejar(Path(cache), trib, hoje, cfg, completo=completo):
            if relogio() > prazo:
                resultado.avisos.append(f"DJEN: o prazo da execução acabou; {SIGLAS[trib]} continua na próxima")
                parar = True
                break
            try:
                d = coletar_dia(coletor, leitor, trib, dia, cfg, agora=relogio_utc, prazo=prazo, relogio=relogio)
            except (RecusaDoHost, CaminhoVedado) as exc:
                resultado.recusa = str(exc)
                resultado.avisos.append(f"DJEN: {exc}. Nada foi contornado; a coleta parou (a aba segue sem a camada nova).")
                parar = True
                break
            except FiltroIgnorado as exc:
                resultado.avisos.append(f"DJEN: {exc}")
                break
            except FetchError as exc:
                resultado.avisos.append(f"DJEN: {SIGLAS[trib]} em {_br(dia)} falhou ({exc}); fica para a próxima execução")
                break
            gravar_dia(Path(cache), d)
            resultado.coletados[trib].append(dia)
            log.info("DJEN: %s em %s: %d publicações em %d página(s)%s", SIGLAS[trib], dia, len(d.itens), d.paginas,
                     f" ({d.aviso})" if d.aviso else "")
    resultado.requisicoes = coletor.requisicoes

    de, ate = janela(hoje, cfg)
    destino = pasta_dados / ARQUIVO
    if not any(resultado.coletados.values()) and destino.is_file():
        # nada novo nesta execução: o djen.json fica como está (e o acervo não é baixado à toa)
        resultado.segundos = round(relogio() - inicio, 1)
        return resultado
    dias_por_trib = {t: dias_no_cache(Path(cache), t, de, ate) for t in cfg["tribunais"]}
    for t, dias in dias_por_trib.items():
        resultado.itens[t] = sum(len(d.itens) for d in dias)
    if not any(dias_por_trib.values()):
        resultado.avisos.append("DJEN: nenhum dia no cache; djen.json não foi gravado")
        resultado.segundos = round(relogio() - inicio, 1)
        return resultado

    indices: dict[str, IndiceAcervo | None] = {}
    for trib, dias in dias_por_trib.items():
        cnjs = {r["cnj"] for d in dias for r in d.itens if r.get("cnj")}
        if not cnjs or sem_acervo:
            indices[trib] = None
            continue
        validade = float((cfg.get("acervo") or {}).get("retrato_validade_h") or 30)
        caminho = localizar_retrato([*acervo, Path(cache)], trib, validade_h=validade, agora=time.time())
        if caminho is None and (cfg.get("acervo") or {}).get("baixar") and resultado.recusa is None:
            try:
                caminho = construir_retrato(coletor, regras, classificador, tpu, Path(cache), trib)
            except Exception as exc:  # noqa: BLE001 — fonte acessória: vira aviso, a camada sai sem a mesa
                resultado.avisos.append(f"DJEN: o acervo do {SIGLAS[trib]} não pôde ser lido ({exc}); sai sem a mesa")
        indices[trib] = ler_indice(caminho, trib, cnjs) if caminho is not None else None
    resultado.requisicoes = coletor.requisicoes

    dados = agregar(dias_por_trib, indices, regras, classificador, catalogo, cfg, hoje, avisos=resultado.avisos)
    texto = serializar(dados)
    if len(texto.encode("utf-8")) > TETO_JSON:
        resultado.avisos.append(f"DJEN: djen.json passou de {TETO_JSON // 1000} KB; não foi gravado")
    else:
        resultado.gravado = _gravar(destino, texto)
        resultado.caminho = destino
    limpar_cache(Path(cache), hoje, int(cfg.get("guardar_dias") or 40))
    # O acervo baixado para o retrato (82 MB por dia no STJ) não se acumula na máquina do dono: o que
    # esta execução não usou e tem mais de um dia e meio sai (o retrato compacto fica em retratos/).
    # Cache dividido com "tribunais atualizar" (tem estado/): não mexe, lá as séries valem meses.
    if not (Path(cache) / "estado").is_dir():
        coletor.limpar(dias=1.5)
    resultado.segundos = round(relogio() - inicio, 1)
    return resultado


# ── a sonda ──────────────────────────────────────────────────────────────────────────────────


def mascarar(item: Mapping[str, Any], leitor: Leitor, n: int) -> dict[str, Any]:
    """Uma publicação pronta para virar amostra de teste: nome de destinatário só se for grupo do
    catálogo (ente, grande litigante); advogados e OAB trocados; número do processo trocado (o
    segmento do tribunal fica); texto reduzido às linhas de título e de relator."""
    saida = dict(item)
    destinatarios = []
    for i, d in enumerate(item.get("destinatarios") or (), 1):
        if not isinstance(d, Mapping):
            continue
        parte = leitor.parte(d)
        nome = str(d.get("nome") or "")
        manter = parte is not None and parte[2] is not None
        destinatarios.append({**d, "nome": nome if manter else f"PARTE {i} (mascarada)"})
    saida["destinatarios"] = destinatarios
    advogados = []
    for i, a in enumerate(item.get("destinatarioadvogados") or (), 1):
        if not isinstance(a, Mapping):
            continue
        adv = dict(a.get("advogado") or {})
        adv.update({"nome": f"ADVOGADO {i} (mascarado)", "numero_oab": "000000"})
        advogados.append({**a, "advogado": adv})
    saida["destinatarioadvogados"] = advogados
    cnj = _cnj(item)
    if cnj:
        falso = f"{9_000_000 + n:07d}00{cnj[9:]}"  # dígito verificador 00 nunca é válido (vai de 02 a 98)
        saida["numero_processo"] = falso
        saida["numeroprocessocommascara"] = f"{falso[:7]}-{falso[7:9]}.{falso[9:13]}.{falso[13]}.{falso[14:16]}.{falso[16:]}"
    # O texto some: fica um título reconhecido (DECISÃO, DESPACHO...) e a linha do relator, refeita só
    # quando o nome casa com a composição (autoridade no cargo); nada mais do original.
    texto = texto_simples(str(item.get("texto") or ""))
    trib = str(item.get("siglaTribunal") or "").lower()
    guardadas = [linha for linha in texto.splitlines()[:3] if _TITULO.match(normalizar(linha))][:1]
    nome = nome_do_relator(texto)
    if nome and trib in SIGLAS:
        relator_id, linha_fora, _por, _desconhecido = leitor.relator(trib, "", texto)
        if relator_id or linha_fora:
            comp = leitor.composicao(trib)
            ministro = comp.ministro(relator_id) if comp is not None and relator_id else None
            if ministro is not None and re.match(r"^(?:MINISTR[OA]|MIN\.)\s", nome) is None:
                nome = f"{'MINISTRA' if ministro.genero == 'f' else 'MINISTRO'} {nome}"
            guardadas.append(f"RELATOR : {nome}")
    saida["texto"] = "<br>".join(html.escape(linha) for linha in guardadas) + "<br>[resto do texto removido na amostra]"
    for chave in ("hash", "link"):
        if saida.get(chave):
            saida[chave] = f"<{chave} removido>"
    return saida


def sondar(
    raiz: Path,
    cache: Path,
    *,
    agora: datetime | None = None,
    dia: str | None = None,
    fetch: net.Fetcher | None = None,
    relogio: Callable[[], float] = time.time,
    dormir: Callable[[float], None] = time.sleep,
    cfg: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Poucos pedidos para saber o que o DJEN dá: o robots.txt e uma página (100 itens) por
    tribunal, num dia útil recente (se vier vazio, o dia útil anterior, até 3). Devolve o relatório
    e grava a amostra mascarada em ``<cache>/djen/sonda/``."""
    cfg = dict(cfg) if cfg is not None else carregar_config(raiz)
    agora = agora or datetime.now(UTC)
    hoje = agora.astimezone(ZoneInfo(FUSO)).date()
    regras, tpu, classificador, catalogo = _contexto(raiz, Path(raiz) / "data" / PASTA)
    coletor = ColetorTribunais(Path(cache), regras=regras_do_coletor(regras, cfg), fetch=fetch or net.fetch, relogio=relogio, dormir=dormir)
    leitor = Leitor(regras, tpu, classificador, catalogo, cfg)
    sonda_cfg = cfg.get("sonda") or PADRAO["sonda"]
    por_pagina = max(1, min(100, int(sonda_cfg.get("itens", 100))))
    dias = [date.fromisoformat(dia)] if dia else dias_uteis(hoje - timedelta(days=1), int(sonda_cfg.get("tentativas_dias", 3)))
    pasta = Path(cache) / PASTA_CACHE / "sonda"
    relatorio: dict[str, Any] = {"quando": _instante(agora), "api": cfg.get("api") or API, "tribunais": {},
                                 "navegador": {t: url_para_navegador(t, dias[0].isoformat()) for t in SIGLAS}}
    for trib in SIGLAS:
        r: dict[str, Any] = {"alcancavel": None, "publica": None, "dia": None, "count": None, "itens_na_pagina": 0}
        relatorio["tribunais"][trib] = r
        for d in dias:
            url = url_da_consulta(SIGLAS[trib], d.isoformat(), 1, por_pagina, str(cfg.get("api") or API))
            try:
                lista, total = envelope(coletor.json(url), url)
            except (RecusaDoHost, CaminhoVedado) as exc:
                r.update(alcancavel=False, recusa=str(exc))
                relatorio["recusa"] = str(exc)
                break
            except FetchError as exc:
                r.update(alcancavel=False, erro=str(exc))
                break
            r.update(alcancavel=True, dia=d.isoformat(), count=total, itens_na_pagina=len(lista))
            if not lista:
                continue
            lidos = [leitor.ler(x, trib) for x in lista]
            validos = [x for x in lidos if x is not None]
            r["publica"] = True
            r["filtro_respeitado"] = all(str(x.get("siglaTribunal") or "").upper() == SIGLAS[trib] and _data_iso(x) == d.isoformat() for x in lista)
            r["campos"] = sorted({k for x in lista for k in x})
            r["tipos_documento"] = Counter(str(x.get("tipoDocumento") or "(vazio)") for x in lista).most_common()
            r["tipos_comunicacao"] = Counter(str(x.get("tipoComunicacao") or "(vazio)") for x in lista).most_common()
            r["meios"] = Counter(str(x.get("meio") or "(vazio)") for x in lista).most_common()
            r["orgaos"] = Counter(str(x.get("nomeOrgao") or "(vazio)") for x in lista).most_common(25)
            r["tipos_lidos"] = Counter(x["tipo"] for x in validos).most_common()
            r["relator_por"] = Counter(x["relator_por"] or "nenhum" for x in validos).most_common()
            r["com_destinatarios"] = sum(1 for x in lista if x.get("destinatarios"))
            r["com_codigo_classe"] = sum(1 for x in lista if x.get("codigoClasse"))
            amostra = [mascarar(x, leitor, i) for i, x in enumerate(lista[: int(sonda_cfg.get("amostra", 12))], 1)]
            destino = pasta / f"djen_{trib}_{d.isoformat()}_mascarada.json"
            _gravar(destino, json.dumps({"status": "success", "message": "Sucesso", "count": total, "items": amostra},
                                        ensure_ascii=False, indent=1) + "\n")
            r["amostra"] = str(destino)
            break
        else:
            if r["alcancavel"]:
                r["publica"] = False
        if relatorio.get("recusa"):
            relatorio["tribunais"].setdefault("stf" if trib == "stj" else "stj", {"alcancavel": False, "recusa": relatorio["recusa"]})
            break
    relatorio["requisicoes"] = coletor.requisicoes
    relatorio["pedidos"] = [{k: p[k] for k in ("host", "caminho", "status")} for p in coletor.diario]
    _gravar(pasta / "sonda.json", json.dumps(relatorio, ensure_ascii=False, indent=1, default=list) + "\n")
    return relatorio


def relatorio_da_sonda(r: Mapping[str, Any]) -> str:
    """O relatório da sonda em texto, para o terminal."""
    linhas = [f"Sonda do DJEN ({r.get('quando')}), API {r.get('api')}"]
    for trib, t in (r.get("tribunais") or {}).items():
        sigla = SIGLAS.get(trib, trib.upper())
        if t.get("alcancavel") is False:
            linhas.append(f"- {sigla}: NÃO alcançável daqui ({t.get('recusa') or t.get('erro')}).")
            continue
        if t.get("publica") is False:
            linhas.append(f"- {sigla}: alcançável; nenhuma publicação nos dias úteis tentados (o tribunal parece não publicar no DJEN).")
            continue
        linhas.append(f"- {sigla}: alcançável; {t.get('count')} publicações em {t.get('dia')} "
                      f"(filtro respeitado: {'sim' if t.get('filtro_respeitado') else 'NÃO'}).")
        linhas.append(f"  tipos de documento: {', '.join(f'{k} ({v})' for k, v in t.get('tipos_documento') or [])}")
        linhas.append(f"  órgãos: {', '.join(f'{k} ({v})' for k, v in (t.get('orgaos') or [])[:8])}")
        linhas.append(f"  relator achado por: {', '.join(f'{k} ({v})' for k, v in t.get('relator_por') or [])}")
        linhas.append(f"  amostra mascarada: {t.get('amostra')}")
    linhas.append(f"Pedidos feitos: {len(r.get('pedidos') or [])}.")
    linhas.append("Para conferir no navegador (no Brasil):")
    for trib, url in (r.get("navegador") or {}).items():
        linhas.append(f"  {SIGLAS.get(trib, trib)}: {url}")
    return "\n".join(linhas)
