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

        all_servers = self.storage.load_servers()
        current_servers = [
            server
            for server in all_servers
            if server.source == "subscription" and server.subscription_id == subscription.id
        ]
        merged = merge_subscription_servers(current_servers, servers)
        stored_servers = self.storage.upsert_servers(merged, existing_servers=all_servers, save=False)
        saved_servers: list[ServerEntry] = []
        saved_ids: set[str] = set()
        for saved_server in stored_servers:
            if saved_server.id in saved_ids:
                continue
            saved_servers.append(saved_server)
            saved_ids.add(saved_server.id)

        previous_server_ids = set(subscription.server_ids)
        subscription.server_ids = [item.id for item in saved_servers]
        orphan_ids = previous_server_ids - saved_ids
        if orphan_ids:
            all_servers[:] = [
                server
                for server in all_servers
                if not (
                    server.id in orphan_ids
                    and server.source == "subscription"
                    and server.subscription_id == subscription.id
                )
            ]
        self.storage.save_servers(all_servers)
        return saved_servers

    def refresh_all(
        self,
        *,
        only_auto_update: bool = False,
    ) -> tuple[
        list[tuple[SubscriptionEntry, int]],
        list[tuple[SubscriptionEntry, str]],
    ]:
        success: list[tuple[SubscriptionEntry, int]] = []
        failed: list[tuple[SubscriptionEntry, str]] = []
        subscriptions = self.storage.load_subscriptions()
        if only_auto_update:
            subscriptions = [subscription for subscription in subscriptions if subscription.auto_update]
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
    return (server.host.lower(), server.port, server.identity_token)
