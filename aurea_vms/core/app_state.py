"""Estado de sesion de la UI compartido entre modulos (no persistido).

Hoy contiene solo el filtro global de sitio (selector de la topbar).
Es una global de modulo al estilo de auth.current_user: proceso unico,
una sesion de UI a la vez. Los modulos no leen el combo de la topbar:
leen current_site_id al recargar y se suscriben a
event_bus.site_filter_changed para enterarse del cambio.
"""

from __future__ import annotations

from aurea_vms.core.event_bus import event_bus

# None = "todos los sitios" (sin filtro).
current_site_id: int | None = None


def set_site_filter(site_id: int | None) -> None:
    global current_site_id
    if site_id == current_site_id:
        return
    current_site_id = site_id
    event_bus.site_filter_changed.emit(site_id)


def reset() -> None:
    """Vuelve al estado sin filtro (logout, tests)."""
    global current_site_id
    current_site_id = None
