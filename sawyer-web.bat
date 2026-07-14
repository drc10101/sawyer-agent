@echo off
title Sawyer Agent
echo.
echo   Sawyer Agent -- http://127.0.0.1:8765
echo   Press Ctrl+C to stop.
echo.
sawyer-web --host 127.0.0.1 --port 8765
if errorlevel 1 (
    echo.
    echo Sawyer Agent is not installed. Run:
    echo   install-sawyer.bat
    echo.
    pause
)