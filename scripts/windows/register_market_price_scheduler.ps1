$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$runner = Join-Path $repoRoot "scripts\windows\run_market_price_refresh.ps1"

if (-not (Test-Path $runner)) { throw "Runner script not found: $runner" }

$taskName = "PWMS-Market-Price-Refresh"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ('-NoProfile -ExecutionPolicy Bypass -File "' + $runner + '"')
$startAt = (Get-Date).AddMinutes(1)
$trigger = New-ScheduledTaskTrigger -Once -At $startAt -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Refresh PWMS stock, ETF, bond and SGB market prices every hour." -User $currentUser -RunLevel Limited -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

Write-Host "PWMS automatic market-price refresh is enabled." -ForegroundColor Green
Write-Host "Task: $taskName"
Write-Host "First run: approximately $startAt"
Write-Host "Frequency: every hour"