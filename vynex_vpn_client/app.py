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
from questionary.constants import INDICATOR_SELECTED, INDICATOR_UNSELECTED
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
from prompt_toolkit.filters import Condition
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
        SingboxBackend,
        XrayBackend,
        select_backend,
    )
    from vynex_vpn_client.config_builder import XrayConfigBuilder
    from vynex_vpn_client.singbox_config_builder import SingboxConfigBuilder
    from vynex_vpn_client.constants import (
        AMNEZIAWG_EXECUTABLE,
        AMNEZIAWG_EXECUTABLE_FALLBACK,
        AMNEZIAWG_WINTUN_DLL,
        APP_NAME,
        APP_VERSION,
        DEFAULT_CONSOLE_COLUMNS,
        DEFAULT_CONSOLE_LINES,
        GEOIP_PATH,
        GEOSITE_PATH,
        LOGO_FILE,
        SINGBOX_EXECUTABLE,
        SUBSCRIPTION_TITLE_BY_HOST,
        WINTUN_DLL,
        XRAY_EXECUTABLE,
    )
    from vynex_vpn_client.healthcheck import HealthcheckResult, XrayHealthChecker
    from vynex_vpn_client.core import SingboxInstaller, XrayInstaller
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
    from vynex_vpn_client.process_manager import SingboxProcessManager, State as XrayState, XrayProcessManager
    from vynex_vpn_client.routing_profiles import RoutingProfileManager
    from vynex_vpn_client.storage import JsonStorage, StorageCorruptionError
    from vynex_vpn_client.subscriptions import SubscriptionManager
    from vynex_vpn_client.system_proxy import SystemProxyState, WindowsSystemProxyManager
    from vynex_vpn_client.utils import (
        RunningProcessDetails,
        WindowsInterfaceDetails,
        add_ipv4_route,
        clamp_port,
        generate_random_password,
        generate_random_username,
        get_active_ipv4_interface,
        get_interface_details,
        is_running_as_admin,
        list_running_processes_by_names,
        pick_random_port,
        remove_ipv4_route,
        terminate_running_processes,
        wait_for_port_listener,
        wait_for_tun_interface_details,
    )
    from vynex_vpn_client.tcp_ping import (
        TcpPingResult,
        TcpPingService,
        TCP_PING_UNSUPPORTED_ERROR,
        is_tcp_ping_unsupported_result,
        sort_tcp_ping_results,
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
        SingboxBackend,
        XrayBackend,
        select_backend,
    )
    from .config_builder import XrayConfigBuilder
    from .singbox_config_builder import SingboxConfigBuilder
    from .constants import (
        AMNEZIAWG_EXECUTABLE,
        AMNEZIAWG_EXECUTABLE_FALLBACK,
        AMNEZIAWG_WINTUN_DLL,
        APP_NAME,
        APP_VERSION,
        DEFAULT_CONSOLE_COLUMNS,
        DEFAULT_CONSOLE_LINES,
        GEOIP_PATH,
        GEOSITE_PATH,
        LOGO_FILE,
        SINGBOX_EXECUTABLE,
        SUBSCRIPTION_TITLE_BY_HOST,
        WINTUN_DLL,
        XRAY_EXECUTABLE,
    )
    from .healthcheck import HealthcheckResult, XrayHealthChecker
    from .core import SingboxInstaller, XrayInstaller
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
    from .process_manager import SingboxProcessManager, State as XrayState, XrayProcessManager
    from .routing_profiles import RoutingProfileManager
    from .storage import JsonStorage, StorageCorruptionError
    from .subscriptions import SubscriptionManager
    from .system_proxy import SystemProxyState, WindowsSystemProxyManager
    from .tcp_ping import (
        TcpPingResult,
        TcpPingService,
        TCP_PING_UNSUPPORTED_ERROR,
        is_tcp_ping_unsupported_result,
        sort_tcp_ping_results,
    )
    from .utils import (
        RunningProcessDetails,
        WindowsInterfaceDetails,
        add_ipv4_route,
        clamp_port,
        generate_random_password,
        generate_random_username,
        get_active_ipv4_interface,
        get_interface_details,
        is_running_as_admin,
        list_running_processes_by_names,
        pick_random_port,
        remove_ipv4_route,
        terminate_running_processes,
        wait_for_port_listener,
        wait_for_tun_interface_details,
    )

FLAG_EMOJI_PATTERN = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
MAX_SERVER_NAME_DISPLAY_WIDTH = 39
WINWS_CONFLICT_PROCESS_NAMES = ("Winws.exe", "Winws2.exe")
PHYSICAL_KEY_LAYOUT_MAP = {
    "q": "й",
    "w": "ц",
    "e": "у",
    "r": "к",
    "t": "е",
    "y": "н",
    "u": "г",
    "i": "ш",
    "o": "щ",
    "p": "з",
    "[": "х",
    "]": "ъ",
    "a": "ф",
    "s": "ы",
    "d": "в",
    "f": "а",
    "g": "п",
    "h": "р",
    "j": "о",
    "k": "л",
    "l": "д",
    ";": "ж",
    "'": "э",
    "z": "я",
    "x": "ч",
    "c": "с",
    "v": "м",
    "b": "и",
    "n": "т",
    "m": "ь",
    ",": "б",
    ".": "ю",
    "/": ".",
}


@dataclass(frozen=True)
class MenuAction:
    title: str
    handler: Callable[[], None]


@dataclass(frozen=True)
class RuntimeNotice:
    message: str
    title: str = "Подключение остановлено"
    border_style: str = "yellow"


@dataclass(frozen=True)
class SelectActionResult:
    action: str
    value: object


class TerminalInquirerControl(InquirerControl):
    @staticmethod
    def _searchable_title(choice: Choice) -> str:
        title = getattr(choice, "title", "")
        if isinstance(title, str):
            return title
        if isinstance(title, list):
            return "".join(token[1] for token in title)
        return str(title)

    @property
    def filtered_choices(self):
        if not self.search_filter:
            return self.choices
        search_filter = str(self.search_filter).lower()
        filtered = [
            choice
            for choice in self.choices
            if search_filter in self._searchable_title(choice).lower()
        ]
        self.found_in_search = len(filtered) > 0
        return filtered if self.found_in_search else self.choices

    @staticmethod
    def _choice_style_class(choice: Choice) -> str | None:
        style_class = getattr(choice, "_vynex_style_class", None)
        if style_class is None:
            return None
        normalized = str(style_class).strip()
        return normalized or None

    def _choice_title_style(self, choice: Choice, *, selected: bool, index: int) -> str:
        if selected:
            base_style = "class:selected"
        elif index == self.pointed_at:
            base_style = "class:highlighted"
        else:
            base_style = "class:text"
        extra_style = self._choice_style_class(choice)
        if extra_style is None:
            return base_style
        return f"class:{extra_style} {base_style}"

    def _get_choice_tokens(self):
        tokens = []

        def append(index: int, choice: Choice):
            selected = choice.value in self.selected_options

            if index == self.pointed_at:
                if self.pointer is not None:
                    tokens.append(("class:pointer", f" {self.pointer} "))
                else:
                    tokens.append(("class:text", " " * 3))
                tokens.append(("[SetCursorPosition]", ""))
            else:
                pointer_length = len(self.pointer) if self.pointer is not None else 1
                tokens.append(("class:text", " " * (2 + pointer_length)))

            if isinstance(choice, Separator):
                tokens.append(("class:separator", f"{choice.title}"))
                tokens.append(("", "\n"))
                return

            if choice.disabled:
                if isinstance(choice.title, list):
                    tokens.append(("class:selected" if selected else "class:disabled", "- "))
                    tokens.extend(choice.title)
                else:
                    tokens.append(
                        (
                            "class:selected" if selected else "class:disabled",
                            f"- {choice.title}",
                        )
                    )
                tokens.append(
                    (
                        "class:selected" if selected else "class:disabled",
                        "" if isinstance(choice.disabled, bool) else f" ({choice.disabled})",
                    )
                )
                tokens.append(("", "\n"))
                return

            shortcut = choice.get_shortcut_title() if self.use_shortcuts else ""
            if selected:
                indicator = f"{INDICATOR_SELECTED} " if self.use_indicator else ""
                tokens.append(("class:selected", indicator))
            else:
                indicator = f"{INDICATOR_UNSELECTED} " if self.use_indicator else ""
                tokens.append(("class:text", indicator))

            if isinstance(choice.title, list):
                if shortcut:
                    tokens.append(
                        (
                            self._choice_title_style(choice, selected=selected, index=index),
                            shortcut,
                        )
                    )
                tokens.extend(choice.title)
            else:
                tokens.append(
                    (
                        self._choice_title_style(choice, selected=selected, index=index),
                        f"{shortcut}{choice.title}",
                    )
                )

            tokens.append(("", "\n"))

        for i, c in enumerate(self.filtered_choices):
            append(i, c)

        current = self.get_pointed_at()
        if self.show_selected:
            answer = current.get_shortcut_title() if self.use_shortcuts else ""
            answer += current.title if isinstance(current.title, str) else current.title[0][1]
            tokens.append(("class:text", f"  Answer: {answer}"))

        show_description = self.show_description and current.description is not None
        if show_description:
            tokens.append(("class:text", f"  Description: {current.description}"))

        if not (self.show_selected or show_description):
            tokens.pop()

        return tokens


