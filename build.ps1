<#
.SYNOPSIS
    Build RainDelay as a single-file Windows executable (dist\RainDelay.exe).

.PARAMETER InstallDeps
    pip-install requirements.txt and PyInstaller before building.

.PARAMETER Run
    Launch the built exe when the build succeeds.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -InstallDeps -Run
#>
[CmdletBinding()]
param(
    [switch]$InstallDeps,
    [switch]$Run
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$exePath = Join-Path $PSScriptRoot 'dist\RainDelay.exe'

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# --- Python ---------------------------------------------------------------
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw 'python not found in PATH. Install Python 3.11+.' }
$pyVer = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$pyVer -lt [version]'3.11') { throw "Python 3.11+ required (found $pyVer)." }
Step "Python $pyVer ($($python.Source))"

# --- Dependencies ---------------------------------------------------------
if ($InstallDeps) {
    Step 'Installing requirements + PyInstaller'
    & python -m pip install --upgrade -r requirements.txt pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }
}

& python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller is not installed. Re-run with -InstallDeps (or: python -m pip install pyinstaller).'
}

if (-not (Test-Path 'assets\raindelay.ico')) { throw 'assets\raindelay.ico is missing (needed for the exe icon).' }
if (-not (Get-ChildItem 'assets' -Filter 'rain*.mp4' -File)) {
    Write-Warning 'No rain*.mp4 in assets\ - the exe will have no rain video.'
}

# --- Exe must not be locked -----------------------------------------------
if (Test-Path $exePath) {
    $running = Get-Process -Name 'RainDelay' -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $exePath }
    if ($running) { throw "dist\RainDelay.exe is running (PID $($running.Id -join ', ')). Quit it from the tray and re-run." }
}

# --- Build ----------------------------------------------------------------
Step 'Building single-file exe (RainDelay.spec)'
$sw = [Diagnostics.Stopwatch]::StartNew()
& python -m PyInstaller RainDelay.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)." }
$sw.Stop()

if (-not (Test-Path $exePath)) { throw "Build finished but $exePath was not produced." }

$version = (Get-Content 'main.py' -TotalCount 3)[2].Trim()
$sizeMB = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
Write-Host ''
Write-Host "Built  : $exePath" -ForegroundColor Green
Write-Host "Version: $version"
Write-Host "Size   : $sizeMB MB"
Write-Host ("Time   : {0:N0}s" -f $sw.Elapsed.TotalSeconds)

if ($Run) {
    Step 'Launching RainDelay.exe'
    Start-Process -FilePath $exePath
}
