from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .process_manager import Server

logger = logging.getLogger(__name__)

_BASE64_BODY_RE = re.compile(r"[A-Za-z0-9+/=\-_\r\n]+")
_SKIP_SINGBOX_TYPES = {"direct", "block", "dns", "selector", "urltest"}
_SUPPORTED_CLASH_TYPES = {"vless", "vmess", "trojan", "ss", "hy2", "hysteria2"}


def fetch_subscription(url: str, timeout: int = 15) -> list[Server]:
    response = httpx.get(
        url,
        headers={"User-Agent": "v2rayN/6.0"},
        follow_redirects=True,
        timeout=timeout,
    )
    response.raise_for_status()
    return _auto_parse(response.text.strip())


def _auto_parse(body: str) -> list[Server]:
    payload = body.strip()
    if not payload:
        return []

    if payload.startswith("{") or payload.startswith("["):
        return _parse_json(payload)

    if len(payload) > 20 and _BASE64_BODY_RE.fullmatch(payload):
        try:
            decoded = base64.b64decode(payload + "==", altchars=b"-_").decode("utf-8")
        except Exception:
            logger.debug("Subscription body is not valid base64", exc_info=True)
        else:
            return _parse_plain(decoded)

    return _parse_plain(payload)


def _parse_plain(text: str) -> list[Server]:
    servers: list[Server] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        server = _parse_uri(line)
        if server is not None:
            servers.append(server)
    return _deduplicate(servers)


def _parse_uri(uri: str) -> Server | None:
    normalized = uri.strip()
    try:
        lowered = normalized.lower()
        if lowered.startswith(("vless://", "trojan://", "hy2://", "hysteria2://")):
            return _parse_standard(normalized)
        if lowered.startswith("vmess://"):
            return _parse_vmess(normalized)
        if lowered.startswith("ss://"):
            return _parse_shadowsocks(normalized)
    except Exception:
        logger.debug("Skipping malformed server URI: %s", normalized, exc_info=True)
    return None


def _parse_standard(uri: str) -> Server:
    parsed = urlparse(uri)
    if not parsed.scheme or not parsed.hostname or parsed.port is None or not parsed.username:
        raise ValueError("Invalid standard URI")

    query = parse_qs(parsed.query, keep_blank_values=True)

    def _query_value(key: str, default: str | None = None) -> str | None:
        values = query.get(key)
        if not values:
            return default
        return values[-1]

    protocol = "hy2" if parsed.scheme.lower() == "hysteria2" else parsed.scheme.lower()
    name = unquote(parsed.fragment) if parsed.fragment else parsed.hostname
    extra = {
        "sni": _query_value("sni"),
        "fp": _query_value("fp"),
        "pbk": _query_value("pbk"),
        "sid": _query_value("sid"),
        "flow": _query_value("flow"),
        "type": _query_value("type", "tcp"),
        "path": _query_value("path"),
        "host": _query_value("host"),
    }
    return Server(
        protocol=protocol,
        address=parsed.hostname,
        port=parsed.port,
        uuid=parsed.username,
        name=name,
        raw_uri=uri,
        extra=extra,
    )


def _parse_vmess(uri: str) -> Server:
    decoded = base64.b64decode(uri[8:] + "==", altchars=b"-_").decode("utf-8")
    data = json.loads(decoded)
    address = data.get("add")
    port = int(data.get("port"))
    uuid = data.get("id")
    if not address or not uuid:
        raise ValueError("Invalid vmess URI")
    return Server(
        protocol="vmess",
        address=address,
        port=port,
        uuid=uuid,
        name=data.get("ps") or address,
        raw_uri=uri,
        extra={
            "sni": data.get("sni") or data.get("host"),
            "net": data.get("net"),
            "path": data.get("path"),
            "tls": data.get("tls"),
        },
    )


def _parse_shadowsocks(uri: str) -> Server:
    body = uri[5:]
    content, _, fragment = body.partition("#")
    content = content.split("?", 1)[0]

    credentials: str
    host_part: str
    if "@" in content:
        encoded_credentials, host_part = content.rsplit("@", 1)
        try:
            credentials = base64.b64decode(encoded_credentials + "==", altchars=b"-_").decode("utf-8")
        except Exception:
            credentials = encoded_credentials
    else:
        decoded = base64.b64decode(content + "==", altchars=b"-_").decode("utf-8")
        if "@" not in decoded:
            raise ValueError("Invalid shadowsocks URI")
        credentials, host_part = decoded.rsplit("@", 1)

    if ":" not in credentials:
        raise ValueError("Invalid shadowsocks credentials")
    method, password = credentials.split(":", 1)

    parsed = urlparse(f"//{host_part}")
    if not parsed.hostname or parsed.port is None:
        raise ValueError("Invalid shadowsocks host")

    return Server(
        protocol="ss",
        address=parsed.hostname,
        port=parsed.port,
        uuid=password,
        name=unquote(fragment) if fragment else parsed.hostname,
        raw_uri=uri,
        extra={"method": method},
    )


