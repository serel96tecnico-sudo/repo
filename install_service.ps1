# install_service.ps1 — Instala el bot de trading como servicio de Windows vía NSSM.
# Requiere ejecutarse como Administrador. Idempotente: si el servicio existe, lo reconfigura.
#
#   Servicio : TradingAgent  (LocalSystem, arranque automático retardado)
#   Comando  : .venv\Scripts\python.exe main.py --schedule
#
$ErrorActionPreference = 'Stop'
$svcName = 'TradingAgent'
$root    = 'C:\trading-agent'
$py      = "$root\.venv\Scripts\python.exe"
$logDir  = "$root\output\logs"
$nssmDir = 'C:\nssm'
$nssm    = "$nssmDir\nssm.exe"
$nssmSrc = 'C:\Users\bucki\AppData\Local\Microsoft\WinGet\Packages\NSSM.NSSM_Microsoft.Winget.Source_8wekyb3d8bbwe\nssm-2.24-101-g897c7ad\win64\nssm.exe'

Start-Transcript -Path "$logDir\service_install.log" -Append -Force | Out-Null

# 1. Copiar nssm.exe a una ruta estable (no depender de la carpeta versionada de WinGet)
if (-not (Test-Path $nssmDir)) { New-Item -ItemType Directory -Path $nssmDir -Force | Out-Null }
if (-not (Test-Path $nssm)) {
    if (-not (Test-Path $nssmSrc)) { throw "No se encontro nssm.exe en $nssmSrc" }
    Copy-Item $nssmSrc $nssm -Force
}
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
if (-not (Test-Path $py))     { throw "No existe el venv en $py" }

# 2. Si el servicio ya existe, detenerlo y eliminarlo para reconfigurar limpio
$existing = Get-Service -Name $svcName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Servicio existente -> detener y reinstalar"
    if ($existing.Status -eq 'Running') { & $nssm stop $svcName | Out-Null; Start-Sleep -Seconds 3 }
    & $nssm remove $svcName confirm | Out-Null
    Start-Sleep -Seconds 2
}

# 3. Crear y configurar el servicio
& $nssm install $svcName $py
& $nssm set $svcName AppParameters 'main.py --schedule'
& $nssm set $svcName AppDirectory  $root
& $nssm set $svcName DisplayName   'Trading Agent (Saetabia)'
& $nssm set $svcName Description    'Bot de swing trading - scheduler diario (main.py --schedule). Arranca solo y se reinicia si falla.'
& $nssm set $svcName ObjectName     LocalSystem
& $nssm set $svcName Start          SERVICE_DELAYED_AUTO_START

# Logging de salida del bot (con rotacion a 10 MB)
& $nssm set $svcName AppStdout $logDir\service_stdout.log
& $nssm set $svcName AppStderr $logDir\service_stderr.log
& $nssm set $svcName AppStdoutCreationDisposition 4
& $nssm set $svcName AppStderrCreationDisposition 4
& $nssm set $svcName AppRotateFiles 1
& $nssm set $svcName AppRotateOnline 1
& $nssm set $svcName AppRotateBytes 10485760

# Reinicio automatico ante caida del proceso
& $nssm set $svcName AppExit Default Restart
& $nssm set $svcName AppRestartDelay 5000
& $nssm set $svcName AppThrottle 10000

# UTF-8 y salida sin buffer (reportes en espanol/emoji + logs en vivo)
& $nssm set $svcName AppEnvironmentExtra PYTHONUNBUFFERED=1 PYTHONUTF8=1

# 4. Arrancar
& $nssm start $svcName
Start-Sleep -Seconds 4
$final = Get-Service -Name $svcName
Write-Host "Estado final: $($final.Status)"
Stop-Transcript | Out-Null
