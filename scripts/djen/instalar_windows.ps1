# Instala a tarefa agendada do DJEN no Windows (Agendador de Tarefas), para o usuário atual, sem
# privilégio de administrador. Rode de dentro do clone dedicado ao robô:
#
#   powershell -ExecutionPolicy Bypass -File scripts\djen\instalar_windows.ps1           (instala)
#   powershell -ExecutionPolicy Bypass -File scripts\djen\instalar_windows.ps1 -Remover  (tira)
#
# Roda às 8h17 e às 20h47; com o computador desligado ou dormindo na hora, roda assim que der
# (StartWhenAvailable). Antes, confira à mão: scripts\djen\rodar_djen.ps1 -Sondar
param([switch]$Remover)

$ErrorActionPreference = 'Stop'
$Nome = 'Triangula DJEN'
$Script = Join-Path $PSScriptRoot 'rodar_djen.ps1'

if (Get-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $Nome -Confirm:$false
}
if ($Remover) { Write-Host 'Tarefa do DJEN removida.'; exit 0 }

$Acao = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"{0}`"" -f $Script) `
  -WorkingDirectory (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Gatilhos = @(
  New-ScheduledTaskTrigger -Daily -At '08:17'
  New-ScheduledTaskTrigger -Daily -At '20:47'
)
$Ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Nome -Action $Acao -Trigger $Gatilhos -Settings $Ajustes `
  -Description 'Triangula: coleta o DJEN (STJ e STF) e envia data/tribunais/djen.json' | Out-Null
Write-Host "Instalado: tarefa '$Nome' (8h17 e 20h47)."
Write-Host "Rodar agora: Start-ScheduledTask -TaskName '$Nome'"
Write-Host ("Log: {0}" -f (Join-Path $env:LOCALAPPDATA 'triangula-djen\logs'))
