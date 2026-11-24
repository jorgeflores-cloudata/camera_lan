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
                self.error = "Windows no pudo abrir esa cámara. Revisa el dispositivo y sus permisos."
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
                    self.error = "La cámara dejó de entregar imágenes."
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
                    self.error = "No se pudo preparar la imagen de la cámara."
                    return
                with self._lock:
                    self._ultimo = jpeg.tobytes()
                self._detener.wait(max(0, 1 / fps - (time.monotonic() - inicio)))
        except (cv2.error, OSError):
            self.error = "La cámara no está disponible."
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
        self.calidad_actual = "media"
        self.calidad_maxima = "media"
        self.fps_actual = 10
        self.fps_maximos = 10
        self.pausa_local = False
        self.pausa_remota = False
        self.permite_control = False
        self.indice_camara = 0
        self.foto: ImageTk.PhotoImage | None = None
        self.ultima_comprobacion = 0.0
        self.limite_fotograma: float | None = None

        direcciones = _direcciones_locales()
        self.ip = tk.StringVar(value=direcciones[0] if len(direcciones) == 1 else "")
        self.puerto = tk.StringVar(value="8443")
        self.camara = tk.StringVar(value="0")
        self.calidad = tk.StringVar(value=self.calidad_maxima)
        self.fps = tk.StringVar(value=str(self.fps_maximos))
        self.ajustes_remotos = tk.BooleanVar(value=False)
        self.estado = tk.StringVar(value="Cámara apagada")
        self.texto_solicitud = tk.StringVar(value="")

        self._construir(direcciones)
        raiz.protocol("WM_DELETE_WINDOW", self._ocultar)
        raiz.bind("<Escape>", lambda _evento: self._dejar_de_compartir())
        raiz.bind("<Unmap>", self._al_ocultar)
        raiz.after(35, self._bucle)

    def _construir(self, direcciones: list[str]) -> None:
        self.raiz.title(f"Cámara LAN · Compartir · v{__version__}")
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
        ttk.Label(panel, text="Compartir cámara", style="Titulo.Host.TLabel").pack(anchor="w")
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
            (campo(0, "IPv4 de este equipo", ttk.Combobox(ajustes, textvariable=self.ip, values=direcciones)), "normal"),
            (campo(1, "Puerto", ttk.Entry(ajustes, textvariable=self.puerto)), "normal"),
            (campo(2, "Índice de cámara", ttk.Spinbox(ajustes, textvariable=self.camara, from_=0, to=15)), "normal"),
            (campo(3, "Calidad máxima", ttk.Combobox(
                ajustes, textvariable=self.calidad, values=tuple(_CALIDADES), state="readonly",
            )), "readonly"),
            (campo(4, "Imágenes por segundo (máximo)", ttk.Spinbox(ajustes, textvariable=self.fps, from_=1, to=15)), "normal"),
        ]
        control = ttk.Checkbutton(
            ajustes, variable=self.ajustes_remotos, style="Host.TCheckbutton",
            text="Permitir al visor cambiar pausa, calidad y velocidad",
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
        ttk.Button(acciones, text="Aceptar y encender cámara", command=lambda: self._responder(True)).pack(side="left")
        ttk.Button(acciones, text="Rechazar", command=lambda: self._responder(False)).pack(side="left", padx=5)

        botones = ttk.Frame(panel, style="Host.TFrame")
        botones.pack(fill="x", pady=(10, 8))
        self.habilitar = ttk.Button(botones, text="Habilitar conexión", command=self._habilitar)
        self.habilitar.pack(side="left")
        self.pausar = ttk.Button(botones, text="Pausar cámara", command=self._alternar_pausa, state="disabled")
        self.pausar.pack(side="left", padx=6)
        self.detener = ttk.Button(botones, text="Cerrar conexión", command=self._cerrar_conexion, state="disabled")
        self.detener.pack(side="left")

        self.area_previa = tk.Frame(panel, bg="#203047")
        self.area_previa.pack(fill="both", expand=True)
        self.previa = tk.Label(
            self.area_previa, text="Vista previa · cámara apagada",
            bg="#203047", fg="white", font=("Segoe UI", 11),
        )
        self.previa.place(relx=.5, rely=.5, anchor="center")

    def _habilitar(self) -> None:
        if self.servidor is not None:
            return
        if not self._puede_compartir():
            messagebox.showerror("Cámara LAN", "Abre esta ventana desde el escritorio local desbloqueado.")
            return
        try:
            ip = str(ipaddress.IPv4Address(self.ip.get().strip()))
            puerto = int(self.puerto.get())
            self.indice_camara = int(self.camara.get())
            self.fps_maximos = int(self.fps.get())
            self.calidad_maxima = self.calidad.get()
            if not (1 <= puerto <= 65535 and 0 <= self.indice_camara <= 15 and 1 <= self.fps_maximos <= 15):
                raise ValueError
            if self.calidad_maxima not in _CALIDADES:
                raise ValueError
        except ValueError:
            messagebox.showerror("Cámara LAN", "Revisa la IPv4, el puerto, el índice de cámara y el límite de imágenes.")
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
        except (ErrorConexion, OSError) as error:
            mensaje = str(error)
            if isinstance(error, OSError) and (
                error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) == 10048
            ):
                mensaje = (
                    f"El puerto {puerto} ya está en uso. Si hay otra versión de Cámara LAN abierta, "
                    "elige Salir en su icono junto al reloj. También puedes indicar otro puerto "
                    "en Compartir y en Ver."
                )
            messagebox.showerror("Cámara LAN", mensaje)
            return

        self.servidor = servidor
        anuncio = AnuncioLAN(ip, puerto)
        try:
            anuncio.iniciar()
        except OSError:
            anuncio.cerrar()
        else:
            self.anuncio = anuncio
        self._mostrar_espera()
        self._botones()
        if self.bandeja is not None:
            self.bandeja.update_menu()

    def _mostrar_espera(self) -> None:
        if self.anuncio is None:
            self._estado("Esperando solicitud por IP y puerto · cámara apagada")
        else:
            self._estado("Disponible en la LAN · cámara apagada")

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
            raise ErrorConexion("El equipo que comparte no pudo aplicar los ajustes.")
        return solicitud.respuesta

    def _mostrar_solicitud(self, solicitud: _Solicitud) -> None:
        if solicitud.cancelada.is_set() or self.solicitud is not None or self.autorizada or not _escritorio_disponible():
            solicitud.respondida.set()
            return
        if time.monotonic() >= solicitud.vence:
            solicitud.respondida.set()
            return

        self.solicitud = solicitud
        self.texto_solicitud.set(f"El dispositivo {solicitud.ip} pide ver la cámara.")
        self.panel_solicitud.pack(fill="x", before=self.pausar.master)
        self._estado("Solicitud pendiente · cámara apagada")
        self._abrir()
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
            self.pausa_local = False
            self.pausa_remota = False
            self.calidad_actual = self.calidad_maxima
            self.fps_actual = self.fps_maximos
            self._estado("Abriendo cámara para la sesión aprobada", True)
        elif self.servidor is not None:
            self._mostrar_espera()
        solicitud.respondida.set()
        self._botones()

    def _aplicar_control(self, solicitud: _Control) -> None:
        if not self.autorizada or time.monotonic() >= solicitud.vence:
            solicitud.respondido.set()
            return
        if not self._puede_compartir():
            self._dejar_de_compartir("La sesión se cerró al perder el escritorio local")
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
        if self.autorizada:
            self.pausa_local = not self.pausa_local
            self._refrescar_captura()
            self._botones()

    def _en_pausa(self) -> bool:
        return self.pausa_local or self.pausa_remota

    def _refrescar_captura(self) -> None:
        if not self.autorizada:
            return
        if self._en_pausa():
            self._detener_captura()
            self._estado("Sesión en pausa · cámara apagada", True)
        elif self.captura is not None:
            self.captura.ajustar(self.calidad_actual, self.fps_actual)
        elif self.captura_cerrando is None or not self.captura_cerrando.viva():
            if self.cancelacion_activa is None or self.cancelacion_activa.is_set() or not self._puede_compartir():
                self._dejar_de_compartir("Sesión cerrada · cámara apagada")
                return
            self.captura_cerrando = None
            self.captura = _Captura(self.indice_camara, self.calidad_actual, self.fps_actual)
            self.limite_fotograma = time.monotonic() + 10
            self._estado("Abriendo cámara para la sesión aprobada", True)

    def _detener_captura(self) -> None:
        if self.captura is not None:
            self.captura.detener()
            self.captura_cerrando = self.captura
            self.captura = None
        if self.servidor is not None:
            self.servidor.publicar(None)
        self.limite_fotograma = None
        self.foto = None
        self.previa.configure(image="", text="Vista previa · cámara apagada")

    def _sesion_terminada(self) -> None:
        if self.autorizada:
            self._dejar_de_compartir("El visor se desconectó · cámara apagada")
        else:
            self._responder(False)

    def _cerrar_conexion(self) -> None:
        self._dejar_de_compartir(f"Conexión cerrada · puerto {self.puerto.get()} liberado")

    def _dejar_de_compartir(self, estado: str = "Cámara apagada") -> None:
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
        self.estado.set(texto)
        self.indicador.configure(
            bg="#9f2f3a" if activo else "#e0e8f5",
            fg="white" if activo else "#1a2940",
        )
        if self.bandeja is not None and self.en_vivo != activo:
            self.bandeja.icon = _icono(activo)
            self.bandeja.title = "Cámara LAN · sesión aprobada" if activo else "Cámara LAN · cámara apagada"
        self.en_vivo = activo

    def _botones(self) -> None:
        preparado = self.servidor is not None
        for campo, normal in self.campos:
            campo.configure(state="disabled" if preparado else normal)
        self.habilitar.configure(state="disabled" if preparado else "normal")
        self.detener.configure(state="normal" if preparado else "disabled")
        self.pausar.configure(
            state="normal" if self.autorizada else "disabled",
            text="Quitar pausa local" if self.pausa_local else "Pausar cámara",
        )

    def _puede_compartir(self) -> bool:
        return self._ventana_visible() and _escritorio_disponible()

    def _ventana_visible(self) -> bool:
        return self.raiz.state() in ("normal", "zoomed")

    def _al_ocultar(self, evento) -> None:
        if evento.widget is self.raiz and not self._ventana_visible():
            self._suspender_si_oculta()

    def _suspender_si_oculta(self) -> None:
        if self.autorizada:
            self._dejar_de_compartir("Ventana oculta · cámara apagada")
        elif self.solicitud is not None:
            self._responder(False)

    def _ocultar(self) -> None:
        self._suspender_si_oculta()
        self.raiz.withdraw()

    def _abrir(self) -> None:
        self.raiz.deiconify()
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
        if self.captura.error:
            self._dejar_de_compartir(self.captura.error)
            return

        jpeg = self.captura.tomar_fotograma()
        if jpeg is None:
            if self.limite_fotograma is not None and time.monotonic() >= self.limite_fotograma:
                self._dejar_de_compartir("La cámara no respondió a tiempo.")
            return

        if not self._puede_compartir() or self.cancelacion_activa is None or self.cancelacion_activa.is_set():
            self._dejar_de_compartir("Sesión cerrada · cámara apagada")
            return
        self.limite_fotograma = time.monotonic() + 10
        with Image.open(BytesIO(jpeg)) as cuadro:
            espacio = (max(1, self.area_previa.winfo_width() - 8), max(1, self.area_previa.winfo_height() - 8))
            cuadro.thumbnail(espacio, Image.Resampling.BILINEAR)
            self.foto = ImageTk.PhotoImage(cuadro)
        self.previa.configure(image=self.foto, text="")
        self.servidor.publicar(jpeg)
        if self.estado.get() != "Compartiendo cámara en vivo":
            self._estado("Compartiendo cámara en vivo", True)

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
                if self.autorizada:
                    self._dejar_de_compartir("Escritorio bloqueado · cámara apagada")
                elif self.solicitud is not None:
                    self._responder(False)
            elif not self._ventana_visible():
                self._suspender_si_oculta()

        if self.solicitud is not None:
            if self.solicitud.cancelada.is_set() or ahora >= self.solicitud.vence:
                self._responder(False)

        if self.autorizada and not self._en_pausa():
            self._refrescar_captura()
            self._mostrar_fotograma()

        if not self.salir:
            self.raiz.after(35, self._bucle)


def ejecutar() -> None:
    import pystray

    raiz = tk.Tk()
    anfitrion = _Anfitrion(raiz)
    anfitrion.bandeja = pystray.Icon(
        "CamaraLAN", _icono(False), "Cámara LAN · cámara apagada",
        pystray.Menu(
            pystray.MenuItem("Abrir", lambda *_: anfitrion.eventos.put(("abrir", None, None)), default=True),
            pystray.MenuItem(
                "Cerrar conexión",
                lambda *_: anfitrion.eventos.put(("cerrar_conexion", anfitrion.generacion, None)),
                enabled=lambda _: anfitrion.servidor is not None,
            ),
            pystray.MenuItem("Salir", lambda *_: anfitrion.eventos.put(("salir", None, None))),
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
