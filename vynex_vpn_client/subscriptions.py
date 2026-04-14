from __future__ import annotations

from typing import Iterable

import httpx

from .models import ServerEntry, SubscriptionEntry, utc_now_iso
from .parsers import parse_server_entries
from .storage import JsonStorage


class SubscriptionManager:
    def __init__(self, storage: JsonStorage) -> None:
        self.storage = storage

    def import_subscription(self, subscription: SubscriptionEntry) -> list[ServerEntry]:
        servers = self.fetch_subscription_servers(subscription.url, subscription_id=subscription.id)
        return self.import_subscription_servers(subscription, servers)

    def import_subscription_servers(
        self,
        subscription: SubscriptionEntry,
        servers: list[ServerEntry],
    ) -> list[ServerEntry]:
        if not servers:
            raise ValueError("Не удалось импортировать ни один сервер из подписки.")

        current_servers = self._load_subscription_servers(subscription.id)
        merged = merge_subscription_servers(current_servers, servers)
        saved_servers: list[ServerEntry] = []
        saved_ids: set[str] = set()

        for server in merged:
            saved_server = self.storage.upsert_server(server)
            if saved_server.id in saved_ids:
                continue
            saved_servers.append(saved_server)
            saved_ids.add(saved_server.id)

        previous_server_ids = set(subscription.server_ids)
        subscription.server_ids = [item.id for item in saved_servers]
        orphan_ids = previous_server_ids - saved_ids
        if orphan_ids:
            self.storage.remove_servers_by_ids(orphan_ids, subscription_id=subscription.id)
        return saved_servers

    def refresh_all(
        self,
    ) -> tuple[
        list[tuple[SubscriptionEntry, int]],
        list[tuple[SubscriptionEntry, str]],
    ]:
        success: list[tuple[SubscriptionEntry, int]] = []
        failed: list[tuple[SubscriptionEntry, str]] = []
        subscriptions = self.storage.load_subscriptions()
        for subscription in subscriptions:
            try:
                imported = self.import_subscription(subscription)
                subscription.updated_at = utc_now_iso()
                subscription.last_error = None
                subscription.last_error_at = None
                self.storage.upsert_subscription(subscription)
                success.append((subscription, len(imported)))
            except Exception as exc:  # noqa: BLE001
                subscription.last_error = str(exc)
                subscription.last_error_at = utc_now_iso()
                self.storage.upsert_subscription(subscription)
                failed.append((subscription, str(exc)))
        return success, failed

    def fetch_subscription_servers(
        self,
        url: str,
        *,
        subscription_id: str | None = None,
    ) -> list[ServerEntry]:
        try:
            text = self._download_subscription_text(url)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Не удалось загрузить подписку: {exc}") from exc
        servers = parse_server_entries(text, source="subscription", subscription_id=subscription_id)
        if not servers:
            raise ValueError("Подписка не содержит поддерживаемых серверов.")
        return servers

    def _download_subscription_text(self, url: str) -> str:
        response = httpx.get(
            url,
            headers={"User-Agent": "v2rayN/6.0"},
            follow_redirects=True,
            timeout=15,
        )
        response.raise_for_status()
        return response.text.strip()

    def _load_subscription_servers(self, subscription_id: str) -> list[ServerEntry]:
        return [
            server
            for server in self.storage.load_servers()
            if server.source == "subscription" and server.subscription_id == subscription_id
        ]

    @staticmethod
    def summarize_protocols(servers: Iterable[ServerEntry]) -> dict[str, int]:
        counters: dict[str, int] = {}
        for server in servers:
            counters[server.protocol] = counters.get(server.protocol, 0) + 1
        return counters


def merge_subscription_servers(old: list[ServerEntry], fresh: list[ServerEntry]) -> list[ServerEntry]:
    old_by_key = {_server_key(server): server for server in old}
    fresh_by_key = {_server_key(server): server for server in fresh}
    merged: list[ServerEntry] = []

    for server in fresh:
        key = _server_key(server)
        previous = old_by_key.get(key)
        if previous is None:
            merged.append(server)
            continue

        updated = ServerEntry.from_dict(server.to_dict())
        updated.id = previous.id
        updated.created_at = previous.created_at
        updated.raw_link = server.raw_link or previous.raw_link
        updated.name = previous.name if previous.extra.get("custom_name") is True else server.name
        updated.extra = dict(previous.extra)
        updated.extra.update(server.extra)
        updated.extra.pop("stale", None)
        merged.append(updated)

    for server in old:
        key = _server_key(server)
        if key in fresh_by_key:
            continue
        stale = ServerEntry.from_dict(server.to_dict())
        stale.extra = dict(server.extra)
        stale.extra["stale"] = True
        merged.append(stale)

    return merged


def _server_key(server: ServerEntry) -> tuple[str, int, str]:
    credential = str(server.extra.get("id") or "") or str(server.extra.get("password") or "")
    return (server.host.lower(), server.port, credential)
