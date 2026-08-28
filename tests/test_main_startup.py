from __future__ import annotations

import aurea_vms.main as main_module
from aurea_vms.models import repository


class EngineConConfigRoto:
    """Doble del analytics_engine cuyo start falla para un config puntual,
    como hace el real cuando create_analyzer no puede construir el
    analizador (ej. line_crossing sin linea)."""

    def __init__(self, broken_config_id: int) -> None:
        self.broken_config_id = broken_config_id
        self.started: list[int] = []

    def start(self, config, device) -> None:
        if config.id == self.broken_config_id:
            raise ValueError("necesita una línea configurada")
        self.started.append(config.id)


def test_un_config_roto_no_impide_el_arranque(temp_db, monkeypatch):
    """_start_enabled_analytics corre antes de crear la MainWindow: si un
    config habilitado pero invalido propagara la excepcion, la app no
    volveria a abrir hasta editar la DB a mano."""
    device = repository.add_device(name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")
    roto = repository.upsert_analytics_config(device.id, "line_crossing", enabled=True)
    sano = repository.upsert_analytics_config(device.id, "motion_detection", enabled=True)

    engine = EngineConConfigRoto(roto.id)
    monkeypatch.setattr(main_module, "analytics_engine", engine)

    main_module._start_enabled_analytics()  # no debe lanzar

    assert sano.id in engine.started
