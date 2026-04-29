from __future__ import annotations

import base64
import json
import zlib

import pytest

from vynex_vpn_client.app import VynexVpnApp
from vynex_vpn_client.parsers import parse_server_entries


def _wg_key(seed: bytes) -> str:
    return base64.b64encode(seed * 32).decode("ascii")


def _sample_awg_conf() -> str:
    return f"""
[Interface]
PrivateKey = {_wg_key(b"a")}
Address = 10.66.66.2/32, fd00::2/128
DNS = 1.1.1.1, 1.0.0.1
MTU = 1280
Jc = 4
Jmin = 8
Jmax = 80
S1 = 15
S2 = 32
S3 = 48
S4 = 24
H1 = 111
H2 = 222
H3 = 333
H4 = 444
I1 = <b 0xdeadbeef>

[Peer]
PublicKey = {_wg_key(b"b")}
PresharedKey = {_wg_key(b"c")}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = vpn.example.com:51820
PersistentKeepalive = 25
""".strip()


def _sample_xray_config() -> dict[str, object]:
    return {
        "inbounds": [],
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "xray.example.com",
                            "port": 443,
                            "users": [
                                {
                                    "id": "11111111-1111-1111-1111-111111111111",
                                    "encryption": "none",
                                    "flow": "xtls-rprx-vision",
                                }
                            ],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "serverName": "reality.example.com",
                        "fingerprint": "chrome",
                        "publicKey": "REALITYPUB",
                        "shortId": "abcd1234",
                        "spiderX": "/",
                    },
                },
            },
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "dns-out", "protocol": "dns"},
        ],
    }


def _sample_awg_native_payload() -> dict[str, object]:
    return {
        "config": _sample_awg_conf(),
        "hostName": "vpn.example.com",
        "port": 51820,
        "client_priv_key": _wg_key(b"a"),
        "client_pub_key": _wg_key(b"d"),
        "server_pub_key": _wg_key(b"b"),
        "psk_key": _wg_key(b"c"),
        "client_ip": "10.66.66.2/32, fd00::2/128",
        "allowed_ips": ["0.0.0.0/0", "::/0"],
        "persistent_keep_alive": 25,
        "mtu": "1280",
        "Jc": 4,
        "Jmin": 8,
        "Jmax": 80,
        "S1": 15,
        "S2": 32,
        "S3": 48,
        "S4": 24,
        "H1": "111",
        "H2": "222",
        "H3": "333",
        "H4": "444",
        "I1": "<b 0xdeadbeef>",
    }


def _sample_vpn_container(*, default_container: str = "amnezia-awg2") -> dict[str, object]:
    return {
        "description": "Office",
        "hostName": "vpn.example.com",
        "dns1": "1.1.1.1",
        "dns2": "1.0.0.1",
        "defaultContainer": default_container,
        "metadata": {"owner": "Daniil"},
        "containers": [
            {
                "container": "amnezia-awg2",
                "awg": {
                    "last_config": json.dumps(_sample_awg_native_payload(), separators=(",", ":")),
                    "protocol_version": "2",
                    "custom_server_field": "keep-me",
                },
                "server_note": "alpha",
            },
            {
                "container": "amnezia-xray",
                "xray": {
                    "last_config": json.dumps(_sample_xray_config(), separators=(",", ":")),
                    "transport_proto": "tcp",
                    "custom_xray_field": "keep-xray",
                },
            },
        ],
    }


def _qt_compress(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, byteorder="big") + zlib.compress(payload, level=8)


def _build_vpn_uri(payload: dict[str, object], *, signed: bool = False) -> str:
    encoded_payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if signed:
        raw = bytes.fromhex("000000ff") + zlib.compress(encoded_payload, level=6)
    else:
        raw = _qt_compress(encoded_payload)
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"vpn://{encoded}"


def test_parse_server_entries_imports_default_awg_from_vpn_uri() -> None:
    uri = _build_vpn_uri(_sample_vpn_container())

    servers = parse_server_entries(uri)

    assert len(servers) == 1
    server = servers[0]
    assert server.protocol == "amneziawg"
    assert server.is_amneziawg is True
    assert server.amneziawg_profile is not None
    assert server.amneziawg_profile.protocol_version == "2.0"
    assert server.raw_link == uri
    metadata = server.extra["vpn_payload"]
    assert metadata["payload_kind"] == "amnezia-container"
    assert metadata["raw_config"]["metadata"]["owner"] == "Daniil"
    assert len(metadata["connections"]) == 2
    default_connection = next(item for item in metadata["connections"] if item["is_default"])
    assert default_connection["source_container"] == "amnezia-awg2"
    assert default_connection["details"]["raw_container"]["server_note"] == "alpha"


def test_parse_server_entries_imports_default_xray_from_vpn_uri() -> None:
    uri = _build_vpn_uri(_sample_vpn_container(default_container="amnezia-xray"))

    servers = parse_server_entries(uri)

    assert len(servers) == 1
    server = servers[0]
    assert server.protocol == "vless"
    assert server.host == "xray.example.com"
    assert server.port == 443
    assert server.extra["public_key"] == "REALITYPUB"
    assert server.extra["short_id"] == "abcd1234"
    assert server.extra["fingerprint"] == "chrome"
    default_connection = next(item for item in server.extra["vpn_payload"]["connections"] if item["is_default"])
    assert default_connection["source_container"] == "amnezia-xray"


