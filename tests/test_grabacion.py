"""MP4 tests with generated frames and no camera."""

from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from unittest.mock import patch

import cv2
from PIL import Image

import grabacion


class Reloj:
    instante = 10.0

    def __call__(self):
        return self.instante


class GrabacionTests(unittest.TestCase):
    def setUp(self):
        carpeta = self.enterContext(TemporaryDirectory(prefix="LAN recording "))
        self.ruta = Path(carpeta) / "test clip.mp4"
        self.rojo = Image.new("RGB", (59, 41), "red")
        self.verde = Image.new("RGB", (32, 64), "green")
        self.reloj = Reloj()
        self.enterContext(patch.object(grabacion, "monotonic", new=self.reloj))

    def _iniciar(self):
        escritura = grabacion.Grabacion(self.ruta, self.rojo)
        self.addCleanup(self._finalizar, escritura)
        return escritura

    def _finalizar(self, escritura):
        escritura.finalizar()
        limite = monotonic() + 5
        while monotonic() < limite:
            resultado = escritura.resultado
            if resultado is not None:
                return resultado
            sleep(0.01)
        self.fail("The MP4 writer did not finish.")

    def _leer_fotogramas(self):
        video = cv2.VideoCapture(str(self.ruta))
        try:
            self.assertTrue(video.isOpened())
            self.assertEqual(video.get(cv2.CAP_PROP_FPS), 15)
            fotogramas = []
            while True:
                ok, imagen = video.read()
                if not ok:
                    return fotogramas
                fotogramas.append(imagen)
        finally:
            video.release()

    def test_graba_mp4_con_tamano_fijo_y_publica_al_finalizar(self):
        anterior = b"existing file"
        self.ruta.write_bytes(anterior)
        escritura = self._iniciar()
        self.reloj.instante = 10.5
        escritura.agregar(self.verde)
        self.assertEqual(self.ruta.read_bytes(), anterior)

        self.reloj.instante = 11.0
        resultado = self._finalizar(escritura)
        self.assertEqual(resultado, grabacion.ResultadoGrabacion(self.ruta))
        imagenes = self._leer_fotogramas()
        self.assertEqual(len(imagenes), 15)
        self.assertEqual([imagen.shape for imagen in imagenes], [(40, 58, 3)] * 15)
        self.assertGreater(imagenes[0][20, 29, 2], 200)
        self.assertGreater(imagenes[-1][20, 29, 1], 90)
        self.assertLess(int(imagenes[-1][20, 2].max()), 10)

    def test_omite_el_tiempo_en_pausa_y_reanuda_con_la_imagen_nueva(self):
        escritura = self._iniciar()
        self.reloj.instante = 10.4
        escritura.pausar()
        self.reloj.instante = 40.4
        escritura.pausar()
        escritura.agregar(self.verde)
        self.reloj.instante = 40.8

        self.assertIsNone(self._finalizar(escritura).error)
        imagenes = self._leer_fotogramas()
        self.assertEqual(len(imagenes), 12)
        self.assertGreater(imagenes[0][20, 29, 2], 200)
        self.assertGreater(imagenes[-1][20, 29, 1], 90)

    def test_conserva_el_destino_y_limpia_el_temporal_si_falla(self):
        anterior = b"existing file"
        self.ruta.write_bytes(anterior)
        with patch.object(grabacion.Grabacion, "_verificar", side_effect=OSError("test failure")):
            escritura = self._iniciar()
            self.assertIsNotNone(self._finalizar(escritura).error)

        self.assertEqual(self.ruta.read_bytes(), anterior)
        self.assertEqual(list(self.ruta.parent.iterdir()), [self.ruta])

    def test_finalizar_inmediatamente_sigue_guardando_un_fotograma(self):
        escritura = self._iniciar()
        escritura.finalizar()
        escritura.agregar(self.verde)
        escritura.pausar()
        self.assertIsNone(self._finalizar(escritura).error)
        self.assertEqual(len(self._leer_fotogramas()), 1)

    def test_rechaza_una_extension_distinta_de_mp4(self):
        with self.assertRaisesRegex(ValueError, "MP4"):
            grabacion.Grabacion(self.ruta.with_suffix(".txt"), self.rojo)
