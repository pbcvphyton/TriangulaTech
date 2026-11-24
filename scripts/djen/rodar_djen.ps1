# DJEN na máquina do dono (Windows): o mesmo que rodar_djen.sh. Coleta o Diário de Justiça
# Eletrônico Nacional (CNJ) e envia só data/tribunais/djen.json, num commit próprio.
#
# Por que aqui: a API do DJEN recusa com 403 geográfico quem está fora do Brasil; a máquina do dono,
# no Brasil, é acesso legítimo. Nada de proxy, VPN ou disfarce: o User-Agent é o do projeto, o
# robots.txt é respeitado e há espera entre os pedidos.
#
# Quem chama: a tarefa agendada que instalar_windows.ps1 registra. À mão:
#   powershell -ExecutionPolicy Bypass -File scripts\djen\rodar_djen.ps1            (coleta e envia)
#   powershell -ExecutionPolicy Bypass -File scripts\djen\rodar_djen.ps1 -Sondar    (só a sonda)
#
# Use um clone só para o robô (o script recusa rodar com alterações locais). Variáveis opcionais:
# QIJ_DJEN_REPO, QIJ_DJEN_CACHE (padrão: %LOCALAPPDATA%\triangula-djen), QIJ_DJEN_PYTHON,
# QIJ_DJEN_RAMO (main), QIJ_DJEN_REMOTO (origin), QIJ_DJEN_SEM_PUSH=1 (ensaio).
param([switch]$Sondar)

# 'Continue': no Windows PowerShell 5.1, o stderr do git com 2>&1 viraria erro fatal; os códigos de
# saída são conferidos um a um.
$ErrorActionPreference = 'Continue'
$Repo = if ($env:QIJ_DJEN_REPO) { $env:QIJ_DJEN_REPO } else { (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }
$Cache = if ($env:QIJ_DJEN_CACHE) { $env:QIJ_DJEN_CACHE } else { Join-Path $env:LOCALAPPDATA 'triangula-djen' }
$Ramo = if ($env:QIJ_DJEN_RAMO) { $env:QIJ_DJEN_RAMO } else { 'main' }
$Remoto = if ($env:QIJ_DJEN_REMOTO) { $env:QIJ_DJEN_REMOTO } else { 'origin' }
$Arquivo = 'data/tribunais/djen.json'
$Tentativas = 5

New-Item -ItemType Directory -Force -Path (Join-Path $Cache 'logs') | Out-Null
$Log = Join-Path $Cache ("logs\{0}.log" -f (Get-Date -Format 'yyyy-MM-dd'))
function Escrever([string]$texto) { $texto | Tee-Object -FilePath $Log -Append | Out-Host }
function Git { & git @args 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $Log -Append | Out-Host; return $LASTEXITCODE }

Escrever ("── {0} · DJEN · clone {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Repo)

$Trava = Join-Path $Cache '.trava'
if (Test-Path $Trava) {
  if ((Get-Item $Trava).LastWriteTime -gt (Get-Date).AddHours(-3)) { Escrever 'Outra execução está em andamento; nada a fazer.'; exit 0 }
  Remove-Item $Trava -Force -Recurse
}
New-Item -ItemType Directory -Path $Trava | Out-Null
try {
  $Py = if ($env:QIJ_DJEN_PYTHON) { $env:QIJ_DJEN_PYTHON }
        elseif (Test-Path (Join-Path $Repo '.venv\Scripts\python.exe')) { Join-Path $Repo '.venv\Scripts\python.exe' }
        else { 'python' }
  Set-Location $Repo

  if ($Sondar) {
    & $Py -m qijournal tribunais djen --sondar --cache $Cache 2>&1 | Tee-Object -FilePath $Log -Append | Out-Host
    exit $LASTEXITCODE
  }

  if (git status --porcelain --untracked-files=no) {
    Escrever 'ERRO: o clone tem alterações locais. Use um clone só para o robô (nada foi coletado nem enviado).'
    exit 1
  }

  # 1. O clone igual ao remoto (um commit do robô que ficou sem enviar é reaplicado por cima).
  if ((Git fetch --quiet $Remoto $Ramo) -ne 0) { Escrever 'ERRO: git fetch falhou.'; exit 1 }
  Git checkout --quiet $Ramo | Out-Null
  if ((Git rebase --quiet "$Remoto/$Ramo") -ne 0) {
    Git rebase --abort | Out-Null
    Escrever 'ERRO: o clone divergiu do remoto de um jeito que o robô não junta; refaça o clone.'
    exit 1
  }

  # 2. A coleta (aviso e recusa do DJEN não são erro: o comando sai 0 e o log diz).
  & $Py -m qijournal tribunais djen --dir . --cache $Cache 2>&1 | Tee-Object -FilePath $Log -Append | Out-Host
  if ($LASTEXITCODE -ne 0) { Escrever 'ERRO: a coleta do DJEN terminou com erro de programa; nada foi enviado.'; exit 1 }

  # 3. Só o djen.json, num commit próprio.
  if (git status --porcelain -- $Arquivo) {
    Git add -- $Arquivo | Out-Null
    if (-not (git config user.email)) {
      git config user.name 'DJEN (máquina do dono)'
      git config user.email 'djen@maquina-do-dono.invalid'
    }
    Git commit --quiet -m ("⚖️ DJEN em {0} (máquina no Brasil)" -f (Get-Date -Format 'dd/MM/yyyy')) -- $Arquivo | Out-Null
  }
  if ([int](git rev-list --count "$Remoto/$Ramo..HEAD") -eq 0) { Escrever "Nenhuma mudança em $Arquivo; nada a enviar."; exit 0 }
  if ($env:QIJ_DJEN_SEM_PUSH -eq '1') { Escrever 'QIJ_DJEN_SEM_PUSH=1: o commit ficou só no clone.'; exit 0 }

  # 4. O envio, com o commit reaplicado sobre o remoto quando os robôs do GitHub publicaram no meio.
  for ($t = 1; $t -le $Tentativas; $t++) {
    if ((Git push --quiet $Remoto "HEAD:$Ramo") -eq 0) { Escrever ("Enviado: {0}." -f (git rev-parse --short HEAD)); exit 0 }
    if ($t -eq $Tentativas) { break }
    Escrever "Envio recusado (tentativa $t/$Tentativas); reaplicando sobre o remoto."
    Start-Sleep -Seconds (15 * $t)
    Git fetch --quiet $Remoto $Ramo | Out-Null
    if ((Git rebase --quiet "$Remoto/$Ramo") -ne 0) {
      Git rebase --abort | Out-Null
      Git reset --quiet --hard "$Remoto/$Ramo" | Out-Null
      Escrever "ERRO: $Arquivo mudou no remoto durante a coleta; nada foi enviado (a próxima execução refaz)."
      exit 1
    }
  }
  Escrever "ERRO: não foi possível enviar depois de $Tentativas tentativas; o commit vai na próxima execução."
  exit 1
}
finally {
  Remove-Item $Trava -Force -Recurse -ErrorAction SilentlyContinue
  Get-ChildItem (Join-Path $Cache 'logs') -Filter '*.log' | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Force
}
