"""O cliente do DataJud (qijournal/tribunais/datajud.py): espera crescente, disjuntor, prazo, teto de
120 pedidos por minuto, só resposta completa (HTTP 200 e ``_shards.failed == 0``), mojibake dos
nomes, a rodada de ``atualizar`` (ligado para uso pessoal: nada publicado sem ``publicar``) e a carga
dos movimentos (lotes por número CNJ, incremental, o diário no cache, a camada ``contas.camada_cnj``).

Sem rede: ``fetch`` falso, relógio e sono falsos (nenhuma espera de verdade). Fixtures reais e
pequenas em ``tests/fixtures/tribunais/``:

- ``datajud_frescor.json``: resposta real da consulta de frescor de 22/11/2026 22:24 UTC (3 de 3
  shards; ``max(@timestamp)`` = 09/11/2026 03:21:46), só com as duas agregações que a C.0 pede;
- ``datajud_ativos_por_gabinete.json``: as contagens da única resposta completa de 23/11/2026
  00:44 UTC (59 órgãos, 345.421 ativos, 3 de 3 shards) com o nome de cada órgão como o índice o
  devolve (com mojibake), tirado do catálogo real de 22/11/2026 22:32 UTC, no formato da consulta
  que o cliente manda;
- ``datajud_parcial.json``: resposta real de 23/11/2026 00:36 UTC, HTTP 200 com 2 de 3 shards
  falhos (motivos encurtados, só os 5 primeiros órgãos);
- ``datajud_lote.json``: seis documentos reais do índice ``api_publica_stj`` (amostra de 22/11/2026)
  no formato da resposta à consulta por lote, só com os campos que ela pede (número CNJ, última
  atualização, código do órgão, código e data de cada movimento): um concluso desde 26/08/2026, um
  com decisão depois da conclusão, um com conclusão sem nenhum ato do magistrado, um de 2007
  concluso desde 2023, um com despacho antes da conclusão e um de 2011 já arquivado;
- ``datajud_robots_401.json``: o corpo real do ``/robots.txt`` do host (23/11/2026): o
  Elasticsearch pede a chave em todo caminho (401 ``security_exception``); não há arquivo de regras.

Os nomes nas fixtures são de gabinetes de ministros e de desembargadores convocados (agentes
públicos), como o CNJ os publica; nenhuma parte nem advogado.
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
import time
from datetime import UTC, date, datetime, timedelta
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
        if url == ROBOTS:  # como o host responde de verdade: 401 (a API pede a chave em todo caminho)
            self.robots += 1
            raise FetchError(url, "HTTP 401", status=401)
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
    """Ligado **e publicado** (o modo da prévia): a fonte e a conferência vão ao meta.json. As
    tentativas e esperas ficam fixas aqui (6; 5, 10, 20, 40 e 80 s): os testes são da mecânica do
    cliente, não dos números do config."""
    return dataclasses.replace(regras, datajud={**regras.datajud, "ativo": True, "publicar": True, "tentativas": 6, "espera_s": [5, 10, 20, 40, 80]})


@pytest.fixture(scope="module")
def desligado(regras: Regras) -> Regras:
    return dataclasses.replace(regras, datajud={**regras.datajud, "ativo": False})


def montar(tmp_path: Path, regras: Regras, respostas: list[Any], *, aleatorio: float = 0.5, **kw: Any) -> tuple[ClienteDataJud, FetchFalso, Relogio]:
    relogio = Relogio()
    fetch = FetchFalso(respostas, relogio)
    coletor = ColetorTribunais(tmp_path / "cache", regras=regras, fetch=fetch, relogio=relogio, dormir=relogio.dormir)
    cliente = ClienteDataJud.do_config(regras, coletor, ambiente={}, relogio=relogio, dormir=relogio.dormir, aleatorio=lambda: aleatorio, **kw)
    return cliente, fetch, relogio


# ── o que o config manda ─────────────────────────────────────────────────────────────────────


def test_o_config_liga_para_uso_pessoal_sem_publicar(regras: Regras) -> None:
    """A decisão do dono (23/11/2026): ligado, para uso pessoal; ``publicar: false`` porque o
    repositório e o site são públicos (item 3.8 do termo). A variável liga numa execução."""
    assert regras.datajud["ativo"] is True and regras.datajud["publicar"] is False
    assert datajud.deve_publicar(regras, {}) is False
    assert datajud.deve_publicar(regras, {"QIJ_DATAJUD_PUBLICAR": "1"}) is True
    assert datajud.deve_publicar(regras, {"QIJ_DATAJUD_PUBLICAR": " sim "}) is True
    assert datajud.deve_publicar(dataclasses.replace(regras, datajud={**regras.datajud, "publicar": True}), {"QIJ_DATAJUD_PUBLICAR": "0"}) is False
    mov = regras.datajud["movimentos"]
    assert (mov["lote"], mov["cobertura_minima"], mov["campo_incremental"]) == (1000, 0.8, "dataHoraUltimaAtualizacao")


def test_desligado_no_config_nao_pede_nada(tmp_path: Path, desligado: Regras) -> None:
    """Com ``datajud.ativo: false``, nenhum pedido sai e a fonte do ``meta.json`` é a do contrato:
    ``ok: null``, ``ativo: false`` e o porquê."""
    fetch = FetchFalso([])
    coletor = ColetorTribunais(tmp_path, regras=desligado, fetch=fetch)
    feito = datajud.atualizar(desligado, coletor, agora=AGORA, cache=tmp_path / "cache")
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
    do_config = ClienteDataJud.do_config(carregar_regras(), cliente.coletor, ambiente={})
    assert (do_config.tentativas, do_config.espera_s) == (3, (5.0, 15.0))  # o config: frescor e conferência
    assert (cliente.limite_falhas, cliente.prazo_s, cliente.por_minuto) == (3, 900.0, 120)


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
    assert fetch.robots == 1  # o robots.txt do host foi lido antes (401: API sem arquivo de regras)
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
        "registros": 345421, "velho_dias": 15, "publicado": True, "atribuicao": "Fonte: CNJ, DataJud (API Pública), metadados processuais do STJ",
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


# ── o robots.txt do host (uma API sem arquivo de regras) ─────────────────────────────────────


def test_robots_401_do_datajud_vale_sem_regras_e_outro_host_continua_parado(tmp_path: Path, regras: Regras) -> None:
    """O ``/robots.txt`` do DataJud responde 401 (``security_exception``: o Elasticsearch pede a
    chave em todo caminho). Para o host marcado ``robots: api`` no config, 4xx = sem regras (RFC
    9309, §2.3.1.3); para os outros, 401 no robots.txt continua parando o host."""
    corpo = fx.ler("datajud_robots_401.json").encode()
    assert b"security_exception" in corpo and b"robots.txt" in corpo
    pedidos: list[str] = []

    def fetch(url: str, **kw: Any) -> net.Response:
        pedidos.append(url)
        if url.endswith("/robots.txt"):
            raise FetchError(url, "HTTP 401", status=401)
        return net.Response(url=url, status=200, content=json.dumps(FRESCOR).encode(), headers={})

    coletor = ColetorTribunais(tmp_path, regras=regras, fetch=fetch, dormir=lambda s: None)
    assert coletor.permitido(URL) is True and coletor.consulta(URL, {"size": 0})["took"]
    outro = ColetorTribunais(tmp_path / "b", regras=regras, fetch=fetch, dormir=lambda s: None)
    with pytest.raises(FetchError, match="inacessível"):
        outro.texto("https://www.stf.jus.br/pagina")  # host comum: 401 no robots.txt para tudo
    assert "https://www.stf.jus.br/pagina" not in pedidos


# ── os movimentos: a árvore da TPU e o resumo de cada documento ──────────────────────────────

LOTE = json.loads(fx.ler("datajud_lote.json"))
MOV = datajud.Movimentos.carregar(Path(__file__).resolve().parents[1] / "data" / "tribunais" / "tpu")


def test_a_arvore_de_movimentos_da_tpu() -> None:
    """Conferido no dump do SGT (versão 85): 976 movimentos em duas raízes; 51 Conclusão em 14 › 48,
    sem filhos; 1 Magistrado com Decisão (3), Julgamento (193) e Despacho (11009)."""
    assert len(MOV) == 976
    assert MOV.cadeia(51) == (14, 48, 51) and not [c for c, pai in MOV.pai.items() if pai == 51]
    assert {c for c, pai in MOV.pai.items() if pai is None} == {1, 14}
    assert MOV.do_magistrado(12435) and MOV.decisao(12435)  # conhecimento para negar provimento ao REsp
    assert MOV.do_magistrado(11010) and not MOV.decisao(11010)  # mero expediente (despacho)
    assert MOV.do_magistrado(12474) and MOV.decisao(12474)  # a "Distribuição" da Presidência é decisão
    assert not MOV.do_magistrado(51) and not MOV.do_magistrado(26) and not MOV.do_magistrado(300218)
    assert 300218 not in MOV and MOV.cadeia(300218) == ()


@pytest.mark.parametrize(
    ("numero", "esperado"),
    [
        # conclusão em 30/07, decisão (12435) em 08/11: sem conclusão em aberto
        ("50219851420254040000", {"c": "2026-07-30", "a": "2026-11-08", "ac": 12435, "d": "2026-11-08", "u": "2026-11-08", "ab": 0}),
        # o último ato do magistrado é de 2025; a conclusão de 26/08/2026 está em aberto
        ("00038801720128260022", {"c": "2026-08-26", "a": "2025-08-15", "ac": 12455, "d": "2025-08-15", "u": "2026-08-26", "ab": 1}),
        # conclusão sem ato nenhum do magistrado depois (nem antes)
        ("00132817920258250000", {"c": "2026-10-21", "a": None, "ac": None, "d": None, "u": "2026-10-26", "ab": 1}),
        # despacho (11010) antes da conclusão: o despacho não é decisão, mas é ato do magistrado
        ("02927998420243000000", {"c": "2026-08-26", "a": "2025-08-01", "ac": 11010, "d": "2024-10-22", "ab": 1}),
        # arquivado em 2021 (246 Definitivo): o resumo não decide se o processo está ativo
        ("01586752420113000000", {"c": "2021-03-17", "a": "2021-03-19", "ac": 11010, "u": "2021-03-29", "uc": 246, "ab": 0}),
    ],
)
def test_resumo_dos_documentos_reais(numero: str, esperado: dict[str, Any]) -> None:
    docs = {d["fonte"]["numeroProcesso"]: d["fonte"] for d in datajud.ler_documentos(LOTE)}
    r = datajud.resumir_documento(docs[numero], MOV, hoje=HOJE)
    assert r is not None and r["n"] == numero and r["em"] == "2026-11-23" and r["x"] == 0
    assert {k: r[k] for k in esperado} == esperado


def test_resumo_ignora_data_impossivel_e_marca_movimento_fora_da_tpu() -> None:
    """O índice tem movimento no ano 9260 (data impossível: fora); código fora da TPU nacional
    (300218) depois da conclusão: não dá para dizer se foi ato do magistrado (``x``)."""
    fonte = {"numeroProcesso": "00132817920258250000", "movimentos": [
        {"codigo": 51, "dataHora": "2026-10-21T10:00:00.000Z"},
        {"codigo": 239, "dataHora": "9260-03-10T12:07:29.000Z"},
        {"codigo": 300218, "dataHora": "2026-10-22T10:00:00.000Z"},
        {"codigo": "x", "dataHora": "2026-10-23T10:00:00.000Z"},
    ]}  # fmt: skip
    r = datajud.resumir_documento(fonte, MOV, hoje=HOJE)
    assert r is not None and (r["c"], r["a"], r["ab"], r["x"], r["u"], r["uc"]) == ("2026-10-21", None, 1, 1, "2026-10-22", 300218)
    assert datajud.resumir_documento({"numeroProcesso": "123"}, MOV, hoje=HOJE) is None


def test_as_consultas_de_lote_e_do_incremental() -> None:
    lote = datajud.consulta_lote(["00000000000000000001", "00000000000000000002"])
    assert lote == {"size": 2, "query": {"terms": {"numeroProcesso.keyword": ["00000000000000000001", "00000000000000000002"]}},
                    "_source": ["numeroProcesso", "dataHoraUltimaAtualizacao", "orgaoJulgador.codigo", "movimentos.codigo", "movimentos.dataHora"]}
    inc = datajud.consulta_incremental("2026-11-08T03:21:46Z", 1000, [1794194506058, "STJ_SUP_x"])
    assert inc["query"] == {"range": {"dataHoraUltimaAtualizacao": {"gt": "2026-11-08T03:21:46Z"}}}
    assert inc["sort"] == [{"dataHoraUltimaAtualizacao": {"order": "asc"}}, {"id.keyword": {"order": "asc"}}]
    assert inc["search_after"] == [1794194506058, "STJ_SUP_x"] and inc["size"] == 1000
    assert "search_after" not in datajud.consulta_incremental("2026-11-08T03:21:46Z", 10)


# ── o diário no cache e a fila ───────────────────────────────────────────────────────────────


def test_o_diario_guarda_cada_lote_e_sobrevive_a_linha_cortada(tmp_path: Path) -> None:
    g = datajud.Guarda(tmp_path)
    g.gravar([{"n": "1", "c": "2026-01-01"}, {"n": "2", "aus": 1}])
    g.gravar([{"n": "1", "c": "2026-02-01"}])
    with g.arquivo.open("a", encoding="utf-8") as f:
        f.write('{"n": "3", "c": "2026-0')  # o tempo-limite cortou a última linha
    de_novo = datajud.Guarda(tmp_path)
    assert len(de_novo) == 2 and de_novo.get("1")["c"] == "2026-02-01" and de_novo.get("2")["aus"] == 1  # type: ignore[index]
    assert de_novo.linhas == 3 and de_novo.compactar() is False  # 3 linhas para 2 processos: ainda dentro da folga (1,5×)
    assert de_novo.compactar(folga=1.0) is True and de_novo.linhas == 2
    assert de_novo.compactar(["1"]) is True and "2" not in datajud.Guarda(tmp_path)


def test_a_fila_vem_de_quem_espera_mais_e_deixa_de_fora_o_que_o_indice_nao_tem() -> None:
    linhas = [
        {"id": "a", "cnj": "00000000000000000001", "sem_decisao_dias": 10, "segredo": False, "sobrestado": False},
        {"id": "b", "cnj": "00000000000000000002", "sem_decisao_dias": 900, "segredo": False, "sobrestado": False},
        {"id": "c", "cnj": "00000000000000000003", "sem_decisao_dias": 999, "segredo": True, "sobrestado": False},  # o índice público não tem
        {"id": "d", "cnj": "00000000000000000004", "sem_decisao_dias": 500, "segredo": False, "sobrestado": True},  # suspenso: fora do tempo
        {"id": "e", "cnj": "123", "sem_decisao_dias": 800, "segredo": False, "sobrestado": False},  # sem número CNJ válido
        {"id": "f", "cnj": "00000000000000000006", "sem_decisao_dias": None, "segredo": False, "sobrestado": False},
        {"id": "g", "cnj": "00000000000000000002", "sem_decisao_dias": 900, "segredo": False, "sobrestado": False},  # repetido
    ]
    assert datajud.a_consultar(linhas) == ["00000000000000000002", "00000000000000000001"]
    assert datajud.execucoes_restantes(1000, [{"consultados": 0}, {"consultados": 300}, {"consultados": 500}]) == 3
    assert datajud.execucoes_restantes(0, []) == 0 and datajud.execucoes_restantes(10, [{"consultados": 0}]) is None


# ── a carga: lotes, marca, incremental, prazo ────────────────────────────────────────────────


def _lote(numeros: list[str]) -> dict[str, Any]:
    """A resposta real do lote, só com os documentos pedidos (os outros números: fora do índice)."""
    hits = [h for h in LOTE["hits"]["hits"] if h["_source"]["numeroProcesso"] in numeros]
    return {**LOTE, "hits": {"total": {"value": len(hits), "relation": "eq"}, "max_score": 1.0, "hits": hits}}


NUMEROS = [h["_source"]["numeroProcesso"] for h in LOTE["hits"]["hits"]]


def test_carga_em_lotes_grava_cada_um_e_para_no_disjuntor(tmp_path: Path, ligado: Regras) -> None:
    """Lotes de 2: o primeiro volta na segunda tentativa, o segundo falha 4 vezes e o terceiro de
    novo; o quarto volta. O que voltou está no diário (com os ausentes), a marca do incremental é a
    do frescor e o histórico guarda a execução."""
    regras = dataclasses.replace(ligado, datajud={**ligado.datajud, "movimentos": {**ligado.datajud["movimentos"], "lote": 2, "espera_s": [0.0]}})
    pedidos = [*NUMEROS[:4], "99999999999999999999", NUMEROS[4]]
    respostas = [http(429), _lote(NUMEROS[:2]), *[http(504)] * 4, *[QUEDA] * 4]
    cliente, fetch, _ = montar(tmp_path, regras, respostas)
    guarda = datajud.Guarda(tmp_path / "cache")
    estado: dict[str, Any] = {}
    carga = datajud.carregar_movimentos(cliente, guarda, pedidos, estado, MOV, regras, hoje=HOJE,
                                        frescor={"max_atualizacao": "2026-11-09T03:21:46Z"})
    assert (carga.pedidos_lote, carga.falhas_lote, carga.consultados, carga.achados) == (1, 2, 2, 2)
    assert len(fetch.posts) == 1 + 1 + 4 + 4 and cliente.falhas_seguidas == 2 and not cliente.aberto
    assert set(datajud.Guarda(tmp_path / "cache").registros) == set(NUMEROS[:2])  # já no disco
    assert estado["marca"] == "2026-11-09T03:21:46Z" and estado["historico"][-1]["consultados"] == 2
    assert carga.pendentes == 4 and "faltam 4 de 6" in carga.texto()
    corpo = json.loads(fetch.posts[0]["data"])
    assert corpo["query"]["terms"]["numeroProcesso.keyword"] == NUMEROS[:2]


def test_carga_marca_os_ausentes_e_pede_de_novo_depois_de_uma_semana(tmp_path: Path, ligado: Regras) -> None:
    regras = dataclasses.replace(ligado, datajud={**ligado.datajud, "movimentos": {**ligado.datajud["movimentos"], "lote": 10}})
    falta = "99999999999999999999"
    cliente, _, _ = montar(tmp_path, regras, [_lote(NUMEROS[:1])])
    guarda = datajud.Guarda(tmp_path / "cache")
    carga = datajud.carregar_movimentos(cliente, guarda, [NUMEROS[0], falta], {}, MOV, regras, hoje=HOJE, frescor={"max_atualizacao": "2026-11-09T03:21:46Z"})
    assert (carga.achados, carga.ausentes, carga.pendentes) == (1, 1, 0) and guarda.get(falta) == {"n": falta, "aus": 1, "em": "2026-11-23"}
    cliente2, fetch2, _ = montar(tmp_path / "2", regras, [_lote([])])
    datajud.carregar_movimentos(cliente2, guarda, [NUMEROS[0], falta], {"marca": "2026-11-09T03:21:46Z"}, MOV, regras,
                                hoje=date(2026, 11, 27), frescor=None)
    assert fetch2.posts == []  # quatro dias depois: nada a pedir
    cliente3, fetch3, _ = montar(tmp_path / "3", regras, [_lote([])])
    datajud.carregar_movimentos(cliente3, guarda, [NUMEROS[0], falta], {"marca": "2026-11-09T03:21:46Z"}, MOV, regras,
                                hoje=date(2026, 12, 1), frescor=None)
    assert json.loads(fetch3.posts[0]["data"])["query"]["terms"]["numeroProcesso.keyword"] == [falta]


def test_sem_frescor_a_marca_sai_do_primeiro_lote(tmp_path: Path, ligado: Regras) -> None:
    """O frescor (uma agregação sobre o índice inteiro) não respondeu: a marca do incremental é a
    atualização mais nova do primeiro lote, normalizada (nunca passa da do índice)."""
    cliente, _, _ = montar(tmp_path, ligado, [_lote(NUMEROS)])
    estado: dict[str, Any] = {}
    datajud.carregar_movimentos(cliente, datajud.Guarda(tmp_path / "c"), NUMEROS, estado, MOV, ligado, hoje=HOJE, frescor=None)
    assert (estado["marca"], estado["marca_de"]) == ("2026-11-09T03:21:46Z", "lote")


def test_incremental_por_atualizacao_com_cursor_e_fim(tmp_path: Path, ligado: Regras) -> None:
    """O índice andou (``max_atualizacao`` > marca): páginas por ``dataHoraUltimaAtualizacao`` desde a
    marca menos a margem (24 h), com o cursor guardado a cada página; a página curta fecha a passada
    e a marca vira o alto do índice. Só os processos da mesa (ou já no diário) entram."""
    regras = dataclasses.replace(ligado, datajud={**ligado.datajud, "movimentos": {**ligado.datajud["movimentos"], "pagina": 2}})
    hits = [dict(h, sort=[i, h["_id"]]) for i, h in enumerate(LOTE["hits"]["hits"])]
    pag1 = {**LOTE, "hits": {"total": {"value": 6, "relation": "eq"}, "hits": hits[:2]}}
    pag2 = {**LOTE, "hits": {"total": {"value": 6, "relation": "eq"}, "hits": hits[2:3]}}
    cliente, fetch, _ = montar(tmp_path, regras, [pag1, pag2])
    guarda = datajud.Guarda(tmp_path / "c")
    guarda.gravar([{"n": n, "c": None, "u": "2026-01-01", "em": "2026-11-01"} for n in NUMEROS])  # carga inicial feita
    estado: dict[str, Any] = {"marca": "2026-11-01T00:00:00Z"}
    gravados: list[Any] = []
    carga = datajud.carregar_movimentos(cliente, guarda, NUMEROS[:2], estado, MOV, regras, hoje=HOJE,
                                        frescor={"max_atualizacao": "2026-11-09T03:21:46Z"},
                                        gravar_estado=lambda: gravados.append(dict(estado)))
    corpos = [json.loads(p["data"]) for p in fetch.posts]
    assert corpos[0]["query"] == {"range": {"dataHoraUltimaAtualizacao": {"gt": "2026-10-31T00:00:00Z"}}} and "search_after" not in corpos[0]
    assert corpos[1]["search_after"] == [1, hits[1]["_id"]]
    assert any(g.get("cursor") == [1, hits[1]["_id"]] for g in gravados)  # o cursor foi guardado antes da página 2
    assert carga.incremental_fim and estado["marca"] == "2026-11-09T03:21:46Z" and estado["cursor"] is None
    assert carga.paginas == 2 and carga.atualizados == 3 and guarda.get(NUMEROS[0])["c"] == "2026-07-30"  # type: ignore[index]


def test_a_carga_para_no_prazo_sem_comecar_lote_que_nao_cabe(tmp_path: Path, ligado: Regras) -> None:
    regras = dataclasses.replace(ligado, datajud={**ligado.datajud, "movimentos": {**ligado.datajud["movimentos"], "lote": 1}})
    cliente, fetch, relogio = montar(tmp_path, regras, [_lote(NUMEROS[:1])] * 6, prazo_s=150)

    def lento(url: str, **kw: Any) -> net.Response:
        relogio.t += 60.0  # cada pedido leva um minuto
        return fetch(url, **kw)

    cliente.coletor.fetch = lento
    carga = datajud.carregar_movimentos(cliente, datajud.Guarda(tmp_path / "c"), NUMEROS, {}, MOV, regras, hoje=HOJE,
                                        frescor={"max_atualizacao": "2026-11-09T03:21:46Z"})
    assert carga.pedidos_lote == 1 and "prazo" in (carga.parou or "") and len(fetch.posts) == 1


# ── a camada do CNJ (contas.camada_cnj) ──────────────────────────────────────────────────────

REF = date(2026, 11, 20)


@pytest.mark.parametrize(
    ("linha", "registro", "esperado"),
    [
        # concluso desde 26/08/2026, depois da última decisão do acervo (15/08/2025): 86 dias até o acervo
        ({"distribuicao": "2024-01-10", "ultima_decisao": "2025-08-15"}, {"c": "2026-08-26", "a": "2025-08-15", "u": "2026-08-26", "ab": 1}, ("c", 86)),
        # a conclusão é anterior à última decisão que o STJ registra: o CNJ não sabe de algo
        ({"distribuicao": "2024-01-10", "ultima_decisao": "2026-09-01"}, {"c": "2026-08-26", "u": "2026-09-02", "ab": 1}, ("n", None)),
        # o último movimento do CNJ é anterior à última distribuição do acervo: atrasado
        ({"distribuicao": "2026-11-12", "ultima_decisao": None}, {"c": "2026-08-26", "u": "2026-11-08", "ab": 1}, ("d", None)),
        # decisão depois da conclusão (ab 0): sem conclusão em aberto
        ({"distribuicao": "2024-01-10"}, {"c": "2026-07-30", "a": "2026-11-08", "u": "2026-11-08", "ab": 0}, ("n", None)),
        # mesmo dia da distribuição: vale (a conclusão vem logo depois)
        ({"distribuicao": "2026-11-05"}, {"c": "2026-11-05", "u": "2026-11-05", "ab": 1}, ("c", 15)),
        ({"distribuicao": "2024-01-10"}, {"c": "2026-10-21", "u": "2026-10-22", "ab": 1, "x": 1}, ("x", None)),
        ({"distribuicao": "2024-01-10"}, {"n": "1", "aus": 1}, ("a", None)),
        ({"distribuicao": "2024-01-10"}, None, ("p", None)),
    ],
)
def test_estado_cnj_de_cada_processo(linha: dict[str, Any], registro: dict[str, Any] | None, esperado: tuple[str, int | None]) -> None:
    from qijournal.tribunais.contas import estado_cnj

    assert estado_cnj(linha, registro, REF) == esperado


def _mesa(regras: Regras) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Dez processos na mesa de um ministro da grade: 8 com data no CNJ (6 conclusos), 1 pendente, 1
    atrasado, mais 1 em segredo e 1 suspenso (fora da base)."""
    mid = next(m.id for m in regras.composicao["stj"].da_grade())
    linhas, registros = [], {}
    casos = [  # (sem decisão, registro); a distribuição é a data do acervo menos o "sem decisão"
        (900, {"c": "2024-06-05", "u": "2026-10-20", "ab": 1}),  # concluso há 898 dias
        (800, {"c": "2024-09-12", "u": "2024-09-12", "ab": 1}),  # 799
        (700, {"c": "2026-08-26", "u": "2026-08-26", "ab": 1}),  # 86: espera há 700 dias sem decisão, mas está concluso há 86
        (400, {"c": "2025-11-01", "u": "2025-11-01", "ab": 1}),  # 384
        (300, {"c": "2026-02-01", "u": "2026-02-01", "ab": 1}),  # 292
        (200, {"c": "2026-10-01", "a": "2026-10-10", "u": "2026-10-10", "ab": 0}),  # sem conclusão em aberto
        (100, {"c": "2026-09-01", "u": "2026-09-01", "ab": 1}),  # 80
        (50, {"c": "2026-10-01", "u": "2026-10-02", "ab": 1}),  # 50
        (40, None),  # pendente
        (30, {"c": "2026-10-21", "u": "2026-10-01", "ab": 1}),  # atrasado: o último movimento do CNJ é anterior à distribuição
    ]
    for i, (sem, reg) in enumerate(casos):
        n = f"{i:020d}"
        linhas.append({"id": f"stj-REsp-{i}", "cnj": n, "relator": mid, "distribuicao": (REF - timedelta(days=sem)).isoformat(),
                       "ultima_decisao": None, "sem_decisao_dias": sem, "segredo": False, "sobrestado": False})
        if reg is not None:
            registros[n] = {"n": n, **reg}
    linhas.append({"id": "stj-HC-s", "cnj": "9" * 20, "relator": mid, "distribuicao": "2020-01-01", "sem_decisao_dias": 999, "segredo": True, "sobrestado": False})
    linhas.append({"id": "stj-REsp-t", "cnj": "8" * 20, "relator": mid, "distribuicao": "2020-01-01", "sem_decisao_dias": 999, "segredo": False, "sobrestado": True})
    return linhas, registros


