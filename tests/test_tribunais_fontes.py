"""O coletor dos Tribunais (qijournal/tribunais/fontes.py): robots.txt, endereços proibidos, pausa
por host, recusa do WAF, POST com cache pelo corpo, resposta parcial do Elasticsearch e download
grande em fluxo. Sem rede: ``fetch`` e relógio falsos; o download em fluxo usa um servidor local."""

from __future__ import annotations

import http.server
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from qijournal import net
from qijournal.net import FetchError
from qijournal.tribunais import fontes
from qijournal.tribunais.fontes import (
    CaminhoVedado,
    ColetorTribunais,
    RecusaDoHost,
    RespostaParcial,
)
from qijournal.tribunais.modelo import carregar_regras
from tests.fixtures import tribunais as fx

STJ = "https://dadosabertos.web.stj.jus.br"
STF = "https://portal.stf.jus.br"
JURIS = "https://jurisprudencia.stf.jus.br/api/search/search"


class Relogio:
    """Relógio que só anda quando o coletor dorme; guarda cada espera."""

    def __init__(self, inicio: float | None = None) -> None:
        self.t = time.time() if inicio is None else inicio
        self.esperas: list[float] = []

    def __call__(self) -> float:
        return self.t

    def dormir(self, segundos: float) -> None:
        self.esperas.append(round(segundos, 3))
        self.t += segundos


class FetchFalso:
    """Responde pelo endereço: bytes, exceção, função ``(url, kw) -> Response`` ou lista (uma
    resposta por chamada; a última se repete). Endereço sem resposta: 404."""

    def __init__(self, respostas: dict[str, Any]) -> None:
        self.respostas = respostas
        self.pedidos: list[dict[str, Any]] = []

    def __call__(self, url: str, **kw: Any) -> net.Response:
        self.pedidos.append({"url": url, **kw})
        r = self.respostas.get(url)
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if r is None:
            raise FetchError(url, "HTTP 404", status=404)
        if isinstance(r, Exception):
            raise r
        if callable(r):
            return r(url, kw)
        return net.Response(url=url, status=200, content=r, headers={"content-type": "text/plain; charset=utf-8"})

    def urls(self) -> list[str]:
        return [p["url"] for p in self.pedidos]


def recusa(url: str = "") -> FetchError:
    return FetchError(url, "HTTP 403", status=403)


def coletor(tmp_path: Path, fetch: FetchFalso, relogio: Relogio | None = None, **kw: Any) -> ColetorTribunais:
    relogio = relogio or Relogio()
    kw.setdefault("pausa_padrao", 0.0)
    kw.setdefault("recusa_espera_s", 120.0)
    kw.setdefault("proibidos", ())
    return ColetorTribunais(tmp_path / "cache", fetch=fetch, relogio=relogio, dormir=relogio.dormir, **kw)


ROBOTS_STJ = fx.ler("robots_dadosabertos_stj.txt").encode()


# ── robots.txt e endereços proibidos ─────────────────────────────────────────────────────────


def test_robots_veda_e_nada_e_pedido(tmp_path: Path) -> None:
    fetch = FetchFalso({f"{STJ}/robots.txt": ROBOTS_STJ, f"{STJ}/dataset/acervo-em-tramitacao": b"<html>ok</html>"})
    c = coletor(tmp_path, fetch)
    with pytest.raises(CaminhoVedado, match="robots.txt"):
        c.json(f"{STJ}/api/3/action/package_show?id=acervo-em-tramitacao")
    with pytest.raises(CaminhoVedado):
        c.texto(f"{STJ}/dataset/acervo-em-tramitacao/history")  # "Disallow: /dataset/*/history" (curinga)
    assert c.texto(f"{STJ}/dataset/acervo-em-tramitacao") == "<html>ok</html>"
    assert fetch.urls() == [f"{STJ}/robots.txt", f"{STJ}/dataset/acervo-em-tramitacao"]
    assert not c.permitido(f"{STJ}/api/") and c.permitido(f"{STJ}/dataset/x/resource/y/download/z.json.gz")
    assert not c.permitido(f"{STJ}/dataset/rate/x") and c.permitido(f"{STJ}/dataset/x/historico")


