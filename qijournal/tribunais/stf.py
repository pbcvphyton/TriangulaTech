"""As fontes do STF: o acervo (a planilha diária), as atas de distribuição, a pesquisa de
jurisprudência e os temas de repercussão geral (e, à parte, a pauta das sessões presenciais).

Cada fonte tem uma função **pura** (lê o texto ou o arquivo já baixado, testável sem rede) e uma
função de **coleta** que pede pelo :class:`fontes.ColetorTribunais` (robots.txt, pausa de 1 s por
host, cache). Nada aqui pede ``portal.stf.jus.br/processos`` (vedado pelo robots.txt): o link do
processo que a página mostra é só para o leitor.

1. **O acervo** (``www.stf.jus.br/.../Acervo_STF_Lista_Geral.xlsx``, refeito todo dia; 22.211
   processos em 22/11/2026). Lido só com a biblioteca padrão (``zipfile`` + ``xml.etree``):
   :func:`linhas_xlsx` dá as células de cada linha pela referência (``"C5"`` → coluna 2), com as
   vazias puladas, as ``sharedStrings`` e as ``inlineStr`` resolvidas. :func:`ler_planilha` acha a
   "Data da última atualização" (linha antes do cabeçalho: é o ``data_dados``), o cabeçalho pelo
   nome das colunas e ignora a linha final de soma. :func:`ler_acervo` monta um
   ``modelo.Processo`` por linha (contrato §1) e a cobertura do casamento com a TPU.

   As **partes** vêm em texto, um campo por polo, e o STF cola os nomes: com mais de uma parte, o
   campo é ``P1`` + ``P2, P3, …, Pn, `` (a primeira e a segunda **sem separador**; as demais com
   vírgula; vírgula no fim): ``"UNIÃOFUNDAÇÃO NACIONAL DOS POVOS INDÍGENAS, "``. Nada aqui adivinha
   a fronteira pelas letras. :func:`separar_polo` separa o que é seguro: as partes depois da
   primeira vírgula, e o pedaço colado só quando ele se escreve de **um único** jeito como dois
   nomes que aparecem **sozinhos** em outro processo da mesma planilha (:func:`nomes_conhecidos`;
   em 22/11/2026, 1.877 de 6.299 pedaços colados). O que sobra colado vai como está, marcado
   ``colado=True``. Reconhecer o litigante (União, INSS, banco) é do módulo ``partes``; o texto
   bruto de cada polo fica em ``polos`` para ele. Advogado pessoa física não é guardado: dos
   advogados ficam só os institucionais (procuradorias, AGU, defensorias, "sem representação") e
   a contagem dos demais.

2. **As atas de distribuição** (``portal.stf.jus.br/atadistribuicao/``): o índice do dia
   (``recuperaata.asp``, XML curto) → cada ata (``listaratas.asp``, XML embutido no HTML) → os
   processos distribuídos por ministro, com as partes por categoria (RECTE, RECDO, IMPTE, PACTE,
   ADV, PROC…) traduzidas em polo, e o totalizador oficial (``qtd_distribuido`` = sorteio;
   ``qtd_redistribuido`` = herança).

3. **A jurisprudência** (``POST jurisprudencia.stf.jus.br/api/search/search``, consulta
   Elasticsearch): uma consulta só, de **agregação**, com decisões monocráticas (base
   ``decisoes``) e acórdãos (``acordaos``) por relator do processo, por mês, e os processos
   distintos. A resposta só vale com ``_shards.failed == 0``. O Presidente aparece com etiquetas
   próprias ("PRESIDENTE", "MINISTRO PRESIDENTE", "EDSON FACHIN (Presidente)"): vão todas para a
   linha da Presidência, nunca para o cartão de quem preside; o mesmo para a Vice-Presidência.

4. **A repercussão geral** (``todostemas.asp`` + ``exportarDados.asp``, cruzados pelo número do
   tema): relator (o do tema, não o sucessor), leading case, situação; **mérito pendente** = há
   repercussão geral reconhecida (sem reafirmação de jurisprudência) e a situação é "Acórdão de
   Repercussão Geral publicado" ou "Analisada Preliminar de Repercussão Geral". O arquivo
   exportado traz acentos quebrados ("HÃ¡"): :func:`corrigir_acentos`.

5. **A pauta** (``/pauta/services/calendario-service.asp``, JSON): as sessões presenciais com os
   processos, o relator, a vista (``ministroVista``) e o destaque. A planilha do acervo já traz a
   coluna "Com pedido de vista".

Nada aqui soma por ministro, tipifica partes nem escreve achados (``contas``, ``partes``,
``achados``).
"""

from __future__ import annotations

import html as html_mod
import io
import logging
import re
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import IO, Any
from xml.etree import ElementTree as ET

from ..net import FetchError
from .areas import Classificador
from .fontes import CaminhoVedado, RecusaDoHost, conferir_elasticsearch
from .modelo import (
    Datas,
    Flags,
    Onde,
    Origem,
    Parte,
    Processo,
    Regras,
    normalizar,
)
from .tpu import TPU

log = logging.getLogger(__name__)

TRIB = "stf"

# ── 0. textos e datas ────────────────────────────────────────────────────────────────────────


class ErroDeFormato(ValueError):
    """A fonte mudou de forma (coluna obrigatória sumiu, XML sem a ata): quem chama vira aviso e
    mantém os dados do último dia bom."""


def _limpo(texto: str | None) -> str:
    """Espaços colapsados (o STF usa ``\\xa0`` no meio de nomes) e pontas cortadas."""
    return " ".join((texto or "").split())


