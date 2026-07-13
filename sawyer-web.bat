@echo off
title Sawyer Agent
echo.
echo   Sawyer Agent -- Secure, Model-Agnostic, Self-Hosted AI Agent
echo.
echo   Starting server on http://127.0.0.1:8765
echo   Press Ctrl+C to stop.
echo.

where python >nul 2>nul && set PY=python || set PY=python3
%PY% -m sawyer_harness.web.server --host 127.0.0.1 --port 8765
if errorlevel 1 (
    echo.
    echo Sawyer Agent is not installed. Run:
    echo   pip install git+https://github.com/drc10101/sawyer-agent.git
    echo.
    pause
)