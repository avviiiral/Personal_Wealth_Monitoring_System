$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$backend = Join-Path $repoRoot "backend"
$python = Join-Path $backend "venv\Scripts\python.exe"
$logDir = Join-Path $backend "logs"
$logFile = Join-Path $logDir "scheduled_market_price_refresh.log"

if (-not (Test-Path $python)) { throw "Python virtual environment not found: $python" }
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
"[$(Get-Date -Format o)] Starting PWMS market-price refresh" | Add-Content $logFile

Push-Location $backend
try {
    & $python manage.py update_market_prices 2>&1 | Tee-Object -FilePath $logFile -Append
    $exitCode = $LASTEXITCODE
}
finally { Pop-Location }

"[$(Get-Date -Format o)] Finished with exit code $exitCode" | Add-Content $logFile
exit $exitCode