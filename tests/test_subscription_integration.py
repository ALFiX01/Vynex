from __future__ import annotations

import base64
import json
from unittest.mock import Mock, patch

from vynex_vpn_client.app import VynexVpnApp
from vynex_vpn_client.models import ServerEntry, SubscriptionEntry
from vynex_vpn_client.parsers import parse_server_entries
from vynex_vpn_client.subscriptions import SubscriptionManager, merge_subscription_servers


def _make_server(
    name: str,
    *,
    host: str = "example.com",
    port: int = 443,
    protocol: str = "vless",
    extra: dict[str, object] | None = None,
) -> ServerEntry:
    return ServerEntry.new(
        name=name,
        protocol=protocol,
        host=host,
        port=port,
        raw_link="",
        extra=extra or {"id": "id-1"},
        source="subscription",
        subscription_id="sub-1",
    )


def test_parse_server_entries_supports_urlsafe_base64_bundle() -> None:
    payload = "\n".join(
        [
            "vless://id-1@example.com:443?security=reality&pbk=KEY&sid=SID&fp=chrome#One",
            "vless://id-2@example.com:8443#Two",
        ]
    )
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")

    servers = parse_server_entries(encoded)

    assert len(servers) == 2
    assert servers[0].extra["public_key"] == "KEY"
    assert servers[0].extra["short_id"] == "SID"
    assert servers[0].extra["fingerprint"] == "chrome"


def test_parse_server_entries_supports_clash_json() -> None:
    payload = json.dumps(
        {
            "proxies": [
                {
                    "type": "vless",
                    "name": "One",
                    "server": "one.example.com",
                    "port": 443,
                    "uuid": "id-1",
                    "tls": True,
                    "servername": "sni.example.com",
                },
                {
                    "type": "ss",
                    "name": "Two",
                    "server": "two.example.com",
                    "port": 8388,
                    "password": "secret",
                    "cipher": "aes-128-gcm",
                },
            ]
        }
    )

    servers = parse_server_entries(payload)

    assert len(servers) == 2
    assert servers[0].extra["id"] == "id-1"
    assert servers[0].extra["sni"] == "sni.example.com"
    assert servers[1].extra["method"] == "aes-128-gcm"


def test_fetch_subscription_servers_uses_v2rayn_user_agent() -> None:
    response = Mock()
    response.text = "vless://id-1@example.com:443#One"
    manager = SubscriptionManager(Mock())

    with patch("vynex_vpn_client.subscriptions.httpx.get", return_value=response) as get_mock:
        servers = manager.fetch_subscription_servers("https://example.com/sub", subscription_id="sub-1")

    assert len(servers) == 1
    get_mock.assert_called_once_with(
        "https://example.com/sub",
        headers={"User-Agent": "v2rayN/6.0"},
        follow_redirects=True,
        timeout=15,
    )
    response.raise_for_status.assert_called_once_with()


def test_merge_subscription_servers_preserves_custom_name_and_marks_stale() -> None:
    old = [
        _make_server("Мой сервер", extra={"id": "id-1", "custom_name": True}),
        _make_server("Old stale", host="stale.example.com", extra={"id": "id-2"}),
    ]
    fresh = [
        _make_server("Server #1", extra={"id": "id-1", "sni": "example.com"}),
    ]

    merged = merge_subscription_servers(old, fresh)

    active = next(server for server in merged if server.extra.get("id") == "id-1")
    stale = next(server for server in merged if server.extra.get("id") == "id-2")

    assert active.name == "Мой сервер"
    assert active.extra["sni"] == "example.com"
    assert stale.extra["stale"] is True


def test_app_detects_json_bundle_for_manual_import() -> None:
    app = object.__new__(VynexVpnApp)
    payload = json.dumps(
        {
            "outbounds": [
                {"type": "vless", "server": "one.example.com", "server_port": 443, "uuid": "id-1", "tag": "One"},
                {"type": "direct", "tag": "Bypass"},
            ]
        }
    )

    import_kind, parsed = app._detect_import_target(payload)

    assert import_kind == "server_bundle"
    assert isinstance(parsed, list)
    assert len(parsed) == 1


def test_subscription_manager_updates_subscription_server_ids_with_stale_entries() -> None:
    storage = Mock()
    subscription = SubscriptionEntry.new(url="https://example.com/sub", title="Example")
    subscription.id = "sub-1"
    old = [
        _make_server("Old", extra={"id": "id-1"}),
        _make_server("Stale", host="stale.example.com", extra={"id": "id-2"}),
    ]
    fresh = [
        _make_server("New", extra={"id": "id-1"}),
    ]
    saved: list[ServerEntry] = []

    def _upsert(server: ServerEntry) -> ServerEntry:
        saved.append(server)
        return server

    storage.load_servers.return_value = old
    storage.upsert_server.side_effect = _upsert
    manager = SubscriptionManager(storage)

    imported = manager.import_subscription_servers(subscription, fresh)

    assert len(imported) == 2
    assert len(subscription.server_ids) == 2
    assert any(server.extra.get("stale") for server in imported)
