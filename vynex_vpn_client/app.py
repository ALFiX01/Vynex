from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import string
import sys
import threading
from typing import Callable
from urllib.parse import urlparse

import questionary
from questionary import Choice, Separator, Style
from questionary.prompts.select import (
    Application,
    DEFAULT_QUESTION_PREFIX,
    DEFAULT_SELECTED_POINTER,
    InquirerControl,
    KeyBindings,
    Keys,
    Question,
    common,
    merge_styles_default,
    utils,
)
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from wcwidth import wcswidth

if __package__ in {None, ""}:
    package_root = Path(__file__).resolve().parent.parent
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))

    from vynex_vpn_client.app_update import AppReleaseInfo, AppUpdateChecker
    from vynex_vpn_client.app_updater import AppSelfUpdater
    from vynex_vpn_client.amneziawg_network import AmneziaWgWindowsNetworkIntegration
    from vynex_vpn_client.amneziawg_process_manager import AmneziaWgProcessManager
    from vynex_vpn_client.backends import (
        AmneziaWgBackend,
        BackendConnectionProfile,
        BackendRuntimeRequest,
        BaseVpnBackend,
        XrayBackend,
        select_backend,
    )
    from vynex_vpn_client.config_builder import XrayConfigBuilder
    from vynex_vpn_client.constants import (
        AMNEZIAWG_EXECUTABLE,
        AMNEZIAWG_EXECUTABLE_FALLBACK,
        AMNEZIAWG_WINTUN_DLL,
        APP_NAME,
        APP_VERSION,
        GEOIP_PATH,
        GEOSITE_PATH,
        LOGO_FILE,
        SUBSCRIPTION_TITLE_BY_HOST,
        XRAY_EXECUTABLE,
    )
    from vynex_vpn_client.healthcheck import HealthcheckResult, XrayHealthChecker
    from vynex_vpn_client.core import XrayInstaller
    from vynex_vpn_client.models import (
        AppSettings,
        LocalProxyCredentials,
        ProxyRuntimeSession,
        RuntimeState,
        ServerEntry,
        SubscriptionEntry,
        utc_now_iso,
    )
    from vynex_vpn_client.parsers import is_supported_share_link, parse_server_entries, parse_share_link
    from vynex_vpn_client.process_manager import State as XrayState, XrayProcessManager
    from vynex_vpn_client.routing_profiles import RoutingProfileManager
    from vynex_vpn_client.storage import JsonStorage
    from vynex_vpn_client.subscriptions import SubscriptionManager
    from vynex_vpn_client.system_proxy import SystemProxyState, WindowsSystemProxyManager
    from vynex_vpn_client.utils import (
        WindowsInterfaceDetails,
        add_ipv4_route,
        clamp_port,
        generate_random_password,
        generate_random_username,
        get_active_ipv4_interface,
        get_interface_details,
        is_running_as_admin,
        pick_random_port,
        remove_ipv4_route,
        wait_for_port_listener,
        wait_for_tun_interface,
    )
else:
    from .app_update import AppReleaseInfo, AppUpdateChecker
    from .app_updater import AppSelfUpdater
    from .amneziawg_network import AmneziaWgWindowsNetworkIntegration
    from .amneziawg_process_manager import AmneziaWgProcessManager
    from .backends import (
        AmneziaWgBackend,
        BackendConnectionProfile,
        BackendRuntimeRequest,
        BaseVpnBackend,
        XrayBackend,
        select_backend,
    )
    from .config_builder import XrayConfigBuilder
    from .constants import (
        AMNEZIAWG_EXECUTABLE,
        AMNEZIAWG_EXECUTABLE_FALLBACK,
        AMNEZIAWG_WINTUN_DLL,
        APP_NAME,
        APP_VERSION,
        GEOIP_PATH,
        GEOSITE_PATH,
        LOGO_FILE,
        SUBSCRIPTION_TITLE_BY_HOST,
        XRAY_EXECUTABLE,
    )
    from .healthcheck import HealthcheckResult, XrayHealthChecker
    from .core import XrayInstaller
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
    from .process_manager import State as XrayState, XrayProcessManager
    from .routing_profiles import RoutingProfileManager
    from .storage import JsonStorage
    from .subscriptions import SubscriptionManager
    from .system_proxy import SystemProxyState, WindowsSystemProxyManager
    from .utils import (
        WindowsInterfaceDetails,
        add_ipv4_route,
        clamp_port,
        generate_random_password,
        generate_random_username,
        get_active_ipv4_interface,
        get_interface_details,
        is_running_as_admin,
        pick_random_port,
        remove_ipv4_route,
        wait_for_port_listener,
        wait_for_tun_interface,
    )

FLAG_EMOJI_PATTERN = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")


@dataclass(frozen=True)
class MenuAction:
    title: str
    handler: Callable[[], None]


