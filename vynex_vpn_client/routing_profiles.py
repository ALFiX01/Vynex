from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from .constants import ROUTING_PROFILES_DIR, ROUTING_PROFILES_RAW_BASE, ROUTING_PROFILES_REPO_API

MANAGED_REMOTE_PROFILES_INDEX = ROUTING_PROFILES_DIR / ".managed_remote_profiles"


@dataclass
class RoutingProfile:
    profile_id: str
    name: str
    description: str
    rules: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoutingProfile":
        return cls(**data)


class RoutingProfileManager:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Vynex-VPN-Client/1.0"})
        self._ensure_defaults()

    def _ensure_defaults(self) -> None:
        ROUTING_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        synced = self._sync_remote_profiles()
        if synced or any(ROUTING_PROFILES_DIR.glob("*.json")):
            return
        managed_names: set[str] = set()
        for profile in self.default_profiles():
            target = self._profile_path(profile.profile_id)
            if not target.exists():
                target.write_text(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            managed_names.add(target.name)
        self._write_managed_remote_profile_names(managed_names)

    def list_profiles(self) -> list[RoutingProfile]:
        profiles: list[RoutingProfile] = []
        for path in sorted(ROUTING_PROFILES_DIR.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                profile = RoutingProfile.from_dict(payload)
                normalized_profile = self._normalize_profile(profile)
                profiles.append(normalized_profile)
                if normalized_profile.to_dict() != profile.to_dict():
                    path.write_text(
                        json.dumps(normalized_profile.to_dict(), indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
            except (OSError, json.JSONDecodeError, TypeError):
                continue
        return profiles

    def get_profile(self, profile_id: str) -> RoutingProfile | None:
        return next((profile for profile in self.list_profiles() if profile.profile_id == profile_id), None)

    def update_profiles(self) -> list[RoutingProfile]:
        if not self._sync_remote_profiles():
            raise RuntimeError("Не удалось обновить профили маршрутизации из GitHub.")
        return self.list_profiles()

    def _sync_remote_profiles(self) -> bool:
        try:
            response = self.session.get(ROUTING_PROFILES_REPO_API, timeout=20)
            response.raise_for_status()
            entries = response.json()
        except (requests.RequestException, ValueError):
            return False

        if not isinstance(entries, list):
            return False

        remote_names: set[str] = set()
        synced_any = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") != "file":
                continue
            name = str(entry.get("name", ""))
            if not name.endswith(".json"):
                continue
            remote_names.add(name)
            download_url = str(entry.get("download_url") or f"{ROUTING_PROFILES_RAW_BASE}/{name}")
            try:
                profile = self._download_remote_profile(download_url)
            except (requests.RequestException, ValueError, TypeError):
                continue
            ROUTING_PROFILES_DIR.joinpath(name).write_text(
                json.dumps(profile.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            synced_any = True
        if remote_names and not synced_any:
            return False
        self._remove_missing_managed_profiles(remote_names)
        self._write_managed_remote_profile_names(remote_names)
        return True

    def _download_remote_profile(self, url: str) -> RoutingProfile:
        response = self.session.get(url, timeout=20)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Routing profile payload must be a JSON object.")
        return self._normalize_profile(RoutingProfile.from_dict(payload))

    @staticmethod
    def _profile_path(profile_id: str) -> Path:
        return ROUTING_PROFILES_DIR / f"{profile_id}.json"

    @staticmethod
    def _read_managed_remote_profile_names() -> set[str]:
        try:
            payload = json.loads(MANAGED_REMOTE_PROFILES_INDEX.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if not isinstance(payload, list):
            return set()
        return {str(item) for item in payload if str(item).endswith(".json")}

    @staticmethod
    def _write_managed_remote_profile_names(profile_names: set[str]) -> None:
        MANAGED_REMOTE_PROFILES_INDEX.write_text(
            json.dumps(sorted(profile_names), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _remove_missing_managed_profiles(self, remote_names: set[str]) -> None:
        stale_names = self._read_managed_remote_profile_names() - remote_names
        for name in stale_names:
            ROUTING_PROFILES_DIR.joinpath(name).unlink(missing_ok=True)

    @staticmethod
    def default_profiles() -> list[RoutingProfile]:
        return [
            RoutingProfile(
                profile_id="default",
                name="Базовый",
                description="Текущая логика: private IP и localhost идут напрямую.",
                rules=[
                    {
                        "type": "field",
                        "ip": [
                            "geoip:private",
                            "127.0.0.0/8",
                            "10.0.0.0/8",
                            "172.16.0.0/12",
                            "192.168.0.0/16",
                        ],
                        "outboundTag": "direct",
                    },
                    {
                        "type": "field",
                        "domain": ["localhost"],
                        "outboundTag": "direct",
                    },
                ],
            ),
            RoutingProfile(
                profile_id="split-ru-youtube",
                name="Умный",
                description="RU/private/tor напрямую, YouTube и tunneling-домены через proxy, реклама блокируется.",
                rules=[
                    {
                        "type": "field",
                        "domain": [
                            "geosite:private",
                            "geosite:category-ru",
                            "localhost",
                        ],
                        "outboundTag": "direct",
                    },
                    {
                        "type": "field",
                        "ip": [
                            "geoip:ru",
                            "geoip:private",
                            "geoip:tor",
                            "127.0.0.0/8",
                            "10.0.0.0/8",
                            "172.16.0.0/12",
                            "192.168.0.0/16",
                        ],
                        "outboundTag": "direct",
                    },
                    {
                        "type": "field",
                        "domain": [
                            "geosite:youtube",
                            "geosite:category-proxy-tunnels",
                            "geosite:0x0",
                        ],
                        "outboundTag": "proxy",
                    },
                    {
                        "type": "field",
                        "ip": ["geoip:cc"],
                        "outboundTag": "proxy",
                    },
                    {
                        "type": "field",
                        "domain": ["geosite:category-ads"],
                        "outboundTag": "block",
                    },
                ],
            ),
        ]

    @staticmethod
    def _normalize_profile(profile: RoutingProfile) -> RoutingProfile:
        normalized_rules: list[dict[str, Any]] = []
        changed = False

        for rule in profile.rules:
            normalized_rule = dict(rule)
            domains = normalized_rule.get("domain")

            if isinstance(domains, list):
                filtered_domains = [domain for domain in domains if domain != "geosite:yt-ads"]
                if filtered_domains != domains:
                    changed = True
                    if filtered_domains:
                        normalized_rule["domain"] = filtered_domains
                    else:
                        continue

            normalized_rules.append(normalized_rule)

        if not changed:
            return profile

        return RoutingProfile(
            profile_id=profile.profile_id,
            name=profile.name,
            description=profile.description,
            rules=normalized_rules,
        )
