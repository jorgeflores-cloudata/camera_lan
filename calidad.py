"""Perfiles de video y límites compartidos por el anfitrión y el visor."""

PERFILES = {
    "baja": (426, 240, 55),
    "media": (640, 360, 70),
    "alta": (960, 540, 82),
    "maxima": (3840, 2160, 95),
}
ETIQUETAS = {"baja": "Low", "media": "Medium", "alta": "High", "maxima": "Maximum"}
FPS_MAXIMOS = 30
MAX_PIXELES = 3840 * 2160
MAX_JPEG = 16 * 1024 * 1024


def limitar_calidad(solicitada: str, maxima: str) -> str:
    niveles = tuple(PERFILES)
    return niveles[min(niveles.index(solicitada), niveles.index(maxima))]
