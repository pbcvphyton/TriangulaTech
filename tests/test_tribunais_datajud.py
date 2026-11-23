"""O cliente opcional do DataJud (qijournal/tribunais/datajud.py): espera crescente, disjuntor, prazo,
teto de 120 pedidos por minuto, só resposta completa (HTTP 200 e ``_shards.failed == 0``), mojibake
dos nomes e a rodada de ``atualizar`` (desligado por padrão: nenhum pedido).

Sem rede: ``fetch`` falso, relógio e sono falsos (nenhuma espera de verdade). Fixtures reais e
pequenas em ``tests/fixtures/tribunais/``:

- ``datajud_frescor.json``: resposta real da consulta de frescor de 22/11/2026 22:24 UTC (3 de 3
  shards; ``max(@timestamp)`` = 09/11/2026 03:21:46), só com as duas agregações que a C.0 pede;
- ``datajud_ativos_por_gabinete.json``: as contagens da única resposta completa de 23/11/2026
  00:44 UTC (59 órgãos, 345.421 ativos, 3 de 3 shards) com o nome de cada órgão como o índice o
  devolve (com mojibake), tirado do catálogo real de 22/11/2026 22:32 UTC, no formato da consulta
  que o cliente manda;
- ``datajud_parcial.json``: resposta real de 23/11/2026 00:36 UTC, HTTP 200 com 2 de 3 shards
  falhos (motivos encurtados, só os 5 primeiros órgãos).

Os nomes nas fixtures são de gabinetes de ministros e de desembargadores convocados (agentes
públicos), como o CNJ os publica; nenhuma parte nem advogado.
"""

from __future__ import annotations

import dataclasses
import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from qijournal import net
from qijournal.net import FetchError
from qijournal.tribunais import datajud
from qijournal.tribunais.datajud import (
    ClienteDataJud,
    ConsultaRecusada,
    ConsultaSemResposta,
    DisjuntorAberto,
)
from qijournal.tribunais.fontes import ColetorTribunais, RespostaParcial
from qijournal.tribunais.modelo import Regras, carregar_regras
from tests.fixtures import tribunais as fx

URL = "https://api-publica.datajud.cnj.jus.br/api_publica_stj/_search"
ROBOTS = "https://api-publica.datajud.cnj.jus.br/robots.txt"
FRESCOR = json.loads(fx.ler("datajud_frescor.json"))
ATIVOS = json.loads(fx.ler("datajud_ativos_por_gabinete.json"))
PARCIAL = json.loads(fx.ler("datajud_parcial.json"))
AGORA = datetime(2026, 11, 23, 9, 50, 0, tzinfo=UTC)
HOJE = date(2026, 11, 23)


class Relogio:
    """Relógio que só anda quando alguém dorme; guarda cada espera."""

    def __init__(self) -> None:
        self.t = time.time()
        self.esperas: list[float] = []

    def __call__(self) -> float:
        return self.t

    def dormir(self, segundos: float) -> None:
        self.esperas.append(round(segundos, 3))
        self.t += max(0.0, segundos)


class FetchFalso:
    """Uma resposta por POST, na ordem: dicionário (HTTP 200 com esse JSON), ``(status, corpo)``,
    ``bytes`` (corpo cru) ou exceção. O robots.txt do DataJud responde 404 (tudo permitido)."""

    def __init__(self, respostas: list[Any], relogio: Relogio | None = None) -> None:
        self.respostas = list(respostas)
        self.relogio = relogio
        self.posts: list[dict[str, Any]] = []
        self.robots = 0

    def __call__(self, url: str, **kw: Any) -> net.Response:
        if url == ROBOTS:
            self.robots += 1
            raise FetchError(url, "HTTP 404", status=404)
        self.posts.append({"url": url, "quando": self.relogio() if self.relogio else None, **kw})
        if not self.respostas:
            raise AssertionError("pedido a mais ao DataJud")
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        status, corpo = r if isinstance(r, tuple) else (200, r)
        conteudo = corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode("utf-8")
        return net.Response(url=url, status=status, content=conteudo, headers={"content-type": "application/json; charset=utf-8"})


def http(status: int) -> FetchError:
    return FetchError(URL, f"HTTP {status}", status=status)


QUEDA = FetchError(URL, "URLError: <urlopen error [Errno 104] Connection reset by peer>")


