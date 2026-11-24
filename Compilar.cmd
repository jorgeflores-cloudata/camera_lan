@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Ejecuta primero Preparar.cmd.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check "pyinstaller>=6.11,<7"
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onefile --hidden-import pystray._win32 --name CamaraLAN camara_lan.py
if errorlevel 1 goto :error
echo Generado dist\CamaraLAN.exe. Abre el programa para seleccionar el modo.
pause
exit /b 0

:error
echo No se pudo compilar Camara LAN.
pause
exit /b 1