def test_parse_server_entries_imports_xray_xhttp_settings() -> None:
    payload = {
        "outbounds": [
            {
                "tag": "Reality XHTTP",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "xray.example.com",
                            "port": 443,
                            "users": [
                                {
                                    "id": "11111111-1111-1111-1111-111111111111",
                                    "encryption": "none",
                                }
                            ],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "reality",
                    "realitySettings": {
                        "serverName": "reality.example.com",
                        "fingerprint": "chrome",
                        "publicKey": "REALITYPUB",
                        "shortId": "abcd1234",
                    },
                    "xhttpSettings": {
                        "host": "edge.example.com",
                        "path": "/xhttp",
                        "mode": "packet-up",
                        "extra": {"xmux": {"maxConcurrency": "1-2"}},
                    },
                },
            }
        ],
    }

    servers = parse_server_entries(json.dumps(payload))

    assert len(servers) == 1
    server = servers[0]
    assert server.extra["network"] == "xhttp"
    assert server.extra["path"] == "/xhttp"
    assert server.extra["host"] == "edge.example.com"
    assert server.extra["mode"] == "packet-up"
    assert server.extra["xhttp_extra"] == {"xmux": {"maxConcurrency": "1-2"}}
    assert server.extra["public_key"] == "REALITYPUB"


def test_app_detect_import_target_accepts_vpn_uri() -> None:
    app = object.__new__(VynexVpnApp)
    uri = _build_vpn_uri(_sample_vpn_container())

    import_kind, payload = app._detect_import_target(uri)

    assert import_kind == "server_bundle"
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0].protocol == "amneziawg"


def test_parse_server_entries_reports_decode_error_for_broken_vpn_uri() -> None:
    with pytest.raises(ValueError, match="decode error:"):
        parse_server_entries("vpn://%%%%")


def test_parse_server_entries_reports_unsupported_vpn_payload_version() -> None:
    uri = _build_vpn_uri({"config_version": 999, "auth_data": {"api_key": "token"}}, signed=True)

    with pytest.raises(ValueError, match="unsupported vpn payload version: 999"):
        parse_server_entries(uri)


def test_parse_server_entries_reports_unsupported_embedded_protocol() -> None:
    uri = _build_vpn_uri(
        {
            "description": "OpenVPN only",
            "defaultContainer": "amnezia-openvpn",
            "containers": [
                {
                    "container": "amnezia-openvpn",
                    "openvpn": {"last_config": "client\ndev tun\nremote example.com 1194\n"},
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="unsupported embedded protocol:"):
        parse_server_entries(uri)


def test_parse_server_entries_reports_incomplete_config_for_missing_last_config() -> None:
    uri = _build_vpn_uri(
        {
            "description": "Broken AWG",
            "defaultContainer": "amnezia-awg2",
            "containers": [
                {
                    "container": "amnezia-awg2",
                    "awg": {"protocol_version": "2"},
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="incomplete config: .*last_config"):
        parse_server_entries(uri)


def test_parse_server_entries_falls_back_to_native_awg_payload_when_embedded_config_has_placeholders() -> None:
    awg_payload = _sample_awg_native_payload()
    awg_payload["client_ip"] = "10.66.66.2"
    awg_payload["I4"] = ""
    awg_payload["I5"] = ""
    awg_payload["config"] = (
        "[Interface]\n"
        "Address = 10.66.66.2/32\n"
        "DNS = $PRIMARY_DNS, $SECONDARY_DNS\n"
        f"PrivateKey = {awg_payload['client_priv_key']}\n"
        "Jc = 4\n"
        "\n"
        "[Peer]\n"
        f"PublicKey = {awg_payload['server_pub_key']}\n"
        f"PresharedKey = {awg_payload['psk_key']}\n"
        "AllowedIPs = 0.0.0.0/0, ::/0\n"
        "Endpoint = vpn.example.com:51820\n"
        "PersistentKeepalive = 25\n"
    )
    uri = _build_vpn_uri(
        {
            "description": "Placeholder DNS",
            "hostName": "vpn.example.com",
            "dns1": "1.1.1.1",
            "dns2": "1.0.0.1",
            "defaultContainer": "amnezia-awg2",
            "containers": [
                {
                    "container": "amnezia-awg2",
                    "awg": {
                        "last_config": json.dumps(awg_payload, separators=(",", ":")),
                        "protocol_version": "2",
                    },
                }
            ],
        }
    )

    servers = parse_server_entries(uri)

    assert len(servers) == 1
    server = servers[0]
    assert server.protocol == "amneziawg"
    assert server.host == "vpn.example.com"
    assert server.port == 51820
    assert server.amneziawg_profile is not None
    assert server.amneziawg_profile.interface.addresses == ["10.66.66.2/32"]
    assert server.amneziawg_profile.interface.dns == ["1.1.1.1", "1.0.0.1"]
