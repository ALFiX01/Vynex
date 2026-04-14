from __future__ import annotations

from unittest.mock import Mock

from vynex_vpn_client.app import VynexVpnApp
from vynex_vpn_client.models import AppSettings, RuntimeState, ServerEntry
from vynex_vpn_client.process_manager import State as XrayState


def _make_app(*, runtime_state: RuntimeState, manager_state: XrayState, manager_pid: int | None = None) -> VynexVpnApp:
    app = object.__new__(VynexVpnApp)
    app.storage = Mock()
    app.storage.load_runtime_state.return_value = runtime_state
    app.storage.save_runtime_state = Mock()
    app.storage.get_server.return_value = ServerEntry.new(
        name="Test server",
        protocol="vless",
        host="example.com",
        port=443,
        raw_link="vless://test@example.com:443",
        extra={"id": "11111111-1111-1111-1111-111111111111"},
    )
    app.process_manager = Mock()
    app.process_manager.state = manager_state
    app.process_manager.pid = manager_pid
    app.process_manager.is_running = Mock(return_value=False)
    app.singbox_process_manager = Mock()
    app._proxy_session = None
    app._runtime_notice = None
    app._disconnect_runtime = Mock()
    app._validated_settings = Mock(return_value=AppSettings())
    app._available_app_update = Mock(return_value=None)
    app._active_routing_profile_name = Mock(return_value="Default")
    return app


def test_current_state_preserves_runtime_during_xray_recovery() -> None:
    state = RuntimeState(
        pid=1001,
        mode="PROXY",
        server_id="server-1",
        routing_profile_name="Default",
    )
    app = _make_app(runtime_state=state, manager_state=XrayState.CRASHED)

    resolved_state = app._current_state()

    assert resolved_state is state
    app._disconnect_runtime.assert_not_called()
    app.storage.save_runtime_state.assert_not_called()


def test_banner_status_line_shows_xray_recovery() -> None:
    state = RuntimeState(
        pid=1001,
        mode="PROXY",
        server_id="server-1",
        routing_profile_name="Default",
    )
    app = _make_app(runtime_state=state, manager_state=XrayState.CRASHED)

    line = app._banner_status_line()

    assert "Xray восстанавливается" in line
    assert "Test server" in line


def test_runtime_pid_label_shows_restart_marker_while_xray_recovers() -> None:
    state = RuntimeState(pid=1001, mode="PROXY")
    app = _make_app(runtime_state=state, manager_state=XrayState.CRASHED, manager_pid=None)

    assert app._runtime_pid_label(state) == "перезапуск"
