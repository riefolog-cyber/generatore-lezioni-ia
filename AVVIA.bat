@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title Generatore lezioni - Pannello di controllo
cd /d "%~dp0"
python --version >nul 2>&1
if errorlevel 1 (
  echo ERRORE: Python non trovato nel PATH. Installa Python 3.10+ da python.org
  echo e riavvia questo file.
  pause >nul
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creo ambiente virtuale .venv una tantum...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt -r requirements-extra.txt
) else (
  call .venv\Scripts\activate.bat
)
if /i "%~1"=="gui" (
  python app.py
) else (
  python panel.py
)
echo.
echo Finito. Premi un tasto per chiudere.
pause >nul
