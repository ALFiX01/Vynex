from __future__ import annotations

from unittest.mock import Mock

from vynex_vpn_client.backends import (
    AmneziaWgBackend,
    BackendConnectionProfile,
    BackendRuntimeRequest,
    SingboxBackend,
    XrayBackend,
    select_backend,
)
from vynex_vpn_client.config_builder import XrayConfigBuilder
from vynex_vpn_client.models import LocalProxyCredentials, ProxyRuntimeSession, ServerEntry
from vynex_vpn_client.parsers import parse_share_link
from vynex_vpn_client.routing_profiles import RoutingProfile
from vynex_vpn_client.singbox_config_builder import SingboxConfigBuilder
from vynex_vpn_client.tcp_ping import TCP_PING_UNSUPPORTED_ERROR, TcpPingService


def _routing_profile() -> RoutingProfile:
    return RoutingProfile(
        profile_id="default",
        name="default",
        description="default",
        rules=[
            {
                "type": "field",
                "domain": ["geosite:youtube", "full:example.com"],
                "outboundTag": "proxy",
            },
            {
                "type": "field",
                "ip": ["geoip:private", "1.1.1.0/24"],
                "process": ["chrome.exe"],
                "outboundTag": "direct",
            },
        ],
    )


def _hy2_server() -> ServerEntry:
    return ServerEntry.new(
        name="hy2",
        protocol="hy2",
        host="vpn.example.com",
        port=443,
        raw_link="hy2://secret@vpn.example.com:443",
        extra={
            "password": "secret",
            "sni": "edge.example.com",
            "insecure": "1",
            "obfs": "salamander",
            "obfs_password": "mask",
            "server_ports": ["443", "8443:9443"],
            "pin_sha256": "deadbeef",
        },
    )


def test_parse_share_link_supports_hysteria2_multi_port_and_userpass() -> None:
    server = parse_share_link(
        "hysteria2://user:pass@example.com:1234,5000-6000/?insecure=1&obfs=salamander"
        "&obfs-password=mask&sni=real.example.com&pinSHA256=deadbeef#Office"
    )

    assert server.protocol == "hy2"
    assert server.name == "Office"
    assert server.port == 1234
    assert server.extra["password"] == "user:pass"
    assert server.extra["server_ports"] == ["1234", "5000:6000"]
    assert server.extra["insecure"] == "1"
    assert server.extra["obfs"] == "salamander"
    assert server.extra["obfs_password"] == "mask"
    assert server.extra["sni"] == "real.example.com"
    assert server.extra["pin_sha256"] == "deadbeef"


def test_parse_share_link_supports_hysteria2_default_port() -> None:
    server = parse_share_link("hy2://secret@example.com/?sni=edge.example.com#Default")

    assert server.protocol == "hy2"
    assert server.port == 443
    assert server.extra["password"] == "secret"


def test_singbox_config_builder_builds_proxy_config_for_hysteria2() -> None:
    builder = SingboxConfigBuilder()
    config = builder.build(
        server=_hy2_server(),
        mode="PROXY",
        routing_profile=_routing_profile(),
        socks_port=1080,
        http_port=8080,
        socks_credentials=LocalProxyCredentials(username="user", password="pass"),
    )

    outbound = config["outbounds"][0]
    assert outbound["type"] == "hysteria2"
    assert outbound["server_ports"] == ["443", "8443:9443"]
    assert "server_port" not in outbound
    assert outbound["tls"]["enabled"] is True
    assert outbound["tls"]["server_name"] == "edge.example.com"
    assert outbound["tls"]["insecure"] is True
    assert outbound["tls"]["certificate_public_key_sha256"] == ["deadbeef"]
    assert outbound["obfs"] == {"type": "salamander", "password": "mask"}
    assert {inbound["tag"] for inbound in config["inbounds"]} == {"socks-in", "http-in"}
    assert config["route"]["final"] == "proxy"


def test_singbox_config_builder_builds_tun_config_and_translates_routing_rules() -> None:
    builder = SingboxConfigBuilder()
    config = builder.build(
        server=_hy2_server(),
        mode="TUN",
        routing_profile=_routing_profile(),
    )

    assert config["inbounds"][0]["type"] == "tun"
    assert config["inbounds"][0]["strict_route"] is True
    assert config["route"]["rules"][0]["action"] == "sniff"
    assert config["route"]["rules"][1]["action"] == "hijack-dns"
    translated_proxy_rule = config["route"]["rules"][2]
    translated_direct_rule = config["route"]["rules"][3]
    assert translated_proxy_rule["geosite"] == ["youtube"]
    assert translated_proxy_rule["domain"] == ["example.com"]
    assert translated_proxy_rule["outbound"] == "proxy"
    assert translated_direct_rule["ip_is_private"] is True
    assert translated_direct_rule["ip_cidr"] == ["1.1.1.0/24"]
    assert translated_direct_rule["process_name"] == ["chrome.exe"]
    assert translated_direct_rule["outbound"] == "direct"


def test_select_backend_prefers_singbox_for_hysteria2() -> None:
    singbox_backend = SingboxBackend(
        installer=Mock(),
        config_builder=SingboxConfigBuilder(),
        process_manager=Mock(),
    )
    xray_backend = XrayBackend(
        installer=None,
        config_builder=XrayConfigBuilder(),
        process_manager=Mock(),
    )
    awg_backend = AmneziaWgBackend(process_manager=Mock())
    profile = BackendConnectionProfile(
        server=_hy2_server(),
        mode="PROXY",
        routing_profile=_routing_profile(),
    )

    selected = select_backend(
        {
            "xray": xray_backend,
            "singbox": singbox_backend,
            "amneziawg": awg_backend,
        },
        profile,
    )

    assert selected is singbox_backend


def test_singbox_backend_builds_runtime_config_for_proxy_mode() -> None:
    backend = SingboxBackend(
        installer=Mock(),
        config_builder=SingboxConfigBuilder(),
        process_manager=Mock(),
    )
    request = BackendRuntimeRequest(
        profile=BackendConnectionProfile(
            server=_hy2_server(),
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

    assert config["outbounds"][0]["type"] == "hysteria2"
    assert config["route"]["final"] == "proxy"


def test_tcp_ping_marks_hysteria2_as_unsupported() -> None:
    result = TcpPingService().ping_server(_hy2_server())

    assert result.ok is False
    assert result.error == TCP_PING_UNSUPPORTED_ERROR
