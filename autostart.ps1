# Edward Auto-Start Script
#
# Orchestrates full startup at login:
#   1. Kill stale ngrok processes
#   2. Restart backend + frontend via restart.ps1
#   3. Wait for frontend on port 3001
#   4. Start ngrok tunnel (permanent free domain)
#   5. Start watchdog in background
#   6. Send WhatsApp notification
#
# Triggered by Windows Task Scheduler at login.
# Run manually to test: .\autostart.ps1
#
# Permanent tunnel URL (never changes):
#   https://unoutraged-cotemporarily-annamarie.ngrok-free.dev

# ==== CONFIGURE THIS ====================================================
# Your WhatsApp chat ID (your own number to message yourself).
# Format: "1234567890@s.whatsapp.net" (country code + number, no + or spaces)
# Find it: check http://localhost:3100/chats when bridge is running
$WhatsAppChatId = "6598587940@s.whatsapp.net"
# Full path to ngrok.exe (installed via winget)
$NgrokExe = "C:\Users\cchen362\AppData\Local\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe"
# ========================================================================

$TunnelUrl   = "https://unoutraged-cotemporarily-annamarie.ngrok-free.dev"
$NgrokDomain = "unoutraged-cotemporarily-annamarie.ngrok-free.dev"

$ErrorActionPreference = "SilentlyContinue"

$ProjectDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$AutostartLog = Join-Path $env:TEMP "edward-autostart.log"
$NgrokLog     = Join-Path $env:TEMP "edward-ngrok.log"

function Log { param($msg) $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"; "$ts  $msg" | Tee-Object -FilePath $AutostartLog -Append | Write-Host }

Log "=== Edward AutoStart ==="
Log "Project: $ProjectDir"

# ---- 1. Kill stale ngrok processes -------------------------------------
$stale = Get-Process ngrok -ErrorAction SilentlyContinue
if ($stale) {
    Log "Stopping stale ngrok process(es)..."
    $stale | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# ---- 1b. Wait for PostgreSQL (Docker must be up first) -----------------
Log "Waiting for PostgreSQL on port 5432..."
$dbUp = $false
for ($i = 0; $i -lt 120; $i++) {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect("127.0.0.1", 5432)
        $tcp.Close()
        $dbUp = $true
        break
    } catch {}
    Start-Sleep -Seconds 2
}
if ($dbUp) { Log "PostgreSQL is UP on port 5432" }
else        { Log "WARNING: PostgreSQL did not come up within 4 min - continuing anyway" }

# ---- 2. Restart backend + frontend -------------------------------------
Log "Starting backend and frontend..."
$restartScript = Join-Path $ProjectDir "restart.ps1"
& powershell -ExecutionPolicy Bypass -File $restartScript
Log "restart.ps1 completed"

# ---- 3. Wait for frontend on port 3001 ---------------------------------
Log "Waiting for frontend on port 3001..."
$frontendUp = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:3001" -UseBasicParsing -TimeoutSec 2
        if ($r) { $frontendUp = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}
if ($frontendUp) { Log "Frontend is UP on port 3001" }
else             { Log "WARNING: Frontend did not respond within 30s - continuing anyway" }

# ---- 4. Start ngrok tunnel ---------------------------------------------
Log "Starting ngrok tunnel..."
if (-not (Test-Path $NgrokExe)) {
    Log "WARNING: ngrok not found at $NgrokExe - falling back to PATH"
    $NgrokExe = "ngrok"
}
# Remove stale log files so redirect does not fail on locked files
Remove-Item $NgrokLog -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $env:TEMP "edward-ngrok-err.log") -Force -ErrorAction SilentlyContinue
$ngrokStarted = $false
try {
    Start-Process $NgrokExe `
        -ArgumentList "http", "--url=$NgrokDomain", "3001", "--log=stdout" `
        -RedirectStandardOutput $NgrokLog `
        -RedirectStandardError (Join-Path $env:TEMP "edward-ngrok-err.log") `
        -WindowStyle Hidden `
        -ErrorAction Stop
    $ngrokStarted = $true
    Log "ngrok process launched"
} catch {
    Log "ERROR starting ngrok: $_"
}

if ($ngrokStarted) {
    # Wait up to 15s for ngrok to connect
    $ngrokUp = $false
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Path $NgrokLog) {
            $logContent = Get-Content $NgrokLog -Raw -ErrorAction SilentlyContinue
            if ($logContent -match "started tunnel|tunnel session started|lvl=info msg") {
                $ngrokUp = $true
                break
            }
        }
    }
    if ($ngrokUp) { Log "ngrok tunnel is UP: $TunnelUrl" }
    else          { Log "ngrok tunnel may still be starting - continuing" }
}

# ---- 5. Start watchdog -------------------------------------------------
Log "Starting watchdog..."
$watchdogScript = Join-Path $ProjectDir "watchdog.ps1"
if (Test-Path $watchdogScript) {
    Start-Process powershell `
        -ArgumentList "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$watchdogScript`"" `
        -WindowStyle Hidden
    Log "Watchdog started"
} else {
    Log "WARNING: watchdog.ps1 not found - skipping"
}

# ---- 6. Send WhatsApp notification -------------------------------------
if ($WhatsAppChatId -eq "FILL-IN-YOUR-NUMBER@s.whatsapp.net") {
    Log "Skipping WhatsApp notification - WhatsAppChatId not configured"
} else {
    # Wait for the WhatsApp bridge to connect (bridge starts with backend but
    # takes time after a cold boot: Docker + backend init + WA handshake can
    # take 90-120s). Poll /status up to 3 min; returns {"connected":true} when ready.
    Log "Waiting for WhatsApp bridge to connect (up to 3 min)..."
    $bridgeReady = $false
    for ($i = 0; $i -lt 180; $i++) {
        try {
            $statusResp = Invoke-WebRequest -Uri "http://localhost:3100/status" -UseBasicParsing -TimeoutSec 2
            $statusJson = $statusResp.Content | ConvertFrom-Json
            if ($statusJson.connected -eq $true) {
                $bridgeReady = $true
                break
            }
        } catch {}
        Start-Sleep -Seconds 1
    }

    if (-not $bridgeReady) {
        Log "WARNING: WhatsApp bridge did not connect within 3 min - skipping notification"
    } else {
        Log "WhatsApp bridge connected"
        $notifyMsg = "Edward is up`n$TunnelUrl"

        $notified = $false
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try {
                $body = @{ chat_id = $WhatsAppChatId; message = $notifyMsg } | ConvertTo-Json
                $response = Invoke-WebRequest `
                    -Uri "http://localhost:3100/send" `
                    -Method Post `
                    -ContentType "application/json" `
                    -Body $body `
                    -UseBasicParsing `
                    -TimeoutSec 10
                if ($response.StatusCode -lt 400) {
                    Log "WhatsApp notification sent to $WhatsAppChatId"
                    $notified = $true
                    break
                }
            } catch {
                Log "WhatsApp attempt $attempt failed: $_"
                if ($attempt -lt 3) { Start-Sleep -Seconds 5 }
            }
        }
        if (-not $notified) {
            Log "WARNING: Could not send WhatsApp notification after 3 attempts"
        }
    }
}

Log "=== AutoStart complete ==="
Log "  Backend:  http://localhost:8000"
Log "  Frontend: http://localhost:3001"
Log "  Tunnel:   $TunnelUrl"
