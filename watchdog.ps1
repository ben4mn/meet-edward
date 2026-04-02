# Edward Watchdog
#
# Background health monitor — polls backend and frontend every 60s.
# Auto-restarts a component after 2 consecutive failures (~2 min grace period).
#
# Started by autostart.ps1 as a detached hidden process.
# Runs until the PC reboots or the process is killed manually.
# Task Scheduler restarts it on next login via autostart.ps1.

$ErrorActionPreference = "SilentlyContinue"

$ProjectDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$WatchdogLog = Join-Path $env:TEMP "edward-watchdog.log"
$PollSeconds = 60
$FailLimit   = 2   # consecutive failures before restart

function Log { param($msg) $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"; "$ts  [watchdog] $msg" | Tee-Object -FilePath $WatchdogLog -Append }

function Test-Service {
    param([string]$Url, [int]$TimeoutSec = 5)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return $true
    } catch {
        return $false
    }
}

function Restart-Component {
    param([string]$Component)
    Log "Restarting $Component..."
    $restartScript = Join-Path $ProjectDir "restart.ps1"
    try {
        & powershell -ExecutionPolicy Bypass -File $restartScript $Component *>> $WatchdogLog
        Log "$Component restart completed"
    } catch {
        Log "ERROR restarting $Component: $_"
    }
}

Log "=== Watchdog started (poll every ${PollSeconds}s, restart after $FailLimit consecutive failures) ==="

$backendFails  = 0
$frontendFails = 0

while ($true) {
    Start-Sleep -Seconds $PollSeconds

    # ── Backend check ─────────────────────────────────────────────────
    if (Test-Service -Url "http://localhost:8000/api/debug/health") {
        if ($backendFails -gt 0) { Log "Backend recovered" }
        $backendFails = 0
    } else {
        $backendFails++
        Log "Backend check failed ($backendFails/$FailLimit)"
        if ($backendFails -ge $FailLimit) {
            Restart-Component -Component "backend"
            $backendFails = 0
        }
    }

    # ── Frontend check ────────────────────────────────────────────────
    if (Test-Service -Url "http://localhost:3001") {
        if ($frontendFails -gt 0) { Log "Frontend recovered" }
        $frontendFails = 0
    } else {
        $frontendFails++
        Log "Frontend check failed ($frontendFails/$FailLimit)"
        if ($frontendFails -ge $FailLimit) {
            Restart-Component -Component "frontend"
            $frontendFails = 0
        }
    }
}