@pytest.fixture(scope="module")
def regras() -> Regras:
    return carregar_regras()


@pytest.fixture(scope="module")
def ligado(regras: Regras) -> Regras:
    return dataclasses.replace(regras, datajud={**regras.datajud, "ativo": True})


def montar(tmp_path: Path, regras: Regras, respostas: list[Any], *, aleatorio: float = 0.5, **kw: Any) -> tuple[ClienteDataJud, FetchFalso, Relogio]:
    relogio = Relogio()
    fetch = FetchFalso(respostas, relogio)
    coletor = ColetorTribunais(tmp_path / "cache", regras=regras, fetch=fetch, relogio=relogio, dormir=relogio.dormir)
    cliente = ClienteDataJud.do_config(regras, coletor, ambiente={}, relogio=relogio, dormir=relogio.dormir, aleatorio=lambda: aleatorio, **kw)
    return cliente, fetch, relogio


# ── o que o config manda ─────────────────────────────────────────────────────────────────────


def test_desligado_por_padrao_nao_pede_nada(tmp_path: Path, regras: Regras) -> None:
    """Com ``datajud.ativo: false`` (o padrão do config), nenhum pedido sai e a fonte do
    ``meta.json`` é a do contrato: ``ok: null``, ``ativo: false`` e o porquê."""
    assert regras.datajud["ativo"] is False
    fetch = FetchFalso([])
    coletor = ColetorTribunais(tmp_path, regras=regras, fetch=fetch)
    feito = datajud.atualizar(regras, coletor, agora=AGORA)
    assert fetch.posts == [] and fetch.robots == 0
    assert feito.fonte == {
        "nome": "CNJ · DataJud (API Pública)", "trib": "cnj", "url": "https://datajud-wiki.cnj.jus.br/api-publica/",
        "data_dados": None, "coletado_em": None, "ok": None, "ativo": False,
        "aviso": "Desligado: o termo de uso (itens 3.3, 3.8, 3.9 e 3.13) e a instabilidade do serviço; a página não usa número do DataJud.",
        "registros": None, "velho_dias": 15,
    }  # fmt: skip
    assert feito.conferencia is None and feito.mudou is False and "desligado" in feito.log


def test_os_numeros_do_cliente_vem_do_config(tmp_path: Path, ligado: Regras) -> None:
    cliente, _, _ = montar(tmp_path, ligado, [])
    assert (cliente.url, cliente.timeout_s, cliente.tentativas, cliente.espera_s, cliente.variacao) == (URL, 90.0, 6, (5.0, 10.0, 20.0, 40.0, 80.0), 0.2)
    assert (cliente.limite_falhas, cliente.prazo_s, cliente.por_minuto) == (3, 720.0, 120)


@pytest.mark.parametrize(("ambiente", "esperada"), [({}, "config"), ({"DATAJUD_API_KEY": "  "}, "config"), ({"DATAJUD_API_KEY": " chave-nova== "}, "chave-nova==")])
def test_a_variavel_troca_a_chave_do_config(ligado: Regras, ambiente: dict[str, str], esperada: str) -> None:
    chave = datajud.chave_da_api(ligado.datajud, ambiente)
    assert chave == (ligado.datajud["chave"] if esperada == "config" else esperada)


def test_o_pedido_leva_a_chave_o_corpo_canonico_e_o_tempo_limite(tmp_path: Path, ligado: Regras) -> None:
    """POST com ``Authorization: APIKey <chave>``, JSON canônico (o cache do coletor e o do
    Elasticsearch dependem do corpo igual), 90 s de tempo-limite e nenhuma retentativa escondida
    no ``net.fetch`` (o laço de espera é o do cliente)."""
    relogio = Relogio()
    fetch = FetchFalso([FRESCOR], relogio)
    coletor = ColetorTribunais(tmp_path, regras=ligado, fetch=fetch, relogio=relogio, dormir=relogio.dormir)
    cliente = ClienteDataJud.do_config(ligado, coletor, ambiente={"DATAJUD_API_KEY": "abc=="}, relogio=relogio, dormir=relogio.dormir)
    assert cliente.frescor() == {"max_timestamp": "2026-11-09T03:21:46Z", "data": "2026-11-09", "max_atualizacao": "2026-11-09T03:21:46Z"}
    (post,) = fetch.posts
    assert post["method"] == "POST" and post["timeout"] == 90.0 and post["retries"] == 0
    assert post["headers"]["Authorization"] == "APIKey abc==" and post["headers"]["Content-Type"] == "application/json"
    assert post["data"] == b'{"aggs":{"max_ts":{"max":{"field":"@timestamp"}},"max_upd":{"max":{"field":"dataHoraUltimaAtualizacao"}}},"size":0}'
    assert fetch.robots == 1  # o robots.txt do host foi lido antes (404: tudo permitido)
    assert not any("abc==" in json.dumps(d) for d in coletor.diario)  # a chave não vai ao diário


