"""``python -m qijournal tribunais atualizar|pagina`` (``qijournal/tribunais/cli.py``) e o botão
cruzado: a linha de comando fixa do brief §6 (o ``tribunais.yml`` depende dela), a aba desligada,
o resumo no ``$GITHUB_STEP_SUMMARY``, os códigos de saída e o botão "Tribunais" que a edição
(``pipeline.anexar_tribunais``) e a aba do Congresso (``congresso.cli.renderizar``) acendem quando
``edicoes/tribunais.html`` existe."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import qijournal.__main__ as principal
from qijournal import pipeline
from qijournal.config import Config, load_config
from qijournal.congresso import cli as congresso_cli
from qijournal.render import congresso as pagina_congresso
from qijournal.render import tribunais as pagina
from qijournal.render.web import brand_view
from qijournal.tribunais import cli, dados
from tests.fixtures.tribunais import fabrica

BUNDLE = Path(__file__).resolve().parent / "fixtures" / "bundle.json"


def _config(**env: str) -> Config:
    config = load_config(env=env)
    config.congresso.publicado = False
    config.tribunais.publicado = False
    return config


def _site(raiz: Path) -> Path:
    (raiz / "data").mkdir(parents=True, exist_ok=True)
    fabrica.gravar(raiz / "data")
    return raiz


# ── a linha de comando ───────────────────────────────────────────────────────


def test_a_linha_de_comando_fixa_do_brief() -> None:
    parser = principal.build_parser()
    a = parser.parse_args(["tribunais", "atualizar", "--dir", ".", "--cache", "/tmp/x", "--completo", "--so", "stj"])
    assert (a.handler, a.dir, a.cache, a.completo, a.so) == (cli.cmd_atualizar, ".", "/tmp/x", True, "stj")
    a = parser.parse_args(["tribunais", "atualizar"])
    assert (a.dir, a.cache, a.completo, a.so) == (".", cli.CACHE_PADRAO, False, None)
    a = parser.parse_args(["tribunais", "pagina", "--dir", "site"])
    assert (a.handler, a.dir) == (cli.cmd_pagina, "site")
    with pytest.raises(SystemExit):
        parser.parse_args(["tribunais", "atualizar", "--so", "trf1"])


def _args(tmp: Path, **kw: Any) -> argparse.Namespace:
    return argparse.Namespace(**{"dir": str(tmp), "cache": str(tmp / "cache"), "completo": False, "so": None, "now": None, **kw})


def test_desligada_nao_coleta_nem_grava(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def proibido(*a: Any, **k: Any) -> None:
        raise AssertionError("com a aba desligada nada é coletado")

    monkeypatch.setattr(dados, "atualizar", proibido)
    desligada = _config(QIJ_NO_TRIBUNAIS="1")
    assert cli.cmd_atualizar(_args(tmp_path), desligada) == cli.EXIT_OK
    _site(tmp_path)
    assert cli.cmd_pagina(_args(tmp_path), desligada) == cli.EXIT_OK
    assert not (tmp_path / "edicoes" / "tribunais.html").exists()


def _resultado(tmp: Path) -> dados.Resultado:
    fontes = {
        "stj_acervo": {"nome": "STJ · acervo em tramitação", "ok": True, "data_dados": "2026-11-20", "registros": 352239, "aviso": None},
        "stj_atas": {"nome": "STJ · atas de distribuição", "ok": False, "data_dados": "2026-11-21", "registros": 5466,
                     "aviso": "A coleta falhou em 23/11 (o portal recusou o acesso (403) duas vezes); ficam os dados de 21/11/2026."},
        "datajud": {"nome": "CNJ · DataJud (API Pública)", "ok": None, "data_dados": None, "registros": None, "aviso": "Desligado"},
    }
    return dados.Resultado(pasta=tmp, gravados=12, apagados=1, avisos=["STJ · atas de distribuição: a coleta falhou"], refeitos=["stj"],
                           numeros={"stj": {"data_dados": "2026-11-20", "processos": 352239, "na_grade": 210361, "na_mesa": 139047,
                                            "ministros": 33, "dossies": 33}},
                           fontes=fontes, requisicoes=41, megabytes=97.3, segundos=600.0, datajud="DataJud: desligado no config (nenhum pedido)")


def test_atualizar_chama_dados_grava_a_pagina_e_escreve_o_resumo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[dict[str, Any]] = []

    def atualizar(root: Path, data_dir: Path, cache_dir: Path, opcoes: dados.Opcoes) -> dados.Resultado:
        chamadas.append({"data_dir": data_dir, "cache_dir": cache_dir, "opcoes": opcoes})
        _site(tmp_path)
        return _resultado(tmp_path)

    monkeypatch.setattr(dados, "atualizar", atualizar)
    resumo = tmp_path / "resumo.md"
    resumo.write_text("antes\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(resumo))
    args = _args(tmp_path, completo=True, so="stj", now=datetime(2026, 11, 23, 2, 30, tzinfo=UTC))
    assert cli.cmd_atualizar(args, _config()) == cli.EXIT_OK
    (c,) = chamadas
    assert c["data_dir"] == tmp_path / "data" and c["cache_dir"] == tmp_path / "cache"
    o = c["opcoes"]
    assert (o.hoje, o.completo, o.so, o.agora) == ("2026-11-22", True, "stj", args.now)  # 02:30 UTC ainda é 22/11 em Brasília
    assert (tmp_path / "edicoes" / "tribunais.html").is_file()
    texto = resumo.read_text(encoding="utf-8")
    assert texto.startswith("antes\n### ⚖️ Tribunais")  # acrescenta, nunca apaga o que o fluxo já escreveu
    assert "| STJ · acervo em tramitação | ✅ | 20/11/2026 | 352.239 |" in texto
    assert "| STJ · atas de distribuição | ⚠️ | 21/11/2026 | 5.466 | A coleta falhou" in texto
    assert "| STJ (20/11/2026) | 352.239 | 210.361 | 139.047 | 33 | 33 |" in texto
    assert "Painéis refeitos: STJ" in texto and "#### Avisos" in texto and "edicoes/tribunais.html" in texto


def test_erro_de_programa_sai_1_e_fonte_que_falha_sai_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def quebrado(*a: Any, **k: Any) -> None:
        raise TypeError("erro de programa")

    monkeypatch.setattr(dados, "atualizar", quebrado)
    argv = ["tribunais", "atualizar", "--dir", str(tmp_path), "--cache", str(tmp_path / "c")]
    assert principal.main(argv, config_loader=_config) == principal.EXIT_ERROR
    monkeypatch.setattr(dados, "atualizar", lambda *a, **k: _resultado(tmp_path))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert principal.main(argv, config_loader=_config) == principal.EXIT_OK  # sem dados para a página: aviso, sai 0


def test_pagina_sem_rede(tmp_path: Path) -> None:
    argv = ["tribunais", "pagina", "--dir", str(tmp_path)]
    assert principal.main(argv, config_loader=_config) == principal.EXIT_ERROR  # sem data/tribunais/
    _site(tmp_path)
    assert principal.main(argv, config_loader=_config) == principal.EXIT_OK
    caminho = tmp_path / "edicoes" / "tribunais.html"
    assert caminho.is_file() and caminho.stat().st_size <= pagina.TETO_BYTES


def test_pagina_acima_do_teto_deixa_a_execucao_vermelha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OP-10: acima do teto a página não é gravada e o comando sai 1 (os dois: atualizar e pagina):
    o passo "Publicar" não roda e nada sai com os dados novos e a página velha."""
    _site(tmp_path)
    monkeypatch.setattr(pagina, "TETO_BYTES", 10_000)
    monkeypatch.setattr(dados, "atualizar", lambda *a, **k: _resultado(tmp_path))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    argv = ["tribunais", "atualizar", "--dir", str(tmp_path), "--cache", str(tmp_path / "c")]
    assert principal.main(argv, config_loader=_config) == principal.EXIT_ERROR
    assert principal.main(["tribunais", "pagina", "--dir", str(tmp_path)], config_loader=_config) == principal.EXIT_ERROR
    assert not (tmp_path / "edicoes" / "tribunais.html").exists()


