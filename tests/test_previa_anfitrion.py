"""Pruebas de la vista previa local sin abrir una cámara ni una ventana."""

from io import BytesIO
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

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
            patch.object(anfitrion, "Acceso"),
        ):
            self.host = anfitrion._Anfitrion(self.raiz)

        self.host.acceso.solicitar.side_effect = lambda accion, _titulo: accion()
        self.host.campos = []
        self.host.habilitar = Mock()
        self.host.detener = Mock()
        self.host.pausar = Mock()
        self.host.indicador = Mock()
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

    def _inicio(self, vence=None, generacion=None):
        inicio = anfitrion._InicioSesion(
            cancelada=threading.Event(),
            vence=time.monotonic() + 30 if vence is None else vence,
        )
        if generacion is None:
            generacion = self.host.generacion
        self.host.eventos.put(("iniciar", generacion, inicio))
        return inicio

    def _conectar_visor(self):
        inicio = self._inicio()
        self.host._bucle()
        self.assertTrue(inicio.respondida.is_set())
        self.assertTrue(inicio.aceptada)
        return inicio

    def test_al_habilitar_muestra_previa_sin_enviar_fotogramas(self):
        self.host._habilitar()
        self.crear_captura.assert_called_once_with(0, "media", 10)

        self.host._bucle()

        self.crear_foto.assert_called_once()
        self.host.previa.configure.assert_called_with(image=self.crear_foto.return_value, text="")
        self.servidor.publicar.assert_not_called()
        self.assertFalse(self.host.autorizada)

    def test_un_visor_conecta_sin_aprobacion_y_recibe_video(self):
        self.host._habilitar()
        self.host._mostrar_fotograma()
        self.servidor.publicar.assert_not_called()

        inicio = self._conectar_visor()

        self.crear_captura.assert_called_once()
        self.raiz.lift.assert_not_called()
        self.servidor.publicar.assert_called_once_with(self.jpeg)

        inicio.cancelada.set()
        self.host._mostrar_fotograma()
        fotogramas = [argumentos.args[0] for argumentos in self.servidor.publicar.call_args_list if argumentos.args[0]]
        self.assertEqual(fotogramas, [self.jpeg])

    def test_el_visor_solo_conecta_despues_de_habilitar_compartir(self):
        inicio = self._inicio()

        self.host._bucle()

        self.assertTrue(inicio.respondida.is_set())
        self.assertFalse(inicio.aceptada)
        self.crear_captura.assert_not_called()
        self.servidor.publicar.assert_not_called()

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
        self._conectar_visor()
        self.servidor.publicar.reset_mock()
        self.raiz.state.return_value = "iconic"

        self.host._al_ocultar(SimpleNamespace(widget=self.raiz))

        self.raiz.deiconify.assert_not_called()
        self.host.acceso.solicitar.assert_not_called()

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

    def test_botones_de_la_ventana_ejecutan_acciones_sin_pin(self):
        with patch.object(anfitrion, "ttk") as ttk, patch.object(anfitrion, "tk"):
            self.host._construir(["192.168.0.2"])
        botones = {
            llamada.kwargs["text"]: llamada.kwargs["command"]
            for llamada in ttk.Button.call_args_list
        }

        botones["Enable LAN sharing"]()
        self.servidor.iniciar.assert_called_once_with()
        self.crear_captura.assert_called_once()

        botones["Pause camera"]()
        self.captura.detener.assert_called_once_with()
        self.assertTrue(self.host.pausa_local)

        botones["Close connection"]()
        self.servidor.cerrar.assert_called_once_with()

        botones["Minimize to tray"]()
        self.raiz.withdraw.assert_called_once_with()
        self.host.acceso.solicitar.assert_not_called()

    def test_el_visor_puede_pausar_y_reanudar_mientras_el_anfitrion_esta_en_bandeja(self):
        self.host.ajustes_remotos.set(True)
        self.host._habilitar()
        self._conectar_visor()
        self.servidor.publicar.reset_mock()
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

    def test_un_visor_conecta_en_bandeja_sin_restaurar_la_ventana(self):
        self.host.bandeja = Mock()
        self.host._habilitar()
        self.host._minimizar_a_bandeja()
        self.host.ultima_comprobacion = time.monotonic() - 1

        self._conectar_visor()

        self.raiz.deiconify.assert_not_called()
        self.raiz.lift.assert_not_called()
        self.crear_captura.assert_called_once()
        self.assertIs(self.host.captura, self.captura)
        self.servidor.publicar.assert_called_once_with(self.jpeg)

    def test_un_acceso_vencido_o_de_otra_generacion_se_rechaza(self):
        self.host._habilitar()
        vencido = self._inicio(vence=time.monotonic() - 1)
        anterior = self._inicio(generacion=self.host.generacion - 1)

        self.host._bucle()

        for inicio in (vencido, anterior):
            self.assertTrue(inicio.respondida.is_set())
            self.assertFalse(inicio.aceptada)
        self.assertFalse(self.host.autorizada)
        self.servidor.publicar.assert_not_called()

    def test_otro_visor_puede_conectar_tras_una_desconexion(self):
        self.host._habilitar()
        primero = self._conectar_visor()
        self.servidor.publicar.reset_mock()
        primero.cancelada.set()
        self.host.eventos.put(("terminar", self.host.generacion, None))

        self.host._bucle()

        self.assertFalse(self.host.autorizada)
        self.assertIs(self.host.servidor, self.servidor)
        self.servidor.cerrar.assert_not_called()
        self.anuncio.cerrar.assert_not_called()
        self.captura.detener.assert_not_called()
        self.assertNotIn(call(self.jpeg), self.servidor.publicar.call_args_list)

        segundo = self._conectar_visor()

        self.assertIs(self.host.cancelacion_activa, segundo.cancelada)
        self.crear_captura.assert_called_once()
        self.servidor.publicar.assert_called_with(self.jpeg)

    def test_cerrar_la_ventana_suspende_el_acceso_desde_la_lan(self):
        self.host._habilitar()
        self.host._ocultar()
        inicio = self._inicio()

        self.host._bucle()

        self.assertTrue(inicio.respondida.is_set())
        self.assertFalse(inicio.aceptada)
        self.assertFalse(self.host.autorizada)
        self.servidor.publicar.assert_not_called()

    def test_bloquear_el_escritorio_apaga_la_previa_y_rechaza_la_solicitud(self):
        self.host._habilitar()
        self.host._minimizar_a_bandeja()
        self.escritorio.return_value = False
        inicio = self._inicio()

        self.host._bucle()

        self.captura.detener.assert_called_once()
        self.crear_captura.assert_called_once()
        self.assertTrue(inicio.respondida.is_set())
        self.assertFalse(inicio.aceptada)
        self.assertIsNone(self.host.captura)
        self.servidor.cerrar.assert_not_called()

    def test_bloquear_el_escritorio_cierra_la_transmision_en_bandeja(self):
        self.host._habilitar()
        self._conectar_visor()
        self.servidor.publicar.reset_mock()
        self.host._minimizar_a_bandeja()
        self.escritorio.return_value = False

        self.host._bucle()

        self.captura.detener.assert_called_once_with()
        self.servidor.cerrar.assert_called_once_with()
        self.servidor.publicar.assert_not_called()
        self.assertFalse(self.host.autorizada)

    def test_abrir_y_salir_desde_la_bandeja_exigen_pin(self):
        self.host._minimizar_a_bandeja()
        self.host.acceso.solicitar.side_effect = None

        for evento, titulo, metodo in (("abrir", "Open", "_abrir"), ("salir", "Exit", "_salir")):
            with self.subTest(evento=evento), patch.object(self.host, metodo) as accion:
                self.host.eventos.put((evento, None, None))

                self.host._bucle()

                accion.assert_not_called()
                self.host.acceso.solicitar.assert_called_once()
                autorizada, titulo_solicitud = self.host.acceso.solicitar.call_args.args
                self.assertEqual(titulo_solicitud, titulo)
                autorizada()
                accion.assert_called_once_with()
                self.host.acceso.solicitar.reset_mock()

    def test_cerrar_conexion_detiene_la_previa_y_libera_el_servidor(self):
        self.host._habilitar()
        self.host._minimizar_a_bandeja()
        self.host.acceso.solicitar.side_effect = None

        self.host.eventos.put(("cerrar_conexion", self.host.generacion, None))
        self._conectar_visor()

        self.servidor.publicar.assert_called_once_with(self.jpeg)
        self.captura.detener.assert_not_called()
        self.anuncio.cerrar.assert_not_called()
        self.servidor.cerrar.assert_not_called()
        self.host.acceso.solicitar.assert_called_once()
        cerrar, titulo = self.host.acceso.solicitar.call_args.args
        self.assertEqual(titulo, "Close connection")

        cerrar()

        self.captura.detener.assert_called_once()
        self.anuncio.cerrar.assert_called_once()
        self.servidor.cerrar.assert_called_once()
        self.assertIsNone(self.host.captura)
        self.assertIsNone(self.host.servidor)
