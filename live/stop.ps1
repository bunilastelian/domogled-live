# stop.ps1 - opreste colectorul si serverul dashboard-ului
#
# Opreste doar procesele Python care ruleaza collector.py sau server.py din acest folder.

$live = $PSScriptRoot
# potrivim pe numele scriptului + pe "copernicus", ca sa prindem procesul
# indiferent daca a fost pornit cu cale absoluta (start.ps1) sau relativa (cd + python server.py)
$meci = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -and
                       $_.CommandLine -match 'collector\.py|server\.py' -and
                       $_.CommandLine -match 'copernicus' }

if (-not $meci) {
    Write-Host "Nimic de oprit (nu ruleaza collector.py sau server.py din $live)." -ForegroundColor Yellow
    exit 0
}

foreach ($p in $meci) {
    $ce = if ($p.CommandLine -like "*collector.py*") { "colector" } else { "server" }
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Host "oprit: $ce (PID $($p.ProcessId))" -ForegroundColor Green
    } catch {
        Write-Host "nu am putut opri PID $($p.ProcessId): $($_.Exception.Message)" -ForegroundColor Red
    }
}
