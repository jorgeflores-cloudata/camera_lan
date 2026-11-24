"""Pruebas del cierre remoto desde el visor, sin ventanas ni cámara."""

import queue
import unittest
from unittest.mock import Mock, patch

import visor


class CierreRemotoVisorTests(unittest.TestCase):
    def setUp(self):
        self.transporte = Mock(cierre_remoto_disponible=True)
        self.sesion = visor._Sesion(self.transporte, visor._Ajustes())
        self.panel = visor._Panel.__new__(visor._Panel)
        self.panel.raiz = Mock()
        self.panel.sesion = self.sesion
        self.panel.conectada = True
        self.panel.cerrando = False
        self.panel.estado = Mock()
        self.panel._actualizar_controles = Mock()

    def _recibir(self):
        with patch.object(visor, "registrar_conexion", return_value=[]):
            visor._recibir(self.sesion, ("192.168.0.2", 8443), queue.SimpleQueue())

    def _eventos(self):
        eventos = []
        while not self.sesion.eventos.empty():
            eventos.append(self.sesion.eventos.get_nowait())
        return eventos

    def test_boton_solo_encola_una_peticion_sin_red_ni_confirmacion(self):
        with patch.object(visor.messagebox, "askyesno") as confirmar:
            self.panel._cerrar_anfitrion()
            self.panel._cerrar_anfitrion()

        self.assertTrue(self.sesion.cerrar_anfitrion.is_set())
        self.transporte.cerrar_anfitrion.assert_not_called()
        self.panel.estado.set.assert_called_once_with("Requesting host app exit…")
        self.panel._actualizar_controles.assert_called_once_with()
        confirmar.assert_not_called()

    def test_no_solicita_cierre_sin_conexion_o_soporte(self):
        for estado in ("desconectada", "sin_soporte", "cancelada", "cerrando"):
            with self.subTest(estado=estado):
                self.panel.conectada = estado != "desconectada"
                self.panel.cerrando = estado == "cerrando"
                self.transporte.cierre_remoto_disponible = estado != "sin_soporte"
                self.sesion.cancelada.clear()
                if estado == "cancelada":
                    self.sesion.cancelada.set()
                self.panel._cerrar_anfitrion()
                self.assertFalse(self.sesion.cerrar_anfitrion.is_set())
        self.transporte.cerrar_anfitrion.assert_not_called()

    def test_hilo_envia_el_cierre_y_solo_confirma_despues_del_ack(self):
        self.sesion.cerrar_anfitrion.set()

        self._recibir()

        self.transporte.cerrar_anfitrion.assert_called_once_with()
        self.transporte.leer.assert_not_called()
        self.transporte.cerrar.assert_called_once_with()
        self.assertEqual(self._eventos(), [
            ("conectada", ""),
            ("cerrada", "Host app exit confirmed. Start it again on the host to reconnect."),
        ])

    def test_hilo_atiende_el_cierre_entre_fotogramas(self):
        def leer(*_argumentos):
            self.sesion.cerrar_anfitrion.set()
            return None, {"pausa": False, "calidad": "media", "fps": 15}

        self.transporte.leer.side_effect = leer

        self._recibir()

        self.transporte.leer.assert_called_once()
        self.transporte.cerrar_anfitrion.assert_called_once_with()
        self.assertEqual(self._eventos()[-1][0], "cerrada")

    def test_error_de_cierre_no_anuncia_exito(self):
        self.sesion.cerrar_anfitrion.set()
        self.transporte.cerrar_anfitrion.side_effect = visor.ErrorConexion("No exit acknowledgement.")

        self._recibir()

        self.assertEqual(self._eventos()[-1], ("cerrada", "No exit acknowledgement."))
        self.transporte.cerrar.assert_called_once_with()

    def test_una_sesion_cancelada_no_envia_cierre_pendiente(self):
        self.sesion.cerrar_anfitrion.set()
        self.sesion.cancelada.set()

        self._recibir()

        self.transporte.cerrar_anfitrion.assert_not_called()
        self.assertEqual(self._eventos(), [])

    def test_boton_se_deshabilita_al_esperar_el_cierre(self):
        for nombre in (
            "entrada_host", "entrada_puerto", "conectar", "olvidar", "boton_buscar",
            "selector_camaras", "selector_recientes", "desconectar", "boton_cerrar_anfitrion",
            "boton_pausa", "selector_calidad", "selector_fps",
            "_actualizar_boton_grabacion", "_actualizar_controles_zoom",
        ):
            setattr(self.panel, nombre, Mock())
        self.panel.busqueda = None
        self.panel.camaras = []
        self.panel.recientes = []
        self.panel.pausa_solicitada = False

        visor._Panel._actualizar_controles(self.panel)
        self.panel.boton_cerrar_anfitrion.configure.assert_called_with(state="normal")

        self.sesion.cerrar_anfitrion.set()
        visor._Panel._actualizar_controles(self.panel)
        self.panel.boton_cerrar_anfitrion.configure.assert_called_with(state="disabled")

        self.sesion.cerrar_anfitrion.clear()
        self.transporte.cierre_remoto_disponible = False
        visor._Panel._actualizar_controles(self.panel)
        self.panel.boton_cerrar_anfitrion.configure.assert_called_with(state="disabled")

    def test_confirmacion_finaliza_la_grabacion_sin_cerrar_el_visor(self):
        self.panel.grabacion = Mock()
        self.panel.guardando_grabacion = False
        self.panel.estado_grabacion = Mock()
        self.panel._actualizar_boton_grabacion = Mock()
        self.panel._actualizar_grabacion = Mock()
        self.panel._actualizar_busqueda = Mock()
        self.panel.resultados_recientes = queue.SimpleQueue()
        self.panel.efectivos = Mock()
        self.panel.aviso_ajustes = Mock()
        self.panel._mostrar_mensaje = Mock()
        mensaje = "Host app exit confirmed. Start it again on the host to reconnect."
        self.sesion.eventos.put(("cerrada", mensaje))

        self.panel._actualizar()

        self.panel.grabacion.finalizar.assert_called_once_with()
        self.assertTrue(self.panel.guardando_grabacion)
        self.assertIsNone(self.panel.sesion)
        self.assertTrue(self.sesion.cancelada.is_set())
        self.panel.estado.set.assert_called_with(mensaje)
        self.panel.raiz.destroy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
