"""O perfil de cada parlamentar pelo que ele votou no Plenário.

Tudo sai das votações nominais abertas da base (:mod:`.base`), lidas com as
réguas de ``config/congresso.yaml`` (:mod:`.regras`):

- **presença**: em quantas das votações em que podia votar ele votou. Na Câmara,
  "podia votar" é estar em exercício no dia (:mod:`.exercicio`); os dados abertos
  não dizem se a ausência foi justificada. No Senado, a própria votação registra
  quem faltou e quem tinha ausência justificada (licença, missão, atividade
  parlamentar): a justificada fica fora da conta e é mostrada à parte;
- **governismo**: nas votações em que a liderança do Governo orientou Sim ou
  Não, quantas vezes votou igual; contado à parte em cada governo;
- **campo** (com quem vota): só nas votações **de conteúdo** (texto principal,
  destaques e emendas) **divisivas**, em que a maioria do polo de esquerda votou de
  um jeito e a do polo de direita, de outro, e nenhum partido de um polo ficou com o
  outro. O saldo vai de −100 (sempre com a esquerda) a +100 (sempre com a direita);
- **rachas**: as votações em que os polos se opuseram mas um partido de um polo
  ficou com o outro (o PSOL com o PL contra o PT, o NOVO com o PT contra o PL). Não
  medem esquerda × direita (os dois extremos contra o governo, por razões opostas) e
  ficam fora do saldo;
- **fidelidade**: quantas vezes votou com a maioria do próprio partido (o partido
  do dia da votação); votar contra um partido fechado é **dissidência**;
- **surpresas**: quem tem campo definido e, numa votação divisiva, ficou com o
  outro polo.

A obstrução conta como Não (é de quem não quer a matéria votada); abstenção,
quem presidia e quem não registrou voto não entram nas contas de posição.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import base
from .base import AnoVotos, Votacao
from .regras import Regras

Intervalos = Mapping[int, list[tuple[str, str]]]

# O campo conta só as votações de conteúdo: o texto principal e os destaques e emendas.
# Requerimentos e urgências são manobra de plenário (quem obstrui × quem toca a pauta) e
# mediriam governo × oposição, não o que cada um quer que vire lei.
CONTEUDO = frozenset((base.MERITO, base.DESTAQUE))

Z_MUDANCA = 2.58  # 99%: uma mudança de comportamento só é dita quando passa disso…
PONTOS_MUDANCA = 15.0  # …e de 15 pontos percentuais
MINIMO_MUDANCA = 30  # votações de cada lado da comparação


@dataclass
class Fracao:
    k: int = 0  # acertos
    n: int = 0  # tentativas

    def soma(self, acerto: bool) -> None:
        self.n += 1
        self.k += 1 if acerto else 0

    @property
    def pct(self) -> float | None:
        return 100.0 * self.k / self.n if self.n else None


@dataclass
class Eixo:
    """Com que polo votou nas votações divisivas."""

    esq: int = 0
    dir: int = 0

    @property
    def n(self) -> int:
        return self.esq + self.dir

    @property
    def saldo(self) -> float | None:
        return 100.0 * (self.dir - self.esq) / self.n if self.n else None


@dataclass
class Recorte:
    """O que se conta de uma pessoa num conjunto de votações."""

    possiveis: int = 0  # votações em que podia votar (sem as ausências justificadas do Senado)
    votou: int = 0
    sem_voto: int = 0  # presente, sem registrar voto
    justificadas: int = 0  # Senado: licença, missão, atividade parlamentar (fora de "possiveis")
    faltas: int = 0  # Senado: falta registrada; Câmara: em exercício e sem registro
    sim: int = 0
    nao: int = 0
    abstencao: int = 0
    obstrucao: int = 0
    governo: Fracao = field(default_factory=Fracao)
    eixo: Eixo = field(default_factory=Eixo)
    partido: Fracao = field(default_factory=Fracao)

    @property
    def presenca(self) -> float | None:
        return 100.0 * self.votou / self.possiveis if self.possiveis else None


@dataclass
class Leitura:
    """O que uma votação diz antes de olhar para alguém: a orientação do Governo, a
    posição de cada polo e a de cada partido."""

    governo: str = ""  # S/N ou vazio
    esquerda: str = ""
    direita: str = ""
    partidos: dict[str, tuple[str, float, int]] = field(default_factory=dict)  # sigla → (lado, coesão, votos)
    racha: dict[str, str] = field(default_factory=dict)  # partido de um polo → o polo com que votou (o outro)

    @property
    def opostos(self) -> bool:
        """Os dois polos votaram em lados opostos, cada um com a coesão pedida."""
        return bool(self.esquerda and self.direita and self.esquerda != self.direita)

    @property
    def divisiva(self) -> bool:
        """Esquerda contra direita: os polos se opuseram e nenhum partido de um polo ficou
        com o outro. Quando o PSOL vota com o PL contra o PT (os dois extremos contra o
        governo, por razões opostas), a votação não diz quem é de esquerda ou de direita:
        vai para os rachas (``racha``)."""
        return self.opostos and not self.racha


@dataclass
class Marca:
    """Um voto que merece ser mostrado (dissidência, surpresa, voto recente)."""

    votacao: Votacao
    voto: str  # caractere da base
    partido: str = ""
    lado_partido: str = ""  # como a maioria do partido votou
    coesao: float = 0.0
    com: str = ""  # surpresa: o polo com que votou ("esquerda"/"direita")


@dataclass
class Perfil:
    id: int
    casa: str
    nome: str = ""
    uf: str = ""
    partido: str = ""  # o mais recente
    ultimo_voto: str = ""  # data da última votação com registro
    partidos: list[tuple[str, str]] = field(default_factory=list)  # (data da 1ª votação, sigla)
    legislaturas: dict[int, Recorte] = field(default_factory=dict)
    governos: dict[str, Recorte] = field(default_factory=dict)
    semestres: dict[str, Recorte] = field(default_factory=dict)
    temas: dict[int, dict[str, Recorte]] = field(default_factory=dict)  # legislatura → tema → recorte
    tipos: dict[int, dict[str, Recorte]] = field(default_factory=dict)  # legislatura → tipo de votação → recorte
    dissidencias: list[Marca] = field(default_factory=list)
    surpresas: list[Marca] = field(default_factory=list)
    recentes: list[Marca] = field(default_factory=list)  # votos em texto principal e urgência, do mais novo
    # legislatura → partido do dia → com que polo votou enquanto estava nele (para quem trocou de partido)
    eixos_por_partido: dict[int, dict[str, Eixo]] = field(default_factory=dict)
    # Fora da linha, por legislatura: [votos contra o próprio partido fechado, votos com o outro
    # polo] no total e em cada tema (uma votação com dois temas conta nos dois).
    fora: dict[int, list[int]] = field(default_factory=dict)
    fora_tema: dict[int, dict[str, list[int]]] = field(default_factory=dict)
    _divisivas: list[tuple[int, Votacao, str, str]] = field(default_factory=list, repr=False)  # (leg, votação, voto, polo)


# ── leitura de uma votação ───────────────────────────────────────────────────


def _maioria(sim: int, nao: int, minimo: int, coesao: float) -> tuple[str, float]:
    total = sim + nao
    if total < minimo or sim == nao:
        return "", 0.0
    lado, parte = (base.SIM, sim / total) if sim > nao else (base.NAO, nao / total)
    return (lado, parte) if parte >= coesao else ("", parte)


def ler_votacao(v: Votacao, partidos: list[str], regras: Regras) -> Leitura:
    """``partidos``: o partido de cada pessoa do ano na data da votação."""
    lim = regras.limiares
    contagem: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for voto, sigla in zip(v.votos, partidos, strict=False):
        lado = base.POSICAO.get(voto)
        if lado and sigla:
            contagem[sigla][0 if lado == base.SIM else 1] += 1
    leitura = Leitura(governo={"S": base.SIM, "N": base.NAO, "O": base.NAO}.get(v.ori.get(base.GOVERNO, ""), ""))
    for sigla, (sim, nao) in contagem.items():
        lado, parte = _maioria(sim, nao, lim.minimo_partido, 0.5)
        if lado:
            leitura.partidos[sigla] = (lado, parte, sim + nao)
    polos = regras.polos_em(v.data)
    if polos is not None:
        for nome, siglas in (("esquerda", polos.esquerda), ("direita", polos.direita)):
            sim = sum(contagem[s][0] for s in siglas if s in contagem)
            nao = sum(contagem[s][1] for s in siglas if s in contagem)
            setattr(leitura, nome, _maioria(sim, nao, lim.minimo_polo, lim.coesao_polo)[0])
        if leitura.opostos:
            # Um partido de um polo que votou, fechado, com o outro polo: racha.
            for siglas, outro in ((polos.esquerda, "direita"), (polos.direita, "esquerda")):
                lado_do_outro = getattr(leitura, outro)
                for s in sorted(siglas):
                    if s not in contagem:
                        continue
                    lado, _parte = _maioria(*contagem[s], lim.minimo_partido, lim.coesao_polo)
                    if lado and lado == lado_do_outro:
                        leitura.racha[s] = outro
    return leitura


# ── acumulação ───────────────────────────────────────────────────────────────


def _em_exercicio(intervalos: list[tuple[str, str]] | None, data: str) -> bool:
    return bool(intervalos) and any(inicio <= data <= fim for inicio, fim in intervalos)


def _semestre(data: str) -> str:
    return f"{data[:4]}-{1 if data[5:7] <= '06' else 2}"


def _contar(r: Recorte, voto: str, casa: str, exercendo: bool | None) -> None:
    """Presença e voto de uma pessoa numa votação. ``exercendo``: Câmara, se estava em
    exercício no dia (``None``: sem a informação; a presença não é contada)."""
    if voto == base.PRESIDIU:
        return  # quem preside não vota: fora da conta
    if casa == "sf":
        if voto == base.SEM_REGISTRO:
            return
        if voto == base.JUSTIFICADA:
            r.justificadas += 1  # não podia votar: contada à parte, fora da presença
            return
        r.possiveis += 1
        if voto == base.FALTA:
            r.faltas += 1
    else:
        if voto == base.SEM_REGISTRO:
            if exercendo:
                r.possiveis += 1
                r.faltas += 1
            return
        if exercendo is not None:
            r.possiveis += 1
    if voto in base.VOTOU:
        r.votou += 1
    elif voto == base.SEM_VOTO:
        r.sem_voto += 1
    if voto == base.SIM:
        r.sim += 1
    elif voto == base.NAO:
        r.nao += 1
    elif voto == base.ABSTENCAO:
        r.abstencao += 1
    elif voto == base.OBSTRUCAO:
        r.obstrucao += 1


def _posicionar(r: Recorte, lado: str, leitura: Leitura, sigla: str, conteudo: bool) -> None:
    if leitura.governo:
        r.governo.soma(lado == leitura.governo)
    if leitura.divisiva and conteudo:
        if lado == leitura.direita:
            r.eixo.dir += 1
        else:
            r.eixo.esq += 1
    do_partido = leitura.partidos.get(sigla)
    if do_partido:
        r.partido.soma(lado == do_partido[0])


def calcular(
    anos: Iterable[AnoVotos],
    regras: Regras,
    *,
    exercicio: Intervalos | None = None,
    presidencia: Intervalos | None = None,
    recentes: int = 16,
    marcas: int = 12,
) -> dict[int, Perfil]:
    """Os perfis de todos os que votaram nos ``anos`` (de uma Casa só).

    ``exercicio``: na Câmara, os períodos em exercício de cada deputado
    (``{id: [(início, fim)…]}``); sem eles, a presença não é contada.
    ``presidencia``: os períodos em que cada um presidiu a Casa; quem preside só
    vota em caso de empate, então essas votações não entram na presença dele.
    """
    perfis: dict[int, Perfil] = {}
    lim = regras.limiares
    for ano in sorted(anos, key=lambda a: a.ano):
        casa = ano.casa
        plenario = [(k, v) for k, v in enumerate(ano.votacoes) if v.plenario]
        for pessoa in ano.pessoas:
            p = perfis.setdefault(pessoa.id, Perfil(id=pessoa.id, casa=casa))
            p.nome, p.uf = pessoa.nome or p.nome, pessoa.uf or p.uf
            for indice, sigla in pessoa.partidos:
                data = ano.votacoes[indice].data if indice < len(ano.votacoes) else f"{ano.ano}-01-01"
                if not p.partidos or p.partidos[-1][1] != sigla:
                    p.partidos.append((data, sigla))
        for k, v in plenario:
            leg = regras.legislatura_em(v.data)
            governo = regras.governo_em(v.data)
            partidos = [pessoa.partido_em(k) for pessoa in ano.pessoas]
            leitura = ler_votacao(v, partidos, regras) if not v.secreta else Leitura()
            semestre = _semestre(v.data)
            for pessoa, voto, sigla in zip(ano.pessoas, v.votos, partidos, strict=False):
                p = perfis[pessoa.id]
                exercendo: bool | None = None
                if casa == "cd" and exercicio is not None:
                    exercendo = _em_exercicio(exercicio.get(pessoa.id), v.data)
                if presidencia is not None and _em_exercicio(presidencia.get(pessoa.id), v.data) and voto not in base.VOTOU:
                    voto = base.PRESIDIU
                if voto == base.SEM_REGISTRO and not exercendo:
                    continue
                recortes = [p.semestres.setdefault(semestre, Recorte())]
                if leg is not None:
                    recortes.append(p.legislaturas.setdefault(leg.numero, Recorte()))
                if governo is not None:
                    recortes.append(p.governos.setdefault(governo.nome, Recorte()))
                for r in recortes:
                    _contar(r, voto, casa, exercendo)
                if voto != base.SEM_REGISTRO and v.data >= p.ultimo_voto:
                    p.ultimo_voto = v.data
                    p.partido = sigla or p.partido
                lado = base.POSICAO.get(voto)
                if not lado or v.secreta:
                    continue
                conteudo = v.tipo in CONTEUDO
                for r in recortes:
                    _posicionar(r, lado, leitura, sigla, conteudo)
                if leg is not None:
                    por_tipo = p.tipos.setdefault(leg.numero, {}).setdefault(v.tipo, Recorte())
                    _contar(por_tipo, voto, casa, None)
                    _posicionar(por_tipo, lado, leitura, sigla, True)
                    for tema in v.temas:
                        por_tema = p.temas.setdefault(leg.numero, {}).setdefault(tema, Recorte())
                        _contar(por_tema, voto, casa, None)
                        _posicionar(por_tema, lado, leitura, sigla, conteudo)
                    if leitura.divisiva and conteudo:
                        p._divisivas.append((leg.numero, v, voto, "direita" if lado == leitura.direita else "esquerda"))
                        no_partido = p.eixos_por_partido.setdefault(leg.numero, {}).setdefault(sigla, Eixo())
                        if lado == leitura.direita:
                            no_partido.dir += 1
                        else:
                            no_partido.esq += 1
                do_partido = leitura.partidos.get(sigla)
                if do_partido and lado != do_partido[0] and do_partido[1] >= lim.coesao_partido:
                    p.dissidencias.append(
                        Marca(votacao=v, voto=voto, partido=sigla, lado_partido=do_partido[0], coesao=do_partido[1])
                    )
                    if leg is not None:
                        p.fora.setdefault(leg.numero, [0, 0])[0] += 1
                        for tema in v.temas:
                            p.fora_tema.setdefault(leg.numero, {}).setdefault(tema, [0, 0])[0] += 1
                if v.tipo in (base.MERITO, base.URGENCIA):
                    p.recentes.append(Marca(votacao=v, voto=voto, partido=sigla))

    for p in perfis.values():
        for numero, r in p.legislaturas.items():
            saldo = r.eixo.saldo
            if saldo is None or r.eixo.n < lim.minimo_divisivas or abs(saldo) < 60:
                continue
            outro = "esquerda" if saldo > 0 else "direita"
            cruzados = [(v, voto, polo) for leg, v, voto, polo in p._divisivas if leg == numero and polo == outro]
            p.surpresas += [Marca(votacao=v, voto=voto, com=polo) for v, voto, polo in cruzados]
            if cruzados:
                p.fora.setdefault(numero, [0, 0])[1] += len(cruzados)
            for v, _voto, _polo in cruzados:
                for tema in v.temas:
                    p.fora_tema.setdefault(numero, {}).setdefault(tema, [0, 0])[1] += 1
        chave = lambda m: (m.votacao.data, m.votacao.hora, m.votacao.id)  # noqa: E731
        peso = lambda m: (m.votacao.tipo == base.MERITO, *chave(m))  # noqa: E731
        p.dissidencias = sorted(p.dissidencias, key=peso, reverse=True)[:marcas]
        p.surpresas = sorted(p.surpresas, key=peso, reverse=True)[:marcas]
        p.recentes = sorted(p.recentes, key=chave, reverse=True)[:recentes]
        p._divisivas = []
    return perfis


# ── mudanças de comportamento ────────────────────────────────────────────────


def z_duas_proporcoes(k1: int, n1: int, k2: int, n2: int) -> float:
    """Estatística z da diferença entre duas proporções (variância conjunta)."""
    if not n1 or not n2:
        return 0.0
    p = (k1 + k2) / (n1 + n2)
    erro = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (k2 / n2 - k1 / n1) / erro if erro else 0.0


@dataclass
class Mudanca:
    """O governismo de alguém num semestre contra o dos semestres anteriores do mesmo governo."""

    semestre: str
    antes: Fracao
    agora: Fracao
    z: float

    @property
    def pontos(self) -> float:
        return (self.agora.pct or 0.0) - (self.antes.pct or 0.0)


def mudanca_recente(p: Perfil, regras: Regras) -> Mudanca | None:
    """O último semestre com votações bastantes contra os anteriores **do mesmo
    governo**: só devolve quando a diferença de governismo passa de 15 pontos e de
    99% (duas proporções)."""
    semestres = sorted(s for s, r in p.semestres.items() if r.governo.n)
    if len(semestres) < 2:
        return None
    ultimo = semestres[-1]
    governo = regras.governo_em(f"{ultimo[:4]}-{'03' if ultimo.endswith('1') else '09'}-01")
    if governo is None:
        return None
    agora = p.semestres[ultimo].governo
    antes = Fracao()
    for s in semestres[:-1]:
        inicio = f"{s[:4]}-{'01' if s.endswith('1') else '07'}-01"
        if governo.inicio <= inicio <= governo.fim:
            antes.k += p.semestres[s].governo.k
            antes.n += p.semestres[s].governo.n
    if agora.n < MINIMO_MUDANCA or antes.n < MINIMO_MUDANCA:
        return None
    z = z_duas_proporcoes(antes.k, antes.n, agora.k, agora.n)
    mudanca = Mudanca(semestre=ultimo, antes=antes, agora=agora, z=z)
    if abs(z) < Z_MUDANCA or abs(mudanca.pontos) < PONTOS_MUDANCA:
        return None
    return mudanca


# ── a Casa ───────────────────────────────────────────────────────────────────


@dataclass
class Bancada:
    """Um partido numa legislatura: quantos votaram por ele e como."""

    sigla: str
    membros: int = 0
    eixo: Eixo = field(default_factory=Eixo)
    governo: Fracao = field(default_factory=Fracao)
    coesao: float | None = None  # média, nas votações, da parte do partido que votou com a maioria dele
    votacoes: int = 0


def bancadas(anos: Iterable[AnoVotos], regras: Regras, legislatura: int) -> list[Bancada]:
    """Como cada partido votou em bloco numa legislatura (o partido do dia de cada voto)."""
    por: dict[str, Bancada] = {}
    membros: dict[str, set[int]] = defaultdict(set)
    soma: dict[str, float] = defaultdict(float)
    for ano in anos:
        for k, v in enumerate(ano.votacoes):
            leg = regras.legislatura_em(v.data)
            if not v.plenario or v.secreta or leg is None or leg.numero != legislatura:
                continue
            partidos = [pessoa.partido_em(k) for pessoa in ano.pessoas]
            leitura = ler_votacao(v, partidos, regras)
            for sigla, (lado, parte, _n) in leitura.partidos.items():
                b = por.setdefault(sigla, Bancada(sigla=sigla))
                b.votacoes += 1
                soma[sigla] += parte
                if leitura.governo:
                    b.governo.soma(lado == leitura.governo)
                if leitura.divisiva and v.tipo in CONTEUDO:
                    if lado == leitura.direita:
                        b.eixo.dir += 1
                    else:
                        b.eixo.esq += 1
            for pessoa, voto, sigla in zip(ano.pessoas, v.votos, partidos, strict=False):
                if sigla and voto in base.PRESENTE:
                    membros[sigla].add(pessoa.id)
    for sigla, b in por.items():
        b.membros = len(membros[sigla])
        b.coesao = 100.0 * soma[sigla] / b.votacoes if b.votacoes else None
    return sorted(por.values(), key=lambda b: (-b.membros, b.sigla))


def resumo_da_votacao(v: Votacao, ano: AnoVotos, indice: int, regras: Regras) -> dict[str, Any]:
    """Uma votação para mostrar: placar, como o Governo e os polos ficaram e, por
    partido, o lado da maioria e quem votou diferente."""
    partidos = [pessoa.partido_em(indice) for pessoa in ano.pessoas]
    leitura = ler_votacao(v, partidos, regras) if not v.secreta else Leitura()
    conta = v.conta()
    por_partido = []
    for sigla, (lado, parte, n) in sorted(leitura.partidos.items(), key=lambda kv: -kv[1][2]):
        contra = [
            pessoa.id
            for pessoa, voto, s in zip(ano.pessoas, v.votos, partidos, strict=False)
            if s == sigla and base.POSICAO.get(voto) not in (None, lado)
        ]
        por_partido.append({"sigla": sigla, "lado": lado, "coesao": round(100 * parte), "votos": n, "contra": contra})
    return {
        "id": v.id,
        "data": v.data,
        "hora": v.hora,
        "tipo": v.tipo,
        "descricao": v.descricao,
        "prop": v.prop,
        "ementa": v.ementa,
        "temas": v.temas,
        "aprovada": v.aprovada,
        "secreta": v.secreta,
        "sim": conta.get(base.SIM, 0),
        "nao": conta.get(base.NAO, 0) + conta.get(base.OBSTRUCAO, 0),
        "abstencao": conta.get(base.ABSTENCAO, 0),
        "governo": leitura.governo,
        "esquerda": leitura.esquerda,
        "direita": leitura.direita,
        "divisiva": leitura.divisiva,
        "racha": dict(leitura.racha),
        "ori": v.ori,
        "partidos": por_partido,
    }
