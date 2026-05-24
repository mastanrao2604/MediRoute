<#
.SYNOPSIS
    Reset isolated test database and seed deterministic fixtures.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Backend = Join-Path $Root "mediroute-backend"
$TestData = Join-Path $Root "tests\.data"
$VenvPy = Join-Path $Backend "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) { $VenvPy = "python" }

New-Item -ItemType Directory -Force -Path $TestData | Out-Null

# Optional Postgres override (isolated DB only — NEVER production)
$LocalEnv = Join-Path $Root "tests\.env.test.local"
if (Test-Path $LocalEnv) {
    Get-Content $LocalEnv | ForEach-Object {
        if ($_ -match '^\s*([^#=]+)=(.*)$') {
            [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process')
        }
    }
}

Write-Host ">>> Resetting isolated test DB..." -ForegroundColor Cyan
& $VenvPy (Join-Path $Root "tests\helpers\db_bootstrap.py")
if ($LASTEXITCODE -ne 0) { throw "DB bootstrap failed" }

# Write test env for stack
$DbFile = Join-Path $TestData "test_mediroute.db"
if ($env:TEST_DATABASE_URL) {
    $DbUrl = $env:TEST_DATABASE_URL
    Write-Host "  [OK] Using Postgres TEST_DATABASE_URL" -ForegroundColor Green
} else {
    $DbUrl = "sqlite:///$($DbFile -replace '\\','/')"
    Write-Host "  [OK] Using SQLite test DB" -ForegroundColor Green
}
$EnvContent = @"
ENV=development
SECRET_KEY=mediroute-test-secret-key-do-not-use-in-production
DATABASE_URL=$DbUrl
SMS_PROVIDER=log
OTP_FORCE_DEV=1
DISPATCH_ENABLED=true
OFFER_FATIGUE_MAX=1000
OFFER_FATIGUE_WINDOW_SEC=900
TEST_BASE_URL=http://127.0.0.1:8765
"@
$EnvPath = Join-Path $Root "tests\.env.test"
Set-Content -Path $EnvPath -Value $EnvContent -Encoding UTF8
Write-Host "  [OK] Test env: $EnvPath" -ForegroundColor Green
