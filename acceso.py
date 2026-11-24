"""Local PIN checks and dialogs for LAN Camera."""

from collections.abc import Callable
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from math import ceil
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
import time
import tkinter as tk
from tkinter import ttk

from transporte import _datos_locales


_PIN_INICIAL = "0000"
_CABECERA = b"LAN Camera PIN v1\n"
_BYTES_SAL = 16
_BYTES_HASH = 32
_LONGITUD_REGISTRO = len(_CABECERA) + _BYTES_SAL + _BYTES_HASH
_ITERACIONES = 400_000
_MAX_INTENTOS = 5
_MENSAJE_FORMATO = "Enter a PIN with 4 to 12 digits (0–9)."


class _ErrorPIN(Exception):
    """An error the local dialog can show without revealing a PIN."""


def _comprobar_formato(pin: str) -> None:
    if not (4 <= len(pin) <= 12 and pin.isascii() and pin.isdecimal()):
        raise _ErrorPIN(_MENSAJE_FORMATO)


def _huella(pin: str, sal: bytes) -> bytes:
    return pbkdf2_hmac("sha256", pin.encode("ascii"), sal, _ITERACIONES, dklen=_BYTES_HASH)


class _PIN:
    def __init__(self, ruta: Path):
        self._ruta = ruta
        self._fallos = 0
        self._bloqueado_hasta = 0.0

    def verificar(self, pin: str) -> None:
        restante = ceil(self._bloqueado_hasta - time.monotonic())
        if restante > 0:
            raise _ErrorPIN(self._mensaje_espera(restante))

        _comprobar_formato(pin)
        if self._coincide(pin):
            self._fallos = 0
            self._bloqueado_hasta = 0.0
            return

        self._fallos += 1
        if self._fallos >= _MAX_INTENTOS:
            segundos = min(30, 2 ** min(self._fallos - _MAX_INTENTOS + 1, 5))
            self._bloqueado_hasta = time.monotonic() + segundos
            raise _ErrorPIN(self._mensaje_espera(segundos))
        raise _ErrorPIN("Incorrect PIN.")

    def cambiar(self, actual: str, nuevo: str, confirmacion: str) -> None:
        _comprobar_formato(nuevo)
        if nuevo != confirmacion:
            raise _ErrorPIN("The new PINs do not match.")
        self.verificar(actual)

        sal = token_bytes(_BYTES_SAL)
        registro = _CABECERA + sal + _huella(nuevo, sal)
        try:
            self._ruta.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with TemporaryDirectory(dir=self._ruta.parent) as temporal:
                candidato = Path(temporal, self._ruta.name)
                candidato.write_bytes(registro)
                candidato.replace(self._ruta)
        except OSError as error:
            raise _ErrorPIN("Could not save the new PIN. Check the local settings folder.") from error

    def _coincide(self, pin: str) -> bool:
        try:
            with self._ruta.open("rb") as archivo:
                registro = archivo.read(_LONGITUD_REGISTRO + 1)
        except FileNotFoundError:
            if not self._ruta.is_symlink():
                return compare_digest(pin, _PIN_INICIAL)
            raise _ErrorPIN("Could not read the saved PIN. Check the local settings folder.") from None
        except OSError as error:
            raise _ErrorPIN("Could not read the saved PIN. Check the local settings folder.") from error
        if len(registro) != _LONGITUD_REGISTRO or not registro.startswith(_CABECERA):
            raise _ErrorPIN("The saved PIN is invalid. Restore the PIN settings file from a backup.")
        inicio = len(_CABECERA)
        limite = inicio + _BYTES_SAL
        return compare_digest(_huella(pin, registro[inicio:limite]), registro[limite:])

    @staticmethod
    def _mensaje_espera(segundos: int) -> str:
        return f"Too many incorrect attempts. Try again in {segundos} seconds."


class Acceso:
    """Runs one local PIN dialog at a time for a Tk window."""

    def __init__(self, raiz: tk.Tk):
        self._raiz = raiz
        self._pin = _PIN(_datos_locales() / "local-pin.bin")
        self._ventana: tk.Toplevel | None = None

    def solicitar(self, accion: Callable[[], None], titulo: str) -> tk.Toplevel:
        return self._dialogo(titulo, ("PIN",), "Unlock", self._pin.verificar, accion)

    def cambiar(self) -> tk.Toplevel:
        return self._dialogo(
            "Change PIN", ("Current PIN", "New PIN", "Confirm new PIN"), "Save PIN", self._pin.cambiar,
        )

    def _dialogo(
        self, titulo: str, campos: tuple[str, ...], texto_boton: str,
        confirmar: Callable[..., None], accion: Callable[[], None] | None = None,
    ) -> tk.Toplevel:
        if self._ventana is not None and self._ventana.winfo_exists():
            self._ventana.lift()
            self._ventana.focus_force()
            return self._ventana

        ventana = tk.Toplevel(self._raiz)
        self._ventana = ventana
        ventana.title(titulo)
        ventana.resizable(False, False)
        if self._raiz.winfo_viewable():
            ventana.transient(self._raiz)

        panel = ttk.Frame(ventana, padding=20)
        panel.grid(sticky="nsew")
        entradas = []
        for campo in campos:
            ttk.Label(panel, text=campo).pack(anchor="w")
            entrada = ttk.Entry(panel, show="*", width=24)
            entrada.pack(fill="x", pady=(2, 8))
            entradas.append(entrada)

        aviso = ttk.Label(panel, foreground="#a32938", wraplength=280)
        aviso.pack(fill="x", pady=(0, 8))

        def cancelar():
            self._ventana = None
            ventana.destroy()

        def aceptar():
            try:
                confirmar(*(entrada.get() for entrada in entradas))
            except _ErrorPIN as error:
                aviso.configure(text=str(error))
                entradas[0].selection_range(0, tk.END)
                entradas[0].focus_set()
                return
            cancelar()
            if accion is not None:
                accion()

        botones = ttk.Frame(panel)
        botones.pack(anchor="e")
        ttk.Button(botones, text="Cancel", command=cancelar).pack(side="right")
        ttk.Button(botones, text=texto_boton, command=aceptar).pack(side="right", padx=(0, 6))
        ventana.protocol("WM_DELETE_WINDOW", cancelar)
        ventana.bind("<Escape>", lambda _evento: cancelar())
        ventana.bind("<Return>", lambda _evento: aceptar())
        ventana.grab_set()
        entradas[0].focus_set()
        return ventana


def autorizar_inicio() -> bool:
    """Prompts for the PIN before any application window is shown."""
    raiz = tk.Tk()
    raiz.withdraw()
    autorizado = False

    def continuar():
        nonlocal autorizado
        autorizado = True

    try:
        ventana = Acceso(raiz).solicitar(continuar, "Unlock LAN Camera")
        raiz.wait_window(ventana)
        return autorizado
    finally:
        raiz.destroy()
