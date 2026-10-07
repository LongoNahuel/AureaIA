"""Tile de la grilla de Vista en Vivo: renderiza el ultimo frame de su
StreamWorker, con overlay OSD (nombre de camara + hora) y las detecciones
del analizador activo. La camara se asigna arrastrando un item desde
DeviceTreeWidget o con doble click (al tile seleccionado); un click en el
tile lo marca como "seleccionado" (borde de acento) para ese flujo."""

from __future__ import annotations

import datetime as dt
import itertools
import math
import time
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import Action, FluentIcon, RoundMenu
from shiboken6 import isValid

from aurea_vms.config.settings import settings
from aurea_vms.core import app_prefs
from aurea_vms.core.analytics.registry import ANALYZER_DISPLAY_NAMES
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import Detection, DetectionEvent
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models import repository
from aurea_vms.models.device import Device
from aurea_vms.ui import icons, render_pool
from aurea_vms.ui.labels import display_class, display_rotor_event
from aurea_vms.ui.theme import STATUS_COLORS
from aurea_vms.ui.widgets.analytics_visuals import (
    STALE_AFTER_S,
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_WARNING,
    draw_heatmap,
)
from aurea_vms.ui.widgets.device_tree import DEVICE_ID_MIME

BORDER_IDLE = "#2a3441"
BORDER_SELECTED = "#3b82f6"

# Marca inteligente: verde con tratamientos distintos según el resultado.
MOTION_STROKE = QColor("#22c55e")
MOTION_FILL = QColor(34, 197, 94, 55)
DETECTION_STROKE = QColor("#22c55e")
DETECTION_FILL = QColor(34, 197, 94, 40)
LABEL_CHIP_BG = QColor(12, 20, 15, 220)
LABEL_CHIP_TEXT = QColor("#eafff2")
ANALYTIC_COLORS = {
    "monitor_tamper": QColor("#f59e0b"),
    "people_counting": QColor("#22c55e"),
    "line_crossing": QColor("#38bdf8"),
    "face_detection": QColor("#c084fc"),
    "motion_detection": QColor("#22c55e"),
}

# Nombres cortos para los chips de estado sobre el video.
SHORT_NAMES = {
    "monitor_tamper": "Incidentes",
    "people_counting": "Personas",
    "line_crossing": "Cruce",
    "face_detection": "Rostros",
}
# Cada cuanto se relee la preferencia de marca (ver _branding_enabled).
BRANDING_REFRESH_S = 2.0
# Motivo de un incidente, para el rotulo de la zona y el cartel.
MOTIVE_TEXT = {
    "patada": "patada",
    "golpe_mano": "mano",
    "consumo": "consumo",
    "preparacion": "preparación",
    "no_va_mas": "fichas tras el no va más",
}
# Colores de una zona de Detección de incidentes segun su estado.
INCIDENT_STATE_COLORS = {"alerta": STATUS_CRITICAL, "previa": STATUS_WARNING}
# Ruleta: manos en una zona de fichas, y cuanto se resalta que se movieron.
ROULETTE_ACTIVITY = "#38bdf8"
CHIP_FLASH_S = 3.0
# Manos en una mesa de juego, por rol. El jugador va en el celeste de "manos
# en la zona"; el crupier en violeta, que no se confunde con los estados de
# las zonas (verde, celeste, ambar, rojo); sin rol todavia, neutro.
HAND_ROLE_COLORS = {"crupier": "#c084fc", "jugador": ROULETTE_ACTIVITY, "persona": "#e5e7eb"}
HAND_ROLE_TEXT = {"crupier": "Crupier", "jugador": "Jugador"}
# El recuadro de la mano lo calcula la analitica (suavizado entre cuadros);
# sin el (eventos viejos), un cuadrado de HAND_BOX_PX alrededor de la palma.
HAND_BOX_PX = 24.0
HAND_BOX_MIN_PX = 12.0
# Sincronia marcas-video: el tile muestra el cuadro sobre el que se calcularon
# las ultimas marcas (DetectionEvent.frame_ts) si es el ultimo que llego o el
# anterior; si las marcas vienen mas atrasadas (la analitica corre a menos fps
# que el stream, o se freno), manda el video. Nunca se muestra un cuadro mas
# viejo que uno ya mostrado.
SYNC_FRAMES = 2
# Ronda de la ruleta: rotulo y color junto a la rueda.
ROUND_STYLE = {
    "apuestas": ("Apuestas abiertas", STATUS_OK),
    "bola": ("Bola en juego", ROULETTE_ACTIVITY),
    "no_va_mas": ("NO VA MÁS", STATUS_WARNING),
    "resultado": ("Resultado · esperando la marca", STATUS_WARNING),
}
# Jugada nueva abierta por la rueda (el crupier freno o re-impulso el plato):
# se avisa junto a la rueda durante NEW_GAME_FLASH_S.
NEW_GAME_FLASH_S = 4.0
# Cuanto dura el resaltado de la linea tras un cruce.
LINE_FLASH_S = 1.5


def _fit(frame: np.ndarray, size: QSize) -> np.ndarray:
    """Cuadro BGR achicado (o agrandado) a `size` con OpenCV, ANTES de pasar a
    Qt. Antes QPixmap.fromImage(...).scaled() convertia el cuadro completo
    con el GIL tomado: medido (2026-09-23) 1,4 ms por cuadro de 2048x1536
    contra 0,04 ms ahora, porque cv2.resize/cvtColor sueltan el GIL. El
    costo total queda parecido (~2-4 ms); lo que se gana es menos GIL
    compartido con los hilos de video y analiticas.

    Al achicar: INTER_LINEAR, y solo por debajo de 1/4 (grillas grandes)
    primero pyrDown, que no hace serrucho. Medido (2026-09-30) sobre la RA-04
    (2048x1536): INTER_AREA tardaba 14-20 ms por cuadro, en el hilo de la
    interfaz, a cualquier tamaño (a 25 fps, el 39% del hilo; a 60 no entraba);
    INTER_LINEAR tarda 0,5-2,2 ms y hasta 1/3 difiere de INTER_AREA en menos
    de 2 niveles de gris en promedio."""
    width, height = size.width(), size.height()
    if (width, height) != (frame.shape[1], frame.shape[0]):
        while frame.shape[1] >= 4 * width and frame.shape[0] >= 4 * height:
            frame = cv2.pyrDown(frame)
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_LINEAR)
    return frame


def frame_to_image(frame: np.ndarray, size: QSize) -> QImage:
    """Como frame_to_pixmap pero devuelve un QImage propio (con copia de los
    datos): sirve en cualquier hilo, y es lo que arma el pool de render.

    Formato RGB32 (BGRA en memoria, el orden de OpenCV): es el que QPainter
    dibuja mas rapido y el que QPixmap.fromImage pasa sin convertir."""
    frame = _fit(frame, size)
    bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
    height, width = bgra.shape[:2]
    return QImage(bgra.data, width, height, 4 * width, QImage.Format.Format_RGB32).copy()


