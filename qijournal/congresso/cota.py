"""A cota parlamentar: o que cada deputado e senador pede de reembolso, e a quem o dinheiro vai.

Duas fontes oficiais, um lançamento por documento fiscal:

- **Câmara**, a Cota para o Exercício da Atividade Parlamentar (CEAP): um arquivo por
  ano em ``dadosabertos.camara.leg.br/arquivos/cotas/Ano-AAAA.csv.zip`` (reserva
  documentada em ``www.camara.leg.br/cotas``), um CSV com ``;`` dentro do zip, refeito
  todo dia. O deputado vem pelo ``ideCadastro`` (o id da API, o das fichas ``cd-<id>``)
  e cada nota traz o link para o comprovante na página da Câmara;
- **Senado**, a CEAPS: ``adm.senado.gov.br/adm-dadosabertos/api/v1/senadores/despesas_ceaps/AAAA``,
  uma lista JSON por ano. O senador vem pelo ``codSenador``, o mesmo código da API
  legislativa (o das fichas ``sf-<código>``). Não há link para o comprovante.

O **valor** de cada lançamento é o que a Casa reembolsou, já sem a glosa (a parte que
ela recusou). Na Câmara, é o ``vlrLiquido`` menos o ``vlrRestituicao`` (o que o deputado
devolveu depois, numa linha à parte que repete a nota): o ``vlrDocumento`` é o valor da
nota inteira e se repete em cada linha quando a nota se divide entre subcotas ou é paga
em parcelas. A exceção é a complementação do auxílio-moradia (subcota 40), em que o
``vlrLiquido`` vem com o sinal trocado: ali vale ``vlrDocumento − vlrGlosa``. Estornos
(bilhetes cancelados, devoluções) entram negativos e abatem o total, como no registro.

Fica fora das contas, para que anos e pessoas se comparem do mesmo jeito (o painel diz o quê):

- **Câmara**: as passagens aéreas compradas pela agência da Câmara (SIGEPA), em todos
  os anos, porque o arquivo oficial deixou de trazê-las em agosto de 2025. :func:`camara`
  as devolve (categoria "PASSAGEM AÉREA - SIGEPA") e :func:`resumir` as separa: só o
  total de cada ano por pessoa, como contexto. As linhas das lideranças, que não têm
  deputado, nem entram;
- **Senado**: o reembolso de moradia (``tipoDespesa`` vazio), que o Senado lança no
  mesmo arquivo mas não conta como cota.

Nenhum CPF é lido nem guardado: nem o do deputado (a coluna ``cpf`` do arquivo da
Câmara), nem o de fornecedor pessoa física (o campo ``cnpj`` só guarda CNPJ; o Senado
já publica o CPF mascarado). O nome de pessoa física também não sai no painel: quem não
tem CNPJ aparece como "pessoa física".

As contas do painel (:func:`resumir`) são somas e contagens sobre o registro oficial,
em centavos inteiros, sem amostra e sem IA: cada documento aponta para a nota.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import statistics
import zipfile
import zlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .. import text
from . import fontes
from .fontes import Coletor

log = logging.getLogger(__name__)

CAMARA_COTAS = f"{fontes.CAMARA_ARQUIVOS}/cotas/Ano-{{ano}}.csv.zip"
CAMARA_COTAS_RESERVA = "https://www.camara.leg.br/cotas/Ano-{ano}.csv.zip"
SENADO_CEAPS = f"{fontes.SENADO_ADM}/api/v1/senadores/despesas_ceaps/{{ano}}"

# O que fica fora das contas de cada Casa (o campo "sem" do painel).
SEM = {
    "cd": "passagens aéreas compradas pela agência da Câmara (SIGEPA), que o arquivo oficial deixou de trazer em agosto de 2025",
    "sf": "o reembolso de moradia, que o Senado lança no mesmo arquivo mas não conta como cota",
}

VALIDADE_ANTIGOS_H = 24 * 30  # anos fechados: o arquivo quase não muda
MES_ATRASADOS = 4  # Câmara: até abril, o ano anterior ainda recebe documentos atrasados
SUBCOTA_MORADIA = "40"  # complementação do auxílio-moradia: o vlrLiquido vem com o sinal trocado
SIGEPA = "SIGEPA"  # na categoria: passagem comprada pela agência da Câmara
ANO_SIGEPA = 2024  # o último ano inteiro em que o arquivo trouxe essas passagens (2025, só até julho)
PESSOA_FISICA = "pessoa física"  # o que aparece no lugar do nome de fornecedor sem CNPJ
DATA_MINIMA = 2000  # Senado: há datas como "0202-02-24"; antes disso, a data fica vazia

LISTA = 15  # quem mais e menos gasta, fornecedores, concentração
REPETIDAS = 20
GLOSAS = 10
POR_PESSOA = 6  # fornecedores e maiores documentos de cada um
MINIMO_MESES = 6  # meses com despesa para a média mensal entrar nas listas e na mediana
CONCENTRACAO_PCT = 25  # fatia do próprio total paga a um fornecedor só
CONCENTRACAO_MINIMO = 50_000  # reais pagos a esse fornecedor

# CNPJs de mentira que a Câmara usa para agrupar despesas sem fornecedor de verdade:
# 01 celular funcional, 02 linha direta, 06 ramal, 07 Correios, 10 fornecedor do exterior.
_PSEUDO = "0" * 12
# O CNPJ da própria Câmara, que aparece como "fornecedor" da complementação do auxílio-moradia.
_DA_CASA = frozenset(("00530352000159",))

# Número de documento que não identifica a nota: o recibo numerado pelo mês ("9/2026",
# "08/26", "102026"), "0", "S/N" e afins (sem dígito nenhum).
_MES_ANO = re.compile(r"\d{1,2}[/.-]\d{2}(?:\d{2})?|(?:0?[1-9]|1[0-2])20\d{2}")
_URL_CAMARA = re.compile(r"https://www\.camara\.leg\.br/\S+")


class CotaIndisponivel(RuntimeError):
    """Um ano da cota não veio (fonte fora do ar ou arquivo fora do formato)."""


@dataclass(frozen=True, slots=True)
class Despesa:
    """Um lançamento da cota, como a Casa registra."""

    pessoa: int  # deputado: id da API da Câmara; senador: código da API legislativa
    ano: int
    mes: int  # o mês do reembolso (a competência), de 1 a 12
    categoria: str  # Câmara: txtDescricao (a subcota); Senado: tipoDespesa
    fornecedor: str
    cnpj: str  # só os dígitos; "" sem CNPJ (pessoa física)
    numero: str  # o número do documento, como veio
    data: str  # emissão, AAAA-MM-DD ou ""
    valor: float  # reembolsado, sem a glosa; estornos e devoluções entram negativos
    glosa: float  # o que a Câmara recusou da nota (o Senado não informa)
    url: str  # o comprovante na página da Câmara ("" no Senado)


# ── normalização ─────────────────────────────────────────────────────────────


def quantia(bruto: Any) -> float:
    """Número da fonte → float: aceita ``1234.56``, ``1234,56`` e ``1.234,56``; vazio ou
    ilegível vira 0."""
    if isinstance(bruto, bool):
        return 0.0
    if isinstance(bruto, (int, float)):
        numero = float(bruto)
    else:
        s = str(bruto or "").strip()
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            numero = float(s) if s else 0.0
        except ValueError:
            return 0.0
    return numero if numero == numero and abs(numero) != float("inf") else 0.0


def _cnpj_valido(digitos: str) -> bool:
    if len(digitos) != 14 or not digitos.isdigit() or len(set(digitos)) == 1:
        return False
    pesos = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)

    def dv(base: str) -> str:
        resto = sum(int(a) * b for a, b in zip(base, pesos[-len(base):])) % 11
        return "0" if resto < 2 else str(11 - resto)

    return digitos[12] == dv(digitos[:12]) and digitos[13] == dv(digitos[:13])


def cnpj(bruto: Any) -> str:
    """Só os dígitos do CNPJ do fornecedor. A máscara da fonte às vezes está errada
    (``085.324.290/0013-1``): vale a sequência de 14 dígitos. Com 12 ou 13 dígitos, o
    zero da frente se perdeu: completa se os dígitos verificadores fecharem. CPF (11
    dígitos, ou mascarado com ``*``) fica vazio: não se guarda."""
    s = str(bruto or "")
    if "*" in s:
        return ""
    digitos = re.sub(r"\D", "", s)
    if len(digitos) == 14:
        return digitos
    if 12 <= len(digitos) < 14 and _cnpj_valido(digitos.zfill(14)):
        return digitos.zfill(14)
    return ""


def e_sigepa(d: Despesa) -> bool:
    """Passagem aérea comprada pela agência da Câmara: fora das contas (ver :data:`SEM`)."""
    return SIGEPA in d.categoria.upper()


def pseudo_cnpj(digitos: str) -> bool:
    """Um CNPJ que não é de fornecedor: ``00000000000001`` a ``…07`` e ``…10``, sob os
    quais a Câmara agrupa o celular funcional, os ramais, os Correios e os fornecedores do
    exterior, e o da própria Câmara (a complementação do auxílio-moradia)."""
    return digitos.startswith(_PSEUDO) or digitos in _DA_CASA


def numero_valido(numero: str) -> bool:
    """O número identifica a nota: tem dígito, não é só o mês e o ano (``9/2026``, ``08/26``,
    ``102026``: há fornecedor que numera assim os recibos de aluguel, e a mesma "nota"
    aparece em vários gabinetes) e não é "0", "S/N" ou vazio."""
    n = (numero or "").strip()
    if not any(c.isdigit() for c in n) or _MES_ANO.fullmatch(n):
        return False
    return bool(_canonico(n))


def _canonico(numero: str) -> str:
    """O número para comparar notas: só letras e dígitos, em maiúsculas, sem zeros à
    esquerda (``000.001.317`` e ``1317`` são a mesma nota)."""
    return re.sub(r"[^0-9A-Z]", "", text.strip_accents(numero).upper()).lstrip("0")


def _texto(bruto: Any) -> str:
    return re.sub(r"\s+", " ", str(bruto or "")).strip()


# Um CPF (com ou sem pontos, inteiro ou mascarado) dentro do nome: o microempreendedor individual
# tem o CPF no nome da empresa ("FULANO DE TAL 123.456.789-00"), e o CPF não é publicado aqui.
_CPF_NO_NOME = re.compile(r"[\s,;:-]*\b\d{3}[.\s]?(?:\d{3}|\*{3})[.\s]?(?:\d{3}|\*{3})\s?-?\s?\d{2}\b")


def nome_do_fornecedor(bruto: Any) -> str:
    """O nome do fornecedor como veio, sem um CPF que esteja dentro dele."""
    return _texto(_CPF_NO_NOME.sub(" ", str(bruto or ""))).strip(" -,;:")


def _data(bruto: Any, teto: int) -> str:
    """``AAAA-MM-DD`` se for uma data de verdade entre 2000 e o fim de ``teto``; senão ``""``."""
    s = str(bruto or "").strip()[:10]
    try:
        dia = date.fromisoformat(s)
    except ValueError:
        return ""
    return s if DATA_MINIMA <= dia.year <= teto else ""


def _url(bruto: Any) -> str:
    """Só links para a página da Câmara: o arquivo é dado de fora e vira link no painel."""
    s = str(bruto or "").strip()
    return s if _URL_CAMARA.fullmatch(s) else ""


# ── Câmara ───────────────────────────────────────────────────────────────────

# As colunas lidas (a ``cpf``, de propósito, não está aqui).
_COLUNAS = (
    "ideCadastro",
    "numAno",
    "numMes",
    "numSubCota",
    "txtDescricao",
    "txtFornecedor",
    "txtCNPJCPF",
    "txtNumero",
    "datEmissao",
    "vlrDocumento",
    "vlrGlosa",
    "vlrLiquido",
    "vlrRestituicao",
    "urlDocumento",
)
_OPCIONAIS = frozenset(("numSubCota", "vlrRestituicao", "urlDocumento"))


def validade_camara(ano: int, *, ano_corrente: int, mes: int, validade_h: float, validade_antigos_h: float) -> float:
    """O ano corrente vale ``validade_h``; o anterior também, até abril (``mes`` é o mês de
    hoje), porque ainda chegam documentos atrasados; os outros, ``validade_antigos_h``."""
    if ano >= ano_corrente or (ano == ano_corrente - 1 and mes <= MES_ATRASADOS):
        return validade_h
    return validade_antigos_h


def camara(
    coletor: Coletor,
    anos: Iterable[int],
    *,
    ano_corrente: int,
    validade_h: float = 20.0,
    validade_antigos_h: float = VALIDADE_ANTIGOS_H,
) -> list[Despesa]:
    """Os lançamentos da cota da Câmara em ``anos``, dos arquivos anuais (o espelho de
    dados abertos; na falha, a reserva). Vêm também as passagens SIGEPA, que
    :func:`resumir` deixa fora das contas (:func:`e_sigepa`). Um ano que não vem levanta
    :class:`CotaIndisponivel`."""
    mes = date.today().month
    saida: list[Despesa] = []
    for ano in sorted(set(anos)):
        validade = validade_camara(ano, ano_corrente=ano_corrente, mes=mes, validade_h=validade_h, validade_antigos_h=validade_antigos_h)
        saida += _camara_ano(coletor, ano, ano_corrente=ano_corrente, validade_h=validade)
    return saida


def _camara_ano(coletor: Coletor, ano: int, *, ano_corrente: int, validade_h: float) -> list[Despesa]:
    erros: list[str] = []
    for url in (CAMARA_COTAS.format(ano=ano), CAMARA_COTAS_RESERVA.format(ano=ano)):
        try:
            caminho = coletor.arquivo(url, validade_h=validade_h)
        except Exception as exc:  # noqa: BLE001 — tenta a reserva; a falha das duas vira o erro do ano
            erros.append(str(exc))
            continue
        try:
            return _ler_zip(caminho, ano, teto=max(ano, ano_corrente))
        except (zipfile.BadZipFile, zlib.error, EOFError, ValueError, UnicodeDecodeError, csv.Error, OSError) as exc:
            caminho.unlink(missing_ok=True)  # o arquivo estragado não fica valendo no cache
            erros.append(f"{url}: arquivo ilegível ({exc})")
    raise CotaIndisponivel(f"Câmara: cota parlamentar de {ano} indisponível ({'; '.join(erros)})")


def _ler_zip(caminho: Path, ano: int, *, teto: int) -> list[Despesa]:
    """Os lançamentos do CSV dentro do zip anual da Câmara."""
    with zipfile.ZipFile(caminho) as pacote:
        nomes = sorted(n for n in pacote.namelist() if n.lower().endswith(".csv"))
        if not nomes:
            raise ValueError("o zip não traz CSV")
        with pacote.open(nomes[0]) as bruto:
            leitor = csv.reader(io.TextIOWrapper(bruto, encoding="utf-8-sig", newline=""), delimiter=";")
            cabecalho = [c.strip() for c in next(leitor, [])]
            posicao = {c: i for i, c in enumerate(cabecalho) if c in _COLUNAS}
            faltam = [c for c in _COLUNAS if c not in posicao and c not in _OPCIONAIS]
            if faltam:
                raise ValueError(f"faltam as colunas {', '.join(faltam)}")
            return _linhas_camara(leitor, posicao, ano, teto)


def _linhas_camara(leitor: Iterable[list[str]], posicao: Mapping[str, int], ano: int, teto: int) -> list[Despesa]:
    # Cada linha ganha uma célula vazia no fim: é dela que se lê a coluna opcional ausente.
    (
        i_pessoa, i_ano, i_mes, i_subcota, i_categoria, i_fornecedor, i_cnpj, i_numero,
        i_data, i_documento, i_glosa, i_liquido, i_restituicao, i_url,
    ) = (posicao.get(c, -1) for c in _COLUNAS)
    largura = max(posicao.values()) + 1
    unico: dict[str, str] = {}  # as repetições (categoria, fornecedor, CNPJ, data) num objeto só
    guarda = unico.setdefault
    saida: list[Despesa] = []
    liderancas = sigepa = sem_mes = 0
    for linha in leitor:
        if len(linha) < largura:
            linha += [""] * (largura - len(linha))
        linha.append("")
        pessoa = fontes.inteiro(linha[i_pessoa])
        if pessoa is None:
            liderancas += 1
            continue
        categoria = _texto(linha[i_categoria])
        if SIGEPA in categoria.upper():
            sigepa += 1
        mes = fontes.inteiro(linha[i_mes])
        if mes is None or not 1 <= mes <= 12:
            sem_mes += 1
            continue
        documento, glosa = quantia(linha[i_documento]), quantia(linha[i_glosa])
        liquido, restituicao = quantia(linha[i_liquido]), quantia(linha[i_restituicao])
        if linha[i_subcota].strip() == SUBCOTA_MORADIA:
            reembolso = documento - glosa - restituicao
        else:
            reembolso = liquido - restituicao
        if restituicao and not liquido:
            glosa = 0.0  # a linha da devolução repete a nota: a glosa já contou na original
        fornecedor = nome_do_fornecedor(linha[i_fornecedor])
        doc_cnpj = cnpj(linha[i_cnpj])
        emissao = _data(linha[i_data], teto)
        saida.append(
            Despesa(
                pessoa=pessoa,
                ano=fontes.inteiro(linha[i_ano]) or ano,
                mes=mes,
                categoria=guarda(categoria, categoria),
                fornecedor=guarda(fornecedor, fornecedor),
                cnpj=guarda(doc_cnpj, doc_cnpj),
                numero=linha[i_numero].strip(),
                data=guarda(emissao, emissao),
                valor=round(reembolso, 2),
                glosa=round(glosa, 2),
                url=_url(linha[i_url]),
            )
        )
    log.info(
        "Câmara %d: %d lançamentos da cota, %d deles passagens SIGEPA (fora: %d de lideranças, %d sem mês)",
        ano,
        len(saida),
        sigepa,
        liderancas,
        sem_mes,
    )
    return saida


# ── Senado ───────────────────────────────────────────────────────────────────


def senado(
    coletor: Coletor,
    anos: Iterable[int],
    *,
    ano_corrente: int,
    validade_h: float = 20.0,
    validade_antigos_h: float = VALIDADE_ANTIGOS_H,
) -> list[Despesa]:
    """Os lançamentos da CEAPS em ``anos``. O ano corrente e o anterior valem
    ``validade_h`` (o anterior ainda recebe estornos meses depois); os outros,
    ``validade_antigos_h``. Um ano que não vem levanta :class:`CotaIndisponivel`."""
    saida: list[Despesa] = []
    for ano in sorted(set(anos)):
        validade = validade_h if ano >= ano_corrente - 1 else validade_antigos_h
        try:
            dados = coletor.json(SENADO_CEAPS.format(ano=ano), validade_h=validade)
        except Exception as exc:  # noqa: BLE001 — quem chama decide o que fazer com o ano que falta
            raise CotaIndisponivel(f"Senado: cota parlamentar de {ano} indisponível ({exc})") from exc
        if not isinstance(dados, list):
            raise CotaIndisponivel(f"Senado: cota parlamentar de {ano} fora do formato (esperava uma lista, veio {type(dados).__name__})")
        saida += _linhas_senado(dados, ano, teto=max(ano, ano_corrente))
    return saida


def _linhas_senado(dados: list[Any], ano: int, *, teto: int) -> list[Despesa]:
    """Cada item da CEAPS: ``codSenador``, ``mes``, ``tipoDespesa``, ``cpfCnpj``,
    ``fornecedor``, ``documento``, ``data``, ``valorReembolsado`` (e ``id``, ``ano``,
    ``nomeSenador``, ``tipoDocumento``, ``detalhamento``, que não se usam). Linhas iguais
    em tudo menos o ``id`` ficam: são do registro oficial."""
    unico: dict[str, str] = {}
    guarda = unico.setdefault
    saida: list[Despesa] = []
    moradia = invalidas = 0
    for item in dados:
        if not isinstance(item, Mapping):
            invalidas += 1
            continue
        pessoa = fontes.inteiro(item.get("codSenador"))
        mes = fontes.inteiro(item.get("mes"))
        if pessoa is None or mes is None or not 1 <= mes <= 12:
            invalidas += 1
            continue
        categoria = _texto(item.get("tipoDespesa"))
        if not categoria:
            moradia += 1  # tipoDespesa vazio: reembolso de moradia, fora da cota
            continue
        fornecedor = nome_do_fornecedor(item.get("fornecedor"))
        doc_cnpj = cnpj(item.get("cpfCnpj"))
        emissao = _data(item.get("data"), teto)
        saida.append(
            Despesa(
                pessoa=pessoa,
                ano=ano,  # o ano do arquivo (o do reembolso)
                mes=mes,
                categoria=guarda(categoria, categoria),
                fornecedor=guarda(fornecedor, fornecedor),
                cnpj=guarda(doc_cnpj, doc_cnpj),
                numero=_texto(item.get("documento")),
                data=guarda(emissao, emissao),
                valor=round(quantia(item.get("valorReembolsado")), 2),
                glosa=0.0,
                url="",
            )
        )
    log.info("Senado %d: %d lançamentos da cota (fora: %d de moradia, %d sem senador ou mês)", ano, len(saida), moradia, invalidas)
    return saida


# ── o painel ─────────────────────────────────────────────────────────────────


def _reais(centavos: int | float) -> float:
    return round(centavos / 100, 2)


def _mais_comum(contagem: Counter[str]) -> str:
    """A grafia mais frequente (empate: a primeira em ordem alfabética)."""
    return min(contagem.items(), key=lambda t: (-t[1], t[0]))[0] if contagem else ""


def _chave_fornecedor(d: Despesa) -> str:
    """CNPJ; sem CNPJ (pessoa física), o nome normalizado com ``~`` na frente; pseudo-CNPJ: ``""``."""
    if d.cnpj:
        return "" if pseudo_cnpj(d.cnpj) else d.cnpj
    nome = text.normalize(d.fornecedor)
    return f"~{nome}" if nome else ""


@dataclass
class _Conta:
    """O que se soma de cada pessoa (em centavos)."""

    anos: Counter[int] = field(default_factory=Counter)  # todos os anos recebidos
    sigepa: Counter[int] = field(default_factory=Counter)  # Câmara: as passagens da agência, por ano
    total: int = 0  # daqui para baixo, só os meses fechados da legislatura
    meses: Counter[str] = field(default_factory=Counter)
    categorias: Counter[str] = field(default_factory=Counter)
    fornecedores: Counter[str] = field(default_factory=Counter)
    notas: Counter[str] = field(default_factory=Counter)
    glosa: int = 0
    glosas: int = 0
    documentos: dict[str, list[Any]] = field(default_factory=dict)  # com o mês corrente: chave → [centavos, centavos da maior linha, a linha]

    @property
    def n_meses(self) -> int:
        """Meses com despesa (saldo positivo no mês: um mês só de estorno não conta)."""
        return sum(1 for v in self.meses.values() if v > 0)

    @property
    def media(self) -> float:
        return self.total / self.n_meses if self.n_meses else 0.0


def resumir(
    despesas: Iterable[Despesa],
    *,
    casa: str,
    inicio: str,
    hoje: str,
    pessoas: Mapping[int, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """O painel da cota de uma Casa e o resumo de cada pessoa.

    Só conta quem está em ``pessoas`` (id → ``{"k", "nome", "partido", "uf", "ex"}``).
    As somas (totais, meses, média mensal, categorias, fornecedores, concentração, glosas)
    vão do mês de ``inicio`` ao último **mês fechado**: o mês de ``hoje`` (AAAA-MM-DD),
    ainda aberto e com documentos por chegar, fica fora delas, para não puxar a média para
    baixo. Ele conta no total do ano (``anos``) e nas listas de notas (as repetidas e os
    maiores documentos de cada um).

    As listas de pessoas do painel (quem mais e menos gasta, concentração, glosas) só têm
    quem está em exercício (``ex``); as notas repetidas valem para todos de ``pessoas``,
    porque o que se lista é a nota. Fornecedor, no painel, é empresa (CNPJ de verdade);
    no resumo por pessoa, quem não tem CNPJ aparece como "pessoa física", sem o nome.
    O resumo por pessoa sai para todos de ``pessoas`` com despesa, com o total de cada ano
    recebido (não só os da legislatura).

    Na Câmara, as passagens SIGEPA ficam fora de tudo isso; cada pessoa ganha o total
    delas em cada ano da legislatura em que o arquivo ainda as traz (``sigepa``), e as
    listas de quem mais e menos gasta, o de :data:`ANO_SIGEPA` (``sigepa_2024``)."""
    desde, aberto = inicio[:7], hoje[:7]
    ano_inicio = int(desde[:4])
    camara = casa == "cd"
    contas: dict[int, _Conta] = {}
    nomes: dict[str, Counter[str]] = defaultdict(Counter)  # CNPJ → grafias do nome
    anos_sigepa: set[int] = set()
    total = 0
    ultimo = ""
    categorias: Counter[str] = Counter()
    por_cnpj: Counter[str] = Counter()
    notas_cnpj: Counter[str] = Counter()
    gabinetes: dict[str, set[int]] = defaultdict(set)
    iguais: dict[tuple[str, str, str, int], list[Despesa]] = defaultdict(list)

    for indice, d in enumerate(despesas):
        if d.pessoa not in pessoas:
            continue
        c = contas.get(d.pessoa)
        if c is None:
            c = contas[d.pessoa] = _Conta()
        centavos = round(d.valor * 100)
        if e_sigepa(d):
            c.sigepa[d.ano] += centavos
            if d.ano >= ano_inicio:
                anos_sigepa.add(d.ano)
            continue
        c.anos[d.ano] += centavos
        mes = f"{d.ano:04d}-{d.mes:02d}"
        if not desde <= mes <= aberto:
            continue
        # As listas de notas contam também o mês aberto.
        chave = _chave_fornecedor(d)
        empresa = bool(chave) and not chave.startswith("~")
        if empresa and centavos > 0 and d.data and numero_valido(d.numero):
            iguais[(chave, _canonico(d.numero), d.data, centavos)].append(d)
        documento = d.url or f"#{indice}"  # a nota dividida em linhas (subcotas, devolução) soma num documento só
        doc = c.documentos.get(documento)
        if doc is None:
            c.documentos[documento] = [centavos, centavos, d]
        else:
            doc[0] += centavos
            if centavos > doc[1]:
                doc[1], doc[2] = centavos, d
        if mes == aberto:
            continue
        # Daqui para baixo, só os meses fechados.
        total += centavos
        ultimo = max(ultimo, mes)
        categorias[d.categoria] += centavos
        c.total += centavos
        c.meses[mes] += centavos
        c.categorias[d.categoria] += centavos
        if d.glosa > 0:
            c.glosa += round(d.glosa * 100)
            c.glosas += 1
        if chave:
            c.fornecedores[chave] += centavos
            if centavos > 0:
                c.notas[chave] += 1
        if empresa:
            nomes[chave][d.fornecedor] += 1
            por_cnpj[chave] += centavos
            gabinetes[chave].add(d.pessoa)
            if centavos > 0:
                notas_cnpj[chave] += 1
    so_no_mes_aberto = {chave for chave, *_resto in iguais if chave not in nomes}
    for (chave, *_resto), docs in iguais.items():  # o fornecedor que só aparece no mês aberto
        if chave in so_no_mes_aberto:
            nomes[chave].update(d.fornecedor for d in docs)

    def linha(pid: int, **extra: Any) -> dict[str, Any]:
        p = pessoas[pid]
        return {"k": p["k"], "nome": p["nome"], "partido": p.get("partido") or "", "uf": p.get("uf") or "", **extra}

    def ordem_nome(pid: int) -> tuple[str, str]:
        return text.normalize(str(pessoas[pid].get("nome") or "")), str(pessoas[pid].get("k") or "")

    def nome_fornecedor(chave: str) -> str:
        return PESSOA_FISICA if chave.startswith("~") else _mais_comum(nomes[chave])

    em_exercicio = [pid for pid, c in contas.items() if pessoas[pid].get("ex") and c.n_meses]
    regulares = [pid for pid in em_exercicio if contas[pid].n_meses >= MINIMO_MESES]

    def gasto(pid: int) -> dict[str, Any]:
        c = contas[pid]
        extra = {f"sigepa_{ANO_SIGEPA}": _reais(c.sigepa[ANO_SIGEPA])} if camara else {}
        return linha(pid, media=_reais(c.media), total=_reais(c.total), meses=c.n_meses, **extra)

    mais = sorted(regulares, key=lambda pid: (-contas[pid].media, ordem_nome(pid)))
    menos = sorted(regulares, key=lambda pid: (contas[pid].media, ordem_nome(pid)))
    mediana = statistics.median(contas[pid].media for pid in regulares) if regulares else None

    concentrados = []
    for pid in em_exercicio:
        c = contas[pid]
        empresas = [(chave, v) for chave, v in c.fornecedores.items() if not chave.startswith("~")]
        if c.total <= 0 or not empresas:
            continue
        chave, pago = min(empresas, key=lambda t: (-t[1], t[0]))
        if pago >= CONCENTRACAO_MINIMO * 100 and pago * 100 >= CONCENTRACAO_PCT * c.total:
            concentrados.append((pago, pid, chave))
    concentrados.sort(key=lambda t: (-t[0], ordem_nome(t[1])))

    repetidas = []
    for (chave, _numero, data, centavos), docs in iguais.items():
        quem: dict[int, Despesa] = {}
        for d in docs:
            quem.setdefault(d.pessoa, d)
        if len(quem) < 2:
            continue
        repetidas.append(
            {
                "fornecedor": nome_fornecedor(chave),
                "cnpj": chave,
                "numero": docs[0].numero,
                "data": data,
                "valor": _reais(centavos),
                "docs": [linha(pid, url=quem[pid].url) for pid in sorted(quem, key=ordem_nome)],
            }
        )
    repetidas.sort(key=lambda r: (r["cnpj"], r["numero"]))
    repetidas.sort(key=lambda r: (r["data"], r["valor"]), reverse=True)

    glosados = sorted((pid for pid in em_exercicio if contas[pid].glosa > 0), key=lambda pid: (-contas[pid].glosa, ordem_nome(pid)))

    painel: dict[str, Any] = {
        "desde": desde,
        "ate": ultimo,
        "sem": SEM.get(casa, ""),
        "total": _reais(total),
        "pessoas": sum(1 for c in contas.values() if c.meses),
        "mediana": _reais(mediana) if mediana is not None else None,
        "mais_gastam": [gasto(pid) for pid in mais[:LISTA]],
        "menos_gastam": [gasto(pid) for pid in menos[:LISTA]],
        "categorias": [
            {"nome": nome, "total": _reais(v), "pct": round(100 * v / total, 1) if total else 0.0}
            for nome, v in sorted(categorias.items(), key=lambda t: (-t[1], t[0]))
        ],
        "fornecedores": [
            {"nome": nome_fornecedor(chave), "cnpj": chave, "total": _reais(v), "gabinetes": len(gabinetes[chave]), "n": notas_cnpj[chave]}
            for chave, v in sorted(por_cnpj.items(), key=lambda t: (-t[1], t[0]))[:LISTA]
            if v > 0
        ],
        "concentrados": [
            linha(
                pid,
                fornecedor=nome_fornecedor(chave),
                cnpj=chave,
                valor=_reais(pago),
                pct=round(100 * pago / contas[pid].total, 1),
                total=_reais(contas[pid].total),
            )
            for pago, pid, chave in concentrados[:LISTA]
        ],
        "repetidas": repetidas[:REPETIDAS],
        "glosas": [linha(pid, valor=_reais(contas[pid].glosa), n=contas[pid].glosas) for pid in glosados[:GLOSAS]],
    }

    por_pessoa: dict[int, dict[str, Any]] = {}
    for pid, c in sorted(contas.items()):
        documentos = sorted(
            (doc for doc in c.documentos.values() if doc[0] > 0),
            key=lambda doc: (-doc[0], doc[2].data, doc[2].fornecedor, doc[2].url),
        )
        resumo: dict[str, Any] = {
            "leg": {"total": _reais(c.total), "meses": c.n_meses, "media": _reais(c.media)},
            "anos": {str(ano): _reais(v) for ano, v in sorted(c.anos.items())},
        }
        if camara:
            resumo["sigepa"] = {str(ano): _reais(c.sigepa[ano]) for ano in sorted(anos_sigepa)}
        resumo |= {
            "categorias": [[nome, _reais(v)] for nome, v in sorted(c.categorias.items(), key=lambda t: (-t[1], t[0])) if v],
            "fornecedores": [
                {"nome": nome_fornecedor(chave), "cnpj": "" if chave.startswith("~") else chave, "valor": _reais(v), "n": c.notas[chave]}
                for chave, v in sorted(c.fornecedores.items(), key=lambda t: (-t[1], t[0]))[:POR_PESSOA]
                if v > 0
            ],
            "maiores": [
                {
                    "d": d.data,
                    "fornecedor": d.fornecedor if d.cnpj else PESSOA_FISICA,
                    "categoria": d.categoria,
                    "valor": _reais(soma),
                    "url": d.url,
                }
                for soma, _maior, d in documentos[:POR_PESSOA]
            ],
            "glosa": {"valor": _reais(c.glosa), "n": c.glosas},
        }
        por_pessoa[pid] = resumo
    return painel, por_pessoa