def test_robots_que_veda_tudo(tmp_path: Path) -> None:
    host = "https://processo.stj.jus.br"
    fetch = FetchFalso({f"{host}/robots.txt": (fx.PASTA / "robots_processo_stj.txt").read_bytes()})
    c = coletor(tmp_path, fetch)
    with pytest.raises(CaminhoVedado):
        c.texto(f"{host}/processo/pesquisa/?termo=202401234567")
    assert fetch.urls() == [f"{host}/robots.txt"]


def test_proibidos_do_config_nem_leem_o_robots(tmp_path: Path) -> None:
    fetch = FetchFalso({})
    c = ColetorTribunais(tmp_path / "cache", regras=carregar_regras(), fetch=fetch, relogio=Relogio(), dormir=lambda s: None)
    for url in (f"{STJ}/api/3/action/package_list", "https://processo.stj.jus.br/processo/pesquisa/?x=1",
                f"{STF}/processos/listarProcessos.asp?classe=ARE&numeroProcesso=1"):
        with pytest.raises(CaminhoVedado, match="proibido"):
            c.texto(url)
    assert fetch.pedidos == []


def test_robots_fica_no_cache_por_um_dia(tmp_path: Path) -> None:
    relogio = Relogio()
    respostas = {f"{STF}/robots.txt": fx.ler("robots_portal_stf.txt").encode(), f"{STF}/atadistribuicao/x.asp": b"ata"}
    primeira = FetchFalso(dict(respostas))
    coletor(tmp_path, primeira, relogio).texto(f"{STF}/atadistribuicao/x.asp")
    segunda = FetchFalso(dict(respostas))
    c2 = coletor(tmp_path, segunda, relogio)
    assert c2.texto(f"{STF}/atadistribuicao/x.asp") == "ata"
    with pytest.raises(CaminhoVedado):
        c2.texto(f"{STF}/processos/detalhe.asp?incidente=1")
    assert segunda.urls() == [f"{STF}/atadistribuicao/x.asp"]  # o robots.txt veio do cache
    for arquivo in (tmp_path / "cache" / "robots").iterdir():  # 25 horas depois
        os.utime(arquivo, (relogio.t - 25 * 3600, relogio.t - 25 * 3600))
    terceira = FetchFalso(dict(respostas))
    coletor(tmp_path, terceira, relogio).texto(f"{STF}/atadistribuicao/x.asp")
    assert terceira.urls() == [f"{STF}/robots.txt", f"{STF}/atadistribuicao/x.asp"]


def test_robots_ausente_permite_e_recusado_ou_fora_do_ar_veda_o_host(tmp_path: Path) -> None:
    sem = FetchFalso({"https://www.cnj.jus.br/sgt/versoes.php": b"versoes"})  # robots.txt: 404
    assert coletor(tmp_path / "a", sem).texto("https://www.cnj.jus.br/sgt/versoes.php") == "versoes"
    recusado = FetchFalso({f"{STJ}/robots.txt": recusa(), f"{STJ}/dataset/x": b"x"})
    c = coletor(tmp_path / "b", recusado)
    with pytest.raises(RecusaDoHost):
        c.texto(f"{STJ}/dataset/x")
    with pytest.raises(RecusaDoHost):
        c.texto(f"{STJ}/dataset/y")  # o host já recusou nesta execução: nada mais é pedido
    assert recusado.urls() == [f"{STJ}/robots.txt", f"{STJ}/robots.txt"]
    fora = FetchFalso({f"{STJ}/robots.txt": FetchError(f"{STJ}/robots.txt", "HTTP 503", status=503), f"{STJ}/dataset/x": b"x"})
    with pytest.raises(FetchError, match="inacessível"):
        coletor(tmp_path / "c", fora).texto(f"{STJ}/dataset/x")
    assert fora.urls() == [f"{STJ}/robots.txt"]


def test_regras_com_curinga_e_allow_mais_longo() -> None:
    rp = fontes.ler_robots("User-agent: *\nDisallow: /a/*/b$\nDisallow: /c/\nAllow: /c/livre/\n"
                           "User-agent: QIJournalBot\nDisallow: /so-para-nos/\nCrawl-delay: 3")
    assert len(rp.grupos) == 2 and fontes.regras_do_robots(rp, net.USER_AGENT).atraso == 3.0
    agente = net.USER_AGENT
    assert not fontes.robots_permite(rp, agente, "https://h/so-para-nos/x")  # o grupo com o nome do robô vale
    assert fontes.robots_permite(rp, agente, "https://h/c/")  # e só ele: o grupo * não se soma
    geral = "Mozilla/5.0 (compatible)"
    assert not fontes.robots_permite(rp, geral, "https://h/a/x/b") and fontes.robots_permite(rp, geral, "https://h/a/x/b/c")
    assert not fontes.robots_permite(rp, geral, "https://h/c/x") and fontes.robots_permite(rp, geral, "https://h/c/livre/x")


