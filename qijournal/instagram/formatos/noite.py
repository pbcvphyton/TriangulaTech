"""A publicação da noite (18:30): Raio-X 2.0 (§4.2) e Placar do dia (§4.3).

Os dois são da série do Raio-X ("RAIO-X Nº 014") e saem em papel. Todo texto vem de
campos da edição, das contas de :mod:`..estatistica` e da escolha de :mod:`..selecao`:
nenhuma IA nova. Os gráficos vêm de :mod:`..graficos`, com a frase do texto alternativo
gerada pela mesma função que desenha.

Cores por papel, não por lado: o lado maior fica sempre à esquerda em ardósia e o menor
à direita em petróleo (no empate, o lado A é o "maior"), na barra, no relógio, no
espectro e no fundo dos slides 3 e 4. "Maioria" só quando o lado maior passa da metade
dos analisados (:attr:`.selecao.Lados.papeis`).
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from qijournal.config import LimiaresConfig
from qijournal.models import Coverage, CoverageOutlet, Edition, IndexTopic, Story

from .. import estatistica as E
from .. import graficos as G
from .. import redacao as R
from .. import selecao as S
from ..conteudo import (
    Peca,
    Slide,
    alt,
    edition_day,
    finish_caption,
    hashtags,
    kicker_date,
    plain,
    section_info,
    source_names,
    sources_line,
    truncate_sentence,
)

SERIE = "raiox"
SLOT = "noite"
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]
SECAO_CURTA = {
    "brasil": "Brasil", "mercados": "Mercados", "juridico": "Jurídico", "politica": "Política", "mundo": "Mundo",
    "tecnologia": "Tecnologia", "imobiliario": "Imobiliário", "esporte": "Esporte", "natureza": "Natureza",
    "variedades": "Cultura",
}

CTA_RAIOX = "Leu só uma versão? Mande para quem leu a outra."
CTA_PLACAR = "Salve: o placar sai todo dia."
METODO = "Classificação de ênfase feita por IA sobre os textos publicados. Enquadramento é escolha do que destacar, não erro."
DEFINICAO = "Dividir: pelo menos 2 veículos de cada lado, entre os assuntos com 4 ou mais veículos comparados."
SERIE_COMPLETA = "Série completa no destaque Raio-X."
RODAPE_RELOGIO = "Horário informado por cada veículo, em Brasília. Não indica quem apurou primeiro."
SUB_PLACAR = "assuntos com 4 ou mais veículos comparados; dividir = pelo menos 2 veículos de cada lado."

FATO_MAX = 260  # slide 2: dek + 1ª frase do corpo, se couber
SUBTITULO_MAX_PALAVRAS = 18
SUBTITULO_MAX_CHARS = 200  # frase do corpo na capa: cortada na vírgula antes disso
MOLDE_MAX = 260  # conclusão por molde: a conta por idioma só entra se o total couber
FRAMING_MAX = 160  # o §4.2 diz 120, mas 99 de 267 enquadramentos dos Raio-X elegíveis passam disso (máx. 158)
LADO_FRAMINGS = 3  # veículos com enquadramento no slide do lado; o resto vai em "Também"
RACHAS_MAX = 3  # linhas do slide 2 do Placar com rachas
QUADRO = 1122  # altura útil do slide de feed no sistema 2 (1350 − faixa − assinatura)
RELOGIO_RX = 630  # px para as linhas do relógio no Raio-X (12 linhas de 52 px no pior caso)
RELOGIO_PLACAR = 540  # no Placar do dia, que tem kicker e assunto acima do gráfico
RELOGIO_LINHA_MIN = 38  # px por veículo antes de juntar os do meio em "+k veículos"


# ── Texto ─────────────────────────────────────────────────────────────────

_FRASE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ“\"(])")
_EM_MEIO = re.compile(r"\bem meio [àa]\b", re.I)
_LADO_ENTRE_PARENTESES = re.compile(r"\s*\(lados? [ab](?: e [ab])?\)", re.I)


def _limpo(texto: str) -> bool:
    """Sem palavra proibida nem verbo que julga veículo (o lint reprovaria) e sem "em meio à"."""
    return not (R.palavras_proibidas(texto) or R.verbos_proibidos(texto) or _EM_MEIO.search(texto or ""))


def _frases(texto: str) -> list[str]:
    return [f.strip() for f in _FRASE.split(plain(texto)) if f.strip()]


def _frases_limpas(texto: str) -> str:
    """Só as frases sem termo proibido ("" se nenhuma sobrar: frase cortada no meio não vai para a arte)."""
    if _limpo(texto):
        return texto
    return " ".join(f for f in _frases(texto) if _limpo(f))


def _tem_dado(texto: str) -> bool:
    """Pelo menos 1 número ou 1 nome próprio (palavra com maiúscula fora do começo de frase)."""
    if re.search(r"\d", texto):
        return True
    palavras = texto.split()
    for antes, w in zip(palavras, palavras[1:]):
        if w.strip("(“\"'")[:1].isupper() and not antes.endswith((".", "!", "?", ":")):
            return True
    return False


def _norm(texto: str) -> str:
    return re.sub(r"\s+", " ", E._fold(plain(texto))).strip(" .")


_FRONTEIRA = re.compile(r"[,;]\s|\s(?:e|enquanto|mas|após)\s")


def _sem_proibidas(texto: str, min_palavras: int = 4, ponto: bool = False) -> str:
    """O texto cortado no começo do primeiro termo proibido. "Em meio a…" abre um adjunto, que sai
    inteiro ("…sobre os governos em meio à inflação" → "…sobre os governos"); nos outros termos o corte
    recua até a última fronteira de oração antes dele (", ", " e ", " enquanto "), para não sobrar frase
    pela metade ("A decisão abre um novo cenário…" → ""). ``""`` se sobrarem menos de ``min_palavras``
    ou se o texto acabar em artigo ou preposição; com ``ponto``, o corte fecha com ponto."""
    texto = plain(texto).strip()
    if _limpo(texto):
        return texto
    achados = [m for m in (R._PROIBIDAS_RE.search(texto), R._VERBOS_PROIBIDOS_RE.search(texto),
                           _EM_MEIO.search(texto)) if m]
    m = min(achados, key=lambda m: m.start())
    if m.group(0).lower().startswith("em meio"):
        head = texto[: m.start()]
    else:
        fronteiras = list(_FRONTEIRA.finditer(texto[: m.start()]))
        head = texto[: fronteiras[-1].start()] if fronteiras else ""
    head = head.rstrip(" ,;:—–-")
    palavras = head.split()
    if len(palavras) < min_palavras or palavras[-1].lower() in E._PENDURADAS | {"meio"} or not _limpo(head):
        return ""
    return head + "." if ponto and not head.endswith((".", "!", "?")) else head


def _sem_adj(texto: str, titulos: list[str]) -> str:
    """Texto corrido sem adjetivo da casa (§4.1): fica se estiver no título de metade dos veículos;
    senão, corta na última "," ou " e " antes dele se sobrar pelo menos 40% do texto; se não,
    tira só a palavra ("risco fiscal bilionário da derrota" → "risco fiscal da derrota"). Se tirar a
    palavra deixar uma emenda sem sentido ("luto e de segurança") ou preposição no fim, ``""``."""
    texto = plain(texto).strip()
    if not any(not E._adj_nos_titulos(E._raiz_adj(m.group(0)), titulos) for m in E.ADJ_CASA.finditer(texto)):
        return texto
    cut = E.sem_adj_casa(texto, titulos)
    if cut and len(cut) >= 0.4 * len(texto):
        return cut.rstrip(" ,;:") + "."
    out = _sem_palavra_adj(texto, titulos)
    return "" if _emenda_quebrada(texto, out) else out


def _sem_palavra_adj(texto: str, titulos: list[str]) -> str:
    """``texto`` sem a palavra do adjetivo da casa (a que não está no título de metade dos veículos)."""
    out = E.ADJ_CASA.sub(lambda m: m.group(0) if E._adj_nos_titulos(E._raiz_adj(m.group(0)), titulos) else "", texto)
    return re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s{2,}", " ", out)).strip()


_EMENDA = re.compile(
    r"\b(?:e|ou|o|a|os|as|um|uma)\s+(?:de|do|da|dos|das|em|no|na|nos|nas|com|para|por)\b|,\s+(?:de|do|da|dos|das)\b", re.I
)


def _emenda_quebrada(antes: str, depois: str) -> bool:
    """Tirar a palavra deixou "e de"/"o da" que não havia, ou artigo/preposição no fim?"""
    if len(_EMENDA.findall(depois)) > len(_EMENDA.findall(antes)):
        return True
    ultima = depois.rstrip(" .;:,").split()[-1:]
    return bool(ultima) and ultima[0].lower() in E._PENDURADAS


# Erros de digitação que já apareceram nos campos da IA ("Detralhou", framing do Valor em 21/10).
ERROS_CONHECIDOS = {"Detralhou": "Detalhou", "detralhou": "detalhou"}
_ERRO_RE = re.compile(r"\b(?:" + "|".join(ERROS_CONHECIDOS) + r")\b")


def _corrige(texto: str) -> str:
    return _ERRO_RE.sub(lambda m: ERROS_CONHECIDOS[m.group(0)], texto)


def _eleicao(cov: Coverage, st: Story | None) -> bool:
    """A cobertura é de uma eleição com todas as letras, de qualquer país (a mesma regra da base do
    placar: :func:`.estatistica.eleicao_na_cobertura`)."""
    return E.eleicao_na_cobertura(cov, st)


def _cita_veiculo(texto: str, nomes: Iterable[str]) -> bool:
    return any(re.search(r"(?<![\wÀ-ú])" + re.escape(n) + r"(?![\wÀ-ú])", texto) for n in nomes if n)


def _selo(numero_serie: int, *partes: str) -> str:
    """``RAIO-X Nº 014 · QUA 21 OUT · TECNOLOGIA`` (sem número, só ``RAIO-X``)."""
    head = f"RAIO-X Nº {numero_serie:03d}" if numero_serie > 0 else "RAIO-X"
    return " · ".join([head, *(p for p in partes if p)]).upper()


def _data_extensa(edition: Edition) -> str:
    day = edition_day(edition)
    return f"{day.day} de {MESES[day.month - 1]}"


def _hora_completa(at: datetime) -> str:
    """``"20/10 13h17"`` (horário de Brasília, sempre com a data: vai na legenda)."""
    return f"{at.astimezone(E.TZ):%d/%m %Hh%M}"


def _lado_waffle(base: int, altura: int = 368) -> int:
    """Lado dos quadrados (7 por linha) para a grade caber em ``altura`` px: até 76 px com base pequena; 29
    assuntos (5 linhas) já pedem 65 px no slide 8, que também leva o CTA no gancho condensado."""
    linhas = max(math.ceil(base / 7), 1)
    return max(36, min(76, altura // linhas - 8))


def _dividiram(rachas: int) -> str:
    """Concordância pelo numerador: "1 de 27 assuntos dividiu", "0 de 14 assuntos dividiram"."""
    return "dividiu" if rachas == 1 else "dividiram"


def _rotulos_waffle(rachas: int) -> tuple[str, str]:
    return (_dividiram(rachas), "sem divisão")


def _duracao_titulo(span: timedelta) -> tuple[str, str]:
    """A duração do relógio para o título, por extenso (:func:`.estatistica.duracao_extenso`); o valor exato
    fica no gráfico e na legenda. Devolve (texto, trecho em laranja)."""
    return E.duracao_extenso(span)


def _lista(nomes: list[str]) -> str:
    return ", ".join(nomes)


def _titulos_literais(edition: Edition) -> set[str]:
    return {_norm(i.title) for t in edition.index for i in t.items if i.title}


# ── Raio-X: peças ─────────────────────────────────────────────────────────


def _chaves_do_fato(story: Story) -> set[str]:
    """O que identifica o fato na manchete: os números, as palavras com maiúscula fora do começo e a
    primeira palavra ("colisão", "68", "Espanha"; "SpaceX", "Tesla"), em minúsculas."""
    palavras = re.findall(r"[\wÀ-ú]+", plain(story.headline))
    chaves = {w for w in palavras if w[:1].isdigit()}
    chaves |= {w for w in palavras[1:] if w[:1].isupper() and len(w) >= 3}
    if palavras and len(palavras[0]) >= 4:
        chaves.add(palavras[0])
    return {c.lower() for c in chaves}


def _nomeia(texto: str, story: Story) -> bool:
    """O texto diz qual é o fato (dados#7): tem alguma das :func:`_chaves_do_fato` da manchete."""
    palavras = {w.lower() for w in re.findall(r"[\wÀ-ú]+", plain(texto))}
    return bool(palavras & _chaves_do_fato(story))


def _corta_na_virgula(frase: str, maximo: int = SUBTITULO_MAX_CHARS) -> str:
    """A frase inteira se couber em ``maximo``; senão, até a última vírgula antes disso (8+ palavras), com ponto."""
    if len(frase) <= maximo:
        return frase
    cut = frase.rfind(", ", 0, maximo)
    head = frase[:cut].rstrip(" ,;:") if cut > 0 else ""
    return head + "." if len(head.split()) >= 8 else ""


def _subtitulo(edition: Edition, story: Story, veiculos: list[str]) -> str:
    """Subtítulo da capa (§4.2): diz qual é o fato, com o sujeito e o quê (:func:`_nomeia`). Na ordem: o assunto
    da cobertura (sem adjetivo da casa, até 18 palavras); a manchete só sem o adjetivo da casa ("SpaceX e Tesla
    avaliam fusão após salto nos resultados operacionais", 21/10); a linha fina; a frase do corpo que nomeia o
    fato, cortada na vírgula (29/10: a manchete é o título da Folha e a linha fina não diz "trens" nem "68").
    Sem nenhuma que nomeie, a linha fina. Nunca título literal de veículo, nome de veículo nem palavra proibida."""
    c = story.coverage
    titulos = E.titulos_por_veiculo(E.topic_of(edition, story.id))
    literais = _titulos_literais(edition)
    candidatas = []
    topic = plain(c.topic) if c else ""
    if topic and E.sem_adj_casa(topic, titulos) == topic and len(topic.split()) <= SUBTITULO_MAX_PALAVRAS:
        candidatas.append(topic)
    manchete = plain(story.headline)
    sem_palavra = _sem_palavra_adj(manchete, titulos)
    if sem_palavra != manchete and len(sem_palavra.split()) >= 5 and not _emenda_quebrada(manchete, sem_palavra):
        candidatas.append(sem_palavra)
    candidatas.append(plain(story.dek))
    candidatas += [_corta_na_virgula(f) for p in story.body[:2] for f in _frases(p)[:2]]

    def serve(texto: str) -> bool:
        return bool(texto) and _limpo(texto) and _norm(texto) not in literais and not _cita_veiculo(texto, veiculos)

    for texto in candidatas:
        if serve(texto) and _nomeia(texto, story):
            return texto
    for texto in [plain(story.dek), E.capa_sem_adj(edition, story)]:
        if serve(texto):
            return texto
    return _sem_proibidas(story.dek) or _sem_proibidas(E.capa_sem_adj(edition, story))


def _fato(story: Story, evitar: str, titulos: list[str], sem_eleicao: bool = False) -> str:
    """Slide 2 (§4.2): a linha fina e a 1ª frase do corpo, se o total couber em 260 caracteres; sem
    número nem nome próprio, entra a frase seguinte. O que a capa já usou (``evitar``: a linha fina ou
    a frase do corpo, mesmo cortada) não volta, para não repetir a capa (T3). Frases com palavra
    proibida ficam de fora e, com ``sem_eleicao`` (período eleitoral), as que falam de eleição de qualquer
    país (:func:`.estatistica.eleicao_no_texto`)."""
    usado = _norm(evitar)

    def ok(f: str) -> bool:
        return (_limpo(f) and _sem_adj(f, titulos) == f and not (usado and _norm(f).startswith(usado))
                and not (sem_eleicao and E.eleicao_no_texto(f)))

    dek = plain(story.dek)
    frases = [f for p in story.body for f in _frases(p) if ok(f)]
    partes = [dek] if dek and ok(dek) and _norm(dek) != _norm(evitar) else []
    for f in frases:
        if not partes:
            if len(f) <= FATO_MAX:
                partes.append(f)
            continue
        if len(" ".join([*partes, f])) <= FATO_MAX:
            partes.append(f)
        break
    if not partes:
        base = frases[0] if frases else dek
        return truncate_sentence(base, FATO_MAX) if base else ""
    texto = " ".join(partes)
    if not _tem_dado(texto):  # sem número nem nome: entra a frase seguinte (sozinha, se as duas não couberem)
        prox = next((f for f in frases if f not in partes), None)
        if prox:
            junto = f"{texto} {prox}"
            texto = prox if len(junto) > FATO_MAX and len(prox) <= FATO_MAX else junto
    return texto


def _conclusao(c: Coverage) -> str:
    """A conclusão da cobertura sem "(lado A)"/"(lado B)": aqui os lados se chamam maioria e minoria."""
    return re.sub(r"\s+([,.;])", r"\1", _LADO_ENTRE_PARENTESES.sub("", plain(c.conclusion))).strip()


# Afirmações da conclusão sobre grupos de veículos que a contagem não sustenta (dados#3). Não sabemos a
# nacionalidade nem o tipo de cada veículo, então frase que atribui um lado a um grupo assim ("veículos
# internacionais", "agências", "jornais generalistas") sai; proporção ("a maioria dos veículos") só fica se a
# maioria tiver mais da metade dos analisados, e número junto de veículo só se for uma das contagens.
_GRUPO_NOME = (r"(?:imprensa|m[íi]dias?|ve[íi]culos?|jornais|jornal|publica[çc](?:[õo]es|[ãa]o)|canais|canal|portais|portal|"
               r"ag[êe]ncias?|coberturas?|reportagens|an[áa]lises|reda[çc][õo]es|emissoras|sites|revistas)")
_GRUPO_QUAL = (r"(?:internacion|nacion|estrangeir|brasileir|espanh|american|europe|brit[âa]nic|anglo|ocident|ingl[eê]s|"
               r"[áa]rabe|asi[áa]tic|latin|regiona|loca[li]|especializad|econ[ôo]mic|financeir|esportiv|generalist|"
               r"investigativ|anal[íi]tic|t[ée]cnic|factua|oficia|estata|p[úu]blic|jur[íi]dic|pol[íi]tic|gera[li]|"
               r"conservador|progressist|governist|alternativ|independent|de neg[óo]cios|de mercado|de economia|"
               r"de tecnologia|de consumo|de servi[çc]o|de circula[çc][ãa]o|de grande alcance|com foco)")
_GRUPO_RE = re.compile(rf"\b{_GRUPO_NOME}\s+{_GRUPO_QUAL}|\bag[êe]ncias\b", re.I)
_PROPORCAO_RE = re.compile(
    rf"\b(?:maioria|maior parte|grande parte|parte expressiva|minoria)\s+d[aeo]s?\s+(?:[\wÀ-ú]+\s+)?{_GRUPO_NOME}\b|"
    r"\b(?:majorit[aá]ri\w+|predomin\w+|quase todos|unanim\w+)",  # "a cobertura majoritária", 4 de 9 (02/11)
    re.I,
)
_NUMEROS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
            "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12}
_NUM = r"(\d+|" + "|".join(_NUMEROS) + r")"
_NUM_GRUPO_RE = re.compile(rf"\b{_NUM}\s+(?:d[aeo]s\s+)?(?:[\wÀ-ú]+\s+)?{_GRUPO_NOME}\b|\boutr[oa]s\s+{_NUM}\b|"
                           rf"\({_NUM}\s+a\s+{_NUM}\)", re.I)


def _frase_sustentada(frase: str, ld: S.Lados) -> bool:
    """A frase da conclusão não afirma nada sobre grupos de veículos que a contagem dos lados desminta."""
    if _GRUPO_RE.search(frase):
        return False
    if _PROPORCAO_RE.search(frase) and (ld.empate or 2 * ld.n_maioria <= ld.total):
        return False
    contagens = {ld.n_maioria, ld.n_minoria, ld.neutros, ld.total}
    for m in _NUM_GRUPO_RE.finditer(frase):
        for g in (x for x in m.groups() if x):
            if (int(g) if g.isdigit() else _NUMEROS[g.lower()]) not in contagens:
                return False
    return True


def _conclusao_verificada(c: Coverage, ld: S.Lados, titulos: list[str]) -> str:
    """A conclusão da IA (sem adjetivo da casa nem termo proibido) só com as frases que a contagem
    sustenta (:func:`_frase_sustentada`); ``""`` se nenhuma sobrar."""
    texto = _frases_limpas(_sem_adj(_conclusao(c), titulos))
    return " ".join(f for f in _frases(texto) if _frase_sustentada(f, ld))


def _destacaram(k: int) -> str:
    """"1 destacou", "3 destacaram"; o zero por extenso, "nenhum destacou" (nunca "0 destacaram", 01/11)."""
    if k == 0:
        return "nenhum destacou"
    return f"{k} destacou" if k == 1 else f"{k} destacaram"


def _molde_conclusao(
    maioria: list[CoverageOutlet],
    neutros: list[CoverageOutlet],
    minoria: list[CoverageOutlet],
    rotulos: tuple[str | None, str | None],
    idiomas: dict[str, str],
    horas: dict[str, datetime] | None = None,
    day: date | None = None,
) -> str:
    """A conclusão por molde, com os números do espectro: "5 de 11 veículos destacaram X; 5, Y; 1 não tomou
    lado."; quando os horários dos dois lados não se cruzam, a janela de cada um (28/10: os 6 de "fuga ilícita"
    das 09h13 às 15h32, os 5 de "asilo político" a partir das 15h37); e, com textos em inglês e em português
    (2+ de cada com lado), a mesma conta por idioma, se o total couber em 260 caracteres."""
    n = len(maioria) + len(neutros) + len(minoria)
    rot_maj, rot_min = rotulos
    if rot_maj and rot_min:
        texto = f"{len(maioria)} de {n} veículos destacaram {rot_maj}; {len(minoria)}, {rot_min}"
    else:
        texto = f"{len(maioria)} de {n} veículos contaram de um jeito; {len(minoria)}, de outro"
    if neutros:
        texto += f"; {len(neutros)} não {'tomou' if len(neutros) == 1 else 'tomaram'} lado"
    texto += "."
    horas = horas or {}
    if rot_maj and rot_min and all(len(g) >= 2 and all(o.name in horas for o in g) for g in (maioria, minoria)):
        ja = sorted(horas[o.name] for o in maioria)
        jb = sorted(horas[o.name] for o in minoria)
        if ja[-1] < jb[0] or jb[-1] < ja[0]:  # as janelas não se cruzam
            pontas = [ja[0], ja[-1], jb[0], jb[-1]]
            datas = {dt.astimezone(E.TZ).date() for dt in pontas}
            quando = ","
            if len(datas) == 1:  # tudo num dia só: a data vai uma vez ("em 27/10, …"), não nas 4 pontas
                d = datas.pop()
                h, quando = [E.hora(dt) for dt in pontas], (f", em {d:%d/%m}," if day and d != day else ",")
            else:
                h = E.horas(pontas, day)
            texto += (f" Pelo horário informado{quando} os {len(maioria)} de {rot_maj} publicaram entre {h[0]} e "
                      f"{h[1]}; os {len(minoria)} de {rot_min}, entre {h[2]} e {h[3]}.")
    conta = {lang: [sum(1 for o in grupo if idiomas.get(o.name) == lang) for grupo in (maioria, minoria)]
             for lang in ("en", "pt")}
    if rot_maj and rot_min and all(sum(v) >= 2 for v in conta.values()):
        (en_a, en_b), (pt_a, pt_b) = conta["en"], conta["pt"]
        idioma = (f" Entre os veículos em inglês, {_destacaram(en_a)} {rot_maj} e {en_b or 'nenhum'}, {rot_min}; "
                  f"em português, {pt_a or 'nenhum'} e {pt_b or 'nenhum'}.")
        if len(texto + idioma) <= MOLDE_MAX:  # a janela de horário, se houver, tem a vez
            texto += idioma
    return texto


def _idiomas(topic: IndexTopic | None) -> dict[str, str]:
    """O idioma do primeiro texto de cada veículo no assunto (``index.items[].lang``)."""
    out: dict[str, str] = {}
    for item in topic.items if topic else []:
        if item.source and item.lang and item.source not in out:
            out[item.source] = item.lang
    return out


def _papel(ld: S.Lados) -> dict[str, str]:
    """Stance da cobertura → papel na arte ("a" = maioria, "b" = minoria)."""
    return {ld.maioria: "a", ld.minoria: "b", "neutro": "neutro"}


def _ordem(outlets: list[CoverageOutlet], horas: dict[str, datetime]) -> list[CoverageOutlet]:
    """Em ordem de horário (relógio); os sem horário no fim, na ordem da cobertura."""
    fim = datetime.max.replace(tzinfo=E.TZ)
    return sorted(outlets, key=lambda o: (o.name not in horas, horas.get(o.name, fim)))


def _linhas_relogio(n: int, disponivel: int = RELOGIO_RX) -> dict[str, int]:
    """Altura e número máximo de linhas do relógio (kwargs de :func:`.graficos.relogio`): com poucos
    veículos as linhas abrem (até 100 px) e ocupam o slide; com muitos, encolhem até 38 px e, se nem
    assim couberem 12, as do meio viram "+k veículos"."""
    linhas = max(min(n, 12), 1)
    if disponivel // linhas >= RELOGIO_LINHA_MIN:
        return {"linha": min(100, disponivel // linhas), "max_linhas": 12}
    return {"linha": RELOGIO_LINHA_MIN, "max_linhas": max(6, disponivel // RELOGIO_LINHA_MIN)}


def _linhas_serif(texto: str, tam: float, largura: float = G.LARGURA, fator: float = 0.53) -> int:
    """Linhas que ``texto`` ocupa em Noto Serif de ``tam`` px (quebra gulosa por palavra; ``fator`` é a
    largura média do caractere em em: 0,515 no peso 500 e 0,54 no 600, medidos no Chromium)."""
    cabe = largura / (fator * tam)
    linhas, atual = 0, 0.0
    for w in texto.split():
        if atual and atual + 1 + len(w) <= cabe:
            atual += 1 + len(w)
        else:
            linhas, atual = linhas + 1, float(len(w))
    return max(linhas, 1)


def _tam_serif(texto: str, altura: float, maximo: int, minimo: int, entrelinha: float, fator: float = 0.53) -> int:
    """O maior corpo (de 2 em 2 px) em que ``texto`` cabe em ``altura`` px; o ``data-fit`` do template
    ainda encolhe se a estimativa errar."""
    for tam in range(maximo, minimo - 1, -2):
        if _linhas_serif(texto, tam, fator=fator) * tam * entrelinha <= altura:
            return tam
    return minimo


def _altura_svg(svg: str) -> float:
    m = re.search(r"height='([\d.]+)'", svg or "")
    return float(m.group(1)) if m else 0.0


def _tam_lista(titulo: str, descricao: str, veiculos: list[dict[str, str]], rodape: list[str]) -> int:
    """Corpo da lista de veículos dos slides 3 e 4: o maior (de 30 a 46 px) que cabe no que sobra do quadro."""
    titulo_h = len(G.linhas_texto(titulo, "hook", 96, G.LARGURA * 0.985, tracking=-0.01)) * 96 * 0.92 + 23
    desc_h = 18 + _linhas_serif(descricao, 32, fator=0.47) * 32 * 1.3 if descricao else 0
    pe_h = 26 + sum(_linhas_serif(r, 28, fator=0.55) * 38 + 12 for r in rodape if r) if any(rodape) else 0
    livre = QUADRO - (34 + 30 + 14 + titulo_h + desc_h + 34 + pe_h) - 16
    for tam in range(46, 29, -1):
        alto = sum(tam * (0.88 * 1.15 + 1.28 + 0.3) + _linhas_serif(v["framing"], tam, fator=0.5) * tam * 1.3
                   for v in veiculos)
        if alto <= livre:
            return tam
    return 30


def _relogio_do_assunto(topic: IndexTopic) -> E.Relogio | None:
    """Relógio de um assunto do index sem matéria (mesma conta de :func:`.estatistica.relogio`)."""
    first: dict[str, tuple[datetime, str]] = {}
    for item in topic.items:
        at = E._parse_dt(item.published)
        if at is None or not item.source or item.source in E.ORGAOS:
            continue
        if item.source not in first or at < first[item.source][0]:
            first[item.source] = (at, item.lang)
    if len(first) < 2:
        return None
    return E.Relogio(sorted((E.Batida(s, at, None, lang) for s, (at, lang) in first.items()), key=lambda b: b.at))


def _tam_gancho(texto: str) -> int:
    """Corpo do gancho: o maior entre 112 e 150 px em 4 linhas; se não couber, em 5 (até 100 px)."""
    largura = G.LARGURA * 0.985
    tam = G.tamanho_gancho(texto, max_linhas=4)
    if len(G.linhas_texto(texto.upper(), "hook", tam, largura, tracking=-0.01)) <= 4:
        return tam
    return G.tamanho_gancho(texto, max_linhas=5, tam_min=100)


def _titulo_lado(contagem: str, rotulo: str, sep: str | None = None) -> str:
    """``"6 de 11 · sinergia em IA"`` numa linha; se não couber, ``"4 de 6: restabelecimento"`` (os dois
    pontos fecham bem a 1ª linha; o "·" ficaria sozinho no fim dela). ``sep`` já decidido para o par
    (:func:`_separador_lados`)."""
    if sep is None:
        sep = _separador_lados([(contagem, rotulo)])
    return f"{contagem}{sep}{rotulo}"


def _separador_lados(pares: list[tuple[str, str]]) -> str:
    """O separador dos títulos dos slides 3 e 4, decidido para o par: se um deles não cabe numa linha com o "·",
    os dois usam ":" (29/10: "5 DE 11: APURAÇÃO DE FALHA HUMANA" e "5 DE 11: VÍTIMAS", e não "5 DE 11 · VÍTIMAS")."""
    for contagem, rotulo in pares:
        if rotulo and len(G.linhas_texto(f"{contagem} · {rotulo}".upper(), "hook", 96, G.LARGURA * 0.985,
                                         tracking=-0.01)) > 1:
            return ": "
    return " · "


def _lado_ctx(
    outlets: list[CoverageOutlet],
    horas: dict[str, datetime],
    day: date,
) -> tuple[list[dict[str, str]], list[str]]:
    """Até 3 veículos com enquadramento ("Veículo · framing", com a hora) e os demais só pelo nome.
    Se uma das horas for de outro dia, todas levam a data ("20/10 22h49" ao lado de "21/10 03h36")."""
    mostrados, resto = [], []
    for o in outlets:
        framing = _corrige(plain(o.framing))
        if framing and len(mostrados) < LADO_FRAMINGS:
            mostrados.append({"name": o.name, "hora": "", "framing": truncate_sentence(framing, FRAMING_MAX)})
        else:
            resto.append(o.name)
    com_hora = [m for m in mostrados if horas.get(m["name"])]
    for m, h in zip(com_hora, E.horas([horas[m["name"]] for m in com_hora], day)):
        m["hora"] = h
    return mostrados, resto


def raio_x(
    edition: Edition,
    brand: dict[str, Any],
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    numero_serie: int,
    entidades_recentes: Iterable[Iterable[str]] = (),
    limiares: LimiaresConfig | None = None,
    story: Story | None = None,
) -> Peca | None:
    """Raio-X 2.0 (§4.2): carrossel de 8 slides (7 sem "Por que importa") sobre a cobertura dividida do dia.

    1 capa-tensão (gancho pelos moldes, subtítulo, barra maioria | sem lado | minoria) · 2 o fato ·
    3 a maioria · 4 a minoria e os sem lado · 5 Relógio da notícia (ou "A atenção", se o relógio não
    vale) · 6 o que isso significa (conclusão + espectro) · 7 por que importa · 8 Placar do dia,
    método e CTA. ``story`` (opcional) é a matéria já escolhida por :func:`.selecao.escolher_noite`;
    sem ela, a escolha é :func:`.selecao.escolher_raiox`. ``None`` sem IA ou sem matéria elegível.
    """
    if edition.mode != "ai":
        return None
    nomes, lim = tuple(nomes), limiares or LimiaresConfig()
    if story is None:
        story = S.escolher_raiox(
            edition, election=election, nomes=nomes, entidades_recentes=entidades_recentes, limiares=lim
        )
    if story is None or story.coverage is None:
        return None
    c: Coverage = story.coverage
    day = edition_day(edition)
    section = section_info(edition, story.section)
    ld = S.lados(c, nomes)
    papel = _papel(ld)
    n_total = ld.total
    # rótulo só com os dois: com um barrado ("desafio de Zelenski", 16/10), a arte toda usa os papéis
    rot_maj, rot_min = (ld.rot_maioria, ld.rot_minoria) if ld.rot_maioria and ld.rot_minoria else (None, None)
    papel_maj, papel_min = ld.papeis  # "maioria" só com mais da metade; no empate, "de um lado"
    nome_maj, nome_min = rot_maj or papel_maj, rot_min or papel_min
    veiculos = [o.name for o in c.outlets]
    nota = f"análise por IA de {n_total} veículos"

    rel = E.relogio(edition, story)
    horas = {b.source: b.at for b in rel.batidas} if rel else {}
    maioria = _ordem([o for o in c.outlets if o.stance == ld.maioria], horas)
    minoria = _ordem([o for o in c.outlets if o.stance == ld.minoria], horas)
    neutros = _ordem([o for o in c.outlets if o.stance not in ("a", "b")], horas)
    topic = E.topic_of(edition, story.id)
    fora = [v for v in (E.topic_sources(topic, veiculos=True) if topic else []) if v not in set(veiculos)]
    if rel:
        fora = sorted(fora, key=lambda v: horas.get(v, datetime.max.replace(tzinfo=E.TZ)))
    gancho = S.gancho_raiox(c, nomes, fora=len(fora))

    selo_capa = _selo(numero_serie, kicker_date(day), SECAO_CURTA.get(story.section, section["title"]))
    selo = _selo(numero_serie)
    slides: list[Slide] = []

    # 1. Capa-tensão
    subtitulo = _subtitulo(edition, story, veiculos)
    if not ld.empate:
        rot_barra = {"a": papel_maj, "n": "sem lado", "b": papel_min}
    else:  # no empate não há maioria: a barra leva os rótulos dos lados
        rot_barra = {"a": rot_maj, "n": "sem lado", "b": rot_min} if rot_maj and rot_min else None
    barra, d_barra = G.barra_anb(len(maioria), len(neutros), len(minoria), rot_barra, altura=140)
    numero_txt = f" nº {numero_serie}" if numero_serie > 0 else ""
    slides.append(
        Slide(
            "rx_capa",
            {
                "selo": selo_capa,
                "gancho": gancho.texto,
                "destaque": gancho.destaque,
                "gancho_px": _tam_gancho(gancho.texto),
                "subtitulo": subtitulo,
                "grafico": barra,
                "nota": nota,
                "section": section,
            },
            alt=alt(f"Raio-X da cobertura{numero_txt}, {_data_extensa(edition)}. {gancho.legenda} "
                    + (f"{subtitulo.rstrip('.')}. " if subtitulo else "") + d_barra),
        )
    )

    # 2. O fato
    titulos = E.titulos_por_veiculo(topic)
    fato = _fato(story, subtitulo, titulos, sem_eleicao=election)
    fontes = source_names(story)
    slides.append(
        Slide(
            "rx_fato",
            {
                "selo": selo,
                "kicker": "O fato",
                "texto": fato,
                "texto_px": _tam_serif(fato, 780, 76, 46, 1.22, 0.515),
                "fontes": sources_line(fontes, 4 if len(", ".join(fontes[:4])) <= 58 else 3),
                "cobertura": f"{n_total} veículos · {len(maioria)} × {len(minoria)}"
                + (f" · {len(neutros)} sem lado" if neutros else ""),
                "a": len(maioria),
                "n": len(neutros),
                "b": len(minoria),
                "nota": nota,
                "section": section,
            },
            alt=alt(f"O fato: {fato}" + (f" Fontes: {_lista(fontes)}." if fontes else "")),
        )
    )

    # 3 e 4. A maioria e a minoria ("metade" só quando todos tomaram lado: 5 × 5 com 1 sem lado não é metade;
    # "maioria" só com mais da metade dos analisados: 6 de 14 com 4 sem lado é "o lado maior")
    if ld.empate and not neutros:
        kickers = ("Metade", "A outra metade")
    elif ld.empate:
        kickers = ("Empate · um lado", "Empate · o outro lado")
    elif ld.tem_maioria:
        kickers = ("A maioria", "A minoria")
    else:
        kickers = ("O lado maior", "O lado menor")
    sep = _separador_lados([(f"{len(maioria)} de {n_total}", rot_maj or ""), (f"{len(minoria)} de {n_total}", rot_min or "")])
    for chave, outlets, rotulo, nome, kicker in (
        ("a", maioria, rot_maj, nome_maj, kickers[0]),
        ("b", minoria, rot_min, nome_min, kickers[1]),
    ):
        mostrados, resto = _lado_ctx(outlets, horas, day)
        lado_txt = _sem_adj(c.side_a if (ld.maioria if chave == "a" else ld.minoria) == "a" else c.side_b, titulos)
        # a regra do rótulo vale para o que fica no lugar dele: a descrição do lado com político ou partido
        # ("Destaca desafio de Zelenski…", 16/10; "Foco no favoritismo democrata…", 02/11) sai (§2)
        if lado_txt and E.cita_politico(lado_txt, nomes):
            lado_txt = ""
        contagem = f"{len(outlets)} de {n_total}"
        titulo = _titulo_lado(contagem, rotulo, sep) if rotulo else contagem  # o papel já está no chapéu
        extra = []
        if chave == "b" and neutros:
            extra = [f"Sem tomar lado ({len(neutros)}): {_lista([o.name for o in neutros])}"]
        ctx = {
            "selo": selo,
            "lado": chave,
            "kicker": kicker,
            "titulo": titulo.upper(),
            "destaque": contagem.upper(),
            "descricao": lado_txt if lado_txt and _limpo(lado_txt) else "",
            "veiculos": mostrados,
            "tambem": _lista(resto),
            "neutros": extra[0] if extra else "",
            "nota": nota,
            "section": section,
        }
        ctx["lista_px"] = _tam_lista(ctx["titulo"], ctx["descricao"], mostrados, [ctx["tambem"], ctx["neutros"]])
        texto_alt = (
            f"{kicker}, {contagem} veículos" + (f": {rotulo}." if rotulo else ".")
            + (f" {ctx['descricao'].rstrip('.')}." if ctx["descricao"] else "")
            + "".join(f" {v['name']}" + (f", {v['hora']}" if v["hora"] else "") + f": {v['framing']}" for v in mostrados)
            + (f" Também: {_lista(resto)}." if resto else "")
            + (f" {extra[0]}." if extra else "")
        )
        slides.append(Slide("rx_lado", ctx, alt=alt(texto_alt)))

    # 5. Relógio da notícia (ou "A atenção")
    if E.relogio_valido(rel, min_veiculos=lim.relogio_min_veiculos, min_span_min=lim.relogio_min_span_min):
        assert rel is not None
        k = len(rel.batidas)
        dur = E.duracao(rel.span)
        dur_titulo, dur_destaque = _duracao_titulo(rel.span)
        titulo_rel = S.inseparavel(f"{k} veículos em {dur_titulo}".upper())
        vir = S.virada(edition, story, rel, nomes)
        contexto = _contexto_atencao(edition, story) if k <= 8 else []
        stance_papel = {o.name: papel.get(o.stance) for o in c.outlets}
        rotulos_rel = {"a": nome_maj, "b": nome_min}
        livre = RELOGIO_RX + _folga_virada(vir) - int(G.altura_legenda_relogio(rel.batidas, stance_papel, rotulos_rel))
        livre -= _sobra_titulo(titulo_rel)
        if contexto and (livre - _altura_contexto(contexto)) // k < RELOGIO_LINHA_MIN:
            contexto = []  # as linhas de contexto só entram se nenhum veículo do relógio ficar escondido
        svg, d_rel = G.relogio(
            rel.batidas,
            stance_papel,
            day,
            rotulos=rotulos_rel,
            **_linhas_relogio(k, livre - _altura_contexto(contexto)),
            base="",
        )
        slides.append(
            Slide(
                "rx_relogio",
                {
                    "selo": f"{selo} · Relógio da notícia".upper(),
                    "titulo": titulo_rel,
                    "titulo_px": _tam_titulo_relogio(titulo_rel),
                    "destaque": S.inseparavel(dur_destaque.upper()),
                    "grafico": svg,
                    "virada": vir.texto if vir else "",
                    "contexto": contexto,
                    "rodape": RODAPE_RELOGIO,
                    "nota": nota,
                    "section": section,
                },
                alt=alt(f"Relógio da notícia: {k} veículos em {dur}. {d_rel} "
                        + (f"{vir.texto} " if vir else "") + " ".join(contexto) + (" " if contexto else "") + RODAPE_RELOGIO),
            )
        )
        analisados = f" ({sum(1 for b in rel.batidas if b.source in stance_papel)} analisados)" if fora else ""
        linha_relogio = (
            f"▸ Relógio: {k} veículos{analisados} em {dur}, de {rel.batidas[0].source} "
            f"({_hora_completa(rel.batidas[0].at)}) a {rel.batidas[-1].source} ({_hora_completa(rel.batidas[-1].at)}), "
            "horário informado por cada veículo."
        )
    else:
        slides.append(_slide_atencao(edition, story, selo, nota, section))
        at = S.atencao(edition, story)
        linha_relogio = f"▸ A atenção: {at.veiculos} veículos, {at.textos} textos; {at.linhas[1]}." if at else ""

    # 6. O que isso significa: a conclusão da IA só com as frases limpas (o lint reprovaria o Raio-X inteiro) e
    # que a contagem sustenta; sem nenhuma, o molde com os números do espectro (e a conta por idioma)
    conclusao = _conclusao_verificada(c, ld, titulos) or _molde_conclusao(
        maioria, neutros, minoria, (rot_maj, rot_min), _idiomas(topic), horas, day
    )
    svg, d_esp = G.espectro(
        [o.name for o in maioria],
        [o.name for o in neutros],
        [o.name for o in minoria],
        rotulos=(nome_maj, nome_min),
        lado_maioria="a",
        fora=fora,
    )
    slides.append(
        Slide(
            "rx_significado",
            {"selo": selo, "kicker": "O que isso significa", "conclusao": conclusao, "grafico": svg, "nota": nota,
             "conclusao_px": _tam_serif(conclusao, QUADRO - 130 - _altura_svg(svg), 76, 34, 1.2, 0.54),
             "section": section},
            alt=alt(f"O que isso significa: {conclusao} {d_esp}"),
        )
    )

    # 7. Por que importa (some sem o campo)
    why = _sem_adj(story.why_it_matters or "", titulos) if story.why_it_matters else ""
    why = why if _limpo(why) else _sem_proibidas(why, 5, ponto=True)
    if why:
        slides.append(
            Slide(
                "rx_importa",
                {"selo": selo, "kicker": "Por que importa", "texto": why, "nota": nota, "section": section,
                 "texto_px": _tam_serif(why, 780, 100, 56, 1.12, 0.54)},
                alt=alt(f"Por que importa: {why}"),
            )
        )

    # 8. Placar do dia + método + CTA
    p = E.placar(edition, election=election, nomes=nomes)
    rachas = E.rachas_do_dia(edition, election=election, nomes=nomes)
    idx = next((i for i, (cov, _) in enumerate(rachas) if cov is c), None)
    svg, d_waf = G.waffle(p.base, p.rachas, idx, rotulos=_rotulos_waffle(p.rachas), lado=_lado_waffle(p.base))
    titulo_placar = f"{p.texto_dia} assuntos {_dividiram(p.rachas)} a cobertura hoje"
    slides.append(
        Slide(
            "rx_placar",
            {
                "selo": f"{selo} · Placar do dia".upper(),
                "kicker": "",
                "titulo": S.inseparavel(titulo_placar.upper()),
                "destaque": S.inseparavel(p.texto_dia.upper()),
                "grafico": svg,
                "definicao": DEFINICAO,
                "metodo": METODO,
                "cta": CTA_RAIOX,
                "serie": SERIE_COMPLETA,
                "nota": "análise por IA",
                "section": section,
            },
            alt=alt(f"Placar do dia: {titulo_placar}. {d_waf} {DEFINICAO} {METODO} {CTA_RAIOX} {SERIE_COMPLETA}"),
        )
    )

    # Legenda (3 a 5 linhas "▸": os sem lado vão na linha da minoria). O fato com sujeito e o quê: o
    # subtítulo da capa ou o texto do slide 2, o primeiro que nomeia o fato (nunca título de veículo).
    fato_legenda = next((t for t in (subtitulo, fato) if t and _nomeia(t, story)), fato or subtitulo)
    linhas = [
        f"▸ O fato: {fato_legenda}",
        f"▸ {nome_maj[0].upper() + nome_maj[1:]} ({len(maioria)}): {_lista([o.name for o in maioria])}",
        f"▸ {nome_min[0].upper() + nome_min[1:]} ({len(minoria)}): {_lista([o.name for o in minoria])}"
        + (f". Sem tomar lado ({len(neutros)}): {_lista([o.name for o in neutros])}" if neutros else ""),
    ]
    opcionais = [
        linha_relogio,
        f"▸ Placar do dia: {p.texto_dia} assuntos com 4 ou mais veículos {_dividiram(p.rachas)} a cobertura.",
    ]
    fecho = [f"Por que importa: {why}" if why else "", METODO, CTA_RAIOX, hashtags("#jornalismo", [story.section], election=election)]
    caption = _legenda(gancho.legenda, linhas, opcionais, fecho)

    return Peca(
        slot=SLOT,
        kind="carousel",
        title="Raio-X da cobertura",
        slides=slides,
        caption=caption,
        story_ids=[story.id],
        serie=SERIE,
        numero_serie=numero_serie,
        entidades=S.entidades(story),
        formula=gancho.formula,
    )


def _tam_titulo_relogio(titulo: str) -> int:
    """112 px se o título couber numa linha; em 2 ("11 VEÍCULOS EM QUASE 23 HORAS"), 88 px, para as 12 linhas
    do relógio continuarem cabendo."""
    return 112 if len(G.linhas_texto(titulo.upper(), "hook", 112, G.LARGURA * 0.985, tracking=-0.01)) <= 1 else 88


def _sobra_titulo(titulo: str) -> int:
    """Quanto o título do relógio ocupa além de 1 linha de 112 px (o RELOGIO_RX conta com uma)."""
    tam = _tam_titulo_relogio(titulo)
    linhas = len(G.linhas_texto(titulo.upper(), "hook", tam, G.LARGURA * 0.985, tracking=-0.01))
    return max(int(linhas * tam * 0.92 - 112 * 0.92), 0)


def _folga_virada(vir: S.Virada | None) -> int:
    """Quanto o relógio ganha (ou perde) conforme a linha de virada tem menos (ou mais) que as 2 linhas previstas."""
    if vir is None:
        return 112
    return 44 * (2 - _linhas_serif(vir.texto, 34, fator=0.5))


def _altura_contexto(linhas: list[str]) -> int:
    """Altura que as linhas de contexto tiram do relógio (30 px de respiro + ~72 px por linha de até 2 linhas)."""
    return 30 + sum(28 + 39 * _linhas_serif(t, 30, fator=0.55) for t in linhas) if linhas else 0


def _contexto_atencao(edition: Edition, story: Story) -> list[str]:
    """As linhas de "A atenção" em frase, para o pé do relógio quando há poucos veículos (o espaço sobra)."""
    at = S.atencao(edition, story)
    if at is None:
        return []
    out = [
        f"{at.textos} textos de {at.veiculos} veículos: o {at.rank}º assunto mais coberto entre os {E.num(at.assuntos)} do dia.",
        f"{E.num(at.uma_fonte)} dos {E.num(at.assuntos)} assuntos do dia saíram em uma fonte só.",
    ]
    if at.textos_en:
        out.append(f"{at.textos_en} dos {at.textos} textos em inglês.")
    return out


def _slide_atencao(edition: Edition, story: Story, selo: str, nota: str, section: dict[str, str]) -> Slide:
    """Substituto do relógio (§4.2): "{k} VEÍCULOS, {t} TEXTOS", o lugar do assunto entre os do dia e,
    se houver, os textos em inglês; a matriz de pontos com o assunto destacado."""
    at = S.atencao(edition, story)
    topic = E.topic_of(edition, story.id)
    if at is None or topic is None:
        k = len(story.coverage.outlets) if story.coverage else len(source_names(story))
        texto = f"{k} veículos analisados"
        return Slide("rx_atencao", {"selo": f"{selo} · A atenção".upper(), "titulo": texto.upper(), "destaque": str(k),
                                    "linhas": [], "grafico": "", "nota": nota, "section": section}, alt=alt(f"A atenção: {texto}."))
    contagens = [len(E.topic_sources(t)) for t in edition.index]
    svg, d = G.matriz_pontos(contagens, edition.index.index(topic), rotulo_destaque="este assunto", altura_max=700)
    titulo, *resto = at.linhas
    return Slide(
        "rx_atencao",
        {
            "selo": f"{selo} · A atenção".upper(),
            "titulo": titulo,
            "destaque": f"{at.veiculos} VEÍCULOS",
            "linhas": resto,
            "grafico": svg,
            "nota": nota,
            "section": section,
        },
        alt=alt(f"A atenção: {at.veiculos} veículos, {at.textos} textos. " + ". ".join(resto) + f". {d}"),
    )


LINHAS_MIN, LINHAS_MAX = 3, 5  # linhas "▸" da legenda (§4)


def _legenda(linha1: str, linhas: list[str], opcionais: list[str], fecho: list[str]) -> str:
    """Legenda no molde do §4.1, até 1.300 caracteres e com 3 a 5 linhas "▸": as opcionais entram na ordem
    até 5; sem caber, saem de trás para a frente (sem baixar de 3) e, por fim, o "Por que importa" é encurtado."""
    opcionais = [o for o in opcionais if o][: max(LINHAS_MAX - len(linhas), 0)]
    fecho = [f for f in fecho if f]

    def montar() -> str:
        return finish_caption([linha1, "\n".join(linhas + opcionais), "\n".join(fecho)])

    caption = montar()
    while len(caption) > R.LEGENDA_MAX and opcionais and len(linhas) + len(opcionais) > LINHAS_MIN:
        opcionais.pop()
        caption = montar()
    if len(caption) > R.LEGENDA_MAX and fecho and fecho[0].startswith("Por que importa"):
        sobra = len(fecho[0]) - (len(caption) - R.LEGENDA_MAX)
        fecho[0] = truncate_sentence(fecho[0], max(sobra, 40))
        caption = montar()
    return caption


# ── Placar do dia ─────────────────────────────────────────────────────────


def _titulo_cobertura(edition: Edition, cov: Coverage, story: Story | None) -> str:
    """O assunto de uma cobertura para a arte: sem adjetivo da casa, sem palavra proibida e nunca
    o título literal de um veículo (``""`` se não sobrar título)."""
    literais = _titulos_literais(edition)
    titulos = E.titulos_por_veiculo(E.topic_of(edition, story.id)) if story else []
    texto = E.sem_adj_casa(cov.topic, titulos, dek=story.dek if story else "")
    if not texto:  # sem linha fina para trocar: tira só o adjetivo
        texto = re.sub(r"\s{2,}", " ", E.ADJ_CASA.sub("", plain(cov.topic))).strip()
    texto = _sem_proibidas(texto)
    if not texto or _norm(texto) in literais:
        texto = _sem_proibidas(E.capa_sem_adj(edition, story)) if story else ""
    return "" if not texto or _norm(texto) in literais else texto


def _titulos_cov(edition: Edition, cov: Coverage, story: Story | None) -> list[str]:
    """Os títulos dos veículos do assunto da cobertura (da matéria ou, sem ela, do assunto do index de mesmo título)."""
    topic = E.topic_of(edition, story.id) if story else next((t for t in edition.index if t.title == cov.topic), None)
    return E.titulos_por_veiculo(topic)


def _rotulos_barra(ld: S.Lados) -> dict[str, str]:
    """Rótulos da barra de um racha: os dos lados quando os dois existem; senão os papéis
    (:attr:`.selecao.Lados.papeis`: "maioria"/"minoria" só com mais da metade, senão "lado maior"/"lado menor"),
    e no empate (2 × 2 não tem maioria) "de um lado"/"do outro"."""
    if ld.rot_maioria and ld.rot_minoria:
        return {"a": ld.rot_maioria, "n": "sem lado", "b": ld.rot_minoria}
    if ld.empate:
        return {"a": "de um lado", "n": "sem lado", "b": "do outro"}
    maj, mi = ld.papeis
    return {"a": maj, "n": "sem lado", "b": mi}


def _titulo_topico(edition: Edition, topic: IndexTopic) -> str:
    story = next((s for s in edition.stories if s.id == topic.story_id), None) if topic.story_id else None
    if story is not None and story.coverage is not None:
        texto = _titulo_cobertura(edition, story.coverage, story)
        if texto:
            return texto
    return _sem_proibidas(E._titulo_assunto(edition, topic))


def placar_do_dia(
    edition: Edition,
    brand: dict[str, Any],
    *,
    election: bool,
    nomes: Iterable[str] = E.NOMES_ELEICAO,
    numero_serie: int,
    limiares: LimiaresConfig | None = None,
) -> Peca | None:
    """Placar do dia (§4.3): 4 slides, variante da noite quando nenhuma matéria é elegível ao Raio-X.

    1 capa "X DE Y ASSUNTOS DIVIDIRAM A COBERTURA HOJE" com o waffle (com 0 rachas, "DIA DE
    CONSENSO") · 2 o maior consenso (ou os rachas, até 3) · 3 o assunto mais coberto, com o relógio
    se valer · 4 método + CTA. ``None`` sem IA ou com base menor que ``limiares.placar_min_base``.
    """
    lim = limiares or LimiaresConfig()
    if edition.mode != "ai":
        return None
    nomes = tuple(nomes)
    p = E.placar(edition, election=election, nomes=nomes)
    if p.base < lim.placar_min_base:
        return None
    day = edition_day(edition)
    consenso = p.rachas == 0
    variante = "Dia de consenso" if consenso else "Placar do dia"
    selo = _selo(numero_serie)
    nota = f"{kicker_date(day).lower()} · classificação por IA"
    story_ids: list[str] = []
    assuntos_usados: set[str] = set()  # títulos das coberturas do slide 2 (o mais coberto pode ser uma delas)
    secoes: list[str] = []
    slides: list[Slide] = []

    # 1. Capa
    titulo = f"{p.texto_dia} assuntos {_dividiram(p.rachas)} a cobertura hoje"
    svg, d_waf = G.waffle(p.base, p.rachas, rotulos=_rotulos_waffle(p.rachas), lado=88 if p.base <= 21 else _lado_waffle(p.base, 480))
    slides.append(
        Slide(
            "placar_capa",
            {
                "selo": _selo(numero_serie, variante),
                "gancho": S.inseparavel(titulo.upper()),
                "gancho_px": _tam_gancho(S.inseparavel(titulo)),
                "destaque": S.inseparavel(p.texto_dia.upper()),
                "subtitulo": SUB_PLACAR,
                "grafico": svg,
                "nota": nota,
            },
            alt=alt(f"{variante}, Raio-X da cobertura" + (f" nº {numero_serie}" if numero_serie > 0 else "")
                    + f", {_data_extensa(edition)}. {titulo}. Base: {SUB_PLACAR} {d_waf}"),
        )
    )

    # 2. O maior consenso, ou os rachas (sem eleição de outro país no período eleitoral)
    pares = []
    for cov, st in [] if consenso else E.rachas_do_dia(edition, election=election, nomes=nomes):
        if election and _eleicao(cov, st):
            continue
        assunto = _titulo_cobertura(edition, cov, st)
        if assunto:
            pares.append((cov, st, assunto))
        if len(pares) == RACHAS_MAX:
            break
    if consenso or not pares:
        par = E.maior_consenso(edition, election=election, nomes=nomes)
        if par is None:  # nenhuma sem lado: a maior cobertura da base
            par = max(p.itens, key=lambda cs: len(cs[0].outlets))
        if election and _eleicao(*par):  # eleição de outro país não vira destaque no período eleitoral
            sem_eleicao = [cs for cs in p.itens if not _eleicao(*cs)]
            sem_lado = [cs for cs in sem_eleicao if cs[0].count("a") == 0 and cs[0].count("b") == 0]
            par = max(sem_lado or sem_eleicao or [par], key=lambda cs: len(cs[0].outlets))
        cov, st = par
        assunto = _titulo_cobertura(edition, cov, st)
        a, b = cov.count("a"), cov.count("b")
        n_neu = len(cov.outlets) - a - b
        sem_lado = a == 0 and b == 0
        svg, d_bar = G.barra_anb(a, n_neu, b, {"a": "de um lado", "n": "sem lado", "b": "do outro"}, base="", altura=140)
        conclusao = _conclusao_verificada(cov, S.lados(cov, nomes), _titulos_cov(edition, cov, st))
        cabeca = f"{len(cov.outlets)} veículos, nenhum lado" if sem_lado else f"{len(cov.outlets)} veículos, sem divisão"
        section = section_info(edition, E.secao_da_cobertura(edition, cov, st))
        slides.append(
            Slide(
                "placar_lista",
                {
                    "selo": selo,
                    "kicker": "O maior consenso",
                    "titulo": cabeca.upper(),
                    "destaque": f"{len(cov.outlets)} VEÍCULOS",
                    "assunto": assunto,
                    "assunto_px": _tam_serif(assunto, 200, 56, 36, 1.16, 0.56),
                    "texto": conclusao,
                    "texto_px": _tam_serif(conclusao, 250, 40, 28, 1.32, 0.5),
                    "itens": [],
                    "nomes": _lista([o.name for o in cov.outlets]),
                    "grafico": svg,
                    "nota": nota,
                    "section": section,
                },
                alt=alt(f"O maior consenso: {assunto}. {cabeca}. {conclusao} {d_bar} Veículos: {_lista([o.name for o in cov.outlets])}."),
            )
        )
        linha2 = f"▸ Maior consenso: {assunto}, {cabeca}."
        linha_consenso = ""
        assuntos_usados.add(cov.topic)
        if st is not None:
            story_ids.append(st.id)
        secoes.append(section["id"])
    else:
        itens, descricoes = [], []
        sozinho = len(pares) == 1
        for cov, st, assunto in pares:
            ld = S.lados(cov, nomes)
            rot = f" · {ld.rot_maioria} × {ld.rot_minoria}" if ld.rot_maioria and ld.rot_minoria else ""
            svg, d = G.barra_anb(ld.n_maioria, ld.neutros, ld.n_minoria, _rotulos_barra(ld), base="",
                                 altura=112 if sozinho else 64)
            conclusao = _conclusao_verificada(cov, ld, _titulos_cov(edition, cov, st)) if sozinho else ""
            itens.append({
                "assunto": assunto,
                "linha": f"{ld.n_maioria} × {ld.n_minoria}{rot}",
                "contagem": f"{ld.n_maioria} × {ld.n_minoria}" + (f" · {ld.neutros} sem lado" if ld.neutros else ""),
                "grafico": svg,
                "texto": conclusao,
                "section": E.secao_da_cobertura(edition, cov, st),
            })
            descricoes.append(d)
            assuntos_usados.add(cov.topic)
            if st is not None:
                story_ids.append(st.id)
        secoes += [i["section"] for i in itens]
        unica = {i["section"] for i in itens}
        section = section_info(edition, unica.pop()) if len(unica) == 1 else {"id": "", "title": "", "color": ""}
        slides.append(
            Slide(
                "placar_lista",
                {
                    "selo": selo,
                    "kicker": f"{p.texto_dia} assuntos",
                    "titulo": "Onde a cobertura dividiu".upper(),
                    "destaque": "",
                    "assunto": "",
                    "texto": "",
                    "itens": [{k: v for k, v in i.items() if k != "section"} for i in itens],
                    "itens_px": 46 if sozinho else (40 if len(itens) == 2 else 36),
                    "nomes": "",
                    "grafico": "",
                    "nota": nota,
                    "section": section,
                },
                alt=alt("Onde a cobertura dividiu: " + " ".join(
                    f"{i['assunto']}: {i['linha']}." + (f" {i['texto']}" if i["texto"] else "") + f" {d}"
                    for i, d in zip(itens, descricoes))),
            )
        )
        linha2 = f"▸ {_dividiram(len(itens)).capitalize()}: " + "; ".join(f"{i['assunto']} ({i['linha']})" for i in itens)
        consenso_par = E.maior_consenso(edition, election=election, nomes=nomes)
        linha_consenso = ""
        if consenso_par is not None and not (election and _eleicao(*consenso_par)):
            assunto_c = _titulo_cobertura(edition, *consenso_par)
            if assunto_c:
                linha_consenso = f"▸ Maior consenso: {assunto_c}, {len(consenso_par[0].outlets)} veículos, nenhum lado."

    # 3. O mais coberto (no período eleitoral, o seguinte do ranking quando o primeiro é eleição de outro país)
    conc = E.concentracao(edition, election=election, nomes=nomes)
    linha3 = ""
    topo, st, k = None, None, 0
    for cand, kc in E.assuntos_mais_cobertos(edition, election=election, nomes=nomes) if conc is not None else []:
        st_c = next((s for s in edition.stories if s.id == cand.story_id), None) if cand.story_id else None
        if kc > 0 and not (election and E.eleicao_escrita(cand.title, st_c.headline if st_c else "")):
            topo, st, k = cand, st_c, kc
            break
    if conc is not None and topo is not None:
        t = E.topic_texts(topo)
        mesmo = (st is not None and st.id in story_ids) or topo.title in assuntos_usados
        assunto = _titulo_topico(edition, topo)
        rel = E.relogio(edition, st) if st is not None else _relogio_do_assunto(topo)
        section = section_info(edition, topo.section)
        texto_assunto = (
            f"O mesmo assunto foi o mais coberto do dia, entre {E.num(conc.assuntos)}."
            if mesmo else f"{assunto}: o mais coberto do dia, entre {E.num(conc.assuntos)} assuntos."
        )
        titulo3 = f"{k} veículos, {t} textos"
        if E.relogio_valido(rel, min_veiculos=lim.relogio_min_veiculos, min_span_min=lim.relogio_min_span_min):
            assert rel is not None
            stance = {o.name: o.stance for o in st.coverage.outlets} if st is not None and st.coverage else None
            rot_rel = {"a": S.rotulo_lado(st.coverage.side_a, nomes) or "um lado",
                       "b": S.rotulo_lado(st.coverage.side_b, nomes) or "outro lado"} if stance else {}
            disponivel = RELOGIO_PLACAR - 42 * (_linhas_serif(texto_assunto, 32, fator=0.54) - 1)
            svg, d = G.relogio(
                rel.batidas,
                stance,
                day,
                rotulos=rot_rel,
                **_linhas_relogio(len(rel.batidas), disponivel - int(G.altura_legenda_relogio(rel.batidas, stance, rot_rel))),
                base="",
            )
            dur = E.duracao(rel.span)
            vir = S.virada(edition, st, rel, nomes) if st is not None else None
            primeiro, ultimo = rel.batidas[0], rel.batidas[-1]
            h0, h1 = E.horas([primeiro.at, ultimo.at], day)  # data nas duas pontas, se uma for de outro dia
            virada_txt = vir.texto if vir else (
                f"Primeiro horário: {primeiro.source}, {h0}. Último: {ultimo.source}, {h1}. São {dur} entre os dois."
            )
            slides.append(
                Slide(
                    "rx_relogio",
                    {
                        "selo": f"{selo} · Relógio da notícia".upper(),
                        "kicker": "O mais coberto",
                        "titulo": titulo3.upper(),
                        "destaque": f"{k} VEÍCULOS",
                        "assunto": texto_assunto,
                        "grafico": svg,
                        "virada": virada_txt,
                        "rodape": RODAPE_RELOGIO,
                        "nota": nota,
                        "section": section,
                    },
                    alt=alt(f"O mais coberto: {titulo3}. {texto_assunto} {d} {virada_txt} {RODAPE_RELOGIO}"),
                )
            )
            linha3 = (f"▸ O mais coberto: {'o mesmo assunto' if mesmo else assunto}, {k} veículos e {t} textos, de {rel.batidas[0].source} "
                      f"({_hora_completa(rel.batidas[0].at)}) a {rel.batidas[-1].source} ({_hora_completa(rel.batidas[-1].at)}).")
        else:
            contagens = [len(E.topic_sources(x)) for x in edition.index]
            svg, d = G.matriz_pontos(contagens, edition.index.index(topo), rotulo_destaque="o mais coberto", altura_max=640)
            linhas = [texto_assunto, f"{E.num(conc.uma_fonte)} dos {E.num(conc.assuntos)} assuntos saíram em uma fonte só"]
            slides.append(
                Slide(
                    "rx_atencao",
                    {
                        "selo": f"{selo} · O mais coberto".upper(),
                        "kicker": "O mais coberto",
                        "titulo": titulo3.upper(),
                        "destaque": f"{k} VEÍCULOS",
                        "linhas": linhas,
                        "grafico": svg,
                        "nota": nota,
                        "section": section,
                    },
                    alt=alt(f"O mais coberto: {titulo3}. " + ". ".join(linhas) + f". {d}"),
                )
            )
            linha3 = f"▸ O mais coberto: {'o mesmo assunto' if mesmo else assunto}, {k} veículos e {t} textos."
        if st is not None and st.id not in story_ids:
            story_ids.append(st.id)
        secoes.append(topo.section)

    # 4. Método + CTA
    base_txt = f"Base: os {p.base} assuntos do dia com 4 ou mais veículos comparados" + (
        "; no período eleitoral, política e eleição ficam fora." if election else "."
    )
    exemplos = [
        G.barra_anb(2, 1, 2, largura=420, altura=72, base="dividiu: 2 × 2"),
        G.barra_anb(3, 2, 1, largura=420, altura=72, base="não dividiu: 3 × 1"),
    ]
    itens_metodo = [DEFINICAO, base_txt, "Limite: classificação por IA dos textos publicados, numa única leitura. "
                    "Enquadramento é escolha do que destacar, não erro."]
    slides.append(
        Slide(
            "placar_metodo",
            {
                "selo": selo,
                "kicker": "Como medimos",
                "titulo": "O que conta como dividir".upper(),
                "destaque": "",
                "exemplos": [svg for svg, _ in exemplos],
                "itens": itens_metodo,
                "cta": CTA_PLACAR,
                "nota": nota,
            },
            alt=alt("Como medimos: o que conta como dividir. " + " ".join(d for _, d in exemplos) + " "
                    + " ".join(itens_metodo) + f" {CTA_PLACAR}"),
        )
    )

    por_secao = " · ".join(
        f"{SECAO_CURTA.get(sid, section_info(edition, sid)['title'])} {r} de {b}"
        for sid, (r, b) in sorted(p.por_secao.items(), key=lambda kv: (-kv[1][1], kv[0]))
    )
    linha1 = (f"{'Dia de consenso' if consenso else 'Placar do dia'}: {p.texto_dia} assuntos com 4 ou mais veículos "
              f"{_dividiram(p.rachas)} a cobertura hoje.")
    uma_fonte = (f"▸ Pauta solitária: {E.num(conc.uma_fonte)} dos {E.num(conc.assuntos)} assuntos do dia saíram em "
                 "uma fonte só." if conc is not None and conc.assuntos else "")
    caption = _legenda(
        linha1,
        [linha2],
        [linha3, f"▸ Por editoria: {por_secao}." if por_secao else "", linha_consenso, uma_fonte],
        ["Dividir = pelo menos 2 veículos de cada lado. " + METODO, CTA_PLACAR,
         hashtags("#jornalismo", secoes, election=election)],
    )
    return Peca(
        slot=SLOT,
        kind="carousel",
        title="Placar do dia",
        slides=slides,
        caption=caption,
        story_ids=story_ids,
        serie=SERIE,
        numero_serie=numero_serie,
        formula="consenso" if consenso else "placar",
    )

