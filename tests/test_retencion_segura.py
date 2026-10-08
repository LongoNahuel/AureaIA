"""Retencion que no borra evidencia sin que alguien lo decida (2026-10-07).

Motivo: la base de desarrollo de Daniel perdio 190 archivos (167,6 MB) al
arrancar despues de 39 dias sin correr. Sin retencion configurada valian 7
dias y 5 GB en silencio, y la primera pasada borro todo lo vencido de golpe.
A un casino que actualiza o prende una PC apagada le pasaba lo mismo.
"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import pytest

from aurea_vms.core import app_prefs, media_store, retention
from aurea_vms.models import repository
from aurea_vms.models.alarm_event import (
    STATUS_ACKNOWLEDGED,
    STATUS_INVESTIGATING,
    STATUS_NEW,
    STATUS_RESOLVED,
)

AHORA = 1_000_000_000.0
DIA = 86400.0


@pytest.fixture(autouse=True)
def _aislado(tmp_path, monkeypatch):
    monkeypatch.setattr(media_store, "settings", SimpleNamespace(media_dir=tmp_path / "media"))
    monkeypatch.setattr(app_prefs, "_PREFS_PATH", tmp_path / "preferences.json")


@pytest.fixture()
def camara(temp_db):
    return repository.add_device(name="Cam", ip="10.0.0.9", rtsp_main_url="rtsp://c/x")


def _media(device_id: int, ts: float, *, alarma: int | None = None, size: int = 1000):
    rel = media_store.build_rel_path("clip", device_id, int(ts * 1000), ts, ".bin")
    media_store.prepare_path(rel).write_bytes(b"x" * size)
    return repository.add_media_asset(
        kind="clip",
        device_id=device_id,
        timestamp=ts,
        rel_path=rel,
        size_bytes=size,
        alarm_event_id=alarma,
    )


def _alarma(device_id: int, estado: str) -> int:
    fila = repository.add_alarm_event(
        rule_id=None,
        device_id=device_id,
        timestamp=AHORA - 30 * DIA,
        object_class="person",
        confidence=0.9,
        severity="alto",
    )
    repository.set_alarm_event_status(fila.id, estado)
    return fila.id


def _una_pasada() -> None:
    worker = retention.RetentionWorker(interval_s=60, first_delay_s=0)
    worker.start()
    time.sleep(0.3)
    worker.stop()
    worker.join(2)


def _configurar(dias: float = 7, gb: float = 5) -> None:
    app_prefs.confirmar_retencion(dias, gb)


class TestSinConfigurar:
    def test_el_caso_de_daniel_no_borra_nada(self, camara, caplog):
        """preferences.json con solo el tema, y media de hace 39 dias."""
        app_prefs._PREFS_PATH.write_text('{"theme": "dark"}', encoding="utf-8")
        viejos = [_media(camara.id, time.time() - 39 * DIA + i) for i in range(20)]

        with caplog.at_level(logging.WARNING, logger=retention.__name__):
            _una_pasada()

        assert all(repository.get_media_asset(m.id) is not None for m in viejos)
        assert all(media_store.absolute_path(m.rel_path).exists() for m in viejos)
        assert "Retención sin configurar" in caplog.text

    def test_sin_archivo_de_preferencias_tampoco(self, camara):
        viejo = _media(camara.id, time.time() - 39 * DIA)

        _una_pasada()

        assert repository.get_media_asset(viejo.id) is not None

    def test_con_solo_los_dias_no_poda_por_tamaño(self, camara):
        """Con los dias guardados y el tope no, valian los 5 GB por defecto."""
        app_prefs.set_retention_days(90)
        reciente = _media(camara.id, time.time() - DIA)

        _una_pasada()

        assert repository.get_media_asset(reciente.id) is not None
        assert app_prefs.retencion_configurada() is False

    def test_configurada_poda(self, camara):
        _configurar(dias=7)
        viejo = _media(camara.id, time.time() - 39 * DIA)

        _una_pasada()

        assert repository.get_media_asset(viejo.id) is None
        assert app_prefs.retencion_configurada() is True


class TestEvidenciaProtegida:
    def test_un_incidente_en_investigacion_no_se_poda_por_edad(self, camara):
        protegida = _media(
            camara.id, AHORA - 30 * DIA, alarma=_alarma(camara.id, STATUS_INVESTIGATING)
        )

        retention.prune(max_age_days=7, max_total_gb=100, now=AHORA)

        assert repository.get_media_asset(protegida.id) is not None
        assert media_store.absolute_path(protegida.rel_path).exists()

    @pytest.mark.parametrize("estado", [STATUS_NEW, STATUS_ACKNOWLEDGED, STATUS_RESOLVED])
    def test_los_demas_estados_siguen_la_retencion(self, camara, estado):
        """En la practica nadie resuelve las alarmas (las 104 de la base de
        desarrollo estaban en "nueva"): protegerlas llenaba el disco."""
        otra = _media(camara.id, AHORA - 30 * DIA, alarma=_alarma(camara.id, estado))

        retention.prune(max_age_days=7, max_total_gb=100, now=AHORA)

        assert repository.get_media_asset(otra.id) is None

    def test_la_media_sin_alarma_si(self, camara):
        suelta = _media(camara.id, AHORA - 30 * DIA)

        retention.prune(max_age_days=7, max_total_gb=100, now=AHORA)

        assert repository.get_media_asset(suelta.id) is None

    def test_tampoco_se_poda_por_tamaño_y_se_avisa(self, camara, caplog):
        protegida = _media(
            camara.id,
            AHORA - DIA,
            alarma=_alarma(camara.id, STATUS_INVESTIGATING),
            size=2 * 1024**2,
        )
        suelta = _media(camara.id, AHORA - 2 * DIA, size=1024**2)

        with caplog.at_level(logging.WARNING, logger=retention.__name__):
            stats = retention.prune(max_age_days=365, max_total_gb=1 / 1024, now=AHORA)

        assert repository.get_media_asset(protegida.id) is not None
        assert repository.get_media_asset(suelta.id) is None  # lo que si se podia
        assert stats["deleted"] == 1
        assert "evidencia de incidentes en investigación" in caplog.text


class TestTopePorPasada:
    @pytest.mark.parametrize(
        ("total", "tope"), [(0, 50), (30, 50), (1000, 100), (3000, 300), (100000, 500)]
    )
    def test_el_tope(self, total, tope):
        assert retention.tope_de_la_pasada(total) == tope

    def test_una_pc_apagada_semanas_no_borra_todo_de_golpe(self, camara):
        viejos = [_media(camara.id, AHORA - 40 * DIA + i) for i in range(120)]

        primera = retention.prune(max_age_days=7, max_total_gb=100, now=AHORA)

        assert primera["deleted"] == 50
        assert primera["al_tope"] is True
        # lo mas viejo primero
        assert repository.get_media_asset(viejos[0].id) is None
        assert repository.get_media_asset(viejos[50].id) is not None

        segunda = retention.prune(max_age_days=7, max_total_gb=100, now=AHORA)
        assert segunda["deleted"] == 50

    def test_el_tope_tambien_corta_la_poda_por_tamaño(self, camara):
        for i in range(80):
            _media(camara.id, AHORA - DIA + i, size=1024**2)

        stats = retention.prune(max_age_days=365, max_total_gb=1 / 1024, now=AHORA)

        assert stats["deleted"] == 50
        assert stats["al_tope"] is True

    def test_debajo_del_tope_no_avisa(self, camara):
        for i in range(10):
            _media(camara.id, AHORA - 40 * DIA + i)

        stats = retention.prune(max_age_days=7, max_total_gb=100, now=AHORA)

        assert stats["deleted"] == 10
        assert stats["al_tope"] is False

    def test_el_worker_lo_loguea_en_error(self, camara, caplog):
        _configurar(dias=7)
        for i in range(60):
            _media(camara.id, time.time() - 40 * DIA + i)

        with caplog.at_level(logging.ERROR, logger=retention.__name__):
            _una_pasada()

        assert "llegó al tope" in caplog.text


class TestLasOtrasPreferenciasNoConfiguranLaRetencion:
    def test_cambiar_el_tema_no_graba_los_defaults(self):
        """Cada setter escribia _read(), con los defaults mezclados: cambiar
        el tema dejaba la retencion configurada en 7 dias y 5 GB."""
        app_prefs.set_theme("light")
        app_prefs.set_intelligent_branding_enabled(False)

        assert app_prefs.retencion_configurada() is False
        import json

        assert json.loads(app_prefs._PREFS_PATH.read_text(encoding="utf-8")) == {
            "theme": "light",
            "intelligent_branding": False,
        }


class TestConfirmacion:
    def test_un_archivo_viejo_con_las_claves_del_bug_no_poda(self, camara, caplog):
        """Lo que dejaba el bug: el tema mas los defaults de retencion, sin
        que nadie los eligiera. Una instalacion asi queda sin configurar."""
        app_prefs._PREFS_PATH.write_text(
            '{"theme": "dark", "retention_days": 7, "retention_max_gb": 5.0}', encoding="utf-8"
        )
        viejo = _media(camara.id, time.time() - 39 * DIA)

        with caplog.at_level(logging.WARNING, logger=retention.__name__):
            _una_pasada()

        assert repository.get_media_asset(viejo.id) is not None
        assert "Retención sin configurar" in caplog.text

    def test_confirmar_graba_las_tres_cosas_juntas(self):
        app_prefs.set_theme("light")
        app_prefs.confirmar_retencion(90, 500)

        assert app_prefs.leer_retencion() == (90.0, 500.0)
        assert app_prefs.get_theme() == "light"

    @pytest.mark.parametrize(("dias", "gb"), [(0, 5), (7, 0.1)])
    def test_confirmar_fuera_de_rango_no_escribe(self, dias, gb):
        with pytest.raises(ValueError):
            app_prefs.confirmar_retencion(dias, gb)

        assert not app_prefs._PREFS_PATH.exists()

    def test_los_setters_sueltos_no_confirman(self):
        app_prefs.set_retention_days(90)
        app_prefs.set_retention_max_gb(500)

        assert app_prefs.retencion_configurada() is False

    def test_con_la_marca_y_sin_el_tope_no_poda_ni_rompe(self, camara):
        """Un archivo editado a mano: sin la clave, leer_retencion levantaba
        KeyError, que el worker no captura, y se llevaba puesto el hilo."""
        app_prefs._PREFS_PATH.write_text(
            '{"retention_days": 7, "retention_confirmada": "2026-10-07"}', encoding="utf-8"
        )

        assert app_prefs.retencion_configurada() is False
        with pytest.raises(app_prefs.RetencionSinConfigurar):
            app_prefs.leer_retencion()
