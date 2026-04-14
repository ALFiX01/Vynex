from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from .models import ServerEntry
from .utils import url_decode

logger = logging.getLogger(__name__)

SUPPORTED_SHARE_LINK_PREFIXES = (
    "vless://",
    "vmess://",
    "trojan://",
    "ss://",
    "hy2://",
    "hysteria2://",
)
_BASE64_BODY_RE = re.compile(r"[A-Za-z0-9+/=\-_\n]+")
_SINGBOX_SKIP_TYPES = {"direct", "block", "dns", "selector", "urltest"}
_CLASH_SUPPORTED_TYPES = {"vless", "vmess", "trojan", "ss", "hy2", "hysteria2"}


def is_supported_share_link(value: str) -> bool:
    return value.strip().lower().startswith(SUPPORTED_SHARE_LINK_PREFIXES)


def extract_supported_share_links(payload: str) -> list[str]:
    return [server.raw_link for server in parse_server_entries(payload) if server.raw_link]


def parse_server_entries(
    payload: str,
    *,
    source: str = "manual",
    subscription_id: str | None = None,
) -> list[ServerEntry]:
    return _auto_parse(payload.strip(), source=source, subscription_id=subscription_id)


def parse_share_link(
    link: str,
    *,
    source: str = "manual",
    subscription_id: str | None = None,
) -> ServerEntry:
    server = _parse_uri(
        link.strip(),
        source=source,
        subscription_id=subscription_id,
        silent=False,
    )
    if server is None:
        raise ValueError("Поддерживаются только ссылки vless://, vmess://, trojan://, ss:// и hy2://.")
    return server


def _auto_parse(
    body: str,
    *,
    source: str,
    subscription_id: str | None,
) -> list[ServerEntry]:
    if not body:
        return []

    if body.startswith("{") or body.startswith("["):
        return _parse_json(body, source=source, subscription_id=subscription_id)

    if len(body) > 20 and _BASE64_BODY_RE.fullmatch(body):
        try:
            decoded = base64.b64decode(body + "==", altchars=b"-_").decode("utf-8")
        except Exception:
            logger.debug("Payload is not valid base64", exc_info=True)
        else:
            parsed = _parse_plain(decoded, source=source, subscription_id=subscription_id)
            if parsed:
                return parsed

    return _parse_plain(body, source=source, subscription_id=subscription_id)


def _parse_plain(
    text: str,
    *,
    source: str,
    subscription_id: str | None,
) -> list[ServerEntry]:
    servers: list[ServerEntry] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        server = _parse_uri(
            line,
            source=source,
            subscription_id=subscription_id,
            silent=True,
        )
        if server is not None:
            servers.append(server)
    return _deduplicate(servers)


def _parse_uri(
    uri: str,
    *,
    source: str,
    subscription_id: str | None,
    silent: bool,
) -> ServerEntry | None:
    normalized = uri.strip()
    lowered = normalized.lower()
    try:
        if lowered.startswith(("vless://", "trojan://", "hy2://", "hysteria2://")):
            return _parse_standard(normalized, source=source, subscription_id=subscription_id)
        if lowered.startswith("vmess://"):
            return _parse_vmess(normalized, source=source, subscription_id=subscription_id)
        if lowered.startswith("ss://"):
            return _parse_shadowsocks(normalized, source=source, subscription_id=subscription_id)
    except Exception as exc:
        if silent:
            logger.debug("Skipping malformed server URI: %s", normalized, exc_info=True)
            return None
        raise ValueError("Некорректная ссылка сервера.") from exc
    if silent:
        return None
    raise ValueError("Неподдерживаемый формат ссылки сервера.")


