from __future__ import annotations

from typing import Iterable

import requests

from .models import ServerEntry, SubscriptionEntry, utc_now_iso
from .parsers import parse_share_link
from .storage import JsonStorage
from .utils import decode_base64


class SubscriptionManager:
    def __init__(self, storage: JsonStorage) -> None:
        self.storage = storage
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Vynex-VPN-Client/1.0"})

    def import_subscription(self, subscription: SubscriptionEntry) -> list[ServerEntry]:
        previous_server_ids = set(subscription.server_ids)
        links = self.fetch_subscription_links(subscription.url)
        imported: list[ServerEntry] = []
        imported_ids: set[str] = set()
        for link in links:
            try:
                server = parse_share_link(link, source="subscription", subscription_id=subscription.id)
            except ValueError:
                continue
            saved_server = self.storage.upsert_server(server)
            if saved_server.id in imported_ids:
                continue
            imported.append(saved_server)
            imported_ids.add(saved_server.id)
        if not imported:
            raise ValueError("Не удалось импортировать ни один сервер из подписки.")
        subscription.server_ids = [item.id for item in imported]
        stale_server_ids = previous_server_ids - imported_ids
        if stale_server_ids:
            self.storage.remove_servers_by_ids(stale_server_ids, subscription_id=subscription.id)
        return imported

    def refresh_all(self) -> tuple[list[tuple[SubscriptionEntry, int]], list[tuple[SubscriptionEntry, str]]]:
        success: list[tuple[SubscriptionEntry, int]] = []
        failed: list[tuple[SubscriptionEntry, str]] = []
        subscriptions = self.storage.load_subscriptions()
        for subscription in subscriptions:
            try:
                imported = self.import_subscription(subscription)
                subscription.updated_at = utc_now_iso()
                self.storage.upsert_subscription(subscription)
                success.append((subscription, len(imported)))
            except Exception as exc:  # noqa: BLE001
                failed.append((subscription, str(exc)))
        return success, failed

    def fetch_subscription_links(self, url: str) -> list[str]:
        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Не удалось загрузить подписку: {exc}") from exc
        text = response.content.decode("utf-8-sig", errors="ignore").strip()
        payload = text if "://" in text else decode_base64(text)
        links = [line.strip() for line in payload.splitlines() if line.strip()]
        valid_links = [line for line in links if self._is_supported_link(line)]
        if not valid_links:
            raise ValueError("Подписка не содержит поддерживаемых ссылок.")
        return valid_links

    @staticmethod
    def _is_supported_link(link: str) -> bool:
        return link.startswith(("vless://", "vmess://", "ss://"))

    @staticmethod
    def summarize_protocols(servers: Iterable[ServerEntry]) -> dict[str, int]:
        counters: dict[str, int] = {}
        for server in servers:
            counters[server.protocol] = counters.get(server.protocol, 0) + 1
        return counters
