"""Pruebas del cierre remoto por TLS, sin iniciar la cámara ni procesos."""

import ipaddress
from pathlib import Path
import socket
import ssl
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import transporte


class CierreRemotoTests(unittest.TestCase):
    def setUp(self):
        redes = transporte._REDES_LAN + (ipaddress.IPv4Network("127.0.0.0/8"),)
        self.enterContext(patch.object(transporte, "_REDES_LAN", redes))
        temporal = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(transporte, "_datos_locales", return_value=Path(temporal)))

    def _servidor(self, al_cerrar=None, puerto=None):
        solicitud = Mock(return_value=True)
        control = Mock(side_effect=lambda pausa, calidad, fps: {
            "pausa": pausa, "calidad": calidad, "fps": fps,
        })
        servidor = transporte.Servidor(
            "127.0.0.1", 8443 if puerto is None else puerto,
            solicitud, control, Mock(), al_cerrar=al_cerrar,
        )
        if puerto is None:
            servidor._puerto = 0
        self.addCleanup(self._cerrar, servidor)
        servidor.iniciar()
        return servidor, servidor._escucha.getsockname()[1], solicitud

    def _visor(self, puerto):
        visor = transporte.Visor("127.0.0.1", puerto)
        self.addCleanup(visor.cerrar)
        return visor

    def _esperar(self, condicion, mensaje):
        limite = time.monotonic() + 5
        while time.monotonic() < limite:
            if condicion():
                return
            time.sleep(.01)
        self.fail(mensaje)

    def _cerrar(self, servidor):
        servidor.cerrar()
        self._esperar(lambda: servidor._sesion is None, "La sesión no se cerró.")

    def _conexion_raw(self, puerto, protocolo):
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE
        contexto.set_alpn_protocols([protocolo])
        tcp = socket.create_connection(("127.0.0.1", puerto), timeout=5)
        self.addCleanup(tcp.close)
        conexion = contexto.wrap_socket(tcp)
        self.addCleanup(conexion.close)
        return conexion

    def _comprobar_stream(self, servidor, visor):
        jpeg = b"\xff\xd8fotograma-remoto\xff\xd9"
        servidor.publicar(jpeg)
        imagen, efectivo = visor.leer(False, "media", 8)
        self.assertEqual(imagen, jpeg)
        self.assertEqual(efectivo, {"pausa": False, "calidad": "media", "fps": 8})

    def test_cierre_confirmado_llama_callback_una_vez_y_libera_puerto(self):
        al_cerrar = Mock()
        servidor, puerto, solicitud = self._servidor(al_cerrar=al_cerrar)
        visor = self._visor(puerto)
        visor.conectar()
        self.assertTrue(visor.cierre_remoto_disponible)
        solicitud.assert_called_once()

        self.assertIsNone(visor.cerrar_anfitrion())

        self._esperar(lambda: al_cerrar.call_count == 1, "No se recibió el cierre remoto.")
        self._esperar(lambda: servidor._sesion is None, "La sesión remota sigue activa.")
        al_cerrar.assert_called_once_with()
        self.assertIsNone(servidor._escucha)
        with self.assertRaises(transporte.ErrorConexion):
            visor.cerrar_anfitrion()
        al_cerrar.assert_called_once_with()
        self._servidor(puerto=puerto)

    def test_sin_callback_no_ofrece_cierre_y_conserva_transmision(self):
        servidor, puerto, _ = self._servidor()
        visor = self._visor(puerto)
        visor.conectar()
        self.assertFalse(visor.cierre_remoto_disponible)
        with self.assertRaises(transporte.ErrorConexion):
            visor.cerrar_anfitrion()
        self._comprobar_stream(servidor, visor)

    def test_cierre_antes_de_conectar_se_rechaza(self):
        visor = self._visor(8443)
        self.assertFalse(visor.cierre_remoto_disponible)
        with self.assertRaises(transporte.ErrorConexion):
            visor.cerrar_anfitrion()

    def test_comando_antes_de_la_sesion_no_cierra_el_anfitrion(self):
        al_cerrar = Mock()
        servidor, puerto, solicitud = self._servidor(al_cerrar=al_cerrar)
        conexion = self._conexion_raw(puerto, transporte._PROTOCOLO)
        transporte._enviar_json(conexion, {"tipo": "cerrar_anfitrion"}, time.monotonic() + 5)
        try:
            self.assertEqual(conexion.recv(1), b"")
        except ConnectionError:
            pass
        self._esperar(lambda: servidor._sesion is None, "No se descartó la solicitud inválida.")
        al_cerrar.assert_not_called()
        solicitud.assert_not_called()
        self.assertIsNotNone(servidor._escucha)

    def test_cliente_anterior_mantiene_respuesta_y_stream_compatibles(self):
        al_cerrar = Mock()
        servidor, puerto, solicitud = self._servidor(al_cerrar=al_cerrar)
        conexion = self._conexion_raw(puerto, transporte._PROTOCOLO_ANTERIOR)
        self.assertEqual(conexion.selected_alpn_protocol(), transporte._PROTOCOLO_ANTERIOR)
        limite = time.monotonic() + 5
        transporte._enviar_json(conexion, {"tipo": "conectar"}, limite)
        self.assertEqual(
            transporte._recibir_json(conexion, limite),
            {"tipo": "resultado", "autorizada": True},
        )
        solicitud.assert_called_once()
        jpeg = b"\xff\xd8fotograma-anterior\xff\xd9"
        servidor.publicar(jpeg)
        control = {"pausa": False, "calidad": "media", "fps": 8}
        transporte._enviar_json(conexion, control, limite)
        self.assertEqual(transporte._recibir_json(conexion, limite), control)
        self.assertEqual(transporte._recibir_paquete(conexion, transporte._MAX_JPEG, limite), jpeg)
        al_cerrar.assert_not_called()

    def test_cliente_nuevo_con_protocolo_anterior_no_ofrece_cierre(self):
        tls_original = transporte._tls_servidor

        def tls_anterior():
            contexto = tls_original()
            contexto.set_alpn_protocols([transporte._PROTOCOLO_ANTERIOR])
            return contexto

        al_cerrar = Mock()
        with patch.object(transporte, "_tls_servidor", side_effect=tls_anterior):
            servidor, puerto, _ = self._servidor(al_cerrar=al_cerrar)
        visor = self._visor(puerto)
        visor.conectar()
        self.assertEqual(visor._conexion.selected_alpn_protocol(), transporte._PROTOCOLO_ANTERIOR)
        self.assertFalse(visor.cierre_remoto_disponible)
        with self.assertRaises(transporte.ErrorConexion):
            visor.cerrar_anfitrion()
        self._comprobar_stream(servidor, visor)
        al_cerrar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
