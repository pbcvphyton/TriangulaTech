"""Os fluxos do GitHub da aba Tribunais: ``tribunais.yml`` (coleta própria, fila própria, commit só de
``data/tribunais`` e da aba) e o que muda em ``daily.yml``, ``congresso.yml`` e ``ci.yml``.

Além da forma dos YAML, os scripts dos passos rodam de verdade (``bash -eo pipefail``, como no
Actions), com ``date``, ``sleep`` e ``gh`` de mentira e repositórios git locais: a decisão do job
"decidir", o "Publicar" dos Tribunais (reaplica o commit sobre o remoto), a corrida com o "Publicar"
da edição (que, sem a exclusão, desfazia o que os Tribunais publicaram no meio dela), a conferência
da validade das fontes, a issue para o dono e a preparação do cache. Sem rede.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FLUXOS = ROOT / ".github" / "workflows"
AGORA, LIMITE, HOJE, DIA = "2026-11-23T12:00:00Z", "2026-11-25T00:00:00Z", "2026-11-23", "23/11/2026"
COMMIT = "⚖️ Tribunais em 23/11/2026"
precisa_de_git = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="sem git ou sem bash")
precisa_de_bash = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("python3") is None, reason="sem bash ou python3")


def _fluxo(nome: str) -> dict[str, Any]:
    yml = yaml.safe_load((FLUXOS / nome).read_text(encoding="utf-8"))
    if True in yml:  # o YAML 1.1 lê a chave "on" como verdadeiro
        yml["on"] = yml.pop(True)
    return yml


def _passo(job: dict[str, Any], nome: str) -> dict[str, Any]:
    (passo,) = [p for p in job["steps"] if p.get("name") == nome]
    return passo


TRIB = _fluxo("tribunais.yml")
JOB = TRIB["jobs"]["tribunais"]
DECIDIR = TRIB["jobs"]["decidir"]


# ── a forma do tribunais.yml ─────────────────────────────────────────────────────────────────


def test_gatilhos_edicao_reserva_e_disparo_manual():
    """Depois de cada "Edição diária" na main, um agendamento de reserva por dia e o disparo
    manual (carga completa, um tribunal só, ensaio). Nunca em pull_request: o job tem permissão
    de escrita."""
    on = TRIB["on"]
    assert TRIB["name"] == "Tribunais" and set(on) == {"workflow_run", "schedule", "workflow_dispatch"}
    assert on["workflow_run"] == {"workflows": ["Edição diária"], "types": ["completed"], "branches": ["main"]}
    assert _fluxo("daily.yml")["name"] == "Edição diária"
    (cron,) = on["schedule"]
    assert cron == {"cron": "41 1 * * *"}  # 22:41 em Brasília, depois da ata (19:30) e do acervo da véspera
    entradas = on["workflow_dispatch"]["inputs"]
    assert list(entradas) == ["completo", "so", "ensaio", "aceitar_acervo"]
    assert entradas["aceitar_acervo"]["options"] == ["nenhum", "stj", "stf"] and entradas["aceitar_acervo"]["default"] == "nenhum"
    assert entradas["completo"]["type"] == "boolean" and entradas["completo"]["default"] is False
    assert entradas["so"]["type"] == "choice" and entradas["so"]["options"] == ["ambos", "stj", "stf"] and entradas["so"]["default"] == "ambos"
    assert entradas["ensaio"]["type"] == "boolean" and entradas["ensaio"]["default"] is False


def test_permissoes_minimas_e_fila_propria():
    """O fluxo só lê; o job que publica pede contents (commit), pages (build) e issues (aviso). A
    fila é do job, própria (nunca a da edição) e não cancela quem está rodando."""
    assert TRIB["permissions"] == {"contents": "read"} and "permissions" not in DECIDIR
    assert JOB["permissions"] == {"contents": "write", "pages": "write", "issues": "write"}
    assert "concurrency" not in TRIB and "concurrency" not in DECIDIR
    assert JOB["concurrency"] == {"group": "tribunais-${{ github.ref }}", "cancel-in-progress": False}
    grupos = {_fluxo(f)["concurrency"]["group"] for f in ("daily.yml", "congresso.yml")}
    assert JOB["concurrency"]["group"] not in grupos
    assert JOB["needs"] == "decidir" and JOB["if"] == "needs.decidir.outputs.rodar == 'true'"


def test_o_decidir_e_leve():
    """Só o meta.json (checkout parcial), sem Python instalado nem cache: termina em segundos."""
    assert DECIDIR["timeout-minutes"] == 5 and DECIDIR["outputs"] == {"rodar": "${{ steps.decidir.outputs.rodar }}"}
    assert DECIDIR["if"] == "github.event_name != 'workflow_run' || github.event.workflow_run.conclusion != 'cancelled'"
    baixar, decidir = DECIDIR["steps"]
    assert baixar["uses"] == "actions/checkout@v5"
    assert baixar["with"] == {"ref": "${{ github.ref_name }}", "sparse-checkout": "data/tribunais/meta.json\ndata/tribunais/djen.json\n",
                              "sparse-checkout-cone-mode": False}  # o djen.json: a máquina do dono pode ter mandado um novo
    assert decidir["id"] == "decidir" and "uses" not in decidir
    assert decidir["env"] == {"EVENT": "${{ github.event_name }}", "QIJ_NO_TRIBUNAIS": "${{ vars.QIJ_NO_TRIBUNAIS }}", "INPUT_ENSAIO": "${{ inputs.ensaio }}"}


def test_passos_tempos_e_a_linha_de_comando_fixa():
    """A ordem dos passos, os tempos (o passo de coleta cabe no job) e a linha de comando que a
    integração garante: ``tribunais atualizar --dir . --cache … [--completo] [--so stj|stf]`` e
    ``tribunais pagina --dir .``. Nenhuma entrada é interpolada direto num script."""
    nomes = [p["name"] for p in JOB["steps"]]
    assert nomes == [
        "Baixar o repositório", "Configurar Python", "Instalar dependências", "Restaurar o cache dos Tribunais",
        "Atualizar os Tribunais", "Refazer a aba com o que ficou gravado", "Publicar (commit e push)", "Pedir build do GitHub Pages",
        "Conferir a validade das fontes", "Avisar o dono (issue)", "Fechar o aviso resolvido", "Guardar o ensaio",
        "Guardar o diário da coleta", "Preparar o cache", "Guardar o cache", "Resultado",
    ]  # fmt: skip
    assert all("${{" not in p.get("run", "") for job in TRIB["jobs"].values() for p in job["steps"])
    assert JOB["timeout-minutes"] == 95  # o cache frio só se sabe no passo
    atualizar = _passo(JOB, "Atualizar os Tribunais")
    assert atualizar["timeout-minutes"] == "${{ (inputs.completo || steps.restaurar.outputs.cache-matched-key == '') && 75 || 30 }}"
    assert atualizar["continue-on-error"] is True
    assert 'args=(--dir . --cache "$RUNNER_TEMP/tribunais")' in atualizar["run"]
    assert 'if [ "$INPUT_COMPLETO" = "true" ]; then args+=(--completo); fi' in atualizar["run"]
    assert 'stj|stf) args+=(--so "$INPUT_SO") ;;' in atualizar["run"]
    assert 'stj|stf) args+=(--aceitar-acervo "$INPUT_ACEITAR") ;;' in atualizar["run"]
    # OP-2: o cache frio (o primeiro dia na main) vira carga completa
    assert atualizar["env"]["FRIO"] == "${{ steps.restaurar.outputs.cache-matched-key == '' }}"
    assert 'if [ "$INPUT_COMPLETO" != "true" ] && [ "$FRIO" = "true" ]; then' in atualizar["run"]
    assert atualizar["run"].rstrip().endswith('python -m qijournal tribunais atualizar "${args[@]}"')
    env = atualizar["env"]
    assert env["QIJ_NO_TRIBUNAIS"] == "${{ !inputs.ensaio && vars.QIJ_NO_TRIBUNAIS || '' }}"
    assert env["DATAJUD_API_KEY"] == "${{ vars.DATAJUD_API_KEY }}" and "secrets." not in json.dumps(env)  # nenhum segredo novo
    assert {env[k] for k in ("INPUT_COMPLETO", "INPUT_SO", "INPUT_ACEITAR")} == {"${{ inputs.completo }}", "${{ inputs.so }}", "${{ inputs.aceitar_acervo }}"}
    assert env["TZ"] == "America/Sao_Paulo"
    pagina = _passo(JOB, "Refazer a aba com o que ficou gravado")
    assert pagina["run"] == "python -m qijournal tribunais pagina --dir ." and pagina["timeout-minutes"] == 5
    assert pagina["if"] == "${{ !cancelled() && steps.atualizar.outcome != 'success' }}"
    resultado = _passo(JOB, "Resultado")
    assert resultado["if"] == "${{ !cancelled() && steps.atualizar.outcome != 'success' }}" and "exit 1" in resultado["run"]
    publicar = _passo(JOB, "Publicar (commit e push)")
    assert publicar["if"] == "${{ !cancelled() && !inputs.ensaio && (steps.atualizar.outcome == 'success' || steps.pagina.outcome == 'success') }}"
    assert "continue-on-error" not in publicar
    for nome in ("Conferir a validade das fontes", "Avisar o dono (issue)", "Fechar o aviso resolvido"):
        assert "!inputs.ensaio" in _passo(JOB, nome)["if"], nome  # o ensaio não publica nem avisa


def test_o_cache_restaura_o_mais_novo_e_guarda_pelo_conteudo():
    restaurar, guardar = _passo(JOB, "Restaurar o cache dos Tribunais"), _passo(JOB, "Guardar o cache")
    assert restaurar["uses"] == "actions/cache/restore@v5" and restaurar["continue-on-error"] is True
    assert restaurar["with"] == {"path": "${{ runner.temp }}/tribunais", "key": "tribunais-${{ github.run_id }}", "restore-keys": "tribunais-"}
    assert guardar["uses"] == "actions/cache/save@v5" and guardar["continue-on-error"] is True
    assert guardar["with"] == {"path": "${{ runner.temp }}/tribunais", "key": "${{ steps.preparar.outputs.chave }}"}
    assert guardar["if"] == "always() && steps.preparar.outputs.guardar == 'true'"
    assert _passo(JOB, "Preparar o cache")["env"] == {"RESTAURADO": "${{ steps.restaurar.outputs.cache-matched-key }}"}
    diario = _passo(JOB, "Guardar o diário da coleta")
    assert diario["with"]["path"] == "${{ runner.temp }}/tribunais/diario/" and diario["if"] == "always()"
    nomes = [p["name"] for p in JOB["steps"]]
    assert nomes.index("Guardar o diário da coleta") < nomes.index("Preparar o cache")  # o diário sai do cache depois de guardado


def test_o_publicar_dos_tribunais_so_leva_a_pasta_e_a_aba():
    script = _passo(JOB, "Publicar (commit e push)")["run"]
    assert re.findall(r"git add\b[^\n]*", script) == ['git add --all -- "${caminhos[@]}"']
    assert "caminhos=(data/tribunais)" in script and "caminhos+=(edicoes/tribunais.html)" in script
    assert re.findall(r"git commit\b[^\n]*", script) == ['git commit --quiet -m "⚖️ Tribunais em $(date +%d/%m/%Y)"']
    for proibido in ("git reset", "git checkout", "git restore", "git rm", "git clean", "git stash", "git merge", "git pull", "--force", "add -A", "add .", "commit -a"):
        assert proibido not in script, proibido
    assert re.findall(r"git (?:fetch|rebase)\b[^\n;]*", script) == ['git fetch --quiet origin "${GITHUB_REF_NAME}"', "git rebase --quiet --autostash FETCH_HEAD", "git rebase --abort || true"]


# ── as mudanças nos outros fluxos ────────────────────────────────────────────────────────────


def test_a_edicao_nao_coleta_os_tribunais_e_exclui_a_pasta_do_commit():
    """O ``daily.yml`` não roda o comando dos Tribunais; passa ``QIJ_NO_TRIBUNAIS`` aos passos que
    desenham cabeçalho (o botão "Tribunais") e exclui ``data/tribunais`` e a aba do ``stage``."""
    diario = _fluxo("daily.yml")
    texto = (FLUXOS / "daily.yml").read_text(encoding="utf-8")
    assert "qijournal tribunais" not in texto
    edicao = diario["jobs"]["edition"]
    for nome in ("Gerar a edição", "Atualizar o Congresso"):
        assert _passo(edicao, nome)["env"]["QIJ_NO_TRIBUNAIS"] == "${{ vars.QIJ_NO_TRIBUNAIS }}", nome
    publicar = _passo(edicao, "Publicar (commit e push)")["run"]
    stage = publicar[publicar.index("stage() {") : publicar.index("}", publicar.index("stage() {"))]
    assert re.search(r"git add \"\$@\" -- index\.html edicoes data \\\n\s+':\(exclude\)data/tribunais' ':\(exclude\)edicoes/tribunais\.html'\n", stage)
    congresso = _fluxo("congresso.yml")["jobs"]["congresso"]
    assert _passo(congresso, "Atualizar o Congresso")["env"]["QIJ_NO_TRIBUNAIS"] == "${{ vars.QIJ_NO_TRIBUNAIS }}"
    assert "git add \"$@\" -- data/congresso edicoes/congresso.html" in _passo(congresso, "Publicar (commit e push)")["run"]


def test_ci_com_folga_e_a_aba_de_exemplo():
    ci = _fluxo("ci.yml")["jobs"]["test"]
    assert ci["timeout-minutes"] == 20 and _passo(ci, "Testes")["run"] == "pytest --durations=15"
    exemplo = _passo(ci, "Aba Tribunais de exemplo (offline)")["run"]
    assert "fabrica.gravar(" in exemplo and 'python -m qijournal tribunais pagina --dir "$dir"' in exemplo
    assert '[ "$size" -le 1500000 ]' in exemplo  # o teto da página no contrato (1,5 MB)
    assert exemplo.index("qijournal/tribunais/cli.py") < exemplo.index("python -m qijournal tribunais pagina")
    nomes = [p["name"] for p in ci["steps"]]
    assert nomes.index("Testes") < nomes.index("Aba Tribunais de exemplo (offline)") < nomes.index("Edição de exemplo (offline)")


# ── os scripts, rodados de verdade ───────────────────────────────────────────────────────────


def _ambiente(tmp: Path, **extra: str) -> dict[str, str]:
    """``date``, ``sleep`` e ``gh`` de mentira na frente do PATH, git sem a configuração da máquina."""
    bin_ = tmp / "bin"
    if not bin_.is_dir():
        bin_.mkdir(parents=True)
        (bin_ / "date").write_text(
            "#!/bin/sh\n"
            'case "$*" in\n'
            f'  "-u +%Y-%m-%dT%H:%M:%SZ") echo {AGORA} ;;\n'
            f'  "-u -d +36 hours +%Y-%m-%dT%H:%M:%SZ") echo {LIMITE} ;;\n'
            f'  "+%F"|"-u +%F") echo {HOJE} ;;\n'
            f'  "+%d/%m/%Y") echo {DIA} ;;\n'
            '  *) echo "date de mentira: $*" >&2; exit 9 ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        (bin_ / "sleep").write_text("#!/bin/sh\n", encoding="utf-8")
        (bin_ / "gh").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$GH_REGISTRO"\n'
            'case "$1 $2" in\n'
            '  "issue list") printf "%s\\n" "$GH_NUMERO" ;;\n'
            '  "issue view") printf "%s\\n" "$GH_ULTIMO" ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        for comando in bin_.iterdir():
            comando.chmod(0o755)
        (tmp / "gitconfig").write_text("[user]\n\tname = Outro\n\temail = outro@example.invalid\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    return {
        "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "HOME": str(tmp), "GIT_CONFIG_GLOBAL": str(tmp / "gitconfig"), "GIT_CONFIG_SYSTEM": os.devnull,
        "GITHUB_OUTPUT": str(tmp / "saida"), "GITHUB_STEP_SUMMARY": str(tmp / "resumo"), "GITHUB_REF_NAME": "main", "GITHUB_REPOSITORY": "dono/jornal",
        "GITHUB_REPOSITORY_OWNER": "dono", "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "7", "RUNNER_TEMP": str(tmp / "runner-temp"),
        "GH_REGISTRO": str(tmp / "gh.log"), "TZ": "America/Sao_Paulo", **extra,
    }  # fmt: skip


def _rodar(script: str, onde: Path, tmp: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    """Roda o script de um passo como o Actions (``bash -eo pipefail``), em ``onde``."""
    (tmp / "passo.sh").write_text(script, encoding="utf-8")
    for nome in ("saida", "resumo"):
        (tmp / nome).write_text("", encoding="utf-8")
    (tmp / "runner-temp").mkdir(exist_ok=True)
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(tmp / "passo.sh")], cwd=onde, env=_ambiente(tmp, **extra),
                          capture_output=True, text=True, timeout=120, check=False)  # fmt: skip


def _saida(tmp: Path) -> dict[str, str]:
    return dict(linha.split("=", 1) for linha in (tmp / "saida").read_text(encoding="utf-8").splitlines() if "=" in linha)


@precisa_de_bash
@pytest.mark.parametrize(
    ("evento", "proxima", "ambiente", "rodar"),
    [
        ("workflow_run", "2026-11-23T22:40:00Z", {}, "false"),  # nada novo antes das 22:40
        ("schedule", "2026-11-23T22:40:00Z", {}, "false"),
        ("workflow_run", "2026-11-23T11:59:59Z", {}, "true"),  # a marca passou
        ("workflow_run", "2026-11-23T12:00:00Z", {}, "true"),
        ("workflow_run", None, {}, "true"),  # sem meta.json: a primeira carga
        ("workflow_run", "", {}, "true"),  # meta sem a marca
        ("workflow_run", "amanhã cedo", {}, "true"),  # marca ilegível não segura a coleta
        ("schedule", "2026-12-30T00:00:00Z", {}, "true"),  # marca longe demais (erro de cálculo): confere assim mesmo
        ("workflow_dispatch", "2026-11-23T22:40:00Z", {}, "true"),  # à mão, sempre
        ("workflow_run", "2026-11-23T11:00:00Z", {"QIJ_NO_TRIBUNAIS": "1"}, "false"),  # desligado
        ("workflow_run", "2026-11-23T11:00:00Z", {"QIJ_NO_TRIBUNAIS": " Sim "}, "false"),
        ("workflow_dispatch", None, {"QIJ_NO_TRIBUNAIS": "true"}, "false"),  # desligado: nem à mão...
        ("workflow_dispatch", None, {"QIJ_NO_TRIBUNAIS": "1", "INPUT_ENSAIO": "true"}, "true"),  # ...salvo o ensaio, que não publica
        ("workflow_run", "2026-11-23T11:00:00Z", {"QIJ_NO_TRIBUNAIS": "0"}, "true"),  # "0" não desliga
    ],
)
def test_o_decidir_rodado_de_verdade(tmp_path: Path, evento: str, proxima: str | None, ambiente: dict[str, str], rodar: str):
    repo = tmp_path / "repo"
    (repo / "data" / "tribunais").mkdir(parents=True)
    if proxima is not None:
        (repo / "data" / "tribunais" / "meta.json").write_text(json.dumps({"v": 1, "proxima_execucao": proxima}), encoding="utf-8")
    feito = _rodar(_passo(DECIDIR, "Verificar se há o que fazer")["run"], repo, tmp_path, EVENT=evento, **ambiente)
    assert feito.returncode == 0, feito.stdout + feito.stderr
    assert _saida(tmp_path) == {"rodar": rodar}
    resumo = (tmp_path / "resumo").read_text(encoding="utf-8")
    assert resumo.strip() and resumo.strip() in feito.stdout  # o porquê vai ao Summary
    assert ("::warning::" in feito.stdout) == (proxima == "2026-12-30T00:00:00Z")


# ── o "Publicar" dos Tribunais e a corrida com a edição ──────────────────────────────────────

INICIAL = {
    "index.html": "ontem\n",
    "edicoes/latest.json": '{"date":"2026-11-22"}\n',
    "edicoes/tribunais.html": "<p>tribunais v1</p>\n",
    "data/congresso/meta.json": "c1\n",
    "data/tribunais/meta.json": '{"v":1,"n":1}\n',
    "data/tribunais/ministros/stj-a.json": "a1\n",
    "data/tribunais/ministros/stj-b.json": "b1\n",
}


class Repos:
    """O repositório do GitHub (``origin.git``), a cópia rasa da execução (``runner``) e a de outro
    fluxo que envia enquanto a execução trabalha (``outro``)."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.env = _ambiente(tmp)
        self.origem, self.runner, self.outro = tmp / "origin.git", tmp / "runner", tmp / "outro"
        self.git(tmp, "init", "--quiet", "--bare", str(self.origem))
        self.git(tmp, "clone", "--quiet", str(self.origem), str(self.outro))
        self.gravar(self.outro, INICIAL)
        self.enviar("inicial")
        self.git(tmp, "clone", "--quiet", "--depth", "1", self.origem.as_uri(), str(self.runner))  # raso, como o checkout do Actions

    def git(self, onde: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=onde, env=self.env, check=True, capture_output=True, text=True, timeout=60).stdout

    @staticmethod
    def gravar(repo: Path, arquivos: dict[str, str | None]) -> None:
        for caminho, texto in arquivos.items():
            destino = repo / caminho
            if texto is None:
                destino.unlink()
                continue
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(texto, encoding="utf-8")

    def enviar(self, mensagem: str, arquivos: dict[str, str | None] | None = None) -> None:
        if arquivos:
            self.gravar(self.outro, arquivos)
        self.git(self.outro, "add", "--all")
        self.git(self.outro, "commit", "--quiet", "-m", mensagem)
        self.git(self.outro, "push", "--quiet", "origin", "main")

    def no_remoto(self) -> dict[str, str]:
        nomes = self.git(self.origem, "ls-tree", "-r", "--name-only", "main").split()
        return {n: self.git(self.origem, "show", f"main:{n}") for n in nomes}


