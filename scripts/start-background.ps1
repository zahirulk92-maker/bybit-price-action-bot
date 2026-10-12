param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot "run-worker.ps1"
$taskName = "ThesisEdge Demo Bot"

try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if ($health) {
        Write-Output "ThesisEdge is already running and healthy at http://127.0.0.1:$Port"
        exit 0
    }
} catch {
    # Continue to start the registered task or detached runner.
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Start-ScheduledTask -TaskName $taskName
} else {
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
        "-File", "`"$runner`"", "-Port", "$Port"
    ) -WorkingDirectory $root -WindowStyle Hidden | Out-Null
}

$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Milliseconds 750
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($health) {
            Write-Output "ThesisEdge background worker is healthy at http://127.0.0.1:$Port"
            exit 0
        }
    } catch {
        # Keep waiting until the deadline.
    }
} while ((Get-Date) -lt $deadline)

throw "ThesisEdge did not become healthy within 30 seconds. Check .runtime\worker.err.log."
