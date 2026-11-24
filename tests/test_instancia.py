from contextlib import nullcontext
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import camara_lan


class InstanciaUnicaTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "El mutex con nombre requiere Windows.")
    def test_excluye_otro_proceso_y_permite_reabrir(self):
        nombre = rf"Local\CamaraLAN.Tests.{uuid4()}"
        comprobar_instancia = (
            "import sys\n"
            "from camara_lan import _instancia_unica\n"
            "with _instancia_unica(sys.argv[1]) as disponible:\n"
            "    print(disponible)\n"
        )
        with camara_lan._instancia_unica(nombre) as disponible:
            self.assertTrue(disponible)
            segundo = subprocess.run(
                [sys.executable, "-c", comprobar_instancia, nombre],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(segundo.returncode, 0, segundo.stderr)
            self.assertEqual(segundo.stdout.strip(), "False")

        with camara_lan._instancia_unica(nombre) as disponible:
            self.assertTrue(disponible)

    def test_instancia_duplicada_no_abre_selector_ni_modo(self):
        for argumentos in ([], ["share"], ["view"]):
            with (
                self.subTest(argumentos=argumentos),
                patch.object(sys, "argv", ["camara_lan.py", *argumentos]),
                patch.object(camara_lan, "_instancia_unica", return_value=nullcontext(False)),
                patch.object(camara_lan, "_ejecutar_modo") as ejecutar,
                patch.object(camara_lan.messagebox, "showwarning") as aviso,
            ):
                self.assertEqual(camara_lan.main(), 0)
                ejecutar.assert_not_called()
                aviso.assert_called_once()

    def test_abre_los_modos_en_ingles_y_acepta_los_alias_anteriores(self):
        for argumento, modo in (("share", "share"), ("view", "view"), ("compartir", "share"), ("ver", "view")):
            with (
                self.subTest(argumento=argumento),
                patch.object(sys, "argv", ["camara_lan.py", argumento]),
                patch.object(camara_lan, "_instancia_unica", return_value=nullcontext(True)),
                patch.object(camara_lan, "_ejecutar_modo", return_value=0) as ejecutar,
            ):
                self.assertEqual(camara_lan.main(), 0)
                ejecutar.assert_called_once_with(modo)
