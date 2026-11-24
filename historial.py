"""Persistent list of recent viewer connections."""

from contextlib import closing
from pathlib import Path
import sqlite3
from stat import S_ISREG

from transporte import ErrorConexion, _datos_locales, _direccion, _puerto


_ARCHIVO = "recent-connections.sqlite3"
_LIMITE = 10
_ESPERA_BLOQUEO = 0.5
_LONGITUD_SHA256 = 32
_TABLA_EXISTE = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'conexiones'"
_SELECCIONAR = "SELECT ip, puerto FROM conexiones ORDER BY orden DESC LIMIT ?"
_REGISTRAR = "INSERT OR REPLACE INTO conexiones (ip, puerto) VALUES (?, ?)"


class ErrorHistorial(Exception):
    """A local storage error that does not affect the camera connection."""


def _recientes_anteriores(datos: Path) -> list[tuple[str, int]]:
    anteriores = []
    for archivo in (datos / "anfitriones").glob("*.sha256"):
        ip, separador, texto_puerto = archivo.stem.rpartition("-")
        if not separador or not (1 <= len(texto_puerto) <= 5 and texto_puerto.isascii() and texto_puerto.isdecimal()):
            continue
        try:
            ip = _direccion(ip)
            puerto = _puerto(int(texto_puerto))
            if archivo.name != f"{ip}-{puerto}.sha256":
                continue
            atributos = archivo.stat(follow_symlinks=False)
        except (ErrorConexion, OSError):
            continue
        if S_ISREG(atributos.st_mode) and atributos.st_size == _LONGITUD_SHA256:
            anteriores.append((atributos.st_mtime_ns, ip, puerto))
    anteriores.sort(reverse=True)
    return [(ip, puerto) for _, ip, puerto in anteriores[:_LIMITE]]


def leer_recientes() -> list[tuple[str, int]]:
    ruta = _datos_locales() / _ARCHIVO
    try:
        if not ruta.exists():
            return _recientes_anteriores(ruta.parent)
        with closing(sqlite3.connect(f"{ruta.absolute().as_uri()}?mode=ro", uri=True, timeout=_ESPERA_BLOQUEO)) as db:
            if db.execute(_TABLA_EXISTE).fetchone() is None:
                return _recientes_anteriores(ruta.parent)
            return db.execute(_SELECCIONAR, (_LIMITE,)).fetchall()
    except (OSError, sqlite3.Error) as error:
        raise ErrorHistorial("Could not load recent connections.") from error


def registrar_conexion(ip: str, puerto: int) -> list[tuple[str, int]]:
    destino = (_direccion(ip), _puerto(puerto))
    ruta = _datos_locales() / _ARCHIVO
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(ruta, timeout=_ESPERA_BLOQUEO, autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL)) as db, db:
            # A single transaction serializes seeding, recency updates and pruning.
            db.execute("BEGIN IMMEDIATE")
            if db.execute(_TABLA_EXISTE).fetchone() is None:
                db.execute("""
                    CREATE TABLE conexiones (
                        orden INTEGER PRIMARY KEY AUTOINCREMENT,
                        ip TEXT NOT NULL,
                        puerto INTEGER NOT NULL,
                        UNIQUE (ip, puerto)
                    )
                """)
                db.executemany(_REGISTRAR, reversed(_recientes_anteriores(ruta.parent)))
            db.execute(_REGISTRAR, destino)
            db.execute("""
                DELETE FROM conexiones
                WHERE orden NOT IN (SELECT orden FROM conexiones ORDER BY orden DESC LIMIT ?)
            """, (_LIMITE,))
            return db.execute(_SELECCIONAR, (_LIMITE,)).fetchall()
    except (OSError, sqlite3.Error) as error:
        raise ErrorHistorial("Could not save the recent connection.") from error
