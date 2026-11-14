"""Montagem e gravação de ``data/congresso/``.

::

    data/congresso/
      base/cd-AAAA.json, sf-AAAA.json   votações nominais de cada ano (anos fechados não mudam)
      base/cd-exercicio.json            quando cada deputado esteve em exercício
      meta.json                         quando foi gerado, o que cobre e as réguas usadas
      resumo.json                       uma linha por parlamentar (tabela, mapa, fichas nas matérias)
      casa-cd.json, casa-sf.json        o painel de cada Casa: bancadas, temas, votações, fila, listas
      achados.json                      a leitura: o que os dados têm a dizer hoje
      fichas/<casa>-<id>.json           o dossiê de cada parlamentar (aberto sob demanda)

Tudo é montado em memória e só então gravado, arquivo a arquivo, de forma atômica.
Um arquivo só é reescrito quando o conteúdo muda (o commit diário leva só a
diferença). Uma fonte fora do ar não derruba a atualização: o que não veio fica
como estava e a falta vira aviso.
"""

from __future__ import annotations

import bisect
import json
import logging
import math
import os
import re
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .. import net, text
from ..net import Fetcher
from . import VERSAO, base, comissoes, cota, exercicio, ficha, fontes, frentes, jornal, materias, pauta, perfil, peso, projetos, regras as regras_mod, relevancia, temas
from .base import AnoVotos
from .ficha import Cadastro
from .fontes import Coletor
from .perfil import Perfil
from .projetos import Proposicao
from .regras import Regras

log = logging.getLogger(__name__)

PASTA = "congresso"
BASE = "base"
FICHAS = "fichas"
RESUMO = "resumo.json"
META = "meta.json"
ACHADOS = "achados.json"
FRENTES = "frentes-cd.json"  # os membros em exercício de cada frente de causa, com o voto de cada um no tema (lido quando a ficha da frente abre)
VOTOS = "votos-{casa}.json"  # os votos de cada um nas votações de conteúdo da legislatura (para o comparativo)
ULTIMAS = 14  # votações nominais mais recentes no painel de cada Casa
LISTA = 15  # tamanho das listas do painel
MINIMO_FIDELIDADE = 50  # votações com posição do partido para entrar nas listas de dissidência
MINIMO_BANCADA = {"cd": 5, "sf": 3}  # parlamentares em exercício para o partido entrar na matriz de temas
MINIMO_PAR = 20  # votações de conteúdo em que os dois partidos tiveram lado, para o par aparecer
JANELA_FURARAM = 240  # dias em que uma votação de texto principal conta em "passaram na frente"
RAPIDO = 120  # do protocolo ao voto em menos que isso: passou na frente
RACHAS_LISTA = 10  # votações de cada partido na lista dos rachas
JORNAL_VOTO = 8  # textos do jornal × votos, por pessoa
TEMA_PESSOA = 10  # votações esquerda × direita num tema para comparar alguém com ele mesmo
ALFA_TEMA = 0.01  # o desvio de um tema passa do acaso contando todas as comparações da Casa (Holm)
MINIMO_RACHA = 5  # votações de um partido com o outro polo para virar achado


@dataclass
class Opcoes:
    hoje: str  # AAAA-MM-DD, a data local do jornal
    completo: bool = False  # refaz também os anos fechados da base e relê todos os anos de proposições
    projetos_desde: int = 2011  # ano das proposições mais antigas acompanhadas na Câmara
    noticias_dias: int = 60  # janela do cruzamento com o banco do jornal
    validade_h: float = 20.0  # cache dos arquivos do dia


@dataclass
class Resultado:
    pasta: Path
    pessoas: dict[str, int] = field(default_factory=dict)
    votacoes: dict[str, int] = field(default_factory=dict)
    gravados: int = 0
    requisicoes: int = 0
    megabytes: float = 0.0
    avisos: list[str] = field(default_factory=list)


# ── arquivos ─────────────────────────────────────────────────────────────────


def pasta_de(data_dir: Path) -> Path:
    return Path(data_dir) / PASTA


def _json(dados: Any) -> str:
    return json.dumps(dados, ensure_ascii=False, separators=(",", ":"), sort_keys=False) + "\n"


def _gravar(caminho: Path, conteudo: str) -> bool:
    """Grava se o conteúdo mudou (arquivo temporário + ``os.replace``). Devolve se gravou."""
    caminho = Path(caminho)
    try:
        if caminho.read_text(encoding="utf-8") == conteudo:
            return False
    except OSError:
        pass
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=caminho.parent, prefix=f".{caminho.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(conteudo)
        os.chmod(tmp, 0o644)
        os.replace(tmp, caminho)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


def ler_json(caminho: Path) -> Any | None:
    try:
        return json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ── base de votos ────────────────────────────────────────────────────────────


def atualizar_base(
    coletor: Coletor, regras: Regras, pasta: Path, opcoes: Opcoes, avisos: list[str]
) -> tuple[dict[str, list[AnoVotos]], int]:
    """A base de votações das duas Casas, com o ano corrente refeito (e os fechados, só
    se faltarem, se foram gravados num formato mais antigo ou com ``opcoes.completo``).
    Devolve ``({casa: [anos]}, gravados)``."""
    corrente = int(opcoes.hoje[:4])
    tc, ts = temas.TemasCamara(coletor, regras), temas.TemasSenado(coletor, regras)
    resultado: dict[str, list[AnoVotos]] = {"cd": [], "sf": []}
    gravados = 0
    for casa in ("cd", "sf"):
        for ano in regras.anos():
            if ano > corrente:
                continue
            existente = base.ler(pasta, casa, ano)
            if existente is not None and ano < corrente and not opcoes.completo and existente.formato >= base.FORMATO:
                resultado[casa].append(existente)
                continue
            validade = opcoes.validade_h if ano == corrente else 24 * 30
            try:
                if casa == "cd":
                    novo = base.camara_ano(coletor, ano, temas=tc, validade_h=validade)
                else:
                    fim = opcoes.hoje if ano == corrente else None
                    novo = base.senado_ano(coletor, ano, temas=ts, validade_h=validade, fim=fim)
            except Exception as exc:  # noqa: BLE001 — a fonte fora do ar não apaga o que já existe
                avisos.append(f"{base.CASAS[casa]} {ano}: votações não atualizadas ({exc})")
                log.warning("Congresso: base %s-%d não atualizada (%s)", casa, ano, exc)
                if existente is not None:
                    resultado[casa].append(existente)
                continue
            if existente is not None and len(novo.votacoes) < len(existente.votacoes) * 0.9:
                avisos.append(
                    f"{base.CASAS[casa]} {ano}: a fonte trouxe {len(novo.votacoes)} votações, menos que as "
                    f"{len(existente.votacoes)} da base; a base foi mantida"
                )
                resultado[casa].append(existente)
                continue
            novo.atualizado = max((v.data for v in novo.votacoes), default="")
            if novo.votacoes or existente is None:
                gravados += _gravar(base.caminho(pasta, casa, ano), base.serializar(novo))
            resultado[casa].append(novo)
    return resultado, gravados


# ── cadastros ────────────────────────────────────────────────────────────────


def _cadastros_camara(coletor: Coletor, perfis: Mapping[int, Perfil], legislatura: int, avisos: list[str]) -> dict[int, Cadastro]:
    """Quem está em exercício hoje (API) e quem votou nesta legislatura e não está mais."""
    cadastros: dict[int, Cadastro] = {}
    try:
        for d in fontes.camara_api(coletor, "deputados", ordem="ASC", ordenarPor="nome"):
            pid = fontes.inteiro(d.get("id"))
            if pid is None:
                continue
            cadastros[pid] = Cadastro(
                id=pid,
                casa="cd",
                nome=str(d.get("nome") or "").strip(),
                partido=base.partido(d.get("siglaPartido")),
                uf=str(d.get("siglaUf") or "").strip(),
                exercicio=True,
                foto=ficha.FOTO_CAMARA.format(id=pid),
                pagina=ficha.PAGINA_CAMARA.format(id=pid),
            )
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"Câmara: lista dos deputados em exercício indisponível ({exc})")
    for pid, p in perfis.items():
        if pid in cadastros or legislatura not in p.legislaturas:
            continue
        cadastros[pid] = Cadastro(
            id=pid,
            casa="cd",
            nome=p.nome,
            partido=p.partido,
            uf=p.uf,
            exercicio=False,
            foto=ficha.FOTO_CAMARA.format(id=pid),
            pagina=ficha.PAGINA_CAMARA.format(id=pid),
        )
    return cadastros


def _cadastros_senado(coletor: Coletor, perfis: Mapping[int, Perfil], legislatura: int, avisos: list[str]) -> dict[int, Cadastro]:
    cadastros: dict[int, Cadastro] = {}
    try:
        bruto = fontes.senado_api(coletor, "senador/lista/atual")
        raiz = bruto.get("ListaParlamentarEmExercicio") or {}
        for item in fontes.lista((raiz.get("Parlamentares") or {}).get("Parlamentar")):
            ident = item.get("IdentificacaoParlamentar") or {}
            pid = fontes.inteiro(ident.get("CodigoParlamentar"))
            if pid is None:
                continue
            mandato = item.get("Mandato") or {}
            extra = {}
            bloco = (ident.get("Bloco") or {}).get("NomeBloco")
            if bloco:
                extra["bloco"] = str(bloco)
            cadastros[pid] = Cadastro(
                id=pid,
                casa="sf",
                nome=str(ident.get("NomeParlamentar") or "").strip(),
                partido=base.partido(ident.get("SiglaPartidoParlamentar")),
                uf=str(ident.get("UfParlamentar") or "").strip(),
                exercicio=True,
                foto=str(ident.get("UrlFotoParlamentar") or ficha.FOTO_SENADO.format(id=pid)).replace("http://", "https://"),
                pagina=str(ident.get("UrlPaginaParlamentar") or ficha.PAGINA_SENADO.format(id=pid)).replace("http://", "https://"),
                condicao=str(mandato.get("DescricaoParticipacao") or "").strip(),
                extra=extra,
            )
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"Senado: lista dos senadores em exercício indisponível ({exc})")
    for pid, p in perfis.items():
        if pid in cadastros or legislatura not in p.legislaturas:
            continue
        cadastros[pid] = Cadastro(
            id=pid,
            casa="sf",
            nome=p.nome,
            partido=p.partido,
            uf=p.uf,
            exercicio=False,
            foto=ficha.FOTO_SENADO.format(id=pid),
            pagina=ficha.PAGINA_SENADO.format(id=pid),
        )
    return cadastros


# ── painel da Casa ───────────────────────────────────────────────────────────


def _media(valores: Iterable[float | None]) -> float | None:
    limpos = [v for v in valores if v is not None]
    return sum(limpos) / len(limpos) if limpos else None


