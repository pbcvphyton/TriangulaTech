"""Na hora do voto: como cada um vota quando a matéria pesa (régua "Projeto de país", seção 5).

**O universo.** As votações nominais abertas do **texto principal** no Plenário, de todos os
anos da base (desde 2019), de PL, PLP, PEC e medida provisória (os tipos de
``leitura.tipos`` e ``leitura.tipos_votadas``), **uma por matéria**: a última do texto
principal (na PEC, o último turno). Não é do texto principal a da redação final nem a da
mudança feita pela outra Casa (as emendas ou o substitutivo do Senado, na Câmara; os da
Câmara, no Senado): nessa o Sim aceita a mudança, e não a matéria
(:func:`leitura.do_texto_principal`). Ficam fora: a votação **quase unânime**
(``limiares.voto_quase_unanime`` do Sim + Não do mesmo lado) e as votações que já saíram das
contas da página (secretas; registro que não bate com o placar).

**O que conta como voto.** Só Sim e Não. Abstenção não é voto, e **obstrução não é voto aqui**
(no resto da página ela vale como Não): nem no voto de cada um, nem na conta do lado do partido.

**A matéria** é lida pela ementa do registro, como os projetos (:mod:`.leitura`): não é o
substitutivo votado no dia. Matéria sem leitura na guarda fica fora dos dois itens.

- **A. Quando pesa** (sem direção): nas votações de matéria de peso ``limiares.voto_peso`` ou
  mais (a leitura pode ter um leitor só), a **presença** (a mesma conta da página: votou ÷
  podia votar) e o **voto com o partido** (com a maioria do partido do dia, quando ao menos
  ``limiares.minimo_partido`` votaram Sim ou Não), ao lado dos mesmos números nas **demais**
  votações do universo (as de matéria lida e de peso menor).
- **B. Voto fiscal** (pela marca do caixa): só matéria com leitura **confirmada** (duas
  leituras de IA, ``r`` ≥ 2) e marca legível: a marca do caixa +1 ou −1, ou a de controle +1
  ou −1 na alavanca do caixa; de peso ``voto_peso`` ou mais, ou na alavanca do caixa com
  qualquer peso (menos nos escopos de ``papeis.voto_fiscal_sem_escopo``). Marcas +1 e −1 na
  mesma matéria: fora. Marca do caixa **em disputa** entre os leitores que confirmaram a
  faixa (:func:`leitura.contas_em_disputa`): fora, também pela marca de controle. É voto
  fiscal o **Sim em matéria de marca +1** e o **Não em matéria de marca −1**. Mede de que
  lado do caixa a pessoa vota, não se votou certo.
- **Contra o próprio partido fechado**: voto diferente do da maioria do partido do dia quando
  ela reuniu ``limiares.coesao_partido`` do Sim + Não (os limiares que a página já usa para a
  dissidência). Por pessoa só há contagem: não há teste de dissidência seletiva (com tão
  poucas votações nenhum teste teria potência), e o campo ``sel`` não existe.

Mínimos: percentuais por pessoa só com ``limiares.voto_minimo`` votos dados; se menos de
``limiares.pessoas_minimo`` pessoas alcançam o mínimo, o dado diz que não dá para responder
por pessoa (``potencia``).

O que fica de fora: os destaques (onde o conflito costuma estar), o texto votado (lê-se a
ementa), a estimativa de custo, o voto contra cada governo e por porta (despesa, renúncia,
receita, controle), a troca de valores da sensibilidade e a dissidência seletiva por partido.
Voto fiscal se confunde com governismo: matéria do Executivo domina o que vai a voto.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import VERSAO, base, leitura, materias, pais_estat as estat, perfil
from .base import AnoVotos, Votacao
from .leitura import Avaliacao, Leitura
from .pais import Contexto, _curta, _pct
from .perfil import Recorte

NO_PAINEL = 30  # votações mais recentes na tabela do bloco "lp" (o arquivo decide-<casa>.json traz todas)
DIVERGENTES = 10  # a lista de quem mais votou contra o próprio partido fechado
EMENTA_VOTACAO = 120
OUTROS = "Outros"  # os partidos com menos de limiares.partido_minimo em exercício, juntos
_TITULO = re.compile(r"^([A-Z]+) (\d+)/(\d{4})$")
VOTO = frozenset((base.SIM, base.NAO))  # o que conta como voto aqui
COLUNAS_DA_VOTACAO = ["id", "d", "k", "t", "w", "ex", "dir", "pes", "mf", "res", "sim", "nao", "contra_partido", "e", "f", "fx", "fe", "fo"]


@dataclass
class Votada:
    """A votação do texto principal de uma matéria, com a leitura da matéria."""

    v: Votacao
    ano: AnoVotos
    indice: int  # a posição da votação no ano (para o partido do dia)
    titulo: str  # "PL 123/2023"
    k: str  # a chave da matéria na página
    a: Avaliacao
    lida: Leitura | None
    sim: int
    nao: int
    de_peso: bool = False  # item A: matéria lida, de peso limiares.voto_peso ou mais
    marca: int = 0  # item B: +1 ou −1 (a marca fiscal da matéria); 0 = fora do voto fiscal
    partidos: list[str] = field(default_factory=list)  # o partido de cada pessoa do ano, no dia
    lados: dict[str, tuple[str, float]] = field(default_factory=dict)  # partido → (lado da maioria, parte)
    contra: int = 0  # votos contra o próprio partido fechado, entre todos os que votaram

    @property
    def entra(self) -> bool:
        """Vai à tabela e aos cartões: é de peso ou está no voto fiscal."""
        return self.de_peso or self.marca != 0

    @property
    def ordem(self) -> tuple[str, str, str]:
        return (self.v.data, self.v.hora, self.v.id)


def marca_fiscal(ctx: Contexto, a: Avaliacao, lida: Leitura | None) -> int:
    """A marca fiscal de uma matéria votada (item B da régua): +1, −1 ou 0 (fora).

    Fora: sem leitura; leitura **não confirmada** (um leitor só); a marca do caixa **em
    disputa** entre os leitores que confirmaram a faixa (:func:`leitura.contas_em_disputa`:
    nem a marca de controle a põe na conta); nenhuma marca legível (a do caixa em 0 ou "nd",
    e a de controle em 0, "nd" ou fora da alavanca do caixa); as duas marcas em sentidos
    opostos; ou matéria leve (peso abaixo de ``limiares.voto_peso``) fora da alavanca do
    caixa, ou nela com escopo de ``papeis.voto_fiscal_sem_escopo``."""
    papeis = ctx.regua.papeis
    if lida is None or not a.lida or not lida.confirmada or not papeis.caixa or leitura.contas_em_disputa(lida):
        return 0
    na_alavanca = bool(papeis.alavanca_do_caixa) and a.alavanca == papeis.alavanca_do_caixa
    sinais = {a.marcas.get(papeis.caixa)}
    if na_alavanca and papeis.controle:
        sinais.add(a.marcas.get(papeis.controle))
    sinais &= {1, -1}
    if len(sinais) != 1:
        return 0
    codigos_de_peso = {codigo for campo, codigo in a.cod if (c := ctx.regua.campo(campo)) is not None and c.de_peso}
    if (a.peso or 0) >= ctx.regua.limiares.voto_peso or (na_alavanca and not codigos_de_peso & set(papeis.voto_fiscal_sem_escopo)):
        return sinais.pop()
    return 0


def lados_dos_partidos(v: Votacao, partidos: list[str], minimo: int) -> dict[str, tuple[str, float]]:
    """Partido → ``(lado da maioria, parte dela)`` numa votação, contando só Sim e Não (aqui a
    obstrução não é voto). Só os partidos em que ``minimo`` pessoas ou mais votaram e a maioria
    não empatou. O partido que **obstruiu** (tantos em obstrução quantos votando, ou mais) não
    tem lado nesta votação: os poucos que votaram não são "o partido"."""
    contagem: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for voto, sigla in zip(v.votos, partidos, strict=False):
        if sigla and (voto in VOTO or voto == base.OBSTRUCAO):
            contagem[sigla][0 if voto == base.SIM else 1 if voto == base.NAO else 2] += 1
    saida = {}
    for sigla, (sim, nao, obstrucao) in contagem.items():
        if sim + nao >= minimo and sim != nao and obstrucao < sim + nao:
            saida[sigla] = (base.SIM, sim / (sim + nao)) if sim > nao else (base.NAO, nao / (sim + nao))
    return saida


def universo(ctx: Contexto, anos: Iterable[AnoVotos]) -> tuple[list[Votada], dict[str, int]]:
    """As votações que entram em "Na hora do voto", da mais recente para a mais antiga, e as
    contagens do caminho: ``materias`` (com votação nominal do texto principal),
    ``quase_unanimes`` (fora), ``universo`` (as que ficam), ``lidas`` e ``sem_leitura``; e
    ``sem_texto``: as matérias com votação nominal de mérito em que nenhuma é a do texto
    principal (só a redação final, só as emendas ou o substitutivo da outra Casa, ou o
    registro não diz o que foi votado: :func:`leitura.do_texto_principal`). Essas ficam fora:
    nelas o Sim não quer dizer aprovar a matéria."""
    lim, lim_pagina = ctx.regua.limiares, ctx.regras.limiares
    tipos = set(ctx.regua.leitura.tipos) | set(ctx.regua.leitura.tipos_votadas)
    ultima: dict[str, tuple[AnoVotos, int]] = {}
    de_merito: set[str] = set()
    for ano in anos:
        for indice, v in enumerate(ano.votacoes):
            if not (v.plenario and v.tipo == base.MERITO and v.prop) or materias.secreta(v):
                continue
            titulo = materias.titulo_da_materia(v.prop)
            achado = _TITULO.match(titulo)
            if not achado or achado.group(1) not in tipos:
                continue
            de_merito.add(titulo)
            if not leitura.do_texto_principal(v):
                continue
            atual = ultima.get(titulo)
            if atual is None or (v.data, v.hora, v.id) >= (atual[0].votacoes[atual[1]].data, atual[0].votacoes[atual[1]].hora, atual[0].votacoes[atual[1]].id):
                ultima[titulo] = (ano, indice)
    conta = {"materias": len(ultima), "sem_texto": len(de_merito - set(ultima)), "quase_unanimes": 0, "universo": 0, "lidas": 0, "sem_leitura": 0}
    saida: list[Votada] = []
    for titulo, (ano, indice) in ultima.items():
        v = ano.votacoes[indice]
        votos = v.conta()
        sim, nao = votos.get(base.SIM, 0), votos.get(base.NAO, 0)
        if not sim + nao or max(sim, nao) / (sim + nao) >= lim.voto_quase_unanime:
            conta["quase_unanimes"] += 1
            continue
        conta["universo"] += 1
        achado = _TITULO.match(titulo)
        assert achado is not None
        chave = leitura.chave_da_materia(ctx.casa, titulo, v.prop_id)
        item = leitura.Item(casa=ctx.casa, chave=chave, titulo=titulo, tipo=achado.group(1), ano=int(achado.group(3)), ementa=v.ementa)
        lida = ctx.leitor.de(item) if chave else None
        a = leitura.avaliar(ctx.regua, lida, tipo=item.tipo)
        conta["lidas" if a.lida else "sem_leitura"] += 1
        partidos = [pessoa.partido_em(indice) for pessoa in ano.pessoas]
        lados = lados_dos_partidos(v, partidos, lim_pagina.minimo_partido)
        contra = sum(
            1
            for voto, sigla in zip(v.votos, partidos, strict=False)
            if voto in VOTO and sigla in lados and lados[sigla][1] >= lim_pagina.coesao_partido and voto != lados[sigla][0]
        )
        saida.append(
            Votada(
                v=v, ano=ano, indice=indice, titulo=titulo, k=materias.chave(ctx.casa, titulo), a=a, lida=lida, sim=sim, nao=nao,
                de_peso=a.lida and (a.peso or 0) >= lim.voto_peso, marca=marca_fiscal(ctx, a, lida), partidos=partidos, lados=lados, contra=contra,
            )
        )
    saida.sort(key=lambda x: x.ordem, reverse=True)
    return saida, conta


@dataclass
class VotoDaPessoa:
    """O que se conta de uma pessoa em "Na hora do voto"."""

    peso: Recorte = field(default_factory=Recorte)  # presença nas votações de matéria de peso
    demais: Recorte = field(default_factory=Recorte)  # e nas demais do universo
    com_partido: list[int] = field(default_factory=lambda: [0, 0, 0, 0])  # [com o partido, votos com posição do partido] nas de peso; idem nas demais
    dados: int = 0  # votos (Sim ou Não) nas votações que entram (de peso ou do voto fiscal)
    fiscal: list[int] = field(default_factory=lambda: [0, 0])  # [com a marca, votos dados] no voto fiscal
    fechado: list[int] = field(default_factory=lambda: [0, 0])  # [seguiu, votos] com o próprio partido fechado, nas que entram
    div_cont: int = 0  # dos votos contra o partido fechado, os que ficaram com a marca
    votos: list[str] = field(default_factory=list)  # um caractere por votação que entra
    contra: list[int] = field(default_factory=list)  # as posições, em ``votos``, dos votos contra o partido fechado

    @property
    def div(self) -> int:
        return self.fechado[1] - self.fechado[0]

    def colunas(self, minimo: int) -> dict[str, Any]:
        """As colunas do voto em ``autores-<casa>.json``. As porcentagens só saem com
        ``minimo`` casos ou mais; as contagens saem sempre."""

        def pct(parte: int, todo: int) -> float | None:
            return _pct(parte, todo) if todo >= max(1, minimo) else None

        return {
            "va": self.fiscal[0],
            "vn": self.fiscal[1],
            "wn": self.dados,
            "div": self.div,
            "div_cont": self.div_cont,
            "pp": pct(self.peso.votou, self.peso.possiveis),
            "pp_n": self.peso.possiveis,
            "pd": pct(self.demais.votou, self.demais.possiveis),
            "pd_n": self.demais.possiveis,
            "fp": pct(self.com_partido[0], self.com_partido[1]),
            "fp_n": self.com_partido[1],
            "fd": pct(self.com_partido[2], self.com_partido[3]),
            "fd_n": self.com_partido[3],
        }


def acumular(
    ctx: Contexto,
    votadas: list[Votada],
    pessoas: Iterable[int],
    *,
    exercicio: Mapping[int, list[tuple[str, str]]] | None = None,
    presidencia: Mapping[int, list[tuple[str, str]]] | None = None,
) -> dict[int, VotoDaPessoa]:
    """O voto de cada pessoa de ``pessoas`` nas votações do universo que têm leitura.
    ``exercicio`` e ``presidencia``: como em :func:`perfil.calcular` (na Câmara, a presença só
    conta para quem estava em exercício no dia; quem presidia a Casa não entra)."""
    lim = ctx.regras.limiares
    saida = {pid: VotoDaPessoa() for pid in pessoas}
    lidas = [x for x in votadas if x.a.lida]
    entram = [x for x in lidas if x.entra]
    posicao = {id(x): n for n, x in enumerate(entram)}
    for x in lidas:
        indice = x.ano.indice()
        n = posicao.get(id(x))
        for pid, pessoa in saida.items():
            i = indice.get(pid)
            voto = x.v.votos[i] if i is not None and i < len(x.v.votos) else base.SEM_REGISTRO
            if n is not None:
                pessoa.votos.append(voto)
            exercendo: bool | None = None
            if ctx.casa == "cd" and exercicio is not None:
                exercendo = perfil._em_exercicio(exercicio.get(pid), x.v.data)
            presente = voto
            if presidencia is not None and perfil._em_exercicio(presidencia.get(pid), x.v.data) and voto not in base.VOTOU:
                presente = base.PRESIDIU
            if not (presente == base.SEM_REGISTRO and not exercendo):
                perfil._contar(pessoa.peso if x.de_peso else pessoa.demais, presente, ctx.casa, exercendo)
            if voto not in VOTO or i is None:
                continue
            lado = x.lados.get(x.partidos[i])
            if lado is not None:
                desloca = 0 if x.de_peso else 2
                pessoa.com_partido[desloca + 1] += 1
                pessoa.com_partido[desloca] += voto == lado[0]
            if n is None:
                continue
            pessoa.dados += 1
            com_a_marca = x.marca != 0 and (voto == base.SIM) == (x.marca == 1)
            if x.marca != 0:
                pessoa.fiscal[1] += 1
                pessoa.fiscal[0] += com_a_marca
            if lado is not None and lado[1] >= lim.coesao_partido:
                pessoa.fechado[1] += 1
                if voto == lado[0]:
                    pessoa.fechado[0] += 1
                else:
                    pessoa.contra.append(n)
                    pessoa.div_cont += com_a_marca
    return saida


def _resultado(v: Votacao) -> str:
    venceu = materias.sim_venceu(v)
    return "" if venceu is None else ("aprovado" if venceu else "rejeitado")


def _linha_da_votacao(ctx: Contexto, x: Votada) -> list[Any]:
    a = x.a
    return [
        x.v.id, x.v.data, x.k, x.titulo, a.peso, a.alavanca, ctx.direcao(a), 1 if x.de_peso else 0, x.marca, _resultado(x.v), x.sim, x.nao, x.contra,
        _curta(x.v.ementa, EMENTA_VOTACAO), 1 if x.titulo in ctx.fichas else 0, a.faixa, a.faixa_da_ementa, a.fo,
    ]


def _distribuicao(valores: list[float], minimo: int) -> list[Any] | None:
    """``[quantos, mediana, 1º quartil, 3º quartil]``; ``None`` com menos de ``minimo`` valores
    (a régua só dá a mediana de um partido com ``limiares.partido_minimo`` pessoas medidas)."""
    q = estat.quartis(valores) if len(valores) >= max(1, minimo) else None
    return [len(valores), round(q[1], 1), round(q[0], 1), round(q[2], 1)] if q else None


def calcular(
    ctx: Contexto,
    anos: Iterable[AnoVotos],
    *,
    exercicio: Mapping[int, list[tuple[str, str]]] | None = None,
    presidencia: Mapping[int, list[tuple[str, str]]] | None = None,
) -> tuple[dict[str, Any], dict[int, dict[str, Any]], dict[str, Any]]:
    """"Na hora do voto": ``(bloco "decide" do lp, as colunas do voto de cada pessoa em
    exercício, conteúdo de decide-<casa>.json)``."""
    lim = ctx.regua.limiares
    votadas, conta = universo(ctx, anos)
    em_exercicio = {pid: c for pid, c in ctx.cadastros.items() if c.exercicio}
    por_pessoa = acumular(ctx, votadas, em_exercicio, exercicio=exercicio, presidencia=presidencia)
    colunas = {pid: voto.colunas(lim.voto_minimo) for pid, voto in por_pessoa.items()}
    lidas = [x for x in votadas if x.a.lida]
    entram = [x for x in lidas if x.entra]
    fiscais = [x for x in entram if x.marca != 0]

    # Por partido (o de hoje): a distribuição das pessoas em cada medida (só com
    # limiares.partido_minimo pessoas medidas; menos que isso, nulo), e em quantas votações do
    # voto fiscal a maioria da bancada (o partido do dia) ficou com a marca.
    grandes = {sigla for sigla, n in _bancadas(em_exercicio.values()).items() if n >= lim.partido_minimo}
    membros: dict[str, list[int]] = defaultdict(list)
    for pid, cadastro in em_exercicio.items():
        membros[cadastro.partido if cadastro.partido in grandes else OUTROS].append(pid)
    por_partido = []
    for sigla in [*sorted(grandes), OUTROS]:
        if not membros.get(sigla):
            continue
        linhas = [colunas[pid] for pid in membros[sigla]]
        com_a_marca = [x.lados[sigla][0] == (base.SIM if x.marca == 1 else base.NAO) for x in fiscais if sigla in x.lados]
        por_partido.append(
            {
                "pt": sigla,
                "n": len(linhas),
                "vf": _distribuicao([100.0 * linha["va"] / linha["vn"] for linha in linhas if linha["vn"] >= lim.voto_minimo], lim.partido_minimo),
                "fp": _distribuicao([linha["fp"] for linha in linhas if linha["fp"] is not None], lim.partido_minimo),
                "pp": _distribuicao([linha["pp"] for linha in linhas if linha["pp"] is not None], lim.partido_minimo),
                "mm": [sum(com_a_marca), len(com_a_marca)] if sigla != OUTROS else None,
            }
        )
    seguiu, fechado = sum(v.fechado[0] for v in por_pessoa.values()), sum(v.fechado[1] for v in por_pessoa.values())
    alcancam = {
        "peso": sum(1 for v in por_pessoa.values() if v.dados >= lim.voto_minimo),
        "fiscal": sum(1 for v in por_pessoa.values() if v.fiscal[1] >= lim.voto_minimo),
    }
    divergentes = sorted(
        ((pid, v) for pid, v in por_pessoa.items() if v.div and v.dados >= lim.voto_minimo),
        key=lambda par: (-par[1].div, -par[1].dados, em_exercicio[par[0]].nome),
    )
    cols = COLUNAS_DA_VOTACAO
    bloco = {
        "voto_peso": lim.voto_peso,
        **conta,  # materias, sem_texto, quase_unanimes, universo, lidas, sem_leitura
        "n_votacoes": len(entram),
        "de_peso": sum(1 for x in entram if x.de_peso),
        "demais": len(lidas) - sum(1 for x in lidas if x.de_peso),
        "fiscais": len(fiscais),
        "confirmadas": sum(1 for x in lidas if x.lida is not None and x.lida.confirmada),
        "favorece": sum(1 for x in fiscais if x.marca == 1),
        "contraria": sum(1 for x in fiscais if x.marca == -1),
        "aprovadas_favorece": sum(1 for x in fiscais if x.marca == 1 and materias.sim_venceu(x.v)),
        "aprovadas_contraria": sum(1 for x in fiscais if x.marca == -1 and materias.sim_venceu(x.v)),
        "com_partido_pct": _pct(seguiu, fechado),
        "com_partido_n": fechado,
        "quando_pesa": {
            "pres": [_pct(sum(v.peso.votou for v in por_pessoa.values()), sum(v.peso.possiveis for v in por_pessoa.values())), _pct(sum(v.demais.votou for v in por_pessoa.values()), sum(v.demais.possiveis for v in por_pessoa.values()))],
            "fid": [_pct(sum(v.com_partido[0] for v in por_pessoa.values()), sum(v.com_partido[1] for v in por_pessoa.values())), _pct(sum(v.com_partido[2] for v in por_pessoa.values()), sum(v.com_partido[3] for v in por_pessoa.values()))],
        },
        "potencia": {"minimo_votos": lim.voto_minimo, "minimo_pessoas": lim.pessoas_minimo, **alcancam, "por_pessoa": {k: n >= lim.pessoas_minimo for k, n in alcancam.items()}},
        "por_partido": por_partido,
        "votacoes": [dict(zip(cols, _linha_da_votacao(ctx, x), strict=True)) for x in entram[:NO_PAINEL]],
        "divergentes": [
            {"k": f"{ctx.casa}-{pid}", "nome": em_exercicio[pid].nome, "pt": em_exercicio[pid].partido, "uf": em_exercicio[pid].uf, "div": v.div, "div_cont": v.div_cont, "vn": v.dados}
            for pid, v in divergentes[:DIVERGENTES]
        ],
    }
    arquivo = {
        "v": VERSAO,
        "casa": ctx.casa,
        "hoje": ctx.hoje,
        "cols": cols,
        "votacoes": [_linha_da_votacao(ctx, x) for x in entram],
        # As demais do universo com leitura (matéria de peso menor): entram só na comparação do item A.
        "demais": [[x.v.id, x.v.data, x.k, x.titulo, x.a.peso] for x in lidas if not x.entra],
        "votos": {f"{ctx.casa}-{pid}": "".join(v.votos) for pid, v in sorted(por_pessoa.items()) if any(c != base.SEM_REGISTRO for c in v.votos)},
        "contra": {f"{ctx.casa}-{pid}": v.contra for pid, v in sorted(por_pessoa.items()) if v.contra},
    }
    return bloco, colunas, arquivo


def _bancadas(cadastros: Iterable[Any]) -> dict[str, int]:
    conta: dict[str, int] = defaultdict(int)
    for c in cadastros:
        if c.partido:
            conta[c.partido] += 1
    return conta
