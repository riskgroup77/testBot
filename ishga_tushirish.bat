@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Akmaljon yordamchi bot
if exist "deploy\server.conf" findstr /c:"DEPLOYED=1" "deploy\server.conf" >nul && goto :serverda
goto :yoqish

:serverda
echo.
echo  DIQQAT: bot SERVERGA o'rnatilgan va o'sha yerda ishlayapti.
echo  Kompyuterda ham yoqilsa, ikkalasi to'qnashadi va bot xato ishlaydi.
echo.
choice /c HY /m " Baribir kompyuterda yoqilsinmi? H = ha, Y = yo'q"
if errorlevel 2 exit /b

:yoqish
if not exist ".venv\Scripts\python.exe" (
  echo Virtual muhit yaratilmoqda...
  python -m venv .venv
)
echo Kutubxonalar tekshirilmoqda...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
echo Sozlash sahifasi: http://127.0.0.1:8765
".venv\Scripts\python.exe" main.py
pause
