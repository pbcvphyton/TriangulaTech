"""As Tabelas Processuais Unificadas do CNJ (TPU): a árvore de assuntos e as classes.

A TPU (Res. CNJ 46/2007) é a régua comum dos tribunais: cada assunto tem um código e um pai, e a
raiz da árvore é o ramo do direito (DIREITO CIVIL, DIREITO PENAL...). A aba usa a árvore para
dizer a área de cada processo (``areas.py``) e o nome de cada código.

A cópia compacta fica versionada em ``data/tribunais/tpu/`` (três arquivos, ~330 KB):

- ``assuntos.json``: um item por linha, ``[codigo, pai, "nome"]`` (com ``0`` no fim se inativo:
  processos antigos ainda trazem códigos inativos, 1,1% das ocorrências no DataJud do STJ);
- ``classes.json``: ``{"itens": [[codigo, pai, "nome", "sigla", flags]], "siglas": {...}}``,
  ``flags`` = 1 (STF) + 2 (STJ) + 4 (inativa); ``siglas`` = a sigla usual de cada tribunal;
- ``versao.json``: a versão do CNJ e a data.

**Atualização**: o CNJ publica a TPU inteira num dump SQL (Oracle/Postgres) no SGT, uma versão a cada
1 a 3 meses (versão 85 em 05/11/2026). :func:`ultima_versao` pergunta a data ao serviço SOAP
``getDataUltimaVersao``; :func:`atualizar` baixa o dump só quando a data mudou e
:func:`atualizar_de_dump` refaz os três arquivos. Sem rede ou com o SGT fora do ar, vale a cópia
versionada (o serviço está depreciado: o CNJ anuncia ``sgt.cloud.cnj.jus.br``).

**Casamento de caminho em texto** (:meth:`TPU.casar_caminho`): o STF manda o assunto em texto,
``"1 - DIREITO PROCESSUAL PENAL || MEDIDAS ASSECURATÓRIAS || BUSCA E APREENSÃO DE BENS"``. O
caminho é casado com a árvore nível a nível, pelo nome normalizado (sem acento, maiúsculas, sem
aspas, entidades HTML desfeitas, espaços colapsados), e o resultado é o nó mais profundo casado e
quantos níveis casaram. No acervo do STF de 22/11/2026, 98,8% das linhas de assunto casaram até a
folha (ver o teste e o relatório da fundação).
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .modelo import normalizar

log = logging.getLogger(__name__)

PASTA = Path(__file__).resolve().parents[2] / "data" / "tribunais" / "tpu"
SEPARADOR = " › "  # o caminho exibido: "DIREITO CIVIL › Família › Alimentos"

# Bits de "flags" em classes.json.
STF, STJ, INATIVA = 1, 2, 4

# A sigla que o tribunal usa quando difere da sigla da TPU (ou quando a TPU tem duas classes com a
# mesma sigla). STJ: tirada do próprio acervo de 20/11/2026 (siglaClasse × codigoClasseCNJ, 352.239
# processos); STF: a planilha do acervo de 22/11/2026 (ACO é "AOC" na TPU). Nas demais, a sigla da
# TPU entre as classes do tribunal, preferindo a classe sob a raiz própria do tribunal.
SIGLAS_USUAIS: dict[str, dict[str, int]] = {
    "stj": {
        "AREsp": 11881, "RHC": 1722, "EAREsp": 11956, "RMS": 1721, "HDE": 1026, "EREsp": 1137, "CR": 1047,
        "PUIL": 11791, "APn": 1033, "TP": 12133, "Ag": 1044, "ET": 327, "AlienBac": 1717, "PBAC": 309,
        "Sd": 1028, "MPUMP": 1268, "EAR": 1066, "EAg": 1016, "Ag/RE": 1045, "SE": 1026, "REsp": 1032,
        "HC": 1720, "CC": 1054, "Rcl": 1030, "AR": 1669, "MS": 1029, "Pet": 1057, "Inq": 1042, "SS": 1679,
        "MI": 1675, "HD": 1049, "MC": 1062, "IF": 1674, "SEC": 1027, "RvCr": 1678, "SLS": 1036, "RO": 1031,
        "EmbAc": 1715, "PePrPr": 313, "InsanAc": 333,  # classes criminais de 1º grau sem a marca STJ na TPU
    },
    "stf": {"ACO": 1315, "RE": 1348, "Rcl": 1342},
}
# As classes próprias de cada tribunal ficam sob uma raiz da árvore de classes ("SUPREMO TRIBUNAL
# FEDERAL" 1310, "SUPERIOR TRIBUNAL DE JUSTIÇA" 5): numa sigla repetida (RE 212 × 1348; REsp 213 ×
# 1032), vence a classe sob a raiz do tribunal.
RAIZES_PROPRIAS = {"stf": 1310, "stj": 5}

# Prefixo das linhas de assunto do STF ("1 - ", "2 - ").
_NUMERACAO = re.compile(r"^\s*\d+\s*-\s*")


def chave(nome: str | None) -> str:
    """A forma de comparação de um nome da árvore: :func:`modelo.normalizar` mais os espaços em
    volta de ``/`` e ``-`` tirados e o ponto final ignorado (``"ICMS/ Imposto"`` = ``"ICMS/IMPOSTO"``)."""
    s = normalizar(nome)
    s = re.sub(r"\s*([/\-–])\s*", r"\1", s)
    return s.rstrip(" .")


@dataclass(frozen=True)
class Casamento:
    """O resultado de :meth:`TPU.casar_caminho`."""

    codigo: int | None  # o nó mais profundo casado (None: nem a raiz casou)
    profundidade: int  # níveis casados a partir da raiz
    niveis: int  # níveis do texto
    texto: str  # o caminho sem a numeração

    @property
    def folha(self) -> bool:
        return self.niveis > 0 and self.profundidade == self.niveis


class TPU:
    """A árvore de assuntos e as classes, carregadas da cópia compacta."""

    def __init__(
        self,
        assuntos: dict[int, tuple[int | None, str, bool]],
        classes: dict[int, tuple[int | None, str, str | None, int]],
        siglas: dict[str, dict[str, int]] | None = None,
        versao: dict[str, Any] | None = None,
    ) -> None:
        self._assuntos = assuntos  # codigo -> (pai, nome, ativo)
        self._classes = classes  # codigo -> (pai, nome, sigla, flags)
        self.versao = dict(versao or {})
        self._filhos: dict[int | None, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        for codigo in sorted(assuntos, key=lambda c: (not assuntos[c][2], c)):  # ativos primeiro
            pai, nome, _ativo = assuntos[codigo]
            self._filhos[pai][chave(nome)].append(codigo)
        self._cadeias: dict[int, tuple[int, ...]] = {}
        self._siglas = {t: dict(v) for t, v in (siglas or _siglas_padrao(classes)).items()}

    # ── carregar ─────────────────────────────────────────────────────────────────────────────

    @classmethod
    def carregar(cls, pasta: Path | str | None = None) -> TPU:
        """Lê ``assuntos.json``, ``classes.json`` e ``versao.json`` de ``pasta`` (padrão:
        ``data/tribunais/tpu/`` do repositório)."""
        pasta = Path(pasta) if pasta is not None else PASTA
        assuntos = {}
        for item in json.loads((pasta / "assuntos.json").read_text(encoding="utf-8")):
            codigo, pai, nome = int(item[0]), item[1], str(item[2])
            assuntos[codigo] = (int(pai) if pai is not None else None, nome, not (len(item) > 3 and item[3] == 0))
        bruto = json.loads((pasta / "classes.json").read_text(encoding="utf-8"))
        classes = {
            int(c): (int(p) if p is not None else None, str(n), s or None, int(f)) for c, p, n, s, f in bruto["itens"]
        }
        try:
            versao = json.loads((pasta / "versao.json").read_text(encoding="utf-8"))
        except OSError:
            versao = {}
        return cls(assuntos, classes, bruto.get("siglas"), versao)

    # ── assuntos ─────────────────────────────────────────────────────────────────────────────

    def __contains__(self, codigo: object) -> bool:
        return codigo in self._assuntos

    def __len__(self) -> int:
        return len(self._assuntos)

    def normalizar_codigo(self, valor: Any) -> int | None:
        """Um código de assunto como as fontes escrevem → código da TPU, ou ``None``.

        ``"10288"`` → 10288; ``" 910288 "`` → 10288 (o STJ repete códigos com o prefixo 9 na lista:
        vale o código sem o 9 quando o inteiro não existe); ``"Nao Informado"``, ``None``, ``""`` e
        código que não existe na TPU → ``None``."""
        if isinstance(valor, bool) or valor is None:
            return None
        texto = str(valor).strip()
        if not texto.isdigit():
            return None
        codigo = int(texto)
        if codigo in self._assuntos:
            return codigo
        if texto.startswith("9") and len(texto) > 1 and int(texto[1:]) in self._assuntos:
            return int(texto[1:])
        return None

    def nome(self, codigo: int | None) -> str | None:
        item = self._assuntos.get(codigo) if codigo is not None else None
        return item[1] if item else None

    def pai(self, codigo: int | None) -> int | None:
        item = self._assuntos.get(codigo) if codigo is not None else None
        return item[0] if item else None

    def ativo(self, codigo: int | None) -> bool:
        item = self._assuntos.get(codigo) if codigo is not None else None
        return bool(item and item[2])

    def cadeia(self, codigo: int | None) -> tuple[int, ...]:
        """Da raiz até o código (``5779`` → ``(899, 5626, 5779)``); vazia se o código não existe."""
        if codigo is None or codigo not in self._assuntos:
            return ()
        guardada = self._cadeias.get(codigo)
        if guardada is not None:
            return guardada
        cadeia: list[int] = []
        atual: int | None = codigo
        while atual is not None and atual in self._assuntos and atual not in cadeia:
            cadeia.append(atual)
            atual = self._assuntos[atual][0]
        resultado = tuple(reversed(cadeia))
        self._cadeias[codigo] = resultado
        return resultado

    def raiz(self, codigo: int | None) -> int | None:
        cadeia = self.cadeia(codigo)
        return cadeia[0] if cadeia else None

    def nivel(self, codigo: int | None) -> int:
        return len(self.cadeia(codigo))

    def caminho(self, codigo: int | None, separador: str = SEPARADOR) -> str | None:
        cadeia = self.cadeia(codigo)
        return separador.join(self._assuntos[c][1] for c in cadeia) if cadeia else None

    def raizes(self) -> list[int]:
        return sorted(c for c, (pai, _n, _a) in self._assuntos.items() if pai is None)

    def raiz_por_nome(self, nome: str | None) -> int | None:
        """A raiz da árvore pelo nome do ramo (``"DIREITO TRIBUTÁRIO"`` → 14); ``None`` se não há."""
        candidatos = self._filhos[None].get(chave(nome)) if nome else None
        return candidatos[0] if candidatos else None

    def casar_caminho(self, texto: str | None) -> Casamento:
        """Casa um caminho em texto (``"1 - RAIZ || NÍVEL 2 || NÍVEL 3"``) com a árvore.

        Desce nível a nível pelo nome normalizado; quando um nome tem homônimos sob o mesmo pai
        (um ativo e um inativo), tenta todos e fica com o que desce mais (o ativo no empate)."""
        bruto = _NUMERACAO.sub("", (texto or "").strip())
        partes = [p.strip() for p in re.split(r"\s*\|\|\s*", bruto)] if bruto else []
        if len(partes) == 1 and " | " in partes[0]:
            partes = [p.strip() for p in partes[0].split(" | ")]
        partes = [p for p in partes if p]
        if not partes:
            return Casamento(None, 0, 0, bruto)
        chaves = [chave(p) for p in partes]
        codigo, profundidade = self._descer(None, chaves, 0)
        return Casamento(codigo, profundidade, len(partes), bruto)

    def _descer(self, pai: int | None, chaves: list[str], i: int) -> tuple[int | None, int]:
        if i >= len(chaves):
            return pai, i
        melhor: tuple[int | None, int] = (pai, i)
        for candidato in self._filhos.get(pai, {}).get(chaves[i], ()):
            achado = self._descer(candidato, chaves, i + 1)
            if achado[1] > melhor[1]:
                melhor = achado
            if melhor[1] == len(chaves):
                break
        return melhor

    def linhas_de_assunto(self, texto: str | None) -> list[str]:
        """As linhas da coluna "Assuntos" do STF (separadas por ``\\r``, ``\\r\\n`` ou ``\\n``)."""
        return [linha.strip() for linha in re.split(r"\r\n|\r|\n", texto or "") if linha.strip()]

    # ── classes ──────────────────────────────────────────────────────────────────────────────

    def classe(self, codigo: int | None) -> dict[str, Any] | None:
        item = self._classes.get(codigo) if codigo is not None else None
        if item is None:
            return None
        pai, nome, sigla, flags = item
        return {"codigo": codigo, "nome": nome, "sigla": sigla, "pai": pai, "stf": bool(flags & STF),
                "stj": bool(flags & STJ), "ativa": not flags & INATIVA}

    def classe_por_sigla(self, trib: str, sigla: str | None) -> dict[str, Any] | None:
        """A classe pela sigla que o tribunal usa (``("stj", "AREsp")`` → 11881, Agravo em Recurso
        Especial; ``("stf", "ACO")`` → 1315, Ação Cível Originária)."""
        if not sigla:
            return None
        codigo = self._siglas.get(trib, {}).get(sigla.strip())
        return self.classe(codigo)

    def nome_classe(self, trib: str, sigla: str | None) -> str | None:
        achada = self.classe_por_sigla(trib, sigla)
        return achada["nome"] if achada else None

    @property
    def siglas(self) -> dict[str, dict[str, int]]:
        return {t: dict(v) for t, v in self._siglas.items()}


def _siglas_padrao(classes: dict[int, tuple[int | None, str, str | None, int]]) -> dict[str, dict[str, int]]:
    """Sigla usual → código: as exceções de :data:`SIGLAS_USUAIS` e, nas demais, a sigla da TPU
    entre as classes do tribunal (no STF, a classe sob a raiz própria do STF vence a genérica)."""
    def raiz(codigo: int) -> int:
        vistos = set()
        while classes.get(codigo) and classes[codigo][0] is not None and codigo not in vistos:
            vistos.add(codigo)
            codigo = classes[codigo][0]  # type: ignore[assignment]
        return codigo

    saida: dict[str, dict[str, int]] = {"stf": {}, "stj": {}}
    for codigo in sorted(classes):
        _pai, _nome, sigla, flags = classes[codigo]
        if not sigla:
            continue
        for trib, bit in (("stf", STF), ("stj", STJ)):
            if not flags & bit:
                continue
            atual = saida[trib].get(sigla)
            propria = RAIZES_PROPRIAS[trib]
            if atual is None or (raiz(codigo) == propria and raiz(atual) != propria):
                saida[trib][sigla] = codigo
    for trib, tabela in SIGLAS_USUAIS.items():
        for sigla, codigo in tabela.items():
            if codigo in classes:
                saida[trib][sigla] = codigo
    return {t: dict(sorted(v.items())) for t, v in saida.items()}


# ── o dump do SGT ────────────────────────────────────────────────────────────────────────────

# Literal SQL do dump: aspas dobradas ('') e, por um vício herdado do MySQL, escapes com barra (\' \").
_LITERAL = re.compile(r"'((?:[^'\\]|\\.|'')*)'", re.DOTALL)
_INSERT = re.compile(r"INSERT INTO ([A-Za-z_]+) \(([^)]*)\) VALUES ")
_PALAVRA = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_NUMERO = re.compile(r"-?\d+(?:\.\d+)?")
_ATRIBUICAO = re.compile(r"textoGrande := ")
TABELAS = ("ITENS", "ASSUNTOS", "CLASSES")  # só estas interessam à aba


def _desfazer(literal: str) -> str:
    return re.sub(r"\\(.)", r"\1", literal.replace("''", "'"), flags=re.DOTALL)


def _valores(s: str, i: int, variaveis: dict[str, str]) -> tuple[list[Any], int]:
    """Lê ``(v1, v2, ...)`` a partir de ``s[i] == "("``."""
    if s[i] != "(":
        raise ValueError(f"esperava '(' na posição {i}: {s[i:i + 40]!r}")
    i += 1
    valores: list[Any] = []
    while True:
        while s[i] in " \t\r\n,":
            i += 1
        c = s[i]
        if c == ")":
            return valores, i + 1
        if c == "'":
            m = _LITERAL.match(s, i)
            if not m:
                raise ValueError(f"literal sem fim na posição {i}")
            valores.append(_desfazer(m.group(1)))
            i = m.end()
            continue
        m = _PALAVRA.match(s, i)
        if m:
            palavra = m.group(0)
            i = m.end()
            if palavra.upper() == "NULL":
                valores.append(None)
            elif palavra == "textoGrande":
                valores.append(variaveis.get("textoGrande"))
            elif palavra.lower() == "to_date":
                interno, i = _valores(s, i, variaveis)
                valores.append(interno[0])
            else:
                raise ValueError(f"palavra inesperada {palavra!r} na posição {i}")
            continue
        m = _NUMERO.match(s, i)
        if m:
            valores.append(m.group(0))
            i = m.end()
            continue
        raise ValueError(f"valor inesperado na posição {i}: {s[i:i + 40]!r}")


def ler_dump(caminho: Path | str, tabelas: Iterable[str] | None = TABELAS) -> dict[str, list[dict[str, Any]]]:
    """As tabelas do dump oficial do SGT (Oracle/Postgres) como listas de dicionários.

    O arquivo vem em ISO-8859-1; os textos longos chegam por uma variável (``textoGrande :=
    '...';``) usada no ``INSERT`` seguinte; datas como ``to_date('dd/mm/aaaa', ...)``; o bloco
    termina em ``END;``. ``tabelas=None`` devolve todas."""
    s = Path(caminho).read_bytes().decode("latin-1")
    queridas = set(tabelas) if tabelas is not None else None
    variaveis: dict[str, str] = {}
    saida: dict[str, list[dict[str, Any]]] = defaultdict(list)
    i, n = 0, len(s)
    while i < n:
        while i < n and s[i] in " \t\r\n":
            i += 1
        if i >= n or s.startswith("END;", i):
            break
        if s.startswith("--", i):
            fim = s.find("\n", i)
            i = n if fim < 0 else fim + 1
            continue
        m = _ATRIBUICAO.match(s, i)
        if m:
            literal = _LITERAL.match(s, m.end())
            if not literal:
                raise ValueError(f"textoGrande sem literal na posição {i}")
            variaveis["textoGrande"] = _desfazer(literal.group(1))
            i = literal.end()
            while s[i] in " \t":
                i += 1
            if s[i] != ";":
                raise ValueError(f"esperava ';' na posição {i}")
            i += 1
            continue
        m = _INSERT.match(s, i)
        if not m:
            raise ValueError(f"instrução inesperada na posição {i}: {s[i:i + 80]!r}")
        tabela = m.group(1)
        colunas = [c.strip() for c in m.group(2).split(",")]
        valores, i = _valores(s, m.end(), variaveis)
        if len(valores) != len(colunas):
            raise ValueError(f"{tabela}: {len(valores)} valores para {len(colunas)} colunas na posição {i}")
        while s[i] in " \t":
            i += 1
        if s[i] != ";":
            raise ValueError(f"esperava ';' na posição {i}")
        i += 1
        if queridas is None or tabela in queridas:
            saida[tabela].append(dict(zip(colunas, valores)))
    return dict(saida)


def _sn(valor: Any) -> bool:
    return str(valor or "").strip().upper() == "S"


def _nome(valor: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(valor or ""))).strip()


def compactar(tabelas: dict[str, list[dict[str, Any]]]) -> tuple[list[list[Any]], dict[str, Any]]:
    """Das tabelas do dump à forma compacta: (itens de ``assuntos.json``, conteúdo de
    ``classes.json``)."""
    itens = tabelas.get("ITENS") or []
    assuntos = []
    for r in itens:
        if r.get("tipo_item") != "A":
            continue
        pai = r.get("cod_item_pai")
        item: list[Any] = [int(r["cod_item"]), int(pai) if pai not in (None, "") else None, _nome(r.get("nome"))]
        if (r.get("situacao") or "A").strip().upper() != "A":
            item.append(0)
        assuntos.append(item)
    assuntos.sort(key=lambda x: x[0])
    detalhes = {int(r["cod_classe"]): r for r in tabelas.get("CLASSES") or []}
    classes = []
    for r in itens:
        if r.get("tipo_item") != "C":
            continue
        codigo = int(r["cod_item"])
        d = detalhes.get(codigo, {})
        flags = (STF if _sn(d.get("stf")) else 0) | (STJ if _sn(d.get("stj")) else 0)
        if (r.get("situacao") or "A").strip().upper() != "A":
            flags |= INATIVA
        pai = r.get("cod_item_pai")
        classes.append([codigo, int(pai) if pai not in (None, "") else None, _nome(r.get("nome")),
                        (d.get("sigla") or "").strip() or None, flags])
    classes.sort(key=lambda x: x[0])
    por_codigo = {c[0]: (c[1], c[2], c[3], c[4]) for c in classes}
    return assuntos, {"itens": classes, "siglas": _siglas_padrao(por_codigo)}


def _versao_do_dump(tabelas: dict[str, list[dict[str, Any]]]) -> tuple[int | None, str | None]:
    """O maior ``num_versao_lancado`` e a maior ``dat_versao`` (o dia) dos itens."""
    versoes = [int(r["num_versao_lancado"]) for r in tabelas.get("ITENS") or [] if str(r.get("num_versao_lancado") or "").isdigit()]
    datas = [str(r["dat_versao"])[:10] for r in tabelas.get("ITENS") or [] if r.get("dat_versao")]
    return (max(versoes) if versoes else None), (max(datas) if datas else None)


def _por_linha(itens: list[list[Any]]) -> str:
    """Lista JSON com um item por linha (o diff do commit fica pequeno)."""
    corpo = ",\n".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) for x in itens)
    return "[\n" + corpo + "\n]\n"


def _gravar_texto(caminho: Path, conteudo: str) -> bool:
    """Grava se mudou (temporário + ``os.replace``). Devolve se gravou."""
    try:
        if caminho.read_text(encoding="utf-8") == conteudo:
            return False
    except OSError:
        pass
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=caminho.parent, prefix=f".{caminho.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(conteudo)
        os.chmod(tmp, 0o644)
        os.replace(tmp, caminho)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


def atualizar_de_dump(
    dump: Path | str, pasta: Path | str | None = None, *, data_versao: str | None = None, fonte: str | None = None
) -> dict[str, Any]:
    """Refaz ``assuntos.json``, ``classes.json`` e ``versao.json`` em ``pasta`` a partir do dump
    do SGT. Devolve o conteúdo de ``versao.json`` mais ``gravados`` (quantos arquivos mudaram).

    ``data_versao`` (``AAAA-MM-DD``): a data que o CNJ declara (SOAP ``getDataUltimaVersao``); sem
    ela, a maior ``dat_versao`` dos itens."""
    pasta = Path(pasta) if pasta is not None else PASTA
    tabelas = ler_dump(dump)
    if not tabelas.get("ITENS"):
        raise ValueError(f"dump do SGT sem a tabela ITENS: {dump}")
    assuntos, classes = compactar(tabelas)
    numero, data_itens = _versao_do_dump(tabelas)
    versao = {
        "versao": numero,
        "data": data_versao or data_itens,
        "fonte": fonte or "https://www.cnj.jus.br/sgt/versoes.php",
        "assuntos": len(assuntos),
        "assuntos_ativos": sum(1 for a in assuntos if len(a) == 3),
        "classes": len(classes["itens"]),
        "gerado_de": (re.search(r"url=([^&]+)", fonte or "") or re.search(r"(.+)", Path(dump).name)).group(1),
    }
    gravados = 0
    gravados += _gravar_texto(pasta / "assuntos.json", _por_linha(assuntos))
    gravados += _gravar_texto(
        pasta / "classes.json",
        '{"itens":' + _por_linha(classes["itens"]).rstrip("\n") + ',\n"siglas":'
        + json.dumps(classes["siglas"], ensure_ascii=False, separators=(",", ":")) + "}\n",
    )
    gravados += _gravar_texto(pasta / "versao.json", json.dumps(versao, ensure_ascii=False, indent=1) + "\n")
    log.info("Tribunais: TPU versão %s (%s): %d assuntos, %d classes, %d arquivo(s) gravado(s)",
             numero, versao["data"], len(assuntos), len(classes["itens"]), gravados)
    return {**versao, "gravados": gravados}


# ── a versão publicada pelo CNJ ──────────────────────────────────────────────────────────────

SOAP_ACAO = "https://www.cnj.jus.br/sgt/sgt_ws.php#getDataUltimaVersao"
SOAP_CORPO = (
    '<?xml version="1.0" encoding="UTF-8"?><soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:sgt="https://www.cnj.jus.br/sgt/sgt_ws.php"><soapenv:Body><sgt:getDataUltimaVersao/></soapenv:Body></soapenv:Envelope>'
)


def data_da_resposta_soap(xml: str) -> str | None:
    """``<return>05/11/2026</return>`` → ``"2026-11-05"``."""
    m = re.search(r"<return>\s*(\d{2})/(\d{2})/(\d{4})\s*</return>", xml or "")
    if not m:
        return None
    dia, mes, ano = m.groups()
    try:
        return date(int(ano), int(mes), int(dia)).isoformat()
    except ValueError:
        return None


def dump_da_pagina(pagina: str) -> tuple[int, str | None] | None:
    """Da página de versões do SGT (``versoes.php``): (número da versão, data) do dump
    Oracle/Postgres (``value="85_dump_dados_oracle_postgres.sql"``)."""
    m = re.search(
        r"(\d{2})/(\d{2})/(\d{4})\s*</td>\s*<td>\s*<input[^>]*value=\"(\d+)_dump_dados_oracle_postgres\.sql\"", pagina or "", re.DOTALL
    )
    if m:
        dia, mes, ano, numero = m.groups()
        return int(numero), f"{ano}-{mes}-{dia}"
    m = re.search(r"value=\"(\d+)_dump_dados_oracle_postgres\.sql\"", pagina or "")
    return (int(m.group(1)), None) if m else None


def ultima_versao(coletor: Any, url: str = "https://www.cnj.jus.br/sgt/sgt_ws.php") -> str | None:
    """A data da última versão da TPU que o CNJ declara (SOAP ``getDataUltimaVersao``), pelo
    coletor dos Tribunais (robots.txt, pausa e cache valem). ``None`` se a resposta não tem data."""
    resposta = coletor.consulta(
        url, SOAP_CORPO.encode("utf-8"), validade_h=6.0,
        cabecalhos={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{SOAP_ACAO}"'}, formato="texto",
    )
    return data_da_resposta_soap(resposta)


def atualizar(coletor: Any, regras: Any, pasta: Path | str | None = None) -> dict[str, Any]:
    """Confere a versão publicada e, se mudou, baixa o dump e refaz a cópia compacta.

    Devolve ``{"mudou": bool, "versao": {...}}``. Erros de rede sobem (quem chama vira aviso e
    mantém a cópia versionada)."""
    pasta = Path(pasta) if pasta is not None else PASTA
    try:
        atual = json.loads((pasta / "versao.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        atual = {}
    fontes_tpu = (regras.fontes.get("tpu") or {}) if regras is not None else {}
    publicada = ultima_versao(coletor, fontes_tpu.get("versao_soap") or "https://www.cnj.jus.br/sgt/sgt_ws.php")
    if publicada and publicada == atual.get("data"):
        return {"mudou": False, "versao": atual}
    pagina = coletor.texto(fontes_tpu.get("versoes") or "https://www.cnj.jus.br/sgt/versoes.php", validade_h=6.0)
    achado = dump_da_pagina(pagina)
    if achado is None:
        raise ValueError("a página de versões do SGT não tem o link do dump (o formato mudou?)")
    numero, data_pagina = achado
    modelo_url = fontes_tpu.get("dump") or (
        "https://www.cnj.jus.br/sgt/enviarArquivo.php?url={versao}_dump_dados_oracle_postgres.sql&nome=dump_dados_oracle_postgres.sql"
    )
    url = modelo_url.format(versao=numero)
    caminho = coletor.arquivo_grande(url, validade_h=24.0 * 7)
    versao = atualizar_de_dump(caminho, pasta, data_versao=publicada or data_pagina, fonte=url)
    gravar_movimentos(caminho, pasta)  # a árvore de movimentos (o DataJud lê), da mesma versão
    return {"mudou": bool(versao.get("gravados")), "versao": versao}


# ── a árvore de movimentos (para o DataJud) ──────────────────────────────────────────────────

MOVIMENTOS = "movimentos.json"


def compactar_movimentos(tabelas: dict[str, list[dict[str, Any]]]) -> list[list[Any]]:
    """Os movimentos da TPU (itens ``M``) como ``[codigo, pai, "nome"]`` (``0`` no fim se inativo).
    Duas raízes: 1 Magistrado (decisão, despacho, julgamento) e 14 Serventuário (51 Conclusão
    fica em 14 › 48)."""
    itens = []
    for r in tabelas.get("ITENS") or []:
        if r.get("tipo_item") != "M":
            continue
        pai = r.get("cod_item_pai")
        item: list[Any] = [int(r["cod_item"]), int(pai) if pai not in (None, "") else None, _nome(r.get("nome"))]
        if (r.get("situacao") or "A").strip().upper() != "A":
            item.append(0)
        itens.append(item)
    itens.sort(key=lambda x: x[0])
    return itens


def gravar_movimentos(dump: Path | str, pasta: Path | str | None = None) -> bool:
    """Refaz ``movimentos.json`` em ``pasta`` a partir do dump do SGT (só grava se mudou)."""
    pasta = Path(pasta) if pasta is not None else PASTA
    itens = compactar_movimentos(ler_dump(dump, ("ITENS",)))
    if not itens:
        raise ValueError(f"dump do SGT sem movimentos: {dump}")
    return _gravar_texto(pasta / MOVIMENTOS, _por_linha(itens))


def iterar_linhas_stf(texto: str | None) -> Iterator[str]:
    """Atalho: as linhas de assunto do STF sem a numeração ("1 - ")."""
    for linha in re.split(r"\r\n|\r|\n", texto or ""):
        linha = _NUMERACAO.sub("", linha.strip())
        if linha:
            yield linha
