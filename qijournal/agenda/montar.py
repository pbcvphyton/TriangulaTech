"""Monta o retrato da Agenda da semana a partir dos dois arquivos de coleta.

Entram ``data/congresso/agenda.json`` (Câmara, Senado e Congresso) e ``data/tribunais/agenda.json``
(STF e STJ), lidos com o leitor tolerante do contrato: arquivo ausente, ilegível ou de outra
versão vale como lado ausente, e a agenda funciona com um lado só. Sai o
:class:`~qijournal.agenda.retrato.Retrato`, com a home compacta e a visão completa da página.

O que este módulo decide (tudo por regra de ``config/agenda.yaml``, sem IA):

- frescor por fonte: fonte velha demais some e vira uma frase com o link oficial;
- a nota de cada item (:mod:`.regua`) e os cortes: 60 na home, 40 na página completa;
- a janela: o dia da edição e os 4 dias úteis seguintes (sábado e domingo que caem no meio
  entram com a sua data, para um prazo que vence no fim de semana não sumir);
- os tetos da home e o piso por instituição: STF, STJ, Câmara, Senado e Congresso aparecem
  sempre, com o melhor item nomeado da janela ou, sem nada, com uma frase que traz o próximo
  evento com o assunto;
- "Já decidido esta semana": os dias já passados da semana corrente;
- a situação em português claro, pelo dicionário de ``config/agenda.yaml``.

Regras de redação: o verbo é sempre "pautado"; nunca previsão, chance ou adjetivo. A home não
publica contagens nem frase de ausência de cobertura: item sem ligação com a mídia fica calado.

:func:`montar` é função pura; :func:`do_dia` é a borda que lê os arquivos (e nunca levanta por
causa de um arquivo: o que não dá para ler fica de fora).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from qijournal.agenda import contrato, ligar, nomes, regua
from qijournal.agenda import regras as regras_mod
from qijournal.agenda.regras import Regras
from qijournal.agenda.retrato import (
    Contagem,
    DiaTela,
    FonteTela,
    Frase,
    ItemTela,
    LinhaTela,
    MateriaNaAgenda,
    Pagina,
    Pe,
    Retrato,
)

log = logging.getLogger(__name__)

# A ordem em que as instituições são conferidas no piso e em que as frases saem.
ORDEM_DO_PISO = (contrato.STF, contrato.STJ, contrato.CAMARA, contrato.SENADO, contrato.CONGRESSO)
DIAS_CURTOS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
DIAS_LONGOS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")
MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
SITUACOES_SEM_DECISAO = ("cancelada", "adiada")  # a sessão não acontece: fica fora da home
RESULTADOS_QUE_DECIDEM = ("aprovado", "aprovado_ressalvados_destaques", "rejeitado")
ROTULO_DO_LINK = "fonte oficial"
SEM_ALCANCE_GERAL = "nada de alcance geral pautado"  # a frase do piso dos tribunais
SEM_RESULTADO = "sem resultado no registro oficial"
# Como as contagens das fontes se chamam na página completa (as demais saem com o nome simples).
ROTULO_DA_CONTAGEM = {
    "audiencias": "audiências públicas", "eventos_cancelados": "eventos cancelados", "outros_eventos": "outros eventos",
    "reunioes_de_outras_comissoes": "reuniões de outras comissões", "sessoes_solenes": "sessões solenes",
    "sessoes_nao_deliberativas": "sessões não deliberativas", "reunioes_nao_deliberativas": "reuniões não deliberativas",
    "itens_terminativos_em_outras_comissoes": "itens terminativos em outras comissões", "sessoes": "sessões lidas",
    "itens": "processos lidos", "propostas": "propostas de afetação", "repetitivos": "recursos repetitivos",
    "vetos_tramitando": "vetos em tramitação", "vetos_trancando": "vetos que trancam a pauta",
}


# ── datas e textos pequenos ──────────────────────────────────────────────────────────────────


def rotulo_do_dia(dia: str) -> str:
    """"qua 25": o dia da semana abreviado e o dia do mês."""
    d = date.fromisoformat(dia[:10])
    return f"{DIAS_CURTOS[d.weekday()]} {d.day}"


def rotulo_longo(dia: str) -> str:
    """"quarta-feira, 25 de novembro"."""
    d = date.fromisoformat(dia[:10])
    return f"{DIAS_LONGOS[d.weekday()]}, {d.day} de {MESES[d.month - 1]}"


def dia_mes(dia: str | None) -> str:
    """"25/11" (vazio sem data ou com data ilegível)."""
    try:
        d = date.fromisoformat(str(dia)[:10])
    except ValueError:
        return ""
    return f"{d.day:02d}/{d.month:02d}"


def hora_de_tela(hora: str | None) -> str:
    """"14h" ou "13h55"; vazio quando a fonte não informa a hora."""
    if not hora or ":" not in hora:
        return ""
    horas, minutos = hora.split(":")[:2]
    try:
        return f"{int(horas)}h" if int(minutos) == 0 else f"{int(horas)}h{int(minutos):02d}"
    except ValueError:
        return ""


def _milhar(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _tela(texto: str | None) -> str:
    """Texto oficial curto (nome de órgão, de fonte, de ministro) limpo para a tela, sem travessão."""
    return nomes.limpar(texto)


def _plural(n: int, um: str, varios: str) -> str:
    return f"{_milhar(n)} {um if n == 1 else varios}"


# ── fontes e frescor ─────────────────────────────────────────────────────────────────────────

# De que fonte vem cada linha (as demais fontes de uma instituição são auxiliares: não derrubam
# linhas e só aparecem na lista de fontes da página completa).
FONTES_PRINCIPAIS = frozenset((
    "camara_eventos", "senado_plenario", "senado_comissoes", "congresso_sessoes", "congresso_prazos",
    "congresso_comissoes", "stf_pauta", "stj_pautas",
))


def chave_da_fonte(sessao: contrato.SessaoAgenda) -> str:
    """A chave da fonte de onde a linha veio (``fontes`` do arquivo de coleta)."""
    inst, foro = sessao.instituicao, sessao.foro
    if inst == contrato.CAMARA:
        return "camara_eventos"
    if inst == contrato.SENADO:
        return "senado_plenario" if foro == "plenario" else "senado_comissoes"
    if inst == contrato.CONGRESSO:
        return "congresso_prazos" if foro == "prazo" else "congresso_comissoes" if foro.startswith("comissao") else "congresso_sessoes"
    return "stf_pauta" if inst == contrato.STF else "stj_pautas"


@dataclass(frozen=True)
class _Fonte:
    fonte: contrato.FonteAgenda
    estado: str  # "ok" | "aviso" | "some"


@dataclass(frozen=True)
class _Linha:
    """Uma sessão com as notas dos seus itens e a faixa em que ela cai."""

    sessao: contrato.SessaoAgenda
    lado: str
    notas: tuple[tuple[regua.Nota, contrato.ItemAgenda], ...]  # do melhor item para o pior
    nota: regua.Nota
    faixa: str  # "passado" | "janela" | "radar" | "fora"

    @property
    def id(self) -> str:
        return self.sessao.id

    @property
    def ordem_no_dia(self) -> tuple[str, str, int, str]:
        """Por dia; dentro do dia, a hora oficial (linha sem hora depois) e, no empate, a nota."""
        return (self.sessao.data, self.sessao.hora or "99:99", -self.nota.total, self.sessao.id)

    @property
    def ordem_por_nota(self) -> tuple[int, str, str, str]:
        return (-self.nota.total, self.sessao.data, self.sessao.hora or "99:99", self.sessao.id)


@dataclass
class _Contexto:
    dia: str
    regras: Regras
    ligacoes: Mapping[str, ligar.Ligacao]
    janela: tuple[str, ...]
    fim: str
    semana: str
    agendas: dict[str, contrato.Agenda] = field(default_factory=dict)
    fontes: dict[str, _Fonte] = field(default_factory=dict)
    linhas: list[_Linha] = field(default_factory=list)
    nomes: dict[int, nomes.Nome] = field(default_factory=dict)

    def nome(self, item: contrato.ItemAgenda) -> nomes.Nome:
        chave = id(item)
        if chave not in self.nomes:
            self.nomes[chave] = nomes.nome_do_item(item, self.regras)
        return self.nomes[chave]

    def faixa(self, data: str) -> str:
        if self.dia <= data <= self.fim:
            return "janela"
        if data > self.fim:
            return "radar"
        return "passado" if data >= self.semana else "fora"

    def estado_da_fonte(self, sessao: contrato.SessaoAgenda, agenda: contrato.Agenda) -> str:
        """O frescor da fonte da linha; sem a fonte esperada no arquivo, o melhor estado entre as
        fontes da instituição (e, sem nenhuma, o do próprio arquivo)."""
        fonte = self.fontes.get(chave_da_fonte(sessao))
        if fonte is not None and fonte.fonte.instituicao == sessao.instituicao:
            return fonte.estado
        estados = [f.estado for f in self.fontes.values() if f.fonte.instituicao == sessao.instituicao]
        if estados:
            return next(e for e in ("ok", "aviso", "some") if e in estados)
        return self.regras.frescor_de(agenda.hoje, self.dia)

    def instituicao_valida(self, instituicao: str) -> bool:
        """A instituição tem fonte de pauta que ainda vale (não sumiu por estar velha)."""
        fontes = [f for f in self.fontes.values() if f.fonte.instituicao == instituicao]
        if not fontes:  # arquivo sem o registro da fonte: valem as linhas que ele trouxe, pela data do arquivo
            return any(l.sessao.instituicao == instituicao for l in self.linhas)
        principais = [f for f in fontes if f.fonte.chave in FONTES_PRINCIPAIS] or fontes
        return any(f.estado != "some" for f in principais)


def _principal(fonte: _Fonte, todas: Iterable[_Fonte]) -> bool:
    """A fonte é de pauta (derruba linhas quando some). Instituição sem nenhuma fonte de nome
    conhecido tem todas as fontes como principais."""
    if fonte.fonte.chave in FONTES_PRINCIPAIS:
        return True
    return not any(f.fonte.chave in FONTES_PRINCIPAIS for f in todas if f.fonte.instituicao == fonte.fonte.instituicao)


# ── itens e linhas prontos para a tela ───────────────────────────────────────────────────────


def _chave_do_prazo(item: contrato.ItemAgenda) -> str:
    return {"mpv": "prazo_mpv", "veto": "prazo_veto"}.get(item.alcance, "prazo_vence")


def palavra_do_resultado(resultado: contrato.Resultado) -> str:
    """A palavra do resultado, conferida com a frase oficial: "aprovado" cuja frase diz
    "ressalvados os destaques" é o texto principal aprovado, com trechos ainda por votar."""
    frase = (resultado.texto or "").lower()
    if resultado.palavra == "aprovado" and "ressalvad" in frase and "destaque" in frase:
        return "aprovado_ressalvados_destaques"
    return resultado.palavra


def _situacao_do_item(linha: _Linha, item: contrato.ItemAgenda, ctx: _Contexto) -> tuple[str, str, str]:
    """``(a frase em português claro, o termo oficial, o resultado oficial ou vazio)``."""
    sessao, regras = linha.sessao, ctx.regras
    validos = regua.estagios_validos(sessao, item)
    estagio = validos[0]
    detalhe = _tela(item.estagio_detalhe) if estagio == item.estagio else ""
    termo = contrato.termo(estagio)
    if detalhe:  # "Urgência (Art. 155, RICD)" já diz o termo: não repete
        termo = detalhe if detalhe.lower().startswith(termo.lower()) else f"{termo} ({detalhe})"
    if validos[1:]:
        termo = "; ".join([termo, *(contrato.termo(e) for e in validos[1:])])
    if estagio == "prazo_vence":
        clara = regras.situacao(_chave_do_prazo(item))
    elif estagio in ("retorno_de_vista", "destaque"):
        clara = regras.situacao(estagio, detalhe or None)
    else:
        clara = regras.situacao(estagio)
    resultado = ""
    com_resultado = item.resultado is not None and (
        sessao.situacao == "encerrada" or item.resultado.palavra in contrato.RESULTADOS_ANTES_DO_FIM
    )
    if com_resultado and item.resultado is not None:
        palavra = palavra_do_resultado(item.resultado)
        resultado = contrato.termo(palavra)
        clara = regras.situacao(palavra)
    elif linha.faixa == "passado" and sessao.foro != "prazo":
        if linha.lado == contrato.LADO_TRIBUNAIS:
            clara = regras.situacao("resultado_tribunal")  # os tribunais não publicam o resultado em dado aberto
        elif sessao.situacao == "encerrada" and not sessao.como_pautado_em:
            clara = SEM_RESULTADO
        else:
            clara = ""  # a linha diz "em andamento" ou "como pautado em DD/MM": sem resultado
    return clara, termo, resultado


def _relator_de_tela(relator: contrato.Relator | None) -> str:
    """O relator como a fonte informa: "Fulano (PT-SP)"; ministro vem só com o nome."""
    if relator is None or not _tela(relator.nome):
        return ""
    sigla = "-".join(parte for parte in (_tela(relator.partido), _tela(relator.uf)) if parte)
    return f"{_tela(relator.nome)} ({sigla})" if sigla else _tela(relator.nome)


def _item_tela(
    linha: _Linha, nota: regua.Nota, item: contrato.ItemAgenda, ctx: _Contexto, *, na_linha_de_prazo: bool = False, pagina: bool = False
) -> ItemTela:
    """O item pronto. ``pagina`` acrescenta o que só a página completa mostra (o relator)."""
    nome = ctx.nome(item)
    apelidos = ctx.regras.apelidos_de(item.identificador, ctx.dia)
    ligacao = ctx.ligacoes.get(item.identificador) or ligar.SEM_LIGACAO
    veiculos = ligacao.n_veiculos
    clara, termo, resultado = _situacao_do_item(linha, item, ctx)
    return ItemTela(
        identificador=item.identificador,
        identificador_tela=f"{item.identificador} ({_tela(apelidos[0])})" if apelidos else item.identificador,
        link=item.link,
        nome=nome.texto,
        nome_curto=nome.curto,
        area=ctx.regras.palavra_de_area(item.area) or "",
        situacao="" if na_linha_de_prazo else clara,  # no prazo, a frase já está na linha
        termo=termo,
        resultado=resultado,
        nota=nota.total,
        conta=nota.conta,
        midia=veiculos,
        midia_texto=f"na mídia: {_plural(veiculos, 'veículo', 'veículos')}" if veiculos else "",
        midia_pagina=f"menção direta encontrada em {_plural(veiculos, 'veículo', 'veículos')}" if veiculos else "",
        materia=ligacao.materias[0] if ligacao.materias else "",
        relator=_relator_de_tela(item.relator) if pagina else "",
    )


def _situacao_da_linha(linha: _Linha, ctx: _Contexto) -> str:
    """O texto da linha inteira (a sessão), ou vazio quando ela só está agendada com a pauta publicada."""
    sessao, regras = linha.sessao, ctx.regras
    if sessao.situacao in SITUACOES_SEM_DECISAO:
        return contrato.termo(sessao.situacao)
    if sessao.foro == "prazo" and linha.notas:
        return regras.situacao(_chave_do_prazo(linha.notas[0][1]))
    if sessao.como_pautado_em:
        return regras.situacao("como_pautado", data=dia_mes(sessao.como_pautado_em))
    if sessao.situacao in ("em_andamento", "encerrada"):
        return contrato.termo(sessao.situacao)
    if not sessao.itens and sessao.pauta != "publicada":
        if sessao.pauta == "nao_publicada" and sessao.instituicao == contrato.CAMARA and sessao.foro == "plenario":
            return regras.situacao("pauta_camara_nao_publicada")
        return contrato.TERMO_PAUTA.get(sessao.pauta, "")
    if linha.faixa == "passado" and linha.lado == contrato.LADO_CONGRESSO:
        return SEM_RESULTADO
    return ""


def _mais_texto(mais: int, nomeados: int) -> str:
    if mais <= 0:
        return ""
    if nomeados:
        return f"mais {_milhar(mais)}"
    return _plural(mais, "item pautado", "itens pautados")


def _linha_tela(
    linha: _Linha,
    ctx: _Contexto,
    *,
    pagina: bool = False,
    piso: bool = False,
    so_o_item: contrato.ItemAgenda | None = None,
) -> LinhaTela:
    """A linha pronta. Na home, só os itens nomeados (até o teto por linha; um só na linha do
    piso) e o resto em "mais N"; na página completa, todos os itens de nota igual ou acima do
    corte, com o que ficou fora. ``so_o_item`` faz a linha de um item só ("Já decidido")."""
    sessao, regras = linha.sessao, ctx.regras
    corte = regras.cortes.pagina
    listados = [par for par in linha.notas if par[0].total >= corte]
    de_prazo = sessao.foro == "prazo"
    if so_o_item is not None:
        escolhidos = [par for par in linha.notas if par[1] is so_o_item]
    elif pagina:
        escolhidos = listados
    else:
        teto = 1 if piso else max(1, regras.tetos.home.itens_por_linha)
        candidatos = linha.notas if piso else listados
        escolhidos = [par for par in candidatos if ctx.nome(par[1])][:teto]
    itens = tuple(_item_tela(linha, nota, item, ctx, na_linha_de_prazo=de_prazo, pagina=pagina) for nota, item in escolhidos)
    mais = 0 if pagina or so_o_item is not None else max(0, len(listados) - sum(1 for par in escolhidos if par in listados))
    situacao = _situacao_da_linha(linha, ctx)
    if so_o_item is not None and itens and itens[0].resultado and sessao.situacao == "encerrada":
        situacao = ""  # o resultado do item já diz que a sessão acabou
    no_celular = max(1, regras.tetos.home.celular_itens_por_linha)
    mais_celular = mais + max(0, len(itens) - no_celular) if not pagina else 0
    instituicao = contrato.NOME_INSTITUICAO.get(sessao.instituicao, sessao.instituicao)
    orgao = "" if de_prazo else _tela(sessao.orgao)
    return LinhaTela(
        id=sessao.id,
        data=sessao.data,
        dia=rotulo_do_dia(sessao.data),
        hora=hora_de_tela(sessao.hora),
        instituicao=instituicao,
        orgao=orgao,
        cabeca=f"{instituicao} · {orgao}" if orgao else instituicao,
        link=sessao.link,
        situacao=situacao,
        itens=itens,
        mais=mais,
        mais_texto=_mais_texto(mais, len(itens)),
        mais_celular=mais_celular,
        mais_celular_texto=_mais_texto(mais_celular, min(len(itens), no_celular)),
        nota=linha.nota.total,
        passado=linha.faixa == "passado",
        piso=piso,
        fora=tuple(
            Contagem(rotulo=contrato.TERMO_FORA.get(motivo, motivo), n=n, texto=f"{contrato.TERMO_FORA.get(motivo, motivo)}: {_milhar(n)}")
            for motivo, n in sessao.fora
            if n
        ) if pagina else (),
        abaixo_do_corte=len(linha.notas) - len(listados) if pagina else 0,
    )


def _dias(linhas: Iterable[LinhaTela], datas: Iterable[str], ctx: _Contexto, *, passado: bool = False, com_vazios: bool = False) -> tuple[DiaTela, ...]:
    """As linhas agrupadas por dia, na ordem do dia. ``datas`` são os dias que aparecem mesmo
    sem linha (com ``com_vazios``, levam a frase do dia vazio)."""
    por_dia: dict[str, list[LinhaTela]] = {d: [] for d in datas}
    for linha in linhas:
        por_dia.setdefault(linha.data, []).append(linha)
    principais = set(ctx.janela[:2]) | {d for d in por_dia if d < ctx.janela[0]}
    return tuple(
        DiaTela(
            data=dia,
            rotulo=rotulo_do_dia(dia),
            rotulo_longo=rotulo_longo(dia),
            linhas=tuple(por_dia[dia]),
            vazio=ctx.regras.situacao("dia_vazio") if com_vazios and not por_dia[dia] else "",
            principal=not passado and dia in principais,
            passado=passado,
        )
        for dia in sorted(por_dia)
        if por_dia[dia] or com_vazios
    )


# ── frases ───────────────────────────────────────────────────────────────────────────────────


def _sem_pauta_ainda(linha: _Linha) -> bool:
    """Sessão que decide (Plenário, Corte Especial, Seção, comissão que decide) com a pauta ainda
    não publicada ou não lida: a página completa a mostra em uma linha, sem itens."""
    sessao = linha.sessao
    return not sessao.itens and sessao.pauta != "publicada" and sessao.foro not in ("turma", "comissao", "prazo")


def _melhor_nomeado(linha: _Linha, ctx: _Contexto) -> tuple[regua.Nota, contrato.ItemAgenda] | None:
    return next((par for par in linha.notas if ctx.nome(par[1])), None)


def _ponto(texto: str) -> str:
    return texto if texto.endswith(("…", ".", "!", "?")) else f"{texto}."


def _frase_da_fonte(fonte: _Fonte) -> Frase:
    f = fonte.fonte
    nome = _tela(f.nome) or contrato.NOME_INSTITUICAO.get(f.instituicao, f.instituicao)
    desde = dia_mes(f.data_dados)
    texto = f"{nome}: sem dado novo desde {desde}; os itens desta fonte saíram da agenda." if desde else f"{nome}: fonte ainda não lida."
    return Frase(tipo="fonte", instituicao=contrato.NOME_INSTITUICAO.get(f.instituicao, f.instituicao), texto=texto, link=f.url, link_rotulo=ROTULO_DO_LINK)


def _frases_de_fonte_velha(ctx: _Contexto) -> list[Frase]:
    """Fonte de pauta velha demais: os itens dela saíram e fica uma frase com o link oficial.
    Uma frase por instituição quando todas as fontes de pauta dela estão velhas; senão, uma por fonte."""
    frases: list[Frase] = []
    for instituicao in ORDEM_DO_PISO:
        fontes = [f for f in ctx.fontes.values() if f.fonte.instituicao == instituicao and _principal(f, ctx.fontes.values())]
        velhas = sorted((f for f in fontes if f.estado == "some"), key=lambda f: f.fonte.chave)
        if not velhas:
            continue
        if len(velhas) < len(fontes):
            frases += [_frase_da_fonte(f) for f in velhas]
            continue
        nome_inst = contrato.NOME_INSTITUICAO.get(instituicao, instituicao)
        desde = dia_mes(max((f.fonte.data_dados or "" for f in velhas), default=""))
        texto = f"{nome_inst}: sem dado novo desde {desde}; os itens saíram da agenda." if desde else f"{nome_inst}: pauta ainda não lida."
        frases.append(Frase(tipo="fonte", instituicao=nome_inst, texto=texto, link=velhas[0].fonte.url, link_rotulo=ROTULO_DO_LINK))
    return frases


def _sem_pauta(ctx: _Contexto) -> str:
    """"Sem pauta válida desde DD/MM." quando nenhuma fonte de pauta vale mais; senão, vazio."""
    if any(ctx.instituicao_valida(i) for i in contrato.INSTITUICOES):
        return ""
    desde = dia_mes(max((f.fonte.data_dados or "" for f in ctx.fontes.values()), default=""))
    return f"Sem pauta válida desde {desde}." if desde else "Sem pauta válida nesta edição."


def _frase_do_proximo(instituicao: str, ctx: _Contexto) -> Frase:
    """A frase do piso: nada da instituição cabe na janela pelo nome; traz o próximo evento com o assunto."""
    nome_inst = contrato.NOME_INSTITUICAO.get(instituicao, instituicao)
    lado_tribunais = instituicao in contrato.INSTITUICOES_DO_LADO[contrato.LADO_TRIBUNAIS]
    sem_pauta = any(l.sessao.instituicao == instituicao and l.faixa == "janela" and _sem_pauta_ainda(l) for l in ctx.linhas)
    if sem_pauta:  # há sessão marcada e a pauta não saiu: não se afirma que não há nada
        base = f"{nome_inst}: pauta das sessões até {dia_mes(ctx.fim)} ainda não publicada"
    else:
        base = f"{nome_inst}: {SEM_ALCANCE_GERAL if lado_tribunais else ctx.regras.situacao('dia_vazio')} até {dia_mes(ctx.fim)}"
    depois = [
        (linha, par)
        for linha in ctx.linhas
        if linha.sessao.instituicao == instituicao and linha.faixa == "radar" and linha.sessao.situacao not in SITUACOES_SEM_DECISAO
        for par in [_melhor_nomeado(linha, ctx)]
        if par is not None
    ]
    if not depois:
        return Frase(tipo="proximo", instituicao=nome_inst, texto=f"{base}.")
    linha, (nota, item) = min(depois, key=lambda x: (x[0].sessao.data, -x[1][0].total, x[0].sessao.hora or "99:99", x[0].id))
    tela = _item_tela(linha, nota, item, ctx, na_linha_de_prazo=linha.sessao.foro == "prazo")
    quando = f"em {dia_mes(linha.sessao.data)}"
    if linha.sessao.foro == "prazo":
        texto = f"{base}; {quando}, {_situacao_da_linha(linha, ctx)}: {tela.nome}"
    else:
        texto = f"{base}; {quando}, {_tela(linha.sessao.orgao)}, {tela.nome}"
    return Frase(tipo="proximo", instituicao=nome_inst, texto=_ponto(texto), itens=(tela,), link=linha.sessao.link, link_rotulo=ROTULO_DO_LINK)


def _fila_da_camara(ctx: _Contexto) -> tuple[_Linha | None, list[tuple[regua.Nota, contrato.ItemAgenda]]]:
    """O que ficou sem deliberação na última sessão do Plenário da Câmara e não está pautado de
    novo em nenhuma sessão da janela (o que está pautado já aparece na linha da sessão)."""
    agenda = ctx.agendas.get(contrato.LADO_CONGRESSO)
    estado = agenda.estado_de(contrato.CAMARA) if agenda else None
    if agenda is None or estado is None or not estado.fila:
        return None, []
    pautados = {
        item.identificador
        for linha in ctx.linhas
        if linha.sessao.instituicao == contrato.CAMARA and linha.faixa == "janela"
        for item in linha.sessao.itens
    }
    fonte = agenda.fonte("camara_eventos")
    sessao = contrato.SessaoAgenda(
        id="camara-fila", instituicao=contrato.CAMARA, orgao="Plenário", foro="plenario", tipo="deliberativa",
        data=estado.fila_de or agenda.hoje, situacao="encerrada", link=fonte.url if fonte else "",
        itens=tuple(item for item in estado.fila if item.identificador not in pautados),
    )
    notas = tuple(regua.notas_da_sessao(sessao, ctx.regras))
    if not notas:
        return None, []
    linha = _Linha(sessao=sessao, lado=contrato.LADO_CONGRESSO, notas=notas, nota=notas[0][0], faixa="fila")
    return linha, list(notas)


def _frase_da_camara(ctx: _Contexto) -> Frase | None:
    """"Pauta do Plenário ainda não publicada pela Câmara" (a Câmara só registra a sessão na
    véspera ou no dia) e a fila da última sessão, rotulada como fila, com os itens de maior nota
    pelo nome. Nunca diz que não haverá sessão."""
    agenda = ctx.agendas.get(contrato.LADO_CONGRESSO)
    estado = agenda.estado_de(contrato.CAMARA) if agenda else None
    if agenda is None or estado is None or not ctx.instituicao_valida(contrato.CAMARA):
        return None
    publicada_ate = estado.pauta_publicada_ate
    if publicada_ate and publicada_ate >= ctx.fim:
        return None
    nao_publicada = ctx.regras.situacao("pauta_camara_nao_publicada")
    texto = f"{nao_publicada[:1].upper()}{nao_publicada[1:]}"
    if publicada_ate and publicada_ate >= ctx.dia:
        texto += f" para depois de {dia_mes(publicada_ate)}"
    texto += "."
    linha, notas = _fila_da_camara(ctx)
    itens: tuple[ItemTela, ...] = ()
    if linha is not None:
        nomeados = [par for par in notas if ctx.nome(par[1])][: max(0, ctx.regras.tetos.home.fila_nomeados)]
        itens = tuple(_item_tela(linha, nota, item, ctx) for nota, item in nomeados)
        if itens:
            fila = ctx.regras.situacao("fila")
            quando = f", em {dia_mes(estado.fila_de)}" if estado.fila_de else ""
            texto += f" {fila[:1].upper()}{fila[1:]}{quando}: " + "; ".join(f"{i.nome} ({i.identificador})" for i in itens) + "."
    fonte = agenda.fonte("camara_eventos")
    return Frase(tipo="camara", instituicao="Câmara", texto=texto, itens=itens, link=fonte.url if fonte else "", link_rotulo=ROTULO_DO_LINK if fonte else "")


def _frases_de_recesso(ctx: _Contexto) -> list[Frase]:
    frases = []
    for agenda in ctx.agendas.values():
        for estado in agenda.estado:
            recesso = estado.recesso
            if recesso is None or recesso.ate < ctx.dia or recesso.de > ctx.fim:
                continue
            nome_inst = contrato.NOME_INSTITUICAO.get(estado.instituicao, estado.instituicao)
            frases.append(Frase(tipo="recesso", instituicao=nome_inst, texto=f"{nome_inst}: {_tela(recesso.nome)}, de {dia_mes(recesso.de)} a {dia_mes(recesso.ate)}."))
    return frases


# ── a home ───────────────────────────────────────────────────────────────────────────────────


def _escolher_para_a_home(ctx: _Contexto) -> tuple[list[tuple[_Linha, bool]], list[Frase], int]:
    """As linhas da home (cada uma com a marca de piso), as frases do piso e quantas linhas
    acima do corte não couberam no teto."""
    regras = ctx.regras
    na_janela = [l for l in ctx.linhas if l.faixa == "janela" and l.sessao.situacao not in SITUACOES_SEM_DECISAO]
    fortes = [l for l in na_janela if l.nota.total >= regras.cortes.home]
    escolhidas: dict[str, tuple[_Linha, bool]] = {}
    frases: list[Frase] = []
    for instituicao in ORDEM_DO_PISO:
        if not ctx.instituicao_valida(instituicao):
            continue
        da_casa = [l for l in fortes if l.sessao.instituicao == instituicao]
        if da_casa:
            melhor = min(da_casa, key=lambda l: l.ordem_por_nota)
            escolhidas[melhor.id] = (melhor, False)
            continue
        nomeaveis = [
            (linha, par)
            for linha in na_janela
            if linha.sessao.instituicao == instituicao
            for par in [_melhor_nomeado(linha, ctx)]
            if par is not None
        ]
        if nomeaveis:
            linha, _par = min(nomeaveis, key=lambda x: (-x[1][0].total, x[0].sessao.data, x[0].sessao.hora or "99:99", x[0].id))
            escolhidas[linha.id] = (linha, True)
        else:
            frases.append(_frase_do_proximo(instituicao, ctx))
    restantes = sorted((l for l in fortes if l.id not in escolhidas), key=lambda l: l.ordem_por_nota)
    vagas = max(0, regras.tetos.home.linhas - len(escolhidas))
    for linha in restantes[:vagas]:
        escolhidas[linha.id] = (linha, False)
    ordenadas = sorted(escolhidas.values(), key=lambda par: par[0].ordem_no_dia)
    return ordenadas, frases, max(0, len(restantes) - vagas)


def _decididos(ctx: _Contexto) -> list[LinhaTela]:
    """"Já decidido esta semana" na home: dos dias já passados da semana, os itens de nota igual
    ou acima do corte da home com resultado que decide (Congresso) e, dos tribunais (que não
    publicam resultado em dado aberto) e das sessões sem registro de encerramento, o melhor item
    da linha. Primeiro o que tem resultado; depois a maior nota e o dia mais recente."""
    corte = ctx.regras.cortes.home
    candidatos: list[tuple[tuple[int, int, str, str], _Linha, contrato.ItemAgenda]] = []
    for linha in ctx.linhas:
        if linha.faixa != "passado" or linha.sessao.situacao in SITUACOES_SEM_DECISAO or linha.sessao.foro == "prazo":
            continue
        fortes = [par for par in linha.notas if par[0].total >= corte]
        decididos = [
            par for par in fortes
            if par[1].resultado is not None and palavra_do_resultado(par[1].resultado) in RESULTADOS_QUE_DECIDEM and linha.sessao.situacao == "encerrada"
        ]
        for nota, item in decididos:
            candidatos.append(((0, -nota.total, _inverso(linha.sessao.data), item.identificador), linha, item))
        sem_resultado_oficial = linha.lado == contrato.LADO_TRIBUNAIS or linha.sessao.situacao != "encerrada" or bool(linha.sessao.como_pautado_em)
        if not decididos and sem_resultado_oficial and fortes:
            nota, item = next((par for par in fortes if ctx.nome(par[1])), fortes[0])
            candidatos.append(((1, -nota.total, _inverso(linha.sessao.data), item.identificador), linha, item))
    candidatos.sort(key=lambda c: c[0])
    return [_linha_tela(linha, ctx, so_o_item=item) for _ordem, linha, item in candidatos[: max(0, ctx.regras.tetos.home.resultados)]]


def _inverso(dia: str) -> str:
    """Chave que põe o dia mais recente primeiro numa ordenação crescente."""
    return "".join(chr(ord("9") - int(c)) if c.isdigit() else c for c in dia)


def _pe(ctx: _Contexto) -> Pe:
    """"Conferido em DD/MM. A pauta pode mudar." A data é a do dado mais novo entre as fontes de
    pauta que valem; a data por fonte só aparece quando alguma está velha."""
    principais = [f for f in ctx.fontes.values() if _principal(f, ctx.fontes.values())]
    validas = [f for f in principais if f.estado != "some" and f.fonte.data_dados]
    conferido_em = max((f.fonte.data_dados or "" for f in validas), default="") or max((f.fonte.data_dados or "" for f in principais), default="")
    if not conferido_em:
        return Pe(texto="A pauta pode mudar.")
    por_fonte: tuple[str, ...] = ()
    if any(f.estado != "ok" for f in principais):
        por_instituicao: dict[str, str] = {}
        for f in principais:
            if f.fonte.data_dados:
                por_instituicao[f.fonte.instituicao] = max(por_instituicao.get(f.fonte.instituicao, ""), f.fonte.data_dados)
        por_fonte = tuple(
            f"{contrato.NOME_INSTITUICAO[inst]}: dados de {dia_mes(por_instituicao[inst])}"
            for inst in contrato.INSTITUICOES
            if inst in por_instituicao
        )
    return Pe(texto=f"Conferido em {dia_mes(conferido_em)}. A pauta pode mudar.", conferido=dia_mes(conferido_em), conferido_em=conferido_em, fontes=por_fonte)


def _materias(ctx: _Contexto) -> tuple[MateriaNaAgenda, ...]:
    """O rótulo "na agenda: qua 25" de cada matéria da edição ligada a um item que está pautado
    na janela (o primeiro dia em que o item aparece, entre as linhas que a página completa mostra)."""
    registros: dict[str, MateriaNaAgenda] = {}
    corte = ctx.regras.cortes.pagina
    for linha in sorted((l for l in ctx.linhas if l.faixa == "janela" and l.sessao.situacao not in SITUACOES_SEM_DECISAO), key=lambda l: l.ordem_no_dia):
        for nota, item in linha.notas:
            ligacao = ctx.ligacoes.get(item.identificador)
            if nota.total < corte or not ligacao:
                continue
            for materia in ligacao.materias:
                registros.setdefault(
                    materia,
                    MateriaNaAgenda(materia=materia, rotulo=f"na agenda: {rotulo_do_dia(linha.sessao.data)}", data=linha.sessao.data, identificador=item.identificador, linha=linha.id),
                )
    return tuple(registros[m] for m in sorted(registros))


# ── a página completa ────────────────────────────────────────────────────────────────────────


def _limites(ctx: _Contexto) -> tuple[str, ...]:
    """O que cada fonte permite dizer (e o que a agenda não afirma), só dos lados presentes."""
    comissoes = {inst: " e ".join(siglas) for inst, siglas in ctx.regras.comissoes_que_decidem}
    limites: list[str] = []
    if contrato.LADO_CONGRESSO in ctx.agendas:
        limites += [
            f"Câmara: entram o Plenário e as comissões que decidem ({comissoes.get(contrato.CAMARA, '')}). A Câmara só registra a sessão do Plenário na véspera ou no dia.",
            f"Senado: entram o Plenário e as comissões que decidem ({comissoes.get(contrato.SENADO, '')}). Itens terminativos de outras comissões ficam só na contagem.",
            f"Congresso: entram as sessões conjuntas, a comissão que decide ({comissoes.get(contrato.CONGRESSO, '')}) e os prazos de medidas provisórias.",
        ]
    if contrato.LADO_TRIBUNAIS in ctx.agendas:
        limites += [
            "STF: só o Plenário presencial. Do Plenário Virtual e das Turmas a fonte só informa a contagem de processos.",
            "STJ: entram a Corte Especial e as Seções. A fonte não informa a hora nem o assunto dos julgamentos; recursos internos em lista e processos em segredo de justiça ficam só na contagem.",
            "Tribunais não publicam o resultado em dado aberto: a linha de um dia já passado aponta o andamento oficial.",
        ]
    limites.append("A nota mede sinais institucionais (onde se decide, quanta gente a decisão alcança, em que ponto está). A cobertura da imprensa não pontua.")
    return tuple(limites)


def _fontes_de_tela(ctx: _Contexto) -> tuple[FonteTela, ...]:
    saida = []
    for chave in sorted(ctx.fontes):
        fonte = ctx.fontes[chave]
        f, data = fonte.fonte, dia_mes(fonte.fonte.data_dados)
        if not data:
            texto = "fonte ainda não lida"
        elif fonte.estado == "some":
            texto = f"sem dado novo desde {data}" + ("; os itens desta fonte saíram da agenda" if _principal(fonte, ctx.fontes.values()) else "")
        elif fonte.estado == "aviso":
            texto = f"dados de {data}, pode ter mudado"
        else:
            texto = f"dados de {data}"
        if not f.ok and data:
            texto += "; a última leitura falhou"
        saida.append(FonteTela(chave=chave, instituicao=contrato.NOME_INSTITUICAO.get(f.instituicao, f.instituicao), nome=_tela(f.nome), url=f.url, data=data, estado=fonte.estado, texto=texto))
    return tuple(saida)


def _contagens(ctx: _Contexto) -> tuple[Contagem, ...]:
    """Os números das fontes, por instituição (só para a página completa)."""
    saida: list[Contagem] = []
    for agenda in ctx.agendas.values():
        for estado in agenda.estado:
            if not ctx.instituicao_valida(estado.instituicao):
                continue
            nome_inst = contrato.NOME_INSTITUICAO.get(estado.instituicao, estado.instituicao)
            for nome, n in estado.contagens:
                rotulo = ROTULO_DA_CONTAGEM.get(nome, nome.replace("_", " "))
                saida.append(Contagem(rotulo=f"{nome_inst}, {rotulo}", n=n, texto=f"{nome_inst}, {rotulo}: {_milhar(n)}"))
            for virtual in estado.virtuais:
                total = virtual.plenario + virtual.turma1 + virtual.turma2
                saida.append(Contagem(
                    rotulo=f"{nome_inst}: julgamento virtual de {dia_mes(virtual.de)} a {dia_mes(virtual.ate)}",
                    n=total,
                    texto=(
                        f"{nome_inst}, julgamento virtual de {dia_mes(virtual.de)} a {dia_mes(virtual.ate)}: {_milhar(virtual.plenario)} no Plenário, "
                        f"{_milhar(virtual.turma1)} na Primeira Turma e {_milhar(virtual.turma2)} na Segunda Turma"
                    ),
                ))
            if estado.instituicao == contrato.CONGRESSO and estado.sessao_conjunta_convocada is False:
                saida.append(Contagem(rotulo="Congresso: sessão conjunta convocada", n=0, texto="Congresso: nenhuma sessão conjunta convocada"))
    return tuple(saida)


def _pagina(ctx: _Contexto, frases: list[Frase]) -> Pagina:
    regras = ctx.regras
    corte = regras.cortes.pagina
    visiveis = [l for l in ctx.linhas if l.nota.total >= corte or _sem_pauta_ainda(l)]
    na_janela = sorted((l for l in visiveis if l.faixa == "janela"), key=lambda l: l.ordem_por_nota)
    teto = max(1, regras.tetos.pagina.linhas)
    dentro = sorted(na_janela[:teto], key=lambda l: l.ordem_no_dia)
    passadas = sorted((l for l in visiveis if l.faixa == "passado" and l.notas), key=lambda l: l.ordem_no_dia)
    radar = sorted((l for l in visiveis if l.faixa == "radar"), key=lambda l: l.ordem_no_dia)
    fora: dict[str, int] = {}
    abaixo = 0
    for linha in ctx.linhas:
        if linha.faixa == "fora":
            continue
        abaixo += sum(1 for nota, _item in linha.notas if nota.total < corte)
        for motivo, n in linha.sessao.fora:
            fora[motivo] = fora.get(motivo, 0) + n
    fila_linha, fila_notas = _fila_da_camara(ctx)
    fila_itens: tuple[ItemTela, ...] = ()
    fila_texto = ""
    if fila_linha is not None and ctx.instituicao_valida(contrato.CAMARA):
        fila_itens = tuple(_item_tela(fila_linha, nota, item, ctx) for nota, item in fila_notas if nota.total >= corte)
        agenda = ctx.agendas[contrato.LADO_CONGRESSO]
        estado = agenda.estado_de(contrato.CAMARA)
        fila = regras.situacao("fila")
        fila_texto = f"{fila[:1].upper()}{fila[1:]} da Câmara" + (f", em {dia_mes(estado.fila_de)}" if estado and estado.fila_de else "") if fila_itens else ""
    sem_pauta = _sem_pauta(ctx)
    return Pagina(
        dias=_dias((_linha_tela(l, ctx, pagina=True) for l in dentro), ctx.janela, ctx, com_vazios=not sem_pauta),
        decidido=_dias((_linha_tela(l, ctx, pagina=True) for l in passadas), (), ctx, passado=True),
        radar=_dias((_linha_tela(l, ctx, pagina=True) for l in radar), (), ctx),
        fila=fila_itens,
        fila_texto=fila_texto,
        frases=tuple(frases),
        fontes=_fontes_de_tela(ctx),
        limites=_limites(ctx),
        fora=tuple(
            Contagem(rotulo=contrato.TERMO_FORA.get(m, m), n=fora[m], texto=f"{contrato.TERMO_FORA.get(m, m)}: {_milhar(fora[m])}")
            for m in contrato.MOTIVOS_FORA
            if fora.get(m)
        ),
        contagens=_contagens(ctx),
        abaixo_do_corte=abaixo,
        fora_do_teto=max(0, len(na_janela) - teto),
        corte=corte,
        corte_home=regras.cortes.home,
        sem_pauta=sem_pauta,
    )


# ── montagem ─────────────────────────────────────────────────────────────────────────────────


def montar(
    congresso: contrato.Agenda | None,
    tribunais: contrato.Agenda | None,
    dia: str,
    regras: Regras | None = None,
    *,
    ligacoes: Mapping[str, ligar.Ligacao] | None = None,
    velho_dias: Mapping[str, int] | None = None,
) -> Retrato:
    """O retrato do dia ``dia`` (a data da edição) a partir de um ou dos dois arquivos de coleta.

    ``ligacoes`` é o resultado de :func:`qijournal.agenda.ligar.ligar` (sem ele nenhum item traz
    marca de mídia). ``velho_dias`` é o limite de cada fonte em ``data/tribunais/meta.json``
    (``{chave: dias}``), usado quando o arquivo de coleta não traz o seu."""
    regras = regras or regras_mod.PADRAO
    dia = dia[:10]
    janela = contrato.janela_da_edicao(dia)
    ctx = _Contexto(dia=dia, regras=regras, ligacoes=ligacoes or {}, janela=janela, fim=janela[-1], semana=contrato.segunda_da_semana(dia))
    limites = dict(velho_dias or {})
    for agenda in (congresso, tribunais):
        if agenda is None:
            continue
        ctx.agendas[agenda.lado] = agenda
        for fonte in agenda.fontes:
            limite = fonte.velho_dias or limites.get(fonte.chave)
            ctx.fontes[fonte.chave] = _Fonte(fonte, regras.frescor_de(fonte.data_dados, dia, limite if isinstance(limite, int) and not isinstance(limite, bool) else None))
    for agenda in ctx.agendas.values():
        vistas: set[str] = set()
        proximos = [e.proximo for e in agenda.estado if e.proximo is not None]
        for sessao in [*agenda.sessoes, *proximos]:
            if sessao.id in vistas or ctx.estado_da_fonte(sessao, agenda) == "some":
                continue
            vistas.add(sessao.id)
            notas = tuple(regua.notas_da_sessao(sessao, regras))
            nota = notas[0][0] if notas else regua.nota_sem_itens(sessao, regras)
            ctx.linhas.append(_Linha(sessao=sessao, lado=agenda.lado, notas=notas, nota=nota, faixa=ctx.faixa(sessao.data)))

    # Frases: primeiro o que falta (lado ausente, fonte velha), depois o piso, a Câmara e os recessos.
    frases: list[Frase] = []
    for lado in contrato.LADOS:
        if lado not in ctx.agendas:
            casas = [contrato.NOME_INSTITUICAO[i] for i in ORDEM_DO_PISO if i in contrato.INSTITUICOES_DO_LADO[lado]]
            quem = ", ".join(casas[:-1]) + " e " + casas[-1] if len(casas) > 1 else casas[0]
            frases.append(Frase(tipo="fonte", instituicao=quem, texto=f"{quem}: pauta indisponível nesta edição."))
            continue
        for instituicao in (i for i in ORDEM_DO_PISO if i in contrato.INSTITUICOES_DO_LADO[lado]):
            if not ctx.agendas[lado].fontes_de(instituicao) and not ctx.instituicao_valida(instituicao):
                nome_inst = contrato.NOME_INSTITUICAO[instituicao]
                frases.append(Frase(tipo="fonte", instituicao=nome_inst, texto=f"{nome_inst}: pauta indisponível nesta edição."))
    frases += _frases_de_fonte_velha(ctx)
    sem_pauta = _sem_pauta(ctx)
    if sem_pauta:  # nada vale mais: uma frase só, e nenhum dia (não se afirma "nada pautado" sem dado)
        frases = [Frase(tipo="fonte", texto=sem_pauta)]
    escolhidas, frases_do_piso, fora_do_teto = _escolher_para_a_home(ctx)
    frases += frases_do_piso
    camara = _frase_da_camara(ctx)
    if camara is not None:
        # A frase da Câmara já diz que a pauta do Plenário não saiu: o piso sem próximo evento não repete.
        frases = [f for f in frases if not (f.tipo == "proximo" and f.instituicao == "Câmara" and not f.itens)]
        frases.append(camara)
    frases += _frases_de_recesso(ctx)

    # Home: as linhas escolhidas, por dia; no celular, hoje e amanhã abrem com poucas linhas.
    linhas_de_tela = [_linha_tela(linha, ctx, piso=piso) for linha, piso in escolhidas]
    dias = list(_dias(linhas_de_tela, janela, ctx, com_vazios=not sem_pauta))
    abertas = max(0, regras.tetos.home.celular_linhas_hoje_amanha)
    for i, dia_tela in enumerate(dias):
        if not dia_tela.principal:
            continue
        novas = []
        for linha in dia_tela.linhas:
            novas.append(replace(linha, aberta_no_celular=abertas > 0))
            abertas -= 1
        dias[i] = replace(dia_tela, linhas=tuple(novas))

    return Retrato(
        dia=dia,
        janela=janela,
        lados=tuple(lado for lado in contrato.LADOS if lado in ctx.agendas),
        dias=tuple(dias),
        frases=tuple(frases),
        decidido=tuple(_decididos(ctx)),
        pe=_pe(ctx),
        fora_do_teto=fora_do_teto,
        materias=_materias(ctx),
        pagina=_pagina(ctx, frases),
    )


# ── a borda: leitura dos arquivos ────────────────────────────────────────────────────────────


def ler_velho_dias(caminho: Path | str) -> dict[str, int]:
    """O limite de cada fonte em ``data/tribunais/meta.json`` (``{chave: velho_dias}``). Arquivo
    ausente, ilegível ou fora do formato devolve vazio."""
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    fontes = dados.get("fontes") if isinstance(dados, dict) else None
    if not isinstance(fontes, dict):
        return {}
    return {
        str(chave): f["velho_dias"]
        for chave, f in fontes.items()
        if isinstance(f, dict) and isinstance(f.get("velho_dias"), int) and not isinstance(f.get("velho_dias"), bool) and f["velho_dias"] > 0
    }


def textos_das_edicoes_anteriores(pasta_busca: Path | str, dia: str, quantas: int = ligar.EDICOES_ANTERIORES) -> list[ligar.Texto]:
    """Os textos dos arquivos de ``data/busca`` das últimas ``quantas`` edições anteriores ao
    dia (a edição do dia entra pelo próprio objeto da edição). Arquivo ilegível é pulado."""
    try:
        arquivos = sorted(a for a in Path(pasta_busca).glob("????-??-??.json") if a.stem < dia[:10])[-quantas:] if quantas > 0 else []
    except OSError:
        return []
    textos: list[ligar.Texto] = []
    for arquivo in arquivos:
        try:
            textos += ligar.textos_da_busca(json.loads(arquivo.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            log.warning("Agenda: %s ilegível (%s); edição fora da ligação com a mídia", arquivo.name, exc)
    return textos


def do_dia(edition: Any, data_dir: Path | str, config: Any = None, *, regras: Regras | None = None) -> Retrato | None:
    """O retrato da edição, lendo só arquivos: ``<data>/congresso/agenda.json``,
    ``<data>/tribunais/agenda.json``, ``<data>/tribunais/meta.json`` e ``<data>/busca``. Devolve
    ``None`` quando nenhum dos dois arquivos de coleta existe ou vale (a edição sai sem a agenda).
    A ligação com a mídia é adicional: se falhar, o retrato sai sem as marcas."""
    data_dir = Path(data_dir)
    congresso = contrato.ler_lado(data_dir, contrato.LADO_CONGRESSO)
    tribunais = contrato.ler_lado(data_dir, contrato.LADO_TRIBUNAIS)
    if congresso is None and tribunais is None:
        return None
    if regras is None:
        regras = regras_mod.carregar(getattr(config, "root", None))
    dia = str(edition.date)
    ligacoes: dict[str, ligar.Ligacao] = {}
    try:
        textos = ligar.textos_da_edicao(edition) + textos_das_edicoes_anteriores(data_dir / "busca", dia)
        oficiais = ligar.veiculos_oficiais_de(getattr(config, "sources", None) or (), regras)
        ligacoes = ligar.ligar(ligar.alvos_da_agenda(congresso, tribunais), textos, regras, dia, veiculos_oficiais=oficiais)
    except Exception as exc:  # noqa: BLE001 — a ligação é adicional: nunca derruba a agenda
        log.warning("Agenda: ligação com a mídia indisponível (%s); itens sem a marca", exc)
    return montar(congresso, tribunais, dia, regras, ligacoes=ligacoes, velho_dias=ler_velho_dias(data_dir / contrato.LADO_TRIBUNAIS / "meta.json"))
