"""Viewer for a live camera stream on the local network."""

from __future__ import annotations

import ipaddress
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from io import BytesIO
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from acceso import Acceso
from descubrimiento import CamaraLAN, buscar_camaras
from transporte import ErrorConexion, Visor
from version import __version__


_RED_LOCAL = tuple(
    ipaddress.IPv4Network(red)
    for red in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_ETIQUETAS_CALIDAD = {"baja": "Low", "media": "Medium", "alta": "High"}
_VALORES_CALIDAD = {etiqueta: valor for valor, etiqueta in _ETIQUETAS_CALIDAD.items()}
_COLOR_FONDO = "#100c19"
_COLOR_TEXTO = "#eee8f6"
_MAXIMO_PIXELES = 4_194_304
_SEGUNDOS_SIN_IMAGEN = 5


@dataclass(frozen=True)
class _Ajustes:
    pausa: bool = False
    calidad: str = "media"
    fps: int = 8


@dataclass
class _Sesion:
    visor: Visor
    ajustes: _Ajustes
    bloqueo: threading.Lock = field(default_factory=threading.Lock)
    cancelada: threading.Event = field(default_factory=threading.Event)
    eventos: queue.SimpleQueue[tuple[str, str]] = field(default_factory=queue.SimpleQueue)
    ultimo: tuple[Image.Image | None, dict, float, _Ajustes] | None = None


@dataclass
class _Busqueda:
    cancelada: threading.Event = field(default_factory=threading.Event)
    resultado: queue.SimpleQueue[list[CamaraLAN] | None] = field(default_factory=queue.SimpleQueue)


def _direccion(texto: str) -> str:
    try:
        direccion = ipaddress.IPv4Address(texto.strip())
    except ipaddress.AddressValueError:
        raise ValueError("Enter the local IPv4 address shown on the host.") from None

    if not any(direccion in red for red in _RED_LOCAL):
        raise ValueError("The address must be on the host's private network.")
    return str(direccion)


def _decodificar(jpeg: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(jpeg), formats=["JPEG"]) as imagen:
            if imagen.width * imagen.height > _MAXIMO_PIXELES:
                raise ValueError
            return imagen.convert("RGB")
    except (Image.DecompressionBombError, OSError, ValueError):
        raise ErrorConexion("The host sent an image that could not be displayed.") from None


def _recibir(sesion: _Sesion) -> None:
    try:
        sesion.visor.conectar()
        if not sesion.cancelada.is_set():
            sesion.eventos.put(("conectada", ""))

        while not sesion.cancelada.is_set():
            with sesion.bloqueo:
                ajustes = sesion.ajustes

            inicio = time.monotonic()
            jpeg, efectivos = sesion.visor.leer(ajustes.pausa, ajustes.calidad, ajustes.fps)
            pausada = efectivos.get("pausa", False)
            imagen = _decodificar(jpeg) if jpeg is not None and not pausada else None

            with sesion.bloqueo:
                if not sesion.cancelada.is_set():
                    sesion.ultimo = (imagen, efectivos, time.monotonic(), ajustes)
            fps = efectivos.get("fps", ajustes.fps)
            if type(fps) is not int or not 1 <= fps <= 15:
                fps = ajustes.fps
            if pausada:
                fps = min(fps, 4)
            demora = 1 / fps - (time.monotonic() - inicio)
            if demora > 0:
                sesion.cancelada.wait(demora)
    except ErrorConexion as error:
        if not sesion.cancelada.is_set():
            sesion.eventos.put(("cerrada", str(error)))
    except Exception:
        if not sesion.cancelada.is_set():
            sesion.eventos.put(("cerrada", "The connection to the host was interrupted."))
    finally:
        sesion.visor.cerrar()


def _buscar_en_red(busqueda: _Busqueda) -> None:
    try:
        camaras = buscar_camaras(busqueda.cancelada)
    except OSError:
        camaras = None
    busqueda.resultado.put(camaras)


class _Panel:
    def __init__(self, raiz: tk.Tk) -> None:
        self.raiz = raiz
        self.acceso = Acceso(raiz)
        self.sesion: _Sesion | None = None
        self.conectada = False
        self.pausa_solicitada = False
        self.imagen: Image.Image | None = None
        self.foto: ImageTk.PhotoImage | None = None
        self.hora_imagen = 0.0
        self.busqueda: _Busqueda | None = None
        self.camaras: list[CamaraLAN] = []

        self.host = tk.StringVar()
        self.puerto = tk.StringVar(value="8443")
        self.camara_elegida = tk.StringVar()
        self.calidad = tk.StringVar(value=_ETIQUETAS_CALIDAD["media"])
        self.fps = tk.StringVar(value="8")
        self.estado = tk.StringVar(value="Disconnected")
        self.efectivos = tk.StringVar(value="Applied settings · —")
        self.aviso_ajustes = tk.StringVar()

        raiz.title(f"LAN Camera — Viewer · v{__version__}")
        raiz.minsize(680, 480)
        raiz.geometry("900x630")
        raiz.protocol("WM_DELETE_WINDOW", lambda: self.acceso.solicitar(self._salir, "Close viewer"))
        estilo = ttk.Style(raiz)
        estilo.configure("Titulo.TLabel", font=("Segoe UI", 19, "bold"))
        estilo.configure("Estado.TLabel", font=("Segoe UI", 10, "bold"))

        panel = ttk.Frame(raiz, padding=18)
        panel.pack(expand=True, fill="both")
        ttk.Label(panel, text="LAN Camera · Viewer", style="Titulo.TLabel").pack(anchor="w", pady=(0, 12))

        busqueda = ttk.Frame(panel)
        busqueda.pack(fill="x", pady=(0, 12))
        busqueda.columnconfigure(0, weight=1)
        ttk.Label(busqueda, text="Cameras on the local network").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.selector_camaras = ttk.Combobox(busqueda, textvariable=self.camara_elegida, state="disabled")
        self.selector_camaras.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self.selector_camaras.bind("<<ComboboxSelected>>", self._elegir_camara)
        self.boton_buscar = ttk.Button(busqueda, text="Find cameras", command=self._pedir_busqueda)
        self.boton_buscar.grid(row=1, column=1, sticky="ew")

        conexion = ttk.Frame(panel)
        conexion.pack(fill="x")
        conexion.columnconfigure(0, weight=1)
        self.entrada_host = self._campo(conexion, "Host IPv4 address", self.host, 0, width=17)
        self.entrada_puerto = self._campo(conexion, "Port", self.puerto, 1, width=7)
        self.conectar = ttk.Button(conexion, text="Connect", command=self._pedir_conexion)
        self.conectar.grid(row=1, column=2, padx=(0, 8), sticky="ew")
        self.desconectar = ttk.Button(
            conexion, text="Disconnect", command=lambda: self.acceso.solicitar(self._detener, "Disconnect"),
        )
        self.desconectar.grid(row=1, column=3, padx=(0, 8), sticky="ew")
        self.olvidar = ttk.Button(
            conexion, text="Forget identity", command=lambda: self.acceso.solicitar(self._olvidar_identidad, "Forget identity"),
        )
        self.olvidar.grid(row=1, column=4, sticky="ew")
        for entrada in (self.entrada_host, self.entrada_puerto):
            entrada.bind("<Return>", self._conectar_desde_teclado)

        ttk.Label(panel, textvariable=self.estado, style="Estado.TLabel", wraplength=640).pack(anchor="w", pady=10)
        self.lienzo = tk.Canvas(panel, bg=_COLOR_FONDO, highlightthickness=0, takefocus=False)
        self.lienzo.pack(expand=True, fill="both")
        self.objeto_imagen = self.lienzo.create_image(0, 0)
        self.objeto_mensaje = self.lienzo.create_text(
            0, 0, text="Connect to view live video", fill=_COLOR_TEXTO,
            justify="center", font=("Segoe UI", 13)
        )
        self.lienzo.bind("<Configure>", self._ajustar_lienzo)

        ajustes = ttk.Frame(panel)
        ajustes.pack(fill="x", pady=(12, 4))
        self.boton_pausa = ttk.Button(ajustes, text="Request pause", command=self._pedir_pausa)
        self.boton_pausa.pack(side="left", padx=(0, 14))
        ttk.Label(ajustes, text="Quality").pack(side="left", padx=(0, 5))
        self.selector_calidad = ttk.Combobox(
            ajustes, textvariable=self.calidad, values=tuple(_ETIQUETAS_CALIDAD.values()), width=7
        )
        self.selector_calidad.pack(side="left", padx=(0, 14))
        ttk.Label(ajustes, text="FPS").pack(side="left", padx=(0, 5))
        self.selector_fps = ttk.Combobox(
            ajustes, textvariable=self.fps, values=tuple(str(valor) for valor in range(1, 16)), width=3
        )
        self.selector_fps.pack(side="left")
        self.selector_calidad.bind("<<ComboboxSelected>>", self._pedir_ajustes)
        self.selector_fps.bind("<<ComboboxSelected>>", self._pedir_ajustes)
        ttk.Button(ajustes, text="Change PIN", command=self.acceso.cambiar).pack(side="right")
        ttk.Label(panel, textvariable=self.efectivos).pack(anchor="w")
        ttk.Label(panel, textvariable=self.aviso_ajustes).pack(anchor="w")
        self._actualizar_controles()
        self.entrada_host.focus_set()
        raiz.after(45, self._actualizar)

    @staticmethod
    def _campo(panel, titulo, variable, columna, **opciones):
        ttk.Label(panel, text=titulo).grid(row=0, column=columna, padx=(0, 8), pady=(0, 4), sticky="w")
        entrada = ttk.Entry(panel, textvariable=variable, **opciones)
        entrada.grid(row=1, column=columna, sticky="ew", padx=(0, 8))
        return entrada

    def _pedir_busqueda(self) -> None:
        busqueda = self.busqueda

        def alternar() -> None:
            if self.busqueda is busqueda:
                self._alternar_busqueda()

        self.acceso.solicitar(alternar, "Find cameras" if busqueda is None else "Cancel search")

    def _alternar_busqueda(self) -> None:
        if self.sesion is not None:
            return
        if self.busqueda is not None:
            self._cancelar_busqueda()
            self.estado.set("Search canceled.")
        else:
            self.busqueda = _Busqueda()
            self.estado.set("Searching for cameras on the local network…")
            threading.Thread(target=_buscar_en_red, args=(self.busqueda,), daemon=True).start()
        self._actualizar_controles()

    def _cancelar_busqueda(self) -> None:
        if self.busqueda is not None:
            self.busqueda.cancelada.set()
            self.busqueda = None

    def _actualizar_busqueda(self) -> None:
        if self.busqueda is None:
            return
        try:
            camaras = self.busqueda.resultado.get_nowait()
        except queue.Empty:
            return
        self.busqueda = None
        if camaras is None:
            self.estado.set("Network search failed. Enter the host's IPv4 address and port.")
        else:
            self.camaras = camaras
            self.selector_camaras.configure(
                values=tuple(f"{camara.nombre} ({camara.ip}:{camara.puerto})" for camara in camaras),
            )
            self.camara_elegida.set("")
            self.estado.set(
                "Choose a camera from the list or enter the host's IPv4 address and port."
                if camaras else "No cameras found. Enter the host's IPv4 address and port."
            )
        self._actualizar_controles()

    def _elegir_camara(self, _evento) -> None:
        indice = self.selector_camaras.current()
        if 0 <= indice < len(self.camaras):
            camara = self.camaras[indice]
            self.host.set(camara.ip)
            self.puerto.set(str(camara.puerto))
            self.conectar.focus_set()

    def _destino(self) -> tuple[str, int]:
        ip = _direccion(self.host.get())
        try:
            puerto = int(self.puerto.get())
        except ValueError:
            puerto = 0
        if not 1 <= puerto <= 65_535:
            raise ValueError("Enter a port number between 1 and 65535.")
        return ip, puerto

    def _olvidar_identidad(self) -> None:
        if self.sesion is not None:
            return
        try:
            ip, puerto = self._destino()
        except ValueError as error:
            self.estado.set(str(error))
            return
        if not messagebox.askyesno(
            "Forget identity",
            f"Forget the saved identity for {ip}:{puerto}? "
            "Verify that this is the camera's address before reconnecting.",
            parent=self.raiz,
            default="no",
        ):
            return

        self._cancelar_busqueda()
        try:
            Visor.olvidar(ip, puerto)
        except ErrorConexion as error:
            self.estado.set(str(error))
        except OSError:
            self.estado.set("Could not forget the saved camera identity.")
        else:
            self.estado.set("Camera identity forgotten. You can reconnect.")
        self._actualizar_controles()

    def _conectar_desde_teclado(self, _evento) -> str:
        self._pedir_conexion()
        return "break"

    def _pedir_conexion(self) -> None:
        self.acceso.solicitar(self._conectar, "Connect")

    def _conectar(self) -> None:
        if self.sesion is not None:
            return

        try:
            ip, puerto = self._destino()
            visor = Visor(ip, puerto)
        except (ValueError, ErrorConexion) as error:
            self.estado.set(str(error))
            return

        self._cancelar_busqueda()
        self.pausa_solicitada = False
        sesion = _Sesion(visor, self._ajustes())
        self.sesion = sesion
        self.estado.set(f"Connecting to {ip}:{puerto}…")
        self._mostrar_mensaje("Connecting…")
        self._actualizar_controles()
        threading.Thread(target=_recibir, args=(sesion,), daemon=True).start()

    def _ajustes(self) -> _Ajustes:
        return _Ajustes(self.pausa_solicitada, _VALORES_CALIDAD[self.calidad.get()], int(self.fps.get()))

    def _mostrar_ajustes_solicitados(self, ajustes: _Ajustes) -> None:
        self.calidad.set(_ETIQUETAS_CALIDAD[ajustes.calidad])
        self.fps.set(str(ajustes.fps))

    def _pedir_ajustes(self, _evento=None) -> None:
        sesion = self.sesion
        if sesion is None or not self.conectada:
            return
        nuevos = self._ajustes()
        with sesion.bloqueo:
            actuales = sesion.ajustes
        self._mostrar_ajustes_solicitados(actuales)
        if nuevos == actuales:
            return

        def aplicar() -> None:
            if self.sesion is sesion and self.conectada:
                self._mostrar_ajustes_solicitados(nuevos)
                self._actualizar_ajustes()

        self.acceso.solicitar(aplicar, "Change settings")

    def _actualizar_ajustes(self) -> None:
        if self.sesion is not None and self.conectada:
            with self.sesion.bloqueo:
                self.sesion.ajustes = self._ajustes()
            self.aviso_ajustes.set("")

    def _pedir_pausa(self) -> None:
        titulo = "Request resume" if self.pausa_solicitada else "Request pause"
        self.acceso.solicitar(self._alternar_pausa, titulo)

    def _alternar_pausa(self) -> None:
        if self.sesion is None or not self.conectada:
            return
        self.pausa_solicitada = not self.pausa_solicitada
        self._actualizar_ajustes()
        self._actualizar_controles()
        if self.pausa_solicitada:
            self.estado.set("Requesting a pause")
            self._mostrar_mensaje("Waiting for the host to pause")

    def _actualizar_controles(self) -> None:
        conectando = self.sesion is not None
        buscando = self.busqueda is not None
        for control in (self.entrada_host, self.entrada_puerto, self.conectar, self.olvidar, self.boton_buscar):
            control.configure(state="disabled" if conectando else "normal")
        self.boton_buscar.configure(text="Cancel search" if buscando else "Find cameras")
        self.selector_camaras.configure(
            state="readonly" if self.camaras and not conectando and not buscando else "disabled",
        )
        self.desconectar.configure(state="normal" if conectando else "disabled")
        self.boton_pausa.configure(
            state="normal" if self.conectada else "disabled",
            text="Request resume" if self.pausa_solicitada else "Request pause",
        )
        for control in (self.selector_calidad, self.selector_fps):
            control.configure(state="readonly" if self.conectada else "disabled")

    def _detener(self, mensaje: str = "Disconnected") -> None:
        sesion = self.sesion
        self.sesion = None
        self.conectada = False
        self.pausa_solicitada = False
        if sesion is not None:
            sesion.cancelada.set()
            sesion.visor.cerrar()
        self.estado.set(mensaje)
        self.efectivos.set("Applied settings · —")
        self.aviso_ajustes.set("")
        self._mostrar_mensaje("Connect to view live video")
        self._actualizar_controles()

    def _actualizar(self) -> None:
        self._actualizar_busqueda()
        sesion = self.sesion
        if sesion is not None:
            while True:
                try:
                    evento, mensaje = sesion.eventos.get_nowait()
                except queue.Empty:
                    break
                if evento == "cerrada":
                    self._detener(mensaje)
                    break
                self.conectada = True
                self.estado.set("Connected")
                self._mostrar_mensaje("Waiting for the first frame")
                self._actualizar_controles()

            if self.sesion is sesion and self.conectada:
                with sesion.bloqueo:
                    ultimo = sesion.ultimo
                    sesion.ultimo = None
                if ultimo is not None:
                    self._mostrar_resultado(*ultimo)
                if self.imagen is not None and time.monotonic() - self.hora_imagen > _SEGUNDOS_SIN_IMAGEN:
                    self.estado.set("Connected · no recent frames")
                    self._mostrar_mensaje("No recent frames")

        self.raiz.after(45, self._actualizar)

    def _mostrar_resultado(
        self, imagen: Image.Image | None, efectivos: dict, recibida: float, solicitud: _Ajustes
    ) -> None:
        if self.pausa_solicitada and not solicitud.pausa:
            return

        pausada = efectivos.get("pausa", False)
        calidad = efectivos.get("calidad", "—")
        fps = efectivos.get("fps", "—")
        etiqueta_calidad = _ETIQUETAS_CALIDAD.get(calidad, "—")
        self.efectivos.set(f"Applied settings · {etiqueta_calidad} quality · {fps} FPS")
        if solicitud == self._ajustes():
            distintos = (pausada, calidad, fps) != (solicitud.pausa, solicitud.calidad, solicitud.fps)
            self.aviso_ajustes.set("The host is using different settings than requested." if distintos else "")
        if pausada:
            self.estado.set("Stream paused")
            self._mostrar_mensaje("Stream paused")
        elif imagen is not None and time.monotonic() - recibida <= _SEGUNDOS_SIN_IMAGEN:
            self.estado.set("Live")
            self.imagen = imagen
            self.hora_imagen = recibida
            self._ajustar_lienzo()
        else:
            self.estado.set("Connected · waiting for video")
            self._mostrar_mensaje("Waiting for a recent frame")

    def _mostrar_mensaje(self, texto: str) -> None:
        self.imagen = None
        self.foto = None
        self.lienzo.itemconfigure(self.objeto_imagen, image="")
        self.lienzo.itemconfigure(self.objeto_mensaje, text=texto, state="normal")
        self._ajustar_lienzo()

    def _ajustar_lienzo(self, _evento=None) -> None:
        ancho, alto = self.lienzo.winfo_width(), self.lienzo.winfo_height()
        self.lienzo.coords(self.objeto_imagen, ancho // 2, alto // 2)
        self.lienzo.coords(self.objeto_mensaje, ancho // 2, alto // 2)
        self.lienzo.itemconfigure(self.objeto_mensaje, width=max(1, ancho - 28))
        if self.imagen is not None and ancho > 1 and alto > 1:
            escala = min(ancho / self.imagen.width, alto / self.imagen.height)
            dimensiones = (max(1, int(self.imagen.width * escala)), max(1, int(self.imagen.height * escala)))
            reducida = self.imagen.resize(dimensiones, Image.Resampling.BILINEAR)
            self.foto = ImageTk.PhotoImage(reducida, master=self.raiz)
            self.lienzo.itemconfigure(self.objeto_imagen, image=self.foto)
            self.lienzo.itemconfigure(self.objeto_mensaje, state="hidden")

    def _salir(self) -> None:
        self._cancelar_busqueda()
        self._detener()
        self.raiz.destroy()


def ejecutar() -> None:
    raiz = tk.Tk()
    _Panel(raiz)
    raiz.mainloop()


if __name__ == "__main__":
    import sys

    from camara_lan import main

    raise SystemExit(main(["view", *sys.argv[1:]]))
