from __future__ import annotations

from unittest.mock import Mock, patch

from vynex_vpn_client.app_service import (
    VynexAppService,
    VynexServiceDependencies,
    WinwsConflictError,
)
from vynex_vpn_client import app_service as app_service_module
from vynex_vpn_client.healthcheck import HealthcheckResult
from vynex_vpn_client.models import AppSettings, RuntimeState, ServerEntry, SubscriptionEntry
from vynex_vpn_client.process_manager import State as ProcessState
from vynex_vpn_client.routing_profiles import RoutingProfile
from vynex_vpn_client.tcp_ping import TcpPingResult
from vynex_vpn_client.utils import RunningProcessDetails


def _routing_profile() -> RoutingProfile:
    return RoutingProfile(
        profile_id="default",
        name="Default",
        description="Default routing",
        rules=[],
    )


def _server() -> ServerEntry:
    return ServerEntry.new(
        name="Test server",
        protocol="vless",
        host="example.com",
        port=443,
        raw_link="vless://11111111-1111-1111-1111-111111111111@example.com:443?security=tls#Test",
        extra={"id": "11111111-1111-1111-1111-111111111111", "security": "tls"},
    )


def _subscription() -> SubscriptionEntry:
    return SubscriptionEntry.new(url="https://example.com/sub", title="example.com")


def _service_with_mocks(server: ServerEntry | None = None) -> tuple[VynexAppService, Mock, Mock]:
    storage = Mock()
    storage.load_runtime_state.return_value = RuntimeState()
    storage.load_settings.return_value = AppSettings(connection_mode="PROXY", set_system_proxy=False)
    storage.get_server.return_value = server or _server()
    storage.get_subscription.return_value = None
    storage.load_subscriptions.return_value = []
    storage.save_runtime_state = Mock()
    storage.save_settings = Mock()

    routing_profiles = Mock()
    routing_profiles.get_profile.return_value = _routing_profile()
    routing_profiles.list_profiles.return_value = [_routing_profile()]

    process_manager = Mock()
    process_manager.state = ProcessState.RUNNING
    process_manager.pid = 1234
    process_manager.start.return_value = 1234
    process_manager.is_running.return_value = True
    process_manager.ensure_no_running_instances = Mock()

    deps = VynexServiceDependencies(
        storage=storage,
        installer=Mock(),
        singbox_installer=Mock(),
        app_update_checker=Mock(get_cached_release=Mock(return_value=None)),
        app_updater=Mock(),
        routing_profiles=routing_profiles,
        process_manager=process_manager,
        singbox_process_manager=Mock(),
        amneziawg_process_manager=Mock(),
        health_checker=Mock(),
        system_proxy_manager=Mock(),
    )
    deps.health_checker.verify_proxy.return_value = HealthcheckResult(ok=True, message="ok")
    service = VynexAppService(deps)
    return service, storage, process_manager


def test_service_import_links_imports_single_server_without_terminal_ui() -> None:
    service, storage, _ = _service_with_mocks()
    imported = _server()
    storage.upsert_server.return_value = imported

    result = service.import_links(imported.raw_link)

    assert result.kind == "server"
    assert result.servers == (imported,)
    storage.upsert_server.assert_called_once()


def test_service_rename_server_marks_subscription_custom_name() -> None:
    server = _server()
    server.source = "subscription"
    server.subscription_id = "sub-1"
    service, storage, _ = _service_with_mocks(server)
    storage.upsert_server.side_effect = lambda item: item

    updated = service.rename_server(server.id, "Renamed")

    assert updated.name == "Renamed"
    assert updated.extra["custom_name"] is True
    storage.upsert_server.assert_called_once_with(updated)


def test_service_toggle_server_favorite_can_remove_existing_favorite() -> None:
    server = _server()
    server.extra["favorite"] = True
    service, storage, _ = _service_with_mocks(server)
    storage.upsert_server.side_effect = lambda item: item

    updated = service.toggle_server_favorite(server.id)

    assert updated.extra["favorite"] is False
    storage.upsert_server.assert_called_once_with(updated)


