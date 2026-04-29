from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavigationItem:
    key: str
    title: str
    subtitle: str


DEFAULT_NAVIGATION_ITEMS: tuple[NavigationItem, ...] = (
    NavigationItem("connect", "Подключение", "Запуск и остановка VPN"),
    NavigationItem("servers", "Серверы", "Управление списком VPN-серверов"),
    NavigationItem("subscriptions", "Подписки", "Источники серверов"),
    NavigationItem("settings", "Настройки", "Режимы и параметры"),
    NavigationItem("components", "Компоненты", "Runtime и базы"),
    NavigationItem("status", "Статус", "Состояние подключения"),
)