def data_br(texto: str | None) -> str | None:
    """``"22/11/26"`` ou ``"22/11/2026"`` (com hora ou não) → ``"2026-11-22"``; senão None."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", texto or "")
    if not m:
        return None
    dia, mes, ano = (int(x) for x in m.groups())
    if ano < 100:
        ano += 2000
    try:
        return date(ano, mes, dia).isoformat()
    except ValueError:
        return None


EXCEL_1900 = date(1899, 12, 30)  # o dia 0 do Excel (com o 29/02/1900 que não existiu)
EXCEL_1904 = date(1904, 1, 1)


def serial_excel(valor: Any, *, base_1904: bool = False) -> str | None:
    """Data em serial do Excel → ``"AAAA-MM-DD"``, ignorando a fração (a hora):
    ``46342`` → ``2026-11-16``; ``"46315.6477893519"`` → ``2026-10-20``. Vazio ou texto → None."""
    if valor in (None, ""):
        return None
    try:
        dias = int(float(valor))
    except (TypeError, ValueError):
        return data_br(str(valor))
    if dias <= 0:
        return None
    return ((EXCEL_1904 if base_1904 else EXCEL_1900) + timedelta(days=dias)).isoformat()


def _dias(ate: str | None, desde: str | None) -> int | None:
    if not ate or not desde:
        return None
    return max(0, (date.fromisoformat(ate) - date.fromisoformat(desde)).days)


def _inteiro(valor: Any) -> int | None:
    try:
        return int(float(str(valor).strip()))
    except (TypeError, ValueError):
        return None


_REAL = re.compile(r"\d+(?:\.\d*)?(?:[eE][+-]?\d+)?")


def _so_digitos(valor: Any) -> str:
    """O número de um processo só com dígitos. A planilha do STF guarda metade dos números como
    real em notação científica (``"1.34462e6"`` é o ARE 1344620: 11.663 de 22.211 linhas em
    22/11/2026); real inteiro vira o inteiro exato (``Decimal``, sem arredondar por ``float``)."""
    texto = str(valor or "").strip()
    if _REAL.fullmatch(texto) and ("." in texto or "e" in texto.lower()):
        try:
            numero = Decimal(texto)
        except InvalidOperation:
            numero = None
        if numero is not None and numero == numero.to_integral_value():
            return str(int(numero))
    return re.sub(r"\D", "", texto)


def corrigir_acentos(texto: str | None) -> str | None:
    """Desfaz o UTF-8 lido como latin-1 (``"HÃ¡"`` → ``"Há"``, ``"questÃ£o"`` → ``"questão"``);
    texto que não tem o defeito volta igual."""
    if not texto or not re.search("[ÃÂ][\u0080-¿]", texto):
        return texto
    try:
        return texto.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texto


UFS = {
    "ACRE": "AC", "ALAGOAS": "AL", "AMAPA": "AP", "AMAZONAS": "AM", "BAHIA": "BA", "CEARA": "CE",
    "DISTRITO FEDERAL": "DF", "ESPIRITO SANTO": "ES", "GOIAS": "GO", "MARANHAO": "MA", "MATO GROSSO": "MT",
    "MATO GROSSO DO SUL": "MS", "MINAS GERAIS": "MG", "PARA": "PA", "PARAIBA": "PB", "PARANA": "PR",
    "PERNAMBUCO": "PE", "PIAUI": "PI", "RIO DE JANEIRO": "RJ", "RIO GRANDE DO NORTE": "RN",
    "RIO GRANDE DO SUL": "RS", "RONDONIA": "RO", "RORAIMA": "RR", "SANTA CATARINA": "SC", "SAO PAULO": "SP",
    "SERGIPE": "SE", "TOCANTINS": "TO",
}


def uf_do_nome(nome: str | None) -> str | None:
    """``"SANTA CATARINA"`` → ``"SC"`` (a procedência das atas); fora da lista → None."""
    return UFS.get(normalizar(nome))


# ── 1. o xlsx, só com a biblioteca padrão ───────────────────────────────────────────────────

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_ESCAPE_OOXML = re.compile(r"_x([0-9A-Fa-f]{4})_")


def coluna_da_referencia(ref: str) -> int:
    """``"C5"`` → 2; ``"AG12"`` → 32 (coluna a partir de 0)."""
    letras = re.match(r"[A-Za-z]+", ref or "")
    if not letras:
        raise ErroDeFormato(f"referência de célula inválida: {ref!r}")
    n = 0
    for ch in letras.group(0).upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _texto_ooxml(el: ET.Element) -> str:
    """O texto de um ``<si>`` ou ``<is>``: ``<t>`` direto ou os ``<r><t>`` (texto rico), sem as
    ``<rPh>`` (leitura fonética); ``_x000D_`` desfeito."""
    partes: list[str] = []
    for filho in el:
        if filho.tag == f"{_NS}t":
            partes.append(filho.text or "")
        elif filho.tag == f"{_NS}r":
            t = filho.find(f"{_NS}t")
            if t is not None:
                partes.append(t.text or "")
    texto = "".join(partes)
    if "_x" in texto:
        texto = _ESCAPE_OOXML.sub(lambda m: chr(int(m.group(1), 16)), texto)
    return texto


def _abrir_zip(arquivo: Path | str | bytes | IO[bytes]) -> zipfile.ZipFile:
    try:
        if isinstance(arquivo, (bytes, bytearray)):
            return zipfile.ZipFile(io.BytesIO(arquivo))
        return zipfile.ZipFile(arquivo)
    except zipfile.BadZipFile as exc:
        raise ErroDeFormato(f"a planilha não é um xlsx válido ({exc})") from exc


def _partes_do_livro(z: zipfile.ZipFile, folha: int) -> tuple[str, str | None, bool]:
    """Caminhos da folha e das sharedStrings pelo ``workbook.xml`` e suas relações; e se o livro
    usa o calendário de 1904."""
    nomes = set(z.namelist())
    folha_padrao = f"xl/worksheets/sheet{folha + 1}.xml"
    textos_padrao = "xl/sharedStrings.xml" if "xl/sharedStrings.xml" in nomes else None
    if "xl/workbook.xml" not in nomes:
        return folha_padrao, textos_padrao, False
    livro = ET.fromstring(z.read("xl/workbook.xml"))
    pr = livro.find(f"{_NS}workbookPr")
    base_1904 = pr is not None and pr.get("date1904") in ("1", "true")
    alvos: dict[str, str] = {}
    textos = textos_padrao
    if "xl/_rels/workbook.xml.rels" in nomes:
        for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(f"{_NS_PKG}Relationship"):
            alvo = rel.get("Target") or ""
            alvo = alvo.lstrip("/") if alvo.startswith("/") else f"xl/{alvo}"
            alvos[rel.get("Id") or ""] = alvo
            if (rel.get("Type") or "").endswith("/sharedStrings"):
                textos = alvo
    folhas = livro.findall(f"{_NS}sheets/{_NS}sheet")
    caminho = folha_padrao
    if len(folhas) > folha:
        caminho = alvos.get(folhas[folha].get(f"{_NS_REL}id") or "", folha_padrao)
    return caminho, textos, base_1904


def linhas_xlsx(arquivo: Path | str | bytes | IO[bytes], folha: int = 0) -> Iterator[tuple[int, dict[int, str]]]:
    """As linhas de uma folha de xlsx: ``(número da linha, {coluna a partir de 0: valor})``.

    Valor sempre em texto: ``sharedStrings`` (``t="s"``) e ``inlineStr`` resolvidos; número como o
    arquivo o guarda (``"46342"``, ``"46315.6477893519"``: data em serial, ver :func:`serial_excel`);
    ``t="b"`` → ``"1"``/``"0"``; célula de erro e célula vazia ficam de fora. A coluna vem da
    referência da célula (``r="C5"``), porque as vazias não aparecem no arquivo; sem referência,
    a seguinte à anterior. Lê em fluxo (``iterparse``): a planilha do STF tem 29 MB de XML."""
    z = _abrir_zip(arquivo)
    with z:
        caminho, caminho_textos, _ = _partes_do_livro(z, folha)
        textos: list[str] = []
        if caminho_textos and caminho_textos in z.namelist():
            with z.open(caminho_textos) as f:
                for _ev, el in ET.iterparse(f):
                    if el.tag == f"{_NS}si":
                        textos.append(_texto_ooxml(el))
                        el.clear()
        if caminho not in z.namelist():
            raise ErroDeFormato(f"a planilha não tem a folha {caminho}")
        with z.open(caminho) as f:
            numero_linha = 0
            for _ev, el in ET.iterparse(f):
                if el.tag != f"{_NS}row":
                    continue
                numero_linha = _inteiro(el.get("r")) or numero_linha + 1
                celulas: dict[int, str] = {}
                col = -1
                for c in el.iter(f"{_NS}c"):
                    ref = c.get("r")
                    col = coluna_da_referencia(ref) if ref else col + 1
                    tipo = c.get("t")
                    v = c.find(f"{_NS}v")
                    if tipo == "inlineStr":
                        is_ = c.find(f"{_NS}is")
                        valor = _texto_ooxml(is_) if is_ is not None else None
                    elif tipo == "e" or v is None:
                        valor = None
                    elif tipo == "s":
                        try:
                            valor = textos[int(v.text or "")]
                        except (ValueError, IndexError) as exc:
                            raise ErroDeFormato(f"célula {ref} aponta para um texto que não existe") from exc
                    else:  # n (número), str (fórmula), b (lógico), d (data ISO)
                        valor = v.text
                    if valor is not None and valor != "":
                        celulas[col] = valor
                el.clear()
                yield numero_linha, celulas


def base_1904(arquivo: Path | str | bytes | IO[bytes]) -> bool:
    """O livro usa o calendário de 1904 (Excel antigo de Mac)? O do STF usa o de 1900."""
    with _abrir_zip(arquivo) as z:
        return _partes_do_livro(z, 0)[2]


# ── 2. a planilha do acervo ─────────────────────────────────────────────────────────────────

# chave interna → cabeçalho oficial (comparado sem acento, caixa, asteriscos e espaços extras)
COLUNAS: dict[str, str] = {
    "classe": "Classe",
    "numero": "Número",
    "link": "Link",
    "cnj": "Número Único Processo",
    "polo_ativo": "Partes - Polos Ativos",
    "adv_ativo": "Advogados - Polo Ativo",
    "polo_passivo": "Partes - Polos Passivos",
    "adv_passivo": "Advogados - Polo Passivo",
    "meio": "Meio Processo",
    "ano": "Ano de Data Autuação",
    "relator": "Relator Atual",
    "data_andamento": "Data Andamento Atual",
    "andamento": "Andamento Atual",
    "data_local": "Data Localização Atual",
    "local": "Localização Atual",
    "legislacao": "Legislação",
    "sem_decisao": "Sem nenhuma decisão",
    "liminar": "**Medida liminar/Cautelar pendente",
    "tutela_antecipada": "**Com pedido de tutela antecipada",
    "tutela_provisoria": "**Com pedido de tutela provisória",
    "sem_decisao_final": "Sem decisão final",
    "recurso_interno": "Recurso interno pendente",
    "ultimo_recurso": "Sigla do Último Recurso",
    "sobrestado": "Sobrestado",
    "pauta": "Em mesa/pauta",
    "acordao_pendente": "Acórdão pendente de publicação",
    "vista": "Com pedido de vista",
    "origem": "Órgão Origem",
    "preferencia_criminal": "Preferência Criminal",
    "ramo": "Ramo Direito Novo",
    "assuntos": "Assuntos",
}
OBRIGATORIAS = ("classe", "numero", "relator", "local", "assuntos")

# Flags Sim/Não da planilha (contrato: modelo.Flags). Tutela: antecipada OU provisória (a coluna
# da antecipada veio vazia em todas as 22.211 linhas de 22/11/2026).
FLAGS_SIM_NAO: dict[str, tuple[str, ...]] = {
    "liminar": ("liminar",),
    "tutela": ("tutela_antecipada", "tutela_provisoria"),
    "sobrestado": ("sobrestado",),
    "vista": ("vista",),
    "pauta": ("pauta",),
    "sem_decisao": ("sem_decisao",),
    "sem_decisao_final": ("sem_decisao_final",),
    "recurso_interno": ("recurso_interno",),
    "acordao_pendente": ("acordao_pendente",),
    "preferencia_criminal": ("preferencia_criminal",),
}

SEGREDO = "SEGREDO DE JUSTICA"  # o polo vem "*SEGREDO DE JUSTIÇA*"

# Andamentos atuais que são conclusão ao relator (concluso_dias). Levantados na planilha de
# 22/11/2026 (103 andamentos distintos): "Conclusos ao(à) Relator(a)" (6.237 processos, 6.115 no
# gabinete do relator) e "... para o acórdão" (19). Ficam de fora, de propósito: "Conclusão (art. 38
# do RISTF)" (49: vai ao substituto, quase sempre outro gabinete), "Conclusos ao(à) Revisor(a)"
# (11), "Conclusos à Presidência" (831) e "Conclusos à Vice-Presidência" (37).
CONCLUSAO_AO_RELATOR = ("CONCLUSOS AO(A) RELATOR(A)",)

# Representação institucional (fica com o nome): o resto dos advogados é pessoa física e só conta.
_INSTITUCIONAL = re.compile(
    r"^(?:PROCURADORA?\s+GERAL|PROCURADORIA|ADVOGAD[OA]\s+GERAL|ADVOCACIA|DEFENSORA?\s+PUBLIC[OA]\s+GERAL"
    r"|DEFENSORIA|CONSULTORA?\s+GERAL|SEM\s+REPRESENTACAO)"
)


def representacao_institucional(nome: str | None) -> bool:
    """``"PROCURADOR GERAL DA FAZENDA NACIONAL"``, ``"ADVOGADO-GERAL DA UNIÃO"``, ``"DEFENSOR
    PÚBLICO-GERAL FEDERAL"``, ``"SEM REPRESENTAÇÃO NOS AUTOS"``: sim. Nome de pessoa: não."""
    return bool(_INSTITUCIONAL.match(normalizar(nome).replace("-", " ")))


def _chave_coluna(texto: str) -> str:
    return normalizar(texto).replace("*", "").strip()


_POR_CABECALHO = {_chave_coluna(v): k for k, v in COLUNAS.items()}


def data_da_atualizacao(texto: str | None) -> str | None:
    """``"Data da última atualização: 22/11/26"`` → ``"2026-11-22"``."""
    if not texto or "ATUALIZACAO" not in normalizar(texto):
        return None
    return data_br(texto)


@dataclass
class Planilha:
    """A planilha do acervo lida: ``registros`` com as chaves de :data:`COLUNAS`."""

    data_dados: str | None
    registros: list[dict[str, str]]
    colunas_novas: list[str] = field(default_factory=list)  # cabeçalhos que o código não conhece
    linhas_ignoradas: int = 0  # depois do cabeçalho, sem Classe ou Número (a soma do fim)
    base_1904: bool = False


