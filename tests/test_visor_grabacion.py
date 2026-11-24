"""Pruebas de grabación del visor con fotogramas simulados y sin ventanas."""

from pathlib import Path
import queue
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import visor


class GrabacionVisorTests(unittest.TestCase):
    def setUp(self):
        self.panel = visor._Panel.__new__(visor._Panel)
        self.panel.raiz = Mock()
        self.panel.sesion = Mock()
        self.panel.conectada = True
        self.panel.pausa_solicitada = False
        self.panel.imagen = Image.new("RGB", (20, 20))
        self.panel.foto = None
        self.panel.hora_imagen = time.monotonic()
        self.panel.busqueda = None
        self.panel.resultados_recientes = queue.SimpleQueue()
        self.panel.grabacion = None
        self.panel.guardando_grabacion = False
        self.panel.cerrando = False
        self.panel.estado_grabacion = Mock()
        self.panel.boton_grabar = Mock()
        self.panel.estado = Mock()
        self.panel.efectivos = Mock()
        self.panel.aviso_ajustes = Mock()
        self.panel.lienzo = Mock()
        self.panel.objeto_imagen = 1
        self.panel.objeto_mensaje = 2
        self.ajustes = visor._Ajustes()
        self.panel._ajustes = Mock(return_value=self.ajustes)
        self.panel._ajustar_lienzo = Mock()
        self.panel._actualizar_controles = Mock()

        self.ruta = Path.cwd() / "viewer-recording.mp4"
        self.elegir_ruta = self.enterContext(patch.object(
            visor.filedialog, "asksaveasfilename", return_value=str(self.ruta),
        ))
        self.crear_grabacion = self.enterContext(patch.object(visor, "Grabacion"))
        self.grabacion = self.crear_grabacion.return_value
        self.grabacion.resultado = None

    def test_solo_inicia_con_un_fotograma_actual(self):
        self.panel.hora_imagen -= visor._SEGUNDOS_SIN_IMAGEN + 1
        self.panel._alternar_grabacion()
        self.elegir_ruta.assert_not_called()
        self.crear_grabacion.assert_not_called()

        self.panel.hora_imagen = time.monotonic()
        self.panel._alternar_grabacion()

        self.crear_grabacion.assert_called_once_with(self.ruta, self.panel.imagen)
        self.assertIs(self.panel.grabacion, self.grabacion)
        self.assertFalse(self.panel.guardando_grabacion)

    def test_no_inicia_si_la_sesion_cambia_en_el_dialogo(self):
        def desconectar_durante_el_dialogo(**_opciones):
            self.panel.sesion = Mock()
            return str(self.ruta)

        self.elegir_ruta.side_effect = desconectar_durante_el_dialogo

        self.panel._alternar_grabacion()

        self.elegir_ruta.assert_called_once()
        self.crear_grabacion.assert_not_called()
        self.assertIsNone(self.panel.grabacion)

    def test_graba_los_fotogramas_visibles_y_pausa_sin_imagen(self):
        imagen = self.panel.imagen
        self.panel.grabacion = self.grabacion
        efectivos = {"pausa": False, "calidad": "media", "fps": 8}
        self.panel._mostrar_resultado(imagen, efectivos, time.monotonic(), self.ajustes)
        self.grabacion.agregar.assert_called_once_with(imagen)

        self.panel._mostrar_resultado(None, efectivos, time.monotonic(), self.ajustes)
        self.grabacion.pausar.assert_called_once_with()

        vencida = time.monotonic() - visor._SEGUNDOS_SIN_IMAGEN - 1
        self.panel._mostrar_resultado(imagen, efectivos, vencida, self.ajustes)
        self.grabacion.agregar.assert_called_once_with(imagen)

    def test_detener_solo_solicita_un_guardado(self):
        self.panel.grabacion = self.grabacion

        self.panel._alternar_grabacion()
        self.panel._alternar_grabacion()

        self.grabacion.finalizar.assert_called_once_with()
        self.assertTrue(self.panel.guardando_grabacion)
        self.assertIs(self.panel.grabacion, self.grabacion)

    def test_cerrar_espera_el_mp4_sin_bloquear(self):
        sesion = self.panel.sesion
        self.panel.grabacion = self.grabacion

        self.panel._salir()

        sesion.visor.cerrar.assert_called_once_with()
        self.grabacion.finalizar.assert_called_once_with()
        self.panel.raiz.destroy.assert_not_called()

        self.grabacion.resultado = SimpleNamespace(ruta=self.ruta, error=None)
        self.panel._actualizar()

        self.panel.raiz.destroy.assert_called_once_with()
        self.assertIsNone(self.panel.grabacion)