# ── tentativas, esperas e o disjuntor ────────────────────────────────────────────────────────


def test_429_504_parcial_e_queda_valem_nova_tentativa_ate_a_completa(tmp_path: Path, ligado: Regras) -> None:
    """A sequência que a sonda de 23/11 viu: 429 (fila do Elasticsearch cheia), 504 do balanceador,
    HTTP 200 com 2 de 3 shards falhos, conexão derrubada; a quinta tentativa volta completa. As
    esperas entre elas são 5, 10, 20 e 40 s (sorteio no meio: fator 1,0)."""
    cliente, fetch, _ = montar(tmp_path, ligado, [http(429), http(504), PARCIAL, QUEDA, ATIVOS])
    ativos = cliente.ativos_por_gabinete()
    assert ativos["total"] == 345421 and len(fetch.posts) == 5
    assert cliente.esperas == [5.0, 10.0, 20.0, 40.0]
    p = cliente.placar
    assert (p.consultas, p.completas, p.tentativas, p.parciais, p.quedas, dict(p.por_status)) == (1, 1, 5, 1, 1, {429: 1, 504: 1})
    assert p.texto().startswith("5 tentativas, 1 completa (1 parcial, 1×429, 1×504, 1 queda de conexão), ")
    assert cliente.falhas_seguidas == 0 and not cliente.aberto


@pytest.mark.parametrize(("sorteio", "fator"), [(0.0, 0.8), (0.999999, 1.2)])
def test_as_esperas_variam_20_por_cento_para_cada_lado(tmp_path: Path, ligado: Regras, sorteio: float, fator: float) -> None:
    cliente, _, _ = montar(tmp_path, ligado, [http(429)] * 6, aleatorio=sorteio)
    with pytest.raises(ConsultaSemResposta, match="6 tentativas sem resposta completa"):
        cliente.frescor()
    assert cliente.esperas == pytest.approx([5 * fator, 10 * fator, 20 * fator, 40 * fator, 80 * fator], abs=1e-3)


def test_tres_consultas_seguidas_sem_resposta_abrem_o_disjuntor(tmp_path: Path, ligado: Regras) -> None:
    """Seis tentativas por consulta; na terceira consulta seguida sem resposta completa, o DataJud
    para nesta execução: a quarta consulta levanta sem pedir nada."""
    cliente, fetch, _ = montar(tmp_path, ligado, [http(429), http(504), QUEDA] * 6)
    for n in range(1, 4):
        with pytest.raises(ConsultaSemResposta):
            cliente.frescor()
        assert len(fetch.posts) == 6 * n and cliente.falhas_seguidas == n
    assert cliente.aberto and "3 consultas seguidas" in (cliente.motivo or "")
    with pytest.raises(DisjuntorAberto, match="parado nesta execução"):
        cliente.ativos_por_gabinete()
    assert len(fetch.posts) == 18 and cliente.placar.completas == 0


def test_uma_resposta_completa_zera_a_conta_do_disjuntor(tmp_path: Path, ligado: Regras) -> None:
    cliente, _, _ = montar(tmp_path, ligado, [http(429)] * 6 * 2 + [FRESCOR] + [http(504)] * 6 * 2, espera_s=[0.0])
    for _ in range(2):
        with pytest.raises(ConsultaSemResposta):
            cliente.frescor()
    assert cliente.falhas_seguidas == 2
    cliente.frescor()
    assert cliente.falhas_seguidas == 0
    for _ in range(2):
        with pytest.raises(ConsultaSemResposta):
            cliente.frescor()
    assert cliente.falhas_seguidas == 2 and not cliente.aberto


