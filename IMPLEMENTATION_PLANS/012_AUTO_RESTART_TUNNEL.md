# Plan 012: Auto-Restart + Fixed Port + Permanent ngrok Tunnel

**Status: COMPLETE**

---

## Problem Statement

After any PC reboot or unexpected shutdown:
1. Backend and frontend die — required manual `restart.ps1`
2. `next dev` auto-increments port (3000→3001→3002) if prior port is still occupied
3. Cloudflare tunnel was run manually — generated a random subdomain each session
4. Chain reaction: new tunnel → new URL → WhatsApp to self → reinstall PWA on phone

## Solution Summary

| Problem | Solution |
|---------|----------|
| Services die on reboot | Windows Task Scheduler runs `autostart.ps1` at login |
| Port drift | Pin Next.js to `-p 3001` |
| Ephemeral tunnel URL | ngrok free static domain — permanent, never changes |
| Docker/PostgreSQL race | `autostart.ps1` waits for port 5432 before starting backend |
| WhatsApp notification | Sent via WhatsApp bridge after it connects (waits up to 3 min) |
| Unexpected crashes | `watchdog.ps1` polls every 60s, restarts after 2 consecutive failures |

---

## What Was Built

### Modified Files
| File | Change |
|------|--------|
| `frontend/package.json` | `"dev": "next dev"` → `"dev": "next dev -p 3001"` |
| `restart.ps1` | All port `3000` refs → `3001`; fixed `$pid` → `$procId` (PS reserved variable) |

### New Files
| File | Purpose |
|------|---------|
| `autostart.ps1` | Task Scheduler entry point: wait for DB → restart services → ngrok → watchdog → WhatsApp notify |
| `watchdog.ps1` | 60s health poll loop — auto-restarts crashed backend or frontend |
| `register-autostart.ps1` | One-time Task Scheduler registration (run as admin) |
| `cloudflare-tunnel.yml` | Kept for reference only — Cloudflare approach was abandoned (requires domain) |

---

## Tunnel: ngrok (not Cloudflare)

Cloudflare named tunnels were attempted but require a domain registered in your Cloudflare account. Since there was no domain, we switched to **ngrok free static domain**.

**Permanent URL:** `https://unoutraged-cotemporarily-annamarie.ngrok-free.dev`
This URL is yours forever — it never changes across reboots or sessions.

**ngrok install path** (installed via winget, NOT on system PATH):
```
C:\Users\cchen362\AppData\Local\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe
```
`autostart.ps1` uses the full path — bare `ngrok` silently fails in Task Scheduler context.

**One-time setup (already done):**
```powershell
ngrok config add-authtoken 3AlHIP9WJulFZv2zoRAqLqLGYnj_7A9tvLr8up7zUzg875GeK
```
ngrok must be v3.20.0+ (free accounts require this). Updated via `ngrok update`.

**ngrok interstitial**: First visit from a new device shows a one-time browser warning page. Click through once per device — never appears again on that device.

---

## autostart.ps1 Flow

```
1. Kill stale ngrok processes
2. Wait for PostgreSQL on port 5432 (up to 4 min — Docker Desktop starts slower than Task Scheduler delay)
3. Call .\restart.ps1 (starts backend :8000 + frontend :3001)
4. Wait for frontend to be up on :3001
5. Start ngrok (full path) with --domain=$NgrokDomain, redirect output to log
6. Wait up to 30s for ngrok to confirm tunnel is up (poll ngrok API at localhost:4040)
7. Start watchdog.ps1 as detached hidden background process
8. Wait for WhatsApp bridge to connect on :3100 (up to 3 min — bridge auto-reconnects from cached credentials)
9. Send WhatsApp: POST http://localhost:3100/send with tunnel URL
10. Log everything to %TEMP%\edward-autostart.log
```

**Key config at top of script:**
```powershell
$WhatsAppChatId = "6598587940@s.whatsapp.net"
$NgrokDomain    = "unoutraged-cotemporarily-annamarie.ngrok-free.dev"
$TunnelUrl      = "https://$NgrokDomain"
$NgrokExe       = "C:\Users\cchen362\AppData\Local\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe"
```

---

## Coverage Matrix

