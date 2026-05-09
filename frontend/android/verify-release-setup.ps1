# verify-release-setup.ps1
# Run from frontend/android/: .\verify-release-setup.ps1

$pass = 0
$fail = 0

function Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green;  $script:pass++ }
function Fail($msg) { Write-Host "  [!!] $msg" -ForegroundColor Red;    $script:fail++ }
function Info($msg) { Write-Host "  [--] $msg" -ForegroundColor Cyan }

Write-Host ""
Write-Host "MediRoute Android Release Verification" -ForegroundColor Yellow
Write-Host "========================================"
Write-Host ""

# 1. keystore.properties exists
$kp = "keystore.properties"
if (Test-Path $kp) {
    Ok "keystore.properties exists"
    $props = Get-Content $kp | Where-Object { ($_ -notmatch "^#") -and ($_ -match "=") }
    $propMap = @{}
    foreach ($line in $props) {
        $parts = $line -split "=", 2
        $propMap[$parts[0].Trim()] = $parts[1].Trim()
    }
    if (($propMap["storePassword"] -eq "YOUR_STORE_PASSWORD") -or (-not $propMap["storePassword"])) {
        Fail "storePassword is still placeholder"
    } else {
        Ok "storePassword is set"
    }
    if (($propMap["keyPassword"] -eq "YOUR_KEY_PASSWORD") -or (-not $propMap["keyPassword"])) {
        Fail "keyPassword is still placeholder"
    } else {
        Ok "keyPassword is set"
    }
    $ksPath = $propMap["storeFile"]
    if ($ksPath) {
        # storeFile is resolved by Gradle relative to app/ subdir
        $resolved = [System.IO.Path]::GetFullPath((Join-Path (Join-Path (Get-Location) "app") $ksPath))
        if (Test-Path $resolved) {
            Ok "Keystore file found: $resolved"
        } else {
            Fail "Keystore NOT found at: $resolved"
        }
    }
} else {
    Fail "keystore.properties missing"
}

# 2. gitignore check
if (Test-Path ".gitignore") {
    $gi = Get-Content ".gitignore" -Raw
    if ($gi -match "keystore\.properties") {
        Ok "keystore.properties is gitignored"
    } else {
        Fail "keystore.properties NOT in .gitignore"
    }
} else {
    Fail ".gitignore missing"
}

# 3. google-services.json
if (Test-Path "app\google-services.json") {
    Ok "google-services.json found"
} else {
    Info "google-services.json not found (OK if not using Firebase)"
}

# 4. gradlew
if (Test-Path "gradlew.bat") { Ok "gradlew.bat present" } else { Fail "gradlew.bat missing" }

# 5. Build outputs
if (Test-Path "app\build\outputs\apk\release\app-release.apk") {
    Ok "Release APK already built"
} else {
    Info "APK not built yet: run .\gradlew.bat assembleRelease"
}
if (Test-Path "app\build\outputs\bundle\release\app-release.aab") {
    Ok "Release AAB already built"
} else {
    Info "AAB not built yet: run .\gradlew.bat bundleRelease"
}

# Summary
Write-Host ""
Write-Host "----------------------------------------"
if ($fail -eq 0) {
    Write-Host "  PASSED: $pass   FAILED: $fail" -ForegroundColor Green
} else {
    Write-Host "  PASSED: $pass   FAILED: $fail" -ForegroundColor Red
}
Write-Host "----------------------------------------"
Write-Host ""
if ($fail -eq 0) {
    Write-Host "All checks passed. Ready to build!" -ForegroundColor Green
} else {
    Write-Host "Fix the above issues before building." -ForegroundColor Red
}
Write-Host ""
