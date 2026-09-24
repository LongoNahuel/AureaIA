"""Smoke del entorno (o del .exe empaquetado): `AureaVMS.exe --smoke`.

Cada chequeo ejercita un camino que SOLO falla empaquetado, porque carga algo
en runtime que el analisis estatico de PyInstaller no ve: revisiones de
Alembic leidas por ruta, binarios nativos de cryptography y onnxruntime, el
WSDL de ONVIF y los .onnx como datas. Importar un modulo no alcanza para
ninguno de esos: hay que usarlo.

Hasta el 23/09 el smoke del .exe corria con el data-dir que habia dejado el
smoke previo al build: la base ya estaba en head (no corria ninguna
revision) y los modelos ya estaban copiados (no se usaba el bundle). Por eso
`run()` avisa cuando el data-dir no esta vacio, y el workflow de Windows le
pasa uno distinto a cada smoke.

Imprime una linea "smoke: <chequeo> OK" por chequeo y "SMOKE OK" al final;
cualquier falla levanta y el proceso sale con error.
"""

from __future__ import annotations

import sys
from pathlib import Path

from aurea_vms.config import resources
from aurea_vms.config.settings import settings

SMOKE_PASSWORD = "smoke-Contraseña-ñ%1"
DEVICE_BINDING = "{http://www.onvif.org/ver10/device/wsdl}DeviceBinding"


def _ok(chequeo: str, detalle: str = "") -> None:
    print(f"smoke: {chequeo} OK" + (f" ({detalle})" if detalle else ""), flush=True)


def check_migraciones(base_nueva: bool) -> str:
    """La base quedo en la ultima revision. Con base nueva eso significa que
    corrieron TODAS las revisiones, env.py incluido, desde los archivos que
    lleva el bundle."""
    from alembic.script import ScriptDirectory

    from aurea_vms.models import db

    cabeza = ScriptDirectory.from_config(db._config_de_alembic(settings.db_path)).get_current_head()
    actual = db.revision_actual(db._engine)
    if actual != cabeza:
        raise RuntimeError(f"la base quedó en {actual}, la cabeza es {cabeza}")
    return f"{actual}, {'base nueva: corrieron todas las revisiones' if base_nueva else 'base preexistente'}"


def check_cifrado() -> str:
    """Una camara con contraseña ida y vuelta por la base. Ejercita la
    creacion de camera_key (os.open con O_EXCL, que en Windows es otro
    codigo), el TypeDecorator y los binarios nativos de cryptography --
    importar `cryptography.fernet` solo carga el modulo, no cifra nada."""
    import sqlalchemy as sa

    from aurea_vms.core import credential_store
    from aurea_vms.models import repository
    from aurea_vms.models.db import get_session

    device = repository.add_device(
        name="smoke",
        ip="127.0.0.1",
        password=SMOKE_PASSWORD,
        rtsp_main_url="rtsp://127.0.0.1/smoke",
    )
    try:
        # SQL de texto a proposito: un select sobre la tabla pasa por el
        # TypeDecorator y devolveria la contraseña ya descifrada.
        with get_session() as session:
            crudo = session.execute(
                sa.text("SELECT password FROM devices WHERE id = :id"), {"id": device.id}
            ).scalar_one()
        if not credential_store.esta_cifrado(crudo):
            raise RuntimeError("la contraseña quedó en texto plano en la base")
        leida = repository.get_device(device.id).password
        if leida != SMOKE_PASSWORD:
            raise RuntimeError("la contraseña no volvió igual al descifrarla")
    finally:
        repository.delete_device(device.id)
    return f"clave en {credential_store._key_path()}"


def check_onvif() -> str:
    """El WSDL de device management, parseado con zeep SIN red: si el spec
    no empaqueto la carpeta wsdl (o lxml no trajo sus binarios), ONVIF muere
    recien en la sala, al primer "Buscar camaras"."""
    import lxml.etree  # noqa: F401 - binarios nativos de lxml
    import onvif
    import wsdiscovery  # noqa: F401
    from zeep import Client, Settings
    from zeep.transports import Transport

    class _SinRed(Transport):
        def _load_remote_data(self, url):
            raise RuntimeError(f"el WSDL intentó bajar {url}: falta en el bundle")

    wsdl_dir = resources.onvif_camera_kwargs().get("wsdl_dir")
    if resources.is_frozen() and wsdl_dir is None:
        raise RuntimeError("no hay carpeta wsdl en el bundle")
    carpeta = Path(wsdl_dir) if wsdl_dir else Path(onvif.__file__).resolve().parent.parent / "wsdl"
    cliente = Client(
        str(carpeta / "devicemgmt.wsdl"), transport=_SinRed(), settings=Settings(strict=False)
    )
    # zeep acepta sin quejarse un archivo que no define nada: lo que importa
    # es que el binding que usa ONVIFCamera este.
    if DEVICE_BINDING not in cliente.wsdl.bindings:
        raise RuntimeError(f"{carpeta / 'devicemgmt.wsdl'} no define {DEVICE_BINDING}")
    return str(carpeta)


def _prohibir_descargas() -> None:
    """Los modelos tienen que salir del bundle (o del repo en dev). Una
    descarga en el smoke es una falla, no un plan B: la sala puede no tener
    internet."""
    from aurea_vms.core.analytics import model_assets

    def _prohibido(url, *_args, **_kwargs):
        raise RuntimeError(f"el smoke intentó descargar {url}: el modelo falta en el bundle")

    model_assets.urllib.request.urlretrieve = _prohibido


def check_analizadores() -> list[str]:
    """Los analizadores reales con sus modelos nativos: las DLL de
    onnxruntime cargan recien al crear la primera sesion de inferencia."""
    import numpy as np

    from aurea_vms.core.analytics.registry import AVAILABLE_ANALYZERS, create_analyzer
    from aurea_vms.models.analytics_config import AnalyticsConfig

    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    # Los que no arrancan sin geometria: la linea de cruce y la pantalla a
    # vigilar de Detección de incidentes (que ademas carga el modelo de pose, RTMPose).
    geometria = {
        "line_crossing": {"line": [[0, 180], [640, 180]]},
        "monitor_tamper": {"zones": [[220, 90, 200, 150]]},
    }
    for name in AVAILABLE_ANALYZERS:
        params = geometria.get(name, {})
        config = AnalyticsConfig(
            device_id=0, analyzer_name=name, confidence_threshold=0.5, params=params
        )
        analyzer = create_analyzer(config)
        analyzer.process_frame(frame, 0.0)
        analyzer.close()
        _ok(f"analizador {name}")
    return list(AVAILABLE_ANALYZERS)


def run() -> int:
    from PySide6.QtWidgets import QApplication

    from aurea_vms.core.logging_setup import setup_logging
    from aurea_vms.models.db import init_db

    settings.ensure_dirs()
    setup_logging()
    base_nueva = not settings.db_path.exists()
    if not base_nueva:
        print(
            f"smoke: AVISO data-dir no vacío ({settings.data_dir}): no se ejercitan "
            "las migraciones ni la copia de modelos desde el bundle",
            flush=True,
        )

    init_db()
    _ok("migraciones", check_migraciones(base_nueva))
    _ok("cifrado", check_cifrado())
    _ok("onvif", check_onvif())
    _prohibir_descargas()
    check_analizadores()

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.processEvents()
    print("SMOKE OK", flush=True)
    return 0
