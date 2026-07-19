@echo off
setlocal enabledelayedexpansion
title Sawyer Agent Installer

:: ── Sawyer Agent installer ──────────────────────────────────────
::  Usage:
::    Double-click this file, or from cmd:
::      curl -sL https://raw.githubusercontent.com/drc10101/sawyer-agent/master/install-sawyer.bat -o install-sawyer.bat && install-sawyer.bat
::
::  Commands (pass as arg, or just double-click for full install):
::    (no arg)   Full install: Python check + pip install + setup + shortcut
::    reinstall  Update package + re-run setup + shortcut
::    setup      Reconfigure API key and provider
::    sandbox    Change permission mode (readonly/readwrite/all)
::    uninstall  Remove Sawyer completely
::    start      Start the server (browser opens automatically)
:: ────────────────────────────────────────────────────────────────

if "%~1"=="" goto install
if /i "%~1"=="install" goto install
if /i "%~1"=="reinstall" goto reinstall
if /i "%~1"=="setup" goto setup
if /i "%~1"=="sandbox" goto sandbox
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

:: ── Step 1: Check for Python ──────────────────────────────────
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] Python not found on PATH.
    echo.
    echo   Sawyer requires Python 3.11 or later.
    echo   Download from: https://python.org
    echo.
    echo   Make sure to check "Add Python to PATH" during setup,
    echo   then close this window and re-run install-sawyer.bat.
    echo.
    pause
    exit /b 1
)

:: Verify Python version is 3.11+
for /f "tokens=2 delims= " %%v in ('python --version 2^>nul') do set "PY_VER=%%v"
for /f "tokens=1,2 delims=." %%a in ("%PY_VER%") do (
    set "PY_MAJOR=%%a"
    set "PY_MINOR=%%b"
)
if %PY_MAJOR% lss 3 (
    echo   [ERROR] Python %PY_VER% is too old. Sawyer requires Python 3.11+.
    echo   Download from: https://python.org
    pause
    exit /b 1
)
if %PY_MAJOR% equ 3 if %PY_MINOR% lss 11 (
    echo   [ERROR] Python %PY_VER% is too old. Sawyer requires Python 3.11+.
    echo   Download from: https://python.org
    pause
    exit /b 1
)
echo   [OK] Python %PY_VER% found.

:: ── Step 2: Check for pip ──────────────────────────────────────
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] pip not found.
    echo.
    echo   pip should come with Python 3.11+. Try reinstalling Python
    echo   from https://python.org with "Add Python to PATH" checked.
    echo.
    echo   Alternatively, install pip manually:
    echo     python -m ensurepip --upgrade
    echo.
    pause
    exit /b 1
)
echo   [OK] pip is available.

:: ── Step 3: Ask about virtual environment ──────────────────────
echo.
echo   Sawyer can be installed in a virtual environment (recommended)
echo   or system-wide. A virtualenv keeps dependencies isolated.
echo.
set /p "USE_VENV=  Install in virtual environment? [Y/n] "
if /i "%USE_VENV%"=="n" goto skip_venv
if /i "%USE_VENV%"=="no" goto skip_venv

:: Create virtualenv if it doesn't exist
set "VENV_DIR=%USERPROFILE%\.sawyer-harness\venv"
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo   Creating virtual environment...
    python -m venv "%VENV_DIR%"
    if %errorlevel% neq 0 (
        echo   [ERROR] Failed to create virtual environment.
        echo   Falling back to system-wide install.
        goto skip_venv
    )
    echo   [OK] Virtual environment created at %VENV_DIR%
)

:: Activate venv for the rest of this script
call "%VENV_DIR%\Scripts\activate.bat"
echo   [OK] Virtual environment activated.

:skip_venv

:: ── Step 4: Install Sawyer ─────────────────────────────────────
echo.
echo   Installing Sawyer Agent...
pip install git+https://github.com/drc10101/sawyer-agent.git --quiet --upgrade
if %errorlevel% neq 0 (
    echo.
    echo   [ERROR] Installation failed.
    echo   Check your internet connection and try again.
    echo   If this persists, try: pip install --no-cache-dir git+https://github.com/drc10101/sawyer-agent.git
    echo.
    pause
    exit /b 1
)
echo   [OK] Package installed.

:: ── Step 5: Run setup wizard ──────────────────────────────────
echo.
echo   Running setup wizard...
echo.
python -m sawyer_harness setup
if %errorlevel% neq 0 (
    echo.
    echo   [WARN] Setup did not complete. Run manually:
    echo     python -m sawyer_harness setup
)

:: ── Step 6: Create desktop shortcut ──────────────────────────
call :create_shortcut

:: ── Done ───────────────────────────────────────────────────────
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
echo   Updating Sawyer Agent...
pip install git+https://github.com/drc10101/sawyer-agent.git --quiet --upgrade --force-reinstall --no-deps
if %errorlevel% neq 0 (
    echo   [ERROR] Update failed.
    pause
    exit /b 1
)
echo   [OK] Package updated.

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
:sandbox
echo.
python -m sawyer_harness sandbox %2
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
echo     (none)      Full install: Python check + pip install + setup + shortcut
echo     reinstall   Update package and reconfigure
echo     setup       Reconfigure API key and provider
echo     sandbox     Change permission mode (interactive or pass mode name)
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
    echo   [OK] Desktop shortcut created.
) else (
    echo   [WARN] Shortcut creation failed.
)
:shortcut_done
endlocal
goto :eof