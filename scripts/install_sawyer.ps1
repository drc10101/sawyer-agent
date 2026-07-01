<#
.SYNOPSIS
    Install Sawyer — Distributed MoE Inference Network
.DESCRIPTION
    Installs sawyer-core, creates a desktop shortcut with icon and menu launcher.
    Requires Python 3.11+ and pip.
.USAGE
    irm https://infill.systems/install/sawyer.ps1 | iex
    OR
    ./install_sawyer.ps1
#>

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$AppName = "Sawyer"
$AppPkg = "sawyer-core"

# --- Uninstall ---
if ($Uninstall) {
    Write-Host "Uninstalling Sawyer..." -ForegroundColor Yellow
    $DesktopShortcut = "$env:PUBLIC\Desktop\Sawyer.lnk"
    $StartShortcut = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Sawyer.lnk"
    if (Test-Path $DesktopShortcut) { Remove-Item $DesktopShortcut -Force; Write-Host "  Removed desktop shortcut" }
    if (Test-Path $StartShortcut) { Remove-Item $StartShortcut -Force; Write-Host "  Removed Start Menu shortcut" }
    Write-Host "Sawyer uninstalled." -ForegroundColor Green
    return
}

# --- Check Python ---
Write-Host ""
Write-Host "  Sawyer — Distributed MoE Inference Network" -ForegroundColor Cyan
Write-Host "  The load is split. Friends help." -ForegroundColor DarkGray
Write-Host ""

$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "3\.(\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 11) { $python = $cmd; break }
        }
    } catch {}
}

if (-not $python) {
    Write-Host "  ERROR: Python 3.11+ not found." -ForegroundColor Red
    Write-Host "  Install Python: https://www.python.org/downloads/" -ForegroundColor White
    Write-Host "  Make sure to check 'Add Python to PATH' during install." -ForegroundColor White
    exit 1
}

$pyVer = & $python --version 2>&1
Write-Host "  Using $pyVer" -ForegroundColor Green

# --- Install package ---
Write-Host "  Installing $AppPkg..." -ForegroundColor Cyan
& $python -m pip install --upgrade $AppPkg
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: pip install failed." -ForegroundColor Red
    exit 1
}

# --- Find installed package location ---
$PkgDir = & $python -c "import sawyer, os; print(os.path.dirname(sawyer.__file__))" 2>&1
$BatPath = Join-Path $PkgDir "sawyer.bat"
$IconPath = Join-Path $PkgDir "sawyer.ico"

if (-not (Test-Path $BatPath)) {
    Write-Host "  WARNING: Launcher not found at $BatPath" -ForegroundColor Yellow
}
if (-not (Test-Path $IconPath)) {
    Write-Host "  WARNING: Icon not found at $IconPath" -ForegroundColor Yellow
}

# --- Create shortcuts ---
$WshShell = New-Object -ComObject WScript.Shell

function New-Shortcut {
    param([string]$Path, [string]$Target, [string]$Icon, [string]$Desc)
    $Shortcut = $WshShell.CreateShortcut($Path)
    $Shortcut.TargetPath = $Target
    $Shortcut.WorkingDirectory = $env:USERPROFILE
    $Shortcut.Description = $Desc
    if ($Icon -and (Test-Path $Icon)) { $Shortcut.IconLocation = "$Icon,0" }
    $Shortcut.Save()
    Write-Host "  Shortcut: $Path" -ForegroundColor DarkGray
}

$DesktopShortcut = "$env:USERPROFILE\Desktop\Sawyer.lnk"
$StartShortcut = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Sawyer.lnk"

New-Shortcut $DesktopShortcut $BatPath $IconPath "Sawyer — Distributed MoE Inference"
New-Shortcut $StartShortcut $BatPath $IconPath "Sawyer — Distributed MoE Inference"

# --- Done ---
Write-Host ""
Write-Host "  Sawyer installed successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "  Quick start:" -ForegroundColor White
Write-Host "    Desktop shortcut: double-click Sawyer" -ForegroundColor Cyan
Write-Host "    Command line:     python -m sawyer chat" -ForegroundColor Cyan
Write-Host "    Serve a node:     python -m sawyer serve" -ForegroundColor Cyan
Write-Host ""