def test_camada_cnj_faixas_cobertura_e_listados(regras: Regras) -> None:
    from qijournal.tribunais.contas import camada_cnj

    linhas, registros = _mesa(regras)
    mid = linhas[0]["relator"]
    c = camada_cnj(linhas, registros, regras, data_dados=REF.isoformat(), listados=["stj-REsp-0", "stj-REsp-5", "stj-HC-s"])
    m = c["ministros"][mid]
    assert (m["base"], m["com_data"], m["segredo"], m["conclusos"]) == (10, 8, 1, 7)
    assert m["estados"] == {"c": 7, "n": 1, "d": 1, "x": 0, "a": 0, "p": 1} and m["cobertura"] == 0.8 and m["suficiente"] is True
    f180, f365 = m["faixas"]
    # sem decisão há mais de 180 dias: 6 candidatos, todos com data; conclusos há mais de 180: 4 (898, 799, 384, 292)
    assert (f180["candidatos"], f180["com_data"], f180["conclusos"], f180["suficiente"]) == (6, 6, 4, True)
    # há mais de 1 ano: 4 candidatos; conclusos há mais de 365: 3 (898, 799 e 384); o de 700 dias sem decisão está concluso há 86
    assert (f365["candidatos"], f365["com_data"], f365["conclusos"]) == (4, 4, 3)
    assert m["mediana"] == 292
    assert c["processos"] == {"stj-REsp-0": ["c", 898, "2024-06-05"], "stj-REsp-5": ["n", None, "2026-10-10"]}  # nunca o em segredo
    assert c["universo"]["base"] == 10 and c["mesa_inteira"]["base"] == 10 and c["limites"] == [180, 365]


