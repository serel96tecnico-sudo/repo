# install_bot_service.ps1 — Instala el bot de Telegram como servicio de Windows via NSSM.
# Requiere ejecutarse como Administrador. Idempotente: si el servicio existe, lo reconfigura.
#
#   Servicio : TradingAgentBot  (LocalSystem, arranque automatico retardado)
#   Comando  : .venv\Scripts\python.exe main.py --bot
#
# Tras instalar el servicio, elimina la tarea programada 'TradingAgent-TelegramBot'
# que abria la ventana de consola al iniciar sesion.
$ErrorActionPreference = 'Stop'
$svcName  = 'TradingAgentBot'
$oldTask  = 'TradingAgent-TelegramBot'
$root     = 'C:\trading-agent'
$py       = "$root\.venv\Scripts\python.exe"
$logDir   = "$root\output\logs"
$nssm     = 'C:\nssm\nssm.exe'

Start-Transcript -Path "$logDir\bot_service_install.log" -Append -Force | Out-Null

if (-not (Test-Path $nssm)) { throw "No se encontro nssm.exe en $nssm" }
if (-not (Test-Path $py))   { throw "No existe el venv en $py" }
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

# 1. Si el servicio ya existe, detenerlo y eliminarlo para reconfigurar limpio
$existing = Get-Service -Name $svcName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Servicio existente -> detener y reinstalar"
    if ($existing.Status -eq 'Running') { & $nssm stop $svcName | Out-Null; Start-Sleep -Seconds 3 }
    & $nssm remove $svcName confirm | Out-Null
    Start-Sleep -Seconds 2
}

# 2. Crear y configurar el servicio
& $nssm install $svcName $py
& $nssm set $svcName AppParameters 'main.py --bot'
& $nssm set $svcName AppDirectory  $root
& $nssm set $svcName DisplayName   'Trading Agent - Bot Telegram'
& $nssm set $svcName Description    'Bot de Telegram (main.py --bot, long polling). Arranca solo, oculto, y se reinicia si falla.'
& $nssm set $svcName ObjectName     LocalSystem
& $nssm set $svcName Start          SERVICE_DELAYED_AUTO_START

# Logging de salida del bot (con rotacion a 10 MB)
& $nssm set $svcName AppStdout $logDir\bot_stdout.log
& $nssm set $svcName AppStderr $logDir\bot_stderr.log
& $nssm set $svcName AppStdoutCreationDisposition 4
& $nssm set $svcName AppStderrCreationDisposition 4
& $nssm set $svcName AppRotateFiles 1
& $nssm set $svcName AppRotateOnline 1
& $nssm set $svcName AppRotateBytes 10485760

# Reinicio automatico ante caida del proceso
& $nssm set $svcName AppExit Default Restart
& $nssm set $svcName AppRestartDelay 5000
& $nssm set $svcName AppThrottle 10000

# UTF-8 y salida sin buffer
& $nssm set $svcName AppEnvironmentExtra PYTHONUNBUFFERED=1 PYTHONUTF8=1

# 3. Eliminar la tarea programada que abria la ventana de consola
$task = Get-ScheduledTask -TaskName $oldTask -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "Eliminando tarea programada '$oldTask' (la que abria la ventana)"
    if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskName $oldTask | Out-Null }
    Unregister-ScheduledTask -TaskName $oldTask -Confirm:$false
} else {
    Write-Host "Tarea '$oldTask' no encontrada (ya eliminada)"
}

# 4. Arrancar el servicio
& $nssm start $svcName
Start-Sleep -Seconds 4
$final = Get-Service -Name $svcName
Write-Host "Estado final del servicio: $($final.Status)"
Stop-Transcript | Out-Null
