<#
.SYNOPSIS
    Stop test backend started by start-test-stack.ps1
#>
$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
$PidFile = Join-Path $Root "tests\.data\stack.pid"

if (-not (Test-Path $PidFile)) { return }

$stackPid = Get-Content $PidFile -ErrorAction SilentlyContinue
if ($stackPid) {
    try {
        Stop-Process -Id ([int]$stackPid) -Force -ErrorAction SilentlyContinue
        Write-Host "  [OK] Stopped test stack PID $stackPid" -ForegroundColor Green
    } catch { }
}
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
