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

import json
import logging
import os
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .. import net, text
from ..net import Fetcher
from . import VERSAO, base, exercicio, ficha, fontes, jornal, perfil, projetos, regras as regras_mod, temas
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
ULTIMAS = 14  # votações nominais mais recentes no painel de cada Casa
LISTA = 15  # tamanho das listas do painel
MINIMO_FIDELIDADE = 50  # votações com posição do partido para entrar nas listas de dissidência
MINIMO_BANCADA = {"cd": 5, "sf": 3}  # parlamentares em exercício para o partido entrar na matriz de temas
JANELA_FURARAM = 240  # dias em que uma votação de texto principal conta em "passaram na frente"
RAPIDO = 120  # do protocolo ao voto em menos que isso: passou na frente


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
    """A base de votações das duas Casas, com o ano corrente refeito (e os fechados,
    só se faltarem ou com ``opcoes.completo``). Devolve ``({casa: [anos]}, gravados)``."""
    corrente = int(opcoes.hoje[:4])
    tc, ts = temas.TemasCamara(coletor, regras), temas.TemasSenado(coletor, regras)
    resultado: dict[str, list[AnoVotos]] = {"cd": [], "sf": []}
    gravados = 0
    for casa in ("cd", "sf"):
        for ano in regras.anos():
            if ano > corrente:
                continue
            existente = base.ler(pasta, casa, ano)
            if existente is not None and ano < corrente and not opcoes.completo:
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
    }


# ── a leitura ────────────────────────────────────────────────────────────────


def _nome_casa(casa: str) -> str:
    return "Câmara" if casa == "cd" else "Senado"


def _na_casa(casa: str) -> str:
    return "Na Câmara" if casa == "cd" else "No Senado"


MINIMO_TEMA = 20  # votações divisivas para um tema entrar no achado do partido que muda de lado
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
        # 3. O que passou na frente.
        furaram = painel.get("furaram") or {}
        if furaram.get("rapidas"):
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
                    "alvo": f"#cg-furaram-{casa}",
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

    # O banco do jornal.
    nomes = jornal.Nomes(jornal.Pessoa(c.chave, c.casa, c.nome) for casa in cadastros.values() for c in casa.values())
    desde = (date.fromisoformat(hoje) - timedelta(days=opcoes.noticias_dias)).isoformat()
    cruzamento = jornal.cruzar(Path(data_dir) / "busca", nomes, desde=desde)

    # Fichas.
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
                    autoria=projetos.resumo_de_autoria(minhas, hoje, regras.limiares, pessoa=pid) if minhas else None,
                    relatorias=projetos.resumo_de_relatorias(relatorias[casa].get(pid) or [], hoje, regras.limiares) or None,
                    jornal=jornal.resumo_de_mencoes(cruzamento.de(cadastro.chave), hoje) or None,
                    mudanca=perfil.mudanca_recente(p, regras) if p else None,
                )
            )
            if casa == "cd" and presidentes.get(pid):
                fichas[casa][-1]["presidiu"] = [list(i) for i in presidentes[pid]]

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
            "ultimas": _ultimas(votos[casa], regras, nomes_casa),
            "listas": _listas(fichas[casa], medias, legislatura.numero, regras),
            "fila": fila,
            "furaram": furaram,
        }

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
        "temas": [{"id": t.id, "nome": t.nome} for t in regras.temas],
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
        "pessoas": [ficha.linha(f, legislatura.numero) for casa in ("cd", "sf") for f in sorted(fichas[casa], key=lambda f: text.normalize(f["nome"]))],
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