def _parse_standard(link: str, *, source: str, subscription_id: str | None) -> ServerEntry:
    parsed = urlparse(link)
    params = parse_qs(parsed.query, keep_blank_values=True)
    if not parsed.hostname or parsed.port is None:
        raise ValueError("Некорректная ссылка сервера.")

    def _param(name: str, default: str | None = None) -> str | None:
        values = params.get(name)
        if not values:
            return default
        return values[-1]

    protocol = parsed.scheme.lower()
    if protocol == "hysteria2":
        protocol = "hy2"

    credential = parsed.username or _param("password")
    if not credential:
        raise ValueError("В ссылке отсутствует идентификатор или пароль.")

    name = unquote(parsed.fragment) or f"{parsed.hostname}:{parsed.port}"
    transport = _param("type", "tcp")
    extra = {
        "type": transport,
        "network": transport,
        "path": url_decode(_param("path")),
        "host": url_decode(_param("host")),
        "sni": url_decode(_param("sni")),
        "alpn": url_decode(_param("alpn")),
        "flow": _param("flow"),
        "security": _param("security", "tls" if protocol == "trojan" else "none"),
        "fp": _param("fp"),
        "fingerprint": _param("fp"),
        "pbk": _param("pbk"),
        "public_key": _param("pbk"),
        "sid": _param("sid"),
        "short_id": _param("sid"),
        "spider_x": url_decode(_param("spx")),
        "service_name": url_decode(_param("serviceName")),
        "authority": url_decode(_param("authority")),
        "header_type": _param("headerType"),
        "allow_insecure": _param("allowInsecure", "false"),
    }
    if protocol == "vless":
        extra["id"] = credential
        extra["encryption"] = _param("encryption", "none")
    else:
        extra["password"] = credential
    if protocol == "hy2":
        extra["obfs"] = _param("obfs")
        extra["obfs_password"] = _param("obfs-password")

    return ServerEntry.new(
        name=name,
        protocol=protocol,
        host=parsed.hostname,
        port=parsed.port,
        raw_link=link,
        extra={key: value for key, value in extra.items() if value is not None},
        source=source,
        subscription_id=subscription_id,
    )


def _parse_vmess(link: str, *, source: str, subscription_id: str | None) -> ServerEntry:
    decoded = base64.b64decode(link.removeprefix("vmess://").strip() + "==", altchars=b"-_").decode("utf-8")
    data = json.loads(decoded)
    host = data.get("add")
    port = int(data.get("port"))
    server_id = data.get("id")
    if not host or not server_id:
        raise ValueError("Некорректная VMess ссылка.")

    extra = {
        "id": server_id,
        "alter_id": int(data.get("aid", 0) or 0),
        "security": data.get("scy", "auto"),
        "network": data.get("net", "tcp"),
        "net": data.get("net"),
        "tls": data.get("tls"),
        "path": data.get("path"),
        "host": data.get("host"),
        "sni": data.get("sni") or data.get("host"),
        "alpn": data.get("alpn"),
        "fingerprint": data.get("fp"),
        "fp": data.get("fp"),
        "header_type": data.get("type"),
        "service_name": data.get("serviceName"),
        "authority": data.get("authority"),
    }
    return ServerEntry.new(
        name=data.get("ps") or f"{host}:{port}",
        protocol="vmess",
        host=host,
        port=port,
        raw_link=link,
        extra={key: value for key, value in extra.items() if value is not None},
        source=source,
        subscription_id=subscription_id,
    )


def _parse_shadowsocks(link: str, *, source: str, subscription_id: str | None) -> ServerEntry:
    body = link.removeprefix("ss://")
    body_without_fragment = body.split("#", 1)[0]
    body_without_query = body_without_fragment.split("?", 1)[0]
    name = unquote(body.split("#", 1)[1]) if "#" in body else ""
    credentials_part: str
    host_part: str

    if "@" in body_without_query:
        credentials_part, host_part = body_without_query.rsplit("@", 1)
        try:
            credentials_text = base64.b64decode(credentials_part + "==", altchars=b"-_").decode("utf-8")
        except Exception:
            credentials_text = credentials_part
    else:
        credentials_text = base64.b64decode(body_without_query + "==", altchars=b"-_").decode("utf-8")
        if "@" not in credentials_text:
            raise ValueError("Некорректная Shadowsocks ссылка.")
        credentials_text, host_part = credentials_text.rsplit("@", 1)

    if ":" not in credentials_text:
        raise ValueError("В Shadowsocks ссылке отсутствуют method:password.")
    method, password = credentials_text.split(":", 1)
    host_url = urlsplit(f"//{host_part}")
    if not host_url.hostname or host_url.port is None:
        raise ValueError("В Shadowsocks ссылке отсутствуют адрес или порт.")
    return ServerEntry.new(
        name=name or f"{host_url.hostname}:{host_url.port}",
        protocol="ss",
        host=host_url.hostname,
        port=host_url.port,
        raw_link=link,
        extra={"method": method, "password": password},
        source=source,
        subscription_id=subscription_id,
    )