# ── pausa por host ───────────────────────────────────────────────────────────────────────────


def test_pausa_do_host_e_o_crawl_delay(tmp_path: Path) -> None:
    relogio = Relogio(1000.0)
    fetch = FetchFalso({f"{STJ}/robots.txt": ROBOTS_STJ, f"{STF}/robots.txt": b"", **{f"{STJ}/dataset/{i}": b"x" for i in "abc"},
                        f"{STF}/atadistribuicao/a.asp": b"ata"})
    c = coletor(tmp_path, fetch, relogio, pausas={"dadosabertos.web.stj.jus.br": 2.0, "portal.stf.jus.br": 1.0})
    for i in "abc":
        c.texto(f"{STJ}/dataset/{i}")
    assert relogio.esperas == [2.0, 10.0, 10.0]  # 2 s do config até ler o robots; depois o Crawl-Delay: 10
    relogio.esperas.clear()
    c.texto(f"{STF}/atadistribuicao/a.asp")
    assert relogio.esperas == [1.0]  # outro host não espera os 10 s do STJ
    assert c.pausa_de("dadosabertos.web.stj.jus.br") == 10.0 and c.pausa_de("portal.stf.jus.br") == 1.0


def test_pausas_vem_do_config(tmp_path: Path) -> None:
    c = ColetorTribunais(tmp_path / "cache", regras=carregar_regras(), fetch=FetchFalso({}))
    assert c.pausa_de("dadosabertos.web.stj.jus.br") == 10.0 and c.pausa_de("jurisprudencia.stf.jus.br") == 1.0
    assert c._espera_da_recusa("dadosabertos.web.stj.jus.br") == 120.0
    assert c._espera_da_recusa("api-publica.datajud.cnj.jus.br") is None  # 403 ali é chave, não taxa


# ── recusa do WAF ────────────────────────────────────────────────────────────────────────────


def test_403_uma_nova_tentativa_depois_da_espera(tmp_path: Path) -> None:
    relogio = Relogio()
    url = f"{STJ}/dataset/acervo-em-tramitacao"
    fetch = FetchFalso({f"{STJ}/robots.txt": b"", url: [recusa(url), b"pagina"]})
    c = coletor(tmp_path, fetch, relogio)
    assert c.texto(url) == "pagina" and relogio.esperas == [120.0] and c.recusas == []
    assert [d["status"] for d in c.diario] == [200, 403, 200]


def test_403_duas_vezes_vira_recusa_do_host(tmp_path: Path) -> None:
    relogio = Relogio()
    url = f"{STJ}/dataset/acervo-em-tramitacao"
    fetch = FetchFalso({f"{STJ}/robots.txt": b"", url: recusa(url)})
    c = coletor(tmp_path, fetch, relogio)
    with pytest.raises(RecusaDoHost, match="403") as erro:
        c.texto(url)
    assert erro.value.status == 403 and relogio.esperas == [120.0]
    assert c.recusas == [{"host": "dadosabertos.web.stj.jus.br", "url": url, "status": 403, "vezes": 2}]
    sem_espera = coletor(tmp_path / "b", FetchFalso({f"{STJ}/robots.txt": b"", url: recusa(url)}), Relogio(), recusa_espera_s=None)
    with pytest.raises(RecusaDoHost):
        sem_espera.texto(url)


def test_outros_erros_sobem_como_estao(tmp_path: Path) -> None:
    url = f"{STF}/x.asp"
    c = coletor(tmp_path, FetchFalso({f"{STF}/robots.txt": b"", url: FetchError(url, "HTTP 500", status=500)}))
    with pytest.raises(FetchError) as erro:
        c.texto(url)
    assert erro.value.status == 500 and not isinstance(erro.value, RecusaDoHost)


