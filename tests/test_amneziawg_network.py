from __future__ import annotations

import base64
from unittest.mock import call, patch

from vynex_vpn_client.amneziawg import parse_amneziawg_config_text
from vynex_vpn_client.amneziawg_network import (
    AmneziaWgAdminRequiredError,
    AmneziaWgDnsApplyError,
    AmneziaWgInterfaceConflictError,
    AmneziaWgRouteApplyError,
    AmneziaWgWindowsNetworkIntegration,
)
from vynex_vpn_client.models import RuntimeState
from vynex_vpn_client.utils import WindowsInterfaceDetails


def _wg_key(seed: bytes) -> str:
    return base64.b64encode(seed * 32).decode("ascii")


def _profile(*, dns: str = "1.1.1.1", allowed_ips: str = "0.0.0.0/0, 10.0.0.0/8"):
    server = parse_amneziawg_config_text(
        (
            "[Interface]\n"
            f"PrivateKey = {_wg_key(b'a')}\n"
            "Address = 10.66.66.2/32\n"
            f"DNS = {dns}\n"
            "\n"
            "[Peer]\n"
            f"PublicKey = {_wg_key(b'b')}\n"
            f"AllowedIPs = {allowed_ips}\n"
            "Endpoint = vpn.example.com:51820\n"
        )
    )
    assert server.amneziawg_profile is not None
    return server.amneziawg_profile


def test_ensure_prerequisites_requires_admin() -> None:
    integration = AmneziaWgWindowsNetworkIntegration()

    with patch("vynex_vpn_client.amneziawg_network.is_running_as_admin", return_value=False):
        try:
            integration.ensure_prerequisites(tunnel_name="office-awg")
        except AmneziaWgAdminRequiredError as exc:
            assert "администратора" in str(exc)
        else:
            raise AssertionError("Expected AmneziaWgAdminRequiredError without admin rights")


def test_ensure_prerequisites_rejects_existing_interface() -> None:
    integration = AmneziaWgWindowsNetworkIntegration()

    with (
        patch("vynex_vpn_client.amneziawg_network.is_running_as_admin", return_value=True),
        patch(
            "vynex_vpn_client.amneziawg_network.get_interface_details",
            return_value=WindowsInterfaceDetails(alias="office-awg", index=7, status="Up"),
        ),
    ):
        try:
            integration.ensure_prerequisites(tunnel_name="office-awg")
        except AmneziaWgInterfaceConflictError as exc:
            assert "конфликтующий интерфейс" in str(exc).lower()
        else:
            raise AssertionError("Expected AmneziaWgInterfaceConflictError for existing interface")


def test_capture_session_detects_missing_routes() -> None:
    integration = AmneziaWgWindowsNetworkIntegration()
    profile = _profile(allowed_ips="10.0.0.0/8, 192.168.0.0/16")

    with (
        patch(
            "vynex_vpn_client.amneziawg_network.get_interface_details",
            return_value=WindowsInterfaceDetails(alias="office-awg", index=11, ipv4="10.66.66.2", status="Up"),
        ),
        patch(
            "vynex_vpn_client.amneziawg_network.get_interface_ipv4_addresses",
            return_value=("10.66.66.2",),
        ),
        patch(
            "vynex_vpn_client.amneziawg_network.get_interface_ipv4_route_prefixes",
            return_value=("10.0.0.0/8",),
        ),
        patch(
            "vynex_vpn_client.amneziawg_network.get_interface_dns_servers",
            return_value=("1.1.1.1",),
        ),
    ):
        try:
            integration.capture_session(profile=profile, tunnel_name="office-awg")
        except AmneziaWgRouteApplyError as exc:
            assert "192.168.0.0/16" in str(exc)
            assert "split-tunnel" in str(exc)
        else:
            raise AssertionError("Expected AmneziaWgRouteApplyError when some AllowedIPs routes are missing")


def test_capture_session_accepts_resolved_dns_for_hostname_entries() -> None:
    integration = AmneziaWgWindowsNetworkIntegration()
    profile = _profile(dns="dns.example.com")

    with (
        patch(
            "vynex_vpn_client.amneziawg_network.get_interface_details",
            return_value=WindowsInterfaceDetails(alias="office-awg", index=11, ipv4="10.66.66.2", status="Up"),
        ),
        patch(
            "vynex_vpn_client.amneziawg_network.get_interface_ipv4_addresses",
            return_value=("10.66.66.2",),
        ),
        patch(
            "vynex_vpn_client.amneziawg_network.get_interface_ipv4_route_prefixes",
            return_value=("0.0.0.0/0", "10.0.0.0/8"),
        ),
        patch(
            "vynex_vpn_client.amneziawg_network.get_interface_dns_servers",
            return_value=("1.1.1.1",),
        ),
    ):
        session = integration.capture_session(profile=profile, tunnel_name="office-awg")

    assert session.route_prefixes == ("0.0.0.0/0", "10.0.0.0/8")


def test_capture_session_requires_dns_when_profile_declares_it() -> None:
    integration = AmneziaWgWindowsNetworkIntegration()
    profile = _profile(dns="1.1.1.1")

    with (
        patch(
            "vynex_vpn_client.amneziawg_network.get_interface_details",
            return_value=WindowsInterfaceDetails(alias="office-awg", index=11, ipv4="10.66.66.2", status="Up"),
        ),
        patch(
            "vynex_vpn_client.amneziawg_network.get_interface_ipv4_addresses",
            return_value=("10.66.66.2",),
        ),
        patch(
            "vynex_vpn_client.amneziawg_network.get_interface_ipv4_route_prefixes",
            return_value=("0.0.0.0/0", "10.0.0.0/8"),
        ),
        patch(
            "vynex_vpn_client.amneziawg_network.get_interface_dns_servers",
            return_value=(),
        ),
    ):
        try:
            integration.capture_session(profile=profile, tunnel_name="office-awg")
        except AmneziaWgDnsApplyError as exc:
            assert "dns" in str(exc).lower()
        else:
            raise AssertionError("Expected AmneziaWgDnsApplyError when DNS is missing on the interface")


def test_cleanup_runtime_state_resets_dns_routes_and_addresses() -> None:
    integration = AmneziaWgWindowsNetworkIntegration()
    state = RuntimeState(
        backend_id="amneziawg",
        mode="TUN",
        tun_interface_name="office-awg",
        tun_interface_index=11,
        tun_interface_addresses=["10.66.66.2/32"],
        tun_dns_servers=["1.1.1.1"],
        tun_route_prefixes=["0.0.0.0/0", "10.0.0.0/8"],
    )

    with (
        patch("vynex_vpn_client.amneziawg_network.remove_ipv4_route") as remove_route,
        patch("vynex_vpn_client.amneziawg_network.reset_interface_dns_servers") as reset_dns,
        patch("vynex_vpn_client.amneziawg_network.remove_interface_ipv4_addresses") as remove_addresses,
    ):
        integration.cleanup_runtime_state(state)

    assert remove_route.mock_calls == [
        call("0.0.0.0/0", interface_index=11),
        call("10.0.0.0/8", interface_index=11),
    ]
    reset_dns.assert_called_once_with("office-awg")
    remove_addresses.assert_called_once_with("office-awg", ["10.66.66.2/32"])
