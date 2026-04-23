from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from questionary import Choice
from questionary.prompts.select import Keys
from rich.console import Console

from vynex_vpn_client.app import (
    MAX_SERVER_NAME_DISPLAY_WIDTH,
    RuntimeNotice,
    SelectActionResult,
    TerminalInquirerControl,
    VynexVpnApp,
)
from vynex_vpn_client.backends import BaseVpnBackend, BackendConnectionProfile
from vynex_vpn_client.constants import DEFAULT_CONSOLE_COLUMNS, DEFAULT_CONSOLE_LINES
from vynex_vpn_client.models import AppSettings, RuntimeState, ServerEntry, SubscriptionEntry
from vynex_vpn_client.parsers import parse_share_link
from vynex_vpn_client.process_manager import State as XrayState
from vynex_vpn_client.storage import StorageCorruptionError
from vynex_vpn_client.system_proxy import SystemProxyState
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
    app._startup_subscription_refresh_thread = None
    app._console_window_size = None
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


def test_current_state_recovers_from_corrupt_runtime_state() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app.storage.load_runtime_state.side_effect = StorageCorruptionError(Path("runtime_state.json"))
    xray_path = Path("C:/Program Files/Vynex/xray.exe")
    app.process_manager._executable_path = xray_path
    app.process_manager.list_running_instances.return_value = [
        SimpleNamespace(pid=4321, executable_path=str(xray_path).lower())
    ]
    app.system_proxy_manager = Mock()
    app.system_proxy_manager.snapshot.return_value = SystemProxyState(
        proxy_enable=1,
        proxy_server="http=127.0.0.1:18080;https=127.0.0.1:18080",
    )

    resolved_state = app._current_state()

    assert resolved_state == RuntimeState()
    app.process_manager.stop.assert_called_once_with(4321)
    app.system_proxy_manager.disable_proxy.assert_called_once()
    app.storage.save_runtime_state.assert_called_once_with(RuntimeState())
    assert app._runtime_notice is not None
    assert "поврежден" in app._runtime_notice.message.lower()


def test_render_screen_uses_runtime_notice_title() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app.logo = ""
    app._runtime_notice = RuntimeNotice(
        message="Health-check не подтвердил доступ в сеть, но подключение оставлено активным.",
        title="Подключение установлено с предупреждением",
        border_style="yellow",
    )

    with patch("vynex_vpn_client.app.os.system"):
        app._render_screen()

    output = app.console.export_text()
    assert "Подключение установлено с предупреждением" in output
    assert "Health-check не подтвердил доступ в сеть" in output
    assert app._runtime_notice is None


def test_render_screen_hides_banner_by_default() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app.logo = "VYNEX"

    with patch("vynex_vpn_client.app.os.system"):
        app._render_screen()

    output = app.console.export_text()
    assert "VYNEX" not in output


def test_render_screen_shows_banner_when_requested() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app.logo = "VYNEX"

    with patch("vynex_vpn_client.app.os.system"):
        app._render_screen(show_banner=True)

    output = app.console.export_text()
    assert "VYNEX" in output


def test_list_console_window_size_grows_for_large_lists() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)

    columns, lines = app._list_console_window_size(26, baseline_items=10)

    assert columns == DEFAULT_CONSOLE_COLUMNS
    assert lines > DEFAULT_CONSOLE_LINES


def test_server_manager_console_window_size_is_wider_than_default() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)

    columns, lines = app._server_manager_console_window_size(30)

    assert columns > DEFAULT_CONSOLE_COLUMNS
    assert lines > DEFAULT_CONSOLE_LINES
    assert lines <= DEFAULT_CONSOLE_LINES + 8


def test_server_manager_console_window_size_does_not_grow_for_medium_lists() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)

    columns, lines = app._server_manager_console_window_size(18)

    assert columns > DEFAULT_CONSOLE_COLUMNS
    assert lines == DEFAULT_CONSOLE_LINES


def test_apply_console_window_size_skips_duplicate_mode_command() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)

    with (
        patch("vynex_vpn_client.app.os.system") as system_mock,
        patch("vynex_vpn_client.app.sys.platform", "win32"),
        patch("vynex_vpn_client.app.sys.stdout", SimpleNamespace(isatty=lambda: True)),
    ):
        app._apply_console_window_size(150, 55)
        app._apply_console_window_size(150, 55)

    system_mock.assert_called_once_with("mode con cols=150 lines=55 > nul")


