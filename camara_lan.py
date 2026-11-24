"""Entrada para los modos Compartir y Ver de Cámara LAN."""

import argparse
import sys
import tkinter as tk
from tkinter import messagebox, ttk


def elegir_modo() -> str | None:
    ventana = tk.Tk()
    ventana.title("Cámara LAN")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modo", choices=("compartir", "ver"), nargs="?")
    modo = parser.parse_args().modo or elegir_modo()
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


if __name__ == "__main__":
    raise SystemExit(main())
