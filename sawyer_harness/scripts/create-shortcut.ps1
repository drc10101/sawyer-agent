# Create-SawyerShortcut.ps1
# Creates a Windows desktop shortcut with a custom icon for Sawyer Agent.
# Technique: WScript.Shell COM object -> .lnk with IconLocation pointing to .ico
#
# This script is called by install-sawyer.bat and by 'python -m sawyer_harness install-shortcuts'.
# It MUST be a separate .ps1 file -- never embed PowerShell COM code inline in a .bat file
# because cmd.exe mangles $ signs in variable expansion.

param(
    [string]$ShortcutName = "Sawyer Agent",
    [string]$DesktopPath = "$env:USERPROFILE\Desktop",
    [string]$WorkingDir = "$env:USERPROFILE\.sawyer-harness"
)

# ── Resolve Python executable ──────────────────────────────────
# Check venv first, then system Python
$venvPython = "$env:USERPROFILE\.sawyer-harness\venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $pythonExe = $venvPython
    Write-Host "[INFO] Using venv Python: $pythonExe"
} else {
    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $pythonExe) {
        $pythonExe = (Get-Command python3 -ErrorAction SilentlyContinue).Source
    }
    if (-not $pythonExe) {
        Write-Host "[ERROR] Python not found on PATH and no venv exists."
        Write-Host "        Run install-sawyer.bat first, or install Python from https://python.org"
        exit 1
    }
    Write-Host "[INFO] Using system Python: $pythonExe"
}

# ── Resolve icon path from the installed package ────────────────
# Look for SAWYER_AGENT.ico first (the real branded icon), fall back to sawyer.ico
$iconPath = python -c "from pathlib import Path; import sawyer_harness; p = Path(sawyer_harness.__file__).parent / 'web' / 'static' / 'SAWYER_AGENT.ico'; print(p if p.exists() else Path(sawyer_harness.__file__).parent / 'web' / 'static' / 'sawyer.ico')" 2>$null

if (-not $iconPath -or -not (Test-Path $iconPath)) {
    Write-Host "[WARN] Icon not found at: $iconPath"
    Write-Host "       Shortcut will use default Python icon."
    $iconPath = ""
} else {
    Write-Host "[INFO] Icon: $iconPath"
}

# ── Ensure working directory exists ────────────────────────────
if (-not (Test-Path $WorkingDir)) {
    New-Item -ItemType Directory -Path $WorkingDir -Force | Out-Null
}

# ── Create the .lnk shortcut ───────────────────────────────────
$shortcutPath = Join-Path $DesktopPath "$ShortcutName.lnk"

# Remove old shortcut if it exists
if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
}

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($shortcutPath)
$lnk.TargetPath = $pythonExe
$lnk.Arguments = "-m sawyer_harness"
$lnk.WorkingDirectory = $WorkingDir
$lnk.Description = "$ShortcutName - Secure, model-agnostic, self-hosted AI agent"

# Set the icon -- this is the key step for custom icons on .lnk shortcuts
# Format: "path\to\icon.ico,index" where index 0 = first icon in the file
if ($iconPath -and (Test-Path $iconPath)) {
    $lnk.IconLocation = "$iconPath,0"
}

$lnk.Save()

Write-Host "[OK] Shortcut created: $shortcutPath"
Write-Host "     Target:  $pythonExe -m sawyer_harness"
Write-Host "     Icon:    $(if ($iconPath) { $iconPath } else { 'default' })"
Write-Host "     WorkDir: $WorkingDir"

# ── Flush the Windows icon cache ────────────────────────────────
# Without this, Windows may show the generic Python icon until reboot
$iconCachePath = "$env:LOCALAPPDATA\IconCache.db"
$explorerCachePath = "$env:LOCALAPPDATA\Microsoft\Windows\Explorer"

if (Test-Path $iconCachePath) {
    Remove-Item $iconCachePath -Force -ErrorAction SilentlyContinue
    Write-Host "     Icon cache flushed."
}

Get-ChildItem "$explorerCachePath\iconcache_*" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
Write-Host "     Explorer icon cache flushed."
Write-Host ""
Write-Host "If the icon doesn't appear immediately, press F5 on the Desktop to refresh."