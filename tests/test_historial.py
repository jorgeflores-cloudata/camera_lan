"""Pruebas del historial del visor sin conexiones de red ni cámara."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import historial


class HistorialTests(unittest.TestCase):
    def setUp(self):
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.datos = Path(temporal.name) / "CamaraLAN"
        reemplazo = patch.object(historial, "_datos_locales", return_value=self.datos)
        reemplazo.start()
        self.addCleanup(reemplazo.stop)

    def test_leer_sin_historial_no_crea_almacenamiento(self):
        self.assertEqual(historial.leer_recientes(), [])
        self.assertFalse(self.datos.exists())

    def test_guardar_conserva_ip_y_puerto_mas_recientes_sin_duplicados(self):
        historial.registrar_conexion(" 192.168.0.3 ", 8443)
        historial.registrar_conexion("192.168.0.3", 8444)
        historial.registrar_conexion("192.168.0.4", 8443)

        self.assertEqual(historial.registrar_conexion("192.168.0.3", 8443), [
            ("192.168.0.3", 8443), ("192.168.0.4", 8443), ("192.168.0.3", 8444),
        ])
        self.assertEqual(historial.leer_recientes(), [
            ("192.168.0.3", 8443), ("192.168.0.4", 8443), ("192.168.0.3", 8444),
        ])

    def test_guarda_las_ultimas_diez_conexiones(self):
        for puerto in range(8443, 8454):
            historial.registrar_conexion("192.168.0.3", puerto)

        self.assertEqual(historial.leer_recientes(), [("192.168.0.3", puerto) for puerto in range(8453, 8443, -1)])

    def test_guarda_conexiones_concurrentes_sin_perder_entradas(self):
        barrera = threading.Barrier(3, timeout=2)

        def guardar(puerto):
            barrera.wait()
            return historial.registrar_conexion("192.168.0.3", puerto)

        with ThreadPoolExecutor(max_workers=3) as ejecuciones:
            resultados = list(ejecuciones.map(guardar, (8443, 8444, 8445)))

        self.assertEqual(sorted(len(resultado) for resultado in resultados), [1, 2, 3])
        self.assertCountEqual(historial.leer_recientes(), [("192.168.0.3", puerto) for puerto in (8443, 8444, 8445)])

    def test_base_de_datos_danada_se_conserva(self):
        self.datos.mkdir()
        ruta = self.datos / historial._ARCHIVO
        ruta.write_bytes(b"archivo danado")

        with self.assertRaises(historial.ErrorHistorial):
            historial.leer_recientes()
        with self.assertRaises(historial.ErrorHistorial):
            historial.registrar_conexion("192.168.0.3", 8443)

        self.assertEqual(ruta.read_bytes(), b"archivo danado")

    def test_importa_destinos_pin_preexistentes_antes_del_primer_guardado(self):
        carpeta = self.datos / "anfitriones"
        carpeta.mkdir(parents=True)
        primero = carpeta / "192.168.0.3-8443.sha256"
        segundo = carpeta / "192.168.0.4-8444.sha256"
        primero.write_bytes(bytes(32))
        segundo.write_bytes(bytes(32))
        os.utime(primero, (1_700_000_000, 1_700_000_000))
        os.utime(segundo, (1_700_000_001, 1_700_000_001))
        (carpeta / "192.168.0.5-8443.sha256").write_bytes(b"invalid")
        (carpeta / "203.0.113.3-8443.sha256").write_bytes(bytes(32))
        (carpeta / "192.168.0.5-08443.sha256").write_bytes(bytes(32))
        (carpeta / "192.168.0.5-65536.sha256").write_bytes(bytes(32))

        anteriores = [("192.168.0.4", 8444), ("192.168.0.3", 8443)]
        self.assertEqual(historial.leer_recientes(), anteriores)
        ruta = self.datos / historial._ARCHIVO
        self.assertFalse(ruta.exists())
        ruta.touch()  # An interrupted first write can leave an empty file.
        self.assertEqual(historial.leer_recientes(), anteriores)

        actualizados = [("192.168.0.7", 8443), *anteriores]
        self.assertEqual(historial.registrar_conexion("192.168.0.7", 8443), actualizados)
        primero.unlink()
        (carpeta / "192.168.0.8-8443.sha256").write_bytes(bytes(32))
        self.assertEqual(historial.leer_recientes(), actualizados)
