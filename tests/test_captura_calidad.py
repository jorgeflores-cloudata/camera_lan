"""Perfiles y negociación de cámara con dispositivos simulados, sin hardware."""

import unittest
from unittest.mock import patch

import cv2
import numpy as np

import anfitrion
import calidad


class _Camara:
    def __init__(self, modos=None, predeterminado=(640, 480)):
        self.modos = modos or {}
        self.predeterminado = predeterminado
        self.ancho = 640
        self.alto = 480
        self.propiedades = []
        self.solicitudes = []
        self.liberada = False

    def isOpened(self):
        return True

    def set(self, propiedad, valor):
        self.propiedades.append((propiedad, valor))
        if propiedad == cv2.CAP_PROP_FRAME_WIDTH:
            self.ancho = valor
        elif propiedad == cv2.CAP_PROP_FRAME_HEIGHT:
            self.alto = valor
        # Algunos drivers dicen que sí, aunque ignoren la resolución.
        return True

    def read(self):
        solicitada = self.ancho, self.alto
        self.solicitudes.append(solicitada)
        ancho, alto = self.modos.get(solicitada, self.predeterminado)
        return True, np.zeros((alto, ancho, 3), dtype=np.uint8)

    def release(self):
        self.liberada = True


class CapturaCalidadTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(anfitrion, "_CALIDADES", calidad.PERFILES))
        self.enterContext(patch.object(anfitrion.threading, "Thread"))

    def _captura(self, nivel="maxima", fps=30, **opciones):
        return anfitrion._Captura(0, nivel, fps, **opciones)

    def _un_fotograma(self, captura, camara):
        with (
            patch.object(anfitrion.cv2, "VideoCapture", return_value=camara),
            patch.object(captura._detener, "wait", side_effect=lambda _tiempo: captura._detener.set()),
        ):
            captura._ejecutar()
        return captura.tomar_fotograma()

    def test_maximum_solicita_4k_y_treinta_fps(self):
        captura = self._captura()
        camara = _Camara({(3840, 2160): (3840, 2160)})

        imagen = captura._configurar(camara)

        self.assertEqual(imagen.shape[:2], (2160, 3840))
        self.assertEqual(camara.solicitudes, [(3840, 2160)])
        self.assertIn((cv2.CAP_PROP_FPS, 30), camara.propiedades)

    def test_driver_ignora_4k_pero_se_encuentra_su_modo_full_hd(self):
        captura = self._captura()
        camara = _Camara({(1920, 1080): (1920, 1080)})

        imagen = captura._configurar(camara)

        self.assertEqual(imagen.shape[:2], (1080, 1920))
        self.assertEqual(camara.solicitudes, [(3840, 2160), (2560, 1440), (1920, 1080)])

    def test_camara_menor_no_se_amplia_para_parecer_4k(self):
        captura = self._captura()
        camara = _Camara()

        datos = self._un_fotograma(captura, camara)

        imagen = cv2.imdecode(np.frombuffer(datos, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(imagen.shape[:2], (480, 640))
        self.assertIsNone(captura.error)
        self.assertTrue(camara.liberada)

    def test_bajar_calidad_preserva_aspecto_y_limite_de_la_fuente(self):
        captura = self._captura("media", 8, calidad_fuente="maxima", fps_fuente=30)
        camara = _Camara(predeterminado=(1920, 1200))

        datos = self._un_fotograma(captura, camara)

        imagen = cv2.imdecode(np.frombuffer(datos, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(imagen.shape[:2], (360, 576))
        self.assertEqual(camara.solicitudes[0], (3840, 2160))
        self.assertTrue(all(valor == 30 for propiedad, valor in camara.propiedades if propiedad == cv2.CAP_PROP_FPS))
        self.assertEqual(captura._calidad_fuente, "maxima")

    def test_perfiles_bajos_reducen_desde_la_fuente_original_de_960(self):
        for nivel, esperado in (("baja", (239, 426)), ("media", (360, 640))):
            with self.subTest(nivel=nivel):
                captura = self._captura(nivel, 10)
                camara = _Camara({(960, 540): (960, 540)})

                datos = self._un_fotograma(captura, camara)

                imagen = cv2.imdecode(np.frombuffer(datos, dtype=np.uint8), cv2.IMREAD_COLOR)
                self.assertEqual(imagen.shape[:2], esperado)
                self.assertEqual(camara.solicitudes, [(960, 540)])

    def test_ajustar_descarta_solo_al_cambiar_y_conserva_la_fuente(self):
        captura = self._captura("alta", 15, calidad_fuente="maxima", fps_fuente=30)
        captura._ultimo = b"anterior"

        captura.ajustar("alta", 15)
        self.assertEqual(captura.tomar_fotograma(), b"anterior")
        captura._ultimo = b"obsoleto"
        captura.ajustar("baja", 5)

        self.assertIsNone(captura.tomar_fotograma())
        self.assertEqual(captura._calidad_fuente, "maxima")
        self.assertEqual(captura._fps_fuente, 30)

    def test_no_publica_un_fotograma_codificado_con_ajustes_anteriores(self):
        captura = self._captura()
        camara = _Camara()
        codificar = cv2.imencode

        def cambiar_durante_la_codificacion(*argumentos):
            resultado = codificar(*argumentos)
            captura.ajustar("baja", 5)
            return resultado

        with patch.object(anfitrion.cv2, "imencode", side_effect=cambiar_durante_la_codificacion):
            datos = self._un_fotograma(captura, camara)

        self.assertIsNone(datos)
        self.assertIsNone(captura.error)

    def test_rechaza_fotograma_codificado_fuera_del_limite(self):
        captura = self._captura()
        camara = _Camara()
        with (
            patch.object(calidad, "MAX_JPEG", 64),
            patch.object(anfitrion.cv2, "imencode", return_value=(True, np.zeros(65, dtype=np.uint8))),
        ):
            datos = self._un_fotograma(captura, camara)

        self.assertIsNone(datos)
        self.assertIn("too large", captura.error)
        self.assertTrue(camara.liberada)


if __name__ == "__main__":
    unittest.main()