def test_service_update_server_link_preserves_id_and_name() -> None:
    server = _server()
    service, storage, _ = _service_with_mocks(server)
    storage.upsert_server.side_effect = lambda item: item
    new_link = "vless://22222222-2222-2222-2222-222222222222@example.org:8443?security=tls#Updated"

    updated = service.update_server_link(server.id, new_link)

    assert updated.id == server.id
    assert updated.name == server.name
    assert updated.host == "example.org"
    assert updated.port == 8443
    storage.upsert_server.assert_called_once()


def test_service_detach_server_from_subscription_delegates_to_storage() -> None:
    server = _server()
    server.source = "subscription"
    server.subscription_id = "sub-1"
    subscription = Mock()
    service, storage, _ = _service_with_mocks(server)
    storage.detach_server_from_subscription.return_value = (server, subscription)

    result = service.detach_server_from_subscription(server.id)

    assert result.server is server
    assert result.subscription is subscription
    storage.detach_server_from_subscription.assert_called_once_with(server.id)


def test_service_add_subscription_url_uses_default_title_and_refreshes() -> None:
    service, storage, _ = _service_with_mocks()
    storage.get_subscription_by_url.return_value = None
    imported_server = _server()
    service.subscription_manager.import_subscription = Mock(return_value=[imported_server])
    storage.upsert_subscription.side_effect = lambda item: item

    result = service.add_subscription_url("https://example.org/sub")

    assert result.kind == "subscription"
    assert result.subscription is not None
    assert result.subscription.title == "example.org"
    assert result.servers == (imported_server,)
    storage.upsert_subscription.assert_called_once()


def test_service_rename_subscription_persists_title() -> None:
    subscription = _subscription()
    service, storage, _ = _service_with_mocks()
    storage.get_subscription.return_value = subscription
    storage.upsert_subscription.side_effect = lambda item: item

    updated = service.rename_subscription(subscription.id, "New title")

    assert updated.title == "New title"
    storage.upsert_subscription.assert_called_once_with(updated)


def test_service_update_subscription_url_rejects_duplicate() -> None:
    subscription = _subscription()
    duplicate = SubscriptionEntry.new(url="https://duplicate.example/sub", title="Duplicate")
    service, storage, _ = _service_with_mocks()
    storage.get_subscription.return_value = subscription
    storage.load_subscriptions.return_value = [subscription, duplicate]

    try:
        service.update_subscription_url(subscription.id, duplicate.url)
    except ValueError as exc:
        assert "уже существует" in str(exc)
    else:
        raise AssertionError("Expected duplicate subscription URL to fail")


def test_service_delete_subscription_requires_disconnect_for_active_owned_server() -> None:
    subscription = _subscription()
    server = _server()
    server.source = "subscription"
    server.subscription_id = subscription.id
    subscription.server_ids = [server.id]
    service, storage, _ = _service_with_mocks(server)
    storage.get_subscription.return_value = subscription
    storage.load_servers.return_value = [server]
    storage.load_runtime_state.return_value = RuntimeState(
        pid=1234,
        backend_id="xray",
        mode="PROXY",
        server_id=server.id,
    )

    try:
        service.delete_subscription(subscription.id, remove_servers=True)
    except RuntimeError as exc:
        assert "активен" in str(exc)
    else:
        raise AssertionError("Expected active subscription server delete to require confirmation")

    storage.delete_subscription.assert_not_called()


def test_service_update_settings_validates_and_saves_values() -> None:
    service, storage, _ = _service_with_mocks()
    profile = _routing_profile()
    service.routing_profiles.get_profile.return_value = profile

    updated = service.update_settings(
        connection_mode="tun",
        set_system_proxy=False,
        auto_update_subscriptions_on_startup=True,
        active_routing_profile_id=profile.profile_id,
    )

    assert updated.connection_mode == "TUN"
    assert updated.set_system_proxy is False
    assert updated.auto_update_subscriptions_on_startup is True
    assert updated.active_routing_profile_id == "default"
    storage.save_settings.assert_called_once_with(updated)