def frame_to_pixmap(frame: np.ndarray, size: QSize) -> QPixmap:
    """El cuadro como pixmap. Solo en el hilo de la GUI (QPixmap no se
    puede crear en otro)."""
    return QPixmap.fromImage(frame_to_image(frame, size))


_CLAVES_RENDER = itertools.count()


@dataclass(frozen=True)
class _Escena:
    """Lo que el overlay lee del recuadro, tomado en la GUI al pedir el
    cuadro: el render corre en otro hilo mientras la GUI sigue cambiando el
    recuadro."""

    eventos: dict[str, DetectionEvent]
    nombre: str
    marcas: bool
    marca: bool
    nombre_marca: str


class _VideoDisplay(QLabel):
    """QLabel cuyo sizeHint NO depende del pixmap actual.

    Por default, QLabel.sizeHint()/minimumSizeHint() devuelven el tamano
    del pixmap cargado. Como el pixmap se reemplaza en cada frame (a veces
    desde resizeEvent, para el estado vacio), eso arma un loop de
    realimentacion: resize -> nuevo pixmap -> nuevo sizeHint -> el layout
    vuelve a resizear el label -> ... y la ventana "crece sola" de a poco.
    Fijar un sizeHint constante corta el loop; el tamano real lo sigue
    determinando el layout (grilla/QSizePolicy.Expanding), no el video.

    Ademas pinta el video el mismo (Fase V1b, `set_cuadro`): fondo, cuadro
    centrado y borde, opaco. Medido 2026-10-07 con 16 recuadros: el render
    ya salia del hilo de la GUI y aun asi un clic tardaba 20-30 ms; lo que
    quedaba era pintar cada cuadro como QLabel con stylesheet (QStyleSheetStyle
    y el repintado de los fondos de atras). Los estados vacio y "sin señal"
    siguen por setPixmap y el pintado normal del QLabel.
    """

    FONDO = QColor("#10151c")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cuadro: QImage | None = None
        self._borde = QPen(QColor(BORDER_IDLE))

    def sizeHint(self) -> QSize:  # noqa: N802 - override de Qt
        return QSize(160, 90)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - override de Qt
        return QSize(160, 90)

    def set_borde(self, color: str, ancho: int) -> None:
        self._borde = QPen(QColor(color))
        self._borde.setWidth(ancho)
        self.setStyleSheet(
            f"background-color: {self.FONDO.name()}; border: {ancho}px solid {color};"
        )
        self.update()

    def set_cuadro(self, imagen: QImage) -> None:
        if self._cuadro is None:
            super().setPixmap(QPixmap())  # suelta el pixmap del estado anterior
            # Opaco: Qt no repinta los fondos de atras en cada cuadro.
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._cuadro = imagen
        self.update()

    def setPixmap(self, pixmap: QPixmap) -> None:  # noqa: N802 - override de Qt
        if self._cuadro is not None:
            self._cuadro = None
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        super().setPixmap(pixmap)

    def pixmap(self) -> QPixmap:
        """El cuadro que se esta mostrando, venga del video o de un estado."""
        if self._cuadro is not None:
            return QPixmap.fromImage(self._cuadro)
        return super().pixmap()

    def paintEvent(self, event) -> None:  # noqa: N802 - override de Qt
        if self._cuadro is None:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, self.FONDO)
        x = (rect.width() - self._cuadro.width()) // 2
        y = (rect.height() - self._cuadro.height()) // 2
        painter.drawImage(x, y, self._cuadro)
        ancho = self._borde.width()
        if ancho > 0:
            painter.setPen(self._borde)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            mitad = ancho / 2
            painter.drawRect(QRectF(rect).adjusted(mitad, mitad, -mitad, -mitad))
        painter.end()


