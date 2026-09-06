@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title Generatore lezioni - Pannello di controllo
cd /d "%~dp0"
if /i "%~1"=="gui" (
  python app.py
) else (
  python panel.py
)
echo.
echo Finito. Premi un tasto per chiudere.
pause >nul
