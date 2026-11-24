"""Background writer for the live video displayed by the Viewer."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Condition, Thread
from time import monotonic

import cv2
import numpy as np
from PIL import Image, ImageOps

from calidad import FPS_MAXIMOS


_FPS = FPS_MAXIMOS
_MAX_PENDIENTES = 6
_MAX_INTERVALO = 1.25


@dataclass(frozen=True)
class ResultadoGrabacion:
    ruta: Path
    error: str | None = None


class _ErrorGrabacion(Exception):
    """An encoder error that can be shown directly to the Viewer user."""


class Grabacion:
    """Records displayed frames and publishes the MP4 only after finalization."""

    def __init__(self, ruta: Path, imagen: Image.Image) -> None:
        self._ruta = Path(ruta).absolute()
        if self._ruta.suffix.lower() != ".mp4":
            raise ValueError("Choose an MP4 file.")

        self._dimensiones = (max(2, imagen.width // 2 * 2), max(2, imagen.height // 2 * 2))
        self._cola: deque[tuple[Image.Image, float]] = deque(maxlen=_MAX_PENDIENTES)
        self._condicion = Condition()
        self._duracion = 0.0
        self._instante: float | None = monotonic()
        self._cerrando = False
        self._resultado: ResultadoGrabacion | None = None
        Thread(target=self._guardar, args=(imagen,), name="camara-lan-mp4", daemon=False).start()

    @property
    def resultado(self) -> ResultadoGrabacion | None:
        with self._condicion:
            return self._resultado

    def agregar(self, imagen: Image.Image) -> None:
        with self._condicion:
            if self._cerrando:
                return
            ahora = monotonic()
            self._acumular(ahora)
            self._instante = ahora
            self._cola.append((imagen, self._duracion))
            self._condicion.notify()

    def pausar(self) -> None:
        with self._condicion:
            if not self._cerrando and self._instante is not None:
                self._acumular(monotonic())
                self._instante = None

    def finalizar(self) -> None:
        with self._condicion:
            if self._cerrando:
                return
            self._acumular(monotonic())
            self._cerrando = True
            self._condicion.notify()

    def _acumular(self, ahora: float) -> None:
        if self._instante is not None:
            self._duracion += ahora - self._instante

    def _siguiente(self) -> tuple[Image.Image | None, float]:
        with self._condicion:
            self._condicion.wait_for(lambda: self._cola or self._cerrando)
            if self._cola:
                return self._cola.popleft()
            return None, self._duracion

    def _cuadro(self, imagen: Image.Image) -> np.ndarray:
        if imagen.mode != "RGB":
            imagen = imagen.convert("RGB")
        if imagen.size != self._dimensiones:
            imagen = ImageOps.pad(imagen, self._dimensiones, Image.Resampling.BILINEAR, color="black")
        return cv2.cvtColor(np.asarray(imagen), cv2.COLOR_RGB2BGR)

    def _codificar(self, ruta: Path, primero: Image.Image) -> int:
        video = cv2.VideoWriter(str(ruta), cv2.VideoWriter_fourcc(*"mp4v"), _FPS, self._dimensiones)
        try:
            if not video.isOpened():
                raise _ErrorGrabacion("Could not start the MP4 encoder.")

            cuadro = self._cuadro(primero)
            video.write(cuadro)
            cantidad = 1
            cuadro_escrito = True
            anterior = 0.0
            duracion = 0.0
            while True:
                imagen, instante = self._siguiente()
                # A stalled stream or a full queue should not generate a long freeze.
                duracion += min(_MAX_INTERVALO, instante - anterior)
                anterior = instante
                objetivo = round(duracion * _FPS)
                while cantidad < objetivo:
                    video.write(cuadro)
                    cantidad += 1
                    cuadro_escrito = True

                if imagen is None:
                    if not cuadro_escrito:
                        video.write(cuadro)
                        cantidad += 1
                    return cantidad

                cuadro = self._cuadro(imagen)
                cuadro_escrito = False
        finally:
            video.release()

    @staticmethod
    def _verificar(ruta: Path, cantidad: int) -> None:
        video = cv2.VideoCapture(str(ruta))
        try:
            if not video.isOpened() or video.get(cv2.CAP_PROP_FRAME_COUNT) != cantidad or not video.read()[0]:
                raise _ErrorGrabacion("The MP4 recording was not written completely.")
        finally:
            video.release()

    def _guardar(self, primero: Image.Image) -> None:
        resultado = ResultadoGrabacion(self._ruta, "Could not save the MP4 recording.")
        try:
            # The temporary file stays on the same volume as the destination.
            with TemporaryDirectory(prefix=".lan-camera-", dir=self._ruta.parent, ignore_cleanup_errors=True) as carpeta:
                temporal = Path(carpeta) / "recording.mp4"
                cantidad = self._codificar(temporal, primero)
                self._verificar(temporal, cantidad)
                temporal.replace(self._ruta)
            resultado = ResultadoGrabacion(self._ruta)
        except _ErrorGrabacion as error:
            resultado = ResultadoGrabacion(self._ruta, str(error))
        except (OSError, cv2.error, ValueError):
            pass
        finally:
            with self._condicion:
                self._cerrando = True
                self._cola.clear()
                self._resultado = resultado
