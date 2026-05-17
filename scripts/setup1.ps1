<#
.SYNOPSIS
    MediRoute - One-Time Setup Script.
    Run this ONCE on a new machine.
    After filling .env files, run: scripts\build-android.ps1
#>

$root     = Split-Path $PSScriptRoot -Parent
$backend  = Join-Path $root "mediroute-backend"
$frontend = Join-Path $root "frontend"

function Step($msg) { Write-Host "`n>>> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  [OK]  $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!!]  $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "`n  [ERR] $msg`n" -ForegroundColor Red; exit 1 }

Write-Host "`n================================================" -ForegroundColor White
Write-Host "  MediRoute - One-Time Setup" -ForegroundColor White
Write-Host "================================================" -ForegroundColor White

# --- Check Node ---
Step "Checking Node.js"
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Fail "node not found. Install from https://nodejs.org (LTS), add to PATH, then re-run."
}
Ok "Node $(& node --version)"

# --- Check Python ---
Step "Checking Python"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Fail "python not found. Install from https://python.org (3.10+), add to PATH, then re-run."
}
Ok "$(& python --version)"

# --- Check Java ---
Step "Checking Java (required for Android)"
$javaOk = (Get-Command java -ErrorAction SilentlyContinue) -ne $null
if (-not $javaOk -and $env:JAVA_HOME) {
    $javaOk = Test-Path (Join-Path $env:JAVA_HOME "bin\java.exe")
}
if (-not $javaOk) {
    Warn "Java not found. Install Android Studio (includes JDK): https://developer.android.com/studio"
    Warn "Set JAVA_HOME after install, then re-run build-android.ps1"
} else {
    Ok "Java detected"
}

# --- Check Android SDK ---
Step "Checking Android SDK"
$androidHome = $env:ANDROID_HOME
if (-not $androidHome) { $androidHome = $env:ANDROID_SDK_ROOT }
if (-not $androidHome) {
    $sdkCandidates = @(
        "$env:LOCALAPPDATA\Android\Sdk",
        "$env:USERPROFILE\AppData\Local\Android\Sdk"
    )
    foreach ($c in $sdkCandidates) {
        if (Test-Path $c) { $androidHome = $c; break }
    }
}
if ($androidHome -and (Test-Path $androidHome)) {
    Ok "Android SDK: $androidHome"
} else {
    Warn "Android SDK not found. Install Android Studio, then set ANDROID_HOME."
    Warn "APK builds will fail until this is resolved."
}

# --- Python venv ---
Step "Creating Python virtual environment"
$venvDir = Join-Path $backend "venv"
if (Test-Path (Join-Path $venvDir "Scripts\python.exe")) {
    Ok "venv already exists - skipping"
} else {
    Write-Host "  Creating venv..."
    & python -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { Fail "python -m venv failed" }
    Ok "venv created at mediroute-backend/venv"
}

# --- Install Python deps ---
Step "Installing backend dependencies"
$pipExe = Join-Path $venvDir "Scripts\pip.exe"
& $pipExe install -r (Join-Path $backend "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }
Ok "Backend dependencies installed"

# --- Install frontend deps ---
Step "Installing frontend dependencies"
Push-Location $frontend
& npm install --silent
if ($LASTEXITCODE -ne 0) { Fail "npm install failed" }
Pop-Location
Ok "Frontend dependencies installed"

# --- Scaffold .env files ---
Step "Scaffolding .env files"
$backendEnv = Join-Path $backend ".env"
if (-not (Test-Path $backendEnv)) {
    Copy-Item (Join-Path $backend ".env.example") $backendEnv
    Ok "Created mediroute-backend/.env"
} else {
    Ok "mediroute-backend/.env already exists"
}

$frontendEnv = Join-Path $frontend ".env"
if (-not (Test-Path $frontendEnv)) {
    Copy-Item (Join-Path $frontend ".env.example") $frontendEnv
    Ok "Created frontend/.env"
} else {
    Ok "frontend/.env already exists"
}

# --- Open .env files for manual editing ---
Write-Host ""
Write-Host "================================================" -ForegroundColor Yellow
Write-Host "  ACTION REQUIRED - Fill in your credentials" -ForegroundColor Yellow
Write-Host "================================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Opening both .env files in Notepad now." -ForegroundColor White
Write-Host ""
Write-Host "  mediroute-backend/.env - fill in:"
Write-Host "    DATABASE_URL            (Supabase connection string, port 6543)"
Write-Host "    SECRET_KEY              (run: python -c `"import secrets; print(secrets.token_hex(32))`")"
Write-Host "    ADMIN_PHONE             (your 10-digit admin phone)"
Write-Host "    ADMIN_SECRET            (choose any strong password)"
Write-Host "    FIREBASE_CREDENTIALS_JSON  (full JSON from Firebase service account key)"
Write-Host ""
Write-Host "  frontend/.env - fill in:"
Write-Host "    VITE_API_URL            (https://your-backend.onrender.com)"
Write-Host "    VITE_GOOGLE_CLIENT_ID   (from Google Cloud Console)"
Write-Host ""

Start-Process notepad $backendEnv
Start-Sleep -Milliseconds 500
Start-Process notepad $frontendEnv

Write-Host ""
Write-Host "  Both files are open in Notepad." -ForegroundColor Cyan
Write-Host "  Fill in values, save both, then press Enter to continue." -ForegroundColor Cyan
Write-Host ""
Read-Host "  Press Enter when done"

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Connect your Android device via USB (USB Debugging ON)"
Write-Host "  Then run:"
Write-Host ""
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "================================================" -ForegroundColor Green