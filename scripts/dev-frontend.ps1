<#
.SYNOPSIS
    Start the MediRoute Vite frontend dev server.
.USAGE
    powershell -ExecutionPolicy Bypass -File scripts\dev-frontend.ps1
#>

$root     = Split-Path $PSScriptRoot -Parent
$frontend = Join-Path $root "frontend"

if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Write-Host "ERROR: node_modules not found. Run scripts\setup.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host "Starting Vite dev server at http://localhost:5173  (Ctrl+C to stop)`n" -ForegroundColor Cyan

Set-Location $frontend
& npm run dev
