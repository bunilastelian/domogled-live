# start.ps1 - porneste colectorul si serverul dashboard-ului, apoi deschide browserul
#
#   .\start.ps1              pornire normala
#   .\start.ps1 -Port 9000   alt port
#
# Se deschid doua ferestre: una pentru colector (log live), una pentru server.
# Inchizi ferestrele ca sa opresti.

param(
    [int]$Port = 8777,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$live = $PSScriptRoot
$py   = Join-Path (Split-Path $live -Parent) ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Error "Nu gasesc Python-ul din .venv la $py"
}
if (-not (Test-Path (Join-Path $live "state\state.json"))) {
    Write-Host "Prima rulare: colectorul va importa istoricul de 7 zile (poate dura ~1 minut)..." -ForegroundColor Yellow
}

Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle='Domogled - COLECTOR'; & '$py' '$live\collector.py'"
)

Start-Sleep -Milliseconds 800

Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle='Domogled - SERVER'; & '$py' '$live\server.py' --port $Port"
)

Start-Sleep -Seconds 3

$url = "http://127.0.0.1:$Port/"
Write-Host ""
Write-Host "  Dashboard:  $url" -ForegroundColor Green
Write-Host "  Colector:   fereastra 'Domogled - COLECTOR' (cate un ciclu la 5 minute)" -ForegroundColor DarkGray
Write-Host "  Oprire:     inchide cele doua ferestre, sau ruleaza  .\stop.ps1" -ForegroundColor DarkGray
Write-Host ""

if (-not $NoBrowser) { Start-Process $url }
