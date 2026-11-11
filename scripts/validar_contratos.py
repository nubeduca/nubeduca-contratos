#!/usr/bin/env python3
"""Valida los contratos de Nubeduca. Sale con código 1 si algo no cumple.

  python scripts/validar_contratos.py

Revisa:
  Eventos
  - catalogo.yaml: nombres, productor y consumidores válidos, sin duplicados.
  - Cada evento tiene esquema (Draft-04, el formato que acepta EventBridge Schema Registry) y ejemplo.
  - El bloque metadata de cada esquema es el de esquemas/_metadata.json.
  - Cada ejemplo es un evento de EventBridge cuyo detail cumple su esquema.
  - No sobran esquemas ni ejemplos fuera del catálogo.
  APIs
  - Cada apis/<ms>.openapi.yaml es OpenAPI 3.0 válido.
  - Toda operación tiene operationId único, x-roles y x-task; las públicas declaran security: [].
  - x-emits solo nombra eventos del catálogo que produce ese mismo microservicio.
  - Todo evento del catálogo lo emite alguna operación, salvo que el catálogo diga emittedBy.
"""
import json
import pathlib
import re
import sys

import yaml
from jsonschema import Draft4Validator
from openapi_spec_validator import validate as validate_openapi

RAIZ = pathlib.Path(__file__).resolve().parent.parent
EVENTOS = RAIZ / "eventos"
APIS = RAIZ / "apis"

MICROSERVICIOS = {"ms-usuarios", "ms-cursos", "ms-contenidos", "ms-evaluaciones",
                  "ms-calificaciones", "ms-notificaciones"}
SOURCES = {"lms.users": "ms-usuarios", "lms.courses": "ms-cursos", "lms.content": "ms-contenidos",
           "lms.assessment": "ms-evaluaciones", "lms.grading": "ms-calificaciones"}
CANALES = {"eventbridge", "eventbridge+sns-fifo"}
ROLES = {"STUDENT", "TEACHER", "ADMIN"}
TAREA = re.compile(r"^[A-Z]+-\d{2}$|^GATE-0$")
DETAIL_TYPE = re.compile(r"^lms\.[a-z]+\.[a-z_]+\.[a-z_]+\.v\d+$")
METODOS = {"get", "put", "post", "patch", "delete"}

errores = []


def error(donde, msg):
    errores.append(f"{donde}: {msg}")


def cargar_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        error(path.relative_to(RAIZ), f"no se puede leer: {e}")
        return None


def validar_eventos():
    catalogo = yaml.safe_load((EVENTOS / "catalogo.yaml").read_text(encoding="utf-8"))
    eventos = catalogo.get("events", [])
    metadata = cargar_json(EVENTOS / "esquemas" / "_metadata.json")
    vistos = {}
    for ev in eventos:
        dt = ev.get("detailType", "?")
        donde = f"catalogo.yaml [{dt}]"
        if dt in vistos:
            error(donde, "evento duplicado")
        vistos[dt] = ev
        if not DETAIL_TYPE.match(dt):
            error(donde, "detailType debe ser lms.<contexto>.<entidad>.<hecho>.v<N>")
        src = ev.get("source")
        if not dt.startswith(f"{src}."):
            error(donde, f"detailType debe empezar con el source {src}")
        if SOURCES.get(src) != ev.get("producer"):
            error(donde, f"el source {src} pertenece a {SOURCES.get(src)}, no a {ev.get('producer')}")
        for c in ev.get("consumers", []):
            if c not in MICROSERVICIOS:
                error(donde, f"consumidor desconocido {c}")
            if c == ev.get("producer"):
                error(donde, "un microservicio no consume su propio evento por el bus")
        if ev.get("channel") not in CANALES:
            error(donde, f"channel debe ser uno de {sorted(CANALES)}")
        if not TAREA.match(str(ev.get("task", ""))):
            error(donde, "task debe ser un id del backlog, por ejemplo ACA-03")

        esquema_path = EVENTOS / "esquemas" / f"{dt}.json"
        ejemplo_path = EVENTOS / "ejemplos" / f"{dt}.json"
        if not esquema_path.exists():
            error(donde, f"falta {esquema_path.relative_to(RAIZ)}")
            continue
        esquema = cargar_json(esquema_path)
        if esquema is None:
            continue
        rel = esquema_path.relative_to(RAIZ)
        if esquema.get("$schema") != "http://json-schema.org/draft-04/schema#":
            error(rel, "$schema debe ser draft-04 (EventBridge Schema Registry no acepta otro)")
        if esquema.get("title") != dt:
            error(rel, f"title debe ser {dt}")
        try:
            Draft4Validator.check_schema(esquema)
        except Exception as e:  # noqa: BLE001
            error(rel, f"esquema inválido: {e.message if hasattr(e, 'message') else e}")
            continue
        meta = esquema.get("properties", {}).get("metadata", {})
        if metadata:
            props = {k: v for k, v in meta.get("properties", {}).items() if k != "aggregateType"}
            base = {k: v for k, v in metadata["properties"].items() if k != "aggregateType"}
            if props != base:
                error(rel, "metadata.properties no coincide con esquemas/_metadata.json")
            if not set(metadata["required"]) <= set(meta.get("required", [])):
                error(rel, "metadata.required debe incluir todos los de _metadata.json")
        if not ev.get("description"):
            error(donde, "falta description")

        if not ejemplo_path.exists():
            error(donde, f"falta {ejemplo_path.relative_to(RAIZ)}")
            continue
        ejemplo = cargar_json(ejemplo_path)
        if ejemplo is None:
            continue
        rel = ejemplo_path.relative_to(RAIZ)
        if ejemplo.get("detail-type") != dt:
            error(rel, f"detail-type debe ser {dt}")
        if ejemplo.get("source") != src:
            error(rel, f"source debe ser {src}")
        validador = Draft4Validator(esquema, format_checker=Draft4Validator.FORMAT_CHECKER)
        for e in sorted(validador.iter_errors(ejemplo.get("detail", {})), key=str):
            ruta = "/".join(str(p) for p in e.absolute_path) or "detail"
            error(rel, f"{ruta}: {e.message}")

    for carpeta in ("esquemas", "ejemplos"):
        for f in sorted((EVENTOS / carpeta).glob("*.json")):
            if f.name != "_metadata.json" and f.stem not in vistos:
                error(f.relative_to(RAIZ), "no está en catalogo.yaml")
    return vistos