def _parse_json(body: str, *, source: str, subscription_id: str | None) -> list[ServerEntry]:
    data = json.loads(body)
    if isinstance(data, dict):
        if "outbounds" in data:
            return _deduplicate(_from_singbox(data.get("outbounds") or [], source=source, subscription_id=subscription_id))
        if "proxies" in data:
            return _deduplicate(_from_clash(data.get("proxies") or [], source=source, subscription_id=subscription_id))
        return []
    if isinstance(data, list):
        servers: list[ServerEntry] = []
        for item in data:
            if not isinstance(item, dict) or "link" not in item:
                continue
            server = _parse_uri(
                str(item.get("link", "")),
                source=source,
                subscription_id=subscription_id,
                silent=True,
            )
            if server is not None:
                servers.append(server)
        return _deduplicate(servers)
    return []


def _from_singbox(
    outbounds: list[dict[str, Any]],
    *,
    source: str,
    subscription_id: str | None,
) -> list[ServerEntry]:
    servers: list[ServerEntry] = []
    for outbound in outbounds:
        if not isinstance(outbound, dict):
            continue
        protocol = str(outbound.get("type") or "").lower()
        if protocol in _SINGBOX_SKIP_TYPES:
            continue
        if protocol == "hysteria2":
            protocol = "hy2"

        host = outbound.get("server")
        port = outbound.get("server_port")
        if not host or port is None:
            continue

        servers.append(
            ServerEntry.new(
                name=str(outbound.get("tag") or f"{host}:{port}"),
                protocol=protocol,
                host=str(host),
                port=int(port),
                raw_link="",
                extra=_normalize_singbox_extra(protocol, outbound),
                source=source,
                subscription_id=subscription_id,
            )
        )
    return servers


def _from_clash(
    proxies: list[dict[str, Any]],
    *,
    source: str,
    subscription_id: str | None,
) -> list[ServerEntry]:
    servers: list[ServerEntry] = []
    for proxy in proxies:
        if not isinstance(proxy, dict):
            continue
        protocol = str(proxy.get("type") or "").lower()
        if protocol not in _CLASH_SUPPORTED_TYPES:
            continue
        if protocol == "hysteria2":
            protocol = "hy2"

        host = proxy.get("server")
        port = proxy.get("port")
        if not host or port is None:
            continue

        servers.append(
            ServerEntry.new(
                name=str(proxy.get("name") or f"{host}:{port}"),
                protocol=protocol,
                host=str(host),
                port=int(port),
                raw_link="",
                extra=_normalize_clash_extra(protocol, proxy),
                source=source,
                subscription_id=subscription_id,
            )
        )
    return servers


