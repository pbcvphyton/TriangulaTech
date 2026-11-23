"""Macroárea, subárea e recortes de um processo, pelas réguas de ``config/tribunais.yaml``.

Uma área por processo (as áreas somam 100% do acervo), decidida nesta ordem (analista §3.1):

1. a **classe** (HC, RHC, APn, Inq, RvCr → Penal; ADI, ADC, ADO, ADPF → Constitucional);
2. o **nó listado mais específico** na árvore TPU do assunto principal (Família 5626 vence a raiz
   Civil 899; Recuperação judicial está sob Empresas 9616, que vence Civil);
3. a **raiz** da árvore;
4. assunto principal **só processual** (raiz 8826): vale o primeiro assunto de mérito da lista, na
   ordem do tribunal; sem nenhum, a área é "Só processual";
5. sem código válido: "Sem assunto"; código que nenhuma regra cobre: "Outros".

No STF o assunto vem em texto e é casado com a árvore (``tpu.casar_caminho``); sem casamento,
vale o ramo do direito ("Ramo Direito Novo") pelo nome da raiz (``por = "ramo"``).

Subárea: o nó listado mais específico, entre as subáreas da área do processo, na cadeia do código
que decidiu a área (ou do principal, quando foi a classe); senão ``"<área>.outros"``. Recortes
(``recortes_por`` no config): por padrão, o assunto que decidiu a área (o principal, ou o primeiro
de mérito da lista) sob um dos códigos do recorte — é a conta do analista (Bancário e crédito:
30.982 processos no STJ em 20/11/2026); com ``todos``, qualquer assunto do processo (33.483).
"Também toca": as outras áreas de mérito que a lista de assuntos toca (não somam; a página não
usa peso fracionado).

Nada aqui olha o texto do ramo do STJ (vem fora do padrão: "Administrativo", "Nao Informado"):
só códigos.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from .modelo import Assunto, Classificacao, Regras
from .tpu import TPU, iterar_linhas_stf

PROCESSUAL = "processual"
SEM_ASSUNTO = "sem_assunto"
OUTROS = "outros"


class Classificador:
    """Classifica processos com as réguas (``Regras``) e a árvore (``TPU``) carregadas uma vez."""

    def __init__(self, regras: Regras, tpu: TPU) -> None:
        self.regras = regras
        self.tpu = tpu
        self._por_classe: dict[str, str] = {}
        self._por_codigo: dict[int, str] = {}
        for area in regras.areas:
            for sigla in area.classes:
                self._por_classe[sigla.strip()] = area.id
            for codigo in (*area.raizes, *area.nos):
                self._por_codigo[codigo] = area.id
        self._residuais = {a.id for a in regras.areas if a.residual}
        self._ordem = {a.id: i for i, a in enumerate(regras.areas)}
        self._subareas: dict[str, dict[int, str]] = {}
        for sub in regras.subareas:
            for codigo in sub.codigos:
                self._subareas.setdefault(sub.area, {})[codigo] = sub.id
        self._recortes = [(r.id, frozenset(r.codigos)) for r in regras.recortes]
        self._recortes_por_todos = str(regras.limiares.get("recortes_por") or "principal") == "todos"
        self._cache: dict[int, str | None] = {}

    # ── por código ───────────────────────────────────────────────────────────────────────────

    def area_do_codigo(self, codigo: int | None) -> str | None:
        """A área de um código TPU sozinho: o nó listado mais específico da cadeia, senão a raiz;
        código que existe mas que nenhuma regra cobre → ``"outros"``; código inexistente → None."""
        if codigo is None:
            return None
        if codigo in self._cache:
            return self._cache[codigo]
        cadeia = self.tpu.cadeia(codigo)
        area: str | None = None
        for c in reversed(cadeia):
            if c in self._por_codigo:
                area = self._por_codigo[c]
                break
        if area is None and cadeia:
            area = OUTROS
        self._cache[codigo] = area
        return area

    def subarea(self, area: str, codigo: int | None) -> str | None:
        """A subárea de ``area`` na cadeia de ``codigo`` (o nó listado mais específico)."""
        tabela = self._subareas.get(area)
        if not tabela:
            return None
        for c in reversed(self.tpu.cadeia(codigo)):
            if c in tabela:
                return tabela[c]
        return f"{area}.outros"

    def recortes(self, codigos: Iterable[int]) -> list[str]:
        cadeias = set()
        for c in codigos:
            cadeias.update(self.tpu.cadeia(c))
        return [rid for rid, alvo in self._recortes if cadeias & alvo]

    # ── um processo ──────────────────────────────────────────────────────────────────────────

    def classificar(self, *, classe: str | None, principal: int | None, assuntos: Sequence[int] = ()) -> Classificacao:
        """Área, subárea, recortes e "também toca" de um processo.

        ``principal``: o código TPU do assunto principal (já normalizado: ``tpu.normalizar_codigo``);
        ``assuntos``: todos os códigos do processo na ordem do tribunal (o principal pode vir junto)."""
        codigos = _unicos([principal, *assuntos])
        sigla = (classe or "").strip()
        area_principal = self.area_do_codigo(principal)
        decisivo: int | None
        if sigla in self._por_classe:
            area, por, decisivo = self._por_classe[sigla], "classe", None
        elif area_principal is not None and area_principal != PROCESSUAL:
            area, por, decisivo = area_principal, "principal", principal
        else:
            area, por, decisivo = SEM_ASSUNTO, "sem_assunto", None
            for c in assuntos:
                a = self.area_do_codigo(c)
                if a is not None and a != PROCESSUAL:
                    area, por, decisivo = a, "lista", c
                    break
            else:
                if area_principal == PROCESSUAL:
                    area, por, decisivo = PROCESSUAL, "so_processual", principal
        base = decisivo if decisivo is not None else principal
        toca = {self.area_do_codigo(c) for c in codigos}
        tambem = sorted(
            (a for a in toca if a and a != area and a not in self._residuais), key=lambda a: self._ordem.get(a, 99)
        )
        return Classificacao(
            area=area,
            por=por,  # type: ignore[typeddict-item]
            codigo=decisivo,
            subarea=self.subarea(area, base) if area not in self._residuais else None,
            recortes=self.recortes(codigos if self._recortes_por_todos else [c for c in (base,) if c is not None]),
            tambem_toca=tambem,
        )

    def assunto(self, codigo: int | None, *, profundidade: int | None = None, texto: str | None = None) -> Assunto:
        """O ``Assunto`` de um código (nome e caminho da TPU)."""
        return Assunto(
            codigo=codigo,
            nome=self.tpu.nome(codigo),
            caminho=self.tpu.caminho(codigo),
            profundidade=self.tpu.nivel(codigo) if profundidade is None else profundidade,
            texto=texto,
        )

    # ── atalhos por tribunal ─────────────────────────────────────────────────────────────────

    def classificar_stj(
        self, *, classe: str | None, codigo_principal: Any, lista_assuntos: str | None
    ) -> tuple[Classificacao, Assunto, list[int]]:
        """Um registro do acervo do STJ: ``siglaClasse``, ``codigoAssuntoCNJ`` (texto, às vezes
        ``"Nao Informado"``) e ``listaAssuntos`` (``"10288 - Nome\\r\\n910288 - Nome"``).
        Devolve (classificação, assunto principal, todos os códigos sem repetição)."""
        principal = self.tpu.normalizar_codigo(codigo_principal)
        lista = [self.tpu.normalizar_codigo(linha.split(" - ", 1)[0]) for linha in re.split(r"\r\n|\r|\n", lista_assuntos or "")]
        codigos = _unicos([principal, *lista])
        classificacao = self.classificar(classe=classe, principal=principal, assuntos=[c for c in lista if c is not None])
        return classificacao, self.assunto(principal), codigos

    def classificar_stf(
        self, *, classe: str | None, assuntos_texto: str | None, ramo: str | None = None
    ) -> tuple[Classificacao, Assunto, list[int]]:
        """Um processo da planilha do STF: ``Classe``, ``Assuntos`` (caminhos em texto, um por
        linha, a primeira é o principal) e ``Ramo Direito Novo``. Sem casamento nenhum, vale o ramo
        pelo nome da raiz (``por = "ramo"``)."""
        casamentos = [self.tpu.casar_caminho(linha) for linha in iterar_linhas_stf(assuntos_texto)]
        principal = casamentos[0].codigo if casamentos else None
        codigos = _unicos(c.codigo for c in casamentos)
        classificacao = self.classificar(classe=classe, principal=principal, assuntos=codigos)
        if classificacao["area"] == SEM_ASSUNTO and ramo:
            raiz = self.tpu.raiz_por_nome(ramo)
            area = self.area_do_codigo(raiz)
            if area is not None:
                classificacao = Classificacao(area=area, por="ramo", codigo=raiz,
                                              subarea=self.subarea(area, raiz) if area not in self._residuais else None,
                                              recortes=[], tambem_toca=[])
        assunto = self.assunto(
            principal,
            profundidade=casamentos[0].profundidade if casamentos else 0,
            texto=casamentos[0].texto if casamentos else None,
        )
        return classificacao, assunto, codigos


def _unicos(codigos: Iterable[int | None]) -> list[int]:
    vistos: list[int] = []
    for c in codigos:
        if c is not None and c not in vistos:
            vistos.append(c)
    return vistos
