# Pull MediRoute NDJSON log from debug APK (run-as requires debuggable build).
$ErrorActionPreference = 'Stop'
$adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
if (-not (Test-Path $adb)) { throw "adb not found at $adb" }
$pkg = 'com.mediroute.app'
$out = Join-Path (Get-Location) "mr-app-log-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
Write-Host "Device:" -ForegroundColor Cyan
& $adb devices
Write-Host "Pulling log to $out" -ForegroundColor Cyan
& $adb shell "run-as $pkg cat files/mr-logs/app.log" | Out-File -FilePath $out -Encoding utf8
if ((Get-Item $out).Length -eq 0) {
  Write-Host "Empty log. Use debug APK from scripts/build-android.ps1" -ForegroundColor Yellow
} else {
  Write-Host "Done. Location lines:" -ForegroundColor Green
  Select-String -Path $out -Pattern 'location|prof_gps' | Select-Object -Last 30
}
