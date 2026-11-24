@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run Launchers\Setup.cmd first.
    echo Press any key to continue...
    pause >nul
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check "pyinstaller>=6.11,<7"
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onefile --hidden-import pystray._win32 --name CamaraLAN camara_lan.py
if errorlevel 1 goto :failed
echo Built dist\CamaraLAN.exe. Open it to choose a mode.
echo Press any key to continue...
pause >nul
exit /b 0

:failed
echo Could not build LAN Camera.
echo Press any key to continue...
pause >nul
exit /b 1
