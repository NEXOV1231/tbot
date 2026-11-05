@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m pip install -r requirements.txt
echo.
echo Запускаю сервер API и бота в отдельных окнах...
echo Окно "TBank API" — сервер (не закрывайте, пока пользуетесь).
echo Окно "TBank Bot" — бот @tbank_bot (не закрывайте, пока пользуетесь).
echo.
start "TBank Bot" cmd /k "py -X utf8 bot.py"
py -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