def test_atualizar_de_verdade_sem_rede_nao_derruba(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem rede nenhuma (todo pedido falha): avisos no meta.json, nenhuma página, saída 0."""
    from qijournal.net import FetchError

    def sem_rede(url: str, **kw: Any) -> Any:
        raise FetchError(url, "sem rede", 503)

    monkeypatch.setattr("qijournal.net.fetch", sem_rede)
    monkeypatch.setattr("qijournal.tribunais.fontes.net.fetch", sem_rede)
    original = dados.atualizar
    monkeypatch.setattr(dados, "atualizar", lambda *a, **k: original(*a, fetch=sem_rede, dormir=lambda s: None, **k))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    argv = ["tribunais", "atualizar", "--dir", str(tmp_path), "--cache", str(tmp_path / "c"), "--now", "2026-11-23T12:00:00"]
    assert principal.main(argv, config_loader=_config) == principal.EXIT_OK
    meta = dados.ler_json(tmp_path / "data" / "tribunais" / "meta.json")
    assert meta["fontes"]["stj_acervo"]["ok"] is False and meta["fontes"]["stf_acervo"]["ok"] is False
    assert not (tmp_path / "edicoes" / "tribunais.html").exists()


# ── o botão cruzado ──────────────────────────────────────────────────────────


def test_a_edicao_acende_o_botao_quando_a_aba_existe(tmp_path: Path) -> None:
    config = _config()
    pipeline.anexar_tribunais(config, tmp_path)
    assert config.tribunais.publicado is False and brand_view(config).tribunais is False
    _site(tmp_path)
    assert pagina.gravar_pagina(tmp_path, _config()) is not None
    pipeline.anexar_tribunais(config, tmp_path)
    assert config.tribunais.publicado is True and brand_view(config).tribunais is True
    desligada = _config(QIJ_NO_TRIBUNAIS="1")
    pipeline.anexar_tribunais(desligada, tmp_path)
    assert desligada.tribunais.publicado is False


def test_a_edicao_offline_tem_o_botao_tribunais(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A capa, o arquivo e as páginas da edição (``render --no-llm``, sem rede nem IA) com a aba gravada."""
    for chave in ("ANTHROPIC_API_KEY", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO", "GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS"):
        monkeypatch.delenv(chave, raising=False)
    _site(tmp_path)
    pagina.gravar_pagina(tmp_path, _config())
    argv = ["render", "--bundle", str(BUNDLE), "--out", str(tmp_path), "--no-llm"]
    assert principal.main(argv, config_loader=_config) == principal.EXIT_OK
    capa = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert '<a class="tool tool-tb" href="edicoes/tribunais.html"' in capa
    arquivo = (tmp_path / "edicoes" / "index.html").read_text(encoding="utf-8")
    assert 'href="tribunais.html"' in arquivo and "tool-tb" in arquivo


def test_a_aba_do_congresso_acende_o_botao_tribunais(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vistos: list[bool] = []

    def falsa(config: Config, data_dir: Path, *, home_href: str, archive_href: str) -> str:
        vistos.append(brand_view(config).tribunais)
        return "<html></html>"

    monkeypatch.setattr(pagina_congresso, "render_congresso_page", falsa)
    config = _config()
    assert congresso_cli.renderizar(config, tmp_path) is not None and vistos == [False]
    _site(tmp_path)
    pagina.gravar_pagina(tmp_path, _config())
    congresso_cli.renderizar(config, tmp_path)
    assert vistos == [False, True]
