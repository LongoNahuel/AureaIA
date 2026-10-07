"""Pool de render de los recuadros de video (Fase V1b, 2026-10-07): achicar,
convertir y dibujar el overlay sale del hilo de la GUI."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage

import aurea_vms.ui.widgets.video_tile as vt_module
from aurea_vms.core.events import DetectionEvent
from aurea_vms.ui import render_pool
from aurea_vms.ui.render_pool import RenderPool
from aurea_vms.ui.widgets.video_tile import VideoTile


@pytest.fixture()
def pool(qapp):
    real = RenderPool(hilos=2)
    yield real
    real.detener()


def _esperar(qapp, condicion, timeout: float = 2.0) -> bool:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        qapp.processEvents()
        if condicion():
            return True
        time.sleep(0.002)
    return False


class TestRenderPool:
    def test_el_trabajo_corre_en_un_hilo_de_render_y_la_entrega_en_la_gui(self, qapp, pool):
        hilos: dict[str, str] = {}

        def trabajo():
            hilos["trabajo"] = threading.current_thread().name
            return "listo"

        def entrega(resultado):
            hilos["entrega"] = threading.current_thread().name

        pool.enviar("a", trabajo, entrega)

        assert _esperar(qapp, lambda: "entrega" in hilos)
        assert hilos["trabajo"].startswith("Render-")
        assert hilos["entrega"] == threading.main_thread().name

    def test_el_ultimo_gana(self, qapp, pool):
        """Mientras un recuadro arma un cuadro, los que llegan se pisan: se
        arma el ultimo, no toda la fila (un cuadro viejo no sirve)."""
        suelto = threading.Event()
        armados: list[int] = []

        def trabajo(n):
            def correr():
                if n == 0:
                    suelto.wait(2.0)
                armados.append(n)
                return n

            return correr

        pool.enviar("a", trabajo(0), lambda _r: None)
        assert _esperar(qapp, lambda: "a" in pool._en_curso)
        for n in (1, 2, 3):
            pool.enviar("a", trabajo(n), lambda _r: None)
        suelto.set()

        assert _esperar(qapp, lambda: len(armados) == 2)
        time.sleep(0.05)
        assert armados == [0, 3]

    def test_uno_en_curso_por_recuadro(self, qapp, pool):
        """Con dos hilos libres, dos cuadros del mismo recuadro igual van de a
        uno: el overlay toca estado del recuadro (el destello de la linea)."""
        en_curso = [0]
        maximo = [0]
        lock = threading.Lock()

        def trabajo():
            with lock:
                en_curso[0] += 1
                maximo[0] = max(maximo[0], en_curso[0])
            time.sleep(0.02)
            with lock:
                en_curso[0] -= 1
            return 1

        hechos: list = []
        for _ in range(5):
            pool.enviar("a", trabajo, hechos.append)
            time.sleep(0.005)

        assert _esperar(qapp, lambda: not pool._en_curso and not pool._pendientes)
        assert maximo[0] == 1

    def test_recuadros_distintos_van_en_paralelo(self, qapp, pool):
        barrera = threading.Barrier(2, timeout=2.0)
        hechos: list = []

        def trabajo():
            barrera.wait()  # sin paralelismo, esto vence el timeout
            return 1

        pool.enviar("a", trabajo, hechos.append)
        pool.enviar("b", trabajo, hechos.append)

        assert _esperar(qapp, lambda: len(hechos) == 2)

    def test_un_trabajo_que_falla_no_mata_el_hilo(self, qapp, pool, caplog):
        def revienta():
            raise RuntimeError("QPainter roto")

        hechos: list = []
        pool.enviar("a", revienta, hechos.append)
        pool.enviar("b", lambda: 7, hechos.append)

        assert _esperar(qapp, lambda: hechos == [7])
        assert "QPainter roto" in caplog.text

    def test_descartar_olvida_lo_que_espera(self, qapp, pool):
        suelto = threading.Event()
        armados: list[str] = []

        def bloquea():
            suelto.wait(2.0)
            armados.append("primero")
            return 1

        pool.enviar("a", bloquea, lambda _r: None)
        assert _esperar(qapp, lambda: "a" in pool._en_curso)
        pool.enviar("a", lambda: armados.append("segundo") or 1, lambda _r: None)
        pool.descartar("a")
        suelto.set()

        assert _esperar(qapp, lambda: not pool._en_curso)
        time.sleep(0.05)
        assert armados == ["primero"]

    def test_detener_para_los_hilos_e_ignora_lo_nuevo(self, qapp):
        local = RenderPool(hilos=2)
        local.detener()

        assert all(not hilo.is_alive() for hilo in local._hilos)
        hechos: list = []
        local.enviar("a", lambda: 1, hechos.append)
        assert not _esperar(qapp, lambda: hechos, timeout=0.1)

    def test_el_pool_compartido_se_recrea_despues_de_detener(self, qapp):
        """Logout -> login: main.py detiene el pool al cerrar la ventana y el
        proximo recuadro lo vuelve a crear."""
        render_pool.usar(None)
        primero = render_pool.pool()
        render_pool.detener()
        segundo = render_pool.pool()

        assert segundo is not primero
        assert isinstance(segundo, RenderPool)
        assert all(not hilo.is_alive() for hilo in primero._hilos)


def _frame(width: int = 1280, height: int = 720) -> np.ndarray:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = (255, 0, 0)  # azul en BGR
    return frame


class _Worker:
    def __init__(self, frame):
        self.frame, self.ts = frame, 1.0

    def get_latest_frame_with_timestamp(self):
        return self.frame, self.ts

    def is_stale(self):
        return False


@pytest.fixture()
def tile(qtbot, monkeypatch):
    widget = VideoTile(0)
    qtbot.addWidget(widget)
    widget._device = SimpleNamespace(name="Cam", id=5)
    widget._analytics_configs = []
    widget._timer.stop()
    widget.resize(660, 400)
    worker = _Worker(_frame())
    monkeypatch.setattr(vt_module.stream_manager, "get_worker", lambda *_a, **_k: worker)
    monkeypatch.setattr(widget, "_en_pantalla", lambda: True)
    return widget


class TestTileConPool:
    def test_el_cuadro_se_arma_en_un_hilo_de_render(self, qapp, tile, pool, monkeypatch):
        render_pool.usar(pool)
        hilos: list[str] = []
        original = vt_module.frame_to_image

        def espiar(*args):
            hilos.append(threading.current_thread().name)
            return original(*args)

        monkeypatch.setattr(vt_module, "frame_to_image", espiar)
        tile._refresh_frame()

        assert _esperar(qapp, lambda: tile.video_label._cuadro is not None)
        assert hilos and hilos[0].startswith("Render-")
        imagen = tile.video_label._cuadro
        centro = QColor(imagen.pixel(imagen.width() // 2, imagen.height() // 2))
        assert (centro.red(), centro.green(), centro.blue()) == (0, 0, 255)

    def test_un_cuadro_de_la_camara_anterior_no_se_muestra(self, tile):
        generacion = tile._generacion
        tile.release()  # cambio de camara: sube la generacion

        tile._mostrar(QImage(10, 10, QImage.Format.Format_RGB32), generacion)

        assert tile.video_label._cuadro is None

    def test_sin_señal_no_lo_pisa_un_cuadro_en_vuelo(self, tile):
        tile._offline_rendered = True

        tile._mostrar(QImage(10, 10, QImage.Format.Format_RGB32), tile._generacion)

        assert tile.video_label._cuadro is None

    def test_un_recuadro_borrado_ignora_su_cuadro(self, qapp):
        from shiboken6 import delete

        suelto = VideoTile(0)  # sin qtbot: el test lo borra
        generacion = suelto._generacion
        delete(suelto)  # se cerro la vista con un cuadro en vuelo
        suelto._mostrar(QImage(10, 10, QImage.Format.Format_RGB32), generacion)  # no explota

    def test_la_escena_es_una_foto_del_estado(self, tile):
        """El overlay corre en otro hilo mientras la GUI sigue recibiendo
        detecciones: lee una copia, no el dict vivo (iterarlo mientras la GUI
        lo cambia levanta RuntimeError)."""
        evento = DetectionEvent(5, "monitor_tamper", time.time())
        tile._latest_events["monitor_tamper"] = evento
        escena = tile._escena()
        tile._latest_events["otro"] = evento

        assert escena.eventos is not tile._latest_events
        assert list(escena.eventos) == ["monitor_tamper"]
        assert escena.nombre == "Cam"


class TestPintadoDelCuadro:
    def test_el_cuadro_se_pinta_opaco_y_el_estado_vuelve_al_qlabel(self, tile):
        label = tile.video_label
        label.set_cuadro(QImage(64, 36, QImage.Format.Format_RGB32))
        assert label.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        assert label.pixmap().size() == QSize(64, 36)

        tile._render_offline_state()

        assert label._cuadro is None
        assert not label.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def test_pinta_cuadro_centrado_y_borde_de_seleccion(self, qtbot, tile):
        tile.show()
        qtbot.waitExposed(tile)
        imagen = QImage(100, 50, QImage.Format.Format_RGB32)
        imagen.fill(QColor("#ff0000"))
        tile.video_label.set_cuadro(imagen)
        tile.set_selected(True)

        captura = tile.video_label.grab().toImage()
        w, h = captura.width(), captura.height()

        assert QColor(captura.pixel(w // 2, h // 2)) == QColor("#ff0000")
        assert QColor(captura.pixel(w // 2, 1)) == QColor(vt_module.BORDER_SELECTED)
        assert QColor(captura.pixel(w // 2, h // 4)) == vt_module._VideoDisplay.FONDO