def ler_planilha(arquivo: Path | str | bytes | IO[bytes]) -> Planilha:
    """Lê ``Acervo_STF_Lista_Geral.xlsx``: a data da atualização (antes do cabeçalho), o
    cabeçalho (a linha que tem "Classe" e "Número") e os registros. Coluna obrigatória ausente →
    :class:`ErroDeFormato`."""
    conteudo = arquivo.read() if hasattr(arquivo, "read") else arquivo  # type: ignore[union-attr]
    data_dados: str | None = None
    indice: dict[int, str] | None = None
    novas: list[str] = []
    registros: list[dict[str, str]] = []
    ignoradas = 0
    for _n, celulas in linhas_xlsx(conteudo):
        if indice is None:
            for valor in celulas.values():
                data_dados = data_dados or data_da_atualizacao(valor)
            chaves = {_chave_coluna(v) for v in celulas.values()}
            if _chave_coluna(COLUNAS["classe"]) in chaves and _chave_coluna(COLUNAS["numero"]) in chaves:
                indice = {}
                for col, valor in celulas.items():
                    chave = _POR_CABECALHO.get(_chave_coluna(valor))
                    if chave:
                        indice[col] = chave
                    else:
                        novas.append(valor)
                faltam = [COLUNAS[k] for k in OBRIGATORIAS if k not in indice.values()]
                if faltam:
                    raise ErroDeFormato(f"a planilha do acervo do STF perdeu as colunas {', '.join(faltam)}")
            continue
        registro = {indice[col]: valor for col, valor in celulas.items() if col in indice}
        if not (registro.get("classe") or "").strip() or not _so_digitos(registro.get("numero")):
            ignoradas += 1
            continue
        registros.append(registro)
    if indice is None:
        raise ErroDeFormato("a planilha do acervo do STF não tem o cabeçalho (Classe, Número)")
    return Planilha(data_dados=data_dados, registros=registros, colunas_novas=novas, linhas_ignoradas=ignoradas,
                    base_1904=base_1904(conteudo))


# ── 2.1 as partes de um polo ─────────────────────────────────────────────────────────────────


def _pedacos(texto: str) -> tuple[str, list[str]] | None:
    """``"P1P2, P3, P4, "`` → ``("P1P2", ["P3", "P4"])``; ``None`` quando é uma parte só. A marca de
    várias partes é a vírgula **com espaço** no fim (6.299 polos em 22/11/2026); sem ela, é uma parte
    só, que pode ter vírgula no nome ("AGENCIA NACIONAL DO PETROLEO, GAS NATURAL…") ou uma vírgula
    solta no fim (2 polos: "PROCURADORA-GERAL DA REPÚBLICA,")."""
    bruto = (texto or "").replace("\xa0", " ")
    if not bruto.endswith(", "):
        return None
    pedacos = [_limpo(p) for p in bruto.rstrip()[:-1].split(", ")]
    return pedacos[0], [p for p in pedacos[1:] if p]


def _uma_parte(texto: str) -> str:
    return _limpo(texto).rstrip(",").rstrip()


def nomes_conhecidos(textos: Iterable[str | None]) -> frozenset[str]:
    """Os nomes que aparecem **sozinhos** nos polos da planilha: o polo inteiro quando é uma parte
    só (sem vírgula nenhuma), e cada parte depois da primeira vírgula quando são várias. É o
    catálogo que permite separar ``"UNIÃOFUNDAÇÃO NACIONAL DOS POVOS INDÍGENAS"`` com segurança."""
    nomes: set[str] = set()
    for texto in textos:
        if not texto or SEGREDO in normalizar(texto):
            continue
        pedacos = _pedacos(texto)
        if pedacos is None:
            if "," not in _uma_parte(texto):
                nomes.add(_uma_parte(texto))
        else:
            nomes.update(pedacos[1])
    nomes.discard("")
    return frozenset(nomes)


def descolar(colado: str, conhecidos: frozenset[str] | set[str]) -> tuple[str, str] | None:
    """Separa ``"AB"`` em ``(A, B)`` só se houver **um único** corte em que A e B são nomes
    conhecidos (sem espaço na emenda). Senão ``None`` (fica colado)."""
    cortes = []
    for i in range(1, len(colado)):
        a, b = colado[:i], colado[i:]
        if a == a.rstrip() and b == b.lstrip() and a in conhecidos and b in conhecidos:
            cortes.append((a, b))
            if len(cortes) > 1:
                return None
    return cortes[0] if cortes else None


def separar_polo(texto: str | None, conhecidos: frozenset[str] | set[str] = frozenset()) -> list[tuple[str, bool]]:
    """As partes de um polo do acervo como ``[(nome, colado)]``.

    Uma parte só (sem vírgula no fim): ``[(texto, False)]``. Várias: a cabeça colada (P1P2) é
    separada por :func:`descolar` quando dá; senão vai inteira com ``colado=True``; as seguintes
    vão uma a uma. Cabeça que já é um nome conhecido (o STF pôs vírgula no fim de uma parte só,
    "PROCURADORA-GERAL DA REPÚBLICA,") não é colada. Segredo de justiça e vazio → ``[]``."""
    if not texto or not texto.strip() or SEGREDO in normalizar(texto):
        return []
    pedacos = _pedacos(texto)
    if pedacos is None:
        return [(_uma_parte(texto), False)]
    cabeca, resto = pedacos
    saida: list[tuple[str, bool]] = []
    if cabeca:
        if cabeca in conhecidos:
            saida.append((cabeca, False))
        else:
            par = descolar(cabeca, conhecidos)
            if par:
                saida.extend([(par[0], False), (par[1], False)])
            else:
                saida.append((cabeca, True))
    saida.extend((p, False) for p in resto)
    return saida


def _sem_inscricao(nome: str | None) -> str | None:
    """``"PROCURADOR-GERAL FEDERAL (00000/DF)"`` → ``"PROCURADOR-GERAL FEDERAL"`` (a inscrição na OAB
    que as atas põem depois do nome)."""
    return _limpo(re.sub(r"\s*\([^)]*\d[^)]*\)\s*$", "", nome or "")) or None


def representantes(texto: str | None) -> tuple[list[str], int]:
    """A coluna de advogados de um polo (separados por vírgula) → (os institucionais, com o nome;
    quantos são pessoa física, sem o nome)."""
    institucionais: list[str] = []
    pessoas = 0
    for nome in (texto or "").split(","):
        nome = _limpo(nome)
        if not nome:
            continue
        if representacao_institucional(nome):
            if nome not in institucionais:
                institucionais.append(nome)
        else:
            pessoas += 1
    return institucionais, pessoas


# ── 2.2 o registro normalizado ───────────────────────────────────────────────────────────────

ONDE_PRESIDENCIA = ("PRESIDENCIA", "VICE PRESIDENCIA", "GABINETE DA VICE PRESIDENCIA", "GABINETE DA PRESIDENCIA",
                    "ASSESSORIA DE ANALISE DE RECURSOS")


def onde_stf(local: str | None, relator_id: str | None, na_mesa: bool, composicao: Any) -> Onde:
    """O local resumido (contrato §1, ``onde``). STF: "GERÊNCIA …" é cartório; a "ASSESSORIA DE
    ANÁLISE DE RECURSOS" é a triagem da Presidência (1.127 dos 1.131 processos ali, em 22/11/2026,
    têm o Presidente como relator); "GABINETE DA VICE-PRESIDÊNCIA" é Presidência, não outro
    gabinete; a PGR é o MP."""
    if na_mesa:
        return "gabinete"
    alvo = normalizar(local).replace("-", " ")
    if not alvo:
        return "outro"
    dono = composicao.do_gabinete(local) if composicao is not None else None
    if dono is not None and dono.id != relator_id:
        return "outro_gabinete"
    if alvo in ONDE_PRESIDENCIA:
        return "presidencia"
    if alvo.startswith("GABINETE"):
        return "outro_gabinete"
    if alvo.startswith("PROCURADORIA GERAL DA REPUBLICA"):
        return "mp"
    if alvo.startswith(("GERENCIA", "COORDENADORIA")):
        return "cartorio"
    return "outro"


def _sim_nao(valor: str | None) -> bool | None:
    alvo = normalizar(valor)
    if alvo in ("SIM", "S"):
        return True
    if alvo in ("NAO", "N"):
        return False
    return None


def flags_do_registro(registro: Mapping[str, str]) -> Flags:
    """As colunas Sim/Não (só as que a planilha preencheu) e a sigla do último recurso."""
    flags: dict[str, Any] = {}
    for nome, colunas in FLAGS_SIM_NAO.items():
        valores = [_sim_nao(registro.get(c)) for c in colunas]
        if any(v is not None for v in valores):
            flags[nome] = any(v is True for v in valores)
    if registro.get("ultimo_recurso"):
        flags["ultimo_recurso"] = _limpo(registro["ultimo_recurso"])
    return flags  # type: ignore[return-value]


def e_conclusao_ao_relator(andamento: str | None) -> bool:
    alvo = normalizar(andamento)
    return any(alvo.startswith(p) for p in CONCLUSAO_AO_RELATOR)


def _parte(polo: str, nome: str, colado: bool) -> Parte:
    parte: dict[str, Any] = {"polo": polo, "papel": f"POLO {polo.upper()}", "nome": nome, "tipo": "desconhecido",
                             "grupo": None, "cnpj_raiz": None, "colado": colado}
    return parte  # type: ignore[return-value]


@dataclass
class _Memo:
    """Classificação por (classe, assuntos, ramo): a planilha repete muito as mesmas combinações."""

    classificador: Classificador
    tpu: TPU
    tabela: dict[tuple[str, str, str], tuple[Any, Any, list[int], list[tuple[int, int]]]] = field(default_factory=dict)

    def __call__(self, classe: str, assuntos: str, ramo: str) -> tuple[Any, Any, list[int], list[tuple[int, int]]]:
        chave = (classe, assuntos, ramo)
        if chave not in self.tabela:
            classificacao, assunto, codigos = self.classificador.classificar_stf(
                classe=classe, assuntos_texto=assuntos, ramo=ramo or None)
            casamentos = [(c.profundidade, c.niveis) for c in map(self.tpu.casar_caminho, self.tpu.linhas_de_assunto(assuntos))]
            self.tabela[chave] = (classificacao, assunto, codigos, casamentos)
        return self.tabela[chave]


