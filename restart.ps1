# Restart Edward: gracefully stop old processes, restart frontend and backend.
#
# Usage:
#   .\restart.ps1              # Restart both frontend and backend
#   .\restart.ps1 frontend     # Restart only the frontend
#   .\restart.ps1 backend      # Restart only the backend

param(
    [string]$Component = "all"
)

$ErrorActionPreference = "SilentlyContinue"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendLog = Join-Path $env:TEMP "edward-backend.log"
$FrontendLog = Join-Path $env:TEMP "edward-frontend.log"

function Write-Info  { param($msg) Write-Host "[restart] $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "[restart] $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "[restart] $msg" -ForegroundColor Red }

# ── Stop processes on a given port ───────────────────────────────────
function Stop-PortProcess {
    param([int]$Port, [string]$Name)

    Write-Info "Stopping $Name..."

    $pids = (Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
             Select-Object -ExpandProperty OwningProcess -Unique)

    if (-not $pids) {
        Write-Info "$Name was not running"
        return
    }

    foreach ($procId in $pids) {
        try { Stop-Process -Id $procId -ErrorAction SilentlyContinue } catch {}
    }

    # Wait up to 5 seconds for graceful stop
    $waited = 0
    while ($waited -lt 10) {
        $still = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
        if (-not $still) { break }
        Start-Sleep -Milliseconds 500
        $waited++
    }

    # Force kill if still running
    $remaining = (Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
                  Select-Object -ExpandProperty OwningProcess -Unique)
    if ($remaining) {
        Write-Warn "$Name didn't stop gracefully, force killing..."
        foreach ($procId in $remaining) {
            try { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue } catch {}
        }
    }

    # Wait for the port to actually be released (OS needs time after kill)
    $portCleared = $false
    for ($w = 0; $w -lt 20; $w++) {
        Start-Sleep -Milliseconds 500
        $check = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
        if (-not $check) { $portCleared = $true; break }
    }
    if (-not $portCleared) {
        Write-Warn "$Name port $Port still occupied after 10s - continuing anyway"
    }

    Write-Info "$Name stopped"
}

# ── Start backend ────────────────────────────────────────────────────
function Start-Backend {
    Write-Info "Starting backend..."

    $startScript = Join-Path $ProjectDir "backend\start.ps1"
    Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -File `"$startScript`"" `
        -RedirectStandardOutput $BackendLog `
        -RedirectStandardError (Join-Path $env:TEMP "edward-backend-err.log") `
        -WindowStyle Hidden

    # Wait up to 15 seconds for backend to come up
    $waited = 0
    while ($waited -lt 30) {
        $conn = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
        if ($conn) {
            Write-Info "Backend is UP on port 8000"
            return
        }
        Start-Sleep -Milliseconds 500
        $waited++
    }

    Write-Err "Backend failed to start within 15s. Check log: $BackendLog"
    if (Test-Path $BackendLog) {
        Get-Content $BackendLog -Tail 20
    }
}

# ── Start frontend ───────────────────────────────────────────────────
function Start-Frontend {
    Write-Info "Starting frontend..."

    $frontendDir = Join-Path $ProjectDir "frontend"
    Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -Command Set-Location '$frontendDir'; npm run dev" `
        -RedirectStandardOutput $FrontendLog `
        -RedirectStandardError (Join-Path $env:TEMP "edward-frontend-err.log") `
        -WindowStyle Hidden

    # Wait up to 20 seconds for frontend to come up
    $waited = 0
    while ($waited -lt 20) {
        try {
            $response = Invoke-WebRequest -Uri "http://localhost:3001" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($response) {
                Write-Info "Frontend is UP on port 3001"
                return
            }
        } catch {}
        Start-Sleep -Seconds 1
        $waited++
    }

    Write-Err "Frontend didn't respond within 20s (port 3001)"
}

# ── Main ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Info "=== Edward Restart ==="
Write-Host ""

switch ($Component) {
    "all" {
        Stop-PortProcess -Port 8000 -Name "backend"
        Stop-PortProcess -Port 3001 -Name "frontend"
        Start-Frontend
        Start-Backend
    }
    "frontend" {
        Stop-PortProcess -Port 3001 -Name "frontend"
        Start-Frontend
    }
    "backend" {
        Stop-PortProcess -Port 8000 -Name "backend"
        Start-Backend
    }
    default {
        Write-Err "Unknown component: $Component"
        Write-Host "Usage: .\restart.ps1 [all|frontend|backend]"
        exit 1
    }
}

Write-Host ""
Write-Info "=== Done ==="
Write-Info "  Backend:  http://localhost:8000  (log: $BackendLog)"
Write-Info "  Frontend: http://localhost:3001  (log: $FrontendLog)"
Write-Host ""
