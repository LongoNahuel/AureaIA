from __future__ import annotations

from pathlib import Path

import PySide6

import aurea_vms.main as main_module
from aurea_vms.models import repository

PYSIDE6_PLATFORMS = str(Path(PySide6.__file__).resolve().parent / "Qt" / "plugins" / "platforms")


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


class TestEnsureLinuxQtPluginPath:
    """El wheel Linux de cv2 deja QT_QPA_PLATFORM_PLUGIN_PATH apuntando a
    sus plugins Qt (donde el xcb no carga); el helper debe re-apuntar a los
    de PySide6 sin pisar un override manual del usuario."""

    def test_corrige_el_path_envenenado_por_cv2(self, monkeypatch):
        monkeypatch.setattr(main_module.sys, "platform", "linux")
        monkeypatch.setenv(
            "QT_QPA_PLATFORM_PLUGIN_PATH", "/x/site-packages/cv2/qt/plugins/platforms"
        )
        main_module._ensure_linux_qt_plugin_path()
        assert main_module.os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] == PYSIDE6_PLATFORMS

    def test_setea_el_path_si_no_estaba(self, monkeypatch):
        monkeypatch.setattr(main_module.sys, "platform", "linux")
        monkeypatch.delenv("QT_QPA_PLATFORM_PLUGIN_PATH", raising=False)
        main_module._ensure_linux_qt_plugin_path()
        assert main_module.os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] == PYSIDE6_PLATFORMS

    def test_el_dir_apuntado_existe_y_trae_el_xcb(self, monkeypatch):
        """Si PySide6 cambiara el layout de plugins, mejor enterarse por un
        test que por un core dump en la maquina de demo."""
        monkeypatch.setattr(main_module.sys, "platform", "linux")
        monkeypatch.delenv("QT_QPA_PLATFORM_PLUGIN_PATH", raising=False)
        main_module._ensure_linux_qt_plugin_path()
        platforms = Path(main_module.os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"])
        assert platforms.is_dir()
        assert any(p.name.startswith("libqxcb") for p in platforms.iterdir())

    def test_respeta_un_override_manual(self, monkeypatch):
        monkeypatch.setattr(main_module.sys, "platform", "linux")
        monkeypatch.setenv("QT_QPA_PLATFORM_PLUGIN_PATH", "/opt/mis-plugins/platforms")
        main_module._ensure_linux_qt_plugin_path()
        assert main_module.os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] == "/opt/mis-plugins/platforms"

    def test_fuera_de_linux_no_toca_nada(self, monkeypatch):
        monkeypatch.setattr(main_module.sys, "platform", "win32")
        monkeypatch.delenv("QT_QPA_PLATFORM_PLUGIN_PATH", raising=False)
        main_module._ensure_linux_qt_plugin_path()
        assert "QT_QPA_PLATFORM_PLUGIN_PATH" not in main_module.os.environ
