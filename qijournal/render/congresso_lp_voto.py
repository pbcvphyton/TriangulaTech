"""A tela da visão por parlamentar, segunda metade: "Na hora do voto" (``#v=decide``), "Pessoa ou
partido?" (``#v=cruzar``) e o bloco "A leitura dos projetos" do Como ler.

Como em :mod:`.congresso_lp` (que chama este módulo e dá os rótulos): aqui só se **apresenta**.
As contas são de :mod:`qijournal.congresso.pais_voto` e :mod:`qijournal.congresso.pais_cruzar`
(os blocos ``decide`` e ``cruzar`` do ``lp`` de cada Casa) e a auditoria da leitura vem de
``meta.json`` (``leitura_projetos``), que só repassa o que foi medido por fora.

- **Nenhum nome de faixa, alavanca, marca ou código é escrito aqui**: os rótulos vêm do config
  (por ``R``, os :class:`.congresso_lp.Rotulos`). O que este módulo nomeia é o que é da tela: as
  **medidas** de cada pessoa (:func:`medidas`: o nome, a unidade, a escala e de que colunas de
  ``autores-<casa>.json`` cada uma sai), as perguntas de "O que anda junto" e as frases.
- **Voto fiscal** é o nome que a régua dá à medida: de que lado do caixa a pessoa vota (Sim em
  matéria de marca +1, Não em matéria de marca −1). Não diz se votou certo.
- **Honestidade**: onde os mínimos da régua não são alcançados, o texto diz "não dá para
  concluir por pessoa" e a tela mostra só o que há por partido; a frase-resposta de "Pessoa ou
  partido?" sai dos números (:func:`resposta`) e muda com eles.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .. import text
from ..congresso import pais_cruzar
from . import congresso as pagina

# As abas desta metade, na ordem: (vista, âncora, nome).
ABAS = (
    ("decide", "cg-decide", "Na hora do voto"),
    ("cruzar", "cg-cruzar", "Pessoa ou partido?"),
)
OUTROS = "Outros partidos"  # como a tela escreve o grupo dos partidos pequenos (pais_voto.OUTROS)
GRUPOS = ((pais_cruzar.VOTO, "Voto"), (pais_cruzar.PROJETOS, "Projetos"), (pais_cruzar.GABINETE, "Gabinete"))
# As medidas de "Na hora do voto" que viram faixa por partido, na ordem do seletor.
DO_VOTO = ("vf", "fp", "pp")
# A força de uma correlação de postos, em palavra: (até, palavra).
FORCAS = ((0.1, "nenhuma"), (0.3, "fraca"), (0.5, "moderada"), (9.0, "forte"))
FORMAS = {"cheio": "●", "vazado": "○", "losango": "◆"}


def _num(valor: Any) -> str:
    return pagina._num(valor)


def _dec(valor: Any, casas: int = 1) -> str:
    """Número com vírgula ("1,9"); sem a casa decimal quando ela é zero ("20")."""
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        return "—"
    return text.format_number_pt(valor, 0 if valor == int(valor) else casas)


def _p(valor: Any) -> str:
    """Percentual com até uma casa ("81,7%"); "—" sem valor."""
    return "—" if valor is None else _dec(valor) + "%"


def _rho(valor: Any) -> str:
    """Correlação com duas casas e o sinal de menos tipográfico ("−0,23")."""
    if valor is None:
        return "—"
    return ("−" if float(valor) < 0 else "") + text.format_number_pt(abs(float(valor)), 2)


def _saldo(valor: Any) -> str:
    return pagina._saldo(valor)


def forca(rho: float) -> str:
    """"nenhuma" (abaixo de 0,1), "fraca" (até 0,3), "moderada" (até 0,5) ou "forte"."""
    return next(palavra for ate, palavra in FORCAS if abs(rho) < ate)


# ── as medidas de cada pessoa ────────────────────────────────────────────────


def medidas(R: Any) -> dict[str, dict[str, Any]]:
    """As medidas da tela, pelo id das contas (:data:`pais_cruzar.MEDIDAS`, mais as duas de
    "quando pesa"): o nome, o nome curto (cabeça de tabela), a unidade (``%``, ``saldo``, ``n``,
    ``peso`` ou ``reais``), a escala do eixo (``min``, ``max``, ``log``, ``zero``) e ``col``: de
    onde sai o valor em ``autores-<casa>.json`` (``[coluna]``, ou ``[parte, todo, mínimo do
    todo]`` para uma proporção; ``padrao``: o que vale quando a coluna vem vazia). ``mais`` e
    ``menos``: como dizer que alguém tem mais ou menos da medida, nas frases de "O que anda
    junto". O script lê isto do manifesto: não repete nome nem conta."""
    cortes = R.m["cortes"]
    caixa = next((rot for ident, rot, _v in R.m["marcas"] if ident == R.m["papeis"].get("caixa")), "contas").lower()
    cem = {"un": "%", "min": 0, "max": 100}
    return {
        "saldo": {"nome": "De que lado vota", "curto": "Lado do voto", "un": "saldo", "min": -100, "max": 100, "zero": True, "col": ["saldo"], "regua": True, "mais": "vota mais à direita", "menos": "vota mais à esquerda"},
        "gov": {"nome": "Governismo", "curto": "Governismo", **cem, "col": ["gov"], "mais": "vota mais com o governo", "menos": "vota menos com o governo"},
        "vf": {"nome": "Voto fiscal", "curto": "Voto fiscal", **cem, "col": ["va", "vn", cortes["min_votos"]], "selo": True, "mais": "dá mais voto fiscal", "menos": "dá menos voto fiscal"},
        "fid": {"nome": "Fidelidade ao partido", "curto": "Fidelidade", **cem, "col": ["fid"], "mais": "é mais fiel ao partido", "menos": "é menos fiel ao partido"},
        "prop": {"nome": "Quantos projetos apresenta", "curto": "Projetos", "un": "n", "min": 1, "log": True, "col": ["prop"], "padrao": 0, "mais": "apresenta mais projetos", "menos": "apresenta menos projetos"},
        "topo": {"nome": f"Parte de {R.topo_pl} confirmados", "curto": f"{R.topo_pl.capitalize()} confirmados", **cem, "col": ["estr", "n", cortes["min_proj"]], "selo": True, "mais": f"tem parte maior de {R.topo_pl} confirmados", "menos": f"tem parte menor de {R.topo_pl} confirmados"},
        "sb": {"nome": f"Parte na faixa “{R.piso}”", "curto": f"Faixa “{R.piso}”", **cem, "col": ["sb", "n", cortes["min_proj"]], "selo": True, "mais": f"tem parte maior na faixa “{R.piso}”", "menos": f"tem parte menor na faixa “{R.piso}”"},
        "tip": {"nome": "Mediana do peso", "curto": "Mediana do peso", "un": "peso", "min": 0, "col": ["tip"], "selo": True, "mais": "tem mediana de peso maior", "menos": "tem mediana de peso menor"},
        "sc": {"nome": f"Saldo de {caixa}", "curto": f"Saldo de {caixa}", "un": "saldo", "min": -100, "max": 100, "zero": True, "col": ["sc"], "selo": True, "mais": "propõe mais do lado do caixa", "menos": "propõe menos do lado do caixa"},
        "tx": {"nome": "Taxa de exceção", "curto": "Taxa de exceção", **cem, "col": ["tx"], "selo": True, "mais": "abre mais exceção", "menos": "abre menos exceção"},
        "pres": {"nome": "Presença", "curto": "Presença", **cem, "col": ["pres"], "mais": "vota mais (presença)", "menos": "vota menos (presença)"},
        "ct": {"nome": "Cota (média por mês)", "curto": "Cota por mês", "un": "reais", "min": 0, "col": ["ct"], "mais": "gasta mais cota", "menos": "gasta menos cota"},
        # só em "Na hora do voto": o item A da régua (quando pesa)
        "fp": {"nome": "Com o partido, quando pesa", "curto": "Com o partido", **cem, "col": ["fp"], "selo": True},
        "pp": {"nome": "Presença, quando pesa", "curto": "Presença", **cem, "col": ["pp"], "selo": True},
    }