def test_camada_cnj_sem_cobertura_nao_da_numero(regras: Regras) -> None:
    """Com menos de 80% dos candidatos com a data no CNJ, a faixa sai sem número
    (``conclusos: null``): a página diz "cobertura insuficiente"."""
    from qijournal.tribunais.contas import camada_cnj

    linhas, registros = _mesa(regras)
    for n in list(registros)[:3]:  # tira três dos que esperam há mais tempo: 3 de 6 candidatos com data
        del registros[n]
    m = camada_cnj(linhas, registros, regras, data_dados=REF.isoformat())["ministros"][linhas[0]["relator"]]
    f180, f365 = m["faixas"]
    assert (f180["com_data"], f180["cobertura"], f180["suficiente"], f180["conclusos"]) == (3, 0.5, False, None)
    assert (f365["com_data"], f365["cobertura"], f365["suficiente"], f365["conclusos"]) == (1, 0.25, False, None)
    assert m["suficiente"] is False and m["mediana"] is None


# ── de ponta a ponta: dados.atualizar nos dois modos, e o que vai a público ──────────────────


def _portal_com_datajud() -> Any:
    """O portal falso de ``test_tribunais_dados`` (STJ e STF com as amostras reais) mais o DataJud:
    o robots.txt responde 401 (como o de verdade), o frescor e os ativos as respostas reais, e cada
    lote devolve, para cada número pedido, o documento real concluso desde 26/08/2026 (só o número
    trocado)."""
    from tests.test_tribunais_dados import Portal

    modelo_doc = next(h for h in LOTE["hits"]["hits"] if h["_source"]["numeroProcesso"] == "00038801720128260022")

    class ComDataJud(Portal):
        def __init__(self) -> None:
            super().__init__()
            self.datajud: list[dict[str, Any]] = []

        def __call__(self, url: str, **kw: Any) -> net.Response:
            if url == ROBOTS:
                self.urls.append(url)
                raise FetchError(url, "HTTP 401", status=401)
            if url != URL:
                return super().__call__(url, **kw)
            self.urls.append(url)
            corpo = json.loads(kw["data"])
            self.datajud.append(corpo)
            if "aggs" in corpo and "max_ts" in corpo["aggs"]:
                resposta: Any = FRESCOR
            elif "aggs" in corpo:
                resposta = ATIVOS
            else:
                numeros = corpo["query"]["terms"]["numeroProcesso.keyword"]
                hits = [{**modelo_doc, "_source": {**modelo_doc["_source"], "numeroProcesso": n}} for n in numeros]
                resposta = {**LOTE, "hits": {"total": {"value": len(hits), "relation": "eq"}, "hits": hits}}
            return net.Response(url=url, status=200, content=json.dumps(resposta).encode(), headers={"content-type": "application/json"})

    return ComDataJud()


