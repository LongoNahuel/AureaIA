"""Configuracion global de la app: paths de datos y defaults.

El directorio de datos (DB, media, logs, modelos) se resuelve segun el
contexto:

1. AUREA_DATA_DIR (env): override explicito -- tests, CI, despliegues.
2. Ejecutable congelado (PyInstaller): un directorio del perfil del
   usuario. NUNCA el directorio de instalacion: bajo Program Files no
   hay permisos de escritura y en onefile seria un temp volatil.
3. Desarrollo: data/ dentro del repo, como siempre.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DIR_NAME = "AureaVMS"


def _resolve_data_dir() -> Path:
    env_override = os.environ.get("AUREA_DATA_DIR")
    if env_override:
        return Path(env_override)
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            return Path(base) / APP_DIR_NAME if base else Path.home() / APP_DIR_NAME
        return Path.home() / ".local" / "share" / APP_DIR_NAME
    return PROJECT_ROOT / "data"


DATA_DIR = _resolve_data_dir()
DB_PATH = DATA_DIR / "aurea_vms.sqlite3"
# Raiz unica de clips/capturas/grabaciones, organizada por
# <tipo>/<fecha>/<camara> e indexada por la tabla media_assets.
MEDIA_DIR = DATA_DIR / "media"
LOG_PATH = DATA_DIR / "aurea_vms.log"


@dataclass(frozen=True)
class Settings:
    data_dir: Path = DATA_DIR
    db_path: Path = DB_PATH
    media_dir: Path = MEDIA_DIR
    log_path: Path = LOG_PATH

    # Vista en vivo
    display_fps: int = 25

    # Analiticas
    analytics_fps: float = 5.0

    # Clips de evento
    clip_pre_seconds: int = 5
    clip_post_seconds: int = 10
    alarm_default_cooldown_seconds: int = 30

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.media_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
