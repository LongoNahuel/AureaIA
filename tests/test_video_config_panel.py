"""Sistema > Audio y Video > Video (2026-10-08): la codificacion de cada
camara por ONVIF, lo que el VMS recibe y el trafico. Y el VMS siempre en
tema oscuro."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QObject, Signal

from aurea_vms.core import auth
from aurea_vms.core.onvif_video import CodecOptions, VideoSettings, VideoStream
from aurea_vms.models import repository
from aurea_vms.models.user import User
from aurea_vms.ui.widgets import video_config_panel as panel_module
from aurea_vms.ui.widgets.video_config_panel import VideoConfigPanel

H264 = CodecOptions(
    resolutions=((1920, 1080), (1280, 720)),
    fps=(1, 30),
    gov_length=(1, 150),
    bitrate_kbps=(64, 8192),
)
JPEG = CodecOptions(resolutions=((1280, 720), (640, 360)), fps=(1, 15))


def _stream(token="enc1", codec="H264", width=1920, height=1080, kbps=4096) -> VideoStream:
    return VideoStream(
        profile_token=f"p-{token}",
        profile_name=f"Perfil {token}",
        config_token=token,
        codec=codec,
        width=width,
        height=height,
        fps=25,
        bitrate_kbps=kbps,
        gov_length=50 if codec == "H264" else None,
        quality=5.0,
        quality_range=(1.0, 6.0),
        options={"H264": H264, "JPEG": JPEG},
    )


STREAMS = [_stream(), _stream("enc2", "JPEG", 640, 360, kbps=512)]


class _SyncWorker(QObject):
    """FunctionWorker sin hilo: corre al llamar a start()."""

    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, func, parent=None) -> None:
        super().__init__(parent)
        self._func = func

    def start(self) -> None:
        try:
            result = self._func()
        except Exception as exc:  # noqa: BLE001 - como FunctionWorker
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
        self.finished.emit()


def _login(role: str = "admin") -> None:
    auth.current_user = User(username=role, password_hash="h", salt="s", role=role)


@pytest.fixture()
def camaras(temp_db):
    onvif = repository.add_device(
        name="Mesa 1",
        ip="10.0.0.5",
        rtsp_main_url="rtsp://10.0.0.5/main",
        onvif_port=80,
        username="admin",
        password="clave",
    )
    rtsp = repository.add_device(name="Simulada", ip="127.0.0.1", rtsp_main_url="rtsp://x/cam1")
    return onvif, rtsp


@pytest.fixture()
def onvif(monkeypatch):
    """Las llamadas a la camara, falsas y anotadas."""
    llamadas = SimpleNamespace(leer=[], aplicar=[], streams=list(STREAMS), error=None)

    def leer(ip, port, user, password, channel):
        llamadas.leer.append((ip, port, user, password, channel))
        if llamadas.error:
            raise RuntimeError(llamadas.error)
        return llamadas.streams

    def aplicar(ip, port, user, password, token, settings):
        llamadas.aplicar.append((ip, token, settings))

    monkeypatch.setattr(panel_module.onvif_video, "fetch_video_streams", leer)
    monkeypatch.setattr(panel_module.onvif_video, "apply_video_settings", aplicar)
    monkeypatch.setattr(panel_module, "FunctionWorker", _SyncWorker)
    return llamadas


@pytest.fixture()
def avisos(monkeypatch):
    registro = SimpleNamespace(confirm=[], respuesta=True, notify=[], warn=[])

    def confirm(_parent, _title, text):
        registro.confirm.append(text)
        return registro.respuesta

    monkeypatch.setattr(panel_module, "confirm", confirm)
    monkeypatch.setattr(panel_module, "notify", lambda _p, _t, text: registro.notify.append(text))
    monkeypatch.setattr(panel_module, "warn", lambda _p, _t, text: registro.warn.append(text))
    return registro


@pytest.fixture()
def panel(qtbot, camaras, onvif, avisos):
    _login()
    widget = VideoConfigPanel()
    qtbot.addWidget(widget)
    return widget


def _elegir(panel, device) -> None:
    panel.device_selector.setCurrentIndex(panel.device_selector.findData(device.id))


class TestCodificacion:
    def test_una_camara_sin_onvif_no_se_lee(self, panel, camaras):
        _elegir(panel, camaras[1])

        assert not panel.read_button.isEnabled()
        assert "solo por RTSP" in panel.status_label.text()

    def test_leer_arma_una_columna_por_perfil(self, panel, camaras, onvif):
        _elegir(panel, camaras[0])
        panel.read_from_camera()

        assert onvif.leer == [("10.0.0.5", 80, "admin", "clave", 1)]
        principal, secundario = panel._editors
        assert principal.settings() == VideoSettings("H264", 1920, 1080, 25, 4096, 50, 5.0)
        assert principal.gov.isEnabled() and not secundario.gov.isEnabled()
        assert secundario.settings().codec == "JPEG"
        assert (principal.bitrate.minimum(), principal.bitrate.maximum()) == (64, 8192)

    def test_cambiar_de_codec_ofrece_sus_resoluciones(self, panel, camaras):
        _elegir(panel, camaras[0])
        panel.read_from_camera()
        principal = panel._editors[0]

        principal.codec.setCurrentIndex(principal.codec.findData("JPEG"))

        resoluciones = [
            principal.resolution.itemData(i) for i in range(principal.resolution.count())
        ]
        # 1920x1080 no es de MJPEG: no se ofrece y queda la primera.
        assert resoluciones == [(1280, 720), (640, 360)]
        assert principal.settings().width == 1280
        assert principal.settings().gov_length is None
        assert principal.fps.maximum() == 15

    def test_la_resolucion_configurada_se_ofrece_aunque_no_figure(self, panel, camaras, onvif):
        onvif.streams = [_stream(width=2048, height=1536)]
        _elegir(panel, camaras[0])
        panel.read_from_camera()

        assert panel._editors[0].settings().width == 2048

    def test_un_error_de_la_camara_se_muestra(self, panel, camaras, onvif):
        onvif.error = "no responde"
        _elegir(panel, camaras[0])
        panel.read_from_camera()

        assert panel.status_label.text() == "No se pudo leer: no responde"
        assert panel.read_button.isEnabled()

    def test_aplicar_confirma_manda_y_relee(self, panel, camaras, onvif, avisos):
        _elegir(panel, camaras[0])
        panel.read_from_camera()
        principal = panel._editors[0]
        principal.fps.setValue(15)
        principal.bitrate.setValue(2048)

        principal.apply_button.click()

        assert "15 FPS" in avisos.confirm[0] and "zonas" not in avisos.confirm[0]
        ((ip, token, settings),) = onvif.aplicar
        assert (ip, token) == ("10.0.0.5", "enc1")
        assert (settings.fps, settings.bitrate_kbps) == (15, 2048)
        assert len(onvif.leer) == 2  # relee lo que la camara guardo
        assert avisos.notify == ["Aplicado en «Mesa 1»."]

    def test_cambiar_la_resolucion_con_analiticas_avisa_por_las_zonas(
        self, panel, camaras, onvif, avisos
    ):
        repository.upsert_analytics_config(camaras[0].id, "monitor_tamper")
        avisos.respuesta = False
        _elegir(panel, camaras[0])
        panel.read_from_camera()
        principal = panel._editors[0]
        principal.resolution.setCurrentIndex(principal.resolution.findData((1280, 720)))

        principal.apply_button.click()

        assert "zonas" in avisos.confirm[0] and "1920×1080" in avisos.confirm[0]
        assert onvif.aplicar == []  # dijo que no

    def test_sin_permiso_no_aplica(self, panel, camaras, onvif, avisos):
        _elegir(panel, camaras[0])
        panel.read_from_camera()
        _login("supervisor")

        panel._editors[0].apply_button.click()

        assert avisos.warn and onvif.aplicar == []


class _Worker:
    def __init__(self, frame, fps=24.6, stale=False):
        self.frame, self.fps, self.stale = frame, fps, stale

    def get_latest_frame(self):
        return self.frame

    def get_fps(self):
        return self.fps

    def is_stale(self):
        return self.stale


class TestMedidoYTrafico:
    def test_lo_que_recibe_el_vms(self, panel, camaras, monkeypatch):
        worker = _Worker(np.zeros((720, 1280, 3), np.uint8))
        monkeypatch.setattr(
            panel_module.stream_manager, "get_worker", lambda device_id, _kind: worker
        )
        _elegir(panel, camaras[1])  # tambien una simulada, sin ONVIF

        assert panel.received_label.text() == "El VMS recibe: 1280×720 a 24,6 FPS"

    def test_sin_señal_y_sin_abrir(self, panel, camaras, monkeypatch):
        worker = _Worker(np.zeros((10, 10, 3), np.uint8), stale=True)
        monkeypatch.setattr(panel_module.stream_manager, "get_worker", lambda *_a: worker)
        panel.refresh_measurements()
        assert "sin señal" in panel.received_label.text()

        monkeypatch.setattr(panel_module.stream_manager, "get_worker", lambda *_a: None)
        panel.refresh_measurements()
        assert "ninguna vista" in panel.received_label.text()

    def test_la_tabla_y_el_total_configurado(self, panel, camaras):
        _elegir(panel, camaras[0])
        panel.read_from_camera()

        filas = {
            panel.traffic_table.item(r, 0).text(): panel.traffic_table.item(r, 4).text()
            for r in range(panel.traffic_table.rowCount())
        }
        assert filas == {"Mesa 1": "4096 kbps", "Simulada": "—"}
        assert "4,1 Mb/s" in panel.total_label.text()

    def test_la_red_de_la_pc_sin_loopback(self, panel, monkeypatch):
        lecturas = iter(
            [
                {"lo": _red(0, 0), "eth0": _red(0, 0)},
                {"lo": _red(9e9, 9e9), "eth0": _red(2_000_000, 500_000)},
            ]
        )
        relojes = iter([100.0, 102.0])
        monkeypatch.setattr(panel_module.psutil, "net_io_counters", lambda pernic: next(lecturas))
        monkeypatch.setattr(panel_module.time, "monotonic", lambda: next(relojes))
        panel._net_last = None

        panel._refresh_network()
        panel._refresh_network()

        # 2 MB en 2 s = 8 Mb/s; 0,5 MB en 2 s = 2 Mb/s.
        assert panel.network_label.text() == "Red de la PC: entra 8,0 Mb/s · sale 2,0 Mb/s"

    def test_solo_mide_mientras_se_ve(self, panel, qtbot):
        panel.show()
        qtbot.waitExposed(panel)
        assert panel._timer.isActive()

        panel.hide()
        assert not panel._timer.isActive()


def _red(recibidos, enviados):
    return SimpleNamespace(bytes_recv=int(recibidos), bytes_sent=int(enviados))


class TestSistema:
    @pytest.fixture()
    def sistema(self, qtbot, camaras):
        from aurea_vms.ui.modules.system_module import SystemModule

        _login()
        modulo = SystemModule()
        qtbot.addWidget(modulo)
        return modulo

    def test_video_es_el_panel_y_no_un_proximamente(self, sistema):
        sistema.focus_section("Audio y Video", "Video")

        assert sistema.pages.currentWidget().findChild(VideoConfigPanel) is sistema.video_panel
        assert sistema.video_panel.device_selector.count() == 2

    def test_ya_no_hay_switch_de_tema(self, sistema):
        assert not hasattr(sistema, "dark_mode_switch")


def test_el_tema_es_siempre_oscuro(qapp):
    """Una base de antes puede traer "light" guardado y main.py se lo pasa a
    apply_theme: igual queda oscuro."""
    from qfluentwidgets import isDarkTheme

    from aurea_vms.ui.theme import apply_theme, build_stylesheet

    apply_theme(False)

    assert isDarkTheme()
    assert qapp.styleSheet() == build_stylesheet(dark=True)