def _normalize_singbox_extra(protocol: str, outbound: dict[str, Any]) -> dict[str, Any]:
    extra = {
        key: value
        for key, value in outbound.items()
        if key not in {"type", "server", "server_port", "uuid", "password", "tag"}
    }
    if protocol in {"vless", "vmess"} and outbound.get("uuid"):
        extra.setdefault("id", str(outbound["uuid"]))
    if protocol in {"trojan", "hy2"} and outbound.get("password"):
        extra.setdefault("password", str(outbound["password"]))
    if protocol == "ss":
        if outbound.get("password"):
            extra.setdefault("password", str(outbound["password"]))
        if outbound.get("method"):
            extra.setdefault("method", str(outbound["method"]))

    tls = outbound.get("tls")
    if isinstance(tls, dict):
        extra.setdefault("security", "reality" if tls.get("reality") else "tls")
        extra.setdefault("sni", tls.get("server_name") or tls.get("serverName"))
        alpn = tls.get("alpn")
        if isinstance(alpn, list):
            extra.setdefault("alpn", ",".join(alpn))
        else:
            extra.setdefault("alpn", alpn)
        extra.setdefault("allow_insecure", "true" if tls.get("insecure") else "false")
        utls = tls.get("utls")
        if isinstance(utls, dict):
            extra.setdefault("fingerprint", utls.get("fingerprint"))
            extra.setdefault("fp", utls.get("fingerprint"))
        reality = tls.get("reality")
        if isinstance(reality, dict):
            extra.setdefault("public_key", reality.get("public_key"))
            extra.setdefault("pbk", reality.get("public_key"))
            extra.setdefault("short_id", reality.get("short_id"))
            extra.setdefault("sid", reality.get("short_id"))

    transport = outbound.get("transport")
    if isinstance(transport, dict):
        transport_type = transport.get("type") or "tcp"
        extra.setdefault("network", transport_type)
        extra.setdefault("type", transport_type)
        extra.setdefault("path", transport.get("path"))
        headers = transport.get("headers")
        if isinstance(headers, dict):
            extra.setdefault("host", headers.get("Host"))
        extra.setdefault("service_name", transport.get("service_name"))

    return {key: value for key, value in extra.items() if value is not None}


def _normalize_clash_extra(protocol: str, proxy: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if protocol in {"vless", "vmess"} and proxy.get("uuid"):
        extra["id"] = str(proxy["uuid"])
    if protocol in {"trojan", "hy2"} and proxy.get("password"):
        extra["password"] = str(proxy["password"])
    if protocol == "ss":
        if proxy.get("password"):
            extra["password"] = str(proxy["password"])
        if proxy.get("cipher"):
            extra["method"] = str(proxy["cipher"])
        elif proxy.get("method"):
            extra["method"] = str(proxy["method"])

    network = proxy.get("network") or proxy.get("net")
    if network:
        extra["network"] = str(network)
        extra["type"] = str(network)
    if proxy.get("tls") is True:
        extra["security"] = "tls"
        extra["tls"] = "tls"
    if proxy.get("servername"):
        extra["sni"] = str(proxy["servername"])
    elif proxy.get("sni"):
        extra["sni"] = str(proxy["sni"])
    if proxy.get("client-fingerprint"):
        extra["fingerprint"] = str(proxy["client-fingerprint"])
        extra["fp"] = str(proxy["client-fingerprint"])

    ws_opts = proxy.get("ws-opts") or proxy.get("ws_opts")
    if isinstance(ws_opts, dict):
        extra.setdefault("network", "ws")
        extra.setdefault("type", "ws")
        extra.setdefault("path", ws_opts.get("path"))
        headers = ws_opts.get("headers")
        if isinstance(headers, dict):
            extra.setdefault("host", headers.get("Host"))

    grpc_opts = proxy.get("grpc-opts") or proxy.get("grpc_opts")
    if isinstance(grpc_opts, dict):
        extra.setdefault("network", "grpc")
        extra.setdefault("type", "grpc")
        extra.setdefault("service_name", grpc_opts.get("grpc-service-name") or grpc_opts.get("serviceName"))

    reality_opts = proxy.get("reality-opts") or proxy.get("reality_opts")
    if isinstance(reality_opts, dict):
        extra["security"] = "reality"
        extra["public_key"] = reality_opts.get("public-key") or reality_opts.get("publicKey")
        extra["pbk"] = extra.get("public_key")
        extra["short_id"] = reality_opts.get("short-id") or reality_opts.get("shortId")
        extra["sid"] = extra.get("short_id")

    if proxy.get("skip-cert-verify") is True:
        extra["allow_insecure"] = "true"

    return {key: value for key, value in extra.items() if value is not None}


def _deduplicate(servers: list[ServerEntry]) -> list[ServerEntry]:
    unique: list[ServerEntry] = []
    seen: set[tuple[str, int, str]] = set()
    for server in servers:
        key = _server_identity(server)
        if key in seen:
            continue
        seen.add(key)
        unique.append(server)
    return unique


def _server_identity(server: ServerEntry) -> tuple[str, int, str]:
    credential = str(server.extra.get("id") or "") or str(server.extra.get("password") or "")
    return (server.host.lower(), server.port, credential)