def validar_apis(catalogo):
    emitidos = set()
    operation_ids = {}
    archivos = sorted(APIS.glob("*.openapi.yaml"))
    nombres = {f.name.replace(".openapi.yaml", "") for f in archivos}
    for ms in sorted(MICROSERVICIOS - nombres):
        error("apis", f"falta {ms}.openapi.yaml")
    for f in archivos:
        ms = f.name.replace(".openapi.yaml", "")
        rel = f.relative_to(RAIZ)
        spec = yaml.safe_load(f.read_text(encoding="utf-8"))
        try:
            validate_openapi(spec)
        except Exception as e:  # noqa: BLE001
            error(rel, f"OpenAPI inválido: {str(e).splitlines()[0]}")
            continue
        if not str(spec.get("openapi", "")).startswith("3.0"):
            error(rel, "usa OpenAPI 3.0.x (API Gateway no importa 3.1)")
        for path, item in spec.get("paths", {}).items():
            for metodo, o in item.items():
                if metodo not in METODOS:
                    continue
                donde = f"{rel} {metodo.upper()} {path}"
                oid = o.get("operationId")
                if not oid:
                    error(donde, "falta operationId")
                elif oid in operation_ids:
                    error(donde, f"operationId {oid} repetido (también en {operation_ids[oid]})")
                else:
                    operation_ids[oid] = donde
                roles = o.get("x-roles")
                if roles is None:
                    error(donde, "falta x-roles")
                elif not set(roles) <= ROLES:
                    error(donde, f"x-roles con roles desconocidos: {sorted(set(roles) - ROLES)}")
                elif not roles and o.get("security") != []:
                    error(donde, "operación pública (x-roles vacío) sin security: []")
                if not TAREA.match(str(o.get("x-task", ""))):
                    error(donde, "falta x-task o no es un id del backlog")
                for ev in o.get("x-emits", []):
                    if ev not in catalogo:
                        error(donde, f"x-emits nombra {ev}, que no está en el catálogo")
                    elif catalogo[ev]["producer"] != ms:
                        error(donde, f"{ev} lo produce {catalogo[ev]['producer']}, no {ms}")
                    emitidos.add(ev)
    for dt, ev in catalogo.items():
        if dt not in emitidos and not ev.get("emittedBy"):
            error(f"catalogo.yaml [{dt}]", "ninguna operación lo emite (x-emits) y no tiene emittedBy")
    return len(operation_ids)


def main():
    catalogo = validar_eventos()
    operaciones = validar_apis(catalogo)
    if errores:
        print(f"{len(errores)} problema(s):")
        for e in errores:
            print(f"  - {e}")
        sys.exit(1)
    print(f"OK: {len(catalogo)} eventos y {operaciones} operaciones en {len(MICROSERVICIOS)} microservicios.")


if __name__ == "__main__":
    main()
