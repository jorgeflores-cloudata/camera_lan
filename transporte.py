"""Canal privado entre las dos aplicaciones de Cámara LAN."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import json
from pathlib import Path
import secrets
import select
import socket
import ssl
import struct
import tempfile
import threading
import time
from typing import TypedDict

from spake2 import SPAKE2_A, SPAKE2_B
from spake2.ed25519_basic import NotOnCurve
from spake2.spake2 import SPAKEError


class ErrorConexion(Exception):
    """Error de configuración o del protocolo con un mensaje para la interfaz."""


class _Control(TypedDict):
    pausa: bool
    calidad: str
    fps: int


_REDES_LAN = tuple(ipaddress.IPv4Network(red) for red in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_MAX_CONTROL = 1024
_MAX_JPEG = 2 * 1024 * 1024
_AUTENTICACION = 6
_APROBACION = 30
_LECTURA = 10
_SESION = 30 * 60
_INTENTOS_AUTENTICACION = 5
_VENTANA_INTENTOS = 60
_LONGITUD = struct.Struct("!I")


def _direccion(ip: str) -> str:
    try:
        direccion = ipaddress.IPv4Address(ip.strip())
    except (AttributeError, ValueError) as error:
        raise ErrorConexion("Escribe una dirección IPv4 privada de la red local.") from error
    if not any(direccion in red for red in _REDES_LAN):
        raise ErrorConexion("La dirección debe pertenecer a la red local privada.")
    return str(direccion)


def _puerto(puerto: int) -> int:
    if type(puerto) is not int or not 1 <= puerto <= 65535:
        raise ErrorConexion("Escribe un puerto entre 1 y 65535.")
    return puerto


def _leer_codigo(codigo: str) -> bytes:
    if not isinstance(codigo, str):
        raise ErrorConexion("Escribe el código privado de 8 dígitos que muestra el anfitrión.")
    codigo = codigo.strip()
    if len(codigo) != 8 or any(digito not in "0123456789" for digito in codigo):
        raise ErrorConexion("El código privado debe contener exactamente 8 dígitos (0–9).")
    return codigo.encode("ascii")


def _tls_servidor(ip: str) -> tuple[ssl.SSLContext, bytes]:
    # El intercambio con el código privado autentica esta huella efímera.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    ahora = datetime.now(timezone.utc)
    sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Camara LAN")])
    clave = ec.generate_private_key(ec.SECP256R1())
    certificado = (
        x509.CertificateBuilder()
        .subject_name(sujeto)
        .issuer_name(sujeto)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(minutes=1))
        .not_valid_after(ahora + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address(ip))]), critical=False)
        .sign(clave, hashes.SHA256())
    )
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    contexto.options |= ssl.OP_NO_TICKET
    # SSLContext requiere rutas de archivo. Se eliminan al cargar el certificado.
    with tempfile.TemporaryDirectory(prefix="camara-lan-") as temporal:
        ruta_certificado = Path(temporal, "cert.pem")
        ruta_clave = Path(temporal, "key.pem")
        ruta_certificado.write_bytes(certificado.public_bytes(serialization.Encoding.PEM))
        ruta_clave.write_bytes(clave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        contexto.load_cert_chain(ruta_certificado, ruta_clave)
    return contexto, certificado.fingerprint(hashes.SHA256())


def _restante(vencimiento: float) -> float:
    resta = vencimiento - time.monotonic()
    if resta <= 0:
        raise ErrorConexion("Venció el tiempo de espera de la conexión.")
    return resta


def _recibir(conexion: socket.socket, cantidad: int, vencimiento: float) -> bytes:
    datos = bytearray(cantidad)
    salida = memoryview(datos)
    while salida:
        conexion.settimeout(_restante(vencimiento))
        leidos = conexion.recv_into(salida)
        if leidos == 0:
            raise ErrorConexion("El otro equipo cerró la conexión.")
        salida = salida[leidos:]
    return bytes(datos)


def _recibir_paquete(conexion: socket.socket, limite: int, vencimiento: float) -> bytes:
    cantidad, = _LONGITUD.unpack(_recibir(conexion, _LONGITUD.size, vencimiento))
    if cantidad > limite:
        raise ErrorConexion("El otro equipo envió un paquete demasiado grande.")
    return _recibir(conexion, cantidad, vencimiento)


def _enviar_paquete(conexion: socket.socket, datos: bytes, vencimiento: float) -> None:
    conexion.settimeout(_restante(vencimiento))
    conexion.sendall(_LONGITUD.pack(len(datos)))
    if datos:
        conexion.settimeout(_restante(vencimiento))
        conexion.sendall(datos)


def _autenticar(
    conexion: ssl.SSLSocket, codigo: bytes, huella: bytes, servidor: bool, vencimiento: float,
) -> None:
    # SPAKE2 permite usar un código corto sin enviarlo ni exponerlo a pruebas
    # de diccionario fuera de línea. Vincularlo al certificado local impide
    # que un intermediario reenvíe el intercambio entre dos conexiones TLS.
    intercambio = (SPAKE2_B if servidor else SPAKE2_A)(
        codigo, idA=b"CamaraLAN2/visor", idB=b"CamaraLAN2/anfitrion/" + huella,
    )
    _enviar_paquete(conexion, intercambio.start(), vencimiento)
    mensaje = _recibir_paquete(conexion, 33, vencimiento)
    if len(mensaje) != 33:
        raise ErrorConexion("El intercambio del código privado no es válido.")
    try:
        clave = intercambio.finish(mensaje)
    except (ValueError, NotOnCurve, SPAKEError) as error:
        raise ErrorConexion("El intercambio del código privado no es válido.") from error

    propia, remota = (b"anfitrion", b"visor") if servidor else (b"visor", b"anfitrion")
    _enviar_paquete(conexion, hmac.digest(clave, b"CamaraLAN2/confirmar/" + propia, "sha256"), vencimiento)
    confirmacion = _recibir_paquete(conexion, 32, vencimiento)
    esperada = hmac.digest(clave, b"CamaraLAN2/confirmar/" + remota, "sha256")
    if not hmac.compare_digest(confirmacion, esperada):
        raise ErrorConexion("El código privado no coincide con el de esta cámara.")


def _recibir_json(conexion: socket.socket, vencimiento: float) -> dict:
    datos = _recibir_paquete(conexion, _MAX_CONTROL, vencimiento)
    try:
        mensaje = json.loads(datos.decode("utf-8"))
    except ValueError as error:
        raise ErrorConexion("El otro equipo envió un mensaje de control inválido.") from error
    if not isinstance(mensaje, dict):
        raise ErrorConexion("El otro equipo envió un mensaje de control inválido.")
    return mensaje


def _enviar_json(conexion: socket.socket, mensaje: dict, vencimiento: float) -> None:
    datos = json.dumps(mensaje, separators=(",", ":")).encode("utf-8")
    if len(datos) > _MAX_CONTROL:
        raise ErrorConexion("El mensaje de control es demasiado grande.")
    _enviar_paquete(conexion, datos, vencimiento)


def _control(mensaje: dict) -> _Control:
    if not isinstance(mensaje, dict) or set(mensaje) != {"pausa", "calidad", "fps"}:
        raise ErrorConexion("Los controles de imagen no son válidos.")
    pausa, calidad, fps = mensaje["pausa"], mensaje["calidad"], mensaje["fps"]
    if type(pausa) is not bool or calidad not in ("baja", "media", "alta") or type(fps) is not int or not 1 <= fps <= 15:
        raise ErrorConexion("Los controles de imagen no son válidos.")
    return {"pausa": pausa, "calidad": calidad, "fps": fps}


def _es_jpeg(datos: bytes) -> bool:
    return (
        isinstance(datos, bytes)
        and 4 < len(datos) <= _MAX_JPEG
        and datos.startswith(b"\xff\xd8")
        and datos.endswith(b"\xff\xd9")
    )


def _apagar(conexion: socket.socket) -> None:
    try:
        conexion.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    conexion.close()


@dataclass
class _Sesion:
    conexion: socket.socket
    cancelado: threading.Event = field(default_factory=threading.Event)
    autorizada: bool = False


class Servidor:
    """Escucha en una IP privada y ofrece una sola sesión aprobada a la vez."""

    def __init__(
        self,
        ip: str,
        puerto: int,
        al_solicitar: Callable[[str, threading.Event], bool],
        al_control: Callable[[bool, str, int], dict],
        al_terminar: Callable[[], None],
    ):
        self._ip = _direccion(ip)
        self._puerto = _puerto(puerto)
        self._al_solicitar = al_solicitar
        self._al_control = al_control
        self._al_terminar = al_terminar
        self._bloqueo = threading.Lock()
        self._escucha: socket.socket | None = None
        self._sesion: _Sesion | None = None
        self._imagen: bytes | None = None
        self._intentos: deque[float] = deque()

    def iniciar(self) -> str:
        with self._bloqueo:
            if self._escucha is not None:
                raise ErrorConexion("La cámara ya está escuchando en la red local.")
            contexto, huella = _tls_servidor(self._ip)
            codigo = f"{secrets.randbelow(100_000_000):08d}"
            secreto = _leer_codigo(codigo)
            self._intentos.clear()
            escucha = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    escucha.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    escucha.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                escucha.bind((self._ip, self._puerto))
                escucha.listen(4)
                escucha.settimeout(0.5)
                self._escucha = escucha
                threading.Thread(
                    target=self._escuchar, args=(escucha, contexto, secreto, huella), daemon=True,
                    name="camara-lan-escucha",
                ).start()
            except BaseException:
                self._escucha = None
                escucha.close()
                raise
        return codigo

    def publicar(self, jpeg: bytes | None) -> None:
        if jpeg is not None and not _es_jpeg(jpeg):
            return
        with self._bloqueo:
            sesion = self._sesion
            if sesion is not None and sesion.autorizada and not sesion.cancelado.is_set():
                self._imagen = jpeg

    def terminar_sesion(self) -> None:
        with self._bloqueo:
            self._cancelar_sesion()

    def cerrar(self) -> None:
        with self._bloqueo:
            escucha, self._escucha = self._escucha, None
            if escucha is not None:
                _apagar(escucha)
            self._cancelar_sesion()

    def _cancelar_sesion(self) -> None:
        self._imagen = None
        if self._sesion is not None:
            self._sesion.cancelado.set()
            _apagar(self._sesion.conexion)

    def _escuchar(self, escucha: socket.socket, contexto: ssl.SSLContext, secreto: bytes, huella: bytes) -> None:
        while True:
            try:
                conexion, (ip, _) = escucha.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                _direccion(ip)
                conexion.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except (OSError, ErrorConexion):
                conexion.close()
                continue
            with self._bloqueo:
                if self._escucha is not escucha or self._sesion is not None:
                    conexion.close()
                    continue
                ahora = time.monotonic()
                while self._intentos and ahora - self._intentos[0] >= _VENTANA_INTENTOS:
                    self._intentos.popleft()
                if len(self._intentos) >= _INTENTOS_AUTENTICACION:
                    conexion.close()
                    continue
                self._intentos.append(ahora)
                sesion = _Sesion(conexion)
                self._sesion = sesion
                try:
                    threading.Thread(
                        target=self._atender, args=(sesion, contexto, secreto, huella, ip), daemon=True,
                        name="camara-lan-sesion",
                    ).start()
                except RuntimeError:
                    self._sesion = None
                    conexion.close()

    def _atender(self, sesion: _Sesion, contexto: ssl.SSLContext, secreto: bytes, huella: bytes, ip: str) -> None:
        autenticada = False
        try:
            with self._bloqueo:
                if sesion.cancelado.is_set():
                    return
                conexion = contexto.wrap_socket(sesion.conexion, server_side=True, do_handshake_on_connect=False)
                sesion.conexion = conexion
            vencimiento = time.monotonic() + _AUTENTICACION
            conexion.settimeout(_restante(vencimiento))
            conexion.do_handshake()
            _autenticar(conexion, secreto, huella, True, vencimiento)
            autenticada = True
            if not self._aprobar(conexion, sesion.cancelado, ip):
                if not sesion.cancelado.is_set():
                    _enviar_json(conexion, {"autorizada": False}, time.monotonic() + 1)
                return
            with self._bloqueo:
                if sesion.cancelado.is_set():
                    return
                self._imagen = None
                sesion.autorizada = True
            _enviar_json(conexion, {"autorizada": True}, time.monotonic() + 1)
            self._transmitir(sesion)
        except (OSError, ErrorConexion):
            pass
        finally:
            sesion.cancelado.set()
            _apagar(sesion.conexion)
            try:
                if autenticada:
                    self._al_terminar()
            finally:
                with self._bloqueo:
                    self._imagen = None
                    self._sesion = None

    def _aprobar(self, conexion: ssl.SSLSocket, cancelado: threading.Event, ip: str) -> bool:
        if cancelado.is_set():
            return False
        vencimiento = time.monotonic() + _APROBACION
        finalizado = threading.Event()

        def vigilar() -> None:
            while not finalizado.is_set() and not cancelado.is_set():
                resto = vencimiento - time.monotonic()
                try:
                    # El visor espera la respuesta antes de enviar cualquier otro mensaje.
                    if resto > 0 and not conexion.pending() and not select.select([conexion], [], [], min(resto, 0.1))[0]:
                        continue
                except (OSError, ValueError):
                    pass
                if not finalizado.is_set():
                    cancelado.set()
                    _apagar(conexion)
                return

        vigilante = threading.Thread(target=vigilar, daemon=True, name="camara-lan-autorizacion")
        vigilante.start()
        try:
            aprobada = self._al_solicitar(ip, cancelado)
        finally:
            finalizado.set()
            vigilante.join()
        return bool(aprobada) and not cancelado.is_set() and time.monotonic() < vencimiento

    def _transmitir(self, sesion: _Sesion) -> None:
        vencimiento_sesion = time.monotonic() + _SESION
        siguiente = time.monotonic()
        while not sesion.cancelado.is_set() and time.monotonic() < vencimiento_sesion:
            vencimiento = min(vencimiento_sesion, time.monotonic() + _LECTURA)
            solicitado = _control(_recibir_json(sesion.conexion, vencimiento))
            demora = max(0.0, siguiente - time.monotonic())
            if sesion.cancelado.wait(demora) or time.monotonic() >= vencimiento_sesion:
                return
            turno = time.monotonic()
            efectivo = _control(self._al_control(solicitado["pausa"], solicitado["calidad"], solicitado["fps"]))
            with self._bloqueo:
                imagen = None if efectivo["pausa"] else self._imagen
            _enviar_json(sesion.conexion, efectivo, vencimiento)
            _enviar_paquete(sesion.conexion, imagen or b"", vencimiento)
            siguiente = turno + 1 / efectivo["fps"]


class Visor:
    """Cliente que autentica la cámara con el código privado sin enviarlo."""

    def __init__(self, ip: str, puerto: int, codigo: str):
        self._ip = _direccion(ip)
        self._puerto = _puerto(puerto)
        self._secreto = _leer_codigo(codigo)
        self._bloqueo = threading.Lock()
        self._peticion = threading.Lock()
        self._conexion: socket.socket | None = None
        self._cerrado = False
        self._aprobado = False

    def conectar(self) -> None:
        with self._bloqueo:
            if self._cerrado or self._conexion is not None:
                raise ErrorConexion("Crea una nueva conexión para abrir el visor.")
            conexion = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._conexion = conexion
        try:
            vencimiento = time.monotonic() + _AUTENTICACION
            conexion.settimeout(_restante(vencimiento))
            conexion.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conexion.connect((self._ip, self._puerto))
            contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            contexto.minimum_version = ssl.TLSVersion.TLSv1_2
            contexto.check_hostname = False
            contexto.verify_mode = ssl.CERT_NONE
            with self._bloqueo:
                if self._cerrado:
                    raise ErrorConexion("Se canceló la conexión.")
                conexion = contexto.wrap_socket(conexion, do_handshake_on_connect=False)
                self._conexion = conexion
            conexion.settimeout(_restante(vencimiento))
            conexion.do_handshake()
            certificado = conexion.getpeercert(binary_form=True)
            if not certificado:
                raise ErrorConexion("La cámara no presentó un certificado.")
            _autenticar(conexion, self._secreto, hashlib.sha256(certificado).digest(), False, vencimiento)
            respuesta = _recibir_json(conexion, time.monotonic() + _APROBACION + 2)
            if respuesta != {"autorizada": True} or respuesta["autorizada"] is not True:
                raise ErrorConexion("La cámara no aprobó la solicitud.")
            with self._bloqueo:
                if self._cerrado:
                    raise ErrorConexion("Se canceló la conexión.")
                self._aprobado = True
        except (OSError, ErrorConexion):
            self.cerrar()
            raise

    def leer(self, pausa: bool, calidad: str, fps: int) -> tuple[bytes | None, dict]:
        control = _control({"pausa": pausa, "calidad": calidad, "fps": fps})
        with self._peticion:
            with self._bloqueo:
                conexion = self._conexion
                if conexion is None or not self._aprobado:
                    raise ErrorConexion("La cámara aún no autorizó esta conexión.")
            try:
                vencimiento = time.monotonic() + _LECTURA + 1
                _enviar_json(conexion, control, vencimiento)
                efectivo = _control(_recibir_json(conexion, vencimiento))
                imagen = _recibir_paquete(conexion, _MAX_JPEG, vencimiento)
                if imagen and (efectivo["pausa"] or not _es_jpeg(imagen)):
                    raise ErrorConexion("La cámara envió una imagen inválida.")
                return imagen or None, efectivo
            except (OSError, ErrorConexion):
                self.cerrar()
                raise

    def cerrar(self) -> None:
        with self._bloqueo:
            self._cerrado = True
            self._aprobado = False
            conexion, self._conexion = self._conexion, None
            if conexion is not None:
                _apagar(conexion)
