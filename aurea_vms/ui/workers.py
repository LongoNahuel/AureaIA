"""Helper generico para correr funciones bloqueantes (I/O de red, OpenCV,
ONVIF) fuera del hilo principal de Qt sin congelar la UI.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal


class FunctionWorker(QThread):
    """Corre `func` en un thread aparte y reporta el resultado (o la
    excepcion) de vuelta via Signals, que Qt entrega en el hilo que
    conecto el slot (normalmente el hilo principal)."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, func: Callable[[], Any], parent=None) -> None:
        super().__init__(parent)
        self._func = func

    def run(self) -> None:
        try:
            result = self._func()
        except Exception as exc:  # noqa: BLE001 - se reporta a la UI, no se silencia
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
