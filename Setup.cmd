@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto :environment

set "LAN_CAMERA_PY=py -3"
%LAN_CAMERA_PY% -c "import sys" >nul 2>&1
if errorlevel 1 (
    set "LAN_CAMERA_PY=python"
    python -c "import sys" >nul 2>&1
    if errorlevel 1 goto :missing_python
)
call :check_python
if errorlevel 1 goto :failed
%LAN_CAMERA_PY% -m venv .venv
if errorlevel 1 goto :failed_environment

:environment
set "LAN_CAMERA_PY=.venv\Scripts\python.exe"
call :check_python
if errorlevel 1 goto :failed
%LAN_CAMERA_PY% --version
%LAN_CAMERA_PY% -m pip install --disable-pip-version-check --only-binary=:all: -r requirements.txt
if errorlevel 1 goto :failed_packages
%LAN_CAMERA_PY% -c "import cv2, cryptography, PIL, pystray"
if errorlevel 1 goto :failed_import
echo Setup complete. Open Share.cmd or View.cmd.
echo Press any key to continue...
pause >nul
exit /b 0

:check_python
%LAN_CAMERA_PY% -c "import sys, sysconfig; sys.exit(not (sys.implementation.name == 'cpython' and sys.version_info[:2] in ((3, 12), (3, 13), (3, 14), (3, 15)) and sysconfig.get_platform() == 'win-amd64' and not sysconfig.get_config_var('Py_GIL_DISABLED')))"
if errorlevel 1 (
    echo LAN Camera requires standard Python 3.12 to 3.15 for Windows x64.
    echo If the existing environment stopped working after a Python change, rename .venv and run Setup.cmd again.
    exit /b 1
)
%LAN_CAMERA_PY% -c "import tkinter; window = tkinter.Tk(); window.withdraw(); window.destroy()"
if errorlevel 1 (
    echo The selected Python installation could not load Tkinter or Tcl/Tk.
    exit /b 1
)
exit /b 0

:missing_python
echo Python was not found through py or python. Install Python for Windows x64.
goto :failed

:failed_environment
echo Could not create .venv. Check the error above.
goto :failed

:failed_packages
echo Could not install the dependencies. Check the pip output above.
goto :failed

:failed_import
echo The dependencies were installed, but Python could not load them. Check the error above.

:failed
echo Press any key to continue...
pause >nul
exit /b 1
