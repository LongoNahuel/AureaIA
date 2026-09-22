from __future__ import annotations

import threading
import time

import numpy as np
import pytest

import aurea_vms.core.stream_manager as sm_module
from aurea_vms.core.stream_manager import StreamManager
from aurea_vms.models.device import Device


class FakeWorker:
    """Doble del StreamWorker: registra start/stop sin abrir RTSP."""

    def __init__(self, device: Device, kind: str = "main") -> None:
        self.device_id = device.id
        self.kind = kind
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: float | None = None) -> None:
        pass


@pytest.fixture()
def manager(monkeypatch) -> StreamManager:
    monkeypatch.setattr(sm_module, "StreamWorker", FakeWorker)
    return StreamManager()


def _device(device_id: int = 1, sub: str | None = "rtsp://cam/sub") -> Device:
    device = Device(
        name=f"Cam {device_id}",
        ip="10.0.0.10",
        rtsp_main_url="rtsp://cam/main",
        rtsp_sub_url=sub,
        username="",
        password="",
    )
    device.id = device_id
    return device


class TestRefCounting:
    def test_dos_acquire_comparten_worker(self, manager):
        device = _device()
        w1 = manager.acquire(device)
        w2 = manager.acquire(device)

        assert w1 is w2
        assert w1.started

    def test_release_solo_detiene_al_llegar_a_cero(self, manager):
        device = _device()
        worker = manager.acquire(device)
        manager.acquire(device)

        manager.release(device.id)
        assert not worker.stopped

        manager.release(device.id)
        assert worker.stopped
        assert manager.get_worker(device.id) is None

    def test_release_de_algo_no_adquirido_es_noop(self, manager):
        manager.release(99)  # no debe explotar

    def test_calidades_distintas_son_workers_distintos(self, manager):
        device = _device()
        main = manager.acquire(device, "main")
        sub = manager.acquire(device, "sub")
        assert main is not sub


class TestFallbackSub:
    def test_sin_sub_url_cae_a_main(self, manager):
        device = _device(sub=None)
        main = manager.acquire(device, "main")
        sub = manager.acquire(device, "sub")

        assert main is sub
        assert manager.get_worker(device.id, "sub") is main

    def test_release_de_sub_redirigido_decrementa_main(self, manager):
        device = _device(sub=None)
        worker = manager.acquire(device, "sub")  # internamente es "main"

        manager.release(device.id, "sub")
        assert worker.stopped


class TestStopAll:
    def test_detiene_todo_y_limpia(self, manager):
        d1, d2 = _device(1), _device(2)
        w1, w2 = manager.acquire(d1), manager.acquire(d2)

        manager.stop_all()

        assert w1.stopped and w2.stopped
        assert manager.get_worker(1) is None
        assert manager.get_worker(2) is None


class TestStopDevice:
    def test_corta_main_y_sub_de_esa_camara_solamente(self, manager):
        objetivo, otro = _device(1), _device(2)
        w_main = manager.acquire(objetivo, "main")
        w_sub = manager.acquire(objetivo, "sub")
        w_otro = manager.acquire(otro)

        manager.stop_device(1)

        assert w_main.stopped and w_sub.stopped
        assert not w_otro.stopped
        assert manager.get_worker(1) is None
        assert manager.get_worker(2) is w_otro

    def test_acquire_posterior_crea_worker_nuevo(self, manager):
        device = _device(1)
        viejo = manager.acquire(device)
        manager.stop_device(1)

        nuevo = manager.acquire(device)
        assert nuevo is not viejo
        assert not nuevo.stopped


