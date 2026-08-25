from __future__ import annotations

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
        import threading

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
        import time

        worker = sm_module.StreamWorker(_device(1))
        worker._latest_frame_ts = time.monotonic()
        assert not worker.is_stale()
