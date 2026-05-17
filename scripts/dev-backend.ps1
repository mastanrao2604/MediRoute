<#
.SYNOPSIS
    Start the MediRoute FastAPI backend in development mode (hot reload).
.USAGE
    powershell -ExecutionPolicy Bypass -File scripts\dev-backend.ps1
#>

$root    = Split-Path $PSScriptRoot -Parent
$backend = Join-Path $root "mediroute-backend"

# Detect venv — supports both 'venv' (new standard) and 'testenv' (legacy)
$venvCandidates = @("venv", "testenv", ".venv")
$pythonExe = $null
foreach ($v in $venvCandidates) {
    $candidate = Join-Path $backend "$v\Scripts\python.exe"
    if (Test-Path $candidate) { $pythonExe = $candidate; break }
}

if (-not $pythonExe) {
    Write-Host "ERROR: No Python venv found in mediroute-backend/. Run scripts\setup.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host "Using Python: $pythonExe" -ForegroundColor DarkGray
Write-Host "Starting backend at http://localhost:8000  (Ctrl+C to stop)`n" -ForegroundColor Cyan

Set-Location $backend
& $pythonExe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