def valor(linha: Mapping[str, Any], medida: Mapping[str, Any]) -> float | None:
    """O valor de uma pessoa (uma linha de ``autores-<casa>.json``) numa medida de
    :func:`medidas`; ``None`` quando ela não alcança o mínimo. É a mesma regra que o script
    aplica (``lpValor``), e um teste a confere contra :func:`pais_cruzar.valores`."""
    col = medida["col"]
    if len(col) == 3:
        todo = linha.get(col[1]) or 0
        return 100.0 * (linha.get(col[0]) or 0) / todo if todo >= max(1, col[2]) else None
    bruto = linha.get(col[0])
    return bruto if bruto is not None else medida.get("padrao")


def _valor_txt(v: Any, un: str) -> str:
    """Um valor de medida, escrito na unidade dela."""
    if v is None:
        return "—"
    if un == "%":
        return pagina._pct(v)
    if un == "saldo":
        return _saldo(v)
    if un == "reais":
        return "R$ " + _num(v)
    return _dec(v)


# ── Na hora do voto ──────────────────────────────────────────────────────────


def fiscal(R: Any) -> dict[str, Any]:
    """Como a tela escreve a marca fiscal de uma matéria votada (+1 ou −1): a forma e os
    rótulos, do config, da marca do caixa e da marca de controle (que também conta, na alavanca
    do caixa)."""
    m = R.m
    papeis = m["papeis"]
    por_marca = {ident: valores for ident, _rot, valores in m["marcas"]}

    def lado(sinal: int) -> dict[str, Any]:
        achados = [next((v for v in por_marca.get(papeis.get(papel) or "", []) if v[3] == sinal), None) for papel in ("caixa", "controle")]
        achados = [v for v in achados if v]
        forma = achados[0][2] if achados else ("cheio" if sinal > 0 else "losango")
        return {"forma": forma, "sinal": FORMAS.get(forma, "○"), "n": "+1" if sinal > 0 else "−1", "rot": " ou ".join(v[1] for v in achados), "curto": achados[0][1] if achados else ""}

    return {"mais": lado(1), "menos": lado(-1)}


