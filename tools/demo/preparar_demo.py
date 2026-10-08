"""Arma los datos de una demo del VMS a partir de una base de desarrollo, sin
tocarla (2026-10-07, demo a posibles clientes en la laptop de Nahuel).

Uso:
    .venv/bin/python tools/demo/preparar_demo.py [--origen data]
        --clave CLAVE_DEL_ADMIN [--destino ~/AureaIA_demo/datos]
        [--analiticas 10,8]

La demo muestra una analitica a la vez: queda prendida la de la primera
camara de --analiticas, y las demas configuradas y apagadas (se prenden con
el interruptor de Analizadores durante la demo).

Deja en --destino:
- una copia consistente de la base (API de backup de SQLite, con el WAL);
- camera_key y preferences.json (retencion subida a RETENTION_DAYS: con 7
  dias, las capturas y clips del historico se borraban en plena demo);
- los modelos (enlace a los del origen: la demo no necesita internet);
- la media de las alarmas del historico, para que el detalle de Alarmas
  muestre su captura y su clip.

Y en la copia:
- la clave del admin, con el bloqueo por intentos fallidos limpio;
- sin las camaras que no son simuladas (las reales no estan en la red de la
  demo y figurarian desconectadas);
- las alarmas del historico reconocidas (siguen en Alarmas; "Alertas
  activas" arranca en 0);
- las analiticas de las camaras de --analiticas livianas para la laptop
  (ruleta a 25 fps de analisis y 5 detecciones de manos por segundo: con 60
  y 15 el VMS llego a 500-550 % de CPU; consumo a 5 fps), con su regla de
  alarma de incidentes; prendida solo la primera, el resto apagadas.

Ademas guarda una copia de lo preparado en <destino>_original, para volver a
el con `tools/demo/demo.sh restaurar`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MARKER = ".demo_aurea"
RETENTION_DAYS = 30
SIMULATED_HOST = "127.0.0."
ROULETTE_LIMITS = {"fps": 25, "manos_fps": 5.0}
CONSUMPTION_FPS = 5


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--origen", type=Path, default=REPO / "data")
    parser.add_argument("--destino", type=Path, default=Path.home() / "AureaIA_demo" / "datos")
    # Sin valor por defecto: la clave de la demo no queda escrita en el repo.
    parser.add_argument("--clave", required=True, help="clave del admin en la copia")
    parser.add_argument(
        "--analiticas",
        default="10,8",
        help="camaras con analitica para la demo (ids, separados por coma); la primera "
        "arranca prendida",
    )
    return parser.parse_args()


def _fresh_dir(path: Path) -> None:
    """Vacia `path` solo si lo creo este script (tiene la marca)."""
    if path.exists():
        if not (path / MARKER).exists():
            sys.exit(f"{path} existe y no es una carpeta de demo: no la toco")
        shutil.rmtree(path)
    path.mkdir(parents=True)
    (path / MARKER).write_text("datos de la demo; los arma tools/demo/preparar_demo.py\n")


def _copy_files(origin: Path, target: Path) -> None:
    source = sqlite3.connect(f"file:{origin / 'aurea_vms.sqlite3'}?mode=ro", uri=True)
    copy = sqlite3.connect(target / "aurea_vms.sqlite3")
    source.backup(copy)
    copy.close()
    source.close()
    shutil.copy2(origin / "camera_key", target / "camera_key")
    os.chmod(target / "camera_key", 0o600)
    prefs = json.loads((origin / "preferences.json").read_text())
    prefs["retention_days"] = max(prefs.get("retention_days", 0), RETENTION_DAYS)
    (target / "preferences.json").write_text(json.dumps(prefs, indent=2, ensure_ascii=False))
    (target / "models").symlink_to((origin / "models").resolve())


def _copy_alarm_media(origin: Path, target: Path) -> int:
    db = sqlite3.connect(f"file:{target / 'aurea_vms.sqlite3'}?mode=ro", uri=True)
    rows = db.execute(
        "select rel_path from media_assets where alarm_event_id is not null"
    ).fetchall()
    db.close()
    copied = 0
    for (rel_path,) in rows:
        source = origin / "media" / rel_path
        if source.exists():
            destination = target / "media" / rel_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied += 1
    return copied


def _prepare_database(password: str, cameras: list[int]) -> None:
    """Corre con AUREA_DATA_DIR apuntando a la copia."""
    from aurea_vms.config.settings import settings
    from aurea_vms.models.db import init_db

    init_db()
    from aurea_vms.core import auth, incident_rules
    from aurea_vms.models import repository

    error = auth.admin_reset_password(1, password)
    if error:
        sys.exit(f"No se pudo poner la clave: {error}")
    db = sqlite3.connect(settings.db_path)
    db.execute("update users set failed_attempts = 0, locked_until = NULL")
    db.commit()
    db.close()

    for device in repository.list_devices():
        if SIMULATED_HOST not in (device.rtsp_main_url or ""):
            repository.delete_device(device.id)
            print(f"  fuera de la demo: {device.name}")
        elif acknowledged := repository.acknowledge_pending_alarm_events(device.id):
            # Las del historico quedan reconocidas: "Alertas activas" arranca en
            # 0 y vuelve a 0 con "Limpiar incidentes" (con alarmas viejas de
            # otra camara pendientes, parecia que no limpiaba).
            print(f"  {acknowledged} alarmas viejas reconocidas: {device.name}")

    for config in repository.list_analytics_configs():
        demo = config.device_id in cameras
        enabled = demo and config.device_id == cameras[0]
        params = dict(config.params or {})
        if demo and params.get("modo") == "ruleta":
            params["fps"] = min(int(params.get("fps", 25)), ROULETTE_LIMITS["fps"])
            params["manos_fps"] = min(
                float(params.get("manos_fps", 5)), ROULETTE_LIMITS["manos_fps"]
            )
        elif demo and params.get("modo") == "consumo":
            params["fps"] = min(int(params.get("fps", CONSUMPTION_FPS)), CONSUMPTION_FPS)
        updated = repository.upsert_analytics_config(
            config.device_id, config.analyzer_name, enabled=enabled, params=params
        )
        if demo:
            incident_rules.ensure_incident_rule(updated)
            state = "prendida" if enabled else "apagada (se prende en la demo)"
            print(f"  camara {config.device_id} {params.get('modo')}: {state}")

    if not auth.authenticate("admin", password):
        sys.exit("La clave nueva no entra")


def main() -> int:
    args = _parse_args()
    target = args.destino.expanduser().resolve()
    cameras = [int(value) for value in args.analiticas.split(",") if value.strip()]
    _fresh_dir(target)
    _copy_files(args.origen.resolve(), target)
    print(f"media de alarmas copiada: {_copy_alarm_media(args.origen.resolve(), target)} archivos")
    os.environ["AUREA_DATA_DIR"] = str(target)
    sys.path.insert(0, str(REPO))
    _prepare_database(args.clave, cameras)

    original = target.with_name(target.name + "_original")
    if original.exists():
        if not (original / MARKER).exists():
            sys.exit(f"{original} existe y no es una carpeta de demo: no la toco")
        shutil.rmtree(original)
    shutil.copytree(target, original, symlinks=True)
    print(f"listo: {target} (copia para restaurar: {original})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