def processo_do_registro(
    registro: Mapping[str, str],
    *,
    data_dados: str | None,
    regras: Regras,
    classificador: Classificador | _Memo,
    conhecidos: frozenset[str] | set[str] = frozenset(),
    base_1904: bool = False,
) -> Processo:
    """Um ``Processo`` (contrato §1, coluna STF) a partir de uma linha da planilha."""
    composicao = regras.composicao[TRIB]
    classe = _limpo(registro.get("classe"))
    numero = _so_digitos(registro.get("numero"))
    cnj = _so_digitos(registro.get("cnj"))
    cnj = cnj if len(cnj) == 20 else None  # o número único tem 20 dígitos (3 processos sem ele em 22/11/2026)
    relator_texto = _limpo(registro.get("relator")) or None
    achado = composicao.relator(relator_texto or "")
    ministro = achado.ministro
    relator_id = ministro.id if ministro else None
    local = _limpo(registro.get("local")) or None
    na_mesa = composicao.na_mesa(relator_id, local)
    if isinstance(classificador, _Memo):
        classificacao, assunto, codigos, _ = classificador(classe, registro.get("assuntos") or "", _limpo(registro.get("ramo")))
    else:
        classificacao, assunto, codigos = classificador.classificar_stf(
            classe=classe, assuntos_texto=registro.get("assuntos") or "", ramo=_limpo(registro.get("ramo")) or None)
    ativo, passivo = registro.get("polo_ativo"), registro.get("polo_passivo")
    segredo = any(SEGREDO in normalizar(t) for t in (ativo, passivo) if t)
    partes: list[Parte] = []
    polos: dict[str, Any] = {}
    for polo, texto, adv in (("ativo", ativo, registro.get("adv_ativo")), ("passivo", passivo, registro.get("adv_passivo"))):
        if segredo:
            polos[polo] = {"texto": None, "representantes": [], "advogados": 0}
            continue
        partes.extend(_parte(polo, nome, colado) for nome, colado in separar_polo(texto, conhecidos))
        inst, pessoas = representantes(adv)
        polos[polo] = {"texto": texto or None, "representantes": inst, "advogados": pessoas}
    andamento = _limpo(registro.get("andamento")) or None
    datas = Datas(
        autuacao=None,
        ano_autuacao=_inteiro(registro.get("ano")),
        distribuicao=None,
        ultima_decisao=None,
        andamento=serial_excel(registro.get("data_andamento"), base_1904=base_1904),
        localizacao=serial_excel(registro.get("data_local"), base_1904=base_1904),
    )
    processo: dict[str, Any] = {
        "trib": TRIB,
        "id": f"{TRIB}-{classe}-{numero}",
        "classe": classe,
        "numero": numero,
        "cnj": cnj,
        "registro": None,
        "relator": ministro.nome if ministro else relator_texto,
        "relator_id": relator_id,
        "fora": None if ministro else achado.fora,
        "orgao": None,
        "local": local,
        "onde": onde_stf(local, relator_id, na_mesa, composicao),
        "na_mesa": na_mesa,
        "area": classificacao["area"],
        "area_por": classificacao["por"],
        "subarea": classificacao["subarea"],
        "recortes": list(classificacao["recortes"]),
        "tambem_toca": list(classificacao["tambem_toca"]),
        "assunto": dict(assunto),
        "assuntos": list(codigos),
        "segredo": segredo,
        "partes": partes,
        "datas": datas,
        "andamento": andamento,
        "sem_decisao_dias": None,
        "concluso_dias": _dias(data_dados, datas["andamento"]) if e_conclusao_ao_relator(andamento) else None,
        "local_dias": _dias(data_dados, datas["localizacao"]),
        "flags": flags_do_registro(registro),
        "origem": Origem(uf=None, orgao=_limpo(registro.get("origem")) or None),
        # Fora do contrato §1 (só o STF tem): o texto de cada polo como veio, para partes.py, e a
        # representação institucional; nunca vai a arquivo de data/tribunais/.
        "polos": polos,
    }
    return processo  # type: ignore[return-value]


@dataclass
class Acervo:
    """O acervo do STF lido: os processos e o que entrou em cada conta (``meta.cobertura.stf``)."""

    data_dados: str | None
    processos: list[Processo]
    cobertura: dict[str, Any]
    avisos: list[str] = field(default_factory=list)


def _casamento_da_linha(profundidade: int, niveis: int) -> str:
    if profundidade == 0:
        return "nada"
    if profundidade == niveis:
        return "folha"
    if profundidade == 1:
        return "raiz"
    return "parcial"


def ler_acervo(
    arquivo: Path | str | bytes | IO[bytes],
    regras: Regras,
    tpu: TPU | None = None,
    classificador: Classificador | None = None,
) -> Acervo:
    """A planilha inteira → os ``Processo`` e a cobertura (processos, grade, segredo, casamento
    das linhas de assunto com a TPU, como a área foi decidida, relatores fora do mapa, partes)."""
    planilha = ler_planilha(arquivo)
    if tpu is None:
        tpu = classificador.tpu if classificador is not None else TPU.carregar()
    memo = _Memo(classificador or Classificador(regras, tpu), tpu)
    conhecidos = nomes_conhecidos(t for r in planilha.registros for t in (r.get("polo_ativo"), r.get("polo_passivo")))
    composicao = regras.composicao[TRIB]
    processos: list[Processo] = []
    casamento: Counter[str] = Counter()
    area_por: Counter[str] = Counter()
    fora: Counter[str] = Counter()
    fora_do_mapa: Counter[str] = Counter()
    partes_conta: Counter[str] = Counter()
    divergem_do_link = 0
    for registro in planilha.registros:
        p = processo_do_registro(registro, data_dados=planilha.data_dados, regras=regras, classificador=memo,
                                 conhecidos=conhecidos, base_1904=planilha.base_1904)
        processos.append(p)
        link = _limpo(registro.get("link"))
        if link and link != f"{p['classe']}-{p['numero']}":  # a coluna Link é "ARE-1344620"
            divergem_do_link += 1
        _c, _a, _cods, linhas = memo(p["classe"], registro.get("assuntos") or "", _limpo(registro.get("ramo")))
        for profundidade, niveis in linhas:
            casamento[_casamento_da_linha(profundidade, niveis)] += 1
        area_por[p["area_por"]] += 1
        if p["relator_id"] is None:
            fora[p["fora"] or "residuos"] += 1
            if p["fora"] == "residuos" and not composicao.relator(_limpo(registro.get("relator"))).conhecido:
                fora_do_mapa[_limpo(registro.get("relator"))] += 1
        for parte in p["partes"]:
            partes_conta["colados" if parte.get("colado") else "partes"] += 1  # type: ignore[union-attr]
    grade = {m.id for m in composicao.da_grade()}
    cobertura: dict[str, Any] = {
        "processos": len(processos),
        "na_grade": sum(1 for p in processos if p["relator_id"] in grade),
        "na_mesa": sum(1 for p in processos if p["na_mesa"]),
        "segredo": sum(1 for p in processos if p["segredo"]),
        "linhas_de_assunto": sum(casamento.values()),
        "casamento": {k: casamento[k] for k in ("folha", "parcial", "raiz", "nada")},
        "area_por": {k: area_por[k] for k in ("principal", "classe", "lista", "so_processual", "ramo", "sem_assunto") if area_por[k]},
        "relatores_fora_do_mapa": [[n, c] for n, c in sorted(fora_do_mapa.items(), key=lambda x: (-x[1], x[0]))],
        "fora_da_grade": {f.id: fora[f.id] for f in composicao.fora_da_grade if fora[f.id]},
        "partes": {"separadas": partes_conta["partes"], "coladas": partes_conta["colados"]},
    }
    avisos = []
    if planilha.data_dados is None:
        avisos.append("A planilha do acervo do STF não trouxe a data da atualização: sem ela, os dias desde a conclusão "
                      "ao relator e desde a última localização ficam vazios.")
    if planilha.colunas_novas:
        avisos.append(f"A planilha do acervo do STF trouxe colunas novas, não lidas: {', '.join(planilha.colunas_novas)}.")
    if fora_do_mapa:
        avisos.append("Relatores do STF fora da composição do config: "
                      + ", ".join(f"{n} ({c})" for n, c in cobertura["relatores_fora_do_mapa"]) + ".")
    if divergem_do_link:
        avisos.append(f"Planilha do acervo do STF: {divergem_do_link} processos com Classe e Número diferentes da coluna Link.")
    return Acervo(data_dados=planilha.data_dados, processos=processos, cobertura=cobertura, avisos=avisos)


def coletar_acervo(coletor: Any, regras: Regras, *, tpu: TPU | None = None, classificador: Classificador | None = None,
                   validade_h: float | None = None) -> Acervo:
    """Baixa a planilha (5,8 MB, um pedido por dia; o cache vale ``fontes.stf.validade_h.acervo``)
    e lê. Erro de rede sobe (``net.FetchError``); planilha fora do formato, :class:`ErroDeFormato`."""
    cfg = regras.fontes.get("stf") or {}
    validade = validade_h if validade_h is not None else float((cfg.get("validade_h") or {}).get("acervo", 20))
    caminho = coletor.arquivo(cfg["acervo"], validade_h=validade)
    return ler_acervo(caminho, regras, tpu=tpu, classificador=classificador)


# ── 3. as atas de distribuição ──────────────────────────────────────────────────────────────

# Categoria da parte nas atas (sem pontos e parênteses: "RECTE.(S)" → "RECTE") → polo. A régua do
# analista (§5.1): recorre/pede × responde; COATOR, IMPDO e autoridade policial não são partes
# (autoridade); ADV e PROC são representação (do polo da parte anterior).
POLO_DA_CATEGORIA: dict[str, str] = {
    "RECTE": "ativo", "AGTE": "ativo", "EMBTE": "ativo", "IMPTE": "ativo", "RECLTE": "ativo", "REQTE": "ativo",
    "AUTOR": "ativo", "PACTE": "ativo", "LIT ATIV": "ativo", "QTE": "ativo", "EXQTE": "ativo", "SUSTE": "ativo",
    "RECDO": "passivo", "AGDO": "passivo", "EMBDO": "passivo", "RECLDO": "passivo", "REQDO": "passivo",
    "INTDO": "passivo", "BENEF": "passivo", "LIT PAS": "passivo", "REU": "passivo", "QDO": "passivo",
    "EXDO": "passivo", "EXTDO": "passivo", "SUSDO": "passivo",
}
AUTORIDADES = {"COATOR", "IMPDO", "AUT POL", "AUT COATORA", "SUSCTE", "SUSCDO"}
REPRESENTACAO = {"ADV", "PROC", "DEF", "DEFENS"}


def chave_categoria(categoria: str | None) -> str:
    """``"RECTE.(S)"`` → ``"RECTE"``; ``"PROC.(A/S)(ES)"`` → ``"PROC"``; ``"AUT. POL."`` → ``"AUT POL"``."""
    s = normalizar(re.sub(r"\([^)]*\)", " ", categoria or ""))
    return re.sub(r"[.\s]+", " ", s).strip()


def _xml_da_pagina(texto: str, raiz: str) -> ET.Element | None:
    """O XML que a página do STF embute num ``<div hidden>`` (ou o próprio XML), sem a
    declaração (que diz ISO-8859-1, mas o texto já veio decodificado)."""
    m = re.search(rf"<{raiz}\b.*?</{raiz}>", texto or "", re.DOTALL)
    if not m:
        return None
    try:
        return ET.fromstring(m.group(0))
    except ET.ParseError:
        pass
    try:  # "&" solto num nome ("BELMONTE & ADVOGADOS"): escapado e lido de novo
        return ET.fromstring(re.sub(r"&(?!#?\w+;)", "&amp;", m.group(0)))
    except ET.ParseError as exc:
        raise ErroDeFormato(f"XML de <{raiz}> mal formado ({exc})") from exc


