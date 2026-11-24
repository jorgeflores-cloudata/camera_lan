@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto :entorno

set "CAMARA_PY=py -3"
%CAMARA_PY% -c "import sys" >nul 2>&1
if errorlevel 1 (
    set "CAMARA_PY=python"
    python -c "import sys" >nul 2>&1
    if errorlevel 1 goto :sin_python
)
call :comprobar_python
if errorlevel 1 goto :fallo
%CAMARA_PY% -m venv .venv
if errorlevel 1 goto :fallo_entorno

:entorno
set "CAMARA_PY=.venv\Scripts\python.exe"
call :comprobar_python
if errorlevel 1 goto :fallo
%CAMARA_PY% --version
%CAMARA_PY% -m pip install --disable-pip-version-check --only-binary=:all: -r requirements.txt
if errorlevel 1 goto :fallo_paquetes
%CAMARA_PY% -c "import cv2, cryptography, PIL, pystray"
if errorlevel 1 goto :fallo_importacion
echo Listo. Abre Compartir.cmd o Ver.cmd.
pause
exit /b 0

:comprobar_python
%CAMARA_PY% -c "import sys, sysconfig; sys.exit(not (sys.implementation.name == 'cpython' and sys.version_info[:2] in ((3, 12), (3, 13), (3, 14), (3, 15)) and sysconfig.get_platform() == 'win-amd64' and not sysconfig.get_config_var('Py_GIL_DISABLED')))"
if errorlevel 1 (
    echo Se requiere Python estandar de 3.12 a 3.15 para Windows x64.
    echo Si cambiaste de Python y el entorno anterior ya no funciona, renombra la carpeta .venv y vuelve a ejecutar Preparar.cmd.
    exit /b 1
)
%CAMARA_PY% -c "import tkinter; ventana = tkinter.Tk(); ventana.withdraw(); ventana.destroy()"
if errorlevel 1 (
    echo La instalacion de Python seleccionada no pudo cargar Tkinter o Tcl/Tk.
    exit /b 1
)
exit /b 0

:sin_python
echo No se encontro Python con los comandos py o python. Instala Python para Windows x64.
goto :fallo

:fallo_entorno
echo No se pudo crear el entorno .venv. Revisa el error que aparece arriba.
goto :fallo

:fallo_paquetes
echo No se pudieron instalar las dependencias. Revisa el mensaje de pip que aparece arriba.
goto :fallo

:fallo_importacion
echo Las dependencias se instalaron, pero no se pudieron cargar. Revisa el mensaje que aparece arriba.

:fallo
pause
exit /b 1
