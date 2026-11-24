"""Ventana local que controla cuándo puede compartirse la cámara."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import errno
from functools import lru_cache
from io import BytesIO
import ipaddress
import os
from queue import Empty, SimpleQueue
import socket
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
from PIL import Image, ImageDraw, ImageTk

from descubrimiento import AnuncioLAN
from transporte import ErrorConexion, Servidor
from version import __version__


_CALIDADES = {
    "baja": (426, 240, 55),
    "media": (640, 360, 70),
    "alta": (960, 540, 82),
}
_ETIQUETAS_CALIDAD = {"Low": "baja", "Medium": "media", "High": "alta"}
_TIEMPO_APROBACION = 30


@lru_cache(maxsize=1)
def _apis_escritorio():
    if os.name != "nt":
        return None
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel.ProcessIdToSessionId.restype = wintypes.BOOL
        kernel.WTSGetActiveConsoleSessionId.argtypes = []
        kernel.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
        user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        user.OpenInputDesktop.restype = wintypes.HANDLE
        user.GetUserObjectInformationW.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        user.GetUserObjectInformationW.restype = wintypes.BOOL
        user.CloseDesktop.argtypes = [wintypes.HANDLE]
        user.CloseDesktop.restype = wintypes.BOOL
        return kernel, user
    except (AttributeError, OSError):
        return None


def _escritorio_disponible() -> bool:
    """Acepta solo el escritorio de entrada normal del equipo físico."""
    apis = _apis_escritorio()
    if apis is None:
        return False
    kernel, user = apis
    try:
        sesion = wintypes.DWORD()
        if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(sesion)):
            return False
        if sesion.value != kernel.WTSGetActiveConsoleSessionId():
            return False

        escritorio = user.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if not escritorio:
            return False
        try:
            nombre = ctypes.create_unicode_buffer(256)
            requerido = wintypes.DWORD()
            return bool(
                user.GetUserObjectInformationW(
                    escritorio, 2, nombre, ctypes.sizeof(nombre), ctypes.byref(requerido)
                ) and nombre.value.casefold() == "default"  # UOI_NAME
            )
        finally:
            user.CloseDesktop(escritorio)
    except (AttributeError, OSError):
        return False


def _direcciones_locales() -> list[str]:
    try:
        _, _, direcciones = socket.gethostbyname_ex(socket.gethostname())
    except OSError:
        return []

    return sorted({ip for ip in direcciones if not ipaddress.IPv4Address(ip).is_loopback})


def _icono(activo: bool) -> Image.Image:
    imagen = Image.new("RGBA", (64, 64))
    dibujo = ImageDraw.Draw(imagen)
    dibujo.rounded_rectangle((2, 2, 62, 62), 15, fill="#ae343b" if activo else "#435d86")
    dibujo.rounded_rectangle((13, 19, 43, 45), 5, fill="white")
    dibujo.polygon(((42, 25), (51, 20), (51, 44), (42, 39)), fill="white")
    return imagen


@dataclass
class _Solicitud:
    ip: str
    cancelada: threading.Event
    vence: float
    respondida: threading.Event = field(default_factory=threading.Event)
    aceptada: bool = False


@dataclass
class _Control:
    pausa: bool
    calidad: str
    fps: int
    vence: float
    respondido: threading.Event = field(default_factory=threading.Event)
    respuesta: dict | None = None


class _Captura:
    """La misma hebra abre, lee y libera la cámara; comparte una sola imagen."""

    def __init__(self, dispositivo: int, calidad: str, fps: int):
        self._dispositivo = dispositivo
        self._calidad = calidad
        self._fps = fps
        self._detener = threading.Event()
        self._lock = threading.Lock()
        self._ultimo: bytes | None = None
        self.error: str | None = None
        self._hilo = threading.Thread(target=self._ejecutar, daemon=True)
        self._hilo.start()

    def viva(self) -> bool:
        return self._hilo.is_alive()

    def detener(self) -> None:
        self._detener.set()

    def ajustar(self, calidad: str, fps: int) -> None:
        with self._lock:
            self._calidad = calidad
            self._fps = fps

    def tomar_fotograma(self) -> bytes | None:
        with self._lock:
            imagen, self._ultimo = self._ultimo, None
            return imagen

    def _ejecutar(self) -> None:
        camara = None
        try:
            if self._detener.is_set():
                return
            camara = cv2.VideoCapture(self._dispositivo, cv2.CAP_DSHOW)
            if not camara.isOpened() and not self._detener.is_set():
                camara.release()
                camara = cv2.VideoCapture(self._dispositivo)
            if self._detener.is_set():
                return
            if not camara.isOpened():
                self.error = "Windows could not open the camera. Check the device and its permissions."
                return

            camara.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
            camara.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)
            while not self._detener.is_set():
                inicio = time.monotonic()
                with self._lock:
                    calidad, fps = self._calidad, self._fps

                ok, imagen = camara.read()
                if self._detener.is_set():
                    return
                if not ok or imagen is None:
                    self.error = "The camera stopped delivering frames."
                    return

                ancho, alto, nivel = _CALIDADES[calidad]
                alto_real, ancho_real = imagen.shape[:2]
                escala = min(1, ancho / ancho_real, alto / alto_real)
                if escala < 1:
                    imagen = cv2.resize(
                        imagen, (max(1, int(ancho_real * escala)), max(1, int(alto_real * escala))),
                        interpolation=cv2.INTER_AREA,
                    )

                ok, jpeg = cv2.imencode(".jpg", imagen, [cv2.IMWRITE_JPEG_QUALITY, nivel])
                if not ok:
                    self.error = "Could not prepare the camera image."
                    return
                with self._lock:
                    self._ultimo = jpeg.tobytes()
                self._detener.wait(max(0, 1 / fps - (time.monotonic() - inicio)))
        except (cv2.error, OSError):
            self.error = "The camera is unavailable."
        finally:
            if camara is not None:
                camara.release()


class _Anfitrion:
    def __init__(self, raiz: tk.Tk):
        self.raiz = raiz
        self.bandeja = None
        self.eventos: SimpleQueue = SimpleQueue()
        self.generacion = 0
        self.servidor: Servidor | None = None
        self.anuncio: AnuncioLAN | None = None
        self.solicitud: _Solicitud | None = None
        self.captura: _Captura | None = None
        self.captura_cerrando: _Captura | None = None
        self.autorizada = False
        self.cancelacion_activa: threading.Event | None = None
        self.en_vivo = False
        self.salir = False
        self._minimizada_en_bandeja = False
        self.calidad_actual = "media"
        self.calidad_maxima = "media"
        self.fps_actual = 10
        self.fps_maximos = 10
        self.pausa_local = False
        self.pausa_remota = False
        self.permite_control = False
        self.indice_camara = 0
        self.foto: ImageTk.PhotoImage | None = None
        self.fotograma_recibido = False
        self.ultima_comprobacion = 0.0
        self.limite_fotograma: float | None = None

        direcciones = _direcciones_locales()
        self.ip = tk.StringVar(value=direcciones[0] if len(direcciones) == 1 else "")
        self.puerto = tk.StringVar(value="8443")
        self.camara = tk.StringVar(value="0")
        self.calidad = tk.StringVar(value="Medium")
        self.fps = tk.StringVar(value=str(self.fps_maximos))
        self.ajustes_remotos = tk.BooleanVar(value=False)
        self.estado = tk.StringVar(value="Camera off")
        self.texto_solicitud = tk.StringVar(value="")

        self._construir(direcciones)
        raiz.protocol("WM_DELETE_WINDOW", self._ocultar)
        raiz.bind("<Escape>", lambda _evento: self._dejar_de_compartir())
        raiz.bind("<Unmap>", self._al_ocultar)
        raiz.after(35, self._bucle)

    def _construir(self, direcciones: list[str]) -> None:
        self.raiz.title(f"LAN Camera · Share · v{__version__}")
        self.raiz.minsize(660, 670)
        self.raiz.geometry("720x750")
        self.raiz.configure(background="#f0f4fa")

        estilo = ttk.Style(self.raiz)
        estilo.configure("Host.TFrame", background="#f0f4fa")
        estilo.configure("Host.TLabel", background="#f0f4fa", foreground="#1a2940")
        estilo.configure("Titulo.Host.TLabel", font=("Segoe UI", 20, "bold"))
        estilo.configure("Host.TCheckbutton", background="#f0f4fa")

        panel = ttk.Frame(self.raiz, padding=22, style="Host.TFrame")
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Share camera", style="Titulo.Host.TLabel").pack(anchor="w")
        self.indicador = tk.Label(
            panel, textvariable=self.estado, anchor="w", padx=10, pady=8,
            background="#e0e8f5", foreground="#1a2940", font=("Segoe UI", 11, "bold"),
        )
        self.indicador.pack(fill="x", pady=(8, 12))

        ajustes = ttk.Frame(panel, style="Host.TFrame")
        ajustes.pack(fill="x")
        ajustes.columnconfigure(1, weight=1)

        def campo(fila, titulo, control):
            ttk.Label(ajustes, text=titulo, style="Host.TLabel").grid(
                row=fila, column=0, sticky="w", padx=(0, 12), pady=3,
            )
            control.grid(row=fila, column=1, sticky="ew", pady=3)
            return control

        self.campos = [
            (campo(0, "This device's IPv4 address", ttk.Combobox(ajustes, textvariable=self.ip, values=direcciones)), "normal"),
            (campo(1, "Port", ttk.Entry(ajustes, textvariable=self.puerto)), "normal"),
            (campo(2, "Camera index", ttk.Spinbox(ajustes, textvariable=self.camara, from_=0, to=15)), "normal"),
            (campo(3, "Maximum quality", ttk.Combobox(
                ajustes, textvariable=self.calidad, values=tuple(_ETIQUETAS_CALIDAD), state="readonly",
            )), "readonly"),
            (campo(4, "Maximum frame rate (FPS)", ttk.Spinbox(ajustes, textvariable=self.fps, from_=1, to=15)), "normal"),
        ]
        control = ttk.Checkbutton(
            ajustes, variable=self.ajustes_remotos, style="Host.TCheckbutton",
            text="Allow the viewer to pause and adjust quality and frame rate",
        )
        control.grid(row=5, column=0, columnspan=2, sticky="w", pady=(5, 0))
        self.campos.append((control, "normal"))

        self.panel_solicitud = ttk.Frame(panel, padding=(0, 8), style="Host.TFrame")
        ttk.Label(
            self.panel_solicitud, textvariable=self.texto_solicitud,
            style="Host.TLabel", wraplength=570,
        ).pack(anchor="w")
        acciones = ttk.Frame(self.panel_solicitud, style="Host.TFrame")
        acciones.pack(anchor="w", pady=(4, 0))
        ttk.Button(acciones, text="Accept and share", command=lambda: self._responder(True)).pack(side="left")
        ttk.Button(acciones, text="Decline", command=lambda: self._responder(False)).pack(side="left", padx=5)

        botones = ttk.Frame(panel, style="Host.TFrame")
        botones.pack(fill="x", pady=(10, 8))
        self.habilitar = ttk.Button(botones, text="Enable connection", command=self._habilitar)
        self.habilitar.pack(side="left")
        self.pausar = ttk.Button(botones, text="Pause camera", command=self._alternar_pausa, state="disabled")
        self.pausar.pack(side="left", padx=6)
        self.detener = ttk.Button(botones, text="Close connection", command=self._cerrar_conexion, state="disabled")
        self.detener.pack(side="left")
        ttk.Button(botones, text="Minimize to tray", command=self._minimizar_a_bandeja).pack(side="right")

        self.area_previa = tk.Frame(panel, bg="#203047")
        self.area_previa.pack(fill="both", expand=True)
        self.previa = tk.Label(
            self.area_previa, text="Preview · camera off",
            bg="#203047", fg="white", font=("Segoe UI", 11),
        )
        self.previa.place(relx=.5, rely=.5, anchor="center")

    def _habilitar(self) -> None:
        if self.servidor is not None:
            return
        if not self._puede_compartir():
            messagebox.showerror("LAN Camera", "Open this window from the unlocked local desktop.")
            return
        try:
            ip = str(ipaddress.IPv4Address(self.ip.get().strip()))
            puerto = int(self.puerto.get())
            self.indice_camara = int(self.camara.get())
            self.fps_maximos = int(self.fps.get())
            self.calidad_maxima = _ETIQUETAS_CALIDAD[self.calidad.get()]
            if not (1 <= puerto <= 65535 and 0 <= self.indice_camara <= 15 and 1 <= self.fps_maximos <= 15):
                raise ValueError
        except (ValueError, KeyError):
            messagebox.showerror("LAN Camera", "Check the IPv4 address, port, camera index, quality, and frame rate.")
            return

        self.permite_control = self.ajustes_remotos.get()
        self.generacion += 1
        generacion = self.generacion
        try:
            servidor = Servidor(
                ip, puerto,
                lambda ip_visora, cancelada: self._solicitar(generacion, ip_visora, cancelada),
                lambda pausa, calidad, fps: self._control(generacion, pausa, calidad, fps),
                lambda: self.eventos.put(("terminar", generacion, None)),
            )
            servidor.iniciar()
        except ErrorConexion as error:
            messagebox.showerror("LAN Camera", str(error))
            return
        except OSError as error:
            codigo_error = getattr(error, "winerror", None) or error.errno
            if error.errno == errno.EADDRINUSE or codigo_error == 10048:
                mensaje = (
                    f"Port {puerto} is already in use. If another version of LAN Camera is open, "
                    "choose Exit from its tray icon. You can also use a different port on both devices."
                )
            else:
                mensaje = f"Could not listen on port {puerto}."
                if codigo_error is not None:
                    mensaje += f" System error {codigo_error}."
            messagebox.showerror("LAN Camera", mensaje)
            return

        self.servidor = servidor
        anuncio = AnuncioLAN(ip, puerto)
        try:
            anuncio.iniciar()
        except OSError:
            anuncio.cerrar()
        else:
            self.anuncio = anuncio
        self.calidad_actual = self.calidad_maxima
        self.fps_actual = self.fps_maximos
        self._refrescar_captura()
        self._mostrar_espera()
        self._botones()
        if self.bandeja is not None:
            self.bandeja.update_menu()

    def _mostrar_espera(self) -> None:
        if self.solicitud is not None:
            conexion = "Request pending"
        elif self.anuncio is None:
            conexion = "Waiting for a request by IP address and port"
        else:
            conexion = "Available on the LAN"

        if self.fotograma_recibido:
            actividad = "camera on" if self._minimizada_en_bandeja else "local preview"
            self._estado(f"{conexion} · {actividad}", True)
        elif self.captura is not None:
            self._estado(f"{conexion} · opening camera", True)
        elif self.pausa_local:
            self._estado(f"{conexion} · camera paused")
        else:
            self._estado(f"{conexion} · camera off")

    def _solicitar(self, generacion: int, ip: str, cancelada: threading.Event) -> bool:
        solicitud = _Solicitud(ip, cancelada, time.monotonic() + _TIEMPO_APROBACION)
        self.eventos.put(("solicitud", generacion, solicitud))
        while not solicitud.respondida.wait(.1):
            if cancelada.is_set() or time.monotonic() >= solicitud.vence:
                return False
        return solicitud.aceptada and not cancelada.is_set() and time.monotonic() < solicitud.vence

    def _control(self, generacion: int, pausa: bool, calidad: str, fps: int) -> dict:
        solicitud = _Control(pausa, calidad, fps, time.monotonic() + 2)
        self.eventos.put(("control", generacion, solicitud))
        if not solicitud.respondido.wait(2) or solicitud.respuesta is None:
            raise ErrorConexion("The host could not apply the requested settings.")
        return solicitud.respuesta

    def _mostrar_solicitud(self, solicitud: _Solicitud) -> None:
        if solicitud.cancelada.is_set() or self.solicitud is not None or self.autorizada or not _escritorio_disponible():
            solicitud.respondida.set()
            return
        if time.monotonic() >= solicitud.vence:
            solicitud.respondida.set()
            return

        self.solicitud = solicitud
        self.texto_solicitud.set(f"Device {solicitud.ip} is requesting to view the camera.")
        self.panel_solicitud.pack(fill="x", before=self.pausar.master)
        self._mostrar_espera()
        if self._ventana_visible():
            self.raiz.lift()
        self.raiz.bell()

    def _responder(self, aceptar: bool) -> None:
        solicitud, self.solicitud = self.solicitud, None
        self.panel_solicitud.pack_forget()
        if solicitud is None:
            return
        if aceptar and self.servidor is not None and not solicitud.cancelada.is_set() and time.monotonic() < solicitud.vence:
            solicitud.aceptada = self._puede_compartir()

        if solicitud.aceptada:
            self.autorizada = True
            self.cancelacion_activa = solicitud.cancelada
            self.pausa_remota = False
            self.calidad_actual = self.calidad_maxima
            self.fps_actual = self.fps_maximos
            if self.pausa_local:
                self._estado("Session paused · camera off")
            else:
                self._estado("Session approved · starting stream", True)
        elif self.servidor is not None:
            self._mostrar_espera()
        solicitud.respondida.set()
        self._botones()

    def _aplicar_control(self, solicitud: _Control) -> None:
        if not self.autorizada or time.monotonic() >= solicitud.vence:
            solicitud.respondido.set()
            return
        if not self._puede_capturar():
            self._dejar_de_compartir("Session ended when the local desktop became unavailable")
            solicitud.respondido.set()
            return

        if self.permite_control:
            self.pausa_remota = solicitud.pausa
            if solicitud.calidad in _CALIDADES:
                calidades = tuple(_CALIDADES)
                self.calidad_actual = calidades[min(
                    calidades.index(solicitud.calidad), calidades.index(self.calidad_maxima),
                )]
            self.fps_actual = max(1, min(solicitud.fps, self.fps_maximos))
            self._refrescar_captura()

        solicitud.respuesta = {
            "pausa": self._en_pausa(), "calidad": self.calidad_actual, "fps": self.fps_actual,
        }
        solicitud.respondido.set()

    def _alternar_pausa(self) -> None:
        if self.servidor is not None:
            self.pausa_local = not self.pausa_local
            self._refrescar_captura()
            self._botones()

    def _en_pausa(self) -> bool:
        return self.pausa_local or self.pausa_remota

    def _refrescar_captura(self) -> None:
        if self.servidor is None:
            return
        if self._en_pausa():
            if self.captura is not None:
                self._detener_captura()
            if self.autorizada:
                self._estado("Session paused · camera off")
            else:
                self._mostrar_espera()
        elif self.captura is not None:
            self.captura.ajustar(self.calidad_actual, self.fps_actual)
        elif self.captura_cerrando is None or not self.captura_cerrando.viva():
            if not self._puede_capturar():
                self._suspender_captura("Local desktop unavailable · camera off")
                return
            if self.autorizada and (self.cancelacion_activa is None or self.cancelacion_activa.is_set()):
                self._dejar_de_compartir("Session ended · camera off")
                return
            self.captura_cerrando = None
            self.captura = _Captura(self.indice_camara, self.calidad_actual, self.fps_actual)
            self.limite_fotograma = time.monotonic() + 10
            if self.autorizada:
                self._estado("Opening camera for the approved session", True)
            else:
                self._mostrar_espera()

    def _detener_captura(self) -> None:
        if self.captura is not None:
            self.captura.detener()
            self.captura_cerrando = self.captura
            self.captura = None
        if self.servidor is not None and self.autorizada:
            self.servidor.publicar(None)
        self.limite_fotograma = None
        self.foto = None
        self.fotograma_recibido = False
        self.previa.configure(image="", text="Preview · camera off")

    def _sesion_terminada(self) -> None:
        if self.autorizada:
            self._dejar_de_compartir("Viewer disconnected · camera off")
        else:
            self._responder(False)

    def _cerrar_conexion(self) -> None:
        self._dejar_de_compartir(f"Connection closed · port {self.puerto.get()} released")

    def _dejar_de_compartir(self, estado: str = "Camera off") -> None:
        self.generacion += 1
        anuncio, self.anuncio = self.anuncio, None
        if anuncio is not None:
            anuncio.cerrar()
        servidor, self.servidor = self.servidor, None
        if servidor is not None:
            servidor.cerrar()
        self._detener_captura()
        self._responder(False)
        self.autorizada = False
        self.cancelacion_activa = None
        self.pausa_local = False
        self.pausa_remota = False
        self._estado(estado)
        self._botones()
        if servidor is not None and self.bandeja is not None:
            self.bandeja.update_menu()

    def _estado(self, texto: str, activo: bool = False) -> None:
        if self.estado.get() == texto and self.en_vivo == activo:
            return
        self.estado.set(texto)
        self.indicador.configure(
            bg="#9f2f3a" if activo else "#e0e8f5",
            fg="white" if activo else "#1a2940",
        )
        if self.bandeja is not None:
            if self.en_vivo != activo:
                self.bandeja.icon = _icono(activo)
            self.bandeja.title = f"LAN Camera · {texto}"
        self.en_vivo = activo

    def _botones(self) -> None:
        preparado = self.servidor is not None
        for campo, normal in self.campos:
            campo.configure(state="disabled" if preparado else normal)
        self.habilitar.configure(state="disabled" if preparado else "normal")
        self.detener.configure(state="normal" if preparado else "disabled")
        self.pausar.configure(
            state="normal" if preparado else "disabled",
            text="Resume camera" if self.pausa_local else "Pause camera",
        )

    def _puede_compartir(self) -> bool:
        return self._ventana_visible() and _escritorio_disponible()

    def _puede_capturar(self) -> bool:
        return (self._minimizada_en_bandeja or self._ventana_visible()) and _escritorio_disponible()

    def _ventana_visible(self) -> bool:
        return self.raiz.state() in ("normal", "zoomed")

    def _al_ocultar(self, evento) -> None:
        if evento.widget is self.raiz and self.raiz.state() == "iconic":
            self._minimizar_a_bandeja()

    def _suspender_si_oculta(self) -> None:
        self._suspender_captura("Window closed · camera off")

    def _suspender_captura(self, estado: str) -> None:
        if self.servidor is None:
            return
        if self.autorizada:
            self._dejar_de_compartir(estado)
            return

        # Keep the camera paused until the local user resumes it.
        if not self.pausa_local:
            self.pausa_local = True
            self._detener_captura()
            self._botones()
        if self.solicitud is not None:
            self._responder(False)
        self._mostrar_espera()

    def _ocultar(self) -> None:
        self._minimizada_en_bandeja = False
        self._suspender_si_oculta()
        self.raiz.withdraw()

    def _minimizar_a_bandeja(self) -> None:
        self._minimizada_en_bandeja = True
        self.raiz.withdraw()
        if self.servidor is not None and not self.autorizada:
            self._mostrar_espera()

    def _abrir(self) -> None:
        self.raiz.deiconify()
        self._minimizada_en_bandeja = False
        self.raiz.lift()

    def _salir(self) -> None:
        self.salir = True
        self._dejar_de_compartir()
        if self.bandeja is not None:
            self.bandeja.stop()
        self.raiz.destroy()

    def _mostrar_fotograma(self) -> None:
        if self.servidor is None or self.captura is None:
            return
        if not self._puede_capturar():
            self._suspender_captura("Local desktop unavailable · camera off")
            return
        if self.autorizada and (self.cancelacion_activa is None or self.cancelacion_activa.is_set()):
            self._dejar_de_compartir("Session ended · camera off")
            return
        if self.captura.error:
            self._dejar_de_compartir(self.captura.error)
            return

        jpeg = self.captura.tomar_fotograma()
        if jpeg is None:
            if self.limite_fotograma is not None and time.monotonic() >= self.limite_fotograma:
                self._dejar_de_compartir("The camera did not respond in time.")
            return

        self.limite_fotograma = time.monotonic() + 10
        self.fotograma_recibido = True
        if self._ventana_visible():
            with Image.open(BytesIO(jpeg)) as cuadro:
                espacio = (max(1, self.area_previa.winfo_width() - 8), max(1, self.area_previa.winfo_height() - 8))
                cuadro.thumbnail(espacio, Image.Resampling.BILINEAR)
                self.foto = ImageTk.PhotoImage(cuadro)
            self.previa.configure(image=self.foto, text="")
        if self.autorizada:
            self.servidor.publicar(jpeg)
            self._estado("Sharing live video", True)
        else:
            self._mostrar_espera()

    def _bucle(self) -> None:
        for _ in range(20):
            try:
                accion, generacion, datos = self.eventos.get_nowait()
            except Empty:
                break

            if accion == "abrir":
                self._abrir()
            elif accion == "salir":
                self._salir()
                return
            elif generacion != self.generacion or self.servidor is None:
                if accion == "solicitud":
                    datos.respondida.set()
                elif accion == "control":
                    datos.respondido.set()
            elif accion == "solicitud":
                self._mostrar_solicitud(datos)
            elif accion == "control":
                self._aplicar_control(datos)
            elif accion == "terminar":
                self._sesion_terminada()
            elif accion == "cerrar_conexion":
                self._cerrar_conexion()

        ahora = time.monotonic()
        if self.servidor is not None and ahora - self.ultima_comprobacion >= .35:
            self.ultima_comprobacion = ahora
            if not _escritorio_disponible():
                self._suspender_captura("Desktop locked · camera off")
            elif not self._minimizada_en_bandeja and not self._ventana_visible() and (
                self.autorizada or not self.pausa_local
            ):
                self._suspender_si_oculta()

        if self.solicitud is not None:
            if self.solicitud.cancelada.is_set() or ahora >= self.solicitud.vence:
                self._responder(False)

        if self.servidor is not None and not self._en_pausa():
            self._refrescar_captura()
            self._mostrar_fotograma()

        if not self.salir:
            self.raiz.after(35, self._bucle)


def ejecutar() -> None:
    import pystray

    raiz = tk.Tk()
    anfitrion = _Anfitrion(raiz)
    anfitrion.bandeja = pystray.Icon(
        "CamaraLAN", _icono(False), "LAN Camera · camera off",
        pystray.Menu(
            pystray.MenuItem("Open", lambda *_: anfitrion.eventos.put(("abrir", None, None)), default=True),
            pystray.MenuItem(
                "Close connection",
                lambda *_: anfitrion.eventos.put(("cerrar_conexion", anfitrion.generacion, None)),
                enabled=lambda _: anfitrion.servidor is not None,
            ),
            pystray.MenuItem("Exit", lambda *_: anfitrion.eventos.put(("salir", None, None))),
        ),
    )
    try:
        anfitrion.bandeja.run_detached()
        raiz.mainloop()
    finally:
        if anfitrion.anuncio is not None:
            anfitrion.anuncio.cerrar()
        if anfitrion.servidor is not None:
            anfitrion.servidor.cerrar()
        if anfitrion.captura is not None:
            anfitrion.captura.detener()
        anfitrion.bandeja.stop()
