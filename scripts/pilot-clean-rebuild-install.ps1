# Ephemeral pilot cleanup — removed after run (or keep if useful).
$ErrorActionPreference = 'Continue'
Write-Host '>>> Stopping Gradle daemons...'
Push-Location 'd:\MediRoute\frontend\android'
& .\gradlew.bat --stop 2>$null
Pop-Location

Write-Host '>>> Releasing ports 8000, 5173 (listeners)...'
foreach ($port in @(8000, 5173)) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object {
      Write-Host "  Stopping PID $($_.OwningProcess) on port $port"
      Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

Write-Host '>>> Removing stale Android outputs...'
$paths = @(
  'd:\MediRoute\frontend\android\app\build',
  'd:\MediRoute\frontend\android\build',
  'd:\MediRoute\build_app_debug',
  'd:\MediRoute\frontend\build_app_debug',
  'd:\MediRoute\frontend\android-root-build'
)
foreach ($p in $paths) {
  if (Test-Path $p) {
    Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  removed: $p"
  }
}

Write-Host '>>> npm run build (frontend)...'
Set-Location 'd:\MediRoute\frontend'
npm run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host '>>> cap sync android...'
npx cap sync android
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host '>>> gradlew clean assembleDebug...'
Set-Location 'd:\MediRoute\frontend\android'
& .\gradlew.bat clean assembleDebug --no-daemon --no-problems-report
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# --- Locate debug APK (same roots as scripts/build-android.ps1) ---
$root = 'd:\MediRoute'
$frontend = Join-Path $root 'frontend'
$androidDir = Join-Path $frontend 'android'
$outputsRoots = @(
  (Join-Path $frontend 'build_app_debug\outputs'),
  (Join-Path $root 'build_app_debug\outputs'),
  (Join-Path $androidDir 'app\build\outputs')
)
$candidates = @()
foreach ($outputsRoot in $outputsRoots) {
  if (-not (Test-Path $outputsRoot)) { continue }
  $candidates += Get-ChildItem -LiteralPath $outputsRoot -Recurse -Filter '*.apk' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -like '*\apk\debug\*' }
}
$apkFile = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $apkFile) {
  Write-Host '[ERR] APK not found after build.'
  exit 1
}
Write-Host ">>> APK: $($apkFile.FullName)  (LastWrite: $($apkFile.LastWriteTime))"

function Resolve-Adb {
  if (Get-Command adb -ErrorAction SilentlyContinue) { return (Get-Command adb).Source }
  $sdk = $env:ANDROID_HOME
  if (-not $sdk) { $sdk = $env:ANDROID_SDK_ROOT }
  if (-not $sdk) { $sdk = "$env:LOCALAPPDATA\Android\Sdk" }
  $p = Join-Path $sdk 'platform-tools\adb.exe'
  if (-not (Test-Path $p)) { throw 'adb.exe not found' }
  return $p
}
$adb = Resolve-Adb
Write-Host '>>> adb devices:'
& $adb devices -l
$dev = & $adb devices | Where-Object { $_ -match '\s+device$' }
if (-not $dev) {
  Write-Host '[ERR] No device in adb devices.'
  exit 1
}

Write-Host '>>> adb install -r ...'
& $adb install -r $apkFile.FullName
if ($LASTEXITCODE -ne 0) {
  Write-Host '>>> install failed; uninstall com.mediroute.app then retry...'
  & $adb uninstall com.mediroute.app
  & $adb install -r $apkFile.FullName
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host '>>> Launch app...'
& $adb shell am start -n 'com.mediroute.app/com.mediroute.app.MainActivity'
Write-Host '>>> Pilot rebuild + install complete.'
exit 0
