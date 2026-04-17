from __future__ import annotations

import json
import os
import shutil
import tempfile
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


class StorageCorruptionError(RuntimeError):
    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(
            f"Файл состояния поврежден и не может быть восстановлен автоматически: {path}"
        )


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
        for filename in ("servers.json", "subscriptions.json", "runtime_state.json", "settings.json"):
            source = LEGACY_DATA_DIR / filename
            destination = DATA_DIR / filename
            if source.exists() and not destination.exists():
                try:
                    shutil.copy2(source, destination)
                except OSError:
                    continue

    @staticmethod
    def _ensure_file(path: Path, default: list[Any] | dict[str, Any]) -> None:
        if not path.exists():
            JsonStorage._write_json(path, default)

    @staticmethod
    def _backup_path(path: Path) -> Path:
        return path.with_name(f"{path.name}.bak")

    @staticmethod
    def _atomic_write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temp_name = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temp_path.replace(path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    @classmethod
    def _load_json_payload(
        cls,
        path: Path,
        *,
        expected_type: type[list[Any]] | type[dict[str, Any]],
    ) -> Any:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, expected_type):
            raise ValueError(f"Файл {path} содержит JSON неподходящего типа.")
        return payload

    @classmethod
    def _read_json(
        cls,
        path: Path,
        default: list[Any] | dict[str, Any],
        *,
        strict: bool = False,
    ) -> Any:
        expected_type = type(default)
        try:
            return cls._load_json_payload(path, expected_type=expected_type)
        except FileNotFoundError:
            backup_path = cls._backup_path(path)
            try:
                payload = cls._load_json_payload(backup_path, expected_type=expected_type)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return default
            try:
                cls._atomic_write_text(
                    path,
                    json.dumps(payload, indent=2, ensure_ascii=False),
                )
            except OSError:
                pass
            return payload
        except (OSError, TypeError):
            return default
        except (json.JSONDecodeError, ValueError) as exc:
            backup_path = cls._backup_path(path)
            try:
                payload = cls._load_json_payload(backup_path, expected_type=expected_type)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                if strict:
                    raise StorageCorruptionError(path) from exc
                return default
            try:
                cls._atomic_write_text(
                    path,
                    json.dumps(payload, indent=2, ensure_ascii=False),
                )
            except OSError:
                pass
            return payload

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        serialized = json.dumps(payload, indent=2, ensure_ascii=False)
        JsonStorage._atomic_write_text(path, serialized)
        try:
            JsonStorage._atomic_write_text(JsonStorage._backup_path(path), serialized)
        except OSError:
            pass

    def load_servers(self) -> list[ServerEntry]:
        raw_items = self._read_json(SERVERS_FILE, [])
        return [ServerEntry.from_dict(item) for item in raw_items]

    def save_servers(self, servers: list[ServerEntry]) -> None:
        self._write_json(SERVERS_FILE, [server.to_dict() for server in servers])

    def upsert_server(self, server: ServerEntry) -> ServerEntry:
        servers = self.load_servers()
        for index, existing in enumerate(servers):
            if existing.id == server.id:
                if any(
                    server.raw_link and other.id != server.id and other.raw_link == server.raw_link
                    for other in servers
                ):
                    raise ValueError("Сервер с такой ссылкой уже существует.")
                server.created_at = existing.created_at
                servers[index] = server
                self.save_servers(servers)
                return server
        if server.is_amneziawg:
            for index, existing in enumerate(servers):
                if not self._same_server_identity(existing, server):
                    continue
                server.id = existing.id
                server.created_at = existing.created_at
                servers[index] = server
                self.save_servers(servers)
                return server
        if server.raw_link:
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

    def detach_server_from_subscription(
        self,
        server_id: str,
    ) -> tuple[ServerEntry | None, SubscriptionEntry | None]:
        servers = self.load_servers()
        target = next((server for server in servers if server.id == server_id), None)
        if target is None:
            return None, None

        previous_subscription_id = target.subscription_id
        target.source = "manual"
        target.subscription_id = None
        self.save_servers(servers)

        if previous_subscription_id is None:
            return target, None

        subscriptions = self.load_subscriptions()
        parent_subscription = next(
            (subscription for subscription in subscriptions if subscription.id == previous_subscription_id),
            None,
        )
        if parent_subscription is None:
            return target, None
        if server_id in parent_subscription.server_ids:
            parent_subscription.server_ids = [
                item_id for item_id in parent_subscription.server_ids if item_id != server_id
            ]
            self.save_subscriptions(subscriptions)
        return target, parent_subscription

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

    def get_subscription(self, subscription_id: str) -> SubscriptionEntry | None:
        return next((item for item in self.load_subscriptions() if item.id == subscription_id), None)

    def save_subscriptions(self, subscriptions: list[SubscriptionEntry]) -> None:
        self._write_json(SUBSCRIPTIONS_FILE, [item.to_dict() for item in subscriptions])

    def upsert_subscription(self, subscription: SubscriptionEntry) -> SubscriptionEntry:
        subscriptions = self.load_subscriptions()
        for index, existing in enumerate(subscriptions):
            if existing.id == subscription.id:
                subscription.created_at = existing.created_at
                subscriptions[index] = subscription
                self.save_subscriptions(subscriptions)
                return subscription
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

    def delete_subscription(
        self,
        subscription_id: str,
        *,
        remove_servers: bool = True,
    ) -> tuple[SubscriptionEntry | None, int]:
        subscriptions = self.load_subscriptions()
        target = next((item for item in subscriptions if item.id == subscription_id), None)
        if target is None:
            return None, 0

        kept_subscriptions = [item for item in subscriptions if item.id != subscription_id]
        self.save_subscriptions(kept_subscriptions)

        servers = self.load_servers()
        kept_servers: list[ServerEntry] = []
        affected_servers = 0
        changed = False
        for server in servers:
            is_owned_by_subscription = server.source == "subscription" and server.subscription_id == subscription_id
            if not is_owned_by_subscription:
                kept_servers.append(server)
                continue
            affected_servers += 1
            changed = True
            if remove_servers:
                continue
            server.source = "manual"
            server.subscription_id = None
            kept_servers.append(server)

        if changed:
            self.save_servers(kept_servers)
        return target, affected_servers

    def load_runtime_state(self) -> RuntimeState:
        raw = self._read_json(
            RUNTIME_STATE_FILE,
            RuntimeState().to_dict(),
            strict=True,
        )
        return RuntimeState.from_dict(raw)

    def save_runtime_state(self, state: RuntimeState) -> None:
        self._write_json(RUNTIME_STATE_FILE, state.to_dict())

    def load_settings(self) -> AppSettings:
        raw = self._read_json(SETTINGS_FILE, AppSettings().to_dict())
        return AppSettings.from_dict(raw)

    def save_settings(self, settings: AppSettings) -> None:
        self._write_json(SETTINGS_FILE, settings.to_dict())

    @staticmethod
    def _same_server_identity(left: ServerEntry, right: ServerEntry) -> bool:
        if left.protocol.lower() != right.protocol.lower():
            return False
        if left.host.lower() != right.host.lower() or left.port != right.port:
            return False
        if not left.identity_token or not right.identity_token:
            return False
        return left.identity_token == right.identity_token