| Scenario | Recovery |
|----------|----------|
| PC reboots / log in | Task Scheduler fires `autostart.ps1` → full restart |
| Docker not ready yet | `autostart.ps1` waits for port 5432 (up to 4 min) before starting backend |
| Backend OOM / crash | Watchdog detects after 2 min → `restart.ps1 backend` |
| Frontend crashes | Watchdog detects after 2 min → `restart.ps1 frontend` |
| VS Code closes/updates | No impact — Edward processes are independent of VS Code |
| Manual dev restart | `.\restart.ps1` as usual — watchdog doesn't interfere |
| Tunnel drops | ngrok auto-reconnects (built-in retry logic) |
| WhatsApp bridge slow | `autostart.ps1` waits up to 3 min for bridge before sending notification |

---

## One-Time Setup (Already Complete)

- [x] Pin Next.js to port 3001 in `package.json`
- [x] Update `restart.ps1` for port 3001
- [x] Install ngrok, update to v3.37.1, add authtoken
- [x] Claim free static domain (`unoutraged-cotemporarily-annamarie.ngrok-free.dev`)
- [x] Set `$WhatsAppChatId` in `autostart.ps1`
- [x] Run `.\register-autostart.ps1` as admin → `EdwardAutostart` task registered
- [x] Set Docker `edward-pg` container restart policy: `docker update --restart=always edward-pg`
- [x] Enable Docker Desktop auto-start on login
- [x] Verified end-to-end after reboot: WhatsApp message received, Edward accessible at ngrok URL

---

## Monitoring

Live log tailing:
```powershell
# Backend output
Get-Content 'C:\Users\cchen362\AppData\Local\Temp\edward-backend.log' -Wait -Tail 50

# Backend errors
Get-Content 'C:\Users\cchen362\AppData\Local\Temp\edward-backend-err.log' -Wait -Tail 50

# Autostart history
Get-Content 'C:\Users\cchen362\AppData\Local\Temp\edward-autostart.log' -Tail 30

# Watchdog history
Get-Content 'C:\Users\cchen362\AppData\Local\Temp\edward-watchdog.log' -Tail 30
```

Log files in `C:\Users\cchen362\AppData\Local\Temp\`:
| File | Contents |
|------|----------|
| `edward-backend.log` | Backend stdout (startup, requests) |
| `edward-backend-err.log` | Backend stderr (errors, tracebacks) |
| `edward-frontend.log` | Next.js stdout |
| `edward-frontend-err.log` | Next.js errors |
| `edward-ngrok.log` | ngrok tunnel events |
| `edward-autostart.log` | Full autostart run history |
| `edward-watchdog.log` | Watchdog health check history |

---

## Task Scheduler Management

```powershell
# Check task is registered
schtasks /Query /TN "EdwardAutostart"

# Remove task (if needed)
schtasks /Delete /TN "EdwardAutostart" /F

# Re-register after changes to register-autostart.ps1
.\register-autostart.ps1   # run as admin
```

---

## Gotchas Encountered

1. **Cloudflare requires a domain** — named tunnels need a zone/domain in your CF account. Free subdomain (`cfargotunnel.com`) is for routing only, not direct browser access. Switched to ngrok.

2. **ngrok not on PATH** — winget installs to a package-specific directory, not `C:\Windows\System32` or similar. Task Scheduler context doesn't inherit user PATH. Must use full exe path in scripts.

3. **ngrok ERR_NGROK_105** — used credential token (`cr_...`) instead of authtoken. Use the longer token from Dashboard → Your Authtoken.

4. **ngrok ERR_NGROK_121** — agent v3.3.1 too old (minimum v3.20.0 for free accounts). Fixed via `ngrok update`.

5. **Docker PostgreSQL race condition** — Task Scheduler fires 10s after login; Docker Desktop + container takes longer. Backend crashed with `Connect call failed ('127.0.0.1', 5432)`. Fixed by waiting for port 5432 in `autostart.ps1`.

6. **Docker container restart policy** — default is `no`. Must run `docker update --restart=always edward-pg` once.

7. **WhatsApp bridge connect time** — bridge takes 30-90s to reconnect cached credentials after backend starts. `autostart.ps1` must wait for `/status` endpoint to return `"connected":true` before sending notification.

8. **PowerShell reserved `$pid`** — `restart.ps1` used `$pid` as a loop variable; that's a PowerShell automatic variable (current process ID). Renamed to `$procId`.

9. **`autostart.ps1` blocking** — early version piped `restart.ps1` output with `*>> $log`, which caused the subprocess to block waiting for the pipe to close. Removed the redirect.

10. **em-dash parse error** — `register-autostart.ps1` used `—` (em-dash) in Write-Host strings; PowerShell misread the encoding as `â€"`, causing syntax errors. Replaced with plain ASCII dashes.
