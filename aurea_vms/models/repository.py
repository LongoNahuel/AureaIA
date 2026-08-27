"""Funciones CRUD basicas. La sesion usa expire_on_commit=False (ver db.py),
por lo que los objetos devueltos siguen siendo legibles luego de cerrarse
la sesion (sirven como DTOs de solo lectura fuera del `with`).
"""

from __future__ import annotations

from sqlalchemy import func

from aurea_vms.models.alarm_event import AlarmEvent as AlarmEventRow
from aurea_vms.models.alarm_rule import AlarmRule
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.models.db import get_session
from aurea_vms.models.device import Device
from aurea_vms.models.media_asset import MediaAsset
from aurea_vms.models.site import Site
from aurea_vms.models.user import User
from aurea_vms.models.zone import Zone


def add_site(**fields: object) -> Site:
    with get_session() as session:
        site = Site(**fields)
        session.add(site)
        session.flush()
        session.refresh(site)
        return site


def list_sites() -> list[Site]:
    with get_session() as session:
        return list(session.query(Site).order_by(Site.name).all())


def get_site(site_id: int) -> Site | None:
    with get_session() as session:
        return session.get(Site, site_id)


def update_site(site_id: int, **fields: object) -> None:
    with get_session() as session:
        site = session.get(Site, site_id)
        if site is not None:
            for key, value in fields.items():
                setattr(site, key, value)


def delete_site(site_id: int) -> None:
    """Borra el sitio y sus zonas (ondelete=CASCADE en Zone.site_id); las
    camaras de esas zonas NO se borran, quedan con zone_id NULL ("Sin
    zona") gracias al ondelete=SET NULL en Device.zone_id."""
    with get_session() as session:
        site = session.get(Site, site_id)
        if site is not None:
            session.delete(site)


def add_device(**fields: object) -> Device:
    with get_session() as session:
        device = Device(**fields)
        session.add(device)
        session.flush()
        session.refresh(device)
        return device


def list_devices(zone_id: int | None = None, site_id: int | None = None) -> list[Device]:
    """zone_id filtra por una zona puntual; site_id filtra por todas las
    zonas de un sitio (el filtro del selector global de la topbar).
    None/None = todas las camaras."""
    with get_session() as session:
        query = session.query(Device).order_by(Device.id)
        if zone_id is not None:
            query = query.filter(Device.zone_id == zone_id)
        if site_id is not None:
            zone_ids = [z.id for z in session.query(Zone).filter(Zone.site_id == site_id).all()]
            query = query.filter(Device.zone_id.in_(zone_ids))
        return list(query.all())


def add_zone(**fields: object) -> Zone:
    with get_session() as session:
        zone = Zone(**fields)
        session.add(zone)
        session.flush()
        session.refresh(zone)
        return zone


def list_zones(site_id: int | None = None) -> list[Zone]:
    with get_session() as session:
        query = session.query(Zone).order_by(Zone.id)
        if site_id is not None:
            query = query.filter(Zone.site_id == site_id)
        return list(query.all())


def get_zone(zone_id: int) -> Zone | None:
    with get_session() as session:
        return session.get(Zone, zone_id)


def update_zone(zone_id: int, **fields: object) -> None:
    with get_session() as session:
        zone = session.get(Zone, zone_id)
        if zone is not None:
            for key, value in fields.items():
                setattr(zone, key, value)


def delete_zone(zone_id: int) -> None:
    with get_session() as session:
        zone = session.get(Zone, zone_id)
        if zone is not None:
            session.delete(zone)


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


def upsert_analytics_config(
    device_id: int, analyzer_name: str, **fields: object
) -> AnalyticsConfig:
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


def count_pending_alarm_events() -> int:
    """Alarmas sin resolver -- un COUNT sobre el indice, para el tile del
    dashboard (antes traia 500 filas completas cada 5s para contarlas)."""
    with get_session() as session:
        return (
            session.query(func.count(AlarmEventRow.id))
            .filter(AlarmEventRow.status != "resuelta")
            .scalar()
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


def add_media_asset(**fields: object) -> MediaAsset:
    with get_session() as session:
        asset = MediaAsset(**fields)
        session.add(asset)
        session.flush()
        session.refresh(asset)
        return asset


def get_media_asset(media_id: int) -> MediaAsset | None:
    with get_session() as session:
        return session.get(MediaAsset, media_id)


def list_media(
    *,
    kind: str | None = None,
    device_id: int | None = None,
    alarm_event_id: int | None = None,
    created_by: int | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[MediaAsset]:
    """Busqueda de media SIEMPRE por indice (nunca escaneando disco):
    cualquier combinacion de filtros, mas nuevo primero, paginada."""
    with get_session() as session:
        query = session.query(MediaAsset)
        if kind is not None:
            query = query.filter(MediaAsset.kind == kind)
        if device_id is not None:
            query = query.filter(MediaAsset.device_id == device_id)
        if alarm_event_id is not None:
            query = query.filter(MediaAsset.alarm_event_id == alarm_event_id)
        if created_by is not None:
            query = query.filter(MediaAsset.created_by == created_by)
        if since is not None:
            query = query.filter(MediaAsset.timestamp >= since)
        if until is not None:
            query = query.filter(MediaAsset.timestamp < until)
        return list(query.order_by(MediaAsset.timestamp.desc()).limit(limit).offset(offset).all())


def list_media_for_events(event_ids: list[int]) -> dict[int, list[MediaAsset]]:
    """Media de un lote de eventos en UNA consulta (el feed de alarmas
    muestra hasta 200 filas: una query por fila seria el clasico N+1)."""
    if not event_ids:
        return {}
    with get_session() as session:
        assets = (
            session.query(MediaAsset)
            .filter(MediaAsset.alarm_event_id.in_(event_ids))
            .order_by(MediaAsset.id)
            .all()
        )
    grouped: dict[int, list[MediaAsset]] = {}
    for asset in assets:
        grouped.setdefault(asset.alarm_event_id, []).append(asset)
    return grouped


def list_media_oldest_first(
    *, older_than: float | None = None, limit: int = 500
) -> list[MediaAsset]:
    """Para la retencion: candidatos a purga, mas viejo primero."""
    with get_session() as session:
        query = session.query(MediaAsset)
        if older_than is not None:
            query = query.filter(MediaAsset.timestamp < older_than)
        return list(query.order_by(MediaAsset.timestamp).limit(limit).all())


def total_media_size_bytes() -> int:
    """Un SUM sobre el indice -- jamas un walk del filesystem."""
    with get_session() as session:
        return session.query(func.coalesce(func.sum(MediaAsset.size_bytes), 0)).scalar()


def delete_media_asset(media_id: int) -> None:
    with get_session() as session:
        asset = session.get(MediaAsset, media_id)
        if asset is not None:
            session.delete(asset)


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