def ler_indice_atas(texto: str) -> dict[str, Any]:
    """O índice do dia (``recuperaata.asp``) → ``{"vazio", "atas": [{num, sessao, data, modo}],
    "presidencia": total registrado à Presidência ou None}``. Dia sem ata: ``situacao="vazio"``."""
    atas = []
    for m in re.finditer(r"<ata_distribuicao\b([^>]*)>", texto or ""):
        attr = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        num = _inteiro(attr.get("num_ata"))
        if num is None:
            continue
        atas.append({"num": num, "sessao": attr.get("tipo_sessao") or "O", "data": data_br(attr.get("dat_ata")),
                     "modo": attr.get("modo_distribuicao")})
    m = re.search(r'<processos_registrados_presidencia\b[^>]*total_processos="(\d+)"', texto or "")
    return {"vazio": not atas, "atas": atas, "presidencia": int(m.group(1)) if m else None}


def _partes_da_ata(el: ET.Element) -> tuple[list[Parte], list[dict[str, Any]]]:
    """As ``<parte categoria nome tip_impressao>`` de um processo → (partes, representantes).
    Advogado pessoa física fica sem nome (``nome=None``); "E OUTRO(A/S)" vira ``e_outros``."""
    partes: list[Parte] = []
    reps: list[dict[str, Any]] = []
    polo_anterior: str | None = None
    for p in el.iter("parte"):
        categoria = p.get("categoria") or ""
        chave = chave_categoria(categoria)
        nome = _limpo(html_mod.unescape(p.get("nome") or "")) or None
        e_outros = bool((p.get("tip_impressao") or "").strip())
        if chave in REPRESENTACAO:
            reps.append({"polo": polo_anterior, "papel": categoria,
                         "nome": _sem_inscricao(nome) if representacao_institucional(nome) else None})
            continue
        if chave in AUTORIDADES:
            parte: dict[str, Any] = {"polo": None, "papel": categoria, "nome": nome, "tipo": "autoridade",
                                     "grupo": None, "cnpj_raiz": None}
        else:
            polo = POLO_DA_CATEGORIA.get(chave, "outro")
            parte = {"polo": polo, "papel": categoria, "nome": nome, "tipo": "desconhecido", "grupo": None,
                     "cnpj_raiz": None}
        if e_outros:
            parte["e_outros"] = True
        polo_anterior = parte["polo"]
        partes.append(parte)  # type: ignore[arg-type]
    return partes, reps


def _processo_da_ata(el: ET.Element, regras: Regras) -> dict[str, Any]:
    composicao = regras.composicao[TRIB]
    classe = _limpo(el.get("sig_classe_proces"))
    numero = _so_digitos(el.get("num_processo"))
    relator_texto = _limpo(el.get("nom_min_relator")) or None
    achado = composicao.relator(relator_texto or "")
    partes, reps = _partes_da_ata(el)
    procedencia = _limpo(el.get("proced")) or None
    return {
        "id": f"{TRIB}-{classe}-{numero}",
        "classe": classe,
        "numero": numero,
        "classe_nome": _limpo(el.get("dsc_classe")) or None,
        "meio": el.get("tipo_processo"),
        "procedencia": procedencia,
        "uf": uf_do_nome(procedencia),
        "origem": _limpo(el.get("dsc_origem")) or None,
        "relator": achado.ministro.nome if achado.ministro else relator_texto,
        "relator_id": achado.ministro.id if achado.ministro else None,
        "fora": None if achado.ministro else achado.fora,
        "partes": partes,
        "representantes": reps,
    }


def ler_ata(texto: str, regras: Regras) -> dict[str, Any]:
    """Uma ata (``listaratas.asp``) → ``{data, num, sessao, modo, presidente, processos,
    totais, total}``. ``totais``: o totalizador oficial por ministro (``sorteio`` =
    ``qtd_distribuido``, ``heranca`` = ``qtd_redistribuido``), sem a linha TOTAL, que vai em
    ``total``. Página sem a ata: :class:`ErroDeFormato`."""
    raiz = _xml_da_pagina(texto, "ata_distribuicao")
    if raiz is None:
        raise ErroDeFormato("a página da ata de distribuição do STF não trouxe o XML da ata")
    composicao = regras.composicao[TRIB]
    totais = []
    total = None
    for m in raiz.iter("ministro"):
        linha = {"codigo": _inteiro(m.get("cod_ministro")), "nome": _limpo(m.get("nom_ministro")),
                 "sorteio": _inteiro(m.get("qtd_distribuido")) or 0, "heranca": _inteiro(m.get("qtd_redistribuido")) or 0,
                 "total": _inteiro(m.get("total")) or 0}
        if normalizar(linha["nome"]) == "TOTAL" or linha["codigo"] == 999:
            total = {k: linha[k] for k in ("sorteio", "heranca", "total")}
            continue
        ministro = next((x for x in composicao.ministros if x.codigo_ata is not None and x.codigo_ata == linha["codigo"]), None)
        ministro = ministro or composicao.por_nome(linha["nome"])
        linha["ministro_id"] = ministro.id if ministro else None
        totais.append(linha)
    presidente = _limpo(raiz.get("nom_min_presidente")) or None
    m_pres = composicao.por_nome(presidente) if presidente else None
    processos = [_processo_da_ata(p, regras) for p in raiz.iter("processo")]
    if total is None:
        total = {"sorteio": sum(t["sorteio"] for t in totais), "heranca": sum(t["heranca"] for t in totais),
                 "total": sum(t["total"] for t in totais)}
    return {
        "data": data_br(raiz.get("dat_ata")),
        "num": _inteiro(raiz.get("num_ata")),
        "sessao": raiz.get("tipo_sessao"),
        "modo": raiz.get("tip_modo_distribuicao"),
        "presidente": {"nome": presidente, "ministro_id": m_pres.id if m_pres else None},
        "processos": processos,
        "totais": totais,
        "total": total,
    }


def ler_feitos_presidencia(texto: str, regras: Regras) -> list[dict[str, Any]]:
    """Os feitos registrados à Presidência no dia (``listarfeitos.asp``): quase todos ARE e RE,
    relator "MINISTRO PRESIDENTE" (é registro, não entrada de ministro)."""
    raiz = _xml_da_pagina(texto, "processos_registrados_presidencia")
    if raiz is None:
        return []
    return [_processo_da_ata(p, regras) for p in raiz.iter("processo")]


def _url(modelo: str, **valores: Any) -> str:
    return modelo.format(**valores)


def coletar_atas(
    coletor: Any,
    regras: Regras,
    *,
    ate: date,
    dias: int | None = None,
    com_presidencia: bool = False,
) -> dict[str, Any]:
    """As atas dos ``dias`` dias até ``ate`` (inclusive; padrão: ``limiares.janelas.
    entradas_recentes_dias``, 30). Um pedido por dia ao índice e um por ata (a pausa de 1 s por
    host é do coletor). Dia já passado (mais de 3 dias) fica no cache por um ano: ata não muda.

    Devolve ``{"janela": [ini, fim], "dias": [{data, vazio, presidencia, atas: [ata]}],
    "avisos": [...]}``. Um dia que falha vira aviso e a coleta segue; recusa do host ou caminho
    vedado (``RecusaDoHost``/``CaminhoVedado``) param tudo e sobem."""
    cfg = regras.fontes.get("stf") or {}
    janelas = regras.limiares.get("janelas") or {}
    dias = int(dias if dias is not None else janelas.get("entradas_recentes_dias", 30))
    recente_h = float((cfg.get("validade_h") or {}).get("atas", 20))
    saida: dict[str, Any] = {"janela": [(ate - timedelta(days=dias - 1)).isoformat(), ate.isoformat()], "dias": [], "avisos": []}
    for i in range(dias - 1, -1, -1):
        dia = ate - timedelta(days=i)
        validade = recente_h if i <= 3 else 24.0 * 400
        try:
            indice = ler_indice_atas(coletor.texto(
                _url(cfg["atas_recupera"], dia=f"{dia.day:02d}", mes=f"{dia.month:02d}", ano=dia.year), validade_h=validade))
            registro: dict[str, Any] = {"data": dia.isoformat(), "vazio": indice["vazio"], "presidencia": indice["presidencia"],
                                        "atas": []}
            for a in indice["atas"]:
                pagina = coletor.texto(_url(cfg["atas_lista"], data=dia.strftime("%d/%m/%Y"), num=a["num"], sessao=a["sessao"]),
                                       validade_h=validade)
                registro["atas"].append(ler_ata(pagina, regras))
            if com_presidencia and indice["presidencia"]:
                url = cfg["atas_lista"].split("listaratas.asp", 1)[0] + f"listarfeitos.asp?data={dia.strftime('%d/%m/%Y')}"
                registro["feitos_presidencia"] = ler_feitos_presidencia(coletor.texto(url, validade_h=validade), regras)
        except (RecusaDoHost, CaminhoVedado):
            raise
        except (FetchError, ErroDeFormato) as exc:
            saida["avisos"].append(f"STF, atas de distribuição de {dia.strftime('%d/%m/%Y')}: {exc}")
            log.warning("Tribunais: ata do STF de %s falhou: %s", dia.isoformat(), exc)
            continue
        saida["dias"].append(registro)
    return saida


# ── 4. a pesquisa de jurisprudência (agregação) ─────────────────────────────────────────────

BASES = {"decisoes": "monocraticas", "acordaos": "acordaos"}
PRECISAO = 40000  # cardinality exata até aqui (o maior relator tem ~12 mil processos em 12 meses)


