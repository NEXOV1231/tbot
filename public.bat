@echo off
cd /d "%~dp0"
echo.
echo Starting API server and bot...
start "TBank API" cmd /k "py -m uvicorn main:app --host 127.0.0.1 --port 8000"
start "TBank Bot" cmd /k "py -X utf8 bot.py"
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tunnel.ps1"
pause
