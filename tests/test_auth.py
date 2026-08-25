from __future__ import annotations

from aurea_vms.core import auth
from aurea_vms.models.user import ROLE_ADMIN, ROLE_OPERATOR


class TestValidatePassword:
    def test_valida_cumple_todo(self):
        assert auth.validate_password("abc123def!") is None

    def test_demasiado_corta(self):
        assert auth.validate_password("ab1!") is not None

    def test_sin_numeros(self):
        assert auth.validate_password("abcdefghi!") is not None

    def test_sin_simbolos(self):
        assert auth.validate_password("abcdefghi1") is not None

    def test_sin_letras(self):
        assert auth.validate_password("123456789!") is not None


class TestPasswordStrength:
    def test_vacia(self):
        assert auth.password_strength("") == ""

    def test_debil(self):
        assert auth.password_strength("abc") == "Débil"

    def test_media(self):
        assert auth.password_strength("abc123") == "Media"

    def test_fuerte(self):
        assert auth.password_strength("abc123def!") == "Fuerte"


class TestHashing:
    def test_mismo_password_distinto_salt_distinto_hash(self):
        h1 = auth._hash_password("secreto123!", b"salt-uno-16bytes")
        h2 = auth._hash_password("secreto123!", b"salt-dos-16bytes")
        assert h1 != h2

    def test_determinismo_con_mismo_salt(self):
        salt = b"un-salt-de-prueba"
        assert auth._hash_password("x", salt) == auth._hash_password("x", salt)


class TestUsuarios:
    def test_primer_arranque_sin_usuarios(self, temp_db):
        assert not auth.has_admin_user()

    def test_create_admin_y_authenticate(self, temp_db):
        auth.create_admin_user("root", "clave123!x")
        assert auth.has_admin_user()

        user = auth.authenticate("root", "clave123!x")
        assert user is not None
        assert user.role == ROLE_ADMIN

    def test_authenticate_password_incorrecta(self, temp_db):
        auth.create_admin_user("root", "clave123!x")
        assert auth.authenticate("root", "otra-clave") is None

    def test_authenticate_usuario_inexistente(self, temp_db):
        assert auth.authenticate("fantasma", "loquesea") is None

    def test_login_setea_sesion_y_logout_la_limpia(self, temp_db):
        auth.create_admin_user("root", "clave123!x")

        assert auth.login("root", "mal") is None
        assert auth.current_user is None

        user = auth.login("root", "clave123!x")
        assert user is not None
        assert auth.current_user is not None
        assert auth.current_user.username == "root"

        auth.logout()
        assert auth.current_user is None

    def test_is_admin_por_rol(self, temp_db):
        auth.create_admin_user("root", "clave123!x")
        operador = auth.create_user("operador1", "clave123!x", ROLE_OPERATOR)

        assert auth.is_admin() is False  # sin sesion
        auth.login("root", "clave123!x")
        assert auth.is_admin() is True
        assert auth.is_admin(operador) is False

    def test_change_password_exige_la_actual(self, temp_db):
        auth.create_admin_user("root", "clave123!x")

        error = auth.change_password("root", "equivocada", "nueva123!x")
        assert error is not None
        assert auth.authenticate("root", "clave123!x") is not None

    def test_change_password_valida_la_nueva(self, temp_db):
        auth.create_admin_user("root", "clave123!x")

        error = auth.change_password("root", "clave123!x", "corta")
        assert error is not None

    def test_change_password_ok(self, temp_db):
        auth.create_admin_user("root", "clave123!x")

        assert auth.change_password("root", "clave123!x", "nueva123!x") is None
        assert auth.authenticate("root", "clave123!x") is None
        assert auth.authenticate("root", "nueva123!x") is not None

    def test_admin_reset_password(self, temp_db):
        auth.create_admin_user("root", "clave123!x")
        user = auth.create_user("operador1", "clave123!x", ROLE_OPERATOR)

        assert auth.admin_reset_password(user.id, "corta") is not None
        assert auth.admin_reset_password(user.id, "reset123!x") is None
        assert auth.authenticate("operador1", "reset123!x") is not None