def consulta_decisoes(inicio: date | str, fim: date | str) -> dict[str, Any]:
    """O corpo da consulta Elasticsearch: decisões monocráticas e acórdãos julgados entre
    ``inicio`` e ``fim`` (``julgamento_data``), agregados por relator do processo
    (``relator_processo_nome.keyword``), base e mês, com os processos distintos (classe + número:
    ``cardinality`` de ``processo_numero`` dentro de cada classe). Nenhum documento volta
    (``size: 0``). Testada ao vivo em 23/11/2026 (``_shards.failed == 0``)."""
    ini, fim_ = (d.isoformat() if isinstance(d, date) else str(d) for d in (inicio, fim))
    mes = {"date_histogram": {"field": "julgamento_data", "calendar_interval": "month", "format": "yyyy-MM"}}
    processos = {"terms": {"field": "processo_classe_processual_unificada_classe_sigla.keyword", "size": 100},
                 "aggs": {"processos": {"cardinality": {"field": "processo_numero", "precision_threshold": PRECISAO}}}}
    return {
        "size": 0,
        "track_total_hits": True,
        "query": {"bool": {"filter": [
            {"terms": {"base": list(BASES)}},
            {"range": {"julgamento_data": {"gte": ini, "lte": fim_, "format": "yyyy-MM-dd"}}},
        ]}},
        "aggs": {
            "ultima": {"max": {"field": "julgamento_data", "format": "yyyy-MM-dd"}},
            "base": {"terms": {"field": "base", "size": 5}, "aggs": {"mes": mes}},
            "classe": processos,
            "relator": {"terms": {"field": "relator_processo_nome.keyword", "size": 200},
                        "aggs": {"base": {"terms": {"field": "base", "size": 5}, "aggs": {"mes": mes}}, "classe": processos}},
        },
    }


def destino_do_relator(rotulo: str | None, composicao: Any) -> tuple[str | None, str | None]:
    """A etiqueta ``relator_processo_nome`` → ``(ministro_id, fora)``. O Presidente vem como
    "PRESIDENTE" (monocráticas), "MINISTRO PRESIDENTE" e "EDSON FACHIN (Presidente)" (acórdãos):
    tudo vai para ``presidencia``. O Vice: "VICE-PRESIDENTE" e "ALEXANDRE DE MORAES
    (Vice-Presidente)" → ``vice``. O resto, pela composição (quem saiu → ``residuos``)."""
    alvo = normalizar(rotulo).replace("-", " ")
    parenteses = re.findall(r"\(([^)]*)\)", alvo)
    if "VICE PRESIDENTE" in parenteses or re.sub(r"^(MINISTR[OA]\s+)", "", alvo) == "VICE PRESIDENTE":
        return None, "vice"
    if "PRESIDENTE" in parenteses or re.sub(r"^(MINISTR[OA]\s+)", "", alvo) == "PRESIDENTE":
        return None, "presidencia"
    achado = composicao.relator(rotulo or "")
    return (achado.ministro.id, None) if achado.ministro else (None, achado.fora or "residuos")


def _soma_processos(bucket: Mapping[str, Any]) -> int | None:
    classes = (bucket.get("classe") or {}).get("buckets")
    if classes is None:
        return None
    return sum(int((c.get("processos") or {}).get("value") or 0) for c in classes)


def _por_base_e_mes(bucket: Mapping[str, Any]) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    totais = {"monocraticas": 0, "acordaos": 0}
    meses: dict[str, dict[str, int]] = {}
    for b in (bucket.get("base") or {}).get("buckets") or ():
        chave = BASES.get(b.get("key"))
        if not chave:
            continue
        totais[chave] += int(b.get("doc_count") or 0)
        for m in (b.get("mes") or {}).get("buckets") or ():
            mes = str(m.get("key_as_string"))
            meses.setdefault(mes, {"monocraticas": 0, "acordaos": 0})[chave] += int(m.get("doc_count") or 0)
    return totais, meses


def _linha(totais: Mapping[str, int], processos: int | None, meses: Mapping[str, Mapping[str, int]]) -> dict[str, Any]:
    return {
        "processos": processos,
        "documentos": totais["monocraticas"] + totais["acordaos"],
        "monocraticas": totais["monocraticas"],
        "acordaos": totais["acordaos"],
        "por_mes": [[m, v["monocraticas"], v["acordaos"]] for m, v in sorted(meses.items())],
    }


def _somar(linha: dict[str, Any], outra: Mapping[str, Any]) -> None:
    for k in ("documentos", "monocraticas", "acordaos"):
        linha[k] += outra[k]
    if outra["processos"] is not None:
        linha["processos"] = (linha["processos"] or 0) + outra["processos"]
    meses = {m: [a, b] for m, a, b in linha["por_mes"]}
    for m, a, b in outra["por_mes"]:
        meses.setdefault(m, [0, 0])
        meses[m][0] += a
        meses[m][1] += b
    linha["por_mes"] = [[m, v[0], v[1]] for m, v in sorted(meses.items())]


def ler_decisoes(resposta: Mapping[str, Any], regras: Regras, *, inicio: date | str, fim: date | str) -> dict[str, Any]:
    """A resposta da agregação → as decisões do STF na janela (contrato §5 ``decisoes`` e §6).

    ``ministros``: ``{id: {processos, documentos, monocraticas, acordaos, por_mes}}`` (só o que ele
    decidiu como relator; nunca o que decidiu como Presidente). ``fora``: ``presidencia``,
    ``vice`` e ``residuos`` (quem saiu), na mesma forma. ``rotulos``: cada etiqueta da fonte e
    para onde foi (a prova da regra do Presidente). Resposta parcial: quem chama já recusou
    (``fontes.conferir_elasticsearch``); aqui confere de novo."""
    conferir_elasticsearch(resposta)
    corpo = resposta.get("result", resposta)
    aggs = corpo.get("aggregations") or {}
    composicao = regras.composicao[TRIB]
    ini, fim_ = (d.isoformat() if isinstance(d, date) else str(d) for d in (inicio, fim))
    totais, meses = _por_base_e_mes(aggs)
    ministros: dict[str, dict[str, Any]] = {}
    fora: dict[str, dict[str, Any]] = {}
    rotulos: list[list[Any]] = []
    fora_do_mapa: list[list[Any]] = []
    for b in (aggs.get("relator") or {}).get("buckets") or ():
        rotulo = str(b.get("key"))
        ministro_id, linha_fora = destino_do_relator(rotulo, composicao)
        t, m = _por_base_e_mes(b)
        linha = _linha(t, _soma_processos(b), m)
        destino = ministros if ministro_id else fora
        chave = ministro_id or linha_fora or "residuos"
        if chave in destino:
            _somar(destino[chave], linha)
        else:
            destino[chave] = linha
        rotulos.append([rotulo, chave, linha["documentos"]])
        if linha_fora == "residuos" and not composicao.relator(rotulo).conhecido:
            fora_do_mapa.append([rotulo, linha["documentos"]])
    ordem = [m.id for m in composicao.ministros]
    ultima = (aggs.get("ultima") or {}).get("value_as_string")
    total = (corpo.get("hits") or {}).get("total")
    documentos = total.get("value") if isinstance(total, Mapping) else total
    saida = {
        "fonte": "stf_jurisprudencia",
        "janela": [ini, fim_],
        "data_dados": ultima,
        "ultimo_arquivo": None,
        "atraso_dias": None,
        "processos": _soma_processos(aggs),
        "documentos": int(documentos) if documentos is not None else totais["monocraticas"] + totais["acordaos"],
        "monocraticas": totais["monocraticas"],
        "acordaos": totais["acordaos"],
        "acordaos_recurso_interno": None,  # o campo do incidente (AgR, ED) não agrega na API
        "presidencia": (fora.get("presidencia") or {}).get("documentos", 0),
        # documento sem relator do processo (o termo da agregação não o vê): 1 em 72.155 em 23/11/2026
        "sem_relator": max(0, (int(documentos) if documentos is not None else 0)
                           - sum(v["documentos"] for v in (*ministros.values(), *fora.values()))),
        "por_mes": [[m, v["monocraticas"], v["acordaos"]] for m, v in sorted(meses.items())],
        "ministros": {k: ministros[k] for k in sorted(ministros, key=lambda x: (ordem.index(x) if x in ordem else 99, x))},
        "fora": {k: fora[k] for k in ("presidencia", "vice", "residuos") if k in fora},
        "rotulos": rotulos,
        "relatores_fora_do_mapa": sorted(fora_do_mapa, key=lambda x: (-x[1], x[0])),
    }
    return saida


def coletar_decisoes(coletor: Any, regras: Regras, *, hoje: date, dias: int | None = None,
                     validade_h: float | None = None) -> dict[str, Any]:
    """Uma consulta (POST) com os ``dias`` dias até ontem (padrão ``limiares.janelas.
    decisoes_dias``, 365). Resposta parcial → ``RespostaParcial`` (do coletor), que sobe."""
    cfg = regras.fontes.get("stf") or {}
    janelas = regras.limiares.get("janelas") or {}
    dias = int(dias if dias is not None else janelas.get("decisoes_dias", 365))
    fim = hoje - timedelta(days=1)
    inicio = fim - timedelta(days=dias - 1)
    validade = validade_h if validade_h is not None else float((cfg.get("validade_h") or {}).get("jurisprudencia", 20))
    resposta = coletor.consulta(cfg["jurisprudencia"], consulta_decisoes(inicio, fim), validade_h=validade, elasticsearch=True)
    return ler_decisoes(resposta, regras, inicio=inicio, fim=fim)


# ── 5. a repercussão geral ──────────────────────────────────────────────────────────────────

SITUACOES_PENDENTES = ("ACORDAO DE REPERCUSSAO GERAL PUBLICADO", "ANALISADA PRELIMINAR DE REPERCUSSAO GERAL")
LINK_TEMA = ("https://portal.stf.jus.br/jurisprudenciaRepercussao/verAndamentoProcesso.asp?incidente={incidente}"
             "&numeroProcesso={numero}&classeProcesso={classe}&numeroTema={tema}")


def _celulas(linha_html: str) -> list[str]:
    return re.findall(r"<td[^>]*>(.*?)</td>", linha_html, re.DOTALL)


def _texto_html(trecho: str) -> str:
    return _limpo(html_mod.unescape(re.sub(r"<[^>]+>", " ", trecho or "")))


def _linhas_do_corpo(pagina: str) -> list[str]:
    inicio = (pagina or "").find("<tbody")
    if inicio < 0:
        raise ErroDeFormato("a lista de temas de repercussão geral não tem a tabela")
    fim = pagina.find("</tbody>", inicio)
    return re.findall(r"<tr[^>]*>(.*?)</tr>", pagina[inicio:fim if fim > 0 else None], re.DOTALL)


def classe_rg(texto: str | None) -> str | None:
    """"Há repercussão geral" → ``ha``; "... com reafirmação de jurisprudência" → ``ha_reafirmacao``;
    "Não há ... (questão infraconstitucional)" → ``nao_ha_infra``; "Não há ..." → ``nao_ha``."""
    alvo = normalizar(corrigir_acentos(texto))
    if not alvo:
        return None
    if alvo.startswith("NAO HA"):
        return "nao_ha_infra" if "INFRACONSTITUCIONAL" in alvo else "nao_ha"
    if alvo.startswith("HA"):
        return "ha_reafirmacao" if "REAFIRMACAO" in alvo else "ha"
    return None