class TestConcurrencia:
    def test_acquire_release_concurrentes_no_duplican_workers(self, manager):
        """Regresion de la carrera real: UI y analiticas llaman acquire()
        del mismo device desde hilos distintos; sin lock aparecian dos
        workers para la misma clave y el ref-counting quedaba roto."""
        device = _device(1)
        errors: list[Exception] = []

        def ciclo() -> None:
            try:
                for _ in range(200):
                    manager.acquire(device)
                    manager.release(device.id)
            except Exception as exc:  # pragma: no cover - solo en fallo
                errors.append(exc)

        threads = [threading.Thread(target=ciclo) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # Balance perfecto de acquire/release: no debe quedar nada vivo.
        assert manager.get_worker(1) is None


class TestIsStale:
    def test_worker_real_sin_frames_es_stale(self):
        # StreamWorker real sin start(): el constructor no abre RTSP.
        worker = sm_module.StreamWorker(_device(1))
        assert worker.is_stale()

    def test_frame_reciente_no_es_stale(self):
        worker = sm_module.StreamWorker(_device(1))
        worker._latest_frame_ts = time.monotonic()
        assert not worker.is_stale()


class TestReportStatus:
    """_report_status persiste online/offline en DB (antes solo lo escribia
    el boton "probar conexion" y el dashboard mostraba streams vivos como
    "Sin probar"), pero solo en transiciones: el loop de reconexion de una
    camara caida no debe escribir "offline" en cada reintento."""

    def _worker_con_spy(self, monkeypatch) -> tuple:
        calls: list[tuple[int, str]] = []
        monkeypatch.setattr(
            sm_module.repository,
            "update_device_status",
            lambda device_id, status: calls.append((device_id, status)),
        )
        return sm_module.StreamWorker(_device(7)), calls

    def test_persiste_solo_transiciones(self, monkeypatch):
        worker, calls = self._worker_con_spy(monkeypatch)

        worker._report_status(True, "Conectado")
        worker._report_status(True, "Conectado")  # sin cambio: no escribe
        worker._report_status(False, "Se perdió la conexión")
        worker._report_status(False, "No se pudo abrir el stream")  # reintento
        worker._report_status(True, "Conectado")

        assert calls == [(7, "online"), (7, "offline"), (7, "online")]

    def test_error_de_db_no_mata_el_hilo_y_reintenta(self, monkeypatch):
        worker, _ = self._worker_con_spy(monkeypatch)

        def explota(device_id, status):
            raise RuntimeError("db caida")

        monkeypatch.setattr(sm_module.repository, "update_device_status", explota)
        worker._report_status(True, "Conectado")  # no debe propagar

        # La DB se recupera: como la escritura anterior fallo, el estado no
        # quedo marcado como persistido y el proximo reporte SI escribe.
        calls: list[tuple[int, str]] = []
        monkeypatch.setattr(
            sm_module.repository,
            "update_device_status",
            lambda device_id, status: calls.append((device_id, status)),
        )
        worker._report_status(True, "Conectado")
        assert calls == [(7, "online")]


# --- Dobles para ejercitar StreamWorker.run(), que hasta la Fase 5 no tenia
# --- una sola linea de cobertura (no habia fake de cv2.VideoCapture).


def _frame(value: int) -> np.ndarray:
    return np.full((64, 64, 3), value, dtype=np.uint8)


class FakeCapture:
    """Doble scripteable de cv2.VideoCapture: no abre ningun socket."""

    def __init__(self, frames: list | None = None, opened: bool = True) -> None:
        self._frames = list(frames or [])
        self._opened = opened
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802 - API de cv2
        return self._opened

    def read(self):
        if not self._frames:
            return False, None
        return True, self._frames.pop(0)

    def release(self) -> None:
        self.released = True


class FakeStopEvent:
    """threading.Event de mentira que corta el loop despues de N esperas y
    deja registrado cuanto se pidio esperar en cada una."""

    def __init__(self, stop_after_waits: int) -> None:
        self.waits: list[float] = []
        self._stop_after = stop_after_waits
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout)
        if len(self.waits) >= self._stop_after:
            self._set = True
        return self._set


@pytest.fixture()
def worker_aislado(monkeypatch):
    """StreamWorker real con la DB y el jitter fuera del camino."""
    monkeypatch.setattr(sm_module.repository, "update_device_status", lambda *_a: None)
    monkeypatch.setattr(sm_module, "RECONNECT_JITTER", 0.0)
    return sm_module.StreamWorker(_device(5))


