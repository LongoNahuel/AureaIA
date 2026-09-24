"""Mapa de calor de ocupacion: grilla de densidad acumulada con olvido
exponencial.

Reemplaza a la lista de "los ultimos 200 puntos" que usaba Conteo de
Personas: con 5 personas a 5 fps eso eran ~8 segundos de historia -- no un
mapa de calor sino un rastro -- y la UI dibujaba 4 elipses por punto en
cada cuadro (800 elipses por repintado). La grilla tiene tamaño fijo
chico, acumula todas las muestras, olvida con una vida media configurable
(lo reciente pesa mas que lo de hace una hora) y viaja a la UI como una
matriz uint8 de pocos KB que se pinta con un solo drawImage.
"""

from __future__ import annotations

import cv2
import numpy as np

# Resolucion de la grilla en su lado mayor. El otro lado sigue la
# proporcion del cuadro: con 64 una celda de 1080p son ~30 px, del orden
# del ancho de un pie de persona en un plano general.
GRID_LONG_SIDE = 64
# Vida media del calor acumulado: a los 10 minutos una marca pesa la mitad.
DEFAULT_HALF_LIFE_S = 600.0
# Desenfoque al publicar, en celdas: convierte las muestras puntuales en
# manchas continuas.
BLUR_SIGMA_CELLS = 1.2


class OccupancyHeatmap:
    def __init__(self, half_life_s: float = DEFAULT_HALF_LIFE_S) -> None:
        self._half_life_s = max(1.0, float(half_life_s))
        self._grid: np.ndarray | None = None
        self._frame_size: tuple[int, int] | None = None
        self._last_ts: float | None = None
        self.samples = 0

    def set_frame_size(self, width: int, height: int) -> None:
        """Dimensiona la grilla segun el cuadro. Un cambio de resolucion de
        la camara reinicia el acumulado: las celdas viejas ya no
        corresponden al mismo lugar de la escena."""
        if (width, height) == self._frame_size or width <= 0 or height <= 0:
            return
        self._frame_size = (width, height)
        scale = GRID_LONG_SIDE / max(width, height)
        cols = max(1, round(width * scale))
        rows = max(1, round(height * scale))
        self._grid = np.zeros((rows, cols), dtype=np.float32)
        self._last_ts = None
        self.samples = 0

    def decay_to(self, timestamp: float) -> None:
        if self._grid is None:
            return
        if self._last_ts is not None and timestamp > self._last_ts:
            self._grid *= 0.5 ** ((timestamp - self._last_ts) / self._half_life_s)
        self._last_ts = timestamp

    def add(self, points, timestamp: float | None = None) -> None:
        """Suma una muestra por punto (pixeles del cuadro)."""
        if self._grid is None or self._frame_size is None:
            return
        if timestamp is not None:
            self.decay_to(timestamp)
        width, height = self._frame_size
        rows, cols = self._grid.shape
        for x, y in points:
            col = min(cols - 1, max(0, int(x * cols / width)))
            row = min(rows - 1, max(0, int(y * rows / height)))
            self._grid[row, col] += 1.0
            self.samples += 1

    def snapshot(self) -> np.ndarray | None:
        """Densidad normalizada 0-255 (uint8, filas x columnas) cubriendo el
        cuadro entero, o None si todavia no hay nada acumulado. Es una
        copia: cruza de hilo (worker -> UI) dentro del DetectionEvent."""
        if self._grid is None or not self._grid.any():
            return None
        blurred = cv2.GaussianBlur(self._grid, (0, 0), BLUR_SIGMA_CELLS)
        peak = float(blurred.max())
        if peak <= 0:
            return None
        return np.clip(blurred * (255.0 / peak), 0, 255).astype(np.uint8)
