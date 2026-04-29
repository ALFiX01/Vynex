from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import os
import threading
from pathlib import Path
from urllib.parse import urlparse

from .app_update import AppReleaseInfo, AppUpdateChecker
from .app_updater import AppSelfUpdater
from .app_updater import AppUpdateApplyPlan
from .amneziawg_network import AmneziaWgWindowsNetworkIntegration
from .amneziawg_process_manager import AmneziaWgProcessManager
from .backends import (
    AmneziaWgBackend,
    BackendConnectionProfile,
    BackendRuntimeRequest,
    BaseVpnBackend,
    SingboxBackend,
    XrayBackend,
    select_backend,
)
from .config_builder import XrayConfigBuilder
from .constants import (
    AMNEZIAWG_EXECUTABLE,
    AMNEZIAWG_EXECUTABLE_FALLBACK,
    AMNEZIAWG_WINTUN_DLL,
    APP_VERSION,
    APP_RELEASES_PAGE,
    GEOIP_PATH,
    GEOSITE_PATH,
    SINGBOX_EXECUTABLE,
    SUBSCRIPTION_TITLE_BY_HOST,
    WINTUN_DLL,
    XRAY_EXECUTABLE,
)
from .core import SingboxInstaller, XrayInstaller
from .healthcheck import HealthcheckResult, XrayHealthChecker
from .models import (
    AppSettings,
    LocalProxyCredentials,
    ProxyRuntimeSession,
    RuntimeState,
    ServerEntry,
    SubscriptionEntry,
    utc_now_iso,
)
from .parsers import is_supported_share_link, parse_server_entries, parse_share_link
from .process_manager import SingboxProcessManager, State as ProcessState, XrayProcessManager
from .routing_profiles import RoutingProfile, RoutingProfileManager
from .singbox_config_builder import SingboxConfigBuilder
from .storage import JsonStorage, StorageCorruptionError
from .subscriptions import SubscriptionManager
from .system_proxy import SystemProxyState, WindowsSystemProxyManager
from .tcp_ping import TcpPingResult, TcpPingService, sort_tcp_ping_results
from .tcp_ping import TCP_PING_UNSUPPORTED_ERROR
from .utils import (
    RunningProcessDetails,
    WindowsInterfaceDetails,
    add_ipv4_route,
    generate_random_password,
    generate_random_username,
    get_active_ipv4_interface,
    is_running_as_admin,
    list_running_processes_by_names,
    pick_random_port,
    remove_ipv4_route,
    terminate_running_processes,
    wait_for_port_listener,
    wait_for_tun_interface_details,
)

WINWS_CONFLICT_PROCESS_NAMES = ("Winws.exe", "Winws2.exe")
ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class ConnectionResult:
    state: RuntimeState
    server: ServerEntry
    backend_id: str
    health_warning: str | None = None


@dataclass(frozen=True)
class ImportResult:
    kind: str
    servers: tuple[ServerEntry, ...] = ()
    subscription: SubscriptionEntry | None = None


@dataclass(frozen=True)
class SubscriptionRefreshResult:
    success: tuple[tuple[SubscriptionEntry, int], ...]
    failed: tuple[tuple[SubscriptionEntry, str], ...]


@dataclass(frozen=True)
class ComponentStatus:
    key: str
    title: str
    installed: bool
    detail: str = ""


@dataclass(frozen=True)
class ComponentsStatus:
    items: tuple[ComponentStatus, ...]


@dataclass(frozen=True)
class ComponentUpdateResult:
    component: str
    details: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AppSelfUpdatePrepareResult:
    release: AppReleaseInfo
    plan: AppUpdateApplyPlan


@dataclass(frozen=True)
class AppRuntimeStatus:
    runtime_state: RuntimeState
    process_state: str
    system_proxy_state: str
    backend_id: str
    backend_title: str
    pid: int | None
    active_server: ServerEntry | None
    routing_profile: str
    healthcheck_result: HealthcheckResult | None = None


@dataclass(frozen=True)
class ServerDetachResult:
    server: ServerEntry | None
    subscription: SubscriptionEntry | None


@dataclass(frozen=True)
class TcpPingRunResult:
    results: tuple[TcpPingResult, ...]
    sorted_results: tuple[tuple[ServerEntry, TcpPingResult], ...] = ()


@dataclass(frozen=True)
class StartupMaintenanceResult:
    runtime_update: ComponentUpdateResult | None = None
    subscription_refresh: SubscriptionRefreshResult | None = None
    app_update: AppReleaseInfo | None = None


@dataclass(frozen=True)
class RuntimeNotice:
    message: str
    title: str = "Подключение остановлено"


class WinwsConflictError(RuntimeError):
    def __init__(self, conflicts: Iterable[RunningProcessDetails]) -> None:
        self.conflicts = tuple(conflicts)
        summary = VynexAppService.format_process_conflict_summary(self.conflicts)
        super().__init__(
            "Найдены конфликтующие процессы Winws. "
            f"Остановите их перед подключением или разрешите автоматическое завершение: {summary}"
        )


@dataclass
class VynexServiceDependencies:
    storage: JsonStorage = field(default_factory=JsonStorage)
    installer: XrayInstaller = field(default_factory=XrayInstaller)
    singbox_installer: SingboxInstaller = field(default_factory=SingboxInstaller)
    app_update_checker: AppUpdateChecker = field(default_factory=AppUpdateChecker)
    app_updater: AppSelfUpdater = field(default_factory=AppSelfUpdater)
    routing_profiles: RoutingProfileManager = field(default_factory=RoutingProfileManager)
    config_builder: XrayConfigBuilder = field(default_factory=XrayConfigBuilder)
    singbox_config_builder: SingboxConfigBuilder = field(default_factory=SingboxConfigBuilder)
    process_manager: XrayProcessManager | None = None
    singbox_process_manager: SingboxProcessManager | None = None
    amneziawg_process_manager: AmneziaWgProcessManager | None = None
    amneziawg_network_integration: AmneziaWgWindowsNetworkIntegration = field(
        default_factory=AmneziaWgWindowsNetworkIntegration
    )
    health_checker: XrayHealthChecker = field(default_factory=XrayHealthChecker)
    tcp_ping_service: TcpPingService = field(default_factory=TcpPingService)
    system_proxy_manager: WindowsSystemProxyManager = field(default_factory=WindowsSystemProxyManager)


