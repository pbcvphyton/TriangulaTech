#!/usr/bin/env bash
# DJEN na máquina do dono (macOS e Linux): coleta o Diário de Justiça Eletrônico Nacional (CNJ) e
# envia só data/tribunais/djen.json, num commit próprio, para o ramo publicado.
#
# Por que aqui: a API do DJEN (comunicaapi.pje.jus.br) recusa com 403 geográfico quem está fora do
# Brasil, e os executores do GitHub Actions estão fora. A máquina do dono, no Brasil, é acesso
# legítimo de quem está no Brasil. Nada aqui usa proxy, VPN ou disfarce: o pedido sai com o
# User-Agent do projeto, respeita o robots.txt e espera entre um pedido e outro.
#
# Quem chama: a tarefa agendada (launchd no macOS, systemd ou cron no Linux; ver instalar.sh). Pode
# rodar à mão. "rodar_djen.sh --sondar" só faz a sonda (poucos pedidos) e não envia nada.
#
# Use um clone SÓ para o robô (o script recusa rodar com alterações locais). O envio usa a
# credencial desse clone: de preferência uma chave de implantação (deploy key) com escrita, só deste
# repositório.
#
# Variáveis (todas opcionais):
#   QIJ_DJEN_REPO      o clone (padrão: a pasta duas acima deste script)
#   QIJ_DJEN_CACHE     o cache da coleta, fora do git (padrão: ~/.cache/triangula-djen)
#   QIJ_DJEN_PYTHON    o Python com as dependências (padrão: <clone>/.venv/bin/python, senão python3)
#   QIJ_DJEN_RAMO      o ramo publicado (padrão: main)
#   QIJ_DJEN_REMOTO    o remoto (padrão: origin)
#   QIJ_DJEN_SEM_PUSH  1 = faz o commit e não envia (ensaio)
#   QIJ_DJEN_COMANDO   troca o comando da coleta (só nos testes)
#
# Saída: 0 quando deu certo ou não havia nada novo; 1 quando algo impediu (o log diz o quê). O log
# de cada dia fica em <cache>/logs/AAAA-MM-DD.log (30 dias).
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${QIJ_DJEN_REPO:-$(cd "$AQUI/../.." && pwd)}"
CACHE="${QIJ_DJEN_CACHE:-$HOME/.cache/triangula-djen}"
RAMO="${QIJ_DJEN_RAMO:-main}"
REMOTO="${QIJ_DJEN_REMOTO:-origin}"
ARQUIVO="data/tribunais/djen.json"
TENTATIVAS=5

mkdir -p "$CACHE/logs"
LOG="$CACHE/logs/$(date +%Y-%m-%d).log"
if [ -t 1 ]; then
  exec > >(tee -a "$LOG") 2>&1
else
  exec >>"$LOG" 2>&1
fi
echo "── $(date '+%Y-%m-%d %H:%M:%S %Z') · DJEN · clone $REPO"

# Uma execução por vez (a tarefa da manhã e a da noite não se atropelam). Trava velha (mais de 3 h,
# de uma execução que morreu) é desfeita.
TRAVA="$CACHE/.trava"
if ! mkdir "$TRAVA" 2>/dev/null; then
  if [ -n "$(find "$TRAVA" -maxdepth 0 -mmin +180 2>/dev/null)" ]; then
    rmdir "$TRAVA" 2>/dev/null || true
    mkdir "$TRAVA"
  else
    echo "Outra execução está em andamento ($TRAVA); nada a fazer."
    exit 0
  fi
fi
trap 'rmdir "$TRAVA" 2>/dev/null || true' EXIT

if [ -n "${QIJ_DJEN_PYTHON:-}" ]; then
  PY="$QIJ_DJEN_PYTHON"
elif [ -x "$REPO/.venv/bin/python" ]; then
  PY="$REPO/.venv/bin/python"
else
  PY="$(command -v python3)"
fi

cd "$REPO"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERRO: $REPO não é um clone do repositório."
  exit 1
fi