def test_service_update_settings_rejects_unknown_routing_profile() -> None:
    service, storage, _ = _service_with_mocks()
    service.routing_profiles.get_profile.return_value = None

    try:
        service.update_settings(active_routing_profile_id="missing")
    except ValueError as exc:
        assert "Профиль маршрутизации не найден" in str(exc)
    else:
        raise AssertionError("Expected missing routing profile to fail")

    storage.save_settings.assert_not_called()


def test_service_components_status_lists_wintun() -> None:
    service, _, _ = _service_with_mocks()

    status = service.get_components_status()

    keys = {item.key for item in status.items}
    assert "wintun" in keys
    assert {"xray", "singbox", "amneziawg", "geoip", "geosite"}.issubset(keys)


def test_service_update_component_returns_installer_warnings() -> None:
    service, _, _ = _service_with_mocks()
    service.installer.warnings = ["geoip fallback failed"]
    service.installer.update_geoip.return_value = Mock(name="path", name_attr="geoip.dat")
    service.installer.update_geoip.return_value.name = "geoip.dat"

    result = service.update_component("geoip")

    assert result.component == "geoip"
    assert result.details == ("geoip.dat",)
    assert result.warnings == ("geoip fallback failed",)


def test_service_startup_maintenance_prepares_runtime_and_auto_refreshes(tmp_path, monkeypatch) -> None:
    service, storage, _ = _service_with_mocks()
    xray_path = tmp_path / "xray.exe"
    geoip_path = tmp_path / "geoip.dat"
    geosite_path = tmp_path / "geosite.dat"
    monkeypatch.setattr(app_service_module, "XRAY_EXECUTABLE", xray_path)
    monkeypatch.setattr(app_service_module, "GEOIP_PATH", geoip_path)
    monkeypatch.setattr(app_service_module, "GEOSITE_PATH", geosite_path)

    subscription = _subscription()
    storage.load_settings.return_value = AppSettings(auto_update_subscriptions_on_startup=True)
    storage.load_subscriptions.return_value = [subscription]
    service.installer.ensure_xray.return_value = xray_path
    service.installer.warnings = ["geoip fallback failed"]
    service.subscription_manager.refresh_all = Mock(return_value=([(subscription, 3)], []))
    release = Mock(is_update_available=False)
    service.app_update_checker.check_latest_release.return_value = release

    progress: list[str] = []
    result = service.run_startup_maintenance(progress_callback=progress.append)

    assert result.app_update is release
    assert result.runtime_update is not None
    assert result.runtime_update.component == "startup_runtime"
    assert result.runtime_update.warnings == ("geoip fallback failed",)
    assert result.subscription_refresh is not None
    assert len(result.subscription_refresh.success) == 1
    service.installer.ensure_xray.assert_called_once()
    service.subscription_manager.refresh_all.assert_called_once_with(only_auto_update=True)
    assert any("Подготовка runtime" in item for item in progress)
    assert any("Авто-обновление подписок" in item for item in progress)


def test_service_prepare_self_update_rejects_unavailable_packaged_mode() -> None:
    service, _, _ = _service_with_mocks()
    release = Mock()
    release.is_update_available = True
    service.app_updater.can_self_update.return_value = False

    try:
        service.prepare_self_update(release)
    except RuntimeError as exc:
        assert "Self-update" in str(exc)
    else:
        raise AssertionError("Expected self-update to require packaged build")


def test_service_delete_active_server_requires_disconnect_confirmation() -> None:
    server = _server()
    service, storage, _ = _service_with_mocks(server)
    storage.load_runtime_state.return_value = RuntimeState(
        pid=1234,
        backend_id="xray",
        mode="PROXY",
        server_id=server.id,
    )

    try:
        service.delete_server(server.id)
    except RuntimeError as exc:
        assert "активен" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError for deleting active server")

    storage.delete_server.assert_not_called()