class VynexAppService:
    def __init__(self, dependencies: VynexServiceDependencies | None = None) -> None:
        deps = dependencies or VynexServiceDependencies()
        self.storage = deps.storage
        self.installer = deps.installer
        self.singbox_installer = deps.singbox_installer
        self.app_update_checker = deps.app_update_checker
        self.app_updater = deps.app_updater
        self.subscription_manager = SubscriptionManager(self.storage)
        self.routing_profiles = deps.routing_profiles
        self.config_builder = deps.config_builder
        self.singbox_config_builder = deps.singbox_config_builder
        self.process_manager = deps.process_manager or XrayProcessManager(
            on_crash_callback=lambda: self._handle_backend_crash("xray")
        )
        self.singbox_process_manager = deps.singbox_process_manager or SingboxProcessManager(
            on_crash_callback=lambda: self._handle_backend_crash("singbox")
        )
        self.amneziawg_process_manager = deps.amneziawg_process_manager or AmneziaWgProcessManager(
            on_crash_callback=lambda: self._handle_backend_crash("amneziawg")
        )
        self.amneziawg_network_integration = deps.amneziawg_network_integration
        self.backends: dict[str, BaseVpnBackend] = {
            "xray": XrayBackend(
                installer=self.installer,
                config_builder=self.config_builder,
                process_manager=self.process_manager,
            ),
            "singbox": SingboxBackend(
                installer=self.singbox_installer,
                config_builder=self.singbox_config_builder,
                process_manager=self.singbox_process_manager,
            ),
            "amneziawg": AmneziaWgBackend(
                installer=self.installer,
                process_manager=self.amneziawg_process_manager,
            ),
        }
        self.health_checker = deps.health_checker
        self.tcp_ping_service = deps.tcp_ping_service
        self.system_proxy_manager = deps.system_proxy_manager
        self.app_release_info: AppReleaseInfo | None = self.app_update_checker.get_cached_release(max_age_seconds=None)
        self.runtime_notice: RuntimeNotice | None = None
        self._app_update_thread: threading.Thread | None = None
        self._runtime_state_cache: RuntimeState | None = None
        self._proxy_session: ProxyRuntimeSession | None = None

    def get_current_state(self) -> RuntimeState:
        return self._current_state()

    def list_servers(self, *, sorted_by_name: bool = False) -> list[ServerEntry]:
        servers = self.storage.load_servers()
        if sorted_by_name:
            return self._sorted_servers(servers)
        return servers

    def get_server(self, server_id: str) -> ServerEntry | None:
        return self.storage.get_server(server_id)

    def rename_server(self, server_id: str, new_name: str) -> ServerEntry:
        server = self._require_server(server_id)
        normalized_name = new_name.strip()
        if not normalized_name:
            raise ValueError("Название сервера не может быть пустым.")
        server.name = normalized_name
        if server.source == "subscription":
            server.extra["custom_name"] = True
        return self.storage.upsert_server(server)

    def set_server_favorite(self, server_id: str, favorite: bool) -> ServerEntry:
        server = self._require_server(server_id)
        server.extra = dict(server.extra)
        if favorite:
            server.extra["favorite"] = True
        else:
            server.extra["favorite"] = False
        return self.storage.upsert_server(server)

    def toggle_server_favorite(self, server_id: str) -> ServerEntry:
        server = self._require_server(server_id)
        return self.set_server_favorite(server.id, not bool(server.extra.get("favorite")))

    def update_server_link(
        self,
        server_id: str,
        raw_link: str,
        *,
        disconnect_active: bool = False,
    ) -> ServerEntry:
        server = self._require_server(server_id)
        if server.source != "manual":
            raise ValueError("Ссылку можно менять только у ручных серверов.")
        if server.is_amneziawg:
            raise ValueError("AWG-профиль импортируется из .conf и не редактируется как share-link.")
        normalized_link = raw_link.strip()
        if not normalized_link:
            raise ValueError("Ссылка сервера не может быть пустой.")

        state = self.get_current_state()
        if state.is_running and state.server_id == server.id:
            if not disconnect_active:
                raise RuntimeError("Этот сервер сейчас активен. Сначала отключите подключение.")
            self.disconnect(silent=True)

        updated_server = parse_share_link(normalized_link)
        updated_server.id = server.id
        updated_server.created_at = server.created_at
        updated_server.name = server.name
        updated_server.source = server.source
        updated_server.subscription_id = server.subscription_id
        updated_server.extra = dict(updated_server.extra)
        if server.extra.get("favorite"):
            updated_server.extra["favorite"] = True
        return self.storage.upsert_server(updated_server)

    def detach_server_from_subscription(self, server_id: str) -> ServerDetachResult:
        server = self._require_server(server_id)
        if server.source != "subscription":
            return ServerDetachResult(server=server, subscription=None)
        detached_server, parent_subscription = self.storage.detach_server_from_subscription(server.id)
        return ServerDetachResult(server=detached_server, subscription=parent_subscription)

    def delete_server(
        self,
        server_id: str,
        *,
        disconnect_active: bool = False,
    ) -> ServerEntry | None:
        server = self._require_server(server_id)
        state = self.get_current_state()
        if state.is_running and state.server_id == server.id:
            if not disconnect_active:
                raise RuntimeError("Этот сервер сейчас активен. Сначала отключите подключение.")
            self.disconnect(silent=True)
        return self.storage.delete_server(server.id)

    def list_subscriptions(self) -> list[SubscriptionEntry]:
        return self.storage.load_subscriptions()

    def get_subscription(self, subscription_id: str) -> SubscriptionEntry | None:
        return self.storage.get_subscription(subscription_id)

    def add_subscription_url(self, url: str) -> ImportResult:
        normalized_url = url.strip()
        parsed = urlparse(normalized_url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Введите корректный URL подписки.")
        return self.import_links(normalized_url)

    def rename_subscription(self, subscription_id: str, new_title: str) -> SubscriptionEntry:
        subscription = self._require_subscription(subscription_id)
        normalized_title = new_title.strip()
        if not normalized_title:
            raise ValueError("Название подписки не может быть пустым.")
        subscription.title = normalized_title
        return self.storage.upsert_subscription(subscription)

    def update_subscription_url(self, subscription_id: str, new_url: str) -> tuple[SubscriptionEntry, list[ServerEntry]]:
        subscription = self._require_subscription(subscription_id)
        normalized_url = new_url.strip()
        parsed = urlparse(normalized_url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Введите корректный URL подписки.")
        duplicate = next(
            (
                item
                for item in self.storage.load_subscriptions()
                if item.url == normalized_url and item.id != subscription.id
            ),
            None,
        )
        if duplicate is not None:
            raise ValueError(f"Подписка с этим URL уже существует: {duplicate.title}.")
        updated_subscription = SubscriptionEntry.from_dict(subscription.to_dict())
        updated_subscription.url = normalized_url
        imported = self._refresh_subscription(updated_subscription)
        return updated_subscription, imported

    def subscription_servers(self, subscription_id: str) -> list[ServerEntry]:
        return sorted(
            [
                server
                for server in self.storage.load_servers()
                if server.source == "subscription" and server.subscription_id == subscription_id
            ],
            key=lambda item: item.name.lower(),
        )

    def delete_subscription(
        self,
        subscription_id: str,
        *,
        remove_servers: bool = True,
        disconnect_active: bool = False,
    ) -> tuple[SubscriptionEntry | None, int]:
        subscription = self._require_subscription(subscription_id)
        servers = self.subscription_servers(subscription.id)
        server_ids = {server.id for server in servers}
        state = self.get_current_state()
        if remove_servers and state.is_running and state.server_id in server_ids:
            if not disconnect_active:
                raise RuntimeError("Сервер этой подписки сейчас активен. Сначала отключите подключение.")
            self.disconnect(silent=True)
        return self.storage.delete_subscription(subscription.id, remove_servers=remove_servers)

    def get_settings(self, *, validated: bool = True) -> AppSettings:
        if validated:
            return self._validated_settings()
        return self.storage.load_settings()

    def save_settings(self, settings: AppSettings, *, validate: bool = True) -> AppSettings:
        if validate:
            settings.set_system_proxy = self._coerce_bool(settings.set_system_proxy)
            settings.connection_mode = self._coerce_connection_mode(settings.connection_mode)
            settings.auto_update_subscriptions_on_startup = self._coerce_bool(
                settings.auto_update_subscriptions_on_startup
            )
        self.storage.save_settings(settings)
        return settings

    def list_routing_profiles(self) -> list[RoutingProfile]:
        return self.routing_profiles.list_profiles()

    def set_active_routing_profile(self, profile_id: str) -> RoutingProfile:
        profile = self.routing_profiles.get_profile(profile_id)
        if profile is None:
            raise ValueError(f"Профиль маршрутизации не найден: {profile_id}")
        settings = self.get_settings()
        settings.active_routing_profile_id = profile.profile_id
        self.storage.save_settings(settings)
        return profile

    def update_settings(
        self,
        *,
        connection_mode: str | None = None,
        set_system_proxy: bool | None = None,
        auto_update_subscriptions_on_startup: bool | None = None,
        active_routing_profile_id: str | None = None,
    ) -> AppSettings:
        settings = self.get_settings()
        if connection_mode is not None:
            settings.connection_mode = self._coerce_connection_mode(connection_mode)
        if set_system_proxy is not None:
            settings.set_system_proxy = self._coerce_bool(set_system_proxy)
        if auto_update_subscriptions_on_startup is not None:
            settings.auto_update_subscriptions_on_startup = self._coerce_bool(auto_update_subscriptions_on_startup)
        if active_routing_profile_id is not None:
            if self.routing_profiles.get_profile(active_routing_profile_id) is None:
                raise ValueError(f"Профиль маршрутизации не найден: {active_routing_profile_id}")
            settings.active_routing_profile_id = active_routing_profile_id
        self.storage.save_settings(settings)
        return settings

    def import_links(self, raw_value: str) -> ImportResult:
        import_kind, payload = self._detect_import_target(raw_value)
        if import_kind == "server":
            server = self.storage.upsert_server(parse_share_link(str(payload)))
            return ImportResult(kind="server", servers=(server,))
        if import_kind == "server_bundle":
            imported = self._import_server_links(payload)  # type: ignore[arg-type]
            return ImportResult(kind="server_bundle", servers=tuple(imported))

        normalized_url = str(payload)
        existing = self.storage.get_subscription_by_url(normalized_url)
        default_title = existing.title if existing else self._subscription_default_title(normalized_url)
        subscription = existing or SubscriptionEntry.new(url=normalized_url, title=default_title)
        subscription.url = normalized_url
        subscription.title = default_title
        imported = self._refresh_subscription(subscription)
        title_kind = "subscription_updated" if existing is not None else "subscription"
        return ImportResult(kind=title_kind, servers=tuple(imported), subscription=subscription)

    def refresh_subscription(self, subscription_id: str) -> list[ServerEntry]:
        subscription = self.storage.get_subscription(subscription_id)
        if subscription is None:
            raise ValueError(f"Подписка не найдена: {subscription_id}")
        return self._refresh_subscription(subscription)

    def refresh_subscriptions(self, *, only_auto_update: bool = False) -> SubscriptionRefreshResult:
        success, failed = self.subscription_manager.refresh_all(only_auto_update=only_auto_update)
        return SubscriptionRefreshResult(success=tuple(success), failed=tuple(failed))

    def missing_startup_runtime_components(self) -> tuple[str, ...]:
        missing_components: list[str] = []
        if not XRAY_EXECUTABLE.exists():
            missing_components.append("Xray-core (xray.exe)")
        if not GEOIP_PATH.exists():
            missing_components.append("geoip.dat")
        if not GEOSITE_PATH.exists():
            missing_components.append("geosite.dat")
        return tuple(missing_components)

    def ensure_startup_runtime(self, *, progress_callback: ProgressCallback | None = None) -> ComponentUpdateResult | None:
        missing_components = self.missing_startup_runtime_components()
        if not missing_components:
            return None
        self._emit_progress(
            progress_callback,
            "Подготовка runtime: " + ", ".join(missing_components),
        )
        path = self.installer.ensure_xray()
        details = (path.name, "geoip.dat", "geosite.dat")
        return ComponentUpdateResult("startup_runtime", details, tuple(self.installer.warnings))

    def run_startup_maintenance(
        self,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> StartupMaintenanceResult:
        self._emit_progress(progress_callback, "Проверка обновлений приложения")
        app_update = self.check_app_update(force=False)

        runtime_update = self.ensure_startup_runtime(progress_callback=progress_callback)

        subscription_refresh: SubscriptionRefreshResult | None = None
        settings = self._validated_settings(raise_on_error=False)
        if settings.auto_update_subscriptions_on_startup and self.storage.load_subscriptions():
            self._emit_progress(progress_callback, "Авто-обновление подписок")
            subscription_refresh = self.refresh_subscriptions(only_auto_update=True)

        return StartupMaintenanceResult(
            runtime_update=runtime_update,
            subscription_refresh=subscription_refresh,
            app_update=app_update,
        )

    def connect(
        self,
        server_id: str,
        *,
        mode: str | None = None,
        routing_profile_id: str | None = None,
        set_system_proxy: bool | None = None,
        terminate_winws_conflicts: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> ConnectionResult:
        selected_server = self.storage.get_server(server_id)
        if selected_server is None:
            raise ValueError(f"Сервер не найден: {server_id}")
        self._ensure_winws_conflicts_resolved(terminate=terminate_winws_conflicts)

        settings = self._validated_settings()
        connection_mode = self._coerce_connection_mode(mode or settings.connection_mode)
        routing_profile = self._get_active_routing_profile(routing_profile_id)
        if routing_profile is None:
            raise RuntimeError("Не найден активный набор правил маршрутизации.")

        connection_profile = BackendConnectionProfile(
            server=selected_server,
            mode=connection_mode,
            routing_profile=routing_profile,
        )
        backend = self._backend_for_connection(connection_profile)
        manager = self._process_manager_for_backend(backend.backend_id)
        use_system_proxy = (
            connection_mode == "PROXY"
            and (settings.set_system_proxy if set_system_proxy is None else bool(set_system_proxy))
        )
        system_proxy_applied = False
        previous_system_proxy: SystemProxyState | None = None
        proxy_session: ProxyRuntimeSession | None = None
        outbound_interface: WindowsInterfaceDetails | None = None
        tun_interface: WindowsInterfaceDetails | None = None
        tun_interface_name_hint: str | None = None
        awg_expected_network = None
        awg_network_session = None
        pid: int | None = None
        helper_pid: int | None = None

        try:
            self._emit_progress(progress_callback, "Подготовка параметров подключения")
            self._ensure_runtime_ready(
                connection_mode,
                server=selected_server,
                routing_profile=routing_profile,
            )
            if connection_mode == "TUN":
                self._emit_progress(progress_callback, "Проверка требований TUN")
                outbound_interface = self._prepare_tun_prerequisites(backend=backend)
            self.disconnect(silent=True)
            manager.ensure_no_running_instances()
            if use_system_proxy:
                previous_system_proxy = self.system_proxy_manager.snapshot()

            if connection_mode == "TUN":
                config = backend.build_runtime_config(
                    BackendRuntimeRequest(
                        profile=connection_profile,
                        outbound_interface_name=outbound_interface.alias if outbound_interface else None,
                    )
                )
                tun_interface_name_hint = str(config.get("tunnel_name") or "").strip() or None
                if backend.backend_id == "amneziawg":
                    awg_profile = selected_server.amneziawg_profile
                    if awg_profile is None or tun_interface_name_hint is None:
                        raise RuntimeError("Для AmneziaWG не удалось определить параметры туннельной сессии.")
                    self.amneziawg_network_integration.ensure_prerequisites(tunnel_name=tun_interface_name_hint)
                    awg_expected_network = self.amneziawg_network_integration.build_expected_state(
                        profile=awg_profile,
                        tunnel_name=tun_interface_name_hint,
                    )
            else:
                proxy_session = self._build_runtime_proxy_session()
                config = backend.build_runtime_config(
                    BackendRuntimeRequest(
                        profile=connection_profile,
                        proxy_session=proxy_session,
                    )
                )

            self._emit_progress(progress_callback, "Запуск ядра подключения")
            pid = manager.start(config)
            self._emit_progress(progress_callback, "Ожидание готовности подключения")
            if connection_mode == "TUN":
                tun_interface = self._wait_for_tun_ready(
                    pid=pid,
                    backend=backend,
                    tun_interface_name=tun_interface_name_hint,
                )
                if backend.backend_id == "amneziawg":
                    awg_profile = selected_server.amneziawg_profile
                    if awg_profile is None or tun_interface_name_hint is None:
                        raise RuntimeError("Для AmneziaWG не удалось определить профиль туннельной сессии.")
                    awg_network_session = self.amneziawg_network_integration.capture_session(
                        profile=awg_profile,
                        tunnel_name=tun_interface_name_hint,
                    )
                    tun_interface = WindowsInterfaceDetails(
                        alias=awg_network_session.interface_name,
                        index=awg_network_session.interface_index,
                        ipv4=awg_network_session.primary_ipv4,
                        status=tun_interface.status if tun_interface is not None else "Up",
                        has_route=bool(awg_network_session.route_prefixes),
                    )
                elif self._tun_route_prefixes(backend.backend_id):
                    self._emit_progress(progress_callback, "Настройка маршрутов Windows")
                    self._apply_tun_routes(tun_interface, backend=backend)
            elif proxy_session is not None:
                self._wait_for_local_proxy_ready(
                    pid=pid,
                    proxy_session=proxy_session,
                    mode=connection_mode,
                    backend_id=backend.backend_id,
                )

            self._emit_progress(progress_callback, "Проверка доступности сети")
            health_warning: str | None = None
            health_result = self._run_healthcheck(
                mode=connection_mode,
                http_port=proxy_session.http_port if proxy_session is not None else None,
            )
            if not health_result.ok:
                health_warning = self._handle_failed_healthcheck(
                    mode=connection_mode,
                    pid=pid,
                    manager=manager,
                    health_result=health_result,
                )
            if connection_mode == "PROXY" and use_system_proxy:
                if proxy_session is None:
                    raise RuntimeError("Для системного proxy не определены локальные порты.")
                self._emit_progress(progress_callback, "Применение системного proxy Windows")
                self.system_proxy_manager.enable_proxy(http_port=proxy_session.http_port)
                system_proxy_applied = True

            tun_route_prefixes = (
                list(awg_network_session.route_prefixes)
                if awg_network_session is not None
                else list(self._tun_route_prefixes(backend.backend_id))
                if connection_mode == "TUN"
                else []
            )
            routing_display_label = self._routing_display_name(backend.backend_id, routing_profile.name)
            state = RuntimeState(
                pid=pid,
                helper_pid=helper_pid,
                backend_id=backend.backend_id,
                mode=connection_mode,
                server_id=selected_server.id,
                started_at=utc_now_iso(),
                system_proxy_enabled=use_system_proxy,
                previous_system_proxy=previous_system_proxy.to_dict() if previous_system_proxy else None,
                routing_profile_id=routing_profile.profile_id,
                routing_profile_name=routing_display_label,
                tun_interface_name=tun_interface.alias if tun_interface else None,
                tun_interface_index=tun_interface.index if tun_interface else None,
                tun_interface_ipv4=tun_interface.ipv4 if tun_interface else None,
                tun_interface_addresses=list(awg_network_session.interface_addresses) if awg_network_session else [],
                tun_dns_servers=list(awg_network_session.dns_servers) if awg_network_session else [],
                tun_route_prefixes=tun_route_prefixes,
                outbound_interface_name=outbound_interface.alias if outbound_interface else None,
            )
            self._save_runtime_state(state)
            self._proxy_session = proxy_session
            return ConnectionResult(
                state=state,
                server=selected_server,
                backend_id=backend.backend_id,
                health_warning=health_warning,
            )
        except Exception:
            if connection_mode == "TUN":
                cleanup_state = RuntimeState(
                    backend_id=backend.backend_id,
                    mode="TUN",
                    tun_interface_name=tun_interface.alias if tun_interface else tun_interface_name_hint,
                    tun_interface_index=tun_interface.index if tun_interface else None,
                    tun_interface_ipv4=tun_interface.ipv4 if tun_interface else None,
                    tun_interface_addresses=list(
                        awg_network_session.interface_addresses
                        if awg_network_session is not None
                        else awg_expected_network.interface_addresses
                        if awg_expected_network is not None
                        else []
                    ),
                    tun_dns_servers=list(
                        awg_network_session.dns_servers
                        if awg_network_session is not None
                        else awg_expected_network.dns_servers
                        if awg_expected_network is not None
                        else []
                    ),
                    tun_route_prefixes=list(
                        awg_network_session.route_prefixes
                        if awg_network_session is not None
                        else awg_expected_network.route_prefixes
                        if awg_expected_network is not None
                        else self._tun_route_prefixes(backend.backend_id)
                    ),
                )
                if pid and backend.backend_id == "amneziawg":
                    manager.stop(pid)
                    pid = None
                self._cleanup_tun_state(cleanup_state)
            if pid:
                manager.stop(pid)
            if helper_pid:
                self.process_manager.stop(helper_pid)
            if system_proxy_applied:
                self.system_proxy_manager.restore(previous_system_proxy)
            self._proxy_session = None
            self._reset_runtime_state()
            raise

    def disconnect(self, *, silent: bool = False) -> RuntimeState:
        state = self._load_runtime_state_or_recover()
        backend_id = self._runtime_backend_id(state)
        if state.pid and str(state.mode or "").upper() == "TUN" and backend_id == "amneziawg":
            self._process_manager_for_runtime_state(state).stop(state.pid)
            state.pid = None
        if str(state.mode or "").upper() == "TUN":
            self._cleanup_tun_state(state)
        if state.pid:
            self._process_manager_for_runtime_state(state).stop(state.pid)
        if state.helper_pid:
            self.process_manager.stop(state.helper_pid)
        self._proxy_session = None
        self._restore_system_proxy(state)
        return self._reset_runtime_state()

    def get_components_status(self) -> ComponentsStatus:
        return ComponentsStatus(
            items=(
                ComponentStatus("xray", "Xray-core", XRAY_EXECUTABLE.exists(), self._xray_version_status_label()),
                ComponentStatus("singbox", "sing-box", SINGBOX_EXECUTABLE.exists(), str(SINGBOX_EXECUTABLE)),
                ComponentStatus("wintun", "wintun.dll", WINTUN_DLL.exists(), str(WINTUN_DLL)),
                ComponentStatus(
                    "amneziawg",
                    "AmneziaWG",
                    AMNEZIAWG_EXECUTABLE.exists()
                    and AMNEZIAWG_EXECUTABLE_FALLBACK.exists()
                    and AMNEZIAWG_WINTUN_DLL.exists(),
                    "amneziawg.exe, awg.exe, wintun.dll",
                ),
                ComponentStatus("geoip", "geoip.dat", GEOIP_PATH.exists(), str(GEOIP_PATH)),
                ComponentStatus("geosite", "geosite.dat", GEOSITE_PATH.exists(), str(GEOSITE_PATH)),
                ComponentStatus(
                    "routing_profiles",
                    "Профили маршрутизации",
                    bool(self.routing_profiles.list_profiles()),
                    f"{len(self.routing_profiles.list_profiles())} шт.",
                ),
            )
        )

    def update_component(self, component: str, *, stop_active_connection: bool = False) -> ComponentUpdateResult:
        normalized = component.strip().lower().replace("-", "_")
        self._prepare_component_update(stop_active_connection=stop_active_connection)
        if normalized in {"xray", "xray_core"}:
            path = self.installer.update_xray()
            return ComponentUpdateResult("xray", (path.name,), tuple(self.installer.warnings))
        if normalized in {"singbox", "sing_box"}:
            path = self.singbox_installer.update_singbox()
            return ComponentUpdateResult("singbox", (path.name,))
        if normalized == "wintun":
            path = self.installer.ensure_xray_tun_runtime()
            return ComponentUpdateResult("wintun", (path.name,), tuple(self.installer.warnings))
        if normalized == "amneziawg":
            self.installer.update_amneziawg()
            return ComponentUpdateResult(
                "amneziawg",
                ("amneziawg.exe", "awg.exe", "wintun.dll"),
                tuple(self.installer.warnings),
            )
        if normalized == "geoip":
            path = self.installer.update_geoip()
            return ComponentUpdateResult("geoip", (path.name,), tuple(self.installer.warnings))
        if normalized == "geosite":
            path = self.installer.update_geosite()
            return ComponentUpdateResult("geosite", (path.name,), tuple(self.installer.warnings))
        if normalized in {"routing", "routing_profiles"}:
            profiles = self.routing_profiles.update_profiles()
            return ComponentUpdateResult("routing_profiles", (f"profiles={len(profiles)}",))
        if normalized == "all":
            return self.update_all_components(stop_active_connection=stop_active_connection)
        raise ValueError(f"Неизвестный компонент: {component}")

    def update_all_components(self, *, stop_active_connection: bool = False) -> ComponentUpdateResult:
        self._prepare_component_update(stop_active_connection=stop_active_connection)
        result = self.installer.update_all_components()
        singbox_path = self.singbox_installer.update_singbox()
        profiles = self.routing_profiles.update_profiles()
        details = [*result.keys(), singbox_path.name, f"routing_profiles={len(profiles)}"]
        return ComponentUpdateResult("all", tuple(details), tuple(self.installer.warnings))

    def run_tcp_ping(
        self,
        servers: Iterable[ServerEntry] | None = None,
        *,
        persist: bool = True,
    ) -> TcpPingRunResult:
        server_list = list(servers) if servers is not None else self.storage.load_servers()
        results = self.tcp_ping_service.ping_many(server_list)
        if persist:
            self.persist_tcp_ping_results(server_list, results)
        sorted_results = sort_tcp_ping_results(server_list, results)
        return TcpPingRunResult(results=tuple(results), sorted_results=tuple(sorted_results))

    def run_tcp_ping_for_server(self, server_id: str, *, persist: bool = True) -> TcpPingRunResult:
        return self.run_tcp_ping([self._require_server(server_id)], persist=persist)

    def best_tcp_ping_server(self, servers: Iterable[ServerEntry] | None = None) -> ServerEntry | None:
        candidates = list(servers) if servers is not None else self.storage.load_servers()
        best: tuple[int, str, ServerEntry] | None = None
        for server in candidates:
            ping = server.extra.get("tcp_ping") if isinstance(server.extra, dict) else None
            if not isinstance(ping, dict) or not ping.get("ok"):
                continue
            latency = ping.get("latency_ms")
            if not isinstance(latency, int):
                continue
            candidate = (latency, server.name.lower(), server)
            if best is None or candidate < best:
                best = candidate
        return best[2] if best is not None else None

    def persist_tcp_ping_results(
        self,
        servers: Iterable[ServerEntry],
        results: Iterable[TcpPingResult],
        *,
        preserve_other_cached_entries: bool = True,
    ) -> list[ServerEntry]:
        result_by_id = {result.server_id: result for result in results}
        updated_servers: list[ServerEntry] = []
        for server in servers:
            result = result_by_id.get(server.id)
            if result is None:
                updated_servers.append(server)
                continue
            updated = ServerEntry.from_dict(server.to_dict())
            extra = dict(updated.extra)
            if preserve_other_cached_entries:
                cached = dict(extra.get("tcp_ping") or {})
            else:
                cached = {}
            cached.update(
                {
                    "ok": result.ok,
                    "latency_ms": result.latency_ms,
                    "error": result.error,
                    "checked_at": result.checked_at,
                }
            )
            extra["tcp_ping"] = cached
            updated.extra = extra
            updated_servers.append(updated)
        if updated_servers:
            self.storage.upsert_servers(updated_servers, continue_on_error=True)
        return updated_servers

    def check_app_update(self, *, force: bool = False) -> AppReleaseInfo:
        self.app_release_info = self.app_update_checker.check_latest_release(force=force)
        return self.app_release_info

    def get_runtime_status(self, *, run_healthcheck: bool = False) -> AppRuntimeStatus:
        state = self.get_current_state()
        backend_id = self._runtime_backend_id(state)
        server = self.storage.get_server(state.server_id) if state.server_id else None
        process_state = self._backend_process_state(state).value
        proxy_state = self._system_proxy_state_label()
        health_result: HealthcheckResult | None = None
        if run_healthcheck and state.is_running:
            if str(state.mode or "").upper() == "TUN":
                health_result = self.health_checker.verify_direct()
            elif self._proxy_session is not None:
                health_result = self.health_checker.verify_proxy(http_port=self._proxy_session.http_port)
            else:
                health_result = HealthcheckResult(
                    ok=False,
                    message="Health-check недоступен: локальные proxy-порты текущей сессии не сохранены.",
                    inconclusive=True,
                )
        return AppRuntimeStatus(
            runtime_state=state,
            process_state=process_state,
            system_proxy_state=proxy_state,
            backend_id=backend_id,
            backend_title=self._backend_engine_title(backend_id),
            pid=state.pid,
            active_server=server,
            routing_profile=state.routing_profile_name or self._active_routing_profile_name(),
            healthcheck_result=health_result,
        )

    def app_release_page_url(self) -> str:
        release = self.available_app_update() or self.app_release_info
        return release.release_url if release is not None and release.release_url else APP_RELEASES_PAGE

    def can_self_update(self) -> bool:
        return self.app_updater.can_self_update()

    def prepare_self_update(
        self,
        release_info: AppReleaseInfo | None = None,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> AppSelfUpdatePrepareResult:
        release = release_info or self.available_app_update()
        if release is None:
            release = self.check_app_update(force=True)
        if not release.is_update_available:
            raise RuntimeError("Новая версия приложения не найдена.")
        if not self.app_updater.can_self_update():
            raise RuntimeError("Self-update доступен только в packaged Windows .exe сборке.")

        def download_progress(done: int, total: int | None) -> None:
            if progress_callback is None:
                return
            if total and total > 0:
                percent = int(done * 100 / total)
                progress_callback(f"Загрузка обновления: {percent}% ({done}/{total} байт)")
            else:
                progress_callback(f"Загрузка обновления: {done} байт")

        if progress_callback is not None:
            progress_callback("Загрузка exe из latest release")
        download = self.app_updater.download_release(release, progress_callback=download_progress)
        if progress_callback is not None:
            progress_callback("Подготовка helper script")
        plan = self.app_updater.prepare_apply_plan(download, current_pid=os.getpid())
        self.app_updater.write_helper_script(plan)
        return AppSelfUpdatePrepareResult(release=release, plan=plan)

    def launch_self_update(self, plan: AppUpdateApplyPlan) -> None:
        self.disconnect(silent=True)
        self.app_updater.launch_helper(plan)

    def get_cached_app_update(self) -> AppReleaseInfo | None:
        self.app_release_info = self.app_update_checker.get_cached_release(max_age_seconds=None)
        return self.app_release_info

    def available_app_update(self) -> AppReleaseInfo | None:
        if self.app_release_info is None:
            return None
        if not self.app_release_info.is_update_available:
            return None
        if not self.app_release_info.latest_version:
            return None
        return self.app_release_info

    def schedule_app_update_check(self) -> None:
        if self.app_update_checker.get_cached_release() is not None:
            return
        if self._app_update_thread is not None and self._app_update_thread.is_alive():
            return
        self._app_update_thread = threading.Thread(
            target=self._refresh_app_update_info_in_background,
            name="vynex-service-app-update-check",
            daemon=True,
        )
        self._app_update_thread.start()

    def _refresh_app_update_info_in_background(self) -> None:
        try:
            self.check_app_update()
        except Exception:
            pass

    def _detect_import_target(self, raw_value: str) -> tuple[str, str | list[ServerEntry]]:
        normalized = raw_value.strip()
        if not normalized:
            raise ValueError("Пустой ввод.")
        if is_supported_share_link(normalized):
            return "server", normalized
        parsed = urlparse(normalized)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            return "subscription", normalized
        servers = parse_server_entries(normalized)
        if not servers:
            raise ValueError(
                "Не удалось определить формат. Вставьте ссылку сервера, vpn:// ключ, URL подписки, AWG-конфиг, путь к .conf, Base64, plain-text или JSON подписки."
            )
        return "server_bundle", servers

    def _require_server(self, server_id: str) -> ServerEntry:
        server = self.storage.get_server(server_id)
        if server is None:
            raise ValueError(f"Сервер не найден: {server_id}")
        return server

    def _require_subscription(self, subscription_id: str) -> SubscriptionEntry:
        subscription = self.storage.get_subscription(subscription_id)
        if subscription is None:
            raise ValueError(f"Подписка не найдена: {subscription_id}")
        return subscription

    def _import_server_links(self, links: list[ServerEntry]) -> list[ServerEntry]:
        imported: list[ServerEntry] = []
        imported_ids: set[str] = set()
        stored_servers = self.storage.upsert_servers(links, continue_on_error=True)
        for server in stored_servers:
            if server.id in imported_ids:
                continue
            imported.append(server)
            imported_ids.add(server.id)
        if not imported:
            raise ValueError("Не удалось импортировать ни один сервер из вставленных данных.")
        return imported

    def _refresh_subscription(self, subscription: SubscriptionEntry) -> list[ServerEntry]:
        imported = self.subscription_manager.import_subscription(subscription)
        subscription.updated_at = utc_now_iso()
        subscription.last_error = None
        subscription.last_error_at = None
        self.storage.upsert_subscription(subscription)
        return imported

    def _load_runtime_state_or_recover(self) -> RuntimeState:
        cached_state = self._runtime_state_cache
        if cached_state is not None:
            return cached_state
        try:
            state = self.storage.load_runtime_state()
        except StorageCorruptionError as exc:
            self._handle_runtime_state_corruption(exc)
            state = RuntimeState()
        self._runtime_state_cache = state
        return state

    def _save_runtime_state(self, state: RuntimeState) -> RuntimeState:
        self.storage.save_runtime_state(state)
        self._runtime_state_cache = state
        return state

    def _reset_runtime_state(self) -> RuntimeState:
        return self._save_runtime_state(RuntimeState())

    def _current_state(self) -> RuntimeState:
        state = self._load_runtime_state_or_recover()
        state = self._sync_runtime_state_with_manager(state)
        manager = self._process_manager_for_runtime_state(state)
        main_dead = bool(
            state.pid
            and not self._backend_runtime_recovery_active(state)
            and not manager.is_running(state.pid)
        )
        helper_dead = bool(state.helper_pid and not self.process_manager.is_running(state.helper_pid))
        if main_dead or helper_dead:
            self._proxy_session = None
            self.disconnect(silent=True)
            return self._load_runtime_state_or_recover()
        self._runtime_state_cache = state
        return state

    def _sync_runtime_state_with_manager(self, state: RuntimeState) -> RuntimeState:
        if not state.is_running:
            return state
        manager = self._process_manager_for_runtime_state(state)
        current_pid = getattr(manager, "pid", None)
        if current_pid is None or current_pid == state.pid:
            return state
        if not manager.is_running(state.pid):
            return state
        state.pid = current_pid
        self._save_runtime_state(state)
        return state

    def _ensure_winws_conflicts_resolved(self, *, terminate: bool) -> None:
        conflicts = list_running_processes_by_names(WINWS_CONFLICT_PROCESS_NAMES)
        if not conflicts:
            return
        if not terminate:
            raise WinwsConflictError(conflicts)
        failed_processes = terminate_running_processes(conflicts)
        if failed_processes:
            failed_summary = self.format_process_conflict_summary(failed_processes)
            raise RuntimeError(
                "Не удалось завершить конфликтующие процессы: "
                f"{failed_summary}. Остановите их вручную и повторите подключение."
            )

    @staticmethod
    def format_process_conflict_summary(
        processes: Iterable[RunningProcessDetails],
    ) -> str:
        return ", ".join(f"{process.name} (PID {process.pid})" for process in processes)

    def _get_active_routing_profile(self, profile_id: str | None = None) -> RoutingProfile | None:
        settings = self.storage.load_settings()
        if profile_id:
            return self.routing_profiles.get_profile(profile_id)
        profile = self.routing_profiles.get_profile(settings.active_routing_profile_id)
        if profile is not None:
            return profile
        fallback = self.routing_profiles.get_profile("default")
        if fallback is not None:
            settings.active_routing_profile_id = fallback.profile_id
            self.storage.save_settings(settings)
        return fallback

    @staticmethod
    def _routing_display_name(backend_id: str | None, routing_name: str | None) -> str:
        if backend_id == "amneziawg":
            return "AWG-конфиг"
        normalized = str(routing_name or "").strip()
        return normalized or "-"

    def _restore_system_proxy(self, state: RuntimeState) -> None:
        if not state.system_proxy_enabled:
            return
        previous_state = SystemProxyState.from_dict(state.previous_system_proxy)
        self.system_proxy_manager.restore(previous_state)

    def _system_proxy_state_label(self) -> str:
        try:
            snapshot = self.system_proxy_manager.snapshot()
        except Exception as exc:  # noqa: BLE001
            return f"недоступно: {exc}"
        if WindowsSystemProxyManager.is_vynex_managed_state(snapshot):
            return "Vynex-managed"
        if snapshot is not None and snapshot.proxy_enable:
            return "включен другим приложением"
        return "выключен"

    def _active_routing_profile_name(self) -> str:
        profile = self._get_active_routing_profile()
        return profile.name if profile else "-"

    def _run_healthcheck(self, *, mode: str, http_port: int | None) -> HealthcheckResult:
        if mode == "TUN":
            return self.health_checker.verify_direct()
        if http_port is None:
            raise RuntimeError(f"Для режима {mode} не определен HTTP порт health-check.")
        return self.health_checker.verify_proxy(http_port=http_port)

    @staticmethod
    def _handle_failed_healthcheck(
        *,
        mode: str,
        pid: int,
        manager,
        health_result: HealthcheckResult,
    ) -> str | None:
        details = (
            "Ядро запущено, но health-check не прошел.\n"
            f"Детали: {health_result.message}"
        )
        if mode == "TUN":
            return (
                "TUN подключение поднято, но быстрый health-check не подтвердил доступ в сеть. "
                "Подключение оставлено активным: проверьте реальный трафик вручную. "
                f"Детали: {health_result.message}"
            )
        if mode == "PROXY" and health_result.inconclusive:
            return (
                "Локальный proxy поднят, но быстрый health-check не подтвердил внешний доступ. "
                "Подключение оставлено активным: проверьте реальный трафик вручную. "
                f"Детали: {health_result.message}"
            )
        manager.stop(pid)
        raise RuntimeError(details)

    def _handle_backend_crash(self, backend_id: str) -> None:
        state = self._load_runtime_state_or_recover()
        if self._runtime_backend_id(state) != backend_id:
            return
        mode = str(state.mode or "").upper()
        if mode not in {"PROXY", "TUN"}:
            return
        self._proxy_session = None
        if mode == "TUN":
            self._cleanup_tun_state(state)
        else:
            self._restore_system_proxy(state)
        self._reset_runtime_state()
        engine_title = self._backend_engine_title(backend_id)
        if mode == "TUN":
            self.runtime_notice = RuntimeNotice(
                message=(
                    f"{engine_title} завершился и не смог восстановиться автоматически. "
                    "Сетевое состояние туннеля очищено, подключение сброшено."
                ),
            )
            return
        self.runtime_notice = RuntimeNotice(
            message=(
                f"{engine_title} завершился и не смог восстановиться автоматически. "
                "Подключение сброшено, системный proxy возвращен в прежнее состояние."
            ),
        )

    def _backend_runtime_recovery_active(self, state: RuntimeState) -> bool:
        if str(state.mode or "").upper() not in {"PROXY", "TUN"} or not state.is_running:
            return False
        backend_state = self._backend_process_state(state)
        if backend_state in {ProcessState.STARTING, ProcessState.STOPPING}:
            return True
        return backend_state == ProcessState.CRASHED and self._backend_supports_crash_recovery(state)

    def _backend_supports_crash_recovery(self, state: RuntimeState | None) -> bool:
        backend = self._backend_for_runtime_state(state)
        return bool(backend is not None and backend.supports_crash_recovery)

    def _backend_process_state(self, state: RuntimeState | None = None) -> ProcessState:
        manager = self._process_manager_for_runtime_state(state)
        manager_state = manager.state
        if (
            state is not None
            and state.is_running
            and manager_state == ProcessState.STOPPED
            and state.pid is not None
            and manager.is_running(state.pid)
        ):
            return ProcessState.RUNNING
        return manager_state

    def _prepare_component_update(self, *, stop_active_connection: bool) -> None:
        state = self._current_state()
        if not state.is_running:
            return
        if not stop_active_connection:
            raise RuntimeError("Обновление компонента требует остановки активного подключения.")
        self.disconnect(silent=True)

    def _validated_settings(self, *, raise_on_error: bool = True) -> AppSettings:
        settings = self.storage.load_settings()
        try:
            settings.set_system_proxy = self._coerce_bool(settings.set_system_proxy)
            settings.connection_mode = self._coerce_connection_mode(settings.connection_mode)
            settings.auto_update_subscriptions_on_startup = self._coerce_bool(
                settings.auto_update_subscriptions_on_startup
            )
            return settings
        except (TypeError, ValueError) as exc:
            if raise_on_error:
                raise ValueError("Параметры proxy в настройках некорректны.") from exc
            return AppSettings(active_routing_profile_id=settings.active_routing_profile_id)

    def _build_runtime_proxy_session(self) -> ProxyRuntimeSession:
        used_ports: set[int] = set()
        http_port = pick_random_port(used_ports=used_ports)
        used_ports.add(http_port)
        socks_port = pick_random_port(used_ports=used_ports)
        return ProxyRuntimeSession(
            socks_port=socks_port,
            http_port=http_port,
            socks_credentials=LocalProxyCredentials(
                username=generate_random_username(),
                password=generate_random_password(),
            ),
        )

    def _prepare_tun_prerequisites(self, *, backend: BaseVpnBackend | None = None) -> WindowsInterfaceDetails | None:
        if not is_running_as_admin():
            raise RuntimeError(
                "TUN режим требует запуска приложения от имени администратора. "
                "Перезапустите Vynex с повышенными правами."
            )
        if backend is not None and backend.backend_id in {"amneziawg", "singbox"}:
            return None
        outbound_interface = get_active_ipv4_interface(
            exclude_aliases={self._tun_interface_name(backend.backend_id if backend is not None else None)}
        )
        if outbound_interface is None:
            raise RuntimeError(
                "Не удалось определить активный IPv4 интерфейс Windows для TUN режима. "
                "Проверьте, что сеть подключена и у системы есть рабочий default route."
            )
        return outbound_interface

    def _wait_for_local_proxy_ready(
        self,
        *,
        pid: int,
        proxy_session: ProxyRuntimeSession,
        mode: str,
        backend_id: str | None = None,
    ) -> None:
        http_ready = wait_for_port_listener(proxy_session.http_port, timeout=12.0)
        socks_ready = wait_for_port_listener(proxy_session.socks_port, timeout=12.0)
        if http_ready and socks_ready:
            return
        manager = self._process_manager_for_mode(mode, backend_id=backend_id)
        if not manager.is_running(pid):
            raise RuntimeError(
                "Ядро завершилось до запуска локальных proxy-inbound.\n"
                f"{manager.read_recent_output()}"
            )
        raise RuntimeError("Ядро не открыло локальные proxy-inbound вовремя.")

    def _wait_for_tun_ready(
        self,
        *,
        pid: int,
        backend: BaseVpnBackend | None = None,
        tun_interface_name: str | None = None,
    ) -> WindowsInterfaceDetails:
        backend_id = backend.backend_id if backend is not None else None
        tun_interface_name = tun_interface_name or self._tun_interface_name(backend_id)
        details = wait_for_tun_interface_details(tun_interface_name, timeout=12.0)
        if details is not None:
            return details
        manager = self._process_manager_for_mode("TUN", backend_id=backend_id)
        if not manager.is_running(pid):
            raise RuntimeError(
                "Ядро завершилось до инициализации TUN интерфейса.\n"
                f"{manager.read_recent_output()}"
            )
        raise RuntimeError(
            "TUN интерфейс был создан, но Windows не назначила ему IPv4 адрес вовремя."
        )

    def _apply_tun_routes(
        self,
        tun_interface: WindowsInterfaceDetails,
        *,
        backend: BaseVpnBackend | None = None,
    ) -> None:
        if tun_interface.ipv4 is None:
            raise RuntimeError("Для TUN интерфейса не определен IPv4 адрес, маршруты не могут быть установлены.")
        backend_id = backend.backend_id if backend is not None else None
        for prefix in self._tun_route_prefixes(backend_id):
            add_ipv4_route(
                prefix,
                interface_index=tun_interface.index,
                next_hop=tun_interface.ipv4,
                route_metric=1,
            )

    def _cleanup_tun_routes(self, state: RuntimeState) -> None:
        if not state.tun_route_prefixes or state.tun_interface_index is None or state.tun_interface_ipv4 is None:
            return
        for prefix in state.tun_route_prefixes:
            remove_ipv4_route(
                prefix,
                interface_index=state.tun_interface_index,
                next_hop=state.tun_interface_ipv4,
            )

    def _cleanup_tun_state(self, state: RuntimeState) -> None:
        if self._runtime_backend_id(state) == "amneziawg":
            self.amneziawg_network_integration.cleanup_runtime_state(state)
            return
        self._cleanup_tun_routes(state)

    def _handle_runtime_state_corruption(self, error: StorageCorruptionError) -> None:
        self._proxy_session = None
        try:
            self._reset_runtime_state()
        except Exception:
            pass
        state_file = getattr(error, "path", None)
        state_label = state_file.name if isinstance(state_file, Path) else "runtime_state.json"
        self.runtime_notice = RuntimeNotice(
            message=(
                f"Файл состояния '{state_label}' был поврежден и не восстановился автоматически. "
                "Активное подключение сброшено."
            ),
        )

    @staticmethod
    def _coerce_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        if isinstance(value, int):
            return bool(value)
        raise ValueError("Некорректное булево значение.")

    @staticmethod
    def _coerce_connection_mode(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Некорректный режим подключения.")
        normalized = value.strip().upper()
        if normalized in {"PROXY", "TUN"}:
            return normalized
        raise ValueError("Некорректный режим подключения.")

    def _runtime_backend_id(self, state: RuntimeState | None) -> str:
        if state is not None and state.backend_id:
            return state.backend_id
        return "xray"

    def _backend_by_id(self, backend_id: str | None) -> BaseVpnBackend | None:
        if backend_id in self.backends:
            return self.backends[backend_id]
        return None

    def _backend_engine_title(self, backend_id: str | None) -> str:
        backend = self._backend_by_id(backend_id)
        if backend is not None:
            return backend.engine_title
        if backend_id == "amneziawg":
            return "AmneziaWG"
        if backend_id == "singbox":
            return "sing-box"
        return "Xray"

    def _tun_interface_name(self, backend_id: str | None) -> str:
        backend = self._backend_by_id(backend_id)
        if backend is not None and backend.tun_interface_name:
            return backend.tun_interface_name
        return self.config_builder.TUN_INTERFACE_NAME

    def _tun_route_prefixes(self, backend_id: str | None) -> tuple[str, ...]:
        backend = self._backend_by_id(backend_id)
        if backend is not None and backend.tun_route_prefixes:
            return backend.tun_route_prefixes
        return tuple(self.config_builder.TUN_ROUTE_PREFIXES)

    def _backend_for_connection(self, profile: BackendConnectionProfile) -> BaseVpnBackend:
        return select_backend(self.backends, profile)

    def _backend_for_runtime_state(self, state: RuntimeState | None) -> BaseVpnBackend | None:
        return self._backend_by_id(self._runtime_backend_id(state))

    def _process_manager_for_backend(self, backend_id: str | None):
        backend = self._backend_by_id(backend_id)
        if backend is not None and backend.process_controller is not None:
            return backend.process_controller
        if backend_id in {None, "xray"}:
            return self.process_manager
        raise NotImplementedError(
            f"Backend '{backend_id}' пока не предоставляет process-controller."
        )

    def _process_manager_for_runtime_state(self, state: RuntimeState | None):
        return self._process_manager_for_backend(self._runtime_backend_id(state))

    def _process_manager_for_mode(self, mode: str | None, *, backend_id: str | None = None):
        return self._process_manager_for_backend(backend_id)

    def _ensure_runtime_ready(
        self,
        mode: str,
        *,
        server: ServerEntry | None = None,
        routing_profile: RoutingProfile | None = None,
    ) -> None:
        if server is None or routing_profile is None:
            if mode == "TUN":
                self.installer.ensure_xray_tun_runtime()
                return
            self.installer.ensure_xray()
            return
        connection_profile = BackendConnectionProfile(
            server=server,
            mode=mode,
            routing_profile=routing_profile,
        )
        backend = self._backend_for_connection(connection_profile)
        backend.ensure_runtime_ready(connection_profile)

    def _xray_version_status_label(self) -> str:
        if not XRAY_EXECUTABLE.exists():
            return "не установлен"
        version = self._xray_version_text()
        if version is None:
            return "версия не определена"
        return self._display_version(version)

    @staticmethod
    def _xray_version_text() -> str | None:
        version = XrayInstaller.get_xray_version(XRAY_EXECUTABLE)
        if version is None:
            return None
        return ".".join(str(part) for part in version)

    @staticmethod
    def _display_version(version: str | None) -> str:
        normalized = str(version or "").strip()
        if not normalized:
            return "-"
        if normalized.lower().startswith("v"):
            return normalized
        return f"v{normalized}"

    @staticmethod
    def _subscription_default_title(url: str) -> str:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host in SUBSCRIPTION_TITLE_BY_HOST:
            return SUBSCRIPTION_TITLE_BY_HOST[host]
        if host:
            return host
        return "Новая подписка"

    @staticmethod
    def _sorted_servers(servers: list[ServerEntry]) -> list[ServerEntry]:
        return sorted(
            servers,
            key=lambda item: (
                bool(item.extra.get("stale")),
                item.protocol.lower(),
                item.name.lower(),
                item.host.lower(),
                item.port,
            ),
        )

    @staticmethod
    def _emit_progress(callback: ProgressCallback | None, message: str) -> None:
        if callback is not None:
            callback(message)
