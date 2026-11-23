"""As fontes do STJ: o Portal de Dados Abertos (CKAN, licença CC-BY).

Cinco conjuntos, todos baixados pelos links que a **página HTML** de cada conjunto publica
(``https://dadosabertos.web.stj.jus.br/dataset/<slug>``, links
``/dataset/<uuid>/resource/<uuid>/download/<arquivo>``). O caminho ``/api/`` do portal é vedado pelo
robots.txt e nunca é pedido; a trava do coletor (``fontes.proibidos``) recusa antes de sair.

- **acervo em tramitação** (``processos_tramitando_AAAAMMDD.json.gz``, ~82 MB com gzip, 518 MB
  aberto, um processo por linha; só o último dia fica publicado): lido **em fluxo**, um registro
  por vez, e entregue como :class:`~.modelo.Processo` (contrato §1). Em 20/11/2026: 352.239
  processos, lidos em ~30 s com ~60 MB de memória;
- **atas de distribuição** (``ataAAAAMMDD.json``, uma por dia): o que entrou, por relator e por
  forma. ``Distribuição`` e ``Redistribuição`` contam como sorteio (a Redistribuição do STJ é, na
  maior parte, a saída da triagem da Presidência para o sorteio); ``Atribuição`` é herança de
  acervo (sucessão de quem saiu do colegiado); ``Registro`` é a entrada na Presidência, na
  Vice-Presidência ou na presidência de órgão, não de um gabinete;
- **pautas futuras** (``pautas_futuras_AAAAMMDD.json.gz``): o que vai a julgamento, por relator,
  órgão e sessão; o processo principal e as petições (AgInt, AgRg, EDcl são recursos internos);
- **precedentes qualificados** (``Temas.csv`` e ``Processos.csv``, sem data no nome e às vezes em
  dois recursos de mesmo nome: vale o de conteúdo mais recente): temas repetitivos, controvérsias,
  IAC, relevância, por relator e situação;
- **íntegras do Diário da Justiça** (``metadadosAAAAMMDD.json``): decisões terminativas e acórdãos
  publicados, por ministro, tipo e desfecho. Saem com atraso (em 22/11/2026, o último arquivo era
  de 15/10/2026): a data do último arquivo vai junto, para a página dizer o atraso.

Duas camadas:

1. **leitura** (funções puras, sem rede): ``links_da_pagina``, ``escolher_*``,
   :func:`ler_acervo`, :func:`ler_ata`, :func:`ler_pautas`, :func:`ler_temas` /
   :func:`ler_processos_precedentes` / :func:`montar_temas` e :func:`ler_integras`;
2. **coleta** (com o :class:`~.fontes.ColetorTribunais`: robots.txt, 10 s entre pedidos, uma nova
   tentativa depois de um 403): :func:`recursos_do_conjunto`, :func:`baixar_acervo`,
   :func:`baixar_atas`, :func:`baixar_pautas`, :func:`baixar_precedentes`,
   :func:`baixar_integras`. Erro de rede, recusa ou página sem link sobem como
   ``net.FetchError`` (quem chama vira aviso e mantém o último dia bom); nas séries (atas e
   íntegras), o que já baixou fica e o erro vai no :class:`Serie`.

Privacidade (régua do analista §5.4): o nome de pessoa física e de MEI **nunca** chega ao registro
(``nome: None`` já aqui), nem o dos advogados. A tipificação das partes daqui é preliminar (polo,
tipo e raiz do CNPJ conferida); a fina, com o catálogo de litigantes, é de ``partes.py``, que pode
ser passada como ``tipificar``.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import re
import time
import zlib
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import IO, Any
from urllib.parse import unquote, urljoin, urlsplit

from ..net import FetchError
from .areas import Classificador
from .modelo import (
    Assunto,
    Classificacao,
    Composicao,
    Parte,
    Processo,
    Regras,
    normalizar,
)

log = logging.getLogger(__name__)

TRIB = "stj"
PORTAL = "https://dadosabertos.web.stj.jus.br"
PAGINA_CONJUNTO = PORTAL + "/dataset/{slug}"
CONJUNTOS = {  # padrão; o config (fontes.stj.conjuntos) vence
    "acervo": "acervo-em-tramitacao",
    "atas": "atas-de-distribuicao",
    "pautas": "pautas-futuras",
    "precedentes": "precedentes-qualificados",
    "integras": "integras-de-decisoes-terminativas-e-acordaos-do-diario-da-justica",
}
# O nome do arquivo de cada conjunto (a data AAAAMMDD é o dia a que o arquivo se refere).
PADROES = {
    "acervo": re.compile(r"^processos_tramitando_(\d{8})\.json\.gz$", re.IGNORECASE),
    "atas": re.compile(r"^ata(\d{8})\.json$", re.IGNORECASE),
    "pautas": re.compile(r"^pautas_futuras_(\d{8})\.json\.gz$", re.IGNORECASE),
    "integras": re.compile(r"^metadados(\d{8})\.json$", re.IGNORECASE),
}
ARQUIVOS_PRECEDENTES = ("temas.csv", "processos.csv")
VALIDADE_IMUTAVEL_H = 24.0 * 400  # um arquivo datado tem URL própria (uuid do recurso): não muda
LINK_TEMA = ("https://processo.stj.jus.br/repetitivos/temas_repetitivos/pesquisa.jsp?novaConsulta=true"
             "&tipo_pesquisa=T&cod_tema_inicial={n}&cod_tema_final={n}")  # para o leitor; nada aqui o coleta
RECURSOS_INTERNOS = frozenset({"AgInt", "AgRg", "EDcl"})  # petições dentro do processo (analista §2.2)
TITULO_MAX = 280  # caracteres da "questão submetida a julgamento" guardados como título do tema


class FormatoInesperado(FetchError):
    """A fonte respondeu, mas não como esperado: página do conjunto sem link de download, arquivo
    truncado ou ilegível. Subclasse de ``net.FetchError`` para quem chama tratar como fonte que
    falhou (aviso; fica o último dia bom)."""


# ── 1. a página do conjunto ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Recurso:
    """Um arquivo publicado na página do conjunto."""

    url: str
    arquivo: str  # o nome no fim da URL ("ata20261119.json")
    data: str | None  # AAAA-MM-DD do nome do arquivo, quando há
    posicao: int  # ordem na página (o CKAN acrescenta no fim: republicação vem depois)


_LINK = re.compile(
    r"""href\s*=\s*["']([^"']*/dataset/[0-9A-Za-z-]+/resource/[0-9A-Za-z-]+/download/[^"'?#]+)["']""", re.IGNORECASE
)


def data_do_nome(arquivo: str) -> str | None:
    """A data AAAAMMDD no nome do arquivo, como ``AAAA-MM-DD`` (``None`` se não há data válida)."""
    for achado in re.findall(r"(?<!\d)(\d{8})(?!\d)", arquivo or ""):
        try:
            return date(int(achado[:4]), int(achado[4:6]), int(achado[6:])).isoformat()
        except ValueError:
            continue
    return None


def links_da_pagina(html: str, base: str = PORTAL) -> list[Recurso]:
    """Os links de download da página HTML de um conjunto do CKAN, na ordem da página, sem
    repetição. Só ``/dataset/<id>/resource/<id>/download/<arquivo>``; nada de ``/api/``."""
    vistos: set[str] = set()
    recursos: list[Recurso] = []
    for bruto in _LINK.findall(html or ""):
        url = urljoin(base.rstrip("/") + "/", bruto.strip())
        if url in vistos or "/api/" in urlsplit(url).path:
            continue
        vistos.add(url)
        arquivo = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        recursos.append(Recurso(url=url, arquivo=arquivo, data=data_do_nome(arquivo), posicao=len(recursos)))
    return recursos


def por_dia(recursos: Iterable[Recurso], padrao: re.Pattern[str] | str) -> dict[str, Recurso]:
    """Um recurso por dia, entre os que casam com ``padrao`` (chave de :data:`PADROES` ou regex).
    Arquivo do mesmo dia publicado mais de uma vez (o STJ republica íntegras): vale o último da
    página."""
    regex = PADROES[padrao] if isinstance(padrao, str) else padrao
    dias: dict[str, Recurso] = {}
    for r in recursos:
        m = regex.match(r.arquivo)
        if not m:
            continue
        dia = data_do_nome(m.group(1))
        if dia is None:
            continue
        atual = dias.get(dia)
        if atual is None or r.posicao > atual.posicao:
            dias[dia] = r
    return dict(sorted(dias.items()))


def escolher_mais_recente(recursos: Iterable[Recurso], padrao: re.Pattern[str] | str) -> Recurso | None:
    """O arquivo do dia mais recente (acervo, pautas)."""
    dias = por_dia(recursos, padrao)
    return dias[max(dias)] if dias else None


def escolher_ultimos_dias(
    recursos: Iterable[Recurso], padrao: re.Pattern[str] | str, dias: int, ate: str | date | None = None
) -> list[Recurso]:
    """Os arquivos dos últimos ``dias`` dias até ``ate`` (inclusive; padrão: o dia do arquivo mais
    recente, que nas íntegras fica semanas atrás de hoje), um por dia, do mais antigo ao mais novo."""
    por = por_dia(recursos, padrao)
    if not por or dias <= 0:
        return []
    fim = date.fromisoformat(str(ate)) if ate is not None else date.fromisoformat(max(por))
    inicio = fim.toordinal() - dias + 1
    return [r for d, r in por.items() if inicio <= date.fromisoformat(d).toordinal() <= fim.toordinal()]


