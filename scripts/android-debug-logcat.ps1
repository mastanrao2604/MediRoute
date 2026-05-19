<#
.SYNOPSIS
  Stream Android logcat with filters useful for MediRoute WebView / JS debug output.

  See also: android-debug-logcat-operational.ps1 (pilot-focused),
             android-debug-verify.ps1, android-pull-mr-logs.ps1
.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\android-debug-logcat.ps1
#>
$ErrorActionPreference = 'Stop'
$adb = $null
if (Get-Command adb -ErrorAction SilentlyContinue) {
  $adb = (Get-Command adb).Source
} else {
  $sdk = $env:ANDROID_HOME
  if (-not $sdk) { $sdk = $env:ANDROID_SDK_ROOT }
  if (-not $sdk) { $sdk = "$env:LOCALAPPDATA\Android\Sdk" }
  $adb = Join-Path $sdk "platform-tools\adb.exe"
  if (-not (Test-Path $adb)) { Write-Error "adb not found. Install Android SDK Platform Tools." }
}

Write-Host "Streaming logcat (Ctrl+C to stop). Filter: MediRoute / MR / chromium console." -ForegroundColor Cyan
Write-Host "Tip: build APK with VITE_DEBUG_LOG=true or set localStorage mediroute_debug_log=1 on device.`n" -ForegroundColor DarkGray

& $adb logcat -v time *:S `
  chromium:D `
  Console:D `
  Capacitor:D `
  Capacitor/Console:D `
  WebView:D `
  ActivityManager:W `
  AndroidRuntime:E
