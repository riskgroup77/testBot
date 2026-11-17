@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Akmaljon yordamchi bot
if not exist ".venv\Scripts\python.exe" (
  echo Virtual muhit yaratilmoqda...
  python -m venv .venv
)
echo Kutubxonalar tekshirilmoqda...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
echo Sozlash sahifasi: http://127.0.0.1:8765
".venv\Scripts\python.exe" main.py
pause