def por_nome(recursos: Iterable[Recurso], arquivo: str) -> list[Recurso]:
    """Os recursos com o nome de arquivo dado, sem diferença de caixa (``temas.csv``)."""
    alvo = arquivo.lower()
    return [r for r in recursos if r.arquivo.lower() == alvo]


def atraso_dias(data_arquivo: str | None, hoje: date) -> int | None:
    """Dias entre o dia do último arquivo publicado e ``hoje`` (íntegras: o atraso que a página diz)."""
    if not data_arquivo:
        return None
    return (hoje - date.fromisoformat(data_arquivo)).days


# ── 2. partes (tipificação preliminar; a fina é de partes.py) ───────────────────────────────

# "Recorre" × "responde" (analista §5.1). No STJ quase tudo é recurso: "ativo" é quem recorre ou
# pede; "passivo", quem responde. O paciente do HC fica no polo ativo, sempre como pessoa física.
PAPEIS_ATIVO = frozenset({
    "AGRAVANTE", "RECORRENTE", "EMBARGANTE", "IMPETRANTE", "REQUERENTE", "RECLAMANTE", "EXEQUENTE", "AUTOR",
    "PARTE AUTORA", "QUERELANTE", "EXCIPIENTE", "LITISCONSORTE ATIVO", "APELANTE", "IMPUGNANTE", "INTERPELANTE",
    "NOTICIANTE", "COMUNICANTE", "PACIENTE",
})
PAPEIS_PASSIVO = frozenset({
    "AGRAVADO", "RECORRIDO", "EMBARGADO", "REQUERIDO", "RECLAMADO", "EXECUTADO", "REU", "PARTE RE", "QUERELADO",
    "EXCEPTO", "LITISCONSORTE PASSIVO", "APELADO", "IMPUGNADO", "INTERPELADO", "NOTICIADO", "ACUSADO", "INVESTIGADO",
    "INDICIADO", "CORREU", "SINDICADO", "COMUNICADO", "REQUISITADO",
})
# Autoridade, não parte (o tribunal impetrado no HC, os juízos do conflito, o próprio STJ como
# requisitante do precatório, o Ministério da Justiça nas cartas rogatórias): fora de "quem se repete".
PAPEIS_AUTORIDADE = frozenset({
    "IMPETRADO", "SUSCITANTE", "SUSCITADO", "REQUISITANTE", "AUTORIDADE CENTRAL", "JUSTICA ROGANTE",
    "TRIBUNAL DE ORIGEM", "DEPRECANTE", "REMETENTE", "REMETENTE EX OFFICIO",
})
# Qualificador: liga-se à parte anterior (representante, herdeiro, outro nome); não é litigante.
PAPEIS_QUALIFICADOR = frozenset({
    "REPRESENTADO POR", "REPRESENTADO", "OUTRO NOME", "HERDEIROS DE", "SUCESSORES DE", "SUCESSOR DE",
    "DENOMINACAO ANTERIOR", "POR INCORPORACAO", "OU", "_", "ASSISTIDO(A) POR", "ASSISTIDO(A) PELO", "CURADOR",
    "CURADOR ESPECIAL", "REPRESENTANTE", "REPRESENTANDO", "REPRESENTANTE DA", "MENOR IMPUBERE", "FORMADORA DO",
    "DEFENSOR DATIVO", "SINDICO", "AGRAVO RETIDO", "AGRAVO REGIMENTAL", "RECURSO ADESIVO", "MANTENEDORA",
})
# O resto (INTERESSADO, TERCEIRO INTERESSADO, ASSISTENTE..., LITISCONSORTE, PARTE, VÍTIMA) e papel
# novo: polo "outro".

SEGREDO_PARTE = "PARTE EM SEGREDO DE JUSTICA"
_SEM_NOME = frozenset({"", "-", "_", "NAO CONSTA", "NAO INDICADO", "NAO INFORMADO", "OS MESMOS", "O MESMO",
                       "A MESMA", "AS MESMAS", "SEM NOME", "DESCONHECIDO", "IGNORADO"})
# Nome que termina com um CPF (11 dígitos, com ou sem pontuação): MEI, tratado como pessoa física.
_CPF_NO_FIM = re.compile(r"(?:\d{11}|\d{3}\.\d{3}\.\d{3}-\d{2})\s*$")
_ENTE = re.compile(
    r"^(?:UNIAO\b|FAZENDA (?:NACIONAL|PUBLICA|DO ESTADO|ESTADUAL|MUNICIPAL|DO MUNICIPIO)|ESTADO D[OAE]S? |"
    r"DISTRITO FEDERAL\b|MUNICIPIO D|PREFEITURA|CAMARA (?:MUNICIPAL|DOS DEPUTADOS)|SENADO FEDERAL|"
    r"ASSEMBLEIA LEGISLATIVA|INSTITUTO NACIONAL DO SEGURO SOCIAL|INSS\b|AGENCIA NACIONAL|BANCO CENTRAL DO BRASIL|"
    r"CONSELHO (?:FEDERAL|REGIONAL|NACIONAL)|DEPARTAMENTO (?:NACIONAL|ESTADUAL|DE ESTRADAS)|"
    r"UNIVERSIDADE (?:FEDERAL|ESTADUAL)|INSTITUTO FEDERAL|AUTARQUIA|SECRETARIA\b)"
    r"|MINISTERIO PUBLICO|DEFENSORIAS? PUBLICAS?|PROCURADORIA|ADVOCACIA[- ]GERAL"
)
_ORGAO_JULGADOR = re.compile(
    r"^(?:TRIBUNALE?\b|SUPERIOR TRIBUNAL|SUPREMO TRIBUNAL|JUIZO\b|JUIZ\b|JUIZA\b|JUZGADO|JUSTICA\b|VARA\b|"
    r"TURMA RECURSAL|(?:PRIMEIRA|SEGUNDA|TERCEIRA|QUARTA|QUINTA|SEXTA) (?:TURMA|SECAO|CAMARA)|SECAO\b|"
    r"CORREGEDORIA\b|MINISTR[OA] D[OAE]S? |SECRETARIO\b|GOVERNADORA?\b|PREFEIT[OA]\b|"
    r"PRESIDENTE D|DELEGAD[OA] D|DIRETORA?\b|COMANDANTE\b|CHEFE D|REITORA?\b|SUPERINTENDENTE\b|"
    r"ADVOGAD[OA][- ]GERAL|PROCURADORA?[- ]GERAL)"
)
_PJ = re.compile(
    r"\b(?:LTDA|EIRELI|S/A|S\.A|LIMITED|LTD|LLC|INC|CORP|CORPORATION|GMBH|BANCO|BANK|CIA|COMPANHIA|SINDICATO|"
    r"ASSOCIACAO|FEDERACAO|CONFEDERACAO|COOPERATIVA|FUNDACAO|INSTITUTO|CONDOMINIO|EMPRESA|ADVOGADOS|SOCIEDADE|"
    r"PARTIDO|IGREJA|HOSPITAL|FACULDADE|COLEGIO|ESCOLA|CLUBE|MASSA FALIDA|CONSORCIO|SEGURADORA|SEGUROS|ASSOCIADOS|"
    r"COMERCIO|INDUSTRIA|SERVICOS|PARTICIPACOES|ENGENHARIA|CONSTRUTORA|TRANSPORTES|AGROPECUARIA|ADMINISTRADORA|"
    r"INVESTIMENTOS|FUNDO|ORGANIZACAO|ENTIDADE|UNIMED|GRUPO DE ATUACAO|OAB|ORDEM DOS ADVOGADOS)\b"
)