UM_LADO = 2 / 3  # com esta parte das matérias do voto fiscal numa marca só, a tela avisa o que a medida conta de fato


def _um_lado_so(n: int, mais: int, menos: int, F: Mapping[str, Any], um: str) -> list[tuple[str, bool]]:
    """O aviso que vai ao lado do gráfico do voto fiscal quando quase todas as matérias são de
    uma marca só, em pedaços (texto, em negrito?): com os números, o que a medida conta de fato
    e com o que ela se confunde. Vazio quando as duas marcas estão equilibradas ou não há voto
    fiscal."""
    if not n or max(mais, menos) < UM_LADO * n:
        return []
    so_nao = menos >= mais
    muitas, poucas = (menos, mais) if so_nao else (mais, menos)
    lado, outro = (F["menos"], F["mais"]) if so_nao else (F["mais"], F["menos"])
    quem, com_o_que = (("de oposição", "Não"), "ser oposição ao governo do dia") if so_nao else (("da base", "Sim"), "apoiar o governo do dia")
    return [
        (f"{'Da' if n == 1 else 'Das'} {_num(n)} {'matéria' if n == 1 else 'matérias'} do voto fiscal, ", False),
        (f"{_num(muitas)} {'é' if muitas == 1 else 'são'} de marca {lado['n']}", True),
        (f" e {'nenhuma' if not poucas else 'só ' + _num(poucas)}, de marca {outro['n']}. ", False),
        (f"Aqui a medida conta quase só quem disse {quem[1]} a matéria de marca {lado['n']} (“{lado['rot']}”): {um} {quem[0]} que vota {quem[1]} a quase tudo o que vai a voto pontua alto do mesmo jeito. ", False),
        (f"Voto fiscal alto, aqui, se confunde com {com_o_que}.", True),
        (" Compare com o governismo de cada um antes de ler independência.", False),
    ]


def _dist(v: Any) -> dict[str, str] | None:
    """``[n, mediana, 1º quartil, 3º quartil]`` de um partido numa medida, em texto."""
    if not v:
        return None
    return {"n": _num(v[0]), "med": _p(v[1]), "q1": _p(v[2]), "q3": _p(v[3])}