def _rodar_dados(tmp: Path, regras: Regras, ambiente: dict[str, str]) -> Any:
    from qijournal.tribunais import dados

    portal = _portal_com_datajud()
    r = dados.atualizar(None, tmp / "data", tmp / "cache", dados.Opcoes(hoje="2026-11-23", agora=AGORA.replace(hour=12), ambiente=ambiente),
                        fetch=portal, regras=regras, relogio=lambda: 1_000_000.0, dormir=lambda s: None)
    return r, portal


def _arquivos(pasta: Path) -> dict[str, bytes]:
    return {str(p.relative_to(pasta)): p.read_bytes() for p in sorted(pasta.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def dois_modos(tmp_path_factory: pytest.TempPathFactory, regras: Regras, desligado: Regras) -> dict[str, Any]:
    """A mesma coleta três vezes: DataJud desligado, ligado para uso pessoal (o config) e publicado
    (``QIJ_DATAJUD_PUBLICAR=1``, a prévia)."""
    raiz = Path(__file__).resolve().parents[1]
    saida = {}
    for nome, reg, amb in (("desligado", desligado, {}), ("pessoal", regras, {}), ("publicado", regras, {"QIJ_DATAJUD_PUBLICAR": "1"})):
        tmp = tmp_path_factory.mktemp(nome)
        shutil.copytree(raiz / "data" / "tribunais" / "tpu", tmp / "data" / "tribunais" / "tpu")
        r, portal = _rodar_dados(tmp, reg, amb)
        saida[nome] = {"tmp": tmp, "r": r, "portal": portal, "pasta": tmp / "data" / "tribunais"}
    return saida


def test_uso_pessoal_consulta_mas_nada_do_datajud_vai_a_publico(dois_modos: dict[str, Any], regras: Regras) -> None:
    """Ligado sem publicar: o DataJud é consultado (frescor e os lotes da mesa) e a camada fica no
    cache; a pasta pública sai **byte a byte igual** à da coleta com o DataJud desligado, menos a
    entrada ``fontes.datajud`` do ``meta.json`` (a de uso pessoal, sem número nem data)."""
    pessoal, desl = dois_modos["pessoal"], dois_modos["desligado"]
    assert any("terms" in (c.get("query") or {}) for c in pessoal["portal"].datajud)  # os lotes foram pedidos
    assert not desl["portal"].datajud
    a, b = _arquivos(pessoal["pasta"]), _arquivos(desl["pasta"])
    assert set(a) == set(b) and "cnj.json" not in a
    assert [k for k in a if a[k] != b[k]] == ["meta.json"]
    ma, mb = json.loads(a["meta.json"]), json.loads(b["meta.json"])
    assert ma["fontes"]["datajud"] == datajud.fonte_pessoal(regras) and "datajud" not in ma["cobertura"].get("stj", {})
    ma["fontes"].pop("datajud"), mb["fontes"].pop("datajud")
    assert ma == mb
    privada = json.loads((pessoal["tmp"] / "cache" / "datajud" / "camada.json").read_text(encoding="utf-8"))
    assert privada["mesa_inteira"]["estados"]["c"] > 0  # o uso pessoal: no cache, fora do git
    assert (pessoal["tmp"] / "cache" / "datajud" / "movimentos.jsonl").is_file()
    assert "movimentos:" in pessoal["r"].datajud and "concluso" not in pessoal["r"].datajud  # o log só tem números de operação


def test_publicado_poe_a_camada_do_cnj_na_pasta_e_na_pagina(dois_modos: dict[str, Any]) -> None:
    from qijournal.config import load_config
    from qijournal.render import tribunais as pagina

    pub = dois_modos["publicado"]
    cnj = json.loads((pub["pasta"] / "cnj.json").read_text(encoding="utf-8"))
    meta = json.loads((pub["pasta"] / "meta.json").read_text(encoding="utf-8"))
    stj = json.loads((pub["pasta"] / "stj.json").read_text(encoding="utf-8"))
    assert cnj["v"] == 1 and cnj["trib"] == "stj" and cnj["data_dados"] == stj["data_dados"]
    assert cnj["fonte"]["selo"] == "CNJ · DataJud" and cnj["fonte"]["indice_ate"] == "2026-11-09"
    assert meta["fontes"]["datajud"]["publicado"] is True and meta["fontes"]["datajud"]["atribuicao"].startswith("Fonte: CNJ")
    assert meta["cobertura"]["stj"]["datajud"]["movimentos"]["base"] == cnj["mesa_inteira"]["base"]
    assert _arquivos(pub["pasta"])["stj.json"] == _arquivos(dois_modos["desligado"]["pasta"])["stj.json"]  # o painel não muda
    config = load_config(env={})
    html = pagina.render_tribunais_page(config, pub["tmp"] / "data", home_href="../", archive_href="./")
    assert html is not None and "CNJ · DataJud" in html and 'class="tb-cnj' in html
    manifesto = json.loads(re.search(r'id="tbManifesto">(.*?)</script>', html, re.DOTALL).group(1))
    assert manifesto["cnj"] == cnj["processos"]
    # o mesmo site, sem a camada (o modo de uso pessoal): nada do CNJ na página
    html_p = pagina.render_tribunais_page(config, dois_modos["pessoal"]["tmp"] / "data", home_href="../", archive_href="./")
    assert html_p is not None and 'class="tb-cnj' not in html_p and '"cnj":' not in html_p and "uso pessoal" in html_p


def test_voltar_a_nao_publicar_tira_a_camada_da_pasta(dois_modos: dict[str, Any], regras: Regras) -> None:
    pub = dois_modos["publicado"]
    _rodar_dados(pub["tmp"], regras, {})
    assert not (pub["pasta"] / "cnj.json").exists()
    assert json.loads((pub["pasta"] / "meta.json").read_text(encoding="utf-8"))["fontes"]["datajud"] == datajud.fonte_pessoal(regras)


def test_a_pasta_publica_do_repositorio_nao_tem_nada_do_datajud() -> None:
    """A trava do item 3.8 do termo no que está no repositório (público): sem ``cnj.json``, a fonte
    do DataJud no ``meta.json`` sem número, e a aba sem a camada do CNJ (enquanto ``datajud.publicar``
    for ``false`` no config)."""
    raiz = Path(__file__).resolve().parents[1]
    regras = carregar_regras()
    if regras.datajud.get("publicar"):
        pytest.skip("datajud.publicar: true no config (depois de dar ciência ao CNJ)")
    pasta = raiz / "data" / "tribunais"
    assert not (pasta / "cnj.json").exists()
    meta = json.loads((pasta / "meta.json").read_text(encoding="utf-8"))
    fonte = meta["fontes"]["datajud"]
    assert fonte.get("publicado") is not True and all(fonte.get(k) is None for k in ("data_dados", "coletado_em", "registros", "ok"))
    assert "datajud" not in (meta.get("cobertura") or {}).get("stj", {})
    for arquivo in pasta.rglob("*.json"):
        texto = arquivo.read_text(encoding="utf-8")
        assert "concluso_cnj" not in texto and '"indice_ate"' not in texto, arquivo
    html = (raiz / "edicoes" / "tribunais.html").read_text(encoding="utf-8")
    assert 'class="tb-cnj' not in html and '<span class="az-selo">CNJ' not in html and "concluso há" not in html.lower().split("stf")[0][:0]
    assert "CNJ · DataJud (API Pública)</a>: uso pessoal, não publicado" in html  # a linha das fontes diz o porquê
    manifesto = json.loads(re.search(r'id="tbManifesto">(.*?)</script>', html, re.DOTALL).group(1))
    assert "cnj" not in manifesto


def test_lote_parcial_aproveita_os_documentos_e_nao_da_ausente_o_que_faltou(tmp_path: Path, ligado: Regras) -> None:
    """HTTP 200 com 1 de 3 shards falho (o que a sonda de 23/11 mais viu, depois do 429): os
    documentos que vieram são inteiros (cada um mora num shard só) e vão ao diário; os que faltaram
    moram no shard que falhou e voltam para o fim da fila, nunca como "fora do índice"."""
    regras = dataclasses.replace(ligado, datajud={**ligado.datajud, "movimentos": {**ligado.datajud["movimentos"], "lote": 3}})
    parcial = {**_lote(NUMEROS[:1]), "_shards": {"total": 3, "successful": 2, "skipped": 0, "failed": 1,
                                                   "failures": [{"shard": 2, "reason": {"type": "es_rejected_execution_exception"}}]}}
    cliente, fetch, _ = montar(tmp_path, regras, [parcial, _lote(NUMEROS[1:3])])
    guarda = datajud.Guarda(tmp_path / "c")
    carga = datajud.carregar_movimentos(cliente, guarda, NUMEROS[:3], {}, MOV, regras, hoje=HOJE, frescor={"max_atualizacao": "2026-11-09T03:21:46Z"})
    assert (carga.pedidos_lote, carga.parciais_lote, carga.achados, carga.ausentes, carga.pendentes) == (2, 1, 3, 0, 0)
    assert json.loads(fetch.posts[1]["data"])["query"]["terms"]["numeroProcesso.keyword"] == NUMEROS[1:3]  # os que faltaram, de novo
    assert not any(g.get("aus") for g in guarda.registros.values())
    assert cliente.placar.aproveitadas == 1 and cliente.placar.completas == 1 and "1 aproveitada" in cliente.placar.texto()


def test_parcial_nunca_vale_para_contagem_nem_pagina(tmp_path: Path, ligado: Regras) -> None:
    """Fora do lote (frescor, ativos, páginas do incremental), resposta parcial é pedida de novo."""
    parcial = {**_lote(NUMEROS[:1]), "_shards": {"total": 3, "successful": 2, "skipped": 0, "failed": 1}}
    cliente, fetch, _ = montar(tmp_path, ligado, [parcial, _lote(NUMEROS[:2])])
    docs = cliente.consultar(datajud.consulta_incremental("2026-11-01T00:00:00Z", 2), ler=datajud.ler_documentos)
    assert len(docs) == 2 and len(fetch.posts) == 2 and cliente.placar.parciais == 1 and cliente.placar.aproveitadas == 0


def test_a_carga_avulsa_sem_retrato_nao_pede_nada(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """``python -m qijournal.tribunais.datajud`` (a carga na máquina do dono) precisa do retrato do
    acervo que ``tribunais atualizar`` deixa no cache; sem ele, sai com 1 antes de qualquer pedido."""
    assert datajud.principal(["--cache", str(tmp_path)]) == 1
    assert "Sem retrato do acervo do STJ" in caplog.text