def test_o_prazo_da_fonte_para_antes_de_uma_espera_que_nao_cabe(tmp_path: Path, ligado: Regras) -> None:
    """Com 30 s de prazo: 429, espera 5 s, 429, espera 10 s, 429; a espera seguinte (20 s) passaria
    do prazo, então o cliente para na hora (não dorme à toa) e o DataJud fica parado."""
    cliente, fetch, relogio = montar(tmp_path, ligado, [http(429)] * 6, prazo_s=30)
    with pytest.raises(ConsultaSemResposta, match="prazo"):
        cliente.frescor()
    assert len(fetch.posts) == 3 and cliente.esperas == [5.0, 10.0]
    assert cliente.aberto and "prazo" in (cliente.motivo or "") and max(relogio.esperas) == 10.0


@pytest.mark.parametrize("erro", [http(401), http(403), http(404)])
def test_chave_recusada_ou_indice_errado_param_o_datajud_na_hora(tmp_path: Path, ligado: Regras, erro: FetchError) -> None:
    """401/403 (chave; o host tem ``recusa_espera_s: null``, então o 403 não espera 120 s) e 404
    (índice): um pedido e o DataJud para nesta execução, sem espera nenhuma."""
    cliente, fetch, relogio = montar(tmp_path, ligado, [erro, FRESCOR])
    with pytest.raises(DisjuntorAberto):
        cliente.frescor()
    with pytest.raises(DisjuntorAberto):
        cliente.frescor()
    assert len(fetch.posts) == 1 and cliente.esperas == [] and all(e < 1 for e in relogio.esperas)


def test_consulta_errada_400_nao_repete_mas_conta_para_o_disjuntor(tmp_path: Path, ligado: Regras) -> None:
    cliente, fetch, _ = montar(tmp_path, ligado, [http(400)])
    with pytest.raises(ConsultaRecusada, match="HTTP 400"):
        cliente.frescor()
    assert len(fetch.posts) == 1 and cliente.falhas_seguidas == 1 and not cliente.aberto


@pytest.mark.parametrize(
    "ruim",
    [
        b'{"took": 4, "_shards": {"total": 3, "succ',  # JSON cortado
        {"took": 4, "aggregations": {}},  # sem _shards
        {"took": 4, "timed_out": False, "_shards": {"total": 3, "successful": 2, "skipped": 0, "failed": 0}},  # um shard sumiu
        {"took": 4, "timed_out": True, "_shards": {"total": 3, "successful": 3, "failed": 0}},
        (203, FRESCOR),  # completo, mas não HTTP 200
    ],
)
def test_so_vale_http_200_com_todos_os_shards(tmp_path: Path, ligado: Regras, ruim: Any) -> None:
    cliente, fetch, _ = montar(tmp_path, ligado, [ruim, FRESCOR])
    assert cliente.frescor()["data"] == "2026-11-09"
    assert len(fetch.posts) == 2 and cliente.esperas == [5.0]


def test_resposta_que_nao_fecha_a_conta_e_pedida_de_novo(tmp_path: Path, ligado: Regras) -> None:
    """Gabinetes que somam mais que o total, total "≥" em vez de exato e balde sem código: a
    resposta não vale e a consulta é repetida (nada de número errado sem aviso)."""
    nao_fecha = {**ATIVOS, "hits": {"total": {"value": 300000, "relation": "eq"}}}
    limite = {**ATIVOS, "hits": {"total": {"value": 10000, "relation": "gte"}}}
    sem_codigo = {**ATIVOS, "aggregations": {"orgao": {"buckets": [{"doc_count": 3}]}}}
    cliente, fetch, _ = montar(tmp_path, ligado, [nao_fecha, limite, sem_codigo, ATIVOS])
    assert cliente.ativos_por_gabinete()["total"] == 345421
    assert len(fetch.posts) == 4 and cliente.placar.parciais == 3


def test_no_maximo_120_pedidos_por_minuto(tmp_path: Path, ligado: Regras) -> None:
    """O item 3.13 do termo, com o teto encolhido para 3 por minuto e espera zero entre as
    tentativas: o quarto pedido espera a janela de 60 s do primeiro."""
    cliente, fetch, _ = montar(tmp_path, ligado, [http(429)] * 5 + [FRESCOR], espera_s=[0.0], por_minuto=3)
    cliente.frescor()
    quando = [p["quando"] for p in fetch.posts]
    assert len(quando) == 6
    for i in range(len(quando)):
        assert sum(1 for t in quando if 0 <= t - quando[i] < 60) <= 3
    assert quando[3] - quando[0] >= 60.0 and quando[5] - quando[2] >= 60.0


