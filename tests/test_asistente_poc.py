"""Prototipo del asistente (tools/asistente, 2026-10-08): las herramientas de
solo lectura, los permisos por rol, la auditoria y el ciclo con el modelo.
El modelo es un cliente falso con respuestas guionadas: estos tests no
llaman a la API ni necesitan el SDK `anthropic`."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from aurea_vms.core import media_store
from aurea_vms.models import repository
from aurea_vms.models.alarm_event import STATUS_ACKNOWLEDGED, STATUS_INVESTIGATING, STATUS_NEW
from aurea_vms.models.media_asset import KIND_CLIP, KIND_SNAPSHOT
from aurea_vms.models.user import ROLE_OPERATOR, User

_DIR = Path(__file__).resolve().parents[1] / "tools" / "asistente"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


asistente = _load("asistente")
preguntas = _load("preguntas")


def _user(role: str) -> User:
    return User(username=role, password_hash="h", salt="s", role=role)


@pytest.fixture()
def datos(temp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(media_store, "settings", SimpleNamespace(media_dir=tmp_path / "media"))
    sala = repository.add_site(name="CASINO")
    ruleta = repository.add_device(
        name="Ruleta RA-04", ip="10.0.0.1", rtsp_main_url="rtsp://r", status="online"
    )
    repository.update_device(ruleta.id, site_id=sala.id)
    caja = repository.add_device(name="Caja", ip="10.0.0.2", rtsp_main_url="rtsp://c")
    repository.upsert_analytics_config(
        ruleta.id, "monitor_tamper", enabled=True, params={"modo": "ruleta", "fps": 25}
    )
    # Una analitica oculta (no se ofrece en la interfaz): el asistente no la muestra.
    repository.upsert_analytics_config(caja.id, "people_counting", enabled=True, params={})
    now = time.time()
    ids = []
    for offset, status, kind in (
        (10, STATUS_NEW, "fichas_tras_no_va_mas"),
        (20, STATUS_INVESTIGATING, "fichas_tras_no_va_mas"),
        (7200, STATUS_ACKNOWLEDGED, "patada_monitor"),
    ):
        event = repository.add_alarm_event(
            device_id=ruleta.id,
            timestamp=now - offset,
            object_class=kind,
            confidence=0.9,
            severity="critico",
            status=status,
        )
        ids.append(event.id)
    repository.update_alarm_event(ids[1], notes="Revisar con el jefe de mesa")
    snapshot = tmp_path / "media" / "snap.jpg"
    snapshot.parent.mkdir(parents=True)
    cv2.imwrite(str(snapshot), np.full((900, 2000, 3), 120, np.uint8))
    for kind, rel in ((KIND_SNAPSHOT, "snap.jpg"), (KIND_CLIP, "clip.mp4")):
        repository.add_media_asset(
            kind=kind, device_id=ruleta.id, alarm_event_id=ids[1], timestamp=now, rel_path=rel
        )
    return SimpleNamespace(ruleta=ruleta, caja=caja, ids=ids)


class TestHerramientas:
    def test_buscar_filtra_y_cuenta(self, datos):
        result = json.loads(
            asistente.buscar_incidentes(
                {
                    "camara_id": datos.ruleta.id,
                    "estado": None,
                    "tipo": None,
                    "ultimas_horas": 1,
                    "limite": 1,
                }
            )
        )
        assert result["total_coincidencias"] == 2  # el de hace 2 h queda afuera
        assert result["mostrando"] == 1
        assert result["incidentes"][0]["estado"] == "sin reconocer"
        assert result["incidentes"][0]["tipo"] == "Fichas tras el no va más"

    def test_un_estado_que_no_existe_es_un_error(self, datos):
        with pytest.raises(ValueError, match="estado"):
            asistente.buscar_incidentes({"estado": "pendiente"})

    def test_resumen(self, datos):
        result = json.loads(asistente.resumen_incidentes({}))

        assert result["total"] == 3
        assert result["por_estado"] == {"sin reconocer": 1, "en investigación": 1, "reconocida": 1}
        assert result["por_camara"] == {"Ruleta RA-04": 3}

    def test_detalle_con_notas_y_media(self, datos):
        result = json.loads(asistente.detalle_incidente({"incidente_id": datos.ids[1]}))

        assert result["notas_del_operador"] == "Revisar con el jefe de mesa"
        assert result["tiene_captura"] and result["tiene_clip"]

    def test_analiticas_solo_las_visibles(self, datos):
        result = json.loads(asistente.estado_analiticas({"camara_id": None}))

        assert result["analiticas"] == [
            {
                "camara": "Ruleta RA-04",
                "camara_id": datos.ruleta.id,
                "analitica": "Incidentes en casinos",
                "modo": "ruleta",
                "habilitada": True,
                "fps": 25,
            }
        ]

    def test_camaras_por_sitio(self, datos):
        result = json.loads(asistente.listar_camaras({"sitio": "casino"}))

        assert [c["nombre"] for c in result["camaras"]] == ["Ruleta RA-04"]
        assert result["camaras"][0]["estado"] == "en línea"

    def test_captura_achicada_como_imagen(self, datos):
        text, image = asistente.ver_captura({"incidente_id": datos.ids[1]})

        assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"
        import base64

        jpeg = np.frombuffer(base64.b64decode(image["source"]["data"]), np.uint8)
        assert cv2.imdecode(jpeg, cv2.IMREAD_COLOR).shape[1] == asistente.SNAPSHOT_MAX_WIDTH

    def test_esquemas_estrictos(self):
        for tool in asistente.TOOLS:
            schema = tool.schema()
            assert schema["strict"] is True
            assert schema["input_schema"]["additionalProperties"] is False
            assert schema["input_schema"]["required"] == list(tool.properties)


# --- el ciclo, con un modelo guionado ---------------------------------------


def _usage(inp=1000, out=100, write=0, read=0):
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_creation_input_tokens=write,
        cache_read_input_tokens=read,
    )


def _tool_use(name: str, args: dict, block_id: str = "t1"):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=args)


def _text(text: str):
    return SimpleNamespace(type="text", text=text)


def _response(content, stop, usage=None):
    return SimpleNamespace(content=content, stop_reason=stop, usage=usage or _usage())


class _Client:
    def __init__(self, *responses):
        self.requests: list[dict] = []
        script = list(responses)

        def create(**kwargs):
            # Copia de los mensajes al momento del pedido, como serializa el SDK.
            self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
            return script.pop(0)

        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


class TestCiclo:
    def test_usa_la_herramienta_y_responde(self, datos):
        client = _Client(
            _response([_tool_use("resumen_incidentes", {})], "tool_use", _usage(3000, 80, 2500)),
            _response([_text("Hubo 3 incidentes.")], "end_turn", _usage(400, 50, 0, 2500)),
        )
        bot = asistente.Asistente(_user("admin"), client=client, effort="low")

        answer = bot.preguntar("¿Cuántos incidentes hubo?")

        assert answer.text == "Hubo 3 incidentes."
        assert answer.tool_calls == [("resumen_incidentes", {})]
        # El resultado de la herramienta volvio al modelo en el segundo pedido.
        result = client.requests[1]["messages"][-1]["content"][0]
        assert result["type"] == "tool_result" and '"total": 3' in result["content"]
        assert bot.auditoria[0]["herramienta"] == "resumen_incidentes"
        assert bot.auditoria[0]["resultado"] == "ok"
        assert answer.usage.requests == 2 and answer.usage.cache_read == 2500
        expected = (3400 * 4 + 130 * 20 + 2500 * 5 + 2500 * 0.2) / 1e6
        assert answer.usage.cost_usd == pytest.approx(expected)

    def test_el_pedido_lleva_modelo_cache_respaldo_y_esfuerzo(self, datos):
        client = _Client(_response([_text("Listo.")], "end_turn"))
        asistente.Asistente(_user("admin"), client=client, effort="low").preguntar("Hola")

        request = client.requests[0]
        assert request["model"] == "claude-opus-5-5"
        assert request["output_config"] == {"effort": "low"}
        assert request["cache_control"] == {"type": "ephemeral"}
        assert request["fallbacks"] == "default"
        assert "server-side-fallback-2026-07-01" in request["betas"]
        assert all(tool["strict"] for tool in request["tools"])

    def test_un_operador_no_ve_las_reglas_y_si_las_pide_falla(self, datos):
        client = _Client(
            _response([_tool_use("reglas_de_alerta", {})], "tool_use"),
            _response([_text("Tu rol no tiene permiso.")], "end_turn"),
        )
        bot = asistente.Asistente(_user(ROLE_OPERATOR), client=client)

        bot.preguntar("¿Qué reglas hay?")

        offered = {tool["name"] for tool in client.requests[0]["tools"]}
        assert "reglas_de_alerta" not in offered
        assert "buscar_incidentes" in offered
        result = client.requests[1]["messages"][-1]["content"][0]
        assert result["is_error"] is True and ROLE_OPERATOR in result["content"]
        assert bot.auditoria[0]["resultado"].startswith("error")

    def test_un_error_de_herramienta_vuelve_al_modelo(self, datos):
        client = _Client(
            _response([_tool_use("detalle_incidente", {"incidente_id": 999})], "tool_use"),
            _response([_text("No existe.")], "end_turn"),
        )
        answer = asistente.Asistente(_user("admin"), client=client).preguntar("#999")

        result = client.requests[1]["messages"][-1]["content"][0]
        assert result["is_error"] is True and "999" in result["content"]
        assert answer.text == "No existe."

    def test_llamadas_en_paralelo_en_un_solo_mensaje(self, datos):
        client = _Client(
            _response(
                [
                    _tool_use("listar_camaras", {"sitio": None}, "a"),
                    _tool_use("resumen_incidentes", {}, "b"),
                ],
                "tool_use",
            ),
            _response([_text("Ok.")], "end_turn"),
        )
        asistente.Asistente(_user("admin"), client=client).preguntar("Estado general")

        results = client.requests[1]["messages"][-1]["content"]
        assert [r["tool_use_id"] for r in results] == ["a", "b"]

    def test_un_rechazo_no_rompe(self, datos):
        client = _Client(_response([], "refusal"))
        answer = asistente.Asistente(_user("admin"), client=client).preguntar("...")

        assert answer.stop_reason == "refusal" and answer.text


class TestPreguntas:
    def test_numeros_en_cifra_o_en_palabra(self):
        assert preguntas.says_number("Hay 9 patadas", 9)
        assert preguntas.says_number("Hubo nueve patadas", 9)
        assert not preguntas.says_number("Incidente #9", 9)
        assert preguntas.says_number("No hay incidentes sin reconocer", 0)

    def test_las_trampas_se_plantan_en_la_copia(self, datos):
        repository.add_device(name="Sabotaje de máquina", ip="10.0.0.9", rtsp_main_url="rtsp://s")

        target = preguntas.plant_injections()

        assert repository.get_alarm_event(target).notes == preguntas.INJECTION_NOTE
        assert any(preguntas.INJECTION_CAMERA in d.name for d in repository.list_devices())
