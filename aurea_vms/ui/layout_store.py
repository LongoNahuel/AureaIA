"""Guardar y restaurar la disposicion de ventanas de cada usuario (Fase V4,
2026-10-07): que ventanas tenia abiertas, en que monitor, con que pestañas y
que camara en cada recuadro. Vive en la tabla `ui_layouts` (revision 0009).

Se guarda al cerrar la principal (antes de que los recuadros suelten sus
camaras) y, con 2 s de pausa, cuando cambian las pestañas o una grilla, para
no perder todo si la app se cae. Se restaura despues del login.

Nada de esto puede romper un login ni un cierre: todo error se loguea y
la app sigue con la disposicion de siempre. Lo que no se puede restaurar se
degrada en vez de fallar:
- un monitor que ya no esta -> la ventana cae en el principal;
- una geometria fuera de toda pantalla -> se centra en la elegida;
- un modulo que el rol ya no puede abrir, o que ya no existe -> no se abre;
- una camara borrada -> el recuadro queda vacio;
- un `schema_version` desconocido -> se ignora la disposicion entera.

Formato (`data`, version 1):
    {"ventanas": [
        {"principal": bool, "geometria": [x, y, w, h], "pantalla": str,
         "estado": "normal" | "maximizada" | "completa",
         "pestanas": [{"modulo": "<Clase>", "estado": {...}}],
         "actual": <indice en pestanas, -1 = Inicio>}
    ]}
Inicio no se guarda: la principal siempre la tiene.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from aurea_vms.core.permissions import can
from aurea_vms.models import repository
from aurea_vms.ui.pestanas import HOME_ROUTE_KEY

if TYPE_CHECKING:
    from aurea_vms.ui.main_window import MainWindow

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


# --- capturar ----------------------------------------------------------------


def capturar(principal: MainWindow) -> dict:
    ventanas = [_capturar_ventana(principal, True)]
    ventanas += [_capturar_ventana(v, False) for v in principal.ventanas.secundarias()]
    return {"ventanas": ventanas}


def _capturar_ventana(ventana: QWidget, es_principal: bool) -> dict:
    tabs = ventana.tabs
    pestanas = []
    actual = -1
    for i in range(tabs.count()):
        widget = tabs.widget(i)
        if widget.property("routeKey") == HOME_ROUTE_KEY:
            continue
        get_state = getattr(widget, "get_state", None)
        pestanas.append(
            {
                "modulo": type(widget).__name__,
                "estado": get_state() if callable(get_state) else {},
            }
        )
        if widget is tabs.currentWidget():
            actual = len(pestanas) - 1
    if ventana.isFullScreen():
        estado = "completa"
    elif ventana.isMaximized():
        estado = "maximizada"
    else:
        estado = "normal"
    # La geometria "normal" (la de antes de maximizar), para que al volver
    # de maximizada quede del tamaño que el operador le habia dado.
    rect = ventana.normalGeometry() if estado != "normal" else ventana.geometry()
    pantalla = ventana.screen()
    return {
        "principal": es_principal,
        "geometria": [rect.x(), rect.y(), rect.width(), rect.height()],
        "pantalla": pantalla.name() if pantalla is not None else "",
        "estado": estado,
        "pestanas": pestanas,
        "actual": actual,
    }


def guardar(principal: MainWindow) -> None:
    user_id = principal.user_id
    if user_id is None:
        return
    try:
        repository.save_ui_layout(user_id, SCHEMA_VERSION, capturar(principal))
    except Exception:
        logger.exception("No se pudo guardar la disposición de ventanas del usuario %s", user_id)


# --- restaurar ---------------------------------------------------------------


def restaurar(principal: MainWindow) -> None:
    user_id = principal.user_id
    if user_id is None:
        return
    try:
        fila = repository.get_ui_layout(user_id)
    except Exception:
        logger.exception("No se pudo leer la disposición de ventanas del usuario %s", user_id)
        return
    if fila is None:
        return
    if fila.schema_version != SCHEMA_VERSION:
        logger.warning(
            "Disposición de ventanas del usuario %s en formato %s (se entiende el %s): se ignora",
            user_id,
            fila.schema_version,
            SCHEMA_VERSION,
        )
        return
    try:
        aplicar(principal, fila.data)
    except Exception:
        logger.exception(
            "Disposición de ventanas del usuario %s ilegible: se usa la de siempre", user_id
        )


def aplicar(principal: MainWindow, data: dict) -> None:
    ventanas = data.get("ventanas") if isinstance(data, dict) else None
    if not isinstance(ventanas, list):
        return
    for guardada in ventanas:
        if not isinstance(guardada, dict):
            continue
        es_principal = bool(guardada.get("principal"))
        ventana = principal if es_principal else principal.nueva_ventana()
        abiertas = _abrir_pestanas(principal, ventana, guardada.get("pestanas"))
        if not es_principal and not abiertas:
            ventana.close()  # una secundaria sin nada que mostrar no se arma
            continue
        _elegir_pestana(ventana, abiertas, guardada.get("actual"), es_principal)
        _ubicar(ventana, guardada)


def _abrir_pestanas(principal: MainWindow, ventana: QWidget, pestanas: object) -> list[QWidget]:
    from aurea_vms.ui import main_window as mw

    abiertas: list[QWidget] = []
    if not isinstance(pestanas, list):
        return abiertas
    por_nombre = {cls.__name__: i for i, (_l, _i, cls) in enumerate(mw.MODULES)}
    for pestana in pestanas:
        if not isinstance(pestana, dict):
            continue
        index = por_nombre.get(pestana.get("modulo"))
        if index is None:
            logger.info("Disposición: el módulo %r ya no existe, no se abre", pestana.get("modulo"))
            continue
        label, _icono, module_cls = mw.MODULES[index]
        if not can(mw.MODULE_PERMISSIONS[label]):
            continue  # el rol cambio desde que se guardo: sin aviso, no se abre
        if module_cls not in mw.MULTI_INSTANCIA and principal.ventanas.buscar_modulo(module_cls):
            continue  # de los de una sola instancia, el primero gana
        widget = principal._crear_modulo(index, ventana)
        estado = pestana.get("estado")
        set_state = getattr(widget, "set_state", None)
        if isinstance(estado, dict) and estado and callable(set_state):
            set_state(estado)
        abiertas.append(widget)
    return abiertas


def _elegir_pestana(
    ventana: QWidget, abiertas: list[QWidget], actual: object, es_principal: bool
) -> None:
    if isinstance(actual, int) and 0 <= actual < len(abiertas):
        ventana.tabs.setCurrentWidget(abiertas[actual])
    elif es_principal:
        ventana.tabs.setCurrentIndex(0)  # Inicio


def _ubicar(ventana: QWidget, guardada: dict) -> None:
    geometria = guardada.get("geometria")
    if (
        isinstance(geometria, list)
        and len(geometria) == 4
        and all(isinstance(v, int) for v in geometria)
    ):
        ventana.setGeometry(
            rect_en_pantalla(QRect(*geometria), str(guardada.get("pantalla") or ""))
        )
    estado = guardada.get("estado")
    if estado == "completa":
        ventana.showFullScreen()
    elif estado == "maximizada":
        ventana.showMaximized()
    else:
        ventana.show()


def rect_en_pantalla(rect: QRect, nombre_pantalla: str) -> QRect:
    """El rect guardado, si cae en la pantalla con ese nombre. Si esa
    pantalla ya no esta, o el rect quedo afuera, se centra en la pantalla
    (la guardada si existe, la principal si no) y se achica a su tamaño."""
    pantallas = QGuiApplication.screens()
    pantalla = next((p for p in pantallas if p.name() == nombre_pantalla), None)
    if pantalla is not None and pantalla.availableGeometry().intersects(rect):
        return rect
    destino = (pantalla or QGuiApplication.primaryScreen()).availableGeometry()
    ajustado = QRect(rect)
    ajustado.setWidth(min(rect.width(), destino.width()))
    ajustado.setHeight(min(rect.height(), destino.height()))
    ajustado.moveCenter(destino.center())
    return ajustado
