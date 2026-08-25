"""Seed idempotente de la base para la demo / desarrollo.

Crea (solo si faltan): 2 sitios, 4 camaras apuntando al rig RTSP local,
una analitica por camara, reglas de alarma con clip+popup y un usuario
por rol. Se puede correr todas las veces que haga falta sin duplicar
nada.

Uso:  python tools/demo/seed_demo.py  [--rtsp-host 127.0.0.1:8554]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aurea_vms.core import auth  # noqa: E402
from aurea_vms.models import repository  # noqa: E402
from aurea_vms.models.db import init_db  # noqa: E402
from aurea_vms.models.user import (  # noqa: E402
    ROLE_AUDITOR,
    ROLE_OPERATOR,
    ROLE_SUPERVISOR,
)

DEMO_PASSWORD = "Aurea123!x"

SITES = [
    ("Sala Principal", "Planta baja: mesas, cajas y accesos"),
    ("Anexo VIP", "Salas privadas y bóveda"),
]

# (nombre, sitio, path RTSP, analitica, params, clases de la regla)
CAMERAS = [
    ("Mesas 01", "Sala Principal", "cam1", "people_counting", {}, ["person"]),
    (
        "Acceso Hall",
        "Sala Principal",
        "cam2",
        "line_crossing",
        # Linea horizontal a media altura de un frame 1280x720.
        {"line": [[100, 360], [1180, 360]], "label_in": "Entrada", "label_out": "Salida"},
        ["person"],
    ),
    ("Bóveda Acceso", "Anexo VIP", "cam3", "face_detection", {}, ["cara"]),
    ("Zona Sur Sala", "Anexo VIP", "cam4", "motion_detection", {}, ["movimiento"]),
]

USERS = [
    ("supervisor", ROLE_SUPERVISOR),
    ("operador", ROLE_OPERATOR),
    ("auditor", ROLE_AUDITOR),
]


def seed(rtsp_host: str) -> None:
    init_db()

    site_ids: dict[str, int] = {}
    existing_sites = {site.name: site.id for site in repository.list_sites()}
    for name, description in SITES:
        if name in existing_sites:
            site_ids[name] = existing_sites[name]
        else:
            site_ids[name] = repository.add_site(name=name, description=description).id
            print(f"+ sitio: {name}")

    existing_devices = {device.name: device for device in repository.list_devices()}
    for name, site_name, path, analyzer, params, classes in CAMERAS:
        device = existing_devices.get(name)
        if device is None:
            device = repository.add_device(
                name=name,
                site_id=site_ids[site_name],
                ip="127.0.0.1",
                port=8554,
                rtsp_main_url=f"rtsp://{rtsp_host}/{path}",
            )
            print(f"+ cámara: {name} -> rtsp://{rtsp_host}/{path}")

        repository.upsert_analytics_config(
            device.id,
            analyzer,
            enabled=True,
            confidence_threshold=0.5,
            params=params,
            object_classes=classes,
        )

        if not repository.list_alarm_rules_for(device.id, analyzer):
            # La boveda (rostros) es la regla "critica" de la narrativa de
            # demo: popup persistente + sonido. El resto, severidad alta.
            critical = analyzer == "face_detection"
            repository.add_alarm_rule(
                device_id=device.id,
                analyzer_name=analyzer,
                object_classes=classes,
                min_confidence=0.5,
                cooldown_seconds=20,
                severity="critico" if critical else "alto",
                actions={"notify_ui": True, "save_clip": True, "play_sound": critical},
            )
            print(f"+ regla de alarma: {name} / {analyzer}")

    if not auth.has_admin_user():
        auth.create_admin_user("admin", DEMO_PASSWORD)
        print("+ usuario: admin (Administrador)")
    for username, role in USERS:
        if repository.get_user_by_username(username) is None:
            auth.create_user(username, DEMO_PASSWORD, role)
            print(f"+ usuario: {username} ({role})")

    print("Seed OK.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rtsp-host", default="127.0.0.1:8554")
    seed(parser.parse_args().rtsp_host)
