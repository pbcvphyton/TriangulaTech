"""A régua da Agenda da semana: a nota de 0 a 85 de cada item de pauta.

A nota é a soma de três partes lidas de ``config/agenda.yaml`` (:mod:`.regras`): FORO (onde se
decide, até 30), ALCANCE (quanta gente a decisão alcança, até 30) e ESTÁGIO (em que ponto está,
até 25). A MÍDIA NÃO PONTUA: a cobertura não escolhe o que entra. A nota mede sinais
institucionais; não diz se a decisão é boa e não prevê resultado.

Tudo aqui é função pura. Além do número, cada nota traz a conta por extenso ("por que está
aqui"), que a página completa mostra ao lado do item.
"""

from __future__ import annotations

from dataclasses import dataclass

from qijournal.agenda import contrato
from qijournal.agenda.regras import Regras


@dataclass(frozen=True)
class Nota:
    """A nota de um item (ou de uma linha sem itens) e as três partes de onde ela saiu."""

    total: int
    foro: int
    alcance: int
    estagio: int
    conta: str  # "Plenário (30) + projeto de lei complementar (30) + em votação (25) = 85"
    estagio_usado: str = "pautado"  # o estágio que pontuou (o de maior peso entre os que valem)


def estagios_validos(sessao: contrato.SessaoAgenda, item: contrato.ItemAgenda) -> tuple[str, ...]:
    """Os estágios do item que valem para a nota e para a tela. "Em votação" só vale com a sessão
    em andamento no dado desta coleta: sessão mantida do arquivo anterior (``como_pautado_em``)
    pode trazer um "em andamento" velho, e a régua não o conta."""
    ao_vivo = sessao.situacao == "em_andamento" and not sessao.como_pautado_em
    validos = tuple(e for e in item.estagios if e != "em_votacao" or ao_vivo)
    return validos or ("pautado",)


def nota_do_item(sessao: contrato.SessaoAgenda, item: contrato.ItemAgenda, regras: Regras) -> Nota:
    """A nota do item na sessão em que ele está pautado, com a conta por extenso."""
    regua = regras.regua
    validos = estagios_validos(sessao, item)
    usado = max(validos, key=lambda e: (regua.pontos_estagio(e), -validos.index(e)))
    foro, alcance, estagio = regua.pontos_foro(sessao.foro), regua.pontos_alcance(item.alcance), regua.pontos_estagio(usado)
    total = min(foro + alcance + estagio, regua.maximo)
    conta = (
        f"{contrato.TERMO_FORO.get(sessao.foro, sessao.foro)} ({foro}) + "
        f"{contrato.TERMO_ALCANCE.get(item.alcance, item.alcance)} ({alcance}) + "
        f"{contrato.TERMO_ESTAGIO.get(usado, usado)} ({estagio}) = {total}"
    )
    return Nota(total=total, foro=foro, alcance=alcance, estagio=estagio, conta=conta, estagio_usado=usado)


def nota_sem_itens(sessao: contrato.SessaoAgenda, regras: Regras) -> Nota:
    """A nota de uma linha que ainda não tem itens (a pauta não saiu ou não foi lida): só o foro."""
    foro = regras.regua.pontos_foro(sessao.foro)
    estagio = regras.regua.pontos_estagio("pauta_nao_publicada")
    total = min(foro + estagio, regras.regua.maximo)
    conta = (
        f"{contrato.TERMO_FORO.get(sessao.foro, sessao.foro)} ({foro}) + "
        f"{contrato.TERMO_PAUTA.get(sessao.pauta, 'sem itens')} ({estagio}) = {total}"
    )
    return Nota(total=total, foro=foro, alcance=0, estagio=estagio, conta=conta, estagio_usado="pauta_nao_publicada")


def ordem_do_item(nota: Nota, item: contrato.ItemAgenda) -> tuple[int, float, int, str]:
    """Chave de ordenação dentro da linha: a maior nota primeiro; no empate, a nota de relevância
    do Congresso (só desempata) e depois a posição na pauta oficial."""
    relevancia = float(item.relevancia) if item.relevancia is not None else -1.0
    ordem = item.ordem if item.ordem is not None else 10**6
    return (-nota.total, -relevancia, ordem, item.identificador)


def notas_da_sessao(sessao: contrato.SessaoAgenda, regras: Regras) -> list[tuple[Nota, contrato.ItemAgenda]]:
    """Os itens da sessão com a nota de cada um, do melhor para o pior."""
    pares = [(nota_do_item(sessao, item, regras), item) for item in sessao.itens]
    return sorted(pares, key=lambda par: ordem_do_item(par[0], par[1]))


def nota_da_linha(sessao: contrato.SessaoAgenda, regras: Regras) -> Nota:
    """A nota da linha: a do seu melhor item; sem itens, só a do foro."""
    pares = notas_da_sessao(sessao, regras)
    return pares[0][0] if pares else nota_sem_itens(sessao, regras)
