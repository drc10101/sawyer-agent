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

:: Find Python executable path
set "PYTHON_EXE="
for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%i"

:: Find the Sawyer icon from the installed package
set "ICON_PATH="
for /f "delims=" %%i in ('python -c "from pathlib import Path; import sawyer_harness; print(Path(sawyer_harness.__file__).parent / 'web' / 'static' / 'sawyer.ico')" 2^>nul') do set "ICON_PATH=%%i"

:: Create .lnk shortcut targeting python.exe directly (avoids batch-file icon override)
set "SHORTCUT=%USERPROFILE%\Desktop\Sawyer Agent.lnk"
if "%PYTHON_EXE%"=="" (
    :: Fallback: cannot create shortcut without python path
    echo   [WARN] Could not locate Python. Skipping shortcut.
    goto :shortcut_done
)

if "%ICON_PATH%"=="" (
    powershell -Command "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut('%SHORTCUT%'); $s.TargetPath='%PYTHON_EXE%'; $s.Arguments='-m sawyer_harness --host 127.0.0.1 --port 8765'; $s.WorkingDirectory='%APP_DIR%'; $s.Description='Sawyer Agent'; $s.Save()" >nul 2>&1
) else (
    powershell -Command "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut('%SHORTCUT%'); $s.TargetPath='%PYTHON_EXE%'; $s.Arguments='-m sawyer_harness --host 127.0.0.1 --port 8765'; $s.WorkingDirectory='%APP_DIR%'; $s.Description='Sawyer Agent'; $s.IconLocation='%ICON_PATH%'; $s.Save()" >nul 2>&1
)
if %errorlevel% equ 0 (
    echo   Desktop shortcut created.
) else (
    echo   [WARN] Shortcut creation failed.
)
:shortcut_done
endlocal
goto :eof