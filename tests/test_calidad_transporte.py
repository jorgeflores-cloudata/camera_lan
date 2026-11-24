"""Maximum-quality transport tests using local TLS and synthetic video only."""

import ipaddress
from pathlib import Path
import socket
import ssl
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

import transporte


class CalidadControlTests(unittest.TestCase):
    def test_maximum_accepts_thirty_fps(self):
        control = {"pausa": False, "calidad": "maxima", "fps": 30}
        self.assertEqual(transporte._control(control), control)

    def test_unknown_quality_and_invalid_fps_are_rejected(self):
        for calidad, fps in (("maxima", 31), ("maxima", 0), ("maxima", True),
                             ("máxima", 30), ("ｍａｘｉｍａ", 30), (None, 30)):
            with self.subTest(calidad=calidad, fps=fps):
                with self.assertRaises(transporte.ErrorConexion):
                    transporte._control({"pausa": False, "calidad": calidad, "fps": fps})


class CalidadTLSTests(unittest.TestCase):
    def setUp(self):
        redes = transporte._REDES_LAN + (ipaddress.IPv4Network("127.0.0.0/8"),)
        self.enterContext(patch.object(transporte, "_REDES_LAN", redes))
        temporal = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(transporte, "_datos_locales", return_value=Path(temporal)))

    def _esperar_sin_sesion(self, servidor):
        limite = time.monotonic() + 5
        while time.monotonic() < limite:
            with servidor._bloqueo:
                if servidor._sesion is None:
                    return
            time.sleep(.01)
        self.fail("The local test session did not finish.")

    def _cerrar(self, servidor):
        servidor.cerrar()
        self._esperar_sin_sesion(servidor)

    def _servidor(self, protocolo=None, forzar_maximo=False):
        solicitud = Mock(return_value=True)
        control = Mock(side_effect=lambda pausa, calidad, fps: {
            "pausa": pausa,
            "calidad": "maxima" if forzar_maximo else calidad,
            "fps": 30 if forzar_maximo else fps,
        })
        salir = Mock()
        servidor = transporte.Servidor("127.0.0.1", 8443, solicitud, control, Mock(), al_cerrar=salir)
        servidor._puerto = 0
        self.addCleanup(self._cerrar, servidor)
        tls_original = transporte._tls_servidor

        def tls_limitado():
            contexto = tls_original()
            contexto.set_alpn_protocols([protocolo])
            return contexto

        if protocolo is None:
            servidor.iniciar()
        else:
            with patch.object(transporte, "_tls_servidor", side_effect=tls_limitado):
                servidor.iniciar()
        return servidor, servidor._escucha.getsockname()[1], solicitud, control, salir

    def _visor(self, puerto):
        visor = transporte.Visor("127.0.0.1", puerto)
        self.addCleanup(visor.cerrar)
        visor.conectar()
        return visor

    def _raw(self, puerto, protocolo):
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE
        contexto.set_alpn_protocols([protocolo])
        tcp = socket.create_connection(("127.0.0.1", puerto), timeout=5)
        self.addCleanup(tcp.close)
        conexion = contexto.wrap_socket(tcp)
        self.addCleanup(conexion.close)
        transporte._enviar_json(conexion, {"tipo": "conectar"}, time.monotonic() + 5)
        respuesta = transporte._recibir_json(conexion, time.monotonic() + 5)
        self.assertTrue(respuesta["autorizada"])
        return conexion

    def test_four_k_detailed_jpeg_over_two_mib_crosses_tls(self):
        # Noise exercises the byte limit; a flat-color 4K frame does not.
        imagen = np.random.default_rng(0).integers(0, 256, (2160, 3840, 3), dtype=np.uint8)
        ok, codificada = cv2.imencode(".jpg", imagen, [cv2.IMWRITE_JPEG_QUALITY, 95])
        self.assertTrue(ok)
        jpeg = codificada.tobytes()
        self.assertGreater(len(jpeg), 2 * 1024 * 1024)
        self.assertTrue(transporte._es_jpeg(jpeg))

        servidor, puerto, solicitud, control, _ = self._servidor()
        visor = self._visor(puerto)
        self.assertEqual(visor._conexion.selected_alpn_protocol(), transporte._PROTOCOLO)
        self.assertEqual(solicitud.call_args.args[2:], ("maxima", 30))
        servidor.publicar(jpeg)

        recibida, efectivo = visor.leer(False, "maxima", 30)

        self.assertEqual(recibida, jpeg)
        self.assertEqual(efectivo, {"pausa": False, "calidad": "maxima", "fps": 30})
        control.assert_called_once_with(False, "maxima", 30)

    def test_new_viewer_limits_requests_to_older_hosts_and_preserves_exit(self):
        for protocolo in (transporte._PROTOCOLO_CIERRE, transporte._PROTOCOLO_ANTERIOR):
            with self.subTest(protocolo=protocolo):
                servidor, puerto, solicitud, control, salir = self._servidor(protocolo)
                visor = self._visor(puerto)
                self.assertEqual(solicitud.call_args.args[2:], ("alta", 15))
                self.assertEqual(
                    visor.cierre_remoto_disponible, protocolo == transporte._PROTOCOLO_CIERRE,
                )
                jpeg = b"\xff\xd8legacy-camera-frame\xff\xd9"
                servidor.publicar(jpeg)

                recibida, efectivo = visor.leer(False, "maxima", 30)

                self.assertEqual(recibida, jpeg)
                self.assertEqual(efectivo, {"pausa": False, "calidad": "alta", "fps": 15})
                control.assert_called_once_with(False, "alta", 15)
                if protocolo == transporte._PROTOCOLO_CIERRE:
                    visor.cerrar_anfitrion()
                    self._esperar_sin_sesion(servidor)
                    salir.assert_called_once_with()
                else:
                    with self.assertRaises(transporte.ErrorConexion):
                        visor.cerrar_anfitrion()
                    salir.assert_not_called()
                visor.cerrar()
                self._esperar_sin_sesion(servidor)

    def test_server_limits_both_request_and_response_for_legacy_peers(self):
        for protocolo in (transporte._PROTOCOLO_CIERRE, transporte._PROTOCOLO_ANTERIOR):
            with self.subTest(protocolo=protocolo):
                servidor, puerto, solicitud, control, _ = self._servidor(forzar_maximo=True)
                conexion = self._raw(puerto, protocolo)
                self.assertEqual(solicitud.call_args.args[2:], ("alta", 15))
                limite = time.monotonic() + 5
                transporte._enviar_json(
                    conexion, {"pausa": False, "calidad": "maxima", "fps": 30}, limite,
                )

                efectivo = transporte._recibir_json(conexion, limite)
                self.assertEqual(efectivo, {"pausa": False, "calidad": "alta", "fps": 15})
                self.assertEqual(transporte._recibir_paquete(conexion, transporte._MAX_JPEG, limite), b"")
                control.assert_called_once_with(False, "alta", 15)
                conexion.close()
                self._esperar_sin_sesion(servidor)


if __name__ == "__main__":
    unittest.main()
