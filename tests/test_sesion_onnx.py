"""Tests de la sesión ONNX compartida.

Cada `YoloxDetector` creaba su propia `InferenceSession` del modelo de 20 MB:
con N cámaras × 2 analíticas (conteo y cruce de línea comparten este backend)
eran 2N copias del mismo archivo en RAM. Y el `close()` del contrato de
`Analyzer` era un hook vacío que **ningún** analizador implementaba, así que
el modelo quedaba vivo hasta que lo juntara el GC.

Sin modelo: se reemplaza `_crear_sesion` por un doble. Lo que se prueba es el
refcount, no onnxruntime.
"""

from __future__ import annotations

import threading

import pytest

import aurea_vms.core.analytics.object_detector_backend as odb


class SesionFalsa:
    def __init__(self, ruta: str) -> None:
        self.ruta = ruta

    def get_inputs(self):
        return [type("E", (), {"name": "images"})()]


@pytest.fixture(autouse=True)
def sesiones_falsas(monkeypatch):
    """La caché es un global de módulo: no puede filtrarse entre tests."""
    creadas: list[str] = []

    def crear(ruta):
        creadas.append(ruta)
        return SesionFalsa(ruta)

    monkeypatch.setattr(odb, "_sesiones", {})
    monkeypatch.setattr(odb, "_crear_sesion", crear)
    monkeypatch.setattr(odb, "_ensure_model", lambda: "/modelos/yolox.onnx")
    return creadas


class TestRefcount:
    def test_dos_detectores_comparten_una_sola_sesion(self, sesiones_falsas):
        primero, segundo = odb.YoloxDetector(), odb.YoloxDetector()

        assert primero._session is segundo._session
        assert len(sesiones_falsas) == 1
        assert odb.sesiones_vivas() == {"/modelos/yolox.onnx": 2}

    def test_cerrar_uno_no_libera_la_sesion(self, sesiones_falsas):
        primero, segundo = odb.YoloxDetector(), odb.YoloxDetector()

        primero.close()

        assert odb.sesiones_vivas() == {"/modelos/yolox.onnx": 1}
        assert segundo._session is not None  # el otro sigue pudiendo inferir

    def test_cerrar_el_ultimo_libera_la_sesion(self, sesiones_falsas):
        primero, segundo = odb.YoloxDetector(), odb.YoloxDetector()

        primero.close()
        segundo.close()

        assert odb.sesiones_vivas() == {}

    def test_despues_de_liberar_se_crea_una_nueva(self, sesiones_falsas):
        odb.YoloxDetector().close()
        odb.YoloxDetector()

        assert len(sesiones_falsas) == 2

    def test_close_es_idempotente(self, sesiones_falsas):
        """Analyzer.close() puede llamarse más de una vez en un apagado
        desprolijo: un doble decremento liberaría una sesión en uso."""
        primero, segundo = odb.YoloxDetector(), odb.YoloxDetector()

        primero.close()
        primero.close()
        primero.close()

        assert odb.sesiones_vivas() == {"/modelos/yolox.onnx": 1}
        assert segundo._session is not None

    def test_soltar_una_sesion_que_no_existe_no_explota(self, sesiones_falsas):
        odb.soltar_sesion("/modelos/inventado.onnx")  # no debe levantar

    def test_modelos_distintos_no_se_pisan(self, sesiones_falsas, monkeypatch):
        primero = odb.YoloxDetector()
        monkeypatch.setattr(odb, "_ensure_model", lambda: "/modelos/otro.onnx")
        segundo = odb.YoloxDetector()

        assert primero._session is not segundo._session
        assert len(odb.sesiones_vivas()) == 2


class TestConcurrencia:
    def test_adquirir_y_soltar_desde_muchos_hilos(self, sesiones_falsas):
        """Los analizadores se construyen desde hilos distintos (cada
        AnalyticsWorker el suyo). Sin lock, dos adquisiciones simultáneas
        crean dos sesiones para el mismo archivo y rompen el conteo."""
        errores: list[Exception] = []
        barrera = threading.Barrier(8)

        def ciclo():
            try:
                barrera.wait()
                for _ in range(100):
                    detector = odb.YoloxDetector()
                    detector.close()
            except Exception as error:  # noqa: BLE001 - se reporta al final
                errores.append(error)

        hilos = [threading.Thread(target=ciclo) for _ in range(8)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()

        assert errores == []
        # Balance perfecto: no puede quedar ninguna sesión viva.
        assert odb.sesiones_vivas() == {}

    def test_los_que_quedan_abiertos_mantienen_la_sesion(self, sesiones_falsas):
        detectores: list[odb.YoloxDetector] = []
        lock = threading.Lock()

        def crear():
            detector = odb.YoloxDetector()
            with lock:
                detectores.append(detector)

        hilos = [threading.Thread(target=crear) for _ in range(8)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()

        assert len(sesiones_falsas) == 1  # una sola, pese a 8 hilos
        assert odb.sesiones_vivas() == {"/modelos/yolox.onnx": 8}


class TestLosAnalizadoresLaSueltan:
    """El contrato de Analyzer.close() ya estaba cableado en
    analytics_engine, pero ninguna implementación lo cumplía."""

    def _config(self, nombre, **params):
        from aurea_vms.models.analytics_config import AnalyticsConfig

        return AnalyticsConfig(
            device_id=1,
            analyzer_name=nombre,
            enabled=True,
            confidence_threshold=0.5,
            object_classes=[],
            params=params,
        )

    @pytest.mark.parametrize(
        ("nombre", "params"),
        [("people_counting", {}), ("line_crossing", {"line": [[0, 0], [10, 10]]})],
    )
    def test_cerrar_el_analizador_suelta_la_sesion(self, sesiones_falsas, nombre, params):
        from aurea_vms.core.analytics.registry import create_analyzer

        analyzer = create_analyzer(self._config(nombre, **params))
        assert odb.sesiones_vivas() == {"/modelos/yolox.onnx": 1}

        analyzer.close()

        assert odb.sesiones_vivas() == {}

    def test_dos_analiticas_de_la_misma_camara_comparten(self, sesiones_falsas):
        from aurea_vms.core.analytics.registry import create_analyzer

        conteo = create_analyzer(self._config("people_counting"))
        cruce = create_analyzer(self._config("line_crossing", line=[[0, 0], [10, 10]]))

        assert len(sesiones_falsas) == 1
        assert odb.sesiones_vivas() == {"/modelos/yolox.onnx": 2}

        conteo.close()
        cruce.close()
        assert odb.sesiones_vivas() == {}
