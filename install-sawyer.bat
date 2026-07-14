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
    echo  Python not found. Please install Python 3.11+ from https://python.org
    echo  Then re-run this script.
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

:: Find the installed icon
for /f "delims=" %%i in ('python -c "import sawyer_harness, os; print(os.path.join(os.path.dirname(sawyer_harness.__file__), 'web', 'static', 'sawyer.ico'))"') do set ICON=%%i

:: Config
set "CFG=%USERPROFILE%\sawyer-agent-config.yaml"

if exist "%CFG%" goto :makelink

echo  Choose your AI provider:
echo    1. Ollama - default
echo    2. OpenAI
echo    3. Anthropic - Claude
echo    4. Custom
echo.
set /p "P=Provider [1]: "
if "%P%"=="" set "P=1"

if "%P%"=="1" set "PN=ollama" & set "DM=glm-5.1:cloud" & set "DU=https://ollama.com/v1"
if "%P%"=="2" set "PN=openai" & set "DM=gpt-4o" & set "DU=https://api.openai.com/v1"
if "%P%"=="3" set "PN=anthropic" & set "DM=claude-sonnet-4-20250514" & set "DU=https://api.anthropic.com"
if "%P%"=="4" set "PN=custom" & set "DM=" & set "DU="

set /p "M=Model [%DM%]: "
if "%M%"=="" set "M=%DM%"
set /p "U=Base URL [%DU%]: "
if "%U%"=="" set "U=%DU%"
echo.
set /p "K=API Key: "

(
echo llm:
echo   provider: %PN%
echo   model: %M%
echo   api_key: %K%
echo   base_url: %U%
echo   max_tokens: 4096
echo   temperature: 0.7
echo security:
echo   sandbox: true
echo   max_command_timeout: 300
echo memory:
echo   backend: sqlite
echo   path: ~/.sawyer-harness/memory.db
) > "%CFG%"
echo  Config saved.
echo.

:makelink
:: Create launcher
set "DIR=%LOCALAPPDATA%\Sawyer Agent"
if not exist "%DIR%" mkdir "%DIR%"
set "BAT=%DIR%\launch.bat"

> "%BAT%" echo @echo off
>> "%BAT%" echo title Sawyer Agent
>> "%BAT%" echo echo   Sawyer Agent starting... http://127.0.0.1:8765
>> "%BAT%" echo start http://127.0.0.1:8765
>> "%BAT%" echo sawyer-web --config "%CFG%" --host 127.0.0.1 --port 8765
>> "%BAT%" echo pause

:: Create desktop shortcut with icon
set "LNK=%USERPROFILE%\Desktop\Sawyer Agent.lnk"
powershell -Command "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut('%LNK%'); $s.TargetPath='%BAT%'; $s.WorkingDirectory='%DIR%'; $s.Description='Sawyer Agent'; if(Test-Path '%ICON%'){$s.IconLocation='%ICON%'};$s.Save()" >nul 2>&1

echo.
echo   ============================================
echo    Done! Double-click "Sawyer Agent" on your
echo    Desktop to start. It opens at:
echo.
echo    http://127.0.0.1:8765
echo   ============================================
echo.
pause