def _medias_por_partido(fichas: Iterable[Mapping[str, Any]], legislatura: int, minimo: int) -> dict[str, dict[str, Any]]:
    """Média, entre os que estão em exercício, do saldo, do governismo e da presença de cada
    partido. No saldo, quem trocou de partido entra só com os votos dados no partido atual
    (com ``minimo`` votações ou mais)."""
    por: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for f in fichas:
        if f.get("exercicio") and f.get("partido"):
            por[f["partido"]].append(f)
    medias = {}
    for sigla, lista in por.items():
        leg = [(f.get("leg") or {}).get(str(legislatura)) or {} for f in lista]
        governo = [next(reversed(list(f["gov"].values())))["pct"] for f in lista if f.get("gov")]
        medias[sigla] = {
            "n": len(lista),
            "saldo": ficha.pct(_media(ficha.saldo_no_partido(f, minimo) for f in lista)),
            "gov": ficha.pct(_media(governo)),
            "pres": ficha.pct(_media((r.get("pres") or {}).get("pct") for r in leg)),
        }
    return medias


def _matriz_de_temas(anos: Iterable[AnoVotos], regras: Regras, legislatura: int, siglas: set[str]) -> dict[str, Any]:
    """Partido × tema: o saldo de cada bancada nas votações de conteúdo divisivas de cada tema."""
    conta: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    divisivas: Counter[str] = Counter()
    for ano in anos:
        for k, v in enumerate(ano.votacoes):
            leg = regras.legislatura_em(v.data)
            if not v.plenario or v.secreta or leg is None or leg.numero != legislatura or v.tipo not in perfil.CONTEUDO:
                continue
            leitura = perfil.ler_votacao(v, [p.partido_em(k) for p in ano.pessoas], regras)
            if not leitura.divisiva:
                continue
            for tema in v.temas or ["sem_tema"]:
                divisivas[tema] += 1
            divisivas["todas"] += 1
            for sigla, (lado, _parte, _n) in leitura.partidos.items():
                if sigla not in siglas:
                    continue
                i = 0 if lado == leitura.direita else 1
                for tema in [*(v.temas or ["sem_tema"]), "todas"]:
                    conta[sigla][tema][i] += 1
    ordem = [t for t, _ in divisivas.most_common() if t not in ("todas", "sem_tema")]
    partidos = {}
    for sigla, por_tema in conta.items():
        linha = {
            tema: [round(100 * (d - e) / (d + e)), d + e] for tema, (d, e) in por_tema.items() if d + e >= 5 and tema != "sem_tema"
        }
        if linha:  # menos de 5 votações divisivas em tudo: o partido não tem linha
            partidos[sigla] = linha
    return {
        "temas": [{"id": t, "nome": regras.nome_do_tema(t), "n": divisivas[t]} for t in ordem],
        "divisivas": divisivas["todas"],
        "partidos": partidos,
    }


def _convivencia(
    anos: Iterable[AnoVotos], regras: Regras, legislatura: int, bancadas: list[Mapping[str, Any]], siglas: set[str]
) -> dict[str, Any]:
    """Partido × partido: em quantas das votações de conteúdo em que os dois tiveram lado os
    dois ficaram do mesmo lado (:func:`perfil.convivencia`). Os partidos vão na ordem do
    saldo (da esquerda para a direita); par com menos de ``MINIMO_PAR`` votações fica vazio."""
    bruto = perfil.convivencia(anos, regras, legislatura)
    saldos = {b["sigla"]: b.get("saldo") for b in bancadas}
    ordem = sorted((s for s in siglas if s in saldos), key=lambda s: (saldos[s] is None, saldos[s] if saldos[s] is not None else 0, s))
    pares = bruto.pop("pares")

    def celula(a: str, b: str) -> list[int] | None:
        if a == b:
            return None
        iguais, total = pares.get((a, b) if a < b else (b, a), (0, 0))
        return [round(100 * iguais / total), total] if total >= MINIMO_PAR else None

    # Partido sem nenhum par com votações suficientes (bancada pequena) não tem linha.
    ordem = [a for a in ordem if any(celula(a, b) for b in ordem)]
    return {**bruto, "partidos": ordem, "matriz": [[celula(a, b) for b in ordem] for a in ordem]}


def _rachas(anos: Iterable[AnoVotos], regras: Regras, legislatura: int, lista: int = RACHAS_LISTA) -> dict[str, Any]:
    """Onde cada polo racha: as votações de conteúdo em que os dois polos se opuseram,
    mas um partido de um polo votou, fechado, com o outro (o PSOL com o PL contra o PT;
    o NOVO com o PT contra o PL). Ficam fora do saldo e aparecem aqui, com o tema e a
    orientação do governo de cada uma."""
    por: dict[str, dict[str, Any]] = {}
    opostas = 0
    for ano in anos:
        for k, v in enumerate(ano.votacoes):
            leg = regras.legislatura_em(v.data)
            if not v.plenario or v.secreta or leg is None or leg.numero != legislatura or v.tipo not in perfil.CONTEUDO:
                continue
            leitura = perfil.ler_votacao(v, [p.partido_em(k) for p in ano.pessoas], regras)
            if not leitura.opostos:
                continue
            opostas += 1
            for sigla, com in leitura.racha.items():
                lado, parte, _n = leitura.partidos[sigla]
                b = por.setdefault(
                    sigla,
                    {"sigla": sigla, "polo": "direita" if com == "esquerda" else "esquerda", "com": com, "n": 0, "contra_governo": 0, "com_governo": 0, "temas": Counter(), "itens": []},
                )
                b["n"] += 1
                if leitura.governo:
                    b["contra_governo" if lado != leitura.governo else "com_governo"] += 1
                b["temas"].update(v.temas)
                b["itens"].append(
                    {
                        "id": v.id,
                        "d": v.data,
                        "t": v.tipo,
                        "p": v.prop,
                        "e": text.truncate(v.ementa or v.descricao, 200),
                        "tm": v.temas,
                        "lado": lado,
                        "parte": round(100 * parte),
                        "polo": getattr(leitura, b["polo"]),
                        "gov": leitura.governo,
                        **({"ap": 1 if v.aprovada else 0} if v.aprovada is not None else {}),
                    }
                )
    partidos = []
    for b in sorted(por.values(), key=lambda b: (-b["n"], b["sigla"])):
        b["itens"].sort(key=lambda i: (i["d"], i["id"]), reverse=True)
        b["itens"] = b["itens"][:lista]
        b["temas"] = [{"id": t, "nome": regras.nome_do_tema(t), "n": n} for t, n in b["temas"].most_common()]
        partidos.append(b)
    return {"opostas": opostas, "partidos": partidos}


def votos_da_legislatura(anos: Iterable[AnoVotos], regras: Regras, legislatura: int, chaves: Mapping[int, str], casa: str) -> dict[str, Any]:
    """Os votos de cada um nas votações de conteúdo (texto principal e destaques) da
    legislatura, abertas e de Plenário, em ordem: um caractere por votação (os códigos da
    base). É o que o comparativo usa para dizer quanto dois parlamentares votam juntos.
    ``chaves``: id → chave da ficha (``cd-123``)."""
    colunas: list[tuple[AnoVotos, int]] = []
    for ano in anos:
        for k, v in enumerate(ano.votacoes):
            leg = regras.legislatura_em(v.data)
            if v.plenario and not v.secreta and leg is not None and leg.numero == legislatura and v.tipo in perfil.CONTEUDO:
                colunas.append((ano, k))
    colunas.sort(key=lambda c: (c[0].votacoes[c[1]].data, c[0].votacoes[c[1]].hora, c[0].votacoes[c[1]].id))
    votacoes = []
    for ano, k in colunas:
        v = ano.votacoes[k]
        leitura = perfil.ler_votacao(v, [p.partido_em(k) for p in ano.pessoas], regras)
        # [id, data, tipo, proposição, ementa, temas, dividiu esquerda e direita (1/0), turno da PEC (0: não diz)]
        votacoes.append([v.id, v.data, v.tipo, v.prop, text.truncate(v.ementa or v.descricao, 140), list(v.temas), 1 if leitura.divisiva else 0, turno(v.descricao) or 0])
    indices = {id(ano): ano.indice() for ano, _k in colunas}
    votos = {}
    partidos: dict[str, list[list[Any]]] = {}
    for pid, chave in sorted(chaves.items(), key=lambda kv: kv[1]):
        linha = "".join(
            ano.votacoes[k].votos[indices[id(ano)][pid]] if pid in indices[id(ano)] else base.SEM_REGISTRO for ano, k in colunas
        )
        if not linha.strip(base.SEM_REGISTRO):
            continue
        votos[chave] = linha
        # O partido no dia de cada votação: [índice da votação em que passa a valer, sigla]. Quem
        # trocou de legenda aparece, em cada votação, no partido em que estava naquele dia.
        trechos: list[list[Any]] = []
        for coluna, (ano, k) in enumerate(colunas):
            i = indices[id(ano)].get(pid)
            if i is None:
                continue
            sigla = ano.pessoas[i].partido_em(k)
            if sigla and (not trechos or trechos[-1][1] != sigla):
                trechos.append([coluna, sigla])
        if trechos:
            partidos[chave] = trechos
    return {"v": VERSAO, "casa": casa, "legislatura": legislatura, "votacoes": votacoes, "votos": votos, "partidos": partidos}


def membros_das_frentes(
    painel: dict[str, Any],
    fichas: Iterable[Mapping[str, Any]],
    saldos_tema: Mapping[int, Mapping[str, tuple[float, int]]],
    saldo_geral: Mapping[int, tuple[float, int]],
) -> dict[str, Any]:
    """A ficha de cada frente de causa do ``painel`` (o que a página lê quando alguém abre uma
    frente): o que o painel diz dela e os membros em exercício, um por linha: ``[chave, saldo no
    tema da frente (ou None), votações do saldo, % das vezes com o coordenador (ou None),
    votações em comum, coordena (0/1)]``, do saldo menor para o maior (sem saldo, no fim)."""
    membros: dict[int, list[list[Any]]] = {c["id"]: [] for c in painel.get("causas") or []}
    temas = {c["id"]: c.get("tema") or frentes.TODAS for c in painel.get("causas") or []}
    for f in fichas:
        if not f.get("exercicio"):
            continue
        for c in (f.get("frentes") or {}).get("causas") or []:
            if c["id"] not in membros:
                continue
            tema = temas[c["id"]]
            saldo = saldo_geral.get(f["id"]) if tema == frentes.TODAS else (saldos_tema.get(f["id"]) or {}).get(tema)
            membros[c["id"]].append([f["k"], saldo[0] if saldo else None, saldo[1] if saldo else 0, c.get("com"), c.get("n") or 0, 1 if c.get("coord") else 0])
    detalhe = {}
    for c in painel.get("causas") or []:
        lista = sorted(membros[c["id"]], key=lambda m: (m[1] is None, m[1] if m[1] is not None else 0, m[0]))
        detalhe[str(c["id"])] = {**c, "membros_total": c.get("membros"), "membros": lista}
    return detalhe


