"""``qijournal/tribunais/dados.py``: a atualização de ponta a ponta, sem rede.

Um portal falso responde pelas fontes do STJ e do STF com as amostras reais de
``tests/fixtures/tribunais/`` (acervo do STJ com 44 processos de 20/11/2026, a planilha do STF com 23
de 22/11/2026, atas, pautas, precedentes, íntegras, jurisprudência e repercussão geral), passando
pelo coletor de verdade (robots.txt, pausa por host, recusa do WAF, POST). Confere: a ordem e o que
vai para ``data/tribunais/``; o ``meta.json`` fonte a fonte; que o dia bom não é sobrescrito (recusa
do portal, total fora da faixa, data que volta, teto de tamanho); o fallback das fontes acessórias;
``--so``; os achados do tribunal não refeito; e que rodar de novo não regrava nada.

A agenda da semana (``agenda.json``, no fim do arquivo): gravada ao lado dos achados e válida pelo
contrato; os mesmos dados dão os mesmos bytes; o teto próprio e o da pasta (quem cai é ela); o
tribunal não refeito fica como estava; rodar na quinta mantém a sessão de quarta; a pauta é lida
mesmo com o painel recusado; e nenhuma falha dela impede os painéis.
"""

from __future__ import annotations

import dataclasses
import gzip
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from qijournal import net
from qijournal.agenda import contrato
from qijournal.net import FetchError
from qijournal.tribunais import agenda as agenda_mod
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
    # O DataJud (ligado para uso pessoal no config) tem os testes de ponta a ponta dele, nos dois
    # modos, em test_tribunais_datajud.py; o portal falso daqui não o serve.
    r = modelo.carregar_regras()
    return dataclasses.replace(r, datajud={**r.datajud, "ativo": False})


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
    assert {p.name for p in pasta.iterdir()} == {"meta.json", "achados.json", "agenda.json", "stj.json", "stf.json", "ministros", "tpu"}
    stj_json = json.loads((pasta / "stj.json").read_text(encoding="utf-8"))
    stf_json = json.loads((pasta / "stf.json").read_text(encoding="utf-8"))
    assert (stj_json["v"], stj_json["trib"], stj_json["data_dados"], stj_json["universo"]["processos"]) == (1, "stj", "2026-11-20", 44)
    assert (stf_json["trib"], stf_json["data_dados"], stf_json["universo"]["processos"]) == ("stf", "2026-11-22", 23)
    dossies = sorted(p.name for p in (pasta / "ministros").glob("*.json"))
    assert dossies and all(n.startswith(("stj-", "stf-")) for n in dossies)
    assert r.gravados == 5 + len(dossies)  # stj, stf, achados, agenda, meta e os dossiês
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


# ── a operação (revisão de 23/11/2026) ───────────────────────────────────────


