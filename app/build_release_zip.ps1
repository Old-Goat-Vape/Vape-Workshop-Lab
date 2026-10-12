#Requires -Version 5.1
<#
.SYNOPSIS
  Build a clean Pauly-ready TVW release tree and TVW_Calculator_Release.zip

.DESCRIPTION
  Creates release/TVW_Calculator/app/ under The_Vape_Workshop_Calculator,
  copies this app folder (excluding venv, caches, backups, etc.),
  copies shared_mix_core into app/shared_mix_core/, verifies required paths,
  then zips release/TVW_Calculator to release/TVW_Calculator_Release.zip

  Run from anywhere; paths are resolved from this script's location.

.EXAMPLE
  cd path\to\The_Vape_Workshop_Calculator\app
  .\build_release_zip.ps1
#>
$ErrorActionPreference = "Stop"

function Write-Ok([string]$msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Fail([string]$msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red }
function Write-Info([string]$msg) { Write-Host "[INFO] $msg" -ForegroundColor Cyan }

$AppSource = $PSScriptRoot
$RepoRoot = Split-Path $AppSource -Parent
$ReleaseRoot = Join-Path $RepoRoot "release"
$ReleaseTree = Join-Path $ReleaseRoot "TVW_Calculator"
$ReleaseApp = Join-Path $ReleaseTree "app"
$ZipPath = Join-Path $ReleaseRoot "TVW_Calculator_Release.zip"

function Find-SharedMixCoreRoot {
    param([string]$AppDir)
    $calcRoot = Split-Path $AppDir -Parent
    $candidates = @(
        (Join-Path (Split-Path $calcRoot -Parent) "shared_mix_core"),
        (Join-Path $calcRoot "shared_mix_core"),
        (Join-Path $AppDir "shared_mix_core")
    )
    foreach ($p in $candidates) {
        $init = Join-Path $p "__init__.py"
        if (Test-Path -LiteralPath $init -PathType Leaf) { return $p }
    }
    $cur = $calcRoot
    for ($i = 0; $i -lt 12; $i++) {
        $try = Join-Path $cur "shared_mix_core"
        $init = Join-Path $try "__init__.py"
        if (Test-Path -LiteralPath $init -PathType Leaf) { return $try }
        $parent = Split-Path $cur -Parent
        if ($parent -eq $cur) { break }
        $cur = $parent
    }
    return $null
}

Write-Info "App source: $AppSource"
Write-Info "Release app: $ReleaseApp"

if (-not (Test-Path (Join-Path $AppSource "app.py"))) {
    Write-Fail "app.py not found next to this script. Place build_release_zip.ps1 in The_Vape_Workshop_Calculator\app\"
    exit 1
}

$SharedMixSource = Find-SharedMixCoreRoot -AppDir $AppSource
if (-not $SharedMixSource) {
    Write-Fail "shared_mix_core not found. Expected e.g. Desktop\shared_mix_core (sibling of The_Vape_Workshop_Calculator) or app\shared_mix_core with __init__.py"
    exit 1
}
Write-Info "shared_mix_core source: $SharedMixSource"

if (Test-Path -LiteralPath $ReleaseTree) {
    Remove-Item -LiteralPath $ReleaseTree -Recurse -Force
}
New-Item -ItemType Directory -Path $ReleaseApp -Force | Out-Null

# robocopy: 0-7 = success (with various copy outcomes); >= 8 = failure
# Use call operator so paths with spaces are one argument each (Start-Process -ArgumentList breaks them).
function Invoke-RobocopyApp {
    & robocopy.exe $AppSource $ReleaseApp /E `
        /XD venv .venv __pycache__ backups logs backup _archive `
        .pytest_cache .mypy_cache .ruff_cache htmlcov .git .github `
        /XF .env *.pyc *.pyo *.pyd *.log *.bak *.backup *_backup.db *.sqlite-shm *.sqlite-wal .coverage *.tmp *.swp `
        /NFL /NDL /NJH /NJS /NP /NS /NC
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        Write-Fail "robocopy app copy failed with exit code $code"
        exit 1
    }
}

function Invoke-RobocopyShared {
    param([string]$Src, [string]$Dst)
    & robocopy.exe $Src $Dst /E `
        /XD __pycache__ .pytest_cache .mypy_cache .ruff_cache .git `
        /XF *.pyc *.pyo *.pyd .env `
        /NFL /NDL /NJH /NJS /NP /NS /NC
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        Write-Fail "robocopy shared_mix_core copy failed with exit code $code"
        exit 1
    }
}

Write-Info "Copying app (with excludes)..."
Invoke-RobocopyApp

$SharedDest = Join-Path $ReleaseApp "shared_mix_core"
if (Test-Path -LiteralPath $SharedDest) {
    Remove-Item -LiteralPath $SharedDest -Recurse -Force
}
New-Item -ItemType Directory -Path $SharedDest -Force | Out-Null
Write-Info "Copying shared_mix_core -> app\shared_mix_core\..."
Invoke-RobocopyShared -Src $SharedMixSource -Dst $SharedDest

$required = @(
    @{ Path = Join-Path $ReleaseApp "app.py"; Name = "app/app.py" },
    @{ Path = Join-Path $ReleaseApp "requirements.txt"; Name = "app/requirements.txt" },
    @{ Path = Join-Path $ReleaseApp "shared_mix_core\__init__.py"; Name = "app/shared_mix_core/__init__.py" },
    @{ Path = Join-Path $ReleaseApp "core\shared_mix_path.py"; Name = "app/core/shared_mix_path.py" },
    @{ Path = Join-Path $ReleaseApp "Start_TVW_Calculator.bat"; Name = "app/Start_TVW_Calculator.bat" }
)

$allOk = $true
foreach ($r in $required) {
    if (Test-Path -LiteralPath $r.Path -PathType Leaf) {
        Write-Ok $r.Name
    } else {
        Write-Fail "Missing: $($r.Name)"
        $allOk = $false
    }
}

if (-not $allOk) {
    Write-Fail "Verification failed. Release tree left at: $ReleaseTree"
    exit 1
}

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Write-Info "Creating zip: $ZipPath"
Compress-Archive -Path $ReleaseTree -DestinationPath $ZipPath -CompressionLevel Optimal -Force

if (-not (Test-Path -LiteralPath $ZipPath -PathType Leaf)) {
    Write-Fail "Zip was not created."
    exit 1
}

$zipSize = (Get-Item -LiteralPath $ZipPath).Length
Write-Ok "Release build succeeded."
Write-Info "Zip: $ZipPath ($zipSize bytes)"
Write-Info "Unpacked layout: $ReleaseTree\app\"
exit 0
