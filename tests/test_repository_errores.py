"""Tests de la frontera de excepciones del repositorio.

Hasta la Fase 7, `ui/dialogs/device_dialog.py` importaba
`sqlalchemy.exc.IntegrityError` para poder decirle al usuario "Ya existe un
sitio llamado X". Era el único import de SQLAlchemy en toda la capa de
widgets, y ataba el diálogo al ORM: el proyecto está diseñado para poder
cambiar de motor de base.
"""

from __future__ import annotations

import pytest
import sqlalchemy.exc

from aurea_vms.models import repository
from aurea_vms.models.errors import DuplicateError, RepositoryError


class TestDuplicados:
    def test_un_sitio_repetido_levanta_una_excepcion_de_dominio(self, temp_db):
        repository.add_site(name="Sala")

        with pytest.raises(DuplicateError):
            repository.add_site(name="Sala")

    def test_un_usuario_repetido_tambien(self, temp_db):
        repository.add_user(username="admin", password_hash="h", salt="s", role="admin")

        with pytest.raises(DuplicateError):
            repository.add_user(username="admin", password_hash="h", salt="s", role="admin")

    def test_una_ruta_de_media_repetida_tambien(self, temp_db):
        device = repository.add_device(name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")
        repository.add_media_asset(
            kind="clip", device_id=device.id, timestamp=1.0, rel_path="clip/x.mp4"
        )

        with pytest.raises(DuplicateError):
            repository.add_media_asset(
                kind="clip", device_id=device.id, timestamp=2.0, rel_path="clip/x.mp4"
            )

    def test_renombrar_a_un_nombre_ya_usado_tambien(self, temp_db):
        """La frontera cubre los update_*, no sólo las altas."""
        repository.add_site(name="Sala")
        anexo = repository.add_site(name="Anexo")

        with pytest.raises(DuplicateError):
            repository.update_site(anexo.id, name="Sala")

    def test_duplicate_es_un_repository_error(self):
        """La UI puede capturar el caso preciso o el general."""
        assert issubclass(DuplicateError, RepositoryError)


class TestNoSeFiltraElOrm:
    def test_la_excepcion_no_es_de_sqlalchemy(self, temp_db):
        repository.add_site(name="Sala")

        with pytest.raises(DuplicateError) as capturada:
            repository.add_site(name="Sala")

        assert not isinstance(capturada.value, sqlalchemy.exc.SQLAlchemyError)
        # La causa original se conserva para poder diagnosticar.
        assert isinstance(capturada.value.__cause__, sqlalchemy.exc.IntegrityError)

    def test_ningun_widget_importa_sqlalchemy(self):
        """Guarda de regresión: `aurea_vms/ui` habla sólo el idioma del
        dominio. Si alguien vuelve a importar el ORM en un diálogo, esto lo
        caza en el CI y no en la revisión."""
        from pathlib import Path

        ui = Path(repository.__file__).resolve().parents[1] / "ui"
        culpables = [
            path.relative_to(ui.parent).as_posix()
            for path in ui.rglob("*.py")
            if "sqlalchemy" in path.read_text(encoding="utf-8")
        ]

        assert culpables == []
