from __future__ import annotations

import json
from typing import Any

from .models import ServerEntry

_SUPPORTED_XRAY_OUTBOUND_PROTOCOLS = frozenset({"vless", "vmess", "trojan", "shadowsocks"})
_SUPPORTED_XRAY_NETWORKS = frozenset({"tcp", "ws", "grpc"})
_SUPPORTED_XRAY_SECURITIES = frozenset({"none", "tls", "reality"})


def is_probable_xray_config_data(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    outbounds = data.get("outbounds")
    if not isinstance(outbounds, list):
        return False
    return any(isinstance(outbound, dict) and outbound.get("protocol") for outbound in outbounds)


def parse_xray_json_config(
    payload: str | dict[str, Any],
    *,
    source: str = "manual",
    subscription_id: str | None = None,
    name: str | None = None,
    strict: bool = False,
) -> list[ServerEntry]:
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            if strict:
                raise ValueError("incomplete config: embedded Xray JSON is malformed.") from exc
            return []
    else:
        data = payload

    if not is_probable_xray_config_data(data):
        if strict:
            raise ValueError("incomplete config: embedded Xray payload does not contain outbounds.")
        return []

    servers: list[ServerEntry] = []
    saw_protocol = False
    saw_supported_protocol = False
    for index, outbound in enumerate(data.get("outbounds") or []):
        if not isinstance(outbound, dict):
            continue
        protocol = str(outbound.get("protocol") or "").strip().lower()
        if not protocol:
            continue
        saw_protocol = True
        if protocol not in _SUPPORTED_XRAY_OUTBOUND_PROTOCOLS:
            continue
        saw_supported_protocol = True
        try:
            servers.append(
                _parse_outbound(
                    outbound,
                    source=source,
                    subscription_id=subscription_id,
                    name=name,
                    index=index,
                )
            )
        except ValueError:
            if strict:
                raise

    if servers:
        return servers
    if strict:
        if saw_supported_protocol:
            raise ValueError("incomplete config: embedded Xray outbound is missing required fields.")
        if saw_protocol:
            raise ValueError("unsupported embedded protocol: Xray config does not contain a supported outbound.")
        raise ValueError("incomplete config: embedded Xray payload does not contain outbounds.")
    return []


def _parse_outbound(
    outbound: dict[str, Any],
    *,
    source: str,
    subscription_id: str | None,
    name: str | None,
    index: int,
) -> ServerEntry:
    protocol = str(outbound.get("protocol") or "").strip().lower()
    settings = _require_mapping(outbound.get("settings"), "settings")
    stream_settings = dict(outbound.get("streamSettings") or {})
    tag = str(outbound.get("tag") or "").strip()

    if protocol in {"vless", "vmess"}:
        server = _first_mapping(settings.get("vnext"), "settings.vnext")
        user = _first_mapping(server.get("users"), "settings.vnext[0].users")
        host = _require_text(server.get("address"), "settings.vnext[0].address")
        port = _require_port(server.get("port"), "settings.vnext[0].port")
        extra = _parse_stream_settings(stream_settings)
        extra["id"] = _require_text(user.get("id"), "settings.vnext[0].users[0].id")
        if protocol == "vless":
            extra["security"] = extra.get("security", "none")
            extra["encryption"] = str(user.get("encryption") or "none")
            if user.get("flow"):
                extra["flow"] = str(user["flow"])
        else:
            extra["security"] = str(user.get("security") or "auto")
            extra["alter_id"] = int(user.get("alterId", 0) or 0)
            if stream_settings.get("security") == "tls":
                extra["tls"] = "tls"
        return ServerEntry.new(
            name=_server_name(name=name, tag=tag, host=host, port=port, index=index),
            protocol=protocol,
            host=host,
            port=port,
            raw_link="",
            extra=extra,
            source=source,
            subscription_id=subscription_id,
        )

    if protocol == "trojan":
        server = _first_mapping(settings.get("servers"), "settings.servers")
        host = _require_text(server.get("address"), "settings.servers[0].address")
        port = _require_port(server.get("port"), "settings.servers[0].port")
        extra = _parse_stream_settings(stream_settings)
        extra["password"] = _require_text(server.get("password"), "settings.servers[0].password")
        extra["security"] = extra.get("security", "tls")
        return ServerEntry.new(
            name=_server_name(name=name, tag=tag, host=host, port=port, index=index),
            protocol="trojan",
            host=host,
            port=port,
            raw_link="",
            extra=extra,
            source=source,
            subscription_id=subscription_id,
        )

    if protocol == "shadowsocks":
        server = _first_mapping(settings.get("servers"), "settings.servers")
        host = _require_text(server.get("address"), "settings.servers[0].address")
        port = _require_port(server.get("port"), "settings.servers[0].port")
        extra = {
            "method": _require_text(server.get("method"), "settings.servers[0].method"),
            "password": _require_text(server.get("password"), "settings.servers[0].password"),
        }
        return ServerEntry.new(
            name=_server_name(name=name, tag=tag, host=host, port=port, index=index),
            protocol="ss",
            host=host,
            port=port,
            raw_link="",
            extra=extra,
            source=source,
            subscription_id=subscription_id,
        )

    raise ValueError(f"unsupported embedded protocol: Xray outbound '{protocol}' is not supported.")


def _parse_stream_settings(stream_settings: dict[str, Any]) -> dict[str, Any]:
    network = str(stream_settings.get("network") or "tcp").strip().lower()
    security = str(stream_settings.get("security") or "none").strip().lower()
    if network not in _SUPPORTED_XRAY_NETWORKS:
        raise ValueError(f"unsupported embedded protocol: Xray network '{network}' is not supported.")
    if security not in _SUPPORTED_XRAY_SECURITIES:
        raise ValueError(f"unsupported embedded protocol: Xray security '{security}' is not supported.")

    extra: dict[str, Any] = {
        "network": network,
        "type": network,
        "security": security,
    }
    if security == "tls":
        tls_settings = dict(stream_settings.get("tlsSettings") or {})
        if tls_settings.get("serverName"):
            extra["sni"] = str(tls_settings["serverName"])
        if tls_settings.get("fingerprint"):
            extra["fingerprint"] = str(tls_settings["fingerprint"])
            extra["fp"] = extra["fingerprint"]
        if isinstance(tls_settings.get("alpn"), list):
            extra["alpn"] = ",".join(str(item).strip() for item in tls_settings["alpn"] if str(item).strip())
        if tls_settings.get("allowInsecure") is True:
            extra["allow_insecure"] = "true"
    elif security == "reality":
        reality_settings = dict(stream_settings.get("realitySettings") or {})
        if reality_settings.get("serverName"):
            extra["sni"] = str(reality_settings["serverName"])
        if reality_settings.get("fingerprint"):
            extra["fingerprint"] = str(reality_settings["fingerprint"])
            extra["fp"] = extra["fingerprint"]
        if reality_settings.get("publicKey"):
            extra["public_key"] = str(reality_settings["publicKey"])
            extra["pbk"] = extra["public_key"]
        if reality_settings.get("shortId"):
            extra["short_id"] = str(reality_settings["shortId"])
            extra["sid"] = extra["short_id"]
        if reality_settings.get("spiderX"):
            extra["spider_x"] = str(reality_settings["spiderX"])

    if network == "ws":
        ws_settings = dict(stream_settings.get("wsSettings") or {})
        extra["path"] = str(ws_settings.get("path") or "/")
        headers = dict(ws_settings.get("headers") or {})
        if headers.get("Host"):
            extra["host"] = str(headers["Host"])
    elif network == "grpc":
        grpc_settings = dict(stream_settings.get("grpcSettings") or {})
        if grpc_settings.get("serviceName"):
            extra["service_name"] = str(grpc_settings["serviceName"])
        if grpc_settings.get("authority"):
            extra["authority"] = str(grpc_settings["authority"])
    else:
        tcp_settings = dict(stream_settings.get("tcpSettings") or {})
        header = dict(tcp_settings.get("header") or {})
        if header.get("type") and str(header["type"]).strip().lower() != "none":
            extra["header_type"] = str(header["type"])

    return {key: value for key, value in extra.items() if value is not None and value != ""}


def _first_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, list) or not value or not isinstance(value[0], dict):
        raise ValueError(f"incomplete config: missing {field_name}[0].")
    return dict(value[0])


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"incomplete config: missing {field_name}.")
    return dict(value)


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"incomplete config: missing {field_name}.")
    return text


def _require_port(value: Any, field_name: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"incomplete config: invalid {field_name}.") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"incomplete config: invalid {field_name}.")
    return port


def _server_name(*, name: str | None, tag: str, host: str, port: int, index: int) -> str:
    base = str(name or "").strip()
    if base:
        return base if index == 0 else f"{base} #{index + 1}"
    if tag:
        return tag
    return f"{host}:{port}"
