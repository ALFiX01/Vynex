from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from .amneziawg import try_parse_amneziawg_config_file, try_parse_amneziawg_config_text
from .models import ServerEntry
from .utils import url_decode
from .vpn_uri import import_vpn_uri, is_vpn_uri
from .xray_import import is_probable_xray_config_data, parse_xray_json_config

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
_PASTE_ARTIFACTS = "\ufeff\u200b\u200c\u200d\u2060\xa0"
_PASTE_WRAPPERS = (
    ("<", ">"),
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
)


def _normalize_uri_candidate(value: str) -> str:
    normalized = value.strip().strip(_PASTE_ARTIFACTS)
    while normalized:
        previous = normalized
        for opener, closer in _PASTE_WRAPPERS:
            if normalized.startswith(opener) and normalized.endswith(closer):
                normalized = normalized[len(opener) : len(normalized) - len(closer)].strip().strip(_PASTE_ARTIFACTS)
                break
        if normalized == previous:
            break
    return normalized


def is_supported_share_link(value: str) -> bool:
    return _normalize_uri_candidate(value).lower().startswith(SUPPORTED_SHARE_LINK_PREFIXES)


def extract_supported_share_links(payload: str) -> list[str]:
    return [server.raw_link for server in parse_server_entries(payload) if server.raw_link]


def parse_server_entries(
    payload: str,
    *,
    source: str = "manual",
    subscription_id: str | None = None,
) -> list[ServerEntry]:
    return _auto_parse(payload.strip().strip(_PASTE_ARTIFACTS), source=source, subscription_id=subscription_id)


