# install_watcher_service.ps1 — Instala el vigilante de precios como servicio de Windows via NSSM.
# Requiere ejecutarse como Administrador. Idempotente: si el servicio existe, lo reconfigura.
#
#   Servicio : TradingAgentWatcher  (LocalSystem, arranque automatico retardado)
#   Comando  : .venv\Scripts\python.exe scripts\price_watcher.py
#
# El watcher se auto-limita al horario de mercado US (9:30-16:00 ET, L-V, no festivo):
# corre 24/7 pero solo consulta precios en sesion, en reposo el resto del tiempo.
$ErrorActionPreference = 'Stop'
$svcName  = 'TradingAgentWatcher'
$root     = 'C:\trading-agent'
$py       = "$root\.venv\Scripts\python.exe"
$logDir   = "$root\output\logs"
$nssm     = 'C:\nssm\nssm.exe'

Start-Transcript -Path "$logDir\watcher_service_install.log" -Append -Force | Out-Null

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
& $nssm set $svcName AppParameters 'scripts\price_watcher.py'
& $nssm set $svcName AppDirectory  $root
& $nssm set $svcName DisplayName   'Trading Agent - Vigilante de precios'
& $nssm set $svcName Description    'Vigila los niveles armados en price_alerts.json y avisa por Telegram (price_watcher.py, bucle 5 min, se auto-limita al horario de mercado).'
& $nssm set $svcName ObjectName     LocalSystem
& $nssm set $svcName Start          SERVICE_DELAYED_AUTO_START

# Logging de salida del watcher (con rotacion a 10 MB)
& $nssm set $svcName AppStdout $logDir\watcher_stdout.log
& $nssm set $svcName AppStderr $logDir\watcher_stderr.log
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

# 3. Arrancar el servicio
& $nssm start $svcName
Start-Sleep -Seconds 4
$final = Get-Service -Name $svcName
Write-Host "Estado final del servicio: $($final.Status)"
Stop-Transcript | Out-Null