def test_aceitar_acervo_a_mao(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    """OP-9: a mudança real de mais de 15% no acervo, conferida pelo dono, entra uma vez com
    ``--aceitar-acervo`` (o "aceitar_acervo" do disparo manual)."""
    tmp, portal, _r = carga
    meta = _meta(tmp)
    meta["fontes"]["stj_acervo"]["registros"] = 60
    (_pasta(tmp) / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    r = _rodar(tmp, portal, regras, aceitar=("stj",))
    assert "stj" in r.refeitos
    f = _meta(tmp)["fontes"]["stj_acervo"]
    assert f["ok"] is True and f["registros"] == 44
    assert any("aceito à mão" in a for a in r.avisos)


def test_so_a_proxima_execucao_nao_regrava_o_meta(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    """OP-8: fonte atrasada muda a proxima_execucao a cada execução; sem mais nada novo, nada é
    regravado (sem commit, sem build do Pages)."""
    tmp, portal, _r = carga
    antes = _meta(tmp)["proxima_execucao"]
    r = _rodar(tmp, portal, regras, agora=AGORA.replace(hour=19))  # a planilha do STF de 23/11 já devia ter saído: atrasada
    assert (r.gravados, r.apagados) == (0, 0) and _meta(tmp)["proxima_execucao"] == antes


def test_proxima_do_acervo_do_stj_pula_o_fim_de_semana() -> None:
    """OP-4: o arquivo de sexta sai no sábado; o seguinte (o de segunda) só na terça às 17:45 UTC."""
    seg = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    assert dados.proxima_execucao({"stj_acervo": {"ok": True, "data_dados": "2026-11-20"}}, seg) == "2026-11-24T17:45:00Z"
    qui = {"stj_acervo": {"ok": True, "data_dados": "2026-11-19"}}
    assert dados.proxima_execucao(qui, datetime(2026, 11, 20, 12, 0, tzinfo=UTC)) == "2026-11-21T17:45:00Z"  # o de sexta, no sábado
    from datetime import date

    assert dados.dias_da_semana(date(2026, 11, 20), date(2026, 11, 24)) == 2  # sexta → terça
    assert dados.dias_da_semana(date(2026, 11, 20), date(2026, 11, 22)) == 0


def test_aviso_da_serie_com_o_ano() -> None:
    """OP-6: a série de 12 meses das íntegras diz o ano (sem ele, "de 16/10 a 08/10" se lê ao contrário)."""
    from types import SimpleNamespace

    pend = [SimpleNamespace(data="2025-10-16"), SimpleNamespace(data="2026-10-08")]
    serie = SimpleNamespace(data_dados="2026-10-15", ultimo_publicado="2026-10-15", erro=None, pendentes=pend, baixados=[1, 2, 3, 4])
    r = SimpleNamespace(opcoes=SimpleNamespace(hoje=HOJE))
    texto, ok = dados._aviso_da_serie(r, serie, "integras")  # type: ignore[arg-type]
    assert ok and "(de 16/10/2025 a 08/10/2026)" in texto


def test_cache_frio_recupera_e_nao_troca_o_publicado_por_menos(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    """OP-2: com o cache frio, a série já publicada baixa sem o teto por execução (recuperação); e
    a série que ainda cobre menos que a publicada não a substitui: o bloco publicado fica (painel,
    ministros, dossiês) e a entrada da fonte no meta.json também."""
    tmp, portal, _r = carga
    stj_json = json.loads((_pasta(tmp) / "stj.json").read_text(encoding="utf-8"))
    stj_json["decisoes"]["documentos"] += 100_000  # o publicado cobria mais (os 12 meses)
    (_pasta(tmp) / "stj.json").write_text(json.dumps(stj_json, ensure_ascii=False), encoding="utf-8")
    meta = _meta(tmp)
    meta["fontes"]["stj_integras"]["registros"] = 99_999
    (_pasta(tmp) / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    portal.urls.clear()
    r = _rodar(tmp, portal, regras, cache="frio")  # um cache vazio, execução comum
    assert len([u for u in portal.urls if "/download/ata2026" in u]) > dados.SERIE_MAXIMO[False]["atas"]  # recuperação
    assert any("modo de recuperação" in a for a in r.avisos)
    novo = json.loads((_pasta(tmp) / "stj.json").read_text(encoding="utf-8"))
    assert novo["decisoes"] == stj_json["decisoes"]
    assert _meta(tmp)["fontes"]["stj_integras"]["registros"] == 99_999
    assert any("fica o bloco publicado" in a for a in r.avisos)


def test_a_poda_do_cache_roda_sempre_e_guarda_o_que_o_estado_cita(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    """OP-3 e OP-5: a poda roda mesmo com o aviso informativo das íntegras; nunca apaga o que um
    estado cita (o acervo em uso, de sexta a terça, mesmo com mais de um dia); apaga a sobra de mais
    de 10 dias e o arquivo grande fora de uso."""
    import os

    tmp, portal, _r = carga
    cache = tmp / "cache"
    estado = json.loads((cache / "estado" / "stj_acervo.json").read_text(encoding="utf-8"))
    acervo = cache / estado["caminho"]
    velho = 1_000_000.0 - 20 * 86400
    sobra = cache / "arquivos" / "sobra-antiga.json"
    sobra.write_text("{}", encoding="utf-8")
    grande = cache / "arquivos" / "acervo-de-outro-dia.json.gz"
    with grande.open("wb") as f:
        f.truncate(41 * 1024 * 1024)
    for c in (sobra, acervo):
        os.utime(c, (velho, velho))
    portal.falhas["/download/processos_tramitando"] = FetchError("x", "HTTP 500", status=500)  # o acervo de hoje não vem: vale o do estado
    _rodar(tmp, portal, regras)
    assert acervo.is_file()  # citado pelo estado: fica
    assert not sobra.exists() and not grande.exists()


# ── o DJe (DJEN): só lido aqui ──────────────────────────────────────────────


def test_fonte_djen_no_meta_e_fora_do_teto(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem ``djen.json``: a fonte sai ``ok: null`` com a frase de que a coleta só roda no Brasil. Com
    ele (o que a máquina do dono envia): a data é o último dia, ``coletado_em`` é o ``gerado_em``, os
    registros somam o mês; velho (mais de 4 dias de segunda a sexta), o aviso. O arquivo não conta no
    teto da pasta (a aba nunca depende dele) e nenhum pedido vai ao DJEN."""
    from tests.fixtures.tribunais import fabrica

    tmp, portal, _r = carga
    f = _meta(tmp)["fontes"]["djen"]
    assert f["ok"] is None and f["aviso"] == dados.SEM_DJEN and f["velho_dias"] == 4 and f["dias_da_semana"] is True
    paineis = {t: json.loads((_pasta(tmp) / f"{t}.json").read_text(encoding="utf-8")) for t in ("stj",)}
    paineis["stj"]["ministros"] = [m for m in paineis["stj"]["ministros"] if m.get("mais_antigos")]
    djen = fabrica.djen(paineis, ultimo_dia="2026-11-20")
    (_pasta(tmp) / "djen.json").write_text(json.dumps(djen, ensure_ascii=False), encoding="utf-8")
    _rodar(tmp, portal, regras)
    f = _meta(tmp)["fontes"]["djen"]
    assert (f["ok"], f["data_dados"], f["coletado_em"], f["registros"], f["tribunais"], f["aviso"]) == (
        True, "2026-11-20", "2026-11-23T11:17:00Z", 4210, ["stj"], None)
    assert not any("comunicaapi" in u or "comunica.pje" in u for u in portal.urls)
    # velho: o último dia é a sexta de 13/11
    djen["tribunais"]["stj"]["ultimo_dia"] = "2026-11-13"
    (_pasta(tmp) / "djen.json").write_text(json.dumps(djen, ensure_ascii=False), encoding="utf-8")
    _rodar(tmp, portal, regras)
    assert "Sem atualização desde 13/11/2026" in _meta(tmp)["fontes"]["djen"]["aviso"]
    # o teto da pasta não conta o djen.json
    grande = "x" * 200_000
    (_pasta(tmp) / "djen.json").write_text(json.dumps({**djen, "lixo": grande}), encoding="utf-8")
    atual = dados._tamanho_atual(_pasta(tmp), set())
    monkeypatch.setattr(dados, "TETO_PASTA", atual - 100_000)  # com o djen.json, a pasta passaria do teto
    r = _rodar(tmp, portal, regras)
    assert not any("acima do teto" in a for a in r.avisos)



def test_os_citados_do_estado_ignoram_o_que_nao_e_caminho(tmp_path: Path) -> None:
    """O estado da repercussão geral guarda os temas (títulos com "/" e de 300 letras): a poda só
    olha o que tem cara de caminho do cache e nunca derruba a execução (23/11: "File name too long")."""
    cache = tmp_path / "cache"
    (cache / "arquivos").mkdir(parents=True)
    (cache / "arquivos" / "a.json").write_text("{}", encoding="utf-8")
    longo = "Constitucionalidade das contribuições / " + "x" * 400
    dados.Estado(cache).gravar("stf_rg", {"caminho": "arquivos/a.json", "dados": {"temas": [{"titulo": longo}, {"titulo": "Lei 8.870/1994"}]}})
    assert dados.Estado(cache).citados() == {cache / "arquivos" / "a.json"}


# ── a agenda da semana (agenda.json) ─────────────────────────────────────────

AMOSTRA_DA_AGENDA = STJ / "pautas_futuras_20261124_agenda.json.gz"  # origem em tests/test_tribunais_agenda.py
# Hipóteses do teste (o portal do STF não deixou guardar estas duas respostas reais): o calendário
# de dezembro sem sessão presencial e o dia 26/11, que o calendário marca, ainda sem processos.
STF_DEZEMBRO = b'{"diasPresenciais": [], "julgamentosVirtuais": []}'
STF_DIA_SEM_PROCESSOS = b'{"data": "26/11/2026", "tipo": "PRESENCIAL", "colegiados": []}'


def _portal_da_agenda(desde: str | None = None) -> Portal:
    """O portal com a amostra da agenda no lugar das pautas e dos precedentes do STJ (o nome do
    arquivo continua o da página: a data do dado é 22/11) e com as duas respostas do STF que
    faltam. ``desde`` (DD/MM/AAAA no arquivo, aqui AAAA-MM-DD): só as sessões dessa data em diante,
    como a fonte de pautas futuras faz quando o dia passa."""
    portal = Portal()
    itens = json.loads(gzip.decompress(AMOSTRA_DA_AGENDA.read_bytes()))
    if desde:
        itens = [i for i in itens if "-".join(reversed(i["dataSessão"].split("/"))) >= desde]
    pautas = gzip.compress(json.dumps(itens, ensure_ascii=False).encode(), mtime=0)
    for url in portal.arquivos:
        nome = url.rsplit("/", 1)[-1].lower()
        if nome.startswith("pautas_futuras_"):
            portal.arquivos[url] = pautas
        elif nome in ("temas.csv", "processos.csv"):
            portal.arquivos[url] = (STJ / nome.replace(".csv", "_agenda.csv")).read_bytes()
    portal.falhas["dados=calendarios&mes=12&ano=2026"] = STF_DEZEMBRO
    portal.falhas["dados=sessao-presencial&data=26/11/2026"] = STF_DIA_SEM_PROCESSOS
    return portal


def _agenda(tmp: Path) -> contrato.Agenda:
    lida = contrato.ler(_pasta(tmp) / "agenda.json", lado=contrato.LADO_TRIBUNAIS)
    assert lida is not None, "agenda.json ausente ou fora do contrato"
    return lida


def test_agenda_gravada_ao_lado_dos_achados_valida_e_dentro_do_teto(carga: tuple[Path, Portal, dados.Resultado]) -> None:
    tmp, _portal, r = carga
    arquivo = _pasta(tmp) / dados.PAUTA_SEMANAL
    assert arquivo.name == "agenda.json" and arquivo.stat().st_size <= 60_000
    texto = arquivo.read_text(encoding="utf-8")
    assert contrato.validar(json.loads(texto)) == [] and texto.endswith("\n") and "\n" not in texto[:-1]
    a = _agenda(tmp)
    assert (a.lado, a.hoje, a.gerado_em, a.janela) == ("tribunais", HOJE, "2026-11-23T12:00:00Z", ("2026-11-23", "2026-12-06"))
    assert [f.chave for f in a.fontes] == ["stf_pauta", "stj_pautas", "stj_precedentes"]
    stj_pautas = a.fonte("stj_pautas")
    meta = _meta(tmp)["fontes"]
    assert stj_pautas is not None and (stj_pautas.ok, stj_pautas.data_dados, stj_pautas.coletado_em, stj_pautas.velho_dias, stj_pautas.url) == (
        True, "2026-11-22", meta["stj_pautas"]["coletado_em"], meta["stj_pautas"]["velho_dias"], meta["stj_pautas"]["url"])
    # o STF: o Plenário de 25/11 com os 10 processos e a hora; o dia 26/11 não foi lido (o portal falso não o serve)
    plenario = next(s for s in a.sessoes if s.id == "stf-plenario-2026-11-25")
    assert (plenario.hora, plenario.modalidade, len(plenario.itens), plenario.pauta) == ("14:00", "presencial", 10, "publicada")
    assert next(s for s in a.sessoes if s.id == "stf-plenario-2026-11-26").pauta == "nao_coletada"
    stf_pauta = a.fonte("stf_pauta")  # o calendário de dezembro falhou: o que foi lido entra, a fonte fica marcada
    assert stf_pauta is not None and (stf_pauta.ok, stf_pauta.data_dados) == (False, HOJE) and "calendário" in (stf_pauta.aviso or "")
    assert len(a.estado_de("stf").virtuais) == 3  # type: ignore[union-attr]
    # o STJ: os 16 itens da amostra, por sessão; só a proposta de relevância entra com nome
    do_stj = a.sessoes_de("stj")
    assert sum(s.total_na_fonte for s in do_stj) == 16 and all(s.hora is None and s.modalidade is None for s in do_stj)
    assert [(i.identificador, i.alcance) for s in do_stj for i in s.itens] == [("AREsp 3296604", "afetacao")]
    assert "PESSOA FÍSICA" not in texto and "ADVOGADO" not in texto and "mascarado" not in texto
    assert not any("genda" in aviso for aviso in r.avisos)


def test_agenda_mesmos_dados_mesmos_bytes(carga: tuple[Path, Portal, dados.Resultado], tmp_path_factory: pytest.TempPathFactory,
                                          regras: modelo.Regras) -> None:
    tmp, portal, _r = carga
    antes = (_pasta(tmp) / "agenda.json").read_bytes()
    r = _rodar(tmp, portal, regras, agora=AGORA.replace(hour=15))  # mais tarde no mesmo dia, mesmos dados: só o relógio mudou
    assert r.gravados == 0 and (_pasta(tmp) / "agenda.json").read_bytes() == antes
    outra = tmp_path_factory.mktemp("outra-carga")  # outra máquina, do zero, com os mesmos dados
    shutil.copytree(RAIZ / "data" / "tribunais" / "tpu", _pasta(outra) / "tpu")
    _rodar(outra, Portal(), regras, completo=True)
    assert (_pasta(outra) / "agenda.json").read_bytes() == antes


def test_agenda_mantem_a_parte_do_tribunal_nao_refeito(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras) -> None:
    tmp, portal, _r = carga
    antes = _agenda(tmp)
    portal.urls.clear()
    _rodar(tmp, portal, regras, so="stf", agora=datetime(2026, 11, 24, 12, 0, tzinfo=UTC))
    depois = _agenda(tmp)
    assert depois.hoje == "2026-11-24" and not any("dadosabertos" in u for u in portal.urls)
    assert [f for f in depois.fontes if f.instituicao == "stj"] == [f for f in antes.fontes if f.instituicao == "stj"]
    assert [s.id for s in depois.sessoes_de("stj")] == [s.id for s in antes.sessoes_de("stj")]
    assert all(s.como_pautado_em == "2026-11-22" for s in depois.sessoes_de("stj"))  # a data do dado em que a fonte as trazia
    assert [s.itens for s in depois.sessoes_de("stj")] == [s.itens for s in antes.sessoes_de("stj")]
    assert depois.estado_de("stj") == antes.estado_de("stj")
    assert depois.fonte("stf_pauta").data_dados == "2026-11-24"  # type: ignore[union-attr]
    assert len(next(s for s in depois.sessoes if s.id == "stf-plenario-2026-11-25").itens) == 10
    # e o contrário: só o STJ; a parte do STF fica
    _rodar(tmp, portal, regras, so="stj", agora=datetime(2026, 11, 25, 12, 0, tzinfo=UTC))
    final = _agenda(tmp)
    assert final.hoje == "2026-11-25" and final.fonte("stf_pauta") == depois.fonte("stf_pauta")
    assert len(next(s for s in final.sessoes if s.id == "stf-plenario-2026-11-25").itens) == 10


def test_agenda_rodar_na_quinta_mantem_a_sessao_de_quarta(tmp_path: Path, regras: modelo.Regras) -> None:
    """As duas fontes só publicam pautas futuras: na quinta, o Plenário e a Corte Especial de quarta
    já saíram delas e ficam do arquivo anterior, marcados com a data do dado em que constavam."""
    _rodar(tmp_path, _portal_da_agenda(), regras, agora=datetime(2026, 11, 25, 12, 0, tzinfo=UTC))
    quarta = _agenda(tmp_path)
    assert quarta.refeita("stf") and quarta.refeita("stj")
    assert {"stf-plenario-2026-11-25", "stj-ce-2026-11-25", "stj-t1-2026-11-24"} <= {s.id for s in quarta.sessoes}
    assert all(s.como_pautado_em is None for s in quarta.sessoes)
    # quinta-feira, cache novo: o STF só responde de quinta em diante e o STJ não lista mais terça nem quarta
    r = _rodar(tmp_path, _portal_da_agenda(desde="2026-11-26"), regras, cache="quinta", agora=datetime(2026, 11, 26, 12, 0, tzinfo=UTC))
    assert r.refeitos == ["stf", "stj"]
    quinta = _agenda(tmp_path)
    assert quinta.hoje == "2026-11-26" and quinta.refeita("stf") and quinta.refeita("stj")
    por_id = {s.id: s for s in quinta.sessoes}
    plenario, corte = por_id["stf-plenario-2026-11-25"], por_id["stj-ce-2026-11-25"]
    assert (plenario.como_pautado_em, plenario.hora, len(plenario.itens)) == ("2026-11-25", "14:00", 10)
    assert corte.como_pautado_em == "2026-11-22" and [i.identificador for i in corte.itens] == ["AREsp 3296604"]
    assert por_id["stj-t1-2026-11-24"].como_pautado_em == "2026-11-22"
    assert (por_id["stf-plenario-2026-11-26"].pauta, por_id["stf-plenario-2026-11-26"].como_pautado_em) == ("nao_publicada", None)
    assert por_id["stj-ce-2026-12-02"].como_pautado_em is None and len(por_id["stj-ce-2026-12-02"].itens) == 4
    assert all(i.resultado is None for s in quinta.sessoes for i in s.itens)  # sessão passada não é julgamento feito
    # o repetitivo de 09/12 continua depois da janela (23/11 a 06/12): fica no estado do STJ
    proximo = quinta.estado_de("stj").proximo  # type: ignore[union-attr]
    assert proximo is not None and (proximo.id, proximo.itens[0].tema.numero) == ("stj-s2-2026-12-09", 1443)  # type: ignore[union-attr]


def test_agenda_nao_depende_do_painel_recusado(tmp_path: Path, regras: modelo.Regras) -> None:
    """Com o acervo recusado (total fora da faixa no STJ, planilha anterior à em uso no STF), o
    painel da véspera fica e a pauta do dia é lida do mesmo jeito."""
    shutil.copytree(RAIZ / "data" / "tribunais" / "tpu", _pasta(tmp_path) / "tpu")
    _rodar(tmp_path, Portal(), regras, completo=True)
    antes = _meta(tmp_path)
    meta = json.loads(json.dumps(antes))
    meta["fontes"]["stj_acervo"]["registros"] = 60  # 44 de 60: abaixo de 85%, o acervo do STJ é recusado
    meta["fontes"]["stf_acervo"]["data_dados"] = "2026-11-23"  # a planilha de 22/11 é anterior: recusada
    (_pasta(tmp_path) / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    paineis = {t: (_pasta(tmp_path) / f"{t}.json").read_bytes() for t in ("stj", "stf")}
    (_pasta(tmp_path) / "agenda.json").unlink()
    r = _rodar(tmp_path, _portal_da_agenda(), regras, cache="outro", agora=datetime(2026, 11, 24, 12, 0, tzinfo=UTC))
    assert r.refeitos == [] and {t: (_pasta(tmp_path) / f"{t}.json").read_bytes() for t in ("stj", "stf")} == paineis
    a = _agenda(tmp_path)
    assert a.hoje == "2026-11-24" and a.refeita("stj") and a.refeita("stf")
    assert {"stj-ce-2026-12-02", "stj-s2-2026-12-03", "stf-plenario-2026-11-25", "stf-plenario-2026-11-26"} <= {s.id for s in a.sessoes}
    assert a.fonte("stj_pautas").coletado_em == "2026-11-24T12:00:00Z"  # type: ignore[union-attr]
    repetitivos = {i.identificador: i.tema.numero for s in a.sessoes for i in s.itens if i.alcance == "repetitivo" and i.tema}
    assert repetitivos == {"REsp 2034211": 1254, "REsp 2235680": 1448, "REsp 2226097": 1404}
    # o meta.json continua descrevendo o painel publicado: as fontes acessórias ficam como estavam
    depois = _meta(tmp_path)["fontes"]
    assert all(depois[fid] == antes["fontes"][fid] for fid in ("stj_pautas", "stj_precedentes", "stf_pauta", "stf_rg"))


def test_falha_da_agenda_nao_impede_os_paineis(tmp_path: Path, regras: modelo.Regras, monkeypatch: pytest.MonkeyPatch) -> None:
    def quebrada(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("defeito na agenda")

    monkeypatch.setattr(dados.pauta_mod, "montar", quebrada)
    r = _rodar(tmp_path, Portal(), regras)
    pasta = _pasta(tmp_path)
    assert r.refeitos == ["stf", "stj"] and (pasta / "stj.json").is_file() and (pasta / "stf.json").is_file() and (pasta / "achados.json").is_file()
    assert not (pasta / "agenda.json").exists()
    assert any("Agenda da semana não gravada" in a and "defeito na agenda" in a for a in r.avisos)
    assert not any("genda" in a for a in _meta(tmp_path)["avisos"])  # aviso só de operação: o meta.json não muda por causa dela


def test_agenda_fora_do_contrato_ou_acima_do_teto_mantem_o_arquivo_anterior(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    tmp, portal, _r = carga
    antes = (_pasta(tmp) / "agenda.json").read_bytes()
    amanha = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    # 1. o teto próprio (config/agenda.yaml → tetos.arquivos.tribunais_bytes): nem emagrecida cabe
    pequeno = SimpleNamespace(tetos=SimpleNamespace(arquivos=SimpleNamespace(tribunais_bytes=2_000)))
    with monkeypatch.context() as m:
        m.setattr(dados.regras_agenda, "carregar", lambda _root=None: pequeno)
        r = _rodar(tmp, portal, regras, agora=amanha)
    assert r.refeitos == ["stf", "stj"] and (_pasta(tmp) / "agenda.json").read_bytes() == antes
    assert any("Agenda da semana não gravada" in a and "AcimaDoTeto" in a for a in r.avisos)
    # 2. agenda fora do contrato: o contrato recusa, fica o arquivo anterior e a execução segue
    original = agenda_mod.texto

    def fora_do_contrato(a: contrato.Agenda, teto: int = 0) -> tuple[str, list[str]]:
        return original(dataclasses.replace(a, gerado_em="ontem"), 60_000)

    with monkeypatch.context() as m:
        m.setattr(dados.pauta_mod, "texto", fora_do_contrato)
        r = _rodar(tmp, portal, regras, agora=amanha.replace(hour=13))
    assert (_pasta(tmp) / "agenda.json").read_bytes() == antes and any("ErroDeContrato" in a for a in r.avisos)
    # 3. sem defeito, a mesma execução grava a agenda do dia
    _rodar(tmp, portal, regras, agora=amanha.replace(hour=14))
    assert _agenda(tmp).hoje == "2026-11-24"


def test_no_teto_da_pasta_quem_cai_e_a_agenda(carga: tuple[Path, Portal, dados.Resultado], regras: modelo.Regras,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    tmp, portal, _r = carga
    antes = (_pasta(tmp) / "agenda.json").read_bytes()
    sem_a_agenda = dados._tamanho_atual(_pasta(tmp), {"agenda.json"})
    monkeypatch.setattr(dados, "TETO_PASTA", sem_a_agenda + 2_000)  # os painéis cabem; com a agenda (9 KB), a pasta passaria
    r = _rodar(tmp, portal, regras, agora=datetime(2026, 11, 24, 12, 0, tzinfo=UTC))
    assert r.refeitos == ["stf", "stj"] and (_pasta(tmp) / "agenda.json").read_bytes() == antes
    assert any("a agenda de hoje não foi gravada" in a for a in r.avisos)
    assert _meta(tmp)["hoje"] == "2026-11-24" and not any("tamanho máximo" in (f.get("aviso") or "") for f in _meta(tmp)["fontes"].values())
