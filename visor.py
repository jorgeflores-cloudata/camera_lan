"""Viewer for a live camera stream on the local network."""

from __future__ import annotations

import ipaddress
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from descubrimiento import CamaraLAN, buscar_camaras
from grabacion import Grabacion
from historial import ErrorHistorial, leer_recientes, registrar_conexion
from transporte import ErrorConexion, Visor
from version import __version__
from zoom import ZOOM_MAXIMO, Zoom, ajustar_dimensiones


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


def _recibir(
    sesion: _Sesion,
    destino: tuple[str, int],
    resultados_recientes: queue.SimpleQueue[list[tuple[str, int]] | None],
) -> None:
    try:
        sesion.visor.conectar()
        if not sesion.cancelada.is_set():
            sesion.eventos.put(("conectada", ""))
            try:
                resultados_recientes.put(registrar_conexion(*destino))
            except ErrorHistorial:
                resultados_recientes.put(None)

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
        self.sesion: _Sesion | None = None
        self.conectada = False
        self.pausa_solicitada = False
        self.imagen: Image.Image | None = None
        self.foto: ImageTk.PhotoImage | None = None
        self.hora_imagen = 0.0
        self.zoom = Zoom()
        self._arrastre: tuple[int, int] | None = None
        self.busqueda: _Busqueda | None = None
        self.camaras: list[CamaraLAN] = []
        self.recientes: list[tuple[str, int]] = []
        self.resultados_recientes: queue.SimpleQueue[list[tuple[str, int]] | None] = queue.SimpleQueue()
        self.grabacion: Grabacion | None = None
        self.guardando_grabacion = False
        self.cerrando = False

        self.host = tk.StringVar()
        self.puerto = tk.StringVar(value="8443")
        self.camara_elegida = tk.StringVar()
        self.calidad = tk.StringVar(value=_ETIQUETAS_CALIDAD["media"])
        self.fps = tk.StringVar(value="8")
        self.estado = tk.StringVar(value="Disconnected")
        self.efectivos = tk.StringVar(value="Applied settings · —")
        self.aviso_ajustes = tk.StringVar()
        self.estado_grabacion = tk.StringVar()
        self.estado_zoom = tk.StringVar(value="100%")

        raiz.title(f"LAN Camera — Viewer · v{__version__}")
        raiz.minsize(680, 480)
        raiz.geometry("900x630")
        raiz.protocol("WM_DELETE_WINDOW", self._salir)
        estilo = ttk.Style(raiz)
        estilo.configure("Titulo.TLabel", font=("Segoe UI", 19, "bold"))
        estilo.configure("Estado.TLabel", font=("Segoe UI", 10, "bold"))

        panel = ttk.Frame(raiz, padding=18)
        panel.pack(expand=True, fill="both")
        ttk.Label(panel, text="LAN Camera · Viewer", style="Titulo.TLabel").pack(anchor="w", pady=(0, 12))

        busqueda = ttk.Frame(panel)
        busqueda.pack(fill="x", pady=(0, 12))
        busqueda.columnconfigure(0, weight=1)
        busqueda.columnconfigure(2, weight=1)
        ttk.Label(busqueda, text="Cameras on the local network").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.selector_camaras = ttk.Combobox(busqueda, textvariable=self.camara_elegida, state="disabled")
        self.selector_camaras.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self.selector_camaras.bind("<<ComboboxSelected>>", self._elegir_camara)
        self.boton_buscar = ttk.Button(busqueda, text="Find cameras", command=self._alternar_busqueda)
        self.boton_buscar.grid(row=1, column=1, sticky="ew")

        self.etiqueta_recientes = ttk.Label(busqueda, text="Recent connections")
        self.etiqueta_recientes.grid(row=0, column=2, sticky="w", padx=(14, 0), pady=(0, 4))
        self.selector_recientes = ttk.Combobox(busqueda, state="disabled")
        self.selector_recientes.grid(row=1, column=2, sticky="ew", padx=(14, 0))
        self.selector_recientes.bind("<<ComboboxSelected>>", self._elegir_reciente)

        conexion = ttk.Frame(panel)
        conexion.pack(fill="x")
        conexion.columnconfigure(0, weight=1)
        self.entrada_host = self._campo(conexion, "Host IPv4 address", self.host, 0, width=17)
        self.entrada_puerto = self._campo(conexion, "Port", self.puerto, 1, width=7)
        self.conectar = ttk.Button(conexion, text="Connect", command=self._conectar)
        self.conectar.grid(row=1, column=2, padx=(0, 8), sticky="ew")
        self.desconectar = ttk.Button(conexion, text="Disconnect", command=self._detener)
        self.desconectar.grid(row=1, column=3, padx=(0, 8), sticky="ew")
        self.olvidar = ttk.Button(conexion, text="Forget identity", command=self._olvidar_identidad)
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
        self.lienzo.bind("<MouseWheel>", self._rueda_zoom)
        if raiz.tk.call("tk", "windowingsystem") == "x11":
            self.lienzo.bind("<Button-4>", lambda evento: self._zoom_en_punto(evento, 1))
            self.lienzo.bind("<Button-5>", lambda evento: self._zoom_en_punto(evento, -1))
        self.lienzo.bind("<ButtonPress-1>", self._iniciar_arrastre)
        self.lienzo.bind("<B1-Motion>", self._mover_arrastre)
        self.lienzo.bind("<ButtonRelease-1>", self._terminar_arrastre)

        navegacion = ttk.Frame(panel)
        navegacion.pack(fill="x", pady=(8, 0))
        self.boton_zoom_menos = ttk.Button(navegacion, text="−", width=3, command=lambda: self._cambiar_zoom(-1))
        self.boton_zoom_menos.pack(side="left")
        ttk.Label(navegacion, textvariable=self.estado_zoom, width=6, anchor="center").pack(side="left")
        self.boton_zoom_mas = ttk.Button(navegacion, text="+", width=3, command=lambda: self._cambiar_zoom(1))
        self.boton_zoom_mas.pack(side="left")
        self.boton_zoom_restaurar = ttk.Button(navegacion, text="Reset zoom", command=self._reiniciar_zoom)
        self.boton_zoom_restaurar.pack(side="left", padx=(8, 0))
        self.boton_grabar = ttk.Button(navegacion, text="Start recording", command=self._alternar_grabacion)
        self.boton_grabar.pack(side="right")

        ajustes = ttk.Frame(panel)
        ajustes.pack(fill="x", pady=(12, 4))
        self.boton_pausa = ttk.Button(ajustes, text="Request pause", command=self._alternar_pausa)
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
        self.selector_calidad.bind("<<ComboboxSelected>>", self._actualizar_ajustes)
        self.selector_fps.bind("<<ComboboxSelected>>", self._actualizar_ajustes)
        ttk.Label(panel, textvariable=self.efectivos).pack(anchor="w")
        ttk.Label(panel, textvariable=self.aviso_ajustes).pack(anchor="w")
        ttk.Label(panel, textvariable=self.estado_grabacion, wraplength=610).pack(anchor="w")
        try:
            self._mostrar_recientes(leer_recientes())
        except ErrorHistorial:
            self._mostrar_recientes(None)
        self._actualizar_controles()
        self.entrada_host.focus_set()
        raiz.after(45, self._actualizar)

    @staticmethod
    def _campo(panel, titulo, variable, columna, **opciones):
        ttk.Label(panel, text=titulo).grid(row=0, column=columna, padx=(0, 8), pady=(0, 4), sticky="w")
        entrada = ttk.Entry(panel, textvariable=variable, **opciones)
        entrada.grid(row=1, column=columna, sticky="ew", padx=(0, 8))
        return entrada

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
            self.selector_recientes.set("")
            self._seleccionar_destino(camara.ip, camara.puerto)

    def _mostrar_recientes(self, recientes: list[tuple[str, int]] | None) -> None:
        if recientes is None:
            self.etiqueta_recientes.configure(text="Recent connections unavailable")
            return
        self.etiqueta_recientes.configure(text="Recent connections")
        if recientes != self.recientes:
            self.recientes = recientes
            self.selector_recientes.configure(values=tuple(f"{ip}:{puerto}" for ip, puerto in recientes))
            self.selector_recientes.set("")

    def _elegir_reciente(self, _evento) -> None:
        indice = self.selector_recientes.current()
        if 0 <= indice < len(self.recientes):
            self.camara_elegida.set("")
            self._seleccionar_destino(*self.recientes[indice])

    def _seleccionar_destino(self, ip: str, puerto: int) -> None:
        self.host.set(ip)
        self.puerto.set(str(puerto))
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
        self._conectar()
        return "break"

    def _conectar(self) -> None:
        if self.sesion is not None or self.cerrando:
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
        threading.Thread(
            target=_recibir, args=(sesion, (ip, puerto), self.resultados_recientes), daemon=True,
        ).start()

    def _ajustes(self) -> _Ajustes:
        return _Ajustes(self.pausa_solicitada, _VALORES_CALIDAD[self.calidad.get()], int(self.fps.get()))

    def _actualizar_ajustes(self, _evento=None) -> None:
        if self.sesion is not None and self.conectada:
            with self.sesion.bloqueo:
                self.sesion.ajustes = self._ajustes()
            self.aviso_ajustes.set("")

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
        disponible = not conectando and not self.cerrando
        for control in (self.entrada_host, self.entrada_puerto, self.conectar, self.olvidar, self.boton_buscar):
            control.configure(state="normal" if disponible else "disabled")
        self.boton_buscar.configure(text="Cancel search" if buscando else "Find cameras")
        self.selector_camaras.configure(
            state="readonly" if self.camaras and disponible and not buscando else "disabled",
        )
        self.selector_recientes.configure(state="readonly" if self.recientes and disponible else "disabled")
        self.desconectar.configure(state="normal" if conectando else "disabled")
        self.boton_pausa.configure(
            state="normal" if self.conectada else "disabled",
            text="Request resume" if self.pausa_solicitada else "Request pause",
        )
        for control in (self.selector_calidad, self.selector_fps):
            control.configure(state="readonly" if self.conectada else "disabled")
        self._actualizar_boton_grabacion()
        self._actualizar_controles_zoom()

    def _actualizar_controles_zoom(self) -> None:
        porcentaje = self.zoom.porcentaje
        disponible = self.imagen is not None and self.conectada and not self.cerrando
        self.estado_zoom.set(f"{porcentaje}%")
        self.boton_zoom_menos.configure(state="normal" if disponible and porcentaje > 100 else "disabled")
        self.boton_zoom_mas.configure(state="normal" if disponible and porcentaje < ZOOM_MAXIMO else "disabled")
        self.boton_zoom_restaurar.configure(state="normal" if porcentaje > 100 and not self.cerrando else "disabled")
        self.lienzo.configure(cursor="fleur" if disponible and porcentaje > 100 else "")

    def _cambiar_zoom(self, pasos: int, ancla: tuple[float, float] = (.5, .5)) -> None:
        if self.imagen is None:
            return
        nuevo = self.zoom.cambiar(pasos, ancla)
        if nuevo != self.zoom:
            self.zoom = nuevo
            self._ajustar_lienzo()
            self._actualizar_controles_zoom()

    def _reiniciar_zoom(self) -> None:
        self.zoom = Zoom()
        self._arrastre = None
        self._ajustar_lienzo()
        self._actualizar_controles_zoom()

    def _zona_imagen(self) -> tuple[int, int, int, int] | None:
        if self.imagen is None:
            return None
        ancho, alto = self.lienzo.winfo_width(), self.lienzo.winfo_height()
        if ancho < 2 or alto < 2:
            return None
        visible_ancho, visible_alto = ajustar_dimensiones(self.imagen.size, (ancho, alto))
        return ancho // 2 - visible_ancho // 2, alto // 2 - visible_alto // 2, visible_ancho, visible_alto

    def _rueda_zoom(self, evento) -> str | None:
        if not evento.delta:
            return None
        pasos = max(1, abs(evento.delta) // 120)
        return self._zoom_en_punto(evento, pasos if evento.delta > 0 else -pasos)

    def _zoom_en_punto(self, evento, pasos: int) -> str | None:
        zona = self._zona_imagen()
        if zona is None:
            return None
        izquierda, arriba, ancho, alto = zona
        x = (evento.x - izquierda) / ancho
        y = (evento.y - arriba) / alto
        self._cambiar_zoom(pasos, (x, y) if 0 <= x <= 1 and 0 <= y <= 1 else (.5, .5))
        return "break"

    def _iniciar_arrastre(self, evento) -> None:
        zona = self._zona_imagen()
        if zona is None or self.zoom.porcentaje == 100:
            return
        izquierda, arriba, ancho, alto = zona
        if izquierda <= evento.x < izquierda + ancho and arriba <= evento.y < arriba + alto:
            self._arrastre = (evento.x, evento.y)

    def _mover_arrastre(self, evento) -> None:
        if self._arrastre is None:
            return
        zona = self._zona_imagen()
        if zona is None:
            return
        _, _, ancho, alto = zona
        x, y = self._arrastre
        self._arrastre = (evento.x, evento.y)
        nuevo = self.zoom.desplazar((evento.x - x) / ancho, (evento.y - y) / alto)
        if nuevo != self.zoom:
            self.zoom = nuevo
            self._ajustar_lienzo()

    def _terminar_arrastre(self, _evento) -> None:
        self._arrastre = None

    def _actualizar_boton_grabacion(self) -> None:
        if self.guardando_grabacion:
            self.boton_grabar.configure(text="Saving…", state="disabled")
        elif self.grabacion is not None:
            self.boton_grabar.configure(text="Stop recording", state="normal")
        else:
            disponible = self.conectada and self.imagen is not None and not self.cerrando
            self.boton_grabar.configure(text="Start recording", state="normal" if disponible else "disabled")

    def _alternar_grabacion(self) -> None:
        if self.cerrando or self.guardando_grabacion:
            return
        if self.grabacion is not None:
            self._finalizar_grabacion()
            return
        sesion = self.sesion
        if (
            sesion is None or not self.conectada or self.imagen is None
            or time.monotonic() - self.hora_imagen > _SEGUNDOS_SIN_IMAGEN
        ):
            return

        archivo = filedialog.asksaveasfilename(
            parent=self.raiz,
            title="Save recording",
            initialfile=datetime.now().strftime("LAN Camera %Y-%m-%d %H-%M-%S.mp4"),
            defaultextension=".mp4",
            filetypes=(("MP4 video", "*.mp4"),),
        )
        if not archivo:
            return
        if (
            self.cerrando or self.sesion is not sesion or not self.conectada or self.imagen is None
            or time.monotonic() - self.hora_imagen > _SEGUNDOS_SIN_IMAGEN
        ):
            self.estado_grabacion.set("Recording could not start because live video is unavailable.")
            return
        try:
            self.grabacion = Grabacion(Path(archivo), self.zoom.aplicar(self.imagen))
        except ValueError as error:
            self.estado_grabacion.set(str(error))
            return
        except RuntimeError:
            self.estado_grabacion.set("Could not start recording.")
            return
        self.estado_grabacion.set("● Recording")
        self._actualizar_boton_grabacion()

    def _finalizar_grabacion(self) -> None:
        if self.grabacion is not None and not self.guardando_grabacion:
            self.guardando_grabacion = True
            self.grabacion.finalizar()
            self.estado_grabacion.set("Saving recording…")
            self._actualizar_boton_grabacion()

    def _actualizar_grabacion(self) -> None:
        if self.grabacion is None:
            return
        resultado = self.grabacion.resultado
        if resultado is None:
            return

        self.grabacion = None
        self.guardando_grabacion = False
        self.estado_grabacion.set(resultado.error or f"Recording saved to {resultado.ruta}")
        self._actualizar_boton_grabacion()
        if resultado.error is not None and self.cerrando:
            messagebox.showerror("Recording could not be saved", resultado.error, parent=self.raiz)

    def _detener(self, mensaje: str = "Disconnected") -> None:
        self._finalizar_grabacion()
        sesion = self.sesion
        self.sesion = None
        self.conectada = False
        self.pausa_solicitada = False
        self.zoom = Zoom()
        if sesion is not None:
            sesion.cancelada.set()
            sesion.visor.cerrar()
        self.estado.set(mensaje)
        self.efectivos.set("Applied settings · —")
        self.aviso_ajustes.set("")
        self._mostrar_mensaje("Connect to view live video")
        self._actualizar_controles()

    def _actualizar(self) -> None:
        self._actualizar_grabacion()
        if self.cerrando and self.grabacion is None:
            self.raiz.destroy()
            return
        self._actualizar_busqueda()
        while True:
            try:
                recientes = self.resultados_recientes.get_nowait()
            except queue.Empty:
                break
            self._mostrar_recientes(recientes)
            self._actualizar_controles()
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
            nueva_imagen = self.imagen is None
            self.imagen = imagen
            self.hora_imagen = recibida
            self._ajustar_lienzo()
            if nueva_imagen:
                self._actualizar_boton_grabacion()
                self._actualizar_controles_zoom()
            if self.grabacion is not None and not self.guardando_grabacion:
                self.grabacion.agregar(self.zoom.aplicar(imagen))
                if nueva_imagen:
                    self.estado_grabacion.set("● Recording")
        else:
            self.estado.set("Connected · waiting for video")
            self._mostrar_mensaje("Waiting for a recent frame")

    def _mostrar_mensaje(self, texto: str) -> None:
        if self.grabacion is not None and not self.guardando_grabacion:
            self.grabacion.pausar()
            self.estado_grabacion.set("Recording paused · waiting for video")
        self.imagen = None
        self.foto = None
        self._arrastre = None
        self.lienzo.itemconfigure(self.objeto_imagen, image="")
        self.lienzo.itemconfigure(self.objeto_mensaje, text=texto, state="normal")
        self._ajustar_lienzo()
        self._actualizar_boton_grabacion()
        self._actualizar_controles_zoom()

    def _ajustar_lienzo(self, _evento=None) -> None:
        ancho, alto = self.lienzo.winfo_width(), self.lienzo.winfo_height()
        self.lienzo.coords(self.objeto_imagen, ancho // 2, alto // 2)
        self.lienzo.coords(self.objeto_mensaje, ancho // 2, alto // 2)
        self.lienzo.itemconfigure(self.objeto_mensaje, width=max(1, ancho - 28))
        if self.imagen is not None and ancho > 1 and alto > 1:
            dimensiones = ajustar_dimensiones(self.imagen.size, (ancho, alto))
            vista = self.zoom.aplicar(self.imagen, dimensiones)
            self.foto = ImageTk.PhotoImage(vista, master=self.raiz)
            self.lienzo.itemconfigure(self.objeto_imagen, image=self.foto)
            self.lienzo.itemconfigure(self.objeto_mensaje, state="hidden")

    def _salir(self) -> None:
        if self.cerrando:
            return
        self.cerrando = True
        self._cancelar_busqueda()
        self._detener()
        if self.grabacion is None:
            self.raiz.destroy()


def ejecutar() -> None:
    raiz = tk.Tk()
    _Panel(raiz)
    raiz.mainloop()


if __name__ == "__main__":
    import sys

    from camara_lan import main

    raise SystemExit(main(["view", *sys.argv[1:]]))
