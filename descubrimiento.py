"""Búsqueda opcional de anfitriones en la misma red IPv4."""

from __future__ import annotations

from contextlib import ExitStack
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import ipaddress
import secrets
import select
import socket
import sys
import threading
import time


_GRUPO = "239.255.42.84"
_PUERTO = 55443
_SOLICITUD = b"CamaraLAN/1?"
_RESPUESTA = b"CamaraLAN/1!"
_LONGITUD_NONCE = 16
_MAX_NOMBRE = 63
_POS_PUERTO = len(_RESPUESTA) + _LONGITUD_NONCE
_POS_NOMBRE = _POS_PUERTO + 2
_MAX_DATAGRAMA = _POS_NOMBRE + _MAX_NOMBRE
_RELLENO = bytes(_MAX_DATAGRAMA - len(_SOLICITUD) - _LONGITUD_NONCE)
_TIEMPO_BUSQUEDA = 1.2
_INTERVALO = 0.1
_MAX_RESULTADOS = 64
_REDES_PRIVADAS = tuple(
    ipaddress.IPv4Network(red) for red in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


@dataclass(frozen=True)
class CamaraLAN:
    nombre: str
    ip: str
    puerto: int


def _ip_privada(ip: str) -> ipaddress.IPv4Address | None:
    try:
        direccion = ipaddress.IPv4Address(ip)
    except ipaddress.AddressValueError:
        return None
    if any(direccion in red for red in _REDES_PRIVADAS):
        return direccion
    return None


class _FilaIPv4(ctypes.Structure):
    _fields_ = [
        ("direccion", wintypes.DWORD), ("indice", wintypes.DWORD), ("mascara", wintypes.DWORD),
        ("difusion", wintypes.DWORD), ("reensamblado", wintypes.DWORD),
        ("reservado", wintypes.WORD), ("tipo", wintypes.WORD),
    ]


class _TablaIPv4(ctypes.Structure):
    _fields_ = [("cantidad", wintypes.DWORD), ("primera", _FilaIPv4)]


def _interfaces_windows() -> dict[str, ipaddress.IPv4Network]:
    # GetIpAddrTable enumera las direcciones de cada adaptador sin consultar DNS.
    tabla_ipv4 = ctypes.WinDLL("iphlpapi").GetIpAddrTable
    tabla_ipv4.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.BOOL)
    tabla_ipv4.restype = wintypes.DWORD
    tamano = wintypes.ULONG()
    insuficiente = 122
    error = tabla_ipv4(None, ctypes.byref(tamano), False)
    # Una interfaz puede cambiar entre la consulta del tamaño y la lectura.
    for _ in range(2):
        if error != insuficiente:
            break
        datos = ctypes.create_string_buffer(tamano.value)
        error = tabla_ipv4(datos, ctypes.byref(tamano), False)
        if error == 0:
            cantidad = wintypes.DWORD.from_buffer(datos).value
            filas = (_FilaIPv4 * cantidad).from_buffer(datos, _TablaIPv4.primera.offset)
            interfaces = {}
            for fila in filas:
                ip = socket.inet_ntoa(fila.direccion.to_bytes(4, sys.byteorder))
                if _ip_privada(ip) is not None:
                    mascara = socket.inet_ntoa(fila.mascara.to_bytes(4, sys.byteorder))
                    try:
                        interfaces[ip] = ipaddress.IPv4Interface((ip, mascara)).network
                    except ipaddress.NetmaskValueError:
                        continue
            return interfaces
    if error == 0:
        return {}
    raise ctypes.WinError(error)


def _interfaces_locales() -> dict[str, ipaddress.IPv4Network | None]:
    if sys.platform == "win32":
        return _interfaces_windows()
    _, _, direcciones = socket.gethostbyname_ex(socket.gethostname())
    return {ip: None for ip in direcciones if _ip_privada(ip) is not None}


def _nombre_local() -> bytes:
    nombre = "".join(caracter for caracter in socket.gethostname() if caracter.isprintable()).strip()
    nombre = nombre.encode("utf-8", errors="ignore")[:_MAX_NOMBRE].decode("utf-8", errors="ignore").strip()
    return nombre.encode("utf-8") or b"LAN Camera"


def _leer_solicitud(datos: bytes) -> bytes | None:
    if len(datos) == _MAX_DATAGRAMA and datos.startswith(_SOLICITUD) and datos.endswith(_RELLENO):
        return datos[len(_SOLICITUD):_POS_PUERTO]
    return None


