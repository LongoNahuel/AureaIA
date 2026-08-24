"""Funciones CRUD basicas. La sesion usa expire_on_commit=False (ver db.py),
por lo que los objetos devueltos siguen siendo legibles luego de cerrarse
la sesion (sirven como DTOs de solo lectura fuera del `with`).
"""

from __future__ import annotations

from aurea_vms.models.alarm_event import AlarmEvent as AlarmEventRow
from aurea_vms.models.alarm_rule import AlarmRule
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.models.db import get_session
from aurea_vms.models.device import Device
from aurea_vms.models.user import User


def add_device(**fields: object) -> Device:
    with get_session() as session:
        device = Device(**fields)
        session.add(device)
        session.flush()
        session.refresh(device)
        return device


def list_devices() -> list[Device]:
    with get_session() as session:
        return list(session.query(Device).order_by(Device.id).all())


def get_device(device_id: int) -> Device | None:
    with get_session() as session:
        return session.get(Device, device_id)


def update_device_status(device_id: int, status: str) -> None:
    with get_session() as session:
        device = session.get(Device, device_id)
        if device is not None:
            device.status = status


def update_device(device_id: int, **fields: object) -> None:
    with get_session() as session:
        device = session.get(Device, device_id)
        if device is not None:
            for key, value in fields.items():
                setattr(device, key, value)


def delete_device(device_id: int) -> None:
    with get_session() as session:
        device = session.get(Device, device_id)
        if device is not None:
            session.delete(device)


def list_analytics_configs(device_id: int | None = None) -> list[AnalyticsConfig]:
    with get_session() as session:
        query = session.query(AnalyticsConfig).order_by(AnalyticsConfig.id)
        if device_id is not None:
            query = query.filter(AnalyticsConfig.device_id == device_id)
        return list(query.all())


def get_analytics_config(config_id: int) -> AnalyticsConfig | None:
    with get_session() as session:
        return session.get(AnalyticsConfig, config_id)


def get_analytics_config_for(device_id: int, analyzer_name: str) -> AnalyticsConfig | None:
    with get_session() as session:
        return (
            session.query(AnalyticsConfig)
            .filter(
                AnalyticsConfig.device_id == device_id,
                AnalyticsConfig.analyzer_name == analyzer_name,
            )
            .one_or_none()
        )


def upsert_analytics_config(device_id: int, analyzer_name: str, **fields: object) -> AnalyticsConfig:
    with get_session() as session:
        config = (
            session.query(AnalyticsConfig)
            .filter(
                AnalyticsConfig.device_id == device_id,
                AnalyticsConfig.analyzer_name == analyzer_name,
            )
            .one_or_none()
        )
        if config is None:
            config = AnalyticsConfig(device_id=device_id, analyzer_name=analyzer_name, **fields)
            session.add(config)
        else:
            for key, value in fields.items():
                setattr(config, key, value)
        session.flush()
        session.refresh(config)
        return config


def set_analytics_config_enabled(config_id: int, enabled: bool) -> None:
    with get_session() as session:
        config = session.get(AnalyticsConfig, config_id)
        if config is not None:
            config.enabled = enabled


def add_alarm_rule(**fields: object) -> AlarmRule:
    with get_session() as session:
        rule = AlarmRule(**fields)
        session.add(rule)
        session.flush()
        session.refresh(rule)
        return rule


def list_alarm_rules(device_id: int | None = None) -> list[AlarmRule]:
    with get_session() as session:
        query = session.query(AlarmRule).order_by(AlarmRule.id)
        if device_id is not None:
            query = query.filter(AlarmRule.device_id == device_id)
        return list(query.all())


def list_alarm_rules_for(device_id: int, analyzer_name: str) -> list[AlarmRule]:
    """Reglas habilitadas que aplican a este device_id: las especificas de
    esa camara + las que aplican a "todas las camaras" (device_id NULL)."""
    with get_session() as session:
        return list(
            session.query(AlarmRule)
            .filter(
                AlarmRule.analyzer_name == analyzer_name,
                AlarmRule.enabled.is_(True),
                (AlarmRule.device_id == device_id) | (AlarmRule.device_id.is_(None)),
            )
            .all()
        )


def get_alarm_rule(rule_id: int) -> AlarmRule | None:
    with get_session() as session:
        return session.get(AlarmRule, rule_id)


def update_alarm_rule(rule_id: int, **fields: object) -> None:
    with get_session() as session:
        rule = session.get(AlarmRule, rule_id)
        if rule is not None:
            for key, value in fields.items():
                setattr(rule, key, value)


def set_alarm_rule_enabled(rule_id: int, enabled: bool) -> None:
    with get_session() as session:
        rule = session.get(AlarmRule, rule_id)
        if rule is not None:
            rule.enabled = enabled


def delete_alarm_rule(rule_id: int) -> None:
    with get_session() as session:
        rule = session.get(AlarmRule, rule_id)
        if rule is not None:
            session.delete(rule)


def add_alarm_event(**fields: object) -> AlarmEventRow:
    with get_session() as session:
        event = AlarmEventRow(**fields)
        session.add(event)
        session.flush()
        session.refresh(event)
        return event


def list_alarm_events(limit: int = 200) -> list[AlarmEventRow]:
    with get_session() as session:
        return list(
            session.query(AlarmEventRow).order_by(AlarmEventRow.id.desc()).limit(limit).all()
        )


def get_alarm_event(alarm_event_id: int) -> AlarmEventRow | None:
    with get_session() as session:
        return session.get(AlarmEventRow, alarm_event_id)


def update_alarm_event(alarm_event_id: int, **fields: object) -> None:
    with get_session() as session:
        event = session.get(AlarmEventRow, alarm_event_id)
        if event is not None:
            for key, value in fields.items():
                setattr(event, key, value)


def set_alarm_event_status(alarm_event_id: int, status: str) -> None:
    with get_session() as session:
        event = session.get(AlarmEventRow, alarm_event_id)
        if event is not None:
            event.status = status


def count_users() -> int:
    with get_session() as session:
        return session.query(User).count()


def add_user(**fields: object) -> User:
    with get_session() as session:
        user = User(**fields)
        session.add(user)
        session.flush()
        session.refresh(user)
        return user


def get_user_by_username(username: str) -> User | None:
    with get_session() as session:
        return session.query(User).filter(User.username == username).one_or_none()


def list_users() -> list[User]:
    with get_session() as session:
        return list(session.query(User).order_by(User.id).all())


def delete_user(user_id: int) -> None:
    with get_session() as session:
        user = session.get(User, user_id)
        if user is not None:
            session.delete(user)


def update_user(user_id: int, **fields: object) -> None:
    with get_session() as session:
        user = session.get(User, user_id)
        if user is not None:
            for key, value in fields.items():
                setattr(user, key, value)
