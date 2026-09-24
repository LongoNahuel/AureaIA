"""Visor forense de una captura de rostro.

Se abre con doble clic en una miniatura (galeria del panel lateral o tira
del dashboard) o clic en la ficha destacada. Pensado para que un operador o
un investigador pueda:

- ampliar la cara en resolucion NATIVA (zoom con la rueda, arrastrar para
  moverse, 1:1 y "pixeles reales" sin suavizado para no inventar detalle);
- ver la escena completa con la caja de la deteccion, para ubicar a la
  persona en el lugar;
- aplicar realces de VISUALIZACION (contraste local, nitidez, grises) sin
  tocar la evidencia: lo que se exporta es siempre el original;
- leer la ficha: camara, fecha y hora, desglose de la
  calidad, distancia entre ojos, posicion en el cuadro y huella SHA-256;
- recorrer las tomas previas de esa captura (el mismo paso por la camara);
- exportar la evidencia (PNG original sin perdida, escena, escena marcada y
  una ficha JSON con los hashes de cada archivo).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CheckBox,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    ToolButton,
)
from qfluentwidgets import FluentIcon as FIF

from aurea_vms.ui.face_registry import FaceRecord, face_registry
from aurea_vms.ui.notify import notify, warn
from aurea_vms.ui.widgets.analytics_visuals import (
    GRID_LINE,
    STATUS_OK,
    STATUS_WARNING,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    LevelBar,
)
from aurea_vms.ui.widgets.face_visuals import quality_label, record_pixmap

# Distancia entre ojos (px) por debajo de la cual la cara no alcanza para una
# identificacion confiable (referencia habitual: ~40 px entre pupilas).
MIN_FORENSIC_EYE_PX = 40
ZOOM_STEPS = (0.25, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0)
BG = "#0e131b"
STRIP_THUMB = QSize(64, 64)


def _bgr_qimage(image: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    height, width = rgb.shape[:2]
    return QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888).copy()


def enhance(
    image: np.ndarray, *, contrast: bool = False, sharpen: bool = False, gray: bool = False
) -> np.ndarray:
    """Realces SOLO de visualizacion. Nunca se aplican a lo exportado."""
    result = image
    if contrast:
        lab = cv2.cvtColor(result, cv2.COLOR_BGR2LAB)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(4, 4))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        result = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    if sharpen:
        blurred = cv2.GaussianBlur(result, (0, 0), 1.2)
        result = cv2.addWeighted(result, 1.6, blurred, -0.6, 0)
    if gray:
        result = cv2.cvtColor(cv2.cvtColor(result, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    return result


def sha256_png(image: np.ndarray) -> tuple[str, bytes]:
    """PNG sin perdida del original y su huella: el hash identifica
    exactamente el archivo exportado."""
    ok, buffer = cv2.imencode(".png", image)
    data = buffer.tobytes() if ok else b""
    return hashlib.sha256(data).hexdigest(), data


def decode_context(record: FaceRecord) -> np.ndarray | None:
    if not record.context_jpeg:
        return None
    array = np.frombuffer(record.context_jpeg, dtype=np.uint8)
    return cv2.imdecode(array, cv2.IMREAD_COLOR)


def context_box(record: FaceRecord) -> tuple[float, float, float, float]:
    """Caja de la cara en pixeles del cuadro de contexto (reducido)."""
    x, y, w, h = record.bbox
    scale = record.context_scale or 1.0
    return x * scale, y * scale, w * scale, h * scale


def evidence_sheet(record: FaceRecord, camera: str) -> dict:
    """Ficha de la toma: lo que se muestra y lo que se exporta."""
    moment = dt.datetime.fromtimestamp(record.timestamp)
    evidence = record.evidence
    return {
        "camara": camera,
        "camara_id": record.device_id,
        "fecha": moment.strftime("%d/%m/%Y"),
        "hora": moment.strftime("%H:%M:%S"),
        "timestamp": record.timestamp,
        "captura_id": record.capture_id,
        "confianza_deteccion": round(record.confidence, 4),
        "calidad": {
            key: round(float(value), 4)
            for key, value in record.details.items()
            if key != "distancia_ojos_px"
        }
        or ({"total": round(record.quality, 4)} if record.quality is not None else {}),
        "distancia_ojos_px": round(float(record.details.get("distancia_ojos_px", 0.0)), 1),
        "caja_en_cuadro": {
            "x": record.bbox[0],
            "y": record.bbox[1],
            "w": record.bbox[2],
            "h": record.bbox[3],
        },
        "resolucion_cuadro": {"w": record.frame_size[0], "h": record.frame_size[1]},
        "resolucion_recorte": {"w": int(evidence.shape[1]), "h": int(evidence.shape[0])},
    }


def export_evidence(record: FaceRecord, target_dir: Path, camera: str) -> Path:
    """Escribe la carpeta de evidencia y devuelve su ruta."""
    moment = dt.datetime.fromtimestamp(record.timestamp).strftime("%Y%m%d_%H%M%S")
    folder = target_dir / f"rostro_cam{record.device_id}_{moment}_c{record.capture_id}"
    folder.mkdir(parents=True, exist_ok=True)
    sheet = evidence_sheet(record, camera)
    files: dict[str, str] = {}

    digest, png = sha256_png(record.evidence)
    (folder / "rostro_original.png").write_bytes(png)
    files["rostro_original.png"] = digest

    context = decode_context(record)
    if record.context_jpeg and context is not None:
        (folder / "escena.jpg").write_bytes(record.context_jpeg)
        files["escena.jpg"] = hashlib.sha256(record.context_jpeg).hexdigest()
        marked = context.copy()
        x, y, w, h = (round(v) for v in context_box(record))
        cv2.rectangle(marked, (x, y), (x + w, y + h), (0, 200, 255), 2)
        digest, png = sha256_png(marked)
        (folder / "escena_marcada.png").write_bytes(png)
        files["escena_marcada.png"] = digest

    sheet["archivos_sha256"] = files
    sheet["nota"] = (
        "rostro_original.png es el recorte sin realces ni reescalado, en la "
        "resolucion nativa de la camara. escena.jpg es el cuadro completo "
        "reducido para contexto."
    )
    (folder / "ficha.json").write_text(json.dumps(sheet, ensure_ascii=False, indent=2), "utf-8")
    return folder


class ZoomView(QGraphicsView):
    """Visor con zoom (rueda / botones), arrastre y lectura del pixel bajo
    el cursor en coordenadas de la imagen mostrada."""

    zoom_changed = Signal(float)
    hovered = Signal(object)  # (x, y) en pixeles de la imagen, o None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item = QGraphicsPixmapItem()
        self._scene.addItem(self._pixmap_item)
        self._box_item = QGraphicsRectItem()
        pen = QPen(QColor("#22d3ee"))
        pen.setWidthF(2.0)
        pen.setCosmetic(True)  # mismo grosor a cualquier zoom
        self._box_item.setPen(pen)
        self._box_item.setVisible(False)
        self._scene.addItem(self._box_item)
        self._zoom = 1.0
        self._fit = True
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor(BG))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setStyleSheet(
            "QScrollBar:vertical, QScrollBar:horizontal { background: transparent;"
            " width: 6px; height: 6px; margin: 0; }"
            "QScrollBar::handle { background: rgba(255,255,255,0.18); border-radius: 3px; }"
            "QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }"
            "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }"
        )

    def set_image(self, image: QImage, box: tuple[float, float, float, float] | None) -> None:
        self._pixmap_item.setPixmap(QPixmap.fromImage(image))
        self._scene.setSceneRect(QRectF(0, 0, image.width(), image.height()))
        if box is not None:
            self._box_item.setRect(QRectF(*box))
        self._box_item.setVisible(box is not None)
        if self._fit:
            self.fit()
        else:
            self._apply()

    def set_pixelated(self, pixelated: bool) -> None:
        """Sin suavizado se ven los pixeles reales: ampliar no inventa detalle."""
        mode = (
            Qt.TransformationMode.FastTransformation
            if pixelated
            else Qt.TransformationMode.SmoothTransformation
        )
        self._pixmap_item.setTransformationMode(mode)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not pixelated)
        self.viewport().update()

    def fit(self) -> None:
        self._fit = True
        rect = self._scene.sceneRect()
        if rect.isEmpty():
            return
        view = self.viewport().rect()
        self._zoom = min(view.width() / rect.width(), view.height() / rect.height()) * 0.96
        self._apply()

    def set_zoom(self, zoom: float) -> None:
        self._fit = False
        self._zoom = max(ZOOM_STEPS[0], min(ZOOM_STEPS[-1], zoom))
        self._apply()

    def step(self, direction: int) -> None:
        if direction > 0:
            target = next((z for z in ZOOM_STEPS if z > self._zoom * 1.01), ZOOM_STEPS[-1])
        else:
            target = next((z for z in reversed(ZOOM_STEPS) if z < self._zoom * 0.99), ZOOM_STEPS[0])
        self.set_zoom(target)

    def _apply(self) -> None:
        self.resetTransform()
        self.scale(self._zoom, self._zoom)
        self.zoom_changed.emit(self._zoom)

    def wheelEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self.step(1 if event.angleDelta().y() > 0 else -1)

    def resizeEvent(self, event) -> None:  # noqa: N802 - override de Qt
        super().resizeEvent(event)
        if self._fit:
            self.fit()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - override de Qt
        super().mouseMoveEvent(event)
        point: QPointF = self.mapToScene(event.position().toPoint())
        rect = self._scene.sceneRect()
        inside = rect.contains(point)
        self.hovered.emit((int(point.x()), int(point.y())) if inside else None)


def _section(title: str, parent: QWidget) -> QLabel:
    label = QLabel(title.upper(), parent)
    label.setStyleSheet(
        f"color: {TEXT_MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px;"
        " background: transparent; padding-top: 6px;"
    )
    return label


def _value(text: str, parent: QWidget, color: str = TEXT_PRIMARY) -> QLabel:
    label = QLabel(text, parent)
    label.setStyleSheet(f"color: {color}; background: transparent;")
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class FaceForensicsDialog(QDialog):
    def __init__(self, record: FaceRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Análisis forense de rostro")
        self.resize(1240, 780)
        self.setStyleSheet(f"QDialog {{ background: {BG}; }} QLabel {{ background: transparent; }}")
        self._record = record
        self._camera = face_registry.camera_name(record.device_id)
        self._view_mode = "face"
        self._hover: tuple[int, int] | None = None

        # --- encabezado ------------------------------------------------------
        self.title_label = QLabel(self)
        self.title_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 20px; font-weight: 700;")
        self.subtitle_label = QLabel(self)
        self.subtitle_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        header = QVBoxLayout()
        header.setSpacing(2)
        header.addWidget(self.title_label)
        header.addWidget(self.subtitle_label)

        # --- barra del visor -----------------------------------------------------
        self.view_selector = SegmentedWidget(self)
        self.view_selector.addItem("face", "Rostro", lambda: self._set_view("face"))
        self.view_selector.addItem("scene", "Escena", lambda: self._set_view("scene"))
        self.view_selector.setCurrentItem("face")

        self.zoom_out = ToolButton(FIF.ZOOM_OUT, self)
        self.zoom_in = ToolButton(FIF.ZOOM_IN, self)
        self.fit_button = PushButton("Ajustar", self)
        self.one_to_one = PushButton("1:1", self)
        self.zoom_label = QLabel("100%", self)
        self.zoom_label.setFixedWidth(52)
        self.zoom_label.setStyleSheet(f"color: {TEXT_SECONDARY};")

        self.pixel_check = CheckBox("Píxeles reales", self)
        self.pixel_check.setToolTip(
            "Sin suavizado al ampliar: se ve exactamente lo que captó la cámara."
        )
        self.contrast_check = CheckBox("Contraste", self)
        self.sharpen_check = CheckBox("Nitidez", self)
        self.gray_check = CheckBox("Grises", self)
        self._enhance_group = QButtonGroup(self)
        self._enhance_group.setExclusive(False)
        for check in (self.contrast_check, self.sharpen_check, self.gray_check):
            self._enhance_group.addButton(check)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.view_selector)
        toolbar.addStretch(1)
        for widget in (
            self.zoom_out,
            self.zoom_label,
            self.zoom_in,
            self.fit_button,
            self.one_to_one,
        ):
            toolbar.addWidget(widget)

        # Realces en su propia fila, junto al aviso de que no tocan la evidencia.
        enhance_row = QHBoxLayout()
        enhance_title = QLabel("Realce de visualización:", self)
        enhance_title.setStyleSheet(f"color: {TEXT_SECONDARY};")
        enhance_row.addWidget(enhance_title)
        for widget in (self.pixel_check, self.contrast_check, self.sharpen_check, self.gray_check):
            enhance_row.addWidget(widget)
        enhance_row.addStretch(1)

        self.viewer = ZoomView(self)
        self.viewer.setMinimumSize(560, 420)
        self.readout = QLabel("", self)
        self.readout.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        self.notice = QLabel(
            "Los realces son solo de visualización: la evidencia exportada es el original sin modificar.",
            self,
        )
        self.notice.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        footer_row = QHBoxLayout()
        footer_row.addWidget(self.readout)
        footer_row.addStretch(1)
        footer_row.addWidget(self.notice)

        viewer_column = QVBoxLayout()
        viewer_column.addLayout(toolbar)
        viewer_column.addWidget(self.viewer, stretch=1)
        viewer_column.addLayout(enhance_row)
        viewer_column.addLayout(footer_row)

        # --- ficha ------------------------------------------------------------------
        self.sheet = QWidget(self)
        self.sheet.setFixedWidth(330)
        self.sheet.setObjectName("forensicSheet")
        self.sheet.setStyleSheet(
            f"#forensicSheet {{ background: {SURFACE}; border: 1px solid {GRID_LINE};"
            " border-radius: 12px; }"
        )
        self.sheet_layout = QVBoxLayout(self.sheet)
        self.sheet_layout.setContentsMargins(16, 12, 16, 14)
        self.sheet_layout.setSpacing(4)
        self._build_sheet()

        # --- tomas de la persona ------------------------------------------------------
        self.history_title = _section("Tomas de esta captura", self)
        self.history = QListWidget(self)
        self.history.setViewMode(QListWidget.ViewMode.IconMode)
        self.history.setFlow(QListWidget.Flow.LeftToRight)
        self.history.setWrapping(False)
        self.history.setMovement(QListWidget.Movement.Static)
        self.history.setIconSize(STRIP_THUMB)
        self.history.setGridSize(QSize(STRIP_THUMB.width() + 16, STRIP_THUMB.height() + 26))
        self.history.setFixedHeight(STRIP_THUMB.height() + 40)
        self.history.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; color: {TEXT_SECONDARY};"
            " font-size: 11px; outline: none; }"
            "QListWidget::item { border-radius: 8px; padding: 2px; }"
            "QListWidget::item:selected { background: rgba(34, 211, 238, 0.18);"
            f" color: {TEXT_PRIMARY}; }}"
        )
        self.history.itemClicked.connect(self._on_history_clicked)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addLayout(viewer_column, stretch=1)
        body.addWidget(self.sheet)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addLayout(body, stretch=1)
        layout.addWidget(self.history_title)
        layout.addWidget(self.history)

        # --- conexiones --------------------------------------------------------------
        self.zoom_in.clicked.connect(lambda: self.viewer.step(1))
        self.zoom_out.clicked.connect(lambda: self.viewer.step(-1))
        self.fit_button.clicked.connect(self.viewer.fit)
        self.one_to_one.clicked.connect(lambda: self.viewer.set_zoom(1.0))
        self.viewer.zoom_changed.connect(lambda z: self.zoom_label.setText(f"{z:.0%}"))
        self.viewer.hovered.connect(self._on_hover)
        self.pixel_check.stateChanged.connect(
            lambda _s: self.viewer.set_pixelated(self.pixel_check.isChecked())
        )
        for check in (self.contrast_check, self.sharpen_check, self.gray_check):
            check.stateChanged.connect(lambda _s: self._render_image())

        self._load_history()
        self._show_record(record)

    # --- ficha ---------------------------------------------------------------------

    def _build_sheet(self) -> None:
        parent = self.sheet
        add = self.sheet_layout.addWidget
        self.sheet_image = QLabel(parent)
        self.sheet_image.setFixedSize(96, 96)
        top = QHBoxLayout()
        top.addWidget(self.sheet_image)
        head_column = QVBoxLayout()
        self.time_label = _value("", parent)
        self.time_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 22px; font-weight: 700;")
        self.camera_label = _value("", parent, TEXT_SECONDARY)
        self.take_label = _value("", parent, TEXT_MUTED)
        head_column.addWidget(self.time_label)
        head_column.addWidget(self.camera_label)
        head_column.addWidget(self.take_label)
        head_column.addStretch(1)
        top.addLayout(head_column, stretch=1)
        self.sheet_layout.addLayout(top)

        add(_section("Registro", parent))
        self.meta_grid = QGridLayout()
        self.meta_grid.setHorizontalSpacing(10)
        self.meta_grid.setVerticalSpacing(3)
        self.meta_values: dict[str, QLabel] = {}
        for row, key in enumerate(("Cámara", "Fecha", "Hora", "Detección")):
            self.meta_grid.addWidget(_value(key, parent, TEXT_MUTED), row, 0)
            self.meta_values[key] = _value("", parent)
            self.meta_grid.addWidget(self.meta_values[key], row, 1)
        self.sheet_layout.addLayout(self.meta_grid)

        add(_section("Calidad de la toma", parent))
        self.quality_total = _value("", parent)
        self.quality_total.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 600;")
        add(self.quality_total)
        self.quality_bars: dict[str, tuple[QLabel, LevelBar]] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)
        for row, (key, label) in enumerate(
            (
                ("frontal", "Frontal"),
                ("nitidez", "Nitidez"),
                ("tamaño", "Tamaño"),
                ("confianza", "Confianza"),
            )
        ):
            name = _value(label, parent, TEXT_SECONDARY)
            name.setFixedWidth(70)
            bar = LevelBar(parent)
            value = _value("", parent, TEXT_SECONDARY)
            value.setFixedWidth(38)
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            grid.addWidget(name, row, 0)
            grid.addWidget(bar, row, 1)
            grid.addWidget(value, row, 2)
            self.quality_bars[key] = (value, bar)
        self.sheet_layout.addLayout(grid)
        self.eye_label = _value("", parent)
        add(self.eye_label)

        add(_section("Imagen", parent))
        self.image_grid = QGridLayout()
        self.image_grid.setHorizontalSpacing(10)
        self.image_grid.setVerticalSpacing(3)
        self.image_values: dict[str, QLabel] = {}
        for row, key in enumerate(("Recorte", "Posición", "Cámara", "SHA-256")):
            self.image_grid.addWidget(_value(key, parent, TEXT_MUTED), row, 0)
            self.image_values[key] = _value("", parent)
            self.image_grid.addWidget(self.image_values[key], row, 1)
        self.image_values["SHA-256"].setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-family: monospace; font-size: 10px;"
        )
        self.sheet_layout.addLayout(self.image_grid)
        self.sheet_layout.addStretch(1)

        buttons = QHBoxLayout()
        self.copy_button = PushButton(FIF.COPY, "Copiar ficha", parent)
        self.export_button = PrimaryPushButton(FIF.SAVE, "Exportar", parent)
        self.export_button.setToolTip(
            "Guarda el rostro original (PNG sin pérdida), la escena, la escena marcada "
            "y una ficha JSON con la huella SHA-256 de cada archivo."
        )
        self.copy_button.clicked.connect(self._copy_sheet)
        self.export_button.clicked.connect(self._export)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.export_button)
        self.sheet_layout.addLayout(buttons)

    def _fill_sheet(self, record: FaceRecord) -> None:
        sheet = evidence_sheet(record, self._camera)
        self.sheet_image.setPixmap(record_pixmap(record, QSize(96, 96), 12))
        self.time_label.setText(sheet["hora"])
        self.camera_label.setText(self._camera)
        best = max(self._takes, key=lambda take: take.quality or 0.0) if self._takes else record
        self.take_label.setText(
            "Mejor toma de esta captura" if record is best else "Toma previa (no es la mejor)"
        )
        self.meta_values["Cámara"].setText(self._camera)
        self.meta_values["Fecha"].setText(sheet["fecha"])
        self.meta_values["Hora"].setText(sheet["hora"])
        self.meta_values["Detección"].setText(f"{record.confidence:.0%} de confianza")

        self.quality_total.setText(quality_label(record.quality))
        for key, (value_label, bar) in self.quality_bars.items():
            value = record.details.get(key)
            if value is None:
                value_label.setText("—")
                bar.set_level(0, STATUS_OK)
                continue
            value_label.setText(f"{value:.0%}")
            bar.set_level(value, STATUS_OK if value >= 0.5 else STATUS_WARNING)

        eyes = sheet["distancia_ojos_px"]
        if eyes:
            warning = eyes < MIN_FORENSIC_EYE_PX
            self.eye_label.setText(
                f"Distancia entre ojos: {eyes:.1f} px"
                + (
                    f" — por debajo de {MIN_FORENSIC_EYE_PX} px, poco detalle para identificar"
                    if warning
                    else ""
                )
            )
            self.eye_label.setStyleSheet(
                f"color: {STATUS_WARNING if warning else TEXT_SECONDARY}; background: transparent;"
            )
        else:
            self.eye_label.setText("")

        crop = sheet["resolucion_recorte"]
        box = sheet["caja_en_cuadro"]
        frame = sheet["resolucion_cuadro"]
        self.image_values["Recorte"].setText(f"{crop['w']} × {crop['h']} px (nativo)")
        self.image_values["Posición"].setText(
            f"x {box['x']}, y {box['y']} · {box['w']} × {box['h']} px"
        )
        self.image_values["Cámara"].setText(
            f"{frame['w']} × {frame['h']} px" if frame["w"] else "—"
        )
        digest, _ = sha256_png(record.evidence)
        self._digest = digest
        self.image_values["SHA-256"].setText(f"{digest[:32]}\n{digest[32:]}")
        self.image_values["SHA-256"].setToolTip(digest)

    # --- visor ----------------------------------------------------------------------

    def _show_record(self, record: FaceRecord) -> None:
        self._record = record
        moment = dt.datetime.fromtimestamp(record.timestamp)
        self.title_label.setText(f"Captura de rostro · {self._camera}")
        self.subtitle_label.setText(
            moment.strftime("%d/%m/%Y  %H:%M:%S") + f"  ·  {quality_label(record.quality)}"
        )
        has_scene = bool(record.context_jpeg)
        self.view_selector.setEnabled(has_scene)
        if not has_scene and self._view_mode == "scene":
            self._view_mode = "face"
            self.view_selector.setCurrentItem("face")
        self._fill_sheet(record)
        self.viewer.fit()
        self._render_image()
        for index in range(self.history.count()):
            item = self.history.item(index)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) is record)

    def _set_view(self, mode: str) -> None:
        self._view_mode = mode
        self.viewer.fit()
        self._render_image()

    def _render_image(self) -> None:
        record = self._record
        box = None
        if self._view_mode == "scene":
            image = decode_context(record)
            if image is None:
                image = record.evidence
            else:
                box = context_box(record)
        else:
            image = record.evidence
        shown = enhance(
            image,
            contrast=self.contrast_check.isChecked(),
            sharpen=self.sharpen_check.isChecked(),
            gray=self.gray_check.isChecked(),
        )
        self._shown = shown
        self.viewer.set_image(_bgr_qimage(shown), box)
        self._update_readout()

    def _on_hover(self, point) -> None:
        self._hover = point
        self._update_readout()

    def _update_readout(self) -> None:
        image = getattr(self, "_shown", None)
        if image is None:
            return
        height, width = image.shape[:2]
        base = f"{width} × {height} px"
        if self._hover is not None:
            x, y = self._hover
            if 0 <= x < width and 0 <= y < height:
                b, g, r = (int(v) for v in image[y, x])
                base += f"  ·  x {x}, y {y}  ·  RGB {r}, {g}, {b}"
        self.readout.setText(base)

    # --- historial ---------------------------------------------------------------------

    def _load_history(self) -> None:
        records = face_registry.history(self._record)
        if not any(r is self._record for r in records):
            records.insert(0, self._record)
        self._takes = records
        self.history.clear()
        for record in records:
            item = QListWidgetItem(record_pixmap(record, STRIP_THUMB, 8), record.when)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            item.setData(Qt.ItemDataRole.UserRole, record)
            item.setToolTip(f"{record.when} · {quality_label(record.quality)}")
            self.history.addItem(item)
        self.history_title.setText(f"TOMAS DE ESTA CAPTURA ({len(records)})")

    def _on_history_clicked(self, item: QListWidgetItem) -> None:
        record = item.data(Qt.ItemDataRole.UserRole)
        if record is not None and record is not self._record:
            self._show_record(record)

    # --- acciones ------------------------------------------------------------------------

    def _copy_sheet(self) -> None:
        record = self._record
        sheet = evidence_sheet(record, self._camera)
        sheet["sha256_rostro_original_png"] = self._digest
        QGuiApplication.clipboard().setText(json.dumps(sheet, ensure_ascii=False, indent=2))
        notify(self, "Ficha copiada", "Los metadatos de la toma quedaron en el portapapeles.")

    def _export(self) -> None:
        target = QFileDialog.getExistingDirectory(self, "Elegí dónde exportar la evidencia")
        if not target:
            return
        record = self._record
        try:
            folder = export_evidence(record, Path(target), self._camera)
        except OSError as exc:
            warn(self, "Exportar evidencia", f"No se pudo escribir la evidencia: {exc}")
            return
        notify(self, "Evidencia exportada", str(folder))


def open_forensics(record: FaceRecord, parent: QWidget | None = None) -> None:
    dialog = FaceForensicsDialog(record, parent.window() if parent is not None else None)
    dialog.exec()
