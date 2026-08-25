from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from aurea_vms.config import resources
from aurea_vms.config.settings import APP_DIR_NAME, PROJECT_ROOT, _resolve_data_dir
from aurea_vms.core.analytics import model_assets


class TestResolveDataDir:
    def test_dev_usa_data_del_repo(self, monkeypatch):
        monkeypatch.delenv("AUREA_DATA_DIR", raising=False)
        assert _resolve_data_dir() == PROJECT_ROOT / "data"

    def test_env_override_gana_siempre(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUREA_DATA_DIR", str(tmp_path / "custom"))
        assert _resolve_data_dir() == tmp_path / "custom"

    def test_frozen_windows_usa_localappdata(self, monkeypatch, tmp_path):
        monkeypatch.delenv("AUREA_DATA_DIR", raising=False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert _resolve_data_dir() == tmp_path / APP_DIR_NAME

    def test_frozen_linux_usa_share(self, monkeypatch):
        monkeypatch.delenv("AUREA_DATA_DIR", raising=False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        assert _resolve_data_dir() == Path.home() / ".local" / "share" / APP_DIR_NAME


class TestBundledPath:
    def test_dev_resuelve_contra_el_repo(self, monkeypatch):
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        assert resources.bundled_path("models/x.tflite") == PROJECT_ROOT / "models/x.tflite"

    def test_frozen_resuelve_contra_meipass(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert resources.bundled_path("wsdl") == tmp_path / "wsdl"


class TestOnvifKwargs:
    def test_en_dev_no_pasa_nada(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        assert resources.onvif_camera_kwargs() == {}

    def test_frozen_con_wsdl_empaquetado(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        (tmp_path / "wsdl").mkdir()
        assert resources.onvif_camera_kwargs() == {"wsdl_dir": str(tmp_path / "wsdl")}

    def test_frozen_sin_wsdl_no_rompe(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert resources.onvif_camera_kwargs() == {}


class TestEnsureModel:
    def test_existente_no_toca_nada(self, monkeypatch, tmp_path):
        monkeypatch.setattr(model_assets, "settings", SimpleNamespace(data_dir=tmp_path))
        model = tmp_path / "models" / "m.tflite"
        model.parent.mkdir(parents=True)
        model.write_bytes(b"pesos")

        assert model_assets.ensure_model("m.tflite", "http://no-se-usa") == str(model)

    def test_copia_desde_el_bundle(self, monkeypatch, tmp_path):
        data_dir = tmp_path / "appdata"
        bundle = tmp_path / "bundle"
        (bundle / "models").mkdir(parents=True)
        (bundle / "models" / "m.tflite").write_bytes(b"pesos-del-bundle")

        monkeypatch.setattr(model_assets, "settings", SimpleNamespace(data_dir=data_dir))
        monkeypatch.setattr(model_assets.resources, "bundled_path", lambda rel: bundle / rel)

        path = model_assets.ensure_model("m.tflite", "http://no-se-usa")
        assert Path(path).read_bytes() == b"pesos-del-bundle"
        assert Path(path) == data_dir / "models" / "m.tflite"
