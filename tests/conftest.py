from __future__ import annotations

import os

# Los tests nunca deben abrir ventanas reales (ni fallar en un runner de CI
# sin display) -- se fija antes de que cualquier import cree la QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from aurea_vms.models import db as db_module  # noqa: E402


@pytest.fixture()
def temp_db(tmp_path):
    """Inicializa una base sqlite temporal y aislada para el test."""
    db_module.init_db(tmp_path / "test.sqlite3", force=True)
    yield
    db_module._engine = None
    db_module._SessionLocal = None


@pytest.fixture(autouse=True)
def _reset_auth_session():
    """La sesion de usuario es una global de modulo; un test que hace login
    no debe contaminar al siguiente."""
    yield
    from aurea_vms.core import auth

    auth.current_user = None