def test_ensure_xray_ready_shows_notice_when_runtime_components_missing() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app.installer = Mock()
    app.installer.warnings = []
    app._show_runtime_auto_install_notice = Mock()

    with patch.object(VynexVpnApp, "_missing_startup_runtime_components", return_value=["geoip.dat"]):
        app._ensure_xray_ready()

    app._show_runtime_auto_install_notice.assert_called_once_with(
        components=["geoip.dat"],
        title="Подготовка приложения",
    )
    app.installer.ensure_xray.assert_called_once_with()


def test_missing_connection_runtime_components_for_awg_lists_all_missing_files(tmp_path: Path) -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app._backend_for_connection = Mock(return_value=SimpleNamespace(backend_id="amneziawg"))
    profile = BackendConnectionProfile(
        server=ServerEntry.new(
            name="AWG",
            protocol="amneziawg",
            host="example.com",
            port=51820,
            raw_link="amneziawg://example",
            extra={"id": "awg-test"},
        ),
        mode="TUN",
        routing_profile=SimpleNamespace(name="AWG"),
    )

    with (
        patch("vynex_vpn_client.app.AMNEZIAWG_EXECUTABLE", tmp_path / "amneziawg.exe"),
        patch("vynex_vpn_client.app.AMNEZIAWG_EXECUTABLE_FALLBACK", tmp_path / "awg.exe"),
        patch("vynex_vpn_client.app.AMNEZIAWG_WINTUN_DLL", tmp_path / "wintun.dll"),
    ):
        missing = app._missing_connection_runtime_components(profile)

    assert missing == [
        "AmneziaWG (amneziawg.exe)",
        "AWG helper (awg.exe)",
        "wintun.dll",
    ]


def test_ensure_runtime_ready_shows_connection_notice_when_components_missing() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    backend = Mock()
    backend.backend_id = "xray"
    app._backend_for_connection = Mock(return_value=backend)
    app._missing_connection_runtime_components = Mock(return_value=["Xray-core (xray.exe)"])
    app._show_runtime_auto_install_notice = Mock()
    server = ServerEntry.new(
        name="Test server",
        protocol="vless",
        host="example.com",
        port=443,
        raw_link="vless://test@example.com:443",
        extra={"id": "ensure-runtime-server"},
    )
    routing_profile = SimpleNamespace(name="Default")

    app._ensure_runtime_ready("PROXY", server=server, routing_profile=routing_profile)

    app._show_runtime_auto_install_notice.assert_called_once_with(
        components=["Xray-core (xray.exe)"],
        title="Идет подключение",
        server_name="Test server",
        routing_name="Default",
    )
    backend.ensure_runtime_ready.assert_called_once()


def test_xray_component_label_includes_detected_version(tmp_path: Path) -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    xray_path = tmp_path / "xray.exe"
    xray_path.write_bytes(b"")

    with (
        patch("vynex_vpn_client.app.XRAY_EXECUTABLE", xray_path),
        patch.object(VynexVpnApp, "_xray_version_text", return_value="26.3.27"),
    ):
        assert app._xray_component_label() == "Xray-core: есть (v26.3.27)"


def test_status_flow_shows_xray_version_when_not_connected() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app.logo = ""
    app._render_screen = Mock()
    app._pause = Mock()
    app._xray_version_status_label = Mock(return_value="v26.3.27")

    app.status_flow()

    output = app.console.export_text()
    assert "Xray-core" in output
    assert "v26.3.27" in output


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
    assert "Режим:" in line
    assert "PROXY" in line


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

    assert "AWG-конфиг" in line
    assert "Умный" not in line
    assert "TUN" in line


def test_banner_status_line_shows_selected_mode_when_not_connected() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app._validated_settings.return_value = AppSettings(connection_mode="TUN")

    line = app._banner_status_line()

    assert "Не подключено" in line
    assert "Режим:" in line
    assert "TUN" in line


