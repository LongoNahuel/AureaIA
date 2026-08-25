"""Configuracion global de la app: paths de datos y defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "aurea_vms.sqlite3"
# Raiz unica de clips/capturas/grabaciones, organizada por
# <tipo>/<fecha>/<camara> e indexada por la tabla media_assets
# (reemplaza a los viejos data/clips y data/snapshots planos).
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
