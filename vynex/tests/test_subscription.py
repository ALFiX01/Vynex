from __future__ import annotations

import base64
import json
from unittest.mock import Mock, patch

from vynex.core.process_manager import Server
from vynex.core.subscription import (
    _auto_parse,
    _deduplicate,
    _parse_plain,
    _parse_uri,
    merge_servers,
    fetch_subscription,
)


def _vless(index: int, *, host: str = "example.com") -> str:
    return f"vless://uuid-{index}@{host}:443?type=tcp#Server-{index}"


def _make_server(name: str, *, address: str = "example.com", port: int = 443, uuid: str = "id") -> Server:
    return Server(
        protocol="vless",
        address=address,
        port=port,
        uuid=uuid,
        name=name,
        raw_uri=f"vless://{uuid}@{address}:{port}#{name}",
        extra={},
    )


def _urlsafe_encoded_text(text: str) -> str:
    encoded = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")
    if "-" in encoded or "_" in encoded:
        return encoded

    for suffix in range(1, 500):
        candidate = f"{text}\n# {suffix} Проба"
        encoded = base64.urlsafe_b64encode(candidate.encode("utf-8")).decode("ascii")
        if "-" in encoded or "_" in encoded:
            return encoded
    raise AssertionError("Failed to build URL-safe base64 sample")


def test_autodetect_base64() -> None:
    payload = "\n".join(_vless(index) for index in range(1, 4))
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")

    servers = _auto_parse(encoded)

    assert len(servers) == 3
    assert all(server.protocol == "vless" for server in servers)


def test_autodetect_url_safe_base64() -> None:
    payload = _vless(1)
    encoded = _urlsafe_encoded_text(payload)

    servers = _auto_parse(encoded)

    assert len(servers) == 1
    assert servers[0].protocol == "vless"


def test_autodetect_json_singbox() -> None:
    body = json.dumps(
        {
            "outbounds": [
                {"type": "vless", "server": "one.example.com", "server_port": 443, "uuid": "id-1", "tag": "One"},
                {"type": "direct", "tag": "Bypass"},
                {"type": "vless", "server": "two.example.com", "server_port": 8443, "uuid": "id-2", "tag": "Two"},
            ]
        }
    )

    servers = _auto_parse(body)

    assert len(servers) == 2
    assert [server.name for server in servers] == ["One", "Two"]


def test_autodetect_json_clash() -> None:
    body = json.dumps(
        {
            "proxies": [
                {"type": "vless", "server": "one.example.com", "port": 443, "uuid": "id-1", "name": "One"},
                {"type": "ss", "server": "two.example.com", "port": 8388, "password": "secret", "name": "Two"},
            ]
        }
    )

    servers = _auto_parse(body)

    assert len(servers) == 2
    assert [server.protocol for server in servers] == ["vless", "ss"]


def test_parse_vless_reality() -> None:
    server = _parse_uri(
        "vless://uuid@host:443?security=reality&pbk=KEY&sid=SID&fp=chrome#Name"
    )

    assert server is not None
    assert server.extra["pbk"] == "KEY"
    assert server.extra["sid"] == "SID"
    assert server.extra["fp"] == "chrome"


def test_parse_vmess_port_as_string() -> None:
    payload = {
        "add": "host",
        "port": "8080",
        "id": "uuid",
        "ps": "Name",
        "host": "sni.host",
    }
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")

    server = _parse_uri(f"vmess://{encoded}")

    assert server is not None
    assert server.port == 8080
    assert isinstance(server.port, int)


def test_parse_shadowsocks_base64_credentials_format() -> None:
    credentials = base64.b64encode(b"aes-128-gcm:secret").decode("ascii")

    server = _parse_uri(f"ss://{credentials}@example.com:8388#Name")

    assert server is not None
    assert server.address == "example.com"
    assert server.port == 8388
    assert server.uuid == "secret"


def test_parse_shadowsocks_embedded_format() -> None:
    payload = base64.b64encode(b"aes-128-gcm:secret@example.com:8388").decode("ascii")

    server = _parse_uri(f"ss://{payload}#Name")

    assert server is not None
    assert server.address == "example.com"
    assert server.port == 8388
    assert server.uuid == "secret"


def test_parse_ipv6() -> None:
    server = _parse_uri("vless://uuid@[::1]:443#test")

    assert server is not None
    assert server.address == "::1"


def test_deduplicate_keeps_first() -> None:
    servers = [
        _make_server("First"),
        _make_server("Second"),
        _make_server("Third"),
    ]

    result = _deduplicate(servers)

    assert len(result) == 1
    assert result[0].name == "First"


def test_merge_preserves_custom_name() -> None:
    old = [
        Server(
            protocol="vless",
            address="example.com",
            port=443,
            uuid="id-1",
            name="Мой сервер",
            raw_uri="vless://id-1@example.com:443#old",
            extra={"custom_name": True},
        )
    ]
    fresh = [
        Server(
            protocol="vless",
            address="example.com",
            port=443,
            uuid="id-1",
            name="Server #1",
            raw_uri="vless://id-1@example.com:443#fresh",
            extra={"sni": "example.com"},
        )
    ]

    merged = merge_servers(old, fresh)

    assert len(merged) == 1
    assert merged[0].name == "Мой сервер"
    assert merged[0].extra["sni"] == "example.com"


def test_merge_marks_stale() -> None:
    old = [
        _make_server("First", uuid="id-1"),
        _make_server("Second", uuid="id-2"),
    ]
    fresh = [_make_server("First Updated", uuid="id-1")]

    merged = merge_servers(old, fresh)
    stale = next(server for server in merged if server.uuid == "id-2")

    assert len(merged) == 2
    assert stale.extra["stale"] is True


def test_fetch_sets_user_agent() -> None:
    response = Mock()
    response.text = _vless(1)

    with patch("vynex.core.subscription.httpx.get", return_value=response) as get_mock:
        fetch_subscription("https://example.com/sub")

    get_mock.assert_called_once_with(
        "https://example.com/sub",
        headers={"User-Agent": "v2rayN/6.0"},
        follow_redirects=True,
        timeout=15,
    )
    response.raise_for_status.assert_called_once_with()


def test_broken_uri_skipped_silently() -> None:
    payload = "\n".join([_vless(1), "this is not a uri"])

    servers = _parse_plain(payload)

    assert len(servers) == 1
    assert servers[0].protocol == "vless"
