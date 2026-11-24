"""Share or view a camera on the local network."""

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
        raise _ErrorInstancia(error, "Could not open the application lock.")
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
    ventana.title(f"LAN Camera · v{__version__}")
    ventana.resizable(False, False)
    panel = ttk.Frame(ventana, padding=24)
    panel.grid()
    ttk.Label(panel, text="LAN Camera", font=("Segoe UI", 18, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
    )
    modo = None

    def elegir(opcion: str) -> None:
        nonlocal modo
        modo = opcion
        ventana.destroy()

    ttk.Button(panel, text="Share camera", command=lambda: elegir("share")).grid(
        row=1, column=0, padx=(0, 8)
    )
    ttk.Button(panel, text="View and control", command=lambda: elegir("view")).grid(
        row=1, column=1
    )
    ventana.mainloop()
    return modo


def _ejecutar_modo(modo: str | None) -> int:
    modo = modo or elegir_modo()
    if modo is None:
        return 0
    if modo == "share" and sys.platform != "win32":
        messagebox.showerror("LAN Camera", "Sharing a camera requires Windows 11.")
        return 1

    try:
        if modo == "share":
            from anfitrion import ejecutar
        else:
            from visor import ejecutar
    except ImportError:
        messagebox.showerror(
            "LAN Camera",
            r"Some dependencies are missing. On Windows, run Launchers\Setup.cmd first.",
        )
        return 1

    ejecutar()
    return 0


def main(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"LAN Camera {__version__}")
    alias = {"compartir": "share", "ver": "view"}
    parser.add_argument(
        "mode", choices=("share", "view"), nargs="?", type=lambda value: alias.get(value, value),
        help="open the selected mode",
    )
    modo = parser.parse_args(argumentos).mode

    try:
        with _instancia_unica() as disponible:
            if disponible:
                return _ejecutar_modo(modo)
            messagebox.showwarning(
                "LAN Camera",
                "LAN Camera is already running. Use the existing window. "
                "If the sharing window is hidden, open it from the system tray.",
            )
            return 0
    except _ErrorInstancia as error:
        messagebox.showerror(
            "LAN Camera", f"Could not check if LAN Camera is already running (Windows error {error.errno}).",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