def _parse_json(body: str) -> list[Server]:
    data = json.loads(body)
    if isinstance(data, dict):
        if "outbounds" in data:
            return _deduplicate(_from_singbox(data.get("outbounds") or []))
        if "proxies" in data:
            return _deduplicate(_from_clash(data.get("proxies") or []))
        return []

    if isinstance(data, list):
        servers = [
            server
            for item in data
            if isinstance(item, dict) and "link" in item
            for server in [_parse_uri(str(item.get("link", "")))]
            if server is not None
        ]
        return _deduplicate(servers)

    return []


def _from_singbox(outbounds: list[dict[str, Any]]) -> list[Server]:
    servers: list[Server] = []
    for outbound in outbounds:
        if not isinstance(outbound, dict):
            continue

        outbound_type = str(outbound.get("type") or "").lower()
        if outbound_type in _SKIP_SINGBOX_TYPES:
            continue

        address = outbound.get("server")
        port = outbound.get("server_port")
        if not address or port is None:
            continue

        protocol = "hy2" if outbound_type == "hysteria2" else outbound_type
        extra = {
            key: value
            for key, value in outbound.items()
            if key not in {"type", "server", "server_port", "uuid", "password", "tag"}
        }
        servers.append(
            Server(
                protocol=protocol,
                address=str(address),
                port=int(port),
                uuid=str(outbound.get("uuid") or outbound.get("password") or ""),
                name=str(outbound.get("tag") or address),
                raw_uri="",
                extra=extra,
            )
        )
    return servers


def _from_clash(proxies: list[dict[str, Any]]) -> list[Server]:
    servers: list[Server] = []
    for proxy in proxies:
        if not isinstance(proxy, dict):
            continue

        proxy_type = str(proxy.get("type") or "").lower()
        if proxy_type not in _SUPPORTED_CLASH_TYPES:
            continue

        address = proxy.get("server")
        port = proxy.get("port")
        if not address or port is None:
            continue

        protocol = "hy2" if proxy_type == "hysteria2" else proxy_type
        servers.append(
            Server(
                protocol=protocol,
                address=str(address),
                port=int(port),
                uuid=str(proxy.get("uuid") or proxy.get("password") or ""),
                name=str(proxy.get("name") or address),
                raw_uri="",
                extra={},
            )
        )
    return servers


def _deduplicate(servers: list[Server]) -> list[Server]:
    unique: list[Server] = []
    seen: set[tuple[str, int, str]] = set()
    for server in servers:
        key = (server.address.lower(), server.port, server.uuid)
        if key in seen:
            continue
        seen.add(key)
        unique.append(server)
    return unique


def merge_servers(old: list[Server], fresh: list[Server]) -> list[Server]:
    old_by_key = {_server_key(server): server for server in old}
    fresh_by_key = {_server_key(server): server for server in fresh}

    merged: list[Server] = []
    for server in fresh:
        key = _server_key(server)
        previous = old_by_key.get(key)
        if previous is None:
            merged.append(server)
            continue

        extra = dict(previous.extra)
        extra.update(server.extra)
        extra.pop("stale", None)
        name = previous.name if previous.extra.get("custom_name") is True else server.name
        merged.append(
            Server(
                protocol=server.protocol,
                address=server.address,
                port=server.port,
                uuid=server.uuid,
                name=name,
                raw_uri=server.raw_uri,
                extra=extra,
            )
        )

    for server in old:
        key = _server_key(server)
        if key in fresh_by_key:
            continue
        extra = dict(server.extra)
        extra["stale"] = True
        merged.append(
            Server(
                protocol=server.protocol,
                address=server.address,
                port=server.port,
                uuid=server.uuid,
                name=server.name,
                raw_uri=server.raw_uri,
                extra=extra,
            )
        )

    return merged


def _server_key(server: Server) -> tuple[str, int, str]:
    return (server.address.lower(), server.port, server.uuid)
