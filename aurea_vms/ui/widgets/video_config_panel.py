"""Sistema > Audio y Video > Video: los parametros de video de cada camara y
el trafico.

- Lo que llega, medido en el stream del VMS: resolucion y FPS. Sirve
  tambien con las camaras simuladas, que no tienen ONVIF.
- La codificacion en la camara, por ONVIF (core/onvif_video.py): codec,
  resolucion, FPS, bitrate, cuadros entre dos I y calidad de cada perfil.
  Se lee y se aplica a pedido, en un worker.
- El trafico: el de red de la PC (psutil, sin loopback) y, por camara, lo
  medido y el bitrate configurado del principal (la ultima lectura ONVIF de
  la sesion). El bitrate recibido de verdad por camara necesita contar
  bytes en el StreamWorker: queda propuesto a Daniel.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import psutil
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    DoubleSpinBox,
    HeaderCardWidget,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    StrongBodyLabel,
    TableWidget,
)

from aurea_vms.core import credential_store, onvif_video
from aurea_vms.core.onvif_video import CODECS, GOP_CODECS, VideoSettings, VideoStream
from aurea_vms.core.permissions import Perm, can
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models import repository
from aurea_vms.ui.notify import confirm, notify, warn
from aurea_vms.ui.workers import FunctionWorker

REFRESH_MS = 2000
# Rangos cuando la camara no los informa en sus opciones.
DEFAULT_FPS = (1, 60)
DEFAULT_BITRATE_KBPS = (16, 40000)
DEFAULT_GOV = (1, 400)
PROFILE_LABELS = ("Principal", "Secundario")
TRAFFIC_COLUMNS = ["Cámara", "Estado", "Resolución", "FPS medido", "Bitrate configurado"]


def _num(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def measure_stream(device_id: int) -> tuple[str, str, str]:
    """(estado, resolucion, FPS) de lo que el VMS recibe de la camara. Solo
    hay stream si una vista o una analitica la tiene abierta."""
    worker = stream_manager.get_worker(device_id, "main")
    if worker is None:
        return "Sin abrir", "—", "—"
    frame = worker.get_latest_frame()
    if frame is None or worker.is_stale():
        return "Sin señal", "—", "—"
    height, width = frame.shape[:2]
    return "Recibiendo", f"{width}×{height}", _num(worker.get_fps())


class _StreamEditor:
    """Los controles de un perfil (una columna de la grilla)."""

    def __init__(self, parent: QWidget, stream: VideoStream) -> None:
        self.stream = stream
        self.codec = ComboBox(parent)
        codecs = [c for c in CODECS if c in stream.options]
        if stream.codec not in codecs:
            codecs.insert(0, stream.codec)
        for codec in codecs:
            self.codec.addItem(CODECS.get(codec, codec), userData=codec)
        self.resolution = ComboBox(parent)
        self.fps = SpinBox(parent)
        self.bitrate = SpinBox(parent)
        self.bitrate.setSuffix(" kbps")
        self.gov = SpinBox(parent)
        self.quality = DoubleSpinBox(parent)
        self.quality.setDecimals(1)
        self.apply_button = PrimaryPushButton("Aplicar", parent)

        self.codec.setCurrentIndex(codecs.index(stream.codec))
        self._on_codec_changed()
        self._select_resolution(stream.width, stream.height)
        if stream.fps is not None:
            self.fps.setValue(stream.fps)
        if stream.bitrate_kbps is not None:
            self.bitrate.setValue(stream.bitrate_kbps)
        if stream.gov_length is not None:
            self.gov.setValue(stream.gov_length)
        if stream.quality is not None:
            low, high = stream.quality_range or (0.0, 100.0)
            self.quality.setRange(low, high)
            self.quality.setValue(stream.quality)
        else:
            self.quality.setEnabled(False)
        self.codec.currentIndexChanged.connect(self._on_codec_changed)

    def widgets(self) -> list[QWidget]:
        return [self.codec, self.resolution, self.fps, self.bitrate, self.gov, self.quality]

    def _on_codec_changed(self, *_args) -> None:
        """Las resoluciones y los rangos son por codec: al cambiarlo se
        mantiene la resolucion elegida si el codec nuevo la acepta; si no,
        la primera que acepta. La que la camara tiene ahora se ofrece
        aunque no figure en sus opciones (para no cambiarla sin querer)."""
        codec = self.codec.currentData()
        options = self.stream.options.get(codec)
        configured = (self.stream.width, self.stream.height)
        current = self.resolution.currentData() or configured
        resolutions = list(options.resolutions) if options and options.resolutions else []
        if codec == self.stream.codec and configured not in resolutions:
            resolutions.insert(0, configured)
        if not resolutions:
            resolutions = [current]
        self.resolution.clear()
        for width, height in resolutions:
            self.resolution.addItem(f"{width}×{height}", userData=(width, height))
        self.resolution.setCurrentIndex(max(self.resolution.findData(current), 0))
        self.fps.setRange(*((options and options.fps) or DEFAULT_FPS))
        self.bitrate.setRange(*((options and options.bitrate_kbps) or DEFAULT_BITRATE_KBPS))
        self.gov.setRange(*((options and options.gov_length) or DEFAULT_GOV))
        self.gov.setEnabled(codec in GOP_CODECS)

    def _select_resolution(self, width: int, height: int) -> None:
        index = self.resolution.findData((width, height))
        if index >= 0:
            self.resolution.setCurrentIndex(index)

    def settings(self) -> VideoSettings:
        codec = self.codec.currentData()
        width, height = self.resolution.currentData()
        return VideoSettings(
            codec=codec,
            width=width,
            height=height,
            fps=self.fps.value(),
            bitrate_kbps=self.bitrate.value(),
            gov_length=self.gov.value() if codec in GOP_CODECS else None,
            quality=self.quality.value() if self.quality.isEnabled() else None,
        )


class VideoConfigPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._workers: list[FunctionWorker] = []
        self._editors: list[_StreamEditor] = []
        self._devices: list[tuple[int, str]] = []
        # Bitrate configurado del principal por camara (ultima lectura ONVIF).
        self._bitrates: dict[int, int] = {}
        self._net_last: tuple[float, int, int] | None = None

        camera_card = HeaderCardWidget(self)
        camera_card.setTitle("Video de la cámara")
        camera = QWidget(camera_card)
        camera_card.viewLayout.addWidget(camera)
        self._camera_layout = QVBoxLayout(camera)

        row = QHBoxLayout()
        self.device_selector = ComboBox(camera)
        self.device_selector.setMinimumWidth(320)
        self.device_selector.currentIndexChanged.connect(self._on_device_changed)
        row.addWidget(self.device_selector)
        self.read_button = PushButton("Leer de la cámara", camera)
        self.read_button.clicked.connect(self.read_from_camera)
        row.addWidget(self.read_button)
        row.addStretch(1)
        self._camera_layout.addLayout(row)

        self.received_label = BodyLabel("", camera)
        self._camera_layout.addWidget(self.received_label)
        self.status_label = CaptionLabel("", camera)
        self.status_label.setWordWrap(True)
        self._camera_layout.addWidget(self.status_label)
        self.streams_box = QWidget(camera)
        self._camera_layout.addWidget(self.streams_box)

        traffic_card = HeaderCardWidget(self)
        traffic_card.setTitle("Tráfico")
        traffic = QWidget(traffic_card)
        traffic_card.viewLayout.addWidget(traffic)
        traffic_layout = QVBoxLayout(traffic)
        self.network_label = BodyLabel("Red de la PC: midiendo…", traffic)
        traffic_layout.addWidget(self.network_label)
        self.traffic_table = TableWidget(traffic)
        self.traffic_table.setColumnCount(len(TRAFFIC_COLUMNS))
        self.traffic_table.setHorizontalHeaderLabels(TRAFFIC_COLUMNS)
        self.traffic_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.traffic_table.verticalHeader().setVisible(False)
        self.traffic_table.setBorderVisible(True)
        self.traffic_table.setBorderRadius(6)
        self.traffic_table.setMinimumHeight(200)
        traffic_layout.addWidget(self.traffic_table)
        self.total_label = CaptionLabel("", traffic)
        self.total_label.setWordWrap(True)
        traffic_layout.addWidget(self.total_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(camera_card)
        layout.addWidget(traffic_card)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh_measurements)

        self.reload_devices()

    # --- camaras -------------------------------------------------

    def reload_devices(self) -> None:
        current_id = self.device_selector.currentData()
        self._devices = [(d.id, d.name) for d in repository.list_devices()]
        self.device_selector.blockSignals(True)
        self.device_selector.clear()
        for device_id, name in self._devices:
            self.device_selector.addItem(name, userData=device_id)
        if not self._devices:
            self.device_selector.addItem("(sin cámaras registradas)", userData=None)
        index = self.device_selector.findData(current_id) if current_id is not None else -1
        self.device_selector.setCurrentIndex(max(index, 0))
        self.device_selector.blockSignals(False)
        self._on_device_changed()

    def _current_device(self):
        device_id = self.device_selector.currentData()
        return repository.get_device(device_id) if device_id is not None else None

    def _onvif_problem(self, device) -> str | None:
        if device is None:
            return "Elegí una cámara."
        if device.onvif_port is None:
            return (
                "Esta cámara está dada de alta solo por RTSP (sin ONVIF): su codificación "
                "se cambia en la propia cámara."
            )
        if credential_store.es_ilegible(device.password):
            return "La contraseña guardada de la cámara no se puede leer: volvé a cargarla."
        return None

    def _on_device_changed(self, *_args) -> None:
        self._set_streams([])
        device = self._current_device()
        problem = self._onvif_problem(device)
        self.read_button.setEnabled(device is not None and problem is None)
        self.status_label.setText(problem or "Leé la codificación para verla y cambiarla.")
        self.refresh_measurements()

    # --- codificacion (ONVIF) -------------------------------------------------

    def read_from_camera(self) -> None:
        device = self._current_device()
        problem = self._onvif_problem(device)
        if problem is not None:
            self.status_label.setText(problem)
            return
        ip, port, user, password = device.ip, device.onvif_port, device.username, device.password
        channel = device.channel or 1
        self.status_label.setText("Leyendo de la cámara…")
        self.read_button.setEnabled(False)
        self._run(
            lambda: onvif_video.fetch_video_streams(ip, port, user, password, channel),
            lambda streams, device_id=device.id: self._on_streams_read(device_id, streams),
            "No se pudo leer",
        )

    def _on_streams_read(self, device_id: int, streams: list[VideoStream]) -> None:
        self.read_button.setEnabled(True)
        if streams and streams[0].bitrate_kbps is not None:
            self._bitrates[device_id] = streams[0].bitrate_kbps
        if self.device_selector.currentData() != device_id:
            return  # se cambio de camara mientras se leia
        self._set_streams(streams)
        self.status_label.setText(
            "Así está configurada en la cámara. Lo que se aplica queda guardado en ella."
        )
        self.refresh_measurements()

    def _set_streams(self, streams: list[VideoStream]) -> None:
        """Rearma la grilla: una columna por perfil."""
        self._camera_layout.removeWidget(self.streams_box)
        self.streams_box.deleteLater()
        self.streams_box = QWidget(self)
        self._camera_layout.addWidget(self.streams_box)
        self._editors = []
        if not streams:
            return
        grid = QGridLayout(self.streams_box)
        labels = ["", "Códec", "Resolución", "FPS", "Bitrate", "Cuadros entre dos I", "Calidad"]
        for row, text in enumerate(labels):
            grid.addWidget(StrongBodyLabel(text, self.streams_box), row, 0)
        for column, stream in enumerate(streams, start=1):
            editor = _StreamEditor(self.streams_box, stream)
            title = (
                PROFILE_LABELS[column - 1] if column <= len(PROFILE_LABELS) else f"Perfil {column}"
            )
            header = StrongBodyLabel(title, self.streams_box)
            header.setToolTip(f"Perfil ONVIF: {stream.profile_name}")
            grid.addWidget(header, 0, column)
            for row, widget in enumerate(editor.widgets(), start=1):
                grid.addWidget(widget, row, column)
            grid.addWidget(editor.apply_button, len(labels), column)
            editor.apply_button.clicked.connect(lambda _=False, e=editor: self._apply(e))
            self._editors.append(editor)
        grid.setColumnStretch(len(streams) + 1, 1)

    def _apply(self, editor: _StreamEditor) -> None:
        if not can(Perm.DEVICE_ADMIN):
            warn(self, "Video", "Tu rol no puede cambiar la configuración de las cámaras.")
            return
        device = self._current_device()
        problem = self._onvif_problem(device)
        if problem is not None:
            self.status_label.setText(problem)
            return
        settings = editor.settings()
        stream = editor.stream
        profile = PROFILE_LABELS[0] if editor is self._editors[0] else stream.profile_name
        text = (
            f"{profile} de «{device.name}»: {CODECS.get(settings.codec, settings.codec)} "
            f"{settings.width}×{settings.height}, {settings.fps} FPS, "
            f"{settings.bitrate_kbps} kbps.\n\n"
            "El video puede cortarse unos segundos mientras la cámara se reconfigura."
        )
        resized = (settings.width, settings.height) != (stream.width, stream.height)
        if editor is self._editors[0] and resized and repository.list_analytics_configs(device.id):
            text += (
                f"\n\nLas zonas de sus analíticas están en píxeles del cuadro de "
                f"{stream.width}×{stream.height}: con otra resolución quedan corridas. "
                "Revisalas en Analizadores."
            )
        if not confirm(self, "Aplicar en la cámara", text):
            return
        ip, port, user, password = device.ip, device.onvif_port, device.username, device.password
        channel = device.channel or 1
        token = stream.config_token

        def apply_and_read() -> list[VideoStream]:
            onvif_video.apply_video_settings(ip, port, user, password, token, settings)
            # Lo que la camara guardo de verdad (puede ajustar valores).
            return onvif_video.fetch_video_streams(ip, port, user, password, channel)

        self.status_label.setText("Aplicando en la cámara…")
        for each in self._editors:
            each.apply_button.setEnabled(False)

        def done(streams, device_id=device.id) -> None:
            self._on_streams_read(device_id, streams)
            notify(self, "Video", f"Aplicado en «{device.name}».")

        self._run(apply_and_read, done, "No se pudo aplicar")

    def _run(self, func: Callable, on_success: Callable, error_prefix: str) -> None:
        worker = FunctionWorker(func, self)
        worker.succeeded.connect(on_success)
        worker.failed.connect(lambda message: self._on_failed(error_prefix, message))
        worker.finished.connect(
            lambda: self._workers.remove(worker) if worker in self._workers else None
        )
        self._workers.append(worker)
        worker.start()

    def _on_failed(self, prefix: str, message: str) -> None:
        self.status_label.setText(f"{prefix}: {message}")
        self.read_button.setEnabled(self._onvif_problem(self._current_device()) is None)
        for editor in self._editors:
            editor.apply_button.setEnabled(True)

    # --- medido y trafico -------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        super().showEvent(event)
        self.refresh_measurements()
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._timer.stop()
        super().hideEvent(event)

    def refresh_measurements(self) -> None:
        device_id = self.device_selector.currentData()
        if device_id is None:
            self.received_label.setText("")
        else:
            state, resolution, fps = measure_stream(device_id)
            if state == "Recibiendo":
                self.received_label.setText(f"El VMS recibe: {resolution} a {fps} FPS")
            elif state == "Sin señal":
                self.received_label.setText("El VMS no recibe video: la cámara está sin señal.")
            else:
                self.received_label.setText(
                    "El VMS no la está recibiendo: ninguna vista ni analítica la tiene abierta."
                )
        self._refresh_network()
        self._refresh_traffic_table()

    def _refresh_network(self) -> None:
        received = sent = 0
        for name, counters in psutil.net_io_counters(pernic=True).items():
            if name == "lo" or name.lower().startswith("loopback"):
                continue
            received += counters.bytes_recv
            sent += counters.bytes_sent
        now = time.monotonic()
        last, self._net_last = self._net_last, (now, received, sent)
        if last is None or now <= last[0]:
            return
        elapsed = now - last[0]
        down = (received - last[1]) * 8 / elapsed / 1e6
        up = (sent - last[2]) * 8 / elapsed / 1e6
        self.network_label.setText(f"Red de la PC: entra {_num(down)} Mb/s · sale {_num(up)} Mb/s")

    def _refresh_traffic_table(self) -> None:
        self.traffic_table.setRowCount(len(self._devices))
        for row, (device_id, name) in enumerate(self._devices):
            state, resolution, fps = measure_stream(device_id)
            kbps = self._bitrates.get(device_id)
            values = [name, state, resolution, fps, f"{kbps} kbps" if kbps else "—"]
            for column, value in enumerate(values):
                self.traffic_table.setItem(row, column, QTableWidgetItem(value))
        if self._bitrates:
            total = sum(self._bitrates.values()) / 1000
            self.total_label.setText(
                f"Configurado en las cámaras leídas: {_num(total)} Mb/s en total. Es el tope "
                "que se le pidió a cada una, no lo medido."
            )
        else:
            self.total_label.setText(
                "El bitrate configurado aparece al leer cada cámara por ONVIF."
            )
