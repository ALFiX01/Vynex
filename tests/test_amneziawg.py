from __future__ import annotations

import base64

import pytest

from vynex_vpn_client.amneziawg import parse_amneziawg_config_file, parse_amneziawg_config_text
from vynex_vpn_client.app import VynexVpnApp
from vynex_vpn_client.models import AmneziaWgProfile
from vynex_vpn_client.models import ServerEntry
from vynex_vpn_client.storage import JsonStorage
import vynex_vpn_client.storage as storage_module


def _wg_key(seed: bytes) -> str:
    return base64.b64encode(seed * 32).decode("ascii")


def _sample_awg_2_0_config() -> str:
    return f"""
[Interface]
PrivateKey = {_wg_key(b"a")}
Address = 10.66.66.2/32, fd00::2/128
DNS = 1.1.1.1, dns.example.com
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
CustomField = custom-value

[Peer]
PublicKey = {_wg_key(b"b")}
PresharedKey = {_wg_key(b"c")}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = vpn.example.com:51820
PersistentKeepalive = 25
PeerNote = peer-extra

[Metadata]
Owner = Daniil
""".strip()


def _sample_awg_1_5_config() -> str:
    return f"""
[Interface]
PrivateKey = {_wg_key(b"a")}
Address = 10.66.66.2/32
Jc = 4
Jmin = 8
Jmax = 80
S1 = 15
S2 = 32
H1 = 111
H2 = 222
H3 = 333
H4 = 444
I1 = <b 0xdeadbeef>
I2 = <r 32>

[Peer]
PublicKey = {_wg_key(b"b")}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
""".strip()


def test_parse_amneziawg_config_text_builds_first_class_profile() -> None:
    server = parse_amneziawg_config_text(_sample_awg_2_0_config())

    assert server.protocol == "amneziawg"
    assert server.host == "vpn.example.com"
    assert server.port == 51820
    assert server.is_amneziawg is True
    assert server.amneziawg_profile is not None
    assert server.amneziawg_profile.protocol_version == "2.0"
    assert server.amneziawg_profile.version_source == "inferred"
    assert "cookie_padding" in server.amneziawg_profile.feature_flags
    assert "transport_padding" in server.amneziawg_profile.feature_flags
    assert "unmapped_fields_present" in server.amneziawg_profile.compatibility_flags
    assert server.amneziawg_profile.interface.obfuscation.s4 == 24
    assert server.amneziawg_profile.interface.extra_fields["CustomField"] == "custom-value"
    assert server.amneziawg_profile.peers[0].extra_fields["PeerNote"] == "peer-extra"
    assert server.amneziawg_profile.extra_sections[0]["name"] == "Metadata"
    assert server.amneziawg_profile.warnings


def test_parse_amneziawg_config_text_detects_awg_1_5() -> None:
    server = parse_amneziawg_config_text(_sample_awg_1_5_config())

    assert server.amneziawg_profile is not None
    assert server.amneziawg_profile.protocol_version == "1.5"
    assert server.amneziawg_profile.feature_flags == ["signature_packets"]
    assert "scalar_headers_only" in server.amneziawg_profile.compatibility_flags


def test_parse_amneziawg_config_text_detects_awg_2_0_by_header_ranges() -> None:
    ranged_config = _sample_awg_1_5_config().replace("H1 = 111", "H1 = 100-200")

    server = parse_amneziawg_config_text(ranged_config)

    assert server.amneziawg_profile is not None
    assert server.amneziawg_profile.protocol_version == "2.0"
    assert "header_ranges" in server.amneziawg_profile.feature_flags


def test_parse_amneziawg_config_text_accepts_padding_above_documented_limits() -> None:
    oversized_padding_config = (
        _sample_awg_2_0_config()
        .replace("S1 = 15", "S1 = 96")
        .replace("S2 = 32", "S2 = 104")
        .replace("S3 = 48", "S3 = 56")
        .replace("S4 = 24", "S4 = 48")
    )

    server = parse_amneziawg_config_text(oversized_padding_config)

    assert server.amneziawg_profile is not None
    assert server.amneziawg_profile.interface.obfuscation.s1 == 96
    assert server.amneziawg_profile.interface.obfuscation.s2 == 104
    assert server.amneziawg_profile.interface.obfuscation.s3 == 56
    assert server.amneziawg_profile.interface.obfuscation.s4 == 48
    assert any("S1, S2, S4" in warning for warning in server.amneziawg_profile.warnings)


