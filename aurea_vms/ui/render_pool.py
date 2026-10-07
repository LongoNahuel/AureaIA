"""Pool de hilos que arma los cuadros de video fuera del hilo de la GUI.

Fase V1b (2026-10-07). Medido antes: 16 recuadros 1080p visibles ocupaban el
81 % del hilo de la GUI (achicar ~1,1 ms, overlay ~1,7 ms, QImage->QPixmap
~0,5 ms por cuadro) y un clic tardaba 60-85 ms en atenderse. Ahora cada
recuadro manda un trabajo que achica, convierte y dibuja el overlay sobre un
QImage (QPainter sobre QImage es valido en cualquier hilo; sobre QPixmap o un
widget, no), y en la GUI solo queda poner el resultado.

Reglas:
- **El ultimo gana.** Cada recuadro tiene a lo sumo un trabajo esperando: si
  llega otro antes de que empiece, reemplaza al anterior (un cuadro viejo no
  vale la pena dibujarlo).
- **Uno en curso por recuadro.** Asi los resultados llegan en orden y el
  estado propio del recuadro (por ejemplo el destello de la linea) lo toca
  un solo hilo a la vez.
- El resultado vuelve a la GUI por una signal encolada; quien lo recibe
  decide si todavia sirve (el recuadro pudo cerrarse o cambiar de camara).
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from collections.abc import Callable, Hashable
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal

logger = logging.getLogger(__name__)

Trabajo = Callable[[], Any]
Entrega = Callable[[Any], None]


def _hilos_por_defecto() -> int:
    """El overlay es Python y tiene el GIL (achicar y convertir lo sueltan):
    mas hilos no arman mas cuadros y le quitan el GIL a la GUI. Medido
    2026-10-07, 16 recuadros 1080p (sesiones/2026-10-07.md):
    1 hilo 303 cuadros/s y atraso p99 3,8 ms; 2 hilos 298/s y 8,4 ms;
    4 hilos 274/s y 80 ms."""
    return 1 if (os.cpu_count() or 1) < 4 else 2


class _Correo(QObject):
    """Vive en el hilo de la GUI: la signal emitida desde un hilo de render
    llega encolada y `_entregar` corre en la GUI."""

    listo = Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.listo.connect(self._entregar, Qt.ConnectionType.QueuedConnection)

    @staticmethod
    def _entregar(entrega: Entrega, resultado: Any) -> None:
        try:
            entrega(resultado)
        except Exception:
            logger.exception("No se pudo mostrar un cuadro ya armado")


class RenderPool:
    def __init__(self, hilos: int | None = None) -> None:
        self._cond = threading.Condition()
        self._pendientes: dict[Hashable, tuple[Trabajo, Entrega]] = {}
        self._en_curso: set[Hashable] = set()
        self._cola: deque[Hashable] = deque()
        self._detenido = False
        self._correo = _Correo()
        self._hilos = [
            threading.Thread(target=self._trabajar, name=f"Render-{i}", daemon=True)
            for i in range(hilos or _hilos_por_defecto())
        ]
        for hilo in self._hilos:
            hilo.start()

    def enviar(self, clave: Hashable, trabajo: Trabajo, entrega: Entrega) -> None:
        """`trabajo` corre en un hilo de render; `entrega(resultado)`, en la
        GUI. Un trabajo de la misma clave que todavia no empezo se pisa."""
        with self._cond:
            if self._detenido:
                return
            nueva = clave not in self._pendientes
            self._pendientes[clave] = (trabajo, entrega)
            if nueva and clave not in self._en_curso:
                self._cola.append(clave)
                self._cond.notify()

    def descartar(self, clave: Hashable) -> None:
        """Olvida el trabajo que espera con esa clave. El que esta en curso
        termina, y su entrega decide si todavia sirve."""
        with self._cond:
            self._pendientes.pop(clave, None)

    def detener(self, timeout: float = 2.0) -> None:
        with self._cond:
            self._detenido = True
            self._pendientes.clear()
            self._cola.clear()
            self._cond.notify_all()
        for hilo in self._hilos:
            hilo.join(timeout)

    def _trabajar(self) -> None:
        while True:
            with self._cond:
                while not self._cola and not self._detenido:
                    self._cond.wait()
                if self._detenido:
                    return
                clave = self._cola.popleft()
                tarea = self._pendientes.pop(clave, None)
                if tarea is None:  # descartado mientras esperaba
                    continue
                self._en_curso.add(clave)
            trabajo, entrega = tarea
            resultado = None
            try:
                resultado = trabajo()
            except Exception:
                logger.exception("Falló el armado de un cuadro (%s)", clave)
            finally:
                with self._cond:
                    self._en_curso.discard(clave)
                    if clave in self._pendientes and not self._detenido:
                        self._cola.append(clave)
                        self._cond.notify()
            if resultado is not None:
                self._correo.listo.emit(entrega, resultado)


class PoolSincronico:
    """Mismo contrato, todo en el hilo que llama. Para los tests que miran el
    pixmap justo despues de `_refresh_frame`."""

    def enviar(self, clave: Hashable, trabajo: Trabajo, entrega: Entrega) -> None:
        resultado = trabajo()
        if resultado is not None:
            entrega(resultado)

    def descartar(self, clave: Hashable) -> None:
        pass

    def detener(self, timeout: float = 2.0) -> None:
        pass


_pool: RenderPool | PoolSincronico | None = None
_pool_lock = threading.Lock()


def pool() -> RenderPool | PoolSincronico:
    """El pool compartido por todos los recuadros. Se crea al primer uso
    (con la QApplication ya armada) y se recrea despues de `detener()`, para
    el ciclo logout->login."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = RenderPool()
        return _pool


def usar(nuevo: RenderPool | PoolSincronico | None) -> None:
    """Reemplaza el pool compartido (tests)."""
    global _pool
    with _pool_lock:
        _pool = nuevo


def detener() -> None:
    global _pool
    with _pool_lock:
        actual, _pool = _pool, None
    if actual is not None:
        actual.detener()
