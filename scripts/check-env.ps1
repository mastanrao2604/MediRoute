<#
.SYNOPSIS
    MediRoute developer environment validator.
    Run before setup to confirm all required tools are present.
.USAGE
    powershell -ExecutionPolicy Bypass -File scripts\check-env.ps1
#>

$root = Split-Path $PSScriptRoot -Parent
$pass = 0
$fail = 0

function Ok($msg)  { Write-Host "  [OK]  $msg" -ForegroundColor Green;  $script:pass++ }
function Err($msg) { Write-Host "  [ERR] $msg" -ForegroundColor Red;    $script:fail++ }
function Warn($msg){ Write-Host "  [!!]  $msg" -ForegroundColor Yellow }
function Section($title) { Write-Host "`n--- $title ---" -ForegroundColor Cyan }

Write-Host "`nMediRoute — Environment Check" -ForegroundColor White

# ─── Python ─────────────────────────────────────────────────────────────────
Section "Python"
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    $ver = & python --version 2>&1
    Ok "Python: $ver  ($($py.Source))"
    $major = ($ver -replace 'Python (\d+)\.\d+.*','$1')
    $minor = ($ver -replace 'Python \d+\.(\d+).*','$1')
    if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 10)) {
        Warn "Python 3.10+ recommended (found $ver)"
    }
} else {
    Err "python not found. Install from https://python.org (add to PATH)"
}

# ─── Node / npm ─────────────────────────────────────────────────────────────
Section "Node.js / npm"
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    $nodeVer = & node --version 2>&1
    Ok "Node: $nodeVer  ($($node.Source))"
} else {
    Err "node not found. Install from https://nodejs.org (LTS)"
}

$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($npm) {
    $npmVer = & npm --version 2>&1
    Ok "npm: $npmVer"
} else {
    Err "npm not found (should ship with Node.js)"
}

# ─── Java ────────────────────────────────────────────────────────────────────
Section "Java (required for Android)"
$java = Get-Command java -ErrorAction SilentlyContinue
if ($java) {
    $javaVer = & java -version 2>&1 | Select-Object -First 1
    Ok "Java: $javaVer"
} elseif ($env:JAVA_HOME) {
    $javaExe = Join-Path $env:JAVA_HOME "bin\java.exe"
    if (Test-Path $javaExe) {
        $javaVer = & $javaExe -version 2>&1 | Select-Object -First 1
        Ok "Java (via JAVA_HOME): $javaVer"
    } else {
        Err "JAVA_HOME set but java.exe not found at: $javaExe"
    }
} else {
    Err "java not found. Install JDK 17+ — Android Studio bundles one at:`n       %LOCALAPPDATA%\Android\Sdk\jdk or via Android Studio settings."
    Warn "Set JAVA_HOME to your JDK folder, or install Android Studio (includes JDK)."
}

# ─── Android SDK / adb ───────────────────────────────────────────────────────
Section "Android SDK"
$androidHome = $env:ANDROID_HOME
if (-not $androidHome) { $androidHome = $env:ANDROID_SDK_ROOT }
if (-not $androidHome) {
    # Check Android Studio default locations
    $candidates = @(
        "$env:LOCALAPPDATA\Android\Sdk",
        "$env:USERPROFILE\AppData\Local\Android\Sdk"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $androidHome = $c; break }
    }
}

if ($androidHome -and (Test-Path $androidHome)) {
    Ok "Android SDK: $androidHome"
    # Check for adb
    $adbPath = Join-Path $androidHome "platform-tools\adb.exe"
    if (Test-Path $adbPath) {
        $adbVer = & $adbPath version 2>&1 | Select-Object -First 1
        Ok "adb: $adbVer"
    } else {
        Err "adb not found at $adbPath — install 'Android SDK Platform Tools' in Android Studio SDK Manager"
    }
} else {
    Err "Android SDK not found. Install Android Studio from https://developer.android.com/studio"
    Warn "After install, set ANDROID_HOME=$env:LOCALAPPDATA\Android\Sdk (or let Android Studio do it)"
}

# ─── Gradle (via gradlew.bat) ─────────────────────────────────────────────────
Section "Gradle"
$gradlew = Join-Path $root "frontend\android\gradlew.bat"
if (Test-Path $gradlew) {
    Ok "gradlew.bat found at frontend/android/gradlew.bat"
} else {
    Err "gradlew.bat missing — run 'npx cap add android' inside frontend/ to regenerate"
}

# ─── Required .env files ─────────────────────────────────────────────────────
Section "Environment files"
$backendEnv = Join-Path $root "mediroute-backend\.env"
if (Test-Path $backendEnv) {
    Ok "mediroute-backend/.env present"
} else {
    Err "mediroute-backend/.env missing — copy from mediroute-backend/.env.example and fill in values"
}

$frontendEnv = Join-Path $root "frontend\.env"
$frontendEnvProd = Join-Path $root "frontend\.env.production"
if ((Test-Path $frontendEnv) -or (Test-Path $frontendEnvProd)) {
    if (Test-Path $frontendEnv)     { Ok "frontend/.env present" }
    if (Test-Path $frontendEnvProd) { Ok "frontend/.env.production present" }
} else {
    Warn "No frontend/.env found — copy frontend/.env.example to frontend/.env (needed for local dev + APK builds)"
}

# ─── Python venv ─────────────────────────────────────────────────────────────
Section "Python virtual environment"
$venvPaths = @("venv", "testenv", ".venv") | ForEach-Object { Join-Path $root "mediroute-backend\$_\Scripts\python.exe" }
$foundVenv = $venvPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($foundVenv) {
    Ok "Python venv found: $foundVenv"
} else {
    Warn "No venv found in mediroute-backend/ — run scripts\setup.ps1 to create one"
}

# ─── Node modules ─────────────────────────────────────────────────────────────
Section "Node modules"
$frontendModules = Join-Path $root "frontend\node_modules"
if (Test-Path $frontendModules) {
    Ok "frontend/node_modules present"
} else {
    Warn "frontend/node_modules missing — run: cd frontend && npm install"
}

# ─── Summary ─────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "────────────────────────────────────────" -ForegroundColor DarkGray
if ($fail -eq 0) {
    Write-Host "  All checks passed ($pass OK)" -ForegroundColor Green
    Write-Host "  Run: scripts\setup.ps1 to install dependencies" -ForegroundColor Green
} else {
    Write-Host "  $fail issue(s) found. Fix errors above, then run scripts\setup.ps1" -ForegroundColor Yellow
}
Write-Host "────────────────────────────────────────" -ForegroundColor DarkGray