TRIBUNAIS_DE_HOJE: dict[str, str | None] = {  # o que a coleta do dia grava
    "data/tribunais/meta.json": '{"v":1,"n":2}\n',
    "data/tribunais/ministros/stj-a.json": "a2\n",
    "data/tribunais/ministros/stj-b.json": None,  # saiu da composição: o dossiê é apagado
    "data/tribunais/ministros/stj-c.json": "c2\n",  # chegou
    "data/tribunais/ministros/.stj-c.json.abc123.tmp": "pela metade",  # sobra de gravação interrompida
    "edicoes/tribunais.html": "<p>tribunais v2</p>\n",
    "index.html": "mexido por engano\n",  # fora da pasta: não vai
}
ESPERADO = {**INICIAL, **{k: v for k, v in TRIBUNAIS_DE_HOJE.items() if v is not None and ".tmp" not in k and k != "index.html"}}
del ESPERADO["data/tribunais/ministros/stj-b.json"]
EDICAO = {"index.html": "hoje\n", "edicoes/2026-11-23.html": "edição\n", "edicoes/latest.json": '{"date":"2026-11-23"}\n'}


@precisa_de_git
@pytest.mark.parametrize("caso", ["sozinho", "a edição publicou no meio", "outro mexeu nos mesmos arquivos", "nada mudou", "a aba sumiu da cópia"])
def test_publicar_os_tribunais_rodado_de_verdade(tmp_path: Path, caso: str):
    """O commit leva só ``data/tribunais`` (com a remoção do dossiê de quem saiu, sem o arquivo
    temporário) e a aba; push recusado: reaplica sobre o remoto, e o que a edição enviou fica;
    conflito nos mesmos arquivos: não publica, avisa e termina sem erro; a aba nunca é apagada."""
    r = Repos(tmp_path)
    gravado = {} if caso == "nada mudou" else dict(TRIBUNAIS_DE_HOJE)
    if caso == "a aba sumiu da cópia":
        gravado["edicoes/tribunais.html"] = None
    r.gravar(r.runner, gravado)
    if caso == "a edição publicou no meio":
        r.enviar("🗞️ Edição de 23/11/2026", EDICAO)
    if caso == "outro mexeu nos mesmos arquivos":
        r.enviar("mão humana", {"data/tribunais/meta.json": '{"v":1,"n":99}\n'})
    antes = r.git(r.origem, "rev-parse", "main").strip()
    feito = _rodar(_passo(JOB, "Publicar (commit e push)")["run"], r.runner, tmp_path)
    assert feito.returncode == 0, feito.stdout + feito.stderr
    assert not (r.runner / ".git" / "rebase-merge").exists() and not (r.runner / ".git" / "rebase-apply").exists()
    remoto, historico = r.no_remoto(), r.git(r.origem, "log", "--format=%s", "main").splitlines()
    if caso == "nada mudou":
        assert _saida(tmp_path) == {"changed": "false"} and r.git(r.origem, "rev-parse", "main").strip() == antes
        return
    if caso == "outro mexeu nos mesmos arquivos":
        assert "::warning::" in feito.stdout and _saida(tmp_path) == {"changed": "false"}
        assert r.git(r.origem, "rev-parse", "main").strip() == antes and remoto["data/tribunais/meta.json"] == '{"v":1,"n":99}\n'
        return
    assert _saida(tmp_path) == {"changed": "true"} and historico[0] == COMMIT
    esperado = dict(ESPERADO)
    if caso == "a edição publicou no meio":
        esperado.update(EDICAO)
        assert historico[:2] == [COMMIT, "🗞️ Edição de 23/11/2026"]  # o commit dos Tribunais vem por cima
    if caso == "a aba sumiu da cópia":
        esperado["edicoes/tribunais.html"] = INICIAL["edicoes/tribunais.html"]  # nunca apagada
    assert remoto == esperado
    mudou = r.git(r.origem, "show", "--format=", "--name-status", "main").split()
    assert all(c.startswith(("data/tribunais/", "edicoes/tribunais.html")) for c in mudou[1::2]) and ["D", "data/tribunais/ministros/stj-b.json"] == mudou[mudou.index("D") : mudou.index("D") + 2]
    assert "Publicado: https://github.com/dono/jornal/commit/" in (tmp_path / "resumo").read_text(encoding="utf-8")


