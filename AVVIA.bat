@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title Simulatore Mindsmith - AVVIA
cd /d "%~dp0"
if /i "%~1"=="gui" (
  python app.py
) else (
  python avvia.py
)
echo.
echo Finito. Premi un tasto per chiudere.
pause >nul
