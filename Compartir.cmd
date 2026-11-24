@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Ejecuta primero Preparar.cmd.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0camara_lan.py" compartir
