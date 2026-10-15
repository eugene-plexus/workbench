"""Pinned MCP Apps wire validation, without executing or fetching app code."""

from __future__ import annotations

import base64
import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

from .interaction_store import InteractionError, encoded

PROTOCOL = "mcp-apps"
VERSION = "2026-01-26"
EXTENSION = "io.modelcontextprotocol/ui"
MIME = "text/html;profile=mcp-app"
READS = ("ui/initialize", "ping", "tools/list", "resources/read")
ACTIONS = ("tools/call", "ui/message", "ui/update-model-context")


def uri(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Invalid UI resource URI")
    parsed = urlsplit(value)
    if (
        not value.startswith("ui://")
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(c.isspace() or ord(c) < 32 or c == "\\" for c in value)
    ):
        raise ValueError("Invalid UI resource URI")
    return value


def visibility(meta: dict[str, Any]) -> list[str]:
    ui = meta.get("ui", {})
    if not isinstance(ui, dict):
        raise ValueError("Invalid tool UI visibility")
    value = ui.get("visibility", ["model", "app"])
    if (
        not isinstance(value, list)
        or not value
        or any(v not in ("model", "app") for v in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("Invalid tool UI visibility")
    return value


def resource_uri(meta: dict[str, Any]) -> str | None:
    try:
        encoded(meta, 16 * 1024)
        return uri(meta.get("ui", {}).get("resourceUri"))
    except (ValueError, AttributeError, InteractionError):
        return None


def fingerprint(server: dict[str, Any]) -> str:
    # Internal binding only. Never expose even a digest of a credential to a view.
    return hashlib.sha256(encoded(server, 128 * 1024).encode()).hexdigest()


def resource(result: dict[str, Any], expected: str) -> dict[str, Any]:
    encoded(result, 256 * 1024)
    contents = result.get("contents")
    if not isinstance(contents, list) or len(contents) != 1:
        raise ValueError("Expected one UI document")
    item = contents[0]
    if item.get("uri") != expected or item.get("mimeType") != MIME:
        raise ValueError("Unexpected UI resource")
    if ("text" in item) == ("blob" in item):
        raise ValueError("Expected text or blob")
    html = item.get("text")
    if html is None:
        html = base64.b64decode(item["blob"], validate=True).decode("utf-8", errors="strict")
    if (
        not isinstance(html, str)
        or len(html.encode("utf-8")) > 192 * 1024
        or not re.match(r"\s*<!doctype\s+html\s*>", html, re.IGNORECASE)
        or not re.search(r"<html(?:\s|>)", html, re.IGNORECASE)
        or "\x00" in html
    ):
        raise ValueError("Expected a bounded HTML document")
    meta = item.get("_meta", {})
    policy = meta.get("ui", {})
    if not isinstance(policy, dict) or set(policy) - {
        "csp",
        "permissions",
        "domain",
        "prefersBorder",
    }:
        raise ValueError("Unsupported UI policy")
    csp = policy.get("csp", {})
    if not isinstance(csp, dict) or set(csp) - {
        "connectDomains",
        "resourceDomains",
        "frameDomains",
        "baseUriDomains",
    }:
        raise ValueError("Unsupported CSP request")
    for domains in csp.values():
        if (
            not isinstance(domains, list)
            or len(domains) > 32
            or any(
                not isinstance(d, str) or len(d) > 2048 or any(c.isspace() for c in d)
                for d in domains
            )
        ):
            raise ValueError("Invalid CSP domains")
    permissions = policy.get("permissions", {})
    if (
        not isinstance(permissions, dict)
        or set(permissions) - {"camera", "microphone", "geolocation", "clipboardWrite"}
        or any(v != {} for v in permissions.values())
    ):
        raise ValueError("Unsupported permission request")
    if "domain" in policy and (
        not isinstance(policy["domain"], str) or len(policy["domain"]) > 2048
    ):
        raise ValueError("Invalid app domain")
    if "prefersBorder" in policy and type(policy["prefersBorder"]) is not bool:
        raise ValueError("Invalid border request")
    return result


def text_block(value: Any) -> str:
    if (
        not isinstance(value, dict)
        or set(value) != {"type", "text"}
        or value["type"] != "text"
        or not isinstance(value["text"], str)
        or len(value["text"]) > 8192
    ):
        raise ValueError("Expected a bounded text block")
    return value["text"]


def action(name: str, arguments: dict[str, Any]) -> None:
    encoded(arguments, 16 * 1024)
    if name == "tools/call":
        if (
            set(arguments) != {"name", "arguments"}
            or not isinstance(arguments["name"], str)
            or not 1 <= len(arguments["name"]) <= 256
            or not isinstance(arguments["arguments"], dict)
        ):
            raise ValueError("Invalid tool request")
    elif name == "ui/message":
        if set(arguments) != {"role", "content"} or arguments["role"] != "user":
            raise ValueError("Only user messages are supported")
        text_block(arguments["content"])
    elif name == "ui/update-model-context":
        if not arguments or set(arguments) - {"content", "structuredContent"}:
            raise ValueError("Invalid context update")
        blocks = arguments.get("content", [])
        if not isinstance(blocks, list) or len(blocks) > 16:
            raise ValueError("Invalid context blocks")
        for block in blocks:
            text_block(block)
        if "structuredContent" in arguments and not isinstance(
            arguments["structuredContent"], dict
        ):
            raise ValueError("Invalid structured context")
    else:
        raise ValueError("Unsupported app method")