def decide(casa: str, nome_casa: str, um: str, uns: str, lp: Mapping[str, Any], R: Any, desde: int | None) -> dict[str, Any] | None:
    """O que "Na hora do voto" mostra de uma Casa sem script: a janela, os quatro números do
    universo, "quando pesa", a frase do partido fechado, a potência (quantos alcançam os
    mínimos), a tabela por partido, a tabela das votações e quem mais divergiu."""
    d = lp.get("decide") or {}
    if not d.get("materias"):
        return None
    cortes = R.m["cortes"]
    peso = int(d.get("voto_peso") or cortes["voto_peso"])
    F = fiscal(R)
    n_fiscais, n_peso, n_entram = int(d.get("fiscais") or 0), int(d.get("de_peso") or 0), int(d.get("n_votacoes") or 0)
    mais, menos = int(d.get("favorece") or 0), int(d.get("contraria") or 0)
    pot = d.get("potencia") or {}
    por_pessoa = pot.get("por_pessoa") or {}
    minimo, pessoas_minimo = int(pot.get("minimo_votos") or cortes["min_votos"]), int(pot.get("minimo_pessoas") or cortes["min_pessoas"])
    quase = round(100 * float(cortes.get("quase_unanime") or 0.9))
    fechado = round(100 * float(cortes.get("partido_fechado") or 0.8))
    onde = f"{'na' if casa == 'cd' else 'no'} {nome_casa}"
    so_leves = n_entram - n_peso  # entram só pelo voto fiscal: matéria leve, da alavanca do caixa
    n_demais = int(d.get("demais") or 0)

    def s(n: int, um_: str, uns_: str) -> str:
        return um_ if n == 1 else uns_

    numeros = [
        {
            "n": _num(d.get("materias")), "un": s(int(d["materias"]), "matéria", "matérias"), "f": "com votação nominal do texto principal no Plenário", "selo": False,
            "x": [f"{'desde ' + str(desde) + ', ' if desde else ''}uma votação por matéria (na PEC, o último turno)", *([f"{_num(d['sem_texto'])} só tiveram voto de emenda, substitutivo ou redação final: fora"] if d.get("sem_texto") else [])],
        },
        {
            "n": _num(d.get("universo")), "un": s(int(d.get("universo") or 0), "fica", "ficam"), "f": "depois de tirar as quase unânimes", "selo": False,
            "x": [f"{_num(d.get('quase_unanimes'))} com {quase}% ou mais do mesmo lado: fora", f"{_num(d.get('lidas'))} com leitura; {_num(d.get('sem_leitura'))} sem leitura, fora"],
        },
        {
            "n": _num(n_peso), "un": f"de matéria de peso {peso} ou mais", "f": "o recorte de “quando pesa”", "selo": True,
            "x": [f"{'a outra lida é' if n_demais == 1 else 'as outras ' + _num(n_demais) + ' lidas são'} de peso menor: {'serve' if n_demais == 1 else 'servem'} de comparação"] if n_demais else [],
        },
        {
            "n": _num(n_fiscais), "un": s(n_fiscais, "entra no voto fiscal", "entram no voto fiscal"), "f": f"{_num(mais)} de marca +1 e {_num(menos)} de marca −1", "selo": True,
            "x": ["as outras não têm marca legível no caixa, confirmada por dois leitores"],
        },
    ]  # fmt: skip

    qp = d.get("quando_pesa") or {}

    def par(nome: str, chave: str, frase: str) -> dict[str, Any] | None:
        v = qp.get(chave) or []
        if len(v) != 2 or v[0] is None or v[1] is None:
            return None
        dif = round(float(v[0]) - float(v[1]), 1)
        return {
            "nome": nome, "frase": frase, "peso": _p(v[0]), "demais": _p(v[1]), "w_peso": round(float(v[0]), 1), "w_demais": round(float(v[1]), 1),
            "dif": "igual nas duas" if dif == 0 else f"{_dec(abs(dif))} {'ponto' if abs(dif) == 1 else 'pontos'} {'a mais' if dif > 0 else 'a menos'} quando pesa",
        }  # fmt: skip

    quando = [x for x in (par("Presença", "pres", "votou, entre quem podia votar"), par("Voto com o partido", "fid", "com a maioria do partido do dia")) if x]

    partidos = []
    for b in d.get("por_partido") or []:
        mm = b.get("mm")
        partidos.append(
            {
                "pt": OUTROS if b.get("pt") == "Outros" else b.get("pt") or "",
                "n": _num(b.get("n")),
                "vf": _dist(b.get("vf")),
                "fp": _dist(b.get("fp")),
                "pp": _dist(b.get("pp")),
                "mm": f"{_num(mm[0])} de {_num(mm[1])}" if mm and mm[1] else "—",
            }
        )

    def marca(mf: Any) -> dict[str, Any] | None:
        return F["mais"] if mf == 1 else F["menos"] if mf == -1 else None

    votacoes = []
    for v in d.get("votacoes") or []:
        votacoes.append(
            {
                "data": pagina._data(v.get("d"), ano=True),
                "k": v.get("k") or "",
                "t": v.get("t") or "",
                "f": bool(v.get("f")),
                "e": v.get("e") or "",
                "peso": v.get("w"),
                "de_peso": bool(v.get("pes")),
                "ex": v.get("ex") or "",
                "eixo": R.eixo(v.get("ex") or ""),
                "marca": marca(v.get("mf")),
                "res": v.get("res") or "—",
                "sim": _num(v.get("sim")),
                "nao": _num(v.get("nao")),
                "contra": _num(v.get("contra_partido")),
            }
        )

    divergentes = [
        {
            "k": i.get("k") or "", "nome": i.get("nome") or "", "legenda": "-".join(x for x in (i.get("pt"), i.get("uf")) if x),
            "valor": f"{_num(i.get('div'))} {s(int(i.get('div') or 0), 'vez', 'vezes')} em {_num(i.get('vn'))} votos",
            "extra": (f"em {_num(i['div_cont'])} {s(int(i['div_cont']), 'dela', 'delas')}, do lado do caixa (voto fiscal)" if i.get("div_cont") else ""),
        }
        for i in d.get("divergentes") or []
        if i.get("nome")
    ]  # fmt: skip

    pode_fiscal, pode_peso = bool(por_pessoa.get("fiscal")), bool(por_pessoa.get("peso"))
    aviso = _um_lado_so(n_fiscais, mais, menos, F, um)
    alcancam = [
        f"{_num(pot.get('fiscal'))} {s(int(pot.get('fiscal') or 0), um, uns)} no voto fiscal" if n_fiscais else "",
        f"{_num(pot.get('peso'))} nas votações que entram",
    ]
    if pode_fiscal:
        potencia = f"Deram {minimo} votos ou mais: {' e '.join(x for x in alcancam if x)}."
    elif n_fiscais:
        potencia = (
            f"No voto fiscal, {'nenhum ' + um + ' deu' if not pot.get('fiscal') else 'só ' + _num(pot.get('fiscal')) + ' ' + s(int(pot['fiscal']), um + ' deu', uns + ' deram')} {minimo} votos ou mais"
            f" ({s(n_fiscais, 'é', 'são')} só {_num(n_fiscais)} {s(n_fiscais, 'votação', 'votações')}; a régua pede ao menos {pessoas_minimo} pessoas): não dá para responder por pessoa. A tabela mostra o que há por partido, sem concluir que “é o partido”."
        )
    else:
        potencia = f"Nenhuma votação {onde} entra no voto fiscal: não dá para responder, nem por pessoa nem por partido."
    com_partido = d.get("com_partido_pct")
    return {
        "janela": [
            (f"Votações nominais do texto principal no Plenário {'da' if casa == 'cd' else 'do'} {nome_casa}{', desde ' + str(desde) if desde else ''}", False),
            ("uma por matéria", False),
            (f"{_num(n_entram)} {s(n_entram, 'votação', 'votações')}", True),
            (f"{_num(n_peso)} de matéria de peso {peso} ou mais" + (f" e {_num(so_leves)} {s(so_leves, 'leve', 'leves')}, só do voto fiscal" if so_leves > 0 else ""), False),
            ("só Sim e Não contam como voto", False),
        ],
        "numeros": numeros,
        "quando": quando,
        # o subtítulo de "Quando pesa": quantas votações de cada lado da comparação
        "quando_sub": (
            f"{'na votação' if n_peso == 1 else 'nas ' + _num(n_peso) + ' votações'} de matéria de peso {peso} ou mais"
            + (f", ao lado {'da' if n_demais == 1 else 'das ' + _num(n_demais)} de peso menor" if n_demais else "")
            + f"; os votos de todos os {uns} em exercício, somados"
        ),
        "peso": peso,
        "fiscais": {
            "tem": n_fiscais > 0,
            # a frase: quantas de cada marca e o que o Plenário fez com elas, em pedaços (texto, em negrito?)
            "frase": [
                (f"{'Da' if n_fiscais == 1 else 'Das'} ", False), (_num(n_fiscais), True), (f" {s(n_fiscais, 'matéria', 'matérias')} do voto fiscal, ", False),
                # a marca vem com o que quer dizer (os rótulos do config) na primeira vez em que aparece na vista
                (_num(mais), True), (f" {s(mais, 'é', 'são')} de marca +1" + (f" (“{F['mais']['rot']}”)" if F["mais"]["rot"] else "") + " e ", False),
                (_num(menos), True), (", de marca −1" + (f" (“{F['menos']['rot']}”)" if F["menos"]["rot"] else "") + ". O Plenário aprovou ", False),
                (_num(d.get("aprovadas_favorece")), True), (" das de marca +1 e ", False), (_num(d.get("aprovadas_contraria")), True), (" das de marca −1.", False),
            ],
        },
        "fechado": {
            "de100": _dec(round(float(com_partido))) if com_partido is not None else "", "pct": _p(com_partido), "n": _num(d.get("com_partido_n")), "corte": fechado,
            "onde": "na votação que entra" if n_entram == 1 else f"nas {_num(n_entram)} votações que entram",
        },
        "potencia": potencia,
        "aviso": aviso,  # quando quase todo o voto fiscal é de uma marca só: o que a medida conta de fato, com os números
        "pode": {"vf": pode_fiscal, "fp": pode_peso, "pp": pode_peso},
        "medidas": [ident for ident in DO_VOTO if (pode_fiscal if ident == "vf" else pode_peso)],
        "partidos": partidos,
        "votacoes": votacoes,
        "n_votacoes": _num(n_entram),
        "titulo_votacoes": "A votação que entra" if n_entram == 1 else f"As {_num(n_entram)} votações que entram",
        "ha_mais": n_entram > len(votacoes),
        "divergentes": divergentes,
        "minimo": minimo,
    }