def parse_share_link(
    link: str,
    *,
    source: str = "manual",
    subscription_id: str | None = None,
) -> ServerEntry:
    server = _parse_uri(
        _normalize_uri_candidate(link),
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

    awg_from_file = try_parse_amneziawg_config_file(
        body,
        source=source,
        subscription_id=subscription_id,
    )
    if awg_from_file is not None:
        return [awg_from_file]

    if is_vpn_uri(body):
        return [import_vpn_uri(body, source=source, subscription_id=subscription_id)]

    if len(body) > 20 and _BASE64_BODY_RE.fullmatch(body):
        try:
            decoded = base64.b64decode(body + "==", altchars=b"-_").decode("utf-8")
        except Exception:
            logger.debug("Payload is not valid base64", exc_info=True)
        else:
            awg_from_base64 = try_parse_amneziawg_config_text(
                decoded,
                source=source,
                subscription_id=subscription_id,
            )
            if awg_from_base64 is not None:
                return [awg_from_base64]
            parsed = _parse_plain(decoded, source=source, subscription_id=subscription_id)
            if parsed:
                return parsed

    awg_from_text = try_parse_amneziawg_config_text(
        body,
        source=source,
        subscription_id=subscription_id,
    )
    if awg_from_text is not None:
        return [awg_from_text]

    if body.startswith("{") or body.startswith("["):
        return _parse_json(body, source=source, subscription_id=subscription_id)

    return _parse_plain(body, source=source, subscription_id=subscription_id)


def _parse_plain(
    text: str,
    *,
    source: str,
    subscription_id: str | None,
) -> list[ServerEntry]:
    servers: list[ServerEntry] = []
    for raw_line in text.splitlines():
        line = _normalize_uri_candidate(raw_line)
        if not line or line.startswith("#"):
            continue
        if is_vpn_uri(line):
            try:
                servers.append(import_vpn_uri(line, source=source, subscription_id=subscription_id))
            except ValueError:
                logger.debug("Skipping malformed vpn:// payload", exc_info=True)
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
    normalized = _normalize_uri_candidate(uri)
    lowered = normalized.lower()
    try:
        if lowered.startswith(("hy2://", "hysteria2://")):
            return _parse_hysteria2(normalized, source=source, subscription_id=subscription_id)
        if lowered.startswith(("vless://", "trojan://")):
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


def _parse_hysteria2(link: str, *, source: str, subscription_id: str | None) -> ServerEntry:
    split = urlsplit(link)
    params = parse_qs(split.query, keep_blank_values=True)

    def _param(name: str, default: str | None = None) -> str | None:
        values = params.get(name)
        if not values:
            return default
        return values[-1]

    userinfo, host, port_spec = _parse_hysteria2_netloc(split.netloc)
    if not host:
        raise ValueError("Некорректная ссылка Hysteria2.")

    password = unquote(userinfo) if userinfo else _param("password")
    if not password:
        raise ValueError("В ссылке Hysteria2 отсутствует пароль.")

    port_source = port_spec or _param("mport")
    port, server_ports = _parse_hysteria2_port_spec(port_source)
    host_label = host if server_ports is None else f"{host}:{port_source}"
    name = unquote(split.fragment) or host_label

    extra: dict[str, Any] = {
        "password": password,
        "sni": url_decode(_param("sni")),
        "obfs": _param("obfs"),
        "obfs_password": _param("obfs-password") or _param("obfs_password"),
        "pin_sha256": _param("pinSHA256"),
        "alpn": _param("alpn"),
        "insecure": _param("insecure"),
        "allow_insecure": _param("allowInsecure"),
        "up_mbps": _param("upmbps") or _param("up_mbps"),
        "down_mbps": _param("downmbps") or _param("down_mbps"),
        "hop_interval": _param("hopInterval") or _param("hop_interval"),
        "hop_interval_max": _param("hopIntervalMax") or _param("hop_interval_max"),
        "bbr_profile": _param("bbrProfile") or _param("bbr_profile"),
    }
    if server_ports is not None:
        extra["server_ports"] = server_ports

    return ServerEntry.new(
        name=name,
        protocol="hy2",
        host=host,
        port=port,
        raw_link=link,
        extra={key: value for key, value in extra.items() if value is not None},
        source=source,
        subscription_id=subscription_id,
    )


def _parse_hysteria2_netloc(netloc: str) -> tuple[str | None, str, str | None]:
    userinfo: str | None = None
    host_port = netloc
    if "@" in netloc:
        userinfo, host_port = netloc.rsplit("@", 1)
    host_port = host_port.strip()
    if not host_port:
        raise ValueError("Некорректная ссылка Hysteria2.")
    if host_port.startswith("["):
        bracket_end = host_port.find("]")
        if bracket_end == -1:
            raise ValueError("Некорректная ссылка Hysteria2.")
        host = host_port[1:bracket_end]
        remainder = host_port[bracket_end + 1 :]
        if not remainder:
            return userinfo, host, None
        if not remainder.startswith(":"):
            raise ValueError("Некорректная ссылка Hysteria2.")
        return userinfo, host, remainder[1:] or None
    if host_port.count(":") == 0:
        return userinfo, host_port, None
    if host_port.count(":") == 1:
        host, port_spec = host_port.split(":", 1)
        return userinfo, host, port_spec or None
    raise ValueError("Некорректная ссылка Hysteria2.")


def _parse_hysteria2_port_spec(value: str | None) -> tuple[int, list[str] | None]:
    if value in (None, ""):
        return 443, None

    parts = [item.strip() for item in str(value).split(",") if item.strip()]
    if not parts:
        raise ValueError("Некорректный порт Hysteria2.")
    if len(parts) == 1 and "-" not in parts[0]:
        port = int(parts[0])
        if not 1 <= port <= 65535:
            raise ValueError("Некорректный порт Hysteria2.")
        return port, None

    server_ports: list[str] = []
    first_port: int | None = None
    for part in parts:
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if not 1 <= start <= end <= 65535:
                raise ValueError("Некорректный диапазон портов Hysteria2.")
            server_ports.append(f"{start}:{end}")
            if first_port is None:
                first_port = start
            continue
        port = int(part)
        if not 1 <= port <= 65535:
            raise ValueError("Некорректный порт Hysteria2.")
        server_ports.append(str(port))
        if first_port is None:
            first_port = port
    if first_port is None:
        raise ValueError("Некорректный порт Hysteria2.")
    return first_port, server_ports


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
        if is_probable_xray_config_data(data):
            return _deduplicate(parse_xray_json_config(data, source=source, subscription_id=subscription_id))
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
        certificate_pins = tls.get("certificate_public_key_sha256")
        if isinstance(certificate_pins, list) and certificate_pins:
            extra.setdefault("pin_sha256", str(certificate_pins[0]))
        reality = tls.get("reality")
        if isinstance(reality, dict):
            extra.setdefault("public_key", reality.get("public_key"))
            extra.setdefault("pbk", reality.get("public_key"))
            extra.setdefault("short_id", reality.get("short_id"))
            extra.setdefault("sid", reality.get("short_id"))
    if protocol == "hy2":
        obfs = outbound.get("obfs")
        if isinstance(obfs, dict):
            extra.setdefault("obfs", obfs.get("type"))
            extra.setdefault("obfs_password", obfs.get("password"))
        for key in ("server_ports", "hop_interval", "hop_interval_max", "up_mbps", "down_mbps", "bbr_profile", "brutal_debug"):
            if outbound.get(key) is not None:
                extra.setdefault(key, outbound.get(key))

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
        extra["insecure"] = "true"
    if protocol == "hy2":
        if proxy.get("obfs"):
            extra["obfs"] = str(proxy["obfs"])
        obfs_password = proxy.get("obfs-password") or proxy.get("obfs_password")
        if obfs_password:
            extra["obfs_password"] = str(obfs_password)
        if proxy.get("ports"):
            extra["server_ports"] = proxy.get("ports")
        if proxy.get("mport"):
            extra["server_ports"] = proxy.get("mport")
        if proxy.get("up"):
            extra["up_mbps"] = proxy.get("up")
        if proxy.get("down"):
            extra["down_mbps"] = proxy.get("down")
        if proxy.get("hop-interval"):
            extra["hop_interval"] = proxy.get("hop-interval")
        if proxy.get("hop_interval"):
            extra["hop_interval"] = proxy.get("hop_interval")
        if proxy.get("pinSHA256"):
            extra["pin_sha256"] = proxy.get("pinSHA256")

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
    return (server.host.lower(), server.port, server.identity_token)
