"""Configuracion global de la app: paths de datos y defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "aurea_vms.sqlite3"
CLIPS_DIR = DATA_DIR / "clips"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
LOG_PATH = DATA_DIR / "aurea_vms.log"


@dataclass(frozen=True)
class Settings:
    data_dir: Path = DATA_DIR
    db_path: Path = DB_PATH
    clips_dir: Path = CLIPS_DIR
    snapshots_dir: Path = SNAPSHOTS_DIR
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
        for path in (self.data_dir, self.clips_dir, self.snapshots_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
