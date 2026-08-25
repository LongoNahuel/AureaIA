"""Panel de control PTZ: selector de camara (solo las que tienen PTZ) +
pad direccional + zoom. Mientras se mantiene apretado un boton, manda
ContinuousMove por ONVIF; al soltar, manda Stop."""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, FluentIcon, HeaderCardWidget, ToolButton

from aurea_vms.core import ptz_control
from aurea_vms.models import repository
from aurea_vms.ui.workers import FunctionWorker

# (etiqueta, icono, pan, tilt)
DIRECTIONS = [
    (0, 1, FluentIcon.CARE_UP_SOLID, 0.0, 1.0),
    (1, 0, FluentIcon.CARE_LEFT_SOLID, -1.0, 0.0),
    (1, 2, FluentIcon.CARE_RIGHT_SOLID, 1.0, 0.0),
    (2, 1, FluentIcon.CARE_DOWN_SOLID, 0.0, -1.0),
]


class PtzControlPanel(HeaderCardWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Operación PTZ")
        self._workers: list[FunctionWorker] = []

        content = QWidget(self)
        self.viewLayout.addWidget(content)
        layout = QVBoxLayout(content)

        self.device_selector = ComboBox(content)
        layout.addWidget(self.device_selector)

        self.status_label = BodyLabel("", content)
        layout.addWidget(self.status_label)

        pad_row = QHBoxLayout()

        pad_grid = QGridLayout()
        for row, col, icon, pan, tilt in DIRECTIONS:
            button = ToolButton(icon, content)
            button.setFixedSize(44, 44)
            button.pressed.connect(lambda p=pan, t=tilt: self._start_move(p, t, 0.0))
            button.released.connect(self._stop)
            pad_grid.addWidget(button, row, col)
        pad_row.addLayout(pad_grid)

        zoom_col = QVBoxLayout()
        zoom_in = ToolButton(FluentIcon.ZOOM_IN, content)
        zoom_in.setFixedSize(44, 44)
        zoom_in.pressed.connect(lambda: self._start_move(0.0, 0.0, 1.0))
        zoom_in.released.connect(self._stop)
        zoom_out = ToolButton(FluentIcon.ZOOM_OUT, content)
        zoom_out.setFixedSize(44, 44)
        zoom_out.pressed.connect(lambda: self._start_move(0.0, 0.0, -1.0))
        zoom_out.released.connect(self._stop)
        zoom_col.addWidget(zoom_in)
        zoom_col.addWidget(zoom_out)
        pad_row.addSpacing(24)
        pad_row.addLayout(zoom_col)
        pad_row.addStretch(1)

        layout.addLayout(pad_row)

        self.reload_devices()

    def reload_devices(self) -> None:
        current_id = self.device_selector.currentData()
        self.device_selector.blockSignals(True)
        self.device_selector.clear()
        ptz_devices = [d for d in repository.list_devices() if d.has_ptz]
        for device in ptz_devices:
            self.device_selector.addItem(device.name, userData=device.id)
        if not ptz_devices:
            self.device_selector.addItem("(sin cámaras PTZ registradas)", userData=None)
        if current_id is not None:
            index = self.device_selector.findData(current_id)
            if index >= 0:
                self.device_selector.setCurrentIndex(index)
        self.device_selector.blockSignals(False)

    def _current_device(self):
        device_id = self.device_selector.currentData()
        return repository.get_device(device_id) if device_id is not None else None

    def _run(self, func) -> None:
        worker = FunctionWorker(func, self)
        worker.failed.connect(lambda msg: self.status_label.setText(f"Error PTZ: {msg}"))
        worker.finished.connect(
            lambda: self._workers.remove(worker) if worker in self._workers else None
        )
        self._workers.append(worker)
        worker.start()

    def _start_move(self, pan: float, tilt: float, zoom: float) -> None:
        device = self._current_device()
        if device is None or device.onvif_port is None:
            self.status_label.setText("Seleccioná una cámara PTZ con ONVIF configurado.")
            return
        self.status_label.setText("Moviendo...")
        self._run(
            lambda: ptz_control.continuous_move(
                device.ip, device.onvif_port, device.username, device.password, pan, tilt, zoom
            )
        )

    def _stop(self) -> None:
        device = self._current_device()
        if device is None or device.onvif_port is None:
            return
        self.status_label.setText("")
        self._run(
            lambda: ptz_control.stop(device.ip, device.onvif_port, device.username, device.password)
        )
