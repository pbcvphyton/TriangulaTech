"""``qijournal/tribunais/dados.py``: a atualização de ponta a ponta, sem rede.

Um portal falso responde pelas fontes do STJ e do STF com as amostras reais de
``tests/fixtures/tribunais/`` (acervo do STJ com 44 processos de 20/11/2026, a planilha do STF com 23
de 22/11/2026, atas, pautas, precedentes, íntegras, jurisprudência e repercussão geral), passando
pelo coletor de verdade (robots.txt, pausa por host, recusa do WAF, POST). Confere: a ordem e o que
vai para ``data/tribunais/``; o ``meta.json`` fonte a fonte; que o dia bom não é sobrescrito (recusa
do portal, total fora da faixa, data que volta, teto de tamanho); o fallback das fontes acessórias;
``--so``; os achados do tribunal não refeito; e que rodar de novo não regrava nada.
"""

from __future__ import annotations

import gzip
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from qijournal import net
from qijournal.net import FetchError
from qijournal.tribunais import dados, modelo, stj
from tests.fixtures import tribunais as fx
from tests.fixtures.tribunais import stf as fx_stf

STJ = fx.PASTA / "stj"
PORTAL = "https://dadosabertos.web.stj.jus.br"
HOJE = "2026-11-23"
AGORA = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
RAIZ = Path(__file__).resolve().parents[1]
PAGINAS = {
    "acervo-em-tramitacao": fx.ler("ckan_dataset_acervo.html"),
    "atas-de-distribuicao": (STJ / "pagina_atas.html").read_text(encoding="utf-8"),
    "pautas-futuras": (STJ / "pagina_pautas.html").read_text(encoding="utf-8"),
    "precedentes-qualificados": (STJ / "pagina_precedentes.html").read_text(encoding="utf-8"),
    "integras-de-decisoes-terminativas-e-acordaos-do-diario-da-justica": (STJ / "pagina_integras.html").read_text(encoding="utf-8"),
}


@pytest.fixture(scope="module")
def regras() -> modelo.Regras:
    return modelo.carregar_regras()


