"""Tests del smoke de empaquetado (aurea_vms/smoke.py).

El smoke es la unica prueba de que el .exe trae lo que carga en runtime.
Estos tests fijan que cada chequeo falla cuando falta lo que dice probar:
un chequeo que pasa siempre es peor que no tenerlo, porque da un build
verde que no probo nada (lo que paso hasta el 23/09).
"""

from __future__ import annotations

import pytest

from aurea_vms import smoke
from aurea_vms.config import resources
from aurea_vms.core import credential_store
from aurea_vms.models import db as db_module


def _cabeza(tmp_path) -> str:
    """La ultima revision de Alembic: no un nombre fijo, que se rompe con
    cada revision nueva."""
    from alembic.script import ScriptDirectory

    config = db_module._config_de_alembic(tmp_path / "cualquiera.sqlite3")
    return ScriptDirectory.from_config(config).get_current_head()


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Clave de cifrado aislada: nunca la del desarrollador."""
    monkeypatch.setattr(credential_store, "_key_path", lambda: tmp_path / "camera_key")
    credential_store.reset_cache()
    yield tmp_path
    credential_store.reset_cache()


class TestMigraciones:
    def test_pasa_con_la_base_en_la_cabeza(self, temp_db, tmp_path):
        assert smoke.check_migraciones(base_nueva=True).startswith(_cabeza(tmp_path))

    def test_falla_si_la_base_quedo_atras(self, temp_db, monkeypatch):
        monkeypatch.setattr(db_module, "revision_actual", lambda _engine: "0005_seguridad")

        with pytest.raises(RuntimeError, match="quedó en 0005_seguridad"):
            smoke.check_migraciones(base_nueva=True)


class TestCifrado:
    def test_ida_y_vuelta_y_no_deja_la_camara(self, temp_db, data_dir):
        from aurea_vms.models import repository

        smoke.check_cifrado()

        assert (data_dir / "camera_key").exists()
        assert repository.list_devices() == []

    def test_falla_si_la_contrasena_queda_en_claro(self, temp_db, data_dir, monkeypatch):
        """Un TypeDecorator que dejara de cifrar pasaria el ida y vuelta:
        por eso el chequeo lee la columna cruda."""
        monkeypatch.setattr(credential_store, "cifrar", lambda valor: valor)

        with pytest.raises(RuntimeError, match="texto plano"):
            smoke.check_cifrado()


class TestOnvif:
    def test_parsea_el_wsdl_sin_red(self):
        assert smoke.check_onvif().endswith("wsdl")

    def test_empaquetado_sin_carpeta_wsdl_falla(self, monkeypatch):
        monkeypatch.setattr(resources, "is_frozen", lambda: True)
        monkeypatch.setattr(resources, "onvif_camera_kwargs", lambda: {})

        with pytest.raises(RuntimeError, match="wsdl"):
            smoke.check_onvif()

    def test_empaquetado_con_wsdl_incompleto_falla(self, tmp_path, monkeypatch):
        (tmp_path / "devicemgmt.wsdl").write_text("<no-es-un-wsdl/>", encoding="utf-8")
        monkeypatch.setattr(resources, "is_frozen", lambda: True)
        monkeypatch.setattr(resources, "onvif_camera_kwargs", lambda: {"wsdl_dir": str(tmp_path)})

        with pytest.raises(RuntimeError, match="DeviceBinding"):
            smoke.check_onvif()

    def test_empaquetado_sin_un_xsd_importado_falla(self, tmp_path, monkeypatch):
        """El caso realista: el spec copia devicemgmt.wsdl pero no algo que
        importa. Sin red, zeep no puede ir a buscarlo."""
        import shutil
        from pathlib import Path

        import onvif

        original = Path(onvif.__file__).resolve().parent.parent / "wsdl"
        copia = tmp_path / "wsdl"
        shutil.copytree(original, copia)
        (copia / "onvif.xsd").unlink()
        monkeypatch.setattr(resources, "is_frozen", lambda: True)
        monkeypatch.setattr(resources, "onvif_camera_kwargs", lambda: {"wsdl_dir": str(copia)})

        with pytest.raises(Exception):  # noqa: B017 - zeep/lxml levantan tipos propios
            smoke.check_onvif()


class TestDescargasProhibidas:
    def test_un_modelo_ausente_falla_en_vez_de_bajarse(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from aurea_vms.core.analytics import model_assets

        monkeypatch.setattr(model_assets, "settings", SimpleNamespace(data_dir=tmp_path))
        monkeypatch.setattr(model_assets.urllib.request, "urlretrieve", None)  # lo restaura
        smoke._prohibir_descargas()

        with pytest.raises(RuntimeError, match="falta en el bundle"):
            model_assets.ensure_model("no_existe.onnx", "http://ejemplo/no_existe.onnx")


class TestConfigDeAlembic:
    def test_una_ruta_con_porcentaje_no_rompe_el_arranque(self, tmp_path):
        ruta = tmp_path / "ana%20" / "aurea.sqlite3"
        ruta.parent.mkdir()

        db_module.init_db(ruta, force=True)

        assert db_module.revision_actual(db_module._engine) == _cabeza(tmp_path)
        db_module._engine.dispose()
        db_module._engine = None
        db_module._SessionLocal = None


@pytest.mark.integration
class TestSmokeCompleto:
    """El smoke entero, como lo corre el workflow de Windows, pero en el CI
    de cada push: una regresion en lo que el smoke ejercita (migraciones,
    cifrado, ONVIF, modelos reales) aparece aca antes que en el build de
    45 minutos."""

    def test_con_data_dir_vacio_ejercita_todo(self, tmp_path):
        import os
        import subprocess
        import sys

        entorno = {
            **os.environ,
            "AUREA_DATA_DIR": str(tmp_path / "vacio"),
            "QT_QPA_PLATFORM": "offscreen",
        }
        resultado = subprocess.run(
            [sys.executable, "-m", "aurea_vms.main", "--smoke"],
            env=entorno,
            capture_output=True,
            text=True,
            timeout=180,
        )

        salida = resultado.stdout
        assert resultado.returncode == 0, resultado.stdout + resultado.stderr
        assert "base nueva: corrieron todas las revisiones" in salida
        assert "smoke: AVISO" not in salida
        for chequeo in ("migraciones", "cifrado", "onvif", "analizador face_detection"):
            assert f"smoke: {chequeo} OK" in salida
        assert "SMOKE OK" in salida
