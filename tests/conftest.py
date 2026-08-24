from __future__ import annotations

import pytest

from aurea_vms.models import db as db_module


@pytest.fixture()
def temp_db(tmp_path):
    """Inicializa una base sqlite temporal y aislada para el test."""
    db_module.init_db(tmp_path / "test.sqlite3", force=True)
    yield
    db_module._engine = None
    db_module._SessionLocal = None
