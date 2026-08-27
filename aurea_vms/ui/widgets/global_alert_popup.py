"""Capa de popups de alarma visible en cualquier pantalla de la app (no
solo dentro del modulo Alarmas): al dispararse una alarma aparece una
tarjeta flotante en la esquina inferior derecha de la ventana principal,
con accion "Reconocer" inline. Las criticas quedan fijas hasta que se
las reconoce; el resto se auto-descarta a los pocos segundos."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    HyperlinkButton,
    TransparentToolButton,
)

from aurea_vms.core.events import AlarmEvent
from aurea_vms.core.permissions import Perm, can
from aurea_vms.models import repository
from aurea_vms.ui.labels import display_class
from aurea_vms.ui.theme import SEVERITY_COLORS

SEVERITY_LABELS = {"critico": "Crítico", "alto": "Alto", "medio": "Medio", "info": "Info"}
AUTO_DISMISS_MS = 8000
MAX_VISIBLE = 5


class _AlertCard(QWidget):
    def __init__(self, event: AlarmEvent, device_name: str, parent: GlobalAlertPopupLayer) -> None:
        super().__init__(parent)
        self._layer = parent
        self._event = event
        color = SEVERITY_COLORS.get(event.severity, "#3b82f6")

        self.setFixedWidth(300)
        self.setStyleSheet(
            f"background-color: rgba(18, 23, 33, 240); border-left: 3px solid {color}; border-radius: 6px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 10, 10)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        severity_label = CaptionLabel(SEVERITY_LABELS.get(event.severity, event.severity), self)
        severity_label.setStyleSheet(f"color: {color}; font-weight: 600;")
        top_row.addWidget(severity_label)
        top_row.addStretch(1)
        close_button = TransparentToolButton(FluentIcon.CLOSE, self)
        close_button.setFixedSize(20, 20)
        close_button.clicked.connect(self._dismiss)
        top_row.addWidget(close_button)
        layout.addLayout(top_row)

        title = BodyLabel(f"{device_name} — {display_class(event.object_class)}", self)
        title.setWordWrap(True)
        layout.addWidget(title)

        confidence_label = CaptionLabel(f"Confianza: {event.confidence:.0%}", self)
        layout.addWidget(confidence_label)

        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        ack_button = HyperlinkButton(self)
        ack_button.setText("Reconocer")
        ack_button.clicked.connect(self._acknowledge)
        # Un rol sin gestion de alertas (ej. Auditor) ve el popup pero no
        # puede reconocerlo.
        ack_button.setEnabled(can(Perm.ALARM_MANAGE))
        buttons_row.addWidget(ack_button)
        layout.addLayout(buttons_row)

        if event.severity != "critico":
            QTimer.singleShot(AUTO_DISMISS_MS, self._dismiss)

    def _acknowledge(self) -> None:
        from aurea_vms.models.alarm_event import STATUS_ACKNOWLEDGED

        repository.set_alarm_event_status(self._event.alarm_event_id, STATUS_ACKNOWLEDGED)
        self._dismiss()

    def _dismiss(self) -> None:
        self._layer.remove_card(self)


class GlobalAlertPopupLayer(QWidget):
    """Contenedor de las tarjetas de alerta, anclado a la esquina inferior
    derecha de la ventana principal. A diferencia de una capa transparente
    que cubre toda la ventana (lo que se probo primero y termino
    bloqueando todos los clicks del resto de la app, incluso donde no
    habia ninguna tarjeta), este widget se autoajusta a su contenido real
    y queda oculto por completo cuando no hay alertas -- asi no puede
    interceptar clicks fuera de si mismo."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._cards: list[_AlertCard] = []

        self.hide()

    def show_alarm(self, event: AlarmEvent, device_name: str) -> None:
        card = _AlertCard(event, device_name, self)
        self._cards.append(card)
        self._layout.addWidget(card)
        card.show()
        self._reflow()

        while len(self._cards) > MAX_VISIBLE:
            self.remove_card(self._cards[0])

    def remove_card(self, card: _AlertCard) -> None:
        if card not in self._cards:
            return
        self._cards.remove(card)
        self._layout.removeWidget(card)
        card.deleteLater()
        self._reflow()

    def _reflow(self) -> None:
        self.adjustSize()
        self.setVisible(bool(self._cards))
        self.reposition()

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        margin = 12
        x = max(0, parent.width() - self.width() - margin)
        y = max(0, parent.height() - self.height() - margin)
        self.move(x, y)
        self.raise_()
