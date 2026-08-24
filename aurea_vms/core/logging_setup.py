"""Configuracion de logging de la app: consola + archivo rotativo en
data/aurea_vms.log. Se llama una sola vez, al arrancar (main.py), antes de
inicializar cualquier otro modulo."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from aurea_vms.config.settings import settings

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    settings.ensure_dirs()

    root = logging.getLogger()
    if root.handlers:
        return  # ya configurado

    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    file_handler = RotatingFileHandler(
        settings.log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)