def test_service_connect_reports_winws_conflict_without_prompting() -> None:
    service, _, process_manager = _service_with_mocks()
    conflicts = [RunningProcessDetails(pid=101, name="Winws.exe")]

    with patch("vynex_vpn_client.app_service.list_running_processes_by_names", return_value=conflicts):
        try:
            service.connect("server-1")
        except WinwsConflictError as exc:
            assert exc.conflicts == tuple(conflicts)
            assert "Winws.exe" in str(exc)
        else:
            raise AssertionError("Expected WinwsConflictError")

    process_manager.start.assert_not_called()


def test_service_connect_proxy_starts_backend_and_saves_runtime_state() -> None:
    server = _server()
    service, storage, process_manager = _service_with_mocks(server)
    progress_steps: list[str] = []

    with (
        patch("vynex_vpn_client.app_service.list_running_processes_by_names", return_value=[]),
        patch("vynex_vpn_client.app_service.pick_random_port", side_effect=[18080, 18081]),
        patch("vynex_vpn_client.app_service.generate_random_username", return_value="user"),
        patch("vynex_vpn_client.app_service.generate_random_password", return_value="pass"),
        patch("vynex_vpn_client.app_service.wait_for_port_listener", return_value=True),
    ):
        result = service.connect(server.id, progress_callback=progress_steps.append)

    assert result.state.pid == 1234
    assert result.state.server_id == server.id
    assert result.state.mode == "PROXY"
    assert result.backend_id == "xray"
    assert result.health_warning is None
    assert process_manager.start.called
    storage.save_runtime_state.assert_called()
    assert "Запуск ядра подключения" in progress_steps
    service.health_checker.verify_proxy.assert_called_once_with(http_port=18080)


def test_service_run_tcp_ping_persists_results() -> None:
    server = _server()
    service, storage, _ = _service_with_mocks(server)
    storage.upsert_servers.return_value = [server]
    ping_result = TcpPingResult(
        server_id=server.id,
        ok=True,
        latency_ms=42,
        error=None,
        checked_at="2026-04-24T00:00:00+00:00",
    )
    service.tcp_ping_service = Mock()
    service.tcp_ping_service.ping_many.return_value = [ping_result]

    result = service.run_tcp_ping([server])

    assert result.results == (ping_result,)
    storage.upsert_servers.assert_called_once()
    saved_server = storage.upsert_servers.call_args.args[0][0]
    assert saved_server.extra["tcp_ping"]["latency_ms"] == 42


def test_service_best_tcp_ping_server_uses_cached_latency() -> None:
    slow = _server()
    slow.name = "Slow"
    slow.extra["tcp_ping"] = {"ok": True, "latency_ms": 90}
    fast = _server()
    fast.name = "Fast"
    fast.extra["tcp_ping"] = {"ok": True, "latency_ms": 25}
    service, storage, _ = _service_with_mocks()
    storage.load_servers.return_value = [slow, fast]

    assert service.best_tcp_ping_server() is fast


def test_service_runtime_status_includes_process_proxy_backend_and_server() -> None:
    server = _server()
    service, storage, process_manager = _service_with_mocks(server)
    state = RuntimeState(
        pid=4321,
        backend_id="xray",
        mode="PROXY",
        server_id=server.id,
        routing_profile_name="Default",
        system_proxy_enabled=True,
    )
    storage.load_runtime_state.return_value = state
    process_manager.state = ProcessState.RUNNING
    process_manager.pid = 4321
    process_manager.is_running.return_value = True
    proxy_state = Mock()
    proxy_state.proxy_enable = 0
    service.system_proxy_manager.snapshot.return_value = proxy_state

    status = service.get_runtime_status()

    assert status.runtime_state is state
    assert status.process_state == "running"
    assert status.system_proxy_state == "выключен"
    assert status.backend_id == "xray"
    assert status.backend_title == "Xray"
    assert status.pid == 4321
    assert status.active_server is server
    assert status.routing_profile == "Default"