class VynexVpnApp:
    def __init__(self) -> None:
        self.console = Console()
        self.storage = JsonStorage()
        self.installer = XrayInstaller()
        self.app_update_checker = AppUpdateChecker()
        self.app_updater = AppSelfUpdater()
        self.subscription_manager = SubscriptionManager(self.storage)
        self.routing_profiles = RoutingProfileManager()
        self.config_builder = XrayConfigBuilder()
        self.process_manager = XrayProcessManager(on_crash_callback=self._handle_xray_crash)
        self.amneziawg_process_manager = AmneziaWgProcessManager(
            on_crash_callback=lambda: self._handle_backend_crash("amneziawg")
        )
        self.amneziawg_network_integration = AmneziaWgWindowsNetworkIntegration()
        self.backends: dict[str, BaseVpnBackend] = {
            "xray": XrayBackend(
                installer=self.installer,
                config_builder=self.config_builder,
                process_manager=self.process_manager,
            ),
            "amneziawg": AmneziaWgBackend(
                installer=self.installer,
                process_manager=self.amneziawg_process_manager,
            ),
        }
        self.health_checker = XrayHealthChecker()
        self.system_proxy_manager = WindowsSystemProxyManager()
        self.app_release_info: AppReleaseInfo | None = self.app_update_checker.get_cached_release(max_age_seconds=None)
        self._app_update_thread: threading.Thread | None = None
        self._proxy_session: ProxyRuntimeSession | None = None
        self._runtime_notice: str | None = None
        self._should_exit = False
        self.logo = self._load_logo()

    def run(self) -> int:
        try:
            self._ensure_xray_ready()
            self._reconcile_runtime_state()
            self._schedule_app_update_check()
            self._startup_quick_import_flow()
            while True:
                self._render_screen()
                action = self._ask_main_menu()
                if action is None:
                    return 0
                if action.title == "Выход":
                    return 0
                action.handler()
                if self._should_exit:
                    return 0
        except KeyboardInterrupt:
            self.console.print("\n[bold yellow]Завершение по Ctrl+C[/bold yellow]")
            return 0
        finally:
            self._shutdown()

    def _ensure_xray_ready(self) -> None:
        try:
            self.installer.ensure_xray()
        except Exception as exc:  # noqa: BLE001
            self.console.print(
                Panel.fit(
                    f"{exc}\n\nПодключение через Xray может быть недоступно, но приложение продолжит работу.",
                    title="Предупреждение runtime",
                    border_style="yellow",
                )
            )
        if self.installer.warnings:
            self._show_installer_warnings()

    def _render_banner(self) -> None:
        title_markup = f"[bold cyan]{self.logo}[/bold cyan]" if self.logo else f"[bold cyan]{APP_NAME}[/bold cyan]"
        max_content_width = max(20, self.console.width - 2)
        title = Text.from_markup(title_markup)
        status = Text.from_markup(self._banner_status_line())
        status.pad_left(1)
        status.truncate(max_content_width, overflow="ellipsis")
        banner = Group(Text(""), title, Text(""), status)
        self.console.print(banner)

    def _render_screen(self) -> None:
        os.system("cls")
        self._render_banner()
        self.console.print()
        if self._runtime_notice:
            self.console.print(
                Panel.fit(
                    self._runtime_notice,
                    title="Подключение остановлено",
                    border_style="yellow",
                )
            )
            self.console.print()
            self._runtime_notice = None
        self.console.print()

    def _ask_main_menu(self) -> MenuAction | None:
        actions = [
            self._vpn_toggle_menu_action(),
            MenuAction("Сервера и подписки", self.server_subscription_flow),
            MenuAction("Компоненты", self.components_flow),
            MenuAction("Настройки", self.settings_flow),
            MenuAction("Статус", self.status_flow),
            MenuAction("Выход", lambda: None),
        ]
        update_action = self._app_update_menu_action()
        if update_action is not None:
            actions.insert(-1, update_action)
        selected_title = self._select(
            "Главное меню",
            choices=[action.title for action in actions],
            use_shortcuts=True
        ).ask()
        if selected_title is None:
            return None
        return next(action for action in actions if action.title == selected_title)

    def _vpn_toggle_menu_action(self) -> MenuAction:
        state = self._current_state()
        if state.is_running:
            return MenuAction("Отключиться", self.disconnect_flow)
        return MenuAction("Подключиться", self.connect_flow)

    def server_subscription_flow(self) -> None:
        while True:
            servers = self.storage.load_servers()
            subscriptions = self.storage.load_subscriptions()
            self._render_screen()
            selected_action = self._select(
                "Управление серверами и подписками",
                choices=[
                    f"Менеджер серверов: {len(servers)}",
                    f"Менеджер подписок: {len(subscriptions)}",
                    "Назад",
                ],
                use_shortcuts=True,
            ).ask()
            if selected_action in (None, "Назад"):
                return
            if selected_action.startswith("Менеджер серверов:"):
                self._show_servers_overview()
            elif selected_action.startswith("Менеджер подписок:"):
                self._show_subscriptions_overview()

    def _show_servers_overview(self) -> None:
        while True:
            servers = self._sorted_servers(self.storage.load_servers())
            state = self._current_state()
            active_server_id = state.server_id if state.is_running else None
            self._render_screen()
            if servers:
                self.console.print(self._servers_table(servers, active_server_id=active_server_id))
            else:
                self.console.print(
                    Panel.fit(
                        "Список серверов пуст. Используйте быстрый импорт: одна строка для сервера или URL подписки.",
                        title="Менеджер серверов",
                        border_style="yellow",
                    )
                )
            choices: list[Choice] = []
            for server in servers:
                choices.append(
                    Choice(
                        title=self._server_manager_choice_title(server, active_server_id=active_server_id),
                        value=server.id,
                    )
                )
            choices.append(Choice(title="Быстрый импорт: сервер / подписка", value="__add__"))
            choices.append(Choice(title="Назад", value="__back__"))
            selected_action = self._select(
                "Менеджер серверов",
                choices=choices,
                use_shortcuts=True,
            ).ask()
            if selected_action in (None, "__back__"):
                return
            if selected_action == "__add__":
                self.add_server_flow()
                continue
            self._server_details_flow(selected_action)

    def _show_subscriptions_overview(self) -> None:
        while True:
            subscriptions = self.storage.load_subscriptions()
            self._render_screen()
            if subscriptions:
                self.console.print(self._subscriptions_table(subscriptions))
            else:
                self.console.print(
                    Panel.fit(
                        "Список подписок пуст. Через быстрый импорт можно вставить URL подписки или одиночный сервер.",
                        title="Менеджер подписок",
                        border_style="yellow",
                    )
                )
            choices: list[Choice] = []
            for subscription in subscriptions:
                choices.append(
                    Choice(
                        title=self._subscription_choice_title(subscription),
                        value=subscription.id,
                    )
                )
            choices.append(Choice(title="Быстрый импорт: сервер / подписка", value="__add__"))
            if subscriptions:
                choices.append(Choice(title="Обновить все подписки", value="__refresh_all__"))
            choices.append(Choice(title="Назад", value="__back__"))
            selected_action = self._select(
                "Менеджер подписок",
                choices=choices,
                use_shortcuts=True,
            ).ask()
            if selected_action in (None, "__back__"):
                return
            if selected_action == "__add__":
                self.add_subscription_flow()
                continue
            if selected_action == "__refresh_all__":
                self.update_subscriptions_flow()
                continue
            self._subscription_details_flow(selected_action)

    def _startup_quick_import_flow(self) -> None:
        if self.storage.load_servers():
            return
        self._show_empty_servers_import_flow(title="Быстрый старт")

    def _show_empty_servers_import_flow(self, *, title: str) -> None:
        while not self.storage.load_servers():
            self._render_screen()
            self.console.print(self._empty_servers_panel(title=title))
            result = self._quick_import_prompt_flow(
                "Вставьте ссылку сервера, AWG-конфиг / путь к .conf, URL подписки или нажмите Enter для перехода в главное меню:"
            )
            if result == "cancelled":
                return

    def _empty_servers_panel(self, *, title: str) -> Panel:
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Поле", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(36, self.console.width - 32))
        table.add_row("Серверов", "0")
        table.add_row("Подписок", str(len(self.storage.load_subscriptions())))
        table.add_row("Что делать", "Вставить ссылку сервера, URL подписки или AWG-конфиг")
        table.add_row("Автоопределение", "Алгоритм сам определит сервер, AWG-конфиг, bundle или подписку")
        table.add_row("Подсказка", "Пустой ввод пропустит импорт и откроет главное меню")
        return Panel.fit(table, title=title, border_style="cyan")

    def connect_flow(self) -> None:
        servers = self.storage.load_servers()
        if not servers:
            self._show_empty_servers_import_flow(title="Нет серверов")
            servers = self.storage.load_servers()
            if not servers:
                return
        name_width = self._server_name_column_width(servers)
        protocol_width = max((self._display_width(server.protocol.upper()) for server in servers), default=5)
        self._render_screen()
        selected_server_id = self._select(
            "Выберите сервер",
            choices=[
                Choice(
                    title=self._server_choice_title(server.name, server.protocol.upper(), f"{server.host}:{server.port}", name_width, protocol_width),
                    value=server.id,
                )
                for server in servers
            ] + [Choice(title="Назад", value="__back__")],
            use_shortcuts=True
        ).ask()
        if not selected_server_id or selected_server_id == "__back__":
            return
        settings = self._validated_settings()
        mode = settings.connection_mode
        selected_server = next(server for server in servers if server.id == selected_server_id)
        routing_profile = self._get_active_routing_profile()
        if routing_profile is None:
            self._render_screen()
            self.console.print(
                Panel.fit(
                    "Не найден активный набор правил маршрутизации. Сначала выберите его в главном меню.",
                    title="Routing Profiles",
                    border_style="red",
                )
            )
            self._pause()
            return
        routing_label = routing_profile.name
        connection_profile = BackendConnectionProfile(
            server=selected_server,
            mode=mode,
            routing_profile=routing_profile,
        )
        backend = self._backend_for_connection(connection_profile)
        routing_display_label = self._routing_display_name(backend.backend_id, routing_label)
        proxy_session: ProxyRuntimeSession | None = None
        manager = self._process_manager_for_backend(backend.backend_id)
        pid: int | None = None
        helper_pid: int | None = None
        use_system_proxy = False
        system_proxy_applied = False
        previous_system_proxy: SystemProxyState | None = None
        outbound_interface: WindowsInterfaceDetails | None = None
        tun_interface: WindowsInterfaceDetails | None = None
        tun_interface_name_hint: str | None = None
        awg_expected_network = None
        awg_network_session = None
        try:
            self._show_connection_progress(
                self._ui_server_name(selected_server.name),
                routing_label,
                "Подготовка параметров подключения...",
            )
            backend.ensure_runtime_ready(connection_profile)
            if mode == "TUN":
                self._show_connection_progress(
                    self._ui_server_name(selected_server.name),
                    routing_label,
                    "Проверка требований TUN...",
                )
                outbound_interface = self._prepare_tun_prerequisites(backend=backend)
            self._disconnect_runtime(silent=True)
            manager.ensure_no_running_instances()
            use_system_proxy = mode == "PROXY" and settings.set_system_proxy
            if use_system_proxy:
                previous_system_proxy = self.system_proxy_manager.snapshot()
            if mode == "TUN":
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
            self._show_connection_progress(
                self._ui_server_name(selected_server.name),
                routing_label,
                "Запуск ядра подключения...",
            )
            pid = manager.start(config)
            self._show_connection_progress(
                self._ui_server_name(selected_server.name),
                routing_label,
                "Ожидание готовности ядра подключения...",
            )
            if mode == "TUN":
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
                    self._show_connection_progress(
                        self._ui_server_name(selected_server.name),
                        routing_label,
                        "Настройка маршрутов Windows...",
                    )
                    self._apply_tun_routes(tun_interface, backend=backend)
            elif proxy_session is not None:
                self._wait_for_local_proxy_ready(
                    pid=pid,
                    proxy_session=proxy_session,
                    mode=mode,
                    backend_id=backend.backend_id,
                )
            self._show_connection_progress(
                self._ui_server_name(selected_server.name),
                routing_label,
                " Проверка доступности сети...",
            )
            health_warning: str | None = None
            health_result = self._run_healthcheck(
                mode=mode,
                http_port=proxy_session.http_port if proxy_session is not None else None,
            )
            if not health_result.ok:
                health_warning = self._handle_failed_healthcheck(
                    mode=mode,
                    pid=pid,
                    manager=manager,
                    health_result=health_result,
                )
            if mode == "PROXY" and use_system_proxy:
                if proxy_session.http_port is None:
                    raise RuntimeError("Для системного proxy не определены локальные порты.")
                self._show_connection_progress(
                    self._ui_server_name(selected_server.name),
                    routing_label,
                    "Применение системного proxy Windows...",
                )
                self.system_proxy_manager.enable_proxy(http_port=proxy_session.http_port)
                system_proxy_applied = True
            tun_route_prefixes = (
                list(awg_network_session.route_prefixes)
                if awg_network_session is not None
                else list(self._tun_route_prefixes(backend.backend_id))
                if mode == "TUN"
                else []
            )
            state = RuntimeState(
                pid=pid,
                helper_pid=helper_pid,
                backend_id=backend.backend_id,
                mode=mode,
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
            self.storage.save_runtime_state(state)
            self._proxy_session = proxy_session
            detail_rows = [
                ("Сервер", self._ui_server_name(selected_server.name)),
                ("Протокол", selected_server.protocol.upper()),
                ("Режим", self._connection_mode_label(mode)),
                ("Маршрутизация", routing_display_label),
                ("PID", str(pid)),
                ("Системный proxy", "включен" if use_system_proxy else "не используется"),
            ]
            if mode == "TUN":
                detail_rows.append(("Ядро", backend.engine_name))
                if tun_interface is not None:
                    detail_rows.append(("TUN интерфейс", f"{tun_interface.alias} ({tun_interface.ipv4 or 'IPv4 не определен'})"))
                if outbound_interface is not None:
                    detail_rows.append(("Внешний интерфейс", outbound_interface.alias))
                route_prefixes = tuple(tun_route_prefixes)
                if route_prefixes:
                    detail_rows.append(("Маршруты", ", ".join(route_prefixes)))
                detail_rows.append(("IPv6", "может обходить TUN, если на системе активен IPv6"))
            else:
                detail_rows.append(("Ядро", backend.engine_name))
                detail_rows.append(("Локальный SOCKS5", "включен, учетные данные только в памяти"))
                detail_rows.append(("Локальный HTTP", "включен на случайном порту текущей сессии"))
            if health_result.checked_url:
                detail_rows.append(("Health-check", health_result.checked_url))
            elif health_warning is not None:
                detail_rows.append(("Health-check", "не подтвержден, соединение оставлено активным"))
            self._render_screen()
            self.console.print(
                Panel.fit(
                    self._key_value_group(detail_rows),
                    title="Подключение установлено с предупреждением" if health_warning else "Подключение установлено",
                    border_style="yellow" if health_warning else "green",
                )
            )
            if health_warning is not None:
                self._runtime_notice = health_warning
            self._pause()
        except Exception as exc:  # noqa: BLE001
            if mode == "TUN":
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
            self.storage.save_runtime_state(RuntimeState())
            self._render_screen()
            self._show_error("Ошибка подключения", exc)
            self._pause()

    def disconnect_flow(self) -> None:
        state = self._current_state()
        if not state.is_running:
            self._render_screen()
            self.console.print(Panel.fit("Активное подключение отсутствует.", border_style="yellow"))
            self._pause()
            return
        self._disconnect_runtime()

    def add_server_flow(self) -> None:
        self._quick_import_prompt_flow(
            "Вставьте ссылку сервера, AWG-конфиг / путь к .conf, URL подписки или список ссылок"
        )

    def _server_details_flow(self, server_id: str) -> None:
        while True:
            server = self.storage.get_server(server_id)
            if server is None:
                return
            parent_subscription = (
                self.storage.get_subscription(server.subscription_id)
                if server.subscription_id
                else None
            )
            self._render_screen()
            self.console.print(self._server_details_panel(server, parent_subscription=parent_subscription))
            choices = ["Удалить сервер"]
            if server.source == "manual":
                choices = ["Переименовать", "Удалить сервер"]
                if not server.is_amneziawg:
                    choices.insert(1, "Изменить ссылку")
            elif server.source == "subscription":
                choices = ["Отвязать от подписки", "Удалить сервер"]
                if parent_subscription is not None:
                    choices.insert(0, "Открыть подписку")
            choices.append("Назад")
            selected_action = self._select(
                "Действия с сервером",
                choices=choices,
                use_shortcuts=True,
            ).ask()
            if selected_action in (None, "Назад"):
                return
            try:
                if selected_action == "Переименовать":
                    self._rename_server_flow(server)
                elif selected_action == "Изменить ссылку":
                    self._edit_server_link_flow(server)
                elif selected_action == "Открыть подписку":
                    if parent_subscription is None:
                        raise ValueError("У сервера больше нет привязанной подписки.")
                    self._subscription_details_flow(parent_subscription.id)
                elif selected_action == "Отвязать от подписки":
                    self._detach_server_from_subscription_flow(server)
                elif selected_action == "Удалить сервер":
                    if self._delete_server_with_prompt(server):
                        return
            except Exception as exc:  # noqa: BLE001
                if self._is_user_cancelled(exc):
                    continue
                self._render_screen()
                self._show_error("Ошибка сервера", exc)
                self._pause()

    def delete_server_flow(self) -> None:
        servers = self._sorted_servers(self.storage.load_servers())
        if not servers:
            self._render_screen()
            self.console.print(Panel.fit("Список серверов пуст.", border_style="yellow"))
            self._pause()
            return

        name_width = max((self._display_width(self._ui_server_name(server.name)) for server in servers), default=12)
        protocol_width = max((self._display_width(server.protocol.upper()) for server in servers), default=5)
        choices = [
            Choice(
                title=(
                    f"{self._pad_display_width(self._truncate_display_width(self._ui_server_name(server.name), name_width), name_width)}"
                    f" | {self._pad_display_width(server.protocol.upper(), protocol_width)}"
                    f" | {server.host}:{server.port}"
                ),
                value=server.id,
            )
            for server in servers
        ] + [Choice(title="Назад", value="__back__")]

        self._render_screen()
        selected_server_id = self._select(
            "Выберите сервер для удаления",
            choices=choices,
            use_shortcuts=True,
        ).ask()
        if selected_server_id in (None, "__back__"):
            return

        server = next((item for item in servers if item.id == selected_server_id), None)
        if server is None:
            return
        self._delete_server_with_prompt(server)

    def _rename_server_flow(self, server: ServerEntry) -> None:
        default_name = self._ui_server_name(server.name)
        raw_name = questionary.text("Новое имя сервера", default=default_name).ask()
        if raw_name is None:
            raise ValueError("Переименование отменено.")
        new_name = raw_name.strip()
        if not new_name or new_name == default_name:
            return
        server.name = new_name
        if server.source == "subscription":
            server.extra["custom_name"] = True
        self.storage.upsert_server(server)
        self._show_server_saved("Сервер обновлен", server)

    def _edit_server_link_flow(self, server: ServerEntry) -> None:
        if server.source != "manual":
            raise ValueError("Ссылку можно менять только у ручных серверов.")
        if server.is_amneziawg:
            raise ValueError("AWG-профиль импортируется из .conf и не редактируется как share-link.")
        raw_link = questionary.text("Новая ссылка сервера", default=server.raw_link).ask()
        if raw_link is None:
            raise ValueError("Изменение ссылки отменено.")
        new_link = raw_link.strip()
        if not new_link or new_link == server.raw_link:
            return

        state = self._current_state()
        if state.is_running and state.server_id == server.id:
            self._render_screen()
            should_disconnect = questionary.confirm(
                "Этот сервер сейчас активен. Отключить текущее подключение и сохранить новую ссылку?",
                default=True,
            ).ask()
            if not should_disconnect:
                raise ValueError("Изменение ссылки отменено.")
            self._disconnect_runtime(silent=True)

        updated_server = parse_share_link(new_link)
        updated_server.id = server.id
        updated_server.created_at = server.created_at
        updated_server.name = server.name
        self.storage.upsert_server(updated_server)
        self._show_server_saved("Ссылка сервера обновлена", updated_server)

    def _detach_server_from_subscription_flow(self, server: ServerEntry) -> None:
        if server.source != "subscription":
            return
        subscription = self.storage.get_subscription(server.subscription_id) if server.subscription_id else None
        subscription_name = (
            self._ui_subscription_title(subscription.title) if subscription else "неизвестной подписки"
        )
        self._render_screen()
        should_detach = questionary.confirm(
            f"Отвязать сервер '{self._ui_server_name(server.name)}' от {subscription_name} и оставить как ручной?",
            default=True,
        ).ask()
        if not should_detach:
            return
        detached_server, parent_subscription = self.storage.detach_server_from_subscription(server.id)
        if detached_server is None:
            self._render_screen()
            self.console.print(Panel.fit("Сервер уже отсутствует в списке.", border_style="yellow"))
            self._pause()
            return
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_row("Сервер", self._ui_server_name(detached_server.name))
        table.add_row("Источник", "ручной")
        table.add_row(
            "Подписка",
            self._ui_subscription_title(parent_subscription.title) if parent_subscription else subscription_name,
        )
        self._render_screen()
        self.console.print(
            Panel.fit(
                table,
                title="Сервер отвязан",
                border_style="green",
            )
        )
        self._pause()

    def _delete_server_with_prompt(self, server: ServerEntry) -> bool:
        if server.source == "subscription":
            self._render_screen()
            selected_action = self._select(
                "Сервер импортирован из подписки",
                choices=[
                    "Удалить сервер из списка",
                    "Отвязать от подписки и оставить как ручной",
                    "Назад",
                ],
                use_shortcuts=True,
            ).ask()
            if selected_action in (None, "Назад"):
                return False
            if selected_action == "Отвязать от подписки и оставить как ручной":
                self._detach_server_from_subscription_flow(server)
                return False

        state = self._current_state()
        if state.is_running and state.server_id == server.id:
            self._render_screen()
            should_disconnect = questionary.confirm(
                "Этот сервер сейчас активен. Отключить текущее подключение и удалить сервер?",
                default=True,
            ).ask()
            if not should_disconnect:
                return False
            self._disconnect_runtime(silent=True)

        self._render_screen()
        prompt = f"Удалить сервер '{self._ui_server_name(server.name)}'?"
        if server.source == "subscription":
            prompt = (
                f"Удалить сервер '{self._ui_server_name(server.name)}' из списка?\n"
                "После следующего обновления подписки он может появиться снова."
            )
        should_delete = questionary.confirm(
            prompt,
            default=False,
        ).ask()
        if not should_delete:
            return False

        deleted_server = self.storage.delete_server(server.id)
        if deleted_server is None:
            self._render_screen()
            self.console.print(Panel.fit("Сервер уже отсутствует в списке.", border_style="yellow"))
            self._pause()
            return True

        self._render_screen()
        self.console.print(
            Panel.fit(
                f"{self._ui_server_name(deleted_server.name)}\nудален из списка серверов.",
                title="Сервер удален",
                border_style="green",
            )
        )
        self._pause()
        return True

    def add_subscription_flow(self) -> None:
        self._quick_import_prompt_flow(
            "Вставьте URL подписки, ссылку сервера, AWG-конфиг / путь к .conf или список ссылок"
        )

    def _quick_import_prompt_flow(self, prompt: str) -> str:
        raw_value = questionary.text(prompt).ask()
        if raw_value is None or not raw_value.strip():
            return "cancelled"
        try:
            self._handle_quick_import(raw_value)
        except Exception as exc:  # noqa: BLE001
            self._render_screen()
            self._show_error("Ошибка импорта", exc)
            self._pause()
            return "error"
        return "imported"

    def _handle_quick_import(self, raw_value: str) -> None:
        import_kind, payload = self._detect_import_target(raw_value)
        if import_kind == "server":
            server = self.storage.upsert_server(parse_share_link(payload))
            self._show_server_saved("Сервер сохранен", server)
            return
        if import_kind == "server_bundle":
            imported = self._import_server_links(payload)
            self._show_server_batch_saved("Импорт выполнен", imported, source_label="Ссылка")
            return

        normalized_url = payload
        existing = self.storage.get_subscription_by_url(normalized_url)
        default_title = existing.title if existing else self._subscription_default_title(normalized_url)
        subscription = existing or SubscriptionEntry.new(url=normalized_url, title=default_title)
        subscription.url = normalized_url
        subscription.title = default_title
        try:
            imported = self._refresh_subscription(subscription)
        except Exception as exc:  # noqa: BLE001
            if existing is not None:
                self._record_subscription_error(existing, exc)
            raise
        title = "Подписка обновлена" if existing is not None else "Подписка сохранена"
        self._show_subscription_refresh_success(title, subscription, imported)

    def _detect_import_target(self, raw_value: str) -> tuple[str, str | list[ServerEntry]]:
        normalized = raw_value.strip()
        if not normalized:
            raise ValueError("Пустой ввод.")
        if "\n" not in normalized and "\r" not in normalized and is_supported_share_link(normalized):
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

    def _import_server_links(self, links: list[ServerEntry]) -> list[ServerEntry]:
        imported: list[ServerEntry] = []
        imported_ids: set[str] = set()
        for server_entry in links:
            try:
                server = self.storage.upsert_server(server_entry)
            except ValueError:
                continue
            if server.id in imported_ids:
                continue
            imported.append(server)
            imported_ids.add(server.id)
        if not imported:
            raise ValueError("Не удалось импортировать ни один сервер из вставленных данных.")
        return imported

    def _subscription_details_flow(self, subscription_id: str) -> None:
        while True:
            subscription = self.storage.get_subscription(subscription_id)
            if subscription is None:
                return
            self._render_screen()
            self.console.print(self._subscription_details_panel(subscription))
            selected_action = self._select(
                "Действия с подпиской",
                choices=[
                    "Обновить",
                    "Изменить название",
                    "Изменить URL",
                    "Показать серверы подписки",
                    "Удалить подписку",
                    "Назад",
                ],
                use_shortcuts=True,
            ).ask()
            if selected_action in (None, "Назад"):
                return
            try:
                if selected_action == "Обновить":
                    imported = self._refresh_subscription(subscription)
                    self._show_subscription_refresh_success("Подписка обновлена", subscription, imported)
                elif selected_action == "Изменить название":
                    self._rename_subscription_flow(subscription)
                elif selected_action == "Изменить URL":
                    self._edit_subscription_url_flow(subscription)
                elif selected_action == "Показать серверы подписки":
                    self._show_subscription_servers(subscription)
                elif selected_action == "Удалить подписку":
                    if self._delete_subscription_flow(subscription):
                        return
            except Exception as exc:  # noqa: BLE001
                if self._is_user_cancelled(exc):
                    continue
                self._render_screen()
                self._show_error("Ошибка подписки", exc)
                self._pause()

    def _rename_subscription_flow(self, subscription: SubscriptionEntry) -> None:
        default_title = self._ui_subscription_title(subscription.title)
        raw_title = questionary.text(
            "Новое название подписки",
            default=default_title,
        ).ask()
        if raw_title is None:
            raise ValueError("Переименование отменено.")
        title = raw_title.strip()
        if not title or title == default_title:
            return
        subscription.title = title
        self.storage.upsert_subscription(subscription)
        self._render_screen()
        self.console.print(
            Panel.fit(
                f"Новое название: {self._ui_subscription_title(subscription.title)}",
                title="Подписка обновлена",
                border_style="green",
            )
        )
        self._pause()

    def _edit_subscription_url_flow(self, subscription: SubscriptionEntry) -> None:
        raw_url = questionary.text("Новый URL подписки", default=subscription.url).ask()
        if raw_url is None:
            raise ValueError("Изменение URL отменено.")
        new_url = raw_url.strip()
        if not new_url or new_url == subscription.url:
            return
        duplicate = next(
            (
                item
                for item in self.storage.load_subscriptions()
                if item.url == new_url and item.id != subscription.id
            ),
            None,
        )
        if duplicate is not None:
            raise ValueError(f"Подписка с этим URL уже существует: {duplicate.title}.")
        updated_subscription = SubscriptionEntry.from_dict(subscription.to_dict())
        updated_subscription.url = new_url
        try:
            imported = self._refresh_subscription(updated_subscription)
        except Exception as exc:  # noqa: BLE001
            self._record_subscription_error(subscription, exc)
            raise
        subscription.url = updated_subscription.url
        subscription.updated_at = updated_subscription.updated_at
        subscription.server_ids = updated_subscription.server_ids
        subscription.last_error = None
        subscription.last_error_at = None
        self._show_subscription_refresh_success("URL подписки обновлен", subscription, imported)

    def _show_subscription_servers(self, subscription: SubscriptionEntry) -> None:
        servers = self._subscription_servers(subscription.id)
        self._render_screen()
        if not servers:
            self.console.print(
                Panel.fit(
                    "У этой подписки сейчас нет привязанных серверов.",
                    title=self._ui_subscription_title(subscription.title),
                    border_style="yellow",
                )
            )
            self._pause()
            return
        table = Table(title=f"Серверы подписки: {self._ui_subscription_title(subscription.title)}")
        table.add_column("Имя", overflow="fold", max_width=max(20, self.console.width - 62))
        table.add_column("Протокол", no_wrap=True)
        table.add_column("Адрес", no_wrap=True)
        for server in servers:
            table.add_row(
                self._ui_server_name(server.name),
                server.protocol.upper(),
                f"{server.host}:{server.port}",
            )
        self.console.print(table)
        self._pause()

    def _delete_subscription_flow(self, subscription: SubscriptionEntry) -> bool:
        servers = self._subscription_servers(subscription.id)
        remove_action = self._select(
            "Как удалить подписку?",
            choices=[
                Choice(
                    title=f"Удалить подписку и ее серверы ({len(servers)})",
                    value="remove",
                ),
                Choice(
                    title=f"Удалить подписку, серверы оставить как ручные ({len(servers)})",
                    value="detach",
                ),
                Choice(title="Назад", value="back"),
            ],
            use_shortcuts=True,
        ).ask()
        if remove_action in (None, "back"):
            return False

        remove_servers = remove_action == "remove"
        state = self._current_state()
        subscription_server_ids = {server.id for server in servers}
        if remove_servers and state.is_running and state.server_id in subscription_server_ids:
            self._render_screen()
            should_disconnect = questionary.confirm(
                "Сервер этой подписки сейчас активен. Отключить текущее подключение и продолжить удаление?",
                default=True,
            ).ask()
            if not should_disconnect:
                return False
            self._disconnect_runtime(silent=True)

        action_text = "удалить подписку и ее серверы" if remove_servers else "удалить подписку и отвязать серверы"
        self._render_screen()
        should_delete = questionary.confirm(
            f"Подтвердите: {action_text} '{self._ui_subscription_title(subscription.title)}'?",
            default=False,
        ).ask()
        if not should_delete:
            return False

        deleted_subscription, affected_servers = self.storage.delete_subscription(
            subscription.id,
            remove_servers=remove_servers,
        )
        if deleted_subscription is None:
            self._render_screen()
            self.console.print(Panel.fit("Подписка уже отсутствует в списке.", border_style="yellow"))
            self._pause()
            return True

        result_label = "Удалено серверов" if remove_servers else "Серверов отвязано"
        table = Table(show_header=False, box=None)
        table.add_row("Подписка", self._ui_subscription_title(deleted_subscription.title))
        table.add_row(result_label, str(affected_servers))
        self._render_screen()
        self.console.print(
            Panel.fit(
                table,
                title="Подписка удалена",
                border_style="green",
            )
        )
        self._pause()
        return True

    def settings_flow(self) -> None:
        while True:
            settings = self._validated_settings(raise_on_error=False)
            active_routing_name = self._active_routing_profile_name()
            self._render_screen()
            selected_action = self._select(
                "Настройки",
                choices=[
                    f"Режим подключения: {self._connection_mode_label(settings.connection_mode)}",
                    "Системный proxy (PROXY): Вкл" if settings.set_system_proxy else "Системный proxy (PROXY): Выкл",
                    f"Набор маршрутизации: {active_routing_name}",
                    "Сбросить системный proxy",
                    "Назад",
                ],
                use_shortcuts=True,
            ).ask()
            if selected_action in (None, "Назад"):
                return
            try:
                if selected_action.startswith("Режим подключения:"):
                    selected_mode = self._select(
                        "Выберите режим подключения",
                        choices=[
                            Choice(title="PROXY (Для браузера и приложений)", value="PROXY"),
                            Choice(title="TUN (Для игр)", value="TUN"),
                        ],
                        use_shortcuts=True,
                    ).ask()
                    if selected_mode is None:
                        continue
                    settings.connection_mode = selected_mode
                    self.storage.save_settings(settings)
                    self._show_settings_saved(settings)
                elif selected_action.startswith("Системный proxy (PROXY):"):
                    system_proxy_answer = questionary.confirm(
                        "Устанавливать Proxy как системный proxy Windows при подключении?",
                        default=settings.set_system_proxy,
                    ).ask()
                    if system_proxy_answer is None:
                        continue
                    settings.set_system_proxy = bool(system_proxy_answer)
                    self.storage.save_settings(settings)
                    self._show_settings_saved(settings)
                elif selected_action.startswith("Набор маршрутизации:"):
                    self.routing_profile_flow()
                elif selected_action == "Сбросить системный proxy":
                    self._reset_system_proxy_flow()
            except Exception as exc:  # noqa: BLE001
                if self._is_user_cancelled(exc):
                    continue
                self._render_screen()
                self._show_error("Ошибка настроек", exc)
                self._pause()

    def components_flow(self) -> None:
        while True:
            self._render_screen()
            selected_action = self._select(
                "Компоненты",
                choices=[
                    self._component_choice_label("Xray-core", XRAY_EXECUTABLE),
                    self._amneziawg_component_label(),
                    self._component_choice_label("geoip.dat", GEOIP_PATH),
                    self._component_choice_label("geosite.dat", GEOSITE_PATH),
                    self._routing_profiles_component_label(),
                    "Обновить все компоненты",
                    "Назад",
                ],
                use_shortcuts=True,
            ).ask()
            if selected_action in (None, "Назад"):
                return
            try:
                if selected_action.startswith("Xray-core"):
                    self._prepare_component_update()
                    path = self.installer.update_xray()
                    self._show_component_result("Xray-core обновлен", path.name)
                elif selected_action.startswith("AmneziaWG"):
                    self._prepare_component_update()
                    self.installer.update_amneziawg()
                    self._show_component_result("AmneziaWG обновлен", "amneziawg.exe, awg.exe, wintun.dll")
                elif selected_action.startswith("geoip.dat"):
                    self._prepare_component_update()
                    path = self.installer.update_geoip()
                    self._show_component_result("geoip.dat обновлен", path.name)
                elif selected_action.startswith("geosite.dat"):
                    self._prepare_component_update()
                    path = self.installer.update_geosite()
                    self._show_component_result("geosite.dat обновлен", path.name)
                elif selected_action.startswith("Профили маршрутизации"):
                    profiles = self.routing_profiles.update_profiles()
                    self._show_component_result(
                        "Профили маршрутизации обновлены",
                        f"Профилей: {len(profiles)}",
                    )
                elif selected_action == "Обновить все компоненты":
                    self._prepare_component_update()
                    result = self.installer.update_all_components()
                    profiles = self.routing_profiles.update_profiles()
                    updated_components = list(result.keys())
                    updated_components.append(f"routing_profiles ({len(profiles)})")
                    self._show_component_result(
                        "Компоненты обновлены",
                        ", ".join(updated_components),
                    )
                if self.installer.warnings:
                    self._show_installer_warnings()
            except Exception as exc:  # noqa: BLE001
                self._render_screen()
                self._show_error("Ошибка обновления", exc)
                self._pause()

    def update_subscriptions_flow(self) -> None:
        subscriptions = self.storage.load_subscriptions()
        if not subscriptions:
            self._render_screen()
            self.console.print(Panel.fit("Список подписок пуст.", border_style="yellow"))
            self._pause()
            return
        success, failed = self.subscription_manager.refresh_all()
        self._render_screen()
        if success:
            table = Table(title="Обновленные подписки")
            table.add_column("Название")
            table.add_column("Серверов")
            for subscription, count in success:
                table.add_row(self._ui_subscription_title(subscription.title), str(count))
            self.console.print(table)
        if failed:
            table = Table(title="Ошибки обновления")
            table.add_column("Название")
            table.add_column("Ошибка")
            table.add_column("Что сделать", overflow="fold", max_width=max(28, self.console.width - 54))
            for subscription, error in failed:
                _, actions, _ = self._error_guidance("Ошибка подписки", error)
                table.add_row(self._ui_subscription_title(subscription.title), error, actions[0] if actions else "-")
            self.console.print(table)
        if not failed:
            self.console.print(Panel.fit("Все подписки обновлены.", border_style="green"))
        self._pause()

    def routing_profile_flow(self) -> None:
        profiles = self.routing_profiles.list_profiles()
        if not profiles:
            self._render_screen()
            self.console.print(
                Panel.fit(
                    "Не найдено ни одного профиля маршрутизации.",
                    title="Routing Profiles",
                    border_style="red",
                )
            )
            self._pause()
            return
        settings = self.storage.load_settings()
        active_profile_id = (
            settings.active_routing_profile_id
            if any(profile.profile_id == settings.active_routing_profile_id for profile in profiles)
            else None
        )
        self._render_screen()
        selected_profile_id = self._select(
            "Выберите набор правил маршрутизации",
            choices=[
                Choice(
                    title=self._routing_profile_choice_title(
                        profile.name,
                        profile.description,
                        profile.profile_id == settings.active_routing_profile_id,
                    ),
                    value=profile.profile_id,
                )
                for profile in profiles
            ],
            default=active_profile_id,
            style=self._routing_profile_select_style(),
            use_shortcuts=True,
        ).ask()
        if not selected_profile_id:
            return
        selected_profile = next(profile for profile in profiles if profile.profile_id == selected_profile_id)
        settings.active_routing_profile_id = selected_profile.profile_id
        self.storage.save_settings(settings)
        self._render_screen()
        self.console.print(
            Panel.fit(
                f"Активный набор: {selected_profile.name}\n{selected_profile.description}",
                title="Маршрутизация обновлена",
                border_style="green",
            )
        )
        self._pause()

    def status_flow(self) -> None:
        state = self._current_state()
        settings = self._validated_settings(raise_on_error=False)
        backend = self._backend_for_runtime_state(state)
        if not state.is_running:
            default_mode = settings.connection_mode
            engine_name = self._backend_engine_name(backend.backend_id if backend is not None else None)
            engine_state_label = self._runtime_engine_state_label(RuntimeState(mode=default_mode))
            table = Table(show_header=False, box=None)
            table.add_row("Версия", f"v{APP_VERSION}")
            if self._available_app_update() is not None:
                table.add_row("Обновление", self._available_app_update_label())
            table.add_row("Режим по умолчанию", self._connection_mode_label(settings.connection_mode))
            table.add_row("Ядро", "Не запущено")
            table.add_row(f"Состояние {engine_name}", engine_state_label)
            table.add_row(
                "Локальные порты",
                "выдаются случайно на время подключения" if settings.connection_mode == "PROXY" else "не используются",
            )
            table.add_row(
                "Системный proxy",
                "Авто" if settings.connection_mode == "PROXY" and settings.set_system_proxy else "Выкл",
            )
            table.add_row(
                "Маршрут",
                self._active_routing_profile_name(),
            )
            if settings.connection_mode == "TUN":
                table.add_row("TUN", "нужны права администратора, маршруты IPv4 ставятся автоматически")
            self._render_screen()
            self.console.print(
                Panel.fit(
                    table,
                    title="Статус",
                    border_style="yellow",
                )
            )
            self._pause()
            return
        server = self.storage.get_server(state.server_id) if state.server_id else None
        table = Table(show_header=False, box=None)
        table.add_row("Версия", f"v{APP_VERSION}")
        if self._available_app_update() is not None:
            table.add_row("Обновление", self._available_app_update_label())
        table.add_row("Процесс", self._runtime_status_text(state))
        table.add_row("PID", self._runtime_pid_label(state))
        table.add_row("Режим", state.mode or "-")
        table.add_row("Сервер", self._ui_server_name(server.name) if server else "-")
        table.add_row("Адрес", f"{server.host}:{server.port}" if server else "-")
        table.add_row("Протокол", server.protocol.upper() if server else "-")
        table.add_row("Старт", state.started_at or "-")
        table.add_row(
            "Routing",
            self._routing_display_name(
                state.backend_id,
                state.routing_profile_name or self._active_routing_profile_name(),
            ),
        )
        if state.mode == "PROXY":
            table.add_row("Ядро", self._backend_engine_name(state.backend_id))
            table.add_row(f"Состояние {self._backend_engine_name(state.backend_id)}", self._runtime_engine_state_label(state))
            table.add_row("Локальные порты", "скрыты")
            table.add_row("SOCKS5", "защищен аутентификацией и не публикуется")
            table.add_row("Системный proxy", "Да" if state.system_proxy_enabled else "Нет")
        elif state.mode == "TUN":
            table.add_row("Ядро", self._backend_engine_name(state.backend_id))
            table.add_row(f"Состояние {self._backend_engine_name(state.backend_id)}", self._runtime_engine_state_label(state))
            table.add_row("TUN", state.tun_interface_name or self._tun_interface_name(state.backend_id))
            table.add_row("IPv4 TUN", state.tun_interface_ipv4 or "-")
            table.add_row("Маршруты", ", ".join(state.tun_route_prefixes) if state.tun_route_prefixes else "-")
            table.add_row("Внешний интерфейс", state.outbound_interface_name or "-")
            table.add_row("Системный proxy", "Нет")
        self._render_screen()
        self.console.print(Panel.fit(table, title="Статус подключения", border_style="cyan"))
        self._pause()

    def _prompt_port(self, title: str, default: int) -> int:
        raw_value = questionary.text(title, default=str(default)).ask()
        if raw_value is None:
            raise ValueError("Ввод порта отменен.")
        try:
            return clamp_port(int(raw_value))
        except ValueError as exc:
            raise ValueError(f"Некорректное значение порта для '{title}'.") from exc

    def _current_state(self) -> RuntimeState:
        state = self.storage.load_runtime_state()
        state = self._sync_runtime_state_with_manager(state)
        manager = self._process_manager_for_runtime_state(state)
        main_dead = bool(
            state.pid
            and not self._backend_runtime_recovery_active(state)
            and not manager.is_running(state.pid)
        )
        # TODO: helper_pid is still tied to the legacy xray process manager.
        # Model backend-specific helper ownership once a non-xray backend starts using it.
        helper_dead = bool(state.helper_pid and not self.process_manager.is_running(state.helper_pid))
        if main_dead or helper_dead:
            self._proxy_session = None
            self._disconnect_runtime(silent=True)
            self.storage.save_runtime_state(RuntimeState())
            return RuntimeState()
        return state

    def _reconcile_runtime_state(self) -> None:
        self._current_state()

    def _check_app_update(self, *, force: bool = False) -> None:
        self.app_release_info = self.app_update_checker.check_latest_release(force=force)

    def _schedule_app_update_check(self) -> None:
        if self.app_update_checker.get_cached_release() is not None:
            return
        if self._app_update_thread is not None and self._app_update_thread.is_alive():
            return
        self._app_update_thread = threading.Thread(
            target=self._refresh_app_update_info_in_background,
            name="vynex-app-update-check",
            daemon=True,
        )
        self._app_update_thread.start()

    def _refresh_app_update_info_in_background(self) -> None:
        try:
            self._check_app_update()
        except Exception:
            pass

    def _available_app_update(self) -> AppReleaseInfo | None:
        if self.app_release_info is None:
            return None
        if not self.app_release_info.is_update_available:
            return None
        if not self.app_release_info.latest_version:
            return None
        return self.app_release_info

    def _available_app_update_label(self) -> str:
        release_info = self._available_app_update()
        if release_info is None or not release_info.latest_version:
            return "-"
        return self._display_version(release_info.latest_version)

    def _app_update_menu_action(self) -> MenuAction | None:
        release_info = self._available_app_update()
        if release_info is None:
            return None
        return MenuAction(
            f"Обновить приложение до {self._available_app_update_label()}",
            self.open_app_update_page_flow,
        )

    def _disconnect_runtime(self, *, silent: bool = False) -> None:
        state = self.storage.load_runtime_state()
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
        self.storage.save_runtime_state(RuntimeState())
        if not silent:
            self.console.print(Panel.fit("Подключение остановлено.", border_style="green"))

    def _shutdown(self) -> None:
        self._disconnect_runtime(silent=True)

    def _run_healthcheck(self, *, mode: str, http_port: int | None) -> HealthcheckResult:
        self._render_screen()
        self.console.print("[cyan]Проверка доступности сети...[/cyan]")
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

    def _restore_system_proxy(self, state: RuntimeState) -> None:
        if not state.system_proxy_enabled:
            return
        previous_state = SystemProxyState.from_dict(state.previous_system_proxy)
        self.system_proxy_manager.restore(previous_state)

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
        self.storage.save_runtime_state(state)
        return state

    def _handle_xray_crash(self) -> None:
        self._handle_backend_crash("xray")

    def _handle_backend_crash(self, backend_id: str) -> None:
        state = self.storage.load_runtime_state()
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
        self.storage.save_runtime_state(RuntimeState())
        engine_title = self._backend_engine_title(backend_id)
        if mode == "TUN":
            self._runtime_notice = (
                f"{engine_title} завершился и не смог восстановиться автоматически. "
                "Сетевое состояние туннеля очищено, подключение сброшено."
            )
            return
        self._runtime_notice = (
            f"{engine_title} завершился и не смог восстановиться автоматически. "
            "Подключение сброшено, системный proxy возвращен в прежнее состояние."
        )

    def _get_active_routing_profile(self):
        settings = self.storage.load_settings()
        profile = self.routing_profiles.get_profile(settings.active_routing_profile_id)
        if profile is not None:
            return profile
        fallback = self.routing_profiles.get_profile("default")
        if fallback is not None:
            settings.active_routing_profile_id = fallback.profile_id
            self.storage.save_settings(settings)
        return fallback

    def _active_routing_profile_name(self) -> str:
        profile = self._get_active_routing_profile()
        return profile.name if profile else "-"

    @staticmethod
    def _routing_display_name(backend_id: str | None, routing_name: str | None) -> str:
        if backend_id == "amneziawg":
            return "из AWG-конфига"
        normalized = str(routing_name or "").strip()
        return normalized or "-"

    def _banner_status_line(self) -> str:
        state = self._current_state()
        settings = self._validated_settings(raise_on_error=False)
        routing_source = self._routing_display_name(
            state.backend_id if state.is_running else None,
            state.routing_profile_name if state.is_running and state.routing_profile_name else self._active_routing_profile_name(),
        )
        routing_name = escape(self._shorten_text(routing_source, 28))
        update_suffix = ""
        if self._available_app_update() is not None:
            update_suffix = f" | [bold yellow]Доступна новая версия:[/bold yellow] {escape(self._available_app_update_label())}"
        if state.is_running:
            server = self.storage.get_server(state.server_id) if state.server_id else None
            server_name = escape(
                self._shorten_text(
                    self._ui_server_name(server.name) if server else "сервер недоступен",
                    32,
                )
            )
            return (
                f"[bold]Статус:[/bold] {self._runtime_status_markup(state)}"
                f" | [bold]Сервер:[/bold] {server_name}"
                f" | [bold]Маршрут:[/bold] {routing_name}"
                f"{update_suffix}"
            )
        proxy_mode = "авто" if settings.set_system_proxy else "выкл"
        return (
            "[bold]Статус:[/bold] [yellow]Не подключено[/yellow]"
            f" | [bold]Маршрут:[/bold] {routing_name}"
            f"{update_suffix}"
        )

    def _banner_border_style(self) -> str:
        return "green" if self._current_state().is_running else "cyan"

    def _runtime_status_markup(self, state: RuntimeState) -> str:
        if not state.is_running:
            return "[yellow]Не подключено[/yellow]"
        backend_title = self._backend_engine_title(self._runtime_backend_id(state))
        backend_state = self._backend_process_state(state)
        if backend_state == XrayState.STARTING:
            return f"[cyan]{backend_title} запускается[/cyan]"
        if backend_state == XrayState.CRASHED:
            if not self._backend_supports_crash_recovery(state):
                return f"[red]{backend_title} завершился с ошибкой[/red]"
            return f"[yellow]{backend_title} восстанавливается[/yellow]"
        if backend_state == XrayState.STOPPING:
            return "[yellow]Подключение останавливается[/yellow]"
        return "[green]Подключено[/green]"

    def _runtime_status_text(self, state: RuntimeState) -> str:
        if not state.is_running:
            return "Не подключено"
        backend_title = self._backend_engine_title(self._runtime_backend_id(state))
        backend_state = self._backend_process_state(state)
        if backend_state == XrayState.STARTING:
            return f"Запуск {backend_title}"
        if backend_state == XrayState.CRASHED:
            if not self._backend_supports_crash_recovery(state):
                return f"Сбой {backend_title}"
            return f"Восстановление {backend_title}"
        if backend_state == XrayState.STOPPING:
            return "Остановка подключения"
        return "Запущен"

    def _runtime_engine_state_label(self, state: RuntimeState) -> str:
        backend_state = self._backend_process_state(state)
        if backend_state == XrayState.STARTING:
            return "Запускается"
        if backend_state == XrayState.RUNNING:
            return "Работает"
        if backend_state == XrayState.STOPPING:
            return "Останавливается"
        if backend_state == XrayState.CRASHED:
            if not self._backend_supports_crash_recovery(state):
                return "Сбой"
            return "Сбой, идет восстановление"
        return "Работает" if state.is_running else "Остановлено"

    def _runtime_pid_label(self, state: RuntimeState) -> str:
        if str(state.mode or "").upper() in {"PROXY", "TUN"}:
            current_pid = self._process_manager_for_runtime_state(state).pid
            if current_pid is not None:
                return str(current_pid)
            backend_state = self._backend_process_state(state)
            if backend_state == XrayState.STARTING:
                return "запуск"
            if backend_state == XrayState.CRASHED and self._backend_supports_crash_recovery(state):
                return "перезапуск"
        return str(state.pid) if state.pid is not None else "-"

    def _backend_runtime_recovery_active(self, state: RuntimeState) -> bool:
        if str(state.mode or "").upper() not in {"PROXY", "TUN"} or not state.is_running:
            return False
        backend_state = self._backend_process_state(state)
        if backend_state in {XrayState.STARTING, XrayState.STOPPING}:
            return True
        return backend_state == XrayState.CRASHED and self._backend_supports_crash_recovery(state)

    def _xray_runtime_recovery_active(self, state: RuntimeState) -> bool:
        return self._backend_runtime_recovery_active(state)

    def _backend_supports_crash_recovery(self, state: RuntimeState | None) -> bool:
        backend = self._backend_for_runtime_state(state)
        return bool(backend is not None and backend.supports_crash_recovery)

    def _backend_process_state(self, state: RuntimeState | None = None) -> XrayState:
        manager = self._process_manager_for_runtime_state(state)
        manager_state = manager.state
        if (
            state is not None
            and state.is_running
            and manager_state == XrayState.STOPPED
            and state.pid is not None
            and manager.is_running(state.pid)
        ):
            return XrayState.RUNNING
        return manager_state

    def _proxy_engine_state(self, state: RuntimeState | None = None) -> XrayState:
        return self._backend_process_state(state)

    def _show_error(self, title: str, error: Exception | str) -> None:
        summary, actions, details = self._error_guidance(title, error)
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Поле", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(36, self.console.width - 32))
        table.add_row("Что случилось", summary)
        if actions:
            table.add_row(
                "Что сделать",
                "\n".join(f"{index}. {action}" for index, action in enumerate(actions, start=1)),
            )
        if details and details != summary:
            table.add_row("Детали", details)
        self.console.print(Panel.fit(table, title=title, border_style="red"))

    def open_app_update_page_flow(self) -> None:
        try:
            cached_release = self.app_release_info
            self._show_app_update_status(cached_release, step="Проверка релиза", status="Запрашивается latest release из GitHub.")
            self._check_app_update(force=True)
            release_info = self._available_app_update()
            if release_info is None:
                if self.app_release_info is not None and self.app_release_info.error:
                    raise RuntimeError(self.app_release_info.error)
                self._render_screen()
                self.console.print(Panel.fit("Новая версия не найдена.", border_style="yellow"))
                self._pause()
                return
            self._render_screen()
            self.console.print(
                Panel.fit(
                    self._app_update_details_table(release_info),
                    title="Доступно обновление приложения",
                    border_style="cyan",
                )
            )
            if not self.app_updater.can_self_update():
                self.console.print(
                    Panel.fit(
                        "Текущий запуск работает не из packaged Windows .exe сборки.\n"
                        "Self-update недоступен, но проверка обновлений продолжит работать.\n"
                        f"Скачайте новую версию вручную: {release_info.release_url or '-'}",
                        title="Self-update недоступен",
                        border_style="yellow",
                    )
                )
                self._pause()
                return

            runtime_state = self._current_state()
            confirmation_message = "Скачать и установить обновление сейчас?"
            if runtime_state.is_running:
                confirmation_message = (
                    "Скачать и установить обновление сейчас?\n"
                    "Активное подключение будет остановлено, приложение перезапустится автоматически."
                )
            should_update = questionary.confirm(
                confirmation_message,
                default=True,
            ).ask()
            if not should_update:
                return

            self._show_app_update_status(release_info, step="Загрузка", status="Скачивается новый exe во временную директорию.")
            download = self.app_updater.download_release(release_info)

            self._show_app_update_status(
                release_info,
                step="Подготовка обновления",
                status=f"Готовится staging-файл {download.staged_executable.name} и helper script.",
            )
            plan = self.app_updater.prepare_apply_plan(download, current_pid=os.getpid())
            self.app_updater.write_helper_script(plan)

            self._show_app_update_status(
                release_info,
                step="Завершение приложения",
                status="Клиент остановит runtime-процессы и передаст управление helper script.",
            )
            self._shutdown()

            self._show_app_update_status(
                release_info,
                step="Запуск новой версии",
                status="Приложение будет перезапущено автоматически.",
            )
            self.app_updater.launch_helper(plan)
            self._should_exit = True
        except Exception as exc:  # noqa: BLE001
            self._render_screen()
            self._show_error("Ошибка обновления приложения", exc)
            self._pause()

    def _app_update_details_table(self, release_info: AppReleaseInfo | None, *, step: str | None = None, status: str | None = None) -> Table:
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Параметр", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(32, self.console.width - 34))
        table.add_row("Текущая", self._display_version(APP_VERSION))
        if release_info is not None:
            table.add_row("Новая", self._display_version(release_info.latest_version) if release_info.latest_version else "-")
        else:
            table.add_row("Новая", "-")
        if release_info is not None:
            table.add_row("Файл", release_info.asset_name or "-")
            table.add_row("Размер", self._format_file_size(release_info.asset_size))
            table.add_row("Опубликован", release_info.published_at or "-")
            table.add_row("Релиз", release_info.release_url or "-")
            if release_info.release_notes:
                notes_preview = release_info.release_notes.replace("\r", " ").replace("\n", " ")
                table.add_row("Описание", self._shorten_text(notes_preview, 240))
            if release_info.error:
                table.add_row("Ошибка", release_info.error)
        if step:
            table.add_row("Этап", step)
        if status:
            table.add_row("Статус", status)
        return table

    def _show_app_update_status(
        self,
        release_info: AppReleaseInfo | None,
        *,
        step: str,
        status: str,
        border_style: str = "cyan",
    ) -> None:
        self._render_screen()
        self.console.print(
            Panel.fit(
                self._app_update_details_table(release_info, step=step, status=status),
                title="Обновление приложения",
                border_style=border_style,
            )
        )

    @staticmethod
    def _format_file_size(size_bytes: int | None) -> str:
        if size_bytes is None or size_bytes < 0:
            return "-"
        units = ("B", "KB", "MB", "GB")
        size = float(size_bytes)
        unit_index = 0
        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1
        if unit_index == 0:
            return f"{int(size)} {units[unit_index]}"
        return f"{size:.1f} {units[unit_index]}"

    @staticmethod
    def _display_version(version: str | None) -> str:
        normalized = str(version or "").strip()
        if not normalized:
            return "-"
        if normalized.lower().startswith("v"):
            return normalized
        return f"v{normalized}"

    def _error_guidance(self, title: str, error: Exception | str) -> tuple[str, list[str], str]:
        details = self._error_text(error)
        normalized = details.lower()

        if title == "Ошибка подключения":
            if "от имени администратора" in normalized or "повышенными правами" in normalized:
                return (
                    "Для TUN режима клиент должен быть запущен с правами администратора.",
                    [
                        "Закройте приложение и запустите `VynexVPNClient.exe` через 'Запуск от имени администратора'.",
                        "После перезапуска повторите подключение в режиме TUN.",
                    ],
                    details,
                )
            if "wintun.dll" in normalized:
                return (
                    "В runtime Xray отсутствует драйверный компонент `wintun.dll` для Windows TUN.",
                    [
                        "Откройте 'Компоненты' и обновите Xray-core.",
                        "Если используете портативную сборку, проверьте, что антивирус не удалил `wintun.dll`.",
                    ],
                    details,
                )
            if "не поддерживает tun режим" in normalized:
                return (
                    "Текущая версия Xray-core слишком старая для TUN режима.",
                    [
                        "Откройте 'Компоненты' и обновите Xray-core до актуальной версии.",
                        "После обновления повторите подключение.",
                    ],
                    details,
                )
            if "не удалось определить активный ipv4 интерфейс" in normalized:
                return (
                    "Клиент не нашел рабочий сетевой интерфейс Windows, через который Xray должен выходить в интернет.",
                    [
                        "Проверьте, что у системы есть активное подключение и default route.",
                        "Если сеть только что переключалась, подождите несколько секунд и повторите попытку.",
                    ],
                    details,
                )
            if "порт" in normalized and "занят" in normalized:
                return (
                    "Локальный proxy-порт уже используется другим приложением.",
                    [
                        "Клиент выбирает порты автоматически, поэтому обычно достаточно повторить подключение.",
                        "Если ошибка повторяется, закройте приложение, которое уже слушает локальный proxy-порт.",
                    ],
                    details,
                )
            if "health-check" in normalized:
                return (
                    "Ядро подключения запустилось, но клиент не смог подтвердить доступ в сеть через него.",
                    [
                        "Попробуйте другой сервер из списка.",
                        "Если проблема повторяется, обновите 'Компоненты' и проверьте доступ в интернет.",
                        "При необходимости временно отключите системный proxy в 'Настройки' и попробуйте снова.",
                    ],
                    details,
                )
            if "не удалось добавить маршрут" in normalized:
                return (
                    "Xray поднял TUN интерфейс, но Windows не приняла маршруты для перехвата трафика.",
                    [
                        "Убедитесь, что приложение запущено от имени администратора.",
                        "Проверьте, не блокирует ли изменение маршрутов другой VPN-клиент или корпоративная политика Windows.",
                    ],
                    details,
                )
            if "tun интерфейс" in normalized and ("ipv4" in normalized or "инициализации" in normalized):
                return (
                    "Xray не смог вовремя поднять TUN интерфейс Windows.",
                    [
                        "Проверьте, что в runtime присутствует `wintun.dll` и используется свежий Xray-core.",
                        "Если в системе уже работает другой VPN/TUN-драйвер, временно отключите его и повторите попытку.",
                    ],
                    details,
                )
            if "локальные proxy-inbound" in normalized or "локальный proxy" in normalized:
                return (
                    "Xray не успел открыть локальные proxy-порты для текущей сессии.",
                    [
                        "Повторите подключение: клиент выберет новые случайные порты.",
                        "Если ошибка повторяется, откройте 'Компоненты' и обновите Xray-core.",
                        "Если Xray завершился сразу, используйте детали ниже для диагностики конкретной ошибки Xray.",
                    ],
                    details,
                )
            if "уже запущен xray.exe" in normalized:
                return (
                    "Обнаружен другой экземпляр Xray, который мешает запуску клиента.",
                    [
                        "Остановите внешнюю копию xray.exe или завершите прошлое подключение.",
                        "После этого повторите подключение.",
                    ],
                    details,
                )
            if "xray.exe не найден" in normalized:
                return (
                    "Исполняемый файл Xray отсутствует в runtime-каталоге.",
                    [
                        "Откройте 'Компоненты' и обновите Xray-core.",
                        "Если используете .exe сборку, убедитесь, что файлы клиента не удалены антивирусом.",
                    ],
                    details,
                )
            if "amneziawg executable не найден" in normalized or "awg.exe" in normalized:
                return (
                    "Исполняемый файл AmneziaWG отсутствует в runtime-каталоге.",
                    [
                        "Проверьте, что бинарник AmneziaWG установлен рядом с runtime клиента.",
                        "Если используете кастомный путь, убедитесь, что он указывает на рабочий `.exe` файл.",
                    ],
                    details,
                )
            if "конфликтующий интерфейс amneziawg" in normalized:
                return (
                    "Windows уже содержит интерфейс AmneziaWG с тем же именем туннеля.",
                    [
                        "Остановите другой экземпляр AmneziaWG/WireGuard с этим туннелем.",
                        "Если это след от прошлого падения, перезагрузите систему или вручную удалите конфликтующий туннель и повторите попытку.",
                    ],
                    details,
                )
            if "windows не применила ожидаемые ipv4-адреса" in normalized:
                return (
                    "AmneziaWG запустился, но Windows не назначила туннелю адреса из профиля.",
                    [
                        "Проверьте корректность `Address` в AWG-конфиге.",
                        "Если в системе есть другой VPN-клиент, временно отключите его и повторите попытку.",
                    ],
                    details,
                )
            if "windows не применила маршруты allowedips" in normalized:
                return (
                    "AmneziaWG поднял интерфейс, но маршруты full-tunnel/split-tunnel из AllowedIPs не появились в Windows.",
                    [
                        "Проверьте, что приложение запущено от имени администратора.",
                        "Убедитесь, что другой VPN или корпоративные политики Windows не блокируют изменение маршрутов.",
                    ],
                    details,
                )
            if "windows не применила dns" in normalized:
                return (
                    "AmneziaWG поднял интерфейс, но DNS серверы из профиля не были применены в Windows.",
                    [
                        "Проверьте поле `DNS` в AWG-конфиге.",
                        "Если у вас включены сторонние DNS-клиенты или endpoint security, временно отключите их и повторите попытку.",
                    ],
                    details,
                )
            if "невалидный runtime config amneziawg" in normalized:
                return (
                    "Клиент сформировал неполный или поврежденный runtime config для AmneziaWG.",
                    [
                        "Переимпортируйте AWG-конфиг и повторите попытку.",
                        "Если ошибка повторяется, проверьте, что исходный `.conf` не поврежден и содержит корректные секции Interface/Peer.",
                    ],
                    details,
                )
            if "доступ запрещен при запуске amneziawg" in normalized:
                return (
                    "Windows не разрешила запуск backend-процесса AmneziaWG.",
                    [
                        "Запустите клиент от имени администратора.",
                        "Проверьте, не блокирует ли бинарник Windows Defender, SmartScreen или корпоративная политика.",
                    ],
                    details,
                )
            if "превышено время ожидания запуска amneziawg" in normalized:
                return (
                    "AmneziaWG стартовал, но не успел поднять интерфейс за отведенное время.",
                    [
                        "Проверьте, что приложение запущено от имени администратора.",
                        "Если в системе уже есть конфликтующий WireGuard/AmneziaWG туннель, остановите его и повторите попытку.",
                    ],
                    details,
                )
            if "amneziawg" in normalized and "wintun" in normalized:
                return (
                    "AmneziaWG не смог создать или инициализировать Wintun интерфейс Windows.",
                    [
                        "Убедитесь, что используется штатный Windows бинарник AmneziaWG и он не заблокирован Defender/SmartScreen.",
                        "Если в системе уже работает другой TUN/Wintun-клиент, остановите его и повторите попытку.",
                    ],
                    details,
                )
            if "amneziawg backend неожиданно завершился" in normalized:
                return (
                    "AmneziaWG завершился во время запуска или сразу после него.",
                    [
                        "Проверьте детали ниже: они содержат последние строки stdout/stderr backend'а.",
                        "Убедитесь, что исходный AWG-конфиг корректен и совместим с используемым Windows backend.",
                    ],
                    details,
                )
            if "code not found in geosite.dat" in normalized or "failed to load geosite" in normalized:
                return (
                    "Активный профиль маршрутизации использует код, которого нет в текущем geosite.dat.",
                    [
                        "Переключитесь на другой профиль маршрутизации или обновите профиль правил.",
                        "Откройте 'Компоненты' и обновите geosite.dat или выберите 'Обновить все компоненты', затем повторите подключение.",
                    ],
                    details,
                )
            if any(
                token in normalized
                for token in (
                    "failed to load config files",
                    "failed to build routing configuration",
                    "invalid field rule",
                    "failed to load geoip",
                    "code not found in geoip.dat",
                    "geoip.dat",
                    "geosite.dat",
                )
            ):
                return (
                    "Xray не смог загрузить конфигурацию или routing-данные клиента.",
                    [
                        "Откройте 'Компоненты' и выберите 'Обновить все компоненты'.",
                        "Если ошибка связана с кастомным профилем маршрутизации, временно переключитесь на базовый профиль и повторите подключение.",
                    ],
                    details,
                )

        if title == "Ошибка парсинга":
            if "поддерживаются только ссылки" in normalized:
                return (
                    "Вставлена ссылка неподдерживаемого формата.",
                    [
                        "Используйте ссылку формата vless://, vmess://, trojan://, ss:// или hy2://.",
                        "Если это URL подписки, добавляйте его через пункт 'Добавить подписку (URL)'.",
                    ],
                    details,
                )
            if any(protocol in normalized for protocol in ("vmess", "vless", "trojan", "shadowsocks", "hy2", "hysteria2")):
                return (
                    "Ссылка сервера повреждена или заполнена не полностью.",
                    [
                        "Проверьте, что ссылка скопирована целиком без лишних символов.",
                        "Если ссылка пришла из подписки или мессенджера, попробуйте скопировать ее заново.",
                    ],
                    details,
                )

        if title == "Ошибка импорта":
            if "не удалось определить формат" in normalized:
                return (
                    "Клиент не смог понять, что именно было вставлено.",
                    [
                        "Для одиночного сервера используйте ссылку vless://, vmess://, trojan://, ss:// или hy2://.",
                        "Для подписки используйте URL формата http:// или https://.",
                        "Также можно вставить Base64-подписку или список share-ссылок построчно.",
                    ],
                    details,
                )
            if "не удалось загрузить подписку" in normalized:
                return (
                    "Клиент не смог скачать содержимое подписки.",
                    [
                        "Проверьте URL подписки и доступ в интернет.",
                        "Если ссылка временно недоступна, повторите попытку позже.",
                    ],
                    details,
                )
            if "не содержит поддерживаемых ссылок" in normalized:
                return (
                    "Подписка загрузилась, но не содержит ссылок, которые поддерживает клиент.",
                    [
                        "Убедитесь, что источник содержит vless://, vmess://, trojan://, ss:// или hy2:// ссылки.",
                        "Если провайдер выдает другой формат, такой импорт этим клиентом не поддерживается.",
                    ],
                    details,
                )
            if "не удалось импортировать ни один сервер" in normalized:
                return (
                    "Во вставленных данных не нашлось ни одного корректного сервера.",
                    [
                        "Проверьте, что ссылка или список скопированы полностью.",
                        "Если это подписка, попробуйте вставить ее URL вместо содержимого.",
                    ],
                    details,
                )

        if title == "Ошибка подписки":
            if "не удалось загрузить подписку" in normalized:
                return (
                    "Клиент не смог скачать содержимое подписки.",
                    [
                        "Проверьте URL подписки и доступ в интернет.",
                        "Если ссылка временно недоступна, повторите попытку позже.",
                    ],
                    details,
                )
            if "не содержит поддерживаемых ссылок" in normalized:
                return (
                    "Подписка загрузилась, но не содержит ссылок, которые поддерживает клиент.",
                    [
                        "Убедитесь, что подписка содержит vless://, vmess://, trojan://, ss:// или hy2:// ссылки.",
                        "Если провайдер выдает другой формат, потребуется другая схема импорта.",
                    ],
                    details,
                )
            if "не удалось импортировать ни один сервер" in normalized:
                return (
                    "Подписка открылась, но ни одна запись не была успешно импортирована.",
                    [
                        "Проверьте, что данные подписки не повреждены и не пусты.",
                        "Попробуйте обновить подписку позже или использовать другой источник.",
                    ],
                    details,
                )

        if title == "Ошибка настроек":
            if "не должны совпадать" in normalized:
                return (
                    "Локальные proxy-параметры настроены с конфликтом.",
                    [
                        "Сбросьте настройки клиента и сохраните их заново.",
                        "При повторении проблемы удалите поврежденный файл настроек клиента.",
                    ],
                    details,
                )
            if "некоррект" in normalized and "порт" in normalized:
                return (
                    "В настройках клиента обнаружено поврежденное значение локального порта.",
                    [
                        "Клиент теперь выбирает порты автоматически, поэтому достаточно пересохранить настройки.",
                        "Если ошибка повторяется, удалите поврежденный файл настроек и запустите клиент заново.",
                    ],
                    details,
                )

        if title == "Ошибка обновления":
            if "сначала отключите активное подключение" in normalized:
                return (
                    "Обновление компонентов нельзя выполнить при активном подключении.",
                    [
                        "Согласитесь на остановку текущего подключения или отключитесь вручную.",
                        "После этого повторите обновление.",
                    ],
                    details,
                )
            if "не удалось скачать" in normalized or "не удалось получить информацию" in normalized:
                return (
                    "Клиент не смог скачать или проверить обновление компонента.",
                    [
                        "Проверьте доступ в интернет и повторите попытку.",
                        "Если проблема сохраняется, обновите компонент вручную и перезапустите клиент.",
                    ],
                    details,
                )

        if title == "Ошибка сервера":
            if "поддерживаются только ссылки" in normalized:
                return (
                    "Вставлена ссылка неподдерживаемого формата.",
                    [
                        "Используйте ссылку формата vless://, vmess://, trojan://, ss:// или hy2://.",
                        "Для сервера из подписки сначала отвяжите его, а потом редактируйте как ручной.",
                    ],
                    details,
                )
            if any(protocol in normalized for protocol in ("vmess", "vless", "trojan", "shadowsocks", "hy2", "hysteria2")):
                return (
                    "Ссылка сервера повреждена или заполнена не полностью.",
                    [
                        "Проверьте, что ссылка скопирована целиком без лишних символов.",
                        "Если это ручной сервер, попробуйте вставить исходную ссылку заново.",
                    ],
                    details,
                )
            if "такой ссылкой уже существует" in normalized:
                return (
                    "Сервер с такой ссылкой уже есть в списке.",
                    [
                        "Откройте существующую запись и используйте ее вместо создания дубля.",
                        "Если нужно сохранить оба варианта, сначала отвяжите или удалите конфликтующую запись.",
                    ],
                    details,
                )
            if "только у ручных серверов" in normalized:
                return (
                    "Это действие доступно только для ручных серверов.",
                    [
                        "Для сервера из подписки сначала используйте действие 'Отвязать от подписки'.",
                        "После этого сервер можно будет редактировать как обычный ручной.",
                    ],
                    details,
                )
            if "привязанной подписки" in normalized:
                return (
                    "У этого сервера уже нет доступной подписки-источника.",
                    [
                        "Откройте менеджер подписок и проверьте, существует ли исходная подписка.",
                        "Если сервер нужен отдельно, отвяжите его от подписки и оставьте как ручной.",
                    ],
                    details,
                )

        if title == "Ошибка обновления приложения":
            if "packaged windows build" in normalized or "windows .exe сборки" in normalized:
                return (
                    "Этот запуск работает не из собранного Windows exe, поэтому self-update отключен.",
                    [
                        "Используйте собранный `VynexVPNClient.exe`, если нужен self-update внутри приложения.",
                        "Проверка новых релизов GitHub продолжит работать и в режиме запуска из исходников.",
                    ],
                    details,
                )
            if "latest release отсутствует exe-asset" in normalized or "не найден exe-asset" in normalized:
                return (
                    "GitHub release найден, но в нем нет exe-файла приложения для self-update.",
                    [
                        "Проверьте assets у последнего релиза репозитория.",
                        "Переопубликуйте релиз с `VynexVPNClient.exe` или другим `.exe` asset.",
                    ],
                    details,
                )
            if "превышено время ожидания" in normalized or "timeout" in normalized:
                return (
                    "Не удалось завершить сетевой запрос к GitHub вовремя.",
                    [
                        "Проверьте подключение к интернету и повторите обновление.",
                        "Если GitHub временно недоступен, повторите попытку позже.",
                    ],
                    details,
                )
            if "размер скачанного файла не совпадает" in normalized or "размер сохраненного файла не совпадает" in normalized:
                return (
                    "Скачанный exe поврежден или загрузился не полностью.",
                    [
                        "Повторите обновление: staging-файл будет скачан заново.",
                        "Если проблема повторяется, проверьте asset последнего релиза GitHub.",
                    ],
                    details,
                )
            if "helper script" in normalized:
                return (
                    "Клиент не смог подготовить или запустить внешний helper script обновления.",
                    [
                        "Проверьте права записи в `%LOCALAPPDATA%\\VynexVPNClient\\updates`.",
                        "Если ошибка повторяется, скачайте новую версию вручную из GitHub Releases.",
                    ],
                    details,
                )

        return (
            "Операция завершилась с ошибкой.",
            [
                "Проверьте детали ниже и повторите действие.",
                "Если ошибка повторяется, измените входные данные или перезапустите клиент.",
            ],
            details,
        )

    @staticmethod
    def _error_text(error: Exception | str) -> str:
        message = str(error).strip()
        return message or "Неизвестная ошибка."

    @staticmethod
    def _is_user_cancelled(error: Exception | str) -> bool:
        return "отмен" in str(error).lower()

    @staticmethod
    def _shorten_text(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        if limit <= 3:
            return value[:limit]
        return f"{value[: limit - 3]}..."

    def _server_name_column_width(self, servers) -> int:
        reserved_width = 24
        max_name_width = max((self._display_width(self._ui_server_name(server.name)) for server in servers), default=12)
        available_width = max(18, self.console.width - reserved_width)
        return min(max_name_width, available_width)

    def _server_choice_title(
        self,
        server_name: str,
        protocol: str,
        address: str,
        name_width: int,
        protocol_width: int,
    ) -> str:
        safe_server_name = self._ui_server_name(server_name)
        aligned_name = self._pad_display_width(self._truncate_display_width(safe_server_name, name_width), name_width)
        aligned_protocol = self._pad_display_width(protocol, protocol_width)
        return f"{aligned_name} | {aligned_protocol} | {address}"

    def _show_server_saved(self, title: str, server: ServerEntry) -> None:
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Параметр", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(30, self.console.width - 32))
        table.add_row("Имя", self._ui_server_name(server.name))
        table.add_row("Протокол", server.protocol.upper())
        table.add_row("Адрес", f"{server.host}:{server.port}")
        table.add_row("Источник", self._server_source_label(server))
        self._render_screen()
        self.console.print(Panel.fit(table, title=title, border_style="green"))
        self._pause()

    def _show_server_batch_saved(self, title: str, servers: list[ServerEntry], *, source_label: str) -> None:
        protocols = self.subscription_manager.summarize_protocols(servers)
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Параметр", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(30, self.console.width - 32))
        table.add_row("Импортировано", str(len(servers)))
        table.add_row(
            "Протоколы",
            ", ".join(f"{name.upper()}: {count}" for name, count in protocols.items()) or "-",
        )
        table.add_row("Источник", source_label)
        self._render_screen()
        self.console.print(Panel.fit(table, title=title, border_style="green"))
        self._pause()

    def _servers_table(self, servers: list[ServerEntry], *, active_server_id: str | None) -> Table:
        name_width = max(20, self.console.width - 80)
        table = Table(title="Серверы")
        table.add_column("Имя", no_wrap=True, overflow="ellipsis", max_width=name_width)
        table.add_column("Протокол", no_wrap=True)
        table.add_column("Адрес", no_wrap=True)
        table.add_column("Источник", no_wrap=True)
        table.add_column("Статус", no_wrap=True)
        for server in servers:
            table.add_row(
                self._truncate_display_width(self._ui_server_name(server.name), name_width),
                server.protocol.upper(),
                f"{server.host}:{server.port}",
                self._server_source_label(server),
                self._server_status_label(server, active_server_id=active_server_id),
            )
        return table

    def _server_manager_choice_title(self, server: ServerEntry, *, active_server_id: str | None) -> str:
        return self._truncate_display_width(
            self._ui_server_name(server.name),
            max(18, self.console.width - 28),
        )

    def _server_details_panel(
        self,
        server: ServerEntry,
        *,
        parent_subscription: SubscriptionEntry | None = None,
    ) -> Panel:
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Параметр", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(34, self.console.width - 34))
        table.add_row("Имя", self._ui_server_name(server.name))
        table.add_row("Протокол", server.protocol.upper())
        table.add_row("Адрес", f"{server.host}:{server.port}")
        if server.is_amneziawg:
            table.add_row("Профиль", "AmneziaWG")
        table.add_row("Источник", self._server_source_label(server))
        table.add_row("Статус", self._server_status_label(server, active_server_id=self._current_state().server_id))
        table.add_row("Создан", self._shorten_text(server.created_at, 19))
        if parent_subscription is not None:
            table.add_row("Подписка", self._ui_subscription_title(parent_subscription.title))
        if server.source == "subscription":
            note = "После обновления подписки параметры сервера могут измениться."
            if server.extra.get("stale"):
                note = "Сервер исчез из последней версии подписки и сохранен как устаревший."
            table.add_row("Примечание", note)
        return Panel.fit(
            table,
            title=f"Сервер: {self._ui_server_name(server.name)}",
            border_style="cyan" if server.source == "manual" else "yellow",
        )

    def _server_source_label(self, server: ServerEntry) -> str:
        if server.source == "manual":
            return "ручной"
        if server.source == "subscription":
            subscription = self.storage.get_subscription(server.subscription_id) if server.subscription_id else None
            if subscription is not None:
                return f"подписка ({self._shorten_text(self._ui_subscription_title(subscription.title), 18)})"
            return "подписка"
        return server.source

    def _server_source_short_label(self, server: ServerEntry) -> str:
        if server.source == "manual":
            return "ручной"
        if server.source == "subscription":
            return "подписка"
        return server.source

    @staticmethod
    def _server_status_label(server: ServerEntry, *, active_server_id: str | None) -> str:
        if server.id == active_server_id:
            return "Активен"
        if server.extra.get("stale"):
            return "Устарел"
        return "Ожидание"

    @staticmethod
    def _sorted_servers(servers: list[ServerEntry]) -> list[ServerEntry]:
        return sorted(
            servers,
            key=lambda item: (
                item.source != "manual",
                bool(item.extra.get("stale")),
                item.protocol.lower(),
                item.name.lower(),
                item.host.lower(),
                item.port,
            ),
        )

    def _refresh_subscription(self, subscription: SubscriptionEntry) -> list[ServerEntry]:
        imported = self.subscription_manager.import_subscription(subscription)
        subscription.updated_at = utc_now_iso()
        subscription.last_error = None
        subscription.last_error_at = None
        self.storage.upsert_subscription(subscription)
        return imported

    def _record_subscription_error(self, subscription: SubscriptionEntry, error: Exception | str) -> None:
        subscription.last_error = self._error_text(error)
        subscription.last_error_at = utc_now_iso()
        self.storage.upsert_subscription(subscription)

    def _show_subscription_refresh_success(
        self,
        title: str,
        subscription: SubscriptionEntry,
        imported: list[ServerEntry],
    ) -> None:
        protocols = self.subscription_manager.summarize_protocols(imported)
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Параметр", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(30, self.console.width - 32))
        table.add_row("Подписка", self._ui_subscription_title(subscription.title))
        table.add_row("Серверов", str(len(imported)))
        table.add_row(
            "Протоколы",
            ", ".join(f"{name.upper()}: {count}" for name, count in protocols.items()) or "-",
        )
        self._render_screen()
        self.console.print(Panel.fit(table, title=title, border_style="green"))
        self._pause()

    def _subscriptions_table(self, subscriptions: list[SubscriptionEntry]) -> Table:
        table = Table(title="Подписки")
        table.add_column("Название", overflow="fold", max_width=max(20, self.console.width - 72))
        table.add_column("Серверов", no_wrap=True)
        table.add_column("Обновлено", no_wrap=True)
        table.add_column("Статус", no_wrap=True)
        for subscription in subscriptions:
            table.add_row(
                self._layout_safe_text(subscription.title),
                str(len(self._subscription_servers(subscription.id))),
                self._shorten_text(subscription.updated_at, 19),
                self._subscription_status_label(subscription),
            )
        return table

    def _subscription_choice_title(self, subscription: SubscriptionEntry) -> str:
        return self._truncate_display_width(
            self._layout_safe_text(subscription.title),
            max(18, self.console.width - 10),
        )

    def _subscription_status_label(self, subscription: SubscriptionEntry) -> str:
        if subscription.last_error:
            return "Ошибка"
        if not self._subscription_servers(subscription.id):
            return "Пусто"
        return "OK"

    def _subscription_details_panel(self, subscription: SubscriptionEntry) -> Panel:
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Параметр", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(34, self.console.width - 34))
        table.add_row("Название", self._ui_subscription_title(subscription.title))
        table.add_row("URL", subscription.url)
        table.add_row("Серверов", str(len(self._subscription_servers(subscription.id))))
        table.add_row("Обновлено", self._shorten_text(subscription.updated_at, 19))
        table.add_row("Статус", self._subscription_status_label(subscription))
        if subscription.last_error:
            table.add_row("Последняя ошибка", subscription.last_error)
            table.add_row("Когда", self._shorten_text(subscription.last_error_at or "-", 19))
        return Panel.fit(
            table,
            title=f"Подписка: {self._ui_subscription_title(subscription.title)}",
            border_style="yellow" if subscription.last_error else "cyan",
        )

    def _subscription_servers(self, subscription_id: str) -> list[ServerEntry]:
        servers = [
            server
            for server in self.storage.load_servers()
            if server.source == "subscription" and server.subscription_id == subscription_id
        ]
        return sorted(servers, key=lambda item: item.name.lower())

    @staticmethod
    def _display_width(value: str) -> int:
        return max(wcswidth(value), len(value), 0)

    @classmethod
    def _pad_display_width(cls, value: str, target_width: int) -> str:
        padding = max(0, target_width - cls._display_width(value))
        return f"{value}{' ' * padding}"

    @classmethod
    def _truncate_display_width(cls, value: str, max_width: int) -> str:
        if cls._display_width(value) <= max_width:
            return value
        if max_width <= 3:
            return value[:max_width]
        result = ""
        for char in value:
            candidate = f"{result}{char}"
            if cls._display_width(candidate) > max_width - 3:
                break
            result = candidate
        return f"{result}..."

    def _key_value_group(self, rows: list[tuple[str, str]], *, gap: int = 2) -> Group:
        key_width = max((self._display_width(key) for key, _ in rows), default=0)
        lines: list[Text] = []
        for key, value in rows:
            safe_value = self._layout_safe_text(str(value))
            line = Text()
            line.append(self._pad_display_width(key, key_width), style="bold")
            line.append(" " * gap)
            line.append(safe_value)
            lines.append(line)
        return Group(*lines)

    @staticmethod
    def _layout_safe_text(value: str) -> str:
        def replace_flag(match: re.Match[str]) -> str:
            pair = match.group(0)
            country_code = "".join(chr(ord(char) - 0x1F1E6 + ord("A")) for char in pair)
            return f"[{country_code}]"

        sanitized = FLAG_EMOJI_PATTERN.sub(replace_flag, value)
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_chars: list[str] = []
        for char in sanitized:
            try:
                char.encode(encoding)
            except UnicodeEncodeError:
                safe_chars.append(f"[U+{ord(char):04X}]")
            else:
                safe_chars.append(char)
        return "".join(safe_chars)

    @classmethod
    def _ui_server_name(cls, value: str) -> str:
        return cls._layout_safe_text(value)

    @classmethod
    def _ui_subscription_title(cls, value: str) -> str:
        return cls._layout_safe_text(value)

    def _show_settings_saved(self, settings: AppSettings) -> None:
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Параметр", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(28, self.console.width - 34))
        table.add_row("Режим подключения", self._connection_mode_label(settings.connection_mode))
        table.add_row(
            "Локальные порты",
            "случайные и скрыты для каждой сессии" if settings.connection_mode == "PROXY" else "не используются",
        )
        table.add_row(
            "SOCKS5",
            "включается только с аутентификацией" if settings.connection_mode == "PROXY" else "не используется",
        )
        table.add_row(
            "Системный proxy",
            (
                "включать автоматически"
                if settings.connection_mode == "PROXY" and settings.set_system_proxy
                else "не изменять"
            ),
        )
        table.add_row(
            "Маршрутизация",
            self._active_routing_profile_name(),
        )
        if settings.connection_mode == "TUN":
            table.add_row("TUN", "используется xray, требуется запуск от администратора")

        self._render_screen()
        self.console.print(
            Panel.fit(
                table,
                title="Настройки сохранены",
                border_style="green",
            )
        )
        self._pause()

    def _show_connection_progress(self, server_name: str, routing_name: str, step: str) -> None:
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Параметр", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(34, self.console.width - 34))
        table.add_row("Сервер", server_name)
        table.add_row("Маршрутизация", routing_name)
        table.add_row("Этап", step)
        self._render_screen()
        self.console.print(
            Panel.fit(
                table,
                title="Идет подключение",
                border_style="cyan",
            )
        )

    def _show_component_result(self, title: str, component_name: str) -> None:
        self._render_screen()
        self.console.print(
            Panel.fit(
                f"{component_name}\nобновлено успешно.",
                title=title,
                border_style="green",
            )
        )
        self._pause()

    def _prepare_component_update(self) -> None:
        state = self._current_state()
        if not state.is_running:
            return
        runtime_label = "Xray"
        should_disconnect = questionary.confirm(
            f"Сейчас запущен {runtime_label}. Остановить подключение для обновления компонента?",
            default=True,
        ).ask()
        if not should_disconnect:
            raise RuntimeError("Обновление отменено: сначала отключите активное подключение.")
        self._disconnect_runtime(silent=True)

    def _show_installer_warnings(self) -> None:
        self.console.print(
            Panel.fit(
                "\n".join(self.installer.warnings),
                title="Предупреждение",
                border_style="yellow",
            )
        )
        self._pause()

    def _reset_system_proxy_flow(self) -> None:
        should_reset = questionary.confirm(
            "Сбросить системный proxy Windows прямо сейчас?",
            default=False,
        ).ask()
        if not should_reset:
            return
        self.system_proxy_manager.disable_proxy()
        state = self.storage.load_runtime_state()
        if state.system_proxy_enabled:
            state.system_proxy_enabled = False
            state.previous_system_proxy = None
            self.storage.save_runtime_state(state)
        self._render_screen()
        self.console.print(
            Panel.fit(
                "Системный proxy Windows отключен.",
                title="Proxy сброшен",
                border_style="green",
            )
        )
        self._pause()

    @staticmethod
    def _component_choice_label(label: str, path: Path) -> str:
        status = "есть" if path.exists() else "отсутствует"
        return f"{label}: {status}"

    @staticmethod
    def _amneziawg_component_label() -> str:
        status = (
            "есть"
            if AMNEZIAWG_EXECUTABLE.exists()
            and AMNEZIAWG_EXECUTABLE_FALLBACK.exists()
            and AMNEZIAWG_WINTUN_DLL.exists()
            else "отсутствует"
        )
        return f"AmneziaWG: {status}"

    def _routing_profiles_component_label(self) -> str:
        profiles_count = len(self.routing_profiles.list_profiles())
        return f"Профили маршрутизации: {profiles_count} шт."

    def _validated_settings(self, *, raise_on_error: bool = True) -> AppSettings:
        settings = self.storage.load_settings()
        try:
            settings.set_system_proxy = self._coerce_bool(settings.set_system_proxy)
            settings.connection_mode = self._coerce_connection_mode(settings.connection_mode)
            return settings
        except (TypeError, ValueError) as exc:
            if raise_on_error:
                raise ValueError("Параметры proxy в настройках некорректны.") from exc
            fallback = AppSettings(active_routing_profile_id=settings.active_routing_profile_id)
            return fallback

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
        if backend is not None and backend.backend_id == "amneziawg":
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
        if wait_for_tun_interface(tun_interface_name, timeout=12.0):
            details = get_interface_details(
                tun_interface_name,
                allow_link_local=True,
            )
            if details is not None and details.ipv4:
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

    @staticmethod
    def _connection_mode_label(value: str) -> str:
        return (
            "TUN (игры и весь трафик)"
            if str(value).upper() == "TUN"
            else "PROXY (браузер и приложения)"
        )

    def _backend_by_id(self, backend_id: str | None) -> BaseVpnBackend | None:
        backends = getattr(self, "backends", None)
        if isinstance(backends, dict) and backend_id in backends:
            return backends[backend_id]
        return None

    def _runtime_backend_id(self, state: RuntimeState | None) -> str:
        if state is not None and state.backend_id:
            return state.backend_id
        return "xray"

    def _backend_engine_name(self, backend_id: str | None) -> str:
        backend = self._backend_by_id(backend_id)
        if backend is not None:
            return backend.engine_name
        if backend_id == "amneziawg":
            return "amneziawg"
        return "xray"

    def _backend_engine_title(self, backend_id: str | None) -> str:
        backend = self._backend_by_id(backend_id)
        if backend is not None:
            return backend.engine_title
        if backend_id == "amneziawg":
            return "AmneziaWG"
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
        backends = getattr(self, "backends", None)
        if isinstance(backends, dict) and backends:
            # TODO: move backend selection to explicit profile metadata once AWG import is implemented.
            return select_backend(backends, profile)
        return XrayBackend(
            installer=getattr(self, "installer", None),
            config_builder=self.config_builder,
            process_manager=self.process_manager,
        )

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
        routing_profile=None,
    ) -> None:
        if server is None or routing_profile is None:
            if mode == "TUN":
                self.installer.ensure_xray_tun_runtime()
                return
            self.installer.ensure_xray()
            return
        backend = self._backend_for_connection(
            BackendConnectionProfile(
                server=server,
                mode=mode,
                routing_profile=routing_profile,
            )
        )
        backend.ensure_runtime_ready(
            BackendConnectionProfile(
                server=server,
                mode=mode,
                routing_profile=routing_profile,
            )
        )

    @staticmethod
    def _pause() -> None:
        questionary.press_any_key_to_continue("Нажмите любую клавишу, чтобы вернуться в меню").ask()

    @staticmethod
    def _choice_title(choice: object) -> str | None:
        if isinstance(choice, Separator):
            return None
        if isinstance(choice, Choice):
            if isinstance(choice.title, str):
                return choice.title
            if isinstance(choice.title, list):
                return "".join(token[1] for token in choice.title)
            return None
        if isinstance(choice, str):
            return choice
        return None

    @staticmethod
    def _style_terminal_choice(choice: object) -> object:
        choice_title = VynexVpnApp._choice_title(choice)
        if choice_title not in {"Назад", "Выход"}:
            return choice
        formatted_title = [("class:terminal-danger", choice_title)]
        if isinstance(choice, str):
            return Choice(title=formatted_title, value=choice)
        if isinstance(choice, Choice):
            return Choice(
                title=formatted_title,
                value=choice.value,
                disabled=choice.disabled,
                checked=choice.checked,
                shortcut_key=choice.shortcut_key,
                description=choice.description,
            )
        return choice

    @staticmethod
    def _with_terminal_choice_spacing(choices: object) -> list[object]:
        source_choices = list(choices)
        formatted_choices: list[object] = []
        if source_choices and not isinstance(source_choices[0], Separator):
            formatted_choices.append(Separator(" "))
        for choice in source_choices:
            styled_choice = VynexVpnApp._style_terminal_choice(choice)
            choice_title = VynexVpnApp._choice_title(styled_choice)
            if (
                formatted_choices
                and choice_title in {"Назад", "Выход"}
                and not isinstance(formatted_choices[-1], Separator)
            ):
                formatted_choices.append(Separator(" "))
            formatted_choices.append(styled_choice)
        return formatted_choices

    @staticmethod
    def _menu_select_style(base_style: Style | None = None) -> Style:
        style_rules = list(base_style.style_rules) if base_style is not None else []
        style_rules.append(("terminal-danger", "fg:ansired bold"))
        return Style(style_rules)

    @staticmethod
    def _back_choice_value(choices: object) -> object | None:
        for choice in choices:
            if VynexVpnApp._choice_title(choice) != "Назад":
                continue
            if isinstance(choice, Choice):
                return choice.value
            if isinstance(choice, str):
                return choice
        return None

    @staticmethod
    def _select_with_escape_back(
        message: str,
        choices,
        default=None,
        qmark: str = DEFAULT_QUESTION_PREFIX,
        pointer: str | None = DEFAULT_SELECTED_POINTER,
        style: Style | None = None,
        use_shortcuts: bool = False,
        use_arrow_keys: bool = True,
        use_indicator: bool = False,
        use_jk_keys: bool = True,
        use_emacs_keys: bool = True,
        use_search_filter: bool = False,
        show_selected: bool = False,
        show_description: bool = True,
        instruction: str | None = None,
        **kwargs,
    ) -> Question:
        if not (use_arrow_keys or use_shortcuts or use_jk_keys or use_emacs_keys):
            raise ValueError(
                "Some option to move the selection is required. Arrow keys, j/k keys, emacs keys, or shortcuts."
            )
        if use_jk_keys and use_search_filter:
            raise ValueError(
                "Cannot use j/k keys with prefix filter search, since j/k can be part of the prefix."
            )
        if use_shortcuts and use_jk_keys:
            if any(getattr(choice, "shortcut_key", "") in ["j", "k"] for choice in choices):
                raise ValueError(
                    "A choice is trying to register j/k as a shortcut key when they are in use as arrow keys disable one or the other."
                )
        if choices is None or len(choices) == 0:
            raise ValueError("A list of choices needs to be provided.")
        if use_shortcuts:
            real_len_of_choices = sum(1 for choice in choices if not isinstance(choice, Separator))
            if real_len_of_choices > len(InquirerControl.SHORTCUT_KEYS):
                raise ValueError(
                    "A list with shortcuts supports a maximum of {} choices as this is the maximum number of keyboard shortcuts that are available. You provided {} choices!".format(
                        len(InquirerControl.SHORTCUT_KEYS), real_len_of_choices
                    )
                )

        merged_style = merge_styles_default([style])
        ic = InquirerControl(
            choices,
            default,
            pointer=pointer,
            use_indicator=use_indicator,
            use_shortcuts=use_shortcuts,
            show_selected=show_selected,
            show_description=show_description,
            use_arrow_keys=use_arrow_keys,
            initial_choice=default,
        )
        back_choice_value = VynexVpnApp._back_choice_value(choices)

        def get_prompt_tokens():
            tokens = [("class:qmark", qmark), ("class:question", f" {message} ")]
            if ic.is_answered:
                current_title = ic.get_pointed_at().title
                if isinstance(current_title, list):
                    tokens.append(("class:answer", "".join(token[1] for token in current_title)))
                else:
                    tokens.append(("class:answer", current_title))
            else:
                if instruction:
                    instruction_msg = instruction
                elif use_shortcuts and use_arrow_keys:
                    instruction_msg = f"(Use shortcuts or arrow keys{', type to filter' if use_search_filter else ''})"
                elif use_shortcuts and not use_arrow_keys:
                    instruction_msg = f"(Use shortcuts{', type to filter' if use_search_filter else ''})"
                else:
                    instruction_msg = f"(Use arrow keys{', type to filter' if use_search_filter else ''})"
                tokens.append(("class:instruction", instruction_msg))
            return tokens

        layout = common.create_inquirer_layout(ic, get_prompt_tokens, **kwargs)
        bindings = KeyBindings()

        @bindings.add(Keys.ControlQ, eager=True)
        @bindings.add(Keys.ControlC, eager=True)
        def abort_prompt(event):
            event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

        if back_choice_value is not None:

            @bindings.add(Keys.Escape, eager=True)
            def select_back(event):
                ic.is_answered = True
                event.app.exit(result=back_choice_value)

        if use_shortcuts:
            for index, choice in enumerate(ic.choices):
                if choice.shortcut_key is None and not choice.disabled and not use_arrow_keys:
                    raise RuntimeError(
                        f"{choice.title} does not have a shortcut and arrow keys for movement are disabled. This choice is not reachable."
                    )
                if isinstance(choice, Separator) or choice.shortcut_key is None or choice.disabled:
                    continue

                def _reg_binding(choice_index, keys):
                    @bindings.add(keys, eager=True)
                    def select_choice(event):
                        ic.pointed_at = choice_index

                _reg_binding(index, choice.shortcut_key)

        def move_cursor_down(event):
            ic.select_next()
            while not ic.is_selection_valid():
                ic.select_next()

        def move_cursor_up(event):
            ic.select_previous()
            while not ic.is_selection_valid():
                ic.select_previous()

        if use_search_filter:

            def search_filter(event):
                ic.add_search_character(event.key_sequence[0].key)

            for character in string.printable:
                bindings.add(character, eager=True)(search_filter)
            bindings.add(Keys.Backspace, eager=True)(search_filter)

        if use_arrow_keys:
            bindings.add(Keys.Down, eager=True)(move_cursor_down)
            bindings.add(Keys.Up, eager=True)(move_cursor_up)
        if use_jk_keys:
            bindings.add("j", eager=True)(move_cursor_down)
            bindings.add("k", eager=True)(move_cursor_up)
        if use_emacs_keys:
            bindings.add(Keys.ControlN, eager=True)(move_cursor_down)
            bindings.add(Keys.ControlP, eager=True)(move_cursor_up)

        @bindings.add(Keys.ControlM, eager=True)
        def set_answer(event):
            ic.is_answered = True
            event.app.exit(result=ic.get_pointed_at().value)

        @bindings.add(Keys.Any)
        def other(event):
            """Disallow inserting other text."""

        return Question(
            Application(
                layout=layout,
                key_bindings=bindings,
                style=merged_style,
                **utils.used_kwargs(kwargs, Application.__init__),
            )
        )

    @staticmethod
    def _select(message: str, **kwargs):
        choices = kwargs.get("choices")
        kwargs = dict(kwargs)
        if choices is not None:
            kwargs["choices"] = VynexVpnApp._with_terminal_choice_spacing(choices)
        kwargs["style"] = VynexVpnApp._menu_select_style(kwargs.get("style"))
        return VynexVpnApp._select_with_escape_back(message, instruction=" ", **kwargs)

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
    def _load_logo() -> str:
        try:
            if LOGO_FILE.exists():
                return LOGO_FILE.read_text(encoding="utf-8").rstrip()
        except OSError:
            pass
        return ""

    @staticmethod
    def _routing_profile_choice_title(name: str, description: str, is_active: bool) -> str:
        active_suffix = " [активен]" if is_active else ""
        return f"{name} | {description}{active_suffix}"

    @staticmethod
    def _routing_profile_select_style() -> Style:
        return Style(
            [
                ("selected", "fg:ansigreen bold"),
            ]
        )


def main() -> int:
    app = VynexVpnApp()
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