def test_startup_auto_refresh_subscriptions_uses_global_setting() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app._validated_settings.return_value = AppSettings(auto_update_subscriptions_on_startup=True)
    app.storage.load_subscriptions.return_value = [Mock()]
    app.subscription_manager = Mock()
    app.subscription_manager.refresh_all.return_value = ([], [])
    thread = Mock()

    with patch("vynex_vpn_client.app.threading.Thread", return_value=thread) as thread_factory:
        app._startup_auto_refresh_subscriptions()

    thread_factory.assert_called_once()
    thread.start.assert_called_once_with()
    app.subscription_manager.refresh_all.assert_not_called()
    assert app._runtime_notice is None


def test_startup_auto_refresh_subscriptions_sets_notice_on_failures() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    failed_subscription = Mock()
    failed_subscription.title = "Problem sub"
    app.subscription_manager = Mock()
    app.subscription_manager.refresh_all.return_value = ([], [(failed_subscription, "timeout")])

    app._refresh_subscriptions_on_startup_in_background()

    assert app._runtime_notice is not None
    assert "С ошибками: 1" in app._runtime_notice.message
    assert "Problem sub: timeout" in app._runtime_notice.message
    assert app._runtime_notice.title == "Авто-обновление подписок"


def test_startup_auto_refresh_subscriptions_skips_when_background_thread_is_alive() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app._startup_subscription_refresh_thread = Mock(is_alive=Mock(return_value=True))
    app._validated_settings.return_value = AppSettings(auto_update_subscriptions_on_startup=True)
    app.storage.load_subscriptions.return_value = [Mock()]

    with patch("vynex_vpn_client.app.threading.Thread") as thread_factory:
        app._startup_auto_refresh_subscriptions()

    thread_factory.assert_not_called()


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


def test_server_choice_title_includes_tcp_ping_when_provided() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)

    title = app._server_choice_title(
        "srv",
        "VMESS",
        "example.com:443",
        20,
        5,
        tcp_ping_label="24 ms",
        ping_width=5,
    )

    assert title.endswith("| 24 ms")


def test_server_choice_title_aligns_address_column_when_width_is_provided() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)

    short_title = app._server_choice_title(
        "srv",
        "VMESS",
        "a:1",
        20,
        5,
        address_width=20,
        tcp_ping_label="24 ms",
        ping_width=5,
    )
    long_title = app._server_choice_title(
        "srv",
        "VMESS",
        "very-long-host.example:443",
        20,
        5,
        address_width=20,
        tcp_ping_label="24 ms",
        ping_width=5,
    )

    assert short_title.index("| 24 ms") == long_title.index("| 24 ms")


def test_best_cached_tcp_ping_server_id_returns_lowest_latency_server() -> None:
    slow = ServerEntry.new(
        name="Slow",
        protocol="vless",
        host="slow.example.com",
        port=443,
        raw_link="",
        extra={"id": "slow-id", "tcp_ping_ok": True, "tcp_ping_ms": 78},
    )
    fast = ServerEntry.new(
        name="Fast",
        protocol="vless",
        host="fast.example.com",
        port=443,
        raw_link="",
        extra={"id": "fast-id", "tcp_ping_ok": True, "tcp_ping_ms": 24},
    )
    failed = ServerEntry.new(
        name="Failed",
        protocol="vless",
        host="failed.example.com",
        port=443,
        raw_link="",
        extra={"id": "failed-id", "tcp_ping_ok": False, "tcp_ping_error": "timeout"},
    )

    best_server_id = VynexVpnApp._best_cached_tcp_ping_server_id([slow, failed, fast])

    assert best_server_id == fast.id


def test_connect_server_choice_highlights_lowest_ping_server() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    server = ServerEntry.new(
        name="Fast",
        protocol="vless",
        host="fast.example.com",
        port=443,
        raw_link="",
        extra={"id": "fast-id", "tcp_ping_ok": True, "tcp_ping_ms": 24},
    )

    choice = app._connect_server_choice(
        server,
        name_width=20,
        protocol_width=5,
        address_width=20,
        ping_width=5,
        is_best=True,
    )

    assert choice.title.startswith("Fast")
    assert getattr(choice, "_vynex_style_class", None) == "best-ping"


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

    assert "VMESS" in title
    assert "31.192.111.158:12667" not in title
    assert "ручной" not in title
    assert "..." in title
    assert app._display_width(title) <= 44


