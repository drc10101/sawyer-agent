@echo off
setlocal enabledelayedexpansion
title Sawyer Agent - Installer
echo.
echo   ============================================
echo    Sawyer Agent - One-Click Setup
echo    Secure, Model-Agnostic, Self-Hosted AI Agent
echo   ============================================
echo.

:: Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [!] Python not found. Please install Python 3.11+ from https://python.org
    echo.
    echo  Or re-run this script after installing Python.
    echo.
    pause
    exit /b 1
)

:: Install Sawyer Agent
echo  [1/3] Installing Sawyer Agent...
pip install git+https://github.com/drc10101/sawyer-agent.git --quiet --upgrade
if %errorlevel% neq 0 (
    echo  [ERROR] Installation failed. Check your internet connection and try again.
    pause
    exit /b 1
)
echo  Done.
echo.

:: Config file path
set "CONFIG_FILE=%USERPROFILE%\sawyer-agent-config.yaml"

:: Skip setup if config exists
if exist "%CONFIG_FILE%" goto :shortcut

echo  [2/3] First-time setup -- configuring your AI provider.
echo.
echo  Choose your provider:
echo    1. Ollama - cloud or local - default
echo    2. OpenAI - GPT-4o, GPT-4.1, etc.
echo    3. Anthropic - Claude
echo    4. Custom OpenAI-compatible endpoint
echo.
set /p "PROVIDER=Provider [1]: "
if "%PROVIDER%"=="" set "PROVIDER=1"

if "%PROVIDER%"=="1" set "PROVIDER_NAME=ollama"
if "%PROVIDER%"=="1" set "DEFAULT_MODEL=glm-5.1:cloud"
if "%PROVIDER%"=="1" set "DEFAULT_URL=https://ollama.com/v1"
if "%PROVIDER%"=="2" set "PROVIDER_NAME=openai"
if "%PROVIDER%"=="2" set "DEFAULT_MODEL=gpt-4o"
if "%PROVIDER%"=="2" set "DEFAULT_URL=https://api.openai.com/v1"
if "%PROVIDER%"=="3" set "PROVIDER_NAME=anthropic"
if "%PROVIDER%"=="3" set "DEFAULT_MODEL=claude-sonnet-4-20250514"
if "%PROVIDER%"=="3" set "DEFAULT_URL=https://api.anthropic.com"
if "%PROVIDER%"=="4" set "PROVIDER_NAME=custom"
if "%PROVIDER%"=="4" set "DEFAULT_MODEL="
if "%PROVIDER%"=="4" set "DEFAULT_URL="

echo.
set /p "MODEL=Model [%DEFAULT_MODEL%]: "
if "%MODEL%"=="" set "MODEL=%DEFAULT_MODEL%"

set /p "BASE_URL=Base URL [%DEFAULT_URL%]: "
if "%BASE_URL%"=="" set "BASE_URL=%DEFAULT_URL%"

echo.
set /p "API_KEY=API Key: "

echo  Saving config...
> "%CONFIG_FILE%" (
    echo llm:
    echo   provider: %PROVIDER_NAME%
    echo   model: %MODEL%
    echo   api_key: %API_KEY%
    echo   base_url: %BASE_URL%
    echo   max_tokens: 4096
    echo   temperature: 0.7
    echo security:
    echo   sandbox: true
    echo   max_command_timeout: 300
    echo memory:
    echo   backend: sqlite
    echo   path: ~/.sawyer-harness/memory.db
)
echo  Config saved to %CONFIG_FILE%
echo.

:shortcut
echo  [3/3] Creating desktop shortcut...

:: Download the Sawyer icon
set "ICON_DIR=%LOCALAPPDATA%\Sawyer Agent"
if not exist "%ICON_DIR%" mkdir "%ICON_DIR%"
set "ICON_FILE=%ICON_DIR%\sawyer-icon.ico"

:: Download icon from GitHub using PowerShell
powershell -Command "try { Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/drc10101/sawyer-agent/master/sawyer_harness/web/static/sawyer-icon.ico' -OutFile '%ICON_FILE%' -UseBasicParsing } catch {}" >nul 2>&1

:: Create the launcher batch file in AppData
set "LAUNCHER=%ICON_DIR%\sawyer-agent.bat"

> "%LAUNCHER%" echo @echo off
>> "%LAUNCHER%" echo title Sawyer Agent
>> "%LAUNCHER%" echo echo.
>> "%LAUNCHER%" echo echo   Sawyer Agent -- http://127.0.0.1:8765
>> "%LAUNCHER%" echo echo   Press Ctrl+C to stop.
>> "%LAUNCHER%" echo echo.
>> "%LAUNCHER%" echo python -m sawyer_harness.web.server --config "%CONFIG_FILE%" --host 127.0.0.1 --port 8765
>> "%LAUNCHER%" echo pause

:: Create a Windows shortcut (.lnk) with the icon using PowerShell
set "LNK_PATH=%USERPROFILE%\Desktop\Sawyer Agent.lnk"
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%LNK_PATH%'); $sc.TargetPath = '%LAUNCHER%'; $sc.WorkingDirectory = '%ICON_DIR%'; $sc.Description = 'Sawyer Agent - Secure, Self-Hosted AI'; if (Test-Path '%ICON_FILE%') { $sc.IconLocation = '%ICON_FILE%' }; $sc.Save()" >nul 2>&1

if exist "%LNK_PATH%" (
    echo  Desktop shortcut created with Sawyer icon.
) else (
    echo  Desktop shortcut created.
)

echo.
echo   ============================================
echo    Setup complete!
echo.
echo    Double-click "Sawyer Agent" on your Desktop
echo    to start chatting. It opens at:
echo.
echo    http://127.0.0.1:8765
echo   ============================================
echo.
pause