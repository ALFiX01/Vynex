from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Callable
from urllib.parse import urlparse

import questionary
from questionary import Choice, Style
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from wcwidth import wcswidth

from .config_builder import XrayConfigBuilder
from .constants import (
    APP_NAME,
    APP_VERSION,
    GEOIP_PATH,
    GEOSITE_PATH,
    LOGO_FILE,
    SUBSCRIPTION_TITLE_BY_HOST,
    XRAY_EXECUTABLE,
    XRAY_CONFIG,
)
from .healthcheck import HealthcheckResult, XrayHealthChecker
from .core import XrayInstaller
from .models import AppSettings, RuntimeState, ServerEntry, SubscriptionEntry, utc_now_iso
from .parsers import parse_share_link
from .process_manager import XrayProcessManager
from .routing_profiles import RoutingProfileManager
from .storage import JsonStorage
from .subscriptions import SubscriptionManager
from .system_proxy import SystemProxyState, WindowsSystemProxyManager
from .utils import clamp_port, is_port_available

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
        self.subscription_manager = SubscriptionManager(self.storage)
        self.routing_profiles = RoutingProfileManager()
        self.config_builder = XrayConfigBuilder()
        self.process_manager = XrayProcessManager()
        self.health_checker = XrayHealthChecker()
        self.system_proxy_manager = WindowsSystemProxyManager()
        self.logo = self._load_logo()

    def run(self) -> int:
        try:
            self._ensure_xray_ready()
            self._reconcile_runtime_state()
            while True:
                self._render_screen()
                action = self._ask_main_menu()
                if action is None:
                    return 0
                if action.title == "Выход":
                    return 0
                action.handler()
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
                    str(exc),
                    title="Ошибка установки Xray-core",
                    border_style="red",
                )
            )
            raise SystemExit(1) from exc
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
        self.console.print()

    def _ask_main_menu(self) -> MenuAction | None:
        actions = [
            MenuAction("Подключиться", self.connect_flow),
            MenuAction("Отключиться", self.disconnect_flow),
            MenuAction("Сервера и подписки", self.server_subscription_flow),
            MenuAction("Компоненты", self.components_flow),
            MenuAction("Настройки", self.settings_flow),
            MenuAction("Статус", self.status_flow),
            MenuAction("Выход", lambda: None),
        ]
        selected_title = self._select(
            "Главное меню",
            choices=[action.title for action in actions],
            use_shortcuts=True
        ).ask()
        if selected_title is None:
            return None
        return next(action for action in actions if action.title == selected_title)

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
                        "Список серверов пуст. Добавьте сервер вручную или через подписку.",
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
            choices.append(Choice(title="Добавить сервер (Ссылка)", value="__add__"))
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
                        "Список подписок пуст. Добавьте первую подписку по URL.",
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
            choices.append(Choice(title="Добавить подписку (URL)", value="__add__"))
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

    def connect_flow(self) -> None:
        servers = self.storage.load_servers()
        if not servers:
            self._render_screen()
            self.console.print(
                Panel.fit(
                    "Сначала добавьте хотя бы один сервер вручную или через подписку.",
                    title="Нет серверов",
                    border_style="yellow",
                )
            )
            self._pause()
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
        mode = "PROXY"
        socks_port = None
        http_port = None
        pid: int | None = None
        use_system_proxy = False
        system_proxy_applied = False
        previous_system_proxy: SystemProxyState | None = None
        try:
            self._show_connection_progress(
                self._ui_server_name(selected_server.name),
                routing_profile.name,
                "Подготовка параметров подключения...",
            )
            settings = self._validated_settings()
            socks_port = settings.proxy_socks_port
            http_port = settings.proxy_http_port
            self._disconnect_runtime(silent=True)
            self.process_manager.ensure_no_running_instances()
            if not is_port_available(socks_port):
                raise ValueError(f"Порт {socks_port} уже занят.")
            if not is_port_available(http_port):
                raise ValueError(f"Порт {http_port} уже занят.")
            use_system_proxy = settings.set_system_proxy
            if use_system_proxy:
                previous_system_proxy = self.system_proxy_manager.snapshot()
            config = self.config_builder.build(
                server=selected_server,
                mode=mode,
                routing_profile=routing_profile,
                socks_port=socks_port,
                http_port=http_port,
            )
            self.config_builder.write(config, XRAY_CONFIG)
            self._show_connection_progress(
                self._ui_server_name(selected_server.name),
                routing_profile.name,
                "Запуск Xray-core...",
            )
            pid = self.process_manager.start(XRAY_CONFIG)
            self._show_connection_progress(
                self._ui_server_name(selected_server.name),
                routing_profile.name,
                "Проверка доступности сети через Xray...",
            )
            health_result = self._run_healthcheck(mode=mode, http_port=http_port)
            if not health_result.ok:
                self.process_manager.stop(pid)
                raise RuntimeError(
                    "Xray запущен, но health-check не прошел.\n"
                    f"Детали: {health_result.message}"
                )
            if mode == "PROXY" and use_system_proxy:
                if http_port is None or socks_port is None:
                    raise RuntimeError("Для системного proxy не определены локальные порты.")
                self._show_connection_progress(
                    self._ui_server_name(selected_server.name),
                    routing_profile.name,
                    "Применение системного proxy Windows...",
                )
                self.system_proxy_manager.enable_proxy(http_port=http_port, socks_port=socks_port)
                system_proxy_applied = True
            state = RuntimeState(
                pid=pid,
                mode=mode,
                server_id=selected_server.id,
                started_at=utc_now_iso(),
                socks_port=socks_port,
                http_port=http_port,
                system_proxy_enabled=use_system_proxy,
                previous_system_proxy=previous_system_proxy.to_dict() if previous_system_proxy else None,
                routing_profile_id=routing_profile.profile_id,
                routing_profile_name=routing_profile.name,
            )
            self.storage.save_runtime_state(state)
            detail_rows = [
                ("Сервер", self._ui_server_name(selected_server.name)),
                ("Протокол", selected_server.protocol.upper()),
                ("Режим", "PROXY"),
                ("Маршрутизация", routing_profile.name),
                ("PID", str(pid)),
                ("SOCKS5", f"127.0.0.1:{socks_port}"),
                ("HTTP", f"127.0.0.1:{http_port}"),
                ("Системный proxy", "включен" if use_system_proxy else "не изменялся"),
            ]
            if health_result.checked_url:
                detail_rows.append(("Health-check", health_result.checked_url))
            self._render_screen()
            self.console.print(
                Panel.fit(
                    self._key_value_group(detail_rows),
                    title="Подключение установлено",
                    border_style="green",
                )
            )
            self._pause()
        except Exception as exc:  # noqa: BLE001
            if pid:
                self.process_manager.stop(pid)
            if system_proxy_applied:
                self.system_proxy_manager.restore(previous_system_proxy)
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
        link = questionary.text("Вставьте ссылку сервера").ask()
        if not link:
            return
        try:
            server = self.storage.upsert_server(parse_share_link(link))
            self._show_server_saved("Сервер сохранен", server)
        except Exception as exc:  # noqa: BLE001
            self._render_screen()
            self._show_error("Ошибка парсинга", exc)
            self._pause()

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
                choices = [
                    "Переименовать",
                    "Изменить ссылку",
                    "Удалить сервер",
                ]
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
        raw_name = questionary.text("Новое имя сервера", default=server.name).ask()
        if raw_name is None:
            raise ValueError("Переименование отменено.")
        new_name = raw_name.strip() or server.name
        if new_name == server.name:
            return
        server.name = new_name
        self.storage.upsert_server(server)
        self._show_server_saved("Сервер обновлен", server)

    def _edit_server_link_flow(self, server: ServerEntry) -> None:
        if server.source != "manual":
            raise ValueError("Ссылку можно менять только у ручных серверов.")
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
        subscription_name = subscription.title if subscription else "неизвестной подписки"
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
        table.add_row("Подписка", parent_subscription.title if parent_subscription else subscription_name)
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
        url = questionary.text("Введите URL подписки").ask()
        if not url:
            return
        normalized_url = url.strip()
        if not normalized_url:
            return
        existing = self.storage.get_subscription_by_url(normalized_url)
        default_title = existing.title if existing else self._subscription_default_title(normalized_url)
        raw_title = questionary.text("Название подписки", default=default_title).ask()
        if raw_title is None:
            return
        title = raw_title or default_title
        subscription = existing or SubscriptionEntry.new(url=normalized_url, title=title)
        subscription.url = normalized_url
        subscription.title = title
        try:
            imported = self._refresh_subscription(subscription)
            self._show_subscription_refresh_success("Подписка сохранена", subscription, imported)
        except Exception as exc:  # noqa: BLE001
            if existing is not None:
                self._record_subscription_error(existing, exc)
            self._render_screen()
            self._show_error("Ошибка подписки", exc)
            self._pause()

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
        raw_title = questionary.text("Новое название подписки", default=subscription.title).ask()
        if raw_title is None:
            raise ValueError("Переименование отменено.")
        title = raw_title.strip() or subscription.title
        if title == subscription.title:
            return
        subscription.title = title
        self.storage.upsert_subscription(subscription)
        self._render_screen()
        self.console.print(
            Panel.fit(
                f"Новое название: {subscription.title}",
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
                    title=subscription.title,
                    border_style="yellow",
                )
            )
            self._pause()
            return
        table = Table(title=f"Серверы подписки: {subscription.title}")
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
            f"Подтвердите: {action_text} '{subscription.title}'?",
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
        table.add_row("Подписка", deleted_subscription.title)
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
                    f"SOCKS порт: {settings.proxy_socks_port}",
                    f"HTTP порт: {settings.proxy_http_port}",
                    "Системный proxy: Вкл" if settings.set_system_proxy else "Системный proxy: Выкл",
                    f"Набор маршрутизации: {active_routing_name}",
                    "Сбросить системный proxy",
                    "Назад",
                ],
                use_shortcuts=True,
            ).ask()
            if selected_action in (None, "Назад"):
                return
            try:
                if selected_action.startswith("SOCKS порт:"):
                    new_port = self._prompt_port("SOCKS порт", settings.proxy_socks_port)
                    if new_port == settings.proxy_http_port:
                        raise ValueError("SOCKS и HTTP порты не должны совпадать.")
                    settings.proxy_socks_port = new_port
                    self.storage.save_settings(settings)
                    self._show_settings_saved(settings)
                elif selected_action.startswith("HTTP порт:"):
                    new_port = self._prompt_port("HTTP порт", settings.proxy_http_port)
                    if new_port == settings.proxy_socks_port:
                        raise ValueError("SOCKS и HTTP порты не должны совпадать.")
                    settings.proxy_http_port = new_port
                    self.storage.save_settings(settings)
                    self._show_settings_saved(settings)
                elif selected_action.startswith("Системный proxy:"):
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
                table.add_row(subscription.title, str(count))
            self.console.print(table)
        if failed:
            table = Table(title="Ошибки обновления")
            table.add_column("Название")
            table.add_column("Ошибка")
            table.add_column("Что сделать", overflow="fold", max_width=max(28, self.console.width - 54))
            for subscription, error in failed:
                _, actions, _ = self._error_guidance("Ошибка подписки", error)
                table.add_row(subscription.title, error, actions[0] if actions else "-")
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
        if not state.is_running:
            table = Table(show_header=False, box=None)
            table.add_row("Xray", "Не запущен")
            table.add_row("SOCKS5", f"127.0.0.1:{settings.proxy_socks_port}")
            table.add_row("HTTP", f"127.0.0.1:{settings.proxy_http_port}")
            table.add_row("Системный proxy", "Авто" if settings.set_system_proxy else "Выкл")
            table.add_row("Routing", self._active_routing_profile_name())
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
        table.add_row("Процесс", "Запущен")
        table.add_row("PID", str(state.pid))
        table.add_row("Режим", state.mode or "-")
        table.add_row("Сервер", self._ui_server_name(server.name) if server else "-")
        table.add_row("Адрес", f"{server.host}:{server.port}" if server else "-")
        table.add_row("Протокол", server.protocol.upper() if server else "-")
        table.add_row("Старт", state.started_at or "-")
        table.add_row("Routing", state.routing_profile_name or self._active_routing_profile_name())
        if state.mode == "PROXY":
            table.add_row("SOCKS5", f"127.0.0.1:{state.socks_port}")
            table.add_row("HTTP", f"127.0.0.1:{state.http_port}")
            table.add_row("Системный proxy", "Да" if state.system_proxy_enabled else "Нет")
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
        if state.pid and not self.process_manager.is_running(state.pid):
            self._restore_system_proxy(state)
            self.storage.save_runtime_state(RuntimeState())
            return RuntimeState()
        return state

    def _reconcile_runtime_state(self) -> None:
        self._current_state()

    def _disconnect_runtime(self, *, silent: bool = False) -> None:
        state = self.storage.load_runtime_state()
        if state.pid:
            self.process_manager.stop(state.pid)
        self._restore_system_proxy(state)
        self.storage.save_runtime_state(RuntimeState())
        if not silent:
            self.console.print(Panel.fit("Подключение остановлено.", border_style="green"))

    def _shutdown(self) -> None:
        self._disconnect_runtime(silent=True)

    def _run_healthcheck(self, *, mode: str, http_port: int | None) -> HealthcheckResult:
        self._render_screen()
        self.console.print("[cyan]Проверка доступности сети через Xray...[/cyan]")
        if http_port is None:
            raise RuntimeError("Для Proxy режима не определен HTTP порт health-check.")
        return self.health_checker.verify_proxy(http_port=http_port)

    def _restore_system_proxy(self, state: RuntimeState) -> None:
        if not state.system_proxy_enabled:
            return
        previous_state = SystemProxyState.from_dict(state.previous_system_proxy)
        self.system_proxy_manager.restore(previous_state)

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

    def _banner_status_line(self) -> str:
        state = self._current_state()
        settings = self._validated_settings(raise_on_error=False)
        routing_name = escape(self._shorten_text(self._active_routing_profile_name(), 28))
        if state.is_running:
            server = self.storage.get_server(state.server_id) if state.server_id else None
            server_name = escape(
                self._shorten_text(
                    self._ui_server_name(server.name) if server else "сервер недоступен",
                    32,
                )
            )
            return (
                "[bold]Статус:[/bold] [green]Подключено[/green]"
                f" | [bold]Сервер:[/bold] {server_name}"
                f" | [bold]Маршрут:[/bold] {routing_name}"
            )
        proxy_mode = "авто" if settings.set_system_proxy else "выкл"
        return (
            "[bold]Статус:[/bold] [yellow]Не подключено[/yellow]"
            f" | [bold]Маршрут:[/bold] {routing_name}"
        )

    def _banner_border_style(self) -> str:
        return "green" if self._current_state().is_running else "cyan"

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

    def _error_guidance(self, title: str, error: Exception | str) -> tuple[str, list[str], str]:
        details = self._error_text(error)
        normalized = details.lower()

        if title == "Ошибка подключения":
            if "порт" in normalized and "занят" in normalized:
                return (
                    "Локальный proxy-порт уже используется другим приложением.",
                    [
                        "Откройте 'Настройки' и задайте свободные SOCKS/HTTP порты.",
                        "Либо закройте приложение, которое уже слушает этот порт, и повторите подключение.",
                    ],
                    details,
                )
            if "health-check" in normalized:
                return (
                    "Xray запустился, но клиент не смог подтвердить доступ в сеть через него.",
                    [
                        "Попробуйте другой сервер из списка.",
                        "Если проблема повторяется, обновите 'Компоненты' и проверьте доступ в интернет.",
                        "При необходимости временно отключите системный proxy в 'Настройки' и попробуйте снова.",
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
                        "Используйте ссылку формата vless://, vmess:// или ss://.",
                        "Если это URL подписки, добавляйте его через пункт 'Добавить подписку (URL)'.",
                    ],
                    details,
                )
            if "vmess" in normalized or "vless" in normalized or "shadowsocks" in normalized:
                return (
                    "Ссылка сервера повреждена или заполнена не полностью.",
                    [
                        "Проверьте, что ссылка скопирована целиком без лишних символов.",
                        "Если ссылка пришла из подписки или мессенджера, попробуйте скопировать ее заново.",
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
                        "Убедитесь, что подписка содержит vless://, vmess:// или ss:// ссылки.",
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
                    "SOCKS и HTTP порты настроены с конфликтом.",
                    [
                        "Укажите разные значения для SOCKS и HTTP.",
                        "Если не уверены, оставьте стандартные 1080 и 1081.",
                    ],
                    details,
                )
            if "некоррект" in normalized and "порт" in normalized:
                return (
                    "Введено неверное значение локального порта.",
                    [
                        "Укажите число от 1 до 65535.",
                        "Используйте свободный порт, который не занят другим приложением.",
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
                        "Используйте ссылку формата vless://, vmess:// или ss://.",
                        "Для сервера из подписки сначала отвяжите его, а потом редактируйте как ручной.",
                    ],
                    details,
                )
            if "vmess" in normalized or "vless" in normalized or "shadowsocks" in normalized:
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
        max_name_width = max((self._display_width(server.name) for server in servers), default=12)
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
        aligned_name = self._pad_display_width(self._truncate_display_width(server_name, name_width), name_width)
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

    def _servers_table(self, servers: list[ServerEntry], *, active_server_id: str | None) -> Table:
        table = Table(title="Серверы")
        table.add_column("Имя", overflow="fold", max_width=max(20, self.console.width - 70))
        table.add_column("Протокол", no_wrap=True)
        table.add_column("Адрес", no_wrap=True)
        table.add_column("Источник", no_wrap=True)
        table.add_column("Статус", no_wrap=True)
        for server in servers:
            table.add_row(
                self._ui_server_name(server.name),
                server.protocol.upper(),
                f"{server.host}:{server.port}",
                self._server_source_label(server),
                "Активен" if server.id == active_server_id else "Ожидание",
            )
        return table

    def _server_manager_choice_title(self, server: ServerEntry, *, active_server_id: str | None) -> str:
        return self._truncate_display_width(
            self._ui_server_name(server.name),
            max(18, self.console.width - 10),
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
        table.add_row("Источник", self._server_source_label(server))
        table.add_row("Создан", self._shorten_text(server.created_at, 19))
        if parent_subscription is not None:
            table.add_row("Подписка", parent_subscription.title)
        if server.source == "subscription":
            table.add_row("Примечание", "После обновления подписки параметры сервера могут измениться.")
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
                return f"подписка ({self._shorten_text(subscription.title, 18)})"
            return "подписка"
        return server.source

    def _server_source_short_label(self, server: ServerEntry) -> str:
        if server.source == "manual":
            return "ручной"
        if server.source == "subscription":
            return "подписка"
        return server.source

    @staticmethod
    def _sorted_servers(servers: list[ServerEntry]) -> list[ServerEntry]:
        return sorted(
            servers,
            key=lambda item: (
                item.source != "manual",
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
        table.add_row("Подписка", subscription.title)
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
        table.add_row("Название", subscription.title)
        table.add_row("URL", subscription.url)
        table.add_row("Серверов", str(len(self._subscription_servers(subscription.id))))
        table.add_row("Обновлено", self._shorten_text(subscription.updated_at, 19))
        table.add_row("Статус", self._subscription_status_label(subscription))
        if subscription.last_error:
            table.add_row("Последняя ошибка", subscription.last_error)
            table.add_row("Когда", self._shorten_text(subscription.last_error_at or "-", 19))
        return Panel.fit(
            table,
            title=f"Подписка: {subscription.title}",
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

        return FLAG_EMOJI_PATTERN.sub(replace_flag, value)

    @classmethod
    def _ui_server_name(cls, value: str) -> str:
        return cls._layout_safe_text(value)

    def _show_settings_saved(self, settings: AppSettings) -> None:
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Параметр", no_wrap=True, style="bold")
        table.add_column("Значение", overflow="fold", max_width=max(28, self.console.width - 34))
        table.add_row("SOCKS5", f"127.0.0.1:{settings.proxy_socks_port}")
        table.add_row("HTTP", f"127.0.0.1:{settings.proxy_http_port}")
        table.add_row("Системный proxy", "включать автоматически" if settings.set_system_proxy else "не изменять")
        table.add_row("Маршрутизация", self._active_routing_profile_name())

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
        should_disconnect = questionary.confirm(
            "Сейчас Xray запущен. Остановить подключение для обновления компонента?",
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

    def _routing_profiles_component_label(self) -> str:
        profiles_count = len(self.routing_profiles.list_profiles())
        return f"Профили маршрутизации: {profiles_count} шт."

    def _validated_settings(self, *, raise_on_error: bool = True) -> AppSettings:
        settings = self.storage.load_settings()
        try:
            settings.proxy_socks_port = clamp_port(int(settings.proxy_socks_port))
            settings.proxy_http_port = clamp_port(int(settings.proxy_http_port))
            settings.set_system_proxy = self._coerce_bool(settings.set_system_proxy)
            if settings.proxy_socks_port == settings.proxy_http_port:
                raise ValueError("SOCKS и HTTP порты в настройках не должны совпадать.")
            return settings
        except (TypeError, ValueError) as exc:
            if raise_on_error:
                raise ValueError(
                    "Параметры proxy в настройках некорректны. Откройте пункт 'Настройки' и сохраните их заново."
                ) from exc
            fallback = AppSettings(active_routing_profile_id=settings.active_routing_profile_id)
            return fallback

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
    def _pause() -> None:
        questionary.press_any_key_to_continue("Нажмите любую клавишу, чтобы вернуться в меню").ask()

    @staticmethod
    def _select(message: str, **kwargs):
        return questionary.select(message, instruction=" ", **kwargs)

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
