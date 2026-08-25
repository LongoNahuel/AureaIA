from __future__ import annotations

from aurea_vms.core import app_state
from aurea_vms.core.event_bus import event_bus


def _capturar(señales: list) -> callable:
    def _slot(site_id):
        señales.append(site_id)

    return _slot


class TestSiteFilter:
    def test_default_sin_filtro(self):
        assert app_state.current_site_id is None

    def test_set_actualiza_y_emite(self):
        recibidos: list = []
        slot = _capturar(recibidos)
        event_bus.site_filter_changed.connect(slot)
        try:
            app_state.set_site_filter(3)
            assert app_state.current_site_id == 3
            assert recibidos == [3]

            app_state.set_site_filter(None)
            assert app_state.current_site_id is None
            assert recibidos == [3, None]
        finally:
            event_bus.site_filter_changed.disconnect(slot)

    def test_mismo_valor_no_reemite(self):
        recibidos: list = []
        slot = _capturar(recibidos)
        app_state.set_site_filter(5)
        event_bus.site_filter_changed.connect(slot)
        try:
            app_state.set_site_filter(5)
            assert recibidos == []
        finally:
            event_bus.site_filter_changed.disconnect(slot)

    def test_reset(self):
        app_state.set_site_filter(9)
        app_state.reset()
        assert app_state.current_site_id is None
