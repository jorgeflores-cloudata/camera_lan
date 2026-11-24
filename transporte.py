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
import os
from pathlib import Path
import select
import socket
import ssl
import struct
import tempfile
import threading
import time
from typing import TypedDict


class ErrorConexion(Exception):
    """Error de configuración o del protocolo con un mensaje para la interfaz."""


class _Control(TypedDict):
    pausa: bool
    calidad: str
    fps: int


_REDES_LAN = tuple(ipaddress.IPv4Network(red) for red in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_PROTOCOLO = "camara-lan/4"
_PROTOCOLO_ANTERIOR = "camara-lan/3"
_PROTOCOLOS = (_PROTOCOLO, _PROTOCOLO_ANTERIOR)
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
        raise ErrorConexion("Enter a private IPv4 address on the local network.") from error
    if not any(direccion in red for red in _REDES_LAN):
        raise ErrorConexion("The address must be on the private local network.")
    return str(direccion)


def _puerto(puerto: int) -> int:
    if type(puerto) is not int or not 1 <= puerto <= 65535:
        raise ErrorConexion("Enter a port between 1 and 65535.")
    return puerto


def _datos_locales() -> Path:
    raiz = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    return (Path(raiz) if raiz else Path.home() / ".local" / "share") / "CamaraLAN"


def _guardar_nuevo(ruta: Path, contenido: bytes) -> None:
    """Publica el archivo completo sin reemplazar una identidad ya escrita."""
    ruta.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=ruta.parent) as temporal:
        candidato = Path(temporal, ruta.name)
        candidato.write_bytes(contenido)
        try:
            os.link(candidato, ruta)
        except FileExistsError:
            pass


def _generar_identidad() -> bytes:
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
        .not_valid_after(ahora + timedelta(days=3650))
        .sign(clave, hashes.SHA256())
    )
    return certificado.public_bytes(serialization.Encoding.PEM) + clave.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _tls_servidor() -> ssl.SSLContext:
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    contexto.options |= ssl.OP_NO_TICKET
    contexto.set_alpn_protocols(list(_PROTOCOLOS))
    ruta = _datos_locales() / "identidad.pem"
    try:
        if not ruta.exists():
            _guardar_nuevo(ruta, _generar_identidad())
        contexto.load_cert_chain(ruta)
    except OSError as error:
        raise ErrorConexion("Could not load the local camera identity. Check its file and permissions.") from error
    return contexto


def _ruta_huella(ip: str, puerto: int) -> Path:
    return _datos_locales() / "anfitriones" / f"{ip}-{puerto}.sha256"


def _comprobar_huella(ruta: Path, huella: bytes) -> bool:
    try:
        fijada = ruta.read_bytes()
    except FileNotFoundError:
        return False
    if not hmac.compare_digest(fijada, huella):
        raise ErrorConexion(
            "The camera's identity has changed. Verify the host computer, then use “Forget identity” "
            "before reconnecting."
        )
    return True


def _restante(vencimiento: float) -> float:
    resta = vencimiento - time.monotonic()
    if resta <= 0:
        raise ErrorConexion("The connection timed out.")
    return resta


def _recibir(conexion: socket.socket, cantidad: int, vencimiento: float) -> bytes:
    datos = bytearray(cantidad)
    salida = memoryview(datos)
    while salida:
        conexion.settimeout(_restante(vencimiento))
        leidos = conexion.recv_into(salida)
        if leidos == 0:
            raise ErrorConexion("The other computer closed the connection.")
        salida = salida[leidos:]
    return bytes(datos)


def _recibir_paquete(conexion: socket.socket, limite: int, vencimiento: float) -> bytes:
    cantidad, = _LONGITUD.unpack(_recibir(conexion, _LONGITUD.size, vencimiento))
    if cantidad > limite:
        raise ErrorConexion("The other computer sent a packet that was too large.")
    return _recibir(conexion, cantidad, vencimiento)


def _enviar_paquete(conexion: socket.socket, datos: bytes, vencimiento: float) -> None:
    conexion.settimeout(_restante(vencimiento))
    conexion.sendall(_LONGITUD.pack(len(datos)))
    if datos:
        conexion.settimeout(_restante(vencimiento))
        conexion.sendall(datos)


def _recibir_json(conexion: socket.socket, vencimiento: float) -> dict:
    datos = _recibir_paquete(conexion, _MAX_CONTROL, vencimiento)
    try:
        mensaje = json.loads(datos.decode("utf-8"))
    except ValueError as error:
        raise ErrorConexion("The other computer sent an invalid control message.") from error
    if not isinstance(mensaje, dict):
        raise ErrorConexion("The other computer sent an invalid control message.")
    return mensaje


def _enviar_json(conexion: socket.socket, mensaje: dict, vencimiento: float) -> None:
    datos = json.dumps(mensaje, separators=(",", ":")).encode("utf-8")
    if len(datos) > _MAX_CONTROL:
        raise ErrorConexion("The control message is too large.")
    _enviar_paquete(conexion, datos, vencimiento)


