"""Funciones CRUD basicas. La sesion usa expire_on_commit=False (ver db.py),
por lo que los objetos devueltos siguen siendo legibles luego de cerrarse
la sesion (sirven como DTOs de solo lectura fuera del `with`).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from typing import TypeVar

from sqlalchemy import func
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aurea_vms.models.alarm_event import STATUS_RESOLVED
from aurea_vms.models.alarm_event import AlarmEvent as AlarmEventRow
from aurea_vms.models.alarm_rule import AlarmRule
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.models.db import get_session
from aurea_vms.models.device import Device
from aurea_vms.models.errors import DuplicateError, RepositoryError
from aurea_vms.models.media_asset import MediaAsset
from aurea_vms.models.site import Site
from aurea_vms.models.user import User
from aurea_vms.models.zone import Zone

T = TypeVar("T")


def _traducir(exc: IntegrityError) -> RepositoryError:
    """Convierte una violacion de constraint en una excepcion de dominio.
    Se miran las dos frases porque la de sqlite y la de postgres no son la
    misma, y la capa esta pensada para poder cambiar de motor."""
    detalle = str(getattr(exc, "orig", exc))
    if "UNIQUE constraint failed" in detalle or "duplicate key" in detalle.lower():
        return DuplicateError(detalle)
    return RepositoryError(detalle)


@contextmanager
def _escritura() -> Iterator[Session]:
    """get_session() con la frontera de excepciones: de repository nunca
    sale una excepcion de SQLAlchemy, siempre una de dominio."""
    try:
        with get_session() as session:
            yield session
    except IntegrityError as exc:
        raise _traducir(exc) from exc


def _insert(model: type[T], fields: dict) -> T:
    """Alta generica: los siete add_* hacian exactamente estas cinco lineas.
    El refresh() deja el objeto legible fuera de la sesion (ver el docstring
    del modulo)."""
    with _escritura() as session:
        row = model(**fields)
        session.add(row)
        session.flush()
        session.refresh(row)
        return row


@cache
def columnas_de(model: type) -> frozenset[str]:
    """Nombres de las columnas mapeadas de un modelo."""
    return frozenset(columna.key for columna in sa_inspect(model).mapper.column_attrs)


def _validar_campos(model: type, fields: dict) -> None:
    """Un kwarg que no es columna levanta, en vez de perderse en silencio.

    Los add_* ya lo hacen desde siempre: el constructor declarativo de
    SQLAlchemy levanta TypeError ante un kwarg desconocido. Los update_*
    usaban setattr, que crea alegremente un atributo Python que nunca llega
    al UPDATE -- el clasico "guardé y no pasó nada", sin error, sin log y sin
    forma de darse cuenta salvo mirando la base.
    """
    desconocidos = sorted(set(fields) - columnas_de(model))
    if desconocidos:
        raise ValueError(
            f"{model.__name__} no tiene la(s) columna(s) {desconocidos}. "
            f"Válidas: {sorted(columnas_de(model))}"
        )


def _update(model: type, row_id: int, fields: dict) -> None:
    """Un id inexistente es un no-op silencioso, igual que antes."""
    _validar_campos(model, fields)
    with _escritura() as session:
        row = session.get(model, row_id)
        if row is not None:
            for key, value in fields.items():
                setattr(row, key, value)


def _normalize_analyzer_name(name: str) -> str:
    return "door_state" if name == "motion_detection" else name


def add_site(**fields: object) -> Site:
    return _insert(Site, fields)


def list_sites() -> list[Site]:
    with get_session() as session:
        return list(session.query(Site).order_by(Site.name).all())


def get_site(site_id: int) -> Site | None:
    with get_session() as session:
        return session.get(Site, site_id)


def update_site(site_id: int, **fields: object) -> None:
    _update(Site, site_id, fields)


def delete_site(site_id: int) -> None:
    """Borra el sitio y sus zonas (ondelete=CASCADE en Zone.site_id); las
    camaras de esas zonas NO se borran, quedan con zone_id NULL ("Sin
    zona").

    La nulificacion se hace aca ademas del ondelete=SET NULL del modelo:
    una DB que migro devices.zone_id via ALTER TABLE puede tener el FK
    sin accion de borrado, y con PRAGMA foreign_keys=ON el CASCADE de las
    zonas fallaria con IntegrityError."""
    with get_session() as session:
        site = session.get(Site, site_id)
        if site is not None:
            zone_ids = session.query(Zone.id).filter(Zone.site_id == site_id)
            session.query(Device).filter(Device.zone_id.in_(zone_ids)).update(
                {Device.zone_id: None}, synchronize_session=False
            )
            session.delete(site)


def add_device(**fields: object) -> Device:
    return _insert(Device, fields)


def _filtrar_por_ubicacion(query, zone_id: int | None, site_id: int | None):
    """zone_id filtra por una zona puntual; site_id por todas las zonas de un
    sitio (el filtro del selector global de la topbar). El sitio va por JOIN:
    antes se traian todas las Zone del sitio a Python para armar un IN(...)."""
    if zone_id is not None:
        query = query.filter(Device.zone_id == zone_id)
    if site_id is not None:
        query = query.join(Zone, Device.zone_id == Zone.id).filter(Zone.site_id == site_id)
    return query


def list_devices(zone_id: int | None = None, site_id: int | None = None) -> list[Device]:
    """None/None = todas las camaras."""
    with get_session() as session:
        query = _filtrar_por_ubicacion(session.query(Device), zone_id, site_id)
        return list(query.order_by(Device.id).all())


def count_devices_by_status(site_id: int | None = None) -> dict[str, int]:
    """{estado: cantidad} en UNA consulta agregada. El dashboard traia TODOS
    los dispositivos cada 5s solo para contarlos por status en Python."""
    with get_session() as session:
        query = _filtrar_por_ubicacion(
            session.query(Device.status, func.count(Device.id)), None, site_id
        )
        return dict(query.group_by(Device.status).all())


def add_zone(**fields: object) -> Zone:
    return _insert(Zone, fields)


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
    _update(Zone, zone_id, fields)


def delete_zone(zone_id: int) -> None:
    """Las camaras de la zona quedan con zone_id NULL ("Sin zona"). Se
    nulifica aca por el mismo motivo que en delete_site: en DBs migradas
    el FK de devices.zone_id puede no tener ON DELETE SET NULL."""
    with get_session() as session:
        zone = session.get(Zone, zone_id)
        if zone is not None:
            session.query(Device).filter(Device.zone_id == zone_id).update(
                {Device.zone_id: None}, synchronize_session=False
            )
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
    _update(Device, device_id, fields)


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
    analyzer_name = _normalize_analyzer_name(analyzer_name)
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
    analyzer_name = _normalize_analyzer_name(analyzer_name)
    _validar_campos(AnalyticsConfig, fields)
    with _escritura() as session:
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
    if "analyzer_name" in fields:
        fields["analyzer_name"] = _normalize_analyzer_name(str(fields["analyzer_name"]))
    return _insert(AlarmRule, fields)


def list_alarm_rules(device_id: int | None = None) -> list[AlarmRule]:
    with get_session() as session:
        query = session.query(AlarmRule).order_by(AlarmRule.id)
        if device_id is not None:
            query = query.filter(AlarmRule.device_id == device_id)
        return list(query.all())


def list_alarm_rules_for(device_id: int, analyzer_name: str) -> list[AlarmRule]:
    """Reglas habilitadas que aplican a este device_id: las especificas de
    esa camara + las que aplican a "todas las camaras" (device_id NULL)."""
    analyzer_name = _normalize_analyzer_name(analyzer_name)
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
    _update(AlarmRule, rule_id, fields)


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
    return _insert(AlarmEventRow, fields)


def _filtrar_eventos(query, device_id: int | None, site_id: int | None):
    """Los eventos cuelgan de una camara, y la camara de una zona: filtrar
    por sitio es un JOIN de dos saltos."""
    if device_id is not None:
        query = query.filter(AlarmEventRow.device_id == device_id)
    if site_id is not None:
        query = (
            query.join(Device, AlarmEventRow.device_id == Device.id)
            .join(Zone, Device.zone_id == Zone.id)
            .filter(Zone.site_id == site_id)
        )
    return query


def list_alarm_events(
    limit: int = 200, *, device_id: int | None = None, site_id: int | None = None
) -> list[AlarmEventRow]:
    """Los mas recientes primero. Sin los filtros, el dashboard traia los
    ultimos 200 GLOBALES y despues descartaba en Python los de otros sitios:
    con el filtro de sitio activo mostraba un subconjunto arbitrario en vez
    de los ultimos 200 de ese sitio."""
    with get_session() as session:
        query = _filtrar_eventos(session.query(AlarmEventRow), device_id, site_id)
        return list(query.order_by(AlarmEventRow.id.desc()).limit(limit).all())


def count_alarm_events(
    *,
    device_id: int | None = None,
    site_id: int | None = None,
    severity: str | None = None,
    status: str | None = None,
    status_not: str | None = None,
) -> int:
    """COUNT agregado sobre los indices. Los contadores del dashboard se
    calculaban en Python sobre la pagina de 200, asi que la tarjeta de
    totales se clavaba en 200 apenas habia mas eventos que eso."""
    with get_session() as session:
        query = _filtrar_eventos(session.query(func.count(AlarmEventRow.id)), device_id, site_id)
        if severity is not None:
            query = query.filter(AlarmEventRow.severity == severity)
        if status is not None:
            query = query.filter(AlarmEventRow.status == status)
        if status_not is not None:
            query = query.filter(AlarmEventRow.status != status_not)
        return query.scalar() or 0


def count_pending_alarm_events(site_id: int | None = None) -> int:
    """Alarmas sin resolver -- un COUNT sobre el indice, para el tile del
    dashboard (antes traia 500 filas completas cada 5s para contarlas)."""
    return count_alarm_events(site_id=site_id, status_not=STATUS_RESOLVED)


def get_alarm_event(alarm_event_id: int) -> AlarmEventRow | None:
    with get_session() as session:
        return session.get(AlarmEventRow, alarm_event_id)


def update_alarm_event(alarm_event_id: int, **fields: object) -> None:
    _update(AlarmEventRow, alarm_event_id, fields)


def set_alarm_event_status(alarm_event_id: int, status: str) -> None:
    with get_session() as session:
        event = session.get(AlarmEventRow, alarm_event_id)
        if event is not None:
            event.status = status


def add_media_asset(**fields: object) -> MediaAsset:
    return _insert(MediaAsset, fields)


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
    return _insert(User, fields)


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
    _update(User, user_id, fields)