def cnpj_valido(cnpj: Any) -> bool:
    """CNPJ com 14 dígitos e os dois dígitos verificadores certos (``00000000000191``, o Banco do
    Brasil, é válido; ``00000000000000`` não)."""
    numeros = re.sub(r"\D", "", str(cnpj or ""))
    if len(numeros) != 14 or len(set(numeros)) == 1:
        return False
    digitos = [int(c) for c in numeros]
    for n in (12, 13):
        pesos = list(range(n - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(d * p for d, p in zip(digitos[:n], pesos)) % 11
        if digitos[n] != (0 if resto < 2 else 11 - resto):
            return False
    return True


_ACENTOS = "ÁÀÂÃÄÅÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑÝáàâãäåéèêëíìîïóòôõöúùûüçñýºª"
_SEM_ACENTOS = "AAAAAAEEEEIIIIOOOOOUUUUCNYaaaaaaeeeeiiiiooooouuuucnyoa"
_TRADUCAO = str.maketrans({**dict(zip(_ACENTOS, _SEM_ACENTOS)), **{c: " " for c in "\"'“”‘’«»`´\xa0"}})


def chave_rapida(texto: str | None) -> str:
    """A forma de comparação de :func:`~.modelo.normalizar` (sem acento, maiúsculas, sem aspas,
    espaços colapsados) para os acentos do português, sem decompor caractere por caractere: são
    ~1,3 milhão de nomes de parte por acervo."""
    return " ".join((texto or "").translate(_TRADUCAO).upper().split())


@lru_cache(maxsize=512)
def polo_do_papel(papel: str | None) -> tuple[str | None, str]:
    """``(polo, natureza)`` de um ``descTipoParte``: natureza ``parte``, ``paciente``,
    ``autoridade`` ou ``qualificador``; o polo é ``None`` para autoridade e qualificador."""
    chave = normalizar(papel)
    if chave in PAPEIS_AUTORIDADE:
        return None, "autoridade"
    if chave in PAPEIS_QUALIFICADOR:
        return None, "qualificador"
    if chave == "PACIENTE":
        return "ativo", "paciente"
    if chave in PAPEIS_ATIVO:
        return "ativo", "parte"
    if chave in PAPEIS_PASSIVO:
        return "passivo", "parte"
    return "outro", "parte"


def _limpo(texto: Any) -> str:
    return " ".join(str(texto or "").split())


def parte_preliminar(bruta: Mapping[str, Any]) -> Parte:
    """Uma parte do STJ (``{descTipoParte, nomeParte, numeroCNPJ, ...}``) → :class:`~.modelo.Parte`
    com polo, tipo **preliminar** e raiz do CNPJ conferida. Pessoa física, MEI (CNPJ com CPF no fim
    do nome), parte em segredo e qualificador sem CNPJ ficam com ``nome: None``; advogados nunca
    entram. Sem CNPJ, o tipo sai do nome: ente público, órgão julgador ou autoridade (vira
    ``autoridade``), pessoa jurídica pelos marcadores (LTDA, S/A, SINDICATO...) ou, no resto, pessoa
    física. ``grupo`` fica para ``partes.py``."""
    papel = _limpo(bruta.get("descTipoParte"))
    polo, natureza = polo_do_papel(papel)
    nome_bruto = _limpo(bruta.get("nomeParte"))
    chave = chave_rapida(nome_bruto)
    cnpj = str(bruta.get("numeroCNPJ") or "")
    if cnpj and not cnpj.isdigit():
        cnpj = re.sub(r"\D", "", cnpj)
    valido = bool(cnpj) and cnpj_valido(cnpj)
    nome: str | None = nome_bruto or None
    raiz: str | None = cnpj[:8] if valido else None
    if chave == SEGREDO_PARTE or chave in _SEM_NOME:
        tipo, nome, raiz = "desconhecido", None, None
    elif natureza == "paciente" or _CPF_NO_FIM.search(nome_bruto):
        tipo, nome, raiz = "pf", None, None
    else:
        orgao = _ORGAO_JULGADOR.match(chave) is not None
        ente = _ENTE.search(chave) is not None
        pj = valido or _PJ.search(chave) is not None
        if natureza == "autoridade":
            tipo = "autoridade"
            if not (orgao or ente or pj):  # nome de gente no papel de autoridade: fica sem nome
                nome = None
        elif orgao:
            tipo, polo = "autoridade", None
        elif ente:
            tipo = "ente"
        elif pj:
            tipo = "pj"
        else:
            tipo, nome, raiz = "pf", None, None
    if natureza == "qualificador":
        tipo = "qualificador"
        if raiz is None:  # representante, herdeiro, curador: gente; só a empresa (outro nome, incorporação) fica
            nome = None
    return Parte(polo=polo, papel=papel, nome=nome, tipo=tipo, grupo=None, cnpj_raiz=raiz)  # type: ignore[typeddict-item]


Tipificador = Callable[[Mapping[str, Any], str], Parte]  # partes.tipificar(parte_bruta, trib)


# ── 3. leitura em fluxo de JSON grande ───────────────────────────────────────────────────────


@dataclass
class Leitura:
    """O que a leitura em fluxo viu (para a sanidade de quem chama)."""

    objetos: int = 0
    ilegiveis: int = 0


def abrir_texto(origem: Path | str | bytes | IO[bytes]) -> IO[str]:
    """Abre um arquivo (ou bytes, ou fluxo binário com ``seek``) como texto UTF-8, descompactando o
    gzip pelo número mágico (não pelo nome)."""
    if isinstance(origem, (str, Path)):
        with open(origem, "rb") as f:
            magia = f.read(2)
        bruto: IO[bytes] = gzip.open(origem, "rb") if magia == b"\x1f\x8b" else open(origem, "rb")  # noqa: SIM115
        return io.TextIOWrapper(bruto, encoding="utf-8-sig", newline="")
    fluxo: IO[bytes] = io.BytesIO(bytes(origem)) if isinstance(origem, (bytes, bytearray)) else origem
    posicao = fluxo.tell()
    magia = fluxo.read(2)
    fluxo.seek(posicao)
    if magia == b"\x1f\x8b":
        fluxo = gzip.GzipFile(fileobj=fluxo, mode="rb")  # type: ignore[assignment]
    return io.TextIOWrapper(fluxo, encoding="utf-8-sig", newline="")


def objetos_json(texto: IO[str], leitura: Leitura | None = None, *, bloco: int = 4 << 20) -> Iterator[Any]:
    """Os objetos de um array JSON, um por vez, sem carregar o arquivo (o acervo do STJ tem 518 MB
    aberto: um objeto por linha; as atas vêm indentadas: as duas formas servem). Objeto ilegível no
    meio do arquivo é contado em ``leitura.ilegiveis`` e pulado (a leitura recomeça na linha
    seguinte que abre um objeto)."""
    decodificar = json.JSONDecoder().raw_decode
    leitura = leitura if leitura is not None else Leitura()
    separadores = " \t\r\n,[]﻿"
    folga = 1 << 16  # erro a menos disto do fim do bloco: o objeto só está cortado; ler mais
    buf, i, fim = "", 0, False
    while True:
        n = len(buf)
        while i < n and buf[i] in separadores:
            i += 1
        if i >= n:
            if fim:
                return
            buf, i = texto.read(bloco), 0
            if not buf:
                return
            continue
        try:
            obj, j = decodificar(buf, i)
        except json.JSONDecodeError as exc:
            if not fim and exc.pos >= n - folga:
                mais = texto.read(bloco)
                fim = not mais
                buf, i = buf[i:] + mais, 0
                continue
            leitura.ilegiveis += 1
            proximo = buf.find("\n{", max(exc.pos, i + 1))
            if proximo < 0:
                if fim:
                    return
                buf, i = texto.read(bloco), 0
                fim = not buf
                continue
            i = proximo + 1
            continue
        leitura.objetos += 1
        yield obj
        i = j
        if i > (1 << 22) and i > len(buf) // 2:
            buf, i = buf[i:], 0


def _carregar_json(origem: Path | str | bytes | Any) -> Any:
    """JSON pequeno (ata, pauta, metadados): de um caminho, bytes (gzip ou não) ou já carregado."""
    if isinstance(origem, (list, dict)):
        return origem
    with abrir_texto(origem) as texto:
        try:
            return json.load(texto)
        except (ValueError, EOFError, OSError, zlib.error) as exc:
            raise FormatoInesperado(str(origem)[:200], f"JSON ilegível ({exc})") from exc


# ── 4. o leitor (composição, classificação e partes com cache) ───────────────────────────────


@lru_cache(maxsize=1 << 16)
def _data_iso(valor: Any) -> str | None:
    """``"2026-11-20"``, ``"2026-11-20T08:01:02"`` ou ``"20/11/2026"`` → ``"2026-11-20"``."""
    texto = str(valor or "").strip()
    if not texto:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", texto)
    if m:
        bruto = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    else:
        m = re.match(r"^(\d{2})/(\d{2})/(\d{4})", texto)
        if not m:
            return None
        bruto = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    try:
        return date.fromisoformat(bruto).isoformat()
    except ValueError:
        return None


def _digitos(valor: Any) -> str:
    texto = str(valor) if valor is not None else ""
    return texto if texto.isdigit() else re.sub(r"\D", "", texto)


def _sim(valor: Any) -> bool:
    """``"SIM"``, ``"S"``, ``"Sim"`` → verdadeiro; ``"NÃO"``, ``"N"``, vazio → falso."""
    return str(valor or "").strip().upper() in ("SIM", "S", "TRUE", "1")


class LeitorSTJ:
    """O que todas as leituras do STJ usam, com cache: o relator pela composição do config, o
    lugar do processo (na mesa e ``onde``), a classificação de área (por classe e assuntos) e as
    partes. As listas e dicionários de classificação (``assunto``, ``assuntos``, ``recortes``,
    ``tambem_toca``) são **compartilhados** entre processos com os mesmos assuntos (352 mil registros
    ficariam caros): trate-os como só leitura."""

    def __init__(self, regras: Regras, classificador: Classificador, *, tipificar: Tipificador | None = None) -> None:
        self.regras = regras
        self.composicao: Composicao = regras.composicao[TRIB]
        self.classificador = classificador
        self.tipificar = tipificar
        self._relatores: dict[Any, tuple[str | None, str | None, str | None, bool]] = {}
        self._lugares: dict[tuple[str | None, str | None], tuple[bool, str]] = {}
        self._classes: dict[tuple[Any, ...], tuple[Classificacao, Assunto, list[int]]] = {}
        self._datas: dict[str, date] = {}
        self._textos: dict[str, str] = {}
        self._pj: dict[tuple[str, str, str], Parte] = {}
        self.grade = {m.id for m in self.composicao.ministros if m.grade}

    def texto(self, valor: Any) -> str | None:
        """Texto limpo e compartilhado (o mesmo local aparece em 64 mil registros)."""
        if valor is None:
            return None
        bruto = str(valor).strip()
        if not bruto:
            return None
        return self._textos.setdefault(bruto, bruto)

    def data(self, iso: str) -> date:
        d = self._datas.get(iso)
        if d is None:
            d = self._datas[iso] = date.fromisoformat(iso)
        return d

    def relator(self, texto: Any) -> tuple[str | None, str | None, str | None, bool]:
        """``(nome de exibição, relator_id, fora, conhecido)``. Nome que não casa com ministro nem
        com linha fora da grade vai para ``residuos`` (``conhecido`` diz se está em ``saiu``)."""
        chave = texto if texto is None else str(texto)
        achado = self._relatores.get(chave)
        if achado is None:
            r = self.composicao.relator(chave)
            nome = r.ministro.nome if r.ministro is not None else self.texto(chave)
            achado = (nome, r.ministro.id if r.ministro is not None else None, r.fora, r.conhecido)
            self._relatores[chave] = achado
        return achado

    def lugar(self, relator_id: str | None, local: str | None) -> tuple[bool, str]:
        """``(na_mesa, onde)`` (contrato §1, ``onde`` na ordem de teste)."""
        chave = (relator_id, local)
        achado = self._lugares.get(chave)
        if achado is None:
            na_mesa = self.composicao.na_mesa(relator_id, local)
            achado = self._lugares[chave] = (na_mesa, onde_stj(local, na_mesa, self.composicao))
        return achado

    def classificar(self, classe: str | None, codigo: Any, lista: Any) -> tuple[Classificacao, Assunto, list[int]]:
        chave = (classe, codigo, lista)
        achado = self._classes.get(chave)
        if achado is None:
            achado = self._classes[chave] = self.classificador.classificar_stj(
                classe=classe, codigo_principal=codigo, lista_assuntos=lista
            )
        return achado

    def partes(self, brutas: Iterable[Mapping[str, Any]] | None) -> list[Parte]:
        saida: list[Parte] = []
        for bruta in brutas or ():
            if not isinstance(bruta, Mapping):
                continue
            if self.tipificar is not None:
                saida.append(self.tipificar({k: v for k, v in bruta.items() if k != "advogados"}, TRIB))
                continue
            cnpj = str(bruta.get("numeroCNPJ") or "")
            if cnpj:  # PJ e ente se repetem muito: cache pelo trio
                chave = (str(bruta.get("descTipoParte") or ""), str(bruta.get("nomeParte") or ""), cnpj)
                parte = self._pj.get(chave)
                if parte is None:
                    parte = parte_preliminar(bruta)
                    for campo in ("papel", "nome"):
                        if parte[campo]:  # type: ignore[literal-required]
                            parte[campo] = self.texto(parte[campo])  # type: ignore[literal-required]
                    self._pj[chave] = parte
                saida.append(dict(parte))  # type: ignore[arg-type]
            else:
                parte = parte_preliminar(bruta)
                parte["papel"] = self.texto(parte["papel"]) or ""
                if parte["nome"]:
                    parte["nome"] = self.texto(parte["nome"])
                saida.append(parte)
        return saida


def onde_stj(local: str | None, na_mesa: bool, composicao: Composicao | None = None) -> str:
    """O local resumido (contrato §1), nesta ordem: ``gabinete`` (na mesa), ``outro_gabinete``,
    ``presidencia`` (Presidência, Vice, a ARP e a Coordenadoria de Triagem), ``stf`` (remetido ou na
    coordenadoria de recursos para o STF), ``mp`` (com o MPF), ``cartorio`` (as coordenadorias) e
    ``outro`` (secretarias de Turma, seções, sem local)."""
    if na_mesa:
        return "gabinete"
    chave = normalizar(local)
    if not chave:
        return "outro"
    if chave.startswith("GABINETE") or (composicao is not None and composicao.do_gabinete(local) is not None):
        return "outro_gabinete"
    if (chave in ("PRESIDENCIA", "VICE-PRESIDENCIA") or chave.startswith("ASSESSORIA DE ADMISSIBILIDADE")
            or "TRIAGEM" in chave):
        return "presidencia"
    if chave == "SUPREMO TRIBUNAL FEDERAL" or (chave.startswith("COORDENADORIA") and "PARA O STF" in chave):
        return "stf"
    if chave.startswith("MINISTERIO PUBLICO"):
        return "mp"
    if chave.startswith("COORDENADORIA"):
        return "cartorio"
    return "outro"


def _id(classe: str, numero: str, registro: str | None) -> str:
    """``stj-<classe>-<número>``; sem número na classe (processo recém-chegado, ainda sem número),
    ``stj-<classe>-r<registro>``."""
    if numero:
        return f"{TRIB}-{classe}-{numero}"
    return f"{TRIB}-{classe}-r{registro or ''}"


# ── 5. o acervo em tramitação ────────────────────────────────────────────────────────────────


@dataclass
class ResumoAcervo:
    """O que a leitura do acervo contou (sanidade e ``meta.json → cobertura.stj``). Preenchido
    durante a iteração de :func:`ler_acervo`; só vale depois que a iteração termina."""

    arquivo_data: str | None = None  # a data do nome do arquivo, se quem chama a deu
    datas: Counter[str] = field(default_factory=Counter)  # o campo "Data" dos registros
    lidos: int = 0
    ilegiveis: int = 0
    processos: int = 0
    na_grade: int = 0
    na_mesa: int = 0
    segredo: int = 0
    sem_numero: int = 0  # numeroNaClasse nulo (o id usa o registro)
    sem_assunto: int = 0
    area_por: Counter[str] = field(default_factory=Counter)
    codigos_fora_da_tpu: int = 0  # código de assunto principal que a TPU versionada não tem
    fora_da_grade: Counter[str] = field(default_factory=Counter)
    cargos: Counter[str] = field(default_factory=Counter)  # ministros fora da grade (Presidência, Vice, Corregedoria)
    relatores: Counter[str] = field(default_factory=Counter)  # rótulo do arquivo → processos
    fora_do_mapa: Counter[str] = field(default_factory=Counter)  # nome que a composição não conhece
    segundos: float = 0.0

    @property
    def data(self) -> str | None:
        """A data do retrato: o ``Data`` mais comum dos registros (todos iguais num arquivo bom)."""
        return self.datas.most_common(1)[0][0] if self.datas else None

    def para_meta(self) -> dict[str, Any]:
        """O bloco ``meta.json → cobertura.stj`` (contrato §3)."""
        return {
            "processos": self.processos,
            "na_grade": self.na_grade,
            "segredo": self.segredo,
            "area_por": dict(sorted(self.area_por.items(), key=lambda kv: (-kv[1], kv[0]))),
            "codigos_fora_da_tpu": self.codigos_fora_da_tpu,
            "relatores_fora_do_mapa": [[n, q] for n, q in sorted(self.fora_do_mapa.items(), key=lambda kv: (-kv[1], kv[0]))],
            "fora_da_grade": dict(sorted(self.fora_da_grade.items(), key=lambda kv: (-kv[1], kv[0]))),
        }

    def problemas(self, minimo_reconhecido: float = 0.95, maximo_ilegivel: float = 0.001) -> list[str]:
        """Frases do que impede usar o arquivo (vazio = arquivo bom): ``Data`` diferente do nome
        do arquivo, linhas ilegíveis acima de 0,1%, relatores das Turmas reconhecidos abaixo de 95%."""
        saida = []
        if not self.processos:
            saida.append("O arquivo do acervo não trouxe nenhum processo.")
            return saida
        if self.arquivo_data and set(self.datas) != {self.arquivo_data}:
            outras = sorted(d for d in self.datas if d != self.arquivo_data)
            saida.append(f"O campo Data dos registros ({', '.join(outras[:3])}) difere do nome do arquivo ({self.arquivo_data}).")
        if self.ilegiveis > maximo_ilegivel * max(1, self.lidos + self.ilegiveis):
            saida.append(f"{self.ilegiveis} registros ilegíveis no arquivo do acervo.")
        desconhecidos = sum(self.fora_do_mapa.values())
        if desconhecidos and self.na_grade / (self.na_grade + desconhecidos) < minimo_reconhecido:
            saida.append(f"{desconhecidos} processos com relator fora da composição do config (composição desatualizada?).")
        return saida


def processo_stj(registro: Mapping[str, Any], leitor: LeitorSTJ, *, data_dados: str | None = None) -> Processo:
    """Um registro do acervo (``processos_tramitando_*.json.gz``) → :class:`~.modelo.Processo`
    (contrato §1). ``data_dados``: a data do retrato quando o registro não traz ``Data``."""
    classe = leitor.texto(registro.get("siglaClasse")) or ""
    numero = _digitos(registro.get("numeroNaClasse"))
    registro_stj = _digitos(registro.get("numeroRegistro")) or None  # único por processo: não vale guardar no cache
    nome, relator_id, fora, _conhecido = leitor.relator(registro.get("nomeMinistroRelator"))
    local = leitor.texto(registro.get("localProcesso"))
    na_mesa, onde = leitor.lugar(relator_id, local)
    classificacao, assunto, codigos = leitor.classificar(
        classe, registro.get("codigoAssuntoCNJ"), registro.get("listaAssuntos")
    )
    autuacao = _data_iso(registro.get("dataRecebimento"))
    distribuicao = _data_iso(registro.get("dataUltimaDistribuicao"))
    decisao = _data_iso(registro.get("dataUltimaDecisao"))
    referencia = _data_iso(registro.get("Data")) or data_dados
    marco = max((d for d in (distribuicao, decisao) if d), default=None)
    sem_decisao = (leitor.data(referencia) - leitor.data(marco)).days if referencia and marco else None
    sobrestado = (leitor.texto(registro.get("sobrestado")) or "N").upper()
    cnj = _digitos(registro.get("numeroUnico")) or None
    return Processo(
        trib=TRIB,  # type: ignore[typeddict-item]
        id=_id(classe, numero, registro_stj),
        classe=classe,
        numero=numero,
        cnj=cnj,
        registro=registro_stj,
        relator=nome,
        relator_id=relator_id,
        fora=fora,
        orgao=leitor.texto(registro.get("codigoOrgaoJulgador")),
        local=local,
        onde=onde,  # type: ignore[typeddict-item]
        na_mesa=na_mesa,
        area=classificacao["area"],
        area_por=classificacao["por"],
        subarea=classificacao["subarea"],
        recortes=classificacao["recortes"],
        tambem_toca=classificacao["tambem_toca"],
        assunto=assunto,
        assuntos=codigos,
        segredo=_sim(registro.get("segredoJustica")),
        partes=leitor.partes(registro.get("partes")),
        datas={
            "autuacao": leitor.texto(autuacao),
            "ano_autuacao": int(autuacao[:4]) if autuacao else None,
            "distribuicao": leitor.texto(distribuicao),
            "ultima_decisao": leitor.texto(decisao),
            "andamento": None,
            "localizacao": None,
        },
        andamento=None,
        sem_decisao_dias=max(0, sem_decisao) if sem_decisao is not None else None,
        concluso_dias=None,
        local_dias=None,
        flags={
            "liminar": _sim(registro.get("pedidoDeLiminar")),
            "sobrestado": sobrestado not in ("N", ""),
            "motivo_sobrestamento": sobrestado if sobrestado not in ("N", "") else None,
        },
        origem={"uf": leitor.texto(registro.get("UF")), "orgao": leitor.texto(registro.get("origem"))},
    )


def _contar(resumo: ResumoAcervo, p: Processo, registro: Mapping[str, Any], leitor: LeitorSTJ) -> None:
    resumo.processos += 1
    rotulo = str(registro.get("nomeMinistroRelator") or "")
    resumo.relatores[rotulo] += 1
    if p["relator_id"] in leitor.grade:
        resumo.na_grade += 1
    elif p["relator_id"]:
        resumo.cargos[p["relator_id"]] += 1
    elif p["fora"]:
        resumo.fora_da_grade[p["fora"]] += 1
    if p["fora"] == "residuos" and not leitor.relator(registro.get("nomeMinistroRelator"))[3]:
        resumo.fora_do_mapa[rotulo] += 1
    if p["na_mesa"]:
        resumo.na_mesa += 1
    if p["segredo"]:
        resumo.segredo += 1
    if not p["numero"]:
        resumo.sem_numero += 1
    resumo.area_por[p["area_por"]] += 1
    bruto = str(registro.get("codigoAssuntoCNJ") or "").strip()
    if p["assunto"]["codigo"] is None:
        resumo.sem_assunto += 1
        if bruto.isdigit():
            resumo.codigos_fora_da_tpu += 1
    data = registro.get("Data")
    if data:
        resumo.datas[str(data)] += 1


def ler_acervo(
    origem: Path | str | bytes | IO[bytes],
    regras: Regras,
    classificador: Classificador,
    *,
    tipificar: Tipificador | None = None,
    data_dados: str | None = None,
    resumo: ResumoAcervo | None = None,
    leitor: LeitorSTJ | None = None,
) -> Iterator[Processo]:
    """Os processos do acervo em tramitação, **um por vez** (o arquivo nunca é carregado inteiro).

    ``origem``: o ``.json.gz`` baixado (ou bytes, ou um fluxo binário). ``data_dados``: a data do
    nome do arquivo (o retrato; vale quando o registro não traz ``Data`` e entra na sanidade de
    ``resumo``). ``resumo``: um :class:`ResumoAcervo` que a leitura preenche. Arquivo truncado ou
    com gzip corrompido: :class:`FormatoInesperado` no meio da iteração (quem chama descarta o
    que já leu e fica com o último dia bom)."""
    leitor = leitor or LeitorSTJ(regras, classificador, tipificar=tipificar)
    resumo = resumo if resumo is not None else ResumoAcervo()
    resumo.arquivo_data = resumo.arquivo_data or data_dados
    leitura = Leitura()
    inicio = time.monotonic()
    try:
        with abrir_texto(origem) as texto:
            for registro in objetos_json(texto, leitura):
                if not isinstance(registro, Mapping):
                    leitura.ilegiveis += 1
                    continue
                try:
                    p = processo_stj(registro, leitor, data_dados=data_dados)
                except (TypeError, ValueError, KeyError, AttributeError) as exc:
                    leitura.ilegiveis += 1
                    log.debug("Tribunais: registro do acervo do STJ ilegível (%s): %s", exc, str(registro)[:200])
                    continue
                _contar(resumo, p, registro, leitor)
                yield p
    except (EOFError, zlib.error, gzip.BadGzipFile, UnicodeDecodeError) as exc:
        raise FormatoInesperado(str(origem)[:200], f"arquivo do acervo truncado ou corrompido ({exc})") from exc
    finally:
        resumo.lidos = leitura.objetos
        resumo.ilegiveis = leitura.ilegiveis
        resumo.segundos = round(time.monotonic() - inicio, 1)


# ── 6. atas de distribuição ──────────────────────────────────────────────────────────────────

FORMAS = {"DISTRIBUICAO": "sorteio", "REDISTRIBUICAO": "sorteio", "ATRIBUICAO": "heranca", "REGISTRO": "registro"}


def tipo_da_forma(forma: str | None) -> str:
    """``descFormaDistribuicao`` → ``sorteio`` (Distribuição, Redistribuição), ``heranca``
    (Atribuição: sucessão no acervo de quem saiu), ``registro`` (Presidência, Vice, presidente de
    órgão) ou ``outro``."""
    return FORMAS.get(normalizar(forma), "outro")


def _segredo_nas_partes(partes: Iterable[Mapping[str, Any]] | None) -> bool | None:
    for p in partes or ():
        if isinstance(p, Mapping) and normalizar(p.get("nomeParte")) == SEGREDO_PARTE:
            return True
    return None


def ler_ata(
    origem: Path | str | bytes | Sequence[Mapping[str, Any]],
    regras: Regras,
    classificador: Classificador,
    *,
    data: str | None = None,
    leitor: LeitorSTJ | None = None,
    tipificar: Tipificador | None = None,
) -> list[dict[str, Any]]:
    """Uma ata de distribuição (``ataAAAAMMDD.json``) → uma entrada por distribuição, na ordem do
    arquivo: ``{trib, data, instante, forma, tipo, id, classe, numero, cnj, registro, orgao,
    relator, relator_id, fora, destino, assunto, area, subarea, recortes, segredo, partes}``.

    ``tipo``: ver :func:`tipo_da_forma`; ``forma`` fica como veio. ``segredo``: ``True`` quando a
    ata mascara as partes, ``None`` quando não diz. A mesma distribuição pode aparecer duas vezes no
    dia (Redistribuição para a Presidência às 8h e para o ministro às 11h): são eventos, não
    repetição; a deduplicação por número de registro dentro da janela é de quem conta."""
    leitor = leitor or LeitorSTJ(regras, classificador, tipificar=tipificar)
    dados = _carregar_json(origem)
    if not isinstance(dados, list):
        raise FormatoInesperado(str(origem)[:200], "a ata não é uma lista de distribuições")
    saida = []
    for item in dados:
        if not isinstance(item, Mapping):
            continue
        classe = leitor.texto(item.get("siglaClasse")) or ""
        numero = _digitos(item.get("numeroNaClasse"))
        registro = _digitos(item.get("numeroRegistro")) or None
        nome, relator_id, fora, _ = leitor.relator(item.get("nomeMinistroRelator"))
        instante = str(item.get("dataHoraDistribuicao") or "").strip() or None
        classificacao, assunto, _codigos = leitor.classificar(classe, item.get("codigoAssuntoCNJ"), None)
        saida.append({
            "trib": TRIB,
            "data": leitor.texto(_data_iso(instante) or data),
            "instante": instante,
            "forma": leitor.texto(item.get("descFormaDistribuicao")),
            "tipo": tipo_da_forma(item.get("descFormaDistribuicao")),
            "id": _id(classe, numero, registro),
            "classe": classe,
            "numero": numero,
            "cnj": _digitos(item.get("numeroUnico")) or None,
            "registro": registro,
            "orgao": leitor.texto(item.get("codigoOrgaoJulgador")),
            "relator": nome,
            "relator_id": relator_id,
            "fora": fora,
            "destino": leitor.texto(item.get("descDestino")),
            "assunto": assunto["codigo"],
            "area": classificacao["area"],
            "subarea": classificacao["subarea"],
            "recortes": classificacao["recortes"],
            "segredo": _segredo_nas_partes(item.get("partes")),
            "partes": leitor.partes(item.get("partes")),
        })
    return saida


# ── 7. pautas futuras ────────────────────────────────────────────────────────────────────────


def _campo(item: Mapping[str, Any], *nomes: str) -> Any:
    """O primeiro campo presente entre grafias (``dataSessão``/``dataSessao``)."""
    for nome in nomes:
        if nome in item:
            return item[nome]
    return None


def ler_pautas(
    origem: Path | str | bytes | Sequence[Mapping[str, Any]],
    regras: Regras,
    classificador: Classificador,
    *,
    leitor: LeitorSTJ | None = None,
    tipificar: Tipificador | None = None,
) -> list[dict[str, Any]]:
    """As pautas futuras (``pautas_futuras_AAAAMMDD.json.gz``) → um item por processo ou petição
    em pauta, por sessão e órgão: ``{trib, sessao, publicada, orgao, id, classe, numero, cnj,
    registro, tipo, peticao, numero_peticao, interno, relator, relator_id, fora, segredo, partes}``.

    ``tipo``: ``"principal"`` (o recurso em si) ou a sigla da petição (``AgInt``, ``AgRg``, ``EDcl``,
    ``RCD``...); ``interno``: AgInt, AgRg ou EDcl. Item em segredo (``segredoDeJustica`` S) conta
    nos números e nunca vai a lista. Ordem: sessão, órgão e a do arquivo."""
    leitor = leitor or LeitorSTJ(regras, classificador, tipificar=tipificar)
    dados = _carregar_json(origem)
    if not isinstance(dados, list):
        raise FormatoInesperado(str(origem)[:200], "a pauta não é uma lista")
    saida = []
    for item in dados:
        if not isinstance(item, Mapping):
            continue
        classe = leitor.texto(item.get("siglaClasse")) or ""
        numero = _digitos(item.get("numeroNaClasse"))
        registro = _digitos(item.get("numeroRegistro")) or None
        sigla = leitor.texto(item.get("siglaPeticao"))
        peticao = leitor.texto(item.get("tipoPeticao"))
        nome, relator_id, fora, _ = leitor.relator(item.get("Relator"))
        saida.append({
            "trib": TRIB,
            "sessao": leitor.texto(_data_iso(_campo(item, "dataSessão", "dataSessao"))),
            "publicada": leitor.texto(_data_iso(item.get("dataPublicacaoPauta"))),
            "orgao": leitor.texto(item.get("orgaoJulgador")),
            "id": _id(classe, numero, registro),
            "classe": classe,
            "numero": numero,
            "cnj": _digitos(item.get("numeroUnico")) or None,
            "registro": registro,
            "tipo": sigla or ("principal" if not peticao else leitor.texto(peticao)),
            "peticao": peticao,
            "numero_peticao": _digitos(item.get("numeroPeticao")) or None,
            "interno": sigla in RECURSOS_INTERNOS,
            "relator": nome,
            "relator_id": relator_id,
            "fora": fora,
            "segredo": _sim(_campo(item, "segredoDeJustica", "segredoJustica")),
            "partes": leitor.partes(item.get("partes")),
        })
    saida.sort(key=lambda x: (x["sessao"] or "9999", x["orgao"] or ""))
    return saida


# ── 8. precedentes qualificados ──────────────────────────────────────────────────────────────

TIPOS_PRECEDENTE = {"TEMA": "repetitivo", "TEMA REPETITIVO": "repetitivo", "CONTROVERSIA": "controversia",
                    "CONTROVERSI": "controversia", "IAC": "iac", "PUIL": "puil", "SIRDR": "sirdr"}


def tipo_do_precedente(tipo: str | None, situacao: str | None = None) -> str:
    """``tipoPrecedente`` → ``repetitivo``, ``controversia``, ``iac``, ``puil``, ``sirdr``;
    vazio com situação de relevância → ``relevancia`` (a relevância do recurso especial, EC 125/2022);
    o resto → ``outro``."""
    chave = normalizar(tipo)
    if chave in TIPOS_PRECEDENTE:
        return TIPOS_PRECEDENTE[chave]
    if chave.startswith("CONTROV"):
        return "controversia"
    if not chave and "RELEVANCIA" in normalizar(situacao):
        return "relevancia"
    return "outro"


def estado_do_tema(situacao: str | None, data_julgamento: str | None = None) -> str:
    """A situação do tema em três estados: ``pendente`` (afetado, em julgamento, sobrestado,
    admitido, relevância reconhecida: o mérito está com o relator), ``julgado`` (acórdão publicado,
    trânsito, mérito julgado, revisado) ou ``encerrado`` (cancelado, vinculado a outro tema,
    prejudicado, sem processo, suspensão indeferida ou finda). Situação vazia: pela data de
    julgamento."""
    s = normalizar(situacao)
    if any(k in s for k in ("CANCEL", "VINCULAD", "PREJUDICAD", "INDEFERID", "FINALIZAD", "DESAFET",
                            "SEM PROCESSO", "SUSPENSAO DEFERIDA")):
        return "encerrado"
    if any(k in s for k in ("TRANSITO", "ACORDAO PUBLICADO", "JULGADO", "REVISAD", "REAFIRMACAO",
                            "NAO CABIMENTO", "TESE NEGATIVA")):
        return "julgado"
    if s:
        return "pendente"
    return "julgado" if data_julgamento else "pendente"


def _ler_csv(origem: Path | str | bytes) -> list[dict[str, str]]:
    if isinstance(origem, (bytes, bytearray)):
        texto = bytes(origem).decode("utf-8-sig", errors="replace")
    else:
        texto = Path(origem).read_bytes().decode("utf-8-sig", errors="replace")
    cabecalho = texto.split("\n", 1)[0]
    delimitador = ";" if cabecalho.count(";") > cabecalho.count(",") else ","
    return [{(k or "").strip(): (v or "").strip() for k, v in linha.items()}
            for linha in csv.DictReader(io.StringIO(texto, newline=""), delimiter=delimitador)]


def data_do_conteudo(linhas: Iterable[Mapping[str, str]], campos: Sequence[str]) -> str | None:
    """A data mais recente que aparece nos ``campos`` (o arquivo de precedentes não tem data no
    nome: é assim que se escolhe entre dois recursos de mesmo nome e que se diz de quando é)."""
    melhor: str | None = None
    for linha in linhas:
        for campo in campos:
            d = _data_iso(linha.get(campo))
            if d and (melhor is None or d > melhor):
                melhor = d
    return melhor


CAMPOS_DATA_TEMAS = ("dataPrimeiraAfetacao", "dataJulgamento", "dataPublicacaoAcordao")
CAMPOS_DATA_PROCESSOS = ("dataAfetacao", "dataJulgamento", "dataPbulicacaoAcordao", "dataPublicacaoAcordao")


def ler_temas(origem: Path | str | bytes) -> list[dict[str, str]]:
    """``Temas.csv`` (vírgula, UTF-8 com BOM, textos com quebra de linha entre aspas), linha a linha."""
    return _ler_csv(origem)


def ler_processos_precedentes(origem: Path | str | bytes) -> list[dict[str, str]]:
    """``Processos.csv``: um processo por linha, ligado ao tema por ``sequencialPrecedente``."""
    return _ler_csv(origem)


def _titulo(texto: str | None) -> str | None:
    limpo = re.sub(r"\s+", " ", (texto or "").replace("\xa0", " ")).strip()
    if not limpo:
        return None
    if len(limpo) <= TITULO_MAX:
        return limpo
    corte = limpo[: TITULO_MAX - 1].rsplit(" ", 1)[0]
    return corte.rstrip(" ,;:.") + "…"


def _codigos_dos_assuntos(texto: str | None, classificador: Classificador) -> list[int]:
    """``"8826- DIREITO PROCESSUAL CIVIL..., 10945- Expurgos..."`` → códigos TPU sem repetição."""
    codigos: list[int] = []
    for bruto in re.findall(r"(?:^|[,;]\s*)(\d+)\s*-", texto or ""):
        c = classificador.tpu.normalizar_codigo(bruto)
        if c is not None and c not in codigos:
            codigos.append(c)
    return codigos


def montar_temas(
    temas: Sequence[Mapping[str, str]],
    processos: Sequence[Mapping[str, str]],
    regras: Regras,
    classificador: Classificador,
    *,
    leitor: LeitorSTJ | None = None,
) -> list[dict[str, Any]]:
    """Um item por precedente (temas repetitivos, controvérsias, IAC, PUIL, SIRDR, relevância):
    ``{trib, seq, tipo, numero, situacao, estado, pendente, orgao, desde, julgado_em, publicado_em,
    titulo, relator, relator_id, fora, relatores, processos, leading, suspensos_origem, assuntos,
    area, rg_stf, link}``.

    O ``Temas.csv`` repete o tema numa linha por tema de repercussão geral ligado: as linhas viram
    um item. O relator é o do processo-paradigma (``leadingCase`` S) no ``Processos.csv``; sem
    paradigma com relator, o mais frequente entre os processos do tema; sem nenhum, quem propôs a
    afetação. Ordem: tipo, número."""
    leitor = leitor or LeitorSTJ(regras, classificador)
    por_seq: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for linha in processos:
        por_seq[str(linha.get("sequencialPrecedente") or "").strip()].append(linha)
    juntos: dict[str, dict[str, Any]] = {}
    for linha in temas:
        seq = str(linha.get("sequencialPrecedente") or "").strip()
        if not seq:
            continue
        item = juntos.get(seq)
        rg = _digitos(linha.get("numeroRepercussaoGeralSTF"))
        if item is not None:
            if rg and int(rg) not in item["rg_stf"]:
                item["rg_stf"].append(int(rg))
            continue
        situacao = re.sub(r"\s+", " ", linha.get("situacao") or "").strip() or None
        tipo = tipo_do_precedente(linha.get("tipoPrecedente"), situacao)
        numero = _digitos(linha.get("numeroPrecedente"))
        julgado = _data_iso(linha.get("dataJulgamento"))
        estado = estado_do_tema(situacao, julgado)
        assuntos = _codigos_dos_assuntos(linha.get("Assuntos"), classificador)
        area = classificador.classificar(classe=None, principal=assuntos[0] if assuntos else None, assuntos=assuntos)["area"]
        juntos[seq] = {
            "trib": TRIB, "seq": int(seq) if seq.isdigit() else seq, "tipo": tipo,
            "numero": int(numero) if numero else None, "situacao": situacao, "estado": estado,
            "pendente": estado == "pendente", "orgao": (linha.get("orgaoJulgador") or "").strip() or None,
            "desde": _data_iso(linha.get("dataPrimeiraAfetacao")), "julgado_em": julgado,
            "publicado_em": _data_iso(linha.get("dataPublicacaoAcordao")),
            "titulo": _titulo(linha.get("questaoSubmetidaAJulgamento")),
            "assuntos": assuntos, "area": area, "rg_stf": [int(rg)] if rg else [],
            "link": LINK_TEMA.format(n=int(numero)) if tipo == "repetitivo" and numero else None,
        }
    for seq, item in juntos.items():
        linhas = por_seq.get(str(item["seq"]), [])
        paradigmas = [x for x in linhas if (x.get("leadingCase") or "").strip().upper() == "S" and (x.get("ministroRelator") or "").strip()]
        contagem = Counter((x.get("ministroRelator") or "").strip() for x in (paradigmas or linhas))
        contagem.pop("", None)
        if not contagem:
            contagem = Counter((x.get("NOME_MINISTRO_AFETACAO") or "").strip() for x in linhas)
            contagem.pop("", None)
        escolhido = min(contagem.items(), key=lambda kv: (-kv[1], kv[0]))[0] if contagem else None
        nome, relator_id, fora, _ = leitor.relator(escolhido) if escolhido else (None, None, None, False)
        todos = Counter((x.get("ministroRelator") or "").strip() for x in linhas)
        todos.pop("", None)
        registros = {(x.get("numeroRegistro") or "").strip() or (x.get("Processo") or "").strip() for x in linhas}
        registros.discard("")
        leading = sorted({_id_de_processo(x.get("Processo")) for x in linhas
                          if (x.get("leadingCase") or "").strip().upper() == "S" and x.get("Processo")} - {None})
        item.update({
            "relator": nome, "relator_id": relator_id, "fora": fora if escolhido else None,
            "relatores": [[n, q] for n, q in sorted(todos.items(), key=lambda kv: (-kv[1], kv[0]))],
            "processos": len(registros), "leading": leading,
            "suspensos_origem": sum(int(_digitos(x.get("quantidadeProcessosSuspensoNaOrigem")) or 0) for x in linhas),
        })
    ordem = {"repetitivo": 0, "iac": 1, "controversia": 2, "relevancia": 3, "puil": 4, "sirdr": 5, "outro": 6}
    saida = list(juntos.values())
    saida.sort(key=lambda t: (ordem.get(t["tipo"], 9), t["numero"] if t["numero"] is not None else 10**9, str(t["seq"])))
    # Chaves na ordem do docstring (o update acrescentou relator e afins no fim).
    campos = ("trib", "seq", "tipo", "numero", "situacao", "estado", "pendente", "orgao", "desde", "julgado_em",
              "publicado_em", "titulo", "relator", "relator_id", "fora", "relatores", "processos", "leading",
              "suspensos_origem", "assuntos", "area", "rg_stf", "link")
    return [{c: t.get(c) for c in campos} for t in saida]


def _id_de_processo(texto: str | None) -> str | None:
    """``"REsp 2101592"`` → ``stj-REsp-2101592``."""
    m = re.match(r"^\s*([A-Za-z][A-Za-z-]*)\s*([\d.]+)\s*$", texto or "")
    return f"{TRIB}-{m.group(1)}-{_digitos(m.group(2))}" if m else None


# ── 9. íntegras do Diário da Justiça (metadados) ─────────────────────────────────────────────


def _codigos_dos_caminhos(texto: str | None, classificador: Classificador) -> list[int]:
    """``"00287.03369.03372., 00287.05555."`` (caminhos TPU) → o último código de cada caminho."""
    codigos: list[int] = []
    for caminho in re.split(r"[,;]\s*", texto or ""):
        partes = [p for p in caminho.strip().split(".") if p.strip().isdigit()]
        if not partes:
            continue
        c = classificador.tpu.normalizar_codigo(partes[-1].strip().lstrip("0") or "0")
        if c is not None and c not in codigos:
            codigos.append(c)
    return codigos


def desfecho(teor: str | None) -> str | None:
    """``teor`` → id estável: ``"Não Conhecendo"`` → ``nao_conhecendo``; ``"Negando"`` → ``negando``."""
    chave = normalizar(teor)
    return re.sub(r"[^A-Z0-9]+", "_", chave).strip("_").lower() or None


def ler_integras(
    origem: Path | str | bytes | Sequence[Mapping[str, Any]],
    regras: Regras,
    classificador: Classificador,
    *,
    arquivo: str | None = None,
    leitor: LeitorSTJ | None = None,
) -> list[dict[str, Any]]:
    """Os metadados de um dia de íntegras (``metadadosAAAAMMDD.json``) → um item por documento:
    ``{trib, arquivo, data, seq, documento, id, classe, numero, registro, recurso, interno, teor,
    desfecho, relator, relator_id, fora, recebimento, distribuicao, assuntos, area, subarea}``.

    ``documento``: ``decisao`` (monocrática) ou ``acordao``; ``recurso``: a sigla do recurso interno
    julgado (AgInt, AgRg, EDcl...) ou ``None`` quando é o processo principal; ``interno``: AgInt,
    AgRg ou EDcl. O dicionário oficial diverge do arquivo (``NM_MINISTRO`` e não ``ministro``;
    datas ISO): os dois são aceitos. Processo em segredo não vem nas íntegras."""
    leitor = leitor or LeitorSTJ(regras, classificador)
    dados = _carregar_json(origem)
    if not isinstance(dados, list):
        raise FormatoInesperado(str(origem)[:200], "os metadados das íntegras não são uma lista")
    saida = []
    for item in dados:
        if not isinstance(item, Mapping):
            continue
        processo = str(item.get("processo") or "").strip()
        m = re.match(r"^([A-Za-z][A-Za-z-]*)\s+([\d.]+)$", processo)
        classe = leitor.texto(m.group(1)) if m else ""
        numero = _digitos(m.group(2)) if m else ""
        registro = _digitos(item.get("numeroRegistro")) or None
        nome, relator_id, fora, _ = leitor.relator(_campo(item, "NM_MINISTRO", "ministro", "nomeMinistro"))
        recurso = leitor.texto(item.get("recurso"))
        tipo_doc = normalizar(item.get("tipoDocumento"))
        codigos = _codigos_dos_caminhos(item.get("assuntos"), classificador)
        classificacao = classificador.classificar(classe=classe or None, principal=codigos[0] if codigos else None,
                                                  assuntos=codigos)
        teor = leitor.texto(item.get("teor"))
        saida.append({
            "trib": TRIB,
            "arquivo": arquivo,
            "data": leitor.texto(_data_iso(item.get("dataPublicacao"))),
            "seq": item.get("SeqDocumento"),
            "documento": "acordao" if tipo_doc.startswith("ACORD") else ("decisao" if tipo_doc.startswith("DECIS") else (tipo_doc.lower() or None)),
            "id": _id(classe or "", numero, registro),
            "classe": classe or None,
            "numero": numero,
            "registro": registro,
            "recurso": recurso,
            "interno": recurso in RECURSOS_INTERNOS,
            "teor": teor,
            "desfecho": desfecho(teor),
            "relator": nome,
            "relator_id": relator_id,
            "fora": fora,
            "recebimento": leitor.texto(_data_iso(item.get("dataRecebimento"))),
            "distribuicao": leitor.texto(_data_iso(_campo(item, "dataDistribuição", "dataDistribuicao"))),
            "assuntos": codigos,
            "area": classificacao["area"],
            "subarea": classificacao["subarea"],
        })
    return saida


# ── 10. a coleta (com o ColetorTribunais) ────────────────────────────────────────────────────


def _cfg(regras: Regras | None) -> Mapping[str, Any]:
    fontes = getattr(regras, "fontes", None) or {}
    return fontes.get(TRIB) or {}


def _validade(regras: Regras | None, chave: str, padrao: float) -> float:
    return float((_cfg(regras).get("validade_h") or {}).get(chave, padrao))


def url_do_conjunto(regras: Regras | None, chave: str) -> str:
    """A página HTML do conjunto (``fontes.stj.pagina_conjunto`` com ``conjuntos.<chave>``)."""
    cfg = _cfg(regras)
    slug = (cfg.get("conjuntos") or {}).get(chave) or CONJUNTOS[chave]
    return str(cfg.get("pagina_conjunto") or PAGINA_CONJUNTO).format(slug=slug)


def recursos_do_conjunto(coletor: Any, regras: Regras | None, chave: str) -> list[Recurso]:
    """Lê a página do conjunto (cache de ``validade_h.pagina`` horas) e devolve os links de
    download. Página sem link nenhum: :class:`FormatoInesperado` (o layout mudou)."""
    url = url_do_conjunto(regras, chave)
    html = coletor.texto(url, validade_h=_validade(regras, "pagina", 6.0))
    recursos = links_da_pagina(html, base=str(_cfg(regras).get("portal") or PORTAL))
    if not recursos:
        raise FormatoInesperado(url, "a página do conjunto não traz nenhum link de download (o layout mudou?)")
    return recursos


@dataclass
class Baixado:
    """Um arquivo baixado (ou do cache): o recurso da página e o caminho no cache."""

    recurso: Recurso
    caminho: Path

    @property
    def data(self) -> str | None:
        return self.recurso.data


@dataclass
class Serie:
    """Uma série de arquivos diários (atas, íntegras): o que está no disco, o que ficou para a
    próxima execução e o erro que parou a série, se houve. ``data_dados``: o dia mais recente baixado."""

    baixados: list[Baixado] = field(default_factory=list)
    pendentes: list[Recurso] = field(default_factory=list)
    erro: FetchError | None = None
    novos: int = 0  # baixados nesta execução (o resto veio do cache)
    ultimo_publicado: str | None = None  # o dia do arquivo mais recente da página

    @property
    def data_dados(self) -> str | None:
        return max((b.data for b in self.baixados if b.data), default=None)


def _sufixo(url: str) -> str:
    return "".join(Path(urlsplit(url).path).suffixes[-2:]) or ".bin"


def _no_cache(coletor: Any, url: str, validade_h: float) -> bool:
    """O arquivo já está no cache do coletor (então pedi-lo não sai para a rede)?"""
    caminho_de = getattr(coletor, "_caminho", None)
    valido = getattr(coletor, "_valido", None)
    if caminho_de is None or valido is None:
        return False
    return bool(valido(caminho_de("arquivos", url, _sufixo(url)), validade_h))


def baixar_acervo(coletor: Any, regras: Regras | None, *, recursos: Sequence[Recurso] | None = None) -> Baixado:
    """O acervo do dia mais recente publicado, **em fluxo** para o cache (82 MB com gzip)."""
    recursos = recursos if recursos is not None else recursos_do_conjunto(coletor, regras, "acervo")
    escolhido = escolher_mais_recente(recursos, "acervo")
    if escolhido is None:
        raise FormatoInesperado(url_do_conjunto(regras, "acervo"), "a página do acervo não traz processos_tramitando_AAAAMMDD.json.gz")
    return Baixado(escolhido, coletor.arquivo_grande(escolhido.url, validade_h=VALIDADE_IMUTAVEL_H))


def baixar_pautas(coletor: Any, regras: Regras | None, *, recursos: Sequence[Recurso] | None = None) -> Baixado:
    """As pautas futuras do dia mais recente publicado."""
    recursos = recursos if recursos is not None else recursos_do_conjunto(coletor, regras, "pautas")
    escolhido = escolher_mais_recente(recursos, "pautas")
    if escolhido is None:
        raise FormatoInesperado(url_do_conjunto(regras, "pautas"), "a página das pautas não traz pautas_futuras_AAAAMMDD.json.gz")
    return Baixado(escolhido, coletor.arquivo(escolhido.url, validade_h=VALIDADE_IMUTAVEL_H))


def _baixar_serie(
    coletor: Any,
    regras: Regras | None,
    chave: str,
    *,
    dias: int,
    ate: str | date | None,
    maximo: int | None,
    prazo: float | None,
    recursos: Sequence[Recurso] | None,
) -> Serie:
    recursos = recursos if recursos is not None else recursos_do_conjunto(coletor, regras, chave)
    todos = por_dia(recursos, chave)
    serie = Serie(ultimo_publicado=max(todos) if todos else None)
    escolhidos = escolher_ultimos_dias(recursos, chave, dias, ate)
    relogio = getattr(coletor, "_relogio", time.time)
    for r in sorted(escolhidos, key=lambda x: x.data or "", reverse=True):  # do mais novo para o mais velho
        em_cache = _no_cache(coletor, r.url, VALIDADE_IMUTAVEL_H)
        if not em_cache and (serie.erro is not None or (maximo is not None and serie.novos >= maximo)
                             or (prazo is not None and relogio() >= prazo)):
            serie.pendentes.append(r)  # depois de um erro, só o que já está no cache
            continue
        try:
            caminho = coletor.arquivo(r.url, validade_h=VALIDADE_IMUTAVEL_H)
        except FetchError as exc:
            log.warning("Tribunais: STJ %s: %s parou a série (%s)", chave, r.arquivo, exc)
            serie.erro = exc
            serie.pendentes.append(r)
            continue
        serie.baixados.append(Baixado(r, caminho))
        if not em_cache:
            serie.novos += 1
    serie.baixados.sort(key=lambda b: b.data or "")
    serie.pendentes.sort(key=lambda x: x.data or "")
    return serie


def baixar_atas(
    coletor: Any,
    regras: Regras | None,
    *,
    dias: int | None = None,
    ate: str | date | None = None,
    maximo: int | None = None,
    prazo: float | None = None,
    recursos: Sequence[Recurso] | None = None,
) -> Serie:
    """As atas dos últimos ``dias`` dias (padrão: ``limiares.janelas.entradas_dias``, 365) até
    ``ate`` (padrão: a ata mais recente). Cada ata tem URL própria e não muda: o que já está no
    cache não é pedido de novo. ``maximo``: quantas baixar de verdade nesta execução (das mais novas
    para as mais velhas; o resto fica em ``pendentes`` para a próxima); ``prazo``: instante (relógio
    do coletor) depois do qual não se baixa mais. Um erro para a série e vai em ``Serie.erro``."""
    if dias is None:
        dias = int(((getattr(regras, "limiares", None) or {}).get("janelas") or {}).get("entradas_dias", 365))
    return _baixar_serie(coletor, regras, "atas", dias=dias, ate=ate, maximo=maximo, prazo=prazo, recursos=recursos)


def baixar_integras(
    coletor: Any,
    regras: Regras | None,
    *,
    dias: int | None = None,
    maximo: int | None = None,
    prazo: float | None = None,
    recursos: Sequence[Recurso] | None = None,
) -> Serie:
    """Os metadados das íntegras dos últimos ``dias`` dias **até o último arquivo publicado** (o STJ
    publica com semanas de atraso; padrão: ``limiares.janelas.decisoes_dias``, 365). Só os
    ``metadadosAAAAMMDD.json`` (os textos em ZIP não são baixados). ``Serie.ultimo_publicado`` é o
    dia do arquivo mais recente: com :func:`atraso_dias`, o atraso que a página diz."""
    if dias is None:
        dias = int(((getattr(regras, "limiares", None) or {}).get("janelas") or {}).get("decisoes_dias", 365))
    return _baixar_serie(coletor, regras, "integras", dias=dias, ate=None, maximo=maximo, prazo=prazo, recursos=recursos)


@dataclass
class Precedentes:
    """Os dois arquivos de precedentes escolhidos e a data do conteúdo (a fonte não declara outra)."""

    temas: Path
    processos: Path
    data_dados: str | None
    candidatos: dict[str, int]  # quantos recursos de mesmo nome a página trazia


def baixar_precedentes(coletor: Any, regras: Regras | None, *, recursos: Sequence[Recurso] | None = None) -> Precedentes:
    """``Temas.csv`` e ``Processos.csv``. A página traz às vezes dois recursos de mesmo nome (em
    22/11/2026: o de 2022, atualizado todo dia, e uma cópia de 13/11 parada); como o nome não tem
    data, baixa os candidatos e fica com o de **conteúdo mais recente** (a maior data de afetação,
    julgamento ou publicação). O arquivo muda todo dia na mesma URL: cache de
    ``validade_h.precedentes`` horas."""
    recursos = recursos if recursos is not None else recursos_do_conjunto(coletor, regras, "precedentes")
    validade = _validade(regras, "precedentes", 20.0)
    escolhidos: dict[str, tuple[Path, str | None]] = {}
    candidatos: dict[str, int] = {}
    for nome, campos in (("temas.csv", CAMPOS_DATA_TEMAS), ("processos.csv", CAMPOS_DATA_PROCESSOS)):
        lista = por_nome(recursos, nome)
        candidatos[nome] = len(lista)
        if not lista:
            raise FormatoInesperado(url_do_conjunto(regras, "precedentes"), f"a página dos precedentes não traz {nome}")
        melhor: tuple[str, int, Path] | None = None
        for r in lista:
            caminho = coletor.arquivo(r.url, validade_h=validade)
            data = data_do_conteudo(_ler_csv(caminho), campos) if len(lista) > 1 else None
            chave = (data or "", r.posicao, caminho)
            if melhor is None or chave[:2] > melhor[:2]:
                melhor = chave
        assert melhor is not None
        escolhidos[nome] = (melhor[2], melhor[0] or None)
    temas_path, data_temas = escolhidos["temas.csv"]
    if data_temas is None:
        data_temas = data_do_conteudo(_ler_csv(temas_path), CAMPOS_DATA_TEMAS)
    return Precedentes(temas=temas_path, processos=escolhidos["processos.csv"][0], data_dados=data_temas,
                       candidatos=candidatos)
