"""Pruebas del perfil Maximum del visor, con imágenes sintéticas."""

from io import BytesIO
import queue
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import visor


class CalidadVisorTests(unittest.TestCase):
    @staticmethod
    def _jpeg(tamano):
        salida = BytesIO()
        with Image.new("RGB", tamano, "navy") as imagen:
            imagen.save(salida, format="JPEG", quality=95)
        return salida.getvalue()

    def test_decodifica_un_fotograma_4k_sin_reducirlo(self):
        imagen = visor._decodificar(self._jpeg((3840, 2160)))
        self.assertEqual(imagen.size, (3840, 2160))
        self.assertEqual(imagen.mode, "RGB")
        imagen.close()

    def test_rechaza_un_fotograma_que_excede_el_limite_4k(self):
        with self.assertRaises(visor.ErrorConexion):
            visor._decodificar(self._jpeg((3841, 2160)))

    def test_maximum_y_30_fps_se_convierten_a_ajustes_del_protocolo(self):
        panel = visor._Panel.__new__(visor._Panel)
        panel.pausa_solicitada = False
        panel.calidad = Mock()
        panel.calidad.get.return_value = "Maximum"
        panel.fps = Mock()
        panel.fps.get.return_value = "30"

        self.assertEqual(panel._ajustes(), visor._Ajustes(False, "maxima", 30))

    def test_el_hilo_respeta_los_30_fps_efectivos_del_anfitrion(self):
        transporte = Mock()
        transporte.leer.return_value = (None, {"pausa": False, "calidad": "maxima", "fps": 30})
        sesion = visor._Sesion(transporte, visor._Ajustes())
        sesion.cancelada = Mock()
        sesion.cancelada.is_set.side_effect = [False, False, False, True]
        with (
            patch.object(visor, "registrar_conexion", return_value=[]),
            patch.object(visor.time, "monotonic", side_effect=[10.0, 10.0, 10.0]),
        ):
            visor._recibir(sesion, ("192.168.0.2", 8443), queue.SimpleQueue())

        transporte.leer.assert_called_once_with(False, "media", 8)
        sesion.cancelada.wait.assert_called_once()
        self.assertAlmostEqual(sesion.cancelada.wait.call_args.args[0], 1 / 30)
        transporte.cerrar.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