def test_server_manager_choice_title_includes_server_metadata() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app.console = Console(width=138, record=True)
    subscription = SubscriptionEntry.new(url="https://subs.eu-fffast.com", title="subs.eu-fffast.com")
    app.storage.get_subscription.return_value = subscription
    server = ServerEntry.new(
        name="[FI] 🎮 Игровой 1",
        protocol="vless",
        host="test.wide-frost.test-cdn-kkk.com",
        port=8443,
        raw_link="vless://example",
        source="subscription",
        subscription_id=subscription.id,
        extra={"id": "manager-choice-server", "tcp_ping_ms": 25, "tcp_ping_ok": True},
    )

    title = app._server_manager_choice_title(server, active_server_id=server.id)

    assert "[FI]" in title
    assert "VLESS" in title
    assert "test.wide-frost.test-cdn-kkk.com:8443" in title
    assert "подписка (subs.eu-fffast.com)" in title
    assert "Активен" in title
    assert title.endswith("25 ms")


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


def test_menu_select_style_includes_best_ping_style() -> None:
    style = VynexVpnApp._menu_select_style()

    assert ("best-ping", "fg:ansigreen bold") in style.style_rules


def test_menu_select_style_includes_settings_value_style() -> None:
    style = VynexVpnApp._menu_select_style()

    assert ("settings-value", "fg:ansiblue bold") in style.style_rules


def test_shortcut_action_key_variants_include_russian_layout_equivalent() -> None:
    variants = VynexVpnApp._shortcut_action_key_variants(("r",))

    assert variants == ("r", "к")


def test_shortcut_action_key_variants_leave_special_keys_unchanged() -> None:
    variants = VynexVpnApp._shortcut_action_key_variants((Keys.Delete,))

    assert variants == (Keys.Delete,)


def test_shortcut_action_binding_variants_register_layout_alternatives_separately() -> None:
    variants = VynexVpnApp._shortcut_action_binding_variants(("r",))

    assert variants == (("r",), ("к",))


def test_shortcut_action_binding_variants_keep_special_keys_as_single_binding() -> None:
    variants = VynexVpnApp._shortcut_action_binding_variants((Keys.Delete,))

    assert variants == ((Keys.Delete,),)


def test_terminal_inquirer_control_keeps_shortcuts_for_styled_string_choices() -> None:
    choice = VynexVpnApp._styled_choice(Choice(title="Fast server", value="fast"), style_class="best-ping")
    control = TerminalInquirerControl(
        choices=[choice, Choice(title="Fallback", value="fallback")],
        use_shortcuts=True,
        use_indicator=False,
        pointer=None,
    )

    rendered = "".join(text for _, text in control._get_choice_tokens())

    assert "1) Fast server" in rendered


def test_terminal_inquirer_control_keeps_shortcuts_for_formatted_choice_titles() -> None:
    control = TerminalInquirerControl(
        choices=[
            VynexVpnApp._settings_menu_choice("Режим подключения: ", "TUN"),
            Choice(title="Назад", value="__back__"),
        ],
        use_shortcuts=True,
        use_indicator=False,
        pointer=None,
    )

    rendered = "".join(text for _, text in control._get_choice_tokens())

    assert "1) Режим подключения: TUN" in rendered


def test_settings_menu_choice_formats_value_with_separate_style() -> None:
    choice = VynexVpnApp._settings_menu_choice("Режим подключения: ", "TUN")

    assert choice.value == "Режим подключения: TUN"
    assert choice.title == [
        ("class:text", "Режим подключения: "),
        ("class:settings-value", "TUN"),
    ]


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
    app._render_screen.assert_called_with(window_size=app._server_manager_console_window_size(1))