class Portal:
    """O ``fetch`` falso: cada fonte do STJ e do STF pela URL (as amostras reais); ``falhas``:
    pedaço de URL → exceção (ou bytes) que substitui a resposta."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.falhas: dict[str, Any] = {}
        self.arquivos: dict[str, bytes] = {}
        for slug, html in PAGINAS.items():
            self.arquivos[f"{PORTAL}/dataset/{slug}"] = html.encode()
            for r in stj.links_da_pagina(html):
                corpo = self._arquivo_stj(r.arquivo)
                if corpo is not None:
                    self.arquivos[r.url] = corpo

    @staticmethod
    def _arquivo_stj(nome: str) -> bytes | None:
        if nome.startswith("processos_tramitando_"):
            return (STJ / "acervo_20261120_amostra.json.gz").read_bytes()
        if nome.startswith("ata2026"):
            return (STJ / ("ata20261119_amostra.json" if nome == "ata20261119.json" else "ata20261121.json")).read_bytes()
        if nome.startswith("pautas_futuras_"):
            return (STJ / "pautas_futuras_20261122_amostra.json.gz").read_bytes()
        if nome == "temas.csv":
            return (STJ / "temas.csv").read_bytes()
        if nome == "processos.csv":
            return (STJ / "processos.csv").read_bytes()
        if nome.startswith("metadados2026"):
            return (STJ / "metadados20261015_amostra.json").read_bytes()
        return None

    def _resposta(self, url: str, data: bytes | None) -> bytes | None:
        if url == f"{PORTAL}/robots.txt":
            return fx.ler("robots_dadosabertos_stj.txt").encode()
        if url == "https://portal.stf.jus.br/robots.txt":
            return fx.ler("robots_portal_stf.txt").encode()
        if url in self.arquivos:
            return self.arquivos[url]
        if url.endswith("Acervo_STF_Lista_Geral.xlsx"):
            return (fx_stf.PASTA / "acervo_amostra.xlsx").read_bytes()
        if "recuperaata.asp" in url:
            for dia in ("19", "20"):
                if f"dia={dia}&mes=11&ano=2026" in url:
                    return fx_stf.ler(f"ata_recuperaata_2026-11-{dia}.xml").encode()
            return fx_stf.ler("ata_recuperaata_2026-11-22_domingo.xml").encode()  # os outros dias: sem ata
        if "listaratas.asp?data=19/11/2026&num=252" in url:
            return fx_stf.ler("ata_2026-11-19_252_trecho.html").encode()
        if "listaratas.asp?data=20/11/2026&num=253" in url:
            return fx_stf.ler("ata_2026-11-20_253.html").encode()
        if url.startswith("https://jurisprudencia.stf.jus.br/api/search/search") and data:
            return fx_stf.ler("juris_agregacao_12m.json").encode()
        if url.endswith("todostemas.asp"):
            return fx_stf.ler("rg_todostemas_trecho.html").encode()
        if url.endswith("exportarDados.asp"):
            return fx_stf.ler("rg_exportar_trecho.xls.html").encode()
        if "dados=calendarios&mes=11&ano=2026" in url:
            return fx_stf.ler("pauta_calendarios_2026-11.json").encode()
        if "dados=sessao-presencial&data=25/11/2026" in url:
            return fx_stf.ler("pauta_sessao-presencial_2026-11-25.json").encode()
        if url.startswith("https://www.cnj.jus.br/sgt/sgt_ws.php"):
            return fx.ler("sgt_getDataUltimaVersao.xml").encode()
        return None

    def __call__(self, url: str, **kw: Any) -> net.Response:
        self.urls.append(url)
        for pedaco, falha in self.falhas.items():
            if pedaco in url:
                if isinstance(falha, Exception):
                    raise falha
                return net.Response(url=url, status=200, content=falha, headers={})
        corpo = self._resposta(url, kw.get("data"))
        if corpo is None:
            raise FetchError(url, "HTTP 404", status=404)
        return net.Response(url=url, status=200, content=corpo, headers={"content-type": "text/html; charset=utf-8"})


def _rodar(tmp: Path, portal: Portal, regras: modelo.Regras, *, cache: str = "cache", agora: datetime = AGORA, **opcoes: Any) -> dados.Resultado:
    return dados.atualizar(None, tmp / "data", tmp / cache, dados.Opcoes(hoje=agora.date().isoformat(), agora=agora, **opcoes),
                           fetch=portal, regras=regras, relogio=lambda: 1_000_000.0, dormir=lambda s: None)


def _pasta(tmp: Path) -> Path:
    return tmp / "data" / "tribunais"


def _meta(tmp: Path) -> dict[str, Any]:
    return json.loads((_pasta(tmp) / "meta.json").read_text(encoding="utf-8"))


@pytest.fixture
def carga(tmp_path: Path, regras: modelo.Regras) -> tuple[Path, Portal, dados.Resultado]:
    """Uma primeira carga completa (``--completo``: as séries inteiras e a conferência da TPU) e boa,
    com a TPU versionada copiada para a pasta de saída."""
    shutil.copytree(RAIZ / "data" / "tribunais" / "tpu", _pasta(tmp_path) / "tpu")
    portal = Portal()
    return tmp_path, portal, _rodar(tmp_path, portal, regras, completo=True)


# ── a carga boa ──────────────────────────────────────────────────────────────


def test_carga_grava_paineis_dossies_achados_e_meta(carga: tuple[Path, Portal, dados.Resultado]) -> None:
    tmp, _portal, r = carga
    pasta = _pasta(tmp)
    assert r.refeitos == ["stf", "stj"] and not r.apagados
    assert {p.name for p in pasta.iterdir()} == {"meta.json", "achados.json", "stj.json", "stf.json", "ministros", "tpu"}
    stj_json = json.loads((pasta / "stj.json").read_text(encoding="utf-8"))
    stf_json = json.loads((pasta / "stf.json").read_text(encoding="utf-8"))
    assert (stj_json["v"], stj_json["trib"], stj_json["data_dados"], stj_json["universo"]["processos"]) == (1, "stj", "2026-11-20", 44)
    assert (stf_json["trib"], stf_json["data_dados"], stf_json["universo"]["processos"]) == ("stf", "2026-11-22", 23)
    dossies = sorted(p.name for p in (pasta / "ministros").glob("*.json"))
    assert dossies and all(n.startswith(("stj-", "stf-")) for n in dossies)
    assert r.gravados == 4 + len(dossies)  # stj, stf, achados, meta e os dossiês
    achados = json.loads((pasta / "achados.json").read_text(encoding="utf-8"))
    assert achados["v"] == 1 and achados["hoje"] == HOJE
    for arquivo in pasta.rglob("*.json"):
        assert arquivo.stat().st_size <= dados.TETO_JSON, arquivo
    assert not list(pasta.rglob(".*")), "sobrou temporário"


def test_meta_fonte_a_fonte(carga: tuple[Path, Portal, dados.Resultado]) -> None:
    tmp, _portal, _r = carga
    meta = _meta(tmp)
    assert list(meta) == ["v", "hoje", "atualizado_em", "proxima_execucao", "fontes", "cobertura", "reguas", "avisos", "correcoes"]
    assert list(meta["fontes"]) == list(dados.IDS_FONTES)
    f = meta["fontes"]
    assert (f["stj_acervo"]["data_dados"], f["stj_acervo"]["registros"], f["stj_acervo"]["ok"]) == ("2026-11-20", 44, True)
    assert f["stj_acervo"]["coletado_em"] == "2026-11-23T12:00:00Z" == meta["atualizado_em"]
    assert f["stj_acervo"]["atribuicao"] == "Fonte: STJ, Portal de Dados Abertos (CC-BY)"
    assert f["stj_acervo"]["url"] == "https://dadosabertos.web.stj.jus.br/dataset/acervo-em-tramitacao"
    assert (f["stf_acervo"]["data_dados"], f["stf_acervo"]["registros"], f["stf_acervo"]["ok"]) == ("2026-11-22", 23, True)
    assert f["stj_pautas"]["data_dados"] == "2026-11-22" and f["stj_atas"]["data_dados"] == "2026-11-22"
    assert f["stj_integras"]["data_dados"] == "2026-10-15" and f["stj_integras"]["atraso_dias"] == 39
    assert "15/10/2026 (39 dias)" in f["stj_integras"]["aviso"] and f["stj_integras"]["ok"] is True
    assert f["stf_jurisprudencia"]["ok"] is True and f["stf_jurisprudencia"]["registros"] == 72155
    assert f["stf_atas"]["data_dados"] == "2026-11-20" and f["stf_rg"]["registros"] == 14
    assert (f["tpu"]["data_dados"], f["tpu"]["trib"], f["tpu"]["ok"]) == ("2026-11-05", "cnj", True)
    assert (f["datajud"]["ok"], f["datajud"]["ativo"]) == (None, False)  # desligado: nenhum pedido
    assert set(meta["cobertura"]) == {"stj", "stf"}
    assert meta["cobertura"]["stj"]["processos"] == 44 and "partes" in meta["cobertura"]["stj"]
    assert meta["cobertura"]["stf"]["linhas_de_assunto"] > 0 and "casamento" in meta["cobertura"]["stf"]
    assert meta["reguas"]["areas"] and meta["correcoes"] == []
    assert any("íntegras" in a for a in meta["avisos"])  # o atraso do STJ vai para o leitor
    assert all(":" not in a.split("(")[0][-6:] or "/" in a for a in meta["avisos"])  # data, nunca a hora


def test_so_endereco_permitido_e_pausa_do_portal(carga: tuple[Path, Portal, dados.Resultado]) -> None:
    _tmp, portal, _r = carga
    stj_urls = [u for u in portal.urls if u.startswith(PORTAL)]
    assert stj_urls[0] == f"{PORTAL}/robots.txt" and stj_urls.count(f"{PORTAL}/robots.txt") == 1
    assert not any("/api/" in u and "dadosabertos" in u for u in portal.urls)
    assert not any(u.startswith(("https://processo.stj.jus.br", "https://portal.stf.jus.br/processos")) for u in portal.urls)
    assert not any("datajud" in u for u in portal.urls)
    assert not any(u.endswith(".zip") for u in portal.urls)  # das íntegras, só os metadados


def test_rodar_de_novo_nao_regrava_nada(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    tmp, portal, _r = carga
    antes = {p: p.read_bytes() for p in _pasta(tmp).rglob("*.json")}
    r = _rodar(tmp, portal, regras)
    assert (r.gravados, r.apagados) == (0, 0)
    assert {p: p.read_bytes() for p in _pasta(tmp).rglob("*.json")} == antes


def test_retrato_da_vespera_e_diario_no_cache(carga: tuple[Path, Portal, dados.Resultado]) -> None:
    tmp, _portal, _r = carga
    retratos = sorted((tmp / "cache" / "retratos").glob("*.tsv.gz"))
    assert [p.name for p in retratos] == ["stf-2026-11-22.tsv.gz", "stj-2026-11-20.tsv.gz"]
    linhas = gzip.decompress(retratos[1].read_bytes()).decode().splitlines()
    assert linhas[0].split("\t")[:4] == ["id", "registro", "cnj", "relator"] and len(linhas) == 45
    assert "PESSOA" not in "".join(linhas)  # nenhuma parte no retrato
    diario = list((tmp / "cache" / "diario").glob("*.jsonl"))
    hosts = {json.loads(linha)["host"] for linha in diario[0].read_text().splitlines()} if len(diario) == 1 else set()
    assert {"dadosabertos.web.stj.jus.br", "www.stf.jus.br", "portal.stf.jus.br", "jurisprudencia.stf.jus.br", "www.cnj.jus.br"} <= hosts
    assert (tmp / "cache" / "estado" / "stj_acervo.json").is_file() and (tmp / "cache" / "estado" / "stf_rg.json").is_file()


def test_dossie_de_quem_saiu_e_apagado(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    tmp, portal, _r = carga
    saiu = _pasta(tmp) / "ministros" / "stj-quem-saiu.json"
    saiu.write_text('{"v":1}\n', encoding="utf-8")
    outro = _pasta(tmp) / "ministros" / "stf-quem-saiu.json"
    outro.write_text('{"v":1}\n', encoding="utf-8")
    r = _rodar(tmp, portal, regras, so="stj")
    assert r.apagados == 1 and not saiu.exists() and outro.exists()  # só o tribunal refeito perde os de quem saiu


# ── o dia bom não é sobrescrito ──────────────────────────────────────────────


def test_recusa_do_portal_mantem_o_painel_do_stj(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    tmp, portal, _r = carga
    stj_antes = (_pasta(tmp) / "stj.json").read_bytes()
    antes = _meta(tmp)["fontes"]
    portal.falhas["dataset/acervo-em-tramitacao"] = FetchError("", "HTTP 403", status=403)
    depois_de = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    r = _rodar(tmp, portal, regras, cache="cache-novo", agora=depois_de)  # cache novo: sem o arquivo da véspera
    assert r.refeitos == ["stf"] and (_pasta(tmp) / "stj.json").read_bytes() == stj_antes
    f = _meta(tmp)["fontes"]
    assert f["stj_acervo"]["ok"] is False and "recusou o acesso (403) duas vezes" in f["stj_acervo"]["aviso"]
    assert "24/11" in f["stj_acervo"]["aviso"] and "fica o painel da última coleta boa" in f["stj_acervo"]["aviso"]
    assert (f["stj_acervo"]["data_dados"], f["stj_acervo"]["coletado_em"], f["stj_acervo"]["registros"]) == (
        antes["stj_acervo"]["data_dados"], antes["stj_acervo"]["coletado_em"], 44)
    for fid in ("stj_atas", "stj_pautas", "stj_precedentes", "stj_integras"):  # as do painel que continua publicado
        assert f[fid] == antes[fid], fid
    assert sum(1 for u in portal.urls if "dataset/acervo-em-tramitacao" in u) >= 2  # a nova tentativa depois do 403
    assert _meta(tmp)["cobertura"]["stj"]["processos"] == 44


def test_acervo_que_falha_usa_o_arquivo_da_ultima_coleta_boa(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    """Com o cache da véspera, o painel é refeito com o arquivo dela e as fontes acessórias de hoje."""
    tmp, portal, _r = carga
    for pagina in (tmp / "cache" / "paginas").iterdir():  # a página do conjunto venceu; o arquivo da véspera ficou
        pagina.unlink()
    portal.falhas["dataset/acervo-em-tramitacao"] = FetchError("", "HTTP 503", status=503)
    r = _rodar(tmp, portal, regras, agora=datetime(2026, 11, 24, 12, 0, tzinfo=UTC))
    assert "stj" in r.refeitos
    f = _meta(tmp)["fontes"]["stj_acervo"]
    assert f["ok"] is False and f["data_dados"] == "2026-11-20" and "as contas usam o arquivo de 20/11/2026" in f["aviso"]
    assert f["coletado_em"] == "2026-11-23T12:00:00Z"


def test_total_fora_da_faixa_e_recusado(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    tmp, portal, _r = carga
    meta = _meta(tmp)
    meta["fontes"]["stj_acervo"]["registros"] = 60  # 44 de 60 = 73%: abaixo de 85%
    (_pasta(tmp) / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    stj_antes = (_pasta(tmp) / "stj.json").read_bytes()
    r = _rodar(tmp, portal, regras)
    assert "stj" not in r.refeitos and (_pasta(tmp) / "stj.json").read_bytes() == stj_antes
    f = _meta(tmp)["fontes"]["stj_acervo"]
    assert f["ok"] is False and f["registros"] == 60
    assert f["aviso"] == "O arquivo de 20/11/2026 trouxe 44 processos, 73% dos 60 de 20/11/2026; foi recusado e fica o painel daquele dia."


def test_data_que_volta_e_recusada(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    tmp, portal, _r = carga
    meta = _meta(tmp)
    meta["fontes"]["stf_acervo"]["data_dados"] = "2026-11-23"  # a planilha de hoje (22/11) é anterior
    (_pasta(tmp) / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    r = _rodar(tmp, portal, regras)
    assert r.refeitos == ["stj"]
    f = _meta(tmp)["fontes"]["stf_acervo"]
    assert f["ok"] is False and f["data_dados"] == "2026-11-23" and "fica o painel da última coleta boa" in f["aviso"]


def test_teto_de_tamanho_mantem_o_dia_anterior(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    tmp, portal, _r = carga
    stf_antes = (_pasta(tmp) / "stf.json").read_bytes()
    monkeypatch.setattr(dados, "TETO_JSON", 1_000)
    r = _rodar(tmp, portal, regras, agora=datetime(2026, 11, 24, 12, 0, tzinfo=UTC))
    assert r.refeitos == [] and (_pasta(tmp) / "stf.json").read_bytes() == stf_antes
    assert "tamanho máximo" in _meta(tmp)["fontes"]["stf_acervo"]["aviso"]


def test_fonte_acessoria_que_falha_usa_a_ultima_coleta_boa(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    tmp, portal, _r = carga
    parcial = json.loads(fx_stf.ler("juris_agregacao_12m.json"))
    parcial.setdefault("result", parcial)["_shards"] = {"total": 5, "successful": 4, "failed": 1}
    portal.falhas["jurisprudencia.stf.jus.br/api"] = json.dumps(parcial).encode()
    r = _rodar(tmp, portal, regras, agora=datetime(2026, 11, 24, 12, 0, tzinfo=UTC))
    assert "stf" in r.refeitos
    f = _meta(tmp)["fontes"]["stf_jurisprudencia"]
    assert f["ok"] is False and "ficam os dados de" in f["aviso"] and f["registros"] == 72155
    painel = json.loads((_pasta(tmp) / "stf.json").read_text(encoding="utf-8"))
    assert painel["decisoes"]["documentos"] == 72155  # o bloco continua, com o dado da última coleta boa


def test_fonte_acessoria_sem_socorro_sai_null(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    tmp, portal, _r = carga
    portal.falhas["dataset/pautas-futuras"] = FetchError("", "HTTP 500", status=500)
    _rodar(tmp, portal, regras, cache="outro-cache")
    f = _meta(tmp)["fontes"]["stj_pautas"]
    assert f["ok"] is False and f["data_dados"] is None and "o bloco sai sem estes dados" in f["aviso"]
    assert json.loads((_pasta(tmp) / "stj.json").read_text(encoding="utf-8"))["pautas"] is None


def test_so_stf_nao_toca_no_stj_e_os_achados_do_stj_ficam(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    tmp, portal, _r = carga
    pasta = _pasta(tmp)
    achados = json.loads((pasta / "achados.json").read_text(encoding="utf-8"))
    achados["achados"].append({"tipo": "mesa_antiga", "trib": "stj", "peso": 99.0, "selo": "FILA", "titulo": "da véspera", "texto": "",
                               "alvo": "#v=ministros&t=stj", "ministro": None, "prova": {}})
    (pasta / "achados.json").write_text(json.dumps(achados, ensure_ascii=False), encoding="utf-8")
    stj_antes = (pasta / "stj.json").read_bytes()
    antes = _meta(tmp)["fontes"]
    portal.urls.clear()
    r = _rodar(tmp, portal, regras, so="stf", agora=datetime(2026, 11, 24, 12, 0, tzinfo=UTC))
    assert r.refeitos == ["stf"] and (pasta / "stj.json").read_bytes() == stj_antes
    assert not any("dadosabertos" in u for u in portal.urls)
    depois = _meta(tmp)["fontes"]
    assert all(depois[f] == antes[f] for f in dados.IDS_FONTES if f.startswith("stj_"))
    novos = json.loads((pasta / "achados.json").read_text(encoding="utf-8"))["achados"]
    assert novos[0]["titulo"] == "da véspera"  # o do STJ ficou (e, pelo peso, é a manchete)


def test_completo_confere_a_tpu_com_um_pedido(carga: tuple[Path, Portal, dados.Resultado]) -> None:
    tmp, portal, _r = carga
    assert [u for u in portal.urls if "cnj.jus.br" in u] == ["https://www.cnj.jus.br/robots.txt", "https://www.cnj.jus.br/sgt/sgt_ws.php"]
    assert _meta(tmp)["fontes"]["tpu"]["ok"] is True  # a versão é a mesma (05/11/2026): o dump não é baixado


def test_serie_comum_baixa_so_as_mais_novas(tmp_path: Path, regras: modelo.Regras) -> None:
    """Sem ``--completo``, cada execução baixa só as atas mais novas que faltam; o resto vem depois."""
    portal = Portal()
    _rodar(tmp_path, portal, regras)
    atas = [u for u in portal.urls if "/download/ata2026" in u]
    assert len(atas) == dados.SERIE_MAXIMO[False]["atas"]
    f = _meta(tmp_path)["fontes"]["stj_atas"]
    assert f["ok"] is True and f["data_dados"] == "2026-11-22" and "ficam para as próximas coletas" in f["aviso"]
    portal.urls.clear()
    _rodar(tmp_path, portal, regras)
    assert len([u for u in portal.urls if "/download/ata2026" in u]) == 5  # as cinco que faltavam na janela


# ── peças ────────────────────────────────────────────────────────────────────


def test_proxima_execucao() -> None:
    agora = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    fontes = {"stj_acervo": {"ok": True, "data_dados": "2026-11-20"}, "stj_atas": {"ok": True, "data_dados": "2026-11-20"},
              "stf_acervo": {"ok": True, "data_dados": "2026-11-20"}}
    assert dados.proxima_execucao(fontes, agora) == "2026-11-21T17:45:00Z"  # a planilha do STF de 21/11
    fontes["stf_acervo"]["data_dados"] = "2026-11-21"
    assert dados.proxima_execucao(fontes, agora) == "2026-11-21T22:40:00Z"  # a ata do STJ de 21/11
    fontes["stj_atas"]["ok"] = False
    assert dados.proxima_execucao(fontes, agora) == "2026-11-21T14:00:00Z"  # fonte que falhou: daqui a 2 h
    assert dados.proxima_execucao({}, agora) == "2026-11-21T14:00:00Z"
    tarde = {k: {"ok": True, "data_dados": "2026-12-30"} for k in dados.AGENDA}
    assert dados.proxima_execucao(tarde, agora) == "2026-11-22T18:00:00Z"  # nunca a mais de 30 h


def test_trocar_grava_so_o_que_mudou_e_o_meta_por_ultimo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ministros").mkdir()
    (tmp_path / "a.json").write_text("igual\n", encoding="utf-8")
    velho = tmp_path / "ministros" / "stj-x.json"
    velho.write_text("x\n", encoding="utf-8")
    ordem: list[str] = []
    original = dados.os.replace

    def replace(origem: Any, destino: Any) -> None:
        ordem.append(Path(destino).name)
        original(origem, destino)

    monkeypatch.setattr(dados.os, "replace", replace)
    gravados, apagados = dados._trocar(tmp_path, {"ministros/stj-y.json": "y\n", "a.json": "igual\n", "meta.json": "m\n"}, [velho])
    assert (gravados, apagados) == (2, 1) and not velho.exists()
    assert [n for n in ordem if not n.startswith(".")][-1] == "meta.json"
    assert (tmp_path / "ministros" / "stj-y.json").read_text() == "y\n" and not [p for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert dados._trocar(tmp_path, {"a.json": "igual\n", "meta.json": "m\n"}, []) == (0, 0)


def test_motivos_curtos_sem_hora() -> None:
    from qijournal.tribunais.fontes import CaminhoVedado, RecusaDoHost

    assert dados._motivo(RecusaDoHost("u", "x", 403)) == "o portal recusou o acesso (403) duas vezes"
    assert dados._motivo(CaminhoVedado("u", "x")) == "o robots.txt do tribunal veda o endereço"
    assert dados._motivo(FetchError("u", "x", 504)) == "o servidor respondeu 504"
    assert dados._motivo(stj.FormatoInesperado("u", "x")) == "o arquivo veio fora do formato esperado"
