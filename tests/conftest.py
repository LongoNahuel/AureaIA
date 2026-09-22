from __future__ import annotations

import os
import shutil

# Los tests nunca deben abrir ventanas reales (ni fallar en un runner de CI
# sin display) -- se fija antes de que cualquier import cree la QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from aurea_vms.core import auth  # noqa: E402
from aurea_vms.models import db as db_module  # noqa: E402

# Valores declarados, capturados ANTES de abaratarlos para los tests.
ITERACIONES_DECLARADAS = auth.ITERACIONES
ITERACIONES_LEGADAS_DECLARADAS = auth.ITERACIONES_LEGADAS


@pytest.fixture(autouse=True)
def _pbkdf2_barato(monkeypatch):
    """PBKDF2 a 600.000 iteraciones cuesta ~150ms por hash -- a proposito, es
    el punto de la funcion. Pero la suite hashea cientos de veces y eso solo
    en test_auth.py eran 33 segundos. Se abarata el coste, no el algoritmo:
    lo que se prueba es el formato, la migracion y el lockout, no cuanto
    tarda. El valor declarado se verifica aparte, con la fixture de abajo."""
    monkeypatch.setattr(auth, "ITERACIONES", 1_000)
    monkeypatch.setattr(auth, "ITERACIONES_LEGADAS", 1_000)


@pytest.fixture()
def iteraciones_declaradas():
    """El coste real configurado, sin el abaratamiento de los tests."""
    return ITERACIONES_DECLARADAS


@pytest.fixture(scope="session")
def _plantilla_migrada(tmp_path_factory):
    """Una base ya migrada, creada UNA vez por corrida.

    init_db sobre una base nueva cuesta ~73ms porque aplica las revisiones de
    Alembic una por una. Con ~60 tests usando temp_db eso son mas de 4
    segundos de cada corrida gastados en recrear siempre el mismo esquema.
    Se migra una sola vez y cada test copia el archivo.
    """
    ruta = tmp_path_factory.mktemp("plantilla") / "plantilla.sqlite3"
    db_module.init_db(ruta, force=True)
    # dispose() cierra las conexiones y vuelca el WAL al archivo principal:
    # sin eso, la copia se llevaria un esquema incompleto.
    db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionLocal = None
    return ruta


@pytest.fixture()
def temp_db(tmp_path, _plantilla_migrada):
    """Base sqlite temporal y aislada para el test, copiada de la plantilla."""
    destino = tmp_path / "test.sqlite3"
    shutil.copyfile(_plantilla_migrada, destino)
    db_module.init_db(destino, force=True)
    yield
    if db_module._engine is not None:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionLocal = None


@pytest.fixture(autouse=True)
def _reset_auth_session():
    """La sesion de usuario es una global de modulo; un test que hace login
    no debe contaminar al siguiente."""
    yield
    from aurea_vms.core import auth

    auth.current_user = None


@pytest.fixture(autouse=True)
def _reset_site_filter():
    """El filtro global de sitio tambien es global de modulo."""
    yield
    from aurea_vms.core import app_state

    app_state.reset()