coletar() {
  if [ -n "${QIJ_DJEN_COMANDO:-}" ]; then
    bash -c "$QIJ_DJEN_COMANDO" -- "$@"
  else
    "$PY" -m qijournal tribunais djen --cache "$CACHE" "$@"
  fi
}

if [ "${1:-}" = "--sondar" ]; then
  shift
  coletar --sondar "$@"
  exit $?
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "ERRO: o clone tem alterações locais. Use um clone só para o robô (nada foi coletado nem enviado):"
  git status --short --untracked-files=no
  exit 1
fi

# 1. O clone igual ao remoto (um commit do robô que ficou sem enviar é reaplicado por cima).
git fetch --quiet "$REMOTO" "$RAMO"
git checkout --quiet "$RAMO"
if ! git rebase --quiet "$REMOTO/$RAMO"; then
  git rebase --abort 2>/dev/null || true
  echo "ERRO: o clone divergiu do remoto de um jeito que o robô não junta; refaça o clone."
  exit 1
fi

# 2. A coleta (aviso e recusa do DJEN não são erro: o comando sai 0 e o log diz).
if ! coletar --dir . "$@"; then
  echo "ERRO: a coleta do DJEN terminou com erro de programa; nada foi enviado."
  exit 1
fi

# 3. Só o djen.json, num commit próprio. Sem mudança (e sem commit de antes por enviar), nada a fazer.
if [ -n "$(git status --porcelain -- "$ARQUIVO")" ]; then
  git add -- "$ARQUIVO"
  if [ -z "$(git config user.email || true)" ]; then
    git config user.name "DJEN (máquina do dono)"
    git config user.email "djen@maquina-do-dono.invalid"
  fi
  git commit --quiet -m "⚖️ DJEN em $(date +%d/%m/%Y) (máquina no Brasil)" -- "$ARQUIVO"
  echo "Commit: $(git rev-parse --short HEAD) ($(git diff --shortstat HEAD~1 HEAD | sed 's/^ *//'))"
fi
if [ "$(git rev-list --count "$REMOTO/$RAMO..HEAD")" -eq 0 ]; then
  echo "Nenhuma mudança em $ARQUIVO; nada a enviar."
  find "$CACHE/logs" -name '*.log' -mtime +30 -delete 2>/dev/null || true
  exit 0
fi

if [ "${QIJ_DJEN_SEM_PUSH:-}" = "1" ]; then
  echo "QIJ_DJEN_SEM_PUSH=1: o commit ficou só no clone."
  exit 0
fi

# 4. O envio. Recusado (os robôs do GitHub publicaram no meio): o commit é reaplicado sobre o remoto
# e enviado de novo. O djen.json só é escrito por aqui, então o rebase não tem conflito; se tiver
# (alguém mexeu no arquivo), nada é enviado e a próxima execução refaz a partir do remoto.
for tentativa in $(seq 1 "$TENTATIVAS"); do
  if git push --quiet "$REMOTO" "HEAD:$RAMO"; then
    echo "Enviado: $(git rev-parse --short HEAD) para $REMOTO/$RAMO."
    find "$CACHE/logs" -name '*.log' -mtime +30 -delete 2>/dev/null || true
    exit 0
  fi
  if [ "$tentativa" -eq "$TENTATIVAS" ]; then
    break
  fi
  echo "Envio recusado (tentativa $tentativa/$TENTATIVAS); reaplicando sobre o remoto."
  sleep $((tentativa * ${QIJ_DJEN_ESPERA_S:-15}))
  git fetch --quiet "$REMOTO" "$RAMO"
  if ! git rebase --quiet "$REMOTO/$RAMO"; then
    git rebase --abort 2>/dev/null || true
    git reset --quiet --hard "$REMOTO/$RAMO"
    echo "ERRO: $ARQUIVO mudou no remoto durante a coleta; nada foi enviado (a próxima execução refaz)."
    exit 1
  fi
done
echo "ERRO: não foi possível enviar depois de $TENTATIVAS tentativas; o commit fica no clone e vai na próxima execução."
exit 1
