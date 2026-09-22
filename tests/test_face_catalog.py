"""Tests del catálogo de rostros.

Este algoritmo -- firma de apariencia, firma geométrica, dedup por umbral,
asignación de ID, reemplazo por área y reinicio diario -- vivía dentro de
ui/widgets/face_gallery.py, con el QListWidget haciendo de modelo de datos,
y no tenía **un solo test**. Encima quedaba fuera del scope de cobertura por
estar en ui/, y el reinicio diario usaba dt.datetime.now() directo.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from aurea_vms.core.face_catalog import (
    DEFAULT_COUNTING_RESET_TIME,
    FaceCapture,
    FaceCatalog,
    FaceCatalogSettings,
    clamp_bbox,
    combined_difference,
    face_signature,
    geometry_signature,
)

# Cara "de referencia": los 5 puntos que devuelve YuNet, con 40px entre ojos.
FRONTAL = ((70.0, 80.0), (110.0, 80.0), (90.0, 100.0), (75.0, 120.0), (105.0, 120.0))


def _ruidoso(semilla: int, lado: int = 40) -> np.ndarray:
    rng = np.random.default_rng(semilla)
    return rng.integers(0, 255, (lado, lado, 3), dtype=np.uint8)


# Los tests de identidad usan firmas sintéticas en vez de recortes reales.
# No es por comodidad: face_signature ecualiza el histograma, y eso aplana
# cualquier ruido aleatorio a una distribución uniforme -- dos ruidos
# independientes terminan a ~0.33 de distancia, justo por debajo del umbral
# por defecto de 0.35, así que "dos caras distintas" armadas con ruido
# matchean como la misma. Con firmas explícitas la distancia es exacta y el
# test dice lo que quiere decir. face_signature se prueba aparte, con
# recortes de verdad, en TestFirmas.
UMBRAL = 0.05


def _firma(indice: int) -> np.ndarray:
    """Firma a distancia 0.1 * |i - j| de cualquier otra: con UMBRAL = 0.05,
    índices distintos son identidades distintas y el mismo índice es la
    misma."""
    return np.full((24, 24), indice * 0.1, dtype=np.float32)


def _settings(**overrides) -> FaceCatalogSettings:
    return FaceCatalogSettings(diff_threshold=UMBRAL, **overrides)


def _capture(signature, geometry=None, area=100, track_id=1) -> FaceCapture:
    return FaceCapture(
        track_id=track_id, signature=signature, geometry=geometry, area=area, confidence=0.9
    )


class TestFirmas:
    def test_la_firma_de_apariencia_es_estable_y_normalizada(self):
        firma = face_signature(_ruidoso(0))

        assert firma.shape == (24, 24)
        assert firma.dtype == np.float32
        assert firma.min() >= 0.0 and firma.max() <= 1.0
        assert np.array_equal(firma, face_signature(_ruidoso(0)))

    def test_dos_recortes_distintos_dan_firmas_distintas(self):
        assert not np.array_equal(face_signature(_ruidoso(1)), face_signature(_ruidoso(2)))

    def test_la_firma_geometrica_normaliza_por_la_distancia_entre_ojos(self):
        """La misma cara más cerca de la cámara tiene que dar la MISMA firma:
        de eso se trata normalizar por la distancia interpupilar."""
        cerca = tuple((x * 2.5, y * 2.5) for x, y in FRONTAL)

        assert geometry_signature(FRONTAL) == pytest.approx(geometry_signature(cerca), abs=1e-5)

    def test_la_firma_geometrica_cubre_todos_los_pares(self):
        assert geometry_signature(FRONTAL).shape == (10,)  # C(5,2)

    @pytest.mark.parametrize(
        "keypoints",
        [None, (), FRONTAL[:4], ((10.0, 10.0), (10.0, 10.0), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0))],
    )
    def test_sin_geometria_utilizable_devuelve_none(self, keypoints):
        """Menos de 5 puntos, o los dos ojos en el mismo lugar (cara de
        perfil, detección degenerada): no hay con qué normalizar."""
        assert geometry_signature(keypoints) is None


class TestCombinedDifference:
    def test_toma_el_maximo_y_no_el_promedio(self):
        """El caso real que motivó el max(): dos personas con recortes de
        piel/fondo parecidos por casualidad pero geometría bien distinta
        matcheaban como el mismo ID si se promediaba."""
        a = _capture(np.zeros((24, 24), np.float32), geometry=np.zeros(10, np.float32))
        b = _capture(np.zeros((24, 24), np.float32), geometry=np.full(10, 0.8, np.float32))

        assert combined_difference(a, b) == pytest.approx(0.8)  # no 0.4

    def test_sin_geometria_en_alguna_cae_a_la_apariencia(self):
        a = _capture(np.zeros((24, 24), np.float32), geometry=np.zeros(10, np.float32))
        b = _capture(np.full((24, 24), 0.25, np.float32), geometry=None)

        assert combined_difference(a, b) == pytest.approx(0.25)


class TestClampBbox:
    def test_recorta_a_los_limites_del_frame(self):
        assert clamp_bbox((-10, -10, 50, 50), 100, 100) == (0, 0, 40, 40)
        assert clamp_bbox((80, 80, 50, 50), 100, 100) == (80, 80, 100, 100)

    def test_una_caja_entera_fuera_del_frame_no_deja_nada(self):
        assert clamp_bbox((200, 200, 10, 10), 100, 100) is None
        assert clamp_bbox((-20, 0, 10, 10), 100, 100) is None


class TestSettings:
    def test_defaults_con_params_vacios(self):
        settings = FaceCatalogSettings.from_params({})

        assert settings.max_captures_per_face == 1
        assert settings.counting_enabled is True
        assert (settings.reset_hour, settings.reset_minute) == (0, 0)

    def test_lee_lo_que_configuro_el_usuario(self):
        settings = FaceCatalogSettings.from_params(
            {
                "capture_diff_threshold": 0.2,
                "max_captures_per_face": 3,
                "counting_enabled": False,
                "counting_reset_time": "06:30",
            }
        )

        assert settings.diff_threshold == 0.2
        assert settings.max_captures_per_face == 3
        assert settings.counting_enabled is False
        assert (settings.reset_hour, settings.reset_minute) == (6, 30)

    @pytest.mark.parametrize("texto", ["", "seis y media", None, "06", 7, ":"])
    def test_un_horario_roto_cae_a_medianoche(self, texto):
        """Lo escribe un humano en un campo de texto: un typo no puede
        romper el panel entero."""
        settings = FaceCatalogSettings.from_params({"counting_reset_time": texto})

        assert (settings.reset_hour, settings.reset_minute) == (0, 0)

    def test_el_default_declarado_es_medianoche(self):
        assert FaceCatalogSettings._parse_reset_time(DEFAULT_COUNTING_RESET_TIME) == (0, 0)


def _observar(catalog, indice, *, area=100, settings=None, geometry=None):
    return catalog.observe(
        signature=_firma(indice),
        geometry=geometry,
        area=area,
        confidence=0.9,
        settings=settings or _settings(),
    )


class TestIdentidades:
    def test_un_rostro_nuevo_estrena_id_y_suma_al_contador(self):
        catalog = FaceCatalog()

        update = _observar(catalog, 1)

        assert update.is_new_identity is True
        assert update.capture.track_id == 1
        assert update.removed_index is None
        assert catalog.total_count == 1

    def test_dos_rostros_distintos_son_dos_ids(self):
        catalog = FaceCatalog()

        primero = _observar(catalog, 1)
        segundo = _observar(catalog, 5)

        assert primero.capture.track_id != segundo.capture.track_id
        assert catalog.total_count == 2

    def test_el_mismo_rostro_no_estrena_id(self):
        catalog = FaceCatalog()

        _observar(catalog, 1, area=100)
        segundo = _observar(catalog, 1, area=500)

        assert segundo.is_new_identity is False
        assert segundo.capture.track_id == 1
        assert catalog.total_count == 1

    def test_el_umbral_es_lo_que_decide(self):
        """Dos firmas a 0.1 de distancia: con umbral 0.05 son dos personas,
        con umbral 0.5 son la misma."""
        estrictas = FaceCatalog()
        _observar(estrictas, 1, settings=_settings())
        _observar(estrictas, 2, settings=_settings())
        assert estrictas.total_count == 2

        laxas = FaceCatalog()
        tolerante = FaceCatalogSettings(diff_threshold=0.5)
        _observar(laxas, 1, settings=tolerante)
        _observar(laxas, 2, settings=tolerante)
        assert laxas.total_count == 1

    def test_con_el_conteo_apagado_no_suma_pero_igual_cataloga(self):
        catalog = FaceCatalog()

        update = _observar(catalog, 1, settings=_settings(counting_enabled=False))

        assert update.is_new_identity is True
        assert catalog.total_count == 0
        assert len(catalog.captures) == 1

    def test_la_captura_mas_reciente_queda_primera(self):
        catalog = FaceCatalog()

        _observar(catalog, 1)
        _observar(catalog, 5)

        assert catalog.captures[0].track_id == 2


class TestReemplazoPorArea:
    def test_con_tope_1_gana_la_toma_mas_grande(self):
        """Lo que hace que la galería muestre la mejor toma de cada persona
        y no la primera, que típicamente es chica y lejana."""
        catalog = FaceCatalog()
        settings = _settings(max_captures_per_face=1)

        _observar(catalog, 1, area=100, settings=settings)
        update = _observar(catalog, 1, area=900, settings=settings)

        assert update.removed_index == 0
        assert [c.area for c in catalog.captures] == [900]

    def test_una_toma_mas_chica_del_mismo_id_se_descarta(self):
        catalog = FaceCatalog()
        settings = _settings(max_captures_per_face=1)

        _observar(catalog, 1, area=900, settings=settings)
        update = _observar(catalog, 1, area=100, settings=settings)

        assert update.capture is None
        assert update.removed_index is None
        assert [c.area for c in catalog.captures] == [900]

    def test_hasta_el_tope_se_suman_tomas_sin_reemplazar(self):
        catalog = FaceCatalog()
        settings = _settings(max_captures_per_face=3)

        for area in (100, 200, 300):
            update = _observar(catalog, 1, area=area, settings=settings)
            assert update.removed_index is None

        assert len(catalog.captures) == 3
        assert {c.track_id for c in catalog.captures} == {1}

    def test_llegado_al_tope_se_reemplaza_la_mas_chica(self):
        catalog = FaceCatalog()
        settings = _settings(max_captures_per_face=2)

        _observar(catalog, 1, area=500, settings=settings)
        _observar(catalog, 1, area=100, settings=settings)  # la más chica
        update = _observar(catalog, 1, area=800, settings=settings)

        assert update.removed_index is not None
        assert sorted(c.area for c in catalog.captures) == [500, 800]


class TestPodaYReset:
    def _llenar(self, catalog, cantidad):
        for i in range(cantidad):
            _observar(catalog, i)

    def test_poda_las_mas_viejas_hasta_el_tope(self):
        catalog = FaceCatalog(max_items=3)
        self._llenar(catalog, 5)

        sacadas = catalog.prune()

        assert sacadas == 2
        assert len(catalog.captures) == 3
        # Quedan las 3 más recientes (la lista va más reciente primero).
        assert [c.track_id for c in catalog.captures] == [5, 4, 3]

    def test_sin_exceso_no_poda_nada(self):
        catalog = FaceCatalog(max_items=10)
        self._llenar(catalog, 3)

        assert catalog.prune() == 0
        assert len(catalog.captures) == 3

    def test_reset_borra_todo_incluidos_los_ids(self):
        catalog = FaceCatalog()
        self._llenar(catalog, 2)

        catalog.reset()

        assert catalog.captures == ()
        assert catalog.total_count == 0
        # El próximo ID vuelve a ser el 1: es otra cámara.
        self._llenar(catalog, 1)
        assert catalog.captures[0].track_id == 1


class TestReinicioDiario:
    """Con el reloj inyectado. Antes esto usaba dt.datetime.now() directo
    dentro de un método de widget y no se podía testear."""

    def _catalog(self, momento: dt.datetime) -> FaceCatalog:
        reloj = {"ahora": momento}
        catalog = FaceCatalog(now=lambda: reloj["ahora"])
        catalog._reloj = reloj  # para moverlo desde el test
        return catalog

    def _contar(self, catalog, indice):
        _observar(catalog, indice)

    def test_antes_de_la_hora_no_reinicia(self):
        catalog = self._catalog(dt.datetime(2026, 9, 22, 5, 0))
        self._contar(catalog, 1)

        assert catalog.apply_daily_reset(_settings(reset_hour=6)) is False
        assert catalog.total_count == 1

    def test_pasada_la_hora_reinicia_una_vez(self):
        catalog = self._catalog(dt.datetime(2026, 9, 22, 7, 0))
        self._contar(catalog, 1)
        settings = _settings(reset_hour=6)

        assert catalog.apply_daily_reset(settings) is True
        assert catalog.total_count == 0

        self._contar(catalog, 2)
        # Mismo día: no vuelve a reiniciar aunque se llame en cada evento.
        assert catalog.apply_daily_reset(settings) is False
        assert catalog.total_count == 1

    def test_al_dia_siguiente_vuelve_a_reiniciar(self):
        catalog = self._catalog(dt.datetime(2026, 9, 22, 7, 0))
        settings = _settings(reset_hour=6)
        catalog.apply_daily_reset(settings)
        self._contar(catalog, 1)

        catalog._reloj["ahora"] = dt.datetime(2026, 9, 23, 7, 0)

        assert catalog.apply_daily_reset(settings) is True
        assert catalog.total_count == 0

    def test_con_el_conteo_apagado_no_reinicia(self):
        catalog = self._catalog(dt.datetime(2026, 9, 22, 7, 0))
        self._contar(catalog, 1)

        assert catalog.apply_daily_reset(_settings(counting_enabled=False)) is False
        assert catalog.total_count == 1

    def test_limpiar_a_mano_no_lo_vuelve_a_pisar_el_reinicio_de_hoy(self):
        """El usuario aprieta la escoba a las 9; el reinicio programado de
        las 6 ya pasó y no tiene que volver a correr hasta mañana."""
        catalog = self._catalog(dt.datetime(2026, 9, 22, 9, 0))
        self._contar(catalog, 1)

        catalog.clear_counter()
        assert catalog.total_count == 0

        self._contar(catalog, 2)
        assert catalog.apply_daily_reset(_settings(reset_hour=6)) is False
        assert catalog.total_count == 1
