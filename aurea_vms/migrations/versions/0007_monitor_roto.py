"""Puerta abierta/cerrada pasa a ser Monitor roto (golpes).

Paso de datos, sin cambios de esquema. La analitica de puerta se retira y su
lugar lo ocupa `monitor_tamper` (core/analytics/monitor_tamper_analyzer.py):
las zonas que se dibujaban sobre una puerta ahora marcan la pantalla de una
maquina, y se detectan patadas y golpes con la mano sobre ella.

- `analytics_configs`: `door_state` -> `monitor_tamper`. Se conservan las
  zonas y los fps. Los parametros propios de la puerta (umbral de cambio,
  porcentaje de apertura, confirmacion, umbral de tiempo) no significan nada
  para la analitica nueva -- `confirmation_frames: 5` le exigiria cinco
  muestras seguidas con el pie sobre la pantalla y no detectaria patadas --,
  asi que salen de los parametros activos pero se guardan en
  `puerta_legado`: el downgrade los restaura tal cual.
- `alarm_rules`: `door_state` -> `monitor_tamper` con clases vacias
  (cualquier golpe). Las clases de puerta ("puerta_abierta") no existen en
  la analitica nueva. El downgrade vuelve a `door_state` con clases vacias:
  la unica perdida posible es la clase elegida en una regla de puerta.

Mismo patron que 0002 con `motion_detection` -> `door_state`; el repository
normaliza los dos nombres retirados a `monitor_tamper`.

Revision ID: 0007_monitor_roto
Revises: 0006_jerarquia_grabadores
Create Date: 2026-09-23
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_monitor_roto"
down_revision: str | None = "0006_jerarquia_grabadores"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEJO = "door_state"
NUEVO = "monitor_tamper"
# Parametros de puerta que siguen valiendo para el monitor.
COMPARTIDOS = ("zones", "fps")
LEGADO = "puerta_legado"


def _cargar(crudo) -> dict:
    if isinstance(crudo, dict):
        return dict(crudo)
    if not crudo:
        return {}
    try:
        valor = json.loads(crudo)
    except (TypeError, ValueError):
        return {}
    return valor if isinstance(valor, dict) else {}


def _renombrar_configs(conn, desde: str, hacia: str, transformar) -> None:
    filas = conn.execute(
        sa.text("SELECT id, params FROM analytics_configs WHERE analyzer_name = :nombre"),
        {"nombre": desde},
    ).fetchall()
    for fila_id, crudo in filas:
        conn.execute(
            sa.text(
                "UPDATE analytics_configs SET analyzer_name = :hacia, params = :params "
                "WHERE id = :id"
            ),
            {"hacia": hacia, "params": json.dumps(transformar(_cargar(crudo))), "id": fila_id},
        )


def _renombrar_reglas(conn, desde: str, hacia: str) -> None:
    conn.execute(
        sa.text(
            "UPDATE alarm_rules SET analyzer_name = :hacia, object_classes = '[]' "
            "WHERE analyzer_name = :desde"
        ),
        {"hacia": hacia, "desde": desde},
    )


def a_monitor(params: dict) -> dict:
    nuevos = {clave: params[clave] for clave in COMPARTIDOS if clave in params}
    legado = {clave: valor for clave, valor in params.items() if clave not in COMPARTIDOS}
    if legado:
        nuevos[LEGADO] = legado
    return nuevos


def a_puerta(params: dict) -> dict:
    viejos = {clave: params[clave] for clave in COMPARTIDOS if clave in params}
    legado = params.get(LEGADO)
    if isinstance(legado, dict):
        viejos.update(legado)
    return viejos


def upgrade() -> None:
    conn = op.get_bind()
    _renombrar_configs(conn, VIEJO, NUEVO, a_monitor)
    _renombrar_reglas(conn, VIEJO, NUEVO)


def downgrade() -> None:
    conn = op.get_bind()
    _renombrar_configs(conn, NUEVO, VIEJO, a_puerta)
    _renombrar_reglas(conn, NUEVO, VIEJO)
