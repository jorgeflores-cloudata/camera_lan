"""Local PIN behavior without opening a Tk window or the camera."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import acceso


class PINTests(unittest.TestCase):
    def setUp(self):
        self.ruta = Path(self.enterContext(TemporaryDirectory()), "CamaraLAN", "local-pin.bin")
        self.enterContext(patch.object(acceso, "_ITERACIONES", 1000))
        self.pin = acceso._PIN(self.ruta)

    def test_default_pin_applies_only_when_record_is_missing(self):
        self.pin.verificar("0000")
        self.assertFalse(self.ruta.exists())
        with self.assertRaisesRegex(acceso._ErrorPIN, "Incorrect PIN"):
            self.pin.verificar("0001")

    def test_change_persists_a_salted_record_and_disables_the_default(self):
        self.pin.cambiar("0000", "098765432109", "098765432109")
        primero = self.ruta.read_bytes()
        nueva_instancia = acceso._PIN(self.ruta)
        nueva_instancia.verificar("098765432109")
        self.assertEqual(len(primero), acceso._LONGITUD_REGISTRO)
        with self.assertRaisesRegex(acceso._ErrorPIN, "Incorrect PIN"):
            nueva_instancia.verificar("0000")

        self.pin.cambiar("098765432109", "098765432109", "098765432109")
        self.assertNotEqual(primero, self.ruta.read_bytes())

    def test_invalid_change_leaves_the_existing_pin_intact(self):
        self.pin.cambiar("0000", "0123", "0123")
        registro = self.ruta.read_bytes()
        for actual, nuevo, confirmacion, error in (
            ("0000", "8888", "8888", "Incorrect PIN"),
            ("0123", "１２３４", "１２３４", "4 to 12 digits"),
            ("0123", "123", "123", "4 to 12 digits"),
            ("0123", "1234567890123", "1234567890123", "4 to 12 digits"),
            ("0123", "8888", "8887", "do not match"),
        ):
            with self.subTest(nuevo=nuevo), self.assertRaisesRegex(acceso._ErrorPIN, error):
                self.pin.cambiar(actual, nuevo, confirmacion)
        self.assertEqual(self.ruta.read_bytes(), registro)

    def test_bad_record_fails_closed(self):
        self.ruta.parent.mkdir()
        self.ruta.write_bytes(b"damaged")
        with self.assertRaisesRegex(acceso._ErrorPIN, "saved PIN is invalid"):
            self.pin.verificar("0000")
        with self.assertRaisesRegex(acceso._ErrorPIN, "saved PIN is invalid"):
            self.pin.cambiar("0000", "9999", "9999")
        self.assertEqual(self.ruta.read_bytes(), b"damaged")

    def test_failed_replacement_leaves_the_previous_pin_working(self):
        self.pin.cambiar("0000", "1234", "1234")
        registro = self.ruta.read_bytes()
        with (
            patch.object(Path, "replace", side_effect=PermissionError),
            self.assertRaisesRegex(acceso._ErrorPIN, "Could not save"),
        ):
            self.pin.cambiar("1234", "9876", "9876")
        self.assertEqual(self.ruta.read_bytes(), registro)
        self.pin.verificar("1234")

    def test_retry_delay_uses_a_clock_without_waiting(self):
        with patch.object(acceso.time, "monotonic", return_value=1000.0) as reloj:
            for _ in range(acceso._MAX_INTENTOS - 1):
                with self.assertRaisesRegex(acceso._ErrorPIN, "Incorrect PIN"):
                    self.pin.verificar("1234")
            with self.assertRaisesRegex(acceso._ErrorPIN, "2 seconds"):
                self.pin.verificar("1234")
            with self.assertRaisesRegex(acceso._ErrorPIN, "2 seconds"):
                self.pin.verificar("0000")
            reloj.return_value = 1003.0
            self.pin.verificar("0000")
            with self.assertRaisesRegex(acceso._ErrorPIN, "Incorrect PIN"):
                self.pin.verificar("1234")


class DialogTests(unittest.TestCase):
    def setUp(self):
        temporal = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.object(acceso, "_datos_locales", return_value=Path(temporal)))
        self.raiz = Mock()
        self.ventana = Mock()
        self.ventana.winfo_exists.return_value = True
        self.entrada = Mock()
        self.entrada.get.return_value = "0000"
        self.enterContext(patch.object(acceso.tk, "Toplevel", return_value=self.ventana))
        self.enterContext(patch.object(acceso.ttk, "Frame"))
        self.enterContext(patch.object(acceso.ttk, "Label"))
        self.enterContext(patch.object(acceso.ttk, "Entry", return_value=self.entrada))
        self.botones = self.enterContext(patch.object(acceso.ttk, "Button"))
        self.acceso = acceso.Acceso(self.raiz)

    def _pulsar(self, texto):
        comando = next(
            llamada.kwargs["command"] for llamada in self.botones.call_args_list
            if llamada.kwargs["text"] == texto
        )
        comando()

    def test_duplicate_dialog_keeps_only_the_pending_action(self):
        original, otra = Mock(), Mock()
        self.assertIs(self.acceso.solicitar(original, "Open viewer"), self.ventana)
        self.assertIs(self.acceso.solicitar(otra, "Connect"), self.ventana)
        self.ventana.lift.assert_called_once()
        self.raiz.wait_window.assert_not_called()
        original.assert_not_called()

        self._pulsar("Unlock")

        original.assert_called_once_with()
        otra.assert_not_called()
        self.ventana.destroy.assert_called_once()

    def test_cancel_does_not_run_the_pending_action(self):
        accion = Mock()
        self.acceso.solicitar(accion, "Open viewer")

        self._pulsar("Cancel")

        accion.assert_not_called()
        self.ventana.destroy.assert_called_once()

    def test_startup_waits_only_for_its_temporary_dialog(self):
        with patch.object(acceso.tk, "Tk", return_value=self.raiz):
            self.raiz.wait_window.side_effect = lambda _: self._pulsar("Unlock")
            self.assertTrue(acceso.autorizar_inicio())
        self.raiz.withdraw.assert_called_once()
        self.raiz.wait_window.assert_called_once_with(self.ventana)
        self.raiz.destroy.assert_called_once()
