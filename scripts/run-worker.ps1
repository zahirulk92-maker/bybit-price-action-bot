param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$runtime = Join-Path $root ".runtime"
$pidFile = Join-Path $runtime "worker.pid"
$stdoutLog = Join-Path $runtime "worker.out.log"
$stderrLog = Join-Path $runtime "worker.err.log"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}

New-Item -ItemType Directory -Path $runtime -Force | Out-Null

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
        if ($health) {
            Write-Output "ThesisEdge is already healthy on port $Port. No duplicate worker was started."
            exit 0
        }
    } catch {
        throw "Port $Port is already occupied by another process."
    }
}

# These are operational boundaries only. Order placement, leverage and risk
# remain authoritative in .env and are deliberately not overridden here.
$env:BYBIT_DEMO = "true"
$env:RUN_ENGINE_IN_WEB = "true"
$env:DASHBOARD_ALLOW_INSECURE_LOCAL = "true"
$env:PYTHONUTF8 = "1"

$modulePath = & $python -c "import price_action_bot; print(price_action_bot.__file__)"
if ($LASTEXITCODE -ne 0 -or -not $modulePath.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "The virtual environment is not bound to the D-drive application source: $modulePath"
}

$arguments = @(
    "-m", "uvicorn", "price_action_bot.web:app",
    "--host", "127.0.0.1",
    "--port", "$Port"
)
$process = Start-Process -FilePath $python -ArgumentList $arguments `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ascii
try {
    $process.WaitForExit()
    exit $process.ExitCode
} finally {
    if (Test-Path -LiteralPath $pidFile) {
        $savedPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
        if ($savedPid -eq "$($process.Id)") {
            Remove-Item -LiteralPath $pidFile -Force
        }
    }
}