def test_robots_que_veda_o_caminho_para_o_datajud_sem_pedido(tmp_path: Path, ligado: Regras) -> None:
    relogio = Relogio()

    def fetch(url: str, **kw: Any) -> net.Response:
        if url == ROBOTS:
            return net.Response(url=url, status=200, content=b"User-agent: *\nDisallow: /api_publica_stj/\n", headers={})
        raise AssertionError("pedido ao DataJud com o caminho vedado")

    coletor = ColetorTribunais(tmp_path, regras=ligado, fetch=fetch, relogio=relogio, dormir=relogio.dormir)
    cliente = ClienteDataJud.do_config(ligado, coletor, ambiente={}, relogio=relogio, dormir=relogio.dormir)
    with pytest.raises(DisjuntorAberto, match="robots.txt"):
        cliente.frescor()
    assert cliente.aberto and cliente.placar.tentativas == 0


# ── as respostas ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("bruto", "certo"),
    [
        ("GABINETE DO MINISTRO PAULO SÃ\x89RGIO DOMINGUES", "GABINETE DO MINISTRO PAULO SÉRGIO DOMINGUES"),
        ("PRESIDÃ\x8aNCIA", "PRESIDÊNCIA"),
        ("Superior Tribunal de JustiÃ§a", "Superior Tribunal de Justiça"),
        ("DESEMBARGADOR CONVOCADO DO TRF 1Âª REGIÃ\x83O", "DESEMBARGADOR CONVOCADO DO TRF 1ª REGIÃO"),
        ("GABINETE DO MINISTRO JOÃO OTÁVIO DE NORONHA", "GABINETE DO MINISTRO JOÃO OTÁVIO DE NORONHA"),  # já certo: fica
        ("GABINETE DA MINISTRA NANCY ANDRIGHI", "GABINETE DA MINISTRA NANCY ANDRIGHI"),
        ("", ""),
        (None, None),
    ],
)
def test_mojibake_dos_nomes_de_orgao(bruto: str | None, certo: str | None) -> None:
    assert datajud.corrigir_mojibake(bruto) == certo


def test_ativos_por_gabinete_da_resposta_real() -> None:
    ativos = datajud.ler_ativos(ATIVOS)
    assert ativos["total"] == 345421 and ativos["fora_da_lista"] == 0 and len(ativos["gabinetes"]) == 59
    assert sum(g["n"] for g in ativos["gabinetes"]) == 345421
    assert ativos["gabinetes"][0] == {"codigo": 76789, "nome": "PRESIDÊNCIA", "n": 93894}
    assert {"codigo": 85154, "nome": "GABINETE DO MINISTRO PAULO SÉRGIO DOMINGUES", "n": 10317} in ativos["gabinetes"]
    nomes = " ".join(g["nome"] or "" for g in ativos["gabinetes"])
    assert not any("\x80" <= c <= "\x9f" for c in nomes) and "Ã§" not in nomes and "Âª" not in nomes  # nada de mojibake
    assert "GABINETE DO MINISTRO FRANCISCO FALCÃO" in nomes and "TRF 1ª REGIÃO" in nomes  # o "Ã" de verdade fica
    com_limite = {**ATIVOS, "hits": {"total": {"value": 10000, "relation": "gte"}}}
    with pytest.raises(RespostaParcial, match="limite"):
        datajud.ler_ativos(com_limite)
    with pytest.raises(RespostaParcial, match="somam mais"):
        datajud.ler_ativos({**ATIVOS, "hits": {"total": {"value": 300000, "relation": "eq"}}})


