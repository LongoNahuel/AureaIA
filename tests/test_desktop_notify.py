"""Tests del canal de notificacion de escritorio.

Lo que fija este archivo es el invariante de hilo: notify() solo toca Qt
desde el hilo de la GUI. El bug que cerro la Fase 3 (B3) era exactamente
eso -- AlarmEngine lo llamaba desde el hilo de un AnalyticsWorker y
construia ahi el QSystemTrayIcon.

Los dobles reemplazan QApplication/QSystemTrayIcon en el namespace del
modulo en vez de depender del entorno: bajo QT_QPA_PLATFORM=offscreen
`isSystemTrayAvailable()` no es determinista, y un test que dependa de eso
pasa o falla segun la maquina.
"""

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

import pytest

import aurea_vms.core.desktop_notify as dn_module


@pytest.fixture(autouse=True)
def _reset_tray():
    """El icono de bandeja es un global de modulo: no puede sobrevivir de
    un test al siguiente."""
    yield
    dn_module._tray_icon = None


class FakeTray:
    def __init__(self) -> None:
        self.messages: list[tuple] = []
        self.shown = False
        self._visible = False

    def isVisible(self) -> bool:  # noqa: N802 - API de Qt
        return self._visible

    def show(self) -> None:
        self.shown = True
        self._visible = True

    def showMessage(self, title, message, icon, timeout) -> None:  # noqa: N802 - API de Qt
        self.messages.append((title, message, icon, timeout))


class TestGuardDeHilo:
    def test_el_hilo_principal_es_el_de_la_gui(self):
        assert dn_module._is_gui_thread()

    def test_sin_qapplication_vale_el_hilo_principal_de_python(self, monkeypatch):
        """Camino de import temprano (y de la suite corrida sola, sin
        ningun test de qtbot que haya creado la app): sin QApplication no
        hay `app.thread()` contra que comparar."""
        monkeypatch.setattr(dn_module, "QApplication", SimpleNamespace(instance=lambda: None))
        assert dn_module._is_gui_thread()

        resultado: list[bool] = []
        thread = threading.Thread(target=lambda: resultado.append(dn_module._is_gui_thread()))
        thread.start()
        thread.join()
        assert resultado == [False]

    def test_un_hilo_secundario_no_es_el_de_la_gui(self):
        resultado: list[bool] = []
        thread = threading.Thread(target=lambda: resultado.append(dn_module._is_gui_thread()))
        thread.start()
        thread.join()
        assert resultado == [False]

    def test_notify_desde_otro_hilo_no_construye_el_widget(self, monkeypatch, caplog):
        """El modo de falla de B3: desde un hilo de analitica, notify() no
        debe llegar ni a mirar el icono de bandeja."""
        llamadas: list[int] = []
        monkeypatch.setattr(dn_module, "_ensure_tray_icon", lambda: llamadas.append(1))

        with caplog.at_level(logging.ERROR, logger=dn_module.__name__):
            thread = threading.Thread(
                target=lambda: dn_module.notify("t", "m"), name="AnalyticsWorker-7"
            )
            thread.start()
            thread.join()

        assert llamadas == []
        assert "AnalyticsWorker-7" in caplog.text

    def test_notify_desde_el_hilo_de_la_gui_muestra_el_globo(self, monkeypatch):
        tray = FakeTray()
        monkeypatch.setattr(dn_module, "_ensure_tray_icon", lambda: tray)

        dn_module.notify("Alarma (critico) — Cam 1", "cara detectada con 80% de confianza.")

        assert tray.shown  # no estaba visible todavia
        title, message, _icon, timeout = tray.messages[0]
        assert title == "Alarma (critico) — Cam 1"
        assert message == "cara detectada con 80% de confianza."
        assert timeout == dn_module.NOTIFICATION_TIMEOUT_MS

    def test_sin_icono_disponible_notify_no_explota(self, monkeypatch):
        monkeypatch.setattr(dn_module, "_ensure_tray_icon", lambda: None)
        dn_module.notify("t", "m")  # no debe levantar

    def test_no_se_re_muestra_un_icono_ya_visible(self, monkeypatch):
        tray = FakeTray()
        tray.show()
        tray.shown = False
        monkeypatch.setattr(dn_module, "_ensure_tray_icon", lambda: tray)

        dn_module.notify("t", "m")

        assert not tray.shown
        assert len(tray.messages) == 1


class TestEnsureTrayIcon:
    def test_sin_qapplication_no_hay_icono(self, monkeypatch):
        monkeypatch.setattr(dn_module, "QApplication", SimpleNamespace(instance=lambda: None))
        assert dn_module._ensure_tray_icon() is None

    def test_sin_bandeja_disponible_queda_en_el_log(self, monkeypatch, caplog):
        """Antes era un no-op mudo: la regla decia "notificar", no pasaba
        nada, y no habia una sola linea para darse cuenta."""
        monkeypatch.setattr(dn_module, "QApplication", SimpleNamespace(instance=lambda: object()))
        monkeypatch.setattr(
            dn_module, "QSystemTrayIcon", SimpleNamespace(isSystemTrayAvailable=lambda: False)
        )

        with caplog.at_level(logging.WARNING, logger=dn_module.__name__):
            assert dn_module._ensure_tray_icon() is None

        assert "bandeja del sistema" in caplog.text

    def test_se_crea_una_sola_vez_y_con_la_app_como_padre(self, monkeypatch):
        app = SimpleNamespace(windowIcon=lambda: "icono")
        creados: list[tuple] = []

        class FakeTrayIcon:
            @staticmethod
            def isSystemTrayAvailable() -> bool:  # noqa: N802 - API de Qt
                return True

            def __init__(self, icon, parent) -> None:
                creados.append((icon, parent))

        monkeypatch.setattr(dn_module, "QApplication", SimpleNamespace(instance=lambda: app))
        monkeypatch.setattr(dn_module, "QSystemTrayIcon", FakeTrayIcon)

        primero = dn_module._ensure_tray_icon()
        segundo = dn_module._ensure_tray_icon()

        assert primero is segundo
        # Con padre: el icono muere con la app, no queda vivo hasta el
        # final del interprete.
        assert creados == [("icono", app)]
