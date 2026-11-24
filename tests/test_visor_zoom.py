"""Pruebas del encuadre que muestra el visor, sin ventanas ni cámara."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import visor
from zoom import Zoom


class ZoomVisorTests(unittest.TestCase):
    def setUp(self):
        self.panel = visor._Panel.__new__(visor._Panel)
        self.panel.raiz = Mock()
        self.panel.conectada = True
        self.panel.cerrando = False
        self.panel.zoom = Zoom()
        self.panel._arrastre = None
        self.panel.imagen = Image.new("RGB", (80, 40), "blue")
        self.panel.imagen.paste("yellow", (12, 0, 68, 40))
        self.panel.foto = None
        self.panel.objeto_imagen = 1
        self.panel.objeto_mensaje = 2
        self.panel.lienzo = Mock()
        self.panel.lienzo.winfo_width.return_value = 200
        self.panel.lienzo.winfo_height.return_value = 200
        self.panel.estado_zoom = Mock()
        self.panel.boton_zoom_menos = Mock()
        self.panel.boton_zoom_mas = Mock()
        self.panel.boton_zoom_restaurar = Mock()
        self.crear_foto = self.enterContext(patch.object(visor.ImageTk, "PhotoImage"))

    def test_ampliar_redibuja_el_encuadre_centrado_sin_alterar_la_imagen(self):
        self.panel._ajustar_lienzo()
        completa = self.crear_foto.call_args.args[0]
        self.assertEqual(completa.getpixel((10, 50)), (0, 0, 255))

        self.panel._cambiar_zoom(3)

        ampliada = self.crear_foto.call_args.args[0]
        self.assertEqual(ampliada.size, (200, 100))
        self.assertEqual(ampliada.getpixel((10, 50)), (255, 255, 0))
        self.panel.lienzo.coords.assert_any_call(self.panel.objeto_imagen, 100, 100)
        self.panel.estado_zoom.set.assert_called_with("200%")
        self.assertEqual(self.panel.imagen.getpixel((5, 20)), (0, 0, 255))

    def test_reiniciar_zoom_recupera_el_encuadre_completo(self):
        self.panel._cambiar_zoom(3, (0.2, 0.8))

        self.panel._reiniciar_zoom()

        self.assertEqual(self.panel.zoom, Zoom())
        restaurada = self.crear_foto.call_args.args[0]
        self.assertEqual(restaurada.getpixel((10, 50)), (0, 0, 255))
        self.panel.estado_zoom.set.assert_called_with("100%")

    def test_rueda_y_arrastre_se_miden_en_el_video_sin_incluir_las_barras(self):
        punto = SimpleNamespace(delta=120, x=50, y=75)
        self.panel._rueda_zoom(punto)
        self.assertEqual(self.panel.zoom.porcentaje, 125)
        self.assertAlmostEqual(self.panel.zoom.centro_x, 0.45)
        self.assertAlmostEqual(self.panel.zoom.centro_y, 0.45)

        self.panel._iniciar_arrastre(SimpleNamespace(x=50, y=30))
        self.assertIsNone(self.panel._arrastre)
        self.panel._iniciar_arrastre(punto)
        self.panel._mover_arrastre(SimpleNamespace(x=58, y=79))
        self.assertAlmostEqual(self.panel.zoom.centro_x, 0.418)
        self.assertAlmostEqual(self.panel.zoom.centro_y, 0.418)
        self.panel._terminar_arrastre(None)
        self.assertIsNone(self.panel._arrastre)
