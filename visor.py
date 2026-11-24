"""Visor local para una transmisión de cámara aprobada por el anfitrión."""

from __future__ import annotations

import ipaddress
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from io import BytesIO
from tkinter import ttk

from PIL import Image, ImageTk

from transporte import ErrorConexion, Visor
from version import __version__


_RED_LOCAL = tuple(
    ipaddress.IPv4Network(red)
    for red in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
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


def _direccion(texto: str) -> str:
    try:
        direccion = ipaddress.IPv4Address(texto.strip())
    except ipaddress.AddressValueError:
        raise ValueError("Escribe la dirección IPv4 local que muestra el anfitrión.") from None

    if not any(direccion in red for red in _RED_LOCAL):
        raise ValueError("La dirección debe pertenecer a la red privada del anfitrión.")
    return str(direccion)


def _decodificar(jpeg: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(jpeg), formats=["JPEG"]) as imagen:
            if imagen.width * imagen.height > _MAXIMO_PIXELES:
                raise ValueError
            return imagen.convert("RGB")
    except (Image.DecompressionBombError, OSError, ValueError):
        raise ErrorConexion("El anfitrión envió una imagen que no se puede mostrar.") from None


def _recibir(sesion: _Sesion) -> None:
    try:
        sesion.visor.conectar()
        if not sesion.cancelada.is_set():
            sesion.eventos.put(("aprobada", ""))

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
            sesion.eventos.put(("cerrada", "No se pudo continuar la conexión con el anfitrión."))
    finally:
        sesion.visor.cerrar()


class _Panel:
    def __init__(self, raiz: tk.Tk) -> None:
        self.raiz = raiz
        self.sesion: _Sesion | None = None
        self.aprobada = False
        self.pausa_solicitada = False
        self.imagen: Image.Image | None = None
        self.foto: ImageTk.PhotoImage | None = None
        self.hora_imagen = 0.0

        self.host = tk.StringVar()
        self.puerto = tk.StringVar(value="8443")
        self.codigo = tk.StringVar()
        self.calidad = tk.StringVar(value="media")
        self.fps = tk.StringVar(value="8")
        self.estado = tk.StringVar(value="Desconectado")
        self.efectivos = tk.StringVar(value="Ajustes efectivos · —")
        self.aviso_ajustes = tk.StringVar()

        raiz.title(f"Cámara LAN — Visor · v{__version__}")
        raiz.minsize(680, 480)
        raiz.geometry("900x630")
        raiz.protocol("WM_DELETE_WINDOW", self._salir)
        estilo = ttk.Style(raiz)
        estilo.configure("Titulo.TLabel", font=("Segoe UI", 19, "bold"))
        estilo.configure("Estado.TLabel", font=("Segoe UI", 10, "bold"))

        panel = ttk.Frame(raiz, padding=18)
        panel.pack(expand=True, fill="both")
        ttk.Label(panel, text="Cámara LAN · Visor", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(panel, text="El anfitrión debe aprobar cada conexión desde su equipo.").pack(anchor="w", pady=(0, 12))

        conexion = ttk.Frame(panel)
        conexion.pack(fill="x")
        conexion.columnconfigure(2, weight=1)
        self.entrada_host = self._campo(conexion, "IPv4 del anfitrión", self.host, 0, width=17)
        self.entrada_puerto = self._campo(conexion, "Puerto", self.puerto, 1, width=7)
        self.entrada_codigo = self._campo(
            conexion, "Código privado (8 dígitos)", self.codigo, 2,
            show="•", exportselection=False, width=12,
            validate="key", validatecommand=(raiz.register(self._validar_codigo), "%P"),
        )
        self.conectar = ttk.Button(conexion, text="Solicitar conexión", command=self._solicitar)
        self.conectar.grid(row=1, column=3, padx=(0, 8), sticky="ew")
        self.desconectar = ttk.Button(conexion, text="Desconectar", command=self._detener)
        self.desconectar.grid(row=1, column=4, sticky="ew")
        self.entrada_codigo.bind("<Return>", self._solicitar_desde_teclado)

        ttk.Label(panel, textvariable=self.estado, style="Estado.TLabel", wraplength=640).pack(anchor="w", pady=10)
        self.lienzo = tk.Canvas(panel, bg=_COLOR_FONDO, highlightthickness=0, takefocus=False)
        self.lienzo.pack(expand=True, fill="both")
        self.objeto_imagen = self.lienzo.create_image(0, 0)
        self.objeto_mensaje = self.lienzo.create_text(
            0, 0, text="Conéctate para ver la imagen en directo", fill=_COLOR_TEXTO,
            justify="center", font=("Segoe UI", 13)
        )
        self.lienzo.bind("<Configure>", self._ajustar_lienzo)

        ajustes = ttk.Frame(panel)
        ajustes.pack(fill="x", pady=(12, 4))
        self.boton_pausa = ttk.Button(ajustes, text="Solicitar pausa", command=self._alternar_pausa)
        self.boton_pausa.pack(side="left", padx=(0, 14))
        ttk.Label(ajustes, text="Calidad").pack(side="left", padx=(0, 5))
        self.selector_calidad = ttk.Combobox(
            ajustes, textvariable=self.calidad, values=("baja", "media", "alta"), width=7
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
        self._actualizar_controles()
        self.entrada_host.focus_set()
        raiz.after(45, self._actualizar)

    @staticmethod
    def _validar_codigo(texto: str) -> bool:
        return len(texto) <= 8 and all("0" <= caracter <= "9" for caracter in texto)

    @staticmethod
    def _campo(panel, titulo, variable, columna, **opciones):
        ttk.Label(panel, text=titulo).grid(row=0, column=columna, padx=(0, 8), pady=(0, 4), sticky="w")
        entrada = ttk.Entry(panel, textvariable=variable, **opciones)
        entrada.grid(row=1, column=columna, sticky="ew", padx=(0, 8))
        return entrada

    def _solicitar_desde_teclado(self, _evento) -> str:
        self._solicitar()
        return "break"

    def _solicitar(self) -> None:
        if self.sesion is not None:
            return

        try:
            ip = _direccion(self.host.get())
            try:
                puerto = int(self.puerto.get())
            except ValueError:
                puerto = 0
            if not 1 <= puerto <= 65_535:
                raise ValueError("Escribe un puerto entre 1 y 65535.")
            codigo = self.codigo.get().strip()
            if not codigo:
                raise ValueError("Escribe el código privado de 8 dígitos que muestra el anfitrión.")
            visor = Visor(ip, puerto, codigo)
        except (ValueError, ErrorConexion) as error:
            self.estado.set(str(error))
            return

        self.codigo.set("")
        self.pausa_solicitada = False
        sesion = _Sesion(visor, self._ajustes())
        self.sesion = sesion
        self.estado.set("Solicitud en curso. Esperando la aprobación del anfitrión.")
        self._mostrar_mensaje("Esperando aprobación")
        self._actualizar_controles()
        threading.Thread(target=_recibir, args=(sesion,), daemon=True).start()

    def _ajustes(self) -> _Ajustes:
        return _Ajustes(self.pausa_solicitada, self.calidad.get(), int(self.fps.get()))

    def _actualizar_ajustes(self, _evento=None) -> None:
        if self.sesion is not None and self.aprobada:
            with self.sesion.bloqueo:
                self.sesion.ajustes = self._ajustes()
            self.aviso_ajustes.set("")

    def _alternar_pausa(self) -> None:
        self.pausa_solicitada = not self.pausa_solicitada
        self._actualizar_ajustes()
        self._actualizar_controles()
        if self.pausa_solicitada:
            self.estado.set("Solicitando pausa")
            self._mostrar_mensaje("Esperando confirmación de la pausa")

    def _actualizar_controles(self) -> None:
        conectando = self.sesion is not None
        for control in (self.entrada_host, self.entrada_puerto, self.entrada_codigo, self.conectar):
            control.configure(state="disabled" if conectando else "normal")
        self.desconectar.configure(state="normal" if conectando else "disabled")
        self.boton_pausa.configure(
            state="normal" if self.aprobada else "disabled",
            text="Solicitar reanudar" if self.pausa_solicitada else "Solicitar pausa",
        )
        for control in (self.selector_calidad, self.selector_fps):
            control.configure(state="readonly" if self.aprobada else "disabled")

    def _detener(self, mensaje: str = "Desconectado") -> None:
        sesion = self.sesion
        self.sesion = None
        self.aprobada = False
        self.pausa_solicitada = False
        if sesion is not None:
            sesion.cancelada.set()
            sesion.visor.cerrar()
        self.estado.set(mensaje)
        self.efectivos.set("Ajustes efectivos · —")
        self.aviso_ajustes.set("")
        self._mostrar_mensaje("Conéctate para ver la imagen en directo")
        self._actualizar_controles()

    def _actualizar(self) -> None:
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
                self.aprobada = True
                self.estado.set("Conexión aprobada")
                self._mostrar_mensaje("Esperando la primera imagen")
                self._actualizar_controles()

            if self.sesion is sesion and self.aprobada:
                with sesion.bloqueo:
                    ultimo = sesion.ultimo
                    sesion.ultimo = None
                if ultimo is not None:
                    self._mostrar_resultado(*ultimo)
                if self.imagen is not None and time.monotonic() - self.hora_imagen > _SEGUNDOS_SIN_IMAGEN:
                    self.estado.set("Conexión aprobada, sin imágenes recientes")
                    self._mostrar_mensaje("No hay imágenes recientes")

        self.raiz.after(45, self._actualizar)

    def _mostrar_resultado(
        self, imagen: Image.Image | None, efectivos: dict, recibida: float, solicitud: _Ajustes
    ) -> None:
        if self.pausa_solicitada and not solicitud.pausa:
            return

        pausada = efectivos.get("pausa", False)
        calidad = efectivos.get("calidad", "—")
        fps = efectivos.get("fps", "—")
        self.efectivos.set(f"Ajustes efectivos · Calidad {calidad} · {fps} FPS")
        if solicitud == self._ajustes():
            distintos = (pausada, calidad, fps) != (solicitud.pausa, solicitud.calidad, solicitud.fps)
            self.aviso_ajustes.set("El anfitrión aplica ajustes distintos a los solicitados." if distintos else "")
        if pausada:
            self.estado.set("Transmisión en pausa")
            self._mostrar_mensaje("Transmisión en pausa")
        elif imagen is not None and time.monotonic() - recibida <= _SEGUNDOS_SIN_IMAGEN:
            self.estado.set("En directo")
            self.imagen = imagen
            self.hora_imagen = recibida
            self._ajustar_lienzo()
        else:
            self.estado.set("Conexión aprobada, esperando imagen")
            self._mostrar_mensaje("Esperando una imagen reciente")

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
        self._detener()
        self.raiz.destroy()


def ejecutar() -> None:
    raiz = tk.Tk()
    _Panel(raiz)
    raiz.mainloop()


if __name__ == "__main__":
    ejecutar()