def _control(mensaje: dict) -> _Control:
    if not isinstance(mensaje, dict) or set(mensaje) != {"pausa", "calidad", "fps"}:
        raise ErrorConexion("The video settings are invalid.")
    pausa, calidad, fps = mensaje["pausa"], mensaje["calidad"], mensaje["fps"]
    if type(pausa) is not bool or calidad not in ("baja", "media", "alta") or type(fps) is not int or not 1 <= fps <= 15:
        raise ErrorConexion("The video settings are invalid.")
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
    cierre_remoto: bool = False


class Servidor:
    """Escucha en una IP privada y ofrece una sola sesión aprobada a la vez."""

    def __init__(
        self,
        ip: str,
        puerto: int,
        al_solicitar: Callable[[str, threading.Event], bool],
        al_control: Callable[[bool, str, int], dict],
        al_terminar: Callable[[], None],
        al_cerrar: Callable[[], None] | None = None,
    ):
        self._ip = _direccion(ip)
        self._puerto = _puerto(puerto)
        self._al_solicitar = al_solicitar
        self._al_control = al_control
        self._al_terminar = al_terminar
        self._al_cerrar = al_cerrar
        self._bloqueo = threading.Lock()
        self._escucha: socket.socket | None = None
        self._sesion: _Sesion | None = None
        self._imagen: bytes | None = None
        self._intentos: deque[float] = deque()

    def iniciar(self) -> None:
        with self._bloqueo:
            if self._escucha is not None:
                raise ErrorConexion("The camera is already listening on the local network.")
            contexto = _tls_servidor()
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
                    target=self._escuchar, args=(escucha, contexto), daemon=True,
                    name="camara-lan-escucha",
                ).start()
            except BaseException:
                self._escucha = None
                escucha.close()
                raise

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

    def _escuchar(self, escucha: socket.socket, contexto: ssl.SSLContext) -> None:
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
                        target=self._atender, args=(sesion, contexto, ip), daemon=True,
                        name="camara-lan-sesion",
                    ).start()
                except RuntimeError:
                    self._sesion = None
                    conexion.close()

    def _atender(self, sesion: _Sesion, contexto: ssl.SSLContext, ip: str) -> None:
        solicitada = False
        try:
            with self._bloqueo:
                if sesion.cancelado.is_set():
                    return
                conexion = contexto.wrap_socket(sesion.conexion, server_side=True, do_handshake_on_connect=False)
                sesion.conexion = conexion
            vencimiento = time.monotonic() + _AUTENTICACION
            conexion.settimeout(_restante(vencimiento))
            conexion.do_handshake()
            protocolo = conexion.selected_alpn_protocol()
            if protocolo not in _PROTOCOLOS:
                return
            if _recibir_json(conexion, vencimiento) != {"tipo": "conectar"}:
                return
            solicitada = True
            if not self._aprobar(conexion, sesion.cancelado, ip):
                if not sesion.cancelado.is_set():
                    respuesta = {"tipo": "resultado", "autorizada": False}
                    if protocolo == _PROTOCOLO:
                        respuesta["cierre_remoto"] = False
                    _enviar_json(conexion, respuesta, time.monotonic() + 1)
                return
            with self._bloqueo:
                if sesion.cancelado.is_set():
                    return
                self._imagen = None
                sesion.autorizada = True
                sesion.cierre_remoto = protocolo == _PROTOCOLO and self._al_cerrar is not None
            respuesta = {"tipo": "resultado", "autorizada": True}
            if protocolo == _PROTOCOLO:
                respuesta["cierre_remoto"] = sesion.cierre_remoto
            _enviar_json(conexion, respuesta, time.monotonic() + 1)
            self._transmitir(sesion)
        except (OSError, ErrorConexion):
            pass
        finally:
            sesion.cancelado.set()
            _apagar(sesion.conexion)
            try:
                if solicitada:
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
            mensaje = _recibir_json(sesion.conexion, vencimiento)
            if mensaje == {"tipo": "cerrar_anfitrion"}:
                aceptado = sesion.cierre_remoto and self._al_cerrar is not None
                _enviar_json(
                    sesion.conexion, {"tipo": "cierre_anfitrion", "aceptado": aceptado}, vencimiento,
                )
                if aceptado:
                    # Confirma la petición antes de cerrar el canal. El callback
                    # encola la salida de la aplicación en el hilo de su interfaz.
                    self.cerrar()
                    self._al_cerrar()
                    return
                continue
            solicitado = _control(mensaje)
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
    """Cliente TLS que recuerda la identidad de cada anfitrión aprobado."""

    def __init__(self, ip: str, puerto: int):
        self._ip = _direccion(ip)
        self._puerto = _puerto(puerto)
        self._bloqueo = threading.Lock()
        self._peticion = threading.Lock()
        self._conexion: socket.socket | None = None
        self._cerrado = False
        self._aprobado = False
        self._cierre_remoto_disponible = False

    @property
    def cierre_remoto_disponible(self) -> bool:
        with self._bloqueo:
            return self._aprobado and self._cierre_remoto_disponible

    @staticmethod
    def olvidar(ip: str, puerto: int) -> None:
        _ruta_huella(_direccion(ip), _puerto(puerto)).unlink(missing_ok=True)

    def conectar(self) -> None:
        with self._bloqueo:
            if self._cerrado or self._conexion is not None:
                raise ErrorConexion("Start a new connection to open the viewer.")
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
            contexto.set_alpn_protocols(list(_PROTOCOLOS))
            with self._bloqueo:
                if self._cerrado:
                    raise ErrorConexion("The connection was canceled.")
                conexion = contexto.wrap_socket(conexion, do_handshake_on_connect=False)
                self._conexion = conexion
            conexion.settimeout(_restante(vencimiento))
            conexion.do_handshake()
            protocolo = conexion.selected_alpn_protocol()
            if protocolo not in _PROTOCOLOS:
                raise ErrorConexion("Update LAN Camera on both computers to a compatible version.")
            certificado = conexion.getpeercert(binary_form=True)
            if not certificado:
                raise ErrorConexion("The camera did not provide a certificate.")
            huella = hashlib.sha256(certificado).digest()
            ruta = _ruta_huella(self._ip, self._puerto)
            fijada = _comprobar_huella(ruta, huella)
            _enviar_json(conexion, {"tipo": "conectar"}, vencimiento)
            respuesta = _recibir_json(conexion, time.monotonic() + _APROBACION + 2)
            campos = {"tipo", "autorizada"}
            if protocolo == _PROTOCOLO:
                campos.add("cierre_remoto")
            if (
                set(respuesta) != campos
                or respuesta["tipo"] != "resultado"
                or type(respuesta["autorizada"]) is not bool
                or (protocolo == _PROTOCOLO and type(respuesta["cierre_remoto"]) is not bool)
            ):
                raise ErrorConexion("The camera sent an invalid connection response.")
            if not respuesta["autorizada"]:
                raise ErrorConexion("The host is not accepting connections. Check that LAN sharing is enabled.")
            with self._bloqueo:
                if self._cerrado:
                    raise ErrorConexion("The connection was canceled.")
                if not fijada:
                    _guardar_nuevo(ruta, huella)
                    if not _comprobar_huella(ruta, huella):
                        raise ErrorConexion("Could not save the camera's identity.")
                self._aprobado = True
                self._cierre_remoto_disponible = respuesta.get("cierre_remoto", False)
        except (OSError, ErrorConexion):
            self.cerrar()
            raise

    def leer(self, pausa: bool, calidad: str, fps: int) -> tuple[bytes | None, dict]:
        control = _control({"pausa": pausa, "calidad": calidad, "fps": fps})
        with self._peticion:
            with self._bloqueo:
                conexion = self._conexion
                if conexion is None or not self._aprobado:
                    raise ErrorConexion("The connection is not ready for video.")
            try:
                vencimiento = time.monotonic() + _LECTURA + 1
                _enviar_json(conexion, control, vencimiento)
                efectivo = _control(_recibir_json(conexion, vencimiento))
                imagen = _recibir_paquete(conexion, _MAX_JPEG, vencimiento)
                if imagen and (efectivo["pausa"] or not _es_jpeg(imagen)):
                    raise ErrorConexion("The camera sent an invalid image.")
                return imagen or None, efectivo
            except (OSError, ErrorConexion):
                self.cerrar()
                raise

    def cerrar_anfitrion(self) -> None:
        """Solicita la salida del anfitrión y espera su confirmación por TLS."""
        with self._peticion:
            with self._bloqueo:
                conexion = self._conexion
                if conexion is None or not self._aprobado:
                    raise ErrorConexion("Connect to the host before requesting its exit.")
                if not self._cierre_remoto_disponible:
                    raise ErrorConexion("Update the host app to enable remote exit.")
            try:
                vencimiento = time.monotonic() + _LECTURA
                _enviar_json(conexion, {"tipo": "cerrar_anfitrion"}, vencimiento)
                respuesta = _recibir_json(conexion, vencimiento)
                if (
                    set(respuesta) != {"tipo", "aceptado"}
                    or respuesta["tipo"] != "cierre_anfitrion"
                    or respuesta["aceptado"] is not True
                ):
                    raise ErrorConexion("The host did not confirm the exit request.")
            except (OSError, ErrorConexion):
                self.cerrar()
                raise
            self.cerrar()

    def cerrar(self) -> None:
        with self._bloqueo:
            self._cerrado = True
            self._aprobado = False
            self._cierre_remoto_disponible = False
            conexion, self._conexion = self._conexion, None
            if conexion is not None:
                _apagar(conexion)