def _frentes_por_linha(dados: Mapping[str, Any]) -> str:
    """O arquivo dos membros das frentes com uma frente por linha."""
    frentes_ = ",\n".join(f"{json.dumps(k)}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}" for k, v in dados["frentes"].items())
    cabeca = json.dumps({k: v for k, v in dados.items() if k != "frentes"}, ensure_ascii=False, separators=(",", ":"))[:-1]
    return f'{cabeca},"frentes":{{\n{frentes_}\n}}}}\n'


def _json_por_linha(dados: Mapping[str, Any]) -> str:
    """O arquivo de votos com uma votação e uma pessoa por linha (diferenças pequenas no git)."""
    votacoes = ",\n".join(json.dumps(v, ensure_ascii=False, separators=(",", ":")) for v in dados["votacoes"])
    votos = ",\n".join(f"{json.dumps(k)}:{json.dumps(v)}" for k, v in dados["votos"].items())
    partidos = ",\n".join(f"{json.dumps(k)}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}" for k, v in (dados.get("partidos") or {}).items())
    cabeca = json.dumps({k: v for k, v in dados.items() if k not in ("votacoes", "votos", "partidos")}, ensure_ascii=False, separators=(",", ":"))[:-1]
    return f'{cabeca},"votacoes":[\n{votacoes}\n],"votos":{{\n{votos}\n}},"partidos":{{\n{partidos}\n}}}}\n'


def _estatisticas(anos: Iterable[AnoVotos], regras: Regras, legislatura: int) -> dict[str, Any]:
    total = conteudo = divisivas = governo = secretas = 0
    datas: list[str] = []
    tipos: Counter[str] = Counter()
    for ano in anos:
        for k, v in enumerate(ano.votacoes):
            leg = regras.legislatura_em(v.data)
            if not v.plenario or leg is None or leg.numero != legislatura:
                continue
            total += 1
            datas.append(v.data)
            tipos[v.tipo] += 1
            if v.secreta:
                secretas += 1
                continue
            leitura = perfil.ler_votacao(v, [p.partido_em(k) for p in ano.pessoas], regras)
            governo += bool(leitura.governo)
            if v.tipo in perfil.CONTEUDO:
                conteudo += 1
                divisivas += leitura.divisiva
    return {
        "total": total,
        "secretas": secretas,
        "conteudo": conteudo,
        "divisivas": divisivas,
        "com_governo": governo,
        "primeira": min(datas, default=""),
        "ultima": max(datas, default=""),
        "tipos": dict(tipos.most_common()),
    }


def _ultimas(anos: list[AnoVotos], regras: Regras, nomes: Mapping[int, str], quantas: int = ULTIMAS) -> list[dict[str, Any]]:
    """As votações nominais de Plenário mais recentes, com quem votou contra o próprio partido."""
    candidatas = [(ano, k, v) for ano in anos for k, v in enumerate(ano.votacoes) if v.plenario]
    candidatas.sort(key=lambda t: (t[2].data, t[2].hora, t[2].id), reverse=True)
    saida = []
    for ano, k, v in candidatas[:quantas]:
        r = perfil.resumo_da_votacao(v, ano, k, regras)
        for p in r["partidos"]:
            p["contra"] = [{"id": pid, "nome": nomes.get(pid, "")} for pid in p["contra"][:12]] if p["coesao"] >= 80 else len(p["contra"])
        r["partidos"] = [p for p in r["partidos"] if p["votos"] >= 3][:14]
        if v.prop and regras.apelido_de(v.prop):
            r["apelido"] = regras.apelido_de(v.prop)
        saida.append(r)
    return saida


