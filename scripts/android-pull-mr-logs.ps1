<#
.SYNOPSIS
  Pull MediRoute NDJSON operational log from debuggable APK internal storage.
  Output: mr-app-log-<timestamp>.log in current directory.

  Requires: USB debugging, debug-signed APK (run-as works).

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\android-pull-mr-logs.ps1
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
$pkg = 'com.mediroute.app'
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$out = Join-Path (Get-Location) "mr-app-log-$ts.log"

Write-Host "Pulling files/mr-logs/app.log from $pkg ..." -ForegroundColor Cyan
& $adb shell "run-as $pkg cat files/mr-logs/app.log" | Out-File -FilePath $out -Encoding utf8

if (-not (Test-Path $out) -or ((Get-Item $out).Length -eq 0)) {
  Write-Host "Pull may be empty — ensure debug APK and app has written logs once." -ForegroundColor Yellow
}
Write-Host "Wrote: $out" -ForegroundColor Green
