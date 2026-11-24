@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Run Setup.cmd first.
    echo Press any key to continue...
    pause >nul
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0camara_lan.py" share