def _capturas(monkeypatch, caps: list[FakeCapture]) -> list[FakeCapture]:
    """Encola un FakeCapture por intento de conexion."""
    cola = list(caps)
    entregadas: list[FakeCapture] = []

    def factory(*_args):
        cap = cola.pop(0) if cola else FakeCapture(opened=False)
        entregadas.append(cap)
        return cap

    monkeypatch.setattr(sm_module.cv2, "VideoCapture", factory)
    return entregadas


class TestBackoff:
    """B5: RECONNECT_DELAY_S era 3.0 fijo y para siempre. Contra una camara
    apagada de verdad eso son cientos de aperturas de socket por hora y por
    camara, todas condenadas a fallar."""

    def test_la_curva_duplica_hasta_el_tope(self):
        curva = [sm_module._reconnect_delay(i, jitter=False) for i in range(1, 7)]
        assert curva == [3.0, 6.0, 12.0, 24.0, 30.0, 30.0]

    def test_nunca_pasa_del_tope(self):
        assert sm_module._reconnect_delay(50, jitter=False) == sm_module.RECONNECT_MAX_DELAY_S

    def test_el_jitter_queda_dentro_del_rango_declarado(self):
        """Hasta +25% sobre la base, nunca por debajo: N camaras que se caen
        juntas (un switch que se reinicia) no deben reintentar en fase."""
        base = sm_module._reconnect_delay(2, jitter=False)
        muestras = [sm_module._reconnect_delay(2) for _ in range(200)]

        assert all(base <= d <= base * (1 + sm_module.RECONNECT_JITTER) for d in muestras)
        assert len(set(muestras)) > 1  # de verdad hay jitter


class TestReconexion:
    def test_fallar_al_abrir_escala_el_backoff(self, worker_aislado, monkeypatch):
        _capturas(monkeypatch, [FakeCapture(opened=False) for _ in range(3)])
        worker_aislado._stop_event = FakeStopEvent(stop_after_waits=3)

        worker_aislado.run()

        assert worker_aislado._stop_event.waits == [3.0, 6.0, 12.0]

    def test_una_conexion_con_frames_resetea_el_backoff(self, worker_aislado, monkeypatch):
        _capturas(
            monkeypatch,
            [
                FakeCapture(opened=False),
                FakeCapture(frames=[_frame(10), _frame(200)]),  # conecta y entrega
                FakeCapture(opened=False),
            ],
        )
        worker_aislado._stop_event = FakeStopEvent(stop_after_waits=3)

        worker_aislado.run()

        # 3s, se recupera -> vuelve a 3s, y recien despues escala.
        assert worker_aislado._stop_event.waits == [3.0, 3.0, 6.0]

    def test_abrir_sin_entregar_frames_no_resetea_el_backoff(self, worker_aislado, monkeypatch):
        """Una camara que abre el socket y se muere al instante (firmware
        colgado, NVR saturado) no puede quedarse para siempre en el delay
        minimo."""
        _capturas(
            monkeypatch,
            [
                FakeCapture(opened=False),
                FakeCapture(frames=[]),  # abre pero read() falla de entrada
                FakeCapture(opened=False),
            ],
        )
        worker_aislado._stop_event = FakeStopEvent(stop_after_waits=3)

        worker_aislado.run()

        assert worker_aislado._stop_event.waits == [3.0, 6.0, 12.0]

    def test_cada_intento_libera_su_capture(self, worker_aislado, monkeypatch):
        caps = _capturas(monkeypatch, [FakeCapture(opened=False), FakeCapture(frames=[_frame(1)])])
        worker_aislado._stop_event = FakeStopEvent(stop_after_waits=2)

        worker_aislado.run()

        assert all(cap.released for cap in caps)


