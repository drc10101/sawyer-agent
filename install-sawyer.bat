@echo off
setlocal enabledelayedexpansion
title Sawyer Agent - Installer
echo.
echo   ============================================
echo    Sawyer Agent - One-Click Setup
echo   ============================================
echo.

:: Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  Python not found. Install Python 3.11+ from https://python.org
    echo  Check "Add Python to PATH" during installation, then re-run.
    pause
    exit /b 1
)

:: Install Sawyer
echo  Installing Sawyer Agent...
pip install git+https://github.com/drc10101/sawyer-agent.git --quiet --upgrade
if %errorlevel% neq 0 (
    echo  Installation failed. Check your internet and try again.
    pause
    exit /b 1
)
echo  Installed.
echo.

:: Run setup (uses Python -- no YAML, no name collisions, no PATH games)
python -m sawyer_harness setup
if %errorlevel% neq 0 (
    echo.
    echo  Setup did not complete. Run this to configure manually:
    echo    python -m sawyer_harness setup
)

echo.
echo   ============================================
echo    Done!
echo.
echo    Start the server:
echo      python -m sawyer_harness
echo.
echo    Other commands:
echo      python -m sawyer_harness setup        Reconfigure
echo      python -m sawyer_harness uninstall     Remove everything
echo      python -m sawyer_harness version        Show version
echo.
echo    Or double-click "Sawyer Agent" on your Desktop.
echo   ============================================
echo.
pause