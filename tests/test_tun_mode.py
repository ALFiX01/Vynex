from __future__ import annotations

from unittest.mock import Mock, call, patch

from vynex_vpn_client.app import VynexVpnApp
from vynex_vpn_client.config_builder import XrayConfigBuilder
from vynex_vpn_client.healthcheck import HealthcheckResult
from vynex_vpn_client.models import RuntimeState
from vynex_vpn_client.utils import WindowsInterfaceDetails


def _make_app() -> VynexVpnApp:
    app = object.__new__(VynexVpnApp)
    app.config_builder = XrayConfigBuilder()
    app.process_manager = Mock()
    app.amneziawg_network_integration = Mock()
    return app


def test_prepare_tun_prerequisites_requires_admin() -> None:
    app = _make_app()

    with patch("vynex_vpn_client.app.is_running_as_admin", return_value=False):
        try:
            app._prepare_tun_prerequisites()
        except RuntimeError as exc:
            assert "администратора" in str(exc)
        else:
            raise AssertionError("Expected RuntimeError when TUN runs without admin rights")


def test_prepare_tun_prerequisites_requires_active_ipv4_interface() -> None:
    app = _make_app()

    with (
        patch("vynex_vpn_client.app.is_running_as_admin", return_value=True),
        patch("vynex_vpn_client.app.get_active_ipv4_interface", return_value=None),
    ):
        try:
            app._prepare_tun_prerequisites()
        except RuntimeError as exc:
            assert "активный IPv4 интерфейс" in str(exc)
        else:
            raise AssertionError("Expected RuntimeError when no outbound interface is available")


def test_prepare_tun_prerequisites_skips_outbound_lookup_for_amneziawg() -> None:
    app = _make_app()
    backend = Mock()
    backend.backend_id = "amneziawg"

    with (
        patch("vynex_vpn_client.app.is_running_as_admin", return_value=True),
        patch("vynex_vpn_client.app.get_active_ipv4_interface") as get_active_interface,
    ):
        assert app._prepare_tun_prerequisites(backend=backend) is None

    get_active_interface.assert_not_called()


def test_prepare_tun_prerequisites_skips_outbound_lookup_for_singbox() -> None:
    app = _make_app()
    backend = Mock()
    backend.backend_id = "singbox"

    with (
        patch("vynex_vpn_client.app.is_running_as_admin", return_value=True),
        patch("vynex_vpn_client.app.get_active_ipv4_interface") as get_active_interface,
    ):
        assert app._prepare_tun_prerequisites(backend=backend) is None

    get_active_interface.assert_not_called()


def test_wait_for_tun_ready_returns_interface_details() -> None:
    app = _make_app()
    details = WindowsInterfaceDetails(
        alias=app.config_builder.TUN_INTERFACE_NAME,
        index=11,
        ipv4="169.254.10.5",
        status="Up",
        has_route=False,
    )

    with (
        patch("vynex_vpn_client.app.wait_for_tun_interface_details", return_value=details),
    ):
        assert app._wait_for_tun_ready(pid=1234) == details


def test_wait_for_tun_ready_reports_recent_output_when_xray_exits() -> None:
    app = _make_app()
    app.process_manager.is_running.return_value = False
    app.process_manager.read_recent_output.return_value = "failed to initialize wintun"

    with patch("vynex_vpn_client.app.wait_for_tun_interface_details", return_value=None):
        try:
            app._wait_for_tun_ready(pid=1234)
        except RuntimeError as exc:
            assert "failed to initialize wintun" in str(exc)
        else:
            raise AssertionError("Expected RuntimeError when xray exits before TUN is ready")


def test_cleanup_tun_routes_removes_all_prefixes() -> None:
    app = _make_app()
    state = RuntimeState(
        mode="TUN",
        tun_interface_index=17,
        tun_interface_ipv4="169.254.20.7",
        tun_route_prefixes=["0.0.0.0/1", "128.0.0.0/1"],
    )

    with patch("vynex_vpn_client.app.remove_ipv4_route") as remove_route:
        app._cleanup_tun_routes(state)

    assert remove_route.mock_calls == [
        call("0.0.0.0/1", interface_index=17, next_hop="169.254.20.7"),
        call("128.0.0.0/1", interface_index=17, next_hop="169.254.20.7"),
    ]


def test_cleanup_tun_state_delegates_awg_cleanup_to_network_layer() -> None:
    app = _make_app()
    state = RuntimeState(
        backend_id="amneziawg",
        mode="TUN",
        tun_interface_name="office-awg",
    )

    app._cleanup_tun_state(state)

    app.amneziawg_network_integration.cleanup_runtime_state.assert_called_once_with(state)


def test_failed_healthcheck_in_tun_mode_becomes_warning() -> None:
    manager = Mock()

    warning = VynexVpnApp._handle_failed_healthcheck(
        mode="TUN",
        pid=1234,
        manager=manager,
        health_result=HealthcheckResult(ok=False, message="timeout"),
    )

    assert warning is not None
    assert "подключение оставлено активным" in warning.lower()
    manager.stop.assert_not_called()


def test_failed_healthcheck_in_proxy_mode_still_stops_runtime() -> None:
    manager = Mock()

    try:
        VynexVpnApp._handle_failed_healthcheck(
            mode="PROXY",
            pid=4321,
            manager=manager,
            health_result=HealthcheckResult(ok=False, message="HTTP 502", inconclusive=False),
        )
    except RuntimeError as exc:
        assert "health-check не прошел" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError for failed proxy health-check")

    manager.stop.assert_called_once_with(4321)


def test_failed_healthcheck_in_proxy_mode_timeout_becomes_warning() -> None:
    manager = Mock()

    warning = VynexVpnApp._handle_failed_healthcheck(
        mode="PROXY",
        pid=4321,
        manager=manager,
        health_result=HealthcheckResult(ok=False, message="timeout", inconclusive=True),
    )

    assert warning is not None
    assert "подключение оставлено активным" in warning.lower()
    manager.stop.assert_not_called()
