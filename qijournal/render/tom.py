"""Tom das manchetes: em que contexto um nome aparece (desfavorável, favorável ou neutro).

É a resposta automática a "a mídia fala bem ou mal dele?", por palavras, sem IA, e
com a mesma regra no servidor (Analítico Político, :mod:`qijournal.render.midia`) e
no navegador (Pesquisa analítica, ``tom.js.j2``, que recebe estas listas):

1. A manchete é dividida em palavras; o nome analisado e os demais nomes do título
   (:func:`qijournal.render.midia.nomes_do_titulo`) ocupam as suas.
2. Cada palavra do dicionário (abaixo) vale para o nome **mais próximo** dela no
   título, até ``JANELA`` palavras de distância (empate: o nome depois da palavra,
   como em "fuga de Flávio", "vitória de Lula"). Palavra logo depois de "não",
   "sem" ou "nem" não conta.
3. Ações contra alguém ("critica", "acusa", "condena", "investiga", "contra"…)
   olham a posição: o nome antes da ação é **quem ataca** (não pesa no tom dele); o
   nome depois é o **alvo** (conta como desfavorável).
4. A manchete é **desfavorável** ao nome quando ele tem mais palavras negativas
   (incluindo ser alvo) que positivas, **favorável** no contrário, **neutra** sem
   nenhuma e **mista** no empate.

Mede o contexto em que o nome aparece nas manchetes (escândalo, derrota, crítica ×
vitória, apoio, aprovação), não a opinião do veículo. Por isso o que conta é a
comparação: um veículo mais negativo que os demais sobre o mesmo nome, no mesmo
período. Só manchetes em português.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

JANELA = 6  # palavras entre a palavra do dicionário e o nome
NEGADORES = frozenset({"nao", "sem", "nem"})

# Palavras sem acento e em minúsculas; "*" no fim = começo de palavra (todas as flexões).
# Expressões de duas palavras valem juntas ("volta atras").
NEGATIVO = (
    # crise, derrota, queda
    "crise crises derrota* derrotad* fracass* fiasco vexame desgast* isolad* racha rachas briga brigas "
    "queda quedas cai caem caiu despenca* desaba* afunda* perde perdem perdeu perda perdas "
    "pior piores piora piorou recua recuou recuo recuos volta_atras perde_apoio perdeu_apoio perdem_apoio "
    "rejeicao reprovacao reprova reprovam reprovado* desaprov* impopular* "
    # escândalo, crime, investigação, punição
    "escandalo* fraude* corrup* propina* desvio* rombo ilegal* irregular* abuso* crime crimes criminos* "
    "suspeit* investigad* investigacao investigacoes inquerito* indiciad* indiciamento* acusad* acusacao acusacoes "
    "denunciad* denuncias reu reus condenad* condenacao condenacoes preso presa presos presas prisao prisoes "
    "foragid* fuga foge fugiu fugir cassad* cassacao inelegivel inelegiveis inelegibilidade impeachment "
    "afastad* afastamento multad* multa multas punid* punicao bloquead* bloqueio "
    # crítica, ataque recebido, ameaça
    "criticas criticado* alvo alvos pressao vaia* vaiad* golpe golpes golpista* antidemocrat* "
    "mentira* mentiu mente erro erros errou erra temor teme temem medo polemic* constrangiment*"
)
POSITIVO = (
    "vence vencem venceu vencer vencedor* vitoria* eleito eleita eleitos reeleit* "
    "lidera lideram liderou liderar liderando a_frente na_frente favorito favorita favoritos favoritismo "
    "cresce crescem cresceu crescimento sobe sobem subiu em_alta dispara_nas avanca avancam avancou avanco "
    "aprova aprovam aprovou aprovado aprovada aprovados aprovadas virada apoio apoios apoia apoiam apoiou elogi* conquist* sucesso* ganha ganham ganhou "
    "absolvid* absolve absolveu absolvicao inocentad* inocencia arquivamento homenage* recupera* melhora melhorou"
)
CONTRA = (
    "critica criticam criticou criticar ataca atacam atacou atacar acusa acusam acusou rebate rebatem rebateu "
    "ironiza ironizou provoca provocou cobra cobram cobrou denuncia denunciam denunciou processa processam "
    "processou ameaca ameacam ameacou xinga xingou condena condenam condenou prende prendem prendeu "
    "investiga investigam investigou indicia indiciou cassa cassou suspende suspendeu barra barrou bloqueia "
    "bloqueou derruba derrubou rejeita rejeitou pressiona pressionam pressionou contra"
)

TOM_ROTULOS = {"neg": "desfavorável", "pos": "favorável", "misto": "misto", "neutro": "neutro"}

_TOKEN = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+")
_MARCAS = re.compile("[̀-ͯ]")


def norm(valor: Any) -> str:
    """Sem acentos e em minúsculas (como :func:`qijournal.render.midia.norm`)."""
    return _MARCAS.sub("", unicodedata.normalize("NFD", str(valor or ""))).lower()


@dataclass(frozen=True)
class Lista:
    """Uma categoria do dicionário: palavras inteiras, começos de palavra e pares."""

    inteiras: frozenset[str]
    comecos: tuple[str, ...]
    pares: frozenset[str]  # "volta atras"

    @classmethod
    def de(cls, texto: str) -> Lista:
        inteiras, comecos, pares = set(), [], set()
        for item in texto.split():
            if "_" in item:
                pares.add(item.replace("_", " "))
            elif item.endswith("*"):
                comecos.append(item[:-1])
            else:
                inteiras.add(item)
        return cls(frozenset(inteiras), tuple(sorted(comecos)), frozenset(pares))

    def casa(self, palavra: str) -> bool:
        return palavra in self.inteiras or any(palavra.startswith(c) for c in self.comecos)

    def exportar(self) -> dict[str, list[str]]:
        """Para a página (``tom.js.j2``)."""
        return {"i": sorted(self.inteiras), "c": list(self.comecos), "p": sorted(self.pares)}


LISTAS = {"neg": Lista.de(NEGATIVO), "pos": Lista.de(POSITIVO), "contra": Lista.de(CONTRA)}


def dicionario() -> dict[str, Any]:
    """O dicionário e os parâmetros, para a Pesquisa analítica (mesma regra no navegador)."""
    return {
        "janela": JANELA,
        "negadores": sorted(NEGADORES),
        **{k: v.exportar() for k, v in LISTAS.items()},
    }


# ── classificação de uma manchete ────────────────────────────────────────────


@dataclass
class Tom:
    tom: str  # neg | pos | misto | neutro
    neg: int = 0
    pos: int = 0
    alvo: bool = False  # o nome é alvo de uma ação contra ele
    ataca: bool = False  # o nome age contra alguém
    gatilhos: list[str] = field(default_factory=list)  # palavras que pesaram (como no título)


def palavras(titulo: str) -> list[tuple[str, str]]:
    """(forma original, forma normalizada) de cada palavra do título."""
    return [(m.group(0), norm(m.group(0))) for m in _TOKEN.finditer(str(titulo or ""))]


def sequencia(nome: str) -> list[str]:
    return [norm(m.group(0)) for m in _TOKEN.finditer(str(nome or ""))]


def ocorrencias(ps: Sequence[str], seq: Sequence[str], *, busca: bool = False) -> list[tuple[int, int]]:
    """Onde a sequência de palavras ``seq`` aparece em ``ps`` (índices da primeira e da
    última). ``busca``: a regra da Pesquisa, em que a última palavra casa pelo começo
    ("Senado" acha "senador"), salvo nome de uma palavra só com até 3 letras."""
    n = len(seq)
    if not n:
        return []
    inteira = not busca or (n == 1 and len(seq[0]) <= 3)
    saida = []
    for i in range(len(ps) - n + 1):
        if all(ps[i + j] == seq[j] for j in range(n - 1)) and (
            ps[i + n - 1] == seq[-1] if inteira else ps[i + n - 1].startswith(seq[-1])
        ):
            saida.append((i, i + n - 1))
    return saida


def classificar(titulo: str, nome: str | Sequence[str], outros: Iterable[str] = ()) -> Tom:
    """O tom da manchete ``titulo`` para ``nome`` (texto ou lista de termos da busca);
    ``outros``: os demais nomes do título."""
    ps = palavras(titulo)
    ns = [n for _o, n in ps]
    termos = [nome] if isinstance(nome, str) else list(nome)
    alvo_idx: set[int] = set()
    ancoras: list[tuple[int, int, bool]] = []  # (início, fim, é o nome analisado)
    for termo in termos:
        for a, b in ocorrencias(ns, sequencia(termo), busca=True):
            ancoras.append((a, b, True))
            alvo_idx.update(range(a, b + 1))
    if not ancoras:
        return Tom("neutro")
    ocupadas = set(alvo_idx)
    for outro in outros:
        for a, b in ocorrencias(ns, sequencia(outro)):
            faixa = set(range(a, b + 1))
            if faixa & alvo_idx:  # o mesmo nome (ou um maior que o contém): é o analisado
                continue
            ancoras.append((a, b, False))
            ocupadas |= faixa
    ancoras.sort()

    def perto(k: int) -> tuple[int, int, bool] | None:
        """O nome mais próximo da palavra k (empate: o de depois), até ``JANELA``."""
        melhor, dist = None, JANELA + 1
        for a, b, eh in ancoras:
            d = a - k if a > k else k - b
            if d < dist or (d == dist and melhor is not None and a > k > melhor[1]):
                melhor, dist = (a, b, eh), d
        return melhor

    def antes(k: int) -> tuple[int, int, bool] | None:
        cand = [x for x in ancoras if x[1] < k and k - x[1] <= JANELA]
        return max(cand, key=lambda x: x[1]) if cand else None

    def depois(k: int) -> tuple[int, int, bool] | None:
        cand = [x for x in ancoras if x[0] > k and x[0] - k <= JANELA]
        return min(cand, key=lambda x: x[0]) if cand else None

    tom = Tom("neutro")
    k = 0
    while k < len(ns):
        if k in ocupadas or (k > 0 and ns[k - 1] in NEGADORES):
            k += 1
            continue
        par = f"{ns[k]} {ns[k + 1]}" if k + 1 < len(ns) and k + 1 not in ocupadas else ""
        cat, largura = None, 1
        for chave in ("neg", "pos"):
            if par and par in LISTAS[chave].pares:
                cat, largura = chave, 2
                break
        if cat is None:
            for chave in ("contra", "neg", "pos"):
                if LISTAS[chave].casa(ns[k]):
                    cat = chave
                    break
        forma = " ".join(o for o, _n in ps[k : k + largura])
        if cat == "contra":
            a, d = antes(k), depois(k)
            if d is not None and d[2]:
                tom.alvo = True
                tom.neg += 1
                tom.gatilhos.append(forma)
            elif a is not None and a[2]:
                tom.ataca = True
        elif cat is not None:
            p = perto(k)
            if p is not None and p[2]:
                setattr(tom, cat, getattr(tom, cat) + 1)
                tom.gatilhos.append(forma)
        k += largura
    if tom.neg > tom.pos:
        tom.tom = "neg"
    elif tom.pos > tom.neg:
        tom.tom = "pos"
    elif tom.neg:
        tom.tom = "misto"
    return tom
