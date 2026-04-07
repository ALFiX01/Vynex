from __future__ import annotations

import json
from urllib.parse import parse_qs, unquote, urlsplit

from .models import ServerEntry
from .utils import decode_base64, url_decode

SUPPORTED_SHARE_LINK_PREFIXES = ("vless://", "vmess://", "ss://")


def is_supported_share_link(value: str) -> bool:
    return value.strip().startswith(SUPPORTED_SHARE_LINK_PREFIXES)


def extract_supported_share_links(payload: str) -> list[str]:
    normalized = payload.strip()
    if not normalized:
        return []
    decoded_payload = normalized
    if "://" not in normalized:
        try:
            decoded_payload = decode_base64(normalized)
        except ValueError:
            decoded_payload = normalized
    return [line.strip() for line in decoded_payload.splitlines() if is_supported_share_link(line)]


def parse_share_link(
    link: str,
    *,
    source: str = "manual",
    subscription_id: str | None = None,
) -> ServerEntry:
    normalized = link.strip()
    if normalized.startswith("vless://"):
        return _parse_vless(normalized, source=source, subscription_id=subscription_id)
    if normalized.startswith("vmess://"):
        return _parse_vmess(normalized, source=source, subscription_id=subscription_id)
    if normalized.startswith("ss://"):
        return _parse_shadowsocks(normalized, source=source, subscription_id=subscription_id)
    raise ValueError("Поддерживаются только ссылки vless://, vmess:// и ss://.")


def _parse_vless(link: str, *, source: str, subscription_id: str | None) -> ServerEntry:
    parsed = urlsplit(link)
    params = {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
    if not parsed.username or not parsed.hostname or not parsed.port:
        raise ValueError("Некорректная VLESS ссылка.")
    name = unquote(parsed.fragment) or f"{parsed.hostname}:{parsed.port}"
    extra = {
        "id": parsed.username,
        "encryption": params.get("encryption", "none"),
        "security": params.get("security", "none"),
        "flow": params.get("flow"),
        "network": params.get("type", "tcp"),
        "path": url_decode(params.get("path")),
        "host": url_decode(params.get("host")),
        "sni": url_decode(params.get("sni")),
        "alpn": url_decode(params.get("alpn")),
        "fingerprint": params.get("fp"),
        "public_key": params.get("pbk"),
        "short_id": params.get("sid"),
        "spider_x": url_decode(params.get("spx")),
        "service_name": url_decode(params.get("serviceName")),
        "authority": url_decode(params.get("authority")),
        "header_type": params.get("headerType"),
        "allow_insecure": params.get("allowInsecure", "false"),
    }
    return ServerEntry.new(
        name=name,
        protocol="vless",
        host=parsed.hostname,
        port=parsed.port,
        raw_link=link,
        extra=extra,
        source=source,
        subscription_id=subscription_id,
    )


def _parse_vmess(link: str, *, source: str, subscription_id: str | None) -> ServerEntry:
    payload = link.removeprefix("vmess://").strip()
    try:
        data = json.loads(decode_base64(payload))
    except json.JSONDecodeError as exc:
        raise ValueError("Некорректная VMess ссылка.") from exc
    host = data.get("add")
    port_raw = data.get("port")
    if not host or not port_raw:
        raise ValueError("В VMess ссылке отсутствуют адрес или порт.")
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError("В VMess ссылке указан неверный порт.") from exc
    name = data.get("ps") or f"{host}:{port}"
    extra = {
        "id": data.get("id"),
        "alter_id": int(data.get("aid", 0) or 0),
        "security": data.get("scy", "auto"),
        "network": data.get("net", "tcp"),
        "tls": data.get("tls", ""),
        "path": data.get("path"),
        "host": data.get("host"),
        "sni": data.get("sni"),
        "alpn": data.get("alpn"),
        "fingerprint": data.get("fp"),
        "header_type": data.get("type"),
        "service_name": data.get("serviceName"),
        "authority": data.get("authority"),
    }
    return ServerEntry.new(
        name=name,
        protocol="vmess",
        host=host,
        port=port,
        raw_link=link,
        extra=extra,
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
            credentials_text = decode_base64(credentials_part)
        except ValueError:
            credentials_text = credentials_part
    else:
        decoded = decode_base64(body_without_query)
        if "@" not in decoded:
            raise ValueError("Некорректная Shadowsocks ссылка.")
        credentials_text, host_part = decoded.rsplit("@", 1)

    if ":" not in credentials_text:
        raise ValueError("В Shadowsocks ссылке отсутствуют method:password.")
    method, password = credentials_text.split(":", 1)
    host_url = urlsplit(f"//{host_part}")
    if not host_url.hostname or not host_url.port:
        raise ValueError("В Shadowsocks ссылке отсутствуют адрес или порт.")
    title = name or f"{host_url.hostname}:{host_url.port}"
    extra = {
        "method": method,
        "password": password,
    }
    return ServerEntry.new(
        name=title,
        protocol="ss",
        host=host_url.hostname,
        port=host_url.port,
        raw_link=link,
        extra=extra,
        source=source,
        subscription_id=subscription_id,
    )