def test_conferencia_com_o_acervo_do_stj(regras: Regras) -> None:
    """Os gabinetes casam com a composição depois de corrigido o mojibake (Paulo Sérgio Domingues,
    João Otávio de Noronha); os de quem saiu (Og Fernandes) ficam sem ministro: o acervo já está
    com outro relator no STJ. Totais: 345.421 no DataJud (carga de 09/11) × 352.239 no arquivo do
    STJ de 20/11 = −1,94%."""
    stj = {"stj-nancy-andrighi": 6225, "stj-paulo-sergio-domingues": 10400}
    c = datajud.conferir(datajud.ler_ativos(ATIVOS), regras.composicao["stj"], stj, total_stj=352239)
    assert (c["total_datajud"], c["total_stj"], c["diferenca"]) == (345421, 352239, -0.0194)
    assert (c["capa_generica"], c["presidencia"], c["vice"]) == (27552, 93894, 12493)
    linha = {g["codigo"]: g for g in c["gabinetes"]}
    assert linha[76767] == {"codigo": 76767, "nome": "GABINETE DA MINISTRA NANCY ANDRIGHI", "ministro": "stj-nancy-andrighi", "datajud": 6174, "stj": 6225}
    assert linha[85154]["ministro"] == "stj-paulo-sergio-domingues" and linha[85154]["stj"] == 10400
    assert linha[76756]["ministro"] == "stj-joao-otavio-de-noronha" and linha[76756]["stj"] is None
    assert linha[76770]["ministro"] is None and linha[76770]["nome"] == "GABINETE DO MINISTRO OG FERNANDES"
    assert linha[76789]["ministro"] is None  # a Presidência não é gabinete de ministro
    assert c["casados"]["gabinetes"] + c["sem_ministro"]["gabinetes"] == 59
    assert c["casados"]["datajud"] + c["sem_ministro"]["datajud"] == 345421 and c["casados"]["stj"] == 6225 + 10400
    sem_correcao = datajud.conferir(
        {"total": 1, "gabinetes": [{"codigo": 85154, "nome": "GABINETE DO MINISTRO PAULO SÃ\x89RGIO DOMINGUES", "n": 1}]}, regras.composicao["stj"]
    )
    assert sem_correcao["gabinetes"][0]["ministro"] is None  # sem a correção, não casaria
    assert [g["datajud"] for g in c["gabinetes"]] == sorted((g["datajud"] for g in c["gabinetes"]), reverse=True)


# ── a rodada (atualizar) ─────────────────────────────────────────────────────────────────────


def test_atualizar_ligado_frescor_novo_refaz_a_conferencia(tmp_path: Path, ligado: Regras) -> None:
    cliente, fetch, _ = montar(tmp_path, ligado, [FRESCOR, ATIVOS])
    painel = {"universo": {"processos": 352239}, "ministros": [{"id": "stj-nancy-andrighi", "processos": 6225}]}
    feito = datajud.atualizar(ligado, None, agora=AGORA, cliente=cliente, painel_stj=painel)
    assert len(fetch.posts) == 2 and feito.mudou is True
    assert feito.fonte == {
        "nome": "CNJ · DataJud (API Pública)", "trib": "cnj", "url": "https://datajud-wiki.cnj.jus.br/api-publica/",
        "data_dados": "2026-11-09", "coletado_em": "2026-11-23T09:50:00Z", "ok": True, "ativo": True, "aviso": None,
        "registros": 345421, "velho_dias": 15,
    }  # fmt: skip
    conf = feito.conferencia
    assert conf is not None and conf["max_timestamp"] == "2026-11-09T03:21:46Z" and conf["data_dados"] == "2026-11-09"
    assert conf["diferenca"] == -0.0194 and conf["casados"]["stj"] == 6225
    assert list(conf)[:3] == ["max_timestamp", "data_dados", "total_datajud"]  # ordem estável (mesmos dados, mesmos bytes)
    assert feito.log.startswith("DataJud: carga de 09/11, 345421 ativos em 59 órgãos, 2 tentativas, 2 completas")