def _furaram(anos: list[AnoVotos], props: Mapping[int, Proposicao], hoje: str) -> dict[str, Any]:
    """Do protocolo ao voto: as matérias aprovadas no Plenário nos últimos meses, pelo
    tempo que levaram desde a apresentação, e quantas passaram na frente (menos de
    ``RAPIDO`` dias), com a urgência quando houve."""
    desde = (date.fromisoformat(hoje) - timedelta(days=JANELA_FURARAM)).isoformat()
    urgencias = {v.prop for ano in anos for v in ano.votacoes if v.plenario and v.tipo == base.URGENCIA and v.aprovada and v.prop}
    vistas: dict[str, dict[str, Any]] = {}
    for ano in anos:
        for v in ano.votacoes:
            if not v.plenario or v.tipo != base.MERITO or not v.aprovada or v.data < desde or not v.prop or v.prop in vistas:
                continue
            prop = props.get(v.prop_id or -1)
            apresentada = v.apresentada or (prop.apresentada if prop else "")
            dias = projetos.dias_entre(apresentada, v.data) if apresentada else None
            if dias is None or dias < 0:
                continue
            vistas[v.prop] = {
                "p": v.prop,
                "e": v.ementa,
                "d": v.data,
                "da": apresentada,
                "dias": dias,
                "urg": 1 if v.prop in urgencias else 0,
                "tm": v.temas,
                "autoria": (prop.autoria if prop and prop.autoria else ""),
                "id": v.id,
            }
    itens = sorted(vistas.values(), key=lambda i: i["dias"])
    dias = sorted(i["dias"] for i in itens)
    return {
        "desde": desde,
        "aprovadas": len(itens),
        "mediana_dias": dias[len(dias) // 2] if dias else None,
        "rapidas_n": sum(1 for d in dias if d < RAPIDO),
        "com_urgencia": sum(1 for i in itens if i["urg"]),
        "rapidas": [i for i in itens if i["dias"] < RAPIDO][:LISTA],
    }


def _binomial_bicaudal(k: int, n: int, p: float) -> float:
    """A chance, sob o acaso, de um resultado tão ou mais improvável que ``k`` em ``n``
    sorteios de chance ``p`` (exata, dois lados: soma as chances que não passam a de ``k``)."""
    if n <= 0:
        return 1.0
    chances = [math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(n + 1)]
    limite = chances[k] * (1 + 1e-9)
    return min(1.0, sum(c for c in chances if c <= limite))


def _holm(pvalores: Mapping[Any, float], alfa: float) -> set[Any]:
    """As chaves que passam do acaso contando todas as comparações (Holm)."""
    m = len(pvalores)
    passam = set()
    for i, (chave, p) in enumerate(sorted(pvalores.items(), key=lambda kv: kv[1])):
        if p > alfa / (m - i):
            break
        passam.add(chave)
    return passam


def _temas_testados(f: Mapping[str, Any], leg: str) -> list[tuple[Mapping[str, Any], float, bool]]:
    """Para quem tem lado definido (saldo além de ±60): cada tema com votações bastantes,
    a chance de o voto naquele tema ser tão diferente do resto dos votos da pessoa só por
    acaso (binomial exata) e se o tema fica do lado oposto ao geral (saldo além de ±20)."""
    campo = f.get("campo") or {}
    eixo = ((f.get("leg") or {}).get(leg) or {}).get("eixo") or {}
    if campo.get("saldo") is None or abs(campo["saldo"]) < 60 or not eixo.get("n"):
        return []
    saida = []
    for t in f.get("temas") or []:
        et = t.get("eixo") or {}
        n_t = et.get("n") or 0
        fora_n = eixo["n"] - n_t
        if n_t < TEMA_PESSOA or fora_n < TEMA_PESSOA or et.get("saldo") is None:
            continue
        p_fora = min(0.99, max(0.01, (eixo["dir"] - et["dir"]) / fora_n))  # chance de votar com a direita fora do tema
        oposto = (et["saldo"] > 0) != (campo["saldo"] > 0) and abs(et["saldo"]) >= 20
        saida.append((t, _binomial_bicaudal(et["dir"], n_t, p_fora), oposto))
    return saida


def marcar_temas_que_viram(fichas: Iterable[dict[str, Any]], legislatura: int) -> None:
    """No dossiê, o tema em que a pessoa fica do lado oposto ao dela marca ``vira``: só o
    que passa do acaso contando os temas dela (Holm a 1%)."""
    leg = str(legislatura)
    for f in fichas:
        testes = _temas_testados(f, leg)
        passam = _holm({t["t"]: p for t, p, _o in testes}, ALFA_TEMA)
        for t, _p, oposto in testes:
            if oposto and t["t"] in passam:
                t["vira"] = 1


def mudam_conforme_tema(fichas: Iterable[Mapping[str, Any]], legislatura: int) -> list[dict[str, Any]]:
    """Quem vota de um lado em quase tudo e do outro num tema: o saldo do tema do lado
    oposto ao geral, com o tema contra o resto dos votos da própria pessoa passando do
    acaso (binomial exata, Holm a 1% sobre todas as pessoas × temas comparados)."""
    leg = str(legislatura)
    candidatos: dict[tuple[str, str], dict[str, Any]] = {}
    pvalores: dict[tuple[str, str], float] = {}
    for f in fichas:
        if not f.get("exercicio"):
            continue
        campo = f.get("campo") or {}
        for t, p, oposto in _temas_testados(f, leg):
            chave = (f["k"], t["t"])
            pvalores[chave] = p
            if oposto:
                et = t["eixo"]
                candidatos[chave] = _linha_de_lista(
                    f, saldo=campo["saldo"], tema=t["t"], tema_nome=t.get("nome") or t["t"], tema_saldo=et["saldo"], n=et["n"],
                    com="esquerda" if et["saldo"] < 0 else "direita",
                )
    passam = _holm(pvalores, ALFA_TEMA)
    saida = [dict(c, p=pvalores[chave]) for chave, c in candidatos.items() if chave in passam]
    saida.sort(key=lambda c: (-abs(c["saldo"] - c["tema_saldo"]), c["p"], c["nome"]))
    melhores, vistos = [], set()
    for c in saida:  # uma linha por pessoa: o tema mais distante
        if c["k"] not in vistos:
            vistos.add(c["k"])
            melhores.append(c)
    return melhores


def _governo_atual(f: Mapping[str, Any], governo: str) -> float | None:
    return ((f.get("gov") or {}).get(governo) or {}).get("pct")


# Os rankings da ficha: chave → como ler o valor (None: fora do ranking). Entre os que estão
# em exercício na Casa; o 1º é quem tem o maior valor.
RANKINGS: tuple[tuple[str, Any], ...] = (
    ("pres", lambda f, leg, gov: None if f.get("_preside") else ((f.get("leg") or {}).get(leg) or {}).get("pres", {}).get("pct")),
    ("gov", lambda f, leg, gov: _governo_atual(f, gov)),
    ("fid", lambda f, leg, gov: (((f.get("leg") or {}).get(leg) or {}).get("fid") or {}).get("pct")),
    ("fora", lambda f, leg, gov: (f.get("fora") or {}).get("partido", 0)),
    ("prop", lambda f, leg, gov: (f.get("autoria") or {}).get("total", 0)),
    ("par", lambda f, leg, gov: (f.get("autoria") or {}).get("paradas_n", 0)),
    ("norma", lambda f, leg, gov: ((f.get("autoria") or {}).get("estados") or {}).get("norma", 0)),
    ("rel", lambda f, leg, gov: (f.get("relatorias") or {}).get("total", 0)),
    ("jor", lambda f, leg, gov: (f.get("jornal") or {}).get("n30", 0)),
    ("ct", lambda f, leg, gov: media_da_cota(f)),
)


def media_da_cota(f: Mapping[str, Any]) -> float | None:
    """A média mensal da cota que entra no ranking e no filtro: com cota.MINIMO_MESES meses ou
    mais de despesa (como as listas), ou zero para quem não pediu reembolso nenhum."""
    c = f.get("cota") or {}
    leg = c.get("leg") or {}
    if c.get("sem_despesa"):
        return 0.0
    return leg.get("media") if (leg.get("meses") or 0) >= cota.MINIMO_MESES else None


def rankings(fichas: Iterable[dict[str, Any]], legislatura: int, governo: str) -> None:
    """Põe em cada ficha de quem está em exercício a posição dela em cada ranking da Casa:
    ``ranks[chave] = [de cima, total, de baixo]`` (com empate, a mesma posição; "de
    baixo" é a posição contando de quem tem o menor valor)."""
    leg = str(legislatura)
    ativos = [f for f in fichas if f.get("exercicio")]
    for f in ativos:
        f["_preside"] = any(not fim for _inicio, fim in f.get("presidiu") or [])
    for chave, ler in RANKINGS:
        valores = [(f, ler(f, leg, governo)) for f in ativos]
        valores = [(f, v) for f, v in valores if v is not None]
        numeros = sorted(v for _f, v in valores)
        total = len(numeros)
        for f, v in valores:
            maiores = total - bisect.bisect_right(numeros, v)
            menores = bisect.bisect_left(numeros, v)
            f.setdefault("ranks", {})[chave] = [maiores + 1, total, menores + 1]
    for f in ativos:
        f.pop("_preside", None)


def votacoes_por_proposicao(anos: Iterable[AnoVotos], regras: Regras, legislatura: int) -> dict[str, list[tuple[AnoVotos, int]]]:
    """Proposição ("PEC 3/2021") → as votações nominais abertas de Plenário dela na legislatura."""
    por: dict[str, list[tuple[AnoVotos, int]]] = defaultdict(list)
    for ano in anos:
        for k, v in enumerate(ano.votacoes):
            leg = regras.legislatura_em(v.data)
            if v.plenario and not v.secreta and v.prop and leg is not None and leg.numero == legislatura:
                por[v.prop].append((ano, k))
    return dict(por)


turno = base.turno  # o turno de uma votação de PEC, pela descrição oficial


def jornal_e_voto(
    mencoes: Iterable[jornal.Mencao], pid: int, por_prop: Mapping[str, list[tuple[AnoVotos, int]]], regras: Regras, *, lista: int = JORNAL_VOTO
) -> list[dict[str, Any]]:
    """No jornal × no voto: cada texto do jornal que cita a pessoa e uma proposição em que
    ela votou, com os votos dela nessa proposição (o texto principal primeiro). Põe o que
    saiu no jornal ao lado do registro; não diz o que a pessoa defendeu."""
    itens: list[dict[str, Any]] = []
    vistos: set[tuple[str, str]] = set()
    indices: dict[int, dict[int, int]] = {}
    for m in mencoes:  # da mais nova para a mais antiga
        for prop in m.props:
            if (m.titulo, prop) in vistos:
                continue
            votos = []
            for ano, k in por_prop.get(prop, []):
                indice = indices.setdefault(id(ano), ano.indice())
                if pid not in indice:
                    continue
                v = ano.votacoes[k]
                voto = v.votos[indice[pid]]
                if voto == base.SEM_REGISTRO:
                    continue
                tu = turno(v.descricao) if v.tipo in (base.MERITO, base.DESTAQUE) else None
                votos.append(
                    {"d": v.data, "t": v.tipo, "x": voto, **({"tu": tu} if tu else {}), **({"ap": 1 if v.aprovada else 0} if v.aprovada is not None else {})}
                )
            if not votos:
                continue
            vistos.add((m.titulo, prop))
            votos.sort(key=lambda x: (x["t"] != base.MERITO, x["t"] != base.DESTAQUE, x["d"], x.get("tu", 0)))
            item = {"d": m.data, "t": m.titulo, "p": prop, "votos": votos[:4], "n_votos": len(votos)}
            apelido = regras.apelido_de(prop)
            if apelido:
                item["apel"] = apelido
            if m.tipo == "n":
                item.update({"v": m.veiculo, "u": m.url})
            else:
                item["id"] = m.materia
            itens.append(item)
            if len(itens) >= lista:
                return itens
    return itens


def _linha_de_lista(f: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    return {"k": f["k"], "nome": f["nome"], "partido": f.get("partido") or "", "uf": f.get("uf") or "", **extra}


def _listas(fichas: list[Mapping[str, Any]], medias: Mapping[str, Mapping[str, Any]], legislatura: int, regras: Regras) -> dict[str, Any]:
    """As listas do painel: quem falta, quem diverge do partido, quem destoa da média da
    bancada, quem mudou, quem tem mais propostas paradas e quem o jornal mais cita."""
    lim = regras.limiares
    ativos = [f for f in fichas if f.get("exercicio")]
    leg = str(legislatura)

    def presenca(f: Mapping[str, Any]) -> dict[str, Any]:
        return ((f.get("leg") or {}).get(leg) or {}).get("pres") or {}

    def fidelidade(f: Mapping[str, Any]) -> dict[str, Any]:
        return ((f.get("leg") or {}).get(leg) or {}).get("fid") or {}

    ausentes = sorted(
        (f for f in ativos if presenca(f).get("n", 0) >= lim.minimo_presenca and not any(not fim for _i, fim in f.get("presidiu") or [])),
        key=lambda f: presenca(f)["pct"],
    )
    dissidentes = sorted((f for f in ativos if fidelidade(f).get("n", 0) >= MINIMO_FIDELIDADE), key=lambda f: fidelidade(f)["pct"])
    destoam = []
    for f in ativos:
        media = medias.get(f.get("partido") or "") or {}
        saldo = ficha.saldo_no_partido(f, lim.minimo_divisivas)  # quem trocou de partido: só os votos no atual
        if saldo is None or media.get("saldo") is None or media.get("n", 0) < 5:
            continue
        destoam.append((abs(saldo - media["saldo"]), f, media["saldo"], saldo))
    destoam.sort(key=lambda t: -t[0])
    mudaram = sorted((f for f in ativos if f.get("mudanca")), key=lambda f: -abs(f["mudanca"]["pontos"]))
    parados = sorted((f for f in ativos if (f.get("autoria") or {}).get("paradas_n")), key=lambda f: -f["autoria"]["paradas_n"])
    citados = sorted((f for f in ativos if (f.get("jornal") or {}).get("n30")), key=lambda f: -f["jornal"]["n30"])
    return {
        "ausentes": [
            _linha_de_lista(f, pct=presenca(f)["pct"], n=presenca(f)["n"], faltas=presenca(f).get("faltas", 0), just=presenca(f).get("just", 0))
            for f in ausentes[:LISTA]
        ],
        "presenca_mediana": ficha.pct(sorted(presenca(f)["pct"] for f in ausentes)[len(ausentes) // 2], 1) if ausentes else None,
        "dissidentes": [_linha_de_lista(f, pct=fidelidade(f)["pct"], n=fidelidade(f)["n"]) for f in dissidentes[:LISTA]],
        "destoam": [
            _linha_de_lista(f, saldo=saldo, partido_saldo=media, dif=round(d), **({"no_partido": True} if (f.get("campo") or {}).get("no_partido") else {}))
            for d, f, media, saldo in destoam[:LISTA]
            if d >= 25
        ],
        "mudaram": [_linha_de_lista(f, **f["mudanca"]) for f in mudaram[:LISTA]],
        "parados": [_linha_de_lista(f, n=f["autoria"]["paradas_n"], total=f["autoria"]["total"]) for f in parados[:LISTA]],
        "citados": [_linha_de_lista(f, n=f["jornal"]["n30"], frase=(f.get("frases") or [""])[0]) for f in citados[:LISTA]],
        "mudam_tema": [{k: v for k, v in c.items() if k != "p"} for c in mudam_conforme_tema(fichas, legislatura)[:LISTA]],
        "jornal_voto": sorted(
            (_linha_de_lista(f, **item) for f in ativos for item in (f.get("jornal_voto") or [])[:2]),
            key=lambda i: (i["d"], i["nome"]),
            reverse=True,
        )[:LISTA],
    }


# ── a leitura ────────────────────────────────────────────────────────────────


def _nome_casa(casa: str) -> str:
    return "Câmara" if casa == "cd" else "Senado"


def _na_casa(casa: str) -> str:
    return "Na Câmara" if casa == "cd" else "No Senado"


MINIMO_TEMA = 20  # votações divisivas para um tema entrar no achado do partido que muda de lado
MINIMO_FRENTE = 20  # membros em exercício com lado no tema para a frente virar achado
MINIMO_SEM_COTA = 100  # votações possíveis na legislatura para quem não pediu reembolso entrar como "gastou zero"
PARTE_FRENTE = 0.2  # parte dos membros do outro lado do coordenador para virar achado
BANCADA_DO_ACHADO = {"cd": 20, "sf": 5}  # parlamentares em exercício para o partido ser o assunto do achado


def _num(valor: int) -> str:
    return text.format_number_pt(valor, 0)


def _sinal(valor: float) -> str:
    """Saldo com o sinal de menos tipográfico (o hífen some ao lado de um número)."""
    return f"{round(valor):+d}".replace("-", "−")


def achados(paineis: Mapping[str, Mapping[str, Any]], regras: Regras) -> list[dict[str, Any]]:
    """O que os dados têm a dizer hoje, um achado por tipo e por Casa, cada um com o
    número e o lugar da página onde está a prova. São contagens de registros oficiais:
    nenhuma frase aqui depende de amostra, então nenhuma precisa de teste; as que
    comparam comportamentos (quem mudou) já chegam filtradas pelo teste de :mod:`.perfil`."""
    saida: list[dict[str, Any]] = []
    for casa, painel in paineis.items():
        nome = _nome_casa(casa)
        matriz = painel.get("temas") or {}
        partidos = matriz.get("partidos") or {}
        # 1. O partido que mais muda de lado conforme o tema.
        melhor = None
        for sigla, por_tema in partidos.items():
            saldos = {t: s for t, (s, n) in por_tema.items() if t != "todas" and n >= MINIMO_TEMA}
            if len(saldos) < 2:
                continue
            alto, baixo = max(saldos, key=saldos.get), min(saldos, key=saldos.get)
            amplitude = saldos[alto] - saldos[baixo]
            membros = next((b["membros"] for b in painel.get("bancadas") or [] if b["sigla"] == sigla), 0)
            if membros < BANCADA_DO_ACHADO[casa]:
                continue
            if saldos[alto] > 20 and saldos[baixo] < -20 and (melhor is None or (amplitude, membros) > (melhor[0], melhor[1])):
                melhor = (amplitude, membros, sigla, alto, saldos[alto], baixo, saldos[baixo])
        if melhor:
            _amp, _m, sigla, alto, s_alto, baixo, s_baixo = melhor
            saida.append(
                {
                    "tipo": "tema",
                    "casa": casa,
                    "peso": 90 + min(melhor[0], 150) / 10,
                    "titulo": f"{_na_casa(casa)}, o {sigla} vota com a direita em {regras.nome_do_tema(alto)} e com a esquerda em {regras.nome_do_tema(baixo)}",
                    "texto": (
                        f"Saldo de {_sinal(s_alto)} em {regras.nome_do_tema(alto)} e de {_sinal(s_baixo)} em {regras.nome_do_tema(baixo)} "
                        "(−100 = sempre com a esquerda; +100 = sempre com a direita), nas votações de conteúdo em que os polos divergiram."
                    ),
                    "alvo": f"#cg-temas-{casa}",
                }
            )
        # 1a. Quanto os polos votam juntos: o maior partido de cada um, em todas as votações de conteúdo.
        cv = painel.get("convivencia") or {}
        polos = regras.polos[-1] if regras.polos else None  # os polos de hoje
        if cv.get("partidos") and polos is not None:
            membros = {b["sigla"]: b["membros"] for b in painel.get("bancadas") or []}
            maiores = [max((p for p in cv["partidos"] if p in lado), key=lambda p: membros.get(p, 0), default="") for lado in (polos.esquerda, polos.direita)]
            celula = cv["matriz"][cv["partidos"].index(maiores[0])][cv["partidos"].index(maiores[1])] if all(maiores) else None
            if celula:
                saida.append(
                    {
                        "tipo": "juntos",
                        "casa": casa,
                        "peso": 72,
                        "titulo": f"{_na_casa(casa)}, {maiores[0]} e {maiores[1]} ficaram do mesmo lado em {celula[0]}% das votações de conteúdo",
                        "texto": (
                            f"Em {_num(cv.get('juntos') or 0)} das {_num(cv.get('conteudo') or 0)} votações de conteúdo do Plenário, os dois polos "
                            f"votaram juntos. O saldo esquerda × direita só olha as {_num(cv.get('divisivas') or 0)} em que eles se opuseram."
                        ),
                        "alvo": f"#cg-juntos-{casa}",
                    }
                )
        # 1b. Onde um polo racha (o PSOL com a direita, o NOVO com a esquerda).
        racha = next((r for r in (painel.get("rachas") or {}).get("partidos") or [] if r["n"] >= MINIMO_RACHA), None)
        if racha:
            temas_r = racha.get("temas") or []
            principal = temas_r[0] if temas_r else None
            governo_txt = ""
            if racha.get("contra_governo"):
                governo_txt = f" Em {_num(racha['contra_governo'])} delas, o {racha['sigla']} votou contra a orientação do governo."
            saida.append(
                {
                    "tipo": "racha",
                    "casa": casa,
                    "peso": 85,
                    "titulo": (
                        f"{_na_casa(casa)}, o {racha['sigla']} votou com a {racha['com']}, contra o resto do polo, "
                        f"em {_num(racha['n'])} votações de conteúdo"
                    ),
                    "texto": (
                        (f"{_num(principal['n'])} delas foram de {principal['nome']}." if principal else "")
                        + governo_txt
                        + " Essas votações não medem esquerda × direita e ficam fora do saldo."
                    ).strip(),
                    "alvo": f"#cg-rachas-{casa}",
                }
            )
        # 2. A fila.
        fila = painel.get("fila") or {}
        filas = fila.get("filas") or {}
        sem_relator = (filas.get(projetos.F_RELATOR) or {}).get("n") or 0
        if sem_relator:
            orgao = next(iter(fila.get("orgaos") or []), None)
            texto = f"{_num(fila.get('tramitando') or 0)} matérias tramitam; {_num(sem_relator)} esperam que alguém designe um relator"
            if orgao and orgao.get("sem_relator"):
                texto += f". Só na {orgao['orgao']} são {_num(orgao['sem_relator'])}"
                if orgao.get("presidente"):
                    texto += f" (presidente: {orgao['presidente']['nome']})"
            saida.append(
                {
                    "tipo": "fila",
                    "casa": casa,
                    "peso": 80,
                    "titulo": f"{nome}: {_num(sem_relator)} propostas paradas na primeira porta, à espera de relator",
                    "texto": texto + ".",
                    "alvo": f"#cg-fila-{casa}",
                }
            )
        prontas = fila.get("prontas_plenario") or []
        if prontas and prontas[0].get("dias"):
            saida.append(
                {
                    "tipo": "pauta",
                    "casa": casa,
                    "peso": 60,
                    "titulo": f"{nome}: {_num(fila.get('prontas_plenario_n') or len(prontas))} projetos prontos para o Plenário não entram na pauta",
                    "texto": f"O mais antigo, {prontas[0]['t']}, espera há {_num(prontas[0]['dias'])} dias: {prontas[0]['e']}",
                    "alvo": f"#cg-fila-{casa}",
                }
            )
        # 2b. O que espera × o que passou, pela mesma régua.
        rel = painel.get("relevancia") or {}
        relevancia_dita = bool(rel.get("fortes_paradas") and rel.get("fracas_rapidas"))
        if relevancia_dita:
            mais_forte = next(iter(rel.get("esperam") or []), None)
            mais_rapida = next((i for i in rel.get("passaram") or [] if i["n"] < regras.relevancia.baixa), None)
            partes = [
                f"Enquanto isso, {_num(rel['fracas_rapidas'])} das {_num(rel.get('passaram_n') or 0)} aprovadas pelo Plenário em 12 meses "
                f"tinham sinais fracos (nota abaixo de {round(regras.relevancia.baixa)}) e passaram em menos de {relevancia.RAPIDA} dias"
            ]
            if mais_rapida:
                quando = {0: "no mesmo dia do protocolo", 1: "no dia seguinte ao protocolo"}.get(
                    mais_rapida["dias"], f"{_num(mais_rapida['dias'])} dias depois do protocolo"
                )
                partes.append(f"A mais rápida, {mais_rapida['t']}, foi aprovada {quando}")
            if mais_forte:
                partes.append(f"A de nota mais alta que espera é {mais_forte['t']} (nota {mais_forte['n']})")
            saida.append(
                {
                    "tipo": "relevancia",
                    "casa": casa,
                    "peso": 78,
                    "titulo": (
                        f"{nome}: {_num(rel.get('fortes_paradas') or 0)} matérias com sinais fortes de relevância estão prontas para o "
                        "Plenário e sem andamento há mais de um ano"
                    ),
                    "texto": ". ".join(partes) + ".",
                    "alvo": f"#cg-relev-{casa}",
                }
            )
        # 3. O que passou na frente (só pelas votações nominais): quando o achado 2b não foi
        # dito; com ele, a mesma coisa já está dita com todas as aprovadas.
        furaram = painel.get("furaram") or {}
        if furaram.get("rapidas") and not relevancia_dita:
            primeira = furaram["rapidas"][0]
            saida.append(
                {
                    "tipo": "furaram",
                    "casa": casa,
                    "peso": 75,
                    "titulo": f"{nome}: {furaram['rapidas_n']} de {furaram['aprovadas']} matérias aprovadas foram do protocolo ao voto em menos de {RAPIDO} dias",
                    "texto": (
                        f"A mais rápida, {primeira['p']}, "
                        + ("foi votada no mesmo dia em que chegou" if primeira["dias"] == 0 else f"levou {primeira['dias']} {'dia' if primeira['dias'] == 1 else 'dias'}")
                        + (", com urgência aprovada" if primeira.get("urg") else "")
                        + f"; metade das aprovadas levou mais de {_num(furaram.get('mediana_dias') or 0)} dias."
                    ),
                    "alvo": f"#cg-relev-{casa}",
                }
            )
        # 4. Quem segura relatorias.
        sentado = next(iter(fila.get("sentados") or []), None)
        if sentado and sentado.get("antigas"):
            saida.append(
                {
                    "tipo": "relatoria",
                    "casa": casa,
                    "peso": 55,
                    "titulo": f"{nome}: {sentado['nome']} tem {sentado['n']} matérias esperando o seu parecer",
                    "texto": f"{sentado['antigas']} delas há mais de {regras.limiares.dias_relatoria // 30} meses; a mais antiga, há {_num(sentado.get('mais_antiga_dias') or 0)} dias.",
                    "alvo": f"#cg-fila-{casa}",
                    "ficha": f"{casa}-{sentado['id']}",
                }
            )
        listas = painel.get("listas") or {}
        # 5. Quem falta.
        ausente = next(iter(listas.get("ausentes") or []), None)
        if ausente:
            saida.append(
                {
                    "tipo": "presenca",
                    "casa": casa,
                    "peso": 50,
                    "titulo": f"{nome}: {ausente['nome']} ({ausente['partido']}-{ausente['uf']}) votou em {ficha._p(ausente['pct'])} das votações nominais",
                    "texto": (
                        f"É a menor participação entre os que estão em exercício: {_num(ausente['n'])} votações em que podia votar"
                        + (
                            f", fora outras {_num(ausente['just'])} com ausência justificada"
                            if ausente.get("just")
                            else (", já sem as ausências justificadas" if casa == "sf" else "")
                        )
                        + f". Metade da Casa votou em mais de {ficha._p(listas.get('presenca_mediana') or 0)}."
                        + (" Os dados abertos da Câmara não dizem se a ausência foi justificada." if casa == "cd" else "")
                    ),
                    "alvo": f"#cg-listas-{casa}",
                    "ficha": ausente["k"],
                }
            )
        # 6. Quem mudou.
        mudou = next(iter(listas.get("mudaram") or []), None)
        if mudou:
            sobe = mudou["pontos"] > 0
            saida.append(
                {
                    "tipo": "mudanca",
                    "casa": casa,
                    "peso": 70,
                    "titulo": f"{nome}: {mudou['nome']} ({mudou['partido']}-{mudou['uf']}) passou a votar {'mais' if sobe else 'menos'} com o governo",
                    "texto": f"De {ficha._p(mudou['antes']['pct'])} para {ficha._p(mudou['agora']['pct'])} das votações com orientação do governo no semestre {mudou['semestre'].replace('-', '/')} ({abs(mudou['pontos'])} pontos; passa de 99% no teste de duas proporções).",
                    "alvo": f"#cg-listas-{casa}",
                    "ficha": mudou["k"],
                }
            )
        # 7. Quem destoa da própria bancada.
        destoa = next(iter(listas.get("destoam") or []), None)
        if destoa:
            saida.append(
                {
                    "tipo": "destoa",
                    "casa": casa,
                    "peso": 65,
                    "titulo": f"{nome}: {destoa['nome']} vota longe do próprio partido",
                    "texto": (
                        f"Saldo de {_sinal(destoa['saldo'])}"
                        + (" desde que está no partido" if destoa.get("no_partido") else "")
                        + f" contra {_sinal(destoa['partido_saldo'])} da média do {destoa['partido']} (−100 = sempre com a esquerda; +100 = sempre com a direita)."
                    ),
                    "alvo": f"#cg-listas-{casa}",
                    "ficha": destoa["k"],
                }
            )
        # 8. Quem vota de um lado em quase tudo e do outro num tema.
        muda = next(iter(listas.get("mudam_tema") or []), None)
        if muda:
            lado_geral = "direita" if muda["saldo"] > 0 else "esquerda"
            saida.append(
                {
                    "tipo": "tema_pessoa",
                    "casa": casa,
                    "peso": 68,
                    "titulo": f"{nome}: {muda['nome']} ({muda['partido']}-{muda['uf']}) vota com a {lado_geral}, menos em {muda['tema_nome']}",
                    "texto": (
                        f"Saldo de {_sinal(muda['saldo'])} no geral e de {_sinal(muda['tema_saldo'])} em {muda['tema_nome']}, em "
                        f"{_num(muda['n'])} votações em que esquerda e direita se opuseram. A diferença passa do acaso contando todas as "
                        "pessoas e temas comparados."
                    ),
                    "alvo": f"#cg-listas-{casa}",
                    "ficha": muda["k"],
                }
            )
        # 4. A frente de causa em que mais membros votam do outro lado do coordenador, no tema dela.
        fr = painel.get("frentes") or {}
        melhor_fr = None
        for c in fr.get("causas") or []:
            coord = c.get("coord") or {}
            lados = c.get("lados") or {}
            if coord.get("saldo") is None or abs(coord["saldo"]) < 60:
                continue  # o coordenador precisa ter lado definido no tema
            com_lado = sum(int(lados.get(x) or 0) for x in ("e", "c", "d"))
            outro = int(lados.get("d" if coord["saldo"] < 0 else "e") or 0)
            if com_lado >= MINIMO_FRENTE and (melhor_fr is None or outro / com_lado > melhor_fr[0]):
                melhor_fr = (outro / com_lado, outro, com_lado, c)
        if melhor_fr and melhor_fr[0] >= PARTE_FRENTE:
            _parte, outro, com_lado, c = melhor_fr
            lado_coord = "esquerda" if c["coord"]["saldo"] < 0 else "direita"
            lado_outro = "direita" if lado_coord == "esquerda" else "esquerda"
            onde = f"em {regras.nome_do_tema(c['tema'])}" if c.get("tema") != "todas" else "nas votações esquerda × direita"
            saida.append(
                {
                    "tipo": "frente",
                    "casa": casa,
                    "peso": 88,
                    "titulo": f"{_na_casa(casa)}, {_num(outro)} dos {_num(com_lado)} membros da frente {c['nome']} votam com a {lado_outro} {onde}; o coordenador, com a {lado_coord}",
                    "texto": (
                        f"O coordenador, {c['coord'].get('nome', '')} ({c['coord'].get('partido', '')}-{c['coord'].get('uf', '')}), tem saldo de "
                        f"{_sinal(c['coord']['saldo'])} {onde}. Assinar uma frente custa uma assinatura: um deputado assina, na mediana, "
                        f"{_num(fr.get('mediana') or 0)} das {_num(fr.get('total') or 0)} frentes da legislatura."
                    ),
                    "alvo": f"#cg-frente-{c['id']}",
                }
            )
        # 5. A cota: quem pagou a maior parte da própria cota a uma empresa só.
        ct = painel.get("cota") or {}
        conc = next(iter(ct.get("concentrados") or []), None)
        if conc and float(conc.get("pct") or 0) >= 50:
            saida.append(
                {
                    "tipo": "cota",
                    "casa": casa,
                    "peso": 80,
                    "titulo": f"{nome}: {conc['nome']} ({conc.get('partido', '')}-{conc.get('uf', '')}) pagou {_num(round(float(conc['pct'])))}% da própria cota a uma empresa só",
                    "texto": (
                        f"R$ {_num(round(float(conc.get('valor') or 0)))} de R$ {_num(round(float(conc.get('total') or 0)))} reembolsados na legislatura (até o último mês fechado) foram para "
                        f"{conc.get('fornecedor', '')}. Cada nota está no registro oficial da Casa."
                    ),
                    "alvo": f"#cg-cota-{casa}",
                    "ficha": conc["k"],
                }
            )
        # 6. Quem vota de um lado nas matérias de nota alta e do outro nas de nota baixa (só o que passa do acaso, 99%).
        for i in (painel.get("listas") or {}).get("muda_peso") or []:
            alta, baixa = i.get("alta"), i.get("baixa")
            if alta is None or baixa is None or not ((alta > 20 and baixa < -20) or (alta < -20 and baixa > 20)):
                continue
            k1, n1 = round(i["alta_n"] * (alta + 100) / 200), i["alta_n"]
            k2, n2 = round(i["baixa_n"] * (baixa + 100) / 200), i["baixa_n"]
            if abs(perfil.z_duas_proporcoes(k1, n1, k2, n2)) < perfil.Z_MUDANCA:
                continue
            lado_alta = "direita" if alta > 0 else "esquerda"
            lado_baixa = "direita" if baixa > 0 else "esquerda"
            saida.append(
                {
                    "tipo": "peso",
                    "casa": casa,
                    "peso": 70,
                    "titulo": f"{nome}: {i['nome']} ({i['partido']}-{i['uf']}) vota com a {lado_alta} nas matérias de mais peso e com a {lado_baixa} nas de menos",
                    "texto": (
                        f"Saldo de {_sinal(alta)} nas {_num(n1)} votações de matérias com nota de relevância da mediana da Casa para cima e de "
                        f"{_sinal(baixa)} nas {_num(n2)} abaixo dela. Nessas votações, pesado pela nota, o saldo vai de {_sinal(i['simples'])} para {_sinal(i['pesado'])}. "
                        "A diferença passa do acaso (99%)."
                    ),
                    "alvo": f"#cg-listas-{casa}",
                    "ficha": i["k"],
                }
            )
            break
    saida.sort(key=lambda a: -a["peso"])
    return saida


# ── atualização completa ─────────────────────────────────────────────────────


def atualizar(
    root: Path,
    data_dir: Path,
    cache_dir: Path,
    opcoes: Opcoes,
    *,
    fetch: Fetcher = net.fetch,
    regras: Regras | None = None,
) -> Resultado:
    """Baixa o que mudou, refaz as contas e grava ``data/congresso/``."""
    regras = regras or regras_mod.carregar(root)
    hoje = opcoes.hoje
    coletor = Coletor(Path(cache_dir), fetch=fetch)
    pasta = pasta_de(data_dir)
    resultado = Resultado(pasta=pasta)
    avisos = resultado.avisos
    legislatura = regras.legislatura_atual(hoje)
    if legislatura is None:
        raise ValueError("config/congresso.yaml sem legislatura que cubra a data de hoje")
    governo = regras.governo_em(hoje)

    votos, gravados = atualizar_base(coletor, regras, pasta / BASE, opcoes, avisos)
    resultado.gravados += gravados
    # As votações cujo registro um a um não bate com o placar anunciado saem das contas (a base
    # gravada fica como veio da fonte).
    fora_do_placar = {
        casa: [
            {"id": v.id, "data": v.data, "prop": v.prop, "anunciado": list(base.placar_anunciado(v.descricao) or ()), "contado": [v.conta().get(base.SIM, 0), v.conta().get(base.NAO, 0)]}
            for ano in anos
            for v in base.sem_as_que_nao_batem(ano, regras.limiares.placar_tolerancia)
        ]
        for casa, anos in votos.items()
    }

    # Câmara: quando cada deputado esteve em exercício (para a presença).
    intervalos = exercicio.ler(pasta / BASE)
    presidentes = exercicio.ler_presidencia(pasta / BASE)
    ids_camara = {p.id for ano in votos["cd"] for p in ano.pessoas}
    try:
        presidentes = exercicio.presidencia(coletor, [leg.numero for leg in regras.legislaturas]) or presidentes
    except Exception as exc:  # noqa: BLE001 — sem a Mesa de hoje, vale a última gravada
        avisos.append(f"Câmara: Mesa Diretora indisponível ({exc}); vale a presidência já gravada")
    try:
        intervalos = exercicio.atualizar(coletor, intervalos, ids_camara, exercicio.em_exercicio_hoje(coletor))
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"Câmara: períodos de exercício não atualizados ({exc})")
    if intervalos:
        resultado.gravados += _gravar(pasta / BASE / exercicio.ARQUIVO, exercicio.serializar(intervalos, presidentes))

    perfis = {
        "cd": perfil.calcular(
            votos["cd"],
            regras,
            exercicio=exercicio.fechados(intervalos, hoje) if intervalos else None,
            presidencia=exercicio.fechados(presidentes, hoje),
        ),
        "sf": perfil.calcular(votos["sf"], regras),
    }
    cadastros = {
        "cd": _cadastros_camara(coletor, perfis["cd"], legislatura.numero, avisos),
        "sf": _cadastros_senado(coletor, perfis["sf"], legislatura.numero, avisos),
    }

    # Projetos, relatorias e filas.
    corrente = int(hoje[:4])
    props_cd: list[Proposicao] = []
    try:
        props_cd = projetos.camara_proposicoes(
            coletor,
            range(opcoes.projetos_desde, corrente + 1),
            temas=temas.TemasCamara(coletor, regras),
            validade_h=opcoes.validade_h,
            validade_antigos_h=0.0 if opcoes.completo else 24 * 8,
            ano_corrente=corrente,
        )
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"Câmara: proposições indisponíveis ({exc})")
    autoria: dict[str, dict[int, list[Proposicao]]] = {"cd": defaultdict(list), "sf": {}}
    for p in props_cd:
        for autor in p.autores:
            autoria["cd"][autor].append(p)
    relatorias = {"cd": projetos.camara_relatorias(props_cd), "sf": {}}
    das_comissoes = projetos.camara_presidentes(coletor, legislatura.numero, hoje) if props_cd else {}
    props_sf: list[Proposicao] = []
    try:
        props_sf = projetos.senado_tramitando(coletor, validade_h=opcoes.validade_h)
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"Senado: matérias em tramitação indisponíveis ({exc})")
    try:
        ativos = {pid: c.nome for pid, c in cadastros["sf"].items() if c.exercicio}
        autoria["sf"] = projetos.senado_de_autoria(coletor, ativos, validade_h=opcoes.validade_h)
        relatorias["sf"] = projetos.senado_relatorias(coletor, validade_h=opcoes.validade_h)
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"Senado: autoria e relatorias indisponíveis ({exc})")

    # O que espera × o que passou: a mesma régua para as prontas e as aprovadas.
    aprovadas: dict[str, list[relevancia.Aprovada]] = {"cd": [], "sf": []}
    if props_cd:
        try:
            aprovadas["cd"], falhas = relevancia.camara_aprovadas(coletor, props_cd, hoje)
            if falhas:
                avisos.append(f"Câmara: {falhas} histórico(s) de matérias aprovadas indisponível(is); ficam fora da comparação")
        except Exception as exc:  # noqa: BLE001
            avisos.append(f"Câmara: matérias aprovadas indisponíveis ({exc})")
    try:
        aprovadas["sf"] = relevancia.senado_aprovadas(coletor, hoje, validade_h=opcoes.validade_h)
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"Senado: matérias aprovadas indisponíveis ({exc})")
    reguas = {"cd": relevancia.Regua(regras, relevancia.apensados(props_cd)), "sf": relevancia.Regua(regras, None)}
    esperando = {"cd": relevancia.prontas(props_cd), "sf": relevancia.prontas(props_sf)}

    # O banco do jornal.
    nomes = jornal.Nomes(jornal.Pessoa(c.chave, c.casa, c.nome) for casa in cadastros.values() for c in casa.values())
    desde = (date.fromisoformat(hoje) - timedelta(days=opcoes.noticias_dias)).isoformat()
    cruzamento = jornal.cruzar(Path(data_dir) / "busca", nomes, desde=desde, apelidos=regras.apelidos_para_busca())
    # O apelido ao lado do número, nas listas de proposições.
    for p in [*props_cd, *props_sf, *(a.prop for casa in aprovadas.values() for a in casa)]:
        if not p.apelido:
            p.apelido = regras.apelido_de(p.titulo)

    # Fichas.
    por_prop = {casa: votacoes_por_proposicao(votos[casa], regras, legislatura.numero) for casa in ("cd", "sf")}
    # Sobre o que cada um mais propõe: as palavras-chave do registro (só a Câmara as traz nas listas).
    vocabularios = {"cd": projetos.Vocabulario(props_cd) if props_cd else None, "sf": None}
    fichas: dict[str, list[dict[str, Any]]] = {"cd": [], "sf": []}
    for casa in ("cd", "sf"):
        for pid, cadastro in cadastros[casa].items():
            p = perfis[casa].get(pid)
            minhas = autoria[casa].get(pid) or []
            fichas[casa].append(
                ficha.montar(
                    cadastro,
                    p,
                    regras,
                    legislatura=legislatura.numero,
                    governo_atual=governo.nome if governo else None,
                    autoria=projetos.resumo_de_autoria(minhas, hoje, regras.limiares, pessoa=pid, vocabulario=vocabularios[casa]) if minhas else None,
                    relatorias=projetos.resumo_de_relatorias(relatorias[casa].get(pid) or [], hoje, regras.limiares) or None,
                    jornal=jornal.resumo_de_mencoes(cruzamento.de(cadastro.chave), hoje) or None,
                    mudanca=perfil.mudanca_recente(p, regras) if p else None,
                )
            )
            if casa == "cd" and presidentes.get(pid):
                fichas[casa][-1]["presidiu"] = [list(i) for i in presidentes[pid]]
            jv = jornal_e_voto(cruzamento.de(cadastro.chave), pid, por_prop[casa], regras)
            if jv:
                fichas[casa][-1]["jornal_voto"] = jv

    # A cota parlamentar, nota a nota: quanto cada um pede de reembolso, com o quê e com quem.
    paineis_cota: dict[str, dict[str, Any]] = {}
    for casa, coletar_cota in (("cd", cota.camara), ("sf", cota.senado)):
        try:
            despesas = coletar_cota(coletor, regras.anos(), ano_corrente=corrente)
            pessoas_casa = {
                pid: {"k": c.chave, "nome": c.nome, "partido": c.partido, "uf": c.uf, "ex": bool(c.exercicio)} for pid, c in cadastros[casa].items()
            }
            paineis_cota[casa], por_pessoa = cota.resumir(despesas, casa=casa, inicio=legislatura.inicio, hoje=hoje, pessoas=pessoas_casa)
            del despesas  # mais de um milhão de lançamentos na Câmara: libera a memória antes de seguir
            for f in fichas[casa]:
                if f["id"] in por_pessoa:
                    f["cota"] = por_pessoa[f["id"]]
            # Quem está em exercício há tempo (MINIMO_SEM_COTA votações possíveis ou mais) e não pediu
            # reembolso nenhum na legislatura: gastou zero, e isso também é dado.
            sem = [
                f
                for f in fichas[casa]
                if f.get("exercicio") and "cota" not in f
                and (((f.get("leg") or {}).get(str(legislatura.numero)) or {}).get("pres") or {}).get("n", 0) >= MINIMO_SEM_COTA
            ]
            for f in sem:
                f["cota"] = {"leg": {"total": 0.0, "meses": 0, "media": 0.0}, "sem_despesa": 1}
            paineis_cota[casa]["sem_despesa"] = [_linha_de_lista(f) for f in sorted(sem, key=lambda f: text.normalize(f["nome"]))]
        except Exception as exc:  # noqa: BLE001 — acessório: sem a cota, a aba sai sem ela
            avisos.append(f"{_nome_casa(casa)}: cota parlamentar indisponível ({exc})")

    # As votações nominais nas comissões (fora das contas do Plenário: só os membros votam).
    paineis_comissoes: dict[str, dict[str, Any]] = {}
    for casa in ("cd", "sf"):
        try:
            if casa == "cd":
                paineis_comissoes[casa], por_pessoa = comissoes.camara(votos["cd"], coletor, regras, legislatura.numero, hoje=hoje)
            else:
                paineis_comissoes[casa], por_pessoa = comissoes.senado(coletor, regras, legislatura.numero, inicio=legislatura.inicio, hoje=hoje)
            for f in fichas[casa]:
                if f["id"] in por_pessoa:
                    f["comissoes"] = por_pessoa[f["id"]]
        except Exception as exc:  # noqa: BLE001 — acessório: sem as comissões, a aba sai sem elas
            avisos.append(f"{_nome_casa(casa)}: votações nas comissões indisponíveis ({exc})")

    # Frentes parlamentares (só a Câmara registra quem assina): quem assina cada frente de causa
    # e como vota no tema dela, ao lado do coordenador.
    painel_frentes: dict[str, Any] = {}
    detalhe_frentes: dict[str, Any] = {}
    try:
        da_legislatura = frentes.coletar(coletor, legislatura.numero)
        causas = frentes.de_causa(da_legislatura, regras)
        pessoas_cd = {
            pid: {"k": c.chave, "nome": c.nome, "partido": c.partido, "uf": c.uf, "ex": bool(c.exercicio)} for pid, c in cadastros["cd"].items()
        }
        saldos_tema = {
            f["id"]: {
                t["t"]: (t["eixo"]["saldo"], t["eixo"]["n"])
                for t in f.get("temas") or []
                if (t.get("eixo") or {}).get("saldo") is not None and t["eixo"]["n"] >= ficha.MINIMO_TEMA_FAIXA
            }
            for f in fichas["cd"]
        }
        saldo_geral = {f["id"]: (f["campo"]["saldo"], f["campo"]["n"]) for f in fichas["cd"] if f.get("campo")}
        painel_frentes, por_deputado = frentes.analisar(
            da_legislatura, causas, votos["cd"], regras, legislatura.numero, pessoas=pessoas_cd, saldos_tema=saldos_tema, saldo_geral=saldo_geral
        )
        for f in fichas["cd"]:
            if f["id"] in por_deputado:
                f["frentes"] = por_deputado[f["id"]]
        detalhe_frentes = membros_das_frentes(painel_frentes, fichas["cd"], saldos_tema, saldo_geral)
    except Exception as exc:  # noqa: BLE001 — acessório: sem as frentes, a aba sai sem elas
        avisos.append(f"Câmara: frentes parlamentares indisponíveis ({exc})")

    for casa in ("cd", "sf"):
        marcar_temas_que_viram(fichas[casa], legislatura.numero)
        rankings(fichas[casa], legislatura.numero, governo.nome if governo else "")

    # Painéis.
    paineis: dict[str, dict[str, Any]] = {}
    indice_props = {p.id: p for p in props_cd}
    for casa in ("cd", "sf"):
        medias = _medias_por_partido(fichas[casa], legislatura.numero, regras.limiares.minimo_divisivas)
        for f in fichas[casa]:
            media = medias.get(f.get("partido") or "")
            if media and media["n"] >= 3:
                f["partido_media"] = media
        em_exercicio = Counter(f["partido"] for f in fichas[casa] if f.get("exercicio") and f.get("partido"))
        nomes_casa = {c.id: c.nome for c in cadastros[casa].values()}
        for ano in votos[casa]:
            for pessoa in ano.pessoas:
                nomes_casa.setdefault(pessoa.id, pessoa.nome)
        bancadas = [
            {
                "sigla": b.sigla,
                "membros": em_exercicio.get(b.sigla, 0),
                "votaram": b.membros,
                "saldo": ficha.pct(b.eixo.saldo),
                "n": b.eixo.n,
                "gov": ficha.pct(b.governo.pct),
                "coesao": ficha.pct(b.coesao),
            }
            for b in perfil.bancadas(votos[casa], regras, legislatura.numero)
            if em_exercicio.get(b.sigla, 0)
        ]
        bancadas.sort(key=lambda b: (-b["membros"], b["sigla"]))
        grandes = {s for s, n in em_exercicio.items() if n >= MINIMO_BANCADA[casa]}
        if casa == "cd":
            fila = projetos.fila_da_casa(props_cd, hoje, regras.limiares, presidentes=das_comissoes, nomes=nomes_casa) if props_cd else {}
            furaram = _furaram(votos[casa], indice_props, hoje)
        else:
            fila = projetos.fila_da_casa(props_sf, hoje, regras.limiares, nomes=nomes_casa, plenario=("PLEN",)) if props_sf else {}
            if fila:
                senadores = sorted(
                    (
                        {"id": pid, "nome": nomes_casa.get(pid, ""), **projetos.resumo_de_relatorias(lista, hoje, regras.limiares, lista=0)}
                        for pid, lista in relatorias["sf"].items()
                        if pid in cadastros["sf"] and cadastros["sf"][pid].exercicio
                    ),
                    key=lambda s: (-(s.get("antigas_n") or 0), -(s.get("total") or 0)),
                )
                fila["sentados"] = [
                    {"id": s["id"], "nome": s["nome"], "n": s["total"], "antigas": s.get("antigas_n") or 0, "mais_antiga_dias": s.get("mais_antiga_dias")}
                    for s in senadores[:LISTA]
                ]
            furaram = _furaram(votos[casa], {}, hoje)
        paineis[casa] = {
            "v": VERSAO,
            "casa": casa,
            "nome": base.CASAS[casa],
            "legislatura": legislatura.numero,
            "periodo": legislatura.rotulo,
            "em_exercicio": sum(1 for f in fichas[casa] if f.get("exercicio")),
            "votacoes": _estatisticas(votos[casa], regras, legislatura.numero),
            "bancadas": bancadas,
            "temas": _matriz_de_temas(votos[casa], regras, legislatura.numero, grandes),
            "convivencia": _convivencia(votos[casa], regras, legislatura.numero, bancadas, grandes),
            "rachas": _rachas(votos[casa], regras, legislatura.numero),
            "relevancia": relevancia.painel(esperando[casa], aprovadas[casa], reguas[casa], hoje, citadas=cruzamento.citadas)
            if esperando[casa] or aprovadas[casa]
            else {},
            "ultimas": _ultimas(votos[casa], regras, nomes_casa),
            "listas": _listas(fichas[casa], medias, legislatura.numero, regras),
            "fila": fila,
            "furaram": furaram,
        }
        if casa == "cd" and painel_frentes:
            paineis[casa]["frentes"] = painel_frentes
        if paineis_cota.get(casa):
            paineis[casa]["cota"] = paineis_cota[casa]
        if paineis_comissoes.get(casa):
            paineis[casa]["comissoes"] = paineis_comissoes[casa]

    # As fichas das matérias: as votadas no Plenário nesta legislatura e as de "o que espera × o que passa".
    fichas_das_materias: dict[str, dict[str, Any]] = {}
    for casa in ("cd", "sf"):
        nomes_casa = {c.id: c.nome for c in cadastros[casa].values()}
        for ano in votos[casa]:
            for pessoa in ano.pessoas:
                nomes_casa.setdefault(pessoa.id, pessoa.nome)
        if casa == "cd":
            conhecidas = dict(indice_props)

            def detalhe(ids: Iterable[int]) -> dict[int, Proposicao]:
                return projetos.camara_detalhes(coletor, ids)
        else:
            conhecidas = {p.processo: p for p in [*props_sf, *(a.prop for a in aprovadas["sf"])] if p.processo}

            def detalhe(ids: Iterable[int]) -> dict[int, Proposicao]:
                return projetos.senado_detalhes(coletor, ids)
        fichas_das_materias[casa], paineis[casa]["cruzada"] = materias.montar(
            casa,
            votos[casa],
            regras,
            legislatura.numero,
            hoje,
            por_id=conhecidas,
            esperando=esperando[casa],
            aprovadas=aprovadas[casa],
            regua=reguas[casa],
            citadas=cruzamento.citadas,
            nomes=nomes_casa,
            fichas={pid: c.chave for pid, c in cadastros[casa].items()},
            detalhe=detalhe,
        )
        # As folhas da aba "Votações e matérias": as matérias votadas por último, cada uma como documento.
        paineis[casa]["folhas"] = materias.folhas(votos[casa], regras, legislatura.numero, fichas_das_materias[casa].get("materias") or {}, nomes_casa)
        # A pauta: os sinais de cada aprovada e o "enquanto isso" (gravados na ficha dela), as
        # contas da Casa e, nos pontos do gráfico, o que os filtros precisam (o 1º tema e os sinais).
        fichas_da_casa = fichas_das_materias[casa]["materias"]
        votos_fora = {materias.titulo_da_materia(v.get("prop") or "") for v in fora_do_placar.get(casa) or []}
        pauta.anotar(fichas_da_casa, regras, casa, votos_desde=legislatura.inicio, votos_fora=votos_fora)
        paineis[casa]["pauta"] = pauta.painel(fichas_da_casa, regras, hoje, casa)
        if paineis[casa]["relevancia"].get("pontos"):
            paineis[casa]["relevancia"]["pontos"] = pauta.pontos(paineis[casa]["relevancia"]["pontos"], fichas_da_casa, regras, casa)

    # O saldo pesado pela relevância da matéria (a nota das fichas das matérias).
    for casa in ("cd", "sf"):
        notas = peso.notas_das_materias(fichas_das_materias[casa].get("materias") or {})
        pesados, cobertura = peso.calcular(votos[casa], regras, legislatura.numero, notas)
        for f in fichas[casa]:
            pesado = pesados.get(f["id"])
            resumo_peso = peso.resumo(pesado, regras.limiares.minimo_divisivas) if pesado else None
            if resumo_peso:
                f["peso"] = resumo_peso
        paineis[casa]["peso"] = cobertura
        paineis[casa]["listas"]["muda_peso"] = peso.muda_quando_pesa(fichas[casa])[:LISTA]

    leitura = achados(paineis, regras)
    polos = regras.polos_em(hoje)
    meta = {
        "v": VERSAO,
        "hoje": hoje,
        "legislatura": {"numero": legislatura.numero, "inicio": legislatura.inicio, "fim": legislatura.fim, "rotulo": legislatura.rotulo},
        "governo": governo.nome if governo else "",
        "governos": [{"nome": g.nome, "inicio": g.inicio, "fim": "" if g.fim == regras_mod.SEM_FIM else g.fim} for g in regras.governos],
        "polos": {"esquerda": sorted(polos.esquerda), "direita": sorted(polos.direita)} if polos else {},
        "campos": [{"id": c.id, "nome": c.nome, "ate": c.ate} for c in regras.campos],
        "limiares": dict(regras.limiares.__dict__),
        # As votações da legislatura que ficaram fora das contas: o registro um a um não bate com o placar anunciado.
        "fora_do_placar": {casa: [v for v in lista if v["data"] >= legislatura.inicio] for casa, lista in fora_do_placar.items()},
        "relevancia": {
            "pesos": dict(regras.relevancia.pesos),
            "origem": dict(regras.relevancia.origem),
            "alcance": dict(regras.relevancia.alcance),
            "janela_demanda_anos": regras.relevancia.janela_demanda_anos,
            "demanda_cheia": regras.relevancia.demanda_cheia,
            "simbolica_teto": regras.relevancia.simbolica_teto,
            "alta": regras.relevancia.alta,
            "baixa": regras.relevancia.baixa,
            "rapida": relevancia.RAPIDA,
        },
        "temas": [{"id": t.id, "nome": t.nome, "curto": t.curto, **temas.definicao(t)} for t in regras.temas],
        "anos": regras.anos(),
        "projetos_desde": opcoes.projetos_desde,
        "jornal": {"edicoes": len(cruzamento.edicoes), "de": cruzamento.edicoes[0] if cruzamento.edicoes else "", "ate": cruzamento.edicoes[-1] if cruzamento.edicoes else "", "manchetes": cruzamento.manchetes},
        "casas": {
            casa: {"pessoas": len(fichas[casa]), "em_exercicio": paineis[casa]["em_exercicio"], "votacoes": paineis[casa]["votacoes"]}
            for casa in ("cd", "sf")
        },
        "avisos": list(avisos),
    }

    # Gravação.
    resumo = {
        "v": VERSAO,
        "hoje": hoje,
        "legislatura": legislatura.numero,
        "pessoas": [ficha.linha(f, legislatura.numero, legislatura.inicio, regras.fusoes) for casa in ("cd", "sf") for f in sorted(fichas[casa], key=lambda f: text.normalize(f["nome"]))],
    }
    atuais = set()
    for casa in ("cd", "sf"):
        for f in fichas[casa]:
            atuais.add(f"{f['k']}.json")
            resultado.gravados += _gravar(pasta / FICHAS / f"{f['k']}.json", _json({"v": VERSAO, **f}))
        resultado.gravados += _gravar(pasta / f"casa-{casa}.json", _json(paineis[casa]))
        resultado.pessoas[casa] = len(fichas[casa])
        resultado.votacoes[casa] = paineis[casa]["votacoes"]["total"]
    if (pasta / FICHAS).is_dir():
        for antigo in (pasta / FICHAS).glob("*.json"):
            if antigo.name not in atuais:
                antigo.unlink(missing_ok=True)
    for casa in ("cd", "sf"):
        chaves = {pid: c.chave for pid, c in cadastros[casa].items()}
        resultado.gravados += _gravar(
            pasta / VOTOS.format(casa=casa), _json_por_linha(votos_da_legislatura(votos[casa], regras, legislatura.numero, chaves, casa))
        )
        resultado.gravados += _gravar(pasta / materias.ARQUIVO.format(casa=casa), materias.serializar(fichas_das_materias[casa]))
    if detalhe_frentes:
        resultado.gravados += _gravar(pasta / FRENTES, _frentes_por_linha({"v": VERSAO, "casa": "cd", "legislatura": legislatura.numero, "frentes": detalhe_frentes}))
    resultado.gravados += _gravar(pasta / RESUMO, _json(resumo))
    resultado.gravados += _gravar(pasta / ACHADOS, _json({"v": VERSAO, "hoje": hoje, "achados": leitura}))
    resultado.gravados += _gravar(pasta / META, _json(meta))
    resultado.requisicoes = coletor.requisicoes
    resultado.megabytes = coletor.bytes / 1e6
    if not avisos:  # com uma fonte fora do ar, parte do cache não foi lida hoje: fica como está
        apagados = coletor.limpar()
        if apagados:
            log.info("Congresso: %d arquivo(s) velho(s) apagado(s) do cache", apagados)
    log.info(
        "Congresso: %d deputados e %d senadores; %d arquivo(s) gravado(s); %d requisições, %.0f MB",
        resultado.pessoas.get("cd", 0),
        resultado.pessoas.get("sf", 0),
        resultado.gravados,
        resultado.requisicoes,
        resultado.megabytes,
    )
    for aviso in avisos:
        log.warning("Congresso: %s", aviso)
    return resultado
