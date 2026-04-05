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

    def delete_server(self, server_id: str) -> ServerEntry | None:
        servers = self.load_servers()
        target = next((server for server in servers if server.id == server_id), None)
        if target is None:
            return None
        kept_servers = [server for server in servers if server.id != server_id]
        self.save_servers(kept_servers)

        subscriptions = self.load_subscriptions()
        changed = False
        for subscription in subscriptions:
            if server_id in subscription.server_ids:
                subscription.server_ids = [item_id for item_id in subscription.server_ids if item_id != server_id]
                changed = True
        if changed:
            self.save_subscriptions(subscriptions)
        return target

    def remove_servers_by_ids(self, server_ids: set[str], *, subscription_id: str | None = None) -> int:
        if not server_ids:
            return 0
        servers = self.load_servers()
        kept_servers: list[ServerEntry] = []
        removed_count = 0
        for server in servers:
            should_remove = server.id in server_ids
            if should_remove and subscription_id is not None:
                should_remove = server.source == "subscription" and server.subscription_id == subscription_id
            if should_remove:
                removed_count += 1
                continue
            kept_servers.append(server)
        if removed_count:
            self.save_servers(kept_servers)
        return removed_count

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