def ler_todos_temas(pagina: str) -> list[dict[str, Any]]:
    """``todostemas.asp`` → um item por tema: número, título, ramo (raiz do 1º assunto), leading
    case (classe, número, incidente), relator (como a página escreve: o do tema, não o sucessor),
    plenário virtual, situação da repercussão geral com a data, trânsito em julgado, data da tese."""
    temas = []
    for linha in _linhas_do_corpo(pagina):
        tds = _celulas(linha)
        if len(tds) < 6:
            continue
        numero = _inteiro(_texto_html(tds[0]))
        if numero is None:
            continue
        caso = re.search(r"incidente=(\d+)&(?:amp;)?numeroProcesso=(\d+)&(?:amp;)?classeProcesso=(\w+)", tds[0])
        titulo = re.search(r"<a[^>]*>(.*?)</a>", tds[1], re.DOTALL)
        assuntos = re.search(r'title="([^"]*)"><strong>Ver Assuntos', tds[1], re.DOTALL)
        primeiro = html_mod.unescape(assuntos.group(1)).split("<br>")[0] if assuntos else ""
        ramo = re.sub(r"^\d+-", "", primeiro).split("|")[0].strip() or None
        relator_html = tds[3]
        relator = _texto_html(re.sub(r"<p>.*", "", relator_html, flags=re.DOTALL)) or None
        situacao_html = tds[4]
        situacao = _texto_html(re.sub(r"<span.*", "", situacao_html, flags=re.DOTALL))
        transito = re.search(r'title="em ([^"]+)"', situacao_html)
        temas.append({
            "numero": numero,
            "titulo": (_texto_html(titulo.group(1)) or None) if titulo else None,
            "ramo": _limpo(ramo) or None,
            "leading_case": ({"classe": caso.group(3), "numero": caso.group(2), "incidente": int(caso.group(1))}
                             if caso else None),
            "relator": relator,
            "plenario_virtual": "PLENARIO VIRTUAL" in normalizar(_texto_html(relator_html)),
            "situacao_rg": re.sub(r"\s*\d{2}/\d{2}/\d{4}\s*$", "", situacao).strip() or None,
            "desde": data_br(situacao),
            "transito": data_br(transito.group(1)) if transito else None,
            "data_tese": data_br(_texto_html(tds[5])),
        })
    return temas


def _pega(tds: list[str], i: int | None) -> str | None:
    return (tds[i] or None) if i is not None and i < len(tds) else None


def ler_exportacao(pagina: str) -> dict[int, dict[str, Any]]:
    """``exportarDados.asp`` (um "xls" que é tabela HTML) → ``{tema: {leading_case, relator,
    ha_rg, rg, julgamento, situacao, observacao}}``, com os acentos consertados. A coluna
    "Assuntos" repete a "Descrição" e não é lida."""
    linhas = _linhas_do_corpo(pagina)
    cab = re.findall(r"<th[^>]*>(.*?)</th>", pagina[: pagina.find("<tbody")], re.DOTALL)
    nomes = [normalizar(corrigir_acentos(_texto_html(c))) for c in cab]

    def col(nome: str) -> int | None:
        return nomes.index(nome) if nome in nomes else None

    i_tema, i_caso, i_rel = col("TEMA"), col("LEADING CASE"), col("RELATOR")
    i_ha, i_jul, i_sit, i_obs = col("HA REPERCUSSAO"), col("DATA DO JULGAMENTO"), col("SITUACAO DO TEMA"), col("OBSERVACAO")
    if i_tema is None or i_sit is None:
        raise ErroDeFormato("a exportação dos temas de repercussão geral perdeu as colunas Tema ou Situação do Tema")
    saida: dict[int, dict[str, Any]] = {}
    for linha in linhas:
        tds = [corrigir_acentos(_texto_html(t)) or "" for t in _celulas(linha)]
        if len(tds) <= max(i for i in (i_tema, i_sit) if i is not None):
            continue
        numero = _inteiro(tds[i_tema])
        if numero is None:
            continue

        ha = _pega(tds, i_ha)
        saida[numero] = {
            "leading_case": _pega(tds, i_caso),
            "relator": _pega(tds, i_rel),
            "ha_rg": ha,
            "rg": classe_rg(ha),
            "julgamento": data_br(_pega(tds, i_jul)),
            "situacao": _pega(tds, i_sit),
            "observacao": _pega(tds, i_obs),
        }
    return saida


def merito_pendente(rg: str | None, situacao: str | None) -> bool:
    """Mérito pendente: repercussão geral reconhecida (sem reafirmação de jurisprudência, que
    julga o mérito junto) e o tema ainda em "Acórdão de Repercussão Geral publicado" ou "Analisada
    Preliminar de Repercussão Geral" (138 temas em 22/11/2026)."""
    return rg == "ha" and normalizar(situacao) in SITUACOES_PENDENTES


def temas_rg(todos: list[dict[str, Any]], exportados: Mapping[int, Mapping[str, Any]], regras: Regras) -> list[dict[str, Any]]:
    """Cruza as duas listas pelo número do tema → um tema por linha, com ``relator_id``/``fora``
    (o relator do tema; "MINISTRO PRESIDENTE" → ``presidencia``; quem saiu → ``residuos``),
    ``situacao`` (a do tema, da exportação), ``rg`` e ``pendente`` (mérito pendente). Tema que só
    uma das listas traz entra com o que houver."""
    composicao = regras.composicao[TRIB]
    por_numero = {t["numero"]: dict(t) for t in todos}
    for numero, ex in exportados.items():
        por_numero.setdefault(numero, {"numero": numero, "titulo": None, "ramo": None, "leading_case": None,
                                       "relator": ex.get("relator"), "plenario_virtual": False, "situacao_rg": None,
                                       "desde": ex.get("julgamento"), "transito": None, "data_tese": None})
    saida = []
    for numero in sorted(por_numero):
        t = por_numero[numero]
        ex = exportados.get(numero) or {}
        relator = t.get("relator") or ex.get("relator")
        ministro_id, fora = destino_do_relator(relator, composicao)
        rg = ex.get("rg") or classe_rg(t.get("situacao_rg"))
        situacao = ex.get("situacao")
        caso = t.get("leading_case")
        saida.append({
            **t,
            "relator": relator,
            "relator_id": ministro_id,
            "fora": fora,
            "rg": rg,
            "situacao": situacao,
            "desde": t.get("desde") or ex.get("julgamento"),
            "pendente": merito_pendente(rg, situacao),
            "cancelado": normalizar(situacao) == "CANCELADO",
            "link": LINK_TEMA.format(incidente=caso["incidente"], numero=caso["numero"], classe=caso["classe"], tema=numero)
            if caso else None,
        })
    return saida


