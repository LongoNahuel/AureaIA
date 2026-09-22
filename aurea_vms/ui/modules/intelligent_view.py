"""Modulo dedicado a la Vista Inteligente y sus analiticas."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from aurea_vms.ui.modules.live_view import LiveViewModule


class IntelligentViewModule(LiveViewModule):
    """Vista Inteligente separada de la visualizacion en vivo convencional."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, smart_only=True)
