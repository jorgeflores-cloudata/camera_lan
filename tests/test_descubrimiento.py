"""Pruebas del descubrimiento IPv4 sin cámaras ni equipos externos."""

import ipaddress
import socket
import sys
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

import descubrimiento as lan


_NONCE = bytes(range(16))


def _respuesta(nombre, puerto=8443, nonce=_NONCE):
    return lan._RESPUESTA + nonce + puerto.to_bytes(2, "big") + nombre


class DescubrimientoTests(unittest.TestCase):
    def test_solicitud_exige_version_nonce_y_relleno(self):
        solicitud = lan._SOLICITUD + _NONCE + lan._RELLENO
        self.assertEqual(lan._leer_solicitud(solicitud), _NONCE)
        self.assertLessEqual(len(_respuesta(b"A" * lan._MAX_NOMBRE)), len(solicitud))
        for invalida in (solicitud[:-1], solicitud + b"x", solicitud[:-1] + b"x", b"x" + solicitud[1:]):
            with self.subTest(invalida=invalida):
                self.assertIsNone(lan._leer_solicitud(invalida))

    def test_respuesta_exige_origen_privado_version_nonce_y_metadata_valida(self):
        respuesta = _respuesta("Cámara".encode("utf-8"))
        self.assertEqual(lan._leer_respuesta(respuesta, "192.168.0.20", _NONCE),
                         lan.CamaraLAN("Cámara", "192.168.0.20", 8443))
        for origen in ("127.0.0.1", "fe80::2", "203.0.113.2", "239.255.42.84"):
            with self.subTest(origen=origen):
                self.assertIsNone(lan._leer_respuesta(respuesta, origen, _NONCE))
        for invalida in (
            _respuesta(b"Otra", nonce=b"x" * 16),
            _respuesta(b"Host", puerto=0), _respuesta(b""), _respuesta(b" "),
            _respuesta(b"Host\nFalso"), _respuesta(b"\xff"), _respuesta(b"x" * 64), b"x" + respuesta[1:],
        ):
            with self.subTest(invalida=invalida):
                self.assertIsNone(lan._leer_respuesta(invalida, "192.168.0.20", _NONCE))

    def test_busqueda_descarta_otro_nonce_y_deduplica_el_origen_y_puerto(self):
        lector = MagicMock()
        lector.__enter__.return_value = lector
        lector.recvfrom.side_effect = [
            (_respuesta(b"Ignorada", nonce=b"x" * 16), ("192.168.0.30", 50001)),
            (_respuesta(b"Zeta"), ("192.168.0.20", 50000)),
            (_respuesta(b"Duplicada"), ("192.168.0.20", 50001)),
            (_respuesta(b"Alfa", puerto=8444), ("192.168.0.20", 50000)),
        ]
        with (
            patch.object(lan, "_interfaces_locales", return_value={"192.168.0.21": None}),
            patch.object(lan, "_MAX_RESULTADOS", 2),
            patch.object(lan.secrets, "token_bytes", return_value=_NONCE),
            patch.object(lan.socket, "socket", return_value=lector),
            patch.object(lan.select, "select", return_value=([lector], [], [])),
        ):
            self.assertEqual(lan.buscar_camaras(), [
                lan.CamaraLAN("Alfa", "192.168.0.20", 8444),
                lan.CamaraLAN("Zeta", "192.168.0.20", 8443),
            ])
        lector.sendto.assert_called_once_with(lan._SOLICITUD + _NONCE + lan._RELLENO, (lan._GRUPO, lan._PUERTO))

    def test_busqueda_cancelada_no_carga_interfaces(self):
        cancelada = Event()
        cancelada.set()
        with patch.object(lan, "_interfaces_locales") as interfaces:
            self.assertEqual(lan.buscar_camaras(cancelada), [])
        interfaces.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "El anfitrión usa Windows.")
    def test_busqueda_real_por_loopback_y_cierre_del_anuncio(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as temporal:
            temporal.bind(("127.0.0.1", 0))
            puerto_descubrimiento = temporal.getsockname()[1]
        red = ipaddress.IPv4Network("127.0.0.0/8")
        with (
            patch.object(lan, "_PUERTO", puerto_descubrimiento),
            patch.object(lan, "_REDES_PRIVADAS", (red,)),
            patch.object(lan, "_interfaces_locales", return_value={"127.0.0.1": red}),
            patch.object(lan, "_TIEMPO_BUSQUEDA", 0.4),
            patch.object(lan, "_nombre_local", return_value=b"Camarita"),
        ):
            anuncio = lan.AnuncioLAN("127.0.0.1", 8443)
            try:
                anuncio.iniciar()
                self.assertEqual(lan.buscar_camaras(), [lan.CamaraLAN("Camarita", "127.0.0.1", 8443)])
            finally:
                anuncio.cerrar()
            anuncio.cerrar()
            self.assertEqual(lan.buscar_camaras(), [])


if __name__ == "__main__":
    unittest.main()
