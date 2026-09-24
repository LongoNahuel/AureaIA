"""Fase 6 (2026-09-24): el watchdog de congelado mira el PTS.

Medido con el rig (sesiones/2026-09-24.md): una camara sana con escena
quieta y un codec sin ruido entrega cuadros identicos durante ~20 s, pero
su PTS avanza en todos. Congelado = PTS quieto Y la misma firma. Y un corte
del watchdog no persiste "offline": reconecta enseguida.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

import aurea_vms.core.stream_manager as sm_module
from aurea_vms.models.device import Device


def _frame(valor: int) -> np.ndarray:
    return np.full((64, 64, 3), valor, dtype=np.uint8)


class CapturaConPts:
    """Doble de cv2.VideoCapture que tambien reporta CAP_PROP_POS_MSEC."""

    def __init__(self, cuadros: list[tuple[np.ndarray, float]]) -> None:
        self._cuadros = list(cuadros)
        self._pts = 0.0
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802 - API de cv2
        return True

    def read(self):
        if not self._cuadros:
            return False, None
        frame, self._pts = self._cuadros.pop(0)
        return True, frame

    def get(self, prop):
        assert prop == cv2.CAP_PROP_POS_MSEC
        return self._pts

    def release(self) -> None:
        self.released = True


@pytest.fixture()
def worker(monkeypatch):
    persistidos: list[tuple] = []
    monkeypatch.setattr(
        sm_module.repository, "update_device_status", lambda *a: persistidos.append(a)
    )
    monkeypatch.setattr(sm_module, "RECONNECT_JITTER", 0.0)
    # Umbral 0: cualquier cuadro "quieto" despues del primero dispara el
    # watchdog, asi el test no depende de relojes.
    monkeypatch.setattr(sm_module, "FROZEN_STREAM_S", 0.0)
    device = Device(name="Cam", ip="10.0.0.10", rtsp_main_url="rtsp://cam/main", password="")
    device.id = 5
    w = sm_module.StreamWorker(device)
    w.persistidos = persistidos
    return w


class TestCriterio:
    def test_cuadros_identicos_con_pts_que_avanza_no_es_congelado(self, worker):
        """El falso positivo medido (cam7 del rig): escena quieta, codec sin
        ruido, camara sana."""
        quieto = _frame(70)
        cap = CapturaConPts([(quieto, 40.0 * i) for i in range(8)])

        assert worker._capture_loop(cap) == (True, False)
        assert not cap._cuadros  # leyo todo, no corto

    def test_mismo_cuadro_y_mismo_pts_es_congelado(self, worker):
        """El decoder colgado: devuelve la misma foto, con el mismo PTS."""
        quieto = _frame(70)
        cap = CapturaConPts([(quieto, 1000.0)] * 8)

        assert worker._capture_loop(cap) == (True, True)
        assert cap._cuadros  # corto antes del EOF

    def test_sin_pts_el_criterio_es_la_firma_como_antes(self, worker):
        """Un backend que no reporta PTS (queda fijo en 0)."""
        quieto = _frame(70)
        cap = CapturaConPts([(quieto, 0.0)] * 8)

        assert worker._capture_loop(cap) == (True, True)

    def test_cuadros_que_cambian_con_pts_quieto_no_es_congelado(self, worker):
        cap = CapturaConPts([(_frame(20 * i), 0.0) for i in range(1, 8)])

        assert worker._capture_loop(cap) == (True, False)

    def test_un_pts_que_vuelve_atras_cuenta_como_vida(self, worker):
        """La camara reinicio su reloj: no es un congelado."""
        quieto = _frame(70)
        cap = CapturaConPts([(quieto, 5000.0), (quieto, 40.0), (quieto, 80.0), (quieto, 120.0)])

        assert worker._capture_loop(cap) == (True, False)


class FakeStopEvent:
    def __init__(self, cortar_tras: int) -> None:
        self.waits: list = []
        self._cortar_tras = cortar_tras
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def wait(self, timeout=None) -> bool:
        self.waits.append(timeout)
        if len(self.waits) >= self._cortar_tras:
            self._set = True
        return self._set


class Cerrada:
    def isOpened(self) -> bool:  # noqa: N802 - API de cv2
        return False

    def release(self) -> None:
        pass


def _capturas(monkeypatch, caps: list) -> None:
    cola = list(caps)
    monkeypatch.setattr(
        sm_module.cv2, "VideoCapture", lambda *_a: cola.pop(0) if cola else Cerrada()
    )


class TestNoPersisteOfflinePorElWatchdog:
    def test_un_corte_del_watchdog_avisa_a_la_ui_pero_no_escribe_offline(self, worker, monkeypatch):
        _capturas(monkeypatch, [CapturaConPts([(_frame(70), 1000.0)] * 5)])
        worker._stop_event = FakeStopEvent(cortar_tras=1)

        worker.run()

        assert worker.persistidos == [(5, "online")]  # el "Conectado"; el corte no

    def test_si_la_reconexion_falla_ahi_si_se_persiste_offline(self, worker, monkeypatch):
        _capturas(monkeypatch, [CapturaConPts([(_frame(70), 1000.0)] * 5), Cerrada()])
        worker._stop_event = FakeStopEvent(cortar_tras=2)

        worker.run()

        assert worker.persistidos == [(5, "online"), (5, "offline")]

    def test_una_conexion_perdida_sigue_persistiendo_offline(self, worker, monkeypatch):
        """Control: solo el corte del watchdog deja de persistir."""
        cambiantes = [(_frame(20 * i), 40.0 * i) for i in range(1, 4)]
        _capturas(monkeypatch, [CapturaConPts(cambiantes)])
        worker._stop_event = FakeStopEvent(cortar_tras=1)

        worker.run()

        assert worker.persistidos == [(5, "online"), (5, "offline")]
