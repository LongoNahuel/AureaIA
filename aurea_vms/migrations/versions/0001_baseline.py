"""Baseline: el esquema tal como estaba al adoptar Alembic (2026-09-22).

Generada con --autogenerate contra una base VACIA, asi que contiene las 8
tablas enteras. Sobre una base que ya existia NO corre: `init_db` la adopta
(la lleva a esta forma con las columnas ad-hoc que se venian agregando a
mano) y la marca en esta revision. Ver aurea_vms/migrations/adopcion.py.

Congelada a proposito: retrata el esquema del 22/09, con sus defectos
incluidos -- las columnas nullables de alarm_rules, la falta de unique en
analytics_configs(device_id, analyzer_name) y en zones(site_id, name). Eso
NO se corrige aca: se corrige en revisiones posteriores, que es el punto de
tener revisiones. Si esta se "mejora", las bases ya migradas quedan en un
estado distinto al de las nuevas.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sites")),
        sa.UniqueConstraint("name", name=op.f("uq_sites_name")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=False),
        sa.Column("salt", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "zones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("critical", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_zones_site_id_sites"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_zones")),
    )
    with op.batch_alter_table("zones", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_zones_site_id"), ["site_id"], unique=False)

    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("device_type", sa.String(length=10), nullable=False),
        sa.Column("channel", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=120), nullable=False),
        sa.Column("password", sa.String(length=120), nullable=False),
        sa.Column("rtsp_main_url", sa.String(length=500), nullable=False),
        sa.Column("rtsp_sub_url", sa.String(length=500), nullable=True),
        sa.Column("onvif_port", sa.Integer(), nullable=True),
        sa.Column("has_ptz", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("manufacturer", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("firmware_version", sa.String(length=120), nullable=True),
        sa.Column("serial_number", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["zones.id"], name=op.f("fk_devices_zone_id_zones"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
    )
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_devices_zone_id"), ["zone_id"], unique=False)

    op.create_table(
        "alarm_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("analyzer_name", sa.String(length=60), nullable=True),
        sa.Column("object_classes", sa.JSON(), nullable=True),
        sa.Column("min_confidence", sa.Float(), nullable=True),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("schedule_days", sa.JSON(), nullable=True),
        sa.Column("schedule_start", sa.String(length=5), nullable=True),
        sa.Column("schedule_end", sa.String(length=5), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_alarm_rules_device_id_devices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alarm_rules")),
    )
    with op.batch_alter_table("alarm_rules", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_alarm_rules_device_id"), ["device_id"], unique=False)

    op.create_table(
        "analytics_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("analyzer_name", sa.String(length=60), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("confidence_threshold", sa.Float(), nullable=False),
        sa.Column("roi_x", sa.Integer(), nullable=True),
        sa.Column("roi_y", sa.Integer(), nullable=True),
        sa.Column("roi_w", sa.Integer(), nullable=True),
        sa.Column("roi_h", sa.Integer(), nullable=True),
        sa.Column("object_classes", sa.JSON(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_analytics_configs_device_id_devices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analytics_configs")),
    )
    with op.batch_alter_table("analytics_configs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_analytics_configs_device_id"), ["device_id"], unique=False
        )

    op.create_table(
        "alarm_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=True),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.Float(), nullable=False),
        sa.Column("object_class", sa.String(length=60), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.String(length=4000), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_alarm_events_device_id_devices"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["alarm_rules.id"],
            name=op.f("fk_alarm_events_rule_id_alarm_rules"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alarm_events")),
    )
    with op.batch_alter_table("alarm_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_alarm_events_device_id"), ["device_id"], unique=False)
        batch_op.create_index("ix_alarm_events_device_ts", ["device_id", "timestamp"], unique=False)
        batch_op.create_index(batch_op.f("ix_alarm_events_timestamp"), ["timestamp"], unique=False)

    op.create_table(
        "media_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("alarm_event_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("timestamp", sa.Float(), nullable=False),
        sa.Column("rel_path", sa.String(length=500), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["alarm_event_id"],
            ["alarm_events.id"],
            name=op.f("fk_media_assets_alarm_event_id_alarm_events"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_media_assets_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_media_assets_device_id_devices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_assets")),
        sa.UniqueConstraint("rel_path", name=op.f("uq_media_assets_rel_path")),
    )
    with op.batch_alter_table("media_assets", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_media_assets_alarm_event_id"), ["alarm_event_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_media_assets_created_by"), ["created_by"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_media_assets_device_id"), ["device_id"], unique=False)
        batch_op.create_index("ix_media_assets_device_ts", ["device_id", "timestamp"], unique=False)
        batch_op.create_index("ix_media_assets_kind_ts", ["kind", "timestamp"], unique=False)
        batch_op.create_index(batch_op.f("ix_media_assets_timestamp"), ["timestamp"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("media_assets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_media_assets_timestamp"))
        batch_op.drop_index("ix_media_assets_kind_ts")
        batch_op.drop_index("ix_media_assets_device_ts")
        batch_op.drop_index(batch_op.f("ix_media_assets_device_id"))
        batch_op.drop_index(batch_op.f("ix_media_assets_created_by"))
        batch_op.drop_index(batch_op.f("ix_media_assets_alarm_event_id"))

    op.drop_table("media_assets")
    with op.batch_alter_table("alarm_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_alarm_events_timestamp"))
        batch_op.drop_index("ix_alarm_events_device_ts")
        batch_op.drop_index(batch_op.f("ix_alarm_events_device_id"))

    op.drop_table("alarm_events")
    with op.batch_alter_table("analytics_configs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_analytics_configs_device_id"))

    op.drop_table("analytics_configs")
    with op.batch_alter_table("alarm_rules", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_alarm_rules_device_id"))

    op.drop_table("alarm_rules")
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_devices_zone_id"))

    op.drop_table("devices")
    with op.batch_alter_table("zones", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_zones_site_id"))

    op.drop_table("zones")
    op.drop_table("users")
    op.drop_table("sites")