def test_server_subscription_flow_exposes_quick_import_at_top_level() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app.storage.load_servers.return_value = []
    app.storage.load_subscriptions.return_value = []
    app._render_screen = Mock()
    app.add_server_flow = Mock()
    app._show_servers_overview = Mock()
    app._show_subscriptions_overview = Mock()
    app._select = Mock(
        side_effect=[
            SimpleNamespace(ask=Mock(return_value="__add__")),
            SimpleNamespace(ask=Mock(return_value="Назад")),
        ]
    )

    app.server_subscription_flow()

    choices = app._select.call_args_list[0].kwargs["choices"]
    assert any(isinstance(choice, Choice) and choice.value == "__add__" for choice in choices)
    app.add_server_flow.assert_called_once()


def test_show_servers_overview_menu_does_not_include_quick_import_or_ping_action_choice() -> None:
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

    choices = app._select.call_args.kwargs["choices"]
    assert not any(isinstance(choice, Choice) and choice.value == "__add__" for choice in choices)
    assert not any(isinstance(choice, Choice) and choice.value == "__tcp_ping_all__" for choice in choices)


def test_show_servers_overview_does_not_render_duplicate_servers_table() -> None:
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

    app.console.print.assert_not_called()


def test_show_subscriptions_overview_menu_does_not_include_quick_import_choice() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    subscription = SubscriptionEntry.new(url="https://example.com/sub", title="Example")
    app.storage.load_subscriptions.return_value = [subscription]
    app.storage.load_servers.return_value = []
    app._render_screen = Mock()
    app._select = Mock(return_value=SimpleNamespace(ask=Mock(return_value="__back__")))
    app.console.print = Mock()

    app._show_subscriptions_overview()

    choices = app._select.call_args.kwargs["choices"]
    assert not any(isinstance(choice, Choice) and choice.value == "__add__" for choice in choices)


def test_subscription_choice_title_includes_source_count_and_status() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    subscription = SubscriptionEntry.new(url="https://example.com/sub", title="Example")
    app.storage.load_servers.return_value = [
        ServerEntry.new(
            name="Imported",
            protocol="vless",
            host="example.com",
            port=443,
            raw_link="",
            source="subscription",
            subscription_id=subscription.id,
        )
    ]

    title = app._subscription_choice_title(subscription)

    assert "Example" in title
    assert "example.com" in title
    assert "1 сервер" in title
    assert "Готова" in title


def test_subscription_details_panel_explains_error_state_and_next_step() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    subscription = SubscriptionEntry.new(url="https://example.com/sub", title="Example")
    subscription.last_error = "timeout"
    subscription.last_error_at = "2026-04-21T18:00:00+00:00"
    app.storage.load_servers.return_value = []

    app.console.print(app._subscription_details_panel(subscription))
    output = app.console.export_text()

    assert "Источник" in output
    assert "example.com" in output
    assert "Что это значит" in output
    assert "завершилась" in output
    assert "ошибкой" in output
    assert "Следующий шаг" in output
    assert "Проверьте URL" in output


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
    assert "UDP-протоколы" in values[-1]


def test_persist_tcp_ping_results_can_preserve_other_cached_entries() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    first = ServerEntry.new(
        name="First",
        protocol="vless",
        host="first.example.com",
        port=443,
        raw_link="",
        extra={"id": "first-id", "tcp_ping_ms": 21, "tcp_ping_ok": True},
    )
    second = ServerEntry.new(
        name="Second",
        protocol="vmess",
        host="second.example.com",
        port=8443,
        raw_link="",
        extra={"id": "second-id", "tcp_ping_ms": 77, "tcp_ping_ok": True},
    )

    app._persist_tcp_ping_results(
        [first, second],
        [TcpPingResult(first.id, True, 19, None, "2026-04-19T10:00:00+00:00")],
        clear_missing=False,
    )

    assert first.extra["tcp_ping_ms"] == 19
    assert second.extra["tcp_ping_ms"] == 77
    app.storage.save_servers.assert_called_once_with([first, second])


def test_handle_server_manager_shortcut_refreshes_ping_for_all_servers() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    app._refresh_all_servers_manager_tcp_ping = Mock()
    app._refresh_server_tcp_ping_cache = Mock()

    app._handle_server_manager_shortcut(SelectActionResult(action="refresh", value="server-42"))

    app._refresh_all_servers_manager_tcp_ping.assert_called_once_with()
    app._refresh_server_tcp_ping_cache.assert_not_called()


