from __future__ import annotations

import hashlib
import logging
import os
import time

import pytest

from aurea_vms.core import auth
from aurea_vms.models import repository
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


# ---------------------------------------------------------------------------
# Fase 10: formato de hash migrable, rehash al login y lockout.
# ---------------------------------------------------------------------------

BUENA = "Aurea123!x"


def _usuario(username="op", password=BUENA, role="operador"):
    return auth.create_user(username, password, role)


def _usuario_con_hash_legado(username="viejo", password=BUENA):
    """Usuario tal como lo escribía la versión anterior: hash hex pelado de
    260.000 iteraciones y el salt en su propia columna."""
    salt = os.urandom(16)
    legado = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, auth.ITERACIONES_LEGADAS
    ).hex()
    return repository.add_user(
        username=username, password_hash=legado, salt=salt.hex(), role="operador"
    )


class TestFormatoDeHash:
    def test_es_auto_descriptivo(self, temp_db):
        user = _usuario()

        algoritmo, iteraciones, salt, derivada = user.password_hash.split("$")
        assert algoritmo == "pbkdf2_sha256"
        assert int(iteraciones) == auth.ITERACIONES
        assert len(bytes.fromhex(salt)) == 16
        assert len(bytes.fromhex(derivada)) == 32

    def test_llega_al_minimo_owasp(self, iteraciones_declaradas):
        """Estaba en 260.000, menos de la mitad del mínimo vigente. Se mira
        el valor declarado: la suite corre con un coste abaratado (ver
        conftest) porque acá se prueba el formato, no cuánto tarda."""
        assert iteraciones_declaradas >= 600_000

    def test_entra_en_la_columna(self, temp_db):
        """password_hash es String(200) y el formato nuevo es más largo que
        el viejo: si no entrara, se truncaría en silencio."""
        assert len(_usuario().password_hash) <= 200

    def test_dos_usuarios_con_la_misma_clave_tienen_hashes_distintos(self, temp_db):
        primero = _usuario("uno")
        segundo = _usuario("dos")

        assert primero.password_hash != segundo.password_hash

    def test_el_salt_viejo_queda_vacio(self, temp_db):
        """La columna `salt` existe solo por las filas legadas."""
        assert _usuario().salt == ""


class TestRehashAlLogin:
    def test_una_contrasena_con_formato_legado_sigue_sirviendo(self, temp_db):
        """Nadie tiene que resetear nada al actualizar la app."""
        _usuario_con_hash_legado()

        assert auth.authenticate("viejo", BUENA) is not None

    def test_el_hash_legado_se_actualiza_al_primer_login(self, temp_db):
        user = _usuario_con_hash_legado()
        assert "$" not in user.password_hash

        auth.authenticate("viejo", BUENA)

        actualizado = repository.get_user_by_username("viejo")
        assert actualizado.password_hash.startswith(f"pbkdf2_sha256${auth.ITERACIONES}$")
        assert actualizado.salt == ""

    def test_despues_de_rehashear_la_contrasena_sigue_andando(self, temp_db):
        _usuario_con_hash_legado()
        auth.authenticate("viejo", BUENA)

        assert auth.authenticate("viejo", BUENA) is not None
        assert auth.authenticate("viejo", "otra-cosa1!") is None

    def test_un_hash_legado_con_la_clave_mal_no_entra(self, temp_db):
        _usuario_con_hash_legado()

        assert auth.authenticate("viejo", "incorrecta1!") is None

    def test_un_hash_al_coste_vigente_no_se_re_escribe(self, temp_db):
        user = _usuario()
        original = user.password_hash

        auth.authenticate("op", BUENA)

        assert repository.get_user_by_username("op").password_hash == original

    def test_un_hash_con_algoritmo_desconocido_no_autentica(self, temp_db, caplog):
        repository.add_user(
            username="raro", password_hash="scrypt$1$aa$bb", salt="", role="operador"
        )

        with caplog.at_level(logging.ERROR, logger=auth.__name__):
            assert auth.authenticate("raro", BUENA) is None

        assert "desconocido" in caplog.text


class TestLockout:
    def _fallar(self, veces, username="op"):
        for _ in range(veces):
            auth.authenticate(username, "incorrecta1!")

    def test_los_intentos_fallidos_se_cuentan(self, temp_db):
        _usuario()

        self._fallar(2)

        assert repository.get_user_by_username("op").failed_attempts == 2

    def test_al_llegar_al_tope_se_bloquea(self, temp_db):
        _usuario()

        self._fallar(auth.MAX_INTENTOS)

        with pytest.raises(auth.CuentaBloqueada):
            auth.authenticate("op", BUENA)  # ni con la correcta

    def test_el_bloqueo_dice_cuanto_falta(self, temp_db):
        """La UI necesita poder distinguir "te equivocaste" de "esperá"."""
        _usuario()
        self._fallar(auth.MAX_INTENTOS)

        with pytest.raises(auth.CuentaBloqueada) as bloqueo:
            auth.authenticate("op", BUENA)

        assert 0 < bloqueo.value.segundos_restantes <= auth.BLOQUEO_SEGUNDOS

    def test_el_bloqueo_vence_solo(self, temp_db):
        """Por ventana de tiempo y NUNCA permanente: dejar al admin afuera
        de un VMS a las 3 AM es peor que el ataque que estamos frenando."""
        user = _usuario()
        self._fallar(auth.MAX_INTENTOS)
        repository.update_user(user.id, locked_until=time.time() - 1)

        assert auth.authenticate("op", BUENA) is not None

    def test_un_login_correcto_limpia_el_contador(self, temp_db):
        _usuario()
        self._fallar(auth.MAX_INTENTOS - 1)

        auth.authenticate("op", BUENA)

        fresco = repository.get_user_by_username("op")
        assert (fresco.failed_attempts, fresco.locked_until) == (0, None)

    def test_bloquear_a_uno_no_bloquea_a_los_demas(self, temp_db):
        _usuario("uno")
        _usuario("dos")

        self._fallar(auth.MAX_INTENTOS, username="uno")

        assert auth.authenticate("dos", BUENA) is not None

    def test_un_usuario_inexistente_no_levanta(self, temp_db):
        """No se puede enumerar usuarios por la excepción."""
        assert auth.authenticate("fantasma", BUENA) is None

    def test_el_reset_de_admin_levanta_el_bloqueo(self, temp_db):
        """Resetearle la contraseña a alguien y dejarlo esperando 15 minutos
        igual no tiene ningún sentido."""
        user = _usuario()
        self._fallar(auth.MAX_INTENTOS)

        assert auth.admin_reset_password(user.id, "Nueva123!x") is None
        assert auth.authenticate("op", "Nueva123!x") is not None


class TestPoliticaEnElAlta:
    def test_create_user_valida_la_contrasena(self, temp_db):
        """Era el único camino de alta que NO la validaba, a diferencia de
        change_password y admin_reset_password."""
        with pytest.raises(ValueError, match="9 caracteres"):
            auth.create_user("op", "corta", "operador")

    def test_una_contrasena_valida_crea_el_usuario(self, temp_db):
        assert auth.create_user("op", BUENA, "operador").username == "op"
