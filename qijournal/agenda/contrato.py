"""O contrato da Agenda da semana: o formato ÚNICO dos dois arquivos de coleta.

``data/congresso/agenda.json`` (escrito pela edição diária) e ``data/tribunais/agenda.json``
(escrito pelo fluxo Tribunais) têm exatamente o mesmo formato, com o campo ``versao``. Quem
escreve valida a saída aqui (:func:`para_json` recusa dado inválido); quem lê usa :func:`ler`,
que nunca levanta exceção: arquivo ausente, ilegível, de formato inesperado ou de versão
desconhecida vale como fonte ausente (``None``).

O arquivo (chaves sempre nesta ordem; mesmos dados, mesmos bytes)::

    {"versao": 1, "lado": "congresso" | "tribunais",
     "gerado_em": "AAAA-MM-DDTHH:MM:SSZ", "hoje": "AAAA-MM-DD",
     "janela": {"de": <segunda-feira da semana de hoje>, "ate": <13 dias depois>},
     "fontes": {<chave>: {instituicao, nome, url, ok, data_dados, coletado_em, aviso, velho_dias}},
     "sessoes": [{id, instituicao, orgao, orgao_sigla, foro, tipo, data, hora, situacao, modalidade,
                  pauta, pauta_confirmada, como_pautado_em, link, itens: [...], fora: {motivo: n}}],
     "estado": {<instituicao>: {recesso, pauta_publicada_ate, fila: [...], fila_de,
                                sessao_conjunta_convocada, proximo, virtuais: [...], contagens}}}

e cada item (de uma sessão, da fila ou do próximo evento)::

    {identificador, tipo, alcance, apelido, ementa, tema: {numero, titulo, tipo}, area, estagio,
     estagio_detalhe, outros_estagios, resultado: {palavra, texto}, relator: {nome, partido, uf},
     ordem, link, partes: [{polo, tipo, nome}], ficha, relevancia, segredo}

Regras que o contrato trava (:func:`validar`):

- só campos oficiais e sinais para a régua: chave desconhecida é erro, então nenhum campo de texto
  escrito por IA entra no arquivo;
- todo item e toda sessão têm link oficial (``https`` em ``.leg.br``, ``.jus.br`` ou ``.gov.br``);
- processo em segredo de justiça nunca é gravado com dados: ``segredo`` é sempre ``false`` no
  arquivo (:class:`SessaoAgenda` tira o item marcado e o soma em ``fora["segredo"]``);
- parte só ente público ou pessoa jurídica (o coletor confere o catálogo de litigantes); pessoa
  física e advogado nunca;
- o STJ não informa hora nem modalidade, e a agenda não afirma nenhuma das duas; os tribunais não
  têm dado de resultado (``resultado`` é sempre nulo no lado ``tribunais``);
- ``resultado.texto`` é só a frase oficial do resultado, sem placar (nenhum placar nesta entrega).

Datas ``AAAA-MM-DD`` e horas ``HH:MM`` são de Brasília, como as fontes informam; instantes
(``gerado_em``, ``coletado_em``) são UTC. Dia útil aqui é segunda a sexta: a agenda não conhece
feriado, a fonte é que diz se há sessão.

Quem coleta (um dos dois lados)::

    novo = Agenda(lado=LADO_CONGRESSO, gerado_em=agora_utc, hoje=hoje, fontes=..., sessoes=..., estado=...)
    anterior = ler(caminho, lado=LADO_CONGRESSO)             # None na primeira vez
    junto = mesclar_com_anterior(novo, anterior, hoje)       # fonte que falhou não vira zero
    if not conteudo_igual(junto, anterior):
        gravar(caminho, para_json(junto))                    # ErroDeContrato: fica o arquivo anterior

Quem lê (a edição)::

    agenda = ler(caminho, lado=LADO_TRIBUNAIS)               # None = fonte ausente; nunca levanta
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

VERSAO = 1  # formato dos dois agenda.json: outro número vale como fonte ausente para quem lê

# ── vocabulário fechado ──────────────────────────────────────────────────────────────────────

# Instituições, na ordem de desempate dentro do dia (a ordem de exibição é por dia e hora).
CAMARA, SENADO, CONGRESSO, STF, STJ = "camara", "senado", "congresso", "stf", "stj"
INSTITUICOES = (CAMARA, SENADO, CONGRESSO, STF, STJ)

# Os dois arquivos: cada lado só traz as suas instituições (um escritor por pasta).
LADO_CONGRESSO, LADO_TRIBUNAIS = "congresso", "tribunais"
LADOS = (LADO_CONGRESSO, LADO_TRIBUNAIS)
INSTITUICOES_DO_LADO = {LADO_CONGRESSO: (CAMARA, SENADO, CONGRESSO), LADO_TRIBUNAIS: (STF, STJ)}
ARQUIVO = "agenda.json"  # data/congresso/agenda.json e data/tribunais/agenda.json (a pasta tem o nome do lado)

# Foro: onde se decide (a primeira parte da régua, até 30 pontos em config/agenda.yaml).
#   plenario          Plenário da Câmara, do Senado, do Congresso (sessão conjunta) ou do STF
#   corte_especial    Corte Especial do STJ
#   secao             Seção do STJ
#   comissao_decide   comissão que decide (config/agenda.yaml → comissoes_que_decidem)
#   prazo             prazo constitucional que vence (medida provisória, veto)
#   turma             Turma do STF ou do STJ
#   comissao          outra comissão
FOROS = ("plenario", "corte_especial", "secao", "comissao_decide", "prazo", "turma", "comissao")
FOROS_DA_INSTITUICAO = {
    CAMARA: ("plenario", "comissao_decide", "comissao"),
    SENADO: ("plenario", "comissao_decide", "comissao"),
    CONGRESSO: ("plenario", "comissao_decide", "comissao", "prazo"),
    STF: ("plenario", "turma"),
    STJ: ("corte_especial", "secao", "turma"),
}

# Tipo de sessão. "prazo" não é sessão: é a linha, sem hora, do prazo constitucional que vence.
TIPOS_SESSAO = ("deliberativa", "conjunta", "julgamento", "reuniao_deliberativa", "prazo")

# Situação da sessão, como a fonte informa. Sessão que não está "encerrada" não tem resultado.
SITUACOES_SESSAO = ("agendada", "em_andamento", "encerrada", "cancelada", "adiada")

# Modalidade: só quando a fonte diz (o STJ não diz; fica nulo).
MODALIDADES = ("presencial", "semipresencial", "virtual")

# A pauta da sessão: publicada (com itens ou contagens), ainda não publicada pela fonte, ou não
# coletada nesta rodada (o prazo de rede acabou antes): a agenda não afirma o que não leu.
ESTADOS_PAUTA = ("publicada", "nao_publicada", "nao_coletada")

# Alcance do item (a segunda parte da régua, até 30 pontos): tudo o que a régua precisa.
#   repercussao_geral        recurso com tema de repercussão geral (STF)
#   repetitivo               recurso repetitivo com tema afetado (STJ)
#   controle_federal         controle concentrado de norma federal (ADI, ADC, ADO, ADPF)
#   pec, plp, mpv, veto      proposta de emenda, lei complementar, medida provisória, veto
#   orcamento                leis orçamentárias (PLN: orçamento, diretrizes, plano plurianual, créditos)
#   indicacao                indicação de autoridade (sabatina e votação)
#   pl                       projeto de lei
#   controle_estadual        ação contra norma estadual ou municipal
#   acao_originaria          ação originária (ACO, AO, ação penal originária...)
#   embargos_de_divergencia  embargos de divergência
#   afetacao                 proposta de afetação ou de relevância
#   pdl                      decreto legislativo
#   individual               caso individual, sem tema
#   outro                    tipo sem linha na régua (projeto de resolução, requerimento de mérito)
ALCANCES = (
    "repercussao_geral", "repetitivo", "controle_federal", "pec", "plp", "mpv", "veto", "orcamento", "indicacao",
    "pl", "controle_estadual", "acao_originaria", "embargos_de_divergencia", "afetacao", "pdl", "individual", "outro",
)

# Estágio do item (a terceira parte da régua, até 25 pontos), do mais específico ao mais geral.
# É a ordem em que :func:`estagio_principal` escolhe o estágio que a linha mostra.
#   em_votacao             a sessão está em andamento e a deliberação do item começou
#   prazo_vence            prazo constitucional que vence na janela (medida provisória, veto)
#   retorno_de_vista       volta ao julgamento depois de pedido de vista (detalhe: o ministro)
#   referendo_de_cautelar  o colegiado confirma ou derruba a decisão provisória do relator
#   destaque               saiu do julgamento virtual a pedido de um ministro (detalhe: o ministro)
#   urgencia               regime de urgência (detalhe: o regime, como a fonte escreve)
#   pauta_confirmada       a fonte diz que a pauta está confirmada (campo oficial do Senado)
#   pautado                consta da pauta, sem confirmação
#   pauta_nao_publicada    a sessão existe e a pauta ainda não saiu (a régua usa para a linha sem itens)
ESTAGIOS = (
    "em_votacao", "prazo_vence", "retorno_de_vista", "referendo_de_cautelar", "destaque", "urgencia",
    "pauta_confirmada", "pautado", "pauta_nao_publicada",
)

# Palavra do resultado (dias passados). Só a palavra e a frase oficial; nenhum placar. Sessão que
# não está encerrada não tem resultado: antes do fim só cabem as palavras de RESULTADOS_ANTES_DO_FIM.
PALAVRAS_RESULTADO = (
    "aprovado", "aprovado_ressalvados_destaques", "rejeitado", "retirado_de_pauta", "adiado", "prejudicado",
    "nao_apreciado", "pedido_de_vista",
)
RESULTADOS_ANTES_DO_FIM = ("retirado_de_pauta", "adiado")

TIPOS_TEMA = ("repercussao_geral", "repetitivo", "iac", "controversia", "relevancia", "outro")
POLOS = ("ativo", "passivo", "outro")
TIPOS_PARTE = ("ente", "pj")  # ente público ou pessoa jurídica do catálogo; pessoa física nunca

# O que ficou fora da seleção, só em contagem. Cada item fora conta UMA vez, pelo primeiro motivo
# que couber, nesta ordem (um agravo em segredo conta em "segredo").
MOTIVOS_FORA = (
    "segredo", "recurso_interno", "vice_presidencia", "sem_tema", "requerimento", "redacao_final", "outorga",
    "simbolica", "outros",
)

# Chaves usuais de ``estado[...].contagens`` (livres: qualquer nome simples vale).
CONTAGENS_USUAIS = (
    "vetos_tramitando", "vetos_trancando", "sessoes_solenes", "sessoes_nao_deliberativas", "audiencias",
    "eventos_cancelados", "sessoes", "itens",
)

DOMINIOS_OFICIAIS = ("leg.br", "jus.br", "gov.br")
DIAS_DA_COLETA = 14  # da segunda-feira da semana corrente até 13 dias depois
DIAS_DA_EDICAO = 5  # o dia da edição e os 4 dias úteis seguintes

# Como cada palavra do vocabulário se escreve por extenso (o termo oficial, usado na página
# completa; a home usa o dicionário de situações de config/agenda.yaml quando houver entrada).
NOME_INSTITUICAO = {CAMARA: "Câmara", SENADO: "Senado", CONGRESSO: "Congresso", STF: "STF", STJ: "STJ"}
TERMO_FORO = {
    "plenario": "Plenário", "corte_especial": "Corte Especial", "secao": "Seção", "comissao_decide": "comissão que decide",
    "prazo": "prazo constitucional", "turma": "Turma", "comissao": "comissão",
}
TERMO_TIPO_SESSAO = {
    "deliberativa": "sessão deliberativa", "conjunta": "sessão conjunta", "julgamento": "sessão de julgamento",
    "reuniao_deliberativa": "reunião deliberativa", "prazo": "prazo constitucional",
}
TERMO_SITUACAO = {
    "agendada": "agendada", "em_andamento": "em andamento", "encerrada": "encerrada", "cancelada": "cancelada",
    "adiada": "adiada",
}
TERMO_MODALIDADE = {"presencial": "presencial", "semipresencial": "semipresencial", "virtual": "virtual"}
TERMO_PAUTA = {
    "publicada": "pauta publicada", "nao_publicada": "pauta ainda não publicada", "nao_coletada": "pauta não lida nesta coleta",
}
TERMO_ALCANCE = {
    "repercussao_geral": "repercussão geral", "repetitivo": "recurso repetitivo",
    "controle_federal": "controle concentrado de norma federal", "pec": "proposta de emenda à Constituição",
    "plp": "projeto de lei complementar", "mpv": "medida provisória", "veto": "veto", "orcamento": "orçamento",
    "indicacao": "indicação de autoridade", "pl": "projeto de lei", "controle_estadual": "ação contra norma estadual",
    "acao_originaria": "ação originária", "embargos_de_divergencia": "embargos de divergência",
    "afetacao": "proposta de afetação ou de relevância", "pdl": "decreto legislativo", "individual": "caso individual",
    "outro": "outro tipo",
}
TERMO_ESTAGIO = {
    "em_votacao": "em votação", "prazo_vence": "prazo que vence", "retorno_de_vista": "retorno de vista",
    "referendo_de_cautelar": "referendo de medida cautelar", "destaque": "destaque", "urgencia": "urgência",
    "pauta_confirmada": "pauta confirmada", "pautado": "pautado", "pauta_nao_publicada": "pauta ainda não publicada",
}
TERMO_RESULTADO = {
    "aprovado": "aprovado", "aprovado_ressalvados_destaques": "aprovado, ressalvados os destaques", "rejeitado": "rejeitado",
    "retirado_de_pauta": "retirado de pauta", "adiado": "adiado", "prejudicado": "prejudicado",
    "nao_apreciado": "não apreciado", "pedido_de_vista": "pedido de vista",
}
TERMO_TEMA = {
    "repercussao_geral": "repercussão geral", "repetitivo": "recurso repetitivo", "iac": "incidente de assunção de competência",
    "controversia": "controvérsia", "relevancia": "relevância", "outro": "tema",
}
TERMO_FORA = {
    "segredo": "em segredo de justiça", "recurso_interno": "recursos internos em lista",
    "vice_presidencia": "agravos contra decisões da Vice-Presidência", "sem_tema": "casos sem tema",
    "requerimento": "requerimentos de rotina", "redacao_final": "redações finais", "outorga": "outorgas de rádio e TV",
    "simbolica": "matérias simbólicas", "outros": "outros",
}


def termo(palavra: str) -> str:
    """O termo oficial de uma palavra de estágio, de resultado ou de situação de sessão (as três
    que a linha mostra); palavra desconhecida volta sem os sublinhados."""
    for tabela in (TERMO_ESTAGIO, TERMO_RESULTADO, TERMO_SITUACAO):
        if palavra in tabela:
            return tabela[palavra]
    return str(palavra).replace("_", " ")


def estagio_principal(candidatos: Iterable[str]) -> str:
    """Entre os estágios que valem para um item, o que a linha mostra: o primeiro na ordem de
    :data:`ESTAGIOS` (os demais vão em ``outros_estagios``). Sem candidato conhecido, ``pautado``."""
    validos = set(candidatos) & set(ESTAGIOS)
    return next((e for e in ESTAGIOS if e in validos), "pautado")


# ── texto, datas e links ─────────────────────────────────────────────────────────────────────

# Espaços especiais (o não separável da API da Câmara entre eles) e caracteres de controle
# (tabulação, quebra de linha) viram espaço simples; os invisíveis somem.
_ESPECIAIS = "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006" \
             "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
_ESPACOS = dict.fromkeys([*range(32), 127, *map(ord, _ESPECIAIS)], " ")
_INVISIVEIS = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))
_NUMERO_QUEBRADO = re.compile(r"(?i)(n[º°]s?)\?(?=\d)")  # "nº?200": o espaço não separável chegou como "?"
_PLACAR = re.compile(r"(?i)\b(?:sim|n[ãa]o|absten[çc][ãa]o|total)\s*:\s*\d")
_RE_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_HORA = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_RE_INSTANTE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_RE_CHAVE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_RE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_RE_TIPO = re.compile(r"^[A-Za-z]{1,16}$")  # a sigla ou a classe: "PLP", "REsp", "CauInomCrim"
_RE_IDENTIFICADOR = re.compile(r"^[A-Za-z]{1,16} \d[\d./-]{0,20}$")
_RE_FICHA = re.compile(r"^(?:cd|sf)-[A-Za-z0-9-]{1,60}$")
_RE_UF = re.compile(r"^[A-Z]{2}$")


def limpar_texto(texto: Any) -> str | None:
    """O texto oficial pronto para gravar: espaços especiais e quebras de linha viram espaço
    simples, caracteres invisíveis somem, ``nº?200`` (o espaço não separável que a API da Câmara
    devolve como ``?``) vira ``nº 200``, e as pontas são aparadas. Vazio vira ``None``."""
    if texto is None:
        return None
    s = str(texto).translate(_INVISIVEIS).translate(_ESPACOS)
    s = _NUMERO_QUEBRADO.sub(r"\1 ", s)
    s = re.sub(r" {2,}", " ", s).strip()
    return s or None


def identificador_de(tipo: str, numero: Any, ano: Any = None) -> str:
    """O identificador que a tela mostra e que liga o item à mídia, sempre no mesmo formato:
    ``identificador_de("PL", "06461", 2019)`` → ``PL 6461/2019``; ``identificador_de("RE", 1326541)``
    → ``RE 1326541`` (zeros à esquerda do número saem)."""
    numero = str(numero).strip().lstrip("0") or "0"
    return f"{str(tipo).strip()} {numero}/{str(ano).strip()}" if ano not in (None, "") else f"{str(tipo).strip()} {numero}"


def id_de_sessao(instituicao: str, *partes: Any) -> str:
    """O id estável de uma sessão, em minúsculas e com hífens: ``id_de_sessao("camara", 83419)`` →
    ``camara-83419``; ``id_de_sessao("stj", "CE", "2026-11-25")`` → ``stj-ce-2026-11-25``;
    ``id_de_sessao("congresso", "prazo", "MPV 1381/2026")`` → ``congresso-prazo-mpv-1381-2026``."""
    junto = "-".join(str(p) for p in (instituicao, *partes))
    return re.sub(r"[^a-z0-9]+", "-", junto.lower()).strip("-")


def link_oficial(url: Any) -> bool:
    """``https`` num domínio oficial (``.leg.br``, ``.jus.br`` ou ``.gov.br``)."""
    if not isinstance(url, str) or not url or len(url) > 500 or any(c.isspace() for c in url):
        return False
    try:
        partes = urlsplit(url)
        host = (partes.hostname or "").lower()
    except ValueError:
        return False
    return partes.scheme == "https" and any(host == d or host.endswith("." + d) for d in DOMINIOS_OFICIAIS)


def _data(valor: Any) -> date | None:
    if not isinstance(valor, str) or not _RE_DATA.match(valor):
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _instante(valor: Any) -> bool:
    if not isinstance(valor, str) or not _RE_INSTANTE.match(valor):
        return False
    try:
        datetime.strptime(valor, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


# ── a janela ─────────────────────────────────────────────────────────────────────────────────


def eh_dia_util(dia: str) -> bool:
    """Segunda a sexta (sem calendário de feriados)."""
    return date.fromisoformat(dia[:10]).weekday() < 5


def segunda_da_semana(dia: str) -> str:
    """A segunda-feira da semana corrente (segunda a domingo) em que o dia cai."""
    d = date.fromisoformat(dia[:10])
    return (d - timedelta(days=d.weekday())).isoformat()


def janela_de_coleta(dia: str) -> tuple[str, str]:
    """O que os coletores pedem e o arquivo guarda: da segunda-feira da semana corrente até 13
    dias depois (os dias já passados da semana entram, para o "Já decidido esta semana")."""
    inicio = date.fromisoformat(segunda_da_semana(dia))
    return inicio.isoformat(), (inicio + timedelta(days=DIAS_DA_COLETA - 1)).isoformat()


def janela_da_edicao(dia: str) -> tuple[str, ...]:
    """Os dias que a agenda mostra: o dia da edição e os 4 dias úteis seguintes. Edição de sábado
    ou de domingo começa na segunda-feira seguinte. Cabe sempre na :func:`janela_de_coleta`."""
    d = date.fromisoformat(dia[:10])
    while d.weekday() >= 5:
        d += timedelta(days=1)
    dias = [d]
    while len(dias) < DIAS_DA_EDICAO:
        d += timedelta(days=1)
        if d.weekday() < 5:
            dias.append(d)
    return tuple(x.isoformat() for x in dias)


def dias_passados_da_semana(dia: str) -> tuple[str, ...]:
    """Os dias úteis da semana corrente anteriores ao dia (o "Já decidido esta semana"). No sábado
    e no domingo são os cinco, de segunda a sexta; na segunda-feira, nenhum."""
    d = date.fromisoformat(dia[:10])
    segunda = d - timedelta(days=d.weekday())
    return tuple(x.isoformat() for x in (segunda + timedelta(days=i) for i in range(5)) if x < d)


def dias_uteis_entre(de: str, ate: str) -> int:
    """Quantos dias úteis há depois de ``de`` até ``ate``, inclusive (0 quando ``ate`` não é
    posterior). É a conta do frescor: dado de sexta lido na segunda tem 1 dia útil; na terça, 2."""
    inicio, fim = date.fromisoformat(de[:10]), date.fromisoformat(ate[:10])
    return sum(1 for i in range(1, (fim - inicio).days + 1) if (inicio + timedelta(days=i)).weekday() < 5)


# ── objetos tipados ──────────────────────────────────────────────────────────────────────────


def _pares(valor: Any, ordem: tuple[str, ...] = ()) -> tuple[tuple[str, int], ...]:
    """Contagens como pares ``(nome, n)`` em ordem estável: primeiro os nomes de ``ordem``, depois
    os demais em ordem alfabética; contagem zero some."""
    itens = valor.items() if isinstance(valor, Mapping) else (valor or ())
    soma: dict[str, int] = {}
    for nome, n in itens:
        soma[str(nome)] = soma.get(str(nome), 0) + int(n)
    posicao = {nome: i for i, nome in enumerate(ordem)}
    return tuple(sorted(((k, v) for k, v in soma.items() if v), key=lambda par: (posicao.get(par[0], len(ordem)), par[0])))


@dataclass(frozen=True)
class Relator:
    """Quem relata, como a fonte informa. Ministro não tem partido nem UF."""

    nome: str
    partido: str | None = None
    uf: str | None = None

    def para_dict(self) -> dict[str, Any]:
        return {"nome": self.nome, "partido": self.partido, "uf": self.uf}

    @classmethod
    def de_dict(cls, d: Mapping[str, Any]) -> Relator:
        return cls(nome=d["nome"], partido=d.get("partido"), uf=d.get("uf"))


@dataclass(frozen=True)
class Tema:
    """O tema de repercussão geral (STF) ou de precedente qualificado (STJ) ligado ao item."""

    numero: int
    titulo: str  # o título oficial inteiro (a tela corta no fim de uma palavra)
    tipo: str  # TIPOS_TEMA

    def para_dict(self) -> dict[str, Any]:
        return {"numero": self.numero, "titulo": self.titulo, "tipo": self.tipo}

    @classmethod
    def de_dict(cls, d: Mapping[str, Any]) -> Tema:
        return cls(numero=d["numero"], titulo=d["titulo"], tipo=d["tipo"])


@dataclass(frozen=True)
class Parte:
    """Uma parte pública permitida: ente público ou pessoa jurídica do catálogo de litigantes
    (``config/tribunais_partes.yaml``; quem confere é o coletor). Pessoa física e advogado nunca."""

    polo: str  # POLOS
    tipo: str  # TIPOS_PARTE
    nome: str

    def para_dict(self) -> dict[str, Any]:
        return {"polo": self.polo, "tipo": self.tipo, "nome": self.nome}

    @classmethod
    def de_dict(cls, d: Mapping[str, Any]) -> Parte:
        return cls(polo=d["polo"], tipo=d["tipo"], nome=d["nome"])


@dataclass(frozen=True)
class Resultado:
    """O resultado oficial de um item já deliberado: a palavra e a frase oficial de onde ela saiu
    (só a frase do resultado do próprio item, sem placar). Os tribunais não têm este dado."""

    palavra: str  # PALAVRAS_RESULTADO
    texto: str | None = None

    def para_dict(self) -> dict[str, Any]:
        return {"palavra": self.palavra, "texto": self.texto}

    @classmethod
    def de_dict(cls, d: Mapping[str, Any]) -> Resultado:
        return cls(palavra=d["palavra"], texto=d.get("texto"))


@dataclass(frozen=True)
class ItemAgenda:
    """Um item de pauta: só campos oficiais e sinais para a régua.

    ``identificador`` é o que a tela mostra e o que liga o item à mídia (``PLP 299/2026``,
    ``RE 1326541``) e começa pelo ``tipo`` (a sigla ou a classe). ``apelido`` é o nome curto que
    a própria fonte publica (o cabeçalho do Senado, o nome da medida provisória); apelido aprovado
    por pessoa fica em ``config/agenda.yaml``, nunca aqui. ``ementa`` é a oficial, inteira; nos
    tribunais só existe o resumo oficial das ações de controle concentrado e dos temas de
    repercussão geral (nos demais fica nula). ``area`` é a classificação oficial já reduzida ao id
    do projeto (tema de ``config/congresso.yaml`` ou macroárea de ``config/tribunais.yaml``).
    ``ficha`` e ``relevancia`` vêm da ficha do Congresso quando o item casa com ela (a nota só
    desempata). ``segredo`` marca o processo em segredo de justiça: :class:`SessaoAgenda` nunca o
    guarda, só o conta."""

    identificador: str
    tipo: str
    alcance: str  # ALCANCES
    estagio: str  # ESTAGIOS (o principal: ver estagio_principal)
    link: str  # página oficial de tramitação ou de andamento
    apelido: str | None = None
    ementa: str | None = None
    tema: Tema | None = None
    area: str | None = None
    estagio_detalhe: str | None = None  # do estágio principal: o ministro da vista ou do destaque, o regime de urgência
    outros_estagios: tuple[str, ...] = ()  # os demais estágios que valem (a régua pontua o maior)
    resultado: Resultado | None = None
    relator: Relator | None = None
    ordem: int | None = None  # a posição na pauta oficial
    partes: tuple[Parte, ...] = ()
    ficha: str | None = None  # "cd-PL-2630-2020": a âncora da ficha na aba Congresso
    relevancia: int | float | None = None  # a nota de relevância da ficha (0 a 100)
    segredo: bool = False

    def __post_init__(self) -> None:
        posicao = {e: i for i, e in enumerate(ESTAGIOS)}
        outros = sorted({str(e) for e in self.outros_estagios} - {self.estagio}, key=lambda e: (posicao.get(e, len(ESTAGIOS)), e))
        object.__setattr__(self, "outros_estagios", tuple(outros))
        object.__setattr__(self, "partes", tuple(self.partes))
        nota = self.relevancia
        if isinstance(nota, float):
            object.__setattr__(self, "relevancia", int(nota) if nota.is_integer() else round(nota, 1))

    @property
    def estagios(self) -> tuple[str, ...]:
        """Todos os estágios que valem para o item, o principal primeiro."""
        return (self.estagio, *self.outros_estagios)

    def para_dict(self) -> dict[str, Any]:
        return {
            "identificador": self.identificador,
            "tipo": self.tipo,
            "alcance": self.alcance,
            "apelido": self.apelido,
            "ementa": self.ementa,
            "tema": self.tema.para_dict() if self.tema else None,
            "area": self.area,
            "estagio": self.estagio,
            "estagio_detalhe": self.estagio_detalhe,
            "outros_estagios": list(self.outros_estagios),
            "resultado": self.resultado.para_dict() if self.resultado else None,
            "relator": self.relator.para_dict() if self.relator else None,
            "ordem": self.ordem,
            "link": self.link,
            "partes": [p.para_dict() for p in self.partes],
            "ficha": self.ficha,
            "relevancia": self.relevancia,
            "segredo": bool(self.segredo),
        }

    @classmethod
    def de_dict(cls, d: Mapping[str, Any]) -> ItemAgenda:
        return cls(
            identificador=d["identificador"],
            tipo=d["tipo"],
            alcance=d["alcance"],
            estagio=d["estagio"],
            link=d["link"],
            apelido=d.get("apelido"),
            ementa=d.get("ementa"),
            tema=Tema.de_dict(d["tema"]) if d.get("tema") else None,
            area=d.get("area"),
            estagio_detalhe=d.get("estagio_detalhe"),
            outros_estagios=tuple(d.get("outros_estagios") or ()),
            resultado=Resultado.de_dict(d["resultado"]) if d.get("resultado") else None,
            relator=Relator.de_dict(d["relator"]) if d.get("relator") else None,
            ordem=d.get("ordem"),
            partes=tuple(Parte.de_dict(p) for p in d.get("partes") or ()),
            ficha=d.get("ficha"),
            relevancia=d.get("relevancia"),
            segredo=bool(d.get("segredo")),
        )


def _ordem_do_item(item: ItemAgenda) -> tuple[bool, int, str]:
    return (item.ordem is None, item.ordem or 0, item.identificador)


def _sem_segredo(itens: Iterable[ItemAgenda]) -> tuple[tuple[ItemAgenda, ...], int]:
    """Os itens que podem ser guardados, na ordem da pauta, e quantos estavam em segredo."""
    todos = tuple(itens)
    publicaveis = tuple(sorted((i for i in todos if not i.segredo), key=_ordem_do_item))
    return publicaveis, len(todos) - len(publicaveis)


@dataclass(frozen=True)
class SessaoAgenda:
    """Uma linha de pauta: uma sessão, um julgamento, uma reunião de comissão ou um prazo.

    ``id`` é estável entre coletas (é por ele que a mesclagem reconhece a mesma sessão):
    ``camara-83419``, ``senado-594181``, ``stf-plenario-2026-11-25``, ``stj-ce-2026-11-25``,
    ``congresso-prazo-mpv-1381-2026``. ``hora``, ``modalidade`` e ``pauta_confirmada`` só existem
    quando a fonte diz. ``como_pautado_em`` é posto pela mesclagem na sessão mantida do arquivo
    anterior: a data do dado em que a fonte a trazia ("como pautado em DD/MM"). ``fora`` conta o
    que ficou fora da seleção, por motivo; item marcado ``segredo`` sai de ``itens`` e entra nessa
    conta ao construir o objeto."""

    id: str
    instituicao: str  # INSTITUICOES
    orgao: str  # por extenso: "Plenário", "Comissão de Constituição e Justiça e de Cidadania", "Corte Especial"
    foro: str  # FOROS
    tipo: str  # TIPOS_SESSAO
    data: str
    situacao: str  # SITUACOES_SESSAO
    link: str
    hora: str | None = None
    orgao_sigla: str | None = None  # "PLEN", "CCJC", "CE": casa com comissoes_que_decidem
    modalidade: str | None = None  # MODALIDADES
    pauta: str = "publicada"  # ESTADOS_PAUTA
    pauta_confirmada: bool | None = None
    como_pautado_em: str | None = None
    itens: tuple[ItemAgenda, ...] = ()
    fora: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        itens, em_segredo = _sem_segredo(self.itens)
        fora = dict(_pares(self.fora))
        if em_segredo:
            fora["segredo"] = fora.get("segredo", 0) + em_segredo
        object.__setattr__(self, "itens", itens)
        object.__setattr__(self, "fora", _pares(fora, MOTIVOS_FORA))

    @property
    def fora_total(self) -> int:
        return sum(n for _motivo, n in self.fora)

    @property
    def total_na_fonte(self) -> int:
        """Quantos itens a fonte publica para a sessão: os guardados mais os contados."""
        return len(self.itens) + self.fora_total

    def para_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "instituicao": self.instituicao,
            "orgao": self.orgao,
            "orgao_sigla": self.orgao_sigla,
            "foro": self.foro,
            "tipo": self.tipo,
            "data": self.data,
            "hora": self.hora,
            "situacao": self.situacao,
            "modalidade": self.modalidade,
            "pauta": self.pauta,
            "pauta_confirmada": self.pauta_confirmada,
            "como_pautado_em": self.como_pautado_em,
            "link": self.link,
            "itens": [i.para_dict() for i in self.itens],
            "fora": dict(self.fora),
        }

    @classmethod
    def de_dict(cls, d: Mapping[str, Any]) -> SessaoAgenda:
        return cls(
            id=d["id"],
            instituicao=d["instituicao"],
            orgao=d["orgao"],
            foro=d["foro"],
            tipo=d["tipo"],
            data=d["data"],
            situacao=d["situacao"],
            link=d["link"],
            hora=d.get("hora"),
            orgao_sigla=d.get("orgao_sigla"),
            modalidade=d.get("modalidade"),
            pauta=d.get("pauta") or "publicada",
            pauta_confirmada=d.get("pauta_confirmada"),
            como_pautado_em=d.get("como_pautado_em"),
            itens=tuple(ItemAgenda.de_dict(i) for i in d.get("itens") or ()),
            fora=_pares(d.get("fora") or {}, MOTIVOS_FORA),
        )


def _ordem_da_sessao(sessao: SessaoAgenda) -> tuple[Any, ...]:
    """Por dia; dentro do dia, a hora oficial (linha sem hora depois); no empate, a instituição."""
    posicao = INSTITUICOES.index(sessao.instituicao) if sessao.instituicao in INSTITUICOES else len(INSTITUICOES)
    return (sessao.data, sessao.hora is None, sessao.hora or "", posicao, sessao.orgao, sessao.id)


@dataclass(frozen=True)
class FonteAgenda:
    """A situação de uma fonte na coleta. ``data_dados`` é o dia do dado que o arquivo mostra e
    ``coletado_em`` o instante em que ele foi lido: quando a fonte falha (``ok`` falso, com o
    ``aviso``), os dois continuam sendo os do último dia bom. ``velho_dias``, quando a fonte tem o
    seu (``data/tribunais/meta.json``), é o limite acima do qual os itens dela somem da agenda."""

    chave: str  # "camara_eventos", "senado_plenario", "congresso_prazos", "stf_pauta", "stj_pautas"
    instituicao: str
    nome: str  # como a tela a chama: "Câmara: eventos e pautas"
    url: str  # o endereço oficial para o leitor conferir
    ok: bool
    data_dados: str | None = None
    coletado_em: str | None = None
    aviso: str | None = None
    velho_dias: int | None = None

    def para_dict(self) -> dict[str, Any]:
        return {
            "instituicao": self.instituicao,
            "nome": self.nome,
            "url": self.url,
            "ok": self.ok,
            "data_dados": self.data_dados,
            "coletado_em": self.coletado_em,
            "aviso": self.aviso,
            "velho_dias": self.velho_dias,
        }

    @classmethod
    def de_dict(cls, chave: str, d: Mapping[str, Any]) -> FonteAgenda:
        return cls(
            chave=chave,
            instituicao=d["instituicao"],
            nome=d["nome"],
            url=d["url"],
            ok=d["ok"],
            data_dados=d.get("data_dados"),
            coletado_em=d.get("coletado_em"),
            aviso=d.get("aviso"),
            velho_dias=d.get("velho_dias"),
        )


@dataclass(frozen=True)
class Recesso:
    """Recesso em curso ou que alcança a janela ("Congresso em recesso até DD/MM")."""

    nome: str
    de: str
    ate: str

    def para_dict(self) -> dict[str, Any]:
        return {"nome": self.nome, "de": self.de, "ate": self.ate}

    @classmethod
    def de_dict(cls, d: Mapping[str, Any]) -> Recesso:
        return cls(nome=d["nome"], de=d["de"], ate=d["ate"])


@dataclass(frozen=True)
class JulgamentoVirtual:
    """Uma sessão virtual do STF: a fonte só informa a contagem de processos por colegiado."""

    de: str
    ate: str
    plenario: int
    turma1: int
    turma2: int

    @property
    def total(self) -> int:
        return self.plenario + self.turma1 + self.turma2

    def para_dict(self) -> dict[str, Any]:
        return {"de": self.de, "ate": self.ate, "plenario": self.plenario, "turma1": self.turma1, "turma2": self.turma2}

    @classmethod
    def de_dict(cls, d: Mapping[str, Any]) -> JulgamentoVirtual:
        return cls(de=d["de"], ate=d["ate"], plenario=d["plenario"], turma1=d["turma1"], turma2=d["turma2"])


@dataclass(frozen=True)
class EstadoInstituicao:
    """O que a agenda precisa saber de uma instituição para escrever as frases.

    - ``recesso``: o recesso que alcança a janela;
    - ``pauta_publicada_ate``: o último dia para o qual a fonte já publicou a pauta do Plenário;
    - ``fila`` e ``fila_de``: o que ficou sem deliberação na última sessão do Plenário da Câmara
      e o dia dessa sessão (rotulada como fila: não tem data marcada);
    - ``sessao_conjunta_convocada``: só no Congresso (``None`` quando a fonte não respondeu);
    - ``proximo``: o próximo evento relevante com data DEPOIS da janela de coleta (o que cai dentro
      dela já está em ``sessoes``);
    - ``virtuais``: só no STF, as contagens do Plenário Virtual e das Turmas;
    - ``contagens``: números para a página completa (nunca para a home), por nome simples."""

    instituicao: str
    recesso: Recesso | None = None
    pauta_publicada_ate: str | None = None
    fila: tuple[ItemAgenda, ...] = ()
    fila_de: str | None = None
    sessao_conjunta_convocada: bool | None = None
    proximo: SessaoAgenda | None = None
    virtuais: tuple[JulgamentoVirtual, ...] = ()
    contagens: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        fila, _em_segredo = _sem_segredo(self.fila)
        object.__setattr__(self, "fila", fila)
        object.__setattr__(self, "virtuais", tuple(sorted(self.virtuais, key=lambda v: (v.de, v.ate))))
        object.__setattr__(self, "contagens", _pares(self.contagens))

    def para_dict(self) -> dict[str, Any]:
        return {
            "recesso": self.recesso.para_dict() if self.recesso else None,
            "pauta_publicada_ate": self.pauta_publicada_ate,
            "fila": [i.para_dict() for i in self.fila],
            "fila_de": self.fila_de,
            "sessao_conjunta_convocada": self.sessao_conjunta_convocada,
            "proximo": self.proximo.para_dict() if self.proximo else None,
            "virtuais": [v.para_dict() for v in self.virtuais],
            "contagens": dict(self.contagens),
        }

    @classmethod
    def de_dict(cls, instituicao: str, d: Mapping[str, Any]) -> EstadoInstituicao:
        return cls(
            instituicao=instituicao,
            recesso=Recesso.de_dict(d["recesso"]) if d.get("recesso") else None,
            pauta_publicada_ate=d.get("pauta_publicada_ate"),
            fila=tuple(ItemAgenda.de_dict(i) for i in d.get("fila") or ()),
            fila_de=d.get("fila_de"),
            sessao_conjunta_convocada=d.get("sessao_conjunta_convocada"),
            proximo=SessaoAgenda.de_dict(d["proximo"]) if d.get("proximo") else None,
            virtuais=tuple(JulgamentoVirtual.de_dict(v) for v in d.get("virtuais") or ()),
            contagens=_pares(d.get("contagens") or {}),
        )


@dataclass(frozen=True)
class Agenda:
    """Um arquivo de agenda inteiro (um dos dois lados). ``hoje`` é o dia da coleta em Brasília;
    ``janela`` sai dele (:func:`janela_de_coleta`) quando não é dada. As listas ficam sempre em
    ordem estável: fontes pela chave, sessões por dia e hora, estado na ordem das instituições."""

    lado: str  # LADOS
    gerado_em: str
    hoje: str
    fontes: tuple[FonteAgenda, ...] = ()
    sessoes: tuple[SessaoAgenda, ...] = ()
    estado: tuple[EstadoInstituicao, ...] = ()
    janela: tuple[str, str] | None = None
    versao: int = VERSAO

    def __post_init__(self) -> None:
        posicao = {inst: i for i, inst in enumerate(INSTITUICOES)}
        object.__setattr__(self, "fontes", tuple(sorted(self.fontes, key=lambda f: f.chave)))
        object.__setattr__(self, "sessoes", tuple(sorted(self.sessoes, key=_ordem_da_sessao)))
        object.__setattr__(self, "estado", tuple(sorted(self.estado, key=lambda e: (posicao.get(e.instituicao, len(posicao)), e.instituicao))))
        object.__setattr__(self, "janela", tuple(self.janela) if self.janela else janela_de_coleta(self.hoje))

    # ── consultas ────────────────────────────────────────────────────────────

    @property
    def instituicoes(self) -> tuple[str, ...]:
        """As instituições que este lado cobre."""
        return INSTITUICOES_DO_LADO.get(self.lado, ())

    def fonte(self, chave: str) -> FonteAgenda | None:
        return next((f for f in self.fontes if f.chave == chave), None)

    def fontes_de(self, instituicao: str) -> tuple[FonteAgenda, ...]:
        return tuple(f for f in self.fontes if f.instituicao == instituicao)

    def refeita(self, instituicao: str) -> bool:
        """A instituição foi coletada por inteiro nesta rodada: tem fonte e todas estão ``ok``."""
        fontes = self.fontes_de(instituicao)
        return bool(fontes) and all(f.ok for f in fontes)

    def data_dados_de(self, instituicao: str) -> str | None:
        """O dia do dado mais novo entre as fontes da instituição."""
        return max((f.data_dados for f in self.fontes_de(instituicao) if f.data_dados), default=None)

    def sessoes_de(self, instituicao: str) -> tuple[SessaoAgenda, ...]:
        return tuple(s for s in self.sessoes if s.instituicao == instituicao)

    def sessoes_em(self, dia: str) -> tuple[SessaoAgenda, ...]:
        return tuple(s for s in self.sessoes if s.data == dia)

    def estado_de(self, instituicao: str) -> EstadoInstituicao | None:
        return next((e for e in self.estado if e.instituicao == instituicao), None)

    # ── ida e volta ──────────────────────────────────────────────────────────

    def para_dict(self) -> dict[str, Any]:
        de, ate = self.janela or janela_de_coleta(self.hoje)
        return {
            "versao": self.versao,
            "lado": self.lado,
            "gerado_em": self.gerado_em,
            "hoje": self.hoje,
            "janela": {"de": de, "ate": ate},
            "fontes": {f.chave: f.para_dict() for f in self.fontes},
            "sessoes": [s.para_dict() for s in self.sessoes],
            "estado": {e.instituicao: e.para_dict() for e in self.estado},
        }

    @classmethod
    def de_dict(cls, d: Mapping[str, Any]) -> Agenda:
        """Monta o objeto de um dicionário JÁ validado (:func:`ler_dados` valida antes)."""
        janela = d.get("janela") or {}
        return cls(
            lado=d["lado"],
            gerado_em=d["gerado_em"],
            hoje=d["hoje"],
            fontes=tuple(FonteAgenda.de_dict(chave, f) for chave, f in (d.get("fontes") or {}).items()),
            sessoes=tuple(SessaoAgenda.de_dict(s) for s in d.get("sessoes") or ()),
            estado=tuple(EstadoInstituicao.de_dict(inst, e) for inst, e in (d.get("estado") or {}).items()),
            janela=(janela["de"], janela["ate"]) if janela.get("de") and janela.get("ate") else None,
            versao=d.get("versao", VERSAO),
        )


# ── validação ────────────────────────────────────────────────────────────────────────────────

_CHAVES_AGENDA = ("versao", "lado", "gerado_em", "hoje", "janela", "fontes", "sessoes", "estado")
_CHAVES_FONTE = ("instituicao", "nome", "url", "ok", "data_dados", "coletado_em", "aviso", "velho_dias")
_CHAVES_SESSAO = (
    "id", "instituicao", "orgao", "orgao_sigla", "foro", "tipo", "data", "hora", "situacao", "modalidade", "pauta",
    "pauta_confirmada", "como_pautado_em", "link", "itens", "fora",
)
_CHAVES_ITEM = (
    "identificador", "tipo", "alcance", "apelido", "ementa", "tema", "area", "estagio", "estagio_detalhe",
    "outros_estagios", "resultado", "relator", "ordem", "link", "partes", "ficha", "relevancia", "segredo",
)
_CHAVES_ESTADO = (
    "recesso", "pauta_publicada_ate", "fila", "fila_de", "sessao_conjunta_convocada", "proximo", "virtuais", "contagens",
)
# Tetos de sanidade dos textos oficiais (folgados: o título de tema mais longo publicado tem 733 caracteres).
TEXTO_MAXIMO = {"ementa": 8000, "titulo": 1200, "resultado": 500, "orgao": 300, "apelido": 300, "nome": 200, "detalhe": 200, "aviso": 500}


def _objeto(valor: Any, chaves: tuple[str, ...], onde: str, erros: list[str]) -> bool:
    """Confere que ``valor`` é um objeto com exatamente ``chaves``. Devolve se dá para olhar dentro."""
    if not isinstance(valor, dict):
        erros.append(f"{onde}: esperado um objeto, veio {type(valor).__name__}")
        return False
    faltam = [c for c in chaves if c not in valor]
    sobram = sorted(str(c) for c in valor if c not in chaves)
    if faltam:
        erros.append(f"{onde}: falta a chave {', '.join(faltam)}")
    if sobram:
        erros.append(f"{onde}: chave desconhecida {', '.join(sobram)}")
    return True


def _texto(valor: Any, onde: str, erros: list[str], *, maximo: int, nulo: bool = False) -> None:
    if valor is None:
        if not nulo:
            erros.append(f"{onde}: texto obrigatório")
        return
    if not isinstance(valor, str) or not valor:
        erros.append(f"{onde}: esperado um texto não vazio")
    elif len(valor) > maximo:
        erros.append(f"{onde}: texto com {len(valor)} caracteres (máximo {maximo})")
    elif valor != limpar_texto(valor):
        erros.append(f"{onde}: texto com espaço especial, quebra de linha ou pontas soltas (use limpar_texto)")


def _palavra(valor: Any, vocabulario: tuple[str, ...], onde: str, erros: list[str], *, nulo: bool = False) -> bool:
    if valor is None and nulo:
        return True
    if not isinstance(valor, str) or valor not in vocabulario:
        erros.append(f"{onde}: {valor!r} fora do vocabulário ({', '.join(vocabulario)})")
        return False
    return True


def _dia(valor: Any, onde: str, erros: list[str], *, nulo: bool = False) -> date | None:
    if valor is None and nulo:
        return None
    dia = _data(valor)
    if dia is None:
        erros.append(f"{onde}: esperada uma data AAAA-MM-DD, veio {valor!r}")
    return dia


def _inteiro(valor: Any, onde: str, erros: list[str], *, minimo: int = 0, nulo: bool = False) -> None:
    if valor is None and nulo:
        return
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < minimo:
        erros.append(f"{onde}: esperado um inteiro a partir de {minimo}, veio {valor!r}")


def _link(valor: Any, onde: str, erros: list[str]) -> None:
    if not link_oficial(valor):
        erros.append(f"{onde}: link oficial obrigatório (https em {', '.join('.' + d for d in DOMINIOS_OFICIAIS)}), veio {valor!r}")


def _contagens(valor: Any, onde: str, erros: list[str], *, nomes: tuple[str, ...] | None = None, minimo: int = 0) -> None:
    if not isinstance(valor, dict):
        erros.append(f"{onde}: esperado um objeto de contagens, veio {type(valor).__name__}")
        return
    for nome, n in valor.items():
        if nomes is not None:
            _palavra(nome, nomes, f"{onde}.{nome}", erros)
        elif not isinstance(nome, str) or not _RE_CHAVE.match(nome):
            erros.append(f"{onde}: nome de contagem inválido {nome!r}")
        _inteiro(n, f"{onde}.{nome}", erros, minimo=minimo)


def _validar_item(item: Any, onde: str, lado: str | None, erros: list[str]) -> None:
    if not _objeto(item, _CHAVES_ITEM, onde, erros):
        return
    identificador, tipo = item.get("identificador"), item.get("tipo")
    if not isinstance(tipo, str) or not _RE_TIPO.match(tipo):
        erros.append(f"{onde}.tipo: esperada a sigla ou a classe (só letras), veio {tipo!r}")
    if not isinstance(identificador, str) or not _RE_IDENTIFICADOR.match(identificador):
        erros.append(f"{onde}.identificador: esperado sigla e número (\"PLP 299/2026\", \"RE 1326541\"), veio {identificador!r}")
    elif isinstance(tipo, str) and not identificador.startswith(tipo + " "):
        erros.append(f"{onde}.identificador: {identificador!r} não começa pelo tipo {tipo!r}")
    _palavra(item.get("alcance"), ALCANCES, f"{onde}.alcance", erros)
    _texto(item.get("apelido"), f"{onde}.apelido", erros, maximo=TEXTO_MAXIMO["apelido"], nulo=True)
    _texto(item.get("ementa"), f"{onde}.ementa", erros, maximo=TEXTO_MAXIMO["ementa"], nulo=True)

    tema = item.get("tema")
    if tema is not None and _objeto(tema, ("numero", "titulo", "tipo"), f"{onde}.tema", erros):
        _inteiro(tema.get("numero"), f"{onde}.tema.numero", erros, minimo=1)
        _texto(tema.get("titulo"), f"{onde}.tema.titulo", erros, maximo=TEXTO_MAXIMO["titulo"])
        _palavra(tema.get("tipo"), TIPOS_TEMA, f"{onde}.tema.tipo", erros)

    area = item.get("area")
    if area is not None and (not isinstance(area, str) or not _RE_CHAVE.match(area)):
        erros.append(f"{onde}.area: esperado o id da área (\"economia\", \"tributario\"), veio {area!r}")

    estagio = item.get("estagio")
    _palavra(estagio, ESTAGIOS, f"{onde}.estagio", erros)
    _texto(item.get("estagio_detalhe"), f"{onde}.estagio_detalhe", erros, maximo=TEXTO_MAXIMO["detalhe"], nulo=True)
    outros = item.get("outros_estagios")
    if not isinstance(outros, list):
        erros.append(f"{onde}.outros_estagios: esperada uma lista")
    else:
        for i, outro in enumerate(outros):
            _palavra(outro, ESTAGIOS, f"{onde}.outros_estagios[{i}]", erros)
        textos = [o for o in outros if isinstance(o, str)]
        if len(set(textos)) != len(textos) or estagio in textos:
            erros.append(f"{onde}.outros_estagios: estágio repetido")

    resultado = item.get("resultado")
    if resultado is not None:
        if lado == LADO_TRIBUNAIS:
            erros.append(f"{onde}.resultado: os tribunais não têm dado de resultado (deixe nulo)")
        elif _objeto(resultado, ("palavra", "texto"), f"{onde}.resultado", erros):
            _palavra(resultado.get("palavra"), PALAVRAS_RESULTADO, f"{onde}.resultado.palavra", erros)
            texto = resultado.get("texto")
            _texto(texto, f"{onde}.resultado.texto", erros, maximo=TEXTO_MAXIMO["resultado"], nulo=True)
            if isinstance(texto, str) and _PLACAR.search(texto):
                erros.append(f"{onde}.resultado.texto: traz placar; nesta entrega vai só a frase do resultado")

    relator = item.get("relator")
    if relator is not None and _objeto(relator, ("nome", "partido", "uf"), f"{onde}.relator", erros):
        _texto(relator.get("nome"), f"{onde}.relator.nome", erros, maximo=TEXTO_MAXIMO["nome"])
        _texto(relator.get("partido"), f"{onde}.relator.partido", erros, maximo=30, nulo=True)
        uf = relator.get("uf")
        if uf is not None and (not isinstance(uf, str) or not _RE_UF.match(uf)):
            erros.append(f"{onde}.relator.uf: esperada a sigla do estado, veio {uf!r}")

    _inteiro(item.get("ordem"), f"{onde}.ordem", erros, minimo=1, nulo=True)
    _link(item.get("link"), f"{onde}.link", erros)

    partes = item.get("partes")
    if not isinstance(partes, list):
        erros.append(f"{onde}.partes: esperada uma lista")
    else:
        for i, parte in enumerate(partes):
            if _objeto(parte, ("polo", "tipo", "nome"), f"{onde}.partes[{i}]", erros):
                _palavra(parte.get("polo"), POLOS, f"{onde}.partes[{i}].polo", erros)
                if parte.get("tipo") not in TIPOS_PARTE:
                    erros.append(f"{onde}.partes[{i}].tipo: só ente público ou pessoa jurídica ({', '.join(TIPOS_PARTE)}); pessoa física nunca")
                _texto(parte.get("nome"), f"{onde}.partes[{i}].nome", erros, maximo=TEXTO_MAXIMO["nome"])

    ficha, nota = item.get("ficha"), item.get("relevancia")
    if ficha is not None and (not isinstance(ficha, str) or not _RE_FICHA.match(ficha)):
        erros.append(f"{onde}.ficha: esperado o id da ficha do Congresso (\"cd-PL-2630-2020\"), veio {ficha!r}")
    if nota is not None:
        if isinstance(nota, bool) or not isinstance(nota, (int, float)) or not 0 <= nota <= 100:
            erros.append(f"{onde}.relevancia: esperada uma nota de 0 a 100, veio {nota!r}")
        if ficha is None:
            erros.append(f"{onde}.relevancia: nota sem ficha (a nota vem da ficha do Congresso)")
    if item.get("segredo") is not False:
        erros.append(f"{onde}.segredo: item em segredo de justiça não é gravado com dados (só entra em fora.segredo)")


def _validar_itens(itens: Any, onde: str, lado: str | None, erros: list[str]) -> None:
    if not isinstance(itens, list):
        erros.append(f"{onde}: esperada uma lista")
        return
    vistos: set[str] = set()
    for i, item in enumerate(itens):
        _validar_item(item, f"{onde}[{i}]", lado, erros)
        identificador = item.get("identificador") if isinstance(item, dict) else None
        if isinstance(identificador, str):
            if identificador in vistos:
                erros.append(f"{onde}[{i}]: {identificador} repetido (linhas repetidas da fonte viram um item)")
            vistos.add(identificador)


def _validar_sessao(sessao: Any, onde: str, lado: str | None, erros: list[str]) -> None:
    if not _objeto(sessao, _CHAVES_SESSAO, onde, erros):
        return
    ident = sessao.get("id")
    if not isinstance(ident, str) or not _RE_ID.match(ident):
        erros.append(f"{onde}.id: esperado um id estável em minúsculas (\"camara-83419\"), veio {ident!r}")
    instituicao, foro, tipo = sessao.get("instituicao"), sessao.get("foro"), sessao.get("tipo")
    do_lado = INSTITUICOES_DO_LADO.get(lado or "", INSTITUICOES)
    if _palavra(instituicao, INSTITUICOES, f"{onde}.instituicao", erros) and instituicao not in do_lado:
        erros.append(f"{onde}.instituicao: {instituicao} não é do lado {lado}")
    _texto(sessao.get("orgao"), f"{onde}.orgao", erros, maximo=TEXTO_MAXIMO["orgao"])
    _texto(sessao.get("orgao_sigla"), f"{onde}.orgao_sigla", erros, maximo=20, nulo=True)
    foros_dela = FOROS_DA_INSTITUICAO.get(instituicao) if isinstance(instituicao, str) else None
    if _palavra(foro, FOROS, f"{onde}.foro", erros) and foros_dela is not None and foro not in foros_dela:
        erros.append(f"{onde}.foro: {foro} não existe em {instituicao}")
    _palavra(tipo, TIPOS_SESSAO, f"{onde}.tipo", erros)
    if (tipo == "prazo") != (foro == "prazo"):
        erros.append(f"{onde}: o tipo \"prazo\" anda junto do foro \"prazo\"")
    _dia(sessao.get("data"), f"{onde}.data", erros)
    hora = sessao.get("hora")
    if hora is not None and (not isinstance(hora, str) or not _RE_HORA.match(hora)):
        erros.append(f"{onde}.hora: esperado HH:MM ou nulo, veio {hora!r}")
    _palavra(sessao.get("situacao"), SITUACOES_SESSAO, f"{onde}.situacao", erros)
    _palavra(sessao.get("modalidade"), MODALIDADES, f"{onde}.modalidade", erros, nulo=True)
    if instituicao == STJ and (hora is not None or sessao.get("modalidade") is not None):
        erros.append(f"{onde}: o STJ não informa hora nem modalidade; a agenda não afirma nenhuma das duas")
    if tipo == "prazo" and hora is not None:
        erros.append(f"{onde}.hora: prazo não tem hora")
    pauta = sessao.get("pauta")
    _palavra(pauta, ESTADOS_PAUTA, f"{onde}.pauta", erros)
    confirmada = sessao.get("pauta_confirmada")
    if confirmada is not None and not isinstance(confirmada, bool):
        erros.append(f"{onde}.pauta_confirmada: esperado verdadeiro, falso ou nulo (só quando a fonte diz)")
    _dia(sessao.get("como_pautado_em"), f"{onde}.como_pautado_em", erros, nulo=True)
    _link(sessao.get("link"), f"{onde}.link", erros)
    _validar_itens(sessao.get("itens"), f"{onde}.itens", lado, erros)
    _contagens(sessao.get("fora"), f"{onde}.fora", erros, nomes=MOTIVOS_FORA, minimo=1)
    if pauta in ("nao_publicada", "nao_coletada") and (sessao.get("itens") or sessao.get("fora")):
        erros.append(f"{onde}.pauta: {pauta} com itens ou contagens")
    situacao = sessao.get("situacao")
    for i, item in enumerate(sessao.get("itens") if isinstance(sessao.get("itens"), list) else ()):
        if not isinstance(item, dict):
            continue
        resultado = item.get("resultado")
        palavra = resultado.get("palavra") if isinstance(resultado, dict) else None
        if palavra is not None and situacao != "encerrada" and palavra not in RESULTADOS_ANTES_DO_FIM:
            erros.append(f"{onde}.itens[{i}].resultado: sessão que não está encerrada não tem resultado")
        if item.get("estagio") == "em_votacao" and situacao != "em_andamento":
            erros.append(f"{onde}.itens[{i}].estagio: em_votacao só vale com a sessão em andamento")


def _validar_estado(estado: Any, instituicao: str, onde: str, lado: str | None, janela_ate: date | None, erros: list[str]) -> str | None:
    """Valida o estado de uma instituição. Devolve o id do próximo evento, se houver."""
    if not _objeto(estado, _CHAVES_ESTADO, onde, erros):
        return None
    recesso = estado.get("recesso")
    if recesso is not None and _objeto(recesso, ("nome", "de", "ate"), f"{onde}.recesso", erros):
        _texto(recesso.get("nome"), f"{onde}.recesso.nome", erros, maximo=TEXTO_MAXIMO["nome"])
        de, ate = _dia(recesso.get("de"), f"{onde}.recesso.de", erros), _dia(recesso.get("ate"), f"{onde}.recesso.ate", erros)
        if de and ate and de > ate:
            erros.append(f"{onde}.recesso: começa depois de terminar")
    _dia(estado.get("pauta_publicada_ate"), f"{onde}.pauta_publicada_ate", erros, nulo=True)
    _validar_itens(estado.get("fila"), f"{onde}.fila", lado, erros)
    for i, item in enumerate(estado.get("fila") if isinstance(estado.get("fila"), list) else ()):
        if isinstance(item, dict) and item.get("resultado") is not None:
            erros.append(f"{onde}.fila[{i}].resultado: item da fila ainda não foi deliberado")
    _dia(estado.get("fila_de"), f"{onde}.fila_de", erros, nulo=True)
    if estado.get("fila") and estado.get("fila_de") is None:
        erros.append(f"{onde}.fila_de: a fila precisa do dia da sessão de onde ela sobrou")
    conjunta = estado.get("sessao_conjunta_convocada")
    if conjunta is not None and not isinstance(conjunta, bool):
        erros.append(f"{onde}.sessao_conjunta_convocada: esperado verdadeiro, falso ou nulo")
    elif conjunta is not None and instituicao != CONGRESSO:
        erros.append(f"{onde}.sessao_conjunta_convocada: só o Congresso tem sessão conjunta")

    id_do_proximo = None
    proximo = estado.get("proximo")
    if proximo is not None:
        _validar_sessao(proximo, f"{onde}.proximo", lado, erros)
        if isinstance(proximo, dict):
            id_do_proximo = proximo.get("id") if isinstance(proximo.get("id"), str) else None
            if proximo.get("instituicao") != instituicao:
                erros.append(f"{onde}.proximo.instituicao: o próximo evento é de outra instituição")
            dia = _data(proximo.get("data"))
            if dia and janela_ate and dia <= janela_ate:
                erros.append(f"{onde}.proximo.data: cai dentro da janela (o lugar dela é em sessoes)")

    virtuais = estado.get("virtuais")
    if not isinstance(virtuais, list):
        erros.append(f"{onde}.virtuais: esperada uma lista")
    else:
        if virtuais and instituicao != STF:
            erros.append(f"{onde}.virtuais: só o STF publica contagens de julgamento virtual")
        for i, virtual in enumerate(virtuais):
            if _objeto(virtual, ("de", "ate", "plenario", "turma1", "turma2"), f"{onde}.virtuais[{i}]", erros):
                de, ate = _dia(virtual.get("de"), f"{onde}.virtuais[{i}].de", erros), _dia(virtual.get("ate"), f"{onde}.virtuais[{i}].ate", erros)
                if de and ate and de > ate:
                    erros.append(f"{onde}.virtuais[{i}]: começa depois de terminar")
                for campo in ("plenario", "turma1", "turma2"):
                    _inteiro(virtual.get(campo), f"{onde}.virtuais[{i}].{campo}", erros)
    _contagens(estado.get("contagens"), f"{onde}.contagens", erros)
    return id_do_proximo


def validar(dados: Any) -> list[str]:
    """Os erros de um arquivo de agenda já lido do JSON (lista vazia = válido). Nunca levanta
    exceção: dado de qualquer forma vira uma lista de erros, cada um com o caminho do campo."""
    erros: list[str] = []
    try:
        _validar_agenda(dados, erros)
    except Exception as erro:  # defesa: um formato que nenhuma checagem previu
        erros.append(f"formato inesperado: {type(erro).__name__}: {erro}")
    return erros


def _validar_agenda(dados: Any, erros: list[str]) -> None:
    if not _objeto(dados, _CHAVES_AGENDA, "agenda", erros):
        return
    versao = dados.get("versao")
    if isinstance(versao, bool) or versao != VERSAO:
        erros.append(f"versao: esperada {VERSAO}, veio {versao!r}")
    lado = dados.get("lado")
    if not _palavra(lado, LADOS, "lado", erros):
        lado = None
    if not _instante(dados.get("gerado_em")):
        erros.append(f"gerado_em: esperado AAAA-MM-DDTHH:MM:SSZ, veio {dados.get('gerado_em')!r}")
    hoje = _dia(dados.get("hoje"), "hoje", erros)

    janela_de = janela_ate = None
    janela = dados.get("janela")
    if _objeto(janela, ("de", "ate"), "janela", erros):
        janela_de, janela_ate = _dia(janela.get("de"), "janela.de", erros), _dia(janela.get("ate"), "janela.ate", erros)
        if hoje and janela_de and janela_ate and (janela_de.isoformat(), janela_ate.isoformat()) != janela_de_coleta(hoje.isoformat()):
            de, ate = janela_de_coleta(hoje.isoformat())
            erros.append(f"janela: esperada de {de} a {ate} (a segunda-feira da semana de hoje e 13 dias depois)")

    do_lado = INSTITUICOES_DO_LADO.get(lado or "", INSTITUICOES)
    fontes = dados.get("fontes")
    if not isinstance(fontes, dict) or not fontes:
        erros.append("fontes: esperado um objeto com ao menos uma fonte")
    else:
        for chave, fonte in fontes.items():
            onde = f"fontes.{chave}"
            if not isinstance(chave, str) or not _RE_CHAVE.match(chave):
                erros.append(f"{onde}: chave de fonte inválida")
            if not _objeto(fonte, _CHAVES_FONTE, onde, erros):
                continue
            if _palavra(fonte.get("instituicao"), INSTITUICOES, f"{onde}.instituicao", erros) and fonte.get("instituicao") not in do_lado:
                erros.append(f"{onde}.instituicao: {fonte.get('instituicao')} não é do lado {lado}")
            _texto(fonte.get("nome"), f"{onde}.nome", erros, maximo=TEXTO_MAXIMO["nome"])
            _link(fonte.get("url"), f"{onde}.url", erros)
            ok = fonte.get("ok")
            if not isinstance(ok, bool):
                erros.append(f"{onde}.ok: esperado verdadeiro ou falso, veio {ok!r}")
            _dia(fonte.get("data_dados"), f"{onde}.data_dados", erros, nulo=True)
            coletado = fonte.get("coletado_em")
            if coletado is not None and not _instante(coletado):
                erros.append(f"{onde}.coletado_em: esperado AAAA-MM-DDTHH:MM:SSZ ou nulo, veio {coletado!r}")
            _texto(fonte.get("aviso"), f"{onde}.aviso", erros, maximo=TEXTO_MAXIMO["aviso"], nulo=True)
            _inteiro(fonte.get("velho_dias"), f"{onde}.velho_dias", erros, minimo=1, nulo=True)
            if ok is True and (fonte.get("data_dados") is None or coletado is None):
                erros.append(f"{onde}: fonte ok precisa de data_dados e de coletado_em")
            if ok is False and fonte.get("aviso") is None:
                erros.append(f"{onde}.aviso: fonte que falhou precisa dizer o que houve")

    ids: list[str] = []
    sessoes = dados.get("sessoes")
    if not isinstance(sessoes, list):
        erros.append("sessoes: esperada uma lista")
    else:
        for i, sessao in enumerate(sessoes):
            onde = f"sessoes[{i}]"
            _validar_sessao(sessao, onde, lado, erros)
            if not isinstance(sessao, dict):
                continue
            if isinstance(sessao.get("id"), str):
                ids.append(sessao["id"])
            dia = _data(sessao.get("data"))
            if dia and janela_de and janela_ate and not janela_de <= dia <= janela_ate:
                erros.append(f"{onde}.data: {dia.isoformat()} fora da janela")
            marcado = _data(sessao.get("como_pautado_em"))
            if marcado and hoje and marcado > hoje:
                erros.append(f"{onde}.como_pautado_em: data no futuro")

    estado = dados.get("estado")
    if not isinstance(estado, dict):
        erros.append("estado: esperado um objeto por instituição")
    else:
        for instituicao, conteudo in estado.items():
            onde = f"estado.{instituicao}"
            if instituicao not in INSTITUICOES:
                erros.append(f"{onde}: instituição desconhecida")
                continue
            if instituicao not in do_lado:
                erros.append(f"{onde}: {instituicao} não é do lado {lado}")
            proximo = _validar_estado(conteudo, instituicao, onde, lado, janela_ate, erros)
            if proximo:
                ids.append(proximo)

    repetidos = sorted({i for i in ids if ids.count(i) > 1})
    if repetidos:
        erros.append(f"sessoes: id repetido ({', '.join(repetidos)})")


# ── ler e escrever ───────────────────────────────────────────────────────────────────────────


class ErroDeContrato(ValueError):
    """A agenda que se tentou escrever não cumpre o contrato. ``erros`` traz a lista inteira."""

    def __init__(self, erros: list[str]) -> None:
        self.erros = list(erros)
        resto = f" (e mais {len(self.erros) - 3})" if len(self.erros) > 3 else ""
        super().__init__("agenda fora do contrato: " + "; ".join(self.erros[:3]) + resto)


def para_json(agenda: Agenda) -> str:
    """O texto do arquivo: JSON compacto, com as chaves na ordem do contrato e uma quebra de linha
    no fim (mesmos dados, mesmos bytes). Levanta :class:`ErroDeContrato` se a agenda não for
    válida: quem coleta nunca grava dado fora do contrato (fica o arquivo anterior)."""
    dados = agenda.para_dict()
    erros = validar(dados)
    if erros:
        raise ErroDeContrato(erros)
    return json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + "\n"


def ler_dados(bruto: Any, *, lado: str | None = None, origem: str = "agenda") -> Agenda | None:
    """A agenda de um JSON já carregado, ou ``None`` quando ele não serve: não é um objeto, é de
    versão desconhecida, não passa na validação ou é do outro lado. Nunca levanta exceção."""
    try:
        if not isinstance(bruto, dict):
            log.warning("%s: formato inesperado (%s no lugar de objeto); a fonte fica ausente", origem, type(bruto).__name__)
            return None
        versao = bruto.get("versao")
        if isinstance(versao, bool) or versao != VERSAO:
            log.warning("%s: versão %r desconhecida (esta leitura entende a %d); a fonte fica ausente", origem, versao, VERSAO)
            return None
        erros = validar(bruto)
        if erros:
            log.warning("%s: formato inesperado (%d erros; o primeiro: %s); a fonte fica ausente", origem, len(erros), erros[0])
            return None
        agenda = Agenda.de_dict(bruto)
        if lado is not None and agenda.lado != lado:
            log.warning("%s: arquivo do lado %s onde se esperava %s; a fonte fica ausente", origem, agenda.lado, lado)
            return None
        return agenda
    except Exception:  # nada na leitura da agenda pode derrubar a edição
        log.warning("%s: não deu para ler a agenda; a fonte fica ausente", origem, exc_info=True)
        return None


def ler(caminho: Path | str, *, lado: str | None = None) -> Agenda | None:
    """Lê um ``agenda.json``. Devolve ``None`` (fonte ausente) quando o arquivo falta, está
    ilegível, tem formato inesperado ou é de versão desconhecida. Nunca levanta exceção."""
    try:
        texto = Path(caminho).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("%s: arquivo ilegível; a fonte fica ausente", caminho)
        return None
    try:
        bruto = json.loads(texto)
    except (ValueError, RecursionError):
        log.warning("%s: JSON ilegível; a fonte fica ausente", caminho)
        return None
    return ler_dados(bruto, lado=lado, origem=str(caminho))


def caminho_do_lado(data_dir: Path | str, lado: str) -> Path:
    """Onde mora o arquivo de um lado: ``<data>/congresso/agenda.json`` ou ``<data>/tribunais/agenda.json``."""
    if lado not in LADOS:
        raise ValueError(f"lado desconhecido: {lado!r}")
    return Path(data_dir) / lado / ARQUIVO


def ler_lado(data_dir: Path | str, lado: str) -> Agenda | None:
    """Lê o arquivo de um lado na pasta de dados (``None`` quando não serve; nunca levanta exceção)."""
    try:
        return ler(caminho_do_lado(data_dir, lado), lado=lado)
    except Exception:  # lado desconhecido ou caminho impossível: fonte ausente
        log.warning("agenda: não deu para ler o lado %r em %s; a fonte fica ausente", lado, data_dir)
        return None


def conteudo_igual(a: Agenda | None, b: Agenda | None) -> bool:
    """As duas agendas dizem o mesmo, fora os relógios (``gerado_em`` e ``coletado_em``): serve
    para a coleta não regravar o arquivo quando nada mudou."""
    if a is None or b is None:
        return a is b

    def sem_relogio(agenda: Agenda) -> Agenda:
        return replace(agenda, gerado_em="", fontes=tuple(replace(f, coletado_em=None) for f in agenda.fontes))

    return sem_relogio(a) == sem_relogio(b)


# ── mesclagem com o arquivo anterior ─────────────────────────────────────────────────────────


def _marcar(sessao: SessaoAgenda, dia_do_dado: str | None) -> SessaoAgenda:
    """A sessão mantida do arquivo anterior, com o dia do dado em que a fonte a trazia."""
    if sessao.como_pautado_em or not dia_do_dado:
        return sessao
    return replace(sessao, como_pautado_em=dia_do_dado)


def _fora_de_votacao(item: ItemAgenda) -> ItemAgenda:
    """O item que estava "em votação" numa sessão que deixou de estar em andamento volta ao
    estágio seguinte que ele tinha (a urgência, a pauta confirmada) ou a ``pautado``."""
    if item.estagio != "em_votacao":
        return item
    principal = estagio_principal(item.outros_estagios)
    return replace(item, estagio=principal, estagio_detalhe=None, outros_estagios=tuple(e for e in item.outros_estagios if e != principal))


def mesclar_com_anterior(novo: Agenda, anterior: Agenda | None, hoje: str, *, acumular_passadas: bool = True) -> Agenda:
    """Junta a coleta de agora com o arquivo anterior do MESMO lado. Duas regras:

    1. Fonte que falhou nunca vira zero: a instituição que não foi coletada por inteiro (alguma
       fonte dela com ``ok`` falso, ou nenhuma fonte dela em ``novo``) mantém as sessões do arquivo
       anterior que a coleta de agora não trouxe, com a data delas (``como_pautado_em``), e o
       estado anterior quando ``novo`` não traz o dela. A fonte que falhou herda ``data_dados`` e
       ``coletado_em`` do último dia bom. Sessão que veio como ``nao_coletada`` e que o arquivo
       anterior tinha com a pauta lida fica com a pauta anterior.
    2. Sessão já passada da semana corrente é mantida do arquivo anterior quando a fonte deixou de
       trazê-la (as pautas dos tribunais só listam o futuro), marcada com ``como_pautado_em``, até
       a virada da semana.

    A poda é a da janela: nada anterior à segunda-feira da semana de ``hoje`` nem posterior a 13
    dias depois dela sobrevive. ``hoje`` e a janela do resultado saem de ``hoje``. Sem arquivo
    anterior (ou com um do outro lado), devolve ``novo`` só podado. O coletor deve omitir de
    ``novo.estado`` a instituição cuja fonte falhou, para o estado anterior valer."""
    hoje = hoje[:10]
    de, ate = janela_de_coleta(hoje)

    def na_janela(sessao: SessaoAgenda) -> bool:
        return de <= sessao.data <= ate

    sessoes: dict[str, SessaoAgenda] = {s.id: s for s in novo.sessoes if na_janela(s)}
    if anterior is None or anterior.lado != novo.lado:
        return replace(novo, hoje=hoje, janela=(de, ate), sessoes=tuple(sessoes.values()))

    refeita = {inst: novo.refeita(inst) for inst in INSTITUICOES_DO_LADO.get(novo.lado, INSTITUICOES)}
    dia_do_dado = {inst: anterior.data_dados_de(inst) or anterior.hoje for inst in refeita}

    for antiga in anterior.sessoes:
        if not na_janela(antiga):
            continue  # a poda: virou a semana
        inst = antiga.instituicao
        agora = sessoes.get(antiga.id)
        if agora is not None:
            if agora.pauta == "nao_coletada" and antiga.pauta == "publicada":
                itens = antiga.itens if agora.situacao == "em_andamento" else tuple(_fora_de_votacao(i) for i in antiga.itens)
                mantida = replace(antiga, situacao=agora.situacao, hora=agora.hora, modalidade=agora.modalidade, data=agora.data, itens=itens)
                sessoes[antiga.id] = _marcar(mantida, dia_do_dado.get(inst))
            continue
        if not refeita.get(inst, False) or (acumular_passadas and antiga.data < hoje):
            sessoes[antiga.id] = _marcar(antiga, dia_do_dado.get(inst))

    fontes: dict[str, FonteAgenda] = {}
    for fonte in novo.fontes:
        velha = anterior.fonte(fonte.chave)
        if not fonte.ok and velha is not None:
            fonte = replace(fonte, data_dados=fonte.data_dados or velha.data_dados, coletado_em=fonte.coletado_em or velha.coletado_em)
        fontes[fonte.chave] = fonte
    for velha in anterior.fontes:  # a instituição que esta rodada nem tentou fica como estava
        if velha.chave not in fontes and not novo.fontes_de(velha.instituicao):
            fontes[velha.chave] = velha

    estado: dict[str, EstadoInstituicao] = {e.instituicao: e for e in novo.estado}
    for velho in anterior.estado:
        inst = velho.instituicao
        if inst in estado or refeita.get(inst, False):
            continue
        proximo = velho.proximo
        if proximo is not None and proximo.data <= ate:  # a semana virou: o próximo evento já cabe na janela
            if na_janela(proximo) and proximo.id not in sessoes:
                sessoes[proximo.id] = _marcar(proximo, dia_do_dado.get(inst))
            proximo = None
        estado[inst] = replace(velho, proximo=proximo)

    return replace(
        novo, hoje=hoje, janela=(de, ate), fontes=tuple(fontes.values()), sessoes=tuple(sessoes.values()), estado=tuple(estado.values()),
    )