# ── POST ─────────────────────────────────────────────────────────────────────────────────────


def _eco(url: str, kw: dict[str, Any]) -> net.Response:
    corpo = json.loads(kw["data"])
    resposta = {"result": {"_shards": {"total": 5, "successful": 5, "failed": 0}, "hits": {"total": {"value": len(corpo)}}}, "search_id": "x"}
    return net.Response(url=url, status=200, content=json.dumps(resposta).encode(), headers={"content-type": "application/json"})


def test_post_com_cache_pela_url_e_pelo_corpo(tmp_path: Path) -> None:
    fetch = FetchFalso({"https://jurisprudencia.stf.jus.br/robots.txt": b"<html>spa</html>", JURIS: _eco})
    c = coletor(tmp_path, fetch)
    consulta = {"size": 0, "query": {"match_all": {}}}
    primeira = c.consulta(JURIS, consulta, validade_h=20, elasticsearch=True)
    assert primeira["result"]["hits"]["total"]["value"] == 2
    assert c.consulta(JURIS, {"query": {"match_all": {}}, "size": 0}, validade_h=20) == primeira  # mesma consulta, outra ordem: cache
    c.consulta(JURIS, {"size": 1, "query": {"match_all": {}}}, validade_h=20)  # outro corpo: novo pedido
    posts = [p for p in fetch.pedidos if p["url"] == JURIS]
    assert len(posts) == 2 and posts[0]["method"] == "POST"
    assert posts[0]["data"] == b'{"query":{"match_all":{}},"size":0}'  # JSON canônico
    assert posts[0]["headers"]["Content-Type"] == "application/json" and "User-Agent" not in posts[0]["headers"]


def test_resposta_parcial_do_elasticsearch_nao_vale_nem_vai_ao_cache(tmp_path: Path) -> None:
    parcial = {"took": 46900, "timed_out": False, "_shards": {"total": 3, "successful": 2, "failed": 1}, "hits": {}}
    completa = {"took": 49800, "timed_out": False, "_shards": {"total": 3, "successful": 3, "failed": 0}, "hits": {}}
    url = "https://api-publica.datajud.cnj.jus.br/api_publica_stj/_search"
    fetch = FetchFalso({"https://api-publica.datajud.cnj.jus.br/robots.txt": b"", url: [json.dumps(parcial).encode(), json.dumps(completa).encode()]})
    c = coletor(tmp_path, fetch)
    with pytest.raises(RespostaParcial, match="1 de 3"):
        c.consulta(url, {"size": 0}, validade_h=20, elasticsearch=True)
    assert c.consulta(url, {"size": 0}, validade_h=20, elasticsearch=True)["_shards"]["failed"] == 0
    with pytest.raises(RespostaParcial):
        fontes.conferir_elasticsearch({"result": {"_shards": {"failed": 0}, "timed_out": True}})


def test_post_de_texto_soap(tmp_path: Path) -> None:
    url = "https://www.cnj.jus.br/sgt/sgt_ws.php"
    fetch = FetchFalso({url: (fx.PASTA / "sgt_getDataUltimaVersao.xml").read_bytes()})
    c = coletor(tmp_path, fetch)
    xml = c.consulta(url, b"<soapenv:Envelope/>", formato="texto", cabecalhos={"Content-Type": "text/xml; charset=utf-8"})
    assert "<return>05/11/2026</return>" in xml
    post = fetch.pedidos[-1]
    assert post["data"] == b"<soapenv:Envelope/>" and post["headers"]["Content-Type"] == "text/xml; charset=utf-8"


# ── arquivos ─────────────────────────────────────────────────────────────────────────────────


def test_arquivo_grande_pelo_baixador_injetado_e_pelo_fetch(tmp_path: Path) -> None:
    url = f"{STJ}/dataset/u/resource/v/download/processos_tramitando_20261120.json.gz"
    baixados: list[str] = []

    def baixar(u: str, destino: Path, **kw: Any) -> int:
        baixados.append(u)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"gz" * 10)
        return 20

    c = coletor(tmp_path, FetchFalso({f"{STJ}/robots.txt": ROBOTS_STJ}), baixar=baixar)
    caminho = c.arquivo_grande(url)
    assert caminho.read_bytes() == b"gz" * 10 and caminho.name.endswith(".json.gz") and c.bytes == len(ROBOTS_STJ) + 20
    assert c.arquivo_grande(url) == caminho and baixados == [url]  # no cache: não baixa de novo
    pelo_fetch = coletor(tmp_path / "b", FetchFalso({f"{STJ}/robots.txt": b"", url: b"conteudo"}))
    assert pelo_fetch.arquivo_grande(url).read_bytes() == b"conteudo"