def _publicar_da_edicao(sem_exclusao: bool = False) -> str:
    script = _passo(_fluxo("daily.yml")["jobs"]["edition"], "Publicar (commit e push)")["run"]
    if sem_exclusao:  # o "Publicar" de antes da aba Tribunais
        script = re.sub(r'git add "\$@" -- index\.html edicoes data \\\n\s*\':\(exclude\)data/tribunais\' \':\(exclude\)edicoes/tribunais\.html\'',
                        'git add "$@" -- index.html edicoes data', script)
        assert ":(exclude)" not in script
    return script


@precisa_de_git
@pytest.mark.parametrize("sem_exclusao", [False, True])
def test_a_edicao_nao_desfaz_o_que_os_tribunais_publicaram_no_meio(tmp_path: Path, sem_exclusao: bool):
    """A corrida provada pelo devops: a edição começa (cópia rasa), os Tribunais publicam a v2 no
    meio, o push da edição é recusado e o commit é refeito sobre o remoto. Com a exclusão no
    ``stage`` a v2 fica; sem ela (o ``stage`` de antes), a edição a desfazia para a v1."""
    r = Repos(tmp_path)
    r.gravar(r.runner, EDICAO)  # a edição grava os arquivos dela (e a cópia dela dos Tribunais é a v1)
    r.enviar("⚖️ Tribunais", {"data/tribunais/meta.json": '{"v":1,"n":2}\n', "edicoes/tribunais.html": "<p>tribunais v2</p>\n"})
    feito = _rodar(_publicar_da_edicao(sem_exclusao), r.runner, tmp_path)
    assert feito.returncode == 0, feito.stdout + feito.stderr
    assert "Push recusado" in feito.stdout  # o caminho da corrida: o commit foi refeito sobre o remoto
    remoto = r.no_remoto()
    assert {k: remoto[k] for k in EDICAO} == EDICAO  # a edição entrou
    tribunais = (remoto["data/tribunais/meta.json"], remoto["edicoes/tribunais.html"])
    if sem_exclusao:
        assert tribunais == (INICIAL["data/tribunais/meta.json"], INICIAL["edicoes/tribunais.html"])  # desfeito: a corrida existe
    else:
        assert tribunais == ('{"v":1,"n":2}\n', "<p>tribunais v2</p>\n")


