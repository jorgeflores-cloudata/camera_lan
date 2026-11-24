"""Pruebas del encuadre digital con fotogramas sintéticos."""

import unittest

from PIL import Image

from zoom import ZOOM_MAXIMO, Zoom, ajustar_dimensiones


class ZoomTests(unittest.TestCase):
    def test_niveles_y_limites(self):
        inicial = Zoom()
        self.assertEqual([inicial.cambiar(i).porcentaje for i in range(7)], [100, 125, 150, 200, 250, 300, 400])
        self.assertIs(inicial.cambiar(-1), inicial)
        maximo = inicial.cambiar(20)
        self.assertEqual(maximo.porcentaje, ZOOM_MAXIMO)
        self.assertIs(maximo.cambiar(1), maximo)
        self.assertEqual(maximo.cambiar(-20), inicial)

    def test_cambio_mantiene_el_punto_bajo_el_cursor(self):
        inicial = Zoom()
        ancla = (0.75, 0.25)
        ampliado = inicial.cambiar(3, ancla)
        self.assertEqual(ampliado.factor, 2.0)
        self.assertAlmostEqual(ampliado.centro_x + (ancla[0] - 0.5) / ampliado.factor, ancla[0])
        self.assertAlmostEqual(ampliado.centro_y + (ancla[1] - 0.5) / ampliado.factor, ancla[1])
        self.assertEqual(inicial, Zoom())

    def test_arrastre_en_la_direccion_del_mouse_y_limitado_a_la_imagen(self):
        inicial = Zoom()
        self.assertIs(inicial.desplazar(0.5, 0.5), inicial)
        ampliado = inicial.cambiar(3)
        movido = ampliado.desplazar(0.2, -0.2)
        self.assertEqual((movido.centro_x, movido.centro_y), (0.4, 0.6))
        borde = ampliado.desplazar(2, -2)
        self.assertEqual((borde.centro_x, borde.centro_y), (0.25, 0.75))
        self.assertEqual(borde.cambiar(-20), inicial)

    def test_recorte_al_ampliar_y_mismas_dimensiones_para_grabar(self):
        imagen = Image.new("RGB", (40, 30), "red")
        imagen.paste((0, 0, 255), (0, 0, 20, 15))
        ampliado = Zoom().cambiar(3, (0, 0))

        self.assertIs(Zoom().aplicar(imagen), imagen)
        grabado = ampliado.aplicar(imagen)
        vista = ampliado.aplicar(imagen, (20, 15))
        self.assertEqual(grabado.size, imagen.size)
        self.assertEqual(vista.size, (20, 15))
        self.assertEqual(grabado.getpixel((20, 15)), (0, 0, 255))
        self.assertEqual(vista.getpixel((10, 7)), (0, 0, 255))
        self.assertEqual(imagen.getpixel((20, 15)), (255, 0, 0))

    def test_el_encuadre_admite_fotogramas_pequenos_y_cambios_de_formato(self):
        ampliado = Zoom().cambiar(20, (1, 1))
        for dimensiones in ((1, 1), (1, 32), (120, 43)):
            with self.subTest(dimensiones=dimensiones):
                imagen = Image.new("RGB", dimensiones, "blue")
                grabado = ampliado.aplicar(imagen)
                self.assertEqual(grabado.size, dimensiones)
                self.assertEqual(grabado.getpixel((0, 0)), (0, 0, 255))

    def test_dimensiones_ajustadas_al_lienzo(self):
        self.assertEqual(ajustar_dimensiones((800, 600), (400, 400)), (400, 300))
        self.assertEqual(ajustar_dimensiones((800, 600), (1000, 300)), (400, 300))
        self.assertEqual(ajustar_dimensiones((1, 25), (2, 2)), (1, 2))
        self.assertEqual(ajustar_dimensiones((100, 100), (0, 0)), (1, 1))
