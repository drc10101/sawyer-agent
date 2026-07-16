# Create-SawyerShortcut.ps1
# Creates a Windows desktop shortcut with a custom icon
# Technique: WScript.Shell COM object -> .lnk with IconLocation pointing to .ico

param(
    [string]$ShortcutName = "Sawyer Agent",
    [string]$DesktopPath = "$env:USERPROFILE\Desktop",
    [string]$WorkingDir = "$env:USERPROFILE\.sawyer-harness"
)

# Resolve Python executable
$pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonExe) {
    Write-Host "[ERROR] Python not found on PATH"
    exit 1
}

# Resolve icon path from the installed package
$iconPath = python -c "from pathlib import Path; import sawyer_harness; print(Path(sawyer_harness.__file__).parent / 'web' / 'static' / 'sawyer.ico')"
if (-not (Test-Path $iconPath)) {
    Write-Host "[WARN] Icon not found at: $iconPath"
    Write-Host "       Shortcut will use default Python icon."
    $iconPath = ""
}

# Create the .lnk shortcut
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
$lnk.Description = "$ShortcutName - Secure AI Agent Framework"

# Set the icon -- this is the key step for custom icons on .lnk shortcuts
# Format: "path\to\icon.ico,index" where index 0 = first icon in the file
if ($iconPath -and (Test-Path $iconPath)) {
    $lnk.IconLocation = "$iconPath,0"
}

$lnk.Save()

Write-Host "Shortcut created: $shortcutPath"
Write-Host "  Target:  $pythonExe -m sawyer_harness"
Write-Host "  Icon:    $(if ($iconPath) { $iconPath } else { 'default' })"
Write-Host "  WorkDir: $WorkingDir"

# Flush the Windows icon cache so the custom icon shows immediately
# Without this, Windows may show the generic Python icon until reboot
$iconCachePath = "$env:LOCALAPPDATA\IconCache.db"
$explorerCachePath = "$env:LOCALAPPDATA\Microsoft\Windows\Explorer"

if (Test-Path $iconCachePath) {
    Remove-Item $iconCachePath -Force -ErrorAction SilentlyContinue
    Write-Host "  Icon cache flushed."
}

Get-ChildItem "$explorerCachePath\iconcache_*" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
Write-Host "  Explorer icon cache flushed."

Write-Host ""
Write-Host "If the icon doesn't appear immediately, press F5 on the Desktop to refresh."