# Install or update the Kryova backend on Windows.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -NoIndex
#
# The PowerShell counterpart of scripts/setup.sh, and the same design: every
# step is idempotent, so re-running it *is* the update. See that file's header
# for why there is no separate update script.

[CmdletBinding()]
param(
    [switch]$NoIndex
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Step($message) { Write-Host "`n> $message" -ForegroundColor White }
function Ok($message)   { Write-Host "  [ok] $message" -ForegroundColor Green }
function Warn($message) { Write-Host "  [!] $message" -ForegroundColor Yellow }
function Fail($message) { Write-Host "  [x] $message" -ForegroundColor Red }

# Run a native command with its stderr discarded, and *only* discarded.
#
# Windows PowerShell 5.1 wraps every stderr line of a native executable in an
# ErrorRecord the moment that stream is redirected, and `$ErrorActionPreference
# = "Stop"` above then makes the first one terminating. So `alembic upgrade head
# 2>$null` killed this script on every **successful** run -- alembic logs its
# progress ("Context impl PostgresqlImpl") to stderr -- and setup exited 1 after
# applying the migrations correctly, never reaching the index or the final
# instructions. Assigning the preference inside a function shadows the script's
# copy for the length of the call and restores it on return.
function Quiet([string]$exe, [string[]]$arguments) {
    $ErrorActionPreference = "Continue"
    & $exe @arguments 2>$null
}

Write-Host "===================================="
Write-Host "  Kryova backend - setup / update"
Write-Host "===================================="

# --- Python -----------------------------------------------------------------
Step "Python"
$python = $null
foreach ($candidate in @("python", "python3", "py")) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $found) { continue }
    # 3.12 or newer: the codebase uses PEP 695 generics and StrEnum.
    & $candidate -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
}
if (-not $python) {
    Fail "Python 3.12 or newer is required and was not found."
    Write-Host "     Install it from https://www.python.org/downloads/ or: winget install Python.Python.3.12"
    exit 1
}
Ok (& $python --version)

# --- Virtual environment ----------------------------------------------------
Step "Virtual environment"
$venvPython = Join-Path (Get-Location) "venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    Ok "reusing .\venv"
} else {
    # A venv directory with no interpreter is a failed previous run, not an
    # install. It holds nothing but packages, so replacing it is safe.
    if (Test-Path "venv") {
        Warn ".\venv is incomplete; recreating it"
        Remove-Item -Recurse -Force "venv"
    }
    & $python -m venv venv
    Ok "created .\venv"
}

# --- Dependencies -----------------------------------------------------------
Step "Dependencies"
& $venvPython -m pip install --upgrade pip --quiet
# A native command's non-zero exit does not throw in PowerShell, `--quiet` hides
# most of what pip says, and this step used to print [ok] either way -- so an
# install that failed on `cadquery-ocp` (the one dependency here big enough and
# platform-specific enough to fail) reported success and the failure surfaced
# hundreds of lines later as an import error.
if (Test-Path "requirements-dev.txt") {
    $requirements = "requirements-dev.txt"
    $label = "runtime + development dependencies"
} else {
    $requirements = "requirements.txt"
    $label = "runtime dependencies"
}
& $venvPython -m pip install -r $requirements --quiet
if ($LASTEXITCODE -ne 0) {
    Fail "pip could not install $requirements (exit $LASTEXITCODE)."
    Write-Host "     Re-run without --quiet to see which package failed:"
    Write-Host "       .\venv\Scripts\python.exe -m pip install -r $requirements"
    exit 1
}
Ok $label

# --- Configuration ----------------------------------------------------------
Step "Configuration"
if (Test-Path ".env") {
    Ok ".env already exists (left untouched)"
} else {
    Copy-Item ".env.example" ".env"
    Ok "created .env from .env.example"
    Warn "set DATABASE_URL and SECRET_KEY in .env before starting the server"
}

# --- Database ---------------------------------------------------------------
Step "Database"
Quiet $venvPython @("-m", "alembic", "upgrade", "head")
if ($LASTEXITCODE -eq 0) {
    Ok "migrations applied"
} else {
    Warn "could not reach the database; skipping migrations"
    Warn "set DATABASE_URL in .env, then re-run this script"
}

# --- Reference manuals ------------------------------------------------------
Step "Reference manuals"
if ($NoIndex) {
    Warn "skipped (-NoIndex)"
} else {
    # --check exits non-zero only when a build is actually needed, which is what
    # keeps a re-run from re-reading hundreds of megabytes of unchanged PDFs.
    Quiet $venvPython @("-m", "app.retrieval.build", "--check") | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Ok "index is up to date"
    } else {
        Write-Host "  documents have changed; rebuilding..."
        Quiet $venvPython @("-m", "app.retrieval.build")
        if ($LASTEXITCODE -eq 0) {
            Ok "index rebuilt"
        } else {
            # The assistant works without it, so this is a warning, not a failure.
            Warn "could not build the index; the assistant will answer without it"
        }
    }
}

# --- Done -------------------------------------------------------------------
Step "Ready"
Write-Host @"
  Start the server:
    .\venv\Scripts\Activate.ps1
    uvicorn app.main:app --reload

  Add reference manuals:
    put PDFs in data\bm25\sources\ and re-run this script
"@