class Servidor:
    """Servidor local que entrega um corpo (ou anuncia mais bytes do que manda)."""

    def __init__(self, corpo: bytes, anunciado: int | None = None) -> None:
        dono = self
        self.agentes: list[str] = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                dono.agentes.append(self.headers.get("User-Agent", ""))
                self.send_response(200)
                self.send_header("Content-Length", str(anunciado if anunciado is not None else len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)

            def log_message(self, *a: object) -> None:
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/acervo.json.gz"

    def fechar(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def test_download_em_fluxo_grava_no_disco_e_confere_o_tamanho(tmp_path: Path) -> None:
    corpo = os.urandom(3 * fontes.BLOCO + 123)
    servidor = Servidor(corpo)
    try:
        destino = tmp_path / "acervo.json.gz"
        assert fontes.baixar_em_fluxo(servidor.url, destino, timeout=5) == len(corpo)
        assert destino.read_bytes() == corpo and "QIJournalBot" in servidor.agentes[0]
    finally:
        servidor.fechar()
    cortado = Servidor(b"metade", anunciado=1000)
    try:
        with pytest.raises(FetchError, match="truncado"):
            fontes.baixar_em_fluxo(cortado.url, tmp_path / "x.gz", timeout=5, tentativas=1, dormir=lambda s: None)
        assert len(cortado.agentes) == 2 and not (tmp_path / "x.gz").exists()
        assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    finally:
        cortado.fechar()


def test_texto_e_json_com_validade_e_diario(tmp_path: Path) -> None:
    url = f"{STF}/pauta/services/calendario-service.asp?dados=calendarios&mes=11&ano=2026"
    fetch = FetchFalso({f"{STF}/robots.txt": b"", url: b'{"dias": [25]}'})
    c = coletor(tmp_path, fetch)
    assert c.json(url, validade_h=20) == {"dias": [25]}
    assert c.json(url, validade_h=20) == {"dias": [25]} and fetch.urls().count(url) == 1
    assert fetch.pedidos[-1]["headers"]["Accept"] == "application/json"
    assert c.requisicoes == 2 and [(d["host"], d["caminho"], d["status"]) for d in c.diario] == [
        ("portal.stf.jus.br", "/robots.txt", 200), ("portal.stf.jus.br", "/pauta/services/calendario-service.asp", 200)]


def test_limpar_apaga_so_o_velho_que_nao_foi_usado(tmp_path: Path) -> None:
    relogio = Relogio()
    url = f"{STF}/a.asp"
    c = coletor(tmp_path, FetchFalso({f"{STF}/robots.txt": b"", url: b"a"}), relogio)
    c.texto(url, validade_h=20)
    velho = tmp_path / "cache" / "paginas" / "sobra-antiga.txt"
    velho.write_text("x", encoding="utf-8")
    os.utime(velho, (relogio.t - 20 * 86400, relogio.t - 20 * 86400))
    assert c.limpar() == 1 and not velho.exists() and len(list((tmp_path / "cache" / "paginas").iterdir())) == 1


def test_fetch_com_assinatura_de_net(tmp_path: Path) -> None:
    """Os ``fetch`` falsos de outros testes recebem ``**kw``: o coletor só passa argumentos que o
    ``net.fetch`` aceita."""
    import inspect

    aceitos = set(inspect.signature(net.fetch).parameters)
    recebidos: set[str] = set()

    def fetch(url: str, **kw: Any) -> net.Response:
        recebidos.update(kw)
        return net.Response(url=url, status=200, content=b"{}", headers={})

    c = coletor(tmp_path, FetchFalso({}))
    c.fetch = fetch  # type: ignore[assignment]
    c.texto(f"{STF}/x")
    c.json(f"{STF}/y")
    c.arquivo(f"{STF}/z.xlsx")
    c.consulta(JURIS, {"q": 1})
    assert recebidos <= aceitos and {"data", "method"} <= recebidos

