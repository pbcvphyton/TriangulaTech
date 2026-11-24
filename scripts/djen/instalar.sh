#!/usr/bin/env bash
# Instala a tarefa agendada do DJEN na máquina do dono (macOS: launchd; Linux: systemd do usuário,
# ou cron se não houver systemd). Rode de dentro do clone dedicado ao robô:
#
#   scripts/djen/instalar.sh            instala (ou reinstala) e mostra como conferir
#   scripts/djen/instalar.sh --remover  tira a tarefa (o clone e o cache ficam)
#
# O cache fica em ~/.cache/triangula-djen (troque com QIJ_DJEN_CACHE). Antes de instalar, confira à
# mão: scripts/djen/rodar_djen.sh --sondar
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$AQUI/../.." && pwd)"
CACHE="${QIJ_DJEN_CACHE:-$HOME/.cache/triangula-djen}"
mkdir -p "$CACHE/logs"

preencher() {  # modelo destino
  sed -e "s#@REPO@#$REPO#g" -e "s#@CACHE@#$CACHE#g" "$1" > "$2"
}

case "$(uname -s)" in
  Darwin)
    DESTINO="$HOME/Library/LaunchAgents/br.triangula.djen.plist"
    launchctl bootout "gui/$(id -u)/br.triangula.djen" 2>/dev/null || true
    if [ "${1:-}" = "--remover" ]; then
      rm -f "$DESTINO"
      echo "Tarefa do DJEN removida do launchd."
      exit 0
    fi
    mkdir -p "$(dirname "$DESTINO")"
    preencher "$AQUI/br.triangula.djen.plist" "$DESTINO"
    plutil -lint "$DESTINO" >/dev/null
    launchctl bootstrap "gui/$(id -u)" "$DESTINO"
    echo "Instalado: $DESTINO (8h17 e 20h47; com o Mac dormindo, roda ao acordar)."
    echo "Rodar agora:   launchctl kickstart gui/$(id -u)/br.triangula.djen"
    echo "Ver o log:     tail -n 40 \"$CACHE/logs/\$(date +%Y-%m-%d).log\""
    ;;
  Linux)
    if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
      PASTA="$HOME/.config/systemd/user"
      if [ "${1:-}" = "--remover" ]; then
        systemctl --user disable --now triangula-djen.timer 2>/dev/null || true
        rm -f "$PASTA/triangula-djen.service" "$PASTA/triangula-djen.timer"
        systemctl --user daemon-reload
        echo "Tarefa do DJEN removida do systemd."
        exit 0
      fi
      mkdir -p "$PASTA"
      preencher "$AQUI/triangula-djen.service" "$PASTA/triangula-djen.service"
      cp "$AQUI/triangula-djen.timer" "$PASTA/triangula-djen.timer"
      systemctl --user daemon-reload
      systemctl --user enable --now triangula-djen.timer
      echo "Instalado: triangula-djen.timer (8h17 e 20h47; Persistent=true roda o que perdeu ao ligar)."
      echo "Para rodar mesmo sem sessão aberta: loginctl enable-linger \"$USER\""
      echo "Rodar agora:   systemctl --user start triangula-djen.service"
      echo "Próximas:      systemctl --user list-timers triangula-djen.timer"
    else
      LINHA="17 8,20 * * * QIJ_DJEN_REPO=\"$REPO\" QIJ_DJEN_CACHE=\"$CACHE\" /bin/bash \"$REPO/scripts/djen/rodar_djen.sh\""
      ATUAL="$(crontab -l 2>/dev/null | grep -v 'scripts/djen/rodar_djen.sh' || true)"
      if [ "${1:-}" = "--remover" ]; then
        printf '%s\n' "$ATUAL" | crontab -
        echo "Tarefa do DJEN removida do cron."
        exit 0
      fi
      printf '%s\n%s\n' "$ATUAL" "$LINHA" | sed '/^$/d' | crontab -
      echo "Instalado no cron (8h17 e 20h17). O cron não roda o que perdeu com a máquina desligada."
    fi
    echo "Ver o log:     tail -n 40 \"$CACHE/logs/\$(date +%Y-%m-%d).log\""
    ;;
  *)
    echo "Sistema não reconhecido ($(uname -s)). No Windows, use scripts\\djen\\instalar_windows.ps1."
    exit 1
    ;;
esac
