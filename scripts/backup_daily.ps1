$repo = "c:\trading-agent"
Set-Location $repo

# Backup portfolio.json (gitignored) a carpeta separada
$backupDir = "$repo\backups"
if (-not (Test-Path $backupDir)) { New-Item -ItemType Directory -Force $backupDir | Out-Null }
$date = Get-Date -Format "yyyy-MM-dd"
Copy-Item "$repo\contex\portfolio.json" "$backupDir\portfolio_$date.json" -Force

# Git commit de archivos trackeados con cambios
$status = git status --porcelain
if ($status) {
    git add agents/entry_scanner.py contex/watchlist.json agents/market_scanner.py config.py
    $msg = "backup auto $date"
    git commit -m $msg 2>&1
    Write-Output "$(Get-Date -Format 'HH:mm:ss') — Git commit OK: $msg"
} else {
    Write-Output "$(Get-Date -Format 'HH:mm:ss') — Sin cambios en git, solo portfolio copiado"
}

Write-Output "Backup completado — portfolio guardado en $backupDir\portfolio_$date.json"
