param(
    [int]$Port = 8000
)

$taskName = "ThesisEdge Demo Bot"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1

$healthStatus = "UNAVAILABLE"
$workerStatus = "unknown"
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
    $healthStatus = "HEALTHY"
    try {
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 10
        $workerStatus = if ($status.worker_online) { "online" } else { "offline" }
    } catch {
        $workerStatus = "health-only"
    }
} catch {
    $healthStatus = "UNAVAILABLE"
}

[pscustomobject]@{
    Health = $healthStatus
    Worker = $workerStatus
    Url = "http://127.0.0.1:$Port"
    ListenerPid = if ($listener) { $listener.OwningProcess } else { $null }
    StartupTask = if ($task) { $task.State } else { "Not registered" }
}
