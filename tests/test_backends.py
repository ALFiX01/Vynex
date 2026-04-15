from __future__ import annotations

import base64
from unittest.mock import Mock

from vynex_vpn_client.amneziawg import parse_amneziawg_config_text
from vynex_vpn_client.amneziawg_process_manager import AmneziaWgProcessManager
from vynex_vpn_client.backends import (
    AmneziaWgBackend,
    BackendConnectionProfile,
    BackendRuntimeRequest,
    XrayBackend,
    select_backend,
)
from vynex_vpn_client.config_builder import XrayConfigBuilder
from vynex_vpn_client.models import LocalProxyCredentials, ProxyRuntimeSession, ServerEntry
from vynex_vpn_client.routing_profiles import RoutingProfile


def _routing_profile() -> RoutingProfile:
    return RoutingProfile(
        profile_id="default",
        name="default",
        description="default",
        rules=[],
    )


def _server(*, protocol: str) -> ServerEntry:
    extra: dict[str, object]
    if protocol == "vless":
        extra = {"id": "11111111-1111-1111-1111-111111111111"}
    elif protocol in {"awg", "amneziawg"}:
        extra = {}
    else:
        extra = {"password": "secret"}
    return ServerEntry.new(
        name=protocol,
        protocol=protocol,
        host="example.com",
        port=443,
        raw_link=f"{protocol}://example",
        extra=extra,
    )


def _wg_key(seed: bytes) -> str:
    return base64.b64encode(seed * 32).decode("ascii")


def _awg_server() -> ServerEntry:
    return parse_amneziawg_config_text(
        (
            "[Interface]\n"
            f"PrivateKey = {_wg_key(b'a')}\n"
            "Address = 10.66.66.2/32\n"
            "\n"
            "[Peer]\n"
            f"PublicKey = {_wg_key(b'b')}\n"
            "AllowedIPs = 0.0.0.0/0\n"
            "Endpoint = vpn.example.com:51820\n"
        )
    )


def test_select_backend_uses_awg_backend_for_awg_profile() -> None:
    xray_backend = XrayBackend(
        installer=None,
        config_builder=XrayConfigBuilder(),
        process_manager=Mock(),
    )
    awg_backend = AmneziaWgBackend()
    profile = BackendConnectionProfile(
        server=_awg_server(),
        mode="TUN",
        routing_profile=_routing_profile(),
    )

    selected = select_backend(
        {
            "xray": xray_backend,
            "amneziawg": awg_backend,
        },
        profile,
    )

    assert selected is awg_backend


def test_xray_backend_builds_proxy_runtime_through_existing_builder() -> None:
    backend = XrayBackend(
        installer=None,
        config_builder=XrayConfigBuilder(),
        process_manager=Mock(),
    )
    request = BackendRuntimeRequest(
        profile=BackendConnectionProfile(
            server=_server(protocol="vless"),
            mode="PROXY",
            routing_profile=_routing_profile(),
        ),
        proxy_session=ProxyRuntimeSession(
            socks_port=1080,
            http_port=8080,
            socks_credentials=LocalProxyCredentials(username="user", password="pass"),
        ),
    )

    config = backend.build_runtime_config(request)

    assert config["outbounds"][0]["protocol"] == "vless"
    assert {inbound["protocol"] for inbound in config["inbounds"]} == {"socks", "http"}


def test_amneziawg_backend_builds_runtime_config_and_exposes_process_controller() -> None:
    process_manager = Mock(spec=AmneziaWgProcessManager)
    backend = AmneziaWgBackend(process_manager=process_manager)
    request = BackendRuntimeRequest(
        profile=BackendConnectionProfile(
            server=_awg_server(),
            mode="TUN",
            routing_profile=_routing_profile(),
        )
    )

    config = backend.build_runtime_config(request)

    assert backend.process_controller is process_manager
    assert config["backend_id"] == "amneziawg"
    assert config["protocol_version"] == "legacy"
    assert config["feature_flags"] == []
    assert config["tunnel_name"]
    assert config["config_path"].endswith(".conf")


def test_amneziawg_backend_rejects_proxy_mode() -> None:
    backend = AmneziaWgBackend(process_manager=Mock(spec=AmneziaWgProcessManager))
    request = BackendRuntimeRequest(
        profile=BackendConnectionProfile(
            server=_awg_server(),
            mode="PROXY",
            routing_profile=_routing_profile(),
        )
    )

    try:
        backend.build_runtime_config(request)
    except NotImplementedError as exc:
        assert "только режим TUN" in str(exc)
    else:
        raise AssertionError("Expected NotImplementedError for unsupported AWG proxy mode")


def test_amneziawg_backend_ensures_runtime_via_installer() -> None:
    installer = Mock()
    backend = AmneziaWgBackend(
        installer=installer,
        process_manager=Mock(spec=AmneziaWgProcessManager),
    )
    profile = BackendConnectionProfile(
        server=_awg_server(),
        mode="TUN",
        routing_profile=_routing_profile(),
    )

    backend.ensure_runtime_ready(profile)

    installer.ensure_amneziawg_runtime.assert_called_once_with()
