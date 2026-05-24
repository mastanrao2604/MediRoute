<#
.SYNOPSIS
    MediRoute operational regression — ONE command before every deployment.
    Fails immediately on critical failure (-x via pytest.ini).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Backend = Join-Path $Root "mediroute-backend"
$Frontend = Join-Path $Root "frontend"
$Reports = Join-Path $Root "tests\reports"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$JunitPath = Join-Path $Reports "junit-$Timestamp.xml"
$ReportPath = Join-Path $Reports "operational-report-$Timestamp.md"
$VenvPy = Join-Path $Backend "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) { $VenvPy = "python" }

$Failed = $false
$Notes = @{}

New-Item -ItemType Directory -Force -Path $Reports | Out-Null

Write-Host "`n================================================" -ForegroundColor White
Write-Host "  MediRoute Operational Regression Suite" -ForegroundColor White
Write-Host "================================================`n" -ForegroundColor White

try {
    # 1. Reset DB
    & "$PSScriptRoot\reset-test-db.ps1"

    # 2. Install test deps
    $TestReq = Join-Path $Root "tests\requirements.txt"
    & $VenvPy -m pip install -q -r $TestReq

    # 3. Start stack
    & "$PSScriptRoot\start-test-stack.ps1"

    # 4. Backend pytest (all suites)
    Write-Host "`n>>> Running backend regression tests..." -ForegroundColor Cyan
    Push-Location $Root
    $env:TEST_BASE_URL = "http://127.0.0.1:8765"
    & $VenvPy -m pytest tests/ --junitxml=$JunitPath -v
    if ($LASTEXITCODE -ne 0) { $Failed = $true; throw "Backend tests FAILED" }
    Pop-Location

    # 5. Frontend vitest
    Write-Host "`n>>> Running frontend regression tests..." -ForegroundColor Cyan
    Push-Location $Frontend
    if (-not (Test-Path "node_modules")) {
        npm install --silent
    }
    npm run test:regression --silent
    if ($LASTEXITCODE -ne 0) { $Failed = $true; throw "Frontend tests FAILED" }
    Pop-Location

    Write-Host "`n>>> ALL SUITES PASSED" -ForegroundColor Green
} catch {
    $Failed = $true
    Write-Host "`n>>> REGRESSION FAILED: $_" -ForegroundColor Red
    $Notes["Failure"] = $_.Exception.Message
} finally {
    & "$PSScriptRoot\stop-test-stack.ps1" 2>$null
}

# Generate report
Write-Host "`n>>> Generating operational report..." -ForegroundColor Cyan
& $VenvPy (Join-Path $Root "tests\helpers\generate_report_cli.py") $JunitPath $ReportPath $(if ($Failed) { "1" } else { "0" })

Write-Host "`nReport: $ReportPath" -ForegroundColor Cyan
if ($Failed) { exit 1 }
exit 0
