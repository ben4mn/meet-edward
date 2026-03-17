# First-time Edward setup for Windows
# PowerShell equivalent of setup.sh

$ErrorActionPreference = "Stop"

Write-Host "=== Edward First-Time Setup ===" -ForegroundColor Green

# ── Check prerequisites ──────────────────────────────────────────────
$missing = @()

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    $missing += "Python 3.11+ (https://www.python.org/downloads/)"
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    $missing += "Node.js 18+ (https://nodejs.org/)"
}

if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
    $missing += "PostgreSQL 16+ (https://www.postgresql.org/download/windows/)"
}

if ($missing.Count -gt 0) {
    Write-Host "Error: Missing prerequisites:" -ForegroundColor Red
    foreach ($m in $missing) {
        Write-Host "  - $m" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "Install the above and ensure they are on your PATH, then re-run this script." -ForegroundColor Yellow
    exit 1
}

# Verify Python version
$pyVersion = python --version 2>&1
Write-Host "Found: $pyVersion"

# Verify Node version
$nodeVersion = node --version 2>&1
Write-Host "Found: Node.js $nodeVersion"

# Verify PostgreSQL
$pgVersion = psql --version 2>&1
Write-Host "Found: $pgVersion"

# ── Set up PostgreSQL database ───────────────────────────────────────
Write-Host ""
Write-Host "Setting up database..." -ForegroundColor Cyan

# Create user and database (ignore errors if they already exist)
try { psql -U postgres -c "CREATE USER edward WITH PASSWORD 'edward';" 2>$null } catch {}
try { psql -U postgres -c "CREATE DATABASE edward OWNER edward;" 2>$null } catch {}
try { psql -U postgres -d edward -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>$null } catch {
    Write-Host "Warning: Could not enable pgvector extension." -ForegroundColor Yellow
    Write-Host "  You may need to install pgvector separately:" -ForegroundColor Yellow
    Write-Host "  https://github.com/pgvector/pgvector#windows" -ForegroundColor Yellow
}

Write-Host "Database ready (edward/edward on localhost:5432)" -ForegroundColor Green

# ── Backend Python setup ─────────────────────────────────────────────
Write-Host ""
Write-Host "Setting up backend..." -ForegroundColor Cyan

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $scriptDir "backend"

python -m venv (Join-Path $backendDir ".venv")
& (Join-Path $backendDir ".venv\Scripts\pip.exe") install -r (Join-Path $backendDir "requirements.txt")

Write-Host "Backend dependencies installed" -ForegroundColor Green

# ── Frontend setup ───────────────────────────────────────────────────
Write-Host ""
Write-Host "Setting up frontend..." -ForegroundColor Cyan

$frontendDir = Join-Path $scriptDir "frontend"
Push-Location $frontendDir
npm install
Pop-Location

Write-Host "Frontend dependencies installed" -ForegroundColor Green

# ── Create .env from template if needed ──────────────────────────────
$envFile = Join-Path $scriptDir ".env"
$envExample = Join-Path $scriptDir ".env.example"

if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Host "Created .env from template. Edit it to add your ANTHROPIC_API_KEY." -ForegroundColor Yellow
    } else {
        Write-Host "Warning: .env.example not found. Create .env manually." -ForegroundColor Yellow
    }
} else {
    Write-Host ".env already exists, skipping." -ForegroundColor Green
}

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Green
Write-Host "1. Add your ANTHROPIC_API_KEY to .env"
Write-Host "2. Run: .\restart.ps1"