# ── Pessoa ou partido? ───────────────────────────────────────────────────────

RESPOSTAS = {
    "voto_sim_projetos_nao": ("No voto, o partido diz quase tudo.", "No que cada um propõe, quase nada."),
    "voto_sim_projetos_parte": ("No voto, o partido diz quase tudo.", "No que cada um propõe, só uma parte."),
    "os_dois": ("O partido diz quase tudo: no voto e no que cada um propõe.", ""),
    "nem_o_voto": ("Nem no voto o partido diz tudo.", ""),
}
SEM_PROJETOS = "No que cada um propõe, não dá para separar o partido do acaso."
ZONAS = {"quase_tudo": "diz quase tudo", "parte": "diz parte", "pouco": "diz pouco: olhe a pessoa"}
# As perguntas de "O que anda junto", pelo par de medidas das contas; {topo} e {piso} vêm do config.
PERGUNTAS = {
    ("sc", "vf"): "Quem propõe do lado do caixa vota do lado do caixa?",
    ("topo", "vf"): "Quem propõe mais {topo} confirmado dá mais voto fiscal?",
    ("saldo", "topo"): "Um lado do voto propõe mais {topo} confirmado?",
    ("saldo", "sb"): "Um lado do voto propõe mais projeto da faixa “{piso}”?",
    ("fid", "topo"): "Quem é mais fiel ao partido propõe menos {topo} confirmado?",
    ("pres", "prop"): "Quem vota mais apresenta mais projeto?",
    ("pres", "topo"): "Quem vota mais propõe mais {topo} confirmado?",
    ("ct", "prop"): "Quem gasta mais cota apresenta mais projeto?",
    ("ct", "topo"): "Quem gasta mais cota propõe mais {topo} confirmado?",
}


