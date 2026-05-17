<#
.SYNOPSIS
    MediRoute one-time setup script.
    Creates Python venv, installs backend + frontend dependencies.
.USAGE
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#>

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "`n    FAILED: $msg" -ForegroundColor Red; exit 1 }

Write-Host "`nMediRoute — Project Setup" -ForegroundColor White
Write-Host "Root: $root"

# ─── 1. Check Node ──────────────────────────────────────────────────────────
Step "Checking Node.js"
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Fail "node not found in PATH. Install from https://nodejs.org (LTS) then re-run this script."
}
Ok "Node $(& node --version)"

# ─── 2. Check Python ────────────────────────────────────────────────────────
Step "Checking Python"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Fail "python not found in PATH. Install from https://python.org (3.10+) then re-run."
}
Ok "$(& python --version)"

# ─── 3. Create Python venv ──────────────────────────────────────────────────
Step "Python virtual environment"
$backendDir = Join-Path $root "mediroute-backend"
$venvDir    = Join-Path $backendDir "venv"

if (Test-Path (Join-Path $venvDir "Scripts\python.exe")) {
    Ok "venv already exists at mediroute-backend/venv — skipping creation"
} else {
    Write-Host "    Creating venv at mediroute-backend/venv ..."
    & python -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { Fail "python -m venv failed" }
    Ok "venv created"
}

# ─── 4. Install Python deps ─────────────────────────────────────────────────
Step "Installing Python dependencies"
$pipExe  = Join-Path $venvDir "Scripts\pip.exe"
$reqFile = Join-Path $backendDir "requirements.txt"
& $pipExe install -r $reqFile --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }
Ok "Backend dependencies installed"

# ─── 5. Check .env (backend) ────────────────────────────────────────────────
Step "Backend .env"
$backendEnv = Join-Path $backendDir ".env"
if (-not (Test-Path $backendEnv)) {
    Write-Host "    Copying .env.example → .env (fill in real values before starting)" -ForegroundColor Yellow
    Copy-Item (Join-Path $backendDir ".env.example") $backendEnv
    Write-Host "    IMPORTANT: Edit mediroute-backend/.env with your real credentials" -ForegroundColor Yellow
} else {
    Ok ".env already present"
}

# ─── 6. Install frontend deps ───────────────────────────────────────────────
Step "Installing frontend dependencies"
$frontendDir = Join-Path $root "frontend"
Push-Location $frontendDir
& npm install --silent
if ($LASTEXITCODE -ne 0) { Fail "npm install failed in frontend/" }
Pop-Location
Ok "Frontend dependencies installed"

# ─── 7. Check .env (frontend) ───────────────────────────────────────────────
Step "Frontend .env"
$frontendEnv = Join-Path $frontendDir ".env"
if (-not (Test-Path $frontendEnv)) {
    Write-Host "    Copying .env.example → .env" -ForegroundColor Yellow
    Copy-Item (Join-Path $frontendDir ".env.example") $frontendEnv
    Write-Host "    IMPORTANT: Edit frontend/.env — set VITE_API_URL to your backend URL" -ForegroundColor Yellow
} else {
    Ok ".env already present"
}

# ─── Done ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor White
Write-Host "    1. Edit mediroute-backend/.env  (DB URL, secrets, FCM credentials)"
Write-Host "    2. Edit frontend/.env           (VITE_API_URL for APK builds)"
Write-Host "    3. Start backend:  scripts\dev-backend.ps1"
Write-Host "    4. Start frontend: scripts\dev-frontend.ps1"
Write-Host "    5. Build Android:  scripts\build-android.ps1"
Write-Host "────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
