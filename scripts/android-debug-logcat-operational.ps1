<#
.SYNOPSIS
  Live pilot logcat: JS console (Capacitor), Chromium/WebView, crashes.

  Pair with persistent NDJSON: .\scripts\android-pull-mr-logs.ps1

.USAGE
  adb logcat -c   # optional: clear ring buffer first
  powershell -ExecutionPolicy Bypass -File scripts\android-debug-logcat-operational.ps1
#>
$ErrorActionPreference = 'Stop'

function Resolve-Adb {
  if (Get-Command adb -ErrorAction SilentlyContinue) { return (Get-Command adb).Source }
  $sdk = $env:ANDROID_HOME
  if (-not $sdk) { $sdk = $env:ANDROID_SDK_ROOT }
  if (-not $sdk) { $sdk = "$env:LOCALAPPDATA\Android\Sdk" }
  $p = Join-Path $sdk "platform-tools\adb.exe"
  if (-not (Test-Path $p)) { throw "adb.exe not found" }
  return $p
}

$adb = Resolve-Adb

Write-Host @"

Streaming logcat (Ctrl+C to stop).
Levels: Capacitor + chromium verbose; ActivityManager warn; AndroidRuntime error.

"@ -ForegroundColor Cyan

& $adb logcat -v time *:S `
  Capacitor:V `
  chromium:V `
  ActivityManager:W `
  AndroidRuntime:E
