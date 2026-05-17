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
.USAGE
    powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1
    powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -Release
    powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -SkipBuild
    powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -Launch
#>

param(
    [switch]$Release,
    [switch]$SkipBuild,
    [switch]$Launch
)

$root       = Split-Path $PSScriptRoot -Parent
$frontend   = Join-Path $root "frontend"
$androidDir = Join-Path $frontend "android"

function Step($msg) { Write-Host "`n>>> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  [OK]  $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "`n  [ERR] $msg`n" -ForegroundColor Red; exit 1 }

$buildVariant = if ($Release) { "Release" } else { "Debug" }
Write-Host "`n================================================" -ForegroundColor White
Write-Host "  MediRoute Android Build - $buildVariant" -ForegroundColor White
Write-Host "================================================" -ForegroundColor White

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
}

# --- Gradle build ---
Step "Building APK ($buildVariant)"
Set-Location $androidDir

if ($Release) {
    $keystoreFile = Join-Path $androidDir "keystore.properties"
    if (-not (Test-Path $keystoreFile)) {
        Fail "Release build requires frontend/android/keystore.properties.`nCopy from keystore.properties.example and fill in your signing credentials."
    }
    & .\gradlew.bat assembleRelease
} else {
    & .\gradlew.bat assembleDebug
}

if ($LASTEXITCODE -ne 0) { Fail "Gradle build failed" }
Ok "APK build succeeded"

# --- Find APK ---
Step "Locating APK"
$apkPattern = if ($Release) { "app\build\outputs\apk\release\*.apk" } else { "app\build\outputs\apk\debug\*.apk" }
$apkFile = Get-ChildItem (Join-Path $androidDir $apkPattern) -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $apkFile) {
    $apkFile = Get-ChildItem (Join-Path $androidDir "app\build\outputs\apk\*\MediRoute.apk") -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $apkFile) { Fail "APK not found after build." }
Ok "APK: $($apkFile.FullName)"

# --- adb install ---
Step "Installing APK to device"
& $adbExe install -r $apkFile.FullName
if ($LASTEXITCODE -ne 0) { Fail "adb install failed" }
Ok "Installed successfully"

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