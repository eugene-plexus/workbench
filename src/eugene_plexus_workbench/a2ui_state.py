"""Bounded, offline A2UI 0.9.1 validation and state reduction. No code evaluation."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012

from .interaction_store import InteractionError, encoded

PROTOCOL = "a2ui"
VERSION = "0.9.1"
EXTENSION = "com.eugeneplexus/a2ui"
MIME = "application/a2ui+json"
CATALOG = "https://eugeneplexus.com/a2ui/catalogs/forms-v1.json"
SCHEMAS = Path(__file__).with_name("a2ui_schema")
FUNCTIONS = {"required", "and", "or", "not"}
VERSIONS = {"v0.9", "v0.9.1"}


def refuse(message: str = "Unsupported or invalid A2UI data.") -> NoReturn:
    raise InteractionError(message, 422)


def no_resource(uri: str) -> Resource[Any]:
    raise NoSuchResource(ref=uri)


def validators(catalog_name: str = "forms-v1.json") -> tuple[Any, Any, Any]:
    catalog = json.loads((SCHEMAS / catalog_name).read_text(encoding="utf-8"))
    common = json.loads((SCHEMAS / "common_types.json").read_text(encoding="utf-8"))
    server = json.loads((SCHEMAS / "server_to_client.json").read_text(encoding="utf-8"))
    client = json.loads((SCHEMAS / "client_to_server.json").read_text(encoding="utf-8"))
    registry: Registry[Any] = Registry(retrieve=no_resource)
    for name, schema in [
        (catalog["$id"], catalog),
        (common["$id"], common),
        ("https://a2ui.org/specification/v0_9/catalog.json", catalog),
    ]:
        registry = registry.with_resource(
            name, Resource.from_contents(schema, default_specification=DRAFT202012)
        )
    return (
        Draft202012Validator(server, registry=registry),
        Draft202012Validator(client, registry=registry),
        Draft202012Validator({"$ref": catalog["$id"] + "#/$defs/anyFunction"}, registry=registry),
    )


SERVER, CLIENT, FUNCTION = validators()


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 80 or any(ord(c) < 32 for c in value):
        refuse("Invalid A2UI identifier.")
    return str(value)


def tokens(path: str) -> list[str]:
    if path in ("", "/"):
        return []
    if not path.startswith("/") or len(path) > 1024 or re.search(r"~(?![01])", path):
        refuse("Use an absolute JSON Pointer.")
    parts = [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]
    if len(parts) > 32 or any(p in {"__proto__", "prototype", "constructor"} for p in parts):
        refuse("Unsupported data path.")
    return parts


def read(data: Any, path: str) -> Any:
    for part in tokens(path):
        if isinstance(data, dict):
            data = data.get(part)
        elif (
            isinstance(data, list)
            and re.fullmatch(r"0|[1-9][0-9]*", part)
            and int(part) < len(data)
        ):
            data = data[int(part)]
        else:
            return None
    return data


def write(data: Any, path: str, value: Any, present: bool = True) -> Any:
    parts = tokens(path)
    if not parts:
        return copy.deepcopy(value) if present else {}
    result = copy.deepcopy(data)
    node = result
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if isinstance(node, dict):
            if last:
                if present:
                    node[part] = copy.deepcopy(value)
                else:
                    node.pop(part, None)
            else:
                if not present and part not in node:
                    return result
                node = node.setdefault(part, {})
        elif isinstance(node, list) and re.fullmatch(r"0|[1-9][0-9]*", part):
            position = int(part)
            if position > len(node) or (position == len(node) and (not last or not present)):
                refuse("Sparse data arrays are not supported.")
            if last:
                if not present:
                    node.pop(position)
                elif position == len(node):
                    node.append(copy.deepcopy(value))
                else:
                    node[position] = copy.deepcopy(value)
            else:
                node = node[position]
        else:
            refuse("The data path does not name a container.")
    return result


def expressions(value: Any, budget: list[int]) -> None:
    budget[0] -= 1
    if budget[0] < 0:
        refuse("The component expressions are too large.")
    if isinstance(value, dict):
        if "call" in value:
            FUNCTION.validate(value)
            if value["call"] not in FUNCTIONS:
                refuse()
        if set(value) == {"path"}:
            tokens(value["path"])
        for child in value.values():
            expressions(child, budget)
    elif isinstance(value, list):
        for child in value:
            expressions(child, budget)


def resolve(value: Any, data: Any, budget: list[int]) -> Any:
    budget[0] -= 1
    if budget[0] < 0:
        refuse("The action expressions are too large.")
    if isinstance(value, list):
        return [resolve(v, data, budget) for v in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"path"}:
        return read(data, value["path"])
    if "call" not in value:
        return value
    FUNCTION.validate(value)
    name, args = value["call"], value["args"]
    if name == "required":
        result = resolve(args["value"], data, budget)
        return result is not None and result not in ("", [], {})
    if name == "not":
        result = resolve(args["value"], data, budget)
        if type(result) is not bool:
            refuse("Expected a boolean expression.")
        return not result
    values = [resolve(v, data, budget) for v in args["values"]]
    if any(type(v) is not bool for v in values):
        refuse("Expected boolean expressions.")
    return all(values) if name == "and" else any(values)


def graph(components: list[dict[str, Any]]) -> tuple[bool, set[str]]:
    if len(components) > 256:
        refuse("The surface has too many components.")
    nodes = {identifier(c["id"]): c for c in components}
    if len(nodes) != len(components):
        refuse("Component IDs must be unique.")
    edges = {k: ([v["child"]] if "child" in v else v.get("children", [])) for k, v in nodes.items()}
    if sum(len(v) for v in edges.values()) > 512:
        refuse("The surface has too many references.")
    memo: dict[str, tuple[bool, int, int]] = {}
    active: set[str] = set()

    def visit(key: str, depth: int) -> tuple[bool, int, int]:
        identifier(key)
        if key in active or depth > 32:
            refuse("Cyclic or excessively deep component graph.")
        if key in memo:
            return memo[key]
        if key not in nodes:
            return False, 1, 1
        active.add(key)
        children = [visit(k, depth + 1) for k in edges[key]]
        active.remove(key)
        result = (
            all(c[0] for c in children),
            1 + sum(c[1] for c in children),
            1 + max((c[2] for c in children), default=0),
        )
        if result[1] > 1024 or result[2] > 32:
            refuse("The expanded component graph is too large.")
        memo[key] = result
        return result

    for key in nodes:
        visit(key, 0)
    reachable: set[str] = set()
    pending = ["root"]
    while pending:
        key = pending.pop()
        if key not in reachable:
            reachable.add(key)
            pending.extend(edges.get(key, []))
    return "root" in nodes and memo["root"][0], reachable


def validate_view(view: dict[str, Any]) -> None:
    if view["catalogId"] != CATALOG:
        refuse("Unknown A2UI catalog.")
    wire = identifier(view["surfaceId"])
    SERVER.validate(
        {
            "version": "v0.9.1",
            "createSurface": {
                "surfaceId": wire,
                "catalogId": CATALOG,
                "theme": view["theme"],
                "sendDataModel": view["sendDataModel"],
            },
        }
    )
    if view["components"]:
        SERVER.validate(
            {
                "version": "v0.9.1",
                "updateComponents": {
                    "surfaceId": wire,
                    "components": view["components"],
                },
            }
        )
    expressions(view["components"], [8192])
    encoded(view["dataModel"], 64 * 1024)

    # Reject dangerous object keys throughout data, including root replacements.
    def keys(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"__proto__", "prototype", "constructor"}:
                    refuse("Unsupported data key.")
                keys(child)
        elif isinstance(value, list):
            for child in value:
                keys(child)

    keys(view["dataModel"])
    view["ready"] = graph(view["components"])[0]


def event(view: dict[str, Any], payload: dict[str, Any]) -> None:
    CLIENT.validate(payload)
    if set(payload) != {"version", "action"} or payload["version"] not in VERSIONS:
        refuse("Only A2UI action events can request server work.")
    action = payload["action"]
    if set(action) != {"name", "surfaceId", "sourceComponentId", "timestamp", "context"}:
        refuse()
    if datetime.fromisoformat(action["timestamp"].replace("Z", "+00:00")).tzinfo is None:
        refuse("The event timestamp must include its time zone.")
    ready, reachable = graph(view["components"])
    component: dict[str, Any] = next(
        (c for c in view["components"] if c["id"] == action["sourceComponentId"]), {}
    )
    expected = component.get("action", {}).get("event")
    if (
        not ready
        or component.get("id") not in reachable
        or not expected
        or action["surfaceId"] != view["surfaceId"]
        or action["name"] != expected["name"]
    ):
        refuse("This event is not offered by the current surface.")
    budget = [1024]
    context = {
        k: resolve(v, view["dataModel"], budget) for k, v in expected.get("context", {}).items()
    }
    if encoded(context, 16 * 1024) != encoded(action["context"], 16 * 1024):
        refuse("The event context does not match the saved form.")
    for c in view["components"]:
        if c["id"] in reachable:
            for check in c.get("checks", []):
                if resolve(check["condition"], view["dataModel"], budget) is not True:
                    refuse("Complete the form's required checks first.")


def input_value(view: dict[str, Any], component_id: str, value: Any) -> dict[str, Any]:
    ready, reachable = graph(view["components"])
    c: dict[str, Any] = next((c for c in view["components"] if c["id"] == component_id), {})
    binding = c.get("value")
    valid_type = (
        c.get("component") == "TextField" and isinstance(value, str) and len(value) <= 8192
    ) or (c.get("component") == "CheckBox" and type(value) is bool)
    if (
        not ready
        or component_id not in reachable
        or not valid_type
        or not isinstance(binding, dict)
        or set(binding) != {"path"}
    ):
        refuse("This component does not offer a writable form value.")
    result = copy.deepcopy(view)
    result["dataModel"] = write(view["dataModel"], binding["path"], value)
    validate_view(result)
    return result
