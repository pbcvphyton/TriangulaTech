"""O DJEN (``qijournal/tribunais/djen.py``): a consulta, a leitura de cada publicação (relator,
tipo, área, partes sem nome de pessoa e sem advogado, segredo), a coleta página a página com o
ritmo e a recusa, o casamento com o acervo (a "mesa"), o ``djen.json``, a sonda, a linha de
comando e a tarefa agendada da máquina do dono (``scripts/djen/``).

Sem rede: ``fetch`` e relógio falsos e as amostras de ``tests/fixtures/tribunais/djen/`` (sintéticas
no formato real; ver o ``__init__.py`` de lá)."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import plistlib
import shutil
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

import qijournal.__main__ as principal
from qijournal import net
from qijournal.net import FetchError
from qijournal.tribunais import cli, contas, djen
from qijournal.tribunais.areas import Classificador
from qijournal.tribunais.dados import Retrato
from qijournal.tribunais.modelo import carregar_regras
from qijournal.tribunais.partes import carregar_catalogo
from qijournal.tribunais.tpu import TPU
from tests.fixtures import tribunais as fx
from tests.test_tribunais_fontes import FetchFalso, Relogio, recusa

RAIZ = Path(__file__).resolve().parents[1]
F = fx.PASTA / "djen"
ROBOTS = f"https://{djen.HOST}/robots.txt"
REGRAS = carregar_regras()
_TPU = TPU.carregar()
CLASSIFICADOR = Classificador(REGRAS, _TPU)
CATALOGO = carregar_catalogo()
# 23/11/2026, 21h em Brasília (segunda-feira): a janela vai até domingo, 22/11; o último dia útil é 20/11.
AGORA = datetime(2026, 11, 24, 0, 0, tzinfo=UTC)
HOJE = date(2026, 11, 23)
CNJ = {n: f"{n:07d}00" + "2026" + "3" + "00" + "0000" for n in range(1, 8)}


def _cfg(**kw: Any) -> dict[str, Any]:
    base = {"itens_por_pagina": 5, "pausa_s": 2, "dias_por_execucao": 2, "acervo": {"baixar": False}}
    return djen.carregar_config(bruto={**base, **kw})


def _leitor(cfg: dict[str, Any] | None = None) -> djen.Leitor:
    return djen.Leitor(REGRAS, _TPU, CLASSIFICADOR, CATALOGO, cfg or _cfg())


def _amostra(nome: str) -> dict[str, Any]:
    return json.loads((F / nome).read_text(encoding="utf-8"))


def _itens() -> list[dict[str, Any]]:
    return [*_amostra("stj_2026-11-20_p1.json")["items"], *_amostra("stj_2026-11-20_p2.json")["items"]]


def _resposta(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _api(respostas: dict[tuple[str, str, int], Any], pedidos: list[tuple[str, str, int]] | None = None):
    """Uma API falsa: (sigla, dia, página) → objeto JSON; o que não está no mapa é dia vazio."""
    def responder(url: str, kw: dict[str, Any]) -> net.Response:
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        chave = (q["siglaTribunal"], q["dataDisponibilizacaoInicio"], int(q["pagina"]))
        assert q["dataDisponibilizacaoInicio"] == q["dataDisponibilizacaoFim"]
        if pedidos is not None:
            pedidos.append(chave)
        corpo = respostas.get(chave, _amostra("vazio.json"))
        if isinstance(corpo, Exception):
            raise corpo
        return net.Response(url=url, status=200, content=_resposta(corpo), headers={"content-type": "application/json"})
    return responder


class FetchDaAPI(FetchFalso):
    """O robots.txt (ausente: 404) e qualquer endereço da API pela função ``api``."""

    def __init__(self, api: Any, robots: Any = None) -> None:
        super().__init__({ROBOTS: robots})
        self.api = api

    def __call__(self, url: str, **kw: Any) -> net.Response:
        if url.startswith(djen.API):
            self.pedidos.append({"url": url, **kw})
            return self.api(url, kw)
        return super().__call__(url, **kw)


def _relogio() -> Relogio:
    return Relogio(AGORA.timestamp())


def _retrato(cache: Path) -> Path:
    """Um retrato do acervo de 20/11 no formato de ``dados.Retrato``: a mesa de Nancy Andrighi tem
    quatro processos (um deles publicado em 20/11); o HC está na mesa de Ribeiro Dantas; o processo
    em segredo está na mesa de Raul Araújo; o MS está no cartório."""
    def proc(cnj: str | None, pid: str, relator: str, onde: str, classe: str, area: str, segredo: int = 0, dias: int | None = None,
             registro: str = "") -> dict[str, Any]:
        return {"id": pid, "registro": registro or None, "cnj": cnj, "relator_id": relator, "onde": onde, "classe": classe, "area": area,
                "assunto": {"codigo": None}, "datas": {}, "sem_decisao_dias": dias, "segredo": bool(segredo), "flags": {}}
    processos = [
        proc(CNJ[1], "stj-AREsp-1", "stj-nancy-andrighi", "gabinete", "AREsp", "civil", dias=400, registro="202600000001"),
        proc("99999990020263000000", "stj-REsp-9", "stj-nancy-andrighi", "gabinete", "REsp", "civil", dias=10),
        proc("99999980020263000000", "stj-REsp-8", "stj-nancy-andrighi", "gabinete", "REsp", "consumidor", dias=20),
        proc("99999970020263000000", "stj-REsp-7", "stj-nancy-andrighi", "gabinete", "REsp", "civil", dias=30),
        proc(CNJ[3], "stj-HC-3", "stj-ribeiro-dantas", "gabinete", "HC", "penal", dias=12, registro="202600000003"),
        proc(CNJ[5], "stj-REsp-5", "stj-raul-araujo", "gabinete", "REsp", "familia", segredo=1, dias=900, registro="202600000005"),
        proc(CNJ[6], "stj-MS-6", "stj-herman-benjamin", "cartorio", "MS", "tributario", dias=5, registro="202600000006"),
    ]
    r = Retrato(cache, "stj", "2026-11-20")
    for _ in r.passar(processos):
        pass
    caminho = r.fechar(True)
    assert caminho is not None
    return caminho


# ── a consulta ───────────────────────────────────────────────────────────────────────────────


def test_a_consulta_e_o_endereco_para_o_navegador() -> None:
    url = djen.url_da_consulta("STJ", "2026-11-20", 3, 100)
    assert url == ("https://comunicaapi.pje.jus.br/api/v1/comunicacao?pagina=3&itensPorPagina=100&siglaTribunal=STJ"
                   "&dataDisponibilizacaoInicio=2026-11-20&dataDisponibilizacaoFim=2026-11-20")
    assert djen.url_para_navegador("stf", "2026-11-20").endswith("?pagina=1&itensPorPagina=5&siglaTribunal=STF"
                                                                 "&dataDisponibilizacaoInicio=2026-11-20&dataDisponibilizacaoFim=2026-11-20")
    assert djen.carregar_config(bruto={"itens_por_pagina": 500})["itens_por_pagina"] == 100
    assert djen.carregar_config(bruto={"tribunais": ["stj", "trf1"]})["tribunais"] == ["stj"]


def test_o_envelope_novo_o_antigo_e_o_erro() -> None:
    itens, total = djen.envelope(_amostra("stj_2026-11-20_p1.json"))
    assert (len(itens), total) == (5, 7)
    itens, total = djen.envelope(_amostra("filtro_ignorado.json"))  # a resposta antiga usava "itens"
    assert len(itens) == 1 and total == 3812345
    with pytest.raises(djen.ErroDoDJEN, match="status"):
        djen.envelope({"status": "error", "message": "Parâmetro inválido", "items": []})
    with pytest.raises(djen.ErroDoDJEN, match="sem items"):
        djen.envelope({"status": "success", "count": 0})
    with pytest.raises(djen.ErroDoDJEN):
        djen.envelope(["não é objeto"])


# ── o texto: relator e tipo ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("texto, esperado", [
    ("RECURSO ESPECIAL Nº 1\nRELATORA : MINISTRA NANCY ANDRIGHI\nRECORRENTE : FULANO", "MINISTRA NANCY ANDRIGHI"),
    ("RELATOR : MIN. FLÁVIO DINO\nRECTE.(S) : FULANO", "MIN. FLAVIO DINO"),
    ("RELATOR: O EXMO. SR. MINISTRO RAUL ARAÚJO - QUARTA TURMA", "MINISTRO RAUL ARAUJO"),
    ("RELATORA: MINISTRA NANCY ANDRIGHI AGRAVANTE : FULANO DE TAL", "MINISTRA NANCY ANDRIGHI"),
    ("RELATOR P/ ACÓRDÃO : MINISTRO MOURA RIBEIRO\nRELATOR : MINISTRO HUMBERTO MARTINS", "MINISTRO HUMBERTO MARTINS"),
    ("RELATOR P/ ACÓRDÃO : MINISTRO MOURA RIBEIRO", "MINISTRO MOURA RIBEIRO"),
    ("DECISÃO\nNego provimento.\nMinistro HERMAN BENJAMIN\nRelator", "HERMAN BENJAMIN"),
    ("DECISÃO\n(AgInt no REsp 1/SP, Rel. Ministro RAUL ARAÚJO, Quarta Turma)", None),  # citação de precedente não conta
    ("", None),
])
def test_o_relator_no_texto(texto: str, esperado: str | None) -> None:
    assert djen.nome_do_relator(texto) == esperado


def test_presidencia_e_vice_vao_para_as_linhas_fora_da_grade() -> None:
    assert djen._cargo("MINISTRO PRESIDENTE DO STJ", "stj") == "Presidente do STJ"
    assert djen._cargo("PRESIDÊNCIA", "stj") == "Presidente do STJ"
    assert djen._cargo("VICE-PRESIDÊNCIA", "stj") == "Vice-Presidente do STJ"
    assert djen._cargo("MIN. PRESIDENTE", "stf") == "PRESIDENTE"
    assert djen._cargo("PRESIDENTE DA TERCEIRA TURMA", "stj") == "PRESIDENTE DA TERCEIRA TURMA"
    leitor = _leitor()
    assert leitor.relator("stj", "PRESIDÊNCIA", "DECISÃO") == (None, "presidencia", "orgao", False)
    assert leitor.relator("stj", "SECRETARIA", "RELATOR : MINISTRO PRESIDENTE DO STJ") == (None, "presidencia", "texto", False)
    assert leitor.relator("stj", "SECRETARIA", "RELATOR : MINISTRO FULANO QUE NAO EXISTE") == (None, None, None, True)


@pytest.mark.parametrize("td, tc, texto, esperado", [
    ("Decisão", "Intimação", "", "decisao"),
    ("Acórdão", "Intimação", "", "acordao"),
    ("Despacho", "Intimação", "", "despacho"),
    ("Intimação", "Intimação", "PAUTA DE JULGAMENTOS\nSessão virtual", "pauta"),
    ("Intimação", "Intimação", "DESPACHO\nIntimem-se", "despacho"),
    ("", "Intimação", "EMENTA\nDIREITO CIVIL", "acordao"),
    ("Ata de Distribuição", "Intimação", "", "distribuicao"),
    ("Outros", "Edital", "", "edital"),
    ("Certidão", "Intimação", "Certifico que", "outros"),
])
def test_o_tipo_da_publicacao(td: str, tc: str, texto: str, esperado: str) -> None:
    assert djen.tipo_do_item({"tipoDocumento": td, "tipoComunicacao": tc}, texto, djen.PADRAO["tipos"]) == esperado


# ── uma publicação ───────────────────────────────────────────────────────────────────────────


def test_a_leitura_das_amostras_do_stj() -> None:
    leitor = _leitor()
    lidos = {r["id"]: r for r in (leitor.ler(x) for x in _itens()) if r}
    resumo = {i: (r["sigla"], r["tipo"], r["orgao_cod"], r["relator_id"] or r["fora"], r["relator_por"], r["segredo"], r["cancelada"])
              for i, r in lidos.items()}
    assert resumo == {
        9001: ("AREsp", "decisao", None, "stj-nancy-andrighi", "orgao", False, False),  # gabinete no nomeOrgao
        9002: ("REsp", "acordao", "T3", "stj-nancy-andrighi", "texto", False, False),  # "RELATORA :" no texto; a citação de Raul Araújo não conta
        9003: ("HC", "pauta", "T5", None, None, False, False),  # coordenadoria: só o órgão (o acervo completa)
        9004: ("AREsp", "decisao", None, "presidencia", "orgao", False, False),  # Presidência, nunca o cartão de quem preside
        9005: ("REsp", "despacho", None, "stj-raul-araujo", "orgao", True, False),  # segredo pelo texto
        9006: ("MS", "despacho", None, "stj-herman-benjamin", "texto", False, False),
        9007: ("AREsp", "decisao", None, "stj-nancy-andrighi", "orgao", False, True),  # cancelada
    }
    assert lidos[9001]["dia"] == "2026-11-20" and lidos[9001]["cnj"] == CNJ[1] and lidos[9001]["adv"] == 2
    assert lidos[9001]["partes"] == [["ativo", "pj", "banco-do-brasil"], ["passivo", "pf", None]]
    assert lidos[9002]["partes"] == [["ativo", "pf", None], ["passivo", "ente", "inss"]]  # MEI (CPF no nome) é pessoa física
    assert lidos[9004]["partes"] == [["ativo", "pj", "caixa"], ["passivo", "pj", None]]  # PJ fora do catálogo: sem grupo
    assert lidos[9006]["partes"] == [["ativo", "ente", "estado-sp"], ["passivo", "ente", "uniao"]]
    # outro tribunal (a estrutura de terceiros é de um TJ) ou tribunal trocado: nada
    assert leitor.ler(_amostra("estrutura_terceiros.json")["exemplo_item"]) is None
    assert leitor.ler(_itens()[0], "stf") is None


def test_nenhum_nome_de_pessoa_nenhum_advogado_e_nada_do_texto() -> None:
    leitor = _leitor()
    texto = json.dumps([leitor.ler(x) for x in _itens()], ensure_ascii=False)
    for proibido in ("CARLOS", "MARIA MODELO", "12345678909", "A. B. C.", "ADVOGADO", "numero_oab", "OAB", "Cuida-se", '"texto":',
                     "PARTE EXEMPLO", "BANCO EXEMPLO"):
        assert proibido not in texto, proibido


# ── a coleta ─────────────────────────────────────────────────────────────────────────────────


def _coletor(tmp_path: Path, fetch: Any, relogio: Relogio, cfg: dict[str, Any]) -> djen.ColetorTribunais:
    return djen.ColetorTribunais(tmp_path / "cache", regras=djen.regras_do_coletor(REGRAS, cfg), fetch=fetch, relogio=relogio,
                                 dormir=relogio.dormir)


def test_um_dia_pagina_a_pagina_com_pausa_e_sem_repetir(tmp_path: Path) -> None:
    cfg = _cfg()
    relogio = _relogio()
    pedidos: list[tuple[str, str, int]] = []
    fetch = FetchDaAPI(_api({("STJ", "2026-11-20", 1): _amostra("stj_2026-11-20_p1.json"),
                             ("STJ", "2026-11-20", 2): _amostra("stj_2026-11-20_p2.json")}, pedidos))
    coletor = _coletor(tmp_path, fetch, relogio, cfg)
    d = djen.coletar_dia(coletor, _leitor(cfg), "stj", "2026-11-20", cfg, agora=lambda: AGORA)
    assert pedidos == [("STJ", "2026-11-20", 1), ("STJ", "2026-11-20", 2)]
    assert fetch.urls()[0] == ROBOTS  # o robots.txt antes de tudo (404: tudo permitido)
    assert (len(d.itens), d.count, d.paginas, d.completo, d.aviso) == (7, 7, 2, True, None)  # o item repetido conta uma vez
    assert relogio.esperas == [2.0, 2.0]  # a pausa do config entre um pedido e o seguinte ao mesmo host
    assert d.coletado_em == "2026-11-24T00:00:00Z" and d.fechado  # coletado em 23/11 (Brasília), depois do dia 20


def test_o_teto_de_paginas_deixa_o_dia_pela_metade(tmp_path: Path) -> None:
    cfg = _cfg(max_paginas_dia=1)
    relogio = _relogio()
    fetch = FetchDaAPI(_api({("STJ", "2026-11-20", 1): _amostra("stj_2026-11-20_p1.json")}))
    d = djen.coletar_dia(_coletor(tmp_path, fetch, relogio, cfg), _leitor(cfg), "stj", "2026-11-20", cfg, agora=lambda: AGORA)
    assert (len(d.itens), d.completo) == (5, False) and "teto de 1 páginas" in (d.aviso or "")
    assert not d.fechado


def test_api_que_ignora_o_filtro_para_na_primeira_pagina(tmp_path: Path) -> None:
    cfg = _cfg()
    relogio = _relogio()
    pedidos: list[tuple[str, str, int]] = []
    fetch = FetchDaAPI(_api({("STJ", "2026-11-20", 1): _amostra("filtro_ignorado.json")}, pedidos))
    with pytest.raises(djen.FiltroIgnorado, match="TJSP"):
        djen.coletar_dia(_coletor(tmp_path, fetch, relogio, cfg), _leitor(cfg), "stj", "2026-11-20", cfg, agora=lambda: AGORA)
    assert pedidos == [("STJ", "2026-11-20", 1)]  # 3,8 milhões de publicações do país: nem a segunda página


def test_os_dias_que_faltam(tmp_path: Path) -> None:
    cfg = _cfg(dias_por_execucao=5)
    cache = tmp_path / "cache"
    assert djen.janela(HOJE, cfg) == (date(2026, 10, 24), date(2026, 11, 22))
    assert djen.planejar(cache, "stj", HOJE, cfg) == ["2026-11-20", "2026-11-19", "2026-11-18", "2026-11-17", "2026-11-16"]
    djen.gravar_dia(cache, djen.Dia("stj", "2026-11-20", completo=True, coletado_em="2026-11-21T12:00:00Z"))  # fechado
    djen.gravar_dia(cache, djen.Dia("stj", "2026-11-19", completo=False, coletado_em="2026-11-21T12:00:00Z"))  # pela metade
    djen.gravar_dia(cache, djen.Dia("stj", "2026-11-18", completo=True, coletado_em="2026-11-18T22:00:00Z"))  # coletado no próprio dia (19h em Brasília)
    assert djen.planejar(cache, "stj", HOJE, cfg)[:3] == ["2026-11-19", "2026-11-18", "2026-11-17"]
    tudo = djen.planejar(cache, "stj", HOJE, cfg, completo=True)
    assert len(tudo) == 19 and "2026-11-20" not in tudo and all(date.fromisoformat(d).weekday() < 5 for d in tudo)
    assert djen.planejar(cache, "stj", HOJE, {**cfg, "incluir_hoje": True})[0] == "2026-11-23"


# ── a recusa: um pedido e nada mais ──────────────────────────────────────────────────────────


def test_a_recusa_geografica_encerra_no_primeiro_pedido(tmp_path: Path) -> None:
    relogio = _relogio()
    fetch = FetchFalso({ROBOTS: recusa(ROBOTS)})
    r = djen.executar(RAIZ, tmp_path, tmp_path / "cache", agora=AGORA, fetch=fetch, relogio=relogio, dormir=relogio.dormir, cfg=_cfg())
    assert fetch.urls() == [ROBOTS]  # nenhuma nova tentativa, nenhum outro tribunal, nada de acervo
    assert r.recusa and "403" in r.recusa
    assert any("Nada foi contornado" in a for a in r.avisos)
    assert not r.gravado and not (tmp_path / "data" / "tribunais" / "djen.json").exists()
    assert relogio.esperas == []  # sem espera de "nova tentativa": a recusa geográfica não passa com o tempo


def test_robots_que_veda_a_api_nada_e_pedido(tmp_path: Path) -> None:
    relogio = _relogio()
    fetch = FetchFalso({ROBOTS: b"User-agent: *\nDisallow: /api/\n"})
    r = djen.executar(RAIZ, tmp_path, tmp_path / "cache", agora=AGORA, fetch=fetch, relogio=relogio, dormir=relogio.dormir, cfg=_cfg())
    assert fetch.urls() == [ROBOTS] and r.recusa and "robots.txt" in r.recusa


def test_falha_de_rede_deixa_o_dia_para_depois_e_o_outro_tribunal_segue(tmp_path: Path) -> None:
    relogio = _relogio()
    pedidos: list[tuple[str, str, int]] = []
    erro = FetchError(djen.API, "HTTP 503", status=503)
    fetch = FetchDaAPI(_api({("STJ", "2026-11-20", 1): erro}, pedidos))
    r = djen.executar(RAIZ, tmp_path, tmp_path / "cache", agora=AGORA, fetch=fetch, relogio=relogio, dormir=relogio.dormir,
                      cfg=_cfg(), sem_acervo=True)
    assert pedidos == [("STJ", "2026-11-20", 1), ("STF", "2026-11-20", 1), ("STF", "2026-11-19", 1)]
    assert r.coletados == {"stj": [], "stf": ["2026-11-20", "2026-11-19"]}
    assert any("STJ em 20/11/2026 falhou" in a for a in r.avisos)


# ── o djen.json ──────────────────────────────────────────────────────────────────────────────


def _executar_com_amostra(tmp_path: Path, **kw: Any) -> tuple[djen.Execucao, dict[str, Any], FetchDaAPI]:
    relogio = _relogio()
    fetch = FetchDaAPI(_api({("STJ", "2026-11-20", 1): _amostra("stj_2026-11-20_p1.json"),
                             ("STJ", "2026-11-20", 2): _amostra("stj_2026-11-20_p2.json")}))
    r = djen.executar(RAIZ, tmp_path, tmp_path / "cache", agora=AGORA, fetch=fetch, relogio=relogio, dormir=relogio.dormir,
                      cfg=kw.pop("cfg", _cfg()), **kw)
    caminho = tmp_path / "data" / "tribunais" / "djen.json"
    return r, json.loads(caminho.read_text(encoding="utf-8")), fetch


def test_o_djen_json_com_o_acervo(tmp_path: Path) -> None:
    _retrato(tmp_path / "cache")
    r, d, _fetch = _executar_com_amostra(tmp_path)
    assert r.gravado and r.coletados == {"stj": ["2026-11-20", "2026-11-19"], "stf": ["2026-11-20", "2026-11-19"]}
    assert d["v"] == 1 and d["hoje"] == "2026-11-23" and d["janela"] == {"de": "2026-10-24", "ate": "2026-11-22", "dias_uteis": 20}
    coletas = [d["tribunais"][t]["coletado_em"] for t in ("stj", "stf")]
    assert d["gerado_em"] == max(coletas) and d["gerado_em"].startswith("2026-11-24T00:00")  # a coleta mais nova: mesmos dados, mesmos bytes
    stj = d["tribunais"]["stj"]
    assert (stj["publica"], stj["ultimo_dia"]) == (True, "2026-11-20")
    assert stj["dias"][-1] == ["2026-11-20", 6, True] and stj["dias"][-2] == ["2026-11-19", 0, True] and stj["dias"][0] == ["2026-10-26", None, False]
    mes = stj["mes"]
    assert (mes["itens"], mes["processos"], mes["mesa"]) == (6, 6, 3)  # a cancelada fica de fora
    assert mes["tipos"] == {"pauta": 1, "acordao": 1, "decisao": 2, "despacho": 2}
    assert mes["areas"]["penal"] == 1 and mes["areas"]["civil"] == 1 and mes["areas"]["familia"] == 1 and mes["areas"]["sem_area"] == 2
    assert stj["dia"] == {**mes, "partes": stj["dia"]["partes"]}  # o último dia é o único com publicação
    partes = mes["partes"]
    assert partes["ativo"] == [["banco-do-brasil", 1], ["caixa", 1], ["estado-sp", 1]]
    assert partes["passivo"] == [["inss", 1], ["mpf", 1], ["uniao", 1]]
    assert (partes["com_pf"], partes["com_pj"], partes["com_ente"]) == (3, 2, 3)  # o processo em segredo não entra nas partes
    assert d["grupos"]["inss"] == "INSS" and d["grupos"]["uniao"] == "União"

    nancy = stj["ministros"]["stj-nancy-andrighi"]
    assert nancy["mes"]["itens"] == 2 and nancy["mes"]["tipos"] == {"acordao": 1, "decisao": 1}
    assert nancy["mesa"]["na_mesa"] == 4 and nancy["mesa"]["publicados"] == 1 and nancy["mesa"]["parte"] == 0.25
    [folha] = nancy["mesa"]["lista"]
    assert folha == {"id": "stj-AREsp-1", "classe": "AREsp", "numero": "1", "cnj": CNJ[1], "registro": "202600000001", "area": "civil",
                     "tipo": "decisao", "dia": "2026-11-20", "dias_sem_decisao": 400}
    # o link para o leitor sai do jeito de sempre (contas.link_oficial), com o trib
    assert contas.link_oficial({**folha, "trib": "stj"}, REGRAS).endswith("termo=202600000001")
    dantas = stj["ministros"]["stj-ribeiro-dantas"]  # a pauta não diz o relator: veio do acervo
    assert dantas["mes"]["tipos"] == {"pauta": 1} and dantas["mesa"]["publicados"] == 1 and dantas["mes"]["areas"] == {"penal": 1}
    raul = stj["ministros"]["stj-raul-araujo"]  # em segredo: conta na mesa, nunca na lista
    assert raul["mesa"]["publicados"] == 1 and raul["mesa"]["lista"] == []
    herman = stj["ministros"]["stj-herman-benjamin"]  # no cartório: publicado, mas não estava na mesa
    assert herman["mesa"]["publicados"] == 0 and herman["partes"] == ["estado-sp", "uniao"]
    assert stj["fora"]["presidencia"]["mes"]["itens"] == 1
    assert set(stj["orgaos"]) == {"T3", "T5"}
    assert stj["acervo"] == {"data": "2026-11-20", "processos": 7, "casados": 4, "processos_casados": 4}
    cob = stj["cobertura"]
    assert cob["canceladas"] == 1 and cob["segredo"] == 1 and cob["relator_por"] == {"acervo": 1, "orgao": 3, "texto": 2}
    assert ["Decisão", 2] in cob["tipos_brutos"] and ["GABINETE DA MINISTRA NANCY ANDRIGHI", 1] in cob["orgaos_brutos"]

    stf = d["tribunais"]["stf"]
    assert stf["publica"] is None and stf["ultimo_dia"] is None and stf["mes"]["itens"] == 0  # dois dias vazios ainda não dizem nada

    texto = (tmp_path / "data" / "tribunais" / "djen.json").read_text(encoding="utf-8")
    assert len(texto.encode()) < djen.ALVO_JSON
    for proibido in (CNJ[5], "CARLOS", "MARIA MODELO", "ADVOGADO", "OAB", "PARTE EXEMPLO", "BANCO EXEMPLO"):
        assert proibido not in texto, proibido  # o processo em segredo nunca aparece; pessoa e advogado, nunca


def test_mesmos_dados_mesmos_bytes_e_nada_de_novo_nada_gravado(tmp_path: Path) -> None:
    r, d, _ = _executar_com_amostra(tmp_path, sem_acervo=True)
    caminho = tmp_path / "data" / "tribunais" / "djen.json"
    antes = caminho.read_bytes()
    dias = {t: djen.dias_no_cache(tmp_path / "cache", t, *djen.janela(HOJE, _cfg())) for t in ("stj", "stf")}
    de_novo = djen.agregar(dias, {"stj": None, "stf": None}, REGRAS, CLASSIFICADOR, CATALOGO, _cfg(), HOJE, avisos=r.avisos)
    assert djen.serializar(de_novo).encode() == antes
    # sem retrato: nada de "mesa" (nem zero), e o acervo fica null
    stj = d["tribunais"]["stj"]
    assert stj["acervo"] is None and "mesa" not in stj["mes"] and stj["ministros"]["stj-nancy-andrighi"]["mesa"] is None
    # sem dia novo para coletar (teto 0): nenhum pedido à API e o arquivo fica como está
    relogio = _relogio()
    fetch = FetchDaAPI(_api({}))
    r2 = djen.executar(RAIZ, tmp_path, tmp_path / "cache", agora=AGORA, fetch=fetch, relogio=relogio, dormir=relogio.dormir,
                       cfg=_cfg(dias_por_execucao=0), sem_acervo=True)
    assert [u for u in fetch.urls() if u.startswith(djen.API)] == [] and not r2.gravado and caminho.read_bytes() == antes


def test_tribunal_que_nao_publica(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    for dia in ("2026-11-18", "2026-11-19", "2026-11-20"):
        djen.gravar_dia(cache, djen.Dia("stf", dia, count=0, completo=True, coletado_em="2026-11-23T12:00:00Z"))
    djen.gravar_dia(cache, djen.Dia("stj", "2026-11-20", count=0, completo=True, coletado_em="2026-11-23T12:00:00Z"))
    dias = {t: djen.dias_no_cache(cache, t, *djen.janela(HOJE, _cfg())) for t in ("stj", "stf")}
    d = djen.agregar(dias, {}, REGRAS, CLASSIFICADOR, CATALOGO, _cfg(), HOJE)
    assert d["tribunais"]["stf"]["publica"] is False and d["tribunais"]["stj"]["publica"] is None


def test_sem_retrato_o_acervo_e_refeito_e_a_falha_vira_aviso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chamados: list[str] = []

    def falha(coletor: Any, regras: Any, classificador: Any, tpu: Any, cache: Path, trib: str) -> Path:
        chamados.append(trib)
        raise FetchError("https://dadosabertos.web.stj.jus.br/dataset/acervo-em-tramitacao", "HTTP 403", status=403)

    monkeypatch.setattr(djen, "construir_retrato", falha)
    r, d, _ = _executar_com_amostra(tmp_path, cfg=_cfg(acervo={"baixar": True}))
    assert chamados == ["stj"]  # o STF não teve publicação: nada a casar, nada a baixar
    assert any("o acervo do STJ não pôde ser lido" in a for a in r.avisos) and d["tribunais"]["stj"]["acervo"] is None


def test_o_retrato_de_outra_pasta_e_o_vencido(tmp_path: Path) -> None:
    outra = tmp_path / "outra"
    caminho = _retrato(outra)
    agora = caminho.stat().st_mtime
    assert djen.localizar_retrato([tmp_path / "vazia", outra], "stj", validade_h=30, agora=agora) == caminho
    assert djen.localizar_retrato([outra], "stj", validade_h=30, agora=agora + 31 * 3600) is None
    assert djen.localizar_retrato([outra], "stf", validade_h=None, agora=agora) is None
    indice = djen.ler_indice(caminho, "stj", {CNJ[1], CNJ[5]})
    assert set(indice.processos) == {CNJ[1], CNJ[5]} and indice.na_mesa["stj-nancy-andrighi"] == 4 and indice.linhas == 7
    assert indice.processos[CNJ[5]]["segredo"] is True and indice.processos[CNJ[1]]["dias"] == 400


def test_o_json_grande_encolhe_e_nunca_passa_do_teto() -> None:
    folha = {"id": "stj-REsp-1", "classe": "REsp", "numero": "1", "cnj": "0" * 20, "area": "civil", "tipo": "decisao", "dia": "2026-11-20",
             "dias_sem_decisao": 1, "link": "https://processo.stj.jus.br/" + "x" * 60}
    ministros = {f"stj-m{i}": {"dia": {}, "mes": {}, "mesa": {"lista": [dict(folha) for _ in range(5)]}, "partes": []} for i in range(400)}
    dados = {"tribunais": {"stj": {"ministros": ministros, "orgaos": {"T1": {"x": "y" * 1000}},
                                   "cobertura": {"orgaos_brutos": [["o" * 100, 1]] * 40, "tipos_brutos": [["t", 1]] * 30}}}}
    texto = djen.serializar(dados)
    assert len(texto.encode()) <= djen.TETO_JSON


# ── a sonda ──────────────────────────────────────────────────────────────────────────────────


def test_a_sonda_poucos_pedidos_e_amostra_mascarada(tmp_path: Path) -> None:
    relogio = _relogio()
    pedidos: list[tuple[str, str, int]] = []
    fetch = FetchDaAPI(_api({("STJ", "2026-11-20", 1): _amostra("stj_2026-11-20_p1.json")}, pedidos))
    rel = djen.sondar(RAIZ, tmp_path / "cache", agora=AGORA, fetch=fetch, relogio=relogio, dormir=relogio.dormir)
    assert pedidos == [("STJ", "2026-11-20", 1), ("STF", "2026-11-20", 1), ("STF", "2026-11-19", 1), ("STF", "2026-11-18", 1)]
    assert len(rel["pedidos"]) == 5  # o robots.txt e as quatro páginas
    stj, stf = rel["tribunais"]["stj"], rel["tribunais"]["stf"]
    assert (stj["alcancavel"], stj["publica"], stj["count"], stj["filtro_respeitado"], stj["dia"]) == (True, True, 7, True, "2026-11-20")
    assert dict(stj["tipos_documento"]) == {"Decisão": 2, "Acórdão": 1, "Intimação": 1, "Despacho": 1}
    assert (stf["alcancavel"], stf["publica"]) == (True, False)
    assert rel["navegador"]["stj"] == djen.url_para_navegador("stj", "2026-11-20")
    amostra = json.loads(Path(stj["amostra"]).read_text(encoding="utf-8"))
    texto = json.dumps(amostra, ensure_ascii=False)
    for proibido in ("CARLOS", "MARIA MODELO", "12345678909", "Cuida-se", "PARTE EXEMPLO", CNJ[1], "A. B. C."):
        assert proibido not in texto, proibido
    nomes = [d["nome"] for x in amostra["items"] for d in x["destinatarios"]]
    assert "BANCO DO BRASIL S/A" in nomes and "INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS" in nomes and "PARTE 2 (mascarada)" in nomes
    assert {a["advogado"]["numero_oab"] for x in amostra["items"] for a in x["destinatarioadvogados"]} == {"000000"}
    assert amostra["items"][1]["texto"] == "RELATOR : MINISTRA NANCY ANDRIGHI<br>[resto do texto removido na amostra]"
    assert amostra["items"][0]["texto"].startswith("DECISÃO<br>RELATOR : MINISTRA NANCY ANDRIGHI")
    # a amostra mascarada volta a ser lida como a original (vira fixture)
    leitor = _leitor()
    assert [leitor.ler(x)["tipo"] for x in amostra["items"]] == ["decisao", "acordao", "pauta", "decisao", "despacho"]
    assert "Sonda do DJEN" in djen.relatorio_da_sonda(rel) and "NÃO" not in djen.relatorio_da_sonda(rel)
    assert (tmp_path / "cache" / "djen" / "sonda" / "sonda.json").is_file()


def test_a_sonda_recusada_um_pedido_so(tmp_path: Path) -> None:
    relogio = _relogio()
    fetch = FetchFalso({ROBOTS: recusa(ROBOTS)})
    rel = djen.sondar(RAIZ, tmp_path / "cache", agora=AGORA, fetch=fetch, relogio=relogio, dormir=relogio.dormir)
    assert fetch.urls() == [ROBOTS]
    assert rel["tribunais"]["stj"]["alcancavel"] is False and rel["tribunais"]["stf"]["alcancavel"] is False
    assert "NÃO alcançável" in djen.relatorio_da_sonda(rel)


# ── a linha de comando ───────────────────────────────────────────────────────────────────────


def test_a_linha_de_comando(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    parser = principal.build_parser()
    a = parser.parse_args(["tribunais", "djen", "--dir", ".", "--cache", "/tmp/c", "--completo", "--so", "stj", "--acervo", "/x",
                           "--acervo", "/y", "--sem-acervo"])
    assert (a.handler, a.dir, a.cache, a.completo, a.so, a.acervo, a.sem_acervo, a.sondar) == (
        cli.cmd_djen, ".", "/tmp/c", True, "stj", ["/x", "/y"], True, False)
    a = parser.parse_args(["tribunais", "djen", "--sondar", "--dia", "2026-11-20"])
    assert (a.sondar, a.dia, a.cache) == (True, "2026-11-20", cli.CACHE_PADRAO)
    chamadas: dict[str, Any] = {}

    def executar(raiz: Path, saida: Path, cache: Path, **kw: Any) -> djen.Execucao:
        chamadas["executar"] = (saida, cache, kw)
        return djen.Execucao(gravado=True, caminho=saida / "djen.json", coletados={"stj": ["2026-11-20"]}, avisos=["DJEN: aviso de teste"])

    def sondar(raiz: Path, cache: Path, **kw: Any) -> dict[str, Any]:
        chamadas["sondar"] = (cache, kw)
        return {"quando": "agora", "api": djen.API, "tribunais": {"stj": {"alcancavel": False, "recusa": "403"}}, "navegador": {}}

    monkeypatch.setattr(djen, "executar", executar)
    monkeypatch.setattr(djen, "sondar", sondar)
    config = principal.load_config(env={})
    args = argparse.Namespace(dir=str(tmp_path), cache=str(tmp_path / "c"), sondar=False, dia=None, completo=False, so=None,
                              acervo=[], sem_acervo=False, now=AGORA)
    assert cli.cmd_djen(args, config) == cli.EXIT_OK
    saida, cache, kw = chamadas["executar"]
    assert (saida, cache, kw["agora"], kw["completo"]) == (tmp_path, tmp_path / "c", AGORA, False)
    args.sondar, args.dia = True, "2026-11-20"
    assert cli.cmd_djen(args, config) == cli.EXIT_OK
    assert chamadas["sondar"][1]["dia"] == "2026-11-20" and "NÃO alcançável" in capsys.readouterr().out
    desligado = principal.load_config(env={"QIJ_NO_TRIBUNAIS": "1"})
    chamadas.clear()
    assert cli.cmd_djen(args, desligado) == cli.EXIT_OK and not chamadas


def test_o_bloco_djen_do_config_do_repositorio() -> None:
    cfg = djen.carregar_config(RAIZ)
    assert cfg["ativo"] is True and cfg["tribunais"] == ["stj", "stf"] and cfg["api"] == djen.API
    assert 1 <= cfg["itens_por_pagina"] <= 100 and cfg["pausa_s"] >= 1 and cfg["incluir_hoje"] is False
    ids = [t["id"] for t in cfg["tipos"]]
    assert len(ids) == len(set(ids)) and djen.OUTROS not in ids
    assert all(p == p.upper() and p.isascii() for t in cfg["tipos"] for p in t["padroes"])  # comparados sem acento, em maiúsculas
    carregar_regras(RAIZ)  # o modelo ignora a chave nova


# ── a tarefa agendada (scripts/djen/) ────────────────────────────────────────────────────────

SCRIPTS = RAIZ / "scripts" / "djen"


def test_os_agendadores() -> None:
    for nome in ("rodar_djen.sh", "instalar.sh"):
        assert subprocess.run(["bash", "-n", str(SCRIPTS / nome)], check=False).returncode == 0, nome
        assert os.access(SCRIPTS / nome, os.X_OK), nome
    plist = plistlib.loads((SCRIPTS / "br.triangula.djen.plist").read_bytes())
    assert plist["ProgramArguments"] == ["/bin/bash", "@REPO@/scripts/djen/rodar_djen.sh"]
    assert {(x["Hour"], x["Minute"]) for x in plist["StartCalendarInterval"]} == {(8, 17), (20, 47)}
    timer = (SCRIPTS / "triangula-djen.timer").read_text(encoding="utf-8")
    assert "Persistent=true" in timer and "OnCalendar=*-*-* 08:17:00" in timer
    servico = (SCRIPTS / "triangula-djen.service").read_text(encoding="utf-8")
    assert "ExecStart=/bin/bash @REPO@/scripts/djen/rodar_djen.sh" in servico
    janelas = (SCRIPTS / "instalar_windows.ps1").read_text(encoding="utf-8")
    assert "-StartWhenAvailable" in janelas and "rodar_djen.ps1" in janelas
    ps = (SCRIPTS / "rodar_djen.ps1").read_text(encoding="utf-8")
    assert "tribunais djen --dir . --cache" in ps and "data/tribunais/djen.json" in ps
    # Nenhum fluxo do GitHub Actions coleta o DJEN (a API recusa fora do Brasil; runner próprio num
    # repositório público seria porta para código de fork na máquina do dono).
    for fluxo in (RAIZ / ".github" / "workflows").glob("*.yml"):
        texto = fluxo.read_text(encoding="utf-8")
        assert "tribunais djen" not in texto and "self-hosted" not in texto, fluxo.name


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="sem git")
def test_rodar_djen_envia_so_o_djen_e_reaplica_sobre_o_remoto(tmp_path: Path) -> None:
    """De ponta a ponta com git de verdade: um remoto, o clone do robô e um "robô do GitHub" que
    publica no meio da coleta. O commit do DJEN só toca ``data/tribunais/djen.json`` e entra por cima."""
    remoto, robo, outro = tmp_path / "remoto.git", tmp_path / "robo", tmp_path / "outro"
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t.invalid", "HOME": str(tmp_path)}
    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(remoto)], check=True, env=env)
    subprocess.run(["git", "clone", "--quiet", str(remoto), str(outro)], check=True, env=env)
    (outro / "data" / "tribunais").mkdir(parents=True)
    (outro / "data" / "tribunais" / "meta.json").write_text("{}\n")
    (outro / "scripts" / "djen").mkdir(parents=True)
    shutil.copy(SCRIPTS / "rodar_djen.sh", outro / "scripts" / "djen" / "rodar_djen.sh")
    for args in (["checkout", "--quiet", "-b", "main"], ["add", "-A"], ["commit", "--quiet", "-m", "início"], ["push", "--quiet", "origin", "main"]):
        subprocess.run(["git", *args], cwd=outro, check=True, env=env)
    subprocess.run(["git", "clone", "--quiet", str(remoto), str(robo)], check=True, env=env)
    # A "coleta" grava o djen.json e, no meio, outro robô publica no remoto (o primeiro envio é recusado).
    comando = (
        f"mkdir -p data/tribunais && echo '{{\"v\":1}}' > data/tribunais/djen.json && "
        f"cd {outro} && echo '{{\"x\":1}}' > data/tribunais/meta.json && git commit --quiet -am 'tribunais' && git push --quiet origin main"
    )
    rodar = {**env, "QIJ_DJEN_REPO": str(robo), "QIJ_DJEN_CACHE": str(tmp_path / "cache"), "QIJ_DJEN_COMANDO": comando,
             "QIJ_DJEN_ESPERA_S": "0"}
    saida = subprocess.run(["bash", str(robo / "scripts" / "djen" / "rodar_djen.sh")], env=rodar, capture_output=True, text=True, check=False)
    log = next((tmp_path / "cache" / "logs").glob("*.log")).read_text(encoding="utf-8")
    assert saida.returncode == 0, log
    assert "Enviado:" in log
    subprocess.run(["git", "pull", "--quiet", "origin", "main"], cwd=outro, check=True, env=env)
    ultimo, anterior = _git("log", "--format=%s", cwd=outro).splitlines()[:2]
    assert ultimo.startswith("⚖️ DJEN em ") and ultimo.endswith(" (máquina no Brasil)") and anterior == "tribunais"
    assert _git("show", "--name-only", "--format=", "HEAD", cwd=outro) == "data/tribunais/djen.json"
    assert json.loads((outro / "data" / "tribunais" / "meta.json").read_text()) == {"x": 1}  # o que o outro robô publicou fica
    # De novo, sem mudança: nada a enviar, sair 0.
    rodar["QIJ_DJEN_COMANDO"] = "true"
    assert subprocess.run(["bash", str(robo / "scripts" / "djen" / "rodar_djen.sh")], env=rodar, check=False).returncode == 0
    # Clone com alteração local: recusa (nada coletado, nada enviado).
    (robo / "data" / "tribunais" / "meta.json").write_text("mexido\n")
    assert subprocess.run(["bash", str(robo / "scripts" / "djen" / "rodar_djen.sh")], env=rodar, check=False).returncode == 1


def test_o_dia_no_cache_e_enxuto_e_versionado(tmp_path: Path) -> None:
    d = djen.Dia("stj", "2026-11-20", itens=[{"id": 1}], count=1, paginas=1, completo=True, coletado_em="2026-11-21T10:00:00Z")
    caminho = djen.gravar_dia(tmp_path, d)
    assert caminho == tmp_path / "djen" / "stj" / "2026-11-20.json.gz"
    with gzip.open(caminho, "rt", encoding="utf-8") as f:
        assert json.load(f)["v"] == djen.VERSAO_DJEN
    assert djen.ler_dia(tmp_path, "stj", "2026-11-20") == d
    caminho.write_bytes(b"lixo")
    assert djen.ler_dia(tmp_path, "stj", "2026-11-20") is None
    djen.gravar_dia(tmp_path, djen.Dia("stj", "2026-09-01"))
    assert djen.limpar_cache(tmp_path, HOJE, 40) == 1
