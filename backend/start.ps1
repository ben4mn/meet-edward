# Start Edward backend locally on Windows
#
# This is the recommended way to run the backend. PowerShell equivalent
# of start.sh for Windows environments.

$ErrorActionPreference = "Stop"

# Move to backend directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# ── Activate virtual environment ─────────────────────────────────────
if (-not (Test-Path ".venv")) {
    Write-Host "Error: .venv not found. Create it first:" -ForegroundColor Red
    Write-Host "  python -m venv .venv"
    Write-Host "  .venv\Scripts\pip install -r requirements.txt"
    exit 1
}

& ".venv\Scripts\Activate.ps1"

# ── Install/upgrade dependencies if requirements.txt is newer ────────
$marker = ".venv\.deps_installed"
$reqFile = "requirements.txt"

$needsInstall = $false
if (-not (Test-Path $marker)) {
    $needsInstall = $true
} elseif ((Get-Item $reqFile).LastWriteTime -gt (Get-Item $marker).LastWriteTime) {
    $needsInstall = $true
}

if ($needsInstall) {
    Write-Host "Installing/upgrading dependencies..."
    pip install -r requirements.txt -q
    New-Item -Path $marker -ItemType File -Force | Out-Null
}

# ── Load environment variables from .env ─────────────────────────────
$envPaths = @(
    (Join-Path $scriptDir "..\.env"),
    (Join-Path $scriptDir ".env")
)

foreach ($envPath in $envPaths) {
    if (Test-Path $envPath) {
        Get-Content $envPath | ForEach-Object {
            $line = $_.Trim()
            # Skip comments and empty lines
            if ($line -and -not $line.StartsWith("#")) {
                $eqIndex = $line.IndexOf("=")
                if ($eqIndex -gt 0) {
                    $key = $line.Substring(0, $eqIndex).Trim()
                    $value = $line.Substring($eqIndex + 1).Trim()
                    # Remove surrounding quotes if present
                    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                        $value = $value.Substring(1, $value.Length - 2)
                    }
                    [Environment]::SetEnvironmentVariable($key, $value, "Process")
                }
            }
        }
        break  # Use the first .env found
    }
}

# ── Set database URL for local postgres ──────────────────────────────
if (-not $env:DATABASE_URL) {
    $env:DATABASE_URL = "postgresql://edward:edward@localhost:5432/edward"
}

Write-Host "Starting Edward backend..." -ForegroundColor Green
Write-Host "  - Database: localhost:5432"
Write-Host "  - Scheduler: enabled (polls every 30s)"
Write-Host "  - API: http://localhost:8000"
Write-Host ""

# ── Start uvicorn ────────────────────────────────────────────────────
# Use run.py on Windows to force SelectorEventLoop (psycopg requires it)
python run.py
