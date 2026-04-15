from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from vynex_vpn_client.amneziawg import parse_amneziawg_config_text
from vynex_vpn_client.amneziawg_runtime import (
    AmneziaWgRuntimeBuilder,
    cleanup_runtime,
    mask_sensitive_config_text,
)
from vynex_vpn_client.backends import AmneziaWgBackend, BackendConnectionProfile, BackendRuntimeRequest
from vynex_vpn_client.routing_profiles import RoutingProfile


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


def _sample_awg_1_5_config(*, legacy_fields: bool = False) -> str:
    legacy_suffix = ""
    if legacy_fields:
        legacy_suffix = "\nJ1 = 11\nJ2 = 22\nJ3 = 33\nITime = 5"
    return f"""
[Interface]
PrivateKey = {_wg_key(b"a")}
Address = 10.66.66.2/32
DNS = 1.1.1.1
S1 = 15
S2 = 32
H1 = 111
H2 = 222
H3 = 333
H4 = 444
I1 = <b 0xdeadbeef>{legacy_suffix}

[Peer]
PublicKey = {_wg_key(b"b")}
PresharedKey = {_wg_key(b"c")}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
PersistentKeepalive = 25
""".strip()


def _routing_profile() -> RoutingProfile:
    return RoutingProfile(
        profile_id="default",
        name="default",
        description="default",
        rules=[],
    )


def test_runtime_builder_creates_conf_file_in_runtime_directory(tmp_path) -> None:
    server = parse_amneziawg_config_text(_sample_awg_2_0_config())
    assert server.amneziawg_profile is not None
    builder = AmneziaWgRuntimeBuilder(tmp_path / "runtime-root")

    artifacts = builder.build_runtime(server.amneziawg_profile)

    config_text = artifacts.config_path.read_text(encoding="utf-8")
    assert artifacts.runtime_dir.parent == tmp_path / "runtime-root"
    assert artifacts.config_path.suffix == ".conf"
    assert artifacts.config_format == "wg-quick-conf"
    assert artifacts.launch_input_kind == "conf_path"
    assert "PrivateKey = " + server.amneziawg_profile.interface.private_key in config_text
    assert "PresharedKey = " + server.amneziawg_profile.peers[0].preshared_key in config_text
    assert "CustomField" not in config_text
    assert "[Metadata]" not in config_text


def test_runtime_builder_omits_2_0_only_fields_for_awg_1_5(tmp_path) -> None:
    server = parse_amneziawg_config_text(_sample_awg_1_5_config())
    assert server.amneziawg_profile is not None
    builder = AmneziaWgRuntimeBuilder(tmp_path / "runtime-root")

    artifacts = builder.build_runtime(server.amneziawg_profile)

    config_text = artifacts.config_path.read_text(encoding="utf-8")
    assert server.amneziawg_profile.protocol_version == "1.5"
    assert "S3 =" not in config_text
    assert "S4 =" not in config_text
    assert "I1 = <b 0xdeadbeef>" in config_text


def test_runtime_builder_preserves_padding_above_documented_limits(tmp_path) -> None:
    oversized_padding_config = (
        _sample_awg_2_0_config()
        .replace("S1 = 15", "S1 = 96")
        .replace("S2 = 32", "S2 = 104")
        .replace("S3 = 48", "S3 = 56")
        .replace("S4 = 24", "S4 = 48")
    )
    server = parse_amneziawg_config_text(oversized_padding_config)
    assert server.amneziawg_profile is not None
    builder = AmneziaWgRuntimeBuilder(tmp_path / "runtime-root")

    artifacts = builder.build_runtime(server.amneziawg_profile)

    config_text = artifacts.config_path.read_text(encoding="utf-8")
    assert "S1 = 96" in config_text
    assert "S2 = 104" in config_text
    assert "S3 = 56" in config_text
    assert "S4 = 48" in config_text


def test_runtime_builder_rejects_legacy_extension_fields_without_backend_support(tmp_path) -> None:
    server = parse_amneziawg_config_text(_sample_awg_1_5_config(legacy_fields=True))
    assert server.amneziawg_profile is not None
    builder = AmneziaWgRuntimeBuilder(tmp_path / "runtime-root")

    with pytest.raises(NotImplementedError, match="J1, J2, J3, ITIME"):
        builder.build_runtime(server.amneziawg_profile)


def test_runtime_artifacts_debug_output_masks_sensitive_values(tmp_path) -> None:
    server = parse_amneziawg_config_text(_sample_awg_2_0_config())
    assert server.amneziawg_profile is not None
    builder = AmneziaWgRuntimeBuilder(tmp_path / "runtime-root")

    artifacts = builder.build_runtime(server.amneziawg_profile)

    debug_payload = json.dumps(artifacts.to_debug_dict(), ensure_ascii=False)
    assert server.amneziawg_profile.interface.private_key not in debug_payload
    assert server.amneziawg_profile.peers[0].preshared_key not in debug_payload
    assert "PrivateKey = YWFh" in debug_payload
    assert "PresharedKey = Y2Nj" in debug_payload


def test_runtime_cleanup_removes_generated_directory(tmp_path) -> None:
    server = parse_amneziawg_config_text(_sample_awg_2_0_config())
    assert server.amneziawg_profile is not None
    builder = AmneziaWgRuntimeBuilder(tmp_path / "runtime-root")

    artifacts = builder.build_runtime(server.amneziawg_profile)
    assert artifacts.runtime_dir.exists()

    builder.cleanup_runtime(artifacts)

    assert not artifacts.runtime_dir.exists()
    cleanup_runtime(None)


def test_backend_exposes_runtime_builder_and_runtime_config(tmp_path) -> None:
    server = parse_amneziawg_config_text(_sample_awg_2_0_config())
    backend = AmneziaWgBackend(runtime_builder=AmneziaWgRuntimeBuilder(tmp_path / "runtime-root"))
    profile = BackendConnectionProfile(
        server=server,
        mode="TUN",
        routing_profile=_routing_profile(),
    )

    config = backend.build_runtime_config(BackendRuntimeRequest(profile=profile))

    assert backend.process_controller is not None
    assert config["protocol_version"] == "2.0"
    assert "cookie_padding" in config["feature_flags"]
    assert Path(config["config_path"]).exists()
    backend.cleanup_runtime(
        type(
            "Artifacts",
            (),
            {
                "runtime_dir": Path(config["runtime_dir"]),
            },
        )()
    )


def test_mask_sensitive_config_text_masks_secret_lines() -> None:
    masked = mask_sensitive_config_text(
        "[Interface]\n"
        "PrivateKey = supersecret\n"
        "\n"
        "[Peer]\n"
        "PresharedKey = anothersecret\n"
    )

    assert "supersecret" not in masked
    assert "anothersecret" not in masked
    assert "PrivateKey = supe" in masked
    assert "PresharedKey = anot" in masked
