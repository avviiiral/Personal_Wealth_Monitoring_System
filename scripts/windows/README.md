# PWMS Automatic Market Price Scheduler

Windows Task Scheduler setup for automatic market-price refreshes.

## What it does

The scheduled task runs `python manage.py update_market_prices` once every hour. This refreshes STOCK, ETF, BOND and SGB prices without manual commands.

It deliberately does **not** run the full `run_scheduled_refresh` pipeline every hour, because that pipeline also performs news, filings, SIP and other external-data work.

## Setup

From the repository root in PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\register_market_price_scheduler.ps1
```

The setup script locates the repository and `backend\venv\Scripts\python.exe`, creates/replaces the Windows Scheduled Task `PWMS-Market-Price-Refresh`, runs it hourly, starts it shortly after registration, and enables missed-run recovery.

## Verify

```powershell
Get-ScheduledTask -TaskName "PWMS-Market-Price-Refresh"
Get-ScheduledTaskInfo -TaskName "PWMS-Market-Price-Refresh"
```

Task output is written to `backend\logs\scheduled_market_price_refresh.log`.

## Remove

```powershell
Unregister-ScheduledTask -TaskName "PWMS-Market-Price-Refresh" -Confirm:$false
```