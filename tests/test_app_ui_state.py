from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from questionary import Choice
from rich.console import Console

from vynex_vpn_client.app import MAX_SERVER_NAME_DISPLAY_WIDTH, VynexVpnApp
from vynex_vpn_client.backends import BaseVpnBackend
from vynex_vpn_client.models import AppSettings, RuntimeState, ServerEntry
from vynex_vpn_client.process_manager import State as XrayState
from vynex_vpn_client.tcp_ping import TCP_PING_UNSUPPORTED_ERROR, TcpPingResult


def _make_app(*, runtime_state: RuntimeState, manager_state: XrayState, manager_pid: int | None = None) -> VynexVpnApp:
    app = object.__new__(VynexVpnApp)
    app.console = Console(width=80, record=True)
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
    app.amneziawg_process_manager = Mock()
    app.amneziawg_process_manager.state = XrayState.STOPPED
    app.amneziawg_process_manager.pid = None
    app.amneziawg_process_manager.is_running = Mock(return_value=False)
    app.amneziawg_network_integration = Mock()
    app.singbox_process_manager = Mock()
    xray_backend = Mock(spec=BaseVpnBackend)
    xray_backend.backend_id = "xray"
    xray_backend.engine_name = "xray"
    xray_backend.engine_title = "Xray"
    xray_backend.tun_interface_name = None
    xray_backend.tun_route_prefixes = ()
    xray_backend.process_controller = app.process_manager
    xray_backend.supports_crash_recovery = True
    awg_backend = Mock(spec=BaseVpnBackend)
    awg_backend.backend_id = "amneziawg"
    awg_backend.engine_name = "amneziawg"
    awg_backend.engine_title = "AmneziaWG"
    awg_backend.tun_interface_name = None
    awg_backend.tun_route_prefixes = ()
    awg_backend.process_controller = app.amneziawg_process_manager
    awg_backend.supports_crash_recovery = False
    app.backends = {
        "xray": xray_backend,
        "amneziawg": awg_backend,
    }
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


def test_banner_status_line_uses_awg_config_routing_label() -> None:
    state = RuntimeState(
        pid=1001,
        backend_id="amneziawg",
        mode="TUN",
        server_id="server-1",
        routing_profile_name="Умный",
    )
    app = _make_app(runtime_state=state, manager_state=XrayState.STOPPED)
    app.amneziawg_process_manager.is_running.return_value = True

    line = app._banner_status_line()

    assert "из AWG-конфига" in line
    assert "Умный" not in line


def test_runtime_pid_label_shows_restart_marker_while_xray_recovers() -> None:
    state = RuntimeState(pid=1001, mode="PROXY")
    app = _make_app(runtime_state=state, manager_state=XrayState.CRASHED, manager_pid=None)

    assert app._runtime_pid_label(state) == "перезапуск"


def test_ui_server_name_is_safe_for_cp1251_console() -> None:
    with patch("vynex_vpn_client.app.sys.stdout", SimpleNamespace(encoding="cp1251")):
        value = VynexVpnApp._ui_server_name("vmess (🇷🇺 game) 🚀")

    assert value == "vmess ([RU] game) [U+1F680]"


def test_server_choice_title_uses_console_safe_name() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)

    with patch("vynex_vpn_client.app.sys.stdout", SimpleNamespace(encoding="cp1251")):
        title = app._server_choice_title("srv 🚀", "VMESS", "example.com:443", 20, 5)

    assert "[U+1F680]" in title


def test_server_manager_choice_title_truncates_long_names() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app.console = Console(width=50, record=True)
    server = ServerEntry.new(
        name="vmess ([RU] game)-tele1324690943_port12667-9.77TB",
        protocol="vmess",
        host="31.192.111.158",
        port=12667,
        raw_link="vmess://example",
        extra={"id": "22222222-2222-2222-2222-222222222222"},
    )

    title = app._server_manager_choice_title(server, active_server_id=None)

    assert title.endswith("...")
    assert app._display_width(title) <= 27


def test_servers_table_truncates_long_names_without_wrapping() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app.console = Console(width=100, record=True)
    server = ServerEntry.new(
        name="vmess ([RU] game)-tele1324690943_port12667-9.77TB",
        protocol="vmess",
        host="31.192.111.158",
        port=12667,
        raw_link="vmess://example",
        extra={"id": "33333333-3333-3333-3333-333333333333"},
    )

    table = app._servers_table([server], active_server_id=None)
    name_cell = table.columns[0]._cells[0]

    assert table.columns[0].no_wrap is True
    assert table.columns[0].overflow == "ellipsis"
    assert name_cell.endswith("...")
    assert app._display_width(name_cell) <= 18
    assert table.columns[5]._cells[0] == "-"


def test_server_name_column_width_caps_maximum_on_wide_console() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app.console = Console(width=200, record=True)
    server = ServerEntry.new(
        name="server-" * 12,
        protocol="vmess",
        host="31.192.111.158",
        port=12667,
        raw_link="vmess://example",
        extra={"id": "44444444-4444-4444-4444-444444444444"},
    )

    assert app._server_name_column_width([server]) == MAX_SERVER_NAME_DISPLAY_WIDTH


def test_server_name_truncation_matches_expected_example() -> None:
    value = "vmess ([RU] game)-tele1324690943_port12667-9.77TB"

    assert VynexVpnApp._truncate_display_width(value, MAX_SERVER_NAME_DISPLAY_WIDTH) == "vmess ([RU] game)-tele1324690943_por..."


