# Commit without Cursor shell injecting --trailer Co-authored-by.
# Usage: .\scripts\git-commit-clean.ps1 -Subject "fix: example" [-Body "optional body"]
param(
    [Parameter(Mandatory = $true)]
    [string]$Subject,
    [string]$Body = ""
)

$ErrorActionPreference = "Stop"
$git = "C:\Program Files\Git\bin\git.exe"
if (-not (Test-Path $git)) { $git = "git" }

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Push-Location $repoRoot
try {
    $msgFile = Join-Path $repoRoot ".git\COMMIT_MSG_CLEAN.tmp"
    $text = if ($Body) { "$Subject`n`n$Body`n" } else { "$Subject`n" }
    [System.IO.File]::WriteAllText($msgFile, $text, [System.Text.UTF8Encoding]::new($false))

    & $git commit -F $msgFile
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Remove-Item $msgFile -ErrorAction SilentlyContinue
    & $git log -1 --format=full
}
finally {
    Pop-Location
}
