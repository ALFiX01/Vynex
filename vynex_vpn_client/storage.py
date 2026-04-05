from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .constants import (
    DATA_DIR,
    LEGACY_DATA_DIR,
    ROUTING_PROFILES_DIR,
    RUNTIME_STATE_FILE,
    SERVERS_FILE,
    SETTINGS_FILE,
    SUBSCRIPTIONS_FILE,
)
from .models import AppSettings, RuntimeState, ServerEntry, SubscriptionEntry


class JsonStorage:
    def __init__(self) -> None:
        self._ensure_layout()

    def _ensure_layout(self) -> None:
        self._migrate_legacy_data()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ROUTING_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_file(SERVERS_FILE, [])
        self._ensure_file(SUBSCRIPTIONS_FILE, [])
        self._ensure_file(RUNTIME_STATE_FILE, RuntimeState().to_dict())
        self._ensure_file(SETTINGS_FILE, AppSettings().to_dict())

    def _migrate_legacy_data(self) -> None:
        if not LEGACY_DATA_DIR.exists():
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for filename in ("servers.json", "subscriptions.json", "runtime_state.json", "xray.log", "config.json"):
            source = LEGACY_DATA_DIR / filename
            destination = DATA_DIR / filename
            if source.exists() and not destination.exists():
                shutil.copy2(source, destination)

    @staticmethod
    def _ensure_file(path: Path, default: list[Any] | dict[str, Any]) -> None:
        if not path.exists():
            path.write_text(json.dumps(default, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _read_json(path: Path, default: list[Any] | dict[str, Any]) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def load_servers(self) -> list[ServerEntry]:
        raw_items = self._read_json(SERVERS_FILE, [])
        return [ServerEntry.from_dict(item) for item in raw_items]

    def save_servers(self, servers: list[ServerEntry]) -> None:
        self._write_json(SERVERS_FILE, [server.to_dict() for server in servers])

    def upsert_server(self, server: ServerEntry) -> ServerEntry:
        servers = self.load_servers()
        for index, existing in enumerate(servers):
            if existing.raw_link == server.raw_link:
                server.id = existing.id
                server.created_at = existing.created_at
                servers[index] = server
                self.save_servers(servers)
                return server
        servers.append(server)
        self.save_servers(servers)
        return server

    def get_server(self, server_id: str) -> ServerEntry | None:
        return next((item for item in self.load_servers() if item.id == server_id), None)

    def load_subscriptions(self) -> list[SubscriptionEntry]:
        raw_items = self._read_json(SUBSCRIPTIONS_FILE, [])
        return [SubscriptionEntry.from_dict(item) for item in raw_items]

    def get_subscription_by_url(self, url: str) -> SubscriptionEntry | None:
        return next((item for item in self.load_subscriptions() if item.url == url), None)

    def save_subscriptions(self, subscriptions: list[SubscriptionEntry]) -> None:
        self._write_json(SUBSCRIPTIONS_FILE, [item.to_dict() for item in subscriptions])

    def upsert_subscription(self, subscription: SubscriptionEntry) -> SubscriptionEntry:
        subscriptions = self.load_subscriptions()
        for index, existing in enumerate(subscriptions):
            if existing.url == subscription.url:
                subscription.id = existing.id
                subscription.created_at = existing.created_at
                subscriptions[index] = subscription
                self.save_subscriptions(subscriptions)
                return subscription
        subscriptions.append(subscription)
        self.save_subscriptions(subscriptions)
        return subscription

    def load_runtime_state(self) -> RuntimeState:
        raw = self._read_json(RUNTIME_STATE_FILE, RuntimeState().to_dict())
        return RuntimeState.from_dict(raw)

    def save_runtime_state(self, state: RuntimeState) -> None:
        self._write_json(RUNTIME_STATE_FILE, state.to_dict())

    def load_settings(self) -> AppSettings:
        raw = self._read_json(SETTINGS_FILE, AppSettings().to_dict())
        return AppSettings.from_dict(raw)

    def save_settings(self, settings: AppSettings) -> None:
        self._write_json(SETTINGS_FILE, settings.to_dict())
