"""Pruebas de la vista previa local sin abrir una cámara ni una ventana."""

from io import BytesIO
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import anfitrion


class _Variable:
    def __init__(self, value=None):
        self.valor = value

    def get(self):
        return self.valor

    def set(self, valor):
        self.valor = valor


class VistaPreviaTests(unittest.TestCase):
    def setUp(self):
        self.raiz = Mock()
        self.raiz.state.return_value = "normal"
        self.raiz.withdraw.side_effect = lambda: setattr(self.raiz.state, "return_value", "withdrawn")
        self.raiz.deiconify.side_effect = lambda: setattr(self.raiz.state, "return_value", "normal")
        with (
            patch.object(anfitrion.tk, "StringVar", _Variable),
            patch.object(anfitrion.tk, "BooleanVar", _Variable),
            patch.object(anfitrion._Anfitrion, "_construir"),
            patch.object(anfitrion, "_direcciones_locales", return_value=["192.168.0.2"]),
        ):
            self.host = anfitrion._Anfitrion(self.raiz)

        self.host.campos = []
        self.host.habilitar = Mock()
        self.host.detener = Mock()
        self.host.pausar = Mock()
        self.host.indicador = Mock()
        self.host.panel_solicitud = Mock()
        self.host.previa = Mock()
        self.host.area_previa = Mock()
        self.host.area_previa.winfo_width.return_value = 100
        self.host.area_previa.winfo_height.return_value = 100

        self.escritorio = self.enterContext(patch.object(anfitrion, "_escritorio_disponible", return_value=True))
        self.crear_captura = self.enterContext(patch.object(anfitrion, "_Captura"))
        self.servidor = self.enterContext(patch.object(anfitrion, "Servidor")).return_value
        self.anuncio = self.enterContext(patch.object(anfitrion, "AnuncioLAN")).return_value
        self.crear_foto = self.enterContext(patch.object(anfitrion.ImageTk, "PhotoImage"))

        salida = BytesIO()
        Image.new("RGB", (20, 20)).save(salida, format="JPEG")
        self.jpeg = salida.getvalue()
        self.captura = self.crear_captura.return_value
        self.captura.error = None
        self.captura.tomar_fotograma.return_value = self.jpeg

    def _solicitud(self):
        return anfitrion._Solicitud("192.168.0.3", threading.Event(), time.monotonic() + 30)

    def test_al_habilitar_muestra_previa_sin_enviar_fotogramas(self):
        self.host._habilitar()
        self.crear_captura.assert_called_once_with(0, "media", 10)

        self.host._bucle()

        self.crear_foto.assert_called_once()
        self.host.previa.configure.assert_called_with(image=self.crear_foto.return_value, text="")
        self.servidor.publicar.assert_not_called()
        self.assertFalse(self.host.autorizada)

    def test_la_solicitud_pendiente_solo_publica_despues_de_aceptar(self):
        self.host._habilitar()
        solicitud = self._solicitud()
        self.host._mostrar_solicitud(solicitud)
        self.host._mostrar_fotograma()
        self.servidor.publicar.assert_not_called()
        self.assertFalse(solicitud.respondida.is_set())

        self.host._responder(True)
        self.host._mostrar_fotograma()

        self.crear_captura.assert_called_once()
        self.assertTrue(solicitud.aceptada)
        self.servidor.publicar.assert_called_once_with(self.jpeg)

        solicitud.cancelada.set()
        self.host._mostrar_fotograma()
        fotogramas = [argumentos.args[0] for argumentos in self.servidor.publicar.call_args_list if argumentos.args[0]]
        self.assertEqual(fotogramas, [self.jpeg])

    def test_ocultar_apaga_la_previa_y_exige_reanudarla(self):
        self.host._habilitar()

        self.host._ocultar()
        self.host._bucle()

        self.raiz.withdraw.assert_called_once_with()
        self.captura.detener.assert_called_once()
        self.crear_captura.assert_called_once()
        self.servidor.cerrar.assert_not_called()
        self.assertIsNone(self.host.captura)
        self.assertIs(self.host.servidor, self.servidor)

        self.captura.viva.return_value = False
        self.host._abrir()
        self.raiz.deiconify.assert_called_once_with()
        self.host._bucle()
        self.crear_captura.assert_called_once()

        self.host._alternar_pausa()
        self.host._bucle()
        self.assertEqual(self.crear_captura.call_count, 2)

    def test_minimizar_a_la_bandeja_conserva_la_previa_local(self):
        self.host._habilitar()
        self.host._bucle()

        self.host._minimizar_a_bandeja()
        self.host.ultima_comprobacion = time.monotonic() - 1
        self.host._bucle()

        self.raiz.withdraw.assert_called_once_with()
        self.crear_captura.assert_called_once()
        self.captura.detener.assert_not_called()
        self.servidor.cerrar.assert_not_called()
        self.anuncio.cerrar.assert_not_called()
        self.servidor.publicar.assert_not_called()
        self.assertIs(self.host.captura, self.captura)
        self.assertFalse(self.host.pausa_local)

        self.crear_foto.reset_mock()
        self.host._abrir()
        self.host._bucle()

        self.raiz.deiconify.assert_called_once_with()
        self.crear_captura.assert_called_once()
        self.crear_foto.assert_called_once()
        self.host.previa.configure.assert_called_with(image=self.crear_foto.return_value, text="")

    def test_minimizar_desde_el_titulo_mantiene_el_video_y_los_controles(self):
        self.host.ajustes_remotos.set(True)
        self.host._habilitar()
        solicitud = self._solicitud()
        self.host._mostrar_solicitud(solicitud)
        self.host._responder(True)
        self.assertTrue(solicitud.aceptada)
        self.host._bucle()
        self.servidor.publicar.reset_mock()
        self.raiz.state.return_value = "iconic"

        self.host._al_ocultar(SimpleNamespace(widget=self.raiz))
        control = anfitrion._Control(False, "baja", 5, time.monotonic() + 30)
        self.host.eventos.put(("control", self.host.generacion, control))
        self.host.ultima_comprobacion = time.monotonic() - 1
        self.host._bucle()

        self.raiz.withdraw.assert_called_once_with()
        self.captura.detener.assert_not_called()
        self.servidor.cerrar.assert_not_called()
        self.anuncio.cerrar.assert_not_called()
        self.assertTrue(self.host.autorizada)
        self.assertTrue(control.respondido.is_set())
        self.assertEqual(control.respuesta, {"pausa": False, "calidad": "baja", "fps": 5})
        self.servidor.publicar.assert_called_once_with(self.jpeg)

    def test_el_visor_puede_pausar_y_reanudar_mientras_el_anfitrion_esta_en_bandeja(self):
        self.host.ajustes_remotos.set(True)
        self.host._habilitar()
        self.host._mostrar_solicitud(self._solicitud())
        self.host._responder(True)
        self.host._minimizar_a_bandeja()

        pausar = anfitrion._Control(True, "media", 10, time.monotonic() + 30)
        self.host._aplicar_control(pausar)
        self.captura.detener.assert_called_once_with()
        self.servidor.publicar.assert_called_once_with(None)
        self.assertTrue(pausar.respuesta["pausa"])

        self.captura.viva.return_value = False
        reanudar = anfitrion._Control(False, "media", 10, time.monotonic() + 30)
        self.host._aplicar_control(reanudar)
        self.host._bucle()

        self.assertTrue(self.host.autorizada)
        self.assertFalse(reanudar.respuesta["pausa"])
        self.assertEqual(self.crear_captura.call_count, 2)
        self.servidor.publicar.assert_called_with(self.jpeg)

    def test_solicitud_en_bandeja_espera_sin_restaurar_y_puede_vencer(self):
        self.host.bandeja = Mock()
        self.host._habilitar()
        self.host._minimizar_a_bandeja()
        solicitud = self._solicitud()
        self.host.eventos.put(("solicitud", self.host.generacion, solicitud))
        self.host.ultima_comprobacion = time.monotonic() - 1

        self.host._bucle()

        self.assertIn("Request pending", self.host.bandeja.title)
        self.raiz.deiconify.assert_not_called()
        self.raiz.lift.assert_not_called()
        self.crear_captura.assert_called_once()
        self.assertIs(self.host.captura, self.captura)
        self.assertIs(self.host.solicitud, solicitud)
        self.assertFalse(solicitud.respondida.is_set())
        self.servidor.publicar.assert_not_called()

        solicitud.vence = time.monotonic() - 1
        self.host._bucle()

        self.assertIsNone(self.host.solicitud)
        self.assertTrue(solicitud.respondida.is_set())
        self.assertFalse(solicitud.aceptada)
        self.raiz.deiconify.assert_not_called()
        self.servidor.cerrar.assert_not_called()

    def test_abrir_desde_la_bandeja_permite_aprobar_la_solicitud(self):
        self.host._habilitar()
        solicitud = self._solicitud()
        self.host._mostrar_solicitud(solicitud)
        self.host._minimizar_a_bandeja()

        self.host._bucle()

        self.assertIs(self.host.solicitud, solicitud)
        self.assertFalse(solicitud.respondida.is_set())
        self.assertFalse(self.host.autorizada)
        self.servidor.publicar.assert_not_called()

        self.host.eventos.put(("abrir", None, None))
        self.host._bucle()
        self.host._responder(True)
        self.host._bucle()

        self.raiz.deiconify.assert_called_once_with()
        self.assertTrue(solicitud.aceptada)
        self.servidor.publicar.assert_called_once_with(self.jpeg)

    def test_aprobar_requiere_una_ventana_visible(self):
        self.host._habilitar()
        self.host._minimizar_a_bandeja()
        solicitud = self._solicitud()
        self.host._mostrar_solicitud(solicitud)

        self.host._responder(True)
        self.host._bucle()

        self.assertFalse(solicitud.aceptada)
        self.assertFalse(self.host.autorizada)
        self.servidor.publicar.assert_not_called()

    def test_bloquear_el_escritorio_apaga_la_previa_y_rechaza_la_solicitud(self):
        self.host._habilitar()
        solicitud = self._solicitud()
        self.host._mostrar_solicitud(solicitud)
        self.host._minimizar_a_bandeja()
        self.escritorio.return_value = False

        self.host._bucle()

        self.captura.detener.assert_called_once()
        self.crear_captura.assert_called_once()
        self.assertTrue(solicitud.respondida.is_set())
        self.assertFalse(solicitud.aceptada)
        self.assertIsNone(self.host.captura)
        self.servidor.cerrar.assert_not_called()

    def test_bloquear_el_escritorio_cierra_la_transmision_en_bandeja(self):
        self.host._habilitar()
        solicitud = self._solicitud()
        self.host._mostrar_solicitud(solicitud)
        self.host._responder(True)
        self.host._minimizar_a_bandeja()
        self.escritorio.return_value = False

        self.host._bucle()

        self.captura.detener.assert_called_once_with()
        self.servidor.cerrar.assert_called_once_with()
        self.servidor.publicar.assert_not_called()
        self.assertFalse(self.host.autorizada)

    def test_cerrar_conexion_detiene_la_previa_y_libera_el_servidor(self):
        self.host._habilitar()
        self.host._minimizar_a_bandeja()

        self.host.eventos.put(("cerrar_conexion", self.host.generacion, None))
        self.host._bucle()

        self.captura.detener.assert_called_once()
        self.anuncio.cerrar.assert_called_once()
        self.servidor.cerrar.assert_called_once()
        self.assertIsNone(self.host.captura)
        self.assertIsNone(self.host.servidor)
