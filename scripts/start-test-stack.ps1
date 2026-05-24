<#
.SYNOPSIS
    Start isolated MediRoute backend for regression tests (port 8765).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Backend = Join-Path $Root "mediroute-backend"
$TestData = Join-Path $Root "tests\.data"
$PidFile = Join-Path $TestData "stack.pid"
$OutLogFile = Join-Path $TestData "stack.out.log"
$ErrLogFile = Join-Path $TestData "stack.err.log"
$EnvFile = Join-Path $Root "tests\.env.test"
$VenvPy = Join-Path $Backend "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) { $VenvPy = "python" }

if (-not (Test-Path $EnvFile)) {
    throw "Missing tests\\.env.test - run scripts\\reset-test-db.ps1 first"
}

New-Item -ItemType Directory -Force -Path $TestData | Out-Null

# Stop existing stack if pid file present
if (Test-Path $PidFile) {
    & "$PSScriptRoot\stop-test-stack.ps1"
}

Write-Host ">>> Starting test backend on :8765..." -ForegroundColor Cyan

# Load test env into process for uvicorn child
Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
        [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process')
    }
}

$UvicornArgs = @(
    "-m", "uvicorn", "app.main:app",
    "--host", "127.0.0.1",
    "--port", "8765",
    "--log-level", "warning"
)

$proc = Start-Process -FilePath $VenvPy `
    -ArgumentList $UvicornArgs `
    -WorkingDirectory $Backend `
    -RedirectStandardOutput $OutLogFile `
    -RedirectStandardError $ErrLogFile `
    -PassThru `
    -WindowStyle Hidden

Set-Content -Path $PidFile -Value $proc.Id -Encoding ASCII
Write-Host "  [OK] PID $($proc.Id) - out: $OutLogFile  err: $ErrLogFile" -ForegroundColor Green

# Wait for health
$deadline = (Get-Date).AddSeconds(90)
$ok = $false
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -TimeoutSec 3
        if ($r.status -eq "healthy") { $ok = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ok) {
    Get-Content $OutLogFile -Tail 40 -ErrorAction SilentlyContinue
    Get-Content $ErrLogFile -Tail 40 -ErrorAction SilentlyContinue
    throw "Test stack failed health check"
}
Write-Host "  [OK] Health check passed" -ForegroundColor Green
