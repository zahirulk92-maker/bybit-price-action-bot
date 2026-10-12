param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$taskName = "ThesisEdge Demo Bot"
$runner = Join-Path $PSScriptRoot "run-worker.ps1"
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -Port $Port"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force `
    -Description "Runs the local ThesisEdge Bybit Demo bot from D drive after sign-in." | Out-Null

Write-Output "Registered '$taskName'. It will start hidden at the next Windows sign-in."
Write-Output "Order placement and risk remain controlled by D:\bybit-price-action-bot\.env."