class VynexVpnApp:
    def __init__(self) -> None:
        self.console = Console()
        self.storage = JsonStorage()
        self.installer = XrayInstaller()
        self.singbox_installer = SingboxInstaller()
        self.app_update_checker = AppUpdateChecker()
        self.app_updater = AppSelfUpdater()
        self.subscription_manager = SubscriptionManager(self.storage)
        self.routing_profiles = RoutingProfileManager()
        self.config_builder = XrayConfigBuilder()
        self.singbox_config_builder = SingboxConfigBuilder()
        self.process_manager = XrayProcessManager(on_crash_callback=self._handle_xray_crash)
        self.singbox_process_manager = SingboxProcessManager(
            on_crash_callback=lambda: self._handle_backend_crash("singbox")
        )
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
        self.health_checker = XrayHealthChecker()
        self.tcp_ping_service = TcpPingService()
        self.system_proxy_manager = WindowsSystemProxyManager()
        self.app_release_info: AppReleaseInfo | None = self.app_update_checker.get_cached_release(max_age_seconds=None)
        self._app_update_thread: threading.Thread | None = None
        self._startup_subscription_refresh_thread: threading.Thread | None = None
        self._proxy_session: ProxyRuntimeSession | None = None
        self._runtime_state_cache: RuntimeState | None = None
        self._runtime_notice: RuntimeNotice | None = None
        self._console_window_size: tuple[int, int] | None = None
        self._should_exit = False
        self.logo = self._load_logo()

    def run(self) -> int:
        try:
            self._ensure_xray_ready()
            self._reconcile_runtime_state()
            self._schedule_app_update_check()
            self._startup_auto_refresh_subscriptions()
            self._startup_quick_import_flow()
            while True:
                self._render_screen(show_banner=True)
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
        missing_components = self._missing_startup_runtime_components()
        if missing_components:
            self._show_runtime_auto_install_notice(
                components=missing_components,
                title="Подготовка приложения",
            )
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

    def _render_banner(self, state: RuntimeState | None = None) -> None:
        runtime_state = state or self._current_state()
        title_markup = f"[bold cyan]{self.logo}[/bold cyan]" if self.logo else f"[bold cyan]{APP_NAME}[/bold cyan]"
        max_content_width = max(20, self.console.width - 2)
        title = Text.from_markup(title_markup)
        status = Text.from_markup(self._banner_status_line(runtime_state))
        status.pad_left(1)
        status.truncate(max_content_width, overflow="ellipsis")
        banner = Group(Text(""), title, Text(""), status)
        self.console.print(
            Panel.fit(
                banner,
                border_style=self._banner_border_style(runtime_state),
                padding=(0, 1),
            )
        )

    @staticmethod
    def _default_console_window_size() -> tuple[int, int]:
        return DEFAULT_CONSOLE_COLUMNS, DEFAULT_CONSOLE_LINES

    @staticmethod
    def _adaptive_console_lines(
        item_count: int,
        *,
        base_lines: int = DEFAULT_CONSOLE_LINES,
        baseline_items: int = 12,
        max_lines: int = 60,
        items_per_extra_line: int = 2,
    ) -> int:
        normalized_count = max(0, int(item_count))
        normalized_step = max(1, int(items_per_extra_line))
        extra_items = max(0, normalized_count - baseline_items)
        extra_lines = (extra_items + normalized_step - 1) // normalized_step
        return min(max_lines, max(base_lines, base_lines + extra_lines))

    def _list_console_window_size(
        self,
        item_count: int,
        *,
        columns: int = DEFAULT_CONSOLE_COLUMNS,
        baseline_items: int = 12,
        max_lines: int = 60,
        items_per_extra_line: int = 2,
    ) -> tuple[int, int]:
        return (
            max(DEFAULT_CONSOLE_COLUMNS, columns),
            self._adaptive_console_lines(
                item_count,
                base_lines=DEFAULT_CONSOLE_LINES,
                baseline_items=baseline_items,
                max_lines=max_lines,
                items_per_extra_line=items_per_extra_line,
            ),
        )

    def _server_manager_console_window_size(self, item_count: int) -> tuple[int, int]:
        return self._list_console_window_size(
            item_count,
            columns=150,
            baseline_items=18,
            max_lines=54,
            items_per_extra_line=2,
        )

    def _apply_console_window_size(self, columns: int, lines: int) -> None:
        target_size = (max(80, int(columns)), max(25, int(lines)))
        if self._console_window_size == target_size:
            return
        if sys.platform != "win32" or not sys.stdout.isatty():
            self._console_window_size = target_size
            return
        try:
            os.system(f"mode con cols={target_size[0]} lines={target_size[1]} > nul")
        except Exception:
            return
        self._console_window_size = target_size

    def _render_screen(
        self,
        *,
        window_size: tuple[int, int] | None = None,
        show_banner: bool = False,
    ) -> None:
        self._apply_console_window_size(*(window_size or self._default_console_window_size()))
        os.system("cls")
        if show_banner:
            self._render_banner(self._current_state())
            self.console.print()
        if self._runtime_notice:
            self.console.print(
                Panel.fit(
                    self._runtime_notice.message,
                    title=self._runtime_notice.title,
                    border_style=self._runtime_notice.border_style,
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
                    Choice(title="Быстрый импорт сервера / подписки", value="__add__"),
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
            elif selected_action == "__add__":
                self.add_server_flow()

    def _show_servers_overview(self) -> None:
        last_ping_signature: tuple[tuple[str, str, str, int], ...] | None = None
        while True:
            loaded_servers = self.storage.load_servers()
            ping_signature = self._servers_tcp_ping_signature(loaded_servers)
            if loaded_servers and ping_signature != last_ping_signature:
                try:
                    loaded_servers = self._refresh_servers_tcp_ping_cache(
                        loaded_servers,
                        status_message=(
                            f"[bold cyan]Обновляем TCP ping в менеджере серверов "
                            f"({len(loaded_servers)})...[/bold cyan]"
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    last_ping_signature = ping_signature
                    self._render_screen()
                    self._show_error("TCP ping серверов", exc)
                    self._pause()
                    loaded_servers = self.storage.load_servers()
                else:
                    last_ping_signature = ping_signature
            else:
                last_ping_signature = ping_signature
            servers = self._sorted_servers(loaded_servers)
            state = self._current_state()
            active_server_id = state.server_id if state.is_running else None
            self._render_screen(window_size=self._server_manager_console_window_size(len(servers)))
            if not servers:
                self.console.print(
                    Panel.fit(
                        "Список серверов пуст. Используйте быстрый импорт в разделе управления серверами и подписками.",
                        title="Менеджер серверов",
                        border_style="yellow",
                    )
                )
            manager_column_widths = self._server_manager_column_widths(
                servers,
                active_server_id=active_server_id,
            )
            choices: list[Choice] = []
            for server in servers:
                choices.append(
                    Choice(
                        title=self._server_manager_choice_title(
                            server,
                            active_server_id=active_server_id,
                            **manager_column_widths,
                        ),
                        value=server.id,
                    )
                )
            choices.append(Choice(title="Назад", value="__back__"))
            selected_action = self._select(
                "Менеджер серверов",
                choices=choices,
                instruction=self._server_manager_instruction(),
                shortcut_actions=[
                    ((Keys.Delete,), "delete"),
                    (("e",), "edit"),
                    (("r",), "refresh"),
                ],
                activate_search_on=("/",),
            ).ask()
            if isinstance(selected_action, SelectActionResult):
                try:
                    self._handle_server_manager_shortcut(selected_action)
                except Exception as exc:  # noqa: BLE001
                    if not self._is_user_cancelled(exc):
                        self._render_screen()
                        self._show_error("Ошибка сервера", exc)
                        self._pause()
                last_ping_signature = self._servers_tcp_ping_signature(self.storage.load_servers())
                continue
            if selected_action in (None, "__back__"):
                return
            self._server_details_flow(selected_action)

    def tcp_ping_all_flow(self) -> None:
        servers = self.storage.load_servers()
        if not servers:
            self._render_screen()
            self.console.print(
                Panel.fit(
                    "Список серверов пуст. Добавьте хотя бы один сервер перед запуском TCP ping.",
                    title="TCP ping серверов",
                    border_style="yellow",
                )
            )
            self._pause()
            return

        try:
            self._render_screen()
            with self.console.status(
                f"[bold cyan]Проверяем TCP ping для {len(servers)} серверов...[/bold cyan]",
                spinner="dots",
            ):
                results = self._tcp_ping_service_instance().ping_many(servers)
        except Exception as exc:  # noqa: BLE001
            self._render_screen()
            self._show_error("TCP ping серверов", exc)
            self._pause()
            return

        self._persist_tcp_ping_results(servers, results)
        state = self._current_state()
        active_server_id = state.server_id if state.is_running else None
        self._render_screen()
        self.console.print(self._tcp_ping_summary_panel(servers, results))
        self.console.print(self._tcp_ping_results_table(servers, results, active_server_id=active_server_id))
        self._pause()

    def _refresh_all_servers_manager_tcp_ping(self) -> list[ServerEntry]:
        current_servers = self.storage.load_servers()
        if not current_servers:
            return []
        return self._refresh_servers_tcp_ping_cache(
            current_servers,
            status_message=f"[bold cyan]Обновляем TCP ping для {len(current_servers)} серверов...[/bold cyan]",
        )

    def _show_subscriptions_overview(self) -> None:
        while True:
            subscriptions = self.storage.load_subscriptions()
            self._render_screen(window_size=self._list_console_window_size(len(subscriptions), baseline_items=10))
            if subscriptions:
                self.console.print(self._subscriptions_table(subscriptions))
            else:
                self.console.print(
                    Panel.fit(
                        "Список подписок пуст. Используйте быстрый импорт в разделе управления серверами и подписками.",
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
            if subscriptions:
                choices.append(Separator(" "))
            if subscriptions:
                choices.append(Choice(title="Обновить все подписки", value="__refresh_all__"))
            choices.append(Choice(title="Назад", value="__back__"))
            selected_action = self._select(
                "Менеджер подписок",
                choices=choices,
                instruction=self._subscription_manager_instruction(),
                shortcut_actions=[
                    ((Keys.Delete,), "delete"),
                    (("e",), "edit"),
                    (("r",), "refresh"),
                ],
                activate_search_on=("/",),
            ).ask()
            if isinstance(selected_action, SelectActionResult):
                try:
                    self._handle_subscription_manager_shortcut(selected_action)
                except Exception as exc:  # noqa: BLE001
                    if not self._is_user_cancelled(exc):
                        self._render_screen()
                        self._show_error("Ошибка подписки", exc)
                        self._pause()
                continue
            if selected_action in (None, "__back__"):
                return
            if selected_action == "__refresh_all__":
                self.update_subscriptions_flow()
                continue
            self._subscription_details_flow(selected_action)

    def _startup_quick_import_flow(self) -> None:
        if self.storage.load_servers():
            return
        self._show_empty_servers_import_flow(title="Быстрый старт")

    def _startup_auto_refresh_subscriptions(self) -> None:
        if self._startup_subscription_refresh_thread is not None and self._startup_subscription_refresh_thread.is_alive():
            return
        try:
            settings = self._validated_settings(raise_on_error=False)
            if not settings.auto_update_subscriptions_on_startup:
                return
            if not self.storage.load_subscriptions():
                return
        except Exception as exc:  # noqa: BLE001
            self._runtime_notice = RuntimeNotice(
                message=f"Не удалось запустить авто-обновление подписок: {self._error_text(exc)}",
                title="Авто-обновление подписок",
                border_style="red",
            )
            return
        self._startup_subscription_refresh_thread = threading.Thread(
            target=self._refresh_subscriptions_on_startup_in_background,
            name="vynex-startup-subscription-refresh",
            daemon=True,
        )
        self._startup_subscription_refresh_thread.start()

    def _refresh_subscriptions_on_startup_in_background(self) -> None:
        try:
            success, failed = self.subscription_manager.refresh_all(only_auto_update=True)
        except Exception as exc:  # noqa: BLE001
            self._runtime_notice = RuntimeNotice(
                message=f"Не удалось запустить авто-обновление подписок: {self._error_text(exc)}",
                title="Авто-обновление подписок",
                border_style="red",
            )
            return

        if not failed:
            return

        details = ", ".join(
            f"{self._ui_subscription_title(subscription.title)}: {error}"
            for subscription, error in failed[:2]
        )
        remaining = len(failed) - 2
        if remaining > 0:
            details = f"{details}, еще ошибок: {remaining}" if details else f"Еще ошибок: {remaining}"
        message = (
            "При запуске не все подписки удалось обновить.\n"
            f"Успешно: {len(success)}\n"
            f"С ошибками: {len(failed)}"
        )
        if details:
            message = f"{message}\n{details}"
        self._runtime_notice = RuntimeNotice(
            message=message,
            title="Авто-обновление подписок",
            border_style="yellow" if success else "red",
        )

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
        address_width = max((self._display_width(f"{server.host}:{server.port}") for server in servers), default=1)
        ping_width = max((self._display_width(self._cached_tcp_ping_label(server)) for server in servers), default=1)
        best_server_id = self._best_cached_tcp_ping_server_id(servers)
        self._render_screen(window_size=self._list_console_window_size(len(servers), baseline_items=11))
        selected_server_id = self._select(
            "Выберите сервер",
            choices=[
                self._connect_server_choice(
                    server,
                    name_width=name_width,
                    protocol_width=protocol_width,
                    address_width=address_width,
                    ping_width=ping_width,
                    is_best=server.id == best_server_id,
                )
                for server in servers
            ] + [Choice(title="Назад", value="__back__")],
            use_shortcuts=True
        ).ask()
        if not selected_server_id or selected_server_id == "__back__":
            return
        if not self._ensure_winws_conflicts_resolved():
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
            self._ensure_runtime_ready(
                mode,
                server=selected_server,
                routing_profile=routing_profile,
            )
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
            self._save_runtime_state(state)
            self._proxy_session = proxy_session
            detail_rows = [
                ("Сервер", self._ui_server_name(selected_server.name)),
                ("Протокол", selected_server.protocol.upper()),
                ("Режим", self._connection_mode_label(mode)),
                ("Маршрут", routing_display_label),
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
                self._runtime_notice = RuntimeNotice(
                    message=health_warning,
                    title="Подключение установлено с предупреждением",
                    border_style="yellow",
                )
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
            self._reset_runtime_state()
            self._render_screen()
            self._show_error("Ошибка подключения", exc)
            self._pause()

    def _ensure_winws_conflicts_resolved(self) -> bool:
        conflicts = list_running_processes_by_names(WINWS_CONFLICT_PROCESS_NAMES)
        if not conflicts:
            return True

        conflict_summary = self._format_process_conflict_summary(conflicts)
        detail_rows = [
            ("Процессы", conflict_summary),
            ("Почему это важно", "Winws может препятствовать нормальной работе VPN."),
            ("Что сделать", "Остановить их перед подключением. Клиент может завершить их автоматически."),
        ]
        self._render_screen()
        self.console.print(
            Panel.fit(
                self._key_value_group(detail_rows),
                title="Найдены конфликтующие процессы",
                border_style="yellow",
            )
        )
        should_terminate = bool(
            questionary.confirm(
                "Автоматически завершить Winws и продолжить подключение?",
                default=True,
            ).ask()
        )
        if not should_terminate:
            self.console.print(
                Panel.fit(
                    "Подключение отменено. Остановите Winws.exe / Winws2.exe и повторите попытку.",
                    border_style="yellow",
                )
            )
            self._pause()
            return False

        failed_processes = terminate_running_processes(conflicts)
        if failed_processes:
            failed_summary = self._format_process_conflict_summary(failed_processes)
            raise RuntimeError(
                "Не удалось завершить конфликтующие процессы: "
                f"{failed_summary}. Остановите их вручную и повторите подключение."
            )
        return True

    @staticmethod
    def _format_process_conflict_summary(processes: list[RunningProcessDetails] | tuple[RunningProcessDetails, ...]) -> str:
        return ", ".join(f"{process.name} (PID {process.pid})" for process in processes)

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

    def _edit_server_from_list_flow(self, server: ServerEntry) -> None:
        if server.source == "subscription":
            self._rename_server_flow(server)
            return
        if server.is_amneziawg:
            self._rename_server_flow(server)
            return
        selected_action = self._select(
            "Редактировать сервер",
            choices=[
                "Переименовать",
                "Изменить ссылку",
                "Назад",
            ],
            use_shortcuts=True,
        ).ask()
        if selected_action in (None, "Назад"):
            return
        if selected_action == "Переименовать":
            self._rename_server_flow(server)
            return
        if selected_action == "Изменить ссылку":
            self._edit_server_link_flow(server)

    def _handle_server_manager_shortcut(self, action_result: SelectActionResult) -> None:
        selected_value = action_result.value
        if selected_value in (None, "__back__", "__add__"):
            return
        if action_result.action == "refresh":
            self._refresh_all_servers_manager_tcp_ping()
            return
        server = self.storage.get_server(str(selected_value))
        if server is None:
            return
        if action_result.action == "edit":
            self._edit_server_from_list_flow(server)
        elif action_result.action == "delete":
            self._delete_server_with_prompt(server)

    def delete_server_flow(self) -> None:
        servers = self._sorted_servers(self.storage.load_servers())
        if not servers:
            self._render_screen()
            self.console.print(Panel.fit("Список серверов пуст.", border_style="yellow"))
            self._pause()
            return

        name_width = self._server_name_column_width(servers)
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

        self._render_screen(window_size=self._list_console_window_size(len(servers), baseline_items=11))
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
                    "Обновить сейчас",
                    "Открыть список серверов",
                    "Переименовать",
                    "Изменить URL",
                    "Удалить подписку",
                    "Назад",
                ],
                use_shortcuts=True,
            ).ask()
            if selected_action in (None, "Назад"):
                return
            try:
                if selected_action == "Обновить сейчас":
                    imported = self._refresh_subscription(subscription)
                    self._show_subscription_refresh_success("Подписка обновлена", subscription, imported)
                elif selected_action == "Переименовать":
                    self._rename_subscription_flow(subscription)
                elif selected_action == "Изменить URL":
                    self._edit_subscription_url_flow(subscription)
                elif selected_action == "Открыть список серверов":
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

    def _edit_subscription_from_list_flow(self, subscription: SubscriptionEntry) -> None:
        selected_action = self._select(
            "Редактировать подписку",
            choices=[
                "Изменить название",
                "Изменить URL",
                "Назад",
            ],
            use_shortcuts=True,
        ).ask()
        if selected_action in (None, "Назад"):
            return
        if selected_action == "Изменить название":
            self._rename_subscription_flow(subscription)
            return
        if selected_action == "Изменить URL":
            self._edit_subscription_url_flow(subscription)

    def _handle_subscription_manager_shortcut(self, action_result: SelectActionResult) -> None:
        selected_value = action_result.value
        if selected_value in (None, "__back__", "__add__"):
            return
        if action_result.action == "refresh":
            if selected_value == "__refresh_all__":
                self.update_subscriptions_flow()
                return
            subscription = self.storage.get_subscription(str(selected_value))
            if subscription is None:
                return
            imported = self._refresh_subscription(subscription)
            self._show_subscription_refresh_success("Подписка обновлена", subscription, imported)
            return
        if selected_value == "__refresh_all__":
            return
        subscription = self.storage.get_subscription(str(selected_value))
        if subscription is None:
            return
        if action_result.action == "edit":
            self._edit_subscription_from_list_flow(subscription)
        elif action_result.action == "delete":
            self._delete_subscription_flow(subscription)

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
        table = Table(title=f"Сервера подписки: {self._ui_subscription_title(subscription.title)}")
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
                    title=f"Удалить подписку и ее сервера ({len(servers)})",
                    value="remove",
                ),
                Choice(
                    title=f"Удалить подписку, сервера оставить как ручные ({len(servers)})",
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

        action_text = "удалить подписку и ее сервера" if remove_servers else "удалить подписку и отвязать сервера"
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
            connection_mode_choice = self._settings_menu_choice(
                "Режим подключения: ",
                self._connection_mode_label(settings.connection_mode),
            )
            system_proxy_choice = self._settings_menu_choice(
                "Системный proxy (PROXY): ",
                "Вкл" if settings.set_system_proxy else "Выкл",
            )
            auto_update_choice = self._settings_menu_choice(
                "Авто-обновление подписок при запуске: ",
                "Вкл" if settings.auto_update_subscriptions_on_startup else "Выкл",
            )
            routing_choice = self._settings_menu_choice(
                "Набор маршрутизации: ",
                active_routing_name,
            )
            selected_action = self._select(
                "Настройки",
                choices=[
                    connection_mode_choice,
                    system_proxy_choice,
                    auto_update_choice,
                    routing_choice,
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
                elif selected_action.startswith("Авто-обновление подписок при запуске:"):
                    auto_update_answer = questionary.confirm(
                        "Обновлять подписки автоматически при запуске клиента?",
                        default=settings.auto_update_subscriptions_on_startup,
                    ).ask()
                    if auto_update_answer is None:
                        continue
                    settings.auto_update_subscriptions_on_startup = bool(auto_update_answer)
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
                    self._xray_component_label(),
                    self._component_choice_label("sing-box", SINGBOX_EXECUTABLE),
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
                elif selected_action.startswith("sing-box"):
                    self._prepare_component_update()
                    path = self.singbox_installer.update_singbox()
                    self._show_component_result("sing-box обновлен", path.name)
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
                    singbox_path = self.singbox_installer.update_singbox()
                    profiles = self.routing_profiles.update_profiles()
                    updated_components = list(result.keys())
                    updated_components.append(singbox_path.name)
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
                title="Маршрут обновлен",
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
            table.add_row("Версия", f"v{APP_VERSION}", style="dim")
            table.add_row("Xray-core", self._xray_version_status_label(), style="dim")
            if self._available_app_update() is not None:
                table.add_row("Обновление", self._available_app_update_label(), style="bold cyan")
            table.add_row(
                "Режим по умолчанию",
                self._connection_mode_label(settings.connection_mode),
                style=self._connection_mode_style(settings.connection_mode),
            )
            table.add_row("Ядро", "Не запущено", style="yellow")
            table.add_row(
                f"Состояние {engine_name}",
                engine_state_label,
                style=self._runtime_engine_state_style(RuntimeState(mode=default_mode)),
            )
            table.add_row(
                "Локальные порты",
                "выдаются случайно на время подключения" if settings.connection_mode == "PROXY" else "Не используются",
                style="dim",
            )
            table.add_row(
                "Системный proxy",
                "Авто" if settings.connection_mode == "PROXY" and settings.set_system_proxy else "Выкл",
                style="cyan" if settings.connection_mode == "PROXY" and settings.set_system_proxy else "dim",
            )
            table.add_row(
                "Маршрут",
                self._active_routing_profile_name(),
                style="bold cyan",
            )
            self._render_screen()
            self.console.print(
                Panel.fit(
                    table,
                    title="Статус",
                    border_style="cyan",
                )
            )
            self._pause()
            return
        server = self.storage.get_server(state.server_id) if state.server_id else None
        table = Table(show_header=False, box=None)
        table.add_row("Версия", f"v{APP_VERSION}", style="dim")
        table.add_row("Xray-core", self._xray_version_status_label(), style="dim")
        if self._available_app_update() is not None:
            table.add_row("Обновление", self._available_app_update_label(), style="bold cyan")
        table.add_row("Процесс", self._runtime_status_text(state), style=self._runtime_engine_state_style(state))
        table.add_row("PID", self._runtime_pid_label(state), style="dim")
        table.add_row("Режим", state.mode or "-", style=self._connection_mode_style(state.mode))
        table.add_row("Сервер", self._ui_server_name(server.name) if server else "-", style="bold green" if server else None)
        table.add_row("Адрес", f"{server.host}:{server.port}" if server else "-")
        table.add_row("Протокол", server.protocol.upper() if server else "-")
        table.add_row("Старт", state.started_at or "-", style="dim")
        table.add_row(
            "Routing",
            self._routing_display_name(
                state.backend_id,
                state.routing_profile_name or self._active_routing_profile_name(),
            ),
            style="bold cyan",
        )
        if state.mode == "PROXY":
            table.add_row("Ядро", self._backend_engine_name(state.backend_id), style="cyan")
            table.add_row(
                f"Состояние {self._backend_engine_name(state.backend_id)}",
                self._runtime_engine_state_label(state),
                style=self._runtime_engine_state_style(state),
            )
            table.add_row("Локальные порты", "скрыты", style="dim")
            table.add_row("SOCKS5", "защищен аутентификацией и не публикуется", style="dim")
            table.add_row("Системный proxy", "Да" if state.system_proxy_enabled else "Нет", style="cyan" if state.system_proxy_enabled else "dim")
        elif state.mode == "TUN":
            table.add_row("Ядро", self._backend_engine_name(state.backend_id), style="cyan")
            table.add_row(
                f"Состояние {self._backend_engine_name(state.backend_id)}",
                self._runtime_engine_state_label(state),
                style=self._runtime_engine_state_style(state),
            )
            table.add_row("TUN", state.tun_interface_name or self._tun_interface_name(state.backend_id), style="blue")
            table.add_row("IPv4 TUN", state.tun_interface_ipv4 or "-", style="blue")
            table.add_row("Маршруты", ", ".join(state.tun_route_prefixes) if state.tun_route_prefixes else "-", style="dim")
            table.add_row("Внешний интерфейс", state.outbound_interface_name or "-", style="dim")
            table.add_row("Системный proxy", "Нет", style="dim")
        self._render_screen()
        self.console.print(Panel.fit(table, title="Статус подключения", border_style=self._banner_border_style(state)))
        self._pause()

    def _prompt_port(self, title: str, default: int) -> int:
        raw_value = questionary.text(title, default=str(default)).ask()
        if raw_value is None:
            raise ValueError("Ввод порта отменен.")
        try:
            return clamp_port(int(raw_value))
        except ValueError as exc:
            raise ValueError(f"Некорректное значение порта для '{title}'.") from exc

    def _load_runtime_state_or_recover(self) -> RuntimeState:
        cached_state = getattr(self, "_runtime_state_cache", None)
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
        # TODO: helper_pid is still tied to the legacy xray process manager.
        # Model backend-specific helper ownership once a non-xray backend starts using it.
        helper_dead = bool(state.helper_pid and not self.process_manager.is_running(state.helper_pid))
        if main_dead or helper_dead:
            self._proxy_session = None
            self._disconnect_runtime(silent=True)
            return self._load_runtime_state_or_recover()
        self._runtime_state_cache = state
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
        self._reset_runtime_state()
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
        self._save_runtime_state(state)
        return state

    def _handle_xray_crash(self) -> None:
        self._handle_backend_crash("xray")

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
            self._runtime_notice = RuntimeNotice(
                message=(
                    f"{engine_title} завершился и не смог восстановиться автоматически. "
                    "Сетевое состояние туннеля очищено, подключение сброшено."
                ),
            )
            return
        self._runtime_notice = RuntimeNotice(
            message=(
                f"{engine_title} завершился и не смог восстановиться автоматически. "
                "Подключение сброшено, системный proxy возвращен в прежнее состояние."
            ),
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
            return "AWG-конфиг"
        normalized = str(routing_name or "").strip()
        return normalized or "-"

    def _banner_status_line(self, state: RuntimeState | None = None) -> str:
        runtime_state = state or self._current_state()
        settings = self._validated_settings(raise_on_error=False)
        mode_value = runtime_state.mode if runtime_state.is_running and runtime_state.mode else settings.connection_mode
        mode_label = self._connection_mode_markup(mode_value)
        routing_source = self._routing_display_name(
            runtime_state.backend_id if runtime_state.is_running else None,
            runtime_state.routing_profile_name
            if runtime_state.is_running and runtime_state.routing_profile_name
            else self._active_routing_profile_name(),
        )
        routing_name = escape(self._shorten_text(routing_source, 28))
        update_suffix = ""
        if self._available_app_update() is not None:
            update_suffix = (
                f" | [bold cyan]Обновление:[/bold cyan] "
                f"[bold yellow]{escape(self._available_app_update_label())}[/bold yellow]"
            )
        if runtime_state.is_running:
            server = self.storage.get_server(runtime_state.server_id) if runtime_state.server_id else None
            server_name = escape(
                self._shorten_text(
                    self._ui_server_name(server.name) if server else "сервер недоступен",
                    32,
                )
            )
            return (
                f"[bold]Статус:[/bold] {self._runtime_status_markup(runtime_state)}"
                f" | [bold]Сервер:[/bold] [white]{server_name}[/white]"
                f" | [bold]Режим:[/bold] {mode_label}"
                f" | [bold]Маршрут:[/bold] [cyan]{routing_name}[/cyan]"
                f"{update_suffix}"
            )
        return (
            "[bold]Статус:[/bold] [yellow]Не подключено[/yellow]"
            f" | [bold]Режим:[/bold] {mode_label}"
            f" | [bold]Маршрут:[/bold] [cyan]{routing_name}[/cyan]"
            f"{update_suffix}"
        )

    def _banner_border_style(self, state: RuntimeState | None = None) -> str:
        runtime_state = state or self._current_state()
        return "green" if runtime_state.is_running else "cyan"

    @staticmethod
    def _connection_mode_style(value: str | None) -> str:
        return "bold blue" if str(value or "").upper() == "TUN" else "bold cyan"

    @classmethod
    def _connection_mode_markup(cls, value: str | None) -> str:
        label = escape(cls._connection_mode_short_label(value or "PROXY"))
        style = cls._connection_mode_style(value)
        return f"[{style}]{label}[/]"

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

    def _runtime_engine_state_style(self, state: RuntimeState) -> str:
        backend_state = self._backend_process_state(state)
        if backend_state == XrayState.STARTING:
            return "cyan"
        if backend_state == XrayState.RUNNING:
            return "bold green"
        if backend_state == XrayState.STOPPING:
            return "yellow"
        if backend_state == XrayState.CRASHED:
            return "bold red" if not self._backend_supports_crash_recovery(state) else "yellow"
        return "dim"

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
                        *self._admin_launch_instructions(),
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
                    "Ядро подключения подняло TUN интерфейс, но Windows не приняла маршруты для перехвата трафика.",
                    [
                        "Убедитесь, что приложение запущено от имени администратора.",
                        "Проверьте, не блокирует ли изменение маршрутов другой VPN-клиент или корпоративная политика Windows.",
                    ],
                    details,
                )
            if "tun интерфейс" in normalized and ("ipv4" in normalized or "инициализации" in normalized):
                return (
                    "Ядро подключения не смогло вовремя поднять TUN интерфейс Windows.",
                    [
                        "Проверьте, что runtime движка собран полностью и не поврежден.",
                        "Если в системе уже работает другой VPN/TUN-драйвер, временно отключите его и повторите попытку.",
                    ],
                    details,
                )
            if "локальные proxy-inbound" in normalized or "локальный proxy" in normalized:
                return (
                    "Ядро подключения не успело открыть локальные proxy-порты для текущей сессии.",
                    [
                        "Повторите подключение: клиент выберет новые случайные порты.",
                        "Если ошибка повторяется, обновите компоненты соответствующего движка в разделе 'Компоненты'.",
                        "Если процесс завершился сразу, используйте детали ниже для диагностики конкретной ошибки runtime.",
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
            if "уже запущен sing-box.exe" in normalized:
                return (
                    "Обнаружен другой экземпляр sing-box, который мешает запуску клиента.",
                    [
                        "Остановите внешнюю копию sing-box.exe или завершите прошлое подключение.",
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
            if "sing-box.exe не найден" in normalized:
                return (
                    "Исполняемый файл sing-box отсутствует в runtime-каталоге.",
                    [
                        "Откройте 'Компоненты' и обновите sing-box.",
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
                    "AmneziaWG поднял интерфейс, но DNS сервера из профиля не были применены в Windows.",
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
            if "в ссылке отсутствует идентификатор или пароль" in normalized:
                return (
                    "В ссылке сервера нет обязательного идентификатора или пароля.",
                    [
                        "Для VLESS и VMess проверьте UUID перед символом @.",
                        "Для Trojan, Shadowsocks и Hysteria2 проверьте пароль или userinfo-часть ссылки.",
                    ],
                    details,
                )
            if "в shadowsocks ссылке отсутствуют method:password" in normalized:
                return (
                    "В ссылке Shadowsocks отсутствуют метод шифрования и пароль.",
                    [
                        "Проверьте, что credentials-часть ссылки содержит `method:password`.",
                        "Если ссылка закодирована в Base64, скопируйте ее заново целиком.",
                    ],
                    details,
                )
            if "некорректный порт hysteria2" in normalized or "в ссылке указан некорректный порт" in normalized:
                return (
                    "В ссылке сервера указан некорректный порт.",
                    [
                        "Проверьте число после двоеточия в адресе сервера.",
                        "Если ссылка была перенесена по строкам, скопируйте ее заново без потери символов.",
                    ],
                    details,
                )
            if "ссылка содержит поврежденные или неполные данные" in normalized:
                return (
                    "Ссылка сервера повреждена или скопирована не полностью.",
                    [
                        "Скопируйте ссылку заново и убедитесь, что она вставлена целиком.",
                        "Если ссылка пришла в Base64 или из мессенджера, проверьте, что не потерялись символы в начале и в конце.",
                    ],
                    details,
                )
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
    def _admin_launch_instructions() -> list[str]:
        if sys.platform != "win32":
            return ["Перезапустите приложение с повышенными правами и повторите действие."]
        if getattr(sys, "frozen", False):
            return ["Закройте приложение и запустите `VynexVPNClient.exe` через 'Запуск от имени администратора'."]
        return [
            "Закройте приложение, откройте PowerShell от имени администратора и запустите `python main.py`.",
            "Если проект запускается из virtualenv, используйте `./.venv/Scripts/python.exe ./main.py`.",
        ]

    @staticmethod
    def _error_text(error: Exception | str) -> str:
        if isinstance(error, str):
            message = error.strip()
            return message or "Неизвестная ошибка."

        messages: list[str] = []
        seen_messages: set[str] = set()
        visited_errors: set[int] = set()
        current: BaseException | None = error

        while current is not None and id(current) not in visited_errors:
            visited_errors.add(id(current))
            message = VynexVpnApp._humanize_error_message(current).strip()
            if message and message not in seen_messages:
                messages.append(message)
                seen_messages.add(message)
            current = current.__cause__ or current.__context__

        if not messages:
            return "Неизвестная ошибка."
        if len(messages) == 1:
            return messages[0]
        return f"{messages[0]} Причина: {' Причина: '.join(messages[1:])}"

    @staticmethod
    def _humanize_error_message(error: BaseException) -> str:
        if isinstance(error, UnicodeDecodeError):
            return "Ссылка содержит поврежденные или неполные данные."

        message = str(error).strip()
        normalized = message.lower()
        if not message:
            return "Неизвестная ошибка."
        if "port could not be cast to integer value" in normalized or "invalid literal for int()" in normalized:
            return "В ссылке указан некорректный порт."
        if any(
            token in normalized
            for token in (
                "invalid start byte",
                "invalid continuation byte",
                "incorrect padding",
                "invalid base64",
                "unterminated string",
                "expecting value",
                "extra data",
            )
        ):
            return "Ссылка содержит поврежденные или неполные данные."
        return message

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

    def _server_name_display_width(self, *, min_width: int, reserved_width: int) -> int:
        available_width = max(min_width, self.console.width - reserved_width)
        return min(MAX_SERVER_NAME_DISPLAY_WIDTH, available_width)

    def _server_name_column_width(self, servers) -> int:
        max_name_width = max((self._display_width(self._ui_server_name(server.name)) for server in servers), default=12)
        available_width = self._server_name_display_width(min_width=18, reserved_width=24)
        return min(max_name_width, available_width)

    def _server_choice_title(
        self,
        server_name: str,
        protocol: str,
        address: str,
        name_width: int,
        protocol_width: int,
        *,
        address_width: int | None = None,
        tcp_ping_label: str | None = None,
        ping_width: int | None = None,
    ) -> str:
        safe_server_name = self._ui_server_name(server_name)
        aligned_name = self._pad_display_width(self._truncate_display_width(safe_server_name, name_width), name_width)
        aligned_protocol = self._pad_display_width(protocol, protocol_width)
        effective_address_width = max(1, address_width or self._display_width(address))
        aligned_address = self._pad_display_width(
            self._truncate_display_width(address, effective_address_width),
            effective_address_width,
        )
        title = f"{aligned_name} | {aligned_protocol} | {aligned_address}"
        if tcp_ping_label is None:
            return title
        effective_ping_width = max(1, ping_width or self._display_width(tcp_ping_label))
        aligned_ping = self._pad_display_width(tcp_ping_label, effective_ping_width)
        return f"{title} | {aligned_ping}"

    def _connect_server_choice(
        self,
        server: ServerEntry,
        *,
        name_width: int,
        protocol_width: int,
        address_width: int,
        ping_width: int,
        is_best: bool,
    ) -> Choice:
        choice = Choice(
            title=self._server_choice_title(
                server.name,
                server.protocol.upper(),
                f"{server.host}:{server.port}",
                name_width,
                protocol_width,
                address_width=address_width,
                tcp_ping_label=self._cached_tcp_ping_label(server),
                ping_width=ping_width,
            ),
            value=server.id,
        )
        if not is_best:
            return choice
        styled_choice = self._styled_choice(choice, style_class="best-ping")
        if isinstance(styled_choice, Choice):
            return styled_choice
        return choice

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

    @staticmethod
    def _settings_menu_choice(label: str, value: str) -> Choice:
        return Choice(
            title=[
                ("class:text", label),
                ("class:settings-value", value),
            ],
            value=f"{label}{value}",
        )

    def _tcp_ping_service_instance(self) -> TcpPingService:
        service = getattr(self, "tcp_ping_service", None)
        if service is None:
            service = TcpPingService()
            self.tcp_ping_service = service
        return service

    def _persist_tcp_ping_results(
        self,
        servers: list[ServerEntry],
        results: list[TcpPingResult],
        *,
        clear_missing: bool = True,
    ) -> None:
        result_by_id = {result.server_id: result for result in results}
        for server in servers:
            result = result_by_id.get(server.id)
            if result is None:
                if clear_missing:
                    server.extra.pop("tcp_ping_ms", None)
                    server.extra.pop("tcp_ping_ok", None)
                    server.extra.pop("tcp_ping_error", None)
                    server.extra.pop("tcp_ping_checked_at", None)
                continue
            server.extra["tcp_ping_ms"] = result.latency_ms
            server.extra["tcp_ping_ok"] = result.ok
            server.extra["tcp_ping_error"] = result.error
            server.extra["tcp_ping_checked_at"] = result.checked_at
        self.storage.save_servers(servers)

    def _refresh_server_tcp_ping_cache(self, server_id: str) -> None:
        servers = self.storage.load_servers()
        server = next((item for item in servers if item.id == server_id), None)
        if server is None:
            return
        results = self._run_servers_tcp_ping(
            [server],
            status_message=(
                f"[bold cyan]Обновляем TCP ping: {self._ui_server_name(server.name)} "
                f"({server.host}:{server.port})...[/bold cyan]"
            ),
        )
        self._persist_tcp_ping_results(servers, results, clear_missing=False)

    def _refresh_servers_tcp_ping_cache(
        self,
        servers: list[ServerEntry],
        *,
        status_message: str | None = None,
    ) -> list[ServerEntry]:
        if not servers:
            return servers
        results = self._run_servers_tcp_ping(
            servers,
            status_message=(
                status_message
                or f"[bold cyan]Проверяем TCP ping для {len(servers)} серверов...[/bold cyan]"
            ),
        )
        self._persist_tcp_ping_results(servers, results, clear_missing=True)
        return servers

    def _run_servers_tcp_ping(
        self,
        servers: list[ServerEntry],
        *,
        status_message: str,
    ) -> list[TcpPingResult]:
        self._render_screen()
        with self.console.status(status_message, spinner="dots"):
            return self._tcp_ping_service_instance().ping_many(servers)

    @staticmethod
    def _servers_tcp_ping_signature(servers: list[ServerEntry]) -> tuple[tuple[str, str, str, int], ...]:
        return tuple(
            sorted(
                (
                    server.id,
                    server.protocol.lower(),
                    server.host.lower(),
                    server.port,
                )
                for server in servers
            )
        )

    def _tcp_ping_summary_panel(self, servers: list[ServerEntry], results: list[TcpPingResult]) -> Panel:
        ordered_results = sort_tcp_ping_results(servers, results)
        available_results = [result for result in results if result.ok]
        unsupported_results = [result for result in results if is_tcp_ping_unsupported_result(result)]
        unavailable_results = [
            result for result in results
            if not result.ok and not is_tcp_ping_unsupported_result(result)
        ]
        best_entry = ordered_results[0] if ordered_results and ordered_results[0][1].ok else None
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Параметр", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(30, self.console.width - 32))
        table.add_row("Всего", str(len(servers)))
        table.add_row("Доступно", str(len(available_results)))
        if unsupported_results:
            table.add_row("Не проверяется", str(len(unsupported_results)))
        table.add_row("Недоступно", str(len(unavailable_results)))
        table.add_row(
            "Лучший",
            (
                f"{self._ui_server_name(best_entry[0].name)} | {best_entry[1].latency_ms} ms"
                if best_entry and best_entry[1].latency_ms is not None
                else "-"
            ),
        )
        if unsupported_results:
            table.add_row("Примечание", "UDP-протоколы (AmneziaWG, Hysteria2) не проверяются через TCP ping.")
        return Panel.fit(
            table,
            title="TCP ping серверов",
            border_style="cyan" if available_results else "yellow",
        )

    def _tcp_ping_results_table(
        self,
        servers: list[ServerEntry],
        results: list[TcpPingResult],
        *,
        active_server_id: str | None,
    ) -> Table:
        ordered_results = sort_tcp_ping_results(servers, results)
        best_server_id = ordered_results[0][0].id if ordered_results and ordered_results[0][1].ok else None
        name_width = self._server_name_display_width(min_width=20, reserved_width=64)
        table = Table(title="Результаты TCP ping")
        table.add_column("Имя", no_wrap=True, overflow="ellipsis", max_width=name_width)
        table.add_column("Протокол", no_wrap=True)
        table.add_column("Адрес", no_wrap=True)
        table.add_column("Статус", no_wrap=True)
        table.add_column("TCP ping", no_wrap=True)
        for server, result in ordered_results:
            name = self._truncate_display_width(self._ui_server_name(server.name), name_width)
            if server.id == active_server_id:
                name = self._truncate_display_width(f"{name} [активен]", name_width)
            table.add_row(
                name,
                server.protocol.upper(),
                f"{server.host}:{server.port}",
                self._tcp_ping_status_label(result),
                self._tcp_ping_result_label(result),
                style=self._tcp_ping_row_style(
                    result,
                    is_best=server.id == best_server_id,
                    is_active=server.id == active_server_id,
                ),
            )
        return table

    @staticmethod
    def _tcp_ping_row_style(result: TcpPingResult, *, is_best: bool, is_active: bool) -> str | None:
        if is_best:
            return "bold green"
        if is_active and result.ok:
            return "bold cyan"
        if result.ok:
            return "green"
        if is_tcp_ping_unsupported_result(result):
            return "yellow"
        return "red"

    @staticmethod
    def _tcp_ping_status_label(result: TcpPingResult) -> str:
        if result.ok:
            return "Доступен"
        if is_tcp_ping_unsupported_result(result):
            return "Не проверяется"
        return "Недоступен"

    @staticmethod
    def _tcp_ping_result_label(result: TcpPingResult) -> str:
        if result.ok and result.latency_ms is not None:
            return f"{result.latency_ms} ms"
        return VynexVpnApp._tcp_ping_error_label(result.error)

    def _cached_tcp_ping_label(self, server: ServerEntry) -> str:
        if server.extra.get("tcp_ping_ok") and server.extra.get("tcp_ping_ms") is not None:
            return f"{server.extra['tcp_ping_ms']} ms"
        return self._tcp_ping_error_label(server.extra.get("tcp_ping_error"))

    @staticmethod
    def _best_cached_tcp_ping_server_id(servers: list[ServerEntry]) -> str | None:
        candidates: list[tuple[int, str, str, int, str]] = []
        for server in servers:
            if not server.extra.get("tcp_ping_ok"):
                continue
            latency_ms = server.extra.get("tcp_ping_ms")
            if latency_ms is None:
                continue
            try:
                normalized_latency = int(latency_ms)
            except (TypeError, ValueError):
                continue
            candidates.append(
                (
                    normalized_latency,
                    server.name.lower(),
                    server.host.lower(),
                    server.port,
                    server.id,
                )
            )
        if not candidates:
            return None
        return min(candidates)[-1]

    @staticmethod
    def _tcp_ping_error_label(error: object) -> str:
        if str(error or "").strip() == TCP_PING_UNSUPPORTED_ERROR:
            return "н/д"
        return str(error or "-")

    def _servers_table(self, servers: list[ServerEntry], *, active_server_id: str | None) -> Table:
        name_width = self._server_name_display_width(min_width=18, reserved_width=96)
        table = Table(title="Сервера")
        table.add_column("Имя", no_wrap=True, overflow="ellipsis", max_width=name_width)
        table.add_column("Протокол", no_wrap=True)
        table.add_column("Адрес", no_wrap=True)
        table.add_column("Источник", no_wrap=True)
        table.add_column("Статус", no_wrap=True)
        table.add_column("TCP ping", no_wrap=True)
        for server in servers:
            table.add_row(
                self._truncate_display_width(self._ui_server_name(server.name), name_width),
                server.protocol.upper(),
                f"{server.host}:{server.port}",
                self._server_source_label(server),
                self._server_status_label(server, active_server_id=active_server_id),
                self._cached_tcp_ping_label(server),
                style=self._server_row_style(server, active_server_id=active_server_id),
            )
        return table

    @staticmethod
    def _allocate_column_widths(
        total_width: int,
        *,
        preferred_widths: tuple[int, ...],
        minimum_widths: tuple[int, ...],
    ) -> tuple[int, ...]:
        if not preferred_widths:
            return ()
        if total_width <= 0:
            return tuple(1 for _ in preferred_widths)
        if len(preferred_widths) != len(minimum_widths):
            raise ValueError("Column width preferences and minimums must have the same length.")

        bounded_total = max(len(preferred_widths), total_width)
        if sum(minimum_widths) > bounded_total:
            base_width = bounded_total // len(preferred_widths)
            widths = [max(1, base_width) for _ in preferred_widths]
            remainder = max(0, bounded_total - sum(widths))
            for index in range(remainder):
                widths[index % len(widths)] += 1
            return tuple(widths)

        widths = list(minimum_widths)
        remaining_width = bounded_total - sum(widths)
        for index, preferred_width in enumerate(preferred_widths):
            if remaining_width <= 0:
                break
            additional_width = min(max(0, preferred_width - widths[index]), remaining_width)
            widths[index] += additional_width
            remaining_width -= additional_width
        return tuple(widths)

    def _server_manager_column_widths(
        self,
        servers: list[ServerEntry],
        *,
        active_server_id: str | None,
    ) -> dict[str, int]:
        protocol_width = max((self._display_width(server.protocol.upper()) for server in servers), default=5)
        status_width = max(
            (
                self._display_width(self._server_status_label(server, active_server_id=active_server_id))
                for server in servers
            ),
            default=8,
        )
        ping_width = max((self._display_width(self._cached_tcp_ping_label(server)) for server in servers), default=6)
        protocol_width = min(max(5, protocol_width), 10)
        status_width = min(max(7, status_width), 10)
        ping_width = min(max(5, ping_width), 10)

        preferred_name_width = min(
            36,
            max((self._display_width(self._ui_server_name(server.name)) for server in servers), default=18),
        )
        preferred_address_width = min(
            40,
            max((self._display_width(f"{server.host}:{server.port}") for server in servers), default=18),
        )
        preferred_source_width = min(
            30,
            max((self._display_width(self._server_source_label(server)) for server in servers), default=12),
        )

        separator_width = self._display_width(" | ") * 5
        fixed_columns_width = protocol_width + status_width + ping_width + separator_width
        available_width = max(18, self.console.width - 6)
        flexible_width = max(3, available_width - fixed_columns_width)
        name_width, address_width, source_width = self._allocate_column_widths(
            flexible_width,
            preferred_widths=(preferred_name_width, preferred_address_width, preferred_source_width),
            minimum_widths=(4, 4, 4),
        )
        return {
            "name_width": name_width,
            "protocol_width": protocol_width,
            "address_width": address_width,
            "source_width": source_width,
            "status_width": status_width,
            "ping_width": ping_width,
        }

    def _server_manager_choice_title(
        self,
        server: ServerEntry,
        *,
        active_server_id: str | None,
        name_width: int | None = None,
        protocol_width: int | None = None,
        address_width: int | None = None,
        source_width: int | None = None,
        status_width: int | None = None,
        ping_width: int | None = None,
    ) -> str:
        if None in (name_width, protocol_width, address_width, source_width, status_width, ping_width):
            widths = self._server_manager_column_widths([server], active_server_id=active_server_id)
            name_width = widths["name_width"]
            protocol_width = widths["protocol_width"]
            address_width = widths["address_width"]
            source_width = widths["source_width"]
            status_width = widths["status_width"]
            ping_width = widths["ping_width"]

        assert name_width is not None
        assert protocol_width is not None
        assert address_width is not None
        assert source_width is not None
        assert status_width is not None
        assert ping_width is not None

        return " | ".join(
            (
                self._pad_display_width(
                    self._truncate_display_width(self._ui_server_name(server.name), name_width),
                    name_width,
                ),
                self._pad_display_width(
                    self._truncate_display_width(server.protocol.upper(), protocol_width),
                    protocol_width,
                ),
                self._pad_display_width(
                    self._truncate_display_width(f"{server.host}:{server.port}", address_width),
                    address_width,
                ),
                self._pad_display_width(
                    self._truncate_display_width(self._server_source_label(server), source_width),
                    source_width,
                ),
                self._pad_display_width(
                    self._truncate_display_width(
                        self._server_status_label(server, active_server_id=active_server_id),
                        status_width,
                    ),
                    status_width,
                ),
                self._pad_display_width(
                    self._truncate_display_width(self._cached_tcp_ping_label(server), ping_width),
                    ping_width,
                ),
            )
        )

    def _server_details_panel(
        self,
        server: ServerEntry,
        *,
        parent_subscription: SubscriptionEntry | None = None,
    ) -> Panel:
        current_state = self._current_state()
        active_server_id = current_state.server_id if current_state.is_running else None
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Параметр", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(34, self.console.width - 34))
        table.add_row("Имя", self._ui_server_name(server.name))
        table.add_row("Протокол", server.protocol.upper())
        table.add_row("Адрес", f"{server.host}:{server.port}")
        if server.is_amneziawg:
            table.add_row("Профиль", "AmneziaWG", style="blue")
        table.add_row("Источник", self._server_source_label(server), style="dim")
        table.add_row("Статус", self._server_status_label(server, active_server_id=active_server_id), style=self._server_row_style(server, active_server_id=active_server_id))
        table.add_row("Создан", self._shorten_text(server.created_at, 19), style="dim")
        if "tcp_ping_checked_at" in server.extra:
            table.add_row("TCP ping", self._cached_tcp_ping_label(server), style=self._server_ping_style(server))
            table.add_row("Проверено", self._shorten_text(str(server.extra.get("tcp_ping_checked_at") or "-"), 19), style="dim")
        if parent_subscription is not None:
            table.add_row("Подписка", self._ui_subscription_title(parent_subscription.title), style="cyan")
        if server.source == "subscription":
            note = "После обновления подписки параметры сервера могут измениться."
            if server.extra.get("stale"):
                note = "Сервер исчез из последней версии подписки и сохранен как устаревший."
            table.add_row("Примечание", note, style="yellow" if server.extra.get("stale") else "dim")
        return Panel.fit(
            table,
            title=f"Сервер: {self._ui_server_name(server.name)}",
            border_style=self._server_panel_border_style(server, active_server_id=active_server_id),
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

    @staticmethod
    def _server_status_label(server: ServerEntry, *, active_server_id: str | None) -> str:
        if server.id == active_server_id:
            return "Активен"
        if server.extra.get("stale"):
            return "Устарел"
        return "Ожидание"

    def _server_row_style(self, server: ServerEntry, *, active_server_id: str | None) -> str | None:
        if server.id == active_server_id:
            return "bold green"
        if server.extra.get("stale"):
            return "yellow"
        ping_style = self._server_ping_style(server)
        if ping_style == "red":
            return ping_style
        return None

    def _server_ping_style(self, server: ServerEntry) -> str | None:
        if server.extra.get("tcp_ping_ok") and server.extra.get("tcp_ping_ms") is not None:
            return "green"
        error = str(server.extra.get("tcp_ping_error") or "").strip()
        if not error:
            return None
        if error == TCP_PING_UNSUPPORTED_ERROR:
            return "yellow"
        return "red"

    def _server_panel_border_style(self, server: ServerEntry, *, active_server_id: str | None) -> str:
        if server.id == active_server_id:
            return "green"
        if server.extra.get("stale"):
            return "yellow"
        ping_style = self._server_ping_style(server)
        if ping_style in {"green", "yellow", "red"}:
            return ping_style
        return "cyan"

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
        table.add_column("Название", overflow="fold", max_width=max(20, self.console.width - 64))
        table.add_column("Источник", no_wrap=True, overflow="ellipsis", max_width=22)
        table.add_column("Серверы", no_wrap=True)
        table.add_column("Статус", no_wrap=True)
        for subscription in subscriptions:
            table.add_row(
                self._layout_safe_text(subscription.title),
                self._subscription_source_label(subscription),
                self._subscription_servers_label(self._subscription_server_count(subscription)),
                self._subscription_status_label(subscription),
                style=self._subscription_row_style(subscription),
            )
        return table

    def _subscription_choice_title(self, subscription: SubscriptionEntry) -> str:
        source_label = self._subscription_source_label(subscription)
        servers_label = self._subscription_servers_label(self._subscription_server_count(subscription))
        status_label = self._subscription_status_label(subscription)
        suffix = f" | {source_label} | {servers_label} | {status_label}"
        title_width = max(18, self.console.width - 10 - self._display_width(suffix))
        title = self._truncate_display_width(self._layout_safe_text(subscription.title), title_width)
        return self._truncate_display_width(
            f"{title}{suffix}",
            max(18, self.console.width - 10),
        )

    def _subscription_status_label(self, subscription: SubscriptionEntry) -> str:
        if subscription.last_error:
            return "Нужна проверка"
        if not self._subscription_server_count(subscription):
            return "Пустая"
        return "Готова"

    def _subscription_row_style(self, subscription: SubscriptionEntry) -> str:
        if subscription.last_error:
            return "bold red"
        if not self._subscription_server_count(subscription):
            return "yellow"
        return "green"

    def _subscription_panel_border_style(self, subscription: SubscriptionEntry) -> str:
        if subscription.last_error:
            return "red"
        if not self._subscription_server_count(subscription):
            return "yellow"
        return "cyan"

    def _subscription_details_panel(self, subscription: SubscriptionEntry) -> Panel:
        subscription_servers = self._subscription_servers(subscription.id)
        server_count = len(subscription_servers)
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Параметр", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(34, self.console.width - 34))
        table.add_row("Название", self._ui_subscription_title(subscription.title))
        table.add_row("Источник", self._subscription_source_label(subscription), style="dim")
        table.add_row("URL", subscription.url)
        table.add_row("Состояние", self._subscription_status_label(subscription), style=self._subscription_row_style(subscription))
        table.add_row("Серверы", self._subscription_servers_label(server_count), style="green" if subscription_servers else "yellow")
        table.add_row("Что это значит", self._subscription_status_hint(subscription), style="dim")
        table.add_row("Последнее обновление", self._shorten_text(subscription.updated_at, 19), style="dim")
        table.add_row("Следующий шаг", self._subscription_next_step(subscription), style="cyan" if not subscription.last_error else "yellow")
        if subscription.last_error:
            table.add_row("Последняя ошибка", subscription.last_error, style="red")
            table.add_row("Когда возникла", self._shorten_text(subscription.last_error_at or "-", 19), style="dim")
        return Panel.fit(
            table,
            title=f"Подписка: {self._ui_subscription_title(subscription.title)}",
            border_style=self._subscription_panel_border_style(subscription),
        )

    def _subscription_server_count(self, subscription: SubscriptionEntry) -> int:
        return len(self._subscription_servers(subscription.id))

    @classmethod
    def _subscription_servers_label(cls, count: int) -> str:
        return f"{count} {cls._pluralize_ru(count, 'сервер', 'сервера', 'серверов')}"

    @staticmethod
    def _pluralize_ru(count: int, one: str, few: str, many: str) -> str:
        normalized = abs(int(count)) % 100
        if 11 <= normalized <= 14:
            return many
        remainder = normalized % 10
        if remainder == 1:
            return one
        if 2 <= remainder <= 4:
            return few
        return many

    def _subscription_source_label(self, subscription: SubscriptionEntry) -> str:
        parsed = urlparse(subscription.url)
        source = (parsed.netloc or "").strip().lower() or subscription.url.strip()
        return self._shorten_text(source or "-", 22)

    def _subscription_next_step(self, subscription: SubscriptionEntry) -> str:
        if subscription.last_error:
            return "Проверьте URL и запустите обновление снова."
        if not self._subscription_server_count(subscription):
            return "Попробуйте обновить подписку или проверить источник."
        return "Можно открыть список серверов или обновить подписку вручную."

    def _subscription_status_hint(self, subscription: SubscriptionEntry) -> str:
        if subscription.last_error:
            return "Последняя попытка обновления завершилась ошибкой."
        if not self._subscription_server_count(subscription):
            return "Подписка сохранена, но серверы пока не загружены."
        return "Подписка загружена и готова к использованию."

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
            "случайные и скрыты для каждой сессии" if settings.connection_mode == "PROXY" else "Не используются",
        )
        table.add_row(
            "SOCKS5",
            "включается только с аутентификацией" if settings.connection_mode == "PROXY" else "Не используется",
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
            "Подписки при запуске",
            "Обновлять автоматически" if settings.auto_update_subscriptions_on_startup else "не обновлять",
        )
        table.add_row(
            "Маршрут",
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
        table.add_row("Маршрут", routing_name)
        table.add_row("Этап", step)
        self._render_screen()
        self.console.print(
            Panel.fit(
                table,
                title="Идет подключение",
                border_style="cyan",
            )
        )

    def _show_runtime_auto_install_notice(
        self,
        *,
        components: list[str],
        title: str,
        server_name: str | None = None,
        routing_name: str | None = None,
    ) -> None:
        if not components:
            return
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Параметр", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(34, self.console.width - 34))
        if server_name is not None:
            table.add_row("Сервер", server_name)
        if routing_name is not None:
            table.add_row("Маршрут", routing_name)
        table.add_row("Отсутствует", ", ".join(components))
        table.add_row(
            "Действие",
            "Клиент автоматически подготовит недостающие компоненты и при необходимости догрузит их. Это может занять некоторое время.",
        )
        self._render_screen()
        self.console.print(Panel.fit(table, title=title, border_style="yellow"))

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
        runtime_label = self._backend_engine_title(self._runtime_backend_id(state))
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
        state = self._load_runtime_state_or_recover()
        if state.system_proxy_enabled:
            state.system_proxy_enabled = False
            state.previous_system_proxy = None
            self._save_runtime_state(state)
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

    def _xray_component_label(self) -> str:
        if not XRAY_EXECUTABLE.exists():
            return "Xray-core: отсутствует"
        version = self._xray_version_text()
        if version is None:
            return "Xray-core: есть (версия: неизвестна)"
        return f"Xray-core: есть ({self._display_version(version)})"

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
            settings.auto_update_subscriptions_on_startup = self._coerce_bool(
                settings.auto_update_subscriptions_on_startup
            )
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
        self._best_effort_stop_managed_instances(getattr(self, "process_manager", None))
        self._best_effort_stop_managed_instances(getattr(self, "singbox_process_manager", None))
        self._best_effort_stop_managed_instances(getattr(self, "amneziawg_process_manager", None))
        self._best_effort_disable_vynex_proxy()
        try:
            self._reset_runtime_state()
        except Exception:
            pass
        state_file = getattr(error, "path", None)
        state_label = state_file.name if isinstance(state_file, Path) else "runtime_state.json"
        self._runtime_notice = RuntimeNotice(
            message=(
                f"Файл состояния '{state_label}' был поврежден и не восстановился автоматически. "
                "Активное подключение сброшено, локальный runtime и системный proxy очищены best-effort."
            ),
        )

    def _best_effort_stop_managed_instances(self, manager: object | None) -> None:
        if manager is None:
            return
        list_running_instances = getattr(manager, "list_running_instances", None)
        stop = getattr(manager, "stop", None)
        if not callable(list_running_instances) or not callable(stop):
            return
        target_paths = self._manager_target_paths(manager)
        try:
            instances = tuple(list_running_instances())
        except Exception:
            return
        for instance in instances:
            pid = getattr(instance, "pid", None)
            executable_path = self._normalize_fs_path(getattr(instance, "executable_path", None))
            if not pid:
                continue
            if target_paths and executable_path not in target_paths:
                continue
            try:
                stop(pid)
            except Exception:
                continue

    def _best_effort_disable_vynex_proxy(self) -> None:
        proxy_manager = getattr(self, "system_proxy_manager", None)
        if proxy_manager is None:
            return
        try:
            snapshot = proxy_manager.snapshot()
        except Exception:
            return
        if not WindowsSystemProxyManager.is_vynex_managed_state(snapshot):
            return
        try:
            proxy_manager.disable_proxy()
        except Exception:
            pass

    def _manager_target_paths(self, manager: object) -> set[str]:
        target_paths: set[str] = set()
        normalize_path = getattr(type(manager), "_normalize_path", None)
        if not callable(normalize_path):
            normalize_path = self._normalize_fs_path
        executable_path = getattr(manager, "_executable_path", None)
        if executable_path is not None:
            normalized = normalize_path(str(executable_path))
            if normalized:
                target_paths.add(normalized)
        iter_candidates = getattr(type(manager), "_iter_executable_candidates", None)
        if callable(iter_candidates):
            try:
                candidates = tuple(iter_candidates(manager))
            except Exception:
                candidates = ()
            for candidate in candidates:
                normalized = normalize_path(str(candidate))
                if normalized:
                    target_paths.add(normalized)
        return target_paths

    @staticmethod
    def _normalize_fs_path(value: object) -> str | None:
        raw_value = str(value or "").strip()
        if not raw_value:
            return None
        try:
            return str(Path(raw_value).resolve()).lower()
        except OSError:
            return str(Path(raw_value)).lower()

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

    @staticmethod
    def _connection_mode_short_label(value: str) -> str:
        return "TUN" if str(value).upper() == "TUN" else "PROXY"

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
        if backend_id == "singbox":
            return "sing-box"
        return "xray"

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
            missing_components = self._missing_startup_runtime_components()
            if mode == "TUN" and not WINTUN_DLL.exists():
                missing_components.append("wintun.dll")
            if missing_components:
                self._show_runtime_auto_install_notice(
                    components=missing_components,
                    title="Подготовка runtime",
                )
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
        missing_components = self._missing_connection_runtime_components(connection_profile)
        if missing_components:
            self._show_runtime_auto_install_notice(
                components=missing_components,
                title="Идет подключение",
                server_name=self._ui_server_name(server.name),
                routing_name=self._routing_display_name(backend.backend_id, getattr(routing_profile, "name", None)),
            )
        backend.ensure_runtime_ready(
            connection_profile
        )

    @staticmethod
    def _missing_startup_runtime_components() -> list[str]:
        missing_components: list[str] = []
        if not XRAY_EXECUTABLE.exists():
            missing_components.append("Xray-core (xray.exe)")
        if not GEOIP_PATH.exists():
            missing_components.append("geoip.dat")
        if not GEOSITE_PATH.exists():
            missing_components.append("geosite.dat")
        return missing_components

    def _missing_connection_runtime_components(self, profile: BackendConnectionProfile) -> list[str]:
        backend = self._backend_for_connection(profile)
        if backend.backend_id == "singbox":
            return ["sing-box (sing-box.exe)"] if not SINGBOX_EXECUTABLE.exists() else []
        if backend.backend_id == "amneziawg":
            missing_components: list[str] = []
            if not AMNEZIAWG_EXECUTABLE.exists():
                missing_components.append("AmneziaWG (amneziawg.exe)")
            if not AMNEZIAWG_EXECUTABLE_FALLBACK.exists():
                missing_components.append("AWG helper (awg.exe)")
            if not AMNEZIAWG_WINTUN_DLL.exists():
                missing_components.append("wintun.dll")
            return missing_components
        missing_components = self._missing_startup_runtime_components()
        if profile.normalized_mode == "TUN" and not WINTUN_DLL.exists():
            missing_components.append("wintun.dll")
        return missing_components

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
        return VynexVpnApp._styled_choice(choice, style_class="terminal-danger")

    @staticmethod
    def _styled_choice(choice: object, *, style_class: str) -> object:
        if isinstance(choice, str):
            styled_choice = Choice(title=choice, value=choice)
            setattr(styled_choice, "_vynex_style_class", style_class)
            return styled_choice
        if isinstance(choice, Choice):
            setattr(choice, "_vynex_style_class", style_class)
            return choice
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
        style_rules.append(("best-ping", "fg:ansigreen bold"))
        style_rules.append(("settings-value", "fg:ansiblue bold"))
        style_rules.append(("instruction", "fg:ansicyan"))
        return Style(style_rules)

    @staticmethod
    def _shortcut_action_key_variants(keys_to_bind: tuple[object, ...]) -> tuple[object, ...]:
        variants: list[object] = []
        reverse_layout_map = {value: key for key, value in PHYSICAL_KEY_LAYOUT_MAP.items()}
        for key in keys_to_bind:
            if key not in variants:
                variants.append(key)
            if not isinstance(key, str) or len(key) != 1:
                continue
            normalized_key = key.lower()
            mapped_key = PHYSICAL_KEY_LAYOUT_MAP.get(normalized_key) or reverse_layout_map.get(normalized_key)
            if mapped_key is not None and mapped_key not in variants:
                variants.append(mapped_key)
        return tuple(variants)

    @staticmethod
    def _shortcut_action_binding_variants(keys_to_bind: tuple[object, ...]) -> tuple[tuple[object, ...], ...]:
        if len(keys_to_bind) != 1:
            return (keys_to_bind,)
        return tuple((variant,) for variant in VynexVpnApp._shortcut_action_key_variants(keys_to_bind))

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
        shortcut_actions: list[tuple[tuple[object, ...], str]] | None = None,
        activate_search_on: tuple[object, ...] = (),
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
        ic = TerminalInquirerControl(
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
        search_active = Condition(lambda: ic.search_filter is not None)
        search_inactive = ~search_active

        @bindings.add(Keys.ControlQ, eager=True)
        @bindings.add(Keys.ControlC, eager=True)
        def abort_prompt(event):
            event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

        if activate_search_on:

            def activate_search(event):
                if ic.search_filter is None:
                    ic.search_filter = ""

            for key in activate_search_on:
                bindings.add(key, eager=True, filter=search_inactive)(activate_search)

            @bindings.add(Keys.Escape, eager=True, filter=search_active)
            def clear_search(event):
                ic.search_filter = None

        if back_choice_value is not None:

            @bindings.add(Keys.Escape, eager=True, filter=search_inactive)
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
                    @bindings.add(keys, eager=True, filter=search_inactive)
                    def select_choice(event):
                        ic.pointed_at = choice_index

                _reg_binding(index, choice.shortcut_key)

        if shortcut_actions:
            for keys, action_name in shortcut_actions:

                def _register_action(keys_to_bind, registered_action):
                    for binding_keys in VynexVpnApp._shortcut_action_binding_variants(tuple(keys_to_bind)):

                        @bindings.add(*binding_keys, eager=True, filter=search_inactive)
                        def trigger_action(event):
                            ic.is_answered = True
                            event.app.exit(
                                result=SelectActionResult(
                                    action=registered_action,
                                    value=ic.get_pointed_at().value,
                                )
                            )

                _register_action(keys, action_name)

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
        elif activate_search_on:

            def search_filter(event):
                ic.add_search_character(event.key_sequence[0].key)

            for character in string.printable:
                bindings.add(character, eager=True, filter=search_active)(search_filter)
            bindings.add(Keys.Backspace, eager=True, filter=search_active)(search_filter)

        if use_arrow_keys:
            bindings.add(Keys.Down, eager=True)(move_cursor_down)
            bindings.add(Keys.Up, eager=True)(move_cursor_up)
        if use_jk_keys:
            bindings.add("j", eager=True, filter=search_inactive)(move_cursor_down)
            bindings.add("k", eager=True, filter=search_inactive)(move_cursor_up)
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
        kwargs.setdefault("instruction", " ")
        return VynexVpnApp._select_with_escape_back(message, **kwargs)

    @staticmethod
    def _server_manager_instruction() -> str:
        return "Enter - открыть, Del - удалить, E - редактировать, R - обновить ping у всех, / - фильтр, Esc - назад"

    @staticmethod
    def _subscription_manager_instruction() -> str:
        return "Enter - открыть, Del - удалить, E - редактировать, R - обновить, / - фильтр, Esc - назад"

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
