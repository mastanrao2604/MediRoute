# Point this repo at scripts/git-hooks (strips Cursor attribution from messages).
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$git = "C:\Program Files\Git\bin\git.exe"
if (-not (Test-Path $git)) { $git = "git" }

Push-Location $repoRoot
& $git config core.hooksPath scripts/git-hooks
Write-Host "core.hooksPath = scripts/git-hooks"
Pop-Location