def resposta(frase: Mapping[str, Any], medidas_do_bloco: Mapping[str, Mapping[str, Any]], zonas: list[float], um: str, uns: str, R: Any, funil: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A frase-resposta de "Pessoa ou partido?" (o título da vista) e a linha fina, dos números
    da Casa. ``olhe``: o conselho curto ("para o voto, olhe o partido; para os projetos, olhe a
    pessoa") só sai quando os números dizem isso: o partido diz quase tudo no voto e, no que
    cada um propõe, até o topo do intervalo fica abaixo do corte de "diz pouco".

    "Olhe a pessoa" não quer dizer que a pessoa se distingue: ``funil`` (o bloco ``autores`` da
    Casa) diz quantos saem da faixa do acaso em "Quem propõe", na mesma medida. Se ninguém
    sai, a linha diz isso com o número, para o conselho não prometer mais do que os dados."""
    caso = frase.get("caso") or pais_cruzar.SEM_COMO_DIZER
    n, k = int(frase.get("n") or 0), int(frase.get("k") or 0)
    v, p = medidas_do_bloco.get(pais_cruzar.VOTO_DA_FRASE) or {}, medidas_do_bloco.get(pais_cruzar.PROJETOS_DA_FRASE) or {}
    if caso not in RESPOSTAS:
        titulo = [f"Com {_num(n)} {'nome' if n == 1 else 'nomes'} em {_num(k)} {'partido' if k == 1 else 'partidos'}, não dá para separar o partido do acaso."]
    else:
        primeira, segunda = RESPOSTAS[caso]
        if not frase.get("projetos_distingue") and caso != "os_dois":
            segunda = SEM_PROJETOS
        titulo = [x for x in (primeira, segunda) if x]
    ic_p = p.get("ic") or []
    olhe = caso == "voto_sim_projetos_nao" and len(ic_p) == 2 and ic_p[1] < zonas[0]
    partes: list[tuple[str, bool]] = []
    if v.get("e2") is not None:
        partes += [("O partido explica ", False), (pagina._pct(v["e2"]), True), (f" da diferença entre {uns} no lado em que votam", False)]
        if p.get("e2") is not None:
            partes += [(" e ", False), (pagina._pct(p["e2"]), True), (f" na parte de {R.topo_pl} confirmados do que propõem", False)]
            if not p.get("distingue") and len(ic_p) == 2:
                partes.append((f" (o intervalo vai de {pagina._pct(ic_p[0])} a {pagina._pct(ic_p[1])} e encosta no que o acaso daria)", False))
        partes.append((". ", False))
    partes.append(("Quanto maior a barra, mais o partido basta; quanto menor, mais é preciso olhar a pessoa.", False))
    if olhe:
        partes.append((" Para o voto, olhe o partido; para os projetos, olhe a pessoa.", False))
        comparados = int((funil or {}).get("plotados") or 0)
        fora = int((funil or {}).get("acima_topo") or 0) + int((funil or {}).get("abaixo_topo") or 0)
        if comparados and not fora:
            quantos = f"nenhum dos {_num(comparados)} {uns} comparados" if comparados > 1 else f"o único {um} comparado não"
            partes.append((f" E, nesta medida, nem pessoa a pessoa a diferença passa do acaso: {quantos} em “Quem propõe” sai da faixa cinza.", False))
    return {"titulo": titulo, "caso": caso, "linha": partes, "olhe": olhe}


def cruzar(casa: str, um: str, uns: str, lp: Mapping[str, Any], R: Any, M: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    """O que "Pessoa ou partido?" mostra de uma Casa sem script: a frase-resposta, as barras de
    quanto o partido explica (uma por medida, em três grupos, da maior para a menor), os pares
    de "O que anda junto" e a tabela das medianas por partido. ``M``: as medidas da tela
    (:func:`medidas`)."""
    c = lp.get("cruzar") or {}
    if not c.get("medidas"):
        return None
    par = c.get("parametros") or {}
    zonas = [float(z) for z in (par.get("zonas") or (30, 70))]
    pessoas_minimo = int(par.get("pessoas_minimo") or R.m["cortes"]["min_pessoas"])
    por_id = {x.get("id"): x for x in c["medidas"]}

    def barra(x: Mapping[str, Any]) -> dict[str, Any]:
        ident = x.get("id") or ""
        def_ = M.get(ident) or {}
        e2, ic, acaso = x.get("e2"), x.get("ic") or [], x.get("acaso")
        sem = e2 is None
        if sem:
            palavra = (
                "sem variação entre as pessoas: não há o que explicar" if x.get("motivo") == "sem_variacao"
                else f"não dá para responder por pessoa: {'ninguém pode ser medido' if not x.get('n') else 'só ' + _num(x.get('n')) + (' pode ser medido' if x.get('n') == 1 else ' podem ser medidos')} (a régua pede ao menos {pessoas_minimo})"
            )  # fmt: skip
        elif not x.get("distingue"):
            palavra = "não se distingue do acaso"
        else:
            palavra = ZONAS.get(x.get("zona") or "", "")
        return {
            "id": ident, "nome": def_.get("nome") or ident, "regua": bool(def_.get("regua")), "selo": bool(def_.get("selo")), "sem": sem, "cinza": sem or not x.get("distingue"),
            "e2": pagina._pct(e2) if not sem else "", "w": 0 if sem else round(float(e2), 1), "palavra": palavra,
            "ic": [round(float(ic[0]), 1), round(float(ic[1]), 1)] if len(ic) == 2 else None,
            "ic_txt": f"intervalo de {round(100 * float(par.get('confianca') or 0.95))}%: de {pagina._pct(ic[0])} a {pagina._pct(ic[1])}" if len(ic) == 2 else "",
            "acaso": round(float(acaso), 1) if acaso is not None else None, "acaso_txt": f"por acaso daria até {pagina._pct(acaso)}" if acaso is not None else "",
            "n": _num(x.get("n")), "k": _num(x.get("k")),
        }  # fmt: skip

    grupos = []
    for ident, nome in GRUPOS:
        do_grupo = [x for x in c["medidas"] if x.get("grupo") == ident]
        do_grupo.sort(key=lambda x: (x.get("e2") is None, -(x.get("e2") or 0.0)))
        if do_grupo:
            grupos.append({"nome": nome, "medidas": [barra(x) for x in do_grupo]})

    pares = []
    for x in c.get("pares") or []:
        a, b = x.get("a") or "", x.get("b") or ""
        ma, mb = M.get(a) or {}, M.get(b) or {}
        pergunta = (PERGUNTAS.get((a, b)) or f"{ma.get('nome', a)} × {mb.get('nome', b)}?").format(topo=R.topo, piso=R.piso)
        rho, dentro, n = x.get("rho"), x.get("dentro"), int(x.get("n") or 0)
        if rho is None:
            pares.append({
                "pergunta": pergunta, "sem": True, "cinza": True, "w": 0, "neg": False, "rho": "",
                "resposta": f"Não dá para responder por pessoa: {'ninguém tem' if not n else 'só ' + _num(n) + (' tem' if n == 1 else ' têm')} as duas medidas (a régua pede ao menos {pessoas_minimo}).",
                "dentro": "",
            })  # fmt: skip
            continue
        passa = bool(x.get("passa"))
        if passa:
            # quem tem mais de uma tem mais (ou menos) da outra; no lado do voto, diz-se o lado
            sujeito = ma.get("mais") if rho > 0 or not ma.get("regua") else ma.get("menos")
            predicado = mb.get("mais") if (rho > 0 or ma.get("regua")) else mb.get("menos")
            texto = f"Quem {sujeito} {predicado}: correlação {forca(rho)} ({_rho(rho)}; n = {_num(n)})."
        else:
            texto = f"Não se distingue do acaso ({_rho(rho)}; n = {_num(n)})."
        pares.append({
            "pergunta": pergunta, "sem": False, "cinza": not passa, "w": round(50 * min(1.0, abs(float(rho))), 1), "neg": rho < 0, "rho": _rho(rho), "resposta": texto,
            "dentro": f"dentro do mesmo partido: {_rho(dentro)} ({forca(dentro)})" if dentro is not None else "",
        })  # fmt: skip

    colunas = [ident for ident, _g, _v in pais_cruzar.MEDIDAS if ident in M]
    tabela = [
        {"pt": OUTROS if b.get("pt") == "Outros" else b.get("pt") or "", "n": _num(b.get("n")), "med": [_valor_txt((b.get("med") or {}).get(ident), M[ident]["un"]) for ident in colunas]}
        for b in c.get("por_partido") or []
    ]
    r = resposta(c.get("frase") or {}, por_id, zonas, um, uns, R, lp.get("autores"))
    alfa = float(par.get("alfa_pares") or pais_cruzar.ALFA_PARES)
    return {
        "titulo": r["titulo"],
        "linha": r["linha"],
        "caso": r["caso"],
        "janela": [
            (f"{_num(c.get('pessoas'))} {uns} em exercício, em {_num(c.get('partidos'))} partidos de dois ou mais", True),
            ("% da diferença entre pessoas que o partido explica, medida a medida", False),
            ("todas as alavancas", False),
        ],
        "grupos": grupos,
        "padrao": next((x["id"] for g in grupos for x in g["medidas"] if not x["sem"]), ""),
        "pares": pares,
        "pares_alfa": round(100 * (1 - alfa)),
        "n_pares": len(pares),
        "colunas": [{"id": ident, "nome": M[ident]["curto"]} for ident in colunas],
        "tabela": tabela,
        "zonas": [_dec(zonas[0]), _dec(zonas[1])],
        "confianca": round(100 * float(par.get("confianca") or 0.95)),
        "permutacoes": _num(par.get("permutacoes")),
        "reamostras": _num(par.get("reamostras")),
        "pessoas_minimo": pessoas_minimo,
    }