def test_atualizar_sem_carga_nova_faz_uma_consulta_so(tmp_path: Path, ligado: Regras) -> None:
    """``max(@timestamp)`` igual ao da conferência anterior: nenhuma outra consulta (ser gentil com
    o CNJ); a conta é refeita com o acervo do STJ do dia e ``coletado_em`` não muda."""
    cliente, fetch, _ = montar(tmp_path, ligado, [FRESCOR])
    antes = datajud.atualizar(ligado, None, agora=AGORA, cliente=montar(tmp_path / "a", ligado, [FRESCOR, ATIVOS])[0], total_stj=352239)
    feito = datajud.atualizar(
        ligado, None, agora=datetime(2026, 11, 24, 9, 0, tzinfo=UTC), cliente=cliente, anterior_fonte=antes.fonte,
        anterior_conferencia=antes.conferencia, stj_por_ministro={"stj-nancy-andrighi": 6300}, total_stj=353000,
    )  # fmt: skip
    assert len(fetch.posts) == 1 and feito.mudou is False and "sem carga nova desde 09/11" in feito.log
    assert feito.fonte["ok"] is True and feito.fonte["coletado_em"] == "2026-11-23T09:50:00Z" and feito.fonte["registros"] == 345421
    assert feito.conferencia is not None and feito.conferencia["total_stj"] == 353000
    assert {g["codigo"]: g["stj"] for g in feito.conferencia["gabinetes"]}[76767] == 6300
    assert [g["datajud"] for g in feito.conferencia["gabinetes"]] == [g["datajud"] for g in antes.conferencia["gabinetes"]]  # type: ignore[index]


def test_atualizar_que_falha_mantem_o_ultimo_dia_bom(tmp_path: Path, ligado: Regras) -> None:
    anterior_fonte = {"data_dados": "2026-11-09", "coletado_em": "2026-11-21T22:41:00Z", "registros": 345421}
    anterior_conf = {"max_timestamp": "2026-11-01T00:00:00Z", "total_datajud": 345421, "gabinetes": []}
    cliente, fetch, _ = montar(tmp_path, ligado, [http(429), http(504), QUEDA, PARCIAL, http(429), http(504)])
    feito = datajud.atualizar(ligado, None, agora=AGORA, cliente=cliente, anterior_fonte=anterior_fonte, anterior_conferencia=anterior_conf)
    assert len(fetch.posts) == 6 and feito.mudou is False and feito.conferencia == anterior_conf
    f = feito.fonte
    assert (f["ok"], f["ativo"], f["data_dados"], f["coletado_em"], f["registros"]) == (False, True, "2026-11-09", "2026-11-21T22:41:00Z", 345421)
    assert f["aviso"] == "O DataJud não deu resposta completa em 23/11; ficam os números de 09/11."  # sem a hora nem o motivo: não regrava no mesmo dia
    assert feito.log.startswith("DataJud: falhou (DataJud, frescor: 6 tentativas sem resposta completa; a última: HTTP 504), "
                                "6 tentativas, 0 completas (1 parcial, 2×429, 2×504, 1 queda de conexão)")


def test_atualizar_recusa_total_que_muda_demais(tmp_path: Path, ligado: Regras) -> None:
    """Sanidade: o total de ativos do DataJud que muda mais de 10% contra o último dia bom não vale
    (carga pela metade no CNJ); fica o anterior, com o aviso."""
    anterior_conf = {"max_timestamp": "2026-11-01T00:00:00Z", "total_datajud": 500000, "gabinetes": []}
    cliente, _, _ = montar(tmp_path, ligado, [FRESCOR, ATIVOS])
    feito = datajud.atualizar(ligado, None, agora=AGORA, cliente=cliente, anterior_fonte={"data_dados": "2026-11-01"}, anterior_conferencia=anterior_conf)
    assert feito.fonte["ok"] is False and feito.conferencia == anterior_conf and "de 500000 para 345421" in feito.log


def test_atualizar_avisa_quando_os_dados_do_cnj_passam_da_validade(tmp_path: Path, ligado: Regras) -> None:
    cliente, _, _ = montar(tmp_path, ligado, [FRESCOR, ATIVOS])
    feito = datajud.atualizar(ligado, None, agora=datetime(2026, 11, 30, 12, 0, tzinfo=UTC), cliente=cliente)
    assert feito.fonte["ok"] is True and feito.fonte["aviso"] == "Os dados do CNJ vão até 09/11/2026 (21 dias)."


def test_ligado_sem_chave_vira_aviso_sem_pedido(tmp_path: Path, ligado: Regras) -> None:
    sem_chave = dataclasses.replace(ligado, datajud={**ligado.datajud, "chave": ""})
    fetch = FetchFalso([])
    feito = datajud.atualizar(sem_chave, ColetorTribunais(tmp_path, regras=sem_chave, fetch=fetch), agora=AGORA, ambiente={})
    assert fetch.posts == [] and feito.fonte["ok"] is False and "sem chave" in (feito.fonte["aviso"] or "")
