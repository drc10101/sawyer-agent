@echo off
setlocal enabledelayedexpansion
title Sawyer Agent

:: ── Sawyer Agent installer ──────────────────────────────────────
::  Usage:
::    Double-click this file, or from PowerShell:
::      irm https://raw.githubusercontent.com/drc10101/sawyer-agent/master/install-sawyer.bat -OutFile install-sawyer.bat; .\install-sawyer.bat
::    From cmd:
::      curl -sL https://raw.githubusercontent.com/drc10101/sawyer-agent/master/install-sawyer.bat -o install-sawyer.bat && install-sawyer.bat
::
::  Commands (pass as arg, or just double-click for full install):
::    (no arg)   Full install: pip install + setup + desktop shortcut
::    reinstall  Update package + re-run setup
::    setup      Reconfigure API key and provider
::    uninstall  Remove Sawyer completely
::    start      Start the server (browser opens automatically)
:: ────────────────────────────────────────────────────────────────

if "%~1"=="" goto install
if /i "%~1"=="install" goto install
if /i "%~1"=="reinstall" goto reinstall
if /i "%~1"=="setup" goto setup
if /i "%~1"=="uninstall" goto uninstall
if /i "%~1"=="start" goto start
goto usage

:: ────────────────────────────────────────────────────────────────
:install
echo.
echo   ============================================
echo    Sawyer Agent - Install
echo   ============================================
echo.

:: Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found.
    echo  Install Python 3.11+ from https://python.org
    echo  Check "Add Python to PATH" during installation, then re-run.
    pause
    exit /b 1
)

:: Install Sawyer
echo  Installing Sawyer Agent...
pip install git+https://github.com/drc10101/sawyer-agent.git --quiet --upgrade
if %errorlevel% neq 0 (
    echo  [ERROR] Installation failed. Check your internet and try again.
    pause
    exit /b 1
)
echo  Package installed.

:: Run setup wizard
echo.
python -m sawyer_harness setup
if %errorlevel% neq 0 (
    echo.
    echo  [WARN] Setup did not complete. Run manually:
    echo    python -m sawyer_harness setup
)

:: Create desktop shortcut
call :create_shortcut

echo.
echo   ============================================
echo    Done! Sawyer Agent is ready.
echo.
echo    Double-click "Sawyer Agent" on your Desktop
echo    to start. The browser opens automatically.
echo.
echo    Other commands:
echo      install-sawyer.bat reinstall   Update and reconfigure
echo      install-sawyer.bat setup       Reconfigure API key only
echo      install-sawyer.bat uninstall   Remove everything
echo   ============================================
echo.
pause
exit /b 0

:: ────────────────────────────────────────────────────────────────
:reinstall
echo.
echo   ============================================
echo    Sawyer Agent - Reinstall
echo   ============================================
echo.

:: Update package
echo  Updating Sawyer Agent...
pip install git+https://github.com/drc10101/sawyer-agent.git --quiet --upgrade --force-reinstall --no-deps
if %errorlevel% neq 0 (
    echo  [ERROR] Update failed.
    pause
    exit /b 1
)
echo  Package updated.

:: Re-run setup
echo.
python -m sawyer_harness setup

:: Recreate shortcut
call :create_shortcut

echo.
echo   Reinstall complete.
pause
exit /b 0

:: ────────────────────────────────────────────────────────────────
:setup
echo.
python -m sawyer_harness setup
pause
exit /b 0

:: ────────────────────────────────────────────────────────────────
:uninstall
echo.
echo   ============================================
echo    Sawyer Agent - Uninstall
echo   ============================================
echo.
python -m sawyer_harness uninstall
pause
exit /b 0

:: ────────────────────────────────────────────────────────────────
:start
echo.
echo   Starting Sawyer Agent...
echo   Browser will open automatically when ready.
echo.
python -m sawyer_harness
goto :eof

:: ────────────────────────────────────────────────────────────────
:usage
echo.
echo   Usage: install-sawyer.bat [command]
echo.
echo   Commands:
echo     (none)      Full install: pip install + setup + shortcut
echo     reinstall   Update package and reconfigure
echo     setup       Reconfigure API key and provider
echo     uninstall   Remove Sawyer completely
echo     start       Start the server
echo.
pause
exit /b 1

:: ────────────────────────────────────────────────────────────────
:: Subroutine: create desktop shortcut
:: ────────────────────────────────────────────────────────────────
:create_shortcut
setlocal
set "APP_DIR=%USERPROFILE%\.sawyer-harness"

if not exist "%APP_DIR%" mkdir "%APP_DIR%"

:: Find the create-shortcut script from the installed package
set "SCRIPT_PATH="
for /f "delims=" %%i in ('python -c "from pathlib import Path; import sawyer_harness; print(Path(sawyer_harness.__file__).parent / 'scripts' / 'create-shortcut.ps1')" 2^>nul') do set "SCRIPT_PATH=%%i"

if "%SCRIPT_PATH%"=="" (
    echo   [WARN] Could not locate shortcut script. Skipping shortcut.
    goto :shortcut_done
)

if not exist "%SCRIPT_PATH%" (
    echo   [WARN] Shortcut script not found at: %SCRIPT_PATH%
    goto :shortcut_done
)

:: Run the PowerShell shortcut creator
powershell -ExecutionPolicy Bypass -File "%SCRIPT_PATH%"

if %errorlevel% equ 0 (
    echo   Desktop shortcut created.
) else (
    echo   [WARN] Shortcut creation failed.
)
:shortcut_done
endlocal
goto :eof