<#
.SYNOPSIS
    Build MediRoute frontend + sync Capacitor + build APK + install to connected Android device.
.PARAMETER Release
    Build a release APK (requires frontend/android/keystore.properties).
    Default: debug APK.
.PARAMETER SkipBuild
    Skip Vite build + Capacitor sync; jump straight to Gradle + adb install.
    Use when you only changed native Android code.
.PARAMETER Launch
    Launch the app on device after install.
.USAGE
    # Debug APK (most common during development):
    powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1

    # Release APK:
    powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -Release

    # Skip JS rebuild (native-only change):
    powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -SkipBuild
#>

param(
    [switch]$Release,
    [switch]$SkipBuild,
    [switch]$Launch
)

$ErrorActionPreference = "Stop"
$root      = Split-Path $PSScriptRoot -Parent
$frontend  = Join-Path $root "frontend"
$androidDir= Join-Path $frontend "android"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "`n    FAILED: $msg" -ForegroundColor Red; exit 1 }

$buildVariant = if ($Release) { "Release" } else { "Debug" }
Write-Host "`nMediRoute Android Build — $buildVariant" -ForegroundColor White

# ─── Resolve adb ─────────────────────────────────────────────────────────────
Step "Locating adb"
$adb = Get-Command adb -ErrorAction SilentlyContinue
if ($adb) {
    $adbExe = $adb.Source
} else {
    $androidHome = $env:ANDROID_HOME
    if (-not $androidHome) { $androidHome = $env:ANDROID_SDK_ROOT }
    if (-not $androidHome) {
        $candidates = @("$env:LOCALAPPDATA\Android\Sdk", "$env:USERPROFILE\AppData\Local\Android\Sdk")
        foreach ($c in $candidates) { if (Test-Path $c) { $androidHome = $c; break } }
    }
    if (-not $androidHome) { Fail "adb not found and ANDROID_HOME not set. Install Android Studio." }
    $adbExe = Join-Path $androidHome "platform-tools\adb.exe"
    if (-not (Test-Path $adbExe)) { Fail "adb.exe not found at $adbExe — install Android SDK Platform Tools" }
}
Ok "adb: $adbExe"

# ─── Check device connected ───────────────────────────────────────────────────
Step "Checking connected device"
$devices = & $adbExe devices 2>&1 | Where-Object { $_ -match "\s+device$" }
if (-not $devices) {
    Fail "No Android device found. Connect device via USB with USB Debugging enabled."
}
Ok "Device: $($devices | Select-Object -First 1)"

# ─── Vite build + Capacitor sync ─────────────────────────────────────────────
if (-not $SkipBuild) {
    Step "Building frontend (Vite)"
    if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
        Fail "node_modules not found in frontend/. Run scripts\setup.ps1 first."
    }
    Set-Location $frontend
    & npm run build
    if ($LASTEXITCODE -ne 0) { Fail "Vite build failed" }
    Ok "Vite build succeeded"

    Step "Syncing Capacitor"
    & npx cap sync android
    if ($LASTEXITCODE -ne 0) { Fail "Capacitor sync failed" }
    Ok "Capacitor synced"
}

# ─── Gradle build ────────────────────────────────────────────────────────────
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

# ─── Find APK ─────────────────────────────────────────────────────────────────
Step "Locating APK"
$apkSearch = if ($Release) { "app\build\outputs\apk\release\*.apk" } else { "app\build\outputs\apk\debug\*.apk" }
$apkFile = Get-ChildItem (Join-Path $androidDir $apkSearch) -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $apkFile) {
    # Fallback — custom output name set in build.gradle
    $apkFile = Get-ChildItem (Join-Path $androidDir "app\build\outputs\apk\*\MediRoute.apk") -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $apkFile) { Fail "APK not found after build in $androidDir\app\build\outputs\apk\" }
Ok "APK: $($apkFile.FullName)"

# ─── adb install ──────────────────────────────────────────────────────────────
Step "Installing APK to device"
& $adbExe install -r $apkFile.FullName
if ($LASTEXITCODE -ne 0) { Fail "adb install failed" }
Ok "Installed successfully"

# ─── Launch (optional) ───────────────────────────────────────────────────────
if ($Launch) {
    Step "Launching app on device"
    & $adbExe shell am start -n "com.mediroute.app/com.mediroute.app.MainActivity"
    if ($LASTEXITCODE -eq 0) { Ok "App launched" }
}

Write-Host ""
Write-Host "────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  Build + install complete!" -ForegroundColor Green
Write-Host "  APK: $($apkFile.FullName)" -ForegroundColor DarkGray
Write-Host "────────────────────────────────────────" -ForegroundColor DarkGray
