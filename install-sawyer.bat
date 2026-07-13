@echo off
title Sawyer Agent - Installer
echo.
echo   ============================================
echo    Sawyer Agent - One-Click Setup
echo    Secure, Model-Agnostic, Self-Hosted AI Agent
echo   ============================================
echo.

:: Check for Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo  [!] Python not found. Installing Python 3.12...
    echo.
    echo  Downloading Python...
    curl -sL -o "%TEMP%\python-installer.exe" https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe
    echo  Installing Python (this may take a minute)...
    "%TEMP%\python-installer.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1
    del "%TEMP%\python-installer.exe"
    echo  Python installed.
    echo.
    :: Refresh PATH
    set "PATH=%PATH%;C:\Python312;C:\Python312\Scripts;%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts"
)

:: Verify Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python is still not available. Please install Python 3.11+ from https://python.org and re-run this script.
    echo.
    pause
    exit /b 1
)

:: Install Sawyer Agent
echo  [1/3] Installing Sawyer Agent...
pip install git+https://github.com/drc10101/sawyer-agent.git --quiet --upgrade 2>nul
if %errorlevel% neq 0 (
    echo  [ERROR] Installation failed. Check your internet connection and try again.
    echo.
    pause
    exit /b 1
)
echo  Done.
echo.

:: Create config if missing
set "CONFIG_FILE=%USERPROFILE%\sawyer-agent-config.yaml"
if not exist "%CONFIG_FILE%" (
    echo  [2/3] First-time setup -- configuring your AI provider.
    echo.
    echo  Choose your provider:
    echo    1. Ollama ^(cloud or local, default^)
    echo    2. OpenAI ^(GPT-4o, GPT-4.1, etc.^)
    echo    3. Anthropic ^(Claude^)
    echo    4. Custom OpenAI-compatible endpoint
    echo.
    set /p "PROVIDER=Provider [1]: "
    if "%PROVIDER%"=="" set "PROVIDER=1"

    if "%PROVIDER%"=="1" (
        set "PROVIDER_NAME=ollama"
        set "DEFAULT_MODEL=glm-5.1:cloud"
        set "DEFAULT_URL=https://ollama.com/v1"
    )
    if "%PROVIDER%"=="2" (
        set "PROVIDER_NAME=openai"
        set "DEFAULT_MODEL=gpt-4o"
        set "DEFAULT_URL=https://api.openai.com/v1"
    )
    if "%PROVIDER%"=="3" (
        set "PROVIDER_NAME=anthropic"
        set "DEFAULT_MODEL=claude-sonnet-4-20250514"
        set "DEFAULT_URL=https://api.anthropic.com"
    )
    if "%PROVIDER%"=="4" (
        set "PROVIDER_NAME=custom"
        set "DEFAULT_MODEL="
        set "DEFAULT_URL="
    )

    echo.
    set /p "MODEL=Model [%DEFAULT_MODEL%]: "
    if "%MODEL%"=="" set "MODEL=%DEFAULT_MODEL%"

    set /p "BASE_URL=Base URL [%DEFAULT_URL%]: "
    if "%BASE_URL%"=="" set "BASE_URL=%DEFAULT_URL%"

    echo.
    set /p "API_KEY=API Key: "

    echo  Saving config...
    (
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
    ) > "%CONFIG_FILE%"
    echo  Config saved to %CONFIG_FILE%
    echo.
) else (
    echo  [2/3] Config found at %CONFIG_FILE% -- skipping setup.
    echo.
)

:: Create desktop shortcut
echo  [3/3] Creating desktop shortcut...

set "SHORTCUT_PATH=%USERPROFILE%\Desktop\Sawyer Agent.bat"
(
    echo @echo off
    echo title Sawyer Agent
    echo echo.
    echo echo   Sawyer Agent -- http://127.0.0.1:8765
    echo echo   Press Ctrl+C to stop.
    echo echo.
    echo python -m sawyer_harness.web.server --config "%CONFIG_FILE%" --host 127.0.0.1 --port 8765
    echo if errorlevel 1 ^(
    echo     echo.
    echo     echo Sawyer Agent failed to start. Re-run install-sawyer.bat to fix.
    echo     pause
    echo ^)
) > "%SHORTCUT_PATH%"

echo  Desktop shortcut created: %SHORTCUT_PATH%
echo.

:: Try to set the icon using a .lnk shortcut (requires powershell)
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%USERPROFILE%\Desktop\Sawyer Agent.lnk'); $sc.TargetPath = '%SHORTCUT_PATH%'; $sc.WorkingDirectory = '%USERPROFILE%'; $sc.IconLocation = '%LOCALAPPDATA%\Programs\Python\Python314\python.exe,0'; $sc.Description = 'Sawyer Agent'; $sc.Save()" >nul 2>&1
if exist "%USERPROFILE%\Desktop\Sawyer Agent.lnk" (
    del "%SHORTCUT_PATH%"
    echo  Desktop shortcut created with icon.
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