class TestWatchdogDeCongelado:
    """B5: un decoder colgado devuelve ok=True con el MISMO frame, asi que el
    corte por read() fallido no llega nunca y la camara queda mostrando una
    foto vieja que parece en vivo."""

    def test_la_firma_distingue_frames(self):
        assert sm_module._frame_signature(_frame(10)) == sm_module._frame_signature(_frame(10))
        assert sm_module._frame_signature(_frame(10)) != sm_module._frame_signature(_frame(200))

    def test_el_mismo_frame_repetido_corta_el_stream(self, worker_aislado, monkeypatch):
        monkeypatch.setattr(sm_module, "FROZEN_STREAM_S", 0.0)
        congelado = _frame(70)
        cap = FakeCapture(frames=[congelado] * 6)

        entrego, frozen = worker_aislado._capture_loop(cap)

        assert (entrego, frozen) == (True, True)
        # Corto antes de agotar los 6 frames: no espero al EOF.
        assert cap._frames

    def test_frames_que_cambian_no_disparan_el_watchdog(self, worker_aislado, monkeypatch):
        monkeypatch.setattr(sm_module, "FROZEN_STREAM_S", 0.0)
        cap = FakeCapture(frames=[_frame(i * 30) for i in range(1, 7)])

        entrego, frozen = worker_aislado._capture_loop(cap)

        assert (entrego, frozen) == (True, False)
        assert not cap._frames  # leyo hasta el EOF

    def test_un_frame_repetido_por_debajo_del_umbral_se_tolera(self, worker_aislado, monkeypatch):
        """Una escena estatica corta (o un mp4 en loop del rig de demo) no
        puede cortar un stream sano: hace falta el umbral de tiempo."""
        monkeypatch.setattr(sm_module, "FROZEN_STREAM_S", 60.0)
        cap = FakeCapture(frames=[_frame(70)] * 6)

        assert worker_aislado._capture_loop(cap) == (True, False)

    def test_el_congelado_reconecta_y_lo_dice(self, worker_aislado, monkeypatch):
        monkeypatch.setattr(sm_module, "FROZEN_STREAM_S", 0.0)
        estados: list[tuple[bool, str]] = []
        monkeypatch.setattr(
            worker_aislado,
            "_report_status",
            lambda online, detail: estados.append((online, detail)),
        )
        _capturas(monkeypatch, [FakeCapture(frames=[_frame(70)] * 5)])
        worker_aislado._stop_event = FakeStopEvent(stop_after_waits=1)

        worker_aislado.run()

        assert estados[0] == (True, "Conectado")
        assert "congelado" in estados[1][1]
        # Entrego frames: el backoff arranca de cero, no castiga un glitch.
        assert worker_aislado._stop_event.waits == [3.0]


class TestParadaOrdenada:
    def test_stop_durante_un_backoff_largo_no_espera_el_tope(self, monkeypatch):
        """El apagado de main.py hace stop_all() + join(timeout=2.0): con un
        backoff de hasta 30s, el hilo tiene que salir en el acto igual."""
        monkeypatch.setattr(sm_module.repository, "update_device_status", lambda *_a: None)
        monkeypatch.setattr(sm_module, "RECONNECT_DELAY_S", 30.0)
        monkeypatch.setattr(sm_module, "RECONNECT_MAX_DELAY_S", 30.0)
        intentos = _capturas(monkeypatch, [])  # todo falla al abrir

        worker = sm_module.StreamWorker(_device(9))
        worker.start()
        try:
            deadline = time.monotonic() + 2.0
            while not intentos and time.monotonic() < deadline:
                time.sleep(0.01)
            assert intentos, "el worker nunca intentó conectar"

            arranque = time.monotonic()
            worker.stop()
            worker.join(timeout=2.0)
        finally:
            worker.stop()

        assert not worker.is_alive()
        assert time.monotonic() - arranque < 2.0


class TestFirmaDeFrame:
    def test_es_barata_sobre_un_frame_grande(self):
        """Corre por cada frame de cada camara: no puede costar nada."""
        frame = np.random.default_rng(0).integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
        arranque = time.perf_counter()
        for _ in range(100):
            sm_module._frame_signature(frame)
        assert (time.perf_counter() - arranque) / 100 < 0.002
