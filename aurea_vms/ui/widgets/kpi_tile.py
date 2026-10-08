"""Tarjeta de un numero (KPI): la misma en Inicio, Vista Inteligente y el
Dashboard de Eventos (Fase 1 de la interfaz, 2026-10-08).

Antes habia tres estilos, y los selectores genericos (`QWidget {...}`,
`QFrame {...}`) tambien le ponian fondo y borde a los textos de adentro: de
ahi las cajas dentro de cajas. Aca el estilo va por objectName.

El color del numero dice el estado (theme.TONES) y nada mas; con "alert" se
suma una barra a la izquierda, para que lo que pide accion se vea primero.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from aurea_vms.ui.theme import FONT_PX, SPACE, TONES, enable_tabular_numbers

_BACKGROUND = "#1e2530"
_BORDER = "#2a3441"
_MUTED = "#9aa3af"
# Mas largo que esto, el valor no es una cifra: va a tamaño de titulo.
MAX_KPI_CHARS = 8


class KpiTile(QFrame):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("kpiTile")
        self.tone = "neutral"
        self._compact = False

        self.value_label = QLabel("—", self)
        self.value_label.setObjectName("kpiValue")
        enable_tabular_numbers(self.value_label)
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("kpiTitle")
        self.detail_label = QLabel("", self)
        self.detail_label.setObjectName("kpiDetail")
        self.detail_label.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["l"], SPACE["m"], SPACE["l"], SPACE["m"])
        layout.setSpacing(SPACE["xs"])
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.detail_label)
        self._apply_style()

    def set_value(self, value: int | str, tone: str = "neutral", detail: str = "") -> None:
        text = str(value)
        self.value_label.setText(text)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        # Un texto ("Incidentes en casinos") va a tamaño de titulo: a tamaño
        # de cifra tapaba la tarjeta.
        compact = len(text) > MAX_KPI_CHARS
        if tone != self.tone or compact != self._compact:
            self.tone, self._compact = tone, compact
            self._apply_style()

    def _apply_style(self) -> None:
        color = TONES.get(self.tone, TONES["neutral"])
        size = FONT_PX["title"] if self._compact else FONT_PX["kpi"]
        accent = f"border-left: 3px solid {color};" if self.tone == "alert" else ""
        self.setStyleSheet(
            f"#kpiTile {{ background: {_BACKGROUND}; border: 1px solid {_BORDER};"
            f" border-radius: 10px; {accent} }}"
            # Fondo transparente en los textos: la hoja global le pone fondo
            # a los QLabel y quedaban rectangulos dentro de la tarjeta.
            f"#kpiValue {{ color: {color}; font-size: {size}px; font-weight: 700;"
            " background: transparent; border: none; }"
            f"#kpiTitle, #kpiDetail {{ color: {_MUTED}; font-size: {FONT_PX['caption']}px;"
            " background: transparent; border: none; }"
        )
