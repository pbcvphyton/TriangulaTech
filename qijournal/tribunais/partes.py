"""As partes: quem recorre × quem responde, o tipo de cada parte e o grupo de litigante.

O catálogo (``config/tribunais_partes.yaml``) diz quem é quem. A régua (analista jurídico §5):

1. **O papel decide antes de tudo.** Autoridade (o tribunal impetrado no HC, os juízos do conflito,
   o próprio STJ como requisitante do precatório, o Ministério da Justiça como autoridade central)
   e qualificador (representado por, herdeiros de, outro nome) não são litigantes: ficam com
   ``polo: None`` e sem grupo. No STJ e no STF quase tudo é recurso: ``ativo`` é quem recorre ou
   pede; ``passivo``, quem responde; ``outro``, o interessado (o Ministério Público como fiscal da
   lei, o assistente).
2. **Pessoa física e MEI nunca têm nome**, nem em memória: ``nome: None`` e ``grupo: None``. MEI é
   a pessoa jurídica com CPF no fim do nome (``stj.parte_preliminar`` já o trata como pessoa
   física). No STF, sem CNPJ, é pessoa física o nome sem nenhuma marca de instituição (LTDA, S/A,
   BANCO, ESTADO, MINISTÉRIO, SINDICATO...): na dúvida, o nome some.
3. **O grupo.** STJ: a **raiz do CNPJ** (8 dígitos, dígito verificador conferido; nunca um prefixo
   solto: 00394 começa a Fazenda Nacional, o Distrito Federal e o Ministério da Justiça); depois as
   famílias por nome (Estado, Ministério Público e Defensoria de cada UF; municípios), que têm uma
   raiz por ente; PJ fora do catálogo vira ``cnpj-<raiz>``. STF e partes do STJ sem CNPJ: o
   **nome**, pelos padrões do catálogo; fora dele, ``nome-<slug>`` (só PJ e ente).
4. **Nomes colados no STF** (``"UNIÃOFUNDAÇÃO NACIONAL DOS POVOS INDÍGENAS"``): o pedaço colado é
   lido por **busca de padrão** no texto (:meth:`Catalogo.reconhecer_texto`): um nome do catálogo
   vale quando começa no início do texto, depois de ``", "`` ou colado no fim de outro nome (sem
   espaço na emenda), e termina no fim, antes de ``", "``/``" - "``/``" ("`` ou colado no começo de
   outro. Cada entidade achada vira uma parte; o que sobra fica ``desconhecido``, sem nome.

``completar(processo)`` aplica tudo isso às partes de um :class:`~.modelo.Processo` (STJ e STF)
no lugar; ``tipificar(parte_bruta, trib)`` é o gancho de uma parte só (contrato §10). A
cobertura do reconhecimento no STF (processos com ao menos um litigante reconhecido em cada polo)
sai de :class:`CoberturaSTF`.

Nada aqui conta processos nem escolhe quem aparece com nome: isso é de ``contas.py`` (nome só com
20 processos ou mais no tribunal, ou 5 ou mais no acervo do ministro; processo em segredo nunca).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import stj as _stj
from .modelo import Parte, Processo, normalizar, slug

ARQUIVO = "tribunais_partes.yaml"  # em <raiz>/config/
TIPOS_LITIGANTE = ("pj", "ente")
POLOS_LITIGANTE = ("ativo", "passivo", "outro")


class ErroDoCatalogo(ValueError):
    """``config/tribunais_partes.yaml`` com chave ausente ou padrão inválido."""


# ── texto ────────────────────────────────────────────────────────────────────────────────────

# Marcas de instituição num nome sem CNPJ: sem nenhuma, é pessoa física (e o nome some). Nada que
# seja também sobrenome comum: "Câmara" só como Câmara Municipal, dos Deputados..., e nem "Sá" (SA no
# fim), "Brasileiro" ou "ME" (a firma individual é a própria pessoa, como o MEI). "S A" no fim fica:
# na fonte é sempre empresa.
_INSTITUICAO = re.compile(
    r"\b(?:LTDA|LIMITADA|EIRELI|S ?/ ?A|S\.A|S/C|EPP|LIMITED|LTD|LLC|INC|CORP|CORPORATION|GMBH|BANCO|BANK|CIA|COMPANHIA|"
    r"SINDICATO|ASSOCIACAO|ASSOC|FEDERACAO|CONFEDERACAO|CONFED|COOPERATIVA|FUNDACAO|INSTITUTO|INSTITUICAO|CONDOMINIO|EMPRESA|"
    r"ADVOGADOS|SOCIEDADE|PARTIDO|IGREJA|HOSPITAL|FACULDADE|UNIVERSIDADE|COLEGIO|ESCOLA|CLUBE|MASSA FALIDA|CONSORCIO|SEGURADORA|"
    r"SEGUROS|ASSOCIADOS|COMERCIO|INDUSTRIA|SERVICOS|PARTICIPACOES|ENGENHARIA|CONSTRUTORA|TRANSPORTES|AGROPECUARIA|"
    r"ADMINISTRADORA|INVESTIMENTOS|FUNDO|ORGANIZACAO|ENTIDADE|UNIMED|OAB|ORDEM DOS ADVOGADOS|DISTRIBUIDORA|ENERGIA|"
    r"SANEAMENTO|TELECOMUNICACOES|PREVIDENCIA|PREVIDENCIARIA|AGENCIA|AUTARQUIA|DEPARTAMENTO|SUPERINTENDENCIA|"
    r"SECRETARIA|MINISTERIO|PROCURADORIA|DEFENSORIA|ADVOCACIA|CONSELHO|CONGRESSO|SENADO|"
    r"CAMARA (?:MUNICIPAL|DOS DEPUTADOS|LEGISLATIVA|DE VEREADORES|DE COMERCIO)|ASSEMBLEIA|MESA D[AO]|"
    r"TRIBUNAL|PREFEITURA|MUNICIPIO|ESTADO D[OAE]|DISTRITO FEDERAL|UNIAO|FAZENDA|INSS|GOVERNO|POLICIA|COMANDO|"
    r"EXERCITO|MARINHA|AERONAUTICA|CENTRO|NUCLEO|REDE|MOVIMENTO|COLETIVO|COMISSAO|COMITE|ALIANCA|COORDENACAO|"
    r"COMUNIDADE|POVOS?|NACAO|NACIONAL|USINA|METALURGICA|CONCESSIONARIA|LOJAS|SUPERMERCADOS?|CIMENTO|SISTEMAS|PRODUTOS|"
    r"ACUCAREIRA|LATICINIOS|MINERACAO|S A$)\b"
)
# Entes públicos que a expressão do STJ (stj._ENTE) não pega (no STF aparecem mais).
_ENTE_EXTRA = re.compile(
    r"\b(?:CONGRESSO NACIONAL|MESA D[AO]|SENADO FEDERAL|CAMARA DOS DEPUTADOS|ASSEMBLEIA LEGISLATIVA|CAMARA MUNICIPAL|CAMARA DE VEREADORES|"
    r"SECRETARIA D|POLICIA (?:FEDERAL|CIVIL|MILITAR)|GOVERNO D|AGENCIA (?:NACIONAL|ESTADUAL|REGULADORA|GOIANA)|"
    r"DEPARTAMENTO (?:NACIONAL|ESTADUAL|DE)|SUPERINTENDENCIA D|CONSELHO (?:NACIONAL|FEDERAL|REGIONAL|SUPERIOR)|"
    r"INSTITUTO DE PREVIDENCIA|FUNDO UNICO DE PREVIDENCIA|MINISTERIO D[AOE]|COMANDO D[AOE]|EXERCITO|MARINHA DO BRASIL|"
    r"UNIVERSIDADE (?:FEDERAL|ESTADUAL)|FUNDACAO NACIONAL|FUNDACAO UNIVERSIDADE)"
)
def _marca_instituicao(resto: str) -> bool:
    return bool(_stj._PJ.search(resto) or _INSTITUICAO.search(resto) or _stj._ENTE.search(resto) or _ENTE_EXTRA.search(resto))


_PARTICULAS = frozenset({"DE", "DA", "DO", "DAS", "DOS", "E", "EM", "NA", "NO", "NAS", "NOS", "A", "O", "AS", "OS", "PARA",
                         "POR", "COM", "SOB", "DI", "DEL", "LA", "LE"})
_SIGLAS_PJ = frozenset({"SA", "S/A", "S.A", "S.A.", "LTDA", "ME", "EPP", "EIRELI", "S/C", "CIA"})


def nome_proprio(texto: str | None) -> str | None:
    """O nome de uma pessoa jurídica com a caixa ajustada para a página: ``"BANCO DO ESTADO DO
    RIO GRANDE DO SUL SA"`` → ``"Banco do Estado do Rio Grande do Sul SA"``. Palavras de até 4
    letras sem vogal ou com ponto/barra ficam como vieram (S/A, CEF, BRB); partículas em minúsculas."""
    if not texto:
        return None
    palavras = " ".join(str(texto).split()).split(" ")
    saida = []
    for i, w in enumerate(palavras):
        chave = normalizar(w)
        if (chave in _SIGLAS_PJ or any(c.isdigit() for c in w) or w.startswith("(")
                or (len(w) <= 4 and not re.search(r"[AEIOUÁÉÍÓÚÂÊÔÃÕaeiou]", w))):
            saida.append(w)
        elif i > 0 and chave in _PARTICULAS:
            saida.append(w.lower())
        elif "-" in w and len(w) > 1:
            saida.append("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-")))
        else:
            saida.append(w[:1].upper() + w[1:].lower())
    return " ".join(saida)


# ── o catálogo ───────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Litigante:
    """Um grupo de litigante: do catálogo, de uma família (``estado-sp``, ``mp-sp``,
    ``municipio-joinville``) ou feito na hora (``cnpj-<raiz>``, ``nome-<slug>``)."""

    id: str
    nome: str
    categoria: str
    tipo: str  # "ente" ou "pj"
    dinheiro_publico: bool = False
    catalogo: bool = True  # False: cnpj-<raiz> ou nome-<slug> (o nome vem do arquivo)


@dataclass
class _Padrao:
    regex: str
    grupo: str | None  # id fixo; None = família de municípios (o id sai do nome casado)
    familia: str | None = None
    contem: bool = False


def ancora(regex: str) -> str:
    """O trecho literal mais longo que todo texto casado pelo padrão contém (sem o que é opcional,
    as classes ``[...]`` e os grupos): ``"(?:FAZENDA )?ESTADO D[OAE] SAO PAULO"`` → ``"SAO PAULO"``.
    Vazio quando não há (o padrão é tentado sempre)."""
    s = regex.replace("\\ ", " ")
    anterior = None
    while anterior != s:  # grupos opcionais, de dentro para fora
        anterior = s
        s = re.sub(r"\((?:\?:|\?P<\w+>)?[^()]*\)[?*]", "|", s)
    anterior = None
    while anterior != s:  # grupos obrigatórios: o conteúdo não é literal garantido
        anterior = s
        s = re.sub(r"\((?:\?:|\?P<\w+>|\?=|\?!)?[^()]*\)", "|", s)
    s = re.sub(r"\[[^\]]*\][?*+]?", "|", s)
    s = re.sub(r"\\.", "|", s)
    s = re.sub(r".[?*]", "|", s)
    s = re.sub(r"[+{}^$.]", "|", s)
    trechos = [t.strip() for t in s.split("|")]
    return max(trechos, key=len) if trechos else ""


# Onde o nome de um município colado acaba: o começo de outra instituição grudado na última letra
# dele, sem espaço ("MUNICIPIO DE MINEIROSMUNICIPIO DE TAUBATE", "...SAO LEOPOLDOPREFEITO DO
# MUNICIPIO DE SAO LEOPOLDO", "...IBIPORAINSTITUTO DE PREVIDENCIA..."). Nome de cidade é palavra
# separada por espaço: uma dessas marcas colada no meio de uma palavra é a emenda do STF.
_EMENDA_NO_MUNICIPIO = re.compile(
    r"(?<=[A-Z0-9])(?=(?:FAZENDA PUBLICA DO )?MUNICIPIO D[EOA]S? |PREFEITURA MUNICIPAL D|PREFEIT[OA] (?:DO MUNICIPIO|MUNICIPAL)|"
    r"CAMARA MUNICIPAL|SECRETARI[OA] MUNICIPAL|SECRETARIA D|INSTITUTO D[EOA] |SERVICO AUTONOMO|FUNDACAO |AGENCIA |"
    r"SINDICATO D|ESTADO D[OAE] |MINISTERIO PUBLICO|DEFENSORIA PUBLICA|CAIXA ECONOMICA|BANCO D)"
)
# Um nome do catálogo seguido só da sigla ("FUNDACAO DOS ECONOMIARIOS FEDERAIS FUNCEF").
_SIGLA_NO_FIM = re.compile(r"\s*-?\s*[A-Z0-9]{2,10}\.?\s*")
# Onde um nome pode terminar dentro de um texto (sem contar a emenda colada).
_FIM_OK = (", ", ",", " - ", " (", "/", " /")


class Catalogo:
    """O catálogo de litigantes lido (``carregar_catalogo``). Guarda em cache o que já
    reconheceu: o mesmo nome aparece em milhares de processos."""

    def __init__(self, bruto: Mapping[str, Any]) -> None:
        self.versao = int(bruto.get("versao") or 1)
        self.categorias: dict[str, dict[str, Any]] = {}
        for cid, c in (bruto.get("categorias") or {}).items():
            tipo = str((c or {}).get("tipo") or "")
            if tipo not in TIPOS_LITIGANTE:
                raise ErroDoCatalogo(f"config/{ARQUIVO}: categorias.{cid}.tipo deve ser ente ou pj")
            self.categorias[str(cid)] = {"nome": str(c.get("nome") or cid), "tipo": tipo,
                                         "dinheiro_publico": bool(c.get("dinheiro_publico"))}
        for obrigatoria in ("outro_ente", "outra_pj", "municipio"):
            if obrigatoria not in self.categorias:
                raise ErroDoCatalogo(f"config/{ARQUIVO}: falta a categoria {obrigatoria}")
        self.classes_fora = frozenset(str(c) for c in (bruto.get("fora_de_quem_se_repete_classes") or ()))
        self.ufs: dict[str, str] = {str(k): normalizar(str(v)) for k, v in (bruto.get("ufs") or {}).items()}
        self.ufs_nome: dict[str, str] = {str(k): str(v) for k, v in (bruto.get("ufs_nome") or {}).items()}
        self.grupos: dict[str, Litigante] = {}
        self._por_raiz: dict[str, str] = {}
        padroes: list[_Padrao] = []
        for i, g in enumerate(bruto.get("grupos") or ()):
            gid = str(g.get("id") or "")
            if not gid or gid.startswith(("cnpj-", "nome-")):
                raise ErroDoCatalogo(f"config/{ARQUIVO}: grupos[{i}].id ausente ou reservado ({gid!r})")
            if gid in self.grupos:
                raise ErroDoCatalogo(f"config/{ARQUIVO}: grupos tem id repetido ({gid})")
            self._registrar(gid, str(g.get("nome") or gid), str(g.get("categoria") or ""), f"grupos[{i}]")
            for raiz in g.get("raizes") or ():
                self._raiz(str(raiz), gid)
            for p in g.get("padroes") or ():
                padroes.append(_Padrao(str(p), gid, contem=bool(g.get("contem"))))
        for f in bruto.get("familias") or ():
            padroes.extend(self._familia(f))
        self._padroes = padroes
        self._contem = [(re.compile(rf"\b(?:{p.regex})\b"), p.grupo) for p in padroes if p.contem]
        # Cada padrão com a sua âncora: o trecho literal que todo nome casado tem de conter. Só os
        # padrões cuja âncora aparece no texto são tentados (o texto de um polo do STF quase sempre
        # é nome de gente, sem âncora nenhuma).
        self._individuais: list[tuple[str, re.Pattern[str], _Padrao]] = []
        for p in padroes:
            if p.contem:
                continue
            try:
                compilado = re.compile(p.regex)
            except re.error as exc:
                raise ErroDoCatalogo(f"config/{ARQUIVO}: padrão inválido {p.regex!r} ({exc})") from exc
            self._individuais.append((ancora(p.regex), compilado, p))
        self._cache_nome: dict[str, str | None] = {}
        self._cache_texto: dict[tuple[str, bool], list[tuple[str, int, int]]] = {}
        self._cache_stf: dict[tuple[str, bool], list[tuple[str, str | None, str | None]]] = {}
        self.nomes_vistos: dict[str, Counter[str]] = {}

    # ── montagem ─────────────────────────────────────────────────────────────────────────────

    def _registrar(self, gid: str, nome: str, categoria: str, onde: str) -> Litigante:
        c = self.categorias.get(categoria)
        if c is None:
            raise ErroDoCatalogo(f"config/{ARQUIVO}: {onde}.categoria {categoria!r} não existe")
        lit = Litigante(id=gid, nome=nome, categoria=categoria, tipo=c["tipo"], dinheiro_publico=c["dinheiro_publico"])
        self.grupos[gid] = lit
        return lit

    def _raiz(self, raiz: str, gid: str) -> None:
        if not re.fullmatch(r"\d{8}", raiz):
            raise ErroDoCatalogo(f"config/{ARQUIVO}: a raiz {raiz!r} de {gid} não tem 8 dígitos")
        outro = self._por_raiz.get(raiz)
        if outro is not None and outro != gid:
            raise ErroDoCatalogo(f"config/{ARQUIVO}: a raiz {raiz} está em {outro} e em {gid}")
        self._por_raiz[raiz] = gid

    def _familia(self, f: Mapping[str, Any]) -> list[_Padrao]:
        fid = str(f.get("id") or "")
        categoria = str(f.get("categoria") or "")
        modelo_id = str(f.get("grupo") or "")
        modelo_nome = str(f.get("nome") or "")
        saida: list[_Padrao] = []
        if "{slug}" in modelo_id:  # municípios: o id sai do nome casado
            for p in f.get("padroes") or ():
                if "(?P<nome>" not in str(p):
                    raise ErroDoCatalogo(f"config/{ARQUIVO}: familias.{fid}: o padrão precisa do grupo (?P<nome>...)")
                fim = r"(?=\s*(?:$|,|\s-\s|\(|/))"
                saida.append(_Padrao(str(p).replace("(?P<nome>", "(?P<nome>") + fim, None, familia=fid))
            self._modelo_municipio = (modelo_id, modelo_nome, categoria)
            return saida
        for sigla, uf in self.ufs.items():
            gid = modelo_id.format(sigla=sigla.lower())
            uf_nome = self.ufs_nome.get(sigla, uf.title())
            if sigla == "DF":
                nome = "Distrito Federal" if fid == "estado" else modelo_nome.replace(" de {uf_nome}", " do {uf_nome}").format(
                    uf_nome="Distrito Federal")
            else:
                artigo = {"Acre": "do", "Amapá": "do", "Amazonas": "do", "Ceará": "do", "Espírito Santo": "do", "Maranhão": "do",
                          "Pará": "do", "Paraná": "do", "Piauí": "do", "Rio de Janeiro": "do", "Rio Grande do Norte": "do",
                          "Rio Grande do Sul": "do", "Tocantins": "do", "Bahia": "da", "Paraíba": "da"}.get(uf_nome, "de")
                nome = modelo_nome.format(artigo_uf=f"Estado {artigo}", uf_nome=uf_nome).replace(" de do ", " do ").replace(
                    " de da ", " da ")
                if fid != "estado":
                    nome = re.sub(r" de (" + re.escape(uf_nome) + r")$", rf" {artigo} \1", nome)
            if gid not in self.grupos:
                self._registrar(gid, nome, categoria, f"familias.{fid}")
            for p in f.get("padroes") or ():
                saida.append(_Padrao(str(p).format(uf=re.escape(uf)), gid, familia=fid))
            if sigla == "DF" and fid == "estado":
                for p in f.get("distrito_federal") or ():
                    saida.append(_Padrao(str(p), gid, familia=fid))
            for raiz in (f.get("raizes_por_uf") or {}).get(sigla, ()) or ():
                self._raiz(str(raiz), gid)
        return saida

    # ── consultas ────────────────────────────────────────────────────────────────────────────

    def litigante(self, gid: str | None) -> Litigante | None:
        """O grupo pelo id (os feitos na hora, ``cnpj-``/``nome-``, com o nome mais visto)."""
        if not gid:
            return None
        lit = self.grupos.get(gid)
        if lit is not None:
            vistos = self.nomes_vistos.get(gid)
            if lit.categoria == "municipio" and vistos:  # o nome com acento, como a fonte escreve
                melhor = min(vistos.items(), key=lambda kv: (-kv[1], kv[0]))[0]
                m = re.match(r"^\s*(?:FAZENDA P[ÚU]BLICA DO\s+)?(?:MUNIC[ÍI]PIO|PREFEITURA MUNICIPAL)\s+D[EOA]S?\s+(.+?)\s*(?:-.*)?$",
                             melhor, re.IGNORECASE)
                if m:
                    return Litigante(id=lit.id, nome=f"Município de {nome_proprio(m.group(1))}", categoria=lit.categoria,
                                     tipo=lit.tipo, dinheiro_publico=lit.dinheiro_publico)
            return lit
        vistos = self.nomes_vistos.get(gid)
        nome = nome_proprio(min(vistos.items(), key=lambda kv: (-kv[1], kv[0]))[0]) if vistos else gid
        tipo = "ente" if vistos and any(self.tipo_pelo_nome(n) == "ente" for n in vistos) else "pj"
        return Litigante(id=gid, nome=nome or gid, categoria="outro_ente" if tipo == "ente" else "outra_pj", tipo=tipo,
                         catalogo=False)

    def grupo_da_raiz(self, raiz: str | None) -> str | None:
        return self._por_raiz.get(raiz) if raiz else None

    def _municipio(self, nome: str) -> str:
        modelo_id, modelo_nome, categoria = self._modelo_municipio
        gid = modelo_id.format(slug=slug(nome))
        if gid not in self.grupos:
            self._registrar(gid, modelo_nome.format(nome=nome_proprio(nome)), categoria, "familias.municipio")
        return gid

    def _grupo_do_casamento(self, m: re.Match[str], p: _Padrao) -> str | None:
        if p.grupo is not None:
            return p.grupo
        nome = m.groupdict().get("nome")
        return self._municipio(nome) if nome else None

    def _candidatos(self, chave: str) -> list[tuple[str, re.Pattern[str], _Padrao]]:
        return [x for x in self._individuais if x[0] in chave]

    def reconhecer_nome(self, nome: str | None) -> str | None:
        """O grupo de um nome só (uma parte do STJ, uma parte do STF que não veio colada): o
        padrão casa do começo do nome e termina no fim ou antes de ``" - "``, ``", "``, ``" ("``
        (``"INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS"`` → ``inss``)."""
        chave = normalizar(nome)
        if not chave:
            return None
        if chave in self._cache_nome:
            return self._cache_nome[chave]
        achado: str | None = None
        melhor = -1
        for _a, compilado, p in self._candidatos(chave):
            m = compilado.match(chave)
            if m is not None and m.end() > melhor and (m.end() == len(chave) or chave.startswith(_FIM_OK, m.end())
                                                       or _SIGLA_NO_FIM.fullmatch(chave, m.end())):
                grupo = self._grupo_do_casamento(m, p)
                if grupo:
                    achado, melhor = grupo, m.end()
        if achado is None:
            achado = next((g for r, g in self._contem if r.search(chave)), None)
        self._cache_nome[chave] = achado
        return achado

    def reconhecer_texto(self, texto: str | None, colado: bool = False) -> list[tuple[str, int, int]]:
        """As entidades do catálogo num texto bruto de polo do STF, colado ou não:
        ``[(grupo, início, fim)]`` no texto normalizado. Um nome vale quando começa no início,
        depois de ``", "`` ou colado no fim de outro (sem espaço na emenda), e termina no fim,
        antes de ``", "``/``" - "``/``" ("`` ou colado no começo de outro. Nome curto (até 8
        letras, "UNIÃO", "INSS") precisa de uma ponta limpa: no meio de uma palavra não vale.

        O município é o único nome que sai do próprio texto (``municipio-<slug>``), e o STF cola a
        parte seguinte na última letra dele. O nome do município acaba na emenda com outra
        entidade achada no texto ou com outra instituição colada (:data:`_EMENDA_NO_MUNICIPIO`).
        ``colado``: o texto é a cabeça colada de um polo (``P1P2``); um município no começo dela que
        vai até o fim sem emenda à vista levaria o nome da parte seguinte (às vezes uma pessoa) e
        fica sem grupo (a parte sai sem nome)."""
        chave = normalizar(texto)
        if not chave:
            return []
        if (chave, colado) in self._cache_texto:
            return self._cache_texto[(chave, colado)]

        def vale(ini: int, fim: int) -> bool:
            inicio_limpo = ini == 0 or chave[ini - 1] == "," or chave[max(0, ini - 2):ini] == ", " or chave[max(0, ini - 3):ini] == " - "
            inicio_colado = ini > 0 and chave[ini - 1] not in " ,-("
            fim_limpo = fim == len(chave) or chave.startswith(_FIM_OK, fim)
            fim_colado = fim < len(chave) and chave[fim] not in " ,-()/"
            curto = fim - ini <= 8
            return (inicio_limpo or inicio_colado) and (fim_limpo or fim_colado) and (not curto or inicio_limpo or fim_limpo)

        casados: list[tuple[int, int, str]] = []
        municipais: list[tuple[re.Pattern[str], _Padrao]] = []
        for _a, compilado, p in self._candidatos(chave):
            if p.grupo is None:  # família dos municípios: depois, com as emendas à vista
                municipais.append((compilado, p))
                continue
            for m in compilado.finditer(chave):
                if vale(m.start(), m.end()):
                    grupo = self._grupo_do_casamento(m, p)
                    if grupo:
                        casados.append((m.start(), m.end(), grupo))
        fixos = sorted(ini for ini, _fim, _g in casados)
        for compilado, p in municipais:
            pos = 0
            while (m := compilado.search(chave, pos)) is not None:
                ini, fim, ini_nome = m.start(), m.end(), m.start("nome")
                emendas = [i for i in fixos if ini_nome < i < fim and chave[i - 1].isalnum()]
                e = _EMENDA_NO_MUNICIPIO.search(chave, ini_nome + 1, fim)
                if e is not None:
                    emendas.append(e.start())
                if emendas:
                    fim = min(emendas)
                    nome = chave[ini_nome:fim].strip()
                    pos = fim
                else:
                    nome = m.group("nome")
                    pos = max(fim, ini + 1)
                    if colado and ini == 0 and fim == len(chave):
                        continue  # o nome pode levar a parte seguinte, colada: sem grupo
                if nome and vale(ini, fim):
                    casados.append((ini, fim, self._municipio(nome)))
        # sobreposição: vence o mais longo ("ESTADO DO PARANA" e não "ESTADO DO PARA")
        achados: list[tuple[str, int, int]] = []
        for ini, fim, grupo in sorted(casados, key=lambda x: (-(x[1] - x[0]), x[0], x[2])):
            if all(fim <= a or ini >= b for _, a, b in achados):
                achados.append((grupo, ini, fim))
        for r, g in self._contem:
            for m in r.finditer(chave):
                if not any(a <= m.start() < b for _, a, b in achados):
                    achados.append((g, m.start(), m.end()))
        achados.sort(key=lambda x: x[1])
        self._cache_texto[(chave, colado)] = achados
        return achados

    # ── tipificação ──────────────────────────────────────────────────────────────────────────

    def _lembrar(self, gid: str, nome: str | None) -> None:
        if nome and gid.startswith(("cnpj-", "nome-", "municipio-")):
            self.nomes_vistos.setdefault(gid, Counter())[nome] += 1

    def completar_stj(self, parte: Parte) -> Parte:
        """Uma parte do STJ já com a tipificação preliminar (``stj.parte_preliminar``) → com o
        grupo e o tipo do catálogo. Autoridade, qualificador, pessoa física e desconhecido ficam
        como estão (sem grupo)."""
        tipo = parte.get("tipo")
        if tipo not in TIPOS_LITIGANTE or parte.get("polo") is None:
            parte["grupo"] = None
            return parte
        raiz = parte.get("cnpj_raiz")
        nome = parte.get("nome")
        gid = self.grupo_da_raiz(raiz)
        if gid is None and raiz:
            gid = self._familia_ou_contem(nome)
        if gid is None and not raiz:
            gid = self.reconhecer_nome(nome)
        if gid is None:
            if raiz:
                gid = f"cnpj-{raiz}"
            else:
                s = slug(nome or "")
                gid = f"nome-{s}" if s else None
        if gid is not None:
            lit = self.grupos.get(gid)
            if lit is not None:
                parte["tipo"] = lit.tipo  # type: ignore[typeddict-item]
            self._lembrar(gid, nome)
        parte["grupo"] = gid
        return parte

    def _familia_ou_contem(self, nome: str | None) -> str | None:
        """Para PJ com CNPJ fora das raízes do catálogo: só as famílias (cada Estado, MP,
        Defensoria e Município tem raiz própria) e os padrões "contém" (as Unimed)."""
        gid = self.reconhecer_nome(nome)
        if gid is None:
            return None
        lit = self.grupos.get(gid)
        if lit is not None and lit.categoria in ("estado", "mp", "defensoria", "municipio", "partido"):
            return gid
        if any(g == gid for _, g in self._contem):
            return gid
        return None

    def tipo_pelo_nome(self, nome: str | None) -> str:
        """Sem CNPJ: ``autoridade`` (órgão julgador, autoridade coatora), ``ente``, ``pj`` ou
        ``pf`` (nenhuma marca de instituição)."""
        chave = _stj.chave_rapida(nome)
        if not chave:
            return "desconhecido"
        if _stj._ORGAO_JULGADOR.match(chave):
            return "autoridade"
        if _stj.firma_individual(chave, marca=_marca_instituicao):
            return "pf"  # MEI e empresário individual com nome de gente: é a própria pessoa
        if _stj._PRIVADA_NO_COMECO.match(chave):
            return "pj"  # sindicato, associação, partido, OAB: entidade privada, mesmo com ente no nome
        if _stj._ENTE.search(chave) or _ENTE_EXTRA.search(chave):
            return "ente"
        if _stj._PJ.search(chave) or _INSTITUICAO.search(chave):
            return "pj"
        return "pf"

    def partes_stf(self, parte: Mapping[str, Any]) -> list[Parte]:
        """Uma parte do STF (do acervo ou da ata; ``tipo`` ``desconhecido`` ou ``autoridade``,
        ``nome`` bruto, ``colado``) → uma ou mais partes tipificadas. O pedaço colado vira uma
        parte por entidade do catálogo achada nele e, se sobrar texto, uma ``desconhecido`` sem
        nome (pode ser gente)."""
        polo = parte.get("polo")
        papel = str(parte.get("papel") or "")
        nome = parte.get("nome")
        colado = bool(parte.get("colado"))
        base = {k: v for k, v in parte.items() if k not in ("nome", "tipo", "grupo", "cnpj_raiz", "colado", "polo", "papel")}
        if parte.get("tipo") == "autoridade" or polo is None:
            return [Parte(polo=None, papel=papel, nome=nome if self.tipo_pelo_nome(nome) != "pf" else None,  # type: ignore[typeddict-item]
                          tipo="autoridade", grupo=None, cnpj_raiz=None)]
        chave = (str(nome or ""), colado)
        resolvido = self._cache_stf.get(chave)
        if resolvido is None:
            resolvido = self._resolver_stf(nome, colado)
            self._cache_stf[chave] = resolvido
        saida = []
        for tipo, gid, nome_saida in resolvido:
            p: dict[str, Any] = {"polo": polo if tipo not in ("autoridade",) else None, "papel": papel, "nome": nome_saida,
                                 "tipo": tipo, "grupo": gid, "cnpj_raiz": None, **base}
            if colado:
                p["colado"] = True
            if gid:
                self._lembrar(gid, nome_saida)
            saida.append(p)  # type: ignore[arg-type]
        return saida  # type: ignore[return-value]

    def _resolver_stf(self, nome: str | None, colado: bool) -> list[tuple[str, str | None, str | None]]:
        if not nome:
            return [("desconhecido", None, None)]
        if not colado:
            gid = self.reconhecer_nome(nome)
            if gid is not None:
                lit = self.grupos.get(gid)
                return [(lit.tipo if lit else "pj", gid, nome)]
            tipo = self.tipo_pelo_nome(nome)
            if tipo == "pf":
                return [("pf", None, None)]
            if tipo == "autoridade":
                return [("autoridade", None, nome)]
            s = slug(nome)
            return [(tipo, f"nome-{s}" if s else None, nome)]
        achados = self.reconhecer_texto(nome, colado=True)
        saida: list[tuple[str, str | None, str | None]] = []
        resto = normalizar(nome)
        for gid, ini, fim in achados:
            lit = self.grupos.get(gid)
            saida.append((lit.tipo if lit else "pj", gid, lit.nome if lit else None))
        for _gid, ini, fim in sorted(achados, key=lambda x: -x[1]):
            resto = resto[:ini] + " " + resto[fim:]
        if len(re.sub(r"[^A-Z0-9]", "", resto)) > 3 or not saida:
            saida.append(("desconhecido", None, None))
        return saida

    def completar(self, processo: Processo) -> Processo:
        """As partes do processo tipificadas e agrupadas, no lugar (STJ e STF)."""
        if processo.get("trib") == "stf":
            novas: list[Parte] = []
            for parte in processo.get("partes") or ():
                novas.extend(self.partes_stf(parte))
            processo["partes"] = novas
        else:
            for parte in processo.get("partes") or ():
                self.completar_stj(parte)
        return processo


def carregar_catalogo(raiz: Path | str | None = None) -> Catalogo:
    """Lê ``<raiz>/config/tribunais_partes.yaml`` (raiz padrão: a do repositório)."""
    raiz = Path(raiz) if raiz is not None else Path(__file__).resolve().parents[2]
    bruto = yaml.safe_load((raiz / "config" / ARQUIVO).read_text(encoding="utf-8")) or {}
    return Catalogo(bruto)


_PADRAO: Catalogo | None = None


def catalogo_padrao() -> Catalogo:
    """O catálogo do repositório, lido uma vez por processo."""
    global _PADRAO
    if _PADRAO is None:
        _PADRAO = carregar_catalogo()
    return _PADRAO


def tipificar(parte_bruta: Mapping[str, Any], trib: str, catalogo: Catalogo | None = None) -> Parte:
    """O gancho de ``stj.LeitorSTJ``/``stj.ler_acervo`` (``tipificar=``): uma parte bruta → a
    :class:`~.modelo.Parte` tipificada. STJ: ``{descTipoParte, nomeParte, numeroCNPJ}``; STF:
    uma parte de ``stf.py`` (a primeira, se o pedaço colado trouxer várias: para todas, use
    :meth:`Catalogo.completar`)."""
    cat = catalogo or catalogo_padrao()
    if trib == "stj":
        return cat.completar_stj(_stj.parte_preliminar(parte_bruta))
    return cat.partes_stf(parte_bruta)[0]


def completar(processo: Processo, catalogo: Catalogo | None = None) -> Processo:
    """:meth:`Catalogo.completar` com o catálogo do repositório."""
    return (catalogo or catalogo_padrao()).completar(processo)


# ── o que se conta de um processo ────────────────────────────────────────────────────────────


def litigantes(processo: Mapping[str, Any]) -> dict[str, set[str]]:
    """Os grupos de litigante do processo e em que polo cada um aparece: ``{grupo: {"ativo",
    "passivo", "outro"}}`` (só PJ e ente com grupo; autoridade, qualificador e gente, nunca)."""
    saida: dict[str, set[str]] = {}
    for parte in processo.get("partes") or ():
        gid = parte.get("grupo")
        if gid and parte.get("tipo") in TIPOS_LITIGANTE and parte.get("polo") in POLOS_LITIGANTE:
            saida.setdefault(gid, set()).add(parte["polo"])
    return saida


def tem_pessoa_fisica(processo: Mapping[str, Any]) -> bool:
    return any(p.get("tipo") == "pf" and p.get("polo") is not None for p in processo.get("partes") or ())


def dinheiro_publico(processo: Mapping[str, Any], catalogo: Catalogo) -> bool:
    """Um ente da medida "dinheiro público no recurso" (União, Fazenda Nacional, INSS, Estado ou
    DF, Município) como quem recorre ou quem responde."""
    for parte in processo.get("partes") or ():
        if parte.get("polo") in ("ativo", "passivo"):
            lit = catalogo.grupos.get(parte.get("grupo") or "")
            if lit is not None and lit.dinheiro_publico:
                return True
    return False


@dataclass
class CoberturaSTF:
    """Quantos processos do STF (sem os em segredo, que não trazem partes) têm ao menos um
    litigante reconhecido pelo catálogo em cada polo: a prova de que a busca por nome funciona."""

    processos: int = 0
    com_partes: Counter[str] = field(default_factory=Counter)  # polo → processos com alguma parte
    reconhecido: Counter[str] = field(default_factory=Counter)  # polo → com grupo do catálogo (ou família)
    agrupado: Counter[str] = field(default_factory=Counter)  # polo → com qualquer grupo (inclui nome-<slug>)
    no_texto: Counter[str] = field(default_factory=Counter)  # polo → entidade do catálogo achada no texto bruto do polo
    colados: int = 0  # partes que vieram coladas
    colados_reconhecidos: int = 0  # ... em que ao menos uma entidade foi achada

    def somar(self, processo: Mapping[str, Any], catalogo: Catalogo) -> None:
        if processo.get("segredo"):
            return
        self.processos += 1
        for polo, bruto in (processo.get("polos") or {}).items():
            if bruto and bruto.get("texto") and catalogo.reconhecer_texto(bruto["texto"]):
                self.no_texto[polo] += 1
        polos: dict[str, set[str]] = {}
        for parte in processo.get("partes") or ():
            polo = parte.get("polo")
            if polo not in ("ativo", "passivo"):
                continue
            estado = polos.setdefault(polo, set())
            estado.add("parte")
            gid = parte.get("grupo")
            if gid:
                estado.add("agrupado")
                if gid in catalogo.grupos:
                    estado.add("reconhecido")
        for polo, estado in polos.items():
            for marca, contador in (("parte", self.com_partes), ("reconhecido", self.reconhecido), ("agrupado", self.agrupado)):
                if marca in estado:
                    contador[polo] += 1

    def somar_colados(self, partes_brutas: Iterable[Mapping[str, Any]], catalogo: Catalogo) -> None:
        for parte in partes_brutas:
            if parte.get("colado"):
                self.colados += 1
                if catalogo.reconhecer_texto(parte.get("nome"), colado=True):
                    self.colados_reconhecidos += 1

    def para_meta(self) -> dict[str, Any]:
        return {
            "processos_sem_segredo": self.processos,
            "polos": {polo: {"com_partes": self.com_partes[polo], "reconhecido": self.reconhecido[polo],
                             "no_texto_bruto": self.no_texto[polo], "agrupado": self.agrupado[polo]}
                      for polo in ("ativo", "passivo")},
            "colados": {"pedacos": self.colados, "com_entidade": self.colados_reconhecidos},
        }


__all__ = [
    "Catalogo", "CoberturaSTF", "ErroDoCatalogo", "Litigante", "carregar_catalogo", "catalogo_padrao", "completar",
    "dinheiro_publico", "litigantes", "nome_proprio", "tem_pessoa_fisica", "tipificar",
]
