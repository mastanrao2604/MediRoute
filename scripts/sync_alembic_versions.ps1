# Copy Alembic revisions from scripts/alembic_versions into mediroute-backend/alembic/versions
# Run before deploy if alembic/versions is missing d4/e5/f6 revisions.
$root = Split-Path $PSScriptRoot -Parent
$src  = Join-Path $root "scripts\alembic_versions"
$dest = Join-Path $root "mediroute-backend\alembic\versions"
if (-not (Test-Path $dest)) { New-Item -ItemType Directory -Force -Path $dest | Out-Null }
Get-ChildItem $src -Filter "*.py" | ForEach-Object {
  Copy-Item $_.FullName (Join-Path $dest $_.Name) -Force
  Write-Host "Copied $($_.Name)"
}
