<#
.SYNOPSIS
    MediRoute - Build APK and install to connected Android device.
.PARAMETER Release
    Build a release APK (requires frontend/android/keystore.properties).
    Default: debug APK.
.PARAMETER SkipBuild
    Skip Vite build + Capacitor sync. Gradle + adb only.
.PARAMETER Launch
    Launch the app on device after install.
.PARAMETER DevBackend
    Bake VITE_API_URL=http://127.0.0.1:8000 into the bundle (overrides .env for this run).
    Runs `adb reverse tcp:8000 tcp:8000` so the phone reaches uvicorn on the PC.
    Required for DEV OTP: backend must use ENV=development (DB dev_otp), not Render/production MSG91.
.PARAMETER ApiUrl
    Override VITE_API_URL for this build only (e.g. http://10.0.2.2:8000 for emulator).
    Does not run adb reverse — use -DevBackend for USB device + localhost.
.USAGE
    powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1
    powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -Release
    powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -SkipBuild
    powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -Launch
    powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -Launch -DevBackend
#>

param(
    [switch]$Release,
    [switch]$SkipBuild,
    [switch]$Launch,
    [switch]$DevBackend,
    [string]$ApiUrl = ""
)

$root       = Split-Path $PSScriptRoot -Parent
$frontend   = Join-Path $root "frontend"
$androidDir = Join-Path $frontend "android"

function Step($msg) { Write-Host "`n>>> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  [OK]  $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "`n  [ERR] $msg`n" -ForegroundColor Red; exit 1 }

if ($DevBackend -and $ApiUrl -ne "") {
    Fail "Use either -DevBackend or -ApiUrl, not both."
}

$viteApiOverride = $null
if ($DevBackend) {
    $viteApiOverride = "http://127.0.0.1:8000"
}
elseif ($ApiUrl -ne "") {
    $viteApiOverride = $ApiUrl
}

$buildVariant = if ($Release) { "Release" } else { "Debug" }

Write-Host "`n================================================" -ForegroundColor White
Write-Host "  MediRoute Android Build - $buildVariant" -ForegroundColor White
Write-Host "================================================" -ForegroundColor White

if ($SkipBuild -and $null -ne $viteApiOverride) {
    Write-Host "  [WARN] -SkipBuild: VITE_API_URL override is skipped - APK still has the URL from the last full frontend build." -ForegroundColor Yellow
}
# --- Resolve node (PATH first, then bundled fallback) ---
$nodeExe = $null
if (Get-Command node -ErrorAction SilentlyContinue) {
    $nodeExe = "node"
} else {
    $bundled = Join-Path $root "mediroute-backend\node.exe"
    if (Test-Path $bundled) { $nodeExe = $bundled }
}
if (-not $nodeExe) { Fail "node not found. Install from https://nodejs.org (LTS) and add to PATH." }

# --- Resolve adb ---
Step "Locating adb"
$adbExe = $null
if (Get-Command adb -ErrorAction SilentlyContinue) {
    $adbExe = (Get-Command adb).Source
} else {
    $androidHome = $env:ANDROID_HOME
    if (-not $androidHome) { $androidHome = $env:ANDROID_SDK_ROOT }
    if (-not $androidHome) {
        $candidates = @(
            "$env:LOCALAPPDATA\Android\Sdk",
            "$env:USERPROFILE\AppData\Local\Android\Sdk"
        )
        foreach ($c in $candidates) {
            if (Test-Path $c) { $androidHome = $c; break }
        }
    }
    if (-not $androidHome) { Fail "adb not found and ANDROID_HOME not set. Install Android Studio." }
    $adbExe = Join-Path $androidHome "platform-tools\adb.exe"
    if (-not (Test-Path $adbExe)) { Fail "adb.exe not found at $adbExe - install Android SDK Platform Tools." }
}
Ok "adb: $adbExe"

# --- Check device connected ---
Step "Checking connected device"
$devices = & $adbExe devices | Where-Object { $_ -match "\s+device$" }
if (-not $devices) {
    Fail "No Android device found. Connect device via USB with USB Debugging enabled."
}
Ok "Device: $($devices | Select-Object -First 1)"

# --- Vite build + Capacitor sync ---
if (-not $SkipBuild) {
    if ($null -ne $viteApiOverride) {
        $prevViteUrl = $env:VITE_API_URL
        $env:VITE_API_URL = $viteApiOverride
        Ok "VITE_API_URL for this build (session override): $viteApiOverride"
    }
    try {
        Step "Building frontend (Vite)"
        if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
            Fail "node_modules not found. Run scripts\setup1.ps1 first."
        }
        Set-Location $frontend
        & $nodeExe "node_modules\vite\bin\vite.js" build
        if ($LASTEXITCODE -ne 0) { Fail "Vite build failed" }
        Ok "Vite build succeeded"

        Step "Syncing Capacitor"
        & $nodeExe "node_modules\@capacitor\cli\bin\capacitor" sync android
        if ($LASTEXITCODE -ne 0) { Fail "Capacitor sync failed" }
        Ok "Capacitor synced"
    } finally {
        if ($null -ne $viteApiOverride) {
            if ($null -eq $prevViteUrl -or $prevViteUrl -eq '') {
                Remove-Item Env:\VITE_API_URL -ErrorAction SilentlyContinue
            } else {
                $env:VITE_API_URL = $prevViteUrl
            }
        }
    }
}

# --- Gradle build ---
Step "Building APK ($buildVariant)"
Set-Location $androidDir

if ($Release) {
    $keystoreFile = Join-Path $androidDir "keystore.properties"
    if (-not (Test-Path $keystoreFile)) {
        Fail "Release build requires frontend/android/keystore.properties.`nCopy from keystore.properties.example and fill in your signing credentials."
    }
    & .\gradlew.bat assembleRelease --no-daemon --no-problems-report
} else {
    & .\gradlew.bat assembleDebug --no-daemon --no-problems-report
}

if ($LASTEXITCODE -ne 0) { Fail "Gradle build failed" }
Ok "APK build succeeded"

# --- Find APK ---
# app/build.gradle sets buildDir to File(rootDir.parentFile.parentFile, "build_app_debug"), which places
# APKs under <repo>/build_app_debug/outputs (not frontend/android/app/build/outputs).
Step "Locating APK"
$apkKind = if ($Release) { "release" } else { "debug" }
$outputsRoots = @(
    (Join-Path $frontend "build_app_debug\outputs"),
    (Join-Path $root "build_app_debug\outputs"),
    (Join-Path $androidDir "app\build\outputs")
)
$candidates = @()
foreach ($outputsRoot in $outputsRoots) {
    if (-not (Test-Path $outputsRoot)) { continue }
    $candidates += Get-ChildItem -LiteralPath $outputsRoot -Recurse -Filter "*.apk" -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like "*\apk\$apkKind\*" }
}
if (-not $candidates -or $candidates.Count -eq 0) {
    foreach ($outputsRoot in $outputsRoots) {
        if (-not (Test-Path $outputsRoot)) { continue }
        $candidates += Get-ChildItem -LiteralPath $outputsRoot -Recurse -Filter "*.apk" -ErrorAction SilentlyContinue
    }
}
$apkFile = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $apkFile) { Fail "APK not found after build." }
Ok "APK: $($apkFile.FullName)"

# --- adb install ---
Step "Installing APK to device"
& $adbExe install -r $apkFile.FullName
if ($LASTEXITCODE -ne 0) { Fail "adb install failed" }
Ok "Installed successfully"

if ($DevBackend) {
    Step "USB reverse proxy (device http://127.0.0.1:8000 -> this PC port 8000)"
    & $adbExe reverse tcp:8000 tcp:8000
    if ($LASTEXITCODE -eq 0) {
        Ok "adb reverse active - start backend: uvicorn app.main:app --host 127.0.0.1 --port 8000"
    } else {
        Write-Host "  [WARN] adb reverse failed - run: adb reverse tcp:8000 tcp:8000" -ForegroundColor Yellow
    }
}

# --- Launch (optional) ---
if ($Launch) {
    Step "Launching app on device"
    & $adbExe shell am start -n "com.mediroute.app/com.mediroute.app.MainActivity"
    if ($LASTEXITCODE -eq 0) { Ok "App launched" }
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "  Build + install complete!" -ForegroundColor Green
Write-Host "  APK: $($apkFile.FullName)" -ForegroundColor DarkGray
Write-Host "================================================" -ForegroundColor Green
