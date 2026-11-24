"""Entrada para los modos Compartir y Ver de Cámara LAN."""

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from version import __version__


_NOMBRE_MUTEX = r"Local\CamaraLAN.2dd601eb-3d8d-43af-85a4-7c6debbbadd7"
_ERROR_YA_EXISTE = 183


class _ErrorInstancia(OSError):
    """No se pudo comprobar la exclusividad de la aplicación."""


@contextmanager
def _instancia_unica(nombre: str = _NOMBRE_MUTEX) -> Iterator[bool]:
    if sys.platform != "win32":
        yield True
        return

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    crear = kernel.CreateMutexW
    crear.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    crear.restype = wintypes.HANDLE
    cerrar = kernel.CloseHandle
    cerrar.argtypes = (wintypes.HANDLE,)
    cerrar.restype = wintypes.BOOL

    ctypes.set_last_error(0)
    mutex = crear(None, False, nombre)
    error = ctypes.get_last_error()
    if not mutex:
        raise _ErrorInstancia(str(ctypes.WinError(error)))
    if error == _ERROR_YA_EXISTE:
        cerrar(mutex)
        yield False
        return

    try:
        yield True
    finally:
        cerrar(mutex)


def elegir_modo() -> str | None:
    ventana = tk.Tk()
    ventana.title(f"Cámara LAN · v{__version__}")
    ventana.resizable(False, False)
    panel = ttk.Frame(ventana, padding=24)
    panel.grid()
    ttk.Label(panel, text="Cámara LAN", font=("Segoe UI", 18, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
    )
    modo = None

    def elegir(opcion):
        nonlocal modo
        modo = opcion
        ventana.destroy()

    ttk.Button(panel, text="Compartir cámara", command=lambda: elegir("compartir")).grid(
        row=1, column=0, padx=(0, 8)
    )
    ttk.Button(panel, text="Ver y controlar", command=lambda: elegir("ver")).grid(
        row=1, column=1
    )
    ventana.mainloop()
    return modo


def _ejecutar_modo(modo: str | None) -> int:
    modo = modo or elegir_modo()
    if modo is None:
        return 0
    if modo == "compartir" and sys.platform != "win32":
        messagebox.showerror("Cámara LAN", "El modo Compartir requiere Windows 11.")
        return 1

    try:
        if modo == "compartir":
            from anfitrion import ejecutar
        else:
            from visor import ejecutar
    except ImportError:
        messagebox.showerror(
            "Cámara LAN",
            "Faltan dependencias. En Windows, ejecuta primero Preparar.cmd.",
        )
        return 1

    ejecutar()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"Cámara LAN {__version__}")
    parser.add_argument("modo", choices=("compartir", "ver"), nargs="?")
    modo = parser.parse_args().modo

    try:
        with _instancia_unica() as disponible:
            if disponible:
                return _ejecutar_modo(modo)
            messagebox.showwarning(
                "Cámara LAN",
                "Cámara LAN ya está abierta. Usa la ventana existente. "
                "Si Compartir está oculto, ábrelo desde el icono junto al reloj.",
            )
            return 0
    except _ErrorInstancia as error:
        messagebox.showerror("Cámara LAN", f"No se pudo comprobar si Cámara LAN ya está abierta.\n\n{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
