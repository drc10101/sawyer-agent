@echo off
title Sawyer Agent
echo.
echo  ███████╗██████╗ ██╗  ██╗███████╗██████╗  ██████╗ ████████╗
echo  ██╔════╝██╔══██╗██║ ██╔╝██╔════╝██╔══██╗██╔═══██╗╚══██╔══╝
echo  ███████╗██████╔╝█████╔╝ █████╗  ██████╔╝██║   ██║   ██║   
echo  ╚════██║██╔═══╝ ██╔═██╗ ██╔══╝  ██╔══██╗██║   ██║   ██║   
echo  ███████║██║     ██║  ██╗███████╗██║  ██║╚██████╔╝   ██║   
echo  ╚══════╝╚═╝     ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ ╚═════╝    ╚═╝   
echo.
echo  Agent v0.4 -- Secure, Model-Agnostic, Self-Hosted AI Agent
echo.
echo  Starting server on http://localhost:8765
echo  Press Ctrl+C to stop.
echo.

cd /d "C:\Users\drc10\development\sawyer-harness"

C:\Python314\python.exe -m sawyer_harness.web.server --host 0.0.0.0 --port 8765

pause