"""Fase 5 (2026-09-24): hilos que mueren a la vista, y nada queda a medias.

- StreamWorker: un error en una conexion reconecta en vez de matar el hilo;
  si igual muere, queda marcado y acquire() lo reemplaza; no informa estado
  despues de stop().
- AnalyticsWorker: acquire() dentro del try (A14); un hilo muerto no cuenta
  como corriendo.
- Sesiones ONNX: no se fugan si la construccion falla (A15); la clave es el
  archivo, no el texto de la ruta.
- clip_recorder: un mp4 que no se pudo indexar no queda huerfano.
- AlarmEngine: en que hilo corre de verdad, eventos encolados tras stop(),
  cooldown atomico.
- excepthooks: lo no capturado va al log.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from sqlalchemy.exc import IntegrityError

import aurea_vms.core.alarm_engine as alarm_engine_mod
import aurea_vms.core.analytics_engine as ae_module
import aurea_vms.core.stream_manager as sm_module
import aurea_vms.main as main_module
from aurea_vms.core import clip_recorder, media_store
from aurea_vms.core.alarm_engine import AlarmEngine
from aurea_vms.core.analytics import object_detector_backend as odb
from aurea_vms.core.analytics import pose_backend
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import Detection, DetectionEvent
from aurea_vms.models.alarm_rule import AlarmRule
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.models.device import Device


def _device(device_id: int = 5) -> Device:
    device = Device(
        name="Cam", ip="10.0.0.10", rtsp_main_url="rtsp://cam/main", username="", password=""
    )
    device.id = device_id
    return device


class Captura:
    """Doble de cv2.VideoCapture que entrega frames y despues, si se pide,
    explota a mitad de la lectura."""

    def __init__(self, frames: int = 1, explota: bool = False) -> None:
        self._frames = frames
        self._explota = explota
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802 - API de cv2
        return True

    def read(self):
        if self._frames:
            self._frames -= 1
            return True, np.random.randint(0, 255, (32, 32, 3), dtype=np.uint8)
        if self._explota:
            raise cv2.error("backend roto a mitad del stream")
        return False, None

    def release(self) -> None:
        self.released = True


@pytest.fixture()
def sin_db(monkeypatch):
    persistidos: list[tuple] = []
    monkeypatch.setattr(
        sm_module.repository, "update_device_status", lambda *a: persistidos.append(a)
    )
    monkeypatch.setattr(sm_module, "RECONNECT_JITTER", 0.0)
    monkeypatch.setattr(sm_module, "RECONNECT_DELAY_S", 0.01)
    return persistidos


class TestStreamWorker:
    def test_un_error_a_mitad_de_la_captura_reconecta_y_suelta_la_captura(
        self, sin_db, monkeypatch
    ):
        capturas = [Captura(explota=True), Captura(frames=50)]
        abiertas: list[Captura] = []

        def abrir(*_a):
            cap = capturas.pop(0) if capturas else Captura(frames=0)
            abiertas.append(cap)
            return cap

        monkeypatch.setattr(sm_module.cv2, "VideoCapture", abrir)
        worker = sm_module.StreamWorker(_device())

        worker.start()
        limite = time.monotonic() + 3
        while len(abiertas) < 2 and time.monotonic() < limite:
            time.sleep(0.01)
        vivo = worker.is_alive()
        worker.stop()
        worker.join(2)

        assert vivo, "el error de captura mato el hilo"
        assert len(abiertas) >= 2  # reconecto
        assert abiertas[0].released  # y no fugo la captura que exploto
        assert not worker.murio

    def test_si_el_hilo_muere_igual_queda_marcado_y_offline(self, sin_db, monkeypatch):
        estados: list = []
        monkeypatch.setattr(
            sm_module,
            "event_bus",
            SimpleNamespace(device_status=SimpleNamespace(emit=estados.append)),
        )
        worker = sm_module.StreamWorker(_device())
        monkeypatch.setattr(worker, "_run", lambda: 1 / 0)

        worker.run()  # no propaga

        assert worker.murio
        assert estados and not estados[-1].online
        assert sin_db == [(5, "offline")]

    def test_no_informa_estado_despues_de_stop(self, sin_db, monkeypatch):
        estados: list = []
        monkeypatch.setattr(
            sm_module,
            "event_bus",
            SimpleNamespace(device_status=SimpleNamespace(emit=estados.append)),
        )
        worker = sm_module.StreamWorker(_device())
        worker.stop()

        worker._report_status(False, "Se perdió la conexión")

        assert estados == []
        assert sin_db == []


class WorkerFalso:
    def __init__(self, device, kind="main") -> None:
        self.device_id = device.id
        self.kind = kind
        self.murio = False
        self.iniciado = False
        self.parado = False

    def start(self) -> None:
        self.iniciado = True

    def stop(self) -> None:
        self.parado = True

    def join(self, timeout=None) -> None:
        pass


class TestStreamManagerReemplazaAlMuerto:
    def test_acquire_no_devuelve_el_cadaver(self, monkeypatch):
        monkeypatch.setattr(sm_module, "StreamWorker", WorkerFalso)
        manager = sm_module.StreamManager()
        primero = manager.acquire(_device())
        primero.murio = True

        segundo = manager.acquire(_device())

        assert segundo is not primero and segundo.iniciado
        assert manager.get_worker(5) is segundo

    def test_el_reemplazo_conserva_las_referencias(self, monkeypatch):
        """Dos consumidores tomaron el worker; muere; un tercero lo pide. Al
        soltarse los tres, el nuevo se detiene: no antes."""
        monkeypatch.setattr(sm_module, "StreamWorker", WorkerFalso)
        manager = sm_module.StreamManager()
        manager.acquire(_device())
        manager.acquire(_device()).murio = True
        nuevo = manager.acquire(_device())

        manager.release(5)
        manager.release(5)
        assert not nuevo.parado
        manager.release(5)
        assert nuevo.parado


class AnalizadorFalso:
    def __init__(self) -> None:
        self.cerrado = False

    def process_frame(self, frame, timestamp):
        raise AssertionError("no deberia llegar a analizar")

    def close(self) -> None:
        self.cerrado = True


def _config() -> AnalyticsConfig:
    config = AnalyticsConfig(device_id=5, analyzer_name="people_counting", params={})
    config.id = 9
    return config


@pytest.fixture()
def analizador(monkeypatch) -> AnalizadorFalso:
    falso = AnalizadorFalso()
    monkeypatch.setattr(ae_module, "create_analyzer", lambda _config: falso)
    return falso


class TestAnalyticsWorker:
    def test_si_acquire_falla_el_analizador_igual_se_cierra(self, analizador, monkeypatch):
        """A14: acquire() estaba antes del try y su falla salteaba el finally:
        la sesion ONNX quedaba tomada."""
        soltados: list = []

        def acquire_roto(*_a):
            raise RuntimeError("no se pudo crear el StreamWorker")

        monkeypatch.setattr(ae_module.stream_manager, "acquire", acquire_roto)
        monkeypatch.setattr(ae_module.stream_manager, "release", lambda *a: soltados.append(a))
        worker = ae_module.AnalyticsWorker(_config(), _device())

        worker.run()

        assert analizador.cerrado
        assert soltados == []  # no se suelta lo que nunca se tomo

    def test_si_el_hilo_muere_no_cuenta_como_corriendo(self, analizador, monkeypatch):
        soltados: list = []
        monkeypatch.setattr(ae_module.stream_manager, "acquire", lambda *a: None)
        monkeypatch.setattr(ae_module.stream_manager, "release", lambda *a: soltados.append(a))

        def get_worker_roto(*_a):
            raise RuntimeError("se rompio leyendo el frame")

        monkeypatch.setattr(ae_module.stream_manager, "get_worker", get_worker_roto)
        engine = ae_module.AnalyticsEngine()

        engine.start(_config(), _device())
        engine._workers[9].join(2)

        assert not engine.is_running(9)
        assert analizador.cerrado
        assert soltados == [(5, "main")]


class SesionFalsa:
    def __init__(self, rota: bool = False) -> None:
        self._rota = rota

    def get_inputs(self):
        if self._rota:
            raise RuntimeError("modelo corrupto")
        return [SimpleNamespace(name="images")]


@pytest.fixture()
def sesiones(monkeypatch, tmp_path):
    """Sesiones ONNX de mentira sobre un archivo real (para resolve())."""
    modelo = tmp_path / "modelo.onnx"
    modelo.write_bytes(b"x")
    rota = {"valor": False}
    monkeypatch.setattr(odb, "_crear_sesion", lambda _ruta: SesionFalsa(rota["valor"]))
    monkeypatch.setattr(odb, "_ensure_model", lambda: str(modelo))
    monkeypatch.setattr(pose_backend, "_ensure_model", lambda: str(modelo))
    yield SimpleNamespace(modelo=modelo, rota=rota)
    odb._sesiones.clear()


class TestSesionesOnnx:
    def test_yolox_que_falla_al_construirse_suelta_la_sesion(self, sesiones):
        sesiones.rota["valor"] = True

        with pytest.raises(RuntimeError):
            odb.YoloxDetector()

        assert odb.sesiones_vivas() == {}

    def test_pose_que_falla_al_construirse_suelta_la_sesion(self, sesiones):
        sesiones.rota["valor"] = True

        with pytest.raises(RuntimeError):
            pose_backend.PoseEstimator()

        assert odb.sesiones_vivas() == {}

    def test_una_linea_mal_formada_no_toma_la_sesion(self, sesiones):
        from aurea_vms.core.analytics.line_crossing_analyzer import LineCrossingAnalyzer

        with pytest.raises((TypeError, ValueError)):
            LineCrossingAnalyzer(line=[(0, 0)])

        assert odb.sesiones_vivas() == {}

    def test_la_misma_ruta_escrita_distinto_es_una_sola_sesion(self, sesiones, monkeypatch):
        relativa = Path("..") / sesiones.modelo.parent.name / sesiones.modelo.name
        monkeypatch.chdir(sesiones.modelo.parent)

        odb.adquirir_sesion(str(sesiones.modelo))
        odb.adquirir_sesion(str(relativa))

        assert odb.sesiones_vivas() == {str(sesiones.modelo.resolve()): 2}
        odb.soltar_sesion(str(relativa))
        odb.soltar_sesion(str(sesiones.modelo))
        assert odb.sesiones_vivas() == {}


@pytest.fixture()
def media_en_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(media_store, "settings", SimpleNamespace(media_dir=tmp_path / "media"))
    return tmp_path / "media"


def _jpeg() -> bytes:
    ok, buf = cv2.imencode(".jpg", np.full((120, 160, 3), 40, dtype=np.uint8))
    assert ok
    return buf.tobytes()


class TestClipHuerfano:
    @pytest.mark.parametrize(
        "error",
        [
            IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed")),
            RuntimeError("database is locked"),
        ],
    )
    def test_si_no_se_puede_indexar_el_mp4_se_borra(self, media_en_tmp, monkeypatch, error):
        def register_roto(*_a, **_k):
            raise error

        monkeypatch.setattr(media_store, "register", register_roto)

        assert clip_recorder._write_mp4(1, 1, [(0.0, _jpeg()), (0.1, _jpeg())]) is None
        assert list(media_en_tmp.rglob("*.mp4")) == []


def _regla(cooldown: int = 30) -> AlarmRule:
    regla = AlarmRule(
        analyzer_name="people_counting",
        object_classes=[],
        min_confidence=0.5,
        cooldown_seconds=cooldown,
        schedule_days=[],
        schedule_start=None,
        schedule_end=None,
        actions={},
        enabled=True,
    )
    regla.id = 1
    return regla


def _evento() -> DetectionEvent:
    return DetectionEvent(
        device_id=1,
        analyzer_name="people_counting",
        timestamp=0.0,
        detections=(Detection("person", 0.9, (0, 0, 10, 10)),),
    )


class TestAlarmEngineEnQueHilo:
    def test_on_detection_corre_en_el_hilo_de_la_gui(self, qapp, monkeypatch):
        """Fija el hecho, no una preferencia: PySide6 encola la entrega a un
        callable comun emitido desde otro hilo. Si esto cambia (o se muda el
        motor a su propio hilo), este test tiene que cambiar a proposito."""
        hilos: list[str] = []
        monkeypatch.setattr(
            alarm_engine_mod.repository,
            "list_alarm_rules_for",
            lambda *_a: hilos.append(threading.current_thread().name) or [],
        )
        motor = AlarmEngine()
        motor.start()
        try:
            emisor = threading.Thread(
                target=lambda: event_bus.detection.emit(_evento()), name="AnalyticsWorker-9"
            )
            emisor.start()
            emisor.join()
            assert hilos == []  # encolado: no corrio en el hilo que emitio
            qapp.processEvents()
        finally:
            motor.stop()

        assert hilos == [threading.main_thread().name]

    def test_los_eventos_encolados_antes_del_stop_no_disparan(self, qapp, monkeypatch):
        leidas: list = []
        monkeypatch.setattr(
            alarm_engine_mod.repository, "list_alarm_rules_for", lambda *a: leidas.append(a) or []
        )
        motor = AlarmEngine()
        motor.start()
        emisor = threading.Thread(target=lambda: event_bus.detection.emit(_evento()))
        emisor.start()
        emisor.join()

        motor.stop()
        qapp.processEvents()

        assert leidas == []


class TestCooldownAtomico:
    def test_dos_eventos_a_la_vez_disparan_una_sola_alarma(self, monkeypatch):
        """Sin el lock, los dos pasaban el chequeo del cooldown antes de que
        cualquiera lo marcara."""
        regla = _regla()
        barrera = threading.Barrier(2)

        def reglas(*_a):
            barrera.wait(2)  # los dos llegan juntos al chequeo
            return [regla]

        monkeypatch.setattr(alarm_engine_mod.repository, "list_alarm_rules_for", reglas)
        disparos: list = []
        motor = AlarmEngine()

        def disparo_lento(*args):
            time.sleep(0.05)
            disparos.append(args)

        monkeypatch.setattr(motor, "_trigger", disparo_lento)
        hilos = [threading.Thread(target=motor._on_detection, args=(_evento(),)) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()

        assert len(disparos) == 1

    def test_un_disparo_fallido_devuelve_la_reserva(self, monkeypatch):
        regla = _regla()
        monkeypatch.setattr(
            alarm_engine_mod.repository, "list_alarm_rules_for", lambda *_a: [regla]
        )
        motor = AlarmEngine()
        intentos: list = []

        def disparo_que_falla_una_vez(*args):
            intentos.append(args)
            if len(intentos) == 1:
                raise RuntimeError("database is locked")

        monkeypatch.setattr(motor, "_trigger", disparo_que_falla_una_vez)

        motor._on_detection(_evento())
        motor._on_detection(_evento())

        assert len(intentos) == 2  # el segundo no quedo frenado por el cooldown
        assert 1 in motor._last_triggered


class TestExcepthooks:
    @pytest.fixture()
    def hooks(self):
        """Los hooks previos (los de pytest y pytest-qt, que fallan el test
        si los llaman) se reemplazan por no-ops: el nuestro los encadena."""
        antes = threading.excepthook, sys.excepthook
        threading.excepthook = lambda _args: None
        sys.excepthook = lambda *_a: None
        main_module._instalar_excepthooks()
        yield
        threading.excepthook, sys.excepthook = antes

    def test_una_excepcion_en_un_hilo_va_al_log(self, hooks, caplog):
        def revienta():
            raise ValueError("error sin capturar en un hilo")

        with caplog.at_level(logging.CRITICAL, logger="aurea_vms.excepciones"):
            hilo = threading.Thread(target=revienta, name="HiloDePrueba")
            hilo.start()
            hilo.join()

        (registro,) = [r for r in caplog.records if r.name == "aurea_vms.excepciones"]
        assert "HiloDePrueba" in registro.getMessage()
        assert registro.exc_info[0] is ValueError

    def test_una_excepcion_del_proceso_va_al_log(self, hooks, caplog, monkeypatch):
        with caplog.at_level(logging.CRITICAL, logger="aurea_vms.excepciones"):
            try:
                raise KeyError("fuera de Qt")
            except KeyError:
                sys.excepthook(*sys.exc_info())

        assert any(r.exc_info and r.exc_info[0] is KeyError for r in caplog.records)