def pendentes_por_relator(temas: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Temas com mérito pendente: ``{"total", "ministros": {id: n}, "fora": {linha: n},
    "relator_saiu": [[nome, n]]}`` (quem saiu, pelo nome como a página escreve)."""
    ministros: Counter[str] = Counter()
    fora: Counter[str] = Counter()
    saiu: Counter[str] = Counter()
    for t in temas:
        if not t.get("pendente"):
            continue
        if t.get("relator_id"):
            ministros[t["relator_id"]] += 1
        else:
            fora[t.get("fora") or "residuos"] += 1
            if (t.get("fora") or "residuos") == "residuos":
                saiu[_limpo(re.sub(r"^MIN\.?\s*", "", t.get("relator") or ""))] += 1
    return {
        "total": sum(ministros.values()) + sum(fora.values()),
        "ministros": dict(sorted(ministros.items(), key=lambda x: (-x[1], x[0]))),
        "fora": dict(sorted(fora.items(), key=lambda x: (-x[1], x[0]))),
        "relator_saiu": [[n, c] for n, c in sorted(saiu.items(), key=lambda x: (-x[1], x[0]))],
    }


def coletar_rg(coletor: Any, regras: Regras, *, validade_h: float | None = None) -> dict[str, Any]:
    """As duas páginas (~5 MB e ~4 MB; a coleta é semanal: ``fontes.stf.validade_h.rg``) →
    ``{"temas": [...], "pendentes": pendentes_por_relator(...)}``."""
    cfg = regras.fontes.get("stf") or {}
    validade = validade_h if validade_h is not None else float((cfg.get("validade_h") or {}).get("rg", 160))
    todos = ler_todos_temas(coletor.texto(cfg["rg_temas"], validade_h=validade))
    exportados = ler_exportacao(coletor.texto(cfg["rg_exportar"], validade_h=validade))
    temas = temas_rg(todos, exportados, regras)
    return {"temas": temas, "pendentes": pendentes_por_relator(temas)}


# ── 6. a pauta (sessões presenciais) ────────────────────────────────────────────────────────

ORGAO_DO_COLEGIADO = {"PLENARIO": "PL", "PRIMEIRA TURMA": "T1", "SEGUNDA TURMA": "T2", "1ª TURMA": "T1", "2ª TURMA": "T2"}
# O resumo oficial do processo (``resumoTema``) só é guardado nas ações de controle concentrado e
# nos recursos com repercussão geral: nos demais, o texto pode trazer nome de pessoa.
CLASSES_CONTROLE = ("ADI", "ADC", "ADO", "ADPF")
RESUMO_MAX = 1500  # caracteres do resumo guardados (limiares.agenda.resumo_max no config vence)
_HORA = re.compile(r"\b([01]?\d|2[0-3])[:h]([0-5]\d)\b")


def _orgao(colegiado: Mapping[str, Any]) -> str | None:
    desc = normalizar(colegiado.get("descricao"))
    for chave, orgao in ORGAO_DO_COLEGIADO.items():
        if normalizar(chave) == desc:
            return orgao
    return {"TP": "PL", "1T": "T1", "2T": "T2", "PT": "T1", "ST": "T2"}.get(normalizar(colegiado.get("codigo")))


def hora_da_sessao(texto: Any) -> str | None:
    """``"25/11/2026 14:00"`` → ``"14:00"``; sem hora no texto, ``None`` (a hora nunca é inventada)."""
    m = _HORA.search(str(texto or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def cortar_no_fim_de_palavra(texto: Any, maximo: int) -> str | None:
    """O texto limpo, cortado no fim de uma palavra com reticências quando passa de ``maximo``."""
    limpo = _limpo(str(texto)) if texto is not None else ""
    if not limpo:
        return None
    if len(limpo) <= maximo:
        return limpo
    corte = limpo[: max(1, maximo - 1)].rsplit(" ", 1)[0]
    return corte.rstrip(" ,;:.") + "…"


def _resumo_max(regras: Regras) -> int:
    try:
        return max(60, int((regras.limiares.get("agenda") or {}).get("resumo_max") or RESUMO_MAX))
    except (TypeError, ValueError):
        return RESUMO_MAX


def _classes_controle(regras: Regras) -> frozenset[str]:
    classes = (regras.limiares.get("agenda") or {}).get("classes_controle") or CLASSES_CONTROLE
    return frozenset(str(c) for c in classes)


def _lista(valor: Any) -> list[Any]:
    """Uma lista de objetos, ou vazia quando o campo falta ou vem em outra forma."""
    return [x for x in valor if isinstance(x, Mapping)] if isinstance(valor, (list, tuple)) else []


def _sessoes_do_dia(dados: Mapping[str, Any]) -> Iterator[tuple[str | None, Mapping[str, Any]]]:
    """Cada sessão da resposta com o órgão do colegiado (``PL``, ``T1``, ``T2``)."""
    for colegiado in _lista(dados.get("colegiados")):
        orgao = _orgao(colegiado)
        for sessao in _lista(colegiado.get("sessoes")):
            yield orgao, sessao


def ler_sessao_presencial(dados: Mapping[str, Any], regras: Regras) -> list[dict[str, Any]]:
    """``calendario-service.asp?dados=sessao-presencial&data=…`` → um item por processo pautado:
    ``{data, orgao, sessao, hora, sessao_numero, sessao_ano, ordem, classe, numero, id, incidente,
    resumo, relator_id, relator_fora, vista_id, vista, destaque_id, destaque, repercussao_geral}``.

    Vista e destaque pelo nome do ministro, por processo. ``hora``, ``sessao_numero`` e
    ``sessao_ano`` são os da sessão; ``ordem`` é a posição do processo na pauta da sessão (a
    partir de 1); ``incidente`` é a cadeia que a fonte informa ("REFERENDO NA MEDIDA CAUTELAR NA
    AÇÃO DIRETA DE INCONSTITUCIONALIDADE"). ``resumo`` é o resumo oficial (``resumoTema``), cortado
    no fim de uma palavra, e só nas ações de controle concentrado e nos recursos com repercussão
    geral: nos demais fica ``None`` (o texto pode trazer nome de pessoa). Campo ausente na resposta
    vira ``None``; nada aqui levanta por campo que falta."""
    composicao = regras.composicao[TRIB]
    controle, maximo = _classes_controle(regras), _resumo_max(regras)
    itens = []
    for orgao, sessao in _sessoes_do_dia(dados):
        dia = data_br(sessao.get("dataInicio")) or data_br(dados.get("data"))
        hora = hora_da_sessao(sessao.get("dataInicio"))
        for ordem, inc in enumerate(_lista(sessao.get("incidentes")), start=1):
            classe = _limpo(inc.get("classe"))
            numero = _so_digitos(inc.get("numero"))
            relator_id, relator_fora = destino_do_relator(inc.get("relator"), composicao)
            vista = _limpo(inc.get("ministroVista")) or None
            destaque = _limpo(inc.get("destaque")) or None
            repercussao = normalizar(inc.get("repercussaoGeral")) == "S"
            com_resumo = repercussao or classe.split("-", 1)[0] in controle
            itens.append({
                "data": dia,
                "orgao": orgao,
                "sessao": _limpo(sessao.get("id")) or None,
                "hora": hora,
                "sessao_numero": _inteiro(sessao.get("numero")),
                "sessao_ano": _inteiro(sessao.get("ano")),
                "ordem": ordem,
                "classe": classe,
                "numero": numero,
                "id": f"{TRIB}-{classe}-{numero}",
                "incidente": _limpo(inc.get("cadeia")) or None,
                "resumo": cortar_no_fim_de_palavra(inc.get("resumoTema"), maximo) if com_resumo else None,
                "relator_id": relator_id,
                "relator_fora": relator_fora,
                "vista": vista,
                "vista_id": destino_do_relator(vista, composicao)[0] if vista else None,
                "destaque": destaque,
                "destaque_id": destino_do_relator(destaque, composicao)[0] if destaque else None,
                "repercussao_geral": repercussao,
            })
    return itens


def ler_sessoes_do_dia(dados: Mapping[str, Any]) -> list[dict[str, Any]]:
    """As sessões presenciais que a resposta de um dia traz, com ou sem processos:
    ``{data, orgao, sessao, hora, numero, ano, tipo, itens}`` (``itens``: quantos processos a
    sessão lista). Dia sem sessão na resposta → lista vazia."""
    saida = []
    for orgao, sessao in _sessoes_do_dia(dados):
        saida.append({
            "data": data_br(sessao.get("dataInicio")) or data_br(dados.get("data")),
            "orgao": orgao,
            "sessao": _limpo(sessao.get("id")) or None,
            "hora": hora_da_sessao(sessao.get("dataInicio")),
            "numero": _inteiro(sessao.get("numero")),
            "ano": _inteiro(sessao.get("ano")),
            "tipo": _limpo(sessao.get("tipo")) or None,
            "itens": len(_lista(sessao.get("incidentes"))),
        })
    return saida


def ler_calendario(dados: Mapping[str, Any], mes: int, ano: int) -> dict[str, Any]:
    """``calendario-service.asp?dados=calendarios&mes=&ano=`` → ``{presenciais: [datas],
    virtuais: [{inicio, fim, PL, T1, T2}]}`` (o Plenário Virtual só dá a contagem por colegiado)."""
    presenciais = []
    for d in dados.get("diasPresenciais") or ():
        try:
            presenciais.append(date(ano, mes, int(d)).isoformat())
        except (TypeError, ValueError):
            continue
    virtuais = [{"inicio": data_br(v.get("dataInicial")), "fim": data_br(v.get("dataFinal")),
                 "PL": _inteiro(v.get("qtdProcessosPlenario")) or 0, "T1": _inteiro(v.get("qtdPrimeiraTurma")) or 0,
                 "T2": _inteiro(v.get("qtdSegundaTurma")) or 0}
                for v in _lista(dados.get("julgamentosVirtuais"))]
    virtuais.sort(key=lambda v: (v["inicio"] or "", v["fim"] or ""))
    return {"presenciais": sorted(presenciais), "virtuais": virtuais}


def coletar_pauta(coletor: Any, regras: Regras, *, hoje: date, dias: int = 14) -> dict[str, Any]:
    """As sessões presenciais de ``hoje`` a ``hoje + dias`` (o calendário do mês e do seguinte,
    e uma consulta por dia de sessão) → ``{janela, sessoes: [[data, orgao, itens]], itens,
    virtuais, avisos, dias, calendarios}``.

    ``dias`` lista TODO dia que o calendário marca como de sessão presencial na janela, com ou sem
    processos: ``{data, lida, sessoes}``, com ``lida`` falso quando a consulta do dia falhou (a
    pauta daquele dia não foi lida nesta coleta) e ``sessoes`` como :func:`ler_sessoes_do_dia`
    devolve (vazia quando a fonte ainda não publicou a pauta do dia). ``calendarios`` diz quantos
    meses foram lidos e quantos falharam (com falha, os dias daquele mês são desconhecidos).
    ``virtuais`` traz as contagens das sessões virtuais por colegiado, como o calendário informa."""
    cfg = regras.fontes.get("stf") or {}
    base = cfg["pauta"]
    validade = float((cfg.get("validade_h") or {}).get("pauta", 20))
    fim = hoje + timedelta(days=dias)
    meses = sorted({(hoje.year, hoje.month), (fim.year, fim.month)})
    presenciais: list[str] = []
    virtuais: list[dict[str, Any]] = []
    avisos: list[str] = []
    lidos = 0
    for ano, mes in meses:
        try:
            cal = ler_calendario(coletor.json(f"{base}?dados=calendarios&mes={mes}&ano={ano}", validade_h=validade), mes, ano)
        except (RecusaDoHost, CaminhoVedado):
            raise
        except (FetchError, ValueError, AttributeError, TypeError) as exc:
            avisos.append(f"STF, calendário da pauta de {mes:02d}/{ano}: {exc}")
            continue
        lidos += 1
        presenciais += [d for d in cal["presenciais"] if hoje.isoformat() <= d <= fim.isoformat()]
        virtuais += [v for v in cal["virtuais"] if v not in virtuais]
    itens: list[dict[str, Any]] = []
    por_dia: list[dict[str, Any]] = []
    for dia in sorted(set(presenciais)):
        data = date.fromisoformat(dia).strftime("%d/%m/%Y")
        try:
            resposta = coletor.json(f"{base}?dados=sessao-presencial&data={data}", validade_h=validade)
            if not isinstance(resposta, Mapping):
                raise ValueError("a resposta da pauta do dia não é um objeto")
            itens += ler_sessao_presencial(resposta, regras)
            por_dia.append({"data": dia, "lida": True, "sessoes": ler_sessoes_do_dia(resposta)})
        except (RecusaDoHost, CaminhoVedado):
            raise
        except (FetchError, ValueError, AttributeError, TypeError) as exc:
            avisos.append(f"STF, pauta de {data}: {exc}")
            por_dia.append({"data": dia, "lida": False, "sessoes": []})
    sessoes = Counter((i["data"], i["orgao"]) for i in itens)
    return {
        "janela": [hoje.isoformat(), fim.isoformat()],
        "sessoes": [[d, o, n] for (d, o), n in sorted(sessoes.items(), key=lambda x: (x[0][0] or "", x[0][1] or ""))],
        "itens": itens,
        "virtuais": sorted(virtuais, key=lambda v: (v["inicio"] or "", v["fim"] or "")),
        "avisos": avisos,
        "dias": por_dia,
        "calendarios": {"lidos": lidos, "falhas": len(meses) - lidos},
    }


__all__ = [
    "Acervo", "ErroDeFormato", "Planilha", "coletar_acervo", "coletar_atas", "coletar_decisoes", "coletar_pauta",
    "coletar_rg", "coluna_da_referencia", "consulta_decisoes", "corrigir_acentos", "cortar_no_fim_de_palavra", "data_br",
    "descolar", "destino_do_relator", "flags_do_registro", "hora_da_sessao", "ler_acervo", "ler_ata", "ler_calendario",
    "ler_decisoes", "ler_exportacao", "ler_feitos_presidencia", "ler_indice_atas", "ler_planilha", "ler_sessao_presencial",
    "ler_sessoes_do_dia", "ler_todos_temas", "linhas_xlsx", "merito_pendente", "nomes_conhecidos", "onde_stf",
    "pendentes_por_relator",
    "processo_do_registro", "representacao_institucional", "representantes", "separar_polo", "serial_excel",
    "temas_rg", "uf_do_nome",
]