def test_servers_table_caps_long_names_on_wide_console() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app.console = Console(width=200, record=True)
    server = ServerEntry.new(
        name="server-" * 12,
        protocol="vmess",
        host="31.192.111.158",
        port=12667,
        raw_link="vmess://example",
        extra={"id": "55555555-5555-5555-5555-555555555555"},
    )

    table = app._servers_table([server], active_server_id=None)
    name_cell = table.columns[0]._cells[0]

    assert name_cell.endswith("...")
    assert app._display_width(name_cell) <= MAX_SERVER_NAME_DISPLAY_WIDTH


def test_servers_table_shows_cached_tcp_ping_in_rightmost_column() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    server = ServerEntry.new(
        name="Pinged",
        protocol="vless",
        host="example.com",
        port=443,
        raw_link="",
        extra={"id": "pinged-id", "tcp_ping_ms": 24, "tcp_ping_ok": True},
    )

    table = app._servers_table([server], active_server_id=None)

    assert table.columns[-1].header == "TCP ping"
    assert table.columns[-1]._cells[0] == "24 ms"


def test_back_choice_value_resolves_choice_value_after_terminal_styling() -> None:
    back_choice = VynexVpnApp._style_terminal_choice(Choice(title="Назад", value="__back__"))

    assert VynexVpnApp._back_choice_value([Choice(title="Вперёд", value="go"), back_choice]) == "__back__"


def test_back_choice_value_returns_none_without_back_option() -> None:
    assert VynexVpnApp._back_choice_value(["Открыть", "Выход"]) is None


def test_show_servers_overview_refreshes_tcp_ping_on_open() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    server = ServerEntry.new(
        name="Open test",
        protocol="vless",
        host="example.com",
        port=443,
        raw_link="",
        extra={"id": "open-id"},
    )
    app.storage.load_servers.return_value = [server]
    app._render_screen = Mock()
    app._current_state = Mock(return_value=RuntimeState())
    app._refresh_servers_tcp_ping_cache = Mock(return_value=[server])
    app._select = Mock(return_value=SimpleNamespace(ask=Mock(return_value="__back__")))
    app.console.print = Mock()

    app._show_servers_overview()

    app._refresh_servers_tcp_ping_cache.assert_called_once()


def test_tcp_ping_results_table_sorts_rows_and_formats_ping_values() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    fast = ServerEntry.new(
        name="Fast",
        protocol="vless",
        host="fast.example.com",
        port=443,
        raw_link="",
        extra={"id": "fast-id"},
    )
    active = ServerEntry.new(
        name="Active",
        protocol="vmess",
        host="active.example.com",
        port=8443,
        raw_link="",
        extra={"id": "active-id"},
    )
    awg = ServerEntry.new(
        name="AWG",
        protocol="amneziawg",
        host="awg.example.com",
        port=51820,
        raw_link="",
        extra={"id": "awg-id"},
    )
    down = ServerEntry.new(
        name="Down",
        protocol="trojan",
        host="down.example.com",
        port=443,
        raw_link="",
        extra={"id": "down-id"},
    )

    table = app._tcp_ping_results_table(
        [active, awg, down, fast],
        [
            TcpPingResult(active.id, True, 45, None, "2026-04-16T00:00:00+00:00"),
            TcpPingResult(awg.id, False, None, TCP_PING_UNSUPPORTED_ERROR, "2026-04-16T00:00:00+00:00"),
            TcpPingResult(down.id, False, None, "timeout", "2026-04-16T00:00:01+00:00"),
            TcpPingResult(fast.id, True, 18, None, "2026-04-16T00:00:02+00:00"),
        ],
        active_server_id=active.id,
    )

    assert table.columns[0]._cells[0] == "Fast"
    assert table.columns[4]._cells[0] == "18 ms"
    assert "[активен]" in table.columns[0]._cells[1]
    assert table.columns[3]._cells[2] == "Не проверяется"
    assert table.columns[4]._cells[2] == "н/д"
    assert table.columns[3]._cells[3] == "Недоступен"


def test_server_details_panel_shows_cached_tcp_ping() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    server = ServerEntry.new(
        name="Cached",
        protocol="vless",
        host="cached.example.com",
        port=443,
        raw_link="",
        extra={
            "id": "cached-id",
            "tcp_ping_ms": 42,
            "tcp_ping_ok": True,
            "tcp_ping_error": None,
            "tcp_ping_checked_at": "2026-04-16T12:34:56+00:00",
        },
    )

    panel = app._server_details_panel(server)
    labels = panel.renderable.columns[0]._cells
    values = panel.renderable.columns[1]._cells

    assert "TCP ping" in labels
    assert "Проверено" in labels
    assert "42 ms" in values


def test_tcp_ping_summary_panel_shows_udp_only_note_for_amneziawg() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    fast = ServerEntry.new(
        name="Fast",
        protocol="vless",
        host="fast.example.com",
        port=443,
        raw_link="",
        extra={"id": "fast-id"},
    )
    awg = ServerEntry.new(
        name="AWG",
        protocol="amneziawg",
        host="awg.example.com",
        port=51820,
        raw_link="",
        extra={"id": "awg-id"},
    )

    panel = app._tcp_ping_summary_panel(
        [fast, awg],
        [
            TcpPingResult(fast.id, True, 18, None, "2026-04-16T00:00:00+00:00"),
            TcpPingResult(awg.id, False, None, TCP_PING_UNSUPPORTED_ERROR, "2026-04-16T00:00:01+00:00"),
        ],
    )
    labels = panel.renderable.columns[0]._cells
    values = panel.renderable.columns[1]._cells

    assert "Не проверяется" in labels
    assert "Примечание" in labels
    assert "1" in values
    assert "AMNEZIAWG использует UDP" in values[-1]
