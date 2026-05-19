<#
.SYNOPSIS
  Quick MediRoute pilot sanity check: adb, device, package install, optional NDJSON log tail hint.
.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\android-debug-verify.ps1
#>
$ErrorActionPreference = 'Stop'

function Resolve-Adb {
  if (Get-Command adb -ErrorAction SilentlyContinue) { return (Get-Command adb).Source }
  $sdk = $env:ANDROID_HOME
  if (-not $sdk) { $sdk = $env:ANDROID_SDK_ROOT }
  if (-not $sdk) { $sdk = "$env:LOCALAPPDATA\Android\Sdk" }
  $p = Join-Path $sdk "platform-tools\adb.exe"
  if (-not (Test-Path $p)) { throw "adb.exe not found at $p" }
  return $p
}

$adb = Resolve-Adb
$pkg = 'com.mediroute.app'

Write-Host "`n=== adb path ===" -ForegroundColor Cyan
Write-Host $adb

Write-Host "`n=== adb devices ===" -ForegroundColor Cyan
& $adb devices -l

Write-Host "`n=== package ($pkg) ===" -ForegroundColor Cyan
& $adb shell pm path $pkg 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "  (not installed or shell blocked)" -ForegroundColor Yellow
} else {
  $pidOut = & $adb shell pidof $pkg 2>$null
  if ($pidOut) { Write-Host "  pidof: $pidOut" -ForegroundColor Green }
}

Write-Host "`n=== persistent MR logs (pull separately) ===" -ForegroundColor Cyan
Write-Host "  .\scripts\android-pull-mr-logs.ps1"
Write-Host "`n=== live logcat (operational) ===" -ForegroundColor Cyan
Write-Host "  .\scripts\android-debug-logcat-operational.ps1"
Write-Host ""