def test_parse_amneziawg_config_file_supports_conf_path(tmp_path) -> None:
    config_path = tmp_path / "office-awg.conf"
    config_path.write_text(_sample_awg_2_0_config(), encoding="utf-8")

    server = parse_amneziawg_config_file(config_path)

    assert server.name == "office-awg"
    assert server.protocol == "amneziawg"
    assert server.amneziawg_profile is not None


def test_amneziawg_validation_returns_clear_error_for_bad_allowed_ips() -> None:
    invalid_config = _sample_awg_2_0_config().replace("AllowedIPs = 0.0.0.0/0, ::/0", "AllowedIPs = not-a-network")

    with pytest.raises(ValueError, match="AllowedIPs"):
        parse_amneziawg_config_text(invalid_config)


def test_explicit_awg_1_5_version_rejects_2_0_only_fields() -> None:
    with pytest.raises(ValueError, match="2.0"):
        AmneziaWgProfile.from_dict(
            {
                "name": "office-awg",
                "protocol_version": "1.5",
                "interface": {
                    "private_key": _wg_key(b"a"),
                    "addresses": ["10.66.66.2/32"],
                    "obfuscation": {
                        "s1": 15,
                        "s2": 32,
                        "s3": 48,
                        "h1": "111",
                        "h2": "222",
                        "h3": "333",
                        "h4": "444",
                        "i1": "<b 0xdeadbeef>",
                    },
                },
                "peers": [
                    {
                        "public_key": _wg_key(b"b"),
                        "allowed_ips": ["0.0.0.0/0"],
                        "endpoint_host": "vpn.example.com",
                        "endpoint_port": 51820,
                    }
                ],
            }
        )


def test_server_entry_roundtrip_preserves_amneziawg_profile() -> None:
    original = parse_amneziawg_config_text(_sample_awg_2_0_config())

    restored = ServerEntry.from_dict(original.to_dict())

    assert restored.is_amneziawg is True
    assert restored.amneziawg_profile is not None
    assert restored.amneziawg_profile.protocol_version == "2.0"
    assert "cookie_padding" in restored.amneziawg_profile.feature_flags
    assert restored.amneziawg_profile.interface.addresses == ["10.66.66.2/32", "fd00::2/128"]
    assert restored.amneziawg_profile.peers[0].endpoint_port == 51820


def test_json_storage_roundtrip_preserves_amneziawg_profile(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage_module, "LEGACY_DATA_DIR", tmp_path / "legacy")
    monkeypatch.setattr(storage_module, "ROUTING_PROFILES_DIR", data_dir / "routing_profiles")
    monkeypatch.setattr(storage_module, "SERVERS_FILE", data_dir / "servers.json")
    monkeypatch.setattr(storage_module, "SUBSCRIPTIONS_FILE", data_dir / "subscriptions.json")
    monkeypatch.setattr(storage_module, "RUNTIME_STATE_FILE", data_dir / "runtime_state.json")
    monkeypatch.setattr(storage_module, "SETTINGS_FILE", data_dir / "settings.json")

    storage = JsonStorage()
    server = parse_amneziawg_config_text(_sample_awg_2_0_config())

    storage.save_servers([server])
    loaded = storage.load_servers()

    assert len(loaded) == 1
    assert loaded[0].is_amneziawg is True
    assert loaded[0].amneziawg_profile is not None
    assert loaded[0].amneziawg_profile.peers[0].allowed_ips == ["0.0.0.0/0", "::/0"]


def test_json_storage_upsert_reuses_amneziawg_identity(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(storage_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage_module, "LEGACY_DATA_DIR", tmp_path / "legacy")
    monkeypatch.setattr(storage_module, "ROUTING_PROFILES_DIR", data_dir / "routing_profiles")
    monkeypatch.setattr(storage_module, "SERVERS_FILE", data_dir / "servers.json")
    monkeypatch.setattr(storage_module, "SUBSCRIPTIONS_FILE", data_dir / "subscriptions.json")
    monkeypatch.setattr(storage_module, "RUNTIME_STATE_FILE", data_dir / "runtime_state.json")
    monkeypatch.setattr(storage_module, "SETTINGS_FILE", data_dir / "settings.json")

    storage = JsonStorage()
    first = storage.upsert_server(parse_amneziawg_config_text(_sample_awg_2_0_config()))
    second = storage.upsert_server(parse_amneziawg_config_text(_sample_awg_2_0_config()))

    assert first.id == second.id
    assert len(storage.load_servers()) == 1


def test_app_detect_import_target_supports_amneziawg_conf_text() -> None:
    app = object.__new__(VynexVpnApp)

    import_kind, payload = app._detect_import_target(_sample_awg_2_0_config())

    assert import_kind == "server_bundle"
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0].is_amneziawg is True
