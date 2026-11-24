"""Encuadre local del video recibido por el Viewer."""

from dataclasses import dataclass

from PIL import Image


_NIVELES = (100, 125, 150, 200, 250, 300, 400)
ZOOM_MAXIMO = _NIVELES[-1]


def _limitar(valor: float, minimo: float, maximo: float) -> float:
    return min(maximo, max(minimo, valor))


@dataclass(frozen=True)
class Zoom:
    indice: int = 0
    centro_x: float = 0.5
    centro_y: float = 0.5

    def __post_init__(self) -> None:
        indice = min(len(_NIVELES) - 1, max(0, self.indice))
        object.__setattr__(self, "indice", indice)
        borde = 0.5 / self.factor
        object.__setattr__(self, "centro_x", _limitar(self.centro_x, borde, 1 - borde))
        object.__setattr__(self, "centro_y", _limitar(self.centro_y, borde, 1 - borde))

    @property
    def porcentaje(self) -> int:
        return _NIVELES[self.indice]

    @property
    def factor(self) -> float:
        return self.porcentaje / 100

    def cambiar(self, pasos: int, ancla: tuple[float, float] = (0.5, 0.5)) -> "Zoom":
        indice = min(len(_NIVELES) - 1, max(0, self.indice + pasos))
        if indice == self.indice:
            return self

        # Mantiene el punto de la imagen bajo el cursor en el mismo lugar de la vista.
        cambio = 1 / self.factor - 100 / _NIVELES[indice]
        x, y = ancla
        return Zoom(
            indice,
            self.centro_x + (_limitar(x, 0, 1) - 0.5) * cambio,
            self.centro_y + (_limitar(y, 0, 1) - 0.5) * cambio,
        )

    def desplazar(self, dx: float, dy: float) -> "Zoom":
        """Desplaza la imagen según el arrastre, como fracción de la vista."""
        if self.indice == 0:
            return self
        return Zoom(self.indice, self.centro_x - dx / self.factor, self.centro_y - dy / self.factor)

    def aplicar(
        self, imagen: Image.Image, dimensiones: tuple[int, int] | None = None,
    ) -> Image.Image:
        dimensiones = imagen.size if dimensiones is None else dimensiones
        if self.indice == 0:
            if dimensiones == imagen.size:
                return imagen
            return imagen.resize(dimensiones, Image.Resampling.BILINEAR)

        ancho, alto = imagen.size
        radio = 0.5 / self.factor
        recorte = (
            max(0.0, self.centro_x - radio) * ancho,
            max(0.0, self.centro_y - radio) * alto,
            min(1.0, self.centro_x + radio) * ancho,
            min(1.0, self.centro_y + radio) * alto,
        )
        return imagen.resize(dimensiones, Image.Resampling.BILINEAR, box=recorte)


def ajustar_dimensiones(original: tuple[int, int], espacio: tuple[int, int]) -> tuple[int, int]:
    ancho, alto = original
    limite_ancho, limite_alto = (max(1, dimension) for dimension in espacio)
    if ancho * limite_alto >= alto * limite_ancho:
        return limite_ancho, max(1, alto * limite_ancho // ancho)
    return max(1, ancho * limite_alto // alto), limite_alto