# ── a validade das fontes e a issue ──────────────────────────────────────────────────────────


def _fonte(nome: str, data_dados: str | None, velho_dias: int, ok: bool | None = True, aviso: str | None = None) -> dict[str, Any]:
    return {"nome": nome, "data_dados": data_dados, "ok": ok, "aviso": aviso, "velho_dias": velho_dias}


META_BOA = {
    "v": 1,
    "fontes": {
        "stj_acervo": _fonte("STJ · acervo em tramitação", "2026-11-20", 3),
        "stj_integras": _fonte("STJ · decisões publicadas (íntegras)", "2026-10-15", 45, aviso="O último arquivo publicado pelo STJ é de 15/10/2026 (39 dias)."),
        "stf_acervo": _fonte("STF · acervo (lista geral)", "2026-11-22", 3),
        "tpu": _fonte("CNJ · Tabelas Processuais Unificadas", "2026-11-05", 120),
        "datajud": _fonte("CNJ · DataJud (API Pública)", None, 15, ok=None, aviso="Desligado."),  # desligada: não conta
    },
    "cobertura": {"stj": {"relatores_fora_do_mapa": []}, "stf": {}},
}


def _validade(tmp_path: Path, meta: dict[str, Any] | None, hoje_falso: str | None = None) -> tuple[subprocess.CompletedProcess[str], str | None]:
    repo = tmp_path / "repo"
    (repo / "data" / "tribunais").mkdir(parents=True, exist_ok=True)
    if meta is not None:
        (repo / "data" / "tribunais" / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    script = _passo(JOB, "Conferir a validade das fontes")["run"]
    if hoje_falso:
        script = script.replace('"$(date +%F)"', f'"{hoje_falso}"')
    feito = _rodar(script, repo, tmp_path)
    alerta = tmp_path / "runner-temp" / "tribunais-alerta.txt"
    return feito, alerta.read_text(encoding="utf-8") if alerta.exists() else None


@precisa_de_bash
def test_fontes_dentro_da_validade_nao_alertam(tmp_path: Path):
    feito, alerta = _validade(tmp_path, META_BOA)
    assert feito.returncode == 0, feito.stderr and alerta is None
    assert alerta is None and "Todas as fontes ligadas dos Tribunais estão dentro da validade." in (tmp_path / "resumo").read_text(encoding="utf-8")


@precisa_de_bash
def test_fonte_parada_fonte_sem_dados_e_relator_fora_do_mapa_alertam(tmp_path: Path):
    meta = json.loads(json.dumps(META_BOA))
    meta["fontes"]["stj_acervo"] = _fonte("STJ · acervo em tramitação", "2026-11-18", 3, ok=False,
                                          aviso="O portal do STJ recusou o acesso (403) duas vezes em 23/11; o acervo é de 18/11.")  # fmt: skip
    meta["fontes"]["stf_atas"] = _fonte("STF · atas de distribuição", None, 4, ok=False, aviso="Sem dados ainda.")
    meta["cobertura"]["stj"]["relatores_fora_do_mapa"] = [["Fulano de Tal (Desembargador Convocado do TJXX)", 6]]
    meta["fontes"]["tpu"]["data_dados"] = "2026-03-01"  # versão da TPU de oito meses atrás: não é dado parado
    feito, alerta = _validade(tmp_path, meta)
    assert feito.returncode == 0, feito.stderr
    assert alerta is not None
    linhas = alerta.splitlines()
    assert linhas[0] == "<!-- tribunais-alerta: stf_atas:sem_dados;stj_acervo:velho;stj:relatores:Fulano de Tal (Desembargador Convocado do TJXX) -->"
    assert linhas[1] == "Tribunais, conferência de 23/11/2026: 3 problema(s)."
    assert "- STJ · acervo em tramitação: dados de 18/11/2026, há 5 dias (validade: 3 dias). O portal do STJ recusou o acesso (403) duas vezes em 23/11; o acervo é de 18/11." in linhas
    assert "- STF · atas de distribuição: nenhum dado coletado até hoje. Sem dados ainda." in linhas
    assert any(linha.startswith("- STJ: relator fora da composição de config/tribunais.yaml (composicao.stj): Fulano de Tal") and "(6 processos)" in linha for linha in linhas)
    assert "DataJud" not in alerta and "TPU" not in alerta and "Tabelas" not in alerta  # desligada; versão velha
    resumo = (tmp_path / "resumo").read_text(encoding="utf-8")
    assert "### Validade das fontes" in resumo and "<!--" not in resumo and "3 problema(s)" in resumo
    # O mesmo problema no dia seguinte tem a mesma assinatura (a issue não ganha um comentário por execução).
    (tmp_path / "runner-temp" / "tribunais-alerta.txt").unlink()
    _, amanha = _validade(tmp_path, meta, hoje_falso="2026-11-24")
    assert amanha is not None and amanha.splitlines()[0] == linhas[0] and amanha != alerta


@precisa_de_bash
def test_sem_meta_json_alerta(tmp_path: Path):
    feito, alerta = _validade(tmp_path, None)
    assert feito.returncode == 0 and alerta is not None and alerta.startswith("<!-- tribunais-alerta: meta -->")


@precisa_de_bash
@pytest.mark.parametrize(("numero", "ultimo", "esperado"), [
    ("", "", "create"),  # nenhuma issue aberta: abre, com o dono
    ("12", "<!-- tribunais-alerta: stj_acervo:velho -->\nTribunais, conferência de 22/11/2026", None),  # o mesmo problema: nada
    ("12", "<!-- tribunais-alerta: stf_acervo:velho -->\nTribunais...", "comment"),  # outro problema: comenta
])  # fmt: skip
def test_a_issue_para_o_dono(tmp_path: Path, numero: str, ultimo: str, esperado: str | None):
    temp = tmp_path / "runner-temp"
    temp.mkdir(parents=True)
    (temp / "tribunais-alerta.txt").write_text("<!-- tribunais-alerta: stj_acervo:velho -->\nTribunais, conferência de 23/11/2026: 1 problema(s).\n", encoding="utf-8")
    feito = _rodar(_passo(JOB, "Avisar o dono (issue)")["run"], tmp_path, tmp_path, GH_NUMERO=numero, GH_ULTIMO=ultimo)
    assert feito.returncode == 0, feito.stdout + feito.stderr
    chamadas = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert chamadas.startswith("issue list --repo dono/jornal --state open --search \"Tribunais: dados parados ou fonte recusada\" in:title")
    if esperado == "create":
        assert "issue create --repo dono/jornal --title Tribunais: dados parados ou fonte recusada --body <!-- tribunais-alerta: stj_acervo:velho -->" in chamadas
        assert "--assignee dono" in chamadas and "actions/runs/7" in chamadas
    elif esperado == "comment":
        assert "issue comment 12 --repo dono/jornal --body <!-- tribunais-alerta: stj_acervo:velho -->" in chamadas
    else:
        assert "issue comment" not in chamadas and "issue create" not in chamadas and "já registra" in feito.stdout


@precisa_de_bash
def test_sem_alerta_a_issue_aberta_e_fechada(tmp_path: Path):
    (tmp_path / "runner-temp").mkdir(parents=True)
    feito = _rodar(_passo(JOB, "Fechar o aviso resolvido")["run"], tmp_path, tmp_path, GH_NUMERO="12")
    assert feito.returncode == 0, feito.stderr
    assert "issue close 12 --repo dono/jornal --comment Resolvido" in (tmp_path / "gh.log").read_text(encoding="utf-8")
    (tmp_path / "gh.log").unlink()
    (tmp_path / "runner-temp" / "tribunais-alerta.txt").write_text("<!-- tribunais-alerta: meta -->\n", encoding="utf-8")
    assert _rodar(_passo(JOB, "Fechar o aviso resolvido")["run"], tmp_path, tmp_path, GH_NUMERO="12").returncode == 0
    assert not (tmp_path / "gh.log").exists()  # com alerta, nem pergunta


# ── o cache ──────────────────────────────────────────────────────────────────────────────────


@precisa_de_bash
def test_preparar_o_cache(tmp_path: Path):
    """Sai do cache: o diário (já é artefato) e sobra de download. Arquivo grande não sai por idade
    (OP-3: o acervo do STJ em uso fica de sexta a terça; quem poda é o comando, pela referência do
    estado). A chave muda com o que foi baixado, não com o robots.txt nem com as páginas relidas:
    sem nada novo, nenhuma entrada nova."""
    cache = tmp_path / "runner-temp" / "tribunais"
    arquivos = {"robots/a.txt": "User-agent: *\n", "paginas/b.txt": "<html>", "arquivos/ata.json": "{}", "api/c.json": "[]", "diario/x.jsonl": "{}\n",
                "arquivos/.d.json.gz.123.tmp": "pela"}  # fmt: skip
    for caminho, texto in arquivos.items():
        (cache / caminho).parent.mkdir(parents=True, exist_ok=True)
        (cache / caminho).write_text(texto, encoding="utf-8")
    ontem, hoje_ = cache / "arquivos" / "acervo-ontem.json.gz", cache / "arquivos" / "acervo-hoje.json.gz"
    for grande in (ontem, hoje_):
        with grande.open("wb") as f:
            f.truncate(41 * 1024 * 1024)  # esparso: 41 MB sem ocupar o disco
    velho = time.time() - 2 * 86400
    os.utime(ontem, (velho, velho))
    script = _passo(JOB, "Preparar o cache")["run"]

    feito = _rodar(script, tmp_path, tmp_path, RESTAURADO="")
    assert feito.returncode == 0, feito.stdout + feito.stderr
    saida = _saida(tmp_path)
    assert saida["guardar"] == "true" and re.fullmatch(r"tribunais-2026-11-23-[0-9a-f]{16}", saida["chave"])
    assert ontem.exists() and hoje_.exists() and not (cache / "diario").exists() and not (cache / "arquivos" / ".d.json.gz.123.tmp").exists()
    assert (cache / "arquivos" / "ata.json").exists() and (cache / "robots" / "a.txt").exists()
    chave = saida["chave"]

    (cache / "robots" / "a.txt").write_text("User-agent: *\nCrawl-delay: 10\n", encoding="utf-8")  # relido hoje: não conta
    (cache / "paginas" / "b.txt").write_text("<html> nova", encoding="utf-8")
    _rodar(script, tmp_path, tmp_path, RESTAURADO="tribunais-2026-11-22-" + chave.rsplit("-", 1)[1])
    assert _saida(tmp_path) == {"chave": chave, "guardar": "false"}

    (cache / "arquivos" / "ata2.json").write_text("{}", encoding="utf-8")  # uma ata nova
    _rodar(script, tmp_path, tmp_path, RESTAURADO=chave)
    assert _saida(tmp_path)["guardar"] == "true" and _saida(tmp_path)["chave"] != chave

    shutil.rmtree(cache)
    _rodar(script, tmp_path, tmp_path, RESTAURADO="")
    assert _saida(tmp_path) == {"guardar": "false"}


@precisa_de_bash
def test_acervo_do_stj_conta_de_segunda_a_sexta(tmp_path: Path):
    """OP-4: o STJ não publica acervo de fim de semana; na terça de manhã, com o arquivo de sexta, a
    fonte está em dia (2 dias da semana); na quarta, não."""
    meta = json.loads(json.dumps(META_BOA))
    meta["fontes"]["stj_acervo"] = {**_fonte("STJ · acervo em tramitação", "2026-11-20", 2), "dias_da_semana": True}
    feito, alerta = _validade(tmp_path, meta, hoje_falso="2026-11-24")  # terça
    assert feito.returncode == 0, feito.stderr
    assert alerta is None
    _, alerta = _validade(tmp_path, meta, hoje_falso="2026-11-25")  # quarta: o de segunda já devia ter saído
    assert alerta is not None and "stj_acervo:velho" in alerta.splitlines()[0]
    assert "há 5 dias (validade: 2 dias de segunda a sexta)" in alerta


def test_a_issue_aponta_para_a_secao_dos_tribunais_no_readme():
    """OP-9: o link da issue leva à subseção dos Tribunais em "Quando algo dá errado"."""
    script = _passo(JOB, "Avisar o dono (issue)")["run"]
    assert "README.md#tribunais-quando-algo-dá-errado" in script
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "### Tribunais: quando algo dá errado" in readme



@precisa_de_bash
@pytest.mark.parametrize(("gerado", "registrado", "rodar"), [("2026-11-23T11:00:00Z", "2026-11-22T23:00:00Z", "true"),
                                                             ("2026-11-23T11:00:00Z", "2026-11-23T11:00:00Z", "false"),
                                                             (None, None, "false")])
def test_o_decidir_refaz_a_aba_quando_chega_djen_novo(tmp_path: Path, gerado: str | None, registrado: str | None, rodar: str):
    """O djen.json chega da máquina do dono num commit próprio: mais novo que o ``fontes.djen.coletado_em``
    do meta.json, a aba é refeita, mesmo antes da proxima_execucao."""
    repo = tmp_path / "repo"
    (repo / "data" / "tribunais").mkdir(parents=True)
    meta = {"v": 1, "proxima_execucao": "2026-11-23T22:40:00Z", "fontes": {"djen": {"coletado_em": registrado}}}
    (repo / "data" / "tribunais" / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if gerado:
        (repo / "data" / "tribunais" / "djen.json").write_text(json.dumps({"v": 1, "gerado_em": gerado}), encoding="utf-8")
    feito = _rodar(_passo(DECIDIR, "Verificar se há o que fazer")["run"], repo, tmp_path, EVENT="workflow_run")
    assert feito.returncode == 0, feito.stdout + feito.stderr
    assert _saida(tmp_path) == {"rodar": rodar}


def test_o_djen_nunca_roda_no_github():
    """OP-7: a coleta do DJEN (recusa fora do Brasil) é só da máquina do dono: nenhum fluxo a chama."""
    for fluxo in (Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml"):
        assert "tribunais djen" not in fluxo.read_text(encoding="utf-8"), fluxo.name
