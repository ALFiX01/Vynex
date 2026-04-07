from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time

import requests

from .constants import (
    APP_RELEASES_API,
    APP_RELEASES_PAGE,
    APP_UPDATE_CACHE_FILE,
    APP_UPDATE_CHECK_TTL_SECONDS,
    APP_VERSION,
)


@dataclass(frozen=True)
class AppReleaseInfo:
    current_version: str
    latest_version: str | None = None
    release_url: str | None = None
    is_update_available: bool = False
    error: str | None = None
    checked_at: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "release_url": self.release_url,
            "is_update_available": self.is_update_available,
            "error": self.error,
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "AppReleaseInfo":
        return cls(
            current_version=str(payload.get("current_version") or APP_VERSION),
            latest_version=str(payload["latest_version"]) if payload.get("latest_version") else None,
            release_url=str(payload["release_url"]) if payload.get("release_url") else None,
            is_update_available=bool(payload.get("is_update_available", False)),
            error=str(payload["error"]) if payload.get("error") else None,
            checked_at=float(payload["checked_at"]) if payload.get("checked_at") is not None else None,
        )


class AppUpdateChecker:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": "Vynex-Client/1.0",
            }
        )

    def get_cached_release(self, *, max_age_seconds: float | None = APP_UPDATE_CHECK_TTL_SECONDS) -> AppReleaseInfo | None:
        try:
            payload = json.loads(APP_UPDATE_CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        try:
            cached = self._normalize_release_info(AppReleaseInfo.from_dict(payload))
        except (TypeError, ValueError):
            return None
        if max_age_seconds is not None:
            if cached.checked_at is None:
                return None
            age_seconds = time.time() - cached.checked_at
            if age_seconds < 0 or age_seconds > max_age_seconds:
                return None
        return cached

    def check_latest_release(self, *, force: bool = False) -> AppReleaseInfo:
        if not force:
            cached = self.get_cached_release()
            if cached is not None:
                return cached
        try:
            response = self.session.get(APP_RELEASES_API, timeout=10)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            cached = self.get_cached_release(max_age_seconds=None)
            if cached is not None:
                return cached
            return AppReleaseInfo(current_version=APP_VERSION, error=str(exc))

        latest_version = self._release_version_label(payload)
        release_url = str(payload.get("html_url") or APP_RELEASES_PAGE)
        release_info = self._normalize_release_info(
            AppReleaseInfo(
                current_version=APP_VERSION,
                latest_version=latest_version,
                release_url=release_url,
                checked_at=time.time(),
            )
        )
        self._save_cached_release(release_info)
        return release_info

    @staticmethod
    def _normalize_release_info(release_info: AppReleaseInfo) -> AppReleaseInfo:
        current_key = AppUpdateChecker._version_key(APP_VERSION)
        latest_key = AppUpdateChecker._version_key(release_info.latest_version or "")
        is_update_available = bool(
            release_info.latest_version
            and current_key is not None
            and latest_key is not None
            and latest_key > current_key
        )
        return AppReleaseInfo(
            current_version=APP_VERSION,
            latest_version=release_info.latest_version,
            release_url=release_info.release_url or APP_RELEASES_PAGE,
            is_update_available=is_update_available,
            error=release_info.error,
            checked_at=release_info.checked_at,
        )

    @staticmethod
    def _save_cached_release(release_info: AppReleaseInfo) -> None:
        try:
            APP_UPDATE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            APP_UPDATE_CACHE_FILE.write_text(
                json.dumps(release_info.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    @staticmethod
    def _release_version_label(payload: dict) -> str | None:
        tag_name = str(payload.get("tag_name") or "").strip()
        if tag_name:
            return tag_name
        name = str(payload.get("name") or "").strip()
        if name:
            return name
        return None

    @staticmethod
    def _version_key(value: str) -> tuple[int, ...] | None:
        numbers = tuple(int(part) for part in re.findall(r"\d+", value))
        if not numbers:
            return None
        trimmed = list(numbers)
        while len(trimmed) > 1 and trimmed[-1] == 0:
            trimmed.pop()
        return tuple(trimmed)