def _leer_respuesta(datos: bytes, origen: str, nonce: bytes) -> CamaraLAN | None:
    if not (_POS_NOMBRE < len(datos) <= _MAX_DATAGRAMA and datos.startswith(_RESPUESTA + nonce)):
        return None
    ip = _ip_privada(origen)
    puerto = int.from_bytes(datos[_POS_PUERTO:_POS_NOMBRE], "big")
    if ip is None or puerto == 0:
        return None
    try:
        nombre = datos[_POS_NOMBRE:].decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not nombre or not nombre.isprintable():
        return None
    return CamaraLAN(nombre, str(ip), puerto)


class AnuncioLAN:
    """Contesta búsquedas mientras el anfitrión acepta solicitudes de conexión."""

    def __init__(self, ip: str, puerto: int):
        if _ip_privada(ip) is None or type(puerto) is not int or not 1 <= puerto <= 65535:
            raise ValueError("LAN discovery requires a private IPv4 address and a valid port.")
        self._ip = ip
        self._puerto = puerto
        self._cerrado = threading.Event()
        self._recursos: ExitStack | None = None
        self._hilo: threading.Thread | None = None

    def iniciar(self) -> None:
        if self._hilo is not None:
            return
        red = _interfaces_locales().get(self._ip)
        if sys.platform == "win32" and red is None:
            raise OSError("The selected IPv4 address is no longer available for LAN discovery.")
        descripcion = self._puerto.to_bytes(2, "big") + _nombre_local()
        with ExitStack() as recursos:
            escucha = recursos.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                escucha.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            escucha.bind(("", _PUERTO))
            grupo = socket.inet_aton(_GRUPO) + socket.inet_aton(self._ip)
            escucha.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, grupo)
            escucha.settimeout(_INTERVALO)

            # Una respuesta unicast sale con la misma IP que escucha por TLS.
            salida = recursos.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            salida.bind((self._ip, 0))
            salida.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, 1)
            salida.setblocking(False)

            self._cerrado.clear()
            hilo = threading.Thread(
                target=self._responder, args=(escucha, salida, red, descripcion),
                daemon=True, name="camara-lan-descubrimiento",
            )
            hilo.start()
            self._recursos = recursos.pop_all()
            self._hilo = hilo

    def _responder(
        self, escucha: socket.socket, salida: socket.socket,
        red: ipaddress.IPv4Network | None, descripcion: bytes,
    ) -> None:
        while not self._cerrado.is_set():
            try:
                datos, origen = escucha.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                return
            nonce = _leer_solicitud(datos)
            if nonce is None:
                continue
            ip = _ip_privada(origen[0])
            if ip is None or (red is not None and ip not in red):
                continue
            try:
                # La respuesta cabe en la solicitud para no amplificar tráfico.
                salida.sendto(_RESPUESTA + nonce + descripcion, origen)
            except OSError:
                continue

    def cerrar(self) -> None:
        self._cerrado.set()
        if self._recursos is not None and self._hilo is not None:
            self._recursos.close()
            self._hilo.join()
            self._recursos = None
            self._hilo = None


def buscar_camaras(cancelada: threading.Event | None = None) -> list[CamaraLAN]:
    """Devuelve anuncios de esta búsqueda. La conexión TLS verifica la identidad aparte."""
    if cancelada is not None and cancelada.is_set():
        return []
    nonce = secrets.token_bytes(_LONGITUD_NONCE)
    solicitud = _SOLICITUD + nonce + _RELLENO
    resultados: dict[tuple[str, int], CamaraLAN] = {}
    with ExitStack() as recursos:
        lectores = []
        for ip in _interfaces_locales():
            if cancelada is not None and cancelada.is_set():
                return []
            with ExitStack() as adaptador:
                try:
                    lector = adaptador.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
                    lector.bind((ip, 0))
                    lector.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                    lector.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                    lector.setblocking(False)
                    lector.sendto(solicitud, (_GRUPO, _PUERTO))
                except OSError:
                    continue
                recursos.enter_context(adaptador.pop_all())
                lectores.append(lector)
        if not lectores:
            return []

        limite = time.monotonic() + _TIEMPO_BUSQUEDA
        while (restante := limite - time.monotonic()) > 0 and len(resultados) < _MAX_RESULTADOS:
            if cancelada is not None and cancelada.is_set():
                return []
            listos, _, _ = select.select(lectores, [], [], min(restante, _INTERVALO))
            for lector in listos:
                try:
                    datos, origen = lector.recvfrom(65535)
                except OSError:
                    continue
                camara = _leer_respuesta(datos, origen[0], nonce)
                if camara is not None:
                    resultados.setdefault((camara.ip, camara.puerto), camara)
                    if len(resultados) == _MAX_RESULTADOS:
                        break

    if cancelada is not None and cancelada.is_set():
        return []
    return sorted(resultados.values(), key=lambda camara: (camara.nombre.casefold(), camara.ip, camara.puerto))
