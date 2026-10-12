param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root ".runtime\worker.pid"

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output "No managed background PID was found. A manually opened bot was not stopped."
    exit 0
}

$workerPid = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $workerPid" -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Output "The recorded worker is no longer running. Stale PID file removed."
    exit 0
}

$expectedPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not $process.ExecutablePath.Equals($expectedPython, [System.StringComparison]::OrdinalIgnoreCase) -or
    $process.CommandLine -notmatch "uvicorn\s+price_action_bot\.web:app") {
    throw "Refusing to stop PID $workerPid because it is not the managed ThesisEdge worker."
}

$descendants = @()
$pendingParents = @($workerPid)
while ($pendingParents.Count -gt 0) {
    $parentId = $pendingParents[0]
    $pendingParents = @($pendingParents | Select-Object -Skip 1)
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $parentId" -ErrorAction SilentlyContinue)
    foreach ($child in $children) {
        $descendants += $child.ProcessId
        $pendingParents += $child.ProcessId
    }
}

foreach ($childPid in @($descendants | Select-Object -Unique | Sort-Object -Descending)) {
    Stop-Process -Id $childPid -Force -ErrorAction SilentlyContinue
}
Stop-Process -Id $workerPid -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Output "ThesisEdge background worker stopped."