def test_refresh_all_servers_manager_tcp_ping_updates_cached_values_in_place() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    server = ServerEntry.new(
        name="Refresh me",
        protocol="vless",
        host="example.com",
        port=443,
        raw_link="",
        extra={"id": "refresh-id"},
    )
    app.storage.load_servers.return_value = [server]
    app._refresh_servers_tcp_ping_cache = Mock(return_value=[server])

    refreshed = app._refresh_all_servers_manager_tcp_ping()

    assert refreshed == [server]
    app._refresh_servers_tcp_ping_cache.assert_called_once_with(
        [server],
        status_message="[bold cyan]Обновляем TCP ping для 1 серверов...[/bold cyan]",
    )


def test_handle_subscription_manager_shortcut_refreshes_selected_subscription() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)
    subscription = SubscriptionEntry.new(url="https://example.com/sub", title="Example")
    imported = [
        ServerEntry.new(
            name="Imported",
            protocol="vless",
            host="example.com",
            port=443,
            raw_link="",
            extra={"id": "imported-id"},
        )
    ]
    app.storage.get_subscription.return_value = subscription
    app._refresh_subscription = Mock(return_value=imported)
    app._show_subscription_refresh_success = Mock()

    app._handle_subscription_manager_shortcut(SelectActionResult(action="refresh", value=subscription.id))

    app._refresh_subscription.assert_called_once_with(subscription)
    app._show_subscription_refresh_success.assert_called_once_with("Подписка обновлена", subscription, imported)


def test_terminal_inquirer_control_filters_formatted_choice_titles() -> None:
    control = TerminalInquirerControl(
        choices=[
            Choice(title="Proxy server", value="proxy"),
            Choice(title=[("class:terminal-danger", "Назад")], value="__back__"),
        ],
        use_shortcuts=False,
    )

    control.search_filter = "наз"
    filtered = control.filtered_choices

    assert len(filtered) == 1
    assert filtered[0].value == "__back__"


def test_error_guidance_for_import_surfaces_missing_server_credentials() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)

    try:
        parse_share_link("vless://example.com:443#NoId")
    except ValueError as exc:
        summary, actions, details = app._error_guidance("Ошибка импорта", exc)
    else:
        raise AssertionError("parse_share_link should fail for VLESS URI without credentials")

    assert summary == "В ссылке сервера нет обязательного идентификатора или пароля."
    assert actions == [
        "Для VLESS и VMess проверьте UUID перед символом @.",
        "Для Trojan, Shadowsocks и Hysteria2 проверьте пароль или userinfo-часть ссылки.",
    ]
    assert details == "Некорректная ссылка сервера. Причина: В ссылке отсутствует идентификатор или пароль."


def test_error_guidance_for_tun_admin_error_uses_source_launch_instructions() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)

    with (
        patch("vynex_vpn_client.app.sys.platform", "win32"),
        patch("vynex_vpn_client.app.sys.frozen", False, create=True),
    ):
        summary, actions, details = app._error_guidance(
            "Ошибка подключения",
            RuntimeError("TUN режим требует запуска приложения от имени администратора."),
        )

    assert summary == "Для TUN режима клиент должен быть запущен с правами администратора."
    assert actions == [
        "Закройте приложение, откройте PowerShell от имени администратора и запустите `python main.py`.",
        "Если проект запускается из virtualenv, используйте `./.venv/Scripts/python.exe ./main.py`.",
        "После перезапуска повторите подключение в режиме TUN.",
    ]
    assert details == "TUN режим требует запуска приложения от имени администратора."


def test_show_error_for_import_surfaces_humanized_nested_reason() -> None:
    app = _make_app(runtime_state=RuntimeState(), manager_state=XrayState.STOPPED)

    try:
        parse_share_link("ss://example.com:8388#NoCreds")
    except ValueError as exc:
        app._show_error("Ошибка импорта", exc)
    else:
        raise AssertionError("parse_share_link should fail for malformed Shadowsocks URI")

    output = app.console.export_text()

    assert "Ссылка сервера повреждена" in output
    assert "или скопирована не" in output
    assert "Причина: Ссылка" in output
    assert "содержит поврежденные или неполные данные." in output