class VideoTile(QWidget):
    clicked = Signal(object)  # emite self
    doubleClicked = Signal(object)  # emite self
    device_assigned = Signal(object)  # emite device_id o None

    def __init__(self, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index = index
        self._device: Device | None = None
        self._latest_events: dict[str, DetectionEvent] = {}
        self._analytics_configs = []
        self._selected = False
        # True mientras el pixmap actual es el estado "sin señal": evita
        # redibujar las rayas en cada tick del timer de display.
        self._offline_rendered = False
        self._last_rendered_ts = 0.0
        self._last_rendered_size = QSize()
        # Render en el pool (Fase V1b): la clave identifica a este recuadro en
        # la cola, y la generacion invalida los cuadros en vuelo cuando cambia
        # la camara (un cuadro de la anterior no puede pisar al de la nueva).
        self._clave_render = next(_CLAVES_RENDER)
        self._generacion = 0
        # Los ultimos cuadros (captured_at, cuadro) para dibujar las marcas
        # sobre el cuadro en que se calcularon (ver SYNC_*).
        self._recent_frames: deque[tuple[float, np.ndarray]] = deque(maxlen=SYNC_FRAMES)
        self._shown_ts = 0.0
        # Todas las vistas usan exclusivamente el flujo principal. El
        # substream puede seguir configurado en el dispositivo para pruebas,
        # pero nunca se abre desde la interfaz.
        self._stream_kind = "main"
        self._intelligent_mode = False
        self._line_flash_until = 0.0
        # Marcas inteligentes (zonas, lineas, cajas, estado, incidentes)
        # sobre el video, en Vista en Vivo y en Vista Inteligente.
        self._smart_marks = True
        # La preferencia de marca se cachea: leerla en cada cuadro abria
        # preferences.json dos veces por cuadro y por recuadro.
        self._branding = app_prefs.intelligent_branding_enabled()
        self._brand_name = app_prefs.get_brand_name()
        self._branding_checked_at = time.monotonic()

        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        self.video_label = _VideoDisplay(self)
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(160, 90)
        self.video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.addWidget(self.video_label)

        self._timer = QTimer(self)
        self._timer.setInterval(max(1, int(1000 / settings.display_fps)))
        self._timer.timeout.connect(self._refresh_frame)
        self._timer.start()

        event_bus.detection.connect(self._on_detection, Qt.ConnectionType.QueuedConnection)

        self._apply_border()
        self._render_empty_state()

    # --- asignacion de camara -------------------------------------------------

    def assign_device(self, device_id: int | None) -> None:
        self.release()
        self._latest_events = {}
        self._analytics_configs = []
        self._last_rendered_ts = 0.0
        self._last_rendered_size = QSize()

        device = repository.get_device(device_id) if device_id is not None else None
        if device is None:
            self._render_empty_state()
            self.device_assigned.emit(None)
            return

        self._device = device
        self.refresh_analytics_configs()
        stream_manager.acquire(device, self._stream_kind)
        self.device_assigned.emit(device.id)

    def release(self) -> None:
        self._generacion += 1
        render_pool.pool().descartar(self._clave_render)
        if self._device is not None:
            stream_manager.release(self._device.id, self._stream_kind)
            self._device = None

    def has_device(self) -> bool:
        return self._device is not None

    @property
    def device_id(self) -> int | None:
        return self._device.id if self._device is not None else None

    def set_stream_kind(self, kind: str) -> None:
        """Mantiene la vista en el flujo principal."""
        kind = "main"
        if kind == self._stream_kind:
            return
        if self._device is not None:
            stream_manager.release(self._device.id, self._stream_kind)
            self._stream_kind = kind
            stream_manager.acquire(self._device, self._stream_kind)
        else:
            self._stream_kind = kind

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_border()

    def set_smart_marks(self, enabled: bool) -> None:
        self._smart_marks = enabled
        self._invalidate_render()
        self.update()

    def _branding_enabled(self) -> bool:
        now = time.monotonic()
        if now - self._branding_checked_at > BRANDING_REFRESH_S:
            self._branding = app_prefs.intelligent_branding_enabled()
            self._brand_name = app_prefs.get_brand_name()
            self._branding_checked_at = now
        return self._branding

    def set_intelligent_mode(self, enabled: bool) -> None:
        self._intelligent_mode = enabled
        if enabled:
            # Las coordenadas de ROI/linea y las detecciones de las
            # analiticas pertenecen siempre al main-stream.
            self.set_stream_kind("main")
            self.refresh_analytics_configs()
        self.update()

    def refresh_analytics_configs(self) -> None:
        if self._device is None:
            self._analytics_configs = []
            return
        self._analytics_configs = [
            config
            for config in repository.list_analytics_configs(self._device.id)
            if config.enabled
        ]
        self._invalidate_render()
        self.update()

    def _apply_border(self) -> None:
        color = BORDER_SELECTED if self._selected else BORDER_IDLE
        width = 2 if self._selected else 1
        self.video_label.set_borde(color, width)

    # --- interaccion -------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.clicked.emit(self)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.doubleClicked.emit(self)
        super().mouseDoubleClickEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasFormat(DEVICE_ID_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        data = bytes(event.mimeData().data(DEVICE_ID_MIME))
        try:
            device_id = int(data.decode())
        except ValueError:
            return
        self.clicked.emit(self)
        self.assign_device(device_id)
        event.acceptProposedAction()

    def resizeEvent(self, event) -> None:  # noqa: N802 - override de Qt
        if self._device is None:
            self._render_empty_state()
        self._offline_rendered = False  # el proximo tick redibuja al tamaño nuevo
        self._last_rendered_size = QSize()
        super().resizeEvent(event)

    def _show_context_menu(self, pos) -> None:
        if self._device is None:
            return
        menu = RoundMenu(parent=self)
        action = Action(FluentIcon.CLOSE, "Quitar cámara")
        action.triggered.connect(lambda: self.assign_device(None))
        menu.addAction(action)
        menu.exec(self.mapToGlobal(pos))

    # --- render -------------------------------------------------

    def _on_detection(self, event: DetectionEvent) -> None:
        if self._device is not None and event.device_id == self._device.id:
            self._latest_events[event.analyzer_name] = event
            self._invalidate_render()
            if event.frame_ts > 0 and self._smart_marks:
                # El cuadro de estas marcas, ya: sin esperar al timer.
                self._refresh_frame()
            else:
                self.update()

    def _invalidate_render(self) -> None:
        self._last_rendered_ts = 0.0
        self._last_rendered_size = QSize()

    def _render_empty_state(self) -> None:
        size = self.video_label.size()
        if size.width() < 10 or size.height() < 10:
            return

        pixmap = QPixmap(size)
        pixmap.fill(QColor("#10151c"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        logo_size = max(32, min(72, min(size.width(), size.height()) // 3))
        icon_pixmap = icons.app_logo_pixmap(logo_size)
        icon_x = (size.width() - icon_pixmap.width()) // 2
        icon_y = (size.height() - icon_pixmap.height()) // 2 - 10
        painter.setOpacity(0.85)
        painter.drawPixmap(icon_x, icon_y, icon_pixmap)
        painter.setOpacity(1.0)

        painter.setPen(QColor("#565f6c"))
        text_rect = QRectF(0, icon_y + icon_pixmap.height() + 8, size.width(), 20)
        label = f"Ventana {self._index + 1}" if self._index >= 0 else "Sin cámara"
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)

        painter.end()
        self.video_label.setPixmap(pixmap)

    def _render_offline_state(self) -> None:
        """Camara asignada sin señal: patron rayado diagonal (spec NOVA)
        para distinguir "stream caido" de "video oscuro" de un vistazo."""
        size = self.video_label.size()
        if size.width() < 10 or size.height() < 10:
            return

        pixmap = QPixmap(size)
        pixmap.fill(QColor("#10151c"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen = QPen(QColor(110, 118, 129, 22))
        pen.setWidth(5)
        painter.setPen(pen)
        step = 18
        for x in range(-size.height(), size.width(), step):
            painter.drawLine(x, size.height(), x + size.height(), 0)

        center_y = size.height() // 2
        painter.setPen(QColor(STATUS_COLORS["offline"]))
        painter.drawText(
            QRectF(0, center_y - 22, size.width(), 20), Qt.AlignmentFlag.AlignCenter, "Sin señal"
        )
        painter.setPen(QColor("#9aa3af"))
        name = self._device.name if self._device is not None else ""
        painter.drawText(
            QRectF(0, center_y + 2, size.width(), 20),
            Qt.AlignmentFlag.AlignCenter,
            f"{name} — reconectando…",
        )
        painter.end()
        self.video_label.setPixmap(pixmap)

    def _en_pantalla(self) -> bool:
        """Si alguien puede ver el recuadro: visible (su pestaña es la actual
        y su ventana esta abierta) y la ventana no esta minimizada. Una
        ventana tapada por otra no se detecta: Qt no lo sabe de forma
        portable."""
        return self.isVisible() and not self.window().isMinimized()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        super().showEvent(event)
        # Volver a una pestaña: dibuja el cuadro actual sin esperar al tick.
        self._invalidate_render()
        self._offline_rendered = False
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - override de Qt
        # Pestaña oculta, ventana cerrada o minimizada: el timer se para.
        # Antes cada recuadro oculto seguia achicando, convirtiendo y
        # pintando a 25-60 fps (medido 2026-10-07: 16 recuadros ocultos
        # ocupaban el 99 % del hilo de la GUI). El stream NO se suelta: al
        # volver, el video sigue al instante.
        self._timer.stop()
        super().hideEvent(event)

    def _refresh_frame(self) -> None:
        if self._device is None:
            return
        if not self._en_pantalla():
            # Red de seguridad: un hide que no llego (minimizar no siempre
            # lo manda a los hijos) o una deteccion que pide redibujar.
            return

        worker = stream_manager.get_worker(self._device.id, self._stream_kind)
        frame, frame_ts = worker.get_latest_frame_with_timestamp() if worker else (None, 0.0)
        if frame is None or worker.is_stale():
            # Antes quedaba el ultimo frame congelado, que parece en vivo --
            # exactamente lo que is_stale() existia para evitar.
            if not self._offline_rendered:
                self._offline_rendered = True
                self._render_offline_state()
            return
        self._offline_rendered = False
        if not self._recent_frames or self._recent_frames[-1][0] != frame_ts:
            self._recent_frames.append((frame_ts, frame))
        frame, frame_ts = self._frame_to_show(frame, frame_ts)
        target_size = self.video_label.size()
        if frame_ts == self._last_rendered_ts and target_size == self._last_rendered_size:
            return

        height, width = frame.shape[:2]
        fitted = QSize(width, height).scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio)
        if fitted.isEmpty():  # tile todavia sin tamaño (antes de mostrarse)
            return
        # Achicar, convertir y dibujar las marcas va al pool de render (Fase
        # V1b): aca solo se toma la foto del estado que el overlay lee. El
        # cuadro es una referencia (los StreamWorker lo reemplazan, no lo
        # pisan) y los eventos, una copia del dict, que la GUI sigue tocando.
        escena = self._escena()
        generacion = self._generacion

        def armar() -> QImage:
            return self._draw_overlay(frame_to_image(frame, fitted), width, height, escena)

        render_pool.pool().enviar(
            self._clave_render, armar, lambda imagen: self._mostrar(imagen, generacion)
        )
        self._last_rendered_ts = frame_ts
        self._last_rendered_size = target_size
        self._shown_ts = max(self._shown_ts, frame_ts)

    def _escena(self) -> _Escena:
        return _Escena(
            eventos=dict(self._latest_events),
            nombre=self._device.name if self._device else "",
            marcas=self._smart_marks,
            marca=self._branding_enabled(),
            nombre_marca=self._brand_name,
        )

    def _mostrar(self, imagen: QImage, generacion: int) -> None:
        """En la GUI, cuando el pool termina un cuadro. Se descarta si el
        recuadro ya no existe, si cambio de camara o si mientras tanto paso a
        "sin señal" (que lo dibuja la GUI)."""
        if not isValid(self) or generacion != self._generacion or self._offline_rendered:
            return
        self.video_label.set_cuadro(imagen)

    def _frame_to_show(self, latest: np.ndarray, latest_ts: float) -> tuple[np.ndarray, float]:
        """El cuadro de las ultimas marcas, si es de los dos ultimos y no es
        mas viejo que lo ya mostrado; si no, el ultimo (ver SYNC_FRAMES)."""
        if not self._smart_marks:
            return latest, latest_ts
        marks_ts = max((event.frame_ts for event in self._latest_events.values()), default=0.0)
        if marks_ts <= 0 or marks_ts < self._shown_ts:
            return latest, latest_ts
        for ts, frame in self._recent_frames:
            if ts == marks_ts:
                return frame, ts
        return latest, latest_ts

    def _draw_overlay(
        self,
        canvas: QPixmap | QImage,
        frame_w: int,
        frame_h: int,
        escena: _Escena | None = None,
    ) -> QPixmap | QImage:
        """Marcas sobre el cuadro. Con un QImage (el pool de render) dibuja
        sobre el mismo y puede correr en cualquier hilo: todo lo que lee del
        recuadro viene en `escena`, salvo las configs de analiticas (una
        lista que la GUI reemplaza entera, nunca muta) y el destello de la
        linea (solo lo toca el render, uno en curso por recuadro). Con un
        QPixmap (tests, GUI) dibuja sobre una copia."""
        if escena is None:
            escena = self._escena()
        result = canvas if isinstance(canvas, QImage) else QPixmap(canvas)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        branding = escena.marca
        if branding:
            self._draw_branding(painter, escena.nombre_marca)
        self._draw_osd_text(
            painter,
            result.width(),
            40 if branding else 8,
            Qt.AlignmentFlag.AlignLeft,
            escena.nombre,
        )
        timestamp = dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self._draw_osd_text(painter, result.width(), 8, Qt.AlignmentFlag.AlignRight, timestamp)
        scale_x = result.width() / frame_w
        scale_y = result.height() / frame_h
        now = time.time()
        # Solo lecturas frescas: si un analizador se detuvo o se colgo, sus
        # ultimas cajas no pueden quedar dibujadas como si fueran en vivo.
        fresh = {
            name: event
            for name, event in escena.eventos.items()
            if now - event.timestamp <= STALE_AFTER_S
        }
        if not escena.marcas:
            painter.end()
            return result

        self._draw_people_heatmap(painter, fresh, result.width(), result.height())
        self._draw_analytics_guides(painter, fresh, scale_x, scale_y, now)
        self._draw_analytics_status(painter, fresh, result.width())

        for analyzer_name, event in fresh.items():
            color = ANALYTIC_COLORS.get(analyzer_name, DETECTION_STROKE)
            for det in event.detections:
                if det.polygon:
                    self._draw_motion_mark(painter, det.polygon, scale_x, scale_y, color)
                else:
                    self._draw_detection_box(painter, det, scale_x, scale_y, color, analyzer_name)

        self._draw_hands(painter, fresh, scale_x, scale_y, result.width())
        self._draw_incident_mark(painter, fresh, result.width(), result.height(), now)
        painter.end()
        return result

    def _draw_hands(
        self,
        painter: QPainter,
        fresh: dict[str, DetectionEvent],
        scale_x: float,
        scale_y: float,
        width: int,
    ) -> None:
        """Un recuadro por mano, del color del rol: trazo fino y relleno tenue;
        mas marcado mientras la mano toca la rueda o una zona de fichas. El
        rol, una sola vez por persona, sobre su mano mas alta."""
        event = fresh.get("monitor_tamper")
        metrics = event.metrics if event is not None else {}
        hands = metrics.get("manos") or []
        if not hands:
            return
        labels: dict = {}
        for index, hand in enumerate(hands):
            role = hand.get("rol")
            color = QColor(HAND_ROLE_COLORS.get(role, HAND_ROLE_COLORS["persona"]))
            box = hand.get("caja")
            if box:
                x, y, w, h = box
                rect = QRectF(x * scale_x, y * scale_y, w * scale_x, h * scale_y)
            else:
                rect = QRectF(0, 0, HAND_BOX_PX, HAND_BOX_PX)
                rect.moveCenter(QPointF(hand["x"] * scale_x, hand["y"] * scale_y))
            if rect.width() < HAND_BOX_MIN_PX or rect.height() < HAND_BOX_MIN_PX:
                center = rect.center()
                rect.setWidth(max(rect.width(), HAND_BOX_MIN_PX))
                rect.setHeight(max(rect.height(), HAND_BOX_MIN_PX))
                rect.moveCenter(center)
            touching = bool(hand.get("en_rueda")) or hand.get("zona") is not None
            fill = QColor(color)
            fill.setAlpha(60 if touching else 22)
            pen = QPen(color)
            pen.setWidthF(3.0 if touching else 1.8)
            painter.setPen(pen)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 4, 4)
            if role in HAND_ROLE_TEXT:
                key = hand.get("persona") if hand.get("persona") is not None else ("mano", index)
                current = labels.get(key)
                if current is None or rect.top() < current[0].y():
                    labels[key] = (QPointF(rect.center().x(), rect.top()), role, color)
        for anchor, role, color in labels.values():
            self._draw_guide_label(
                painter, anchor - QPointF(0, 12), HAND_ROLE_TEXT[role], color, centered=True
            )

    def _draw_incident_mark(
        self,
        painter: QPainter,
        fresh: dict[str, DetectionEvent],
        width: int,
        height: int,
        now: float,
    ) -> None:
        """Marca de incidente sobre todo el video: marco que late y un cartel
        con que paso y donde, mientras dura. Rojo para el incidente, ambar
        para la alerta previa (preparacion de consumo)."""
        event = fresh.get("monitor_tamper")
        status = event.metrics.get("estado") if event is not None else None
        if status not in INCIDENT_STATE_COLORS:
            return
        zones = event.metrics.get("zonas") or []
        noun = {"puesto": "el puesto", "zona": "la zona"}.get(
            event.metrics.get("zona_tipo"), "la pantalla"
        )
        alerts = [
            (index, MOTIVE_TEXT.get(zone.get("motivo") or "", "golpe"))
            for index, zone in enumerate(zones)
            if zone.get("estado") == status
        ]
        if not alerts:
            return
        color = INCIDENT_STATE_COLORS[status]
        # Latido de ~1,2 s: el marco va de 45% a 100% de opacidad.
        pulse = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(now * 2 * math.pi / 1.2))
        frame_color = QColor(color)
        frame_color.setAlphaF(pulse)
        pen = QPen(frame_color)
        pen.setWidthF(6.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(3, 3, width - 6, height - 6))

        detail = " · ".join(f"{motive} en {noun} {index + 1}" for index, motive in alerts)
        title = "INCIDENTE" if status == "alerta" else "ALERTA PREVIA"
        text = f"⚠  {title}  ·  {detail}"
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(max(12, min(18, width // 55)))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        banner_w = min(width - 24.0, metrics.horizontalAdvance(text) + 28.0)
        banner = QRectF((width - banner_w) / 2, 34, banner_w, metrics.height() + 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(banner, 8, 8)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(banner, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_analytics_guides(
        self,
        painter: QPainter,
        fresh: dict[str, DetectionEvent],
        scale_x: float,
        scale_y: float,
        now: float,
    ) -> None:
        for config in self._analytics_configs:
            name = config.analyzer_name
            color = ANALYTIC_COLORS.get(name, QColor("#93c5fd"))
            event = fresh.get(name)
            metrics = event.metrics if event else {}
            rois = self._rois_for_config(config)
            zone_counts = metrics.get("zones") if name == "people_counting" else None
            for index, (x, y, width, height) in enumerate(rois):
                rect = QRectF(x * scale_x, y * scale_y, width * scale_x, height * scale_y)
                zone_color = color
                zone_text = None
                screens = metrics.get("zonas") if name == "monitor_tamper" else None
                zone_alert = False
                if metrics.get("modo") == "ruleta":
                    zone = screens[index] if screens and index < len(screens) else {}
                    armed = bool((metrics.get("ronda") or {}).get("pano_armado"))
                    zone_color, zone_text = self._roulette_zone_style(zone, index, now, armed)
                    zone_alert = zone.get("estado") == "alerta" or armed
                    if zone.get("estado") == "alerta":
                        tint = QColor(zone_color)
                        tint.setAlpha(60)
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(tint)
                        painter.drawRect(rect)
                        self._draw_corner_brackets(painter, rect, zone_color, 3.0)
                    self._draw_chip_spots(painter, zone, scale_x, scale_y, now)
                elif screens and index < len(screens):
                    screen = screens[index]
                    zone_state = screen.get("estado")
                    zone_alert = zone_state in INCIDENT_STATE_COLORS
                    zone_color = QColor(INCIDENT_STATE_COLORS.get(zone_state, STATUS_OK))
                    motive = MOTIVE_TEXT.get(screen.get("motivo") or "", "golpe")
                    noun = "Puesto" if metrics.get("zona_tipo") == "puesto" else "Pantalla"
                    if zone_state == "alerta":
                        zone_text = f"INCIDENTE · {motive}"
                    elif zone_state == "previa":
                        zone_text = f"PREVIA · {motive}"
                    else:
                        zone_text = f"{noun} {index + 1} · OK"
                    tint = QColor(zone_color)
                    tint.setAlpha(70 if zone_alert else 14)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(tint)
                    painter.drawRect(rect)
                    if zone_alert:
                        self._draw_corner_brackets(painter, rect, zone_color, 3.0)
                elif name == "people_counting" and event is not None:
                    count = (
                        zone_counts[index]
                        if zone_counts and index < len(zone_counts)
                        else metrics.get("occupancy", 0)
                    )
                    zone_text = (
                        f"Zona {index + 1} · {count}" if len(rois) > 1 else f"{count} en zona"
                    )
                    if metrics.get("alerta_maxima"):
                        zone_color = QColor(STATUS_CRITICAL)
                pen = QPen(zone_color)
                pen.setWidthF(3.0 if zone_alert else 1.6)
                pen.setStyle(Qt.PenStyle.SolidLine if zone_alert else Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect)
                if zone_text:
                    self._draw_guide_label(painter, rect.topLeft(), zone_text, zone_color)

            if metrics.get("modo") == "ruleta" and metrics.get("rueda"):
                self._draw_roulette_wheel(painter, metrics, scale_x, scale_y)

            line = (config.params or {}).get("line")
            if name == "line_crossing" and line and len(line) == 2:
                if event is not None and event.triggers:
                    self._line_flash_until = now + LINE_FLASH_S
                self._draw_crossing_line(
                    painter, config, line, metrics, scale_x, scale_y, now < self._line_flash_until
                )

    @staticmethod
    def _roulette_zone_style(
        zone: dict, index: int, now: float, armed: bool = False
    ) -> tuple[QColor, str]:
        """Zona de fichas: roja con fichas movidas tras el no va mas, ambar
        unos segundos despues de un movimiento normal, celeste con manos
        adentro, y verde quieta (ambar y trazo lleno si el paño esta armado)."""
        if zone.get("estado") == "alerta":
            return QColor(STATUS_CRITICAL), f"Zona {index + 1} · FICHAS TRAS NO VA MÁS"
        last_change = zone.get("ultimo_cambio")
        if last_change is not None and now - last_change <= CHIP_FLASH_S:
            return QColor(STATUS_WARNING), f"Zona {index + 1} · FICHAS MOVIDAS"
        if zone.get("estado") == "actividad":
            owners = [
                HAND_ROLE_TEXT[role].lower()
                for role in zone.get("manos") or ()
                if role in HAND_ROLE_TEXT
            ]
            text = f"Zona {index + 1} · manos"
            return QColor(ROULETTE_ACTIVITY), f"{text} · {' y '.join(owners)}" if owners else text
        if armed:
            return QColor(STATUS_WARNING), f"Zona {index + 1} · ARMADA"
        return QColor(STATUS_OK), f"Zona {index + 1} · quieta"

    @staticmethod
    def _draw_chip_spots(
        painter: QPainter, zone: dict, scale_x: float, scale_y: float, now: float
    ) -> None:
        """Donde cambiaron las fichas (la ficha agregada, sacada o corrida),
        mientras dura el aviso: rojo si fue tras el no va mas."""
        last_change = zone.get("ultimo_cambio")
        alert = zone.get("estado") == "alerta"
        if not alert and (last_change is None or now - last_change > CHIP_FLASH_S):
            return
        color = QColor(STATUS_CRITICAL if alert else STATUS_WARNING)
        pen = QPen(color)
        pen.setWidthF(2.4)
        for x, y, w, h in zone.get("posiciones") or ():
            center = QPointF((x + w / 2) * scale_x, (y + h / 2) * scale_y)
            radius = max(7.0, max(w * scale_x, h * scale_y) / 2 + 4)
            halo = QColor(color)
            halo.setAlpha(55)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(center, radius + 5, radius + 5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, radius, radius)

    def _draw_roulette_wheel(
        self, painter: QPainter, metrics: dict, scale_x: float, scale_y: float
    ) -> None:
        """La elipse del plato y, al costado, su velocidad."""
        cx, cy, width, height, angle = metrics["rueda"]
        painter.save()
        painter.translate(cx * scale_x, cy * scale_y)
        painter.rotate(angle)
        pen = QPen(ANALYTIC_COLORS["monitor_tamper"])
        pen.setWidthF(2.2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Con la escala del tile distinta en x e y la elipse girada queda
        # levemente deformada; con el angulo casi recto de las camaras de
        # mesa la diferencia no se ve.
        painter.drawEllipse(QPointF(0, 0), width * scale_x / 2, height * scale_y / 2)
        painter.restore()
        speed = metrics.get("velocidad_deg_s")
        if speed is None:
            text = "Ruleta · midiendo…"
        else:
            arrow = "↻" if metrics.get("sentido") == "horario" else "↺"
            text = f"{arrow} {speed:.0f} °/s · {metrics.get('rpm', 0):.1f} rpm"
        top = QPointF((cx - width / 2) * scale_x, (cy - height / 2) * scale_y - 22)
        self._draw_guide_label(painter, top, text, ANALYTIC_COLORS["monitor_tamper"])
        self._draw_round(painter, metrics, top, scale_x, scale_y)

    def _draw_round(
        self, painter: QPainter, metrics: dict, anchor: QPointF, scale_x: float, scale_y: float
    ) -> None:
        """Arriba de la rueda, el estado de la ronda (con la velocidad de la
        bola); sobre la pista, la bola."""
        ronda = metrics.get("ronda") or {}
        state = ronda.get("estado")
        game = ronda.get("ultima_jugada")
        if state in ROUND_STYLE:
            text, color = ROUND_STYLE[state]
            if state in ("bola", "no_va_mas") and ronda.get("bola_deg_s"):
                text = f"{text} · bola {ronda['bola_deg_s']:.0f} °/s"
            elif state == "apuestas" and game and time.time() - game["t"] <= NEW_GAME_FLASH_S:
                what = display_rotor_event(game.get("tipo"))
                text, color = f"Nueva jugada · la rueda {what}", ROULETTE_ACTIVITY
            if state == "no_va_mas":
                font = painter.font()
                font.setBold(True)
                painter.setFont(font)
            self._draw_guide_label(painter, anchor - QPointF(0, 22), text, QColor(color))
            font = painter.font()
            font.setBold(False)
            painter.setFont(font)
        ball = metrics.get("bola")
        if ball:
            center = QPointF(ball[0] * scale_x, ball[1] * scale_y)
            painter.setPen(QPen(QColor(ROULETTE_ACTIVITY), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, 8.0, 8.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(center, 2.5, 2.5)

    def _draw_crossing_line(
        self,
        painter: QPainter,
        config,
        line,
        metrics: dict,
        scale_x: float,
        scale_y: float,
        flashing: bool,
    ) -> None:
        """Linea + flechas de sentido con su etiqueta y contador a cada
        lado. El analizador cuenta "entrada" al pasar del lado positivo al
        negativo de la linea (ver line_crossing_analyzer._signed_distance):
        la normal (-dy, dx) apunta al lado positivo, asi que la flecha de
        entrada va en sentido opuesto a ella."""
        params = config.params or {}
        (x1, y1), (x2, y2) = line
        p1 = QPointF(float(x1) * scale_x, float(y1) * scale_y)
        p2 = QPointF(float(x2) * scale_x, float(y2) * scale_y)
        color = ANALYTIC_COLORS["line_crossing"]
        if flashing:
            color = QColor(STATUS_OK)
        pen = QPen(color)
        pen.setWidthF(4.5 if flashing else 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(p1, p2)

        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        length = math.hypot(dx, dy)
        if length < 12:
            return
        normal = QPointF(-dy / length, dx / length)  # hacia el lado positivo
        mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2)
        if metrics.get("direction_enabled", params.get("direction_enabled", True)) is False:
            total = metrics.get("total")
            text = f"Cruces · {total}" if total is not None else "Cruces"
            self._draw_guide_label(painter, mid + normal * 14, text, color)
            return

        arrow = 40.0
        label_in = params.get("label_in") or "Entrada"
        label_out = params.get("label_out") or "Salida"
        for direction, label, count in (
            (-1.0, label_in, metrics.get("count_in")),
            (1.0, label_out, metrics.get("count_out")),
        ):
            tip = mid + normal * (arrow * direction)
            base = mid + normal * (8 * direction)
            self._draw_arrow(painter, base, tip, color)
            text = f"{label} · {count}" if count is not None else label
            self._draw_guide_label(
                painter, tip + normal * (14 * direction), text, color, centered=True
            )

    @staticmethod
    def _draw_arrow(painter: QPainter, start: QPointF, tip: QPointF, color: QColor) -> None:
        pen = QPen(color)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(start, tip)
        dx, dy = tip.x() - start.x(), tip.y() - start.y()
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        head = 7.0
        left = QPointF(tip.x() - ux * head - uy * head * 0.6, tip.y() - uy * head + ux * head * 0.6)
        right = QPointF(
            tip.x() - ux * head + uy * head * 0.6, tip.y() - uy * head - ux * head * 0.6
        )
        path = QPainterPath(tip)
        path.lineTo(left)
        path.lineTo(right)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(path)

    def _draw_people_heatmap(
        self, painter: QPainter, fresh: dict[str, DetectionEvent], width: int, height: int
    ) -> None:
        if not self._people_heatmap_enabled():
            return
        event = fresh.get("people_counting")
        if event is None:
            return
        grid = event.metrics.get("heatmap")
        if isinstance(grid, np.ndarray):
            # La grilla cubre el cuadro entero: se estira sobre el pixmap.
            draw_heatmap(painter, grid, QRectF(0, 0, width, height))

    def _people_heatmap_enabled(self) -> bool:
        """El overlay sigue el estado guardado, aunque aún exista un evento
        anterior en memoria con puntos del mapa de calor."""
        for config in self._analytics_configs:
            if config.analyzer_name == "people_counting":
                return bool((config.params or {}).get("heatmap_enabled", True))
        return False

    @staticmethod
    def _status_text(name: str, event: DetectionEvent | None) -> str:
        """Resumen de una linea por analitica, con la metrica que importa
        (antes era len(detections) para todas: la puerta mostraba siempre 0
        y el cruce, las personas visibles en vez de los cruces)."""
        if event is None:
            return "sin datos"
        metrics = event.metrics
        if name == "people_counting":
            text = f"{metrics.get('occupancy', 0)} personas"
            if metrics.get("max_people"):
                text += f" / {metrics['max_people']}"
            return text
        if name == "line_crossing":
            if metrics.get("direction_enabled", True) is False:
                return f"{metrics.get('total', 0)} cruces"
            return f"Ent. {metrics.get('count_in', 0)} · Sal. {metrics.get('count_out', 0)}"
        if name == "monitor_tamper":
            if metrics.get("modo") == "ruleta":
                if metrics.get("estado") == "alerta":
                    return f"ALERTA · fichas tras el no va más ({metrics.get('incidentes', 0)})"
                state = (metrics.get("ronda") or {}).get("estado")
                if state in ("no_va_mas", "resultado"):
                    return f"Ruleta {ROUND_STYLE[state][0]} · paño armado"
                speed = metrics.get("velocidad_deg_s")
                moved = metrics.get("fichas_movidas", 0)
                wheel = "buscando la rueda" if speed is None else f"{speed:.0f} °/s"
                return f"Ruleta {wheel} · {moved} movimientos de fichas"
            if metrics.get("estado") == "alerta":
                last = metrics.get("ultimo_incidente") or metrics.get("ultimo_golpe") or {}
                return f"ALERTA · {MOTIVE_TEXT.get(last.get('motivo') or '', 'golpe')}"
            if metrics.get("estado") == "previa":
                return "PREVIA · preparación"
            return f"OK · {metrics.get('incidentes', 0)} incidentes"
        if name == "face_detection":
            faces = metrics.get("caras", len(event.detections))
            return f"{faces} cara" if faces == 1 else f"{faces} caras"
        return f"{len(event.detections)} detecciones"

    def _draw_analytics_status(
        self, painter: QPainter, fresh: dict[str, DetectionEvent], width: int
    ) -> None:
        if not self._analytics_configs:
            return
        metrics = painter.fontMetrics()
        # Abajo a la derecha, apilados hacia arriba: arriba ya estan el
        # nombre de la camara y la hora, y las zonas suelen dibujarse ahi.
        device = painter.device()
        y = (device.height() if device is not None else 200) - 28.0
        for config in reversed(self._analytics_configs):
            name = config.analyzer_name
            short = SHORT_NAMES.get(name, ANALYZER_DISPLAY_NAMES.get(name, name))
            event = fresh.get(name)
            text = f"{short}  {self._status_text(name, event)}"
            chip_width = min(width - 16.0, metrics.horizontalAdvance(text) + 26.0)
            rect = QRectF(width - chip_width - 8, y, chip_width, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 23, 42, 215))
            painter.drawRoundedRect(rect, 6, 6)
            # Punto de identidad de la analitica; gris si no hay lecturas.
            dot = ANALYTIC_COLORS.get(name, DETECTION_STROKE) if event else QColor("#6e7681")
            painter.setBrush(dot)
            painter.drawEllipse(QPointF(rect.left() + 10, rect.center().y()), 3.5, 3.5)
            painter.setPen(QColor("#e5e7eb") if event else QColor("#9aa3af"))
            painter.drawText(
                rect.adjusted(18, 0, -6, 0),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                text,
            )
            y -= 24

    @staticmethod
    def _roi_for_config(config) -> tuple[int, int, int, int] | None:
        values = (config.roi_x, config.roi_y, config.roi_w, config.roi_h)
        if None in values:
            return None
        return values

    @classmethod
    def _rois_for_config(cls, config) -> list[tuple[int, int, int, int]]:
        zones = (config.params or {}).get("zones", [])
        valid = [tuple(zone) for zone in zones if len(zone) == 4]
        return valid or ([cls._roi_for_config(config)] if cls._roi_for_config(config) else [])

    @staticmethod
    def _draw_guide_label(
        painter: QPainter, point: QPointF, text: str, color: QColor, centered: bool = False
    ) -> None:
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 16
        if centered:
            rect = QRectF(point.x() - width / 2, point.y() - 9, width, 18)
        else:
            rect = QRectF(point.x() + 5, point.y() + 5, width, 18)
        # Que no se salga del cuadro (etiquetas de flechas cerca del borde).
        device = painter.device()
        if device is not None:
            rect.moveLeft(max(2.0, min(rect.left(), device.width() - width - 2)))
            rect.moveTop(max(2.0, min(rect.top(), device.height() - 20.0)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(8, 15, 24, 220))
        painter.drawRoundedRect(rect, 4, 4)
        # Identidad en la barra lateral; el texto en tinta neutra.
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(rect.left(), rect.top(), 3, rect.height()), 1.5, 1.5)
        painter.setPen(QColor("#e5e7eb"))
        painter.drawText(rect.adjusted(4, 0, 0, 0), Qt.AlignmentFlag.AlignCenter, text)

    @staticmethod
    def _draw_branding(painter: QPainter, text: str) -> None:
        # El nombre llega en la escena, cacheado junto con _branding: leerlo
        # aca abria preferences.json en cada cuadro de cada recuadro (medido
        # 2026-10-07: ~0,6 ms por render).
        font = painter.font()
        font.setBold(True)
        font.setPointSize(max(7, font.pointSize()))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        icon_size = 18
        width = metrics.horizontalAdvance(text) + icon_size + 22
        rect = QRectF(8, 8, width, 26)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(8, 15, 24, 220))
        painter.drawRoundedRect(rect, 7, 7)
        painter.setBrush(QColor("#22c55e"))
        painter.drawEllipse(QRectF(15, 15, 6, 6))
        painter.setPen(QColor("#eafff2"))
        painter.drawText(QRectF(28, 8, width - 34, 26), Qt.AlignmentFlag.AlignVCenter, text)

    @staticmethod
    def _draw_motion_mark(
        painter: QPainter,
        polygon: tuple[tuple[int, int], ...],
        scale_x: float,
        scale_y: float,
        color: QColor,
    ) -> None:
        if len(polygon) < 3:
            return
        path = QPainterPath()
        points = [QPointF(px * scale_x, py * scale_y) for px, py in polygon]
        path.moveTo(points[0])
        for point in points[1:]:
            path.lineTo(point)
        path.closeSubpath()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 55))
        painter.drawPath(path)

        pen = QPen(color)
        pen.setWidthF(1.6)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawPath(path)

    def _draw_detection_box(
        self,
        painter: QPainter,
        det: Detection,
        scale_x: float,
        scale_y: float,
        color: QColor,
        analyzer_name: str,
    ) -> None:
        x, y, w, h = det.bbox
        rect = QRectF(x * scale_x, y * scale_y, w * scale_x, h * scale_y)
        if analyzer_name == "face_detection":
            self._draw_face_reticle(painter, rect, det.confidence, color)
            return
        if analyzer_name == "monitor_tamper":
            # La zona ya se pinta con su estado; aca solo el pie o la mano
            # que golpeo la pantalla.
            self._draw_strike_points(painter, det, scale_x, scale_y)
            return

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 40))
        painter.drawRoundedRect(rect, 4, 4)

        pen = QPen(color)
        pen.setWidthF(1.8)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 4, 4)

        self._draw_label_chip(
            painter,
            rect.topLeft(),
            f"{ANALYZER_DISPLAY_NAMES.get(analyzer_name, analyzer_name)} · "
            f"{display_class(det.label)} {det.confidence:.0%}",
            color,
        )
        self._draw_confidence_bar(painter, rect, det.confidence)

    @staticmethod
    def _draw_strike_points(
        painter: QPainter, det: Detection, scale_x: float, scale_y: float
    ) -> None:
        """El pie o la mano que golpeo: punto rojo con halo."""
        halo = QColor(STATUS_CRITICAL)
        halo.setAlpha(90)
        for px, py in det.keypoints or ():
            center = QPointF(px * scale_x, py * scale_y)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(center, 16, 16)
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(QColor(STATUS_CRITICAL))
            painter.drawEllipse(center, 7, 7)

    @staticmethod
    def _draw_corner_brackets(painter: QPainter, rect: QRectF, color: QColor, width: float) -> None:
        corner = max(10.0, min(rect.width(), rect.height()) * 0.18)
        pen = QPen(color)
        pen.setWidthF(width + 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        for x, y, dx, dy in (
            (rect.left(), rect.top(), 1, 1),
            (rect.right(), rect.top(), -1, 1),
            (rect.left(), rect.bottom(), 1, -1),
            (rect.right(), rect.bottom(), -1, -1),
        ):
            painter.drawLine(QPointF(x, y), QPointF(x + corner * dx, y))
            painter.drawLine(QPointF(x, y), QPointF(x, y + corner * dy))

    @staticmethod
    def _draw_face_reticle(
        painter: QPainter, rect: QRectF, confidence: float, color: QColor
    ) -> None:
        """Marcador de rostro: solo las cuatro esquinas, sin relleno ni
        caja completa -- no tapa la cara que el operador quiere ver. La
        etiqueta (confianza) aparece solo si la cara es lo bastante grande
        en pantalla como para que no la tape."""
        corner = max(6.0, min(rect.width(), rect.height()) * 0.24)
        pen = QPen(color)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
        for x, y, dx, dy in (
            (left, top, 1, 1),
            (right, top, -1, 1),
            (left, bottom, 1, -1),
            (right, bottom, -1, -1),
        ):
            painter.drawLine(QPointF(x, y), QPointF(x + corner * dx, y))
            painter.drawLine(QPointF(x, y), QPointF(x, y + corner * dy))
        if rect.width() < 36:
            return
        text = f"{confidence:.0%}"
        metrics = painter.fontMetrics()
        chip = QRectF(0, 0, metrics.horizontalAdvance(text) + 12, metrics.height() + 2)
        chip.moveCenter(QPointF(rect.center().x(), rect.top() - chip.height() / 2 - 4))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(8, 15, 24, 200))
        painter.drawRoundedRect(chip, chip.height() / 2, chip.height() / 2)
        painter.setPen(QColor("#e5e7eb"))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)

    @staticmethod
    def _draw_confidence_bar(painter: QPainter, rect: QRectF, confidence: float) -> None:
        bar_rect = QRectF(rect.left(), rect.bottom() + 5, min(rect.width(), 100), 3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 55))
        painter.drawRoundedRect(bar_rect, 1.5, 1.5)
        painter.setBrush(DETECTION_STROKE)
        painter.drawRoundedRect(
            QRectF(
                bar_rect.left(),
                bar_rect.top(),
                bar_rect.width() * max(0.0, min(1.0, confidence)),
                bar_rect.height(),
            ),
            1.5,
            1.5,
        )

    @staticmethod
    def _draw_label_chip(
        painter: QPainter, top_left: QPointF, text: str, color: QColor = LABEL_CHIP_TEXT
    ) -> None:
        metrics = painter.fontMetrics()
        chip_w = metrics.horizontalAdvance(text) + 10
        chip_h = metrics.height() + 4
        chip_rect = QRectF(top_left.x(), top_left.y() - chip_h - 2, chip_w, chip_h)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(LABEL_CHIP_BG)
        painter.drawRoundedRect(chip_rect, 3, 3)

        painter.setPen(color)
        painter.drawText(chip_rect, Qt.AlignmentFlag.AlignCenter, text)

    @staticmethod
    def _draw_osd_text(
        painter: QPainter, width: int, y: int, alignment: Qt.AlignmentFlag, text: str
    ) -> None:
        if not text:
            return
        rect = QRectF(6, y, width - 12, 16)
        shadow_rect = rect.translated(1, 1)
        painter.setPen(QColor(0, 0, 0, 200))
        painter.drawText(shadow_rect, int(alignment), text)
        painter.setPen(QColor("#f2f2f2"))
        painter.drawText(rect, int(alignment), text)
