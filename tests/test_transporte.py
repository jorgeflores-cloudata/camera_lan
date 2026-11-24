"""Pruebas de conexión TLS en loopback, sin abrir una cámara."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import ipaddress
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import transporte


class EmparejamientoTLSTests(unittest.TestCase):
    def setUp(self):
        redes = transporte._REDES_LAN + (ipaddress.IPv4Network("127.0.0.0/8"),)
        reemplazo = patch.object(transporte, "_REDES_LAN", redes)
        reemplazo.start()
        self.addCleanup(reemplazo.stop)
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.datos = Path(temporal.name)
        datos_locales = patch.object(transporte, "_datos_locales", return_value=self.datos)
        datos_locales.start()
        self.addCleanup(datos_locales.stop)

    def _crear_servidor(self, aceptar=True, puerto=None):
        solicitud = Mock(side_effect=aceptar) if callable(aceptar) else Mock(return_value=aceptar)
        control = Mock(side_effect=lambda pausa, calidad, fps: {
            "pausa": pausa, "calidad": calidad, "fps": fps,
        })
        terminado = Mock()
        servidor = transporte.Servidor("127.0.0.1", 8443 if puerto is None else puerto, solicitud, control, terminado)
        if puerto is None:
            # La interfaz pública conserva la validación del puerto; solo la prueba
            # pide al sistema operativo uno libre para evitar conflictos locales.
            servidor._puerto = 0
        self.addCleanup(self._cerrar_servidor, servidor)
        servidor.iniciar()
        puerto = servidor._escucha.getsockname()[1]
        return servidor, puerto, solicitud, control, terminado

    def _crear_visor(self, puerto):
        visor = transporte.Visor("127.0.0.1", puerto)
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

    def test_requiere_aprobacion_antes_de_transmitir_y_recordar_la_identidad(self):
        pendiente = threading.Event()
        autorizar = threading.Event()

        def esperar_aprobacion(_ip, _cancelada):
            pendiente.set()
            return autorizar.wait(5)

        servidor, puerto, solicitud, control, terminado = self._crear_servidor(aceptar=esperar_aprobacion)
        visor = self._crear_visor(puerto)

        # El transporte trata JPEG como bytes; la prueba no abre una cámara.
        jpeg = b"\xff\xd8fotograma-de-prueba\xff\xd9"
        with ThreadPoolExecutor(max_workers=1) as executor:
            conexion = executor.submit(visor.conectar)
            try:
                self.assertTrue(pendiente.wait(5))
                self.assertFalse(conexion.done())
                servidor.publicar(jpeg)
                with self.assertRaisesRegex(transporte.ErrorConexion, "aún no autorizó"):
                    visor.leer(False, "alta", 12)
                self.assertFalse(transporte._ruta_huella("127.0.0.1", puerto).exists())
                control.assert_not_called()
            finally:
                autorizar.set()
            conexion.result(timeout=5)

        solicitud.assert_called_once()
        self.assertEqual(solicitud.call_args.args[0], "127.0.0.1")
        self.assertIsInstance(visor._conexion, transporte.ssl.SSLSocket)
        self.assertTrue(visor._conexion.cipher())
        self.assertTrue(transporte._ruta_huella("127.0.0.1", puerto).exists())
        imagen, _ = visor.leer(False, "alta", 12)
        self.assertIsNone(imagen)
        control.reset_mock()
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

    def test_cerrar_mientras_espera_libera_el_puerto(self):
        servidor, puerto, _, _, _ = self._crear_servidor()
        servidor.cerrar()
        self._crear_servidor(puerto=puerto)

    def test_cerrar_con_visor_desconecta_y_libera_el_puerto(self):
        servidor, puerto, _, _, _ = self._crear_servidor()
        visor = self._crear_visor(puerto)
        visor.conectar()
        ruta_huella = transporte._ruta_huella("127.0.0.1", puerto)
        huella = ruta_huella.read_bytes()

        servidor.cerrar()
        _, _, nueva_solicitud, _, _ = self._crear_servidor(puerto=puerto)
        with self.assertRaises((OSError, transporte.ErrorConexion)):
            visor.leer(False, "media", 10)

        visor_nuevo = self._crear_visor(puerto)
        visor_nuevo.conectar()
        self.assertEqual(ruta_huella.read_bytes(), huella)
        nueva_solicitud.assert_called_once()

    def test_rechazo_del_anfitrion_impide_la_sesion(self):
        servidor, puerto, solicitud, control, _ = self._crear_servidor(aceptar=False)
        visor = self._crear_visor(puerto)
        with self.assertRaisesRegex(transporte.ErrorConexion, "no aprobó"):
            visor.conectar()
        self._esperar_desocupado(servidor)
        solicitud.assert_called_once()
        control.assert_not_called()
        self.assertIsNone(visor._conexion)
        self.assertFalse(transporte._ruta_huella("127.0.0.1", puerto).exists())

    def test_protocolo_o_solicitud_invalidos_no_piden_aprobacion(self):
        servidor, puerto, solicitud, control, terminado = self._crear_servidor()
        for protocolo, datos in (
            ("camara-lan/2", None),
            (transporte._PROTOCOLO, b'{"tipo":"desconocido"}'),
            (transporte._PROTOCOLO, b"x" * (transporte._MAX_CONTROL + 1)),
        ):
            with self.subTest(protocolo=protocolo, longitud=len(datos or b"")):
                contexto = transporte.ssl.SSLContext(transporte.ssl.PROTOCOL_TLS_CLIENT)
                contexto.check_hostname = False
                contexto.verify_mode = transporte.ssl.CERT_NONE
                contexto.set_alpn_protocols([protocolo])
                with socket.create_connection(("127.0.0.1", puerto), timeout=5) as tcp:
                    with contexto.wrap_socket(tcp) as conexion:
                        try:
                            if datos is not None:
                                transporte._enviar_paquete(conexion, datos, time.monotonic() + 5)
                            self.assertEqual(conexion.recv(1), b"")
                        except ConnectionError:
                            pass
                self._esperar_desocupado(servidor)
                solicitud.assert_not_called()
                control.assert_not_called()
                terminado.assert_not_called()

    def test_identidad_distinta_rechazada_hasta_olvidarla_expresamente(self):
        primero, puerto, _, _, _ = self._crear_servidor()
        self._crear_visor(puerto).conectar()
        self._cerrar_servidor(primero)
        ruta_huella = transporte._ruta_huella("127.0.0.1", puerto)
        huella_anterior = ruta_huella.read_bytes()

        (self.datos / "identidad.pem").unlink()
        servidor, _, solicitud, _, _ = self._crear_servidor(puerto=puerto)
        with self.assertRaisesRegex(transporte.ErrorConexion, "identidad de la cámara cambió"):
            self._crear_visor(puerto).conectar()
        self._esperar_desocupado(servidor)
        solicitud.assert_not_called()
        self.assertEqual(ruta_huella.read_bytes(), huella_anterior)

        transporte.Visor.olvidar("127.0.0.1", puerto)
        self.assertFalse(ruta_huella.exists())
        self._crear_visor(puerto).conectar()
        solicitud.assert_called_once()
        self.assertNotEqual(ruta_huella.read_bytes(), huella_anterior)

    def test_limita_sexto_intento_y_se_recupera_al_vencer_la_ventana(self):
        servidor, puerto, solicitud, _, _ = self._crear_servidor(aceptar=False)
        for _ in range(5):
            visor = self._crear_visor(puerto)
            with self.assertRaisesRegex(transporte.ErrorConexion, "no aprobó"):
                visor.conectar()
            self._esperar_desocupado(servidor)

        visor_limitado = self._crear_visor(puerto)
        with self.assertRaises((transporte.ErrorConexion, OSError)):
            visor_limitado.conectar()
        self._esperar_desocupado(servidor)
        self.assertEqual(solicitud.call_count, 5)
        with servidor._bloqueo:
            self.assertEqual(len(servidor._intentos), 5)
            servidor._intentos = deque(
                instante - transporte._VENTANA_INTENTOS - 1
                for instante in servidor._intentos
            )

        solicitud.return_value = True
        visor = self._crear_visor(puerto)
        visor.conectar()
        self.assertEqual(solicitud.call_count, 6)
        with servidor._bloqueo:
            self.assertEqual(len(servidor._intentos), 1)
        visor.cerrar()
        self._esperar_desocupado(servidor)


if __name__ == "__main__":
    unittest.main()
