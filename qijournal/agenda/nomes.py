"""O nome de tela de um item da Agenda da semana, sem IA.

O assunto vem antes do número, e um leigo tem de entender. O nome sai, nesta ordem:

1. do nome curto que a própria fonte publica (o cabeçalho do Senado, o nome da medida provisória);
2. da tabela de nomes aprovados por pessoa em ``config/agenda.yaml``;
3. do título do tema (repercussão geral ou repetitivo), cortado no fim de uma palavra;
4. da ementa: "Altera/Modifica/Acrescenta <norma>, para X" é mostrada a partir de X, com a
   primeira letra maiúscula; "Institui", "Cria", "Dispõe sobre" e os demais verbos, com corte
   normal (só a primeira oração, sem a cauda "e altera a Lei nº ...").

O nome tem até 90 caracteres e a versão curta, até 60 (os tetos de ``config/agenda.yaml``), e
nunca termina no meio de uma referência legal: "Altera a Lei Complementar nº 200, de 30 de agosto
de 2023, ..." é o exemplo do que não pode sair. Sem texto aproveitável o nome fica vazio: o item
não é nomeado na home (entra em "mais N"). Tudo aqui é função pura.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from qijournal.agenda import contrato
from qijournal.agenda.regras import Regras

NOME_ATE = 90
CURTO_ATE = 60
MINIMO = 12  # menos que isto não diz o assunto: o item fica sem nome
RETICENCIAS = "…"


@dataclass(frozen=True)
class Nome:
    """O nome do item, a versão curta (celular) e de onde ele saiu (``""`` = sem nome)."""

    texto: str = ""
    curto: str = ""
    origem: str = ""  # "apelido" | "aprovado" | "tema" | "ementa" | ""

    def __bool__(self) -> bool:
        return bool(self.texto)


SEM_NOME = Nome()

# Uma norma citada pelo número: "Lei nº 9.503, de 23 de setembro de 1997", "Lei Complementar nº
# 200", "Decreto-Lei nº 2.848", "Lei 10.826/2003", "Leis nºs 8.069, de 13 de julho de 1990".
_NORMA = re.compile(
    r"(?:Leis?(?:\s+Complementar(?:es)?)?(?:\s+federal)?|Decretos?-Leis?|Decretos?|Medidas?\s+Provis[óo]rias?"
    r"|Emendas?\s+Constitucion(?:al|ais)|Resolu[çc][ãa]o|Portaria)"
    r"\s+(?:n[ºo°.]{0,2}s?\.?\s*)?\d[\d.]*(?:/\d{2,4})?"
    r"(?:\s*,?\s*de\s+\d{1,2}[ºo°]?\s+de\s+[a-zçA-Z]+\s+de\s+\d{4})?(?:\s*,?\s*de\s+\d{4})?",
    re.IGNORECASE,
)
# A ementa que só muda outra norma: o verbo e, logo depois, a norma mudada.
_VERBO_DE_ALTERACAO = re.compile(r"^(?:Altera(?:m)?|Modifica(?:m)?|Acrescenta(?:m)?|D[áa]\s+nova\s+reda[çc][ãa]o|Insere(?:m)?|Revoga(?:m)?)\b", re.IGNORECASE)
_PALAVRA_DE_NORMA = re.compile(
    r"\b(?:Leis?|Decretos?(?:-Leis?)?|Constitui[çc][ãa]o|Regimento|C[óo]digo|Consolida[çc][ãa]o|Estatuto|Medidas?\s+Provis[óo]rias?"
    r"|Emendas?|Resolu[çc][ãa]o|Ato\s+das\s+Disposi[çc][õo]es|arts?\.|artigos?|dispositivos?)",
    re.IGNORECASE,
)
# "…, para dispor sobre X", "… a fim de prever X": a finalidade, que é o assunto.
_FINALIDADE = re.compile(r"(?:^|[\s,)\"”])(?:para|a\s+fim\s+de)\s+([a-zà-ú]+(?:ar|er|ir|or|ôr))\b")
_NAO_E_VERBO = frozenset(
    "maior menor melhor pior qualquer trabalhador militar servidor consumidor empregador mulher lugar lar familiar "
    "particular exterior interior superior inferior setor valor favor motor eleitor produtor agricultor".split()
)
# A cauda que não diz o assunto.
_CAUDA = re.compile(
    r"(?:[,;]?\s*(?:e\s+)?d[áa]\s+outras\s+provid[êe]ncias|,?\s*nas\s+condi[çc][õo]es\s+que\s+especifica"
    r"|,?\s*para\s+os?\s+fi(?:m|ns)\s+que\s+especifica|,?\s*na\s+forma\s+que\s+especifica)\s*\.?\s*$",
    re.IGNORECASE,
)
# "…; e altera a Lei nº …", "…, altera as Leis nº …": a segunda oração, só de referência legal.
_SEGUNDA_ORACAO = re.compile(
    r"[,;]?\s+(?:e\s+)?(?:altera|modifica|revoga|acrescenta)\s+(?:dispositivos?\s+d)?[aeo]s?\s+"
    r"(?:Leis?|Decretos?|Consolida[çc][ãa]o|C[óo]digo|Constitui[çc][ãa]o|Medidas?|arts?\.|artigos?)\b",
    re.IGNORECASE,
)
# O que não pode ficar pendurado no fim de um nome cortado.
_PENDURADO = re.compile(
    r"(?:[\s,;:(\"“–-]+(?:de\s+que\s+tratam?|previst[oa]s?\s+n[oa]s?|nos\s+termos\s+d[aeo]s?|na\s+forma\s+d[aeo]s?"
    r"|institu[ií]d[oa]s?\s+pel[oa]s?|criad[oa]s?\s+pel[oa]s?|aprovad[oa]s?\s+pel[oa]s?"
    r"|(?:d[oa]s?\s+|n[oa]s?\s+)?(?:arts?\.?|artigos?|incisos?|§+|caput)(?:\s*[\dºo°IVX-]+[A-Z-]*)?"
    r"|a\s+ser(?:em)?|d[aeo]s?|n[oa]s?|pel[oa]s?|[ao]s?|à|às|ao|aos|e|é|são|ou|em|que|se|com|por|para|sobre|entre|sem|sob|como|um|uma|além|bem|R\$))+[\s,;:(\"“–-]*$",
    re.IGNORECASE,
)
_TRAVESSAO_ENTRE_ESPACOS = re.compile(r"\s+[–—]\s+")


def limpar(texto: str | None) -> str:
    """O texto oficial pronto para a tela: espaços especiais limpos, sem travessão e sem o ponto final."""
    limpo = contrato.limpar_texto(texto) or ""
    limpo = _TRAVESSAO_ENTRE_ESPACOS.sub(", ", limpo).replace("–", "-").replace("—", "-")
    limpo = re.sub(r"\s+([,;.:])", r"\1", limpo)
    limpo = re.sub(r",(?=[^\s\d])", ", ", limpo)  # "nº 12.288,de 20 de julho": falta o espaço na fonte
    return re.sub(r"\s{2,}", " ", limpo).strip().rstrip(".").strip()


def _sem_pendurado(texto: str) -> str:
    anterior = None
    while anterior != texto:
        anterior = texto
        texto = _PENDURADO.sub("", texto).rstrip(" ,;:(-")
    return texto


def cortar(texto: str, limite: int) -> str:
    """Corta no fim de uma palavra, dentro do limite (com as reticências), sem deixar uma
    referência legal pela metade nem uma preposição pendurada. Curto demais, devolve vazio."""
    texto = texto.strip()
    if len(texto) <= limite:
        return texto if len(texto) >= MINIMO else ""
    corte = texto.rfind(" ", 0, limite)  # o último espaço que deixa lugar para as reticências
    if corte <= 0:
        return ""
    for norma in _NORMA.finditer(texto):
        if norma.start() < corte < norma.end() or (norma.end() == corte and re.match(r"\s*,?\s*de\s+\d", texto[corte:])):
            corte = norma.start()
            break
    resto = texto[:corte]
    aberto = resto.rfind("(")
    if aberto > resto.rfind(")"):  # o parêntese que o corte deixaria aberto sai inteiro
        resto = resto[:aberto]
    resto = _sem_pendurado(resto)
    return f"{resto}{RETICENCIAS}" if len(resto) >= MINIMO else ""


def _maiuscula(texto: str) -> str:
    return texto[:1].upper() + texto[1:]


def assunto_da_ementa(ementa: str | None) -> str:
    """O trecho da ementa que diz o assunto, inteiro (o corte vem depois): a finalidade da ementa
    que altera outra norma, ou a primeira oração das demais. Vazio quando a ementa só cita normas."""
    texto = limpar(ementa)
    if not texto:
        return ""
    altera = _VERBO_DE_ALTERACAO.match(texto) and _PALAVRA_DE_NORMA.search(texto[:80])
    if altera:
        finalidade = next((m for m in _FINALIDADE.finditer(texto) if m.group(1).lower() not in _NAO_E_VERBO), None)
        if finalidade is None:
            return ""
        texto = texto[finalidade.start(1):]
    texto = texto.split(";")[0]
    if not altera:
        segunda = _SEGUNDA_ORACAO.search(texto)
        if segunda and segunda.start() >= MINIMO:
            texto = texto[: segunda.start()]
    texto = _CAUDA.sub("", texto).strip().rstrip(".,;: ").strip()
    return _maiuscula(texto)


def _nome(texto: str, origem: str, ate: int, curto_ate: int) -> Nome:
    longo = cortar(texto, ate)
    if not longo or not re.search(r"[A-Za-zÀ-ú]{3}", longo):
        return SEM_NOME
    return Nome(texto=longo, curto=cortar(texto, curto_ate) or longo[:curto_ate], origem=origem)


def nome_do_item(item: contrato.ItemAgenda, regras: Regras, *, ate: int | None = None, curto_ate: int | None = None) -> Nome:
    """O nome de tela do item (ver a ordem no cabeçalho do módulo); :data:`SEM_NOME` quando a
    fonte não traz texto aproveitável."""
    ate = ate or regras.tetos.home.nome_ate or NOME_ATE
    curto_ate = min(curto_ate or regras.tetos.home.celular_nome_ate or CURTO_ATE, ate)
    candidatos = (
        ("apelido", limpar(item.apelido)),
        ("aprovado", limpar(regras.nome_aprovado(item.identificador))),
        ("tema", limpar(item.tema.titulo) if item.tema else ""),
        ("ementa", assunto_da_ementa(item.ementa)),
    )
    for origem, texto in candidatos:
        if texto:
            nome = _nome(_maiuscula(texto), origem, ate, curto_ate)
            if nome:
                return nome
    return SEM_NOME
