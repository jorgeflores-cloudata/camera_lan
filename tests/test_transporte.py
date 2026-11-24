"""Pruebas del emparejamiento por TLS en loopback, sin abrir una cámara."""

from collections import deque
import ipaddress
import time
import unittest
from unittest.mock import Mock, patch

import transporte


class CodigoPrivadoTests(unittest.TestCase):
    def test_conserva_ceros_iniciales(self):
        self.assertEqual(transporte._leer_codigo("00001234"), b"00001234")

    def test_rechaza_codigos_fuera_del_formato(self):
        for codigo in (
            "", "1234567", "123456789", "1234a678", "1234-678", "1234 678",
            "１２３４５６７８", "١٢٣٤٥٦٧٨", "1234567²", 12345678, None,
            "LAN1.anterior.anterior",
        ):
            with self.subTest(codigo=codigo):
                with self.assertRaises(transporte.ErrorConexion):
                    transporte._leer_codigo(codigo)


class EmparejamientoTLSTests(unittest.TestCase):
    def setUp(self):
        redes = transporte._REDES_LAN + (ipaddress.IPv4Network("127.0.0.0/8"),)
        reemplazo = patch.object(transporte, "_REDES_LAN", redes)
        reemplazo.start()
        self.addCleanup(reemplazo.stop)

    def _crear_servidor(self, aceptar=True, numero=1234):
        solicitud = Mock(return_value=aceptar)
        control = Mock(side_effect=lambda pausa, calidad, fps: {
            "pausa": pausa, "calidad": calidad, "fps": fps,
        })
        terminado = Mock()
        servidor = transporte.Servidor("127.0.0.1", 8443, solicitud, control, terminado)
        # La interfaz pública conserva la validación del puerto; solo la prueba
        # pide al sistema operativo uno libre para evitar conflictos locales.
        servidor._puerto = 0
        self.addCleanup(self._cerrar_servidor, servidor)
        with patch.object(transporte.secrets, "randbelow", return_value=numero):
            codigo = servidor.iniciar()
        puerto = servidor._escucha.getsockname()[1]
        return servidor, puerto, codigo, solicitud, control, terminado

    def _crear_visor(self, puerto, codigo):
        visor = transporte.Visor("127.0.0.1", puerto, codigo)
        self.addCleanup(visor.cerrar)
        return visor

    def _esperar_desocupado(self, servidor):
        limite = time.monotonic() + 5
        while time.monotonic() < limite:
            with servidor._bloqueo:
                if servidor._sesion is None:
                    return
            time.sleep(0.01)
        self.fail("El servidor no liberó la sesión cerrada.")

    def _cerrar_servidor(self, servidor):
        servidor.cerrar()
        self._esperar_desocupado(servidor)

    def test_genera_ocho_digitos_incluidos_ceros_iniciales(self):
        for numero, esperado in ((0, "00000000"), (1234, "00001234"), (99_999_999, "99999999")):
            with self.subTest(numero=numero):
                servidor, _, codigo, _, _, _ = self._crear_servidor(numero=numero)
                self.assertEqual(codigo, esperado)
                self.assertRegex(codigo, r"^[0-9]{8}$")
                self._cerrar_servidor(servidor)

    def test_codigo_correcto_requiere_aprobacion_y_transmite(self):
        servidor, puerto, codigo, solicitud, control, terminado = self._crear_servidor()
        visor = self._crear_visor(puerto, codigo)
        visor.conectar()
        solicitud.assert_called_once()
        self.assertEqual(solicitud.call_args.args[0], "127.0.0.1")
        self.assertIsInstance(visor._conexion, transporte.ssl.SSLSocket)
        self.assertTrue(visor._conexion.cipher())

        # El transporte trata JPEG como bytes; no hace falta una cámara ni un
        # decodificador de imagen para verificar el envío y los controles.
        jpeg = b"\xff\xd8fotograma-de-prueba\xff\xd9"
        servidor.publicar(jpeg)
        imagen, efectivo = visor.leer(False, "alta", 12)
        self.assertEqual(imagen, jpeg)
        self.assertEqual(efectivo, {"pausa": False, "calidad": "alta", "fps": 12})
        control.assert_called_once_with(False, "alta", 12)

        imagen, efectivo = visor.leer(True, "baja", 3)
        self.assertIsNone(imagen)
        self.assertEqual(efectivo, {"pausa": True, "calidad": "baja", "fps": 3})
        visor.cerrar()
        self._esperar_desocupado(servidor)
        terminado.assert_called_once()

    def test_rechazo_del_anfitrion_impide_la_sesion(self):
        servidor, puerto, codigo, solicitud, control, _ = self._crear_servidor(aceptar=False)
        visor = self._crear_visor(puerto, codigo)
        with self.assertRaisesRegex(transporte.ErrorConexion, "no aprobó"):
            visor.conectar()
        self._esperar_desocupado(servidor)
        solicitud.assert_called_once()
        control.assert_not_called()
        self.assertIsNone(visor._conexion)

    def test_codigo_incorrecto_no_llega_a_pedir_aprobacion(self):
        servidor, puerto, _, solicitud, control, terminado = self._crear_servidor()
        visor = self._crear_visor(puerto, "99999999")
        with self.assertRaises((transporte.ErrorConexion, OSError)):
            visor.conectar()
        self._esperar_desocupado(servidor)
        solicitud.assert_not_called()
        control.assert_not_called()
        terminado.assert_not_called()
        self.assertIsNone(visor._conexion)

    def test_huella_distinta_rechaza_incluso_el_codigo_correcto(self):
        contexto, huella = transporte._tls_servidor("127.0.0.1")
        huella_distinta = bytes([huella[0] ^ 1]) + huella[1:]
        with patch.object(transporte, "_tls_servidor", return_value=(contexto, huella_distinta)):
            servidor, puerto, codigo, solicitud, _, _ = self._crear_servidor()
        visor = self._crear_visor(puerto, codigo)
        with self.assertRaises((transporte.ErrorConexion, OSError)):
            visor.conectar()
        self._esperar_desocupado(servidor)
        solicitud.assert_not_called()

    def test_limita_sexto_intento_y_se_recupera_al_vencer_la_ventana(self):
        servidor, puerto, codigo, solicitud, _, _ = self._crear_servidor()
        for _ in range(5):
            visor = self._crear_visor(puerto, "99999999")
            with self.assertRaises((transporte.ErrorConexion, OSError)):
                visor.conectar()
            self._esperar_desocupado(servidor)

        visor_limitado = self._crear_visor(puerto, codigo)
        with self.assertRaises((transporte.ErrorConexion, OSError)):
            visor_limitado.conectar()
        self._esperar_desocupado(servidor)
        solicitud.assert_not_called()
        with servidor._bloqueo:
            self.assertEqual(len(servidor._intentos), 5)
            servidor._intentos = deque(
                instante - transporte._VENTANA_INTENTOS - 1
                for instante in servidor._intentos
            )

        visor = self._crear_visor(puerto, codigo)
        visor.conectar()
        solicitud.assert_called_once()
        with servidor._bloqueo:
            self.assertEqual(len(servidor._intentos), 1)
        visor.cerrar()
        self._esperar_desocupado(servidor)


if __name__ == "__main__":
    unittest.